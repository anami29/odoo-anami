from odoo import _, api, fields, models
from odoo.exceptions import UserError

POOLS = [("running", "Running"), ("fixed", "Fixed")]


def tms_distribution(*accounts):
    """One analytic distribution key naming one account per plan (Trips, Vehicles, Branch)."""
    ids = [str(acc.id) for acc in accounts if acc]
    return {",".join(ids): 100.0} if ids else {}


class AccountAnalyticLine(models.Model):
    """BR-PNL-17, RL-09: the standard charge lives in analytic entries only, with no general-ledger line."""

    _inherit = "account.analytic.line"

    tms_trip_id = fields.Many2one("tms.order", string="Trip", index="btree_not_null", readonly=True)
    tms_vehicle_id = fields.Many2one("fleet.vehicle", string="Truck", index="btree_not_null", readonly=True)
    tms_std_side = fields.Selection([("trip", "Charged to trip"), ("truck", "Recovered by truck pool")],
                                    string="Standard charge side", readonly=True, index="btree_not_null")
    tms_std_pool = fields.Selection(POOLS, string="Pool", readonly=True)
    tms_km = fields.Float(string="Km", readonly=True)
    tms_rate = fields.Float(string="Rate per km", readonly=True, digits=(16, 4))
    tms_std_rate_id = fields.Many2one("tms.std.rate", string="Standard rate", readonly=True)
    tms_close_id = fields.Many2one("tms.pnl.close", string="Month-end close", readonly=True, index="btree_not_null",
                                   ondelete="restrict")

    def write(self, vals):
        if not self.env.context.get("tms_close_run") and self.filtered("tms_std_side"):
            raise UserError(_("Standard charge entries change only through the month-end close (BR-PNL-22)."))
        return super().write(vals)

    def unlink(self):
        std = self.filtered("tms_std_side")
        if std and not self.env.context.get("tms_close_run"):
            raise UserError(_("Standard charge entries change only through the month-end close (BR-PNL-22)."))
        if std.filtered(lambda line: line.tms_close_id.state == "closed"):
            raise UserError(_("The standard charge of a closed period is frozen (BR-PNL-22)."))
        return super().unlink()


class FleetVehicle(models.Model):
    _inherit = "fleet.vehicle"

    tms_analytic_account_id = fields.Many2one("account.analytic.account", string="Vehicle analytic account",
                                              readonly=True, copy=False)
    tms_review_flag = fields.Boolean(string="Flagged for repair-or-replace review", tracking=True, copy=False,
                                     help="BR-PNL-21: adverse running spending variance in consecutive closed months.")
    tms_review_flag_date = fields.Date(readonly=True, copy=False, string="Review flag date")

    @api.model_create_multi
    def create(self, vals_list):
        vehicles = super().create(vals_list)
        for vehicle in vehicles:
            vehicle._tms_analytic_account()
        return vehicles

    def _tms_analytic_account(self):
        """BR-LED-01: each owned or market vehicle is an account in plan "Vehicles"."""
        self.ensure_one()
        if not self.tms_analytic_account_id:
            plan = self.env.ref("tms_profitability.analytic_plan_vehicles", raise_if_not_found=False)
            if not plan:
                return self.env["account.analytic.account"]
            self.sudo().tms_analytic_account_id = self.env["account.analytic.account"].sudo().create({
                "name": self.license_plate or self.name, "plan_id": plan.id, "company_id": False})
        return self.tms_analytic_account_id

    def action_tms_clear_review_flag(self):
        if not self.env.user.has_group("tms_core.group_fleet_manager"):
            raise UserError(_("The Fleet Manager clears the review flag."))
        self.write({"tms_review_flag": False, "tms_review_flag_date": False})
        return True


class TmsCategory(models.Model):
    _inherit = "tms.category"

    truck_category_id = fields.Many2one("tms.truck.category", string="Truck cost category",
                                        help="BR-PNL-03: the pool a truck running or fixed expense of this type "
                                             "lands in.")
