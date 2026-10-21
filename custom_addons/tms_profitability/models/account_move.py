from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

PNL_COST_TYPES = ("expense", "expense_direct_cost")
REVENUE_KINDS = ("freight", "rate_correction", "charge", "supplementary", "dead_freight", "deduction")
TRUCK_CLASSES = ("truck_running", "truck_fixed")


def cover_months(date_from, date_to):
    """Whole months a cover period spans (BR-PNL-10): 1 Apr to 31 Mar is 12."""
    if not date_from or not date_to or date_to < date_from:
        return 0
    return max(1, round(((date_to - date_from).days + 1) / 30.4375))


def month_end(date):
    return date + relativedelta(day=31)


class AccountAccount(models.Model):
    _inherit = "account.account"

    tms_pnl_exclude = fields.Boolean(
        string="Kept out of the TMS P&L",
        help="BR-PNL-12: truck depreciation, loan instalments and interest stay out of every P&L view. "
             "Depreciation accounts are always left out.")


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    tms_truck_category_id = fields.Many2one("tms.truck.category", string="Truck cost category")
    tms_cover_from = fields.Date(string="Cover from")
    tms_cover_to = fields.Date(string="Cover to")
    tms_useful_life_months = fields.Integer(string="Useful life (months)")
    tms_pnl_late_date = fields.Date(string="P&L period (late line)", readonly=True, copy=False,
                                    help="BR-PNL-22: a line naming a trip of a closed period lands here.")
    tms_expense_account_id = fields.Many2one("account.account", string="Expense account released to",
                                             readonly=True, copy=False)
    tms_prepaid_schedule_id = fields.Many2one("tms.prepaid.schedule", readonly=True, copy=False, string="Prepaid schedule")

    @api.onchange("tms_truck_category_id")
    def _onchange_tms_truck_category(self):
        cat = self.tms_truck_category_id
        if cat:
            self.tms_cost_class = "truck_%s" % cat.cost_pool
            if cat.account_id:
                self.account_id = cat.account_id

    @api.onchange("tms_trip_id")
    def _onchange_tms_trip_pnl(self):
        if self.tms_trip_id and not self.tms_cost_class:
            self.tms_cost_class = "trip_direct"
        if self.tms_trip_id and not self.vehicle_id:
            self.vehicle_id = self.tms_trip_id.primary_vehicle_id or self.tms_trip_id.planned_vehicle_id

    # ------------------------------------------------------------------ scope
    def _tms_pnl_subject(self):
        """BR-PNL-01: vendor bill and expense lines that book cost carry a cost class."""
        self.ensure_one()
        if self.display_type != "product" or self.tms_line_kind in ("accrual", "recovery", "claim"):
            return False
        account = self.account_id
        if account.tms_pnl_exclude or account.account_type == "expense_depreciation":
            return False
        if self.tms_cost_class or self.tms_expense_id or self.tms_expense_account_id:
            return True
        return self.move_id.move_type in ("in_invoice", "in_refund") and account.account_type in PNL_COST_TYPES

    def _tms_pnl_branch(self):
        """The branch whose P&L the line lands in: the truck's home branch for truck costs (BR-BRN-10)."""
        self.ensure_one()
        if self.tms_cost_class in TRUCK_CLASSES and self.vehicle_id.tms_home_branch_id:
            return self.vehicle_id.tms_home_branch_id
        if self.tms_trip_id:
            return self.tms_trip_id.company_id
        return self.company_id

    def _tms_pnl_trips(self):
        """Trips a line's amount belongs to, with their share (revenue splits by charged weight)."""
        self.ensure_one()
        if self.tms_consignment_id and self.tms_line_kind in REVENUE_KINDS:
            links = self.tms_consignment_id.sudo().env["tms.trip.consignment"].search([
                ("consignment_id", "=", self.tms_consignment_id.id), ("state", "!=", "cancel")])
            if self.tms_trip_id:
                links = links.filtered(lambda link: link.trip_id == self.tms_trip_id)
            if not links:
                return [(self.tms_trip_id, 1.0)] if self.tms_trip_id else []
            total = sum(links.mapped("weight"))
            return [(link.trip_id, (link.weight / total) if total else 1.0 / len(links)) for link in links]
        if self.tms_trip_id:
            return [(self.tms_trip_id, 1.0)]
        return []

    # ------------------------------------------------------------------ normalise before posting
    def _tms_pnl_normalise(self):
        """Module defaults applied before the checks: the class of a trip line, the primary truck and the
        replacement vehicle (BR-PNL-05, BR-PNL-06), an overhead line naming nothing (BR-PNL-04), the cover
        period of a monthly fixed cost."""
        for line in self:
            if not line._tms_pnl_subject():
                continue
            vals = {}
            cls = line.tms_cost_class
            if line.tms_overhead or cls == "overhead":
                vals.update({"tms_cost_class": "overhead", "tms_trip_id": False, "vehicle_id": False,
                             "tms_replacement_vehicle_id": False})
                if "tms_estimate_line_id" in line._fields and line.tms_estimate_line_id:
                    vals["tms_estimate_line_id"] = False
                line.write(vals)
                continue
            if not cls:
                if line.tms_trip_id or line.tms_estimate_line_id:
                    cls = vals["tms_cost_class"] = "trip_direct"
                elif line.tms_truck_category_id:
                    cls = vals["tms_cost_class"] = "truck_%s" % line.tms_truck_category_id.cost_pool
            if cls == "trip_direct" and line.tms_trip_id:
                trip = line.tms_trip_id
                primary = trip.primary_vehicle_id
                if primary and line.vehicle_id and line.vehicle_id != primary \
                        and line.vehicle_id in trip.assignment_ids.mapped("vehicle_id"):
                    vals.update({"tms_replacement_vehicle_id": line.vehicle_id.id, "vehicle_id": primary.id})
                elif not line.vehicle_id and (primary or trip.planned_vehicle_id):
                    vals["vehicle_id"] = (primary or trip.planned_vehicle_id).id
            if cls == "truck_fixed" and not (line.tms_cover_from and line.tms_cover_to):
                date = line.move_id.invoice_date or line.move_id.date or fields.Date.context_today(line)
                vals.update({"tms_cover_from": date.replace(day=1), "tms_cover_to": month_end(date)})
            if vals:
                line.write(vals)

    def _tms_pnl_check(self):
        """BR-PNL-01..04, BR-PNL-09, BR-PNL-11: the cost-class rules enforced at posting."""
        threshold_lines = self.env["account.move.line"]
        for line in self:
            if not line._tms_pnl_subject():
                continue
            label = line.name or line.account_id.display_name
            cls = line.tms_cost_class
            if not cls:
                raise UserError(_("Line '%s' has no cost class: Trip direct, Truck running, Truck fixed or "
                                  "Overhead (BR-PNL-01).", label))
            if cls == "trip_direct":
                estimate_line = line.tms_estimate_line_id if "tms_estimate_line_id" in line._fields else False
                if not line.tms_trip_id and not estimate_line:
                    raise UserError(_("Trip direct line '%s' must name its trip (BR-PNL-02).", label))
                primary = line.tms_trip_id.primary_vehicle_id
                if primary and line.vehicle_id and line.vehicle_id != primary:
                    raise UserError(_("Trip direct line '%(l)s' must name the trip's primary truck %(v)s; a "
                                      "replacement goes in the replacement vehicle (BR-PNL-02, BR-PNL-06).",
                                      l=label, v=primary.display_name))
            elif cls in TRUCK_CLASSES:
                if line.tms_trip_id:
                    raise UserError(_("Truck running or fixed line '%s' names no trip; the truck's trips pay "
                                      "through the standard rate (BR-PNL-03).", label))
                if not line.vehicle_id:
                    raise UserError(_("Truck running or fixed line '%s' must name an owned vehicle "
                                      "(BR-PNL-03).", label))
                if line.vehicle_id.tms_ownership != "owned":
                    raise UserError(_("Line '%(l)s' books a running or fixed cost on market vehicle %(v)s; its "
                                      "hire already pays for it (BR-PNL-09).", l=label,
                                      v=line.vehicle_id.display_name))
                cat = line.tms_truck_category_id
                if not cat:
                    raise UserError(_("Truck running or fixed line '%s' must name a truck cost category "
                                      "(BR-PNL-03).", label))
                if "truck_%s" % cat.cost_pool != cls:
                    raise UserError(_("Category %(c)s is in the %(p)s pool, but line '%(l)s' is %(k)s (BR-PNL-03).",
                                      c=cat.name, p=dict(cat._fields["cost_pool"].selection)[cat.cost_pool], l=label,
                                      k=dict(line._fields["tms_cost_class"].selection)[cls]))
                if cls == "truck_fixed":
                    if not (line.tms_cover_from and line.tms_cover_to) or line.tms_cover_to < line.tms_cover_from:
                        raise UserError(_("Truck fixed line '%s' needs its cover period (#18.2).", label))
                else:
                    threshold_lines |= line
            elif cls == "overhead":
                if line.tms_trip_id or line.vehicle_id:
                    raise UserError(_("Overhead line '%s' names a branch or head office, and no vehicle or "
                                      "trip (BR-PNL-04).", label))
        for line in threshold_lines:
            threshold = line.company_id.tms_capitalisation_threshold
            if threshold and line.balance >= threshold and line.tms_useful_life_months < 1 \
                    and not line.tms_expense_account_id:
                raise UserError(_("Repair '%(l)s' of %(a)s is at or above the capitalisation threshold %(t)s: "
                                  "enter its useful life in months (BR-PNL-11).", l=line.name or "",
                                  a=line.balance, t=threshold))

    def _tms_pnl_prepaid_redirect(self):
        """BR-PNL-10, BR-PNL-11: a fixed cost covering more than one month, or a repair at or above the
        capitalisation threshold with a useful life, posts to prepaid and is released monthly."""
        for line in self:
            if not line._tms_pnl_subject() or line.tms_expense_account_id or line.balance <= 0:
                continue
            spread = False
            if line.tms_cost_class == "truck_fixed":
                spread = cover_months(line.tms_cover_from, line.tms_cover_to) > 1
            elif line.tms_cost_class == "truck_running":
                threshold = line.company_id.tms_capitalisation_threshold
                spread = bool(threshold) and line.balance >= threshold and line.tms_useful_life_months > 1
            if spread:
                prepaid = line.company_id._tms_pnl_account("prepaid")
                line.write({"tms_expense_account_id": line.account_id.id, "account_id": prepaid.id,
                            "analytic_distribution": False})

    # ------------------------------------------------------------------ analytic (BR-LED-01, BR-LED-08)
    def _tms_pnl_distribution(self):
        self.ensure_one()
        if self.display_type != "product" or self.tms_expense_account_id:
            return {}
        cls = self.tms_cost_class
        if cls in TRUCK_CLASSES and self.vehicle_id:
            branch = self.vehicle_id.tms_home_branch_id or self.company_id
            return {"%s,%s" % (self.vehicle_id._tms_analytic_account().id, branch._tms_analytic_account().id): 100.0}
        if cls == "overhead" or self.tms_overhead:
            return {str(self.company_id._tms_analytic_account().id): 100.0}
        trips = self._tms_pnl_trips()
        if not trips:
            return {}
        branch = self.company_id._tms_analytic_account()
        dist = {}
        for trip, share in trips:
            ids = [trip._tms_analytic_account().id]
            vehicle = trip.primary_vehicle_id or self.vehicle_id
            if vehicle:
                ids.append(vehicle._tms_analytic_account().id)
            ids.append(branch.id)
            key = ",".join(str(i) for i in ids if i)
            dist[key] = dist.get(key, 0.0) + share * 100.0
        return dist

    def _tms_pnl_set_analytic(self):
        plans = (self.env.ref("tms_profitability.analytic_plan_trips")
                 | self.env.ref("tms_profitability.analytic_plan_vehicles")
                 | self.env.ref("tms_profitability.analytic_plan_branch"))
        Account = self.env["account.analytic.account"].sudo()
        for line in self:
            ours = line._tms_pnl_distribution()
            if not ours:
                continue
            if not line.analytic_distribution:
                line.analytic_distribution = ours
                continue
            # merge: keep the user's keys and add our plans they do not name yet
            ours_ids = [int(i) for i in next(iter(ours)).split(",")]
            merged = {}
            for key, pct in line.analytic_distribution.items():
                ids = [int(i) for i in key.split(",") if i]
                used = Account.browse(ids).mapped("root_plan_id")
                extra = [i for i in ours_ids if Account.browse(i).root_plan_id not in used
                         and Account.browse(i).root_plan_id in plans]
                merged[",".join(str(i) for i in ids + extra)] = pct
            line.analytic_distribution = merged

    # ------------------------------------------------------------------ period (BR-PNL-22, BR-PNL-23)
    def _tms_pnl_date(self):
        """The date that puts the line in a P&L period."""
        self.ensure_one()
        if self.tms_line_kind == "accrual" or not self._tms_pnl_trips():
            return self.date
        if self.tms_pnl_late_date:
            return self.tms_pnl_late_date
        dates = [trip.tms_pnl_date for trip, _share in self._tms_pnl_trips() if trip.tms_pnl_date]
        return max(dates) if dates else False

    def _tms_pnl_set_late(self):
        """BR-PNL-22: a line naming a trip of a closed period posts to the next open period."""
        Close = self.env["tms.pnl.close"]
        for line in self:
            if line.display_type != "product" or line.tms_line_kind == "accrual":
                continue
            late = False
            for trip, _share in line._tms_pnl_trips():
                if trip.tms_pnl_date and Close._tms_is_closed(trip.company_id, trip.tms_pnl_date):
                    nxt = Close._tms_next_open(trip.company_id, trip.tms_pnl_date)
                    late = max(late, nxt) if late else nxt
            if late != line.tms_pnl_late_date:
                line.tms_pnl_late_date = late


class AccountMove(models.Model):
    _inherit = "account.move"

    tms_close_id = fields.Many2one("tms.pnl.close", string="Month-end close", readonly=True, copy=False,
                                   index="btree_not_null")

    def _tms_before_post(self):
        for move in self:
            if move.state != "draft":
                continue
            lines = move.sudo().line_ids
            lines._tms_pnl_normalise()
            lines._tms_pnl_prepaid_redirect()
            lines._tms_pnl_set_analytic()
            lines._tms_pnl_set_late()
        return super()._tms_before_post()

    def _tms_check_post(self):
        res = super()._tms_check_post()
        Close = self.env["tms.pnl.close"]
        for move in self:
            move.sudo().line_ids._tms_pnl_check()
            # BR-PNL-22: a closed period is frozen; costs dated by accounting date cannot land in it
            for line in move.sudo().line_ids.filtered(lambda line: line.display_type == "product"):
                if line.tms_line_kind == "accrual" or line._tms_pnl_trips():
                    continue
                if (line._tms_pnl_subject() or line.tms_cost_class) and \
                        Close._tms_is_closed(line._tms_pnl_branch(), line.date):
                    raise UserError(_("The P&L period of %(d)s is closed for %(b)s; date line '%(l)s' in the "
                                      "next open period (BR-PNL-22).", d=line.date,
                                      b=line._tms_pnl_branch().name, l=line.name or ""))
        return res

    def _tms_after_post(self):
        res = super()._tms_after_post()
        Schedule = self.env["tms.prepaid.schedule"].sudo()
        for move in self:
            for line in move.line_ids.filtered(lambda line: line.tms_expense_account_id
                                               and not line.tms_prepaid_schedule_id):
                Schedule._tms_create_from_line(line)
            # OP-14 on invoice basis: the first posted invoice dates the trip
            if move.move_type == "out_invoice":
                for line in move.invoice_line_ids:
                    for trip, _share in line._tms_pnl_trips():
                        if not trip.tms_pnl_date and trip.delivered_on and \
                                trip.company_id.tms_revenue_dating == "invoice":
                            trip.sudo().tms_pnl_date = move.invoice_date or move.date
        return res

    def button_draft(self):
        """BR-PNL-22: entries of a closed period are reversed, never reopened."""
        Close = self.env["tms.pnl.close"]
        for move in self:
            for line in move.sudo().line_ids.filtered(lambda line: line.display_type == "product"):
                relevant = line.tms_line_kind or line.tms_cost_class or line.tms_trip_id
                if not relevant:
                    continue
                date = line._tms_pnl_date()
                if date and Close._tms_is_closed(line._tms_pnl_branch(), date):
                    raise UserError(_("%(m)s is in the closed P&L period of %(d)s; reverse it instead "
                                      "(BR-PNL-22, BR-AUD-04).", m=move.name, d=date))
        return super().button_draft()


class TmsTripExpense(models.Model):
    _inherit = "tms.trip.expense"

    tms_truck_category_id = fields.Many2one("tms.truck.category", string="Truck cost category",
                                            compute="_compute_tms_truck_category", store=True, readonly=False)
    tms_cover_from = fields.Date(string="Cover from")
    tms_cover_to = fields.Date(string="Cover to")
    tms_useful_life_months = fields.Integer(string="Useful life (months)")

    @api.depends("category_id")
    def _compute_tms_truck_category(self):
        for exp in self:
            exp.tms_truck_category_id = exp.category_id.truck_category_id or exp.tms_truck_category_id

    def _tms_check_before_post(self):
        """BR-PNL-01..04, BR-PNL-08, BR-PNL-09 on the expense before its entry is built."""
        res = super()._tms_check_before_post()
        for exp in self.sudo():
            cls = exp.tms_cost_class
            if not cls:
                raise UserError(_("Expense %s has no cost class (BR-PNL-01).", exp.name))
            if cls in TRUCK_CLASSES:
                vehicle = exp.vehicle_id
                if not vehicle:
                    raise UserError(_("Expense %s books a truck cost and must name the owned vehicle "
                                      "(BR-PNL-03).", exp.name))
                if vehicle.tms_ownership != "owned":
                    raise UserError(_("Expense %(e)s books a running or fixed cost on market vehicle %(v)s "
                                      "(BR-PNL-09).", e=exp.name, v=vehicle.display_name))
                if not exp.tms_truck_category_id:
                    raise UserError(_("Expense %s must name a truck cost category (BR-PNL-03).", exp.name))
            elif cls == "trip_direct" and not exp.trip_id and not exp.estimate_line_id:
                raise UserError(_("Trip direct expense %s must name its trip (BR-PNL-02).", exp.name))
        return res

    def _tms_expense_line_vals(self):
        """BR-PNL-03, BR-PNL-04, BR-PNL-08: a truck repair on the road is truck running and names no trip; an
        overhead expense names no trip or vehicle."""
        vals = super()._tms_expense_line_vals()
        cls = self.tms_cost_class
        if cls in TRUCK_CLASSES:
            vals.update({"tms_trip_id": False, "tms_estimate_line_id": False, "tms_overhead": False,
                         "vehicle_id": self.vehicle_id.id,
                         "tms_truck_category_id": self.tms_truck_category_id.id,
                         "tms_cover_from": self.tms_cover_from, "tms_cover_to": self.tms_cover_to,
                         "tms_useful_life_months": self.tms_useful_life_months})
            if self.tms_truck_category_id.account_id and not self.category_id.account_id:
                vals["account_id"] = self.tms_truck_category_id.account_id.id
        elif cls == "overhead" or self.tms_overhead_reason:
            vals.update({"tms_cost_class": "overhead", "tms_trip_id": False, "vehicle_id": False,
                         "tms_estimate_line_id": False})
        return vals


class PurchaseOrderLine(models.Model):
    _inherit = "purchase.order.line"

    def _prepare_account_move_line(self, move=False):
        """BR-PNL-01, BR-PNL-02: a bill line bought for an order item is trip direct."""
        res = super()._prepare_account_move_line(move=move)
        if not res.get("tms_cost_class") and (res.get("tms_trip_id") or res.get("tms_estimate_line_id")):
            res["tms_cost_class"] = "trip_direct"
        return res
