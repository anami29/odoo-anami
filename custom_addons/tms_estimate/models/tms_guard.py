"""Approval and status fields of this module change only through their actions (BR-AUD-06)."""
from odoo import api, models


class TmsRpcGuard(models.AbstractModel):
    _inherit = "tms.rpc.guard"

    @api.model
    def _tms_guarded_fields(self):
        res = super()._tms_guarded_fields()
        for model, names in {
            "tms.estimate": {"state"},
            "tms.estimate.amendment": {"state", "approved_by_id", "requested_by_id"},
            "sale.order.line": {"tms_approval_state", "tms_approved_by_id", "tms_approved_amount"},
            "account.move": {"tms_variance_approved_by_id"},
            "tms.trip.expense": {"variance_approved_by_id"},
        }.items():
            res.setdefault(model, set()).update(names)
        return res
