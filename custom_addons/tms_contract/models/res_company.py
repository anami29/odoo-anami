from odoo import _, fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    # BR-CFG-15
    tms_contract_sequence_id = fields.Many2one("ir.sequence", string="Contract series", copy=False)
    tms_contract_util_alert = fields.Float(string="Contract utilisation alert %", default=80.0)
    tms_contract_expiry_days = fields.Integer(string="Contract expiry alert (days)", default=15)
    tms_contract_extension_limit = fields.Float(
        string="Extension limit %", default=20.0,
        help="An extension adding more than this share of the contract value is approved by the Operations Head.")
    tms_contract_excess_ceiling = fields.Float(
        string="Excess ceiling %", default=10.0,
        help="Approved excess above this share of the contract value needs a director (OP-20).")
    tms_contract_runon_days = fields.Integer(
        string="Run-on days", default=30,
        help="Items released while the contract was Active book LRs up to this many days after its end date.")

    def _tms_contract_sequence(self):
        """BR-CON-01: each branch numbers its contracts from its own series."""
        self.ensure_one()
        if not self.tms_contract_sequence_id:
            seq = self.env["ir.sequence"].sudo().create({
                "name": _("Contracts %s", self.name),
                "code": "tms.contract.%s" % self.id,
                "prefix": "CON/%(year)s/",
                "padding": 4,
                "company_id": self.id,
                "use_date_range": True,
            })
            self.sudo().tms_contract_sequence_id = seq
        return self.tms_contract_sequence_id


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    tms_contract_sequence_id = fields.Many2one(related="company_id.tms_contract_sequence_id", readonly=False)
    tms_contract_util_alert = fields.Float(related="company_id.tms_contract_util_alert", readonly=False)
    tms_contract_expiry_days = fields.Integer(related="company_id.tms_contract_expiry_days", readonly=False)
    tms_contract_extension_limit = fields.Float(related="company_id.tms_contract_extension_limit", readonly=False)
    tms_contract_excess_ceiling = fields.Float(related="company_id.tms_contract_excess_ceiling", readonly=False)
    tms_contract_runon_days = fields.Integer(related="company_id.tms_contract_runon_days", readonly=False)
