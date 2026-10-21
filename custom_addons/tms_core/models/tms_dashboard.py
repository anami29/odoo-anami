"""BR-DSH-01: dashboard filters by branch, customer, vehicle and period; BR-DSH-05: vehicle utilisation."""
from odoo import _, api, fields, models, tools

PARTNER_PATHS = ("billing_partner_id", "customer_id", "order_partner_id", "partner_id",
                 "consignment_link_ids.consignment_id.billing_partner_id", "consignment_id.billing_partner_id",
                 "trip_id.consignment_link_ids.consignment_id.billing_partner_id")
VEHICLE_PATHS = ("primary_vehicle_id", "vehicle_id", "trip_id.primary_vehicle_id")
# the customer filter reads the trip's customers on vendor-side records, and skips records with no customer
MODEL_PARTNER_PATHS = {
    "tms.vendor.bill.match": "trip_id.consignment_link_ids.consignment_id.billing_partner_id",
    "account.move.line": None,
    "tms.override.log": None,
    "tms.truck.pnl": None,
    "tms.vendor.pnl": None,
}


class TmsKpi(models.Model):
    _inherit = "tms.kpi"

    def _branches(self):
        ids = self.env.context.get("tms_kpi_company_ids")
        companies = self.env["res.company"].browse(ids) & self.env.companies if ids else self.env.companies
        return [("company_id", "in", companies.ids)]

    def _period_start(self):
        date_from = self.env.context.get("tms_kpi_date_from")
        return fields.Date.to_date(date_from) if date_from else super()._period_start()

    @api.model
    def _tms_path_exists(self, model, path):
        Model = self.env[model]
        for name in path.split("."):
            field = Model._fields.get(name)
            if not field or not (field.store or field.search):
                return False
            if field.relational:
                Model = self.env[field.comodel_name]
        return True

    @api.model
    def _tms_scope(self, model):
        """Customer and vehicle filters for one model, on the first path the model has."""
        ctx = self.env.context
        domain = []
        if ctx.get("tms_kpi_partner_id"):
            if model in MODEL_PARTNER_PATHS:
                path = MODEL_PARTNER_PATHS[model]
            else:
                path = next((p for p in PARTNER_PATHS if self._tms_path_exists(model, p)), None)
            if path:
                partner = self.env["res.partner"].browse(ctx["tms_kpi_partner_id"]).commercial_partner_id
                domain.append((path, "child_of", partner.id))
        if ctx.get("tms_kpi_vehicle_id"):
            path = next((p for p in VEHICLE_PATHS if self._tms_path_exists(model, p)), None)
            if path:
                domain.append((path, "=", ctx["tms_kpi_vehicle_id"]))
        return domain

    def _kpi_scope(self, model):
        return self._tms_scope(model)

    def action_drill(self):
        action = super().action_drill()
        if action and action.get("res_model"):
            action["domain"] = list(action["domain"]) + self._tms_scope(action["res_model"])
        return action


class TmsKpiFilter(models.TransientModel):
    _name = "tms.kpi.filter"
    _description = "Dashboard filter"

    company_ids = fields.Many2many("res.company", string="Branches", default=lambda s: s.env.companies)
    partner_id = fields.Many2one("res.partner", string="Customer")
    vehicle_id = fields.Many2one("fleet.vehicle")
    date_from = fields.Date(string="Period from")

    def action_apply(self):
        self.ensure_one()
        ctx = {
            "create": False,
            "tms_kpi_company_ids": self.company_ids.ids,
            "tms_kpi_partner_id": self.partner_id.id,
            "tms_kpi_vehicle_id": self.vehicle_id.id,
            "tms_kpi_date_from": fields.Date.to_string(self.date_from) if self.date_from else False,
        }
        parts = [p for p in (self.partner_id.display_name, self.vehicle_id.display_name,
                             self.date_from and _("from %s", self.date_from)) if p]
        return {
            "type": "ir.actions.act_window",
            "name": _("Exception dashboard") + (" – " + ", ".join(parts) if parts else ""),
            "res_model": "tms.kpi",
            "view_mode": "kanban,list",
            "context": ctx,
        }


class TmsVehicleUtilisation(models.Model):
    """BR-DSH-05: hours on trip and km run per owned vehicle and month, from the assignments."""

    _name = "tms.vehicle.utilisation"
    _description = "Vehicle utilisation"
    _auto = False
    _order = "month desc, vehicle_id"

    month = fields.Date(readonly=True)
    vehicle_id = fields.Many2one("fleet.vehicle", readonly=True)
    company_id = fields.Many2one("res.company", string="Branch", readonly=True)
    trip_count = fields.Integer(string="Trips", readonly=True)
    hours_on_trip = fields.Float(readonly=True)
    km_run = fields.Float(readonly=True)
    utilisation = fields.Float(string="Utilisation %", readonly=True, aggregator="avg")

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute("""
            CREATE OR REPLACE VIEW %s AS (
                SELECT row_number() OVER (ORDER BY s.month, s.vehicle_id, s.company_id) AS id,
                       s.month, s.vehicle_id, s.company_id, s.trip_count, s.hours_on_trip, s.km_run,
                       ROUND((100.0 * s.hours_on_trip / NULLIF(EXTRACT(EPOCH FROM
                           (s.month + INTERVAL '1 month') - s.month) / 3600.0, 0))::numeric, 1) AS utilisation
                  FROM (
                    -- each assignment's hours are split over the months it spans; its km go to its start month
                    SELECT m.month::date AS month, a.vehicle_id, a.company_id,
                           COUNT(DISTINCT a.trip_id) AS trip_count,
                           SUM(EXTRACT(EPOCH FROM (
                               LEAST(COALESCE(a.date_to, now() AT TIME ZONE 'UTC'), m.month + INTERVAL '1 month')
                               - GREATEST(a.date_from, m.month))) / 3600.0) AS hours_on_trip,
                           SUM(CASE WHEN date_trunc('month', a.date_from) = m.month
                                    THEN COALESCE(a.km_run, 0) ELSE 0 END) AS km_run
                      FROM tms_trip_assignment a
                      JOIN fleet_vehicle v ON v.id = a.vehicle_id
                      CROSS JOIN LATERAL generate_series(
                          date_trunc('month', a.date_from),
                          date_trunc('month', COALESCE(a.date_to, now() AT TIME ZONE 'UTC')),
                          INTERVAL '1 month') AS m(month)
                     WHERE a.fleet_type = 'owned'
                     GROUP BY 1, 2, 3
                  ) s
            )""" % self._table)
