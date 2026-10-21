from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class TmsConsignment(models.Model):
    _inherit = "tms.consignment"

    odc_suggested = fields.Boolean(compute="_compute_odc_suggested", string="ODC suggested")
    tms_regular_reason = fields.Char(string="Reason to keep Regular", copy=False, tracking=True)
    ewaybill_validity_mismatch = fields.Boolean(compute="_compute_ewaybill_validity_mismatch",
                                                string="E-way bill validity mismatch")

    @api.depends("line_ids.piece_length", "line_ids.piece_width", "line_ids.piece_height",
                 "line_ids.actual_weight", "line_ids.packages", "company_id")
    def _compute_odc_suggested(self):
        for lr in self:
            company = lr.company_id or self.env.company
            lr.odc_suggested = any(line._tms_odc_exceeded(company) for line in lr.line_ids)

    @api.depends("ewaybill_ids.validity_mismatch", "ewaybill_ids.state")
    def _compute_ewaybill_validity_mismatch(self):
        for lr in self:
            lr.ewaybill_validity_mismatch = any(
                b.validity_mismatch for b in lr.ewaybill_ids if b.state != "cancelled")

    @api.constrains("tms_cargo_type")
    def _check_odc_enabled(self):
        for lr in self:
            if lr.tms_cargo_type == "odc" and not lr.company_id.tms_odc_enabled:
                raise ValidationError(_("ODC is switched off for %s (BR-CFG-13).", lr.company_id.name))

    def write(self, vals):
        res = super().write(vals)
        if vals.get("tms_regular_reason"):
            for lr in self.filtered(lambda x: x.tms_cargo_type == "regular" and x.odc_suggested):
                self.env["tms.override.log"].log("cargo_regular", lr, _("LR %s kept Regular", lr.name),
                                                 reason=lr.tms_regular_reason)
        return res

    def _tms_check_confirm(self):
        res = super()._tms_check_confirm()
        self._tms_check_odc_lr()
        return res

    def _tms_check_odc_lr(self):
        """BR-ODC-02, BR-ODC-04."""
        for lr in self:
            if lr.tms_cargo_type == "regular":
                item_reason = "sale_line_id" in lr._fields and lr.sale_line_id.tms_regular_reason
                if lr.odc_suggested and not (lr.tms_regular_reason or item_reason):
                    raise UserError(_("LR %s has a piece above an ODC threshold: make it ODC or give a reason to "
                                      "keep it Regular (BR-ODC-02).", lr.name))
                continue
            missing = lr.line_ids.filtered(lambda x: not (x.piece_length and x.piece_width and x.piece_height))
            if missing:
                raise UserError(_("ODC LR %(lr)s needs the piece length, width and height on %(c)s (BR-ODC-04).",
                                  lr=lr.name, c=", ".join(missing.mapped("commodity"))))
            regular_bills = lr.ewaybill_ids.filtered(lambda b: not b.is_odc and b.state != "cancelled")
            if regular_bills:
                raise UserError(_("E-way bills of an ODC LR are generated as Over Dimensional Cargo (BR-ODC-04): %s",
                                  ", ".join(regular_bills.mapped("name"))))


class TmsConsignmentLine(models.Model):
    _inherit = "tms.consignment.line"

    piece_odc = fields.Boolean(string="ODC piece", compute="_compute_piece_odc")

    @api.depends("piece_length", "piece_width", "piece_height", "actual_weight", "packages")
    def _compute_piece_odc(self):
        for line in self:
            line.piece_odc = bool(line._tms_odc_exceeded(line.company_id or self.env.company))

    def _tms_odc_exceeded(self, company):
        weight_t = (self.actual_weight / max(self.packages, 1)) / 1000.0
        return company._tms_odc_exceeded(self.piece_length, self.piece_width, self.piece_height, weight_t)


class TmsEwaybill(models.Model):
    _inherit = "tms.ewaybill"

    @api.model_create_multi
    def create(self, vals_list):
        """BR-ODC-04: bills of an ODC LR are Over Dimensional Cargo, valid at 20 km a day."""
        for vals in vals_list:
            if vals.get("consignment_id"):
                lr = self.env["tms.consignment"].browse(vals["consignment_id"])
                if lr.tms_cargo_type == "odc":
                    vals["is_odc"] = True
        return super().create(vals_list)

    @api.onchange("consignment_id")
    def _onchange_consignment_odc(self):
        if self.consignment_id.tms_cargo_type == "odc":
            self.is_odc = True
