# -*- coding: utf-8 -*-
"""Give a bidder someone who can actually log in and bid. FIX-028.

Before this, inviting a bidder meant three steps in two apps and a piece
of knowledge the interface never stated: the portal decides what a bidder
may see from the organisation their own CONTACT sits under, not from the
'Portal Users and Roles' list on the participant. A buyer who filled in
that list had granted nothing. A buyer who wanted to do it properly had to
go to Contacts, create a child contact under the right company, grant
portal access, and come back -- and Contacts is an app most buyers have no
licence for, which is where user testing stopped.

So the whole operation lives here, on the participant, where the buyer
already is. It runs with sudo for the contact and user records only: the
buyer is not granted the Contacts app, they are granted this one
well-defined thing. What it creates is always a child of the
participant's own commercial partner, so the contact it makes is one the
portal will actually resolve.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class AuctionInviteBidderWizard(models.TransientModel):
    _name = "auction.invite.bidder.wizard"
    _description = "Invite a Bidder to the Portal"

    participant_id = fields.Many2one(
        "auction.participant", required=True, readonly=True)
    event_id = fields.Many2one(related="participant_id.event_id",
                               readonly=True)
    organisation_id = fields.Many2one(
        related="participant_id.commercial_partner_id", readonly=True,
        string="Organisation")

    mode = fields.Selection(
        [("new", "A new person at this organisation"),
         ("existing", "Someone who already has a login here")],
        required=True, default="new")

    name = fields.Char(string="Full Name")
    email = fields.Char(string="Email")
    phone = fields.Char()
    # Limited to people already under this organisation, because anyone
    # else would resolve to a different bidder and see nothing here.
    user_id = fields.Many2one(
        "res.users", string="Existing Login",
        domain="[('partner_id.commercial_partner_id', '=', organisation_id)]")

    role = fields.Selection(
        [("authorised_bidder", "Authorised bidder"),
         ("observer", "Observer"),
         ("document_coordinator", "Document coordinator")],
        required=True, default="authorised_bidder")

    send_invitation = fields.Boolean(
        string="Email the portal invitation", default=True,
        help="Uncheck if no mail server is configured. The login is still "
             "created and can be invited later from Settings, Users.")

    existing_note = fields.Char(compute="_compute_existing_note")

    @api.depends("participant_id")
    def _compute_existing_note(self):
        for wiz in self:
            holder = wiz.participant_id.user_ids.filtered(
                lambda u: u.role == "authorised_bidder")
            wiz.existing_note = (
                _("%s currently holds Authorised bidder for this "
                  "participation.") % holder[0].user_id.login
                if holder else False)

    # ------------------------------------------------------------------
    def _contact_for(self):
        """The contact who will log in, always under this organisation."""
        self.ensure_one()
        Partner = self.env["res.partner"].sudo()
        org = self.participant_id.commercial_partner_id
        if not org:
            raise UserError(_(
                "This participant has no organisation, so there is nothing "
                "to attach a login to. Set the Partner on the participant "
                "first."))
        if self.mode == "existing":
            if not self.user_id:
                raise UserError(_("Choose the login to give a role to."))
            return self.user_id.partner_id, self.user_id
        if not self.name or not self.email:
            raise UserError(_(
                "A name and an email address are needed to create a login."))
        email = self.email.strip()
        # Reuse a contact already under this organisation with that email,
        # rather than quietly creating a second one.
        contact = Partner.search([
            ("email", "=ilike", email),
            ("commercial_partner_id", "=", org.id),
        ], limit=1)
        if not contact:
            contact = Partner.create({
                "name": self.name.strip(),
                "email": email,
                "phone": self.phone,
                "parent_id": org.id,
                "type": "contact",
            })
        return contact, contact.user_ids[:1]

    def _login_for(self, contact, user):
        """A portal login for that contact, created once."""
        if user:
            return user
        Users = self.env["res.users"].sudo()
        clash = Users.with_context(active_test=False).search(
            [("login", "=ilike", contact.email)], limit=1)
        if clash:
            if clash.partner_id.commercial_partner_id != \
                    self.participant_id.commercial_partner_id:
                raise UserError(_(
                    "A login already exists for %(email)s, but it belongs "
                    "to %(other)s. One email address cannot act for two "
                    "bidding organisations.",
                    email=contact.email,
                    other=clash.partner_id.commercial_partner_id.display_name))
            return clash
        return Users.with_context(no_reset_password=True).create({
            "name": contact.name,
            "login": contact.email,
            "email": contact.email,
            "partner_id": contact.id,
            "groups_id": [(6, 0, [
                self.env.ref("base.group_portal").id,
                self.env.ref("custom_auction_base.group_auction_bidder").id,
            ])],
        })

    def action_invite(self):
        self.ensure_one()
        contact, user = self._contact_for()
        user = self._login_for(contact, user)

        Role = self.env["auction.participant.user"]
        existing = Role.search([
            ("participant_id", "=", self.participant_id.id),
            ("user_id", "=", user.id),
        ], limit=1)
        if existing:
            existing.role = self.role
        else:
            # Not sudo: the two constraints on this model are exactly the
            # governance checks that must apply to a buyer's action.
            Role.create({
                "participant_id": self.participant_id.id,
                "user_id": user.id,
                "role": self.role,
            })

        if self.send_invitation:
            self._try_invite(user)

        self.env["auction.audit"].sudo().log(
            action="bidder_invited", model="auction.participant",
            res_id=self.participant_id.id,
            event_id=self.participant_id.event_id.id,
            detail={"user": user.login, "role": self.role})
        return {"type": "ir.actions.act_window_close"}

    def _try_invite(self, user):
        """Send the portal invitation, but never fail the whole action on it.

        Most SME installs have no mail server until somebody gets round to
        it, and a login that exists is far more use than a transaction
        rolled back because an SMTP host was unreachable. This is the same
        failure that rolled back an early close in FIX-013, so it is
        handled the same way: inside a savepoint, with the outcome
        recorded rather than raised.

        auction.participant carries no chatter, so the note goes to the
        audit log, which is this module's own record and always present.
        """
        try:
            with self.env.cr.savepoint():
                user.sudo().action_reset_password()
            sent = True
        except Exception as exc:  # noqa: BLE001 - deliberate, see docstring
            sent = False
            reason = str(exc)[:200]
        self.env["auction.audit"].sudo().log(
            action="bidder_invite_sent" if sent else "bidder_invite_failed",
            model="auction.participant", res_id=self.participant_id.id,
            event_id=self.participant_id.event_id.id,
            detail={"user": user.login} if sent
            else {"user": user.login, "error": reason})
