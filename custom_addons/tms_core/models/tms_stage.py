from odoo import _, api, fields, models
from odoo.exceptions import UserError

STAGE_CODES = [
    ("draft", "Draft"),
    ("confirmed", "Confirmed"),
    ("started", "Started"),
    ("in_transit", "In Transit"),
    ("delivered", "Delivered"),
    ("done", "Done"),
    ("cancel", "Cancelled"),
]


class TmsStage(models.Model):
    """BR-EXE-01, BR-EXE-02: base stages carry a technical code; seeded coded stages are locked."""

    _inherit = "tms.stage"

    tms_code = fields.Selection(STAGE_CODES, string="Technical code", readonly=True, copy=False)

    _sql_constraints = [
        ("tms_code_uniq", "unique(tms_code, stage_type)", "Each technical code is held by one stage."),
    ]

    def write(self, vals):
        if "tms_code" in vals and not self.env.context.get("tms_seed"):
            raise UserError(_("Technical stage codes cannot be changed (BR-EXE-02)."))
        if "stage_type" in vals and self.filtered("tms_code"):
            raise UserError(_("Coded stages keep their type (BR-EXE-02)."))
        return super().write(vals)

    def unlink(self):
        if self.filtered("tms_code"):
            raise UserError(_("Coded stages cannot be deleted (BR-EXE-02)."))
        return super().unlink()

    @api.model
    def _tms_seed_codes(self):
        """Called from data: code the base module's seeded stages and create the three TMS-only stages."""
        mapping = {
            "tms.tms_stage_order_draft": "draft",
            "tms.tms_stage_order_confirmed": "confirmed",
            "tms.tms_stage_order_completed": "done",
            "tms.tms_stage_order_cancelled": "cancel",
        }
        seeded = self.with_context(tms_seed=True)
        for xmlid, code in mapping.items():
            stage = self.env.ref(xmlid, raise_if_not_found=False)
            if stage and not stage.tms_code:
                seeded.browse(stage.id).write({"tms_code": code})
        extra = [("started", _("Started"), 21), ("in_transit", _("In Transit"), 22), ("delivered", _("Delivered"), 23)]
        for code, name, seq in extra:
            if not self.search([("tms_code", "=", code), ("stage_type", "=", "order")]):
                stage = seeded.create({"name": name, "sequence": seq, "stage_type": "order", "is_default": True,
                                       "custom_color": "#ECF0F1"})
                seeded.browse(stage.id).write({"tms_code": code})
        done = self.env.ref("tms.tms_stage_order_completed", raise_if_not_found=False)
        if done:
            done.name = _("Done")

    def get_stage(self, code):
        return self.search([("tms_code", "=", code), ("stage_type", "=", "order")], limit=1)
