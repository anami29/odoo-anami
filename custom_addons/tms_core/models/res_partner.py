from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .tools import inr, notify_detached, users_in_group

FREIGHT_BASIS = [("paid", "Paid"), ("topay", "To-Pay"), ("tbb", "To-Be-Billed")]
BILLING_CYCLES = [("lr", "Per LR"), ("weekly", "Weekly"), ("fortnightly", "Fortnightly"), ("monthly", "Monthly")]
CYCLE_DAYS = {"lr": 0, "weekly": 7, "fortnightly": 15, "monthly": 30}


class ResPartner(models.Model):
    _inherit = "res.partner"

    # BR-MD-01 customer master
    tms_freight_basis = fields.Selection(FREIGHT_BASIS, string="Default freight basis")
    tms_billing_cycle = fields.Selection(BILLING_CYCLES, string="Billing cycle")
    tms_credit_limit = fields.Monetary(string="TMS credit limit", currency_field="currency_id")
    tms_unregistered = fields.Boolean(string="Unregistered for GST")
    tms_credit_from_submission = fields.Boolean(
        string="Credit days count from submission", help="BR-INV-13"
    )
    # BR-MD-16
    tms_free_hours_loading = fields.Float(string="Free hours at loading", default=0.0)
    tms_free_hours_unloading = fields.Float(string="Free hours at unloading", default=0.0)
    tms_detention_rate = fields.Monetary(string="Detention rate per day", currency_field="currency_id")
    # BR-MD-10 brokers and transporters
    tms_is_transporter = fields.Boolean(string="Transporter / broker")
    tms_gta_status = fields.Selection(
        [("non_gta", "Non-GTA truck owner"), ("gta_rcm", "GTA (reverse charge)"), ("gta_fcm", "GTA charging forward")],
        string="GTA status",
    )
    tms_advance_percent = fields.Float(string="Default advance %", default=0.0)
    tms_tds_declaration_ids = fields.One2many("tms.tds.declaration", "partner_id", string="TDS declarations")
    tms_exposure = fields.Monetary(compute="_compute_tms_exposure", currency_field="currency_id", string="Credit exposure")
    tms_has_overdue = fields.Boolean(compute="_compute_tms_exposure", string="Overdue invoice")

    def tms_has_valid_tds_declaration(self, date=None):
        self.ensure_one()
        date = date or fields.Date.context_today(self)
        return bool(
            self.commercial_partner_id.tms_tds_declaration_ids.filtered(
                lambda d: d.date_from <= date <= d.date_to and d.vehicle_count <= 10
            )
        )

    # ------------------------------------------------------------------
    # BR-CRD-01..04 credit control
    # ------------------------------------------------------------------
    def _tms_exposure_parts(self):
        self.ensure_one()
        partner = self.commercial_partner_id
        moves = self.env["account.move"].sudo().search(
            [
                ("partner_id", "child_of", partner.id),
                ("move_type", "in", ("out_invoice", "out_refund")),
                ("state", "=", "posted"),
                ("payment_state", "not in", ("paid", "in_payment", "reversed")),
            ]
        )
        unpaid = sum(m.amount_residual_signed for m in moves)
        lrs = self.env["tms.consignment"].sudo().search(
            [
                ("billing_partner_id", "child_of", partner.id),
                ("state", "in", ("delivered", "not_delivered")),
                ("freight_basis", "!=", "paid"),
            ]
        )
        uninvoiced = sum(lrs.mapped("freight_uninvoiced"))
        reservations = self._tms_open_reservations()
        overdue_limit = self.env.company.tms_overdue_limit_days
        cutoff = fields.Date.context_today(self) - timedelta(days=overdue_limit)
        overdue = any(
            m.invoice_date_due and m.invoice_date_due < cutoff and m.move_type == "out_invoice" for m in moves
        )
        return unpaid, uninvoiced, reservations, overdue

    def _tms_open_reservations(self):
        """Hook: tms_contract adds open contract reservations."""
        return 0.0

    def _compute_tms_exposure(self):
        for partner in self:
            if not partner.id:
                partner.tms_exposure = 0.0
                partner.tms_has_overdue = False
                continue
            unpaid, uninvoiced, reservations, overdue = partner._tms_exposure_parts()
            partner.tms_exposure = unpaid + uninvoiced + reservations
            partner.tms_has_overdue = overdue

    def tms_check_credit(self, amount, document, what):
        """Refuse when exposure + amount exceeds the limit or an invoice is overdue (BR-CRD-02).

        A logged override (context tms_credit_override_reason) by the Branch Manager or
        Finance Head passes (BR-CRD-03)."""
        self.ensure_one()
        partner = self.commercial_partner_id
        if not partner.tms_credit_limit and not partner.tms_has_overdue:
            return True
        unpaid, uninvoiced, reservations, overdue = partner._tms_exposure_parts()
        exposure = unpaid + uninvoiced + reservations
        blocked_limit = partner.tms_credit_limit and exposure + amount > partner.tms_credit_limit
        if not (blocked_limit or overdue):
            return True
        reason = self.env.context.get("tms_credit_override_reason")
        user = self.env.user
        if reason and (
            user.has_group("tms_core.group_branch_manager") or user.has_group("tms_core.group_finance_head")
        ):
            self.env["tms.override.log"].log(
                "credit", document, _("Credit block overridden on %s", what), reason=reason, amount=amount
            )
            return True
        msg = _(
            "%(what)s is refused by credit control for %(partner)s: exposure %(exp)s + %(amt)s "
            "against a limit of %(lim)s%(overdue)s. A Branch Manager or the Finance Head may override with a reason.",
            what=what,
            partner=partner.display_name,
            exp=inr(exposure),
            amt=inr(amount),
            lim=inr(partner.tms_credit_limit),
            overdue=_("; an invoice is overdue beyond the limit") if overdue else "",
        )
        # BR-NTF-17: the user blocked and the branch accountants, outside the rolled-back transaction
        if document.id and hasattr(document, "activity_schedule"):
            company = document.company_id if "company_id" in document._fields else None
            accountants = users_in_group(self.env, "tms_core.group_branch_accountant", company)
            notify_detached(
                self.env, document._name, document.id, (accountants | user).ids, _("Blocked by credit"), msg
            )
        raise UserError(msg)


class TmsTdsDeclaration(models.Model):
    """BR-MD-10, BR-HIR-10: transporter declaration (owns ten or fewer goods carriages)."""

    _name = "tms.tds.declaration"
    _description = "Transporter TDS declaration"
    _order = "date_from desc"

    partner_id = fields.Many2one("res.partner", required=True, ondelete="cascade")
    date_from = fields.Date(required=True, string="Year from")
    date_to = fields.Date(required=True, string="Year to")
    vehicle_count = fields.Integer(required=True)
    reference = fields.Char()
    attachment = fields.Binary(string="File", attachment=True)
    attachment_name = fields.Char()

    @api.constrains("vehicle_count")
    def _check_count(self):
        for rec in self:
            if rec.vehicle_count < 0:
                raise UserError(_("The vehicle count cannot be negative."))
