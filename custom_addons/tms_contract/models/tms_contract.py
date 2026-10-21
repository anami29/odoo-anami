import logging
from datetime import timedelta

from odoo import SUPERUSER_ID, _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from odoo.addons.tms_core.models.tools import schedule, users_in_group
from odoo.addons.tms_estimate.models.product import COST_BASES

_logger = logging.getLogger(__name__)

CONTRACT_STATES = [
    ("draft", "Draft"),
    ("active", "Active"),
    ("exhausted", "Exhausted"),
    ("expired", "Expired"),
    ("closed", "Closed"),
]
RATE_BASES = COST_BASES + [("tonne", "Per tonne"), ("trip", "Per trip")]
RESERVATION_EVENTS = [
    ("release", "Release"),
    ("incidental", "Incidental cost above estimate"),
    ("charge", "Customer charge outside the incidental categories"),
    ("amendment", "Item amendment"),
    ("escalation", "Escalation"),
    ("excess_absorbed", "Excess absorbed by an extension"),
    ("invoice", "Invoice posted"),
    ("credit_note", "Rate-correction credit note"),
    ("closure", "Closure"),
    ("cancel", "Cancellation"),
    ("restatement", "GST restatement"),
    ("migration", "Opening reservation"),
]
# invoice line kinds that count as invoiced against the contract (BR-CON-09)
INVOICED_KINDS = ("freight", "charge", "rate_correction", "dead_freight", "supplementary")
GST_RATES = {"fcm5": 5.0, "fcm18": 18.0}


class TmsContract(models.Model):
    """BR-CON-01..27: a customer's order for transport at fixed km rates up to a fixed value."""

    _name = "tms.contract"
    _description = "Sales contract"
    _inherit = ["mail.thread", "mail.activity.mixin", "tms.reason.mixin"]
    _order = "id desc"
    _check_company_auto = True

    name = fields.Char(string="Number", default="/", copy=False, readonly=True, tracking=True, index=True)
    state = fields.Selection(CONTRACT_STATES, default="draft", required=True, tracking=True, copy=False, index=True)
    submitted = fields.Boolean(readonly=True, copy=False, tracking=True)
    company_id = fields.Many2one("res.company", string="Branch", required=True, default=lambda s: s.env.company,
                                 tracking=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    partner_id = fields.Many2one("res.partner", string="Customer", required=True, tracking=True,
                                 domain=[("is_company", "=", True)])
    billing_partner_ids = fields.Many2many(
        "res.partner", "tms_contract_billing_partner_rel", "contract_id", "partner_id", string="Billing parties",
        help="One per plant and GSTIN; contract LRs bill only to these (BR-CON-24).")
    branch_ids = fields.Many2many(
        "res.company", "tms_contract_branch_rel", "contract_id", "company_id", string="Releasing branches",
        help="Branches that release items and book LRs against the one balance (BR-CON-24).")
    user_id = fields.Many2one("res.users", string="Sales Executive", default=lambda s: s.env.user, tracking=True)
    po_number = fields.Char(string="Customer PO / LOI", tracking=True)
    po_date = fields.Date(string="PO / LOI date")
    po_file = fields.Binary(string="PO / LOI file", attachment=True)
    po_filename = fields.Char()
    date_start = fields.Date(string="Start", required=True, default=fields.Date.context_today, tracking=True)
    date_end = fields.Date(string="End", required=True, tracking=True)
    value = fields.Monetary(string="Contract value", required=True, tracking=True)
    value_basis = fields.Selection([("untaxed", "Before GST"), ("taxed", "Including GST")], default="untaxed",
                                   required=True, tracking=True,
                                   help="Every budget, reservation and invoiced amount is measured on this basis.")
    payment_term_id = fields.Many2one("account.payment.term", string="Payment terms")
    incidental_category_ids = fields.Many2many(
        "tms.category", "tms_contract_incidental_category_rel", "contract_id", "category_id",
        string="Incidentals billed at actual", domain=[("use_charge", "=", True)],
        help="Customer charges in these categories bill at the amount of their posted bill or expense (BR-CON-07).")
    claims_reduce_invoiced = fields.Boolean(
        help="The PO lets claim credit notes reduce the amount invoiced against the value (BR-CON-09).")
    opening_consumption = fields.Monetary(
        help="Amount invoiced against the contract before go-live (BR-MIG-09).", tracking=True)
    rate_ids = fields.One2many("tms.contract.rate", "contract_id", string="Rates", copy=True)
    extension_ids = fields.One2many("tms.contract.extension", "contract_id", string="Extensions")
    excess_ids = fields.One2many("tms.contract.excess", "contract_id", string="Excess approvals")
    reservation_ids = fields.One2many("tms.contract.reservation", "contract_id", string="Reservation ledger")
    escalation_ids = fields.One2many("tms.contract.escalation", "contract_id", string="Escalation revisions")
    item_ids = fields.One2many("sale.order.line", "tms_contract_id", string="Items",
                               domain=[("tms_is_movement", "=", True)])
    order_id = fields.Many2one("sale.order", string="Contract order", readonly=True, copy=False)
    approved_by_id = fields.Many2one("res.users", readonly=True, copy=False, tracking=True)
    approved_on = fields.Datetime(readonly=True, copy=False)
    closed_on = fields.Date(readonly=True, copy=False)
    # BR-MD-16, BR-CON-27
    free_hours_loading = fields.Float(string="Free hours at loading")
    free_hours_unloading = fields.Float(string="Free hours at unloading")
    detention_rate = fields.Monetary(string="Detention rate per day")
    min_commitment = fields.Monetary(string="Minimum commitment")
    shortfall_move_id = fields.Many2one("account.move", string="Shortfall invoice", readonly=True, copy=False)
    sla_transit_days = fields.Float(string="SLA transit days")
    sla_penalty_per_day = fields.Monetary(string="SLA penalty per day late")
    sla_move_ids = fields.Many2many("account.move", "tms_contract_sla_move_rel", "contract_id", "move_id",
                                    string="SLA deduction notes", readonly=True, copy=False)
    deposit_kind = fields.Selection([("none", "None"), ("deposit", "Security deposit"), ("bg", "Bank guarantee")],
                                    default="none", string="Security")
    deposit_amount = fields.Monetary(string="Security amount")
    deposit_reference = fields.Char(string="Security reference")
    deposit_expiry = fields.Date(string="Security expiry")
    # BR-CON-22
    escalation_clause = fields.Boolean(string="Diesel escalation clause")
    diesel_base_price = fields.Float(string="Base diesel price")
    diesel_base_date = fields.Date(string="Base price date")
    diesel_index_source = fields.Char(string="Index source")
    escalation_band = fields.Float(string="Band %", help="No revision while the change stays within the band.")
    escalation_review = fields.Selection([("monthly", "Monthly"), ("quarterly", "Quarterly"),
                                          ("half_yearly", "Half-yearly")], string="Review frequency")
    escalation_revises_released = fields.Boolean(
        string="Revision also applies to released items",
        help="The PO revises released items too: the difference bills as a supplementary line (BR-CON-22).")
    # alerts already sent (BR-NTF-13, 15, 16, 19)
    util_alert_sent = fields.Boolean(copy=False, readonly=True)
    expiry_alert_sent = fields.Boolean(copy=False, readonly=True)
    overrun_alert_sent = fields.Boolean(copy=False, readonly=True)
    deposit_alert_sent = fields.Boolean(copy=False, readonly=True)
    # BR-CON-17 figures
    extension_total = fields.Monetary(compute="_compute_figures", string="Extensions added")
    value_total = fields.Monetary(compute="_compute_figures", string="Value with extensions")
    excess_approved = fields.Monetary(compute="_compute_figures", string="Approved excess (open)")
    invoiced = fields.Monetary(compute="_compute_figures")
    reserved = fields.Monetary(compute="_compute_figures", string="Open reservations")
    balance = fields.Monetary(compute="_compute_figures")
    utilisation = fields.Float(compute="_compute_figures", string="Utilisation %")
    over_contract = fields.Monetary(compute="_compute_figures", string="Over-contract",
                                    help="Reserved or invoiced above value plus extensions (BR-CON-19).")
    fully_reserved = fields.Boolean(compute="_compute_figures")
    item_count = fields.Integer(compute="_compute_counts")
    lr_count = fields.Integer(compute="_compute_counts")
    invoice_count = fields.Integer(compute="_compute_counts")
    sla_penalty_due = fields.Monetary(compute="_compute_sla", string="SLA penalties due")
    as_at_date = fields.Date(string="Balance as at")
    balance_as_at = fields.Monetary(compute="_compute_balance_as_at", string="Balance on that date")

    _sql_constraints = [
        ("value_positive", "CHECK(value >= 0)", "The contract value cannot be negative."),
    ]

    # ------------------------------------------------------------------ computes
    @api.depends("value", "opening_consumption", "extension_ids.state", "extension_ids.added_value",
                 "excess_ids.state", "excess_ids.open_amount", "reservation_ids.amount", "item_ids.tms_release_state")
    def _compute_figures(self):
        for contract in self:
            if not contract.id:
                contract.update({"extension_total": 0.0, "value_total": contract.value, "excess_approved": 0.0,
                                 "invoiced": 0.0, "reserved": 0.0, "balance": contract.value, "utilisation": 0.0,
                                 "over_contract": 0.0, "fully_reserved": False})
                continue
            figs = contract._tms_figures()
            value_total = figs["V"]
            used = figs["I"] + figs["R"]
            contract.extension_total = value_total - contract.value
            contract.value_total = value_total
            contract.excess_approved = sum(contract.sudo().excess_ids.mapped("open_amount"))
            contract.invoiced = figs["I"]
            contract.reserved = figs["R"]
            contract.balance = figs["balance"]
            contract.utilisation = used / value_total * 100.0 if value_total else 0.0
            contract.over_contract = max(used - value_total, 0.0)
            contract.fully_reserved = contract.state != "draft" and figs["balance"] <= 0.005

    def _compute_counts(self):
        for contract in self:
            items = contract.sudo().item_ids
            contract.item_count = len(items)
            lrs = items.mapped("tms_lr_ids")
            contract.lr_count = len(lrs)
            contract.invoice_count = len(contract._tms_invoices())

    @api.depends("as_at_date")
    def _compute_balance_as_at(self):
        """BR-CON-20: the balance can be read as at any date."""
        for contract in self:
            contract.balance_as_at = contract._tms_balance_at(contract.as_at_date) if contract.as_at_date \
                and contract.id else 0.0

    def _compute_sla(self):
        for contract in self:
            contract.sla_penalty_due = sum(amount for _lr, _days, amount in contract._tms_sla_penalties())

    # ------------------------------------------------------------------ figures (#23.7)
    def _tms_gross_factor(self, date=None):
        """Measure on the value basis: GST at the GTA option in force on the date (BR-CON-01)."""
        self.ensure_one()
        if self.value_basis != "taxed":
            return 1.0
        option = self.company_id.tms_get_gta_option(date or fields.Date.context_today(self))
        return 1.0 + GST_RATES.get(option, 0.0) / 100.0

    def _tms_extension_value(self, date=None):
        self.ensure_one()
        exts = self.sudo().extension_ids.filtered(lambda e: e.state == "approved")
        if date:
            exts = exts.filtered(lambda e: e.approved_on and e.approved_on.date() <= date)
        return sum(exts.mapped("added_value"))

    def _tms_invoiced_by_item(self, date=None):
        """Σ I_j: posted invoice lines on the contract's items, net of rate-correction credit notes."""
        self.ensure_one()
        self.env.flush_all()
        kinds = list(INVOICED_KINDS) + (["claim"] if self.claims_reduce_invoiced else [])
        amount = "aml.price_total" if self.value_basis == "taxed" else "aml.price_subtotal"
        query = """
            SELECT COALESCE(aml.tms_sale_line_id, lr.sale_line_id) AS item,
                   SUM(CASE WHEN am.move_type = 'out_refund' THEN -1 ELSE 1 END * {amount})
              FROM account_move_line aml
              JOIN account_move am ON am.id = aml.move_id
         LEFT JOIN tms_consignment lr ON lr.id = aml.tms_consignment_id
              JOIN sale_order_line sol ON sol.id = COALESCE(aml.tms_sale_line_id, lr.sale_line_id)
             WHERE am.state = 'posted' AND am.move_type IN ('out_invoice', 'out_refund')
               AND aml.display_type = 'product' AND aml.tms_line_kind IN %s
               AND sol.tms_contract_id = %s {date_clause}
          GROUP BY 1
        """.format(amount=amount, date_clause="AND am.date <= %s" if date else "")
        params = [tuple(kinds), self.id] + ([date] if date else [])
        self.env.cr.execute(query, params)
        return {row[0]: row[1] or 0.0 for row in self.env.cr.fetchall()}

    def _tms_reserved_by_item(self, date=None):
        self.ensure_one()
        self.env["tms.contract.reservation"].flush_model()
        query = """SELECT sale_line_id, SUM(amount) FROM tms_contract_reservation
                    WHERE contract_id = %s {date_clause} GROUP BY sale_line_id""".format(
            date_clause="AND date <= %s" if date else "")
        self.env.cr.execute(query, [self.id] + ([date] if date else []))
        return {row[0]: row[1] or 0.0 for row in self.env.cr.fetchall()}

    def _tms_figures(self):
        """V, ΣI, ΣR and the contract balance (#23.7), straight from the database."""
        self.ensure_one()
        contract = self.sudo()
        inv = contract._tms_invoiced_by_item()
        res = contract._tms_reserved_by_item()
        value = contract.value + contract._tms_extension_value()
        invoiced = contract.opening_consumption + sum(inv.values())
        reserved = sum(res.values())
        return {"V": value, "I": invoiced, "R": reserved, "balance": value - invoiced - reserved,
                "inv": inv, "res": res}

    def _tms_balance_at(self, date):
        self.ensure_one()
        contract = self.sudo()
        inv = contract._tms_invoiced_by_item(date)
        res = contract._tms_reserved_by_item(date)
        return contract.value + contract._tms_extension_value(date) - contract.opening_consumption \
            - sum(inv.values()) - sum(res.values())

    def _tms_open_excess(self, items=None, moves=None):
        """X_j: open approved excess earmarked to the item or invoice; no other item can use it."""
        self.ensure_one()
        excess = self.sudo().excess_ids.filtered(
            lambda e: e.state == "approved" and e.open_amount > 0.005 and (
                (items and e.sale_line_id in items) or (moves and e.move_id in moves)))
        return sum(excess.mapped("open_amount"))

    def _tms_lock(self):
        """BR-CON-25: balance checks and reservation writes run one at a time per contract."""
        if self.ids:
            self.env.cr.execute("SELECT id FROM tms_contract WHERE id IN %s FOR UPDATE", (tuple(self.ids),))

    def _tms_invoices(self):
        self.ensure_one()
        lines = self.env["account.move.line"].sudo().search([
            "|", ("tms_sale_line_id.tms_contract_id", "=", self.id),
            ("tms_consignment_id.sale_line_id.tms_contract_id", "=", self.id),
            ("move_id.move_type", "in", ("out_invoice", "out_refund"))])
        return lines.mapped("move_id")

    # ------------------------------------------------------------------ rates
    def _tms_rate_in_force(self, rate, date):
        """The contract's rate for the rate line's element on a date (BR-CON-05, BR-CON-16)."""
        self.ensure_one()
        candidates = self.sudo().rate_ids.filtered(
            lambda r: r.element_key == rate.element_key and r._tms_effective()
            and (not r.date_from or r.date_from <= date))
        return candidates.sorted(lambda r: (r.date_from or fields.Date.to_date("1900-01-01"), r.id))[-1:]

    # ------------------------------------------------------------------ CRUD
    @api.model_create_multi
    def create(self, vals_list):
        contracts = super().create(vals_list)
        for contract in contracts:
            if not contract.billing_partner_ids:
                contract.billing_partner_ids = contract.partner_id
            if not contract.branch_ids:
                contract.branch_ids = contract.company_id
        return contracts

    def write(self, vals):
        frozen = {"partner_id", "value", "value_basis", "date_start", "date_end", "company_id", "opening_consumption"}
        if frozen & set(vals) and not self.env.context.get("tms_contract_extension"):
            for contract in self.filtered(lambda c: c.state != "draft"):
                raise UserError(_("Contract %s is active: value, dates and customer change only through an approved "
                                  "extension (BR-CON-13).", contract.name))
        return super().write(vals)

    def unlink(self):
        if any(c.state != "draft" or c.order_id for c in self):
            raise UserError(_("Activated contracts are never deleted; close them (BR-AUD-04)."))
        return super().unlink()

    @api.constrains("date_start", "date_end")
    def _check_dates(self):
        for contract in self:
            if contract.date_end and contract.date_start and contract.date_end < contract.date_start:
                raise ValidationError(_("The contract ends before it starts."))

    # ------------------------------------------------------------------ workflow (WF-11)
    def action_submit(self):
        """UC-35: the Sales Executive submits; the Operations Head approves."""
        for contract in self:
            if contract.state != "draft":
                raise UserError(_("Only draft contracts are submitted."))
            if not contract.rate_ids.filtered(lambda r: not r.extension_id and not r.escalation_id):
                raise UserError(_("A contract needs at least one rate line (BR-CON-02)."))
            if contract.value <= 0:
                raise UserError(_("Enter the contract value."))
            if not contract.billing_partner_ids:
                raise UserError(_("List at least one billing party (BR-CON-24)."))
            contract.submitted = True
            users = users_in_group(self.env, "tms_core.group_operations_head", contract.company_id) - self.env.user
            schedule(contract, users, _("Contract %s to approve", contract.partner_id.name))
        return True

    def action_approve(self):
        """BR-CON-04: approval numbers the contract and opens its contract order with no items."""
        for contract in self:
            if contract.state != "draft" or not contract.submitted:
                raise UserError(_("Only submitted draft contracts are approved."))
            if not self.env.user.has_group("tms_core.group_operations_head"):
                raise UserError(_("The Operations Head approves contracts (BR-CON-04)."))
            if self.env.user in (contract.create_uid | contract.user_id):
                raise UserError(_("Whoever drafted the contract cannot approve it (BR-BRN-09)."))
            name = contract.company_id._tms_contract_sequence().next_by_id(contract.date_start)
            order = self.env["sale.order"].sudo().with_company(contract.company_id).create({
                "partner_id": contract.partner_id.id,
                "company_id": contract.company_id.id,
                "user_id": contract.user_id.id,
                "client_order_ref": contract.po_number,
                "origin": name,
                "payment_term_id": contract.payment_term_id.id,
                "require_signature": False,
                "tms_contract_id": contract.id,
            })
            order.action_confirm()
            contract.write({"name": name, "state": "active", "submitted": False, "order_id": order.id,
                            "approved_by_id": self.env.user.id, "approved_on": fields.Datetime.now()})
            contract.activity_ids.action_feedback(feedback=_("Approved"))
            contract._tms_update_state()
        return True

    def action_reject(self):
        reason = self.env.context.get("tms_reason")
        if not reason:
            raise UserError(_("A rejection needs a reason."))
        for contract in self:
            if not self.env.user.has_group("tms_core.group_operations_head"):
                raise UserError(_("The Operations Head rejects contracts."))
            contract.submitted = False
            contract.activity_ids.action_feedback(feedback=_("Rejected"))
            contract.message_post(body=_("Rejected: %s", reason))
            schedule(contract, contract.user_id, _("Contract rejected"), reason)
        return True

    def action_close(self):
        """BR-CON-14: the Sales Executive closes the contract once every item is closed for billing."""
        for contract in self:
            if contract.state in ("draft", "closed"):
                raise UserError(_("Only activated contracts are closed."))
            open_items = contract.sudo().item_ids.filtered(lambda i: i.tms_release_state in ("draft", "released"))
            if open_items.filtered(lambda i: i.tms_release_state == "released"):
                raise UserError(_("Items %s are still open for billing.",
                                  ", ".join(str(n) for n in open_items.mapped("tms_item_no"))))
            open_items.filtered(lambda i: i.tms_release_state == "draft").write({"tms_release_state": "cancel"})
            contract.sudo().excess_ids._tms_lapse()
            contract.write({"state": "closed", "closed_on": fields.Date.context_today(self)})
            if contract.order_id and contract.order_id.state == "sale":
                contract.order_id.sudo().action_lock()
        return True

    def _tms_update_state(self):
        """BR-CON-14: Exhausted when invoiced reaches value plus extensions and excess; Expired after its end."""
        today = fields.Date.context_today(self)
        for contract in self.filtered(lambda c: c.state in ("active", "exhausted", "expired")):
            figs = contract._tms_figures()
            excess = sum(contract.sudo().excess_ids.filtered(lambda e: e.state in ("approved", "closed")).mapped(
                lambda e: e.amount - e.lapsed_amount - e.absorbed_amount))
            if contract.date_end and today > contract.date_end:
                new = "expired"
            elif figs["V"] and figs["I"] >= figs["V"] + excess - 0.005:
                new = "exhausted"
            else:
                new = "active"
            if new != contract.state:
                contract.with_context(tms_contract_extension=True).state = new

    def _tms_check_can_release(self, date):
        """BR-CON-10, BR-CON-11: only a new release needs an Active contract."""
        self.ensure_one()
        self._tms_update_state()
        if self.state != "active":
            raise UserError(_("Contract %(c)s is %(s)s: no item is released until an extension is approved "
                              "(BR-CON-11).", c=self.name, s=dict(CONTRACT_STATES)[self.state]))
        if date < self.date_start or date > self.date_end:
            raise UserError(_("Contract %s is not in force on this date.", self.name))
        if self.env.company not in self.branch_ids:
            raise UserError(_("Branch %(b)s may not release items of contract %(c)s (BR-CON-24).",
                              b=self.env.company.name, c=self.name))

    # ------------------------------------------------------------------ alerts
    def _tms_after_change(self):
        """BR-NTF-13 utilisation alert, BR-NTF-16 overrun alert."""
        for contract in self.sudo():
            if contract.state == "draft":
                continue
            figs = contract._tms_figures()
            util = (figs["I"] + figs["R"]) / figs["V"] * 100.0 if figs["V"] else 0.0
            limit = contract.company_id.tms_contract_util_alert
            if limit and util >= limit and not contract.util_alert_sent:
                users = contract.user_id | users_in_group(self.env, "tms_core.group_branch_manager",
                                                          contract.company_id)
                summary = _("Contract %(c)s is %(u).0f%% used", c=contract.name, u=util)
                schedule(contract, users, summary)
                contract._tms_email(users, summary)
                contract.util_alert_sent = True
            elif util < limit and contract.util_alert_sent:
                contract.util_alert_sent = False
            if figs["balance"] < -0.005 and not contract.overrun_alert_sent:
                users = contract.user_id | users_in_group(self.env, "tms_core.group_finance_head",
                                                          contract.company_id)
                summary = _("Contract %(c)s overrun by %(a).2f: new releases are held", c=contract.name,
                            a=-figs["balance"])
                schedule(contract, users, summary)
                contract._tms_email(users, summary)
                contract.overrun_alert_sent = True
            elif figs["balance"] >= -0.005 and contract.overrun_alert_sent:
                contract.overrun_alert_sent = False

    def _tms_email(self, users, subject, body=None):
        partners = users.mapped("partner_id").filtered("email")
        if partners:
            self.sudo().message_notify(partner_ids=partners.ids, subject=subject, body=body or subject,
                                       record_name=self.name)

    @api.model
    def _cron_contract_daily(self):
        """Expiry (BR-CON-14), expiry alert (BR-NTF-15), cover date passed (BR-NTF-18), security expiry
        (BR-NTF-19), reservation re-sync and utilisation alerts."""
        today = fields.Date.context_today(self)
        contracts = self.sudo().search([("state", "in", ("active", "exhausted", "expired"))])
        contracts._tms_update_state()
        for contract in contracts:
            days = contract.company_id.tms_contract_expiry_days
            if contract.state == "active" and days and contract.date_end <= today + timedelta(days=days) \
                    and not contract.expiry_alert_sent:
                schedule(contract, contract.user_id, _("Contract %(c)s ends on %(d)s", c=contract.name,
                                                       d=contract.date_end))
                contract.expiry_alert_sent = True
            if contract.deposit_kind != "none" and contract.deposit_expiry \
                    and contract.deposit_expiry <= today + timedelta(days=30) and not contract.deposit_alert_sent:
                users = contract.user_id | users_in_group(self.env, "tms_core.group_finance_head",
                                                          contract.company_id)
                schedule(contract, users, _("Security %(r)s of contract %(c)s expires on %(d)s",
                                            r=contract.deposit_reference or "", c=contract.name,
                                            d=contract.deposit_expiry))
                contract.deposit_alert_sent = True
            items = contract.item_ids.filtered(lambda i: i.tms_release_state == "released")
            items._tms_sync_reservation("restatement", note=_("Daily re-sync"))
            items._tms_try_auto_close()
        contracts._tms_after_change()
        self.env["tms.contract.excess"]._cron_cover_overdue()

    # ------------------------------------------------------------------ BR-CON-27
    def _tms_sla_penalties(self):
        """Transit-time SLA: each day beyond the SLA transit days costs the penalty per day."""
        self.ensure_one()
        out = []
        if not (self.sla_transit_days and self.sla_penalty_per_day):
            return out
        for lr in self.sudo().item_ids.mapped("tms_lr_ids").filtered(lambda x: x.delivered_on):
            transit = (lr.delivered_on.date() - lr.date).days
            late = transit - self.sla_transit_days
            if late > 0:
                out.append((lr, late, round(late * self.sla_penalty_per_day, 2)))
        return out

    def action_raise_sla_deductions(self):
        """BR-CON-27: SLA penalties post as credit notes with reason Deduction; Invoiced stays unchanged."""
        self.ensure_one()
        done_lrs = self.sla_move_ids.mapped("invoice_line_ids.tms_consignment_id")
        rows = [(lr, days, amount) for lr, days, amount in self._tms_sla_penalties() if lr not in done_lrs]
        if not rows:
            raise UserError(_("No SLA penalty is due."))
        product = self.env.ref("tms_core.product_freight")
        move = self.env["account.move"].sudo().with_company(self.company_id).create({
            "move_type": "out_refund",
            "partner_id": rows[0][0].billing_partner_id.commercial_partner_id.id,
            "journal_id": self.company_id.tms_branch_journal("sale").id,
            "tms_credit_reason": "deduction",
            "invoice_origin": self.name,
            "invoice_line_ids": [(0, 0, {
                "product_id": product.id,
                "name": _("SLA penalty, LR %(lr)s, %(d)s day(s) late", lr=lr.name, d=days),
                "quantity": 1, "price_unit": amount, "tax_ids": [(5, 0, 0)],
                "tms_consignment_id": lr.id, "tms_line_kind": "deduction"}) for lr, days, amount in rows],
        })
        self.sla_move_ids = [(4, move.id)]
        return {"type": "ir.actions.act_window", "res_model": "account.move", "res_id": move.id, "view_mode": "form"}

    def action_bill_shortfall(self):
        """BR-CON-27: shortfall against the minimum commitment bills at the contract's end."""
        self.ensure_one()
        if self.shortfall_move_id:
            raise UserError(_("The shortfall is already billed."))
        if self.state not in ("expired", "closed", "exhausted"):
            raise UserError(_("The shortfall bills once the contract has ended."))
        figs = self._tms_figures()
        shortfall = self.min_commitment - figs["I"]
        if shortfall <= 0.005:
            raise UserError(_("Invoiced %(i).2f meets the minimum commitment.", i=figs["I"]))
        move = self.env["account.move"].sudo().with_company(self.company_id).create({
            "move_type": "out_invoice",
            "partner_id": self.billing_partner_ids[:1].commercial_partner_id.id or self.partner_id.id,
            "journal_id": self.company_id.tms_branch_journal("sale").id,
            "invoice_origin": self.name,
            "invoice_payment_term_id": self.payment_term_id.id,
            "invoice_line_ids": [(0, 0, {
                "product_id": self.env.ref("tms_core.product_freight").id,
                "name": _("Minimum commitment shortfall, contract %s", self.name),
                "quantity": 1, "price_unit": shortfall})],
        })
        self.shortfall_move_id = move
        return {"type": "ir.actions.act_window", "res_model": "account.move", "res_id": move.id, "view_mode": "form"}

    # ------------------------------------------------------------------ buttons
    def action_new_item(self):
        self.ensure_one()
        if not self.order_id:
            raise UserError(_("Activate the contract first: items are lines of its contract order."))
        return {"type": "ir.actions.act_window", "name": _("New contract item"), "res_model": "sale.order.line",
                "view_mode": "form", "views": [(self.env.ref("tms_contract.tms_contract_item_form").id, "form")],
                "context": {"default_tms_contract_id": self.id, "default_tms_is_movement": True}}

    def action_view_items(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "name": _("Items"), "res_model": "sale.order.line",
                "view_mode": "list,form",
                "views": [(self.env.ref("tms_contract.tms_contract_item_list").id, "list"),
                          (self.env.ref("tms_contract.tms_contract_item_form").id, "form")],
                "domain": [("tms_contract_id", "=", self.id), ("tms_is_movement", "=", True)],
                "context": {"default_tms_contract_id": self.id, "default_tms_is_movement": True}}

    def action_view_lrs(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "name": _("LRs"), "res_model": "tms.consignment",
                "view_mode": "list,form", "domain": [("sale_line_id.tms_contract_id", "=", self.id)]}

    def action_view_invoices(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "name": _("Invoices"), "res_model": "account.move",
                "view_mode": "list,form", "domain": [("id", "in", self._tms_invoices().ids)]}

    def action_new_extension(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "name": _("Extension"), "res_model": "tms.contract.extension",
                "view_mode": "form", "context": {"default_contract_id": self.id}}

    def action_print_statement(self):
        return self.env.ref("tms_contract.action_report_contract_statement").report_action(self)


class TmsContractRate(models.Model):
    """BR-CON-02, BR-CON-16: one line per vehicle class, accessory or contracted element."""

    _name = "tms.contract.rate"
    _description = "Contract rate"
    _order = "contract_id, sequence, date_from, id"

    contract_id = fields.Many2one("tms.contract", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="contract_id.company_id", store=True)
    currency_id = fields.Many2one(related="contract_id.currency_id")
    sequence = fields.Integer(default=10)
    kind = fields.Selection([("vehicle", "Vehicle class"), ("accessory", "Accessory"), ("element", "Other element")],
                            required=True, default="vehicle")
    vehicle_class_id = fields.Many2one("tms.vehicle.class", string="Vehicle class")
    product_id = fields.Many2one("product.product", string="Accessory or element",
                                 domain=[("tms_cost_item", "=", True)])
    name = fields.Char(compute="_compute_name", store=True, readonly=False)
    basis = fields.Selection(RATE_BASES, required=True, default="km")
    rate = fields.Monetary(required=True)
    date_from = fields.Date(string="Effective from")
    extension_id = fields.Many2one("tms.contract.extension", ondelete="cascade", index="btree_not_null")
    escalation_id = fields.Many2one("tms.contract.escalation", ondelete="cascade", index="btree_not_null")
    escalation_per_rupee = fields.Float(string="Escalation per ₹1 of diesel",
                                        help="Change in this rate for each rupee of diesel price change (BR-CON-22).")
    element_key = fields.Char(compute="_compute_element_key", store=True, index=True)
    effective = fields.Boolean(compute="_compute_effective", string="In force")

    @api.depends("kind", "vehicle_class_id", "product_id")
    def _compute_name(self):
        for rate in self:
            if rate.kind == "vehicle" and rate.vehicle_class_id:
                rate.name = rate.vehicle_class_id.name
            elif rate.product_id:
                rate.name = rate.product_id.name
            elif not rate.name:
                rate.name = False

    @api.depends("kind", "vehicle_class_id", "product_id", "name")
    def _compute_element_key(self):
        for rate in self:
            if rate.kind == "vehicle" and rate.vehicle_class_id:
                rate.element_key = "vc:%s" % rate.vehicle_class_id.id
            elif rate.product_id:
                rate.element_key = "p:%s" % rate.product_id.id
            else:
                rate.element_key = "n:%s" % (rate.name or "").strip().lower()

    @api.depends("extension_id.state", "escalation_id.state")
    def _compute_effective(self):
        for rate in self:
            rate.effective = rate._tms_effective()

    def _tms_effective(self):
        self.ensure_one()
        if self.extension_id:
            return self.extension_id.state == "approved"
        if self.escalation_id:
            return self.escalation_id.state == "applied"
        return True

    @api.depends("name", "rate", "basis", "date_from")
    def _compute_display_name(self):
        basis = dict(RATE_BASES)
        for rate in self:
            rate.display_name = "%s · %s %s%s" % (rate.name or "", rate.rate, basis.get(rate.basis, ""),
                                                 (" from %s" % rate.date_from) if rate.date_from else "")

    @api.constrains("kind", "vehicle_class_id", "product_id", "name")
    def _check_element(self):
        for rate in self:
            if rate.kind == "vehicle" and not rate.vehicle_class_id:
                raise ValidationError(_("A vehicle rate names its vehicle class."))
            if rate.kind == "accessory" and not rate.product_id:
                raise ValidationError(_("An accessory rate names its cost item."))
            if not rate.name:
                raise ValidationError(_("Name the contracted element."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("extension_id") and not vals.get("contract_id"):
                vals["contract_id"] = self.env["tms.contract.extension"].browse(vals["extension_id"]).contract_id.id
            if vals.get("extension_id") and not vals.get("date_from"):
                ext = self.env["tms.contract.extension"].browse(vals["extension_id"])
                vals["date_from"] = ext.rates_effective_date or ext.date
        rates = super().create(vals_list)
        rates._tms_check_editable()
        for rate in rates.filtered(lambda r: not r.date_from):
            rate.date_from = rate.contract_id.date_start
        return rates

    def write(self, vals):
        self._tms_check_editable()
        return super().write(vals)

    def unlink(self):
        self._tms_check_editable()
        if self.env["tms.contract.item.rate"].sudo().search_count([("rate_id", "in", self.ids)]):
            raise UserError(_("Rates used by items are never deleted."))
        return super().unlink()

    def _tms_check_editable(self):
        """BR-CON-16: rates change only through an extension with new rates or the escalation clause."""
        if self.env.context.get("tms_contract_rate_admin"):
            return
        for rate in self:
            if rate.contract_id.state == "draft":
                continue
            if rate.extension_id and rate.extension_id.state in ("draft", "to_approve"):
                continue
            if rate.escalation_id and rate.escalation_id.state == "draft":
                continue
            raise UserError(_("Contract rates change only through an approved extension carrying new rates, or "
                              "the escalation clause (BR-CON-16)."))


class TmsContractExtension(models.Model):
    """BR-CON-13, BR-CON-16: added value, a new end date or new rates, from the customer's amendment."""

    _name = "tms.contract.extension"
    _description = "Contract extension"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "id desc"

    name = fields.Char(string="Customer amendment reference", required=True, tracking=True)
    contract_id = fields.Many2one("tms.contract", required=True, ondelete="restrict", index=True,
                                  domain=[("state", "not in", ("draft", "closed"))])
    company_id = fields.Many2one(related="contract_id.company_id", store=True)
    currency_id = fields.Many2one(related="contract_id.currency_id")
    date = fields.Date(default=fields.Date.context_today, required=True)
    file = fields.Binary(string="Amendment file", attachment=True)
    filename = fields.Char()
    added_value = fields.Monetary(tracking=True)
    new_date_end = fields.Date(string="New end date", tracking=True)
    rate_ids = fields.One2many("tms.contract.rate", "extension_id", string="New rates")
    rates_effective_date = fields.Date(string="New rates effective from")
    state = fields.Selection([("draft", "Draft"), ("to_approve", "To approve"), ("approved", "Approved"),
                              ("refused", "Refused")], default="draft", tracking=True, copy=False)
    approval_role = fields.Selection([("bm", "Branch Manager"), ("oh", "Operations Head")],
                                     compute="_compute_approval_role", store=True)
    requested_by_id = fields.Many2one("res.users", default=lambda s: s.env.user, readonly=True)
    approved_by_id = fields.Many2one("res.users", readonly=True, copy=False)
    approved_on = fields.Datetime(readonly=True, copy=False)
    absorbed_excess = fields.Monetary(readonly=True, copy=False,
                                      help="Open extension-awaited excess this extension consumed (BR-CON-18).")

    @api.depends("rate_ids", "added_value", "contract_id.value", "contract_id.company_id.tms_contract_extension_limit")
    def _compute_approval_role(self):
        for ext in self:
            limit = ext.contract_id.value * ext.contract_id.company_id.tms_contract_extension_limit / 100.0
            ext.approval_role = "oh" if ext.rate_ids or ext.added_value > limit + 0.005 else "bm"

    @api.constrains("added_value", "new_date_end", "rate_ids")
    def _check_content(self):
        for ext in self:
            if ext.added_value < 0:
                raise ValidationError(_("An extension adds value; it never reduces it."))

    def action_submit(self):
        for ext in self:
            if ext.state != "draft":
                continue
            if not (ext.added_value or ext.new_date_end or ext.rate_ids):
                raise UserError(_("An extension adds value, a new end date or new rates."))
            if not ext.file:
                raise UserError(_("Attach the customer's amendment (BR-CON-13, AS-12)."))
            if ext.rate_ids and not ext.rates_effective_date:
                raise UserError(_("New rates need their effective date (BR-CON-16)."))
            ext.state = "to_approve"
            group = "tms_core.group_operations_head" if ext.approval_role == "oh" else "tms_core.group_branch_manager"
            users = users_in_group(self.env, group, ext.company_id) - ext.requested_by_id
            schedule(ext.contract_id, users, _("Extension %s to approve", ext.name))
        return True

    def action_approve(self):
        """On approval an Exhausted or Expired contract returns to Active; held invoices are posted."""
        for ext in self:
            if ext.state != "to_approve":
                raise UserError(_("Submit the extension first."))
            group = "tms_core.group_operations_head" if ext.approval_role == "oh" else "tms_core.group_branch_manager"
            if not self.env.user.has_group(group):
                raise UserError(_("This extension needs the %s's approval (BR-CON-13).",
                                  dict(self._fields["approval_role"].selection)[ext.approval_role]))
            if self.env.user == ext.requested_by_id:
                raise UserError(_("You cannot approve an extension you requested."))
            contract = ext.contract_id.sudo()
            contract._tms_lock()
            vals = {}
            if ext.new_date_end and ext.new_date_end > contract.date_end:
                vals["date_end"] = ext.new_date_end
                vals["expiry_alert_sent"] = False
            if vals:
                contract.with_context(tms_contract_extension=True).write(vals)
            ext.sudo().write({"state": "approved", "approved_by_id": self.env.user.id,
                              "approved_on": fields.Datetime.now()})
            ext._tms_absorb_excess()
            contract.activity_ids.filtered(lambda a: ext.name in (a.summary or "")).action_feedback(
                feedback=_("Approved"))
            contract.message_post(body=_("Extension %(e)s approved: +%(v)s, end %(d)s", e=ext.name,
                                         v=ext.added_value, d=contract.date_end))
            contract._tms_update_state()
            ext._tms_post_held_invoices()
            held = contract.item_ids.filtered(lambda i: i.tms_release_state == "draft" and i.tms_shortfall > 0.005)
            if held:
                schedule(contract, contract.user_id, _("Extension approved: held items can be released"))
            contract._tms_after_change()
        return True

    def action_refuse(self):
        for ext in self:
            if ext.state != "to_approve":
                continue
            if not (self.env.user.has_group("tms_core.group_branch_manager")
                    or self.env.user.has_group("tms_core.group_operations_head")):
                raise UserError(_("A Branch Manager or the Operations Head refuses extensions."))
            ext.state = "refused"
        return True

    def _tms_absorb_excess(self):
        """BR-CON-18: an approved extension consumes open extension-awaited excess first, as a dated line."""
        for ext in self:
            available = ext.added_value
            contract = ext.contract_id.sudo()
            excesses = contract.excess_ids.filtered(
                lambda e: e.state == "approved" and e.cover == "extension" and e.open_amount > 0.005
            ).sorted(lambda e: (e.approved_on or fields.Datetime.now(), e.id))
            for excess in excesses:
                if available <= 0.005:
                    break
                take = min(available, excess.open_amount)
                excess.absorbed_amount += take
                available -= take
                ext.absorbed_excess += take
                self.env["tms.contract.reservation"].sudo().create({
                    "contract_id": contract.id, "sale_line_id": excess.sale_line_id.id, "event": "excess_absorbed",
                    "amount": 0.0, "source": "%s,%s" % (ext._name, ext.id),
                    "note": _("Excess %(x)s: %(a).2f absorbed by extension %(e)s", x=excess.name, a=take, e=ext.name),
                })
                if excess.open_amount <= 0.005:
                    excess.state = "closed"

    def _tms_post_held_invoices(self):
        """UC-37: once the extension is approved, the held invoices post."""
        for ext in self:
            holds = self.env["tms.contract.hold"].sudo().search([
                ("contract_ref", "=", ext.contract_id.id), ("kind", "=", "invoice"), ("state", "=", "open")])
            moves = self.env["account.move"].sudo().browse(set(holds.mapped("res_id"))).exists().filtered(
                lambda m: m.state == "draft")
            for move in moves:
                try:
                    with self.env.cr.savepoint():
                        move.action_post()
                except UserError as err:
                    _logger.info("Held invoice %s still waits: %s", move.id, err)


class TmsContractExcess(models.Model):
    """BR-CON-18: an amount over the contract value, approved by the Contract Excess Approver group."""

    _name = "tms.contract.excess"
    _description = "Contract excess approval"
    _inherit = ["mail.thread", "mail.activity.mixin", "tms.reason.mixin"]
    _order = "id desc"

    name = fields.Char(default="/", readonly=True, copy=False)
    contract_id = fields.Many2one("tms.contract", required=True, ondelete="restrict", index=True)
    company_id = fields.Many2one(related="contract_id.company_id", store=True)
    currency_id = fields.Many2one(related="contract_id.currency_id")
    amount = fields.Monetary(required=True, tracking=True)
    reason = fields.Text(required=True)
    sale_line_id = fields.Many2one("sale.order.line", string="Item covered",
                                   domain="[('tms_contract_id', '=', contract_id), ('tms_is_movement', '=', True)]")
    move_id = fields.Many2one("account.move", string="Invoice covered", domain=[("move_type", "=", "out_invoice")])
    cover = fields.Selection([("extension", "Extension awaited"), ("risk", "At Premier's risk")], required=True,
                             default="extension", tracking=True)
    expected_cover_date = fields.Date(tracking=True)
    state = fields.Selection([("draft", "Draft"), ("requested", "Requested"), ("approved", "Approved"),
                              ("refused", "Refused"), ("closed", "Closed")], default="draft", tracking=True,
                             copy=False)
    requested_by_id = fields.Many2one("res.users", default=lambda s: s.env.user, readonly=True)
    approved_by_id = fields.Many2one("res.users", readonly=True, copy=False)
    approved_on = fields.Datetime(readonly=True, copy=False)
    absorbed_amount = fields.Monetary(readonly=True, copy=False)
    lapsed_amount = fields.Monetary(readonly=True, copy=False)
    open_amount = fields.Monetary(compute="_compute_open", store=True)
    needs_director = fields.Boolean(compute="_compute_needs_director")
    cover_alert_sent = fields.Boolean(readonly=True, copy=False)

    @api.depends("state", "amount", "absorbed_amount", "lapsed_amount")
    def _compute_open(self):
        for excess in self:
            excess.open_amount = max(excess.amount - excess.absorbed_amount - excess.lapsed_amount, 0.0) \
                if excess.state == "approved" else 0.0

    @api.depends("amount", "contract_id.value", "contract_id.company_id.tms_contract_excess_ceiling")
    def _compute_needs_director(self):
        """OP-20: above the excess ceiling per contract a director, not the CFO, approves."""
        for excess in self:
            contract = excess.contract_id
            ceiling = contract.value * contract.company_id.tms_contract_excess_ceiling / 100.0
            # Premier's open exposure: approved excess not yet absorbed by an extension or lapsed
            others = contract.sudo().excess_ids.filtered(lambda e: e.state == "approved" and e != excess)
            total = sum(others.mapped("open_amount")) + excess.amount
            excess.needs_director = total > ceiling + 0.005

    @api.constrains("sale_line_id", "move_id", "amount")
    def _check_target(self):
        for excess in self:
            if bool(excess.sale_line_id) == bool(excess.move_id):
                raise ValidationError(_("An excess covers one item or one invoice (BR-CON-18)."))
            if excess.amount <= 0:
                raise ValidationError(_("The excess amount must be positive."))
            if excess.sale_line_id and excess.sale_line_id.tms_contract_id != excess.contract_id:
                raise ValidationError(_("The item is not on this contract."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "/") == "/":
                vals["name"] = self.env["ir.sequence"].next_by_code("tms.contract.excess") or "/"
        return super().create(vals_list)

    def write(self, vals):
        if {"amount", "sale_line_id", "move_id", "contract_id"} & set(vals):
            if any(e.state not in ("draft", "requested") for e in self):
                raise UserError(_("An approved excess never changes (BR-AUD-04)."))
        return super().write(vals)

    def unlink(self):
        if any(e.state not in ("draft",) for e in self):
            raise UserError(_("Requested excess is refused, never deleted (BR-AUD-04)."))
        return super().unlink()

    def action_request(self):
        """BR-NTF-14: the Finance Head hears of every excess request."""
        for excess in self:
            if excess.state != "draft":
                continue
            excess.state = "requested"
            group = "tms_core.group_director" if excess.needs_director else "tms_core.group_finance_head"
            users = users_in_group(self.env, group, excess.company_id) - excess.requested_by_id
            if excess.needs_director:
                users |= users_in_group(self.env, "tms_core.group_finance_head", excess.company_id) \
                    - excess.requested_by_id
            schedule(excess, users, _("Contract excess %(a).2f to approve on %(c)s", a=excess.amount,
                                      c=excess.contract_id.name), excess.reason)
        return True

    def action_approve(self):
        for excess in self:
            if excess.state != "requested":
                raise UserError(_("Only requested excess is approved."))
            user = self.env.user
            if not user.has_group("tms_core.group_contract_excess_approver"):
                raise UserError(_("Only the Contract Excess Approver group (the Finance Head or a director) approves "
                                  "an excess over the contract value (BR-CON-18, RL-10)."))
            if excess.needs_director and not user.has_group("tms_core.group_director"):
                raise UserError(_("This excess passes the excess ceiling: a director approves it (OP-20)."))
            if user == excess.requested_by_id:
                raise UserError(_("You cannot approve an excess you requested (BR-CON-18)."))
            excess.write({"state": "approved", "approved_by_id": user.id, "approved_on": fields.Datetime.now()})
            excess.activity_ids.action_feedback(feedback=_("Approved"))
            self.env["tms.override.log"].log(
                "contract_excess", excess.contract_id,
                _("Excess %(x)s of %(a).2f on %(t)s (%(c)s)", x=excess.name, a=excess.amount,
                  t=excess.sale_line_id.display_name or excess.move_id.display_name,
                  c=dict(self._fields["cover"].selection)[excess.cover]),
                reason=excess.reason, amount=excess.amount)
        return True

    def action_refuse(self):
        for excess in self:
            if excess.state != "requested":
                continue
            if not self.env.user.has_group("tms_core.group_contract_excess_approver"):
                raise UserError(_("Only the Contract Excess Approver group refuses an excess."))
            excess.state = "refused"
            excess.activity_ids.action_feedback(feedback=_("Refused"))
        return True

    def _tms_lapse(self):
        """BR-CON-18: unused excess lapses when its item or invoice closes."""
        for excess in self.filtered(lambda e: e.state == "approved"):
            excess.write({"lapsed_amount": excess.lapsed_amount + excess.open_amount, "state": "closed"})
        self.filtered(lambda e: e.state in ("draft", "requested")).write({"state": "refused"})

    @api.model
    def _cron_cover_overdue(self):
        """BR-NTF-18: the expected-cover date passes with no extension."""
        today = fields.Date.context_today(self)
        for excess in self.sudo().search([("state", "=", "approved"), ("cover", "=", "extension"),
                                          ("expected_cover_date", "<", today), ("cover_alert_sent", "=", False)]):
            contract = excess.contract_id
            users = contract.user_id | users_in_group(self.env, "tms_core.group_finance_head", contract.company_id)
            summary = _("Excess %(x)s on %(c)s: no extension by %(d)s", x=excess.name, c=contract.name,
                        d=excess.expected_cover_date)
            schedule(excess, users, summary)
            contract._tms_email(users, summary)
            excess.cover_alert_sent = True


class TmsContractReservation(models.Model):
    """BR-CON-20: every change to an item's reservation is a dated line; R_j is the sum of its lines."""

    _name = "tms.contract.reservation"
    _description = "Contract reservation line"
    _order = "date desc, id desc"

    contract_id = fields.Many2one("tms.contract", required=True, ondelete="restrict", index=True, readonly=True)
    company_id = fields.Many2one(related="contract_id.company_id", store=True)
    currency_id = fields.Many2one(related="contract_id.currency_id")
    sale_line_id = fields.Many2one("sale.order.line", string="Item", index=True, readonly=True)
    item_no = fields.Integer(related="sale_line_id.tms_item_no", string="Item no.")
    consignment_id = fields.Many2one("tms.consignment", string="LR", readonly=True)
    trip_id = fields.Many2one("tms.order", string="Trip", readonly=True)
    date = fields.Date(default=fields.Date.context_today, required=True, readonly=True, index=True)
    event = fields.Selection(RESERVATION_EVENTS, required=True, readonly=True)
    amount = fields.Monetary(readonly=True)
    source = fields.Reference(selection="_selection_source", string="Source document", readonly=True)
    note = fields.Char(readonly=True)
    user_id = fields.Many2one("res.users", default=lambda s: s.env.user, readonly=True)

    @api.model
    def _selection_source(self):
        return [("sale.order.line", "Item"), ("account.move", "Journal entry"), ("tms.trip.charge", "Charge"),
                ("tms.trip.expense", "Expense"), ("tms.contract.extension", "Extension"),
                ("tms.contract.escalation", "Escalation"), ("tms.estimate.amendment", "Estimate amendment"),
                ("tms.consignment.amendment", "LR amendment"), ("tms.consignment", "LR"),
                ("tms.contract.excess", "Excess")]

    def write(self, vals):
        raise UserError(_("Reservation lines are a ledger: they are never changed (BR-CON-20, BR-AUD-04)."))

    def unlink(self):
        raise UserError(_("Reservation lines are a ledger: they are never deleted (BR-CON-20, BR-AUD-04)."))


class TmsContractEscalation(models.Model):
    """BR-CON-22: each diesel revision is a dated rate change for items released after it."""

    _name = "tms.contract.escalation"
    _description = "Contract diesel escalation revision"
    _inherit = ["mail.thread"]
    _order = "date desc, id desc"

    contract_id = fields.Many2one("tms.contract", required=True, ondelete="restrict", index=True,
                                  domain=[("escalation_clause", "=", True)])
    company_id = fields.Many2one(related="contract_id.company_id", store=True)
    currency_id = fields.Many2one(related="contract_id.currency_id")
    date = fields.Date(string="Effective from", required=True, default=fields.Date.context_today)
    diesel_price = fields.Float(required=True)
    base_price = fields.Float(related="contract_id.diesel_base_price")
    change = fields.Float(compute="_compute_change", string="Change per litre")
    change_pct = fields.Float(compute="_compute_change", string="Change %")
    within_band = fields.Boolean(compute="_compute_change")
    line_ids = fields.One2many("tms.contract.escalation.line", "escalation_id", string="Rate lines revised")
    state = fields.Selection([("draft", "Draft"), ("applied", "Applied")], default="draft", tracking=True)
    applied_by_id = fields.Many2one("res.users", readonly=True)
    applied_on = fields.Datetime(readonly=True)

    @api.depends("diesel_price", "base_price", "contract_id.escalation_band")
    def _compute_change(self):
        for rev in self:
            rev.change = rev.diesel_price - rev.base_price
            rev.change_pct = rev.change / rev.base_price * 100.0 if rev.base_price else 0.0
            rev.within_band = abs(rev.change_pct) <= rev.contract_id.escalation_band

    def action_compute(self):
        for rev in self:
            if rev.state != "draft":
                continue
            rev.line_ids.unlink()
            contract = rev.contract_id
            lines = []
            seen = set()
            base_rates = contract.rate_ids.filtered(lambda r: not r.escalation_id and r._tms_effective())
            for rate in base_rates.sorted(lambda r: (r.date_from or rev.date, r.id), reverse=True):
                if rate.element_key in seen or (rate.date_from and rate.date_from > rev.date):
                    continue
                seen.add(rate.element_key)
                if not rate.escalation_per_rupee:
                    continue
                adjustment = 0.0 if rev.within_band else rate.escalation_per_rupee * rev.change
                lines.append((0, 0, {"rate_id": rate.id, "old_rate": rate.rate, "adjustment": adjustment,
                                     "new_rate": rate.rate + adjustment}))
            rev.line_ids = lines
        return True

    def action_apply(self):
        for rev in self:
            if rev.state != "draft":
                continue
            if not self.env.user.has_group("tms_core.group_operations_head"):
                raise UserError(_("The Operations Head applies escalation revisions (BR-CON-16)."))
            if not rev.contract_id.escalation_clause:
                raise UserError(_("The contract carries no escalation clause."))
            if not rev.line_ids:
                rev.action_compute()
            Rate = self.env["tms.contract.rate"].sudo()
            for line in rev.line_ids:
                base = line.rate_id
                Rate.create({"contract_id": rev.contract_id.id, "kind": base.kind,
                             "vehicle_class_id": base.vehicle_class_id.id, "product_id": base.product_id.id,
                             "name": base.name, "basis": base.basis, "rate": line.new_rate, "date_from": rev.date,
                             "escalation_id": rev.id, "escalation_per_rupee": base.escalation_per_rupee,
                             "sequence": base.sequence})
            rev.write({"state": "applied", "applied_by_id": self.env.user.id, "applied_on": fields.Datetime.now()})
            if rev.contract_id.escalation_revises_released:
                items = rev.contract_id.sudo().item_ids.filtered(
                    lambda i: i.tms_release_state == "released" and i.tms_released_on
                    and i.tms_released_on < rev.date)
                for item in items:
                    item._tms_compute_supplementary(rev.date)
                items._tms_sync_reservation("escalation", source=rev)
                rev.contract_id._tms_after_change()
        return True


class TmsContractEscalationLine(models.Model):
    _name = "tms.contract.escalation.line"
    _description = "Escalation rate line"

    escalation_id = fields.Many2one("tms.contract.escalation", required=True, ondelete="cascade")
    currency_id = fields.Many2one(related="escalation_id.currency_id")
    rate_id = fields.Many2one("tms.contract.rate", string="Rate line", required=True)
    name = fields.Char(related="rate_id.name")
    old_rate = fields.Monetary(string="Base rate")
    adjustment = fields.Monetary()
    new_rate = fields.Monetary()


class TmsContractHold(models.Model):
    """A refused release or a held invoice (BR-CON-10, BR-CON-12, KPI-21).

    Holds are written outside the refused transaction, so they keep plain integer references: a foreign key
    would wait on the rows the refused transaction has locked."""

    _name = "tms.contract.hold"
    _description = "Contract hold"
    _order = "id desc"

    contract_ref = fields.Integer(index=True, readonly=True)
    kind = fields.Selection([("release", "Movement release"), ("invoice", "Invoice")], required=True, readonly=True)
    res_model = fields.Char(readonly=True)
    res_id = fields.Integer(index=True, readonly=True)
    amount = fields.Float(readonly=True)
    shortfall = fields.Float(readonly=True)
    state = fields.Selection([("open", "Open"), ("cleared", "Cleared")], default="open", readonly=True)
    user_id = fields.Many2one("res.users", default=lambda s: s.env.user, readonly=True, index=False)

    @api.model
    def _tms_record(self, kind, record, contract, amount, shortfall, detached=False):
        vals = {"contract_ref": contract.id, "kind": kind, "res_model": record._name, "res_id": record.id,
                "amount": amount, "shortfall": shortfall}
        if not detached:
            existing = self.sudo().search([("res_model", "=", record._name), ("res_id", "=", record.id),
                                           ("state", "=", "open")])
            if existing:
                return existing
            return self.sudo().create(vals)
        try:
            with self.env.registry.cursor() as cr:
                env = api.Environment(cr, SUPERUSER_ID, {})
                if not env[record._name].browse(record.id).exists():
                    return self.browse()  # not committed yet: nothing to point at
                vals["user_id"] = env["res.users"].browse(self.env.uid).exists().id
                Hold = env["tms.contract.hold"]
                if not Hold.search_count([("res_model", "=", record._name), ("res_id", "=", record.id),
                                          ("state", "=", "open")]):
                    Hold.create(vals)
        except Exception:  # noqa: BLE001 - recording the hold must never mask the refusal
            _logger.exception("Could not record the contract hold on %s,%s", record._name, record.id)
        return self.browse()

    @api.model
    def _tms_clear(self, records):
        if records:
            self.sudo().search([("res_model", "=", records._name), ("res_id", "in", records.ids),
                                ("state", "=", "open")]).write({"state": "cleared"})
