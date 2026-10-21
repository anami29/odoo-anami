from odoo import _, fields, models
from odoo.exceptions import UserError


class TmsEstimateAmendmentWizard(models.TransientModel):
    """BR-EST-12: open a locked estimate for an amendment, keeping the baseline."""

    _name = "tms.estimate.amendment.wizard"
    _description = "Request estimate amendment"

    estimate_id = fields.Many2one("tms.estimate", required=True)
    reason = fields.Text(required=True)

    def action_open(self):
        self.ensure_one()
        est = self.estimate_id
        if est.state != "locked":
            raise UserError(_("Only locked estimates are amended."))
        if est.amendment_ids.filtered(lambda a: a.state in ("open", "to_approve")):
            raise UserError(_("An amendment is already open on this estimate."))
        amendment = self.env["tms.estimate.amendment"].create({
            "estimate_id": est.id,
            "reason": self.reason,
            "baseline_json": est._tms_snapshot(),
            "baseline_amount": est.amount,
        })
        if not est.baseline_json:
            est.with_context(tms_estimate_unlock=True).write({"baseline_json": est._tms_snapshot(),
                                                               "baseline_amount": est.amount,
                                                               "baseline_cost": est.estimated_cost})
        est.with_context(tms_estimate_unlock=True).write({"state": "amending"})
        est.line_ids.with_context(tms_estimate_unlock=True).write({"locked": False})
        est.message_post(body=_("Amendment opened: %s", self.reason))
        return {"type": "ir.actions.act_window", "res_model": "tms.estimate", "res_id": est.id, "view_mode": "form",
                "context": {"tms_amendment_id": amendment.id}}
