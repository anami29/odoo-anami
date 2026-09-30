# -*- coding: utf-8 -*-
"""Event header — the governing record. BRD group EVT."""
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from ..services import crypto as crypto_svc
from ..services import registry as mech_registry

_logger = logging.getLogger(__name__)


class AuctionEvent(models.Model):
    _name = "auction.event"
    _description = "Auction Event"
    _inherit = ["mail.thread", "mail.activity.mixin"]   # safe here, NOT on the ledger
    _order = "id desc"

    name = fields.Char(required=True, copy=False, readonly=True,
                       default=lambda s: _("New"))
    title = fields.Char(required=True, tracking=True)
    direction = fields.Selection(
        [("reverse", "Reverse — procurement"),
         ("forward", "Forward — disposal")],
        required=True, default="reverse", tracking=True)
    mechanism = fields.Selection(
        selection="_selection_mechanism", required=True, tracking=True)
    structure = fields.Selection(
        [("single", "Single stage"), ("two_envelope", "Two envelope")],
        required=True, default="single",
        help="AMD-AUC-001 AMD-01 decoupled structure from mechanism. "
             "multi_round is out of scope for v1.0 — see GRP-AUC-001 #5.")

    category_id = fields.Many2one("auction.category", required=True, index=True)
    company_id = fields.Many2one("res.company", required=True, index=True,
                                 default=lambda s: s.env.company)
    owner_id = fields.Many2one("res.users", required=True,
                               default=lambda s: s.env.user, tracking=True)
    deputy_id = fields.Many2one(
        "res.users", tracking=True,
        help="GAP-001. A live event with an absent owner has no operator.")
    currency_id = fields.Many2one(
        "res.currency", required=True,
        default=lambda s: s.env.company.currency_id)

    estimated_value = fields.Monetary(
        groups="custom_auction_base.group_auction_event_owner",
        help="Internal only. Excluded from every portal serialiser by "
             "allowlist, not by view omission.")

    publish_datetime = fields.Datetime(tracking=True)
    bid_open_datetime = fields.Datetime(required=True, tracking=True)
    bid_close_datetime = fields.Datetime(required=True, index=True, tracking=True)
    tech_close_datetime = fields.Datetime(
        help="AMD-AUC-001 AMD-01: required where structure is two_envelope "
             "and the commercial stage is live.")
    tech_open_datetime = fields.Datetime()
    comm_open_datetime = fields.Datetime()

    state = fields.Selection(
        [("draft", "Draft"), ("under_approval", "Under approval"),
         ("approved", "Approved"), ("published", "Published"), ("live", "Live"),
         ("bidding_closed", "Bidding closed"), ("under_evaluation", "Under evaluation"),
         ("award_pending", "Award pending approval"), ("awarded", "Awarded"),
         ("unsuccessful", "Unsuccessful"), ("cancelled", "Cancelled")],
        required=True, default="draft", index=True, tracking=True)

    version = fields.Integer(required=True, default=1, readonly=True)
    content_version = fields.Integer(required=True, default=1, readonly=True)
    anonymise = fields.Boolean(default=True)

    rule_set_id = fields.Many2one("auction.rule.set", required=True)
    rule_snapshot_id = fields.Many2one("auction.rule.set", readonly=True, copy=False)

    section_ids = fields.One2many("auction.section", "event_id")
    lot_ids = fields.One2many("auction.lot", "event_id")
    participant_ids = fields.One2many("auction.participant", "event_id")
    bid_ids = fields.One2many("auction.bid", "event_id")
    security_ids = fields.One2many("auction.bid.security", "event_id")

    require_bid_security = fields.Boolean(default=True)
    bid_security_amount = fields.Monetary()
    security_grace_hours = fields.Integer(
        default=48,
        help="BR-PRQ-025. Grace after close for finance to complete "
             "verification before a bid becomes non-responsive.")
    security_verify_lead_hours = fields.Integer(
        default=24, help="BR-PRQ-024. Verification deadline before close.")

    # Wrapped data encryption key. Never logged, never exported.
    dek_wrapped = fields.Binary(
        attachment=False, copy=False,
        groups="custom_auction_base.group_auction_opener")
    dek_fingerprint = fields.Char(readonly=True, copy=False)
    dek_salt = fields.Binary(attachment=False, copy=False,
                             groups="custom_auction_base.group_auction_opener")

    @api.model
    def _selection_mechanism(self):
        return mech_registry.selection()

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                seq = ("auction.event.forward"
                       if vals.get("direction") == "forward"
                       else "auction.event.reverse")
                vals["name"] = self.env["ir.sequence"].next_by_code(seq) or "/"
        return super().create(vals_list)

    @api.constrains("bid_open_datetime", "bid_close_datetime",
                    "tech_open_datetime", "comm_open_datetime")
    def _check_schedule(self):
        """Field-specific errors, not a generic one. BR-EVT-010."""
        for ev in self:
            if ev.bid_close_datetime <= ev.bid_open_datetime:
                raise ValidationError(_(
                    "Bid close must be after bid open. Close is %(close)s and "
                    "open is %(open)s.",
                    close=ev.bid_close_datetime, open=ev.bid_open_datetime))
            if ev.comm_open_datetime and \
                    ev.comm_open_datetime < ev.bid_close_datetime:
                raise ValidationError(_(
                    "Commercial opening cannot precede bid close."))
            if ev.tech_open_datetime and ev.comm_open_datetime and \
                    ev.comm_open_datetime < ev.tech_open_datetime:
                raise ValidationError(_(
                    "Commercial opening cannot precede technical opening."))

    @api.constrains("mechanism", "direction")
    def _check_mechanism_direction(self):
        for ev in self:
            if ev.mechanism and \
                    mech_registry.get(ev.mechanism).direction != ev.direction:
                raise ValidationError(_(
                    "Mechanism %(m)s is not available for a %(d)s event.",
                    m=ev.mechanism, d=ev.direction))

    # ------------------------------------------------------------------
    def action_publish(self):
        """Completeness gate, rule snapshot, key generation, invitations."""
        for ev in self:
            failures = ev._completeness_failures()
            if failures:
                raise UserError(_("This event is not ready to publish:\n  • %s")
                                % "\n  • ".join(failures))
            ev.rule_snapshot_id = ev.rule_set_id.snapshot()
            ev._generate_dek()
            ev.write({"state": "published",
                      "publish_datetime": fields.Datetime.now()})
            ev.lot_ids.write({"state": "pending"})
            self.env["auction.audit"].log(
                action="event_published", model=self._name, res_id=ev.id,
                event_id=ev.id, detail={"version": ev.version})

    def _completeness_failures(self):
        """Every failure listed before blocking. BR-INV-001."""
        self.ensure_one()
        out = []
        if not self.lot_ids:
            out.append(_("No lots have been defined."))
        if not self.lot_ids.mapped("line_ids"):
            out.append(_("No line items have been defined."))
        if not self.participant_ids:
            out.append(_("No participants have been invited."))
        if not self.deputy_id:
            out.append(_("A deputy event owner is required before publication."))
        if self.require_bid_security and not self.bid_security_amount:
            out.append(_("Bid security is required but no amount is set."))
        if self.structure == "two_envelope" and not self.tech_open_datetime:
            out.append(_("A two-envelope event needs a technical opening time."))
        return out

    def _generate_dek(self):
        """Per-event data key, wrapped by a KEK held outside the database."""
        import os
        self.ensure_one()
        dek = crypto_svc.generate_dek()
        kek = crypto_svc.kek_from_environment()
        self.sudo().write({
            "dek_wrapped": crypto_svc.wrap_dek(dek, kek),
            "dek_fingerprint": crypto_svc.fingerprint(dek),
            "dek_salt": os.urandom(16),
        })

    def _dek(self):
        """Unwrap for sealing. Opening goes through auction.opening.execute."""
        self.ensure_one()
        kek = crypto_svc.kek_from_environment()
        return crypto_svc.unwrap_dek(self.sudo().dek_wrapped, kek)

    def _stage_evaluation_values(self, bid, plaintext):
        """Decrypted values go into the evaluation working set. The ledger
        envelope is never overwritten. Evaluation lands in stage 10."""
        _logger.info("auction.event: staged %d bytes for bid %s",
                     len(plaintext), bid.reference)

    def action_open_bidding(self):
        for ev in self:
            ev.write({"state": "live"})
            ev.lot_ids.filtered(lambda l: l.state == "pending").write({
                "state": "open"})

    def action_cancel(self, reason=None):
        """GAP-024: cancellation after bids are received is not the same act
        as cancelling a draft, and requires approval one tier above."""
        for ev in self:
            has_bids = bool(ev.bid_ids.filtered(lambda b: b.state == "active"))
            if has_bids and not self.env.user.has_group(
                    "custom_auction_base.group_auction_approver"):
                raise UserError(_(
                    "This event has received bids. Cancellation requires "
                    "approval at one tier above the event value band."))
            ev.bid_ids.filtered(lambda b: b.state == "active")._set_state(
                "void", reason="event cancelled")
            ev.write({"state": "cancelled"})
            self.env["auction.audit"].log(
                action="event_cancelled", model=self._name, res_id=ev.id,
                event_id=ev.id, detail={"reason": reason, "had_bids": has_bids})
