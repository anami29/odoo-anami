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

    # FIX-028. What the portal will ACTUALLY let through, as opposed to
    # what the role list below appears to promise. Computed from the one
    # thing the portal consults: the organisation a login's own contact
    # sits under.
    portal_user_count = fields.Integer(compute="_compute_portal_reach")
    portal_reach_warning = fields.Char(compute="_compute_portal_reach")
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


    def _compute_portal_reach(self):
        """Who can actually sign in and act for this bidder.

        A login reaches this participation when its own contact sits under
        the participation's organisation. Nothing else grants it -- not a
        row in Portal Users and Roles, not a group. Stating the real number
        here is the difference between a buyer who knows the bidder can log
        in and a buyer who assumes it.
        """
        Users = self.env["res.users"].sudo()
        for rec in self:
            org = rec.commercial_partner_id
            reach = Users.search_count([
                ("partner_id.commercial_partner_id", "=", org.id),
                ("share", "=", True),
            ]) if org else 0
            rec.portal_user_count = reach
            if not org:
                rec.portal_reach_warning = _(
                    "No organisation set, so nobody can be given access.")
            elif not reach:
                rec.portal_reach_warning = _(
                    "Nobody at %s can sign in yet. Use Invite Bidder: "
                    "adding a row below grants no access on its own."
                ) % org.display_name
            else:
                rec.portal_reach_warning = False

    def action_invite_bidder(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Invite Bidder"),
            "res_model": "auction.invite.bidder.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_participant_id": self.id},
        }


class AuctionParticipantUser(models.Model):
    """Per-event role assignment. AMD-AUC-001 AMD-07."""

    _name = "auction.participant.user"
    _description = "Participant Portal User Role"

    participant_id = fields.Many2one("auction.participant", required=True,
                                     index=True, ondelete="cascade")
    user_id = fields.Many2one("res.users", required=True, index=True,
                              ondelete="cascade")
    # A portal bidder's partner is usually the COMPANY contact, so
    # res.users.display_name renders the organisation and the list read
    # "Shree Precision Components Pvt Ltd  Authorised bidder" -- which
    # names no person at all and is half of the confusion user testing
    # reported about contacts. The login always identifies the human.
    user_login = fields.Char(related="user_id.login", string="Login",
                             readonly=True)
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

    @api.constrains("role", "user_id", "participant_id")
    def _check_not_bidding_for_rivals(self):
        """FIX-027. One person may not bid for two rivals in one tender.

        The constraint above enforces one authorised bidder PER
        PARTICIPANT, which reads like the same rule and is not: nothing
        stopped one person being the authorised bidder for two COMPETING
        organisations on the same event. User testing did exactly that and
        the system accepted it.

        The training manual already asserted this could not happen, which
        is the worst configuration of all -- a control that exists only in
        the documentation. Whether the duplicate row currently grants any
        access is beside the point: it is a governance record of who is
        answerable for a bid, and two rival bids answerable to one person
        is the arrangement every tender rule in existence is written to
        prevent.
        """
        for rec in self:
            if rec.role != "authorised_bidder" or not rec.user_id:
                continue
            event = rec.participant_id.event_id
            if not event:
                continue
            clash = self.search([
                ("id", "!=", rec.id),
                ("role", "=", "authorised_bidder"),
                ("user_id", "=", rec.user_id.id),
                ("participant_id.event_id", "=", event.id),
                ("participant_id", "!=", rec.participant_id.id),
            ], limit=1)
            if clash:
                raise ValidationError(_(
                    "%(login)s is already the authorised bidder for "
                    "%(other)s on %(event)s. One person cannot submit for "
                    "two competing bidders in the same tender. Name a "
                    "different person, or make this one an observer.",
                    login=rec.user_id.login,
                    other=clash.participant_id.display_name,
                    event=event.name))

    @api.constrains("user_id", "participant_id")
    def _check_user_reaches_the_portal(self):
        """FIX-028. A role row that grants nothing must not look as if it does.

        Portal access is resolved entirely from the logged-in user's own
        partner: `portal_for_user` matches the participant whose
        commercial_partner_id equals the user's, and the record rules do
        the same. These role rows are read by nothing in that path.

        So naming someone here who sits under a different organisation --
        or under no organisation -- grants them precisely nothing, while
        the form says 'Portal Users and Roles' and the buyer reasonably
        believes the bidder can now log in. User testing hit this three
        separate ways and reported it as three separate problems.

        Rejecting the row is the honest answer: the buyer is told what is
        actually required, and pointed at the button that does it.
        """
        for rec in self:
            if not rec.user_id or not rec.participant_id:
                continue
            theirs = rec.user_id.partner_id.commercial_partner_id
            ours = rec.participant_id.commercial_partner_id
            if not ours or theirs == ours:
                continue
            raise ValidationError(_(
                "%(login)s belongs to %(theirs)s, not to %(ours)s, so "
                "adding them here would grant no access at all: the portal "
                "decides what a bidder may see from the organisation their "
                "own contact sits under, never from this list.\n\n"
                "Use 'Invite Bidder' on the participant to create a contact "
                "under %(ours)s and send them portal access.",
                login=rec.user_id.login,
                theirs=theirs.display_name or _("no organisation"),
                ours=ours.display_name))
