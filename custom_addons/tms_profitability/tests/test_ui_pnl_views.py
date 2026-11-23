from odoo.tests import TransactionCase, tagged
from odoo.tools.safe_eval import safe_eval


@tagged("post_install", "-at_install")
class TestUiPnlViews(TransactionCase):
    """Trip, sales order and truck P&L open as charts with the defaults the screens use."""

    def _views(self, model, *kinds):
        return self.env[model].get_views([(False, kind) for kind in kinds])["views"]

    def test_graph_views_load(self):
        for model, row, measure in (("tms.trip.pnl", "trip_id", "contribution"),
                                    ("tms.so.pnl", "order_id", "contribution"),
                                    ("tms.truck.pnl", "vehicle_id", "operating_result")):
            arch = self._views(model, "graph")["graph"]["arch"]
            self.assertIn('type="bar"', arch, model)
            self.assertIn('name="%s"' % row, arch, model)
            self.assertIn('name="%s"' % measure, arch, model)
        for xmlid in ("tms_profitability.action_tms_trip_pnl", "tms_estimate.action_tms_so_pnl",
                      "tms_profitability.action_tms_truck_pnl"):
            self.assertIn("graph", self.env.ref(xmlid).view_mode.split(","), xmlid)

    def test_truck_pnl_defaults(self):
        action = self.env.ref("tms_profitability.action_tms_truck_pnl")
        ctx = safe_eval(action.context)
        self.assertEqual(ctx.get("search_default_filter_period"), 1)
        self.assertEqual(ctx.get("search_default_group_vehicle"), 1)
        views = self._views("tms.truck.pnl", "list", "search")
        self.assertIn('default_period="month"', views["search"]["arch"])
        self.assertIn('name="below_zero"', views["search"]["arch"])
        self.assertIn('widget="badge"', views["list"]["arch"])
        # the filter domain runs against the view
        self.env["tms.truck.pnl"].search([("operating_result", "<", 0)])

    def test_trip_pnl_fleet_badge(self):
        arch = self._views("tms.trip.pnl", "list")["list"]["arch"]
        self.assertIn('widget="badge"', arch)
