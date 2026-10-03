# -*- coding: utf-8 -*-
"""Create an event from a template, and save an event back as a template."""
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class AuctionTemplateApplyWizard(models.TransientModel):
    _name = "auction.template.apply.wizard"
    _description = "Create Event from Template"

    template_id = fields.Many2one(
        "auction.event.template", required=True, readonly=True)
    title = fields.Char(required=True)
    bid_open_datetime = fields.Datetime(
        required=True, default=lambda s: fields.Datetime.now())
    owner_id = fields.Many2one(
        "res.users", required=True, default=lambda s: s.env.user)
    estimated_value = fields.Monetary()
    currency_id = fields.Many2one(related="template_id.currency_id")

    preview = fields.Text(compute="_compute_preview")

    @api.depends("template_id", "bid_open_datetime", "estimated_value")
    def _compute_preview(self):
        from dateutil.relativedelta import relativedelta
        for wiz in self:
            tpl = wiz.template_id
            if not tpl or not wiz.bid_open_datetime:
                wiz.preview = ""
                continue
            close = wiz.bid_open_datetime + relativedelta(days=tpl.bid_window_days)
            opening = close + relativedelta(hours=tpl.comm_open_offset_hours)
            security = (tpl.security_amount if tpl.security_basis == "fixed"
                        else wiz.estimated_value * tpl.security_percent / 100.0)
            wiz.preview = _(
                "Bidding closes %(close)s\n"
                "Envelopes may be opened %(open)s\n"
                "%(lots)s lots, %(lines)s line items, %(parts)s participants\n"
                "Bid security %(sec).2f",
                close=close, open=opening, lots=tpl.lot_count,
                lines=tpl.line_count, parts=len(tpl.partner_ids), sec=security)

    def action_apply(self):
        self.ensure_one()
        event = self.template_id._instantiate(
            title=self.title,
            bid_open=self.bid_open_datetime,
            owner=self.owner_id,
            estimated_value=self.estimated_value,
        )
        return {
            "type": "ir.actions.act_window",
            "res_model": "auction.event",
            "res_id": event.id,
            "view_mode": "form",
        }


class AuctionTemplateSaveWizard(models.TransientModel):
    """Save an existing event back as a template. BR-TPL-012.

    Bidder-specific and bid data are excluded: an event's participants carry
    over as a default panel, but nothing about what anyone bid.
    """

    _name = "auction.template.save.wizard"
    _description = "Save Event as Template"

    event_id = fields.Many2one("auction.event", required=True, readonly=True)
    name = fields.Char(required=True)
    description = fields.Text()
    include_participants = fields.Boolean(
        default=True, string="Carry the participant panel")
    include_content = fields.Boolean(
        default=True, string="Carry lots and line items")

    def action_save(self):
        self.ensure_one()
        event = self.event_id
        if not event.bid_open_datetime or not event.bid_close_datetime:
            raise UserError(_("This event has no schedule to derive from."))

        window = (event.bid_close_datetime - event.bid_open_datetime).days or 1
        lead = ((event.bid_open_datetime - event.publish_datetime).days
                if event.publish_datetime else 2)
        opening = (
            int((event.comm_open_datetime - event.bid_close_datetime)
                .total_seconds() // 3600)
            if event.comm_open_datetime else 24)

        template = self.env["auction.event.template"].create({
            "name": self.name,
            "description": self.description,
            "direction": event.direction,
            "mechanism_id": event.mechanism_id.id,
            "structure": event.structure,
            "category_id": event.category_id.id,
            "rule_set_id": event.rule_set_id.id,
            "currency_id": event.currency_id.id,
            "anonymise": event.anonymise,
            "bid_window_days": max(window, 1),
            "publish_lead_days": max(lead, 0),
            "comm_open_offset_hours": max(opening, 0),
            "require_bid_security": event.require_bid_security,
            "security_basis": "fixed",
            "security_amount": event.bid_security_amount,
            "security_verify_lead_hours": event.security_verify_lead_hours,
            "security_grace_hours": event.security_grace_hours,
            "partner_ids": [(6, 0, event.participant_ids.mapped("partner_id").ids)]
                           if self.include_participants else False,
        })

        if self.include_content:
            close_date = event.bid_close_datetime.date()
            for lot in event.lot_ids:
                tlot = self.env["auction.event.template.lot"].create({
                    "template_id": template.id,
                    "name": lot.name,
                    "sequence": lot.sequence,
                    "award_basis": lot.award_basis,
                    "all_or_nothing": lot.all_or_nothing,
                    "disclose_limit": lot.disclose_limit,
                    "precision_digits": lot.precision_digits,
                })
                for line in lot.line_ids:
                    offset = ((line.required_by - close_date).days
                              if line.required_by else 30)
                    self.env["auction.event.template.line"].create({
                        "template_lot_id": tlot.id,
                        "name": line.name,
                        "sequence": line.sequence,
                        "product_id": line.product_id.id or False,
                        "product_qty": line.product_qty,
                        "product_uom_id": line.product_uom_id.id or False,
                        "specification": line.specification,
                        "hsn_sac": line.hsn_sac,
                        "delivery_location_id":
                            line.delivery_location_id.id,
                        "delivery_location":
                            line.delivery_location,
                        "line_type": line.line_type,
                        "delivery_location": line.delivery_location,
                        "delivery_offset_days": offset,
                        "schedule_periods": len(line.schedule_ids),
                    })

        return {
            "type": "ir.actions.act_window",
            "res_model": "auction.event.template",
            "res_id": template.id,
            "view_mode": "form",
        }
