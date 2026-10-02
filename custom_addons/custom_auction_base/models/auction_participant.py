# -*- coding: utf-8 -*-
"""Participants and per-event roles.

AMD-AUC-001 AMD-07: roles are DATA, never res.groups. The same person may
legitimately be the authorised bidder on one event and an observer on
another, and a res.groups membership is global to the database and cannot
express that. One group grants portal access to the module; the role is a
field checked inside submit().
"""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class AuctionParticipant(models.Model):
    _name = "auction.participant"
    _description = "Auction Participant"
    _order = "event_id, id"
    _rec_name = "display_name_computed"

    event_id = fields.Many2one("auction.event", required=True, index=True,
                               ondelete="cascade")
    partner_id = fields.Many2one("res.partner", required=True, index=True)
    commercial_partner_id = fields.Many2one(
        related="partner_id.commercial_partner_id", store=True, index=True)
    alias = fields.Char(help="Bidder A, Bidder B. Lifted at award.")
    # STORED. This is _rec_name, so every name_search on a participant goes
    # through it -- and a non-stored field cannot be searched, so typing a
    # bidder's name into any search box raised "Non-stored field
    # auction.participant.display_name_computed cannot be searched" and the
    # view fell over. Every dependency is stored, so storing this is safe,
    # and lifting anonymity at award recomputes it for that event. FIX-017.
    display_name_computed = fields.Char(
        compute="_compute_display", store=True, index=True)

    state = fields.Selection(
        [("invited", "Invited"), ("viewed", "Viewed"),
         ("intent_confirmed", "Intent confirmed"), ("declined", "Declined"),
         ("prerequisites_met", "Prerequisites met"), ("qualified", "Qualified"),
         ("disqualified", "Disqualified"), ("submitted", "Bid submitted"),
         ("withdrawn", "Withdrawn"), ("excluded", "Excluded")],
        required=True, default="invited", index=True)

    technically_qualified = fields.Boolean(
        default=False,
        help="Set at technical evaluation sign-off. Commercial envelopes are "
             "opened only for qualified bidders. BR-MCH-008.")
    classification = fields.Selection(
        [("mse", "Micro or Small Enterprise"),
         ("dpiit", "DPIIT-recognised startup"),
         ("oem", "OEM"), ("dealer", "Authorised dealer"),
         ("trader", "Trader"), ("other", "Other")])
    prerequisites_accepted = fields.Boolean(default=False)
    accepted_by = fields.Many2one("res.users", readonly=True)
    accepted_at = fields.Datetime(readonly=True)
    accepted_ip = fields.Char(readonly=True)
    accepted_doc_hash = fields.Char(readonly=True, size=64)

    user_ids = fields.One2many("auction.participant.user", "participant_id")
    security_id = fields.Many2one("auction.bid.security",
                                  compute="_compute_security")
    exclusion_reason = fields.Text()

    _sql_constraints = [
        ("participant_uq", "unique(event_id, partner_id)",
         "A partner participates once in an event."),
        ("participant_alias_uq", "unique(event_id, alias)",
         "Aliases must be unique within an event."),
    ]

    @api.depends("partner_id", "alias", "event_id.anonymise")
    def _compute_display(self):
        for rec in self:
            rec.display_name_computed = (
                rec.alias if (rec.event_id.anonymise and rec.alias)
                else rec.partner_id.display_name)

    def _compute_security(self):
        Security = self.env["auction.bid.security"]
        for rec in self:
            rec.security_id = Security.search([
                ("event_id", "=", rec.event_id.id),
                ("participant_id", "=", rec.id)], limit=1)

    def _bid_security_permits_bidding(self):
        """Gating per SCP-AUC-001 #4.3 — bid on declared, award on verified."""
        self.ensure_one()
        if not self.event_id.require_bid_security:
            return True
        security = self.security_id
        return bool(security) and security.permits_bidding()


class AuctionParticipantUser(models.Model):
    """Per-event role assignment. AMD-AUC-001 AMD-07."""

    _name = "auction.participant.user"
    _description = "Participant Portal User Role"

    participant_id = fields.Many2one("auction.participant", required=True,
                                     index=True, ondelete="cascade")
    user_id = fields.Many2one("res.users", required=True, index=True,
                              ondelete="cascade")
    role = fields.Selection(
        [("authorised_bidder", "Authorised bidder"),
         ("observer", "Observer"),
         ("document_coordinator", "Document coordinator")],
        required=True, default="observer")

    _sql_constraints = [
        ("participant_user_uq", "unique(participant_id, user_id)",
         "A user holds one role per participation."),
    ]

    @api.constrains("role", "participant_id")
    def _check_single_bidder(self):
        for rec in self:
            if rec.role != "authorised_bidder":
                continue
            others = self.search_count([
                ("participant_id", "=", rec.participant_id.id),
                ("role", "=", "authorised_bidder"),
                ("id", "!=", rec.id),
            ])
            if others:
                raise ValidationError(_(
                    "One authorised bidder per participation. Reassign the "
                    "existing holder first."))
