from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from odoo.addons.tms_core.models.tools import notify_detached, schedule, users_in_group

from .analytic import POOLS
from .account_move import month_end

TRIP_KINDS = ("revenue", "accrued_revenue", "direct", "accrued_cost", "recovery", "std_charge")
TRUCK_KINDS = ("std_recovered", "truck_running", "truck_fixed")


class TmsPnlClose(models.Model):
    """BR-PNL-22, BR-PNL-31, BR-PNL-36, #18.3: the month-end close of a branch.

    1) release prepaid fixed costs and spread repairs; 2) accrue unbilled freight and open hire of trips
    delivered in the month; 3) compute the standard charge of each owned trip; 4) compute each truck's pools,
    recovery variance and split; 5) the Finance Head reviews the recovery statement and the reconciliation check,
    then closes. A re-run before close replaces the earlier run; a closed period is frozen."""

    _name = "tms.pnl.close"
    _description = "Month-end P&L close"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date_from desc, company_id"

    name = fields.Char(compute="_compute_name", store=True)
    company_id = fields.Many2one("res.company", string="Branch", required=True, default=lambda s: s.env.company,
                                 tracking=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    date_from = fields.Date(string="Period", required=True, tracking=True,
                            default=lambda s: fields.Date.context_today(s).replace(day=1)
                            - relativedelta(months=1))
    date_to = fields.Date(compute="_compute_date_to", store=True)
    state = fields.Selection([("draft", "Draft"), ("computed", "Computed"), ("closed", "Closed")], default="draft",
                             required=True, tracking=True, copy=False)
    run_count = fields.Integer(readonly=True, copy=False, string="Runs")
    run_on = fields.Datetime(readonly=True, copy=False)
    run_by_id = fields.Many2one("res.users", readonly=True, copy=False)
    closed_on = fields.Datetime(readonly=True, copy=False)
    closed_by_id = fields.Many2one("res.users", readonly=True, copy=False)
    move_ids = fields.One2many("account.move", "tms_close_id", string="Accruals and reversals")
    std_line_ids = fields.One2many("account.analytic.line", "tms_close_id", string="Standard charge entries")
    truck_ids = fields.One2many("tms.pnl.close.truck", "close_id", string="Recovery statement")
    diff_ids = fields.One2many("tms.pnl.close.diff", "close_id", string="Reconciliation differences")
    released_count = fields.Integer(string="Months released", readonly=True, copy=False)
    std_charge_total = fields.Monetary(compute="_compute_totals", string="Standard charge")
    accrued_revenue = fields.Monetary(compute="_compute_totals")
    accrued_cost = fields.Monetary(compute="_compute_totals")
    recovery_variance = fields.Monetary(compute="_compute_totals")
    difference = fields.Monetary(string="Reconciliation difference", readonly=True, copy=False)
    note = fields.Text(readonly=True, copy=False)

    _sql_constraints = [("period_uniq", "unique(company_id, date_from)", "A branch closes a period once.")]

    @api.depends("company_id.name", "date_from")
    def _compute_name(self):
        for close in self:
            close.name = "%s %s" % (close.company_id.name or "", close.date_from.strftime("%Y-%m")
                                    if close.date_from else "")

    @api.depends("date_from")
    def _compute_date_to(self):
        for close in self:
            close.date_to = month_end(close.date_from) if close.date_from else False

    @api.depends("std_line_ids.amount", "move_ids.line_ids.balance", "truck_ids.variance")
    def _compute_totals(self):
        for close in self:
            close.std_charge_total = -sum(close.std_line_ids.filtered(
                lambda line: line.tms_std_side == "trip").mapped("amount"))
            accruals = close.move_ids.filtered(lambda m: m.date <= close.date_to).mapped("line_ids").filtered(
                lambda line: line.tms_line_kind == "accrual")
            close.accrued_revenue = -sum(accruals.filtered(
                lambda line: line.account_id.account_type in ("income", "income_other")).mapped("balance"))
            close.accrued_cost = sum(accruals.filtered(
                lambda line: line.account_id.account_type in ("expense", "expense_direct_cost")).mapped("balance"))
            close.recovery_variance = sum(close.truck_ids.mapped("variance"))

    # ------------------------------------------------------------------ period helpers
    @api.model
    def _tms_is_closed(self, company, date):
        if not company or not date:
            return False
        return bool(self.sudo().search_count([("company_id", "=", company.id), ("state", "=", "closed"),
                                              ("date_from", "<=", date), ("date_to", ">=", date)], limit=1))

    @api.model
    def _tms_next_open(self, company, date):
        """BR-PNL-22: the first day of the next open period after ``date``."""
        nxt = date.replace(day=1) + relativedelta(months=1)
        while self._tms_is_closed(company, nxt):
            nxt += relativedelta(months=1)
        return nxt

    def _tms_check_role(self, finance_only=False):
        user = self.env.user
        if finance_only:
            if not user.has_group("tms_core.group_finance_head"):
                raise UserError(_("The Finance Head closes the period (#18.3 step 5)."))
        elif not (user.has_group("tms_core.group_finance_head")
                  or user.has_group("tms_core.group_branch_accountant")):
            raise UserError(_("The Branch Accountant or the Finance Head runs the month-end close."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("date_from"):
                vals["date_from"] = fields.Date.to_date(vals["date_from"]).replace(day=1)
        return super().create(vals_list)

    def unlink(self):
        if self.filtered(lambda c: c.state == "closed"):
            raise UserError(_("A closed period is never deleted (BR-AUD-04)."))
        for close in self:
            close.sudo()._tms_clear_run()
        return super().unlink()

    # ------------------------------------------------------------------ run
    def action_run(self):
        """#18.3 steps 1 to 4 and the reconciliation check; a re-run replaces the earlier run."""
        self._tms_check_role()
        for close in self:
            if close.state == "closed":
                raise UserError(_("Period %s is closed and frozen (BR-PNL-22).", close.name))
            run = close.sudo().with_context(tms_close_run=True)
            run._tms_clear_run()
            run._tms_step_release()
            run._tms_step_accrue()
            run._tms_step_standard_charge()
            run._tms_step_trucks()
            run._tms_step_reconcile()
            run.write({"state": "computed", "run_count": close.run_count + 1, "run_on": fields.Datetime.now(),
                       "run_by_id": self.env.user.id})
            if close.difference:
                close._tms_notify_difference()
        return True

    def _tms_clear_run(self):
        self.ensure_one()
        drafts = self.move_ids.filtered(lambda m: m.state == "draft")
        drafts.with_context(force_delete=True).unlink()
        self.std_line_ids.with_context(tms_close_run=True).unlink()
        self.truck_ids.unlink()
        self.diff_ids.unlink()
        self.write({"difference": 0.0, "note": False})

    def _tms_step_release(self):
        """Step 1: release prepaid fixed costs and spread repairs (BR-PNL-10, BR-PNL-11)."""
        lines = self.env["tms.prepaid.schedule"]._tms_release_due(self.date_to, self.company_id)
        self.released_count = len(lines)

    def _tms_trips(self, extra=None):
        domain = [("company_id", "=", self.company_id.id), ("tms_pnl_date", ">=", self.date_from),
                  ("tms_pnl_date", "<=", self.date_to), ("state_code", "!=", "cancel")]
        return self.env["tms.order"].search(domain + (extra or []))

    # ------------------------------------------------------------------ step 2: accruals (BR-PNL-36)
    def _tms_income_account(self, product):
        account = product.product_tmpl_id.with_company(self.company_id)._get_product_accounts()["income"] \
            if product else False
        return account or self._tms_default_account("income")

    def _tms_expense_account(self, product):
        account = product.product_tmpl_id.with_company(self.company_id)._get_product_accounts()["expense"] \
            if product else False
        return account or self._tms_default_account("expense")

    def _tms_default_account(self, account_type):
        Account = self.env["account.account"].with_company(self.company_id)
        return Account.search(Account._check_company_domain(self.company_id)
                              + [("account_type", "=", account_type)], limit=1)

    def _tms_step_accrue(self):
        """Step 2: delivered trips not yet invoiced accrue their freight, approved unbilled customer charges and
        open hire commitments, dated the last day of the delivery month; reversed on the first of the next."""
        company = self.company_id
        rev_account = company._tms_pnl_account("accrued_revenue")
        cost_account = company._tms_pnl_account("accrued_cost")
        freight_product = company.tms_freight_product_id or self.env.ref("tms_core.product_freight")
        journal = company.tms_branch_journal("general")
        for trip in self._tms_trips():
            vehicle = trip.primary_vehicle_id
            common = {"tms_trip_id": trip.id, "tms_line_kind": "accrual", "vehicle_id": vehicle.id}
            lines = []
            links = trip.consignment_link_ids.filtered(lambda x: x.state != "cancel")
            for link in links:
                lr = link.consignment_id
                lr_links = lr.trip_link_ids.filtered(lambda x: x.state != "cancel")
                total = sum(lr_links.mapped("weight"))
                share = (link.weight / total) if total else 1.0 / (len(lr_links) or 1)
                amount = company.currency_id.round(lr.freight_uninvoiced * share)
                if amount > 0:
                    lines.append(("revenue", self._tms_income_account(freight_product), amount,
                                  _("Accrued freight LR %s", lr.name), {"tms_consignment_id": lr.id}))
            for charge in trip.charge_ids.filtered(lambda c: c.state == "approved" and c.recover_from == "customer"
                                                   and not c.move_line_id):
                lines.append(("revenue", charge._tms_income_account(), charge.amount,
                              _("Accrued charge %s", charge.name),
                              {"tms_consignment_id": charge.consignment_id.id}))
            for po in trip.hire_memo_ids.filtered(lambda p: p.state in ("purchase", "done")):
                for pol in po.order_line:
                    amount = company.currency_id.round(max(pol.product_qty - pol.qty_invoiced, 0.0) * pol.price_unit)
                    if amount > 0:
                        lines.append(("cost", self._tms_expense_account(pol.product_id), amount,
                                      _("Accrued hire %(po)s item %(i)s", po=po.name, i=pol.tms_item_no or ""), {}))
            for exp in trip.expense_ids.filtered(lambda e: e.state == "approved" and not e.move_id):
                account = exp.category_id.account_id or self._tms_expense_account(exp.category_id.product_id)
                lines.append(("cost", account, exp.amount, _("Accrued expense %s", exp.name), {}))
            if not lines:
                continue
            line_vals = []
            for kind, account, amount, label, extra in lines:
                if kind == "revenue":
                    line_vals += [
                        (0, 0, dict(common, name=label, account_id=account.id, debit=0.0, credit=amount, **extra)),
                        (0, 0, {"name": label, "account_id": rev_account.id, "debit": amount, "credit": 0.0}),
                    ]
                else:
                    line_vals += [
                        (0, 0, dict(common, name=label, account_id=account.id, debit=amount, credit=0.0,
                                    tms_cost_class="trip_direct", **extra)),
                        (0, 0, {"name": label, "account_id": cost_account.id, "debit": 0.0, "credit": amount}),
                    ]
            self.env["account.move"].with_company(company).create({
                "move_type": "entry",
                "date": self.date_to,
                "journal_id": journal.id,
                "ref": _("Month-end accrual %(t)s %(p)s", t=trip.name, p=self.name),
                "tms_close_id": self.id,
                "tms_is_accrual": True,
                "line_ids": line_vals,
            })

    # ------------------------------------------------------------------ step 3: standard charge (BR-PNL-17)
    def _tms_step_standard_charge(self):
        """Step 3: each owned trip delivered pays own km × (running + fixed rate) in force on its START date, as
        analytic entries moving cost from the trip to the truck's pools; nothing touches the general ledger."""
        company = self.company_id
        Line = self.env["account.analytic.line"].with_context(tms_close_run=True)
        trips_plan = self.env.ref("tms_profitability.analytic_plan_trips")
        vehicles_plan = self.env.ref("tms_profitability.analytic_plan_vehicles")
        branch_plan = self.env.ref("tms_profitability.analytic_plan_branch")
        charged = Line.search([("tms_std_side", "!=", False)]).mapped("tms_trip_id")
        trips = self.env["tms.order"].search([
            ("company_id", "=", company.id), ("tms_pnl_date", "<=", self.date_to), ("state_code", "!=", "cancel"),
            ("fleet_type", "=", "owned"), ("tms_std_km", ">", 0), ("id", "not in", charged.ids)])
        missing = []
        for trip in trips:
            vehicle = trip.primary_vehicle_id
            if vehicle.tms_ownership != "owned":
                continue
            rate = trip._tms_std_rate()
            if not rate:
                missing.append(trip.name)
                continue
            if not trip.tms_std_rate_id:
                trip.tms_std_rate_id = rate
            date = trip.tms_pnl_date
            if date < self.date_from and self._tms_is_closed(company, date):
                date = self.date_from  # BR-PNL-22: late, to the next open period
            home = vehicle.tms_home_branch_id or company
            km = trip.tms_std_km
            for pool, per_km in (("running", rate.running_rate), ("fixed", rate.fixed_rate)):
                amount = company.currency_id.round(km * per_km)
                common = {"date": date, "tms_trip_id": trip.id, "tms_vehicle_id": vehicle.id, "tms_std_pool": pool,
                          "tms_km": km, "tms_rate": per_km, "tms_std_rate_id": rate.id, "tms_close_id": self.id}
                label = _("Standard charge %(p)s %(t)s: %(k)s km × %(r)s",
                          p=dict(POOLS)[pool].lower(), t=trip.name, k=km, r=per_km)
                Line.create([
                    dict(common, name=label, amount=-amount, company_id=trip.company_id.id, tms_std_side="trip",
                         **{trips_plan._column_name(): trip._tms_analytic_account().id,
                            branch_plan._column_name(): trip.company_id._tms_analytic_account().id}),
                    dict(common, name=label, amount=amount, company_id=home.id, tms_std_side="truck",
                         **{vehicles_plan._column_name(): vehicle._tms_analytic_account().id,
                            branch_plan._column_name(): home._tms_analytic_account().id}),
                ])
        if missing:
            self.note = _("No standard rate in force on the START date of: %s", ", ".join(missing))

    # ------------------------------------------------------------------ step 4: trucks (BR-PNL-19, BR-PNL-37)
    def _tms_step_trucks(self):
        self.env.flush_all()
        self.env["tms.truck.pnl"].invalidate_model()
        rows = self.env["tms.truck.pnl"].sudo().search([("period", "=", self.date_from),
                                                       ("company_id", "=", self.company_id.id),
                                                       ("fleet_type", "=", "owned")])
        vals = []
        for row in rows:
            for pool in ("running", "fixed"):
                vals.append({
                    "close_id": self.id, "vehicle_id": row.vehicle_id.id, "cost_pool": pool,
                    "charged": row.running_charged if pool == "running" else row.fixed_charged,
                    "actual": row.running_actual if pool == "running" else row.fixed_actual,
                    "volume_variance": row.volume_variance if pool == "fixed" else 0.0,
                    "spending_variance": row.running_variance if pool == "running" else row.fixed_spending_variance,
                    "other_variance": row.fixed_other_variance if pool == "fixed" else 0.0,
                    "planned_km": row.planned_km, "charged_km": row.charged_km, "odometer_km": row.odometer_km,
                    "idle_days": row.idle_days,
                })
        self.env["tms.pnl.close.truck"].create(vals)

    # ------------------------------------------------------------------ reconciliation (BR-PNL-31)
    def _tms_step_reconcile(self):
        """The customer view plus recovery variance must equal the truck view. Lines in one view and not the
        other are the difference; trips that cross branches reconcile at company level."""
        self.env.flush_all()
        self.env["tms.pnl.line"].invalidate_model()
        companies = self.env["res.company"].sudo().search([("id", "child_of", self.company_id.root_id.id)])
        Ledger = self.env["tms.pnl.line"].sudo()
        base = [("company_id", "in", companies.ids), ("date", ">=", self.date_from), ("date", "<=", self.date_to)]
        orphans = [
            (Ledger.search(base + [("kind", "in", ("revenue", "accrued_revenue")), ("trip_id", "=", False)]),
             _("Revenue of an LR on no trip: in the customer view, not the truck view")),
            (Ledger.search(base + [("kind", "in", TRIP_KINDS), ("trip_id", "!=", False),
                                   ("vehicle_id", "=", False)]),
             _("Trip with no truck: in the customer view, not the truck view")),
            (Ledger.search(base + [("kind", "in", TRIP_KINDS), ("trip_id.state_code", "=", "cancel")]),
             _("Cancelled trip: in the truck view, not the customer view")),
            (Ledger.search(base + [("kind", "in", TRUCK_KINDS), ("vehicle_id", "=", False)]),
             _("Truck pool line with no truck")),
        ]
        vals = []
        total = 0.0
        for lines, reason in orphans:
            sign = -1 if "not the customer view" in reason else 1
            for line in lines:
                vals.append({"close_id": self.id, "reason": reason, "amount": sign * line.amount,
                             "trip_id": line.trip_id.id, "move_line_id": line.move_line_id.id,
                             "kind": line.kind})
                total += sign * line.amount
        self.env["tms.pnl.close.diff"].create(vals)
        self.difference = self.currency_id.round(total)
        return self.difference

    def _tms_notify_difference(self, detached=False):
        """BR-NTF-12: a close with a reconciliation difference."""
        users = users_in_group(self.env, "tms_core.group_finance_head", self.company_id)
        summary = _("Reconciliation difference")
        note = _("%(c)s has a reconciliation difference of %(d)s.", c=self.name, d=self.difference)
        if detached:
            notify_detached(self.env, self._name, self.id, users.ids, summary, note)
        else:
            schedule(self, users, summary, note)

    # ------------------------------------------------------------------ close (step 5)
    def action_close(self):
        self._tms_check_role(finance_only=True)
        for close in self:
            if close.state != "computed":
                raise UserError(_("Run the close before closing %s.", close.name))
            run = close.sudo().with_context(tms_close_run=True)
            run.diff_ids.unlink()
            if run._tms_step_reconcile():
                close._tms_notify_difference(detached=True)
                raise UserError(_("%(c)s cannot close: the customer view plus recovery variance differs from the "
                                  "truck view by %(d)s (BR-PNL-31).", c=close.name, d=run.difference))
            reverse_date = close.date_to + relativedelta(days=1)
            for move in run.move_ids.filtered(lambda m: m.state == "draft"):
                move.action_post()
                reversal = move._reverse_moves([{"date": reverse_date, "tms_close_id": close.id,
                                                 "ref": _("Reversal of %s", move.ref)}])
                reversal.action_post()
            run.write({"state": "closed", "closed_on": fields.Datetime.now(), "closed_by_id": self.env.user.id})
            run._tms_review_flags()
            run._tms_rate_review()
        return True

    def _tms_review_flags(self):
        """BR-PNL-21, BR-NTF-12: adverse running spending variance in consecutive closed months flags a truck
        for a repair-or-replace review."""
        months = self.company_id.tms_review_flag_months or 3
        periods = [self.date_from - relativedelta(months=i) for i in range(months)]
        closes = self.search([("company_id", "=", self.company_id.id), ("state", "=", "closed"),
                              ("date_from", "in", periods)])
        if len(closes) < months:
            return
        rows = closes.mapped("truck_ids").filtered(lambda r: r.cost_pool == "running")
        users = users_in_group(self.env, "tms_core.group_fleet_manager", self.company_id)
        for vehicle in rows.mapped("vehicle_id"):
            vrows = rows.filtered(lambda r, v=vehicle: r.vehicle_id == v)
            if len(vrows.mapped("close_id")) == months and all(r.variance < 0 for r in vrows):
                if not vehicle.tms_review_flag:
                    vehicle.write({"tms_review_flag": True, "tms_review_flag_date": fields.Date.context_today(self)})
                    schedule(vehicle, users, _("Repair-or-replace review"),
                             _("%(v)s ran over its running rate for %(n)s closed months (BR-PNL-21).",
                               v=vehicle.display_name, n=months))

    def _tms_rate_review(self):
        """#18.3: rates older than the review interval are put to the Finance Head for review."""
        interval = self.company_id.tms_rate_review_months or 3
        limit = self.date_to - relativedelta(months=interval)
        users = users_in_group(self.env, "tms_core.group_finance_head", self.company_id)
        Rate = self.env["tms.std.rate"]
        for cls in self.env["tms.vehicle.class"].search([]):
            rate = Rate._tms_rate_for(cls, self.date_to, self.company_id)
            if rate and rate.date_from <= limit:
                schedule(rate, users, _("Standard rate review"),
                         _("%(r)s is older than %(n)s months; review it against the recovery statement.",
                           r=rate.display_name, n=interval))

    def action_view_trucks(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "name": _("Recovery statement"),
                "res_model": "tms.recovery.statement", "view_mode": "list,pivot",
                "domain": [("period", "=", self.date_from), ("company_id", "=", self.company_id.id)]}


class TmsPnlCloseTruck(models.Model):
    """The recovery statement frozen at close: truck × pool."""

    _name = "tms.pnl.close.truck"
    _description = "Recovery statement at close"
    _order = "close_id, vehicle_id, cost_pool"

    close_id = fields.Many2one("tms.pnl.close", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="close_id.company_id", store=True)
    period = fields.Date(related="close_id.date_from", store=True)
    vehicle_id = fields.Many2one("fleet.vehicle", string="Truck", required=True)
    cost_pool = fields.Selection(POOLS, string="Pool", required=True)
    charged = fields.Float()
    actual = fields.Float()
    variance = fields.Float(compute="_compute_variance", store=True)
    volume_variance = fields.Float()
    spending_variance = fields.Float()
    other_variance = fields.Float(string="Km gap and rate change")
    planned_km = fields.Float()
    charged_km = fields.Float()
    odometer_km = fields.Float(string="Actual km (odometer)")
    gap_km = fields.Float(compute="_compute_variance", store=True, string="Km between trips")
    idle_days = fields.Integer()

    @api.depends("charged", "actual", "odometer_km", "charged_km")
    def _compute_variance(self):
        for row in self:
            row.variance = row.charged - row.actual
            row.gap_km = max(row.odometer_km - row.charged_km, 0.0)

    def unlink(self):
        if self.filtered(lambda r: r.close_id.state == "closed"):
            raise UserError(_("The recovery statement of a closed period is frozen (BR-PNL-22)."))
        return super().unlink()


class TmsPnlCloseDiff(models.Model):
    _name = "tms.pnl.close.diff"
    _description = "Reconciliation difference line"
    _order = "close_id, reason, id"

    close_id = fields.Many2one("tms.pnl.close", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="close_id.company_id", store=True)
    reason = fields.Char(required=True)
    kind = fields.Selection(selection=lambda s: s.env["tms.pnl.line"]._fields["kind"].selection)
    trip_id = fields.Many2one("tms.order", string="Trip")
    move_line_id = fields.Many2one("account.move.line", string="Journal item")
    amount = fields.Float()
