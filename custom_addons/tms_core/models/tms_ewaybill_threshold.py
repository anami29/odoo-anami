"""BR-EWB-03: the e-way bill threshold (50,000 default) with per-state intra-state overrides."""
from odoo import api, fields, models


class TmsEwaybillStateThreshold(models.Model):
    _name = "tms.ewaybill.state.threshold"
    _description = "Intra-state e-way bill threshold"
    _order = "state_id"

    company_id = fields.Many2one("res.company", required=True, default=lambda s: s.env.company.root_id,
                                 ondelete="cascade")
    state_id = fields.Many2one("res.country.state", required=True, domain="[('country_id.code', '=', 'IN')]")
    threshold = fields.Monetary(required=True, currency_field="currency_id")
    currency_id = fields.Many2one(related="company_id.currency_id")

    _sql_constraints = [("state_uniq", "unique(company_id, state_id)", "One threshold per state.")]


class ResCompany(models.Model):
    _inherit = "res.company"

    tms_ewaybill_state_threshold_ids = fields.One2many("tms.ewaybill.state.threshold", "company_id",
                                                       string="Intra-state thresholds")


class TmsConsignment(models.Model):
    _inherit = "tms.consignment"

    def _tms_ewaybill_threshold(self):
        self.ensure_one()
        company = self.company_id
        origin_state = self.origin_id.state_id
        if origin_state and origin_state == self.destination_id.state_id:
            Threshold = self.env["tms.ewaybill.state.threshold"].sudo()
            override = Threshold.search([("company_id", "in", (company | company.parent_ids).ids),
                                         ("state_id", "=", origin_state.id)], order="company_id desc", limit=1)
            if override:
                return override.threshold
        return company.tms_ewaybill_threshold
