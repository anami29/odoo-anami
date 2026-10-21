from odoo import _, api, fields, models
from odoo.exceptions import UserError

OVERRIDE_KINDS = [
    ("payload", "Payload override"),
    ("tolerance", "Hire tolerance override"),
    ("typed_cost", "Typed cost"),
    ("typed_rate", "Typed rate LR"),
    ("charged_weight", "Charged weight override"),
    ("cargo_regular", "Cargo Type kept Regular"),
    ("overhead", "Overhead marking"),
    ("mode_unlock", "Mode unlock"),
    ("enforcement", "Compliance enforcement switch"),
    ("contract_excess", "Contract excess"),
    ("partial_posting", "Partial posting of a held contract invoice"),
    ("noncontract_charge", "Non-incidental charge billed on a contract"),
    ("contract_customer_rate", "Typed-rate LR or ordinary order for a contract customer"),
    ("self_approved_charge", "Self-approved charge"),
    ("credit", "Credit-limit override"),
    ("equipment", "ODC equipment substitution"),
    ("waiver", "Waiver"),
    ("claim", "Claim cap, notice or owner's-risk acceptance"),
    ("other", "Other"),
]


class TmsOverrideLog(models.Model):
    """BR-AUD-06: every override logs who, when and why. Read-only for every role (BR-AUD-03)."""

    _name = "tms.override.log"
    _description = "TMS override log"
    _order = "create_date desc, id desc"
    _rec_name = "summary"

    kind = fields.Selection(OVERRIDE_KINDS, required=True, readonly=True)
    summary = fields.Char(required=True, readonly=True)
    reason = fields.Text(readonly=True)
    user_id = fields.Many2one("res.users", default=lambda s: s.env.user, readonly=True, required=True)
    company_id = fields.Many2one("res.company", string="Branch", default=lambda s: s.env.company, readonly=True)
    res_model = fields.Char(readonly=True)
    res_id = fields.Many2oneReference(model_field="res_model", readonly=True)
    amount = fields.Float(readonly=True)

    @api.model
    def log(self, kind, record, summary, reason=None, amount=0.0):
        company = record.company_id if record and "company_id" in record._fields else self.env.company
        if record and record._name == "res.company":
            company = record
        vals = {
            "kind": kind,
            "summary": summary,
            "reason": reason,
            "res_model": record._name if record else False,
            "res_id": record.id if record else False,
            "amount": amount,
            "company_id": (company[:1].id or self.env.company.id),
        }
        rec = self.sudo().create(vals)
        if record and hasattr(record, "message_post"):
            record.sudo().message_post(body=_("Override (%(kind)s): %(summary)s. Reason: %(reason)s",
                                       kind=dict(OVERRIDE_KINDS)[kind], summary=summary, reason=reason or "-"))
        return rec

    def write(self, vals):
        raise UserError(_("Override log entries are read-only."))

    def unlink(self):
        raise UserError(_("Override log entries cannot be deleted."))

    def action_open_record(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": self.res_model,
            "res_id": self.res_id,
            "view_mode": "form",
        }


class TmsReasonMixin(models.AbstractModel):
    """Buttons with context ``tms_reason_method`` open the reason wizard for that method."""

    _name = "tms.reason.mixin"
    _description = "Reason button helper"

    def action_open_reason(self):
        method = self.env.context.get("tms_reason_method")
        if not method or not method.startswith("action_"):
            raise UserError(_("No action to run."))
        title = self.env.context.get("tms_reason_title") or _("Reason")
        return self.env["tms.reason.wizard"].open_for(self, method, title,
                                                      show_waive=self.env.context.get("tms_show_waive", False))
