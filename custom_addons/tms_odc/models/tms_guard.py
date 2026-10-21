"""Approval and status fields of this module change only through their actions (BR-AUD-06)."""
from odoo import api, models


class TmsRpcGuard(models.AbstractModel):
    _inherit = "tms.rpc.guard"

    @api.model
    def _tms_guarded_fields(self):
        res = super()._tms_guarded_fields()
        for model, names in {
            "tms.order": {"survey_approved", "survey_approved_by_id", "survey_approved_on"},
            "tms.trip.equipment": {"approved_by_id", "approved_on"},
        }.items():
            res.setdefault(model, set()).update(names)
        return res
