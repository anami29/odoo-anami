from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .common import TmsCommon


@tagged("post_install", "-at_install")
class TestRemoteGuard(TmsCommon):
    """BR-AUD-06, BR-BRN-07, BR-BRN-08: approvals and statuses move only through their actions."""

    def _guard(self, user):
        return self.env["tms.rpc.guard"].with_user(user)

    def test_status_write_refused(self):
        trip = self._trip(self._lr())
        cat = self.env.ref("tms_core.tms_category_loading")
        charge = self.env["tms.trip.charge"].create({
            "trip_id": trip.id, "consignment_id": trip.consignment_ids.id, "category_id": cat.id,
            "amount": 8000.0, "recover_from": "customer"})
        with self.assertRaises(AccessError):
            self._guard(self.u_dispatcher)._tms_check_call(
                "tms.trip.charge", "write", [charge.ids, {"state": "approved"}], {})
        # nested: a POD status sent inside the trip form's one2many
        link = trip.consignment_link_ids
        with self.assertRaises(AccessError):
            self._guard(self.u_dispatcher)._tms_check_call(
                "tms.order", "web_save", [trip.ids, {"consignment_link_ids": [[1, link.id, {"pod_status": "verified"}]]},
                                          {}], {})
        # an ordinary field passes
        self._guard(self.u_dispatcher)._tms_check_call(
            "tms.trip.charge", "write", [charge.ids, {"note": "Loading delay"}], {})
        # importing a status column is refused
        with self.assertRaises(AccessError):
            self._guard(self.u_dispatcher)._tms_check_call("tms.trip.charge", "load", [["name", "state"], []], {})

    def test_create_and_delete_roles(self):
        with self.assertRaises(AccessError):
            self._guard(self.u_dispatcher)._tms_check_call("tms.consignment", "create", [{"consignor_id": 1}], {})
        self._guard(self.u_clerk)._tms_check_call("tms.consignment", "web_save", [[], {"consignor_id": 1}, {}], {})
        with self.assertRaises(AccessError):
            self._guard(self.u_clerk)._tms_check_call("tms.consignment", "unlink", [[1]], {})
        self._guard(self.u_admin)._tms_check_call("tms.consignment", "unlink", [[1]], {})

    def test_internal_context_stripped(self):
        kwargs = {"context": {"tms_credit_override_reason": "free pass", "tms_reason_method": "action_reject",
                              "lang": "en_US"}}
        self._guard(self.u_clerk)._tms_check_call("tms.consignment", "action_confirm", [[1]], kwargs)
        self.assertNotIn("tms_credit_override_reason", kwargs["context"])
        self.assertIn("tms_reason_method", kwargs["context"])
        self.assertIn("lang", kwargs["context"])

    def test_reason_wizard_runs_public_actions_only(self):
        lr = self._lr()
        wiz = self.env["tms.reason.wizard"].create({"res_model": "tms.consignment", "res_ids": str(lr.id),
                                                     "method": "_tms_refresh_state", "reason": "x"})
        with self.assertRaises(UserError):
            wiz.action_confirm()


@tagged("post_install", "-at_install")
class TestTaxes(TmsCommon):
    def test_inter_state_freight_igst(self):
        """BR-INV-05: a customer in another state gets IGST through the l10n_in fiscal position."""
        customer = self.customer.copy({"name": "Gujarat Ceramics", "vat": "24AAACG1234A1Z5",
                                       "state_id": self.env.ref("base.state_in_gj").id})
        lr = self._lr(basis="paid", consignor_id=customer.id, billing_partner_id=customer.id)
        moves = self.env["tms.invoice.lr.wizard"]._tms_create_invoices(lr)
        tax = moves.invoice_line_ids.tax_ids
        self.assertEqual(len(tax), 1)
        self.assertEqual(tax.amount_type, "percent")
        self.assertIn("IGST", tax.name)
        intra = self._lr(basis="paid")
        moves = self.env["tms.invoice.lr.wizard"]._tms_create_invoices(intra)
        self.assertEqual(moves.invoice_line_ids.tax_ids.amount_type, "group")

    def _hire_bill(self, hire, vendor_vals=None, commission=0.0):
        if vendor_vals:
            self.vendor.write(vendor_vals)
        lr = self._lr()
        trip = self._trip(lr, fleet="market", start=False, planned_hire_amount=hire)
        memo = trip.hire_memo_ids
        if commission:
            memo.write({"order_line": [Command.create({
                "product_id": self.env.ref("tms_core.product_vehicle_hire").id, "name": "Broker commission",
                "product_qty": 1, "price_unit": commission, "tms_hire_element": "commission"})]})
        trip.action_confirm()
        trip.button_start_order()
        self._deliver_and_verify(trip)
        memo.action_create_invoice()
        bill = memo.invoice_ids
        bill.invoice_date = fields.Date.today()
        bill.action_post()
        return bill

    def test_hire_tds_above_threshold(self):
        """BR-HIR-10, BR-HIR-12, BR-HIR-13: TDS above the per-payment limit; commission under its section."""
        bill = self._hire_bill(50000.0, commission=25000.0)
        withhold = bill.l10n_in_withhold_move_ids
        self.assertTrue(withhold)
        sections = withhold.line_ids.tax_ids.mapped("l10n_in_section_id.name")
        self.assertTrue(any("SI.6" in s for s in sections), sections)
        self.assertTrue(any("SI.1(ii)" in s for s in sections), sections)
        self.assertEqual(bill.payment_state in ("partial", "in_payment", "paid", "not_paid"), True)

    def test_hire_tds_declaration_exempt(self):
        """BR-HIR-10, BR-HIR-14: a declaration on file exempts the hire; the extract lists it."""
        today = fields.Date.today()
        self.env["tms.tds.declaration"].create({"partner_id": self.vendor.id, "date_from": today - timedelta(days=30),
                                                "date_to": today + timedelta(days=300), "vehicle_count": 4,
                                                "reference": "DECL-1"})
        bill = self._hire_bill(50000.0)
        self.assertFalse(bill.l10n_in_withhold_move_ids)
        self.assertTrue(bill.tms_tds_declaration_exempt)
        action = self.env.ref("tms_core.action_tms_tds_declaration_extract")
        lines = self.env["account.move.line"].search(eval(action.domain))
        self.assertIn(bill, lines.mapped("move_id"))
        self.assertEqual(lines.filtered(lambda line: line.move_id == bill)[:1].tms_vendor_pan, "ABCPS1234D")

    def test_hire_tds_below_threshold(self):
        bill = self._hire_bill(20000.0)
        self.assertFalse(bill.l10n_in_withhold_move_ids)

    def test_gta_status_sets_gst(self):
        """BR-HIR-11: no GST for a non-GTA owner; 5% reverse charge for a GTA vendor."""
        bill = self._hire_bill(20000.0)
        self.assertFalse(bill.invoice_line_ids.tax_ids)
        bill2 = self._hire_bill(20000.0, vendor_vals={"tms_gta_status": "gta_rcm",
                                                      "state_id": self.env.ref("base.state_in_mh").id})
        tax = bill2.invoice_line_ids.tax_ids
        self.assertTrue(tax)
        self.assertTrue(tax.l10n_in_reverse_charge)


@tagged("post_install", "-at_install")
class TestOperationsControls(TmsCommon):
    def test_breakdown_halts_trip(self):
        """WF-04, BR-NTF-08: a breakdown logs a Delayed DSR line and blocks delivery until resolved."""
        trip = self._trip(self._lr())
        trip.action_depart()
        self.assertEqual(trip.state_code, "in_transit")
        bd = self.env["tms.trip.breakdown"].create({"trip_id": trip.id, "location": "Malegaon"})
        self.assertEqual(trip.state_code, "started")
        self.assertTrue(trip.dsr_ids.filtered(lambda d: d.status == "delayed_breakdown"))
        with self.assertRaises(UserError):
            trip.consignment_link_ids.action_mark_delivered()
        wiz = self.env["tms.breakdown.resolve.wizard"].create({"breakdown_id": bd.id, "resolution": "repair"})
        wiz.action_resolve()
        trip.consignment_link_ids.action_mark_delivered()
        self.assertEqual(trip.state_code, "delivered")

    def test_market_documents_on_trip(self):
        """BR-CMP-05: documents captured on the trip pass the gate for an unregistered market vehicle."""
        bare = self.env["tms.market.vehicle"].create({"name": "GJ01ZZ0001", "owner_id": self.vendor.id})
        lr = self._lr()
        trip = self._trip(lr, fleet="market", start=False, planned_market_vehicle_id=bare.id)
        trip.action_confirm()
        with self.assertRaises(UserError):
            trip.button_start_order()
        until = fields.Date.today() + timedelta(days=200)
        trip.write({"market_document_ids": [Command.create({"type_id": t.id, "number": "X-%s" % t.code,
                                                            "valid_until": until})
                                            for t in self._doc_types().filtered(
                                                lambda t: t.applies_to in ("vehicle", "both"))]})
        trip.button_start_order()
        self.assertEqual(trip.state_code, "started")

    def test_expense_recovery_on_save(self):
        """BR-EXP-06: the recovery charge exists as soon as the expense is saved."""
        trip = self._trip(self._lr())
        cat = self.env["tms.category"].search([("use_expense", "=", True)], limit=1)
        exp = self.env["tms.trip.expense"].create({
            "trip_id": trip.id, "category_id": cat.id, "amount": 1500.0, "payment_mode": "company_cash",
            "recoverable_from": "customer", "recoverable_amount": 1500.0})
        self.assertTrue(exp.charge_id)
        self.assertEqual(exp.charge_id.state, "draft")
        exp.recoverable_amount = 1200.0
        self.assertEqual(exp.charge_id.amount, 1200.0)

    def test_ewaybill_state_threshold(self):
        """BR-EWB-03: an intra-state override replaces the 50,000 default."""
        lr = self._lr(confirm=False, value=60000.0)
        self.assertTrue(lr.ewaybill_required)
        self.env["tms.ewaybill.state.threshold"].create({"company_id": self.company.id,
                                                        "state_id": self.env.ref("base.state_in_mh").id,
                                                        "threshold": 100000.0})
        lr.invalidate_recordset()
        lr._compute_ewaybill_required()
        self.assertFalse(lr.ewaybill_required)

    def test_refused_goods_godown(self):
        """BR-LR-10, BR-POD-09: refused goods delivered later, or returned to origin on a new LR."""
        for action, vehicle, driver in (("action_deliver_from_godown", self.vehicle, self.driver),
                                        ("action_return_to_origin", self.vehicle2, self.driver2)):
            lr = self._lr()
            trip = self._trip(lr, planned_vehicle_id=vehicle.id, planned_driver_id=driver.id)
            link = trip.consignment_link_ids
            link.write({"outcome_evidence": b"UkVG", "outcome_evidence_name": "refusal.pdf"})
            link.action_mark_refused()
            self.assertTrue(lr.godown_hold)
            getattr(lr, action)()
            self.assertFalse(lr.godown_hold)
            if action == "action_deliver_from_godown":
                self.assertTrue(lr.godown_delivered_on)
            else:
                ret = lr.return_lr_ids
                self.assertEqual(ret.origin_id, lr.destination_id)
                self.assertEqual(ret.billing_partner_id, lr.billing_partner_id)

    def test_credit_override_wizard(self):
        """BR-CRD-03: the override runs through the reason wizard and is logged."""
        self.customer.tms_credit_limit = 100.0
        lr = self._lr(confirm=False)
        action = lr.with_user(self.u_manager).action_open_credit_override()
        wiz = self.env["tms.reason.wizard"].with_user(self.u_manager).with_context(action["context"]).create(
            {"reason": "Known customer"})
        wiz.action_confirm()
        self.assertEqual(lr.state, "confirmed")
        self.customer.tms_credit_limit = 1000000.0

    def test_lr_amendment_reject_needs_bm(self):
        lr = self._lr()
        am = self.env["tms.consignment.amendment"].create({"consignment_id": lr.id, "reason": "Wrong consignee",
                                                           "new_packages": 90})
        with self.assertRaises(UserError):
            am.with_user(self.u_clerk).action_reject()
        am.with_user(self.u_manager).action_reject()
        with self.assertRaises(UserError):
            am.with_user(self.u_manager).action_approve()

    def test_kpi_filter_and_utilisation(self):
        """BR-DSH-01, BR-DSH-05."""
        trip = self._trip(self._lr())
        other = self.customer.copy({"name": "Other customer"})
        Kpi = self.env["tms.kpi"]
        kpi = Kpi.search([("code", "=", "KPI-05")]) or Kpi.search([], limit=1)
        action = self.env["tms.kpi.filter"].create({"partner_id": other.id}).action_apply()
        scoped = Kpi.with_context(**action["context"]).browse(kpi.ids)
        scoped._compute_value()
        self.assertEqual(scoped.value_count, 0)
        # UT-DSH-02: the customer filter narrows the amount as well as the count
        charge = self.env["tms.trip.charge"].create({
            "trip_id": trip.id, "category_id": self.env.ref("tms_core.tms_category_detention").id,
            "amount": 1200.0, "recover_from": "customer", "partner_id": self.customer.id})
        charge.action_confirm()
        kpi09 = Kpi.search([("code", "=", "KPI-09")])
        mine = self.env["tms.kpi.filter"].create({"partner_id": self.customer.id}).action_apply()
        for ctx, count, amount in ((action["context"], 0, 0.0), (mine["context"], 1, 1200.0)):
            tile = Kpi.with_context(**ctx).browse(kpi09.ids)
            tile._compute_value()
            self.assertEqual((tile.value_count, tile.value_amount), (count, amount))
        rows = self.env["tms.vehicle.utilisation"].search([("vehicle_id", "=", trip.primary_vehicle_id.id)])
        self.assertTrue(rows)
        self.assertGreaterEqual(rows[0].trip_count, 1)


@tagged("post_install", "-at_install")
class TestBranchAccounting(TmsCommon):
    def test_strict_branch_journal(self):
        """BR-BRN-04, BR-CFG-07: a branch's TMS invoice posts in the branch's own sale journal."""
        branch = self.env["res.company"].create({
            "name": "Premier Transport Nagpur", "parent_id": self.company.id, "tms_branch_code": "NGP",
            "state_id": self.env.ref("base.state_in_mh").id, "country_id": self.env.ref("base.in").id,
            "vat": "27AAGCP1234L1ZV"})
        self.env.cr.precommit.run()  # Odoo loads the branch's chart at commit time
        self.env.user.company_ids = [Command.link(branch.id)]
        self.env["tms.gta.option"].create({"company_id": branch.id,
                                           "date_from": fields.Date.today() - timedelta(days=30),
                                           "date_to": fields.Date.today() + timedelta(days=30), "option": "fcm5"})
        lr = self.with_company_lr(branch)
        moves = self.env["tms.invoice.lr.wizard"].with_company(branch)._tms_create_invoices(lr)
        self.assertEqual(moves.journal_id.company_id, branch)
        moves.action_post()
        # the same document in the parent's journal is refused
        lr2 = self.with_company_lr(branch)
        moves2 = self.env["tms.invoice.lr.wizard"].with_company(branch)._tms_create_invoices(lr2)
        parent_journal = self.env["account.journal"].search([("company_id", "=", self.company.id),
                                                             ("type", "=", "sale")], limit=1)
        moves2.journal_id = parent_journal
        with self.assertRaises(UserError):
            moves2.action_post()

    def with_company_lr(self, branch):
        lr = self.env["tms.consignment"].with_company(branch).create({
            "company_id": branch.id, "consignor_id": self.customer.id, "consignee_id": self.consignee.id,
            "freight_basis": "paid", "origin_id": self.loc_bhiwandi.id, "destination_id": self.loc_nagpur.id,
            "rate_type": "per_kg", "typed_rate": 4.0, "typed_rate_reason": "Counter rate",
            "line_ids": [Command.create({"commodity": "Rice", "packaging_type": "bags", "packages": 10,
                                         "actual_weight": 1000.0, "declared_value": 10000.0})]})
        lr.action_confirm()
        return lr


@tagged("post_install", "-at_install")
class TestReviewFixes(TmsCommon):
    def test_bulk_post_runs_tms_checks(self):
        """Bulk "Post entries" (account.move._post) still blocks a hire bill over tolerance (BR-HIR-07)."""
        trip = self._trip(self._lr(), fleet="market")
        memo = trip.hire_memo_ids
        self._deliver_and_verify(trip)
        memo.action_create_invoice()
        bill = memo.invoice_ids
        bill.invoice_date = fields.Date.today()
        bill.invoice_line_ids.price_unit = 30200.0
        wiz = self.env["validate.account.move"].with_context(active_model="account.move",
                                                             active_ids=bill.ids).create({})
        with self.assertRaises(UserError):
            wiz.validate_move()
        self.assertEqual(bill.state, "draft")

    def test_posted_advance_locked(self):
        trip = self._trip(self._lr())
        adv = self.env["tms.driver.advance"].create({"trip_id": trip.id, "driver_id": self.driver.id,
                                                     "amount": 2000.0})
        adv.action_post()
        with self.assertRaises(UserError):
            adv.amount = 1500.0
        with self.assertRaises(UserError):
            adv.action_post()
        with self.assertRaises(UserError):
            adv.with_user(self.u_dispatcher).unlink()

    def test_guard_copy_quick_create_nested(self):
        guard = self.env["tms.rpc.guard"]
        lr = self._lr()
        with self.assertRaises(AccessError):
            guard.with_user(self.u_dispatcher)._tms_check_call("tms.consignment", "copy", [lr.ids, {}], {})
        with self.assertRaises(AccessError):
            guard.with_user(self.u_clerk)._tms_check_call("tms.consignment", "copy",
                                                         [lr.ids, {"state": "invoiced"}], {})
        with self.assertRaises(AccessError):
            guard.with_user(self.u_dispatcher)._tms_check_call("tms.driver", "name_create", ["New driver"], {})
        trip = self._trip(lr)
        with self.assertRaises(AccessError):
            guard.with_user(self.u_verifier)._tms_check_call(
                "tms.order", "web_save", [trip.ids, {"charge_ids": [[0, 0, {"amount": 10}]]}, {}], {})

    def test_admin_only_delete(self):
        """BR-BRN-08: only the TMS Administrator deletes TMS documents, also from inside a form."""
        trip = self._trip(self._lr())
        incident = self.env["tms.trip.incident"].create({
            "trip_id": trip.id, "category_id": self.env.ref("tms_core.tms_category_vehicle_breakdown").id,
            "location": "Nashik", "description": "Tyre burst"})
        with self.assertRaises(AccessError):
            incident.with_user(self.u_dispatcher).unlink()
        incident.with_user(self.u_admin).unlink()
        self.assertFalse(incident.exists())

    def test_bm_approves_hire_cancellation(self):
        trip = self._trip(self._lr(), fleet="market", start=False)
        memo = trip.hire_memo_ids
        memo.with_user(self.u_manager).with_context(tms_reason="Vehicle not placed").action_tms_approve_cancel()
        self.assertEqual(memo.tms_cancel_approved_by_id, self.u_manager)

    def test_submission_shift_not_cumulative(self):
        """BR-INV-13: correcting the submission date moves the due date by the correction only."""
        self.customer.tms_credit_from_submission = True
        lr = self._lr(basis="paid")
        move = self.env["tms.invoice.lr.wizard"]._tms_create_invoices(lr)
        move.action_post()
        due = move.invoice_date_due
        move.tms_submission_date = move.invoice_date + timedelta(days=10)
        self.assertEqual(move.invoice_date_due, due + timedelta(days=10))
        move.tms_submission_date = move.invoice_date + timedelta(days=12)
        self.assertEqual(move.invoice_date_due, due + timedelta(days=12))
