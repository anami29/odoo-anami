"""Screens aligned to the agreed mockup in 18.0.1.6.0 (TMS-UIG-001, screen audit of 18.0.1.5.1): the LR list and
form, the trip sheet (route and schedule, compliance gatekeeper, financials), the daily status report, the Indian
locale, vehicle names, hire memo numbers and the operating-mode badge."""

from unittest.mock import patch

from lxml import etree

from odoo import Command, fields
from odoo.tests import tagged

from .common import TmsCommon


@tagged("post_install", "-at_install")
class TestUiScreens(TmsCommon):

    # ------------------------------------------------------------------ cross-cutting
    def test_indian_locale_is_active(self):
        lang = self.env["res.lang"]._lang_get("en_IN")
        self.assertTrue(lang and lang.active)
        self.assertEqual(lang.date_format, "%d/%m/%Y")
        self.assertEqual(lang.grouping, "[3,2,0]")
        self.assertEqual(self.env["ir.default"]._get("res.partner", "lang"), "en_IN")

    def test_vehicle_reads_as_its_plate(self):
        self.assertEqual(self.vehicle.display_name, "MH04LE7713")

    def test_hire_memo_number_series(self):
        trip = self._trip(self._lr(), fleet="market", start=False)
        memo = trip.hire_memo_ids[:1]
        self.assertTrue(memo.name.startswith("HM/%s/" % fields.Date.today().year), memo.name)
        plain = self.env["purchase.order"].create({"partner_id": self.vendor.id})
        self.assertFalse(plain.name.startswith("HM/"), plain.name)

    def test_mode_badge(self):
        self.assertEqual(self.company.with_user(self.u_dispatcher).tms_mode_label(), "Hybrid")
        outsider = self.env["res.users"].create({"name": "Outsider", "login": "outsider",
                                                 "groups_id": [Command.set([self.env.ref("base.group_user").id])]})
        self.assertFalse(self.company.with_user(outsider).tms_mode_label())

    # ------------------------------------------------------------------ LR list and form
    def test_lr_screen_fields(self):
        lr = self._lr(confirm=False, value=150000.0)
        self.assertTrue(lr.ewaybill_required)
        self.assertEqual(lr.ewaybill_coverage, "0 / 1")
        self.assertIn("Line 1 (Rice bags, declared ₹1,50,000.00) has no e-way bill number", lr.ewaybill_banner)
        self.assertTrue(lr.line_ids.ewaybill_needed)
        self.assertIn("Confirm the LR", lr.plan_banner)
        lr.action_confirm()
        action = lr.action_plan_trip()
        self.assertEqual(action["res_model"], "tms.plan.trip.wizard")
        self.assertEqual(action["context"]["active_ids"], lr.ids)
        bill = self.env["tms.ewaybill"].create({"name": "331244809175", "consignment_id": lr.id,
                                                "valid_until": fields.Datetime.add(fields.Datetime.now(), days=2)})
        lr.line_ids.ewaybill_id = bill
        self.assertEqual(lr.ewaybill_coverage, "1 / 1")
        self.assertFalse(lr.ewaybill_banner)
        self.assertFalse(lr.line_ids.ewaybill_needed)
        trip = self._trip(lr, start=False)
        self.assertEqual(lr.trip_display, trip.name)
        self.assertIn(trip.name, lr.plan_banner)
        self.assertEqual(lr.action_open_trip()["res_id"], trip.id)
        self.assertFalse(lr.invoice_eligible)
        self.assertEqual(lr.invoice_hint, "Invoiced once the POD is verified.")
        self.assertTrue(lr.lr_series)

    def test_lr_views(self):
        form = etree.fromstring(self.env["tms.consignment"].get_view(view_type="form")["arch"])
        for name in ("action_plan_trip", "action_open_trip", "action_view_ewaybills", "action_view_claims"):
            self.assertTrue(form.xpath("//button[@name='%s']" % name), name)
        self.assertTrue(form.xpath("//page[@name='other_info']"))
        lst = etree.fromstring(self.env["tms.consignment"].get_view(view_type="list")["arch"])
        for name in ("total_packages", "total_actual_weight", "trip_display", "ewaybill_coverage"):
            self.assertTrue(lst.xpath("//field[@name='%s']" % name), name)
        self.assertTrue(lst.xpath("//header/button[@name='action_plan_trip']"))
        self.assertTrue(self.env["tms.consignment"].get_view(view_type="kanban")["arch"])

    # ------------------------------------------------------------------ trip sheet
    def test_trip_sheet(self):
        lr = self._lr()
        trip = self._trip(lr)
        self.assertIn("→", trip.schedule_display)
        self.assertIn("24:00 h", trip.schedule_display)
        self.assertTrue(trip.actual_display)
        self.assertIn("stops: Nagpur", trip.route_detail)
        self.assertIn("registration certificate", trip.gate_table.lower())
        self.assertIn("MH04LE7713", trip.gate_table)
        self.assertIn("Checked", trip.gate_table)
        self.assertIn("Gross revenue", trip.ledger_html)
        self.assertIn(lr.name, trip.ledger_html)
        self.assertIn("Awaits POD", trip.ledger_html)
        self._deliver_and_verify(trip)
        trip.invalidate_recordset(["ledger_html"])
        self.assertIn("To invoice", trip.ledger_html)

    def test_trip_form_fleet_type_once(self):
        """After the start the trip-level fleet type leaves the sheet: Current assignment shows it."""
        form = etree.fromstring(self.env["tms.order"].get_view(view_type="form")["arch"])
        node = form.xpath("//group[@name='planned_assignment']/field[@name='fleet_type']")
        self.assertTrue(node)
        self.assertEqual(len(form.xpath("//field[@name='fleet_type'][not(ancestor::notebook)]")), 1)
        names = [p.get("name") for p in form.xpath("//notebook/page")]
        for name in ("consignments", "dsr", "assignments", "incidents", "charges", "expenses", "ledger", "pod"):
            self.assertIn(name, names)
        self.assertLess(names.index("dsr"), names.index("assignments"))
        self.assertLess(names.index("ledger"), names.index("pod"))

    def test_print_dsr(self):
        trip = self._trip(self._lr())
        with patch.object(type(self.env["ir.actions.report"]), "report_action",
                          lambda report, docids, *a, **k: {"report": report.report_name, "ids": docids.ids}):
            action = self.env["tms.order"].action_print_dsr()
            self.assertEqual(action["report"], "tms_core.report_dsr")
            self.assertIn(trip.id, action["ids"])
            self.assertEqual(trip.action_print_dsr()["ids"], trip.ids)
