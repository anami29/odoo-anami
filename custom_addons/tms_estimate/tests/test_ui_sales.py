"""Sales screens aligned to the agreed mockup in 18.0.1.6.0: pipeline cards, the opportunity, the freight
quotation's rate approval, rejection and acceptance, and the cost estimate's summary and cost booking."""

import base64

from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import tagged

from .test_tms_estimate import EstimateCommon


@tagged("post_install", "-at_install")
class TestUiSales(EstimateCommon):

    def _opportunity(self):
        return self.env["crm.lead"].create({
            "name": "Taloja outbound FTL", "type": "opportunity", "partner_id": self.customer.id,
            "tms_lane_ids": [
                Command.create({"origin_id": self.loc_bhiwandi.id, "destination_id": self.loc_nagpur.id,
                                "loads_per_month": 10, "target_rate": 40000}),
                Command.create({"origin_id": self.loc_bhiwandi.id, "destination_id": self.consignee.id,
                                "loads_per_month": 4, "target_rate": 25000}),
            ]})

    # ------------------------------------------------------------------ pipeline and opportunity
    def test_opportunity_lanes(self):
        lead = self._opportunity()
        if lead.stage_id == self.env.ref("crm.stage_lead1"):
            self.assertEqual(lead.probability, 10.0)
        self.assertTrue(lead.tms_lane_summary.startswith("2 lanes · Bhiwandi → Nagpur"))
        self.assertEqual(lead.tms_lane_loads, 14)
        self.assertEqual(lead.expected_revenue, 500000 * 12)
        self.assertIn("₹5,00,000.00 × 12 = ₹60,00,000.00", lead.tms_lane_formula)
        lead.stage_id = self.env.ref("crm.stage_lead2")
        self.assertEqual(lead.probability, 40.0)
        ctx = lead._prepare_opportunity_quotation_context()
        lines = ctx["default_order_line"]
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0][2]["tms_is_movement"])
        self.assertEqual(lines[0][2]["product_uom_qty"], 10)
        order = self.env["sale.order"].with_context(**ctx).create({"partner_id": self.customer.id})
        self.assertEqual(len(order.order_line), 2)
        self.assertTrue(all(order.order_line.mapped("tms_estimate_id")))
        lead.invalidate_recordset(["tms_order_id", "tms_order_badge"])
        self.assertEqual(lead.tms_order_id, order)
        self.assertIn(order.name, lead.tms_order_badge)

    def test_match_note_and_lost_reason(self):
        lead = self._opportunity()
        lead.tms_vat = self.customer.vat
        self.assertEqual(lead.tms_match_note, "Existing customer · matched by GSTIN")
        reasons = self.env["crm.lost.reason"].search([]).mapped("name")
        for name in ("Rate too high", "Lane not served", "Credit terms not accepted"):
            self.assertIn(name, reasons)
        wiz = self.env["crm.lead.lost"].create({"lead_ids": [Command.set(lead.ids)]})
        with self.assertRaises(UserError):
            wiz.action_lost_reason_apply()

    # ------------------------------------------------------------------ quotation
    def test_rate_approval_reject_and_request(self):
        order = self._order(km=100.0)
        line = order.order_line
        est = line.tms_estimate_id
        self.env["tms.estimate.line"].with_user(self.u_planner).create(
            {"estimate_id": est.id, "product_id": self.p_toll.id, "markup_pct": 5.0})
        order._tms_refresh_approvals()
        self.assertEqual(order.tms_rate_approval, "required")
        self.assertIn("below the markup floor", order.tms_approval_banner)
        order.action_tms_request_approval()
        self.assertEqual(order.tms_rate_approval, "pending")
        with self.assertRaises(UserError):
            order.with_user(self.u_dispatcher).with_context(tms_reason="No").action_tms_reject_with_reason()
        order.with_user(self.u_manager).with_context(tms_reason="Keep within 5% of the card").action_tms_reject_with_reason()
        self.assertEqual(line.tms_approval_state, "rejected")
        self.assertEqual(order.tms_rate_approval, "rejected")
        self.assertIn("Keep within 5% of the card", order.tms_approval_banner)
        self.assertTrue(order.tms_needs_approval)
        with self.assertRaises(UserError):
            order.action_quotation_sent()
        # the same rates stay rejected; changed rates need a new request
        order._tms_refresh_approvals()
        self.assertEqual(line.tms_approval_state, "rejected")
        est.line_ids.filtered(lambda x: x.product_id == self.p_toll).markup_pct = 6.0
        order._tms_refresh_approvals()
        self.assertEqual(line.tms_approval_state, "to_approve")
        self.assertEqual(order.tms_rate_approval, "required")
        order.action_tms_request_approval()
        order.with_user(self.u_manager).action_tms_approve_rates()
        self.assertEqual(order.tms_rate_approval, "approved")
        self.assertIn("Rates approved by", order.tms_approval_banner)

    def test_customer_master_and_acceptance(self):
        order = self._order(km=100.0, tms_acceptance_file=False, tms_acceptance_name=False)
        self.assertTrue(order.tms_master_ok, order.tms_master_status)
        self.assertEqual(order.tms_master_status, "Complete")
        self.env["tms.estimate.line"].create({"estimate_id": order.order_line.tms_estimate_id.id,
                                              "product_id": self.p_survey.id})
        action = order.action_tms_confirm()
        self.assertEqual(action["res_model"], "tms.quotation.acceptance.wizard")
        wiz = self.env["tms.quotation.acceptance.wizard"].with_context(**action["context"]).create({
            "file": base64.b64encode(b"PO 4500021877"), "file_name": "PO_4500021877.pdf",
            "accepted_by": "Mahesh Iyer"})
        wiz.action_attach_confirm()
        self.assertEqual(order.state, "sale")
        self.assertEqual(order.tms_acceptance_from, "Mahesh Iyer")
        bare = self.env["res.partner"].create({"name": "Bare customer"})
        other = self._order(km=50.0)
        other.partner_id = bare
        self.assertFalse(other.tms_master_ok)
        self.assertIn("PAN", other.tms_master_status)

    # ------------------------------------------------------------------ cost estimate
    def test_estimate_summary_and_booking(self):
        order = self._order(km=100.0)
        est = order.order_line.tms_estimate_id
        eline = self.env["tms.estimate.line"].create({"estimate_id": est.id, "product_id": self.p_survey.id})
        order.action_confirm()
        self.assertTrue(est.locked_on)
        self.assertIn("Total distance", est.summary_html)
        self.assertIn("Estimate locked", est.subtitle)
        trip = self._trip(self._lr(), start=False)
        expense = self.env["tms.trip.expense"].create({
            "trip_id": trip.id, "category_id": self.env.ref("tms_core.tms_category_toll").id,
            "amount": eline.estimated_cost + 5000.0, "payment_mode": "driver_cash"})
        wiz = self.env["tms.estimate.book.wizard"].create({
            "estimate_id": est.id, "source": "expense", "expense_id": expense.id, "estimate_line_id": eline.id})
        self.assertIn("over its estimate", wiz.warning)
        wiz.action_book()
        self.assertEqual(expense.estimate_line_id, eline)
        self.assertIn(expense.name, eline.booked_html)
        self.assertRegex(eline.with_context(tms_show_remaining=True).display_name, r"(left|over)$")
