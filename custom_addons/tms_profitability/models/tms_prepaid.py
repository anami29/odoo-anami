from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .account_move import cover_months, month_end


class TmsPrepaidSchedule(models.Model):
    """BR-PNL-10, BR-PNL-11, BR-MIG-08: a fixed cost covering more than one month, or a repair at or above the
    capitalisation threshold, released to the truck's pool in equal monthly amounts."""

    _name = "tms.prepaid.schedule"
    _description = "Prepaid truck cost release schedule"
    _inherit = ["mail.thread"]
    _order = "date_from desc, id desc"

    name = fields.Char(compute="_compute_name", store=True)
    company_id = fields.Many2one("res.company", string="Branch", required=True, default=lambda s: s.env.company)
    currency_id = fields.Many2one(related="company_id.currency_id")
    kind = fields.Selection([("prepaid", "Prepaid fixed cost"), ("spread", "Repair spread over useful life")],
                            required=True, default="prepaid", tracking=True)
    vehicle_id = fields.Many2one("fleet.vehicle", required=True, domain=[("tms_ownership", "=", "owned")],
                                 tracking=True)
    category_id = fields.Many2one("tms.truck.category", string="Truck cost category", required=True, tracking=True)
    cost_class = fields.Selection([("truck_running", "Truck running"), ("truck_fixed", "Truck fixed")],
                                  compute="_compute_cost_class", store=True)
    move_line_id = fields.Many2one("account.move.line", string="Source line", readonly=True, copy=False)
    amount = fields.Monetary(required=True, tracking=True)
    date_from = fields.Date(string="First month", required=True, tracking=True)
    date_to = fields.Date(string="Cover to")
    months = fields.Integer(required=True, tracking=True)
    expense_account_id = fields.Many2one("account.account", string="Expense account", required=True)
    prepaid_account_id = fields.Many2one("account.account", string="Prepaid account", required=True)
    line_ids = fields.One2many("tms.prepaid.schedule.line", "schedule_id", string="Monthly releases")
    released_amount = fields.Monetary(compute="_compute_released")
    remaining_amount = fields.Monetary(compute="_compute_released")
    state = fields.Selection([("draft", "Draft"), ("running", "Releasing"), ("done", "Released")],
                             compute="_compute_state", store=True)

    @api.depends("vehicle_id", "category_id", "date_from")
    def _compute_name(self):
        for sch in self:
            sch.name = "%s / %s / %s" % (sch.vehicle_id.license_plate or "", sch.category_id.name or "",
                                         sch.date_from or "")

    @api.depends("category_id.cost_pool")
    def _compute_cost_class(self):
        for sch in self:
            sch.cost_class = "truck_%s" % sch.category_id.cost_pool if sch.category_id else False

    @api.depends("line_ids.state", "line_ids.amount")
    def _compute_released(self):
        for sch in self:
            released = sum(sch.line_ids.filtered(lambda line: line.state == "released").mapped("amount"))
            sch.released_amount = released
            sch.remaining_amount = sch.amount - released

    @api.depends("line_ids.state")
    def _compute_state(self):
        for sch in self:
            if not sch.line_ids:
                sch.state = "draft"
            elif all(line.state == "released" for line in sch.line_ids):
                sch.state = "done"
            else:
                sch.state = "running"

    @api.model
    def _tms_create_from_line(self, line):
        cls = line.tms_cost_class
        if cls == "truck_fixed":
            kind, start, months = "prepaid", line.tms_cover_from, cover_months(line.tms_cover_from, line.tms_cover_to)
        else:
            kind, start, months = "spread", line.date, line.tms_useful_life_months
        sch = self.create({
            "company_id": line.company_id.id,
            "kind": kind,
            "vehicle_id": line.vehicle_id.id,
            "category_id": line.tms_truck_category_id.id,
            "move_line_id": line.id,
            "amount": line.balance,
            "date_from": start.replace(day=1),
            "date_to": line.tms_cover_to,
            "months": max(months, 1),
            "expense_account_id": line.tms_expense_account_id.id,
            "prepaid_account_id": line.account_id.id,
        })
        sch.action_plan()
        line.tms_prepaid_schedule_id = sch
        return sch

    def action_plan(self):
        """Equal monthly amounts dated the last day of each month; the last absorbs rounding."""
        for sch in self:
            if sch.line_ids.filtered(lambda line: line.state == "released"):
                raise UserError(_("Releases have started; the schedule is fixed."))
            sch.line_ids.unlink()
            cur = sch.currency_id
            each = cur.round(sch.amount / sch.months)
            vals = []
            for i in range(sch.months):
                amount = each if i < sch.months - 1 else cur.round(sch.amount - each * (sch.months - 1))
                vals.append({"schedule_id": sch.id, "date": month_end(sch.date_from + relativedelta(months=i)),
                             "amount": amount, "sequence": i + 1})
            self.env["tms.prepaid.schedule.line"].create(vals)
        return True

    @api.model
    def _tms_release_due(self, date_to, companies=None):
        """Close step 1 and the monthly cron: release every planned month up to ``date_to``."""
        domain = [("state", "=", "planned"), ("date", "<=", date_to)]
        if companies:
            domain.append(("schedule_id.company_id", "in", companies.ids))
        lines = self.env["tms.prepaid.schedule.line"].sudo().search(domain, order="date, id")
        lines._tms_release()
        return lines

    @api.model
    def _cron_release_prepaid(self):
        today = fields.Date.context_today(self)
        self._tms_release_due(today.replace(day=1) - relativedelta(days=1))


class TmsPrepaidScheduleLine(models.Model):
    _name = "tms.prepaid.schedule.line"
    _description = "Prepaid monthly release"
    _order = "schedule_id, sequence"

    schedule_id = fields.Many2one("tms.prepaid.schedule", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="schedule_id.company_id", store=True)
    currency_id = fields.Many2one(related="schedule_id.currency_id")
    sequence = fields.Integer()
    date = fields.Date(required=True)
    amount = fields.Monetary(required=True)
    state = fields.Selection([("planned", "Planned"), ("released", "Released")], default="planned", required=True)
    move_id = fields.Many2one("account.move", readonly=True, copy=False)

    def unlink(self):
        if self.filtered(lambda line: line.state == "released"):
            raise UserError(_("A released month is never deleted (BR-AUD-04)."))
        return super().unlink()

    def _tms_release(self):
        Close = self.env["tms.pnl.close"]
        for line in self:
            sch = line.schedule_id
            company = sch.company_id
            branch = sch.vehicle_id.tms_home_branch_id or company
            date = line.date
            if Close._tms_is_closed(branch, date):
                date = month_end(Close._tms_next_open(branch, date))
            journal = company.tms_expense_journal_id or company.tms_branch_journal("general")
            label = _("%(s)s release %(i)s/%(n)s", s=sch.name, i=line.sequence, n=sch.months)
            move = self.env["account.move"].sudo().with_company(company).create({
                "move_type": "entry",
                "date": date,
                "journal_id": journal.id,
                "ref": label,
                "line_ids": [
                    (0, 0, {"name": label, "account_id": sch.expense_account_id.id, "debit": line.amount,
                            "credit": 0.0, "vehicle_id": sch.vehicle_id.id, "tms_cost_class": sch.cost_class,
                            "tms_truck_category_id": sch.category_id.id,
                            "tms_cover_from": date.replace(day=1), "tms_cover_to": month_end(date),
                            "tms_useful_life_months": 1}),
                    (0, 0, {"name": label, "account_id": sch.prepaid_account_id.id, "debit": 0.0,
                            "credit": line.amount}),
                ],
            })
            move.action_post()
            line.write({"state": "released", "move_id": move.id})
        return True
