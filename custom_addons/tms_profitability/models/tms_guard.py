"""Approval and status fields of this module change only through their actions (BR-AUD-06)."""
from odoo import api, models


class TmsRpcGuard(models.AbstractModel):
    _inherit = "tms.rpc.guard"

    @api.model
    def _tms_guarded_fields(self):
        res = super()._tms_guarded_fields()
        for model, names in {
            "tms.pnl.close": {"state", "closed_by_id", "closed_on", "run_by_id"},
            "tms.prepaid.schedule": {"state"},
            "tms.prepaid.schedule.line": {"state"},
        }.items():
            res.setdefault(model, set()).update(names)
        return res

    @api.model
    def _tms_create_roles(self):
        res = super()._tms_create_roles()
        for model, roles in {
            "tms.std.rate": ["finance_head"],
        }.items():
            res[model] = ["tms_core.group_%s" % role for role in roles]
        return res
