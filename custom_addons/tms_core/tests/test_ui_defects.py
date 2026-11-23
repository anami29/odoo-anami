from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import TmsCommon

@tagged("post_install", "-at_install")
class TestUiDefects(TmsCommon):
    """Regression tests for the defects found in the delivered screens and controls."""

    # ------------------------------------------------------------------ helpers
    def _move_form_arch(self):
        return self.env.ref("account.view_move_form")._get_combined_arch()

    def _line_column(self, arch, name):
        nodes = arch.xpath("//field[@name='invoice_line_ids']/list/field[@name='%s']" % name)
        return nodes

    def _kpi_with(self, xmlid, result):
        """The KPI record with its value method patched to return ``result``; returns (kpi, patcher)."""
        kpi = self.env.ref(xmlid)
        Kpi = type(self.env["tms.kpi"])
        return kpi, patch.object(Kpi, kpi._method(), lambda self: result, create=True)

    def _alert(self, xmlid, result):
        kpi, patcher = self._kpi_with(xmlid, result)
        with patcher:
            kpi.invalidate_recordset()
            kpi._compute_value()
            return kpi.alert

    # ------------------------------------------------------------------ KPI alert rules
    def test_kpi_alert_follows_its_rule(self):
        none = [("id", "in", [])]
        # Trend only: never red, whatever the count
        self.assertFalse(self._alert("tms_core.kpi_04", (5, 0.0, "tms.order", none)))
        # Any: red with a count
        self.assertTrue(self._alert("tms_core.kpi_01", (1, 0.0, "tms.order", none)))
        self.assertFalse(self._alert("tms_core.kpi_01", (0, 0.0, "tms.order", none)))
        # Above the target: average hours to verification against 48 hours
        self.assertFalse(self._alert("tms_core.kpi_07", (4, 30.0, "tms.trip.consignment", none)))
        self.assertTrue(self._alert("tms_core.kpi_07", (4, 72.0, "tms.trip.consignment", none)))
        # Below 90%: fleet recovery
        self.assertFalse(self._alert("tms_core.kpi_18", (1, 95.0, "tms.truck.pnl", none)))
        self.assertTrue(self._alert("tms_core.kpi_18", (1, 60.0, "tms.truck.pnl", none)))
        # Count above the target
        self.assertFalse(self._alert("tms_core.kpi_24", (3, 0.0, "tms.override.log", none)))
        self.assertTrue(self._alert("tms_core.kpi_24", (4, 0.0, "tms.override.log", none)))

    def test_kpi_alert_older_than(self):
        fresh = self.env["res.partner"].create({"name": "Fresh claimant"})
        old = self.env["res.partner"].create({"name": "Old claimant"})
        self.env.cr.execute("UPDATE res_partner SET create_date = %s WHERE id = %s",
                            (fields.Datetime.now() - timedelta(days=60), old.id))
        old.invalidate_recordset(["create_date"])
        self.assertFalse(self._alert("tms_core.kpi_08", (1, 0.0, "res.partner", [("id", "in", fresh.ids)])))
        self.assertTrue(self._alert("tms_core.kpi_08", (2, 0.0, "res.partner", [("id", "in", (fresh | old).ids)])))

    def test_kpi_alert_default_without_setting(self):
        """A KPI loaded before the alert setting existed still follows its built-in rule."""
        kpi = self.env.ref("tms_core.kpi_04")
        kpi.write({"alert_mode": False, "alert_threshold": 0.0})
        self.assertEqual(kpi._tms_alert_setting(), ("trend", 0.0))
        self.assertFalse(self._alert("tms_core.kpi_04", (5, 0.0, "tms.order", [("id", "in", [])])))
        unknown = self.env["tms.kpi"].create({"code": "KPI-99", "name": "Custom"})
        self.assertEqual(unknown._tms_alert_setting(), ("any", 0.0))

    # ------------------------------------------------------------------ compliance warning windows
    def test_security_days_seeded(self):
        icp = self.env["ir.config_parameter"].sudo()
        self.assertEqual(icp.get_param("tms.default_driver_license_security_days"), "15")
        self.assertEqual(icp.get_param("tms.default_vehicle_insurance_security_days"), "15")
        # seeded once: an upgrade keeps the value set in Settings
        for xmlid in ("icp_driver_security_days", "icp_vehicle_security_days"):
            data = self.env["ir.model.data"].search([("module", "=", "tms_core"), ("name", "=", xmlid)])
            self.assertTrue(data.noupdate, xmlid)

    # ------------------------------------------------------------------ consumer mode
    def test_consumer_mode_not_offered(self):
        for model in ("res.config.settings", "res.company"):
            selection = self.env[model].fields_get(["tms_operation_mode"])["tms_operation_mode"]["selection"]
            keys = [key for key, _label in selection]
            self.assertIn("hybrid", keys)
            self.assertIn("broker", keys)
            self.assertNotIn("consumer", keys)
        with self.assertRaises(UserError):
            self.company.tms_operation_mode = "consumer"
        # a branch already holding the value still reads it on screen
        other = self.env["res.company"].create({"name": "Legacy branch"})
        self.env.cr.execute("UPDATE res_company SET tms_operation_mode = 'consumer' WHERE id = %s", (other.id,))
        other.invalidate_recordset()
        selection = self.env["res.company"].fields_get(["tms_operation_mode"])["tms_operation_mode"]["selection"]
        self.assertIn("consumer", [key for key, _label in selection])

    # ------------------------------------------------------------------ return to origin
    def test_return_to_origin_hidden(self):
        arch = self.env.ref("tms_core.tms_consignment_form")._get_combined_arch()
        buttons = arch.xpath("//button[@name='action_return_to_origin']")
        self.assertTrue(buttons)
        for button in buttons:
            self.assertIn(button.get("invisible"), ("1", "True"))
        self.assertTrue(hasattr(self.env["tms.consignment"], "action_return_to_origin"))

    # ------------------------------------------------------------------ claims
    def test_claim_credit_note_reverses_freight_invoice(self):
        lr = self._lr()
        trip = self._trip(lr)
        self._deliver_and_verify(trip, damage_qty=1)
        invoice = self.env["tms.invoice.lr.wizard"]._tms_create_invoices(lr)
        invoice.action_post()
        self.assertEqual(invoice.move_type, "out_invoice")
        claim = lr.claim_ids
        claim.write({"estimated_amount": 800.0, "notice_date": fields.Date.today()})
        claim.action_submit()
        claim.with_user(self.u_manager).action_approve()
        self.assertEqual(claim.credit_note_id.reversed_entry_id, invoice)
        claim.credit_note_id.action_post()
        self.assertEqual(claim.state, "settled")

    def test_claim_credit_note_without_invoice(self):
        lr = self._lr()
        trip = self._trip(lr)
        self._deliver_and_verify(trip, shortage_qty=1)
        claim = lr.claim_ids
        claim.write({"estimated_amount": 300.0, "notice_date": fields.Date.today()})
        claim.action_submit()
        claim.with_user(self.u_manager).action_approve()
        self.assertTrue(claim.credit_note_id)
        self.assertFalse(claim.credit_note_id.reversed_entry_id)

    def test_incident_claim_links_pod_line(self):
        lr = self._lr()
        trip = self._trip(lr)
        incident = self.env["tms.trip.incident"].create({
            "trip_id": trip.id,
            "consignment_id": lr.id,
            "category_id": self.env.ref("tms_core.tms_category_damage").id,
            "occurred_at": fields.Datetime.now(),
            "description": "Cartons crushed in transit",
        })
        incident.action_create_claim()
        claim = incident.claim_id
        self.assertFalse(claim.pod_link_id)
        self._deliver_and_verify(trip, damage_qty=1)
        link = trip.consignment_link_ids
        self.assertEqual(lr.claim_ids, claim)
        self.assertEqual(claim.pod_link_id, link)
        self.assertEqual(link.claim_id, claim)

    # ------------------------------------------------------------------ invoice lines
    def test_bill_columns_hidden_on_customer_invoices(self):
        arch = self._move_form_arch()
        names = ["tms_cost_class", "tms_overhead", "tms_overhead_reason"]
        model = self.env["account.move.line"]
        names += [n for n in ("vehicle_id", "tms_truck_category_id", "tms_cover_from", "tms_cover_to",
                              "tms_estimate_line_id") if n in model._fields]
        for name in names:
            nodes = self._line_column(arch, name)
            self.assertTrue(nodes, name)
            for node in nodes:
                hidden = (node.get("column_invisible") or "").replace('"', "'")
                self.assertTrue(("'out_invoice'" in hidden and "'out_refund'" in hidden)
                                or "not in ('in_invoice', 'in_refund')" in hidden, (name, hidden))
        charge = self._line_column(arch, "tms_charge_id")
        self.assertTrue(charge)
        self.assertEqual(charge[0].get("optional"), "show")
        self.assertNotIn("out_invoice", charge[0].get("column_invisible") or "")

    def test_variance_button_only_on_bills(self):
        if "tms_variance_approved_by_id" not in self.env["account.move"]._fields:
            self.skipTest("estimates not installed")
        arch = self._move_form_arch()
        button = arch.xpath("//button[@name='action_tms_approve_variance']")
        self.assertTrue(button)
        self.assertIn("'in_invoice'", button[0].get("invisible"))
        self.assertIn("move_type not in", button[0].get("invisible"))

    def test_payable_actions_open_bill_list(self):
        bill_list = self.env.ref("account.view_in_invoice_bill_tree")
        form = self.env.ref("account.view_move_form")
        xmlids = ["tms_core.action_tms_hire_payables"]
        if self.env.ref("tms_estimate.action_tms_variance_bills", raise_if_not_found=False):
            xmlids.append("tms_estimate.action_tms_variance_bills")
        for xmlid in xmlids:
            action = self.env.ref(xmlid)
            views = action.view_ids.sorted("sequence")
            self.assertEqual(views[0].view_mode, "list", xmlid)
            self.assertEqual(views[0].view_id, bill_list, xmlid)
            self.assertEqual(views.filtered(lambda v: v.view_mode == "form").view_id, form, xmlid)
            data = action.read()[0]
            self.assertEqual(list(data["views"][0]), [bill_list.id, "list"], xmlid)

    # ------------------------------------------------------------------ hire bill message
    def test_hire_block_message_in_indian_format(self):
        lr = self._lr()
        trip = self._trip(lr, fleet="market")
        memo = trip.hire_memo_ids
        self._deliver_and_verify(trip)
        memo.action_create_invoice()
        bill = memo.invoice_ids
        bill.invoice_date = fields.Date.today()
        bill.invoice_line_ids.price_unit = 130200.0
        with self.assertRaises(UserError) as err:
            bill.action_post()
        message = str(err.exception)
        self.assertIn("approved ₹30,000.00", message)
        self.assertIn("billed ₹1,30,200.00", message)
        self.assertIn("tolerance ₹150.00", message)
        self.assertNotIn("BR-", message)


@tagged("post_install", "-at_install")
class TestUiRatioGroups(TmsCommon):
    def test_ratio_columns_are_sum_over_sum(self):
        """Group rows of P&L ratio columns are sum(numerator) / sum(denominator), never an average."""
        from odoo.addons.tms_core.models.tools import ratio_read_group

        class Parent:
            def read_group(self, domain, specs, groupby, **kw):
                self.specs = specs
                # two trips: 10 % margin on 1,000 and 50 % margin on 9,000 -> group margin 46 %, average would be 30 %
                return [{"tms_rsum_trip_margin": 100.0 + 4500.0, "tms_rsum_revenue": 1000.0 + 9000.0,
                         "tms_rsum_running_charged": 60.0, "tms_rsum_fixed_charged": 30.0,
                         "tms_rsum_running_actual": 100.0, "tms_rsum_fixed_actual": 50.0}]

        parent = Parent()
        ratios = {"margin_pct": (("trip_margin",), ("revenue",), 100.0),
                  "recovery_pct": (("running_charged", "fixed_charged"), ("running_actual", "fixed_actual"), 100.0)}
        group = ratio_read_group(parent, ratios, [], ["margin_pct:avg", "recovery_pct"], ["vehicle_id"])[0]
        self.assertAlmostEqual(group["margin_pct"], 46.0)
        self.assertAlmostEqual(group["recovery_pct"], 60.0)
        self.assertFalse([k for k in group if k.startswith("tms_rsum_")])
        for model in ("tms.trip.pnl", "tms.customer.pnl", "tms.truck.pnl", "tms.fleet.pnl"):
            if model in self.env:
                self.assertTrue(self.env[model]._tms_ratios, model)
