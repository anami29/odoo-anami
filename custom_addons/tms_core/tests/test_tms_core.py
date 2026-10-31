from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from .common import TmsCommon


@tagged("post_install", "-at_install")
class TestLr(TmsCommon):
    def test_lr_numbering_and_totals(self):
        """BR-MD-12, BR-LR-05, BR-LR-06, #23.1: branch series, charged weight rounded up to 10 kg."""
        lr = self._lr(weight=1234.0, rate=5.0)
        self.assertTrue(lr.name.startswith("BHW/LR/"))
        self.assertEqual(lr.charged_weight, 1240.0)
        self.assertEqual(lr.freight_amount, 6200.0)
        self.assertEqual(lr.state, "confirmed")
        self.assertEqual(lr.tms_mode, "hybrid")

    def test_billing_party_defaults(self):
        """BR-LR-02."""
        lr = self._lr(basis="topay", confirm=False)
        self.assertEqual(lr.billing_partner_id, self.consignee)
        lr2 = self._lr(basis="paid", confirm=False)
        self.assertEqual(lr2.billing_partner_id, self.customer)

    def test_confirm_needs_rate_or_item(self):
        """BR-LR-08."""
        lr = self._lr(confirm=False, rate_type=False, typed_rate=0.0)
        with self.assertRaises(UserError):
            lr.action_confirm()

    def test_charged_weight_override_needs_reason(self):
        lr = self._lr(confirm=False)
        with self.assertRaises(ValidationError):
            lr.write({"charged_weight_override": 12000.0})
        lr.write({"charged_weight_override": 12000.0, "charged_weight_reason": "Volumetric load"})
        self.assertEqual(lr.charged_weight, 12000.0)
        self.assertTrue(self.env["tms.override.log"].search([("kind", "=", "charged_weight"),
                                                             ("res_id", "=", lr.id)]))

    def test_locked_after_confirmation(self):
        """BR-LR-17: changes after confirmation only through an approved amendment."""
        lr = self._lr()
        with self.assertRaises(UserError):
            lr.consignee_id = self.customer
        with self.assertRaises(UserError):
            lr.line_ids.actual_weight = 5000.0
        amendment = self.env["tms.consignment.amendment"].with_user(self.u_clerk).create({
            "consignment_id": lr.id, "reason": "Consignee corrected", "new_consignee_id": self.customer.id,
            "new_actual_weight": 9990.0})
        with self.assertRaises(UserError):
            amendment.with_user(self.u_clerk).action_approve()
        amendment.with_user(self.u_manager).action_approve()
        self.assertEqual(lr.consignee_id, self.customer)
        self.assertEqual(lr.total_actual_weight, 9990.0)

    def test_cancel_rules(self):
        """BR-LR-11, BR-LR-18."""
        lr = self._lr()
        trip = self._trip(lr, start=False)
        trip.action_confirm()
        lr.invalidate_recordset()
        # vehicle not yet placed until an assignment exists: cancelling is still refused once a trip links it
        link = trip.consignment_link_ids
        link.with_context(tms_reason="Customer postponed").action_remove_from_trip()
        self.assertEqual(link.state, "cancel")
        dead = lr.charge_ids.filtered(lambda c: c.category_id.code == "DEADFREIGHT")
        self.assertEqual(dead.amount, lr.freight_amount)
        lr.action_cancel()
        self.assertEqual(lr.state, "cancel")

    def test_ewaybill_validity(self):
        """#23.5: 450 km generated on the 10th runs to midnight at the end of the 13th; ODC 720 km = 36 days."""
        lr = self._lr()
        generated = fields.Datetime.to_datetime("2026-10-10 10:00:00")
        bill = self.env["tms.ewaybill"].create({"name": "123456789012", "consignment_id": lr.id,
                                                "generated_on": generated, "distance_km": 450})
        self.assertEqual(bill.valid_until.date(), fields.Date.to_date("2026-10-13"))
        odc = self.env["tms.ewaybill"].create({"name": "123456789013", "consignment_id": lr.id,
                                               "generated_on": generated, "distance_km": 720, "is_odc": True})
        self.assertEqual((odc.valid_until.date() - generated.date()).days, 36)
        with self.assertRaises(ValidationError):
            self.env["tms.ewaybill"].create({"name": "123", "consignment_id": lr.id})

    def test_missing_ewaybill_blocks_start(self):
        """BR-EWB-03, BR-EWB-04."""
        lr = self._lr(value=90000.0)
        self.assertTrue(lr.ewaybill_missing)
        trip = self._trip(lr, start=False)
        trip.action_confirm()
        with self.assertRaises(UserError):
            trip.button_start_order()

    def test_credit_block_and_override(self):
        """BR-CRD-02, BR-CRD-03."""
        self.customer.tms_credit_limit = 100.0
        lr = self._lr(confirm=False)
        with self.assertRaises(UserError):
            lr.action_confirm()
        lr.with_user(self.u_manager).with_context(tms_credit_override_reason="Known customer").action_confirm()
        self.assertEqual(lr.state, "confirmed")
        self.assertTrue(self.env["tms.override.log"].search([("kind", "=", "credit")]))
        self.customer.tms_credit_limit = 1000000.0

    def test_paid_lr_outside_credit(self):
        """BR-CRD-04."""
        self.customer.tms_credit_limit = 1.0
        lr = self._lr(basis="paid")
        self.assertEqual(lr.state, "confirmed")
        self.customer.tms_credit_limit = 1000000.0


@tagged("post_install", "-at_install")
class TestTrip(TmsCommon):
    def test_full_owned_trip(self):
        """WF-02/03/05: confirm, gate, start, depart, deliver, POD, settle, close, invoice."""
        lr = self._lr()
        trip = self._trip(lr)
        self.assertEqual(trip.state_code, "started")
        self.assertEqual(trip.gate_status, "pass")
        self.assertEqual(trip.vehicle_id, self.vehicle)
        self.assertEqual(trip.primary_vehicle_id, self.vehicle)
        self.assertEqual(lr.state, "dispatched")
        self.assertEqual(lr.vehicle_number, "MH04LE7713")
        self.env["tms.trip.dsr"].create({"trip_id": trip.id, "location": "Nashik", "status": "departed"})
        self.assertEqual(trip.state_code, "in_transit")
        with self.assertRaises(ValidationError):
            self.env["tms.trip.dsr"].create({"trip_id": trip.id, "location": "Dhule", "status": "delayed_other"})
        self._deliver_and_verify(trip)
        self.assertEqual(trip.state_code, "delivered")
        self.assertEqual(lr.state, "delivered")
        self.assertEqual(lr.pod_status, "verified")
        # driver advance and settlement (UC-11: 10,000 − 9,200 + 300 = 1,100)
        adv = self.env["tms.driver.advance"].create({"trip_id": trip.id, "driver_id": self.driver.id,
                                                     "amount": 10000.0})
        adv.action_post()
        fuel = self.env.ref("tms_core.tms_category_fuel")
        exp = self.env["tms.trip.expense"].create({"trip_id": trip.id, "category_id": fuel.id, "amount": 9200.0,
                                                   "payment_mode": "driver_cash", "receipt": b"UkNQ",
                                                   "fuel_litres": 100.0})
        with self.assertRaises(UserError):
            exp.with_user(self.u_dispatcher).action_approve()
        exp.with_user(self.u_accountant).action_approve()
        self.assertEqual(exp.state, "approved")
        self.assertEqual(exp.vehicle_id, self.vehicle)
        fine = self.env.ref("tms_core.tms_category_traffic_fine")
        charge = self.env["tms.trip.charge"].create({"trip_id": trip.id, "category_id": fine.id, "amount": 300.0,
                                                     "recover_from": "driver"})
        charge.action_confirm()
        self.assertEqual(charge.state, "approved")
        self.assertTrue(charge.self_approved)
        with self.assertRaises(UserError):
            trip.action_done()  # settlement and odometer missing
        settlement = self.env["tms.driver.settlement"].create({"trip_id": trip.id, "driver_id": self.driver.id})
        self.assertEqual(settlement.balance, 1100.0)
        with self.assertRaises(UserError):
            settlement.with_user(self.u_dispatcher).action_done()
        settlement.with_user(self.u_accountant).action_done()
        self.assertEqual(charge.state, "deducted")
        trip.end_odometer = 10590.0
        trip.action_done()
        self.assertEqual(trip.state_code, "done")
        self.assertEqual(trip.own_km, 590.0)
        # invoice the To-Be-Billed LR (BR-INV-03)
        moves = self.env["tms.invoice.lr.wizard"]._tms_create_invoices(lr)
        self.assertEqual(moves.tms_gst_mode, "fcm5")
        self.assertAlmostEqual(moves.amount_untaxed, lr.freight_amount)
        moves.action_post()
        self.assertEqual(lr.state, "invoiced")

    def test_stage_drag_blocked(self):
        """BR-EXE-02."""
        trip = self._trip(self._lr(), start=False)
        with self.assertRaises(UserError):
            trip.stage_id = self.env["tms.stage"].get_stage("done")
        with self.assertRaises(UserError):
            trip.vehicle_id = self.vehicle2

    def test_gate_failure(self):
        """BR-CMP-03, BR-CMP-06: an expiring PUC blocks START."""
        puc = self.vehicle.tms_compliance_document_ids.filtered(lambda d: d.type_code == "PUC")
        puc.valid_until = fields.Date.today()
        lr = self._lr()
        trip = self._trip(lr, start=False, scheduled_duration=72.0)
        trip.action_confirm()
        with self.assertRaises(UserError):
            trip.button_start_order()
        puc.valid_until = fields.Date.today() + timedelta(days=300)

    def test_enforcement_off_flags(self):
        """BR-CMP-07, BR-CFG-06."""
        with self.assertRaises(UserError):
            self.company.with_user(self.u_dispatcher).sudo(False).write({"tms_enforce_compliance": False})
        # on a live database the TMS Administrator also holds Settings rights (README #4)
        self.u_admin.groups_id = [Command.link(self.env.ref("base.group_system").id)]
        admin = self.company.with_user(self.u_admin).sudo(False)
        with self.assertRaisesRegex(UserError, "reason"):  # UT-CFG-14: a switch needs its reason
            admin.write({"tms_enforce_compliance": False})
        settings = self.env["res.config.settings"].with_company(self.company)
        settings.create({"tms_enforce_compliance": False, "tms_enforce_reason": "Portal outage"})
        self.assertFalse(self.company.tms_enforce_compliance)
        log = self.env["tms.override.log"].search([("kind", "=", "enforcement")], order="id desc", limit=1)
        self.assertEqual(log.reason, "Portal outage")
        puc = self.vehicle.tms_compliance_document_ids.filtered(lambda d: d.type_code == "PUC")
        puc.valid_until = fields.Date.today() - timedelta(days=1)
        trip = self._trip(self._lr())
        self.assertTrue(trip.started_without_gate)
        self.assertEqual(trip.open_assignment_id.gate_result, "not_enforced")
        admin.with_context(tms_reason="Portal restored").write({"tms_enforce_compliance": True})
        puc.valid_until = fields.Date.today() + timedelta(days=300)

    def test_one_open_assignment_per_vehicle(self):
        """BR-ASG-14."""
        self._trip(self._lr())
        trip2 = self._trip(self._lr(), start=False)
        with self.assertRaises(UserError):
            trip2.action_confirm()

    def test_payload(self):
        """BR-TRP-05."""
        lr = self._lr(weight=30000.0)
        trip = self._trip(lr, start=False)
        with self.assertRaises(UserError):
            trip.action_confirm()
        trip.payload_override_reason = "Light cargo, axle within limit"
        trip.with_user(self.u_ops).action_approve_payload()
        trip.action_confirm()
        self.assertEqual(trip.state_code, "confirmed")

    def test_pod_self_verify_refused(self):
        """BR-POD-03."""
        trip = self._trip(self._lr())
        link = trip.consignment_link_ids
        link.action_mark_delivered()
        link.write({"pod_file": b"UE9E"})
        link.with_user(self.u_manager).action_upload_pod()
        with self.assertRaises(UserError):
            link.with_user(self.u_manager).action_verify_pod()
        with self.assertRaises(UserError):
            link.with_user(self.u_dispatcher).action_verify_pod()
        link.with_user(self.u_verifier).action_verify_pod()
        self.assertEqual(link.pod_status, "verified")

    def test_shortage_creates_claim(self):
        """BR-POD-04, BR-CLM-04..08."""
        lr = self._lr()
        trip = self._trip(lr)
        self._deliver_and_verify(trip, shortage_qty=2)
        claim = lr.claim_ids
        self.assertEqual(len(claim), 1)
        self.assertEqual(claim.damage_type, "shortage")
        self.assertTrue(claim.incident_id)
        claim.write({"estimated_amount": 800.0, "notice_date": fields.Date.today()})
        claim.with_user(self.u_dispatcher).action_submit()
        with self.assertRaises(UserError):
            claim.with_user(self.u_dispatcher).action_approve()
        claim.with_user(self.u_manager).action_approve()
        self.assertEqual(claim.state, "approved")
        self.assertEqual(claim.credit_note_id.move_type, "out_refund")
        self.assertFalse(claim.credit_note_id.invoice_line_ids.tax_ids)
        claim.credit_note_id.action_post()
        self.assertEqual(claim.state, "settled")

    def test_claim_cap(self):
        """BR-CLM-10."""
        lr = self._lr(value=1000.0)
        trip = self._trip(lr)
        self._deliver_and_verify(trip, damage_qty=1)
        claim = lr.claim_ids
        claim.write({"estimated_amount": 5000.0})
        claim.action_submit()
        with self.assertRaises(UserError):
            claim.with_user(self.u_ops).action_approve()
        claim.cap_override_reason = "Court order"
        claim.with_user(self.u_ops).action_override_cap()
        claim.with_user(self.u_ops).action_approve()
        self.assertEqual(claim.approved_amount, 5000.0)

    def test_owner_risk_needs_reason(self):
        """BR-CLM-09, BR-AUD-06: accepting carrier liability asks for and logs a reason (UT-CLM-15, 16)."""
        lr = self._lr(risk_basis="owner")
        trip = self._trip(lr)
        self._deliver_and_verify(trip, damage_qty=1)
        claim = lr.claim_ids
        claim.write({"estimated_amount": 500.0})
        claim.action_submit()
        with self.assertRaises(UserError):
            claim.with_user(self.u_manager).action_approve()
        with self.assertRaises(UserError):
            claim.with_user(self.u_ops).action_accept_owner_risk()
        action = claim.with_user(self.u_ops).action_open_accept_owner_risk()
        wiz = self.env["tms.reason.wizard"].with_user(self.u_ops).with_context(action["context"]).create(
            {"reason": "Driver negligence admitted"})
        wiz.action_confirm()
        self.assertEqual(claim.owner_risk_accepted_by_id, self.u_ops)
        log = self.env["tms.override.log"].search([("res_model", "=", "tms.damage.claim"), ("res_id", "=", claim.id), ("kind", "=", "claim")], limit=1)
        self.assertEqual(log.reason, "Driver negligence admitted")
        claim.with_user(self.u_manager).action_approve()
        self.assertEqual(claim.state, "approved")

    def test_breakdown_replacement_own(self):
        """BR-ASG-04..12: breakdown, gate on the replacement, append-only assignments, Part-B tasks."""
        lr = self._lr()
        bill = self.env["tms.ewaybill"].create({"name": "123456789099", "consignment_id": lr.id,
                                                "distance_km": 800})
        lr.line_ids.ewaybill_id = bill
        trip = self._trip(lr, start=False)
        trip.action_confirm()
        trip.ewaybill_update_ids.write({"evidence_reference": "PORTAL-1"})
        trip.ewaybill_update_ids.action_done()
        trip.button_start_order()
        bd = self.env["tms.trip.breakdown"].create({"trip_id": trip.id, "location": "Malegaon"})
        self.assertTrue(bd.service_log_id)
        self.assertEqual(bd.incident_id.category_id.code, "BREAKDOWN")
        wiz = self.env["tms.breakdown.resolve.wizard"].create({
            "breakdown_id": bd.id, "resolution": "replace_own", "vehicle_id": self.vehicle2.id,
            "driver_id": self.driver2.id, "odometer_at_breakdown": 10300.0, "replacement_odometer": 500.0})
        wiz.action_resolve()
        self.assertEqual(len(trip.assignment_ids), 2)
        self.assertEqual(trip.vehicle_id, self.vehicle2)
        self.assertEqual(trip.primary_vehicle_id, self.vehicle)
        self.assertEqual(trip.own_km, 300.0)
        self.assertEqual(bd.state, "resolved")
        self.assertIn("MH04LE7713", lr.vehicle_history)
        pending = trip.ewaybill_update_ids.filtered(lambda t: t.state == "pending")
        self.assertTrue(pending)
        with self.assertRaises(UserError):
            trip.action_depart()
        with self.assertRaises(UserError):
            trip.assignment_ids[0].unlink()

    def test_detention_charge(self):
        """BR-CHG-09 acceptance: 4 free hours, 2,000 a day, unloading 30 hours past arrival -> 2,000."""
        lr = self._lr()
        lr.billing_partner_id.commercial_partner_id.write({
            "tms_free_hours_unloading": 4.0, "tms_detention_rate": 2000.0})
        trip = self._trip(lr)
        now = fields.Datetime.now()
        self.env["tms.trip.dsr"].create({"trip_id": trip.id, "location": "Nagpur", "status": "at_unloading",
                                         "logged_at": now - timedelta(hours=30)})
        link = trip.consignment_link_ids
        link.delivered_at = now
        link.action_mark_delivered()
        detention = trip.charge_ids.filtered(lambda c: c.category_id.code == "DETENTION")
        self.assertEqual(detention.state, "draft")
        self.assertAlmostEqual(detention.amount, 2000.0, places=2)

    def test_detention_per_lr(self):
        """BR-CHG-09: on a trip carrying two customers, detention goes to the customer whose stop waited."""
        other = self.customer.copy({"name": "Satara Mills", "tms_detention_rate": 3000.0,
                                    "tms_free_hours_unloading": 2.0})
        self.customer.write({"tms_free_hours_unloading": 4.0, "tms_detention_rate": 2000.0})
        lr1 = self._lr(weight=4000.0)
        lr2 = self._lr(weight=4000.0, consignor_id=other.id)
        trip = self._trip(lr1 | lr2)
        now = fields.Datetime.now()
        self.env["tms.trip.dsr"].create({"trip_id": trip.id, "location": "Satara", "status": "at_unloading",
                                         "consignment_id": lr2.id, "logged_at": now - timedelta(hours=28)})
        for link in trip.consignment_link_ids:
            link.delivered_at = now
            link.action_mark_delivered()
        detention = trip.charge_ids.filtered(lambda c: c.category_id.code == "DETENTION")
        self.assertEqual(len(detention), 1)
        self.assertEqual(detention.consignment_id, lr2)
        self.assertEqual(detention.partner_id, other)
        self.assertAlmostEqual(detention.amount, 3000.0, places=2)

    def test_print_second_language(self):
        """BR-PRN-08, UT-PRN-09: with Marathi set on the branch, the LR and POD form carry both languages."""
        lr = self._lr()
        trip = self._trip(lr)
        Report = self.env["ir.actions.report"]
        html = str(Report._render_qweb_html("tms_core.report_lr", lr.ids)[0], "utf-8")
        self.assertIn("Consignor", html)
        self.assertNotIn("प्रेषक", html)
        marathi = self.env.ref("tms_core.lang_mr_IN")
        self.company.tms_second_language_id = marathi
        html = str(Report._render_qweb_html("tms_core.report_lr", lr.ids)[0], "utf-8")
        self.assertIn("Consignor / प्रेषक", html)
        self.assertIn("DRIVER COPY / चालक प्रत", html)
        pod = str(Report._render_qweb_html("tms_core.report_pod_form", trip.consignment_link_ids.ids)[0], "utf-8")
        self.assertIn("Packages received / मिळालेले नग", pod)

    def test_charge_needs_bm(self):
        """BR-CHG-04."""
        trip = self._trip(self._lr())
        cat = self.env.ref("tms_core.tms_category_loading")
        charge = self.env["tms.trip.charge"].with_user(self.u_dispatcher).create({
            "trip_id": trip.id, "consignment_id": trip.consignment_ids.id, "category_id": cat.id,
            "amount": 8000.0, "recover_from": "customer"})
        charge.with_user(self.u_dispatcher).action_confirm()
        self.assertEqual(charge.state, "to_approve")
        with self.assertRaises(UserError):
            charge.with_user(self.u_dispatcher).action_approve()
        charge.with_user(self.u_manager).action_approve()
        self.assertEqual(charge.state, "approved")

    def test_mode_lock(self):
        """BR-CFG-04."""
        self._lr()
        self.assertTrue(self.company.tms_mode_locked)
        with self.assertRaises(UserError):
            self.company.tms_operation_mode = "broker"
        self.company.with_user(self.u_admin).action_tms_unlock_mode("Restructure")
        self.company.tms_operation_mode = "broker"
        self.assertEqual(self.company.tms_operation_mode, "broker")
        with self.assertRaises(UserError):
            self.company.tms_operation_mode = "hybrid"
        self.company.with_user(self.u_admin).action_tms_unlock_mode("Back to hybrid")
        self.company.tms_operation_mode = "hybrid"


@tagged("post_install", "-at_install")
class TestHire(TmsCommon):
    def test_market_trip_three_way_match(self):
        """BR-HIR-01..09, #23.2: a ₹30,000 hire allows ₹150; a ₹30,200 bill is blocked."""
        lr = self._lr()
        trip = self._trip(lr, fleet="market")
        memo = trip.hire_memo_ids
        self.assertEqual(memo.order_line.tms_item_no, 10)
        self.assertEqual(trip.open_assignment_id.hire_memo_id, memo)
        # advance capped at 70% (vendor) of 30,000
        adv = self.env["tms.hire.advance"].create({"purchase_id": memo.id, "kind": "bank", "amount": 22000.0})
        with self.assertRaises(UserError):
            adv.action_post()
        adv.amount = 20000.0
        adv.action_post()
        self.assertEqual(memo.tms_advance_total, 20000.0)
        # the hire is received only from verified PODs
        with self.assertRaises(UserError):
            memo.order_line.qty_received = 1.0
        self._deliver_and_verify(trip)
        self.assertEqual(memo.order_line.qty_received, 1.0)
        memo.action_create_invoice()
        bill = memo.invoice_ids
        bill.invoice_date = fields.Date.today()
        bill.invoice_line_ids.price_unit = 30200.0
        with self.assertRaises(UserError):
            bill.action_post()
        match = bill.tms_match_ids
        self.assertEqual(match.state, "blocked")
        self.assertEqual(match.line_ids.tolerance, 150.0)
        with self.assertRaises(UserError):
            match.with_user(self.u_accountant).with_context(tms_reason="x").action_override()
        match.with_user(self.u_finance).with_context(tms_reason="Agreed loading delay").action_override()
        bill.action_post()
        self.assertEqual(match.state, "posted")
        self.assertEqual(bill.invoice_line_ids.tms_trip_id, trip)
        self.assertEqual(bill.invoice_line_ids.tms_cost_class, "trip_direct")

    def test_vendor_recovery_deducted(self):
        """BR-CHG-06, BR-HIR-09."""
        lr = self._lr()
        trip = self._trip(lr, fleet="market")
        memo = trip.hire_memo_ids
        self._deliver_and_verify(trip)
        cat = self.env.ref("tms_core.tms_category_damage_recovery")
        charge = self.env["tms.trip.charge"].create({"trip_id": trip.id, "category_id": cat.id,
                                                     "amount": 2000.0, "recover_from": "vendor"})
        self.assertEqual(charge.partner_id, self.vendor)
        charge.action_confirm()
        memo.action_create_invoice()
        bill = memo.invoice_ids
        bill.invoice_date = fields.Date.today()
        match = self.env["tms.vendor.bill.match"].create({"move_id": bill.id, "purchase_id": memo.id})
        match._tms_refresh()
        self.assertEqual(match.state, "blocked")  # recovery not yet on the bill
        match.action_apply_recoveries()
        self.assertEqual(match.state, "passed")
        bill.action_post()
        self.assertEqual(charge.state, "deducted")
        self.assertAlmostEqual(bill.amount_untaxed, 28000.0)

    def test_branch_manager_approves_extra(self):
        """BR-HIR-02, BR-CHG-09, UT-HIR-03: the Branch Manager approves a halting extra in the screen's own
        access rights; it raises a draft customer detention charge, and a waiver reason raises none."""
        lr = self._lr()
        trip = self._trip(lr, fleet="market")
        memo = trip.hire_memo_ids[:1]
        extra = self.env.ref("tms_core.product_hire_extra")
        memo.write({"order_line": [
            Command.create({"product_id": extra.id, "product_qty": 1, "price_unit": 2000.0,
                            "tms_hire_element": "halting"}),
            Command.create({"product_id": extra.id, "product_qty": 1, "price_unit": 1500.0,
                            "tms_hire_element": "halting", "tms_waiver_reason": "Customer delay disputed"})]})
        first, second = memo.order_line.filtered(lambda line: line.tms_hire_element == "halting")
        with self.assertRaises(UserError):
            first.with_user(self.u_dispatcher).action_tms_approve_extra()
        (first | second).with_user(self.u_manager).action_tms_approve_extra()
        self.assertTrue(first.tms_extra_approved and second.tms_extra_approved)
        self.assertEqual(first.tms_extra_approved_by_id, self.u_manager)
        self.assertEqual(first.tms_charge_id.state, "draft")
        self.assertAlmostEqual(first.tms_charge_id.amount, 2000.0)
        self.assertFalse(second.tms_charge_id)
        self.assertTrue(self.env["tms.override.log"].search([("kind", "=", "waiver"), ("res_id", "=", memo.id)]))

    def test_fleet_manager_creates_routes(self):
        """BR-MD-02, UT-MD-03."""
        route = self.env["tms.route"].with_user(self.u_fleet).create({
            "name": "Bhiwandi–Nagpur", "origin_location_id": self.loc_bhiwandi.id,
            "destination_location_id": self.loc_nagpur.id})
        self.assertTrue(route)

    def test_finance_head_keeps_gta_options(self):
        """BR-CFG-10, UT-CFG-22."""
        Option = self.env["tms.gta.option"].with_user(self.u_finance)
        today = fields.Date.today()
        with self.assertRaises(ValidationError):
            Option.create({"company_id": self.company.id, "date_from": today, "date_to": today + timedelta(days=30),
                           "option": "rcm"})
        with self.assertRaises(ValidationError):
            Option.create({"company_id": self.company.id, "date_from": today + timedelta(days=900),
                           "date_to": today + timedelta(days=800), "option": "rcm"})
        menu = self.env.ref("tms_core.menu_tms_gta_option")
        self.assertIn(menu.id, self.env["ir.ui.menu"].with_user(self.u_finance)._visible_menu_ids())

    def test_menus_grouped_by_activity(self):
        """UT-CFG-23: Operations, Trip money and Reporting hold only group headings, each holding its items."""
        ref = self.env.ref
        for top in ("tms.operations", "tms_core.menu_tms_finance", "tms.reporting"):
            headings = ref(top).child_id
            self.assertTrue(headings, top)
            self.assertFalse(headings.filtered("action"), "%s has an item outside a group" % top)
        expected = {
            "tms_core.menu_tms_lr": "tms_core.menu_tms_ops_booking",
            "tms_core.menu_tms_ewaybill": "tms_core.menu_tms_ops_booking",
            "tms.menu_tms_operation_trip": "tms_core.menu_tms_ops_trips",
            "tms_core.menu_tms_pod": "tms_core.menu_tms_ops_trips",
            "tms_core.menu_tms_dsr": "tms_core.menu_tms_ops_trips",
            "tms_core.menu_tms_incident": "tms_core.menu_tms_ops_exceptions",
            "tms_core.menu_tms_claim": "tms_core.menu_tms_ops_exceptions",
            "tms.menu_fleet_odometers": "tms_core.menu_tms_ops_fleet",
            "tms_core.menu_tms_hire_memo": "tms_core.menu_tms_money_hire",
            "tms_core.menu_tms_report_lr": "tms_core.menu_tms_rep_registers",
            "tms_core.menu_tms_report_tds": "tms_core.menu_tms_rep_ageing",
            "tms.menu_tms_report_trip": "tms_core.menu_tms_rep_fleet",
        }
        for item, heading in expected.items():
            self.assertEqual(ref(item).parent_id, ref(heading), item)
        visible = self.env["ir.ui.menu"].with_user(self.u_clerk)._visible_menu_ids()
        self.assertIn(ref("tms_core.menu_tms_ops_booking").id, visible)
        self.assertIn(ref("tms_core.menu_tms_lr").id, visible)

    def test_hire_memo_needs_pan_and_bank(self):
        """BR-HIR-03."""
        vendor = self.env["res.partner"].create({"name": "No PAN carrier"})
        memo = self.env["purchase.order"].create({
            "partner_id": vendor.id, "tms_is_hire_memo": True,
            "order_line": [Command.create({"product_id": self.env.ref("tms_core.product_vehicle_hire").id,
                                           "product_qty": 1, "price_unit": 1000, "tms_hire_element": "hire"})]})
        with self.assertRaises(UserError):
            memo.button_confirm()

    def test_item_numbering(self):
        """BR-NUM-01, BR-NUM-02."""
        product = self.env.ref("tms_core.product_vehicle_hire")
        po = self.env["purchase.order"].create({"partner_id": self.vendor.id, "order_line": [
            Command.create({"product_id": product.id, "product_qty": 1, "price_unit": 1}),
            Command.create({"product_id": product.id, "product_qty": 1, "price_unit": 1})]})
        self.assertEqual(po.order_line.mapped("tms_item_no"), [10, 20])
        po.order_line[1].unlink()
        po.write({"order_line": [Command.create({"product_id": product.id, "product_qty": 1, "price_unit": 1})]})
        self.assertEqual(sorted(po.order_line.mapped("tms_item_no")), [10, 30])
        po.write({"order_line": [Command.create({"product_id": product.id, "product_qty": 1, "price_unit": 1,
                                                 "tms_item_no": 15})]})
        with self.assertRaises(UserError):
            po.order_line.filtered(lambda line: line.tms_item_no == 15).tms_item_no = 25
        with self.assertRaises(ValidationError):
            po.write({"order_line": [Command.create({"product_id": product.id, "product_qty": 1, "price_unit": 1,
                                                     "tms_item_no": 15})]})


@tagged("post_install", "-at_install")
class TestAudit(TmsCommon):
    def test_override_log_read_only(self):
        log = self.env["tms.override.log"].log("other", self.company, "test", reason="r")
        with self.assertRaises(UserError):
            log.write({"reason": "x"})
        with self.assertRaises(UserError):
            log.unlink()

    def test_kpis_compute(self):
        self._trip(self._lr())
        for kpi in self.env["tms.kpi"].search([]):
            kpi._compute_value()
            if kpi.available:
                self.assertIsInstance(kpi.action_drill(), dict)
