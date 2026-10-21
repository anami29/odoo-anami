from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .analytic import POOLS


class TmsTruckCategory(models.Model):
    """BR-MD-15: truck cost categories name their pool, running or fixed."""

    _name = "tms.truck.category"
    _description = "Truck cost category"
    _inherit = ["mail.thread"]
    _order = "cost_pool, sequence, name"

    name = fields.Char(required=True, translate=True, tracking=True)
    code = fields.Char()
    sequence = fields.Integer(default=10)
    cost_pool = fields.Selection(POOLS, string="Pool", required=True, tracking=True)
    account_id = fields.Many2one("account.account", string="Default expense account")
    active = fields.Boolean(default=True)

    _sql_constraints = [("code_uniq", "unique(code)", "The truck cost category code must be unique.")]


class TmsStdRate(models.Model):
    """BR-PNL-13, BR-PNL-14: effective-dated running and fixed rates per km of a vehicle class.

    A rate is the pool's annual budget divided by the class's planned annual km; the record keeps both."""

    _name = "tms.std.rate"
    _description = "Standard rate per km"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "vehicle_class_id, date_from desc"
    _rec_name = "display_name"

    vehicle_class_id = fields.Many2one("tms.vehicle.class", string="Vehicle class", required=True, tracking=True,
                                       ondelete="restrict")
    company_id = fields.Many2one("res.company", string="Branch", help="Empty: every branch.", tracking=True)
    currency_id = fields.Many2one("res.currency", default=lambda s: s.env.company.currency_id, required=True)
    date_from = fields.Date(string="Effective from", required=True, tracking=True)
    planned_km = fields.Float(string="Planned annual km", required=True, tracking=True)
    running_budget = fields.Monetary(string="Running budget a year", required=True, tracking=True)
    fixed_budget = fields.Monetary(string="Fixed budget a year", required=True, tracking=True)
    running_rate = fields.Float(compute="_compute_rates", store=True, digits=(16, 4), string="Running (per km)")
    fixed_rate = fields.Float(compute="_compute_rates", store=True, digits=(16, 4), string="Fixed (per km)")
    standard_rate = fields.Float(compute="_compute_rates", store=True, digits=(16, 4), string="Standard (per km)")
    used = fields.Boolean(compute="_compute_used", help="A standard charge or a started trip uses this rate.")
    note = fields.Text()

    _sql_constraints = [
        ("class_date_uniq", "unique(vehicle_class_id, company_id, date_from)",
         "A vehicle class has one rate per effective date."),
        ("planned_km_positive", "CHECK(planned_km > 0)", "Planned annual km must be positive."),
    ]

    @api.depends("vehicle_class_id.name", "date_from")
    def _compute_display_name(self):
        for rate in self:
            rate.display_name = _("%(c)s from %(d)s", c=rate.vehicle_class_id.name or "", d=rate.date_from or "")

    @api.depends("planned_km", "running_budget", "fixed_budget")
    def _compute_rates(self):
        """BR-PNL-14: rate per km = annual budget of the pool / planned annual km of the class."""
        for rate in self:
            km = rate.planned_km or 0.0
            rate.running_rate = round(rate.running_budget / km, 4) if km else 0.0
            rate.fixed_rate = round(rate.fixed_budget / km, 4) if km else 0.0
            rate.standard_rate = rate.running_rate + rate.fixed_rate

    def _compute_used(self):
        Line = self.env["account.analytic.line"].sudo()
        Trip = self.env["tms.order"].sudo()
        for rate in self:
            rate.used = bool(rate.id) and bool(
                Line.search_count([("tms_std_rate_id", "=", rate.id)], limit=1)
                or Trip.search_count([("tms_std_rate_id", "=", rate.id)], limit=1))

    @api.onchange("vehicle_class_id")
    def _onchange_vehicle_class(self):
        if self.vehicle_class_id and not self.planned_km:
            self.planned_km = self.vehicle_class_id.planned_annual_km

    # ------------------------------------------------------------------ BR-PNL-13: Finance Head only
    def _tms_check_finance_head(self):
        if not self.env.su and not self.env.user.has_group("tms_core.group_finance_head"):
            raise UserError(_("Only the Finance Head maintains standard rates (BR-PNL-13)."))

    @api.model_create_multi
    def create(self, vals_list):
        self._tms_check_finance_head()
        for vals in vals_list:
            if vals.get("vehicle_class_id") and not vals.get("planned_km"):
                vals["planned_km"] = self.env["tms.vehicle.class"].browse(vals["vehicle_class_id"]).planned_annual_km
        rates = super().create(vals_list)
        rates._tms_check_backdating()
        return rates

    def write(self, vals):
        self._tms_check_finance_head()
        frozen = {"vehicle_class_id", "company_id", "date_from", "planned_km", "running_budget", "fixed_budget"}
        if frozen & set(vals) and any(rate.used for rate in self):
            raise UserError(_("This rate is in use; record a new effective-dated rate instead (BR-PNL-13)."))
        res = super().write(vals)
        if {"date_from", "vehicle_class_id"} & set(vals):
            self._tms_check_backdating()
        return res

    def unlink(self):
        self._tms_check_finance_head()
        if any(rate.used for rate in self):
            raise UserError(_("A rate in use is never deleted (BR-AUD-04)."))
        return super().unlink()

    def _tms_check_backdating(self):
        """BR-PNL-13: a new rate never applies to trips started before its effective date, nor changes the
        rate of a trip already started under another rate."""
        Trip = self.env["tms.order"].sudo()
        for rate in self:
            clash = Trip.search([
                ("tms_std_rate_id.vehicle_class_id", "=", rate.vehicle_class_id.id),
                ("tms_std_rate_id", "!=", rate.id),
                ("tms_std_rate_id.date_from", "<", rate.date_from),
                ("date_start", ">=", fields.Datetime.to_datetime(rate.date_from)),
            ], limit=1)
            if clash:
                raise ValidationError(_(
                    "Trip %(t)s started on %(d)s under the earlier rate; date the new rate after it (BR-PNL-13).",
                    t=clash.name, d=clash.date_start))

    @api.model
    def _tms_rate_for(self, vehicle_class, date, company=None):
        """The rate in force on ``date`` for the class, a branch's own rate before the company-wide one."""
        if not vehicle_class or not date:
            return self.browse()
        company = company or self.env.company
        rates = self.sudo().search([("vehicle_class_id", "=", vehicle_class.id),
                                    ("date_from", "<=", fields.Date.to_date(date)),
                                    ("company_id", "in", [company.id, False])], order="date_from desc, id desc")
        if not rates:
            return rates
        latest = rates[0].date_from
        same_day = rates.filtered(lambda r: r.date_from == latest)
        return (same_day.filtered("company_id") or same_day)[:1]

    def action_tms_view_trips(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "name": _("Trips charged at this rate"), "res_model": "tms.order",
                "view_mode": "list,form", "domain": [("tms_std_rate_id", "=", self.id)]}


class TmsVehicleClass(models.Model):
    _inherit = "tms.vehicle.class"

    std_rate_ids = fields.One2many("tms.std.rate", "vehicle_class_id", string="Standard rates")
    current_running_rate = fields.Float(compute="_compute_current_rate", digits=(16, 4), string="Running per km")
    current_fixed_rate = fields.Float(compute="_compute_current_rate", digits=(16, 4), string="Fixed per km")
    current_standard_rate = fields.Float(compute="_compute_current_rate", digits=(16, 4),
                                         string="Standard per km")

    @api.depends("std_rate_ids.date_from", "std_rate_ids.standard_rate")
    def _compute_current_rate(self):
        today = fields.Date.context_today(self)
        for cls in self:
            rate = self.env["tms.std.rate"]._tms_rate_for(cls, today)
            cls.current_running_rate = rate.running_rate
            cls.current_fixed_rate = rate.fixed_rate
            cls.current_standard_rate = rate.standard_rate


class TmsEstimateLine(models.Model):
    _inherit = "tms.estimate.line"

    def _tms_standard_rate(self, vehicle_class, date):
        """BR-PNL-15, BR-EST-03: owned-truck lines are priced at the standard rate in force on the estimate
        date."""
        rate = self.env["tms.std.rate"]._tms_rate_for(vehicle_class, date, self.company_id or self.env.company)
        if not rate:
            return super()._tms_standard_rate(vehicle_class, date)
        return rate.standard_rate
