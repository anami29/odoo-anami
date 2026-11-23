from odoo import Command, fields
from odoo.tests import tagged

from .test_tms_estimate import EstimateCommon


@tagged("post_install", "-at_install")
class TestUiSoPnl(EstimateCommon):
    """Sales order P&L: group margins are contribution ÷ revenue, a chart, and a drill to estimate lines."""

    def _confirmed_order(self, product=None):
        order = self._order(km=100.0)
        eline = self.env["tms.estimate.line"].create({"estimate_id": order.order_line.tms_estimate_id.id,
                                                      "product_id": (product or self.p_survey).id})
        order.action_confirm()
        return order, eline

    def _two_orders_billed(self):
        """Two orders of one customer on one trip; only the first books its estimated cost."""
        order_a, eline_a = self._confirmed_order()
        # a bigger item: unequal revenues, so an average of row margins differs from the true margin
        order_b, _eline_b = self._confirmed_order(self.p_lifting)
        lrs = self.env["tms.consignment"]
        for order in (order_a, order_b):
            lrs |= self._lr(confirm=True, rate_type=False, typed_rate=0.0, typed_rate_reason=False,
                            sale_line_id=order.order_line.id)
        trip = self._trip(lrs)
        self._deliver_and_verify(trip)
        for lr in lrs:
            self.env["tms.invoice.lr.wizard"]._tms_create_invoices(lr).action_post()
        bill = self.env["account.move"].create({
            "move_type": "in_invoice", "partner_id": self.vendor.id, "invoice_date": fields.Date.today(),
            "invoice_line_ids": [Command.create({
                "name": "Survey", "quantity": 1, "price_unit": 15000.0,
                "account_id": self.company_data["default_account_expense"].id,
                "tms_trip_id": trip.id, "tms_cost_class": "trip_direct", "tax_ids": [],
                "tms_estimate_line_id": eline_a.id})]})
        bill.action_post()
        self.env.flush_all()
        return order_a, order_b

    def test_group_margin_is_contribution_over_revenue(self):
        order_a, order_b = self._two_orders_billed()
        Pnl = self.env["tms.so.pnl"]
        domain = [("order_id", "in", (order_a | order_b).ids)]
        rows = Pnl.search(domain)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(rows.mapped("revenue")))
        revenue, contribution = sum(rows.mapped("revenue")), sum(rows.mapped("contribution"))
        expected = contribution / revenue * 100.0
        average = sum(rows.mapped("margin_pct")) / 2.0
        self.assertNotAlmostEqual(expected, average, places=2)
        # list group rows ask for the field, the pivot and graph for field:aggregator
        for spec in ("margin_pct", "margin_pct:avg"):
            groups = Pnl.read_group(domain, [spec, "revenue"], ["partner_id"])
            self.assertEqual(len(groups), 1)
            self.assertAlmostEqual(groups[0]["margin_pct"], expected, places=4)
            self.assertNotIn("tms_ratio_contribution", groups[0])
        # one item per order: the order's group margin is the item's own margin
        for group in Pnl.read_group(domain, ["margin_pct"], ["order_id"]):
            row = rows.filtered(lambda r, g=group: r.order_id.id == g["order_id"][0])
            self.assertAlmostEqual(group["margin_pct"], row.margin_pct, places=4)
        # planned margin and cost variance follow the same rule
        group = Pnl.read_group(domain, ["planned_margin_pct", "variance_pct"], ["partner_id"])[0]
        planned_rev = sum(rows.mapped("planned_revenue"))
        self.assertAlmostEqual(group["planned_margin_pct"],
                               sum(rows.mapped("planned_contribution")) / planned_rev * 100.0, places=4)
        est = sum(rows.mapped("estimated_cost"))
        self.assertAlmostEqual(group["variance_pct"], sum(rows.mapped("variance")) / est * 100.0, places=4)
        row_a = rows.filtered(lambda r: r.order_id == order_a)
        self.assertAlmostEqual(row_a.planned_contribution, row_a.planned_revenue - row_a.estimated_cost)
        self.assertAlmostEqual(row_a.variance_pct, 0.0)

    def test_drill_to_estimate_lines(self):
        order, eline = self._confirmed_order()
        self.env.flush_all()
        row = self.env["tms.so.pnl"].search([("order_id", "=", order.id)])
        action = row.action_open_estimate_lines()
        self.assertEqual(action["res_model"], "tms.so.pnl.line")
        lines = self.env["tms.so.pnl.line"].search(action["domain"])
        self.assertIn(eline, lines.estimate_line_id)
        self.assertEqual(lines.order_id, order)

    def test_views_and_defaults(self):
        views = self.env["tms.so.pnl"].get_views([(False, "list"), (False, "graph"), (False, "search")])["views"]
        self.assertIn("order_id", views["graph"]["arch"])
        self.assertIn("contribution", views["graph"]["arch"])
        self.assertIn("badge", views["list"]["arch"])
        self.assertIn("action_open_estimate_lines", views["list"]["arch"])
        action = self.env.ref("tms_estimate.action_tms_so_pnl")
        self.assertIn("graph", action.view_mode)
        self.assertIn("search_default_by_order", action.context)
