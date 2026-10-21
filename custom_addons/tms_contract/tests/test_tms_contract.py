import base64
from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.tms_estimate.tests.test_tms_estimate import EstimateCommon

PDF = base64.b64encode(b"%PDF-1.4 amendment")


class ContractCommon(EstimateCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        def user(login, *groups):
            return cls.env["res.users"].with_context(no_reset_password=True).create({
                "name": login.title(), "login": login, "email": "%s@example.com" % login,
                "company_id": cls.company.id, "company_ids": [Command.set(cls.company.ids)],
                "groups_id": [Command.set([cls.env.ref("base.group_user").id] + [cls.env.ref(g).id for g in groups])],
            })
        cls.u_sales = user("salesexec", "tms_core.group_sales_executive")
        cls.u_director = user("director", "tms_core.group_director")
        cls.company.write({"tms_estimate_variance_tolerance": 50.0, "tms_contract_excess_ceiling": 10.0})
        cls.customer.tms_credit_limit = 100000000.0
        cls.cat_toll = cls.env.ref("tms_core.tms_category_toll")
        cls.cat_loading = cls.env.ref("tms_core.tms_category_loading")
        cls.cat_state = cls.env.ref("tms_core.tms_category_state_charge")
        cls.cat_detention = cls.env.ref("tms_core.tms_category_detention")
        cls.p_toll_cost = cls.env.ref("tms_estimate.product_cost_toll")
        cls.p_loading_cost = cls.env.ref("tms_estimate.product_cost_loading")

    def _contract(self, value=5000000.0, opening=0.0, activate=True, **kw):
        vals = {
            "partner_id": self.customer.id,
            "po_number": "PO/KAF/2026/118",
            "po_date": fields.Date.today() - timedelta(days=30),
            "date_start": fields.Date.today() - timedelta(days=30),
            "date_end": fields.Date.today() + timedelta(days=335),
            "value": value,
            "opening_consumption": opening,
            "incidental_category_ids": [Command.set((self.cat_toll | self.cat_loading | self.cat_state).ids)],
            "rate_ids": [
                Command.create({"kind": "vehicle", "vehicle_class_id": self.vehicle_class.id, "basis": "km",
                                "rate": 58.0, "escalation_per_rupee": 0.5}),
                Command.create({"kind": "accessory", "product_id": self.p_puller.id, "basis": "km", "rate": 120.0}),
                Command.create({"kind": "accessory", "product_id": self.p_axle.id, "basis": "unit_km",
                                "rate": 27.50}),
                Command.create({"kind": "accessory", "product_id": self.p_escort.id, "basis": "km", "rate": 15.0}),
                Command.create({"kind": "element", "name": "Lump movement", "basis": "trip", "rate": 10000.0}),
            ],
        }
        vals.update(kw)
        contract = self.env["tms.contract"].with_user(self.u_sales).create(vals)
        if activate:
            contract.with_user(self.u_sales).action_submit()
            contract.with_user(self.u_ops).action_approve()
        return contract.with_env(self.env)

    def _rate(self, contract, record):
        if record._name == "tms.contract.rate":
            return record
        key = "vc:%s" % record.id if record._name == "tms.vehicle.class" else "p:%s" % record.id
        return contract.rate_ids.filtered(lambda r: r.element_key == key)[:1]

    def _item(self, contract, km, uses, incidentals=()):
        item = self.env["sale.order.line"].create({
            "tms_contract_id": contract.id, "tms_origin_id": self.loc_bhiwandi.id,
            "tms_destination_id": self.loc_nagpur.id, "tms_distance_km": km})
        for record, units in uses:
            self.env["tms.contract.item.rate"].create({"sale_line_id": item.id,
                                                       "rate_id": self._rate(contract, record).id, "units": units})
        for product, cost in incidentals:
            self.env["tms.estimate.line"].create({"estimate_id": item.tms_estimate_id.id, "product_id": product.id,
                                                  "unit_cost": cost, "typed_cost_reason": "Expected at actual"})
        return item

    def _contract_lr(self, item, **kw):
        vals = dict(confirm=True, rate_type=False, typed_rate=0.0, typed_rate_reason=False, sale_line_id=item.id)
        vals.update(kw)
        return self._lr(**vals)

    def _expense(self, trip, lr, category, eline, amount):
        exp = self.env["tms.trip.expense"].create({
            "trip_id": trip.id, "consignment_id": lr.id, "category_id": category.id, "amount": amount,
            "estimate_line_id": eline.id, "receipt": PDF, "receipt_name": "receipt.pdf",
            "recoverable_from": "customer", "recoverable_amount": amount})
        exp.with_user(self.u_accountant).action_approve()
        return exp

    def _invoice(self, item, *amounts):
        return self.env["account.move"].create({
            "move_type": "out_invoice", "partner_id": self.customer.id, "invoice_date": fields.Date.today(),
            "invoice_line_ids": [Command.create({
                "product_id": self.env.ref("tms_core.product_freight").id, "name": "Freight %s" % amount,
                "quantity": 1, "price_unit": amount, "tax_ids": [], "tms_sale_line_id": item.id,
                "tms_line_kind": "freight"}) for amount in amounts]})

    def _extension(self, contract, value=0.0, **kw):
        ext = self.env["tms.contract.extension"].with_user(self.u_sales).create(dict(
            {"contract_id": contract.id, "name": "AMD-%s" % value, "added_value": value, "file": PDF,
             "filename": "amendment.pdf"}, **kw))
        ext.with_user(self.u_sales).action_submit()
        return ext

    def _lump(self, contract):
        return contract.rate_ids.filtered(lambda r: r.name == "Lump movement")

    def _balance(self, contract):
        return contract._tms_figures()["balance"]


@tagged("post_install", "-at_install")
class TestContract(ContractCommon):
    def test_activation(self):
        """BR-CON-01..04, UC-35."""
        draft = self.env["tms.contract"].with_user(self.u_sales).create({
            "partner_id": self.customer.id, "date_end": fields.Date.today() + timedelta(days=90), "value": 100000.0})
        with self.assertRaises(UserError):
            draft.with_user(self.u_sales).action_submit()  # no rate line
        own = self._contract(activate=False)
        own.with_user(self.u_sales).action_submit()
        with self.assertRaises(UserError):
            own.with_user(self.u_manager).action_approve()  # not the Operations Head
        drafted_by_ops = self.env["tms.contract"].with_user(self.u_ops).create({
            "partner_id": self.customer.id, "date_end": fields.Date.today() + timedelta(days=90), "value": 1.0,
            "rate_ids": [Command.create({"kind": "vehicle", "vehicle_class_id": self.vehicle_class.id,
                                         "rate": 50.0})]})
        drafted_by_ops.with_user(self.u_ops).action_submit()
        with self.assertRaises(UserError):
            drafted_by_ops.with_user(self.u_ops).action_approve()  # the drafter never approves
        own.with_user(self.u_ops).action_approve()
        self.assertEqual(own.state, "active")
        self.assertTrue(own.name.startswith("CON/"))
        self.assertEqual(own.order_id.state, "sale")
        self.assertFalse(own.order_id.order_line)
        self.assertEqual(own.order_id.tms_contract_id, own)
        self.assertAlmostEqual(self._balance(own), 5000000.0)
        self.assertEqual(own.billing_partner_ids, self.customer)
        self.assertEqual(own.branch_ids, self.company)
        with self.assertRaises(UserError):
            own.value = 6000000.0  # value changes only by extension
        with self.assertRaises(UserError):
            own.rate_ids[0].rate = 60.0  # BR-CON-16

    def test_worked_example(self):
        """#23.7 steps 1 to 6: CON/2026/0007, ₹50,00,000 with ₹47,80,000 already invoiced or reserved."""
        contract = self._contract(opening=4780000.0)
        self.assertAlmostEqual(self._balance(contract), 220000.0)
        # step 1: item 10, 32 ft MXL Taloja–Chennai 1,350 km, incidentals toll 9,800 and loading 2,700
        item10 = self._item(contract, 1350.0, [(self.vehicle_class, 1)],
                            [(self.p_toll_cost, 9800.0), (self.p_loading_cost, 2700.0)])
        self.assertEqual(item10.tms_item_no, 10)
        self.assertAlmostEqual(item10.tms_freight, 78300.0)
        self.assertAlmostEqual(item10.price_unit, 78300.0)
        self.assertTrue(item10.tms_estimate_id.is_contract)
        self.assertTrue(all(line.markup_pct == 0.0 for line in item10.tms_estimate_id.line_ids))
        with self.assertRaises(UserError):
            self._contract_lr(item10, confirm=False)  # BR-CON-11: not released yet
        item10.with_user(self.u_clerk).action_tms_release()
        self.assertEqual(item10.tms_release_state, "released")
        self.assertEqual(item10.tms_estimate_id.state, "locked")
        self.assertAlmostEqual(item10.tms_budget_reserved, 90800.0)
        self.assertAlmostEqual(self._balance(contract), 129200.0)
        first = contract.reservation_ids
        self.assertEqual(first.event, "release")
        self.assertAlmostEqual(first.amount, 90800.0)
        # step 2: item 20, ODC with puller, 8 axle lines and escort over 720 km, incidentals 21,000
        item20 = self._item(contract, 720.0, [(self.p_puller, 1), (self.p_axle, 8), (self.p_escort, 1)],
                            [(self.p_toll_cost, 21000.0)])
        self.assertAlmostEqual(item20.tms_freight, 255600.0)
        with self.assertRaises(UserError):
            item20.action_tms_release()
        item20.invalidate_recordset()
        self.assertTrue(item20.tms_held)
        self.assertAlmostEqual(item20.tms_shortfall, 147400.0)
        self.assertEqual(item20.tms_release_state, "draft")
        wiz = self.env["tms.contract.release.wizard"].create({"sale_line_id": item20.id})
        self.assertAlmostEqual(wiz.shortfall, 147400.0)
        self.assertAlmostEqual(wiz.budget, 276600.0)
        kpi = self.env.ref("tms_core.kpi_21")
        self.assertEqual(kpi._kpi_kpi_21()[0], 1)
        # step 3: an extension of 10,00,000 (not above 20% of the value: the Branch Manager approves)
        ext = self._extension(contract, 1000000.0)
        self.assertEqual(ext.approval_role, "bm")
        with self.assertRaises(UserError):
            ext.with_user(self.u_sales).action_approve()
        ext.with_user(self.u_manager).action_approve()
        item20.action_tms_release()
        self.assertAlmostEqual(self._balance(contract), 852600.0)
        # step 4: item 10 on the road: toll 10,900 and loading 3,300 posted, 1,700 over the estimate
        lr10 = self._contract_lr(item10)
        trip10 = self._trip(lr10)
        elines = item10.tms_estimate_id.line_ids
        exp_toll = self._expense(trip10, lr10, self.cat_toll, elines.filtered(lambda x: x.product_id == self.p_toll_cost),
                                 10900.0)
        exp_load = self._expense(trip10, lr10, self.cat_loading,
                                 elines.filtered(lambda x: x.product_id == self.p_loading_cost), 3300.0)
        self.assertAlmostEqual(self._balance(contract), 850900.0)
        topup = contract.reservation_ids.filtered(lambda r: r.event == "incidental")
        self.assertAlmostEqual(sum(topup.mapped("amount")), 1700.0)
        self.assertEqual(topup.mapped("trip_id"), trip10)
        # step 5: item 20 on the road: the customer accepts a detention charge of 18,000
        lr20 = self._contract_lr(item20, weight=40000.0)
        detention = self.env["tms.trip.charge"].create({"consignment_id": lr20.id, "category_id": self.cat_detention.id,
                                                        "amount": 18000.0})
        detention.action_confirm()
        self.assertEqual(detention.state, "to_approve")
        with self.assertRaises(UserError):
            detention.with_user(self.u_manager).action_approve()  # outside the incidentals: the Operations Head
        detention.with_user(self.u_ops).action_approve()
        self.assertTrue(self.env["tms.override.log"].search([("kind", "=", "noncontract_charge")]))
        self.assertAlmostEqual(self._balance(contract), 832900.0)
        # step 6: item 10 invoiced after delivery: freight 78,300, toll 10,900, loading 3,300
        self._deliver_and_verify(trip10)
        for charge in (exp_toll | exp_load).mapped("charge_id"):
            charge.action_confirm()
            self.assertEqual(charge.state, "to_approve")  # BR-CHG-04: every contract customer charge
            charge.with_user(self.u_manager).action_approve()
        self.assertAlmostEqual(self._balance(contract), 832900.0)
        move = self.env["tms.invoice.lr.wizard"]._tms_create_invoices(lr10)
        self.assertEqual(len(move), 1)
        self.assertAlmostEqual(move.amount_untaxed, 92500.0)
        self.assertFalse(move.tms_contract_awaiting)
        receipts = self.env["ir.attachment"].search([("res_model", "=", "account.move"), ("res_id", "=", move.id)])
        self.assertEqual(len(receipts), 2)  # BR-CON-07: the receipts go with the invoice
        move.action_post()
        item10.invalidate_recordset()
        self.assertAlmostEqual(item10.tms_open_reservation, 0.0)
        self.assertEqual(item10.tms_release_state, "closed")
        self.assertAlmostEqual(self._balance(contract), 832900.0)
        invoiced_line = contract.reservation_ids.filtered(lambda r: r.event == "invoice")
        self.assertAlmostEqual(invoiced_line.amount, -92500.0)
        # BR-CON-17, BR-PNL-35, BR-PRN-11
        contract.invalidate_recordset()
        self.assertAlmostEqual(contract.invoiced, 4780000.0 + 92500.0)
        self.assertAlmostEqual(contract.value_total, 6000000.0)
        self.env.flush_all()
        pnl = self.env["tms.contract.pnl"].search([("contract_id", "=", contract.id)])
        self.assertAlmostEqual(pnl.balance, 832900.0)
        self.assertAlmostEqual(pnl.revenue, 92500.0)
        self.assertAlmostEqual(pnl.actual, 14200.0)  # incidentals are revenue and direct cost alike
        html = self.env["ir.actions.report"]._render_qweb_html("tms_contract.report_contract_statement",
                                                               contract.ids)[0]
        self.assertIn(contract.name.encode(), html)
        self.assertIn(b"Released movements", html)
        # BR-CRD-01: open reservations count in exposure
        self.assertAlmostEqual(self.customer._tms_open_reservations(), 276600.0 + 18000.0)

    def test_excess(self):
        """BR-CON-18, BR-CON-19, RL-10, UC-36 acceptance."""
        self.company.tms_contract_excess_ceiling = 50.0
        contract = self._contract(value=100000.0)
        lump = self._lump(contract)
        item_a = self._item(contract, 800.0, [(lump, 1)])
        item_a.tms_contract_rate_ids.write({"trips": 8})
        self.assertAlmostEqual(item_a.tms_freight, 80000.0)
        item_a.action_tms_release()
        item_b = self._item(contract, 500.0, [(lump, 1)])
        item_b.tms_contract_rate_ids.write({"trips": 5})
        with self.assertRaises(UserError):
            item_b.action_tms_release()
        wiz = self.env["tms.contract.excess.wizard"].with_user(self.u_sales).create({
            "contract_id": contract.id, "sale_line_id": item_b.id, "amount": 30000.0, "reason": "Rush order",
            "cover": "extension", "expected_cover_date": fields.Date.today() + timedelta(days=10)})
        excess = self.env["tms.contract.excess"].browse(wiz.action_request()["res_id"])
        self.assertEqual(excess.state, "requested")
        for approver in (self.u_manager, self.u_ops, self.u_sales):
            with self.assertRaises(UserError):
                excess.with_user(approver).action_approve()
        excess.with_user(self.u_finance).action_approve()
        self.assertTrue(self.env["tms.override.log"].search([("kind", "=", "contract_excess")]))
        item_b.action_tms_release()
        self.assertEqual(item_b.tms_release_state, "released")
        contract.invalidate_recordset()
        self.assertAlmostEqual(contract.over_contract, 30000.0)
        self.assertAlmostEqual(self._balance(contract), -30000.0)
        count, amount, _model, _dom = self.env.ref("tms_core.kpi_22")._kpi_kpi_22()
        self.assertEqual((count, amount), (1, 30000.0))
        # a second excess above the ceiling needs a director; the requester never approves
        big = self.env["tms.contract.excess"].with_user(self.u_finance).create({
            "contract_id": contract.id, "sale_line_id": item_b.id, "amount": 60000.0, "reason": "Extra lift",
            "cover": "extension", "expected_cover_date": fields.Date.today() + timedelta(days=5)})
        big.with_user(self.u_finance).action_request()
        self.assertTrue(big.needs_director)
        with self.assertRaises(UserError):
            big.with_user(self.u_finance).action_approve()
        big.with_user(self.u_director).action_approve()
        # an extension consumes the extension-awaited excess first, as dated lines
        ext = self._extension(contract, 100000.0)
        self.assertEqual(ext.approval_role, "oh")
        ext.with_user(self.u_ops).action_approve()
        self.assertAlmostEqual(ext.absorbed_excess, 90000.0)
        self.assertEqual((excess | big).mapped("state"), ["closed", "closed"])
        self.assertEqual(len(contract.reservation_ids.filtered(lambda r: r.event == "excess_absorbed")), 2)
        contract.invalidate_recordset()
        self.assertAlmostEqual(contract.over_contract, 0.0)
        self.assertAlmostEqual(self._balance(contract), 70000.0)
        # BR-NTF-18: cover dates pass without an extension
        late = self.env["tms.contract.excess"].with_user(self.u_sales).create({
            "contract_id": contract.id, "sale_line_id": item_a.id, "amount": 1000.0, "reason": "x",
            "cover": "extension", "expected_cover_date": fields.Date.today() - timedelta(days=1)})
        late.with_user(self.u_sales).action_request()
        late.with_user(self.u_finance).action_approve()
        self.env["tms.contract.excess"]._cron_cover_overdue()
        self.assertTrue(late.cover_alert_sent)
        self.assertTrue(late.activity_ids)

    def test_invoice_gate(self):
        """BR-CON-12, BR-CON-13, BR-CON-14, UC-37 acceptance."""
        contract = self._contract(value=50000.0)
        item = self._item(contract, 100.0, [(self._lump(contract), 1)])
        item.action_tms_release()
        self.assertAlmostEqual(item._tms_item_figures()["reserved"], 10000.0)
        self.assertAlmostEqual(self._balance(contract), 40000.0)
        # a 52,000 invoice against a 40,000 balance and 10,000 reserved stays in draft, Awaiting extension
        held = self._invoice(item, 45000.0, 7000.0)
        with self.assertRaises(UserError):
            held.action_post()
        self.assertEqual(held.state, "draft")
        self.assertTrue(held.tms_contract_awaiting)
        self.assertAlmostEqual(held.tms_contract_shortfall, 2000.0)
        # the Branch Accountant posts the lines that fit and holds the rest
        wiz = self.env["tms.contract.partial.post.wizard"].create({"move_id": held.id, "reason": "Bill what fits"})
        with self.assertRaises(UserError):
            wiz.with_user(self.u_manager).action_confirm()
        action = wiz.with_user(self.u_accountant).action_confirm()
        rest = self.env["account.move"].browse(action["res_id"])
        self.assertEqual(held.state, "posted")
        self.assertEqual(rest.state, "draft")
        self.assertAlmostEqual(rest.amount_untaxed, 7000.0)
        self.assertTrue(rest.tms_contract_awaiting)
        self.assertTrue(self.env["tms.override.log"].search([("kind", "=", "partial_posting")]))
        self._invoice(item, self._balance(contract)).action_post()
        contract._tms_update_state()
        self.assertEqual(contract.state, "exhausted")
        with self.assertRaises(UserError):
            self._item(contract, 10.0, [(self.vehicle_class, 1)]).action_tms_release()
        # an extension returns the contract to Active and the held invoice posts
        ext = self._extension(contract, 1000000.0)
        with self.assertRaises(UserError):
            ext.with_user(self.u_manager).action_approve()  # above the extension limit: the Operations Head
        ext.with_user(self.u_ops).action_approve()
        self.assertEqual(contract.state, "active")
        self.assertEqual(rest.state, "posted")
        # a customer deduction posts as a credit note and leaves Invoiced unchanged (BR-CON-27)
        before = contract._tms_figures()["I"]
        lr = self._contract_lr(item)
        refund = self.env["account.move"].create({
            "move_type": "out_refund", "partner_id": self.customer.id, "tms_credit_reason": "deduction",
            "invoice_date": fields.Date.today(),
            "invoice_line_ids": [Command.create({"name": "Short-paid", "quantity": 1, "price_unit": 1500.0,
                                                 "tax_ids": [], "tms_consignment_id": lr.id})]})
        refund.action_post()
        self.assertEqual(refund.invoice_line_ids.tms_line_kind, "deduction")
        self.assertAlmostEqual(contract._tms_figures()["I"], before)

    def test_runon_and_booking_rules(self):
        """BR-CON-11, BR-CON-14, BR-CON-24, OP-19."""
        contract = self._contract(value=1000000.0, date_start=fields.Date.today() - timedelta(days=60),
                                  date_end=fields.Date.today() + timedelta(days=5))
        item = self._item(contract, 100.0, [(self.vehicle_class, 1)])
        item.action_tms_release()
        with self.assertRaises(UserError):
            self._contract_lr(item, confirm=False, freight_basis="topay")  # To-Pay refused
        stranger = self.env["res.partner"].create({"name": "Unlisted plant"})
        with self.assertRaises(UserError):
            self._contract_lr(item, confirm=False, billing_partner_id=stranger.id)
        contract.with_context(tms_contract_extension=True).date_end = fields.Date.today() - timedelta(days=10)
        self.env["tms.contract"]._cron_contract_daily()
        self.assertEqual(contract.state, "expired")
        lr = self._contract_lr(item)  # within the 30 run-on days
        self.assertEqual(lr.state, "confirmed")
        with self.assertRaises(UserError):
            self._item(contract, 50.0, [(self.vehicle_class, 1)]).action_tms_release()  # only Active releases
        contract.with_context(tms_contract_extension=True).date_end = fields.Date.today() - timedelta(days=40)
        with self.assertRaises(UserError):
            self._contract_lr(item, confirm=False)
        # an extension of time returns it to Active
        ext = self._extension(contract, 0.0, new_date_end=fields.Date.today() + timedelta(days=60))
        ext.with_user(self.u_manager).action_approve()
        self.assertEqual(contract.state, "active")
        self._contract_lr(item)

    def test_contract_customer_rules(self):
        """BR-CON-15, BR-CHG-04, BR-MD-16, BR-CON-23, UC-36 A3."""
        contract = self._contract(value=1000000.0, detention_rate=3000.0, free_hours_unloading=6.0)
        lr = self._lr(confirm=False)
        with self.assertRaises(UserError):
            lr.action_confirm()  # typed rate for a contract customer
        with self.assertRaises(UserError):
            lr.with_user(self.u_clerk).with_context(tms_reason="x").action_tms_approve_contract_rate()
        lr.with_user(self.u_manager).with_context(tms_reason="Spot load outside the PO").action_tms_approve_contract_rate()
        lr.action_confirm()
        self.assertEqual(lr.state, "confirmed")
        self.assertTrue(self.env["tms.override.log"].search([("kind", "=", "contract_customer_rate"),
                                                             ("res_id", "=", lr.id)]))
        order = self._order(km=100.0)
        self.env["tms.estimate.line"].create({"estimate_id": order.order_line.tms_estimate_id.id,
                                              "product_id": self.p_survey.id})
        with self.assertRaises(UserError):
            order.action_confirm()
        order.with_user(self.u_manager).with_context(tms_reason="Separate ODC lot").action_tms_approve_contract_customer()
        order.action_confirm()
        self.assertEqual(order.state, "sale")
        # every customer charge on a contract item needs the Branch Manager, however small
        item = self._item(contract, 100.0, [(self.vehicle_class, 1)])
        item.action_tms_release()
        clr = self._contract_lr(item)
        trip = self._trip(clr)
        self.assertEqual(trip._tms_free_hours(self.customer), (0.0, 6.0, 3000.0))
        small = self.env["tms.trip.charge"].create({"consignment_id": clr.id, "trip_id": trip.id,
                                                    "category_id": self.cat_detention.id, "amount": 1000.0})
        small.action_confirm()
        self.assertEqual(small.state, "to_approve")
        # BR-CON-23: amending a released item needs the Operations Head and logs an amendment line
        wiz = self.env["tms.contract.item.amend.wizard"].with_context(default_sale_line_id=item.id).create(
            {"reason": "Diversion via Pune"})
        wiz.line_ids.write({"km": 150.0})
        with self.assertRaises(UserError):
            wiz.with_user(self.u_manager).action_apply()
        wiz.with_user(self.u_ops).action_apply()
        self.assertAlmostEqual(item.tms_freight, 150.0 * 58.0)
        amend = contract.reservation_ids.filtered(lambda r: r.event == "amendment")
        self.assertAlmostEqual(amend.amount, 50.0 * 58.0)
        self.assertAlmostEqual(clr.freight_amount, 150.0 * 58.0)
        # UC-36 A3: a movement cancelled before any LR returns its reservation
        spare = self._item(contract, 200.0, [(self.vehicle_class, 1)])
        spare.action_tms_release()
        balance = self._balance(contract)
        spare.action_tms_cancel_item()
        self.assertEqual(spare.tms_release_state, "cancel")
        self.assertAlmostEqual(self._balance(contract), balance + 200.0 * 58.0)

    def test_escalation_and_alerts(self):
        """BR-CON-22, BR-NTF-13, BR-NTF-19, KPI-20."""
        contract = self._contract(value=100000.0, escalation_clause=True, diesel_base_price=90.0,
                                  escalation_band=2.0, escalation_revises_released=True,
                                  deposit_kind="bg", deposit_reference="BG/77",
                                  deposit_expiry=fields.Date.today() + timedelta(days=20))
        item = self._item(contract, 1350.0, [(self.vehicle_class, 1)])  # 78,300: 78% used
        item.action_tms_release()
        self.assertFalse(contract.util_alert_sent)
        rev = self.env["tms.contract.escalation"].create({"contract_id": contract.id, "diesel_price": 100.0,
                                                          "date": fields.Date.today() + timedelta(days=1)})
        rev.action_compute()
        self.assertAlmostEqual(rev.line_ids.new_rate, 63.0)
        with self.assertRaises(UserError):
            rev.with_user(self.u_manager).action_apply()
        rev.with_user(self.u_ops).action_apply()
        mxl = self._rate(contract, self.vehicle_class)
        self.assertAlmostEqual(contract._tms_rate_in_force(mxl, fields.Date.today()).rate, 58.0)
        self.assertAlmostEqual(contract._tms_rate_in_force(mxl, fields.Date.today() + timedelta(days=1)).rate, 63.0)
        self.assertAlmostEqual(item.tms_supplementary_amount, 1350.0 * 5.0)
        esc = contract.reservation_ids.filtered(lambda r: r.event == "escalation")
        self.assertAlmostEqual(esc.amount, 6750.0)
        self.assertTrue(contract.util_alert_sent)  # 85,050 of 1,00,000 reserved
        count, top, _m, _d = self.env.ref("tms_core.kpi_20")._kpi_kpi_20()
        self.assertEqual(count, 1)
        self.assertAlmostEqual(top, 85.05, places=2)
        self.env["tms.contract"]._cron_contract_daily()
        self.assertTrue(contract.deposit_alert_sent)
        action = item.action_tms_invoice_supplementary()
        supp = self.env["account.move"].browse(action["res_id"])
        self.assertAlmostEqual(supp.amount_untaxed, 6750.0)
        self.assertEqual(supp.invoice_line_ids.tms_line_kind, "supplementary")
