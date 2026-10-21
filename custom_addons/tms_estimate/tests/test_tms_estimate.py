import base64
from odoo import Command, fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from odoo.addons.tms_core.tests.common import TmsCommon


class EstimateCommon(TmsCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.u_planner = cls.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Planner", "login": "planner", "company_id": cls.company.id,
            "company_ids": [Command.set(cls.company.ids)],
            "groups_id": [Command.set([cls.env.ref("base.group_user").id,
                                       cls.env.ref("tms_core.group_planner").id,
                                       cls.env.ref("tms_core.group_sales_executive").id,
                                       cls.env.ref("sales_team.group_sale_salesman_all_leads").id])]})
        cls.env.user.groups_id = [Command.link(cls.env.ref("sales_team.group_sale_manager").id)]
        cls.company.write({"tms_markup_floor": 10.0, "tms_value_limit": 5000000.0,
                           "tms_estimate_variance_tolerance": 5.0})
        cls.customer.write({"tms_freight_basis": "tbb"})
        cls.cat_misc = cls.env.ref("tms_estimate.cost_category_misc")

        def item(name, basis, cost, markup):
            return cls.env["product.product"].create({
                "name": name, "type": "service", "tms_cost_item": True, "tms_cost_basis": basis,
                "standard_price": cost, "tms_default_markup": markup, "purchase_ok": True})
        cls.p_puller = item("Puller ABC", "unit_km", 95, 25)
        cls.p_axle = item("Axle XYZ", "unit_km", 22, 25)
        cls.p_mob = item("Puller mobilisation", "unit_km", 60, 25)
        cls.p_escort = item("Pilot escort vehicle", "unit_day", 4500, 20)
        cls.p_crew = item("Steersman and helpers", "unit_day", 800, 20)
        cls.p_toll = item("Toll, ODC axle class", "lump", 38000, 0)
        cls.p_permit = item("ODC permit fee", "state", 25000, 0)
        cls.p_survey = item("Route survey and report", "lump", 15000, 30)
        cls.p_lifting = item("Overhead line lifting", "lump", 30000, 15)
        cls.p_misc = item("Misc. and contingency", "lump", 10000, 0)
        cls.p_misc.tms_cost_category_id = cls.cat_misc

    def _order(self, km=720.0, **kw):
        order = self.env["sale.order"].create({
            "partner_id": self.customer.id,
            "require_signature": False,
            "tms_acceptance_file": base64.b64encode(b"Accepted by email"),
            "tms_acceptance_name": "acceptance.txt",
            "order_line": [Command.create({
                "product_id": self.env.ref("tms_estimate.product_movement").id,
                "name": "ODC move, 92 t transformer",
                "tms_is_movement": True,
                "tms_origin_id": self.loc_bhiwandi.id,
                "tms_destination_id": self.loc_nagpur.id,
                "tms_distance_km": km,
                "product_uom_qty": 1,
            })],
            **kw,
        })
        return order

    def _worked_example(self, est):
        """#9.1 worked example: S00044 item 10, 720 km, 9 days."""
        est.write({"km_per_day": 80.0, "state_count": 3})
        self.assertEqual(est.days, 9)
        L = self.env["tms.estimate.line"]
        L.create({"estimate_id": est.id, "product_id": self.p_puller.id, "units": 1})
        L.create({"estimate_id": est.id, "product_id": self.p_axle.id, "units": 14})
        L.create({"estimate_id": est.id, "product_id": self.p_mob.id, "units": 1, "km": 60})
        L.create({"estimate_id": est.id, "product_id": self.p_escort.id, "units": 1})
        L.create({"estimate_id": est.id, "product_id": self.p_crew.id, "units": 4})
        L.create({"estimate_id": est.id, "product_id": self.p_toll.id})
        L.create({"estimate_id": est.id, "product_id": self.p_permit.id, "units": 1})
        L.create({"estimate_id": est.id, "product_id": self.p_survey.id})
        L.create({"estimate_id": est.id, "product_id": self.p_lifting.id})
        L.create({"estimate_id": est.id, "product_id": self.p_misc.id})


@tagged("post_install", "-at_install")
class TestEstimate(EstimateCommon):
    def test_worked_example(self):
        order = self._order()
        line = order.order_line
        self.assertEqual(line.tms_item_no, 10)
        est = line.tms_estimate_id
        self.assertTrue(est)
        self._worked_example(est)
        self.assertEqual(est.line_ids.mapped("line_no"), list(range(1, 11)))
        self.assertAlmostEqual(est.estimated_cost, 531060.0)
        self.assertAlmostEqual(est.marked_up_total, 627360.0)
        self.assertAlmostEqual(est.markup_pct, 18.13, places=1)
        self.assertAlmostEqual(est.rate_per_km, 871.33)
        self.assertAlmostEqual(est.amount, 627357.60)
        self.assertAlmostEqual(est.rounding_difference, -2.40)
        self.assertAlmostEqual(line.price_unit, 627357.60)
        self.assertAlmostEqual(line.tms_rate_per_km, 871.33)
        with self.assertRaises(UserError):
            line.price_unit = 1000.0
        # a typed cost needs a reason
        with self.assertRaises(ValidationError):
            est.line_ids[0].unit_cost = 90.0
        est.line_ids[0].write({"unit_cost": 90.0, "typed_cost_reason": "Negotiated"})
        self.assertTrue(self.env["tms.override.log"].search([("kind", "=", "typed_cost")]))

    def test_copy_fresh_costs(self):
        """BR-QTN-12, BR-EST-09, UT-QTN-28: the copy keeps the cost lines, takes today's costs and clears
        typed-cost reasons."""
        order = self._order()
        est = order.order_line.tms_estimate_id
        self._worked_example(est)
        est.line_ids[0].write({"unit_cost": 90.0, "typed_cost_reason": "Negotiated"})
        self.p_axle.standard_price = 24.0
        action = order.action_tms_copy_fresh()
        new = self.env["sale.order"].browse(action["res_id"])
        self.assertNotEqual(new, order)
        new_est = new.order_line.tms_estimate_id
        self.assertTrue(new_est)
        self.assertNotEqual(new_est, est)
        self.assertEqual(len(new_est.line_ids), len(est.line_ids))
        self.assertEqual(new_est.state_count, 3)
        axle = new_est.line_ids.filtered(lambda line: line.product_id == self.p_axle)
        self.assertAlmostEqual(axle.unit_cost, 24.0)
        puller = new_est.line_ids.filtered(lambda line: line.product_id == self.p_puller)
        self.assertAlmostEqual(puller.unit_cost, 95.0)
        self.assertFalse(any(new_est.line_ids.mapped("typed_cost_reason")))
        self.assertAlmostEqual(new.order_line.price_unit, new_est.amount)
        self.assertGreater(new_est.estimated_cost, 531060.0)

    def test_approval_and_lock(self):
        """BR-EST-08, BR-EST-10, BR-EST-11, BR-QTN-07, BR-QTN-08."""
        order = self._order(km=100.0)
        line = order.order_line
        est = line.tms_estimate_id.with_user(self.u_planner)
        self.env["tms.estimate.line"].with_user(self.u_planner).create(
            {"estimate_id": est.id, "product_id": self.p_toll.id, "markup_pct": 5.0})
        with self.assertRaises(UserError):
            order.action_quotation_sent()  # 5 % markup, below the 10 % floor
        self.assertEqual(line.tms_approval_state, "to_approve")
        with self.assertRaises(UserError):
            line.with_user(self.u_dispatcher).action_tms_approve()
        line.with_user(self.u_manager).action_tms_approve()
        self.assertEqual(line.tms_approval_state, "approved")
        order.action_confirm()
        self.assertEqual(est.line_ids.sorted("line_no")[-1:].product_id.tms_cost_category_id, self.cat_misc)
        self.assertEqual(est.state, "locked")
        self.assertTrue(all(est.line_ids.mapped("locked")))
        with self.assertRaises(UserError):
            est.line_ids[0].markup_pct = 50.0
        with self.assertRaises(UserError):
            est.distance_km = 200.0
        extra = self.env["tms.estimate.line"].create({"estimate_id": est.id, "product_id": self.p_crew.id,
                                                      "unit_cost": 999})
        self.assertTrue(extra.unplanned)
        self.assertEqual(extra.estimated_cost, 0.0)
        with self.assertRaises(UserError):
            order._create_invoices()

    def test_customer_master_required(self):
        """BR-QTN-07."""
        bare = self.env["res.partner"].create({"name": "Bare customer"})
        order = self._order(km=100.0)
        order.partner_id = bare
        self.env["tms.estimate.line"].create({"estimate_id": order.order_line.tms_estimate_id.id,
                                              "product_id": self.p_survey.id})
        with self.assertRaises(UserError):
            order.action_confirm()

    def test_lr_freight_split(self):
        """BR-EST-13, #23.1: LRs of one movement share its amount by charged weight."""
        order = self._order(km=100.0)
        est = order.order_line.tms_estimate_id
        self.env["tms.estimate.line"].create({"estimate_id": est.id, "product_id": self.p_survey.id})
        order.action_confirm()
        amount = order.order_line.price_unit
        lr1 = self._lr(confirm=False, rate_type=False, typed_rate=0.0, typed_rate_reason=False,
                       sale_line_id=order.order_line.id, weight=3000.0)
        self.assertAlmostEqual(lr1.freight_amount, amount)
        lr2 = self._lr(confirm=False, rate_type=False, typed_rate=0.0, typed_rate_reason=False,
                       sale_line_id=order.order_line.id, weight=1000.0)
        lr1.invalidate_recordset()
        self.assertAlmostEqual(lr1.freight_amount, amount * 0.75, places=2)
        self.assertAlmostEqual(lr2.freight_amount, amount * 0.25, places=2)
        lr1.action_confirm()
        lr2.action_confirm()
        with self.assertRaises(ValidationError):
            self._lr(confirm=False, basis="paid", rate_type=False, typed_rate=0.0, typed_rate_reason=False,
                     sale_line_id=order.order_line.id)
        self.assertEqual(order.order_line.tms_lr_charged_weight, 4000.0)

    def test_actual_cost_rules(self):
        """BR-ACT-01..06: estimate line on bill lines; trip lines need it; variance needs the BM."""
        order = self._order(km=100.0)
        est = order.order_line.tms_estimate_id
        eline = self.env["tms.estimate.line"].create({"estimate_id": est.id, "product_id": self.p_survey.id})
        order.action_confirm()
        lr = self._lr(confirm=True, rate_type=False, typed_rate=0.0, typed_rate_reason=False,
                      sale_line_id=order.order_line.id)
        trip = self._trip(lr)
        expense_account = self.company_data["default_account_expense"]

        def bill(amount, estimate_line=False):
            return self.env["account.move"].create({
                "move_type": "in_invoice", "partner_id": self.vendor.id, "invoice_date": fields.Date.today(),
                "invoice_line_ids": [Command.create({
                    "name": "Survey", "quantity": 1, "price_unit": amount, "account_id": expense_account.id,
                    "tms_trip_id": trip.id, "tms_cost_class": "trip_direct", "tax_ids": [],
                    "tms_estimate_line_id": estimate_line and estimate_line.id})]})
        b1 = bill(1000.0)
        with self.assertRaises(UserError):
            b1.action_post()
        b1.invoice_line_ids.write({"tms_overhead": True, "tms_overhead_reason": "Branch survey"})
        b1.action_post()
        b2 = bill(15000.0, eline)
        b2.action_post()
        eline.invalidate_recordset()
        self.assertAlmostEqual(eline.actual, 15000.0)
        b3 = bill(1000.0, eline)  # takes the line 6.7 % over: above the 5 % tolerance
        with self.assertRaises(UserError):
            b3.action_post()
        b3.with_user(self.u_manager).action_tms_approve_variance()
        b3.action_post()
        self.env.flush_all()
        pnl = self.env["tms.so.pnl.line"].search([("estimate_line_id", "=", eline.id)])
        self.assertAlmostEqual(pnl.actual, 16000.0)

    def test_charge_becomes_item(self):
        """BR-CHG-05."""
        order = self._order(km=100.0)
        self.env["tms.estimate.line"].create({"estimate_id": order.order_line.tms_estimate_id.id,
                                              "product_id": self.p_survey.id})
        order.action_confirm()
        lr = self._lr(confirm=True, rate_type=False, typed_rate=0.0, typed_rate_reason=False,
                      sale_line_id=order.order_line.id)
        trip = self._trip(lr)
        cat = self.env.ref("tms_core.tms_category_detention")
        charge = self.env["tms.trip.charge"].create({"trip_id": trip.id, "consignment_id": lr.id,
                                                     "category_id": cat.id, "amount": 2000.0})
        charge.action_confirm()
        self.assertEqual(charge.sale_line_id.order_id, order)
        self.assertEqual(charge.sale_line_id.tms_item_no, 20)
        self.assertAlmostEqual(charge.sale_line_id.price_unit, 2000.0)

    def test_invoice_and_pnl(self):
        order = self._order(km=100.0)
        self.env["tms.estimate.line"].create({"estimate_id": order.order_line.tms_estimate_id.id,
                                              "product_id": self.p_survey.id})
        order.action_confirm()
        lr = self._lr(confirm=True, rate_type=False, typed_rate=0.0, typed_rate_reason=False,
                      sale_line_id=order.order_line.id)
        trip = self._trip(lr)
        self._deliver_and_verify(trip)
        move = self.env["tms.invoice.lr.wizard"]._tms_create_invoices(lr)
        self.assertEqual(move.invoice_line_ids.tms_sale_line_id, order.order_line)
        move.action_post()
        self.assertEqual(lr.state, "invoiced")
        self.assertTrue(lr.freight_frozen)
        self.env.flush_all()
        pnl = self.env["tms.so.pnl"].search([("sale_line_id", "=", order.order_line.id)])
        self.assertAlmostEqual(pnl.revenue, order.order_line.price_unit, places=2)

    def test_amendment(self):
        """BR-EST-12."""
        order = self._order(km=100.0)
        est = order.order_line.tms_estimate_id
        self.env["tms.estimate.line"].create({"estimate_id": est.id, "product_id": self.p_survey.id})
        order.action_confirm()
        old_amount = est.amount
        wiz = self.env["tms.estimate.amendment.wizard"].create({"estimate_id": est.id, "reason": "Longer route"})
        wiz.action_open()
        self.assertEqual(est.state, "amending")
        est.write({"distance_km": 120.0})
        am = est.amendment_ids
        am.action_submit()
        with self.assertRaises(UserError):
            am.action_approve()  # same user
        am.with_user(self.u_manager).action_approve()
        self.assertEqual(est.state, "locked")
        self.assertEqual(est.version, 2)
        self.assertEqual(am.baseline_amount, old_amount)
        self.assertAlmostEqual(order.order_line.price_unit, est.amount)

    def test_crm(self):
        """BR-CRM-02..06."""
        lead = self.env["crm.lead"].create({"name": "Transformer moves", "type": "opportunity",
                                            "partner_id": self.customer.id})
        lead.write({"tms_lane_ids": [Command.create({"origin_id": self.loc_bhiwandi.id,
                                                     "destination_id": self.loc_nagpur.id,
                                                     "loads_per_month": 4, "target_rate": 50000.0})]})
        self.assertAlmostEqual(lead.expected_revenue, 4 * 50000.0 * 12)
        with self.assertRaises(UserError):
            lead.action_set_lost()
        order = self._order(km=100.0, opportunity_id=lead.id)
        self.env["tms.estimate.line"].create({"estimate_id": order.order_line.tms_estimate_id.id,
                                              "product_id": self.p_survey.id})
        order.action_quotation_sent()
        self.assertEqual(lead.stage_id, self.env.ref("crm.stage_lead3"))
        order.action_confirm()
        self.assertTrue(lead.stage_id.is_won)
