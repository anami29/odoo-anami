from odoo import models


class TmsKpi(models.Model):
    _inherit = "tms.kpi"

    def _tms_period_trips(self):
        self.env.flush_all()
        self.env["tms.trip.pnl"].invalidate_model()
        return self._kpi_search("tms.trip.pnl", self._branches() + [("date", ">=", self._period_start())])

    def _kpi_kpi_13(self):
        """KPI-13: trip margin over delivered trips, and as a share of trip revenue."""
        rows = self._tms_period_trips()
        return len(rows), sum(rows.mapped("trip_margin")), "tms.trip.pnl", [("id", "in", rows.ids)]

    def _kpi_kpi_14(self):
        """KPI-14: delivered trips with a negative trip margin."""
        rows = self._tms_period_trips().filtered(lambda r: r.trip_margin < 0)
        return len(rows), sum(rows.mapped("trip_margin")), "tms.trip.pnl", [("id", "in", rows.ids)]

    def _kpi_kpi_18(self):
        """KPI-18: standard charge as a share of actual truck cost; trucks below 90% for the period."""
        self.env.flush_all()
        self.env["tms.truck.pnl"].invalidate_model()
        period = self._period_start().replace(day=1)  # a filter date inside the month reads that month
        rows = self._kpi_search("tms.truck.pnl", self._branches() + [("period", "=", period),
                                                                   ("fleet_type", "=", "owned")])
        low = rows.filtered(lambda r: (r.running_actual + r.fixed_actual) and r.recovery_pct < 90.0)
        charged = sum(rows.mapped("running_charged")) + sum(rows.mapped("fixed_charged"))
        actual = sum(rows.mapped("running_actual")) + sum(rows.mapped("fixed_actual"))
        fleet_pct = (charged / actual * 100.0) if actual else 0.0
        return len(low), fleet_pct, "tms.truck.pnl", [("id", "in", low.ids)]

    def _compute_value(self):
        super()._compute_value()
        for kpi in self.filtered(lambda k: k.code == "KPI-13"):
            rows = kpi._tms_period_trips()
            revenue = sum(rows.mapped("revenue"))
            share = (kpi.value_amount / revenue * 100.0) if revenue else 0.0
            kpi.alert = bool(rows) and share < self.env.company.tms_margin_target_pct
