from odoo import _, fields, models


class ResCompany(models.Model):
    """BR-CFG-13: the ODC switch, the piece thresholds and the planned ODC km per day."""

    _inherit = "res.company"

    tms_odc_enabled = fields.Boolean(string="ODC enabled", default=False,
                                     help="Off until Operations confirm the thresholds (DC-25, OP-04).")
    tms_odc_max_width = fields.Float(string="ODC width threshold (m)", default=2.6)
    tms_odc_max_height = fields.Float(string="ODC height threshold (m)", default=3.8)
    tms_odc_max_length = fields.Float(string="ODC length threshold (m)", default=12.0)
    tms_odc_max_weight = fields.Float(string="ODC weight threshold (t)", default=25.0)
    tms_odc_km_per_day = fields.Float(string="Planned ODC km per day", default=80.0)

    def _tms_odc_exceeded(self, length=0.0, width=0.0, height=0.0, weight_t=0.0):
        """Names of the thresholds a piece exceeds (BR-ODC-02); empty when the switch is off."""
        self.ensure_one()
        if not self.tms_odc_enabled:
            return []
        out = []
        for value, limit, label in ((length, self.tms_odc_max_length, _("length")),
                                    (width, self.tms_odc_max_width, _("width")),
                                    (height, self.tms_odc_max_height, _("height")),
                                    (weight_t, self.tms_odc_max_weight, _("weight"))):
            if limit and value and value > limit:
                out.append(label)
        return out


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    tms_odc_enabled = fields.Boolean(related="company_id.tms_odc_enabled", readonly=False)
    tms_odc_max_width = fields.Float(related="company_id.tms_odc_max_width", readonly=False)
    tms_odc_max_height = fields.Float(related="company_id.tms_odc_max_height", readonly=False)
    tms_odc_max_length = fields.Float(related="company_id.tms_odc_max_length", readonly=False)
    tms_odc_max_weight = fields.Float(related="company_id.tms_odc_max_weight", readonly=False)
    tms_odc_km_per_day = fields.Float(related="company_id.tms_odc_km_per_day", readonly=False)
