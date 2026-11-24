"""Exception and settlement screens aligned to the agreed mockup in 18.0.1.6.0: the incident's reference,
treatment and prefilled charge, the claim's settlement legs, the charge billing status, the freight invoice's unbilled
charges, the hire bill's match elements, the two one-page registers and Settings · Transport."""

from odoo import fields
from odoo.tests import tagged

from .common import TmsCommon


@tagged("post_install", "-at_install")
class TestUiScreensD(TmsCommon):

    def _incident(self, trip, lr, category="tms_core.tms_category_detention", **vals):
        return self.env["tms.trip.incident"].create({
            "trip_id": trip.id, "consignment_id": lr.id, "category_id": self.env.ref(category).id,
            "occurred_at": fields.Datetime.now(), "location": "Kalamna", **vals})

    def test_incident_reference_and_charge(self):
        lr = self._lr()
        trip = self._trip(lr)
        inc = self._incident(trip, lr, responsible="customer", estimated_amount=2500.0)
        self.assertTrue(inc.name.startswith("INC/%s/" % fields.Date.today().year), inc.name)
        self.assertIn("Kalamna", inc.subtitle)
        self.assertEqual(inc.treatment, "none")
        action = inc.action_create_charge()
        charge = self.env["tms.trip.charge"].browse(action["res_id"])
        self.assertEqual(charge.amount, 2500.0)
        self.assertEqual(charge.recover_from, "customer")
        self.assertEqual(charge.partner_id, lr.billing_partner_id)
        self.assertEqual(charge.incident_id, inc)
        if charge.state == "approved":
            self.assertEqual(inc.state, "resolved")
        self.assertEqual(inc.customer_charge, 2500.0)
        self.assertEqual(inc.treatment, "charge_customer")
        self.assertIn(charge.name, inc.realised_via)
        self.assertEqual(charge.billing_detail, "to_invoice" if charge.state == "approved" else "na")
        self.assertEqual(charge.customer_amount, 2500.0)

    def test_claim_settlement_and_banner(self):
        lr = self._lr()
        trip = self._trip(lr)
        claim = self.env["tms.damage.claim"].create({
            "consignment_id": lr.id, "trip_id": trip.id, "damage_type": "shortage", "affected_qty": 3,
            "estimated_amount": 8100.0, "notice_date": fields.Date.today()})
        self.assertTrue(claim.name.startswith("DC/") or claim.name.startswith("CLM/"), claim.name)
        claim.action_submit()
        self.assertIn("Under review", claim.claim_banner)
        self.assertIn("Customer leg", claim.settlement_html)
        self.assertIn("Net impact", claim.settlement_html)
        self.assertEqual(claim.pod_status, "Not uploaded")
        self._deliver_and_verify(trip)
        claim.invalidate_recordset(["pod_status", "claim_banner"])
        self.assertIn("Verified", claim.pod_status)
        claim.with_user(self.u_manager).action_approve()
        claim.invalidate_recordset(["claim_banner"])
        self.assertTrue(claim.approved_on)
        self.assertIn("Approved by", claim.claim_banner)

    def test_freight_invoice_unbilled_charges(self):
        lr = self._lr(basis="paid")
        trip = self._trip(lr)
        move = self.env["tms.invoice.lr.wizard"]._tms_create_invoices(lr)
        self.assertEqual(move.tms_inv_lr_count, 1)
        self.assertIn(lr.name, move.tms_billing_basis)
        self.assertEqual(move.tms_ui_trip_count, 1)
        charge = self.env["tms.trip.charge"].create({
            "trip_id": trip.id, "consignment_id": lr.id, "category_id": self.env.ref("tms_core.tms_category_detention").id,
            "amount": 1200.0, "recover_from": "customer", "partner_id": lr.billing_partner_id.id})
        charge.action_confirm()
        if charge.state != "approved":
            charge.with_user(self.u_manager).action_approve()
        move.invalidate_recordset()
        self.assertEqual(move.tms_unbilled_charge_ids, charge)
        self.assertIn(charge.name, move.tms_unbilled_note)
        move.action_tms_add_unbilled_charges()
        self.assertTrue(move.invoice_line_ids.filtered(lambda line: line.tms_charge_id == charge))
        self.assertEqual(charge.billing_detail, "on_draft")
        move.invalidate_recordset()
        self.assertFalse(move.tms_unbilled_charge_ids)

    def test_hire_bill_elements(self):
        lr = self._lr()
        trip = self._trip(lr, fleet="market")
        memo = trip.hire_memo_ids
        self._deliver_and_verify(trip)
        memo.action_create_invoice()
        bill = memo.invoice_ids
        bill.invoice_date = fields.Date.today()
        bill.invoice_line_ids.price_unit = 30500.0
        bill.invalidate_recordset()
        hire = bill.tms_match_line_ids.filtered(lambda line: line.kind == "hire")
        self.assertEqual(hire.result_label, "Over tolerance")
        self.assertIn("Verified digital POD", bill.tms_pod_element)
        self.assertTrue(bill.tms_tds_note)
        hire.action_use_approved()
        bill.invalidate_recordset()
        self.assertEqual(bill.tms_match_ids.state, "passed")
        self.assertEqual(bill.tms_match_line_ids.filtered(lambda line: line.kind == "hire").result_label, "Matched")
        self.assertTrue(bill.message_ids.filtered(lambda m: "Three-way match" in (m.body or "")))

    def test_registers(self):
        lr = self._lr()
        trip = self._trip(lr)
        cockpit = self.env["tms.cockpit"]
        data = cockpit.get_compliance_register(self.company.id)
        plates = [v["plate"] for v in data["vehicles"]]
        self.assertIn("MH04LE7713", plates)
        row = next(v for v in data["vehicles"] if v["plate"] == "MH04LE7713")
        self.assertEqual(row["trip"], trip.name)
        self.assertIn("rc", row["cells"])
        self.assertTrue(data["drivers"])
        self.assertIn("The gate checks", data["legend"])
        exp = self.env["tms.trip.expense"].create({
            "trip_id": trip.id, "category_id": self.env.ref("tms_core.tms_category_unloading").id,
            "amount": 1200.0, "payment_mode": "driver_cash", "recoverable_from": "customer",
            "recoverable_amount": 1200.0})
        data = cockpit.get_charges_register(self.company.id)
        keys = [t["key"] for t in data["tiles"]]
        self.assertEqual(keys, ["customers", "vendors", "absorbed", "leakage"])
        self.assertIn(exp.name, [e["ref"] for e in data["expenses"]])
        row = next(e for e in data["expenses"] if e["ref"] == exp.name)
        self.assertEqual(row["leak"], not exp.charge_id)
        self.assertTrue(row["status"])

    def test_settings_ui(self):
        settings = self.env["res.config.settings"].with_company(self.company).create({})
        self.assertTrue(settings.tms_required_docs)
        self.assertEqual(settings.tms_allow_manual_lr, self.company.tms_allow_manual_lr)
