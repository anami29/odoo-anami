from odoo import _, api, fields, models
from odoo.exceptions import UserError


class TmsOrder(models.Model):
    _inherit = "tms.order"

    tms_analytic_account_id = fields.Many2one("account.analytic.account", string="Trip analytic account",
                                              readonly=True, copy=False)
    tms_std_rate_id = fields.Many2one("tms.std.rate", string="Standard rate", readonly=True, copy=False,
                                      help="BR-PNL-17: the rate in force on the START date.")
    tms_pnl_date = fields.Date(string="P&L date", readonly=True, copy=False, index=True,
                               help="BR-PNL-23, OP-14: dates the trip's revenue, direct cost and standard charge.")
    tms_return_km = fields.Float(string="Empty return km", copy=False,
                                 help="#18.3: left out of the charged km when the empty return does not count.")
    tms_std_km = fields.Float(compute="_compute_tms_std_km", store=True, string="Charged km",
                              help="BR-PNL-16, BR-PNL-18: own km of the primary owned truck, odometer at START to close.")
    tms_pnl_km = fields.Float(compute="_compute_tms_std_km", store=True, string="Trip km")
    tms_std_charge = fields.Monetary(compute="_compute_tms_margin", string="Standard charge")
    tms_std_charge_posted = fields.Boolean(compute="_compute_tms_margin", string="Charge posted")
    tms_pnl_revenue = fields.Monetary(compute="_compute_tms_margin", string="Revenue (P&L)")
    tms_pnl_direct = fields.Monetary(compute="_compute_tms_margin", string="Trip direct cost (P&L)")
    tms_pnl_recoveries = fields.Monetary(compute="_compute_tms_margin", string="Recoveries (P&L)")
    tms_pnl_replacement = fields.Monetary(compute="_compute_tms_margin", string="Replacement add-on")
    tms_pnl_contribution = fields.Monetary(compute="_compute_tms_margin", string="Contribution (P&L)")
    tms_trip_margin = fields.Monetary(compute="_compute_tms_margin", string="Trip margin")
    tms_margin_pct = fields.Float(compute="_compute_tms_margin", string="Margin %")
    tms_pnl_wip = fields.Boolean(compute="_compute_tms_margin", string="Work in progress")

    @api.depends("own_km", "start_odometer", "end_odometer", "tms_return_km", "fleet_type", "primary_vehicle_id",
                 "primary_vehicle_id.tms_ownership", "route_id.distance", "company_id.tms_empty_return_in_km")
    def _compute_tms_std_km(self):
        for trip in self:
            km = 0.0
            owned = trip.fleet_type == "owned" and (trip.primary_vehicle_id.tms_ownership or "owned") == "owned"
            if owned:
                km = trip.own_km
                if not km and trip.end_odometer and trip.start_odometer and trip.end_odometer > trip.start_odometer:
                    km = trip.end_odometer - trip.start_odometer
                if km and not trip.company_id.tms_empty_return_in_km:
                    km = max(km - (trip.tms_return_km or 0.0), 0.0)
            trip.tms_std_km = km
            trip.tms_pnl_km = km if owned else (trip.route_id.distance or 0.0)

    def _compute_tms_margin(self):
        """BR-LED-05: contribution = revenue + recoveries − trip direct cost; trip margin = contribution − the
        standard charge (posted, or provisional at the START-date rate until the close posts it)."""
        self.env.flush_all()
        Pnl = self.env["tms.trip.pnl"].sudo()
        Pnl.invalidate_model()
        for trip in self:
            row = Pnl.browse(trip.id).exists() if trip.id else Pnl
            trip.tms_pnl_revenue = row.revenue
            trip.tms_pnl_direct = row.direct_cost
            trip.tms_pnl_recoveries = row.recoveries
            trip.tms_pnl_replacement = row.replacement_cost
            trip.tms_pnl_contribution = row.contribution
            trip.tms_std_charge = row.std_charge
            trip.tms_std_charge_posted = not row.std_provisional and bool(row.std_charge)
            trip.tms_trip_margin = row.trip_margin
            trip.tms_margin_pct = row.margin_pct
            trip.tms_pnl_wip = row.wip if row else trip.state_code not in ("draft", "cancel")

    # ------------------------------------------------------------------ hooks of tms_core
    def _tms_analytic_account(self):
        """BR-LED-01: each trip gets an analytic account in plan "Trips" at confirmation."""
        self.ensure_one()
        if not self.tms_analytic_account_id:
            plan = self.env.ref("tms_profitability.analytic_plan_trips")
            self.sudo().tms_analytic_account_id = self.env["account.analytic.account"].sudo().create({
                "name": self.name, "plan_id": plan.id, "company_id": False})
        return self.tms_analytic_account_id

    def _tms_on_confirm(self):
        res = super()._tms_on_confirm()
        for trip in self:
            trip._tms_analytic_account()
        return res

    def _tms_on_start(self):
        """BR-PNL-17: the rate in force on the START date is the trip's rate."""
        res = super()._tms_on_start()
        for trip in self:
            trip._tms_analytic_account()
            vehicle = trip.primary_vehicle_id
            if trip.fleet_type == "owned" and vehicle.tms_ownership == "owned":
                rate = self.env["tms.std.rate"]._tms_rate_for(vehicle.tms_vehicle_class_id,
                                                               trip.date_start or fields.Datetime.now(),
                                                               trip.company_id)
                trip.sudo().tms_std_rate_id = rate
        return res

    def _tms_on_delivered(self):
        """BR-PNL-23, OP-14: on delivery basis the delivery date dates the trip."""
        res = super()._tms_on_delivered()
        for trip in self:
            if trip.company_id.tms_revenue_dating == "delivery" and not trip.tms_pnl_date:
                date = trip.delivered_on or fields.Datetime.now()
                trip.sudo().tms_pnl_date = fields.Date.context_today(trip, date)
        return res

    def _tms_std_rate(self):
        """The trip's rate: snapshot at START, else the rate in force on the START date."""
        self.ensure_one()
        if self.tms_std_rate_id:
            return self.tms_std_rate_id
        vehicle = self.primary_vehicle_id
        if not vehicle or vehicle.tms_ownership != "owned" or not self.date_start:
            return self.env["tms.std.rate"]
        return self.env["tms.std.rate"]._tms_rate_for(vehicle.tms_vehicle_class_id, self.date_start, self.company_id)

    def write(self, vals):
        if "tms_return_km" in vals:
            Close = self.env["tms.pnl.close"]
            for trip in self:
                if trip.tms_pnl_date and Close._tms_is_closed(trip.company_id, trip.tms_pnl_date):
                    raise UserError(_("Trip %s is in a closed P&L period (BR-PNL-22).", trip.name))
        return super().write(vals)

    def action_tms_view_pnl_lines(self):
        """BR-PNL-33: every figure drills to its source lines."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Ledger of %s", self.name),
            "res_model": "tms.pnl.line",
            "view_mode": "list,pivot",
            "domain": [("trip_id", "=", self.id)],
            "context": {"search_default_group_kind": 1},
        }

    def action_tms_view_legs(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Legs of %s", self.name),
            "res_model": "tms.trip.leg",
            "view_mode": "list,pivot",
            "domain": [("trip_id", "=", self.id)],
        }
