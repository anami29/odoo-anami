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
                raise UserError(_("Choose a lost reason."))
        return super().action_set_lost(**additional_values)

    tms_vat = fields.Char(string="GSTIN")
    tms_pan = fields.Char(string="PAN")

    @staticmethod
    def _tms_clean_tax_id(value):
        return (value or "").strip().upper().replace(" ", "")

    def _tms_lead_tax_ids(self):
        """GSTIN and PAN of the lead: the context keys first (callers may pass them), then the lead's own fields.
        Without a PAN, the PAN held inside the GSTIN (characters 3 to 12) is used."""
        self.ensure_one()
        vat = self._tms_clean_tax_id(self.env.context.get("tms_lead_vat") or self.tms_vat)
        pan = self._tms_clean_tax_id(self.env.context.get("tms_lead_pan") or self.tms_pan)
        if not pan and len(vat) == 15:
            pan = vat[2:12]
        return vat, pan

    def _tms_match_partner_by_tax_id(self):
        """BR-CRM-05: the existing customer with the lead's GSTIN, else with its PAN; empty when neither matches."""
        self.ensure_one()
        Partner = self.env["res.partner"]
        vat, pan = self._tms_lead_tax_ids()
        company_domain = [("company_id", "in", [False, self.company_id.id or self.env.company.id])]
        if vat:
            partner = Partner.search([("vat", "=ilike", vat)] + company_domain, order="id", limit=1)
            if partner:
                return partner.commercial_partner_id
        if pan:
            partner = Partner.search(["|", ("l10n_in_pan", "=ilike", pan), ("vat", "=ilike", "__%s%%" % pan)]
                                     + company_domain, order="id", limit=1)
            if partner:
                return partner.commercial_partner_id
        return Partner

    def _find_matching_partner(self, email_only=False):
        """BR-CRM-05: match the customer by GSTIN, then PAN, before the standard email and name match."""
        self.ensure_one()
        if not self.partner_id:
            partner = self._tms_match_partner_by_tax_id()
            if partner:
                return partner
        return super()._find_matching_partner(email_only=email_only)

    def _handle_partner_assignment(self, force_partner_id=False, create_missing=True):
        for lead in self:
            if not force_partner_id and not lead.partner_id and create_missing:
                # link the customer already registered under this GSTIN or PAN instead of creating a duplicate
                match = lead._tms_match_partner_by_tax_id()
                if match:
                    lead.partner_id = match
            super(CrmLead, lead)._handle_partner_assignment(force_partner_id=force_partner_id,
                                                            create_missing=create_missing)
            if lead.partner_id and create_missing:
                partner = lead.partner_id.commercial_partner_id
                vals = {}
                if lead.tms_vat and not partner.vat:
                    vals["vat"] = lead._tms_clean_tax_id(lead.tms_vat)
                if lead.tms_pan and not partner.l10n_in_pan:
                    vals["l10n_in_pan"] = lead._tms_clean_tax_id(lead.tms_pan)
                if vals:
                    partner.write(vals)
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
