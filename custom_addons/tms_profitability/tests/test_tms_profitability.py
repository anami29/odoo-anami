from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged
from odoo.tools import float_round

from odoo.addons.tms_estimate.tests.test_tms_estimate import EstimateCommon


class PnlCommon(EstimateCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.today = fields.Date.today()
        cls.month = cls.today.replace(day=1)
        # #18.3: 32 ft MXL, 72,000 km a year, running 5,04,000, fixed 3,42,000 → 7.00 + 4.75 = 11.75 per km
        cls.rate = cls.env["tms.std.rate"].with_user(cls.u_finance).create({
            "vehicle_class_id": cls.vehicle_class.id,
            "date_from": cls.today - timedelta(days=400),
            "running_budget": 504000.0,
            "fixed_budget": 342000.0,
        })
        cls.expense_account = cls.company_data["default_account_expense"]
        cls.cat_repair = cls.env.ref("tms_profitability.truck_category_repairs")
        cls.cat_insurance = cls.env.ref("tms_profitability.truck_category_insurance")
        cls.cat_salary = cls.env.ref("tms_profitability.truck_category_driver_salary")
        cls.fuel = cls.env.ref("tms_core.tms_category_fuel")
        cls.company.tms_capitalisation_threshold = 50000.0

    def _bill(self, lines, date=None, partner=None):
        date = date or self.today
        return self.env["account.move"].create({
            "move_type": "in_invoice",
            "partner_id": (partner or self.vendor).id,
            "invoice_date": date,
            "date": date,
            "invoice_line_ids": [Command.create(dict({"name": "Line", "quantity": 1, "tax_ids": [],
                                                      "account_id": self.expense_account.id}, **vals))
                                 for vals in lines],
        })

    def _expense(self, trip, amount, category=None, **kw):
        exp = self.env["tms.trip.expense"].create(dict({
            "trip_id": trip.id, "category_id": (category or self.fuel).id, "amount": amount,
            "payment_mode": "fuel_card", "receipt": b"UkNQ"}, **kw))
        exp.with_user(self.u_accountant).action_approve()
        return exp

    def _invoice(self, lrs):
        moves = self.env["tms.invoice.lr.wizard"]._tms_create_invoices(lrs)
        moves.action_post()
        return moves

    def _delivered_trip(self, km=590.0, weight=10000.0, rate=2.86, fleet="owned", **kw):
        lr = self._lr(weight=weight, rate=rate, **kw)
        trip = self._trip(lr, fleet=fleet)
        self._deliver_and_verify(trip)
        if fleet == "owned":
            trip.end_odometer = 10000.0 + km
        return trip, lr

    def _close(self, date_from=None, company=None):
        return self.env["tms.pnl.close"].with_user(self.u_finance).create({
            "company_id": (company or self.company).id, "date_from": date_from or self.month})


@tagged("post_install", "-at_install")
class TestStandardRate(PnlCommon):
    def test_rate_example(self):
        """#18.3: 5,04,000 / 72,000 = 7.00 running, 3,42,000 / 72,000 = 4.75 fixed, 11.75 standard."""
        self.assertAlmostEqual(self.rate.planned_km, 72000.0)
        self.assertAlmostEqual(self.rate.running_rate, 7.0)
        self.assertAlmostEqual(self.rate.fixed_rate, 4.75)
        self.assertAlmostEqual(self.rate.standard_rate, 11.75)
        self.assertAlmostEqual(self.vehicle_class.current_standard_rate, 11.75)
        # BR-PNL-15: owned-truck lines of a cost estimate are priced at the standard rate
        line = self.env["tms.estimate.line"]
        self.assertAlmostEqual(line._tms_standard_rate(self.vehicle_class, self.today), 11.75)
        self.assertIsNone(line._tms_standard_rate(self.env["tms.vehicle.class"].create(
            {"name": "Trailer 40 ft", "planned_annual_km": 60000}), self.today))

    def test_rate_finance_head_only(self):
        """BR-PNL-13: only the Finance Head maintains rates; a rate in use is never changed."""
        with self.assertRaises(UserError):
            self.env["tms.std.rate"].with_user(self.u_manager).create({
                "vehicle_class_id": self.vehicle_class.id, "date_from": self.today,
                "running_budget": 1.0, "fixed_budget": 1.0})
        with self.assertRaises(UserError):
            self.rate.with_user(self.u_accountant).write({"running_budget": 600000.0})
        trip = self._trip(self._lr())
        self.assertEqual(trip.tms_std_rate_id, self.rate)  # BR-PNL-17: START-date rate
        self.assertTrue(self.rate.used)
        with self.assertRaises(UserError):
            self.rate.with_user(self.u_finance).write({"running_budget": 600000.0})
        # a back-dated rate may not take over a trip already started under the earlier rate
        with self.assertRaises(ValidationError):
            self.env["tms.std.rate"].with_user(self.u_finance).create({
                "vehicle_class_id": self.vehicle_class.id, "date_from": self.today - timedelta(days=10),
                "running_budget": 600000.0, "fixed_budget": 342000.0})
        new = self.env["tms.std.rate"].with_user(self.u_finance).create({
            "vehicle_class_id": self.vehicle_class.id, "date_from": self.today + timedelta(days=1),
            "running_budget": 576000.0, "fixed_budget": 342000.0})
        self.assertAlmostEqual(new.running_rate, 8.0)
        self.assertEqual(trip.tms_std_rate_id, self.rate)


@tagged("post_install", "-at_install")
class TestTripMargin(PnlCommon):
    def test_margin_example(self):
        """#23.4: TMS/00031 gives 6,240 − 590 × 11.75 = −693 (BR-LED-05, BR-PNL-17)."""
        trip, lr = self._delivered_trip(km=590.0)
        self.assertEqual(trip.tms_pnl_date, self.today)  # OP-14 delivery basis
        self.assertTrue(trip.tms_analytic_account_id)  # BR-LED-01
        self._invoice(lr)
        self._expense(trip, 22360.0)
        trip.invalidate_recordset()
        self.assertAlmostEqual(trip.tms_pnl_revenue, 28600.0)
        self.assertAlmostEqual(trip.tms_pnl_direct, 22360.0)
        self.assertAlmostEqual(trip.tms_pnl_contribution, 6240.0)
        self.assertAlmostEqual(trip.tms_std_km, 590.0)
        self.assertAlmostEqual(trip.tms_std_charge, 6932.5)
        self.assertFalse(trip.tms_std_charge_posted)
        self.assertAlmostEqual(trip.tms_trip_margin, -692.5)
        self.assertEqual(float_round(trip.tms_trip_margin, 0, rounding_method="HALF-UP"), -693)
        # KPI-14: a loss-making trip
        kpi = self.env["tms.kpi"].search([("code", "=", "KPI-14")])
        self.assertEqual(kpi.value_count, 1)
        # BR-LED-01, BR-LED-08: the bill and invoice lines carry the Trips, Vehicles and Branch plans
        exp_line = trip.expense_ids.move_id.line_ids.filtered(lambda line: line.debit)
        key = next(iter(exp_line.analytic_distribution))
        ids = {int(i) for i in key.split(",")}
        self.assertIn(trip.tms_analytic_account_id.id, ids)
        self.assertIn(self.vehicle.tms_analytic_account_id.id, ids)
        self.assertIn(self.company.tms_analytic_account_id.id, ids)
        analytic = self.env["account.analytic.line"].search([("move_line_id", "=", exp_line.id)])
        self.assertAlmostEqual(analytic.amount, -22360.0)
        # the standard charge posted by the close: analytic entries only, no journal item (RL-09)
        close = self._close()
        close.with_user(self.u_finance).action_run()
        trip.invalidate_recordset()
        self.assertTrue(trip.tms_std_charge_posted)
        self.assertAlmostEqual(trip.tms_trip_margin, -692.5)
        std = close.std_line_ids
        self.assertEqual(len(std), 4)
        self.assertFalse(std.mapped("move_line_id"))
        self.assertAlmostEqual(sum(std.filtered(lambda line: line.tms_std_side == "trip").mapped("amount")), -6932.5)
        self.assertAlmostEqual(sum(std.mapped("amount")), 0.0)

    def test_empty_return_setting(self):
        """#18.3, BR-CFG-14: the empty return counts in trip km unless the setting says otherwise."""
        trip, _lr = self._delivered_trip(km=700.0)
        trip.tms_return_km = 110.0
        self.assertAlmostEqual(trip.tms_std_km, 700.0)
        self.company.tms_empty_return_in_km = False
        self.assertAlmostEqual(trip.tms_std_km, 590.0)


@tagged("post_install", "-at_install")
class TestCostClasses(PnlCommon):
    def test_refusals(self):
        """BR-PNL-01..04, BR-PNL-09 at posting."""
        trip = self._trip(self._lr())
        market_trip = self._trip(self._lr(), fleet="market")
        market = market_trip.primary_vehicle_id
        self.assertEqual(market.tms_ownership, "market")
        self.assertTrue(market.tms_analytic_account_id)  # BR-LED-01: market vehicles too
        # BR-PNL-01: no class
        with self.assertRaises(UserError):
            self._bill([{"price_unit": 500.0}]).action_post()
        # BR-PNL-03: running needs an owned vehicle, a category of its pool and no trip
        with self.assertRaises(UserError):
            self._bill([{"price_unit": 500.0, "tms_cost_class": "truck_running", "vehicle_id": self.vehicle.id}]
                       ).action_post()
        with self.assertRaises(UserError):
            self._bill([{"price_unit": 500.0, "tms_cost_class": "truck_running", "vehicle_id": self.vehicle.id,
                         "tms_truck_category_id": self.cat_insurance.id}]).action_post()
        with self.assertRaises(UserError):
            self._bill([{"price_unit": 500.0, "tms_cost_class": "truck_running", "vehicle_id": self.vehicle.id,
                         "tms_truck_category_id": self.cat_repair.id, "tms_trip_id": trip.id}]).action_post()
        # BR-PNL-09: a running or fixed line naming a market vehicle
        with self.assertRaises(UserError):
            self._bill([{"price_unit": 500.0, "tms_cost_class": "truck_running", "vehicle_id": market.id,
                         "tms_truck_category_id": self.cat_repair.id}]).action_post()
        # BR-PNL-02: a trip direct line names the trip's primary truck
        with self.assertRaises(UserError):
            self._bill([{"price_unit": 500.0, "tms_cost_class": "trip_direct", "tms_trip_id": trip.id,
                         "vehicle_id": self.vehicle2.id}]).action_post()
        ok = self._bill([{"price_unit": 500.0, "tms_trip_id": trip.id}])
        ok.action_post()
        line = ok.invoice_line_ids
        self.assertEqual(line.tms_cost_class, "trip_direct")
        self.assertEqual(line.vehicle_id, self.vehicle)
        # BR-PNL-04: an overhead line names no vehicle or trip
        oh = self._bill([{"price_unit": 900.0, "tms_cost_class": "overhead", "vehicle_id": self.vehicle.id,
                          "tms_trip_id": trip.id}])
        oh.action_post()
        self.assertFalse(oh.invoice_line_ids.vehicle_id)
        self.assertFalse(oh.invoice_line_ids.tms_trip_id)
        self.assertEqual(oh.invoice_line_ids.analytic_distribution,
                         {str(self.company.tms_analytic_account_id.id): 100.0})
        # a running line with its category posts to the truck's pool, analytic on Vehicles and Branch
        run = self._bill([{"price_unit": 4800.0, "vehicle_id": self.vehicle.id,
                           "tms_truck_category_id": self.cat_repair.id, "tms_cost_class": "truck_running"}])
        run.action_post()
        self.assertEqual(run.state, "posted")
        # BR-PNL-09 on expenses, BR-PNL-08: a roadside repair is truck running and names no trip
        repair = self.env.ref("tms_core.tms_category_roadside_repair")
        with self.assertRaises(UserError):
            self._expense(market_trip, 6800.0, category=repair)
        exp = self._expense(trip, 6800.0, category=repair)
        debit = exp.move_id.line_ids.filtered(lambda line: line.debit)
        self.assertEqual(debit.tms_cost_class, "truck_running")
        self.assertEqual(debit.tms_truck_category_id, self.cat_repair)
        self.assertFalse(debit.tms_trip_id)
        self.assertEqual(debit.vehicle_id, self.vehicle)

    def test_prepaid_and_spread(self):
        """BR-PNL-10: a fixed cost covering more than one month posts to prepaid and releases monthly;
        BR-PNL-11: a repair at or above the threshold is spread over its useful life."""
        start = self.month - relativedelta(months=2)
        bill = self._bill([{"price_unit": 120000.0, "vehicle_id": self.vehicle.id, "tms_cost_class": "truck_fixed",
                            "tms_truck_category_id": self.cat_insurance.id, "tms_cover_from": start,
                            "tms_cover_to": start + relativedelta(years=1, days=-1)}])
        bill.action_post()
        line = bill.invoice_line_ids
        self.assertEqual(line.account_id, self.company.tms_prepaid_account_id)
        sch = line.tms_prepaid_schedule_id
        self.assertEqual(sch.months, 12)
        self.assertEqual(len(sch.line_ids), 12)
        self.assertAlmostEqual(sch.line_ids[0].amount, 10000.0)
        self.env["tms.prepaid.schedule"]._cron_release_prepaid()
        released = sch.line_ids.filtered(lambda r: r.state == "released")
        self.assertEqual(len(released), 2)
        rel = released[0].move_id.line_ids.filtered(lambda r: r.debit)
        self.assertEqual(rel.account_id, self.expense_account)
        self.assertEqual(rel.tms_cost_class, "truck_fixed")
        self.assertEqual(rel.date, (start + relativedelta(day=31)))
        # a monthly fixed cost (driver salary) books straight to the pool, its cover period defaulted
        sal = self._bill([{"price_unit": 28500.0, "vehicle_id": self.vehicle.id, "tms_cost_class": "truck_fixed",
                           "tms_truck_category_id": self.cat_salary.id}])
        sal.action_post()
        self.assertEqual(sal.invoice_line_ids.account_id, self.expense_account)
        self.assertEqual(sal.invoice_line_ids.tms_cover_from, self.today.replace(day=1))
        # BR-PNL-11
        big = self._bill([{"price_unit": 120000.0, "vehicle_id": self.vehicle.id, "tms_cost_class": "truck_running",
                           "tms_truck_category_id": self.cat_repair.id}])
        with self.assertRaises(UserError):
            big.action_post()
        big.invoice_line_ids.tms_useful_life_months = 24
        big.action_post()
        spread = big.invoice_line_ids.tms_prepaid_schedule_id
        self.assertEqual(spread.kind, "spread")
        self.assertEqual(spread.months, 24)
        self.assertAlmostEqual(spread.line_ids[0].amount, 5000.0)


@tagged("post_install", "-at_install")
class TestClose(PnlCommon):
    def test_close_accrual_rerun_frozen(self):
        """#18.3 order, BR-PNL-22 re-run and frozen period, BR-PNL-36 accrual and reversal."""
        trip, lr = self._delivered_trip(km=590.0)
        market_trip, _mlr = self._delivered_trip(fleet="market")
        close = self._close()
        with self.assertRaises(UserError):
            close.with_user(self.u_dispatcher).action_run()
        close.with_user(self.u_accountant).action_run()
        self.assertEqual(close.state, "computed")
        accruals = close.move_ids
        self.assertEqual(len(accruals), 2)
        self.assertTrue(all(m.state == "draft" and m.date == close.date_to for m in accruals))
        self.assertAlmostEqual(close.accrued_revenue, 2 * 28600.0)
        self.assertAlmostEqual(close.accrued_cost, 30000.0)  # open hire of the market trip
        self.assertEqual(len(close.std_line_ids), 4)
        self.assertAlmostEqual(close.std_charge_total, 6932.5)
        self.assertEqual(close.difference, 0.0)
        # a re-run before close replaces the earlier run
        first = accruals.ids
        close.with_user(self.u_accountant).action_run()
        self.assertEqual(close.run_count, 2)
        self.assertFalse(self.env["account.move"].browse(first).exists())
        self.assertEqual(len(close.move_ids), 2)
        self.assertEqual(len(close.std_line_ids), 4)
        # only the Finance Head closes
        with self.assertRaises(UserError):
            close.with_user(self.u_accountant).action_close()
        close.with_user(self.u_finance).action_close()
        self.assertEqual(close.state, "closed")
        posted = close.move_ids.filtered(lambda m: m.date == close.date_to)
        reversals = close.move_ids - posted
        self.assertEqual(len(posted), 2)
        self.assertEqual(len(reversals), 2)
        self.assertTrue(all(m.state == "posted" for m in close.move_ids))
        self.assertTrue(all(m.date == close.date_to + timedelta(days=1) for m in reversals))
        self.env.flush_all()
        self.env.invalidate_all()
        rows = self.env["tms.pnl.line"].search([("trip_id", "=", trip.id), ("kind", "=", "accrued_revenue")])
        self.assertAlmostEqual(sum(rows.filtered(lambda r: r.date == close.date_to).mapped("amount")), 28600.0)
        self.assertAlmostEqual(sum(rows.mapped("amount")), 0.0)
        # frozen: no re-run, no change to the standard charge
        with self.assertRaises(UserError):
            close.with_user(self.u_finance).action_run()
        with self.assertRaises(UserError):
            close.std_line_ids[:1].with_context(tms_close_run=True).unlink()
        # a late line naming a trip of the closed period posts to the next open period
        nxt = close.date_to + timedelta(days=1)
        moves = self._invoice(lr)
        self.assertEqual(moves.invoice_line_ids.filtered("tms_consignment_id").tms_pnl_late_date, nxt)
        exp = self._expense(trip, 1500.0, date=self.today)
        self.assertEqual(exp.move_id.line_ids.filtered("tms_trip_id").tms_pnl_late_date, nxt)
        # a truck cost dated in the closed period is refused
        with self.assertRaises(UserError):
            self._bill([{"price_unit": 4800.0, "vehicle_id": self.vehicle.id, "tms_cost_class": "truck_running",
                         "tms_truck_category_id": self.cat_repair.id}]).action_post()
        # posted entries of a closed period are reversed, never reset; a late line lives in the open period
        with self.assertRaises(UserError):
            posted[0].button_draft()
        exp.move_id.button_draft()

    def test_difference_blocks_close(self):
        """BR-PNL-31, BR-NTF-12: revenue of an LR on no trip is in the customer view only; the period cannot
        close until the difference is zero."""
        lr = self._lr()
        inv = self.env["account.move"].create({
            "move_type": "out_invoice", "partner_id": self.customer.id, "invoice_date": self.today,
            "invoice_line_ids": [Command.create({"name": "Freight", "quantity": 1, "price_unit": 1000.0,
                                                 "tax_ids": [], "tms_consignment_id": lr.id,
                                                 "tms_line_kind": "freight"})]})
        inv.action_post()
        close = self._close()
        close.with_user(self.u_finance).action_run()
        self.assertAlmostEqual(close.difference, 1000.0)
        self.assertEqual(len(close.diff_ids), 1)
        self.assertTrue(close.activity_ids.filtered(lambda a: a.user_id == self.u_finance))
        with self.assertRaises(UserError):
            close.with_user(self.u_finance).action_close()
        self.assertEqual(close.state, "computed")

    def test_truck_pnl_and_review_flag(self):
        """#18.5: 715 own km against a 6,000 km month; running charged 5,005 against 30,100; fixed charged
        3,396.25 against 28,500, all volume variance; recovery variance −50,199 (BR-PNL-19, BR-PNL-28)."""
        trip, _lr = self._delivered_trip(km=715.0)
        trip.action_done()
        self.assertAlmostEqual(trip.own_km, 715.0)
        self._bill([{"price_unit": 30100.0, "vehicle_id": self.vehicle.id, "tms_cost_class": "truck_running",
                     "tms_truck_category_id": self.cat_repair.id}]).action_post()
        self._bill([{"price_unit": 28500.0, "vehicle_id": self.vehicle.id, "tms_cost_class": "truck_fixed",
                     "tms_truck_category_id": self.cat_salary.id}]).action_post()
        # two earlier closed months with adverse running variance
        for i in (2, 1):
            past = self.env["tms.pnl.close"].sudo().create({"company_id": self.company.id,
                                                           "date_from": self.month - relativedelta(months=i)})
            self.env["tms.pnl.close.truck"].sudo().create({"close_id": past.id, "vehicle_id": self.vehicle.id,
                                                          "cost_pool": "running", "charged": 1000.0, "actual": 2000.0})
            past.state = "closed"
        close = self._close()
        close.with_user(self.u_finance).action_run()
        self.env.flush_all()
        self.env.invalidate_all()
        row = self.env["tms.truck.pnl"].search([("vehicle_id", "=", self.vehicle.id), ("period", "=", self.month)])
        self.assertAlmostEqual(row.running_charged, 5005.0)
        self.assertAlmostEqual(row.fixed_charged, 3396.25)
        self.assertAlmostEqual(row.running_actual, 30100.0)
        self.assertAlmostEqual(row.fixed_actual, 28500.0)
        self.assertAlmostEqual(row.recovery_variance, -50198.75)
        self.assertAlmostEqual(row.planned_km, 6000.0)
        self.assertAlmostEqual(row.odometer_km, 715.0)
        self.assertAlmostEqual(row.volume_variance, -25103.75)
        self.assertAlmostEqual(row.fixed_spending_variance, 0.0)
        self.assertAlmostEqual(row.fixed_other_variance, 0.0)
        self.assertAlmostEqual(row.running_variance, -25095.0)
        self.assertAlmostEqual(row.operating_result, row.trip_margin + row.recovery_variance)
        stmt = self.env["tms.recovery.statement"].search([("vehicle_id", "=", self.vehicle.id),
                                                          ("period", "=", self.month)])
        self.assertEqual(set(stmt.mapped("cost_pool")), {"running", "fixed"})
        self.assertEqual(len(close.truck_ids), 2)
        # KPI-18: the truck recovers far below 90%
        kpi = self.env["tms.kpi"].search([("code", "=", "KPI-18")])
        self.assertEqual(kpi.value_count, 1)
        # BR-PNL-31: the branch view reconciles
        branch = self.env["tms.branch.pnl"].search([("company_id", "=", self.company.id),
                                                    ("period", "=", self.month)])
        self.assertAlmostEqual(branch.check_difference, 0.0)
        # BR-PNL-21: the third adverse closed month flags the truck and tells the Fleet Manager
        close.with_user(self.u_finance).action_close()
        self.assertTrue(self.vehicle.tms_review_flag)
        self.assertTrue(self.vehicle.activity_ids.filtered(lambda a: a.user_id == self.u_fleet))


@tagged("post_install", "-at_install")
class TestLegs(PnlCommon):
    def test_leg_split(self):
        """BR-PNL-25, BR-PNL-26: a trip moving an order item and an LR without one splits into two legs by
        charged weight; each leg carries its share of the direct cost and the standard charge."""
        order = self._order(km=590.0)
        est = order.order_line.tms_estimate_id
        eline = self.env["tms.estimate.line"].create({"estimate_id": est.id, "product_id": self.p_survey.id})
        order.action_confirm()
        lr1 = self._lr(weight=6000.0, confirm=True, rate_type=False, typed_rate=0.0, typed_rate_reason=False,
                       sale_line_id=order.order_line.id)
        lr2 = self._lr(weight=4000.0)
        trip = self._trip(lr1 | lr2)
        self._deliver_and_verify(trip)
        trip.end_odometer = 10590.0
        self._expense(trip, 10000.0, estimate_line_id=eline.id)
        close = self._close()
        close.with_user(self.u_finance).action_run()
        self.env.flush_all()
        self.env.invalidate_all()
        legs = self.env["tms.trip.leg"].search([("trip_id", "=", trip.id)])
        self.assertEqual(len(legs), 2)
        item_leg = legs.filtered("sale_line_id")
        lr_leg = legs - item_leg
        self.assertEqual(lr_leg.consignment_id, lr2)
        self.assertAlmostEqual(item_leg.share, 0.6)
        self.assertAlmostEqual(item_leg.direct_cost, 6000.0)
        self.assertAlmostEqual(lr_leg.direct_cost, 4000.0)
        self.assertAlmostEqual(item_leg.std_charge, 6932.5 * 0.6)
        self.assertAlmostEqual(sum(legs.mapped("leg_margin")), -16932.5)
        so_pnl = self.env["tms.so.pnl"].search([("sale_line_id", "=", order.order_line.id)])
        self.assertAlmostEqual(so_pnl.standard_charge, 6932.5 * 0.6)
        cust = self.env["tms.customer.pnl"].search([("partner_id", "=", self.customer.id),
                                                    ("period", "=", self.month)])
        self.assertAlmostEqual(sum(cust.mapped("trip_margin")), -16932.5)
        # BR-PNL-33, UT-PNL-56: customer, fleet and branch rows drill to their source rows
        drilled = self.env["tms.trip.leg"]
        for row in cust:
            drilled |= self.env["tms.trip.leg"].search(row.action_open_lines()["domain"])
        self.assertEqual(drilled, legs)
        fleet = self.env["tms.fleet.pnl"].search([("period", "=", self.month)])
        trips = self.env["tms.trip.pnl"].browse()
        for row in fleet:
            trips |= self.env["tms.trip.pnl"].search(row.action_open_lines()["domain"])
        self.assertIn(trip, trips.mapped("trip_id"))
        branch = self.env["tms.branch.pnl"].search([("company_id", "=", self.company.id), ("period", "=", self.month)])
        lines = self.env["tms.pnl.line"].search(branch.action_open_lines()["domain"])
        self.assertTrue(lines.filtered(lambda line: line.trip_id == trip))
        # BR-PNL-34: a Sales Executive sees the customer view but no recovery variance
        self.env["tms.customer.pnl"].with_user(self.u_planner).search([])
        with self.assertRaises(AccessError):
            self.env["tms.truck.pnl"].with_user(self.u_planner).search([])
        with self.assertRaises(AccessError):
            self.env["tms.recovery.statement"].with_user(self.u_planner).search([])
