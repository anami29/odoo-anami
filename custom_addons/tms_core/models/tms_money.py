from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .tms_category import COST_CLASSES
from .tools import schedule, users_in_group

RECOVER_FROM = [("customer", "Customer"), ("vendor", "Vendor"), ("driver", "Driver"), ("third_party", "Third party")]
PAYMENT_MODES = [
    ("driver_cash", "Driver cash"),
    ("company_cash", "Company cash"),
    ("bank", "Bank"),
    ("fuel_card", "Fuel card"),
    ("fastag", "FASTag"),
    ("vendor_credit", "Vendor credit"),
]


def _default_account(env, company, account_type):
    Account = env["account.account"]
    return Account.search([*Account._check_company_domain(company), ("account_type", "=", account_type)], limit=1)


class TmsTripCharge(models.Model):
    """BR-CHG-01..09: charges carry what a trip recovers."""

    _name = "tms.trip.charge"
    _description = "Trip charge or recovery"
    _inherit = ["mail.thread", "mail.activity.mixin", "tms.reason.mixin"]
    _order = "id desc"
    _check_company_auto = True

    name = fields.Char(default="/", copy=False, readonly=True)
    trip_id = fields.Many2one("tms.order", ondelete="restrict", index=True, tracking=True)
    consignment_id = fields.Many2one("tms.consignment", string="LR", index=True, tracking=True)
    company_id = fields.Many2one("res.company", string="Branch", compute="_compute_company", store=True,
                                 readonly=False, required=True, default=lambda s: s.env.company)
    currency_id = fields.Many2one(related="company_id.currency_id")
    category_id = fields.Many2one("tms.category", required=True, domain=[("use_charge", "=", True)], tracking=True)
    amount = fields.Monetary(required=True, tracking=True)
    recover_from = fields.Selection(RECOVER_FROM, required=True, default="customer", tracking=True)
    partner_id = fields.Many2one("res.partner", string="Recovery partner", compute="_compute_partner", store=True,
                                 readonly=False, tracking=True)
    driver_id = fields.Many2one("tms.driver")
    incident_id = fields.Many2one("tms.trip.incident", string="Source incident")
    expense_id = fields.Many2one("tms.trip.expense", string="Cost document")
    claim_id = fields.Many2one("tms.damage.claim")
    hire_memo_id = fields.Many2one("purchase.order", string="Hire memo")
    note = fields.Text()
    detention_kind = fields.Selection([("loading", "Loading"), ("unloading", "Unloading"), ("hire", "Hire memo")])
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("to_approve", "To approve"),
            ("approved", "Approved"),
            ("billed", "Billed"),
            ("deducted", "Deducted"),
            ("cancel", "Cancelled"),
            ("waived", "Waived"),
        ],
        default="draft",
        tracking=True,
        copy=False,
    )
    approved_by_id = fields.Many2one("res.users", readonly=True, copy=False, tracking=True)
    approved_on = fields.Datetime(readonly=True, copy=False)
    self_approved = fields.Boolean(readonly=True, copy=False)
    waive_reason = fields.Text(tracking=True)
    move_line_id = fields.Many2one("account.move.line", string="Settling line", readonly=True, copy=False)
    move_id = fields.Many2one("account.move", related="move_line_id.move_id", string="Settling document")
    recovery_move_id = fields.Many2one("account.move", readonly=True, copy=False)
    billing_status = fields.Selection(
        [("na", "-"), ("unbilled", "Unbilled"), ("done", "Billed / deducted")], compute="_compute_billing_status",
        store=True,
    )

    @api.depends("trip_id", "consignment_id")
    def _compute_company(self):
        for charge in self:
            charge.company_id = charge.consignment_id.company_id or charge.trip_id.company_id or charge.company_id \
                or self.env.company

    @api.depends("recover_from", "consignment_id", "trip_id", "driver_id")
    def _compute_partner(self):
        for charge in self:
            if charge.recover_from == "customer":
                charge.partner_id = charge.consignment_id.billing_partner_id or charge.partner_id
            elif charge.recover_from == "vendor":
                memo = charge.hire_memo_id or charge.trip_id.hire_memo_ids.filtered(
                    lambda p: p.state in ("purchase", "done"))[:1]
                charge.partner_id = memo.partner_id or charge.partner_id
            elif charge.recover_from == "driver":
                driver = charge.driver_id or charge.trip_id.open_assignment_id.driver_id or \
                    charge.trip_id.assignment_ids[:1].driver_id
                charge.driver_id = driver
                charge.partner_id = driver.partner_id
            else:
                charge.partner_id = charge.partner_id

    @api.depends("state", "recover_from")
    def _compute_billing_status(self):
        for charge in self:
            if charge.state in ("billed", "deducted"):
                charge.billing_status = "done"
            elif charge.state == "approved":
                charge.billing_status = "unbilled"
            else:
                charge.billing_status = "na"

    @api.constrains("amount")
    def _check_amount(self):
        for charge in self:
            if charge.amount <= 0:
                raise ValidationError(_("A charge amount must be positive."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "/") == "/":
                vals["name"] = self.env["ir.sequence"].next_by_code("tms.trip.charge") or "/"
        return super().create(vals_list)

    def unlink(self):
        if any(c.state not in ("draft", "cancel") for c in self):
            raise UserError(_("Approved charges are never deleted; cancel or waive them."))
        return super().unlink()

    # ---------------------------------------------------------------- approval
    def _tms_needs_bm_approval(self):
        """BR-CHG-04: above the self-approval limit; tms_contract adds every customer charge on a contract item."""
        self.ensure_one()
        return self.amount > self.company_id.tms_charge_self_approval_limit

    def action_confirm(self):
        for charge in self:
            if charge.state != "draft":
                continue
            if not charge.partner_id and charge.recover_from != "driver":
                raise UserError(_("Choose the recovery partner first."))
            if charge._tms_needs_bm_approval():
                charge.state = "to_approve"
                users = users_in_group(self.env, "tms_core.group_branch_manager", charge.company_id)
                schedule(charge, users - charge.create_uid, _("Charge %s awaits approval", charge.name))  # BR-NTF-06
            else:
                charge.write({"state": "approved", "self_approved": True, "approved_by_id": self.env.user.id,
                              "approved_on": fields.Datetime.now()})
                self.env["tms.override.log"].log("self_approved_charge", charge,
                                                 _("Charge %s approved on confirmation", charge.name),
                                                 reason=_("Within the self-approval limit (BR-CHG-04)"),
                                                 amount=charge.amount)
                charge._tms_on_approved()
        return True

    def action_approve(self):
        for charge in self:
            if charge.state != "to_approve":
                raise UserError(_("Only charges awaiting approval are approved."))
            if not self.env.user.has_group("tms_core.group_branch_manager"):
                raise UserError(_("A Branch Manager approves this charge (BR-CHG-04)."))
            if charge.create_uid == self.env.user:
                raise UserError(_("You cannot approve a charge you raised."))
            charge.write({"state": "approved", "approved_by_id": self.env.user.id,
                          "approved_on": fields.Datetime.now()})
            charge.activity_ids.action_feedback(feedback=_("Approved"))
            charge._tms_on_approved()
        return True

    def _tms_on_approved(self):
        """Hook: tms_estimate adds a customer charge as an order item (BR-CHG-05);
        tms_contract tops up the reservation (BR-CON-20)."""
        return True

    def action_waive(self):
        reason = self.env.context.get("tms_reason")
        if not reason:
            raise UserError(_("A waiver needs a reason."))
        for charge in self:
            if not self.env.user.has_group("tms_core.group_branch_manager"):
                raise UserError(_("Only a Branch Manager waives a charge."))
            charge.write({"state": "waived", "waive_reason": reason})
            self.env["tms.override.log"].log("waiver", charge, _("Charge %s waived", charge.name), reason=reason,
                                             amount=charge.amount)
        return True

    def action_cancel(self):
        for charge in self:
            if charge.state in ("billed", "deducted"):
                raise UserError(_("Billed or deducted charges are reversed through credit notes."))
            charge.state = "cancel"
        return True

    def action_draft(self):
        self.filtered(lambda c: c.state == "cancel").write({"state": "draft"})

    # ---------------------------------------------------------------- settlement
    def _tms_income_account(self):
        self.ensure_one()
        product = self.category_id.product_id
        account = product.property_account_income_id or product.categ_id.property_account_income_categ_id
        return account or _default_account(self.env, self.company_id, "income_other") or _default_account(
            self.env, self.company_id, "income")

    def action_invoice_third_party(self):
        """BR-CHG-03: a third-party recovery is invoiced to that party."""
        moves = self.env["account.move"]
        for charge in self:
            if charge.recover_from != "third_party" or charge.state != "approved":
                raise UserError(_("Only approved third-party recoveries are invoiced here."))
            move = self.env["account.move"].with_company(charge.company_id).create({
                "move_type": "out_invoice",
                "partner_id": charge.partner_id.id,
                "journal_id": charge.company_id.tms_branch_journal("sale").id,
                "invoice_line_ids": [(0, 0, {
                    "name": "%s %s" % (charge.name, charge.category_id.name),
                    "quantity": 1,
                    "price_unit": charge.amount,
                    "account_id": charge._tms_income_account().id,
                    "tms_charge_id": charge.id,
                    "tms_trip_id": charge.trip_id.id,
                    "tms_consignment_id": charge.consignment_id.id,
                    "tms_line_kind": "recovery",
                    "tax_ids": [(5, 0, 0)],
                })],
            })
            charge.write({"move_line_id": move.invoice_line_ids[:1].id, "state": "billed"})
            moves |= move
        return {"type": "ir.actions.act_window", "res_model": "account.move", "view_mode": "list,form",
                "domain": [("id", "in", moves.ids)]}

    def action_vendor_debit_note(self):
        """BR-CHG-06: once the hire bill is posted, a vendor recovery becomes a debit note."""
        moves = self.env["account.move"]
        for charge in self:
            if charge.recover_from != "vendor" or charge.state != "approved":
                raise UserError(_("Only approved vendor recoveries become debit notes."))
            # the vendor recovery belongs to the trip's branch, which hired the vehicle (BR-BRN-04, BR-BRN-10)
            company = charge.trip_id.company_id or charge.company_id
            move = self.env["account.move"].with_company(company).create({
                "move_type": "in_refund",
                "partner_id": charge.partner_id.id,
                "journal_id": company.tms_branch_journal("purchase").id,
                "invoice_date": fields.Date.context_today(self),
                "invoice_line_ids": [(0, 0, {
                    "name": "%s %s" % (charge.name, charge.category_id.name),
                    "quantity": 1,
                    "price_unit": charge.amount,
                    "account_id": charge._tms_income_account().id,
                    "tms_charge_id": charge.id,
                    "tms_trip_id": charge.trip_id.id,
                    "tms_line_kind": "recovery",
                    "vehicle_id": charge.trip_id.primary_vehicle_id.id,
                    "tax_ids": [(5, 0, 0)],
                })],
            })
            charge.write({"move_line_id": move.invoice_line_ids[:1].id})
            moves |= move
        return {"type": "ir.actions.act_window", "res_model": "account.move", "view_mode": "list,form",
                "domain": [("id", "in", moves.ids)]}

    def _tms_mark_settled_from_moves(self):
        for charge in self:
            line = charge.move_line_id
            if line and line.parent_state == "posted" and charge.state == "approved":
                charge.state = "deducted" if line.move_id.move_type in ("in_invoice", "in_refund") else "billed"


class TmsTripExpense(models.Model):
    """BR-EXP-01..09: expenses carry what a trip costs."""

    _name = "tms.trip.expense"
    _description = "Trip expense"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date desc, id desc"
    _check_company_auto = True

    name = fields.Char(default="/", copy=False, readonly=True)
    trip_id = fields.Many2one("tms.order", ondelete="restrict", index=True, tracking=True)
    consignment_id = fields.Many2one("tms.consignment", string="LR")
    company_id = fields.Many2one("res.company", string="Branch", required=True, compute="_compute_company",
                                 store=True, readonly=False, default=lambda s: s.env.company)
    currency_id = fields.Many2one(related="company_id.currency_id")
    date = fields.Date(required=True, default=fields.Date.context_today, tracking=True)
    category_id = fields.Many2one("tms.category", string="Type", required=True, domain=[("use_expense", "=", True)],
                                  tracking=True)
    amount = fields.Monetary(required=True, tracking=True)
    payment_mode = fields.Selection(PAYMENT_MODES, required=True, default="driver_cash", tracking=True)
    paid_to_id = fields.Many2one("res.partner", string="Paid to")
    driver_id = fields.Many2one("tms.driver", compute="_compute_driver", store=True, readonly=False)
    bill_reference = fields.Char()
    receipt = fields.Binary(attachment=True)
    receipt_name = fields.Char()
    state = fields.Selection([("draft", "Draft"), ("approved", "Approved"), ("cancel", "Cancelled")],
                             default="draft", tracking=True, copy=False)
    approved_by_id = fields.Many2one("res.users", readonly=True, copy=False)
    incident_id = fields.Many2one("tms.trip.incident")
    recoverable_from = fields.Selection([("none", "Not recoverable"), ("customer", "Customer"),
                                         ("vendor", "Vendor")], default="none", required=True)
    recoverable_amount = fields.Monetary()
    charge_id = fields.Many2one("tms.trip.charge", string="Recovery charge", readonly=True, copy=False)
    vehicle_id = fields.Many2one("fleet.vehicle", compute="_compute_vehicle", store=True, readonly=False)
    tms_cost_class = fields.Selection(COST_CLASSES, string="Cost class", compute="_compute_cost_class", store=True,
                                      readonly=False)
    tms_overhead_reason = fields.Char(string="Overhead reason")
    fuel_litres = fields.Float()
    fuel_rate = fields.Float()
    fuel_odometer = fields.Float()
    fuel_station = fields.Char()
    is_fuel = fields.Boolean(related="category_id.is_fuel")
    move_id = fields.Many2one("account.move", readonly=True, copy=False)

    @api.depends("trip_id")
    def _compute_company(self):
        for exp in self:
            exp.company_id = exp.trip_id.company_id or exp.company_id or self.env.company

    @api.depends("trip_id")
    def _compute_driver(self):
        for exp in self:
            exp.driver_id = exp.trip_id.open_assignment_id.driver_id or exp.trip_id.assignment_ids[:1].driver_id \
                or exp.trip_id.planned_driver_id or exp.driver_id

    @api.depends("trip_id")
    def _compute_vehicle(self):
        """BR-PNL-02, BR-ASG-13: trip costs name the trip's primary truck."""
        for exp in self:
            exp.vehicle_id = exp.trip_id.primary_vehicle_id or exp.trip_id.planned_vehicle_id or exp.vehicle_id

    @api.depends("category_id")
    def _compute_cost_class(self):
        for exp in self:
            exp.tms_cost_class = exp.category_id.default_cost_class or "trip_direct"

    @api.onchange("fuel_litres", "fuel_rate")
    def _onchange_fuel(self):
        if self.fuel_litres and self.fuel_rate:
            self.amount = round(self.fuel_litres * self.fuel_rate, 2)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "/") == "/":
                vals["name"] = self.env["ir.sequence"].next_by_code("tms.trip.expense") or "/"
        records = super().create(vals_list)
        records._tms_ensure_recovery_charge()  # BR-EXP-06: on save
        return records

    def write(self, vals):
        locked = {"trip_id", "category_id", "amount", "payment_mode", "date", "vehicle_id", "estimate_line_id"}
        if locked & set(vals) and any(e.state == "approved" for e in self):
            raise UserError(_("An approved expense is not edited; reverse its entry instead."))
        res = super().write(vals)
        if {"recoverable_from", "recoverable_amount"} & set(vals):
            for exp in self.filtered(lambda e: e.charge_id and e.charge_id.state == "draft"):
                if exp.recoverable_from == "none" or not exp.recoverable_amount:
                    charge = exp.charge_id
                    exp.charge_id = False
                    charge.write({"state": "cancel"}) if "cancel" in dict(
                        charge._fields["state"].selection) else charge.unlink()
                else:
                    exp.charge_id.write({"amount": exp.recoverable_amount, "recover_from": exp.recoverable_from})
            self._tms_ensure_recovery_charge()
        return res

    def unlink(self):
        if any(e.state == "approved" for e in self):
            raise UserError(_("Approved expenses are never deleted; reverse their entry instead."))
        return super().unlink()

    def _tms_ensure_recovery_charge(self):
        """BR-EXP-06: a recoverable expense creates a draft charge for the recoverable amount."""
        for exp in self:
            if exp.recoverable_from != "none" and exp.recoverable_amount and not exp.charge_id:
                category = self.env["tms.category"].search(
                    [("use_charge", "=", True), ("code", "=", exp.category_id.code)], limit=1
                ) or self.env.ref("tms_core.tms_category_misc_recovery")
                exp.charge_id = self.env["tms.trip.charge"].create({
                    "trip_id": exp.trip_id.id,
                    "consignment_id": exp.consignment_id.id,
                    "category_id": category.id,
                    "amount": exp.recoverable_amount,
                    "recover_from": exp.recoverable_from,
                    "expense_id": exp.id,
                    "incident_id": exp.incident_id.id,
                    "note": _("Recovery of expense %s", exp.name),
                })

    def _tms_check_before_post(self):
        """Hook: tms_estimate enforces the estimate line (BR-ACT-05) and variance tolerance (BR-ACT-06);
        tms_profitability enforces the cost class rules (BR-PNL-01..09)."""
        return True

    def _tms_expense_line_vals(self):
        """Hook to add analytic distribution and estimate line to the expense's debit line."""
        self.ensure_one()
        account = self.category_id.account_id or self.category_id.product_id.property_account_expense_id \
            or _default_account(self.env, self.company_id, "expense")
        return {
            "name": "%s %s" % (self.name, self.category_id.name),
            "account_id": account.id,
            "debit": self.amount,
            "credit": 0.0,
            "tms_trip_id": self.trip_id.id,
            "tms_consignment_id": self.consignment_id.id,
            "tms_expense_id": self.id,
            "tms_cost_class": self.tms_cost_class,
            "tms_line_kind": "expense",
            # BR-ASG-13, BR-PNL-06: a replacement's costs book to the trip's primary truck, naming the replacement
            "vehicle_id": (self.trip_id.primary_vehicle_id or self.vehicle_id).id,
            "tms_replacement_vehicle_id": self.vehicle_id.id if self.vehicle_id and self.trip_id.primary_vehicle_id
            and self.vehicle_id != self.trip_id.primary_vehicle_id else False,
            "tms_overhead": self.tms_cost_class == "overhead" or bool(self.tms_overhead_reason),
            "tms_overhead_reason": self.tms_overhead_reason,
        }

    def _tms_credit_account_and_partner(self):
        self.ensure_one()
        company = self.company_id
        mode = self.payment_mode
        if mode == "driver_cash":
            account = company.tms_driver_advance_account_id
            partner = self.driver_id.partner_id
            if not partner:
                raise UserError(_("A driver-cash expense needs the driver."))
        elif mode == "company_cash":
            journal = company.tms_cash_journal_id or company.tms_branch_journal("cash")
            account, partner = journal.default_account_id, self.paid_to_id
        elif mode == "bank":
            journal = company.tms_bank_journal_id or company.tms_branch_journal("bank")
            account, partner = journal.default_account_id, self.paid_to_id
        elif mode == "fuel_card":
            account, partner = company.tms_fuel_card_account_id, self.paid_to_id
        elif mode == "fastag":
            account, partner = company.tms_fastag_account_id, self.paid_to_id
        else:
            partner = self.paid_to_id
            if not partner:
                raise UserError(_("A vendor-credit expense needs the vendor it is owed to."))
            account = partner.with_company(company).property_account_payable_id
        if not account:
            raise UserError(_("Configure the account for payment mode %s in Transport settings (BR-EXP-09).",
                              dict(PAYMENT_MODES)[mode]))
        return account, partner

    def action_approve(self):
        """BR-EXP-05, RL-05: needs a receipt and the Branch Accountant; posts to the trip and the vehicle."""
        for exp in self:
            if exp.state != "draft":
                continue
            if not self.env.user.has_group("tms_core.group_branch_accountant"):
                raise UserError(_("The Branch Accountant approves expenses (BR-EXP-05)."))
            if not exp.receipt:
                raise UserError(_("An expense without a receipt cannot be approved (RL-05)."))
            exp._tms_check_before_post()
            exp._tms_ensure_recovery_charge()
            account, partner = exp._tms_credit_account_and_partner()
            debit = exp._tms_expense_line_vals()
            journal = exp.company_id.tms_expense_journal_id or exp.company_id.tms_branch_journal("general")
            move = self.env["account.move"].with_company(exp.company_id).create({
                "move_type": "entry",
                "date": exp.date,
                "journal_id": journal.id,
                "ref": "%s %s" % (exp.name, exp.trip_id.name or ""),
                "line_ids": [
                    (0, 0, dict(debit, partner_id=partner.id if partner else False)),
                    (0, 0, {"name": exp.name, "account_id": account.id, "debit": 0.0, "credit": exp.amount,
                            "partner_id": partner.id if partner else False}),
                ],
            })
            move.action_post()
            exp.write({"state": "approved", "approved_by_id": self.env.user.id, "move_id": move.id})
        return True

    def action_cancel(self):
        for exp in self:
            if exp.move_id and exp.move_id.state == "posted":
                exp.move_id._reverse_moves([{"ref": _("Reversal of %s", exp.name)}], cancel=True)
            exp.state = "cancel"
        return True

    def action_create_recovery(self):
        self._tms_ensure_recovery_charge()


class TmsDriverAdvance(models.Model):
    """BR-EXP-07."""

    _name = "tms.driver.advance"
    _description = "Driver advance"
    _inherit = ["mail.thread"]
    _order = "date desc, id desc"

    name = fields.Char(default="/", copy=False, readonly=True)
    trip_id = fields.Many2one("tms.order", required=True, ondelete="restrict", index=True)
    company_id = fields.Many2one(related="trip_id.company_id", store=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    driver_id = fields.Many2one("tms.driver", required=True)
    amount = fields.Monetary(required=True)
    payment_mode = fields.Selection([("cash", "Cash"), ("bank", "Bank")], default="cash", required=True)
    date = fields.Date(required=True, default=fields.Date.context_today)
    paid_by_id = fields.Many2one("res.users", default=lambda s: s.env.user)
    move_id = fields.Many2one("account.move", readonly=True, copy=False)
    state = fields.Selection([("draft", "Draft"), ("posted", "Posted"), ("cancel", "Cancelled")], default="draft",
                             tracking=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "/") == "/":
                vals["name"] = self.env["ir.sequence"].next_by_code("tms.driver.advance") or "/"
        return super().create(vals_list)

    LOCKED_AFTER_POST = {"trip_id", "driver_id", "amount", "payment_mode", "date"}

    def write(self, vals):
        if self.LOCKED_AFTER_POST & set(vals) and any(adv.state != "draft" for adv in self):
            raise UserError(_("A posted driver advance is not edited; cancel its entry instead (BR-EXP-07)."))
        return super().write(vals)

    def unlink(self):
        if any(adv.state == "posted" for adv in self):
            raise UserError(_("A posted driver advance is never deleted (BR-AUD-01)."))
        return super().unlink()

    def action_post(self):
        for adv in self:
            if adv.state != "draft":
                raise UserError(_("Only a draft advance is posted."))
            company = adv.company_id
            if not company.tms_driver_advance_account_id:
                raise UserError(_("Configure the driver advance account in Transport settings."))
            journal = (company.tms_cash_journal_id or company.tms_branch_journal("cash")) if adv.payment_mode == "cash" \
                else (company.tms_bank_journal_id or company.tms_branch_journal("bank"))
            move = self.env["account.move"].with_company(company).create({
                "move_type": "entry",
                "date": adv.date,
                "journal_id": journal.id,
                "ref": "%s %s" % (adv.name, adv.trip_id.name),
                "line_ids": [
                    (0, 0, {"name": adv.name, "account_id": company.tms_driver_advance_account_id.id,
                            "partner_id": adv.driver_id.partner_id.id, "debit": adv.amount, "credit": 0.0,
                            "tms_trip_id": adv.trip_id.id}),
                    (0, 0, {"name": adv.name, "account_id": journal.default_account_id.id,
                            "debit": 0.0, "credit": adv.amount}),
                ],
            })
            move.action_post()
            adv.write({"move_id": move.id, "state": "posted"})
        return True


class TmsDriverSettlement(models.Model):
    """BR-EXP-08, #23.3: S = Σ advances − Σ driver-cash expenses + Σ driver recoveries."""

    _name = "tms.driver.settlement"
    _description = "Driver settlement"
    _inherit = ["mail.thread"]
    _order = "id desc"

    name = fields.Char(default="/", copy=False, readonly=True)
    trip_id = fields.Many2one("tms.order", required=True, ondelete="restrict", index=True)
    company_id = fields.Many2one(related="trip_id.company_id", store=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    driver_id = fields.Many2one("tms.driver", required=True)
    advance_total = fields.Monetary(compute="_compute_totals", store=True)
    cash_expense_total = fields.Monetary(compute="_compute_totals", store=True)
    recovery_total = fields.Monetary(compute="_compute_totals", store=True)
    balance = fields.Monetary(compute="_compute_totals", store=True,
                              help="Positive: collected from the driver. Negative: paid to the driver.")
    state = fields.Selection([("draft", "Draft"), ("done", "Done")], default="draft", tracking=True)
    move_id = fields.Many2one("account.move", readonly=True, copy=False)
    recovery_move_id = fields.Many2one("account.move", readonly=True, copy=False)
    payment_mode = fields.Selection([("cash", "Cash"), ("bank", "Bank")], default="cash", required=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "/") == "/":
                vals["name"] = self.env["ir.sequence"].next_by_code("tms.driver.settlement") or "/"
        return super().create(vals_list)

    def _lines(self):
        self.ensure_one()
        trip = self.trip_id
        advances = trip.advance_ids.filtered(lambda a: a.state == "posted" and a.driver_id == self.driver_id)
        expenses = trip.expense_ids.filtered(lambda e: e.state == "approved" and e.payment_mode == "driver_cash"
                                             and e.driver_id == self.driver_id)
        charges = trip.charge_ids.filtered(lambda c: c.recover_from == "driver" and c.driver_id == self.driver_id
                                           and c.state in ("approved", "deducted"))
        return advances, expenses, charges

    @api.depends("trip_id.advance_ids.state", "trip_id.expense_ids.state", "trip_id.charge_ids.state", "driver_id",
                 "trip_id.advance_ids.amount", "trip_id.expense_ids.amount", "trip_id.charge_ids.amount")
    def _compute_totals(self):
        for st in self:
            if st.state == "done":
                continue
            advances, expenses, charges = st._lines()
            st.advance_total = sum(advances.mapped("amount"))
            st.cash_expense_total = sum(expenses.mapped("amount"))
            st.recovery_total = sum(charges.mapped("amount"))
            st.balance = st.advance_total - st.cash_expense_total + st.recovery_total

    def action_done(self):
        if not self.env.su and not self.env.user.has_group("tms_core.group_branch_accountant"):
            raise UserError(_("The Branch Accountant settles driver advances."))
        for st in self:
            if st.state == "done":
                continue
            company = st.company_id
            adv_account = company.tms_driver_advance_account_id
            if not adv_account:
                raise UserError(_("Configure the driver advance account in Transport settings."))
            st._compute_totals()
            advances, expenses, charges = st._lines()
            partner = st.driver_id.partner_id
            general = company.tms_branch_journal("general")
            to_deduct = charges.filtered(lambda c: c.state == "approved")
            if to_deduct:
                lines = [(0, 0, {"name": c.name, "account_id": c._tms_income_account().id, "debit": 0.0,
                                 "credit": c.amount, "tms_trip_id": st.trip_id.id, "tms_charge_id": c.id,
                                 "tms_line_kind": "recovery", "vehicle_id": st.trip_id.primary_vehicle_id.id})
                         for c in to_deduct]
                lines.append((0, 0, {"name": _("Driver recoveries %s", st.name), "account_id": adv_account.id,
                                     "partner_id": partner.id, "debit": sum(to_deduct.mapped("amount")),
                                     "credit": 0.0}))
                rec_move = self.env["account.move"].with_company(company).create({
                    "move_type": "entry", "journal_id": general.id, "ref": st.name, "line_ids": lines})
                rec_move.action_post()
                for c in to_deduct:
                    c.write({"state": "deducted",
                             "move_line_id": rec_move.line_ids.filtered(lambda line, c=c: line.tms_charge_id == c).id})
                st.recovery_move_id = rec_move
            if st.currency_id.round(st.balance):
                journal = (company.tms_cash_journal_id or company.tms_branch_journal("cash")) \
                    if st.payment_mode == "cash" else (company.tms_bank_journal_id or company.tms_branch_journal("bank"))
                amount = abs(st.balance)
                collect = st.balance > 0
                move = self.env["account.move"].with_company(company).create({
                    "move_type": "entry", "journal_id": journal.id, "ref": st.name,
                    "line_ids": [
                        (0, 0, {"name": st.name, "account_id": journal.default_account_id.id,
                                "debit": amount if collect else 0.0, "credit": 0.0 if collect else amount}),
                        (0, 0, {"name": st.name, "account_id": adv_account.id, "partner_id": partner.id,
                                "debit": 0.0 if collect else amount, "credit": amount if collect else 0.0}),
                    ],
                })
                move.action_post()
                st.move_id = move
            st.state = "done"
        return True
