from odoo import _, api, fields, models
from odoo.exceptions import UserError

from odoo.addons.tms_core.models.tms_consignment import CARGO_TYPES


class CrmLead(models.Model):
    """BR-CRM-01..06."""

    _inherit = "crm.lead"

    tms_lane_ids = fields.One2many("tms.lead.lane", "lead_id", string="Enquired lanes")
    tms_lane_revenue = fields.Monetary(compute="_compute_tms_lane_revenue", string="Expected revenue from lanes",
                                       currency_field="company_currency")

    @api.depends("tms_lane_ids.monthly_value")
    def _compute_tms_lane_revenue(self):
        """BR-CRM-04: Σ monthly volume × target or quoted amount × 12."""
        for lead in self:
            lead.tms_lane_revenue = sum(lead.tms_lane_ids.mapped("monthly_value")) * 12

    @api.onchange("tms_lane_ids")
    def _onchange_tms_lanes(self):
        if self.tms_lane_ids:
            self.expected_revenue = self.tms_lane_revenue

    def write(self, vals):
        res = super().write(vals)
        if "tms_lane_ids" in vals:
            for lead in self.filtered("tms_lane_ids"):
                super(CrmLead, lead).write({"expected_revenue": lead.tms_lane_revenue})
        return res

    def action_set_lost(self, **additional_values):
        """BR-CRM-03: marking an opportunity Lost needs a lost reason."""
        if not additional_values.get("lost_reason_id") and any(
                lead.type == "opportunity" and not lead.lost_reason_id for lead in self):
            if not self.env.context.get("tms_allow_lost_without_reason"):
                raise UserError(_("Choose a lost reason (BR-CRM-03)."))
        return super().action_set_lost(**additional_values)

    def _find_matching_partner(self, email_only=False):
        """BR-CRM-05: match the customer by GSTIN, then PAN, before creating a duplicate."""
        self.ensure_one()
        Partner = self.env["res.partner"]
        vat = self.env.context.get("tms_lead_vat")
        pan = self.env.context.get("tms_lead_pan")
        if vat:
            partner = Partner.search([("vat", "=", vat)], limit=1)
            if partner:
                return partner
        if pan:
            partner = Partner.search([("l10n_in_pan", "=", pan)], limit=1)
            if partner:
                return partner
        return super()._find_matching_partner(email_only=email_only)

    tms_vat = fields.Char(string="GSTIN")
    tms_pan = fields.Char(string="PAN")

    def _handle_partner_assignment(self, force_partner_id=False, create_missing=True):
        for lead in self:
            super(CrmLead, lead.with_context(tms_lead_vat=lead.tms_vat, tms_lead_pan=lead.tms_pan)) \
                ._handle_partner_assignment(force_partner_id=force_partner_id, create_missing=create_missing)
            if lead.partner_id and create_missing:
                vals = {}
                if lead.tms_vat and not lead.partner_id.vat:
                    vals["vat"] = lead.tms_vat
                if lead.tms_pan and not lead.partner_id.l10n_in_pan:
                    vals["l10n_in_pan"] = lead.tms_pan
                if vals:
                    lead.partner_id.write(vals)
        return True

    def _prepare_customer_values(self, partner_name, is_company=False, parent_id=False):
        vals = super()._prepare_customer_values(partner_name, is_company=is_company, parent_id=parent_id)
        if self.tms_vat and is_company:
            vals["vat"] = self.tms_vat
        if self.tms_pan and is_company:
            vals["l10n_in_pan"] = self.tms_pan
        return vals


class TmsLeadLane(models.Model):
    """BR-CRM-02: enquired lanes of a qualified opportunity."""

    _name = "tms.lead.lane"
    _description = "Enquired lane"

    lead_id = fields.Many2one("crm.lead", required=True, ondelete="cascade")
    company_id = fields.Many2one(related="lead_id.company_id", store=True)
    currency_id = fields.Many2one(related="lead_id.company_currency")
    origin_id = fields.Many2one("res.partner", string="Origin", domain=[("tms_location", "=", True)], required=True)
    destination_id = fields.Many2one("res.partner", string="Destination", domain=[("tms_location", "=", True)],
                                     required=True)
    service_type = fields.Selection([("ftl", "Full truck load"), ("ptl", "Part load"), ("express", "Express"),
                                     ("odc", "ODC move")], default="ftl", required=True)
    vehicle_class_id = fields.Many2one("tms.vehicle.class", string="Vehicle type")
    tms_cargo_type = fields.Selection(CARGO_TYPES, string="Cargo Type", default="regular", required=True)
    loads_per_month = fields.Float()
    tonnage_per_month = fields.Float()
    target_rate = fields.Monetary(help="Target amount per load")
    quoted_amount = fields.Monetary(help="Quoted amount per load")
    monthly_value = fields.Monetary(compute="_compute_monthly_value", store=True)

    @api.depends("loads_per_month", "target_rate", "quoted_amount")
    def _compute_monthly_value(self):
        for lane in self:
            lane.monthly_value = lane.loads_per_month * (lane.quoted_amount or lane.target_rate)
