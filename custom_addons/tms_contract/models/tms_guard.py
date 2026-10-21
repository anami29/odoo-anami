"""Approval and status fields of this module change only through their actions (BR-AUD-06)."""
from odoo import api, models


class TmsRpcGuard(models.AbstractModel):
    _inherit = "tms.rpc.guard"

    @api.model
    def _tms_guarded_fields(self):
        res = super()._tms_guarded_fields()
        for model, names in {
            "tms.contract": {"state", "approved_by_id", "approved_on", "closed_on", "excess_approved"},
            "tms.contract.extension": {"state", "approved_by_id", "approved_on", "requested_by_id"},
            "tms.contract.excess": {"state", "approved_by_id", "approved_on", "requested_by_id"},
            "tms.contract.escalation": {"state", "applied_by_id"},
            "tms.contract.hold": {"state"},
            "sale.order": {"tms_contract_approved_by_id"},
            "sale.order.line": {"tms_markup_approved_by_id", "tms_released_by_id"},
        }.items():
            res.setdefault(model, set()).update(names)
        return res

    @api.model
    def _tms_create_roles(self):
        res = super()._tms_create_roles()
        for model, roles in {
            "tms.contract": ["sales_executive"],
        }.items():
            res[model] = ["tms_core.group_%s" % role for role in roles]
        return res
