# -*- coding: utf-8 -*-
"""Event header — the governing record. BRD group EVT."""
import json
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from ..services import crypto as crypto_svc

_logger = logging.getLogger(__name__)


def _payload_id(value):
    """Recover a record id from a canonical payload leaf.

    ``chain.canonical_payload`` renders every numeric leaf as a fixed
    precision string so that the tamper-evidence hash is reproducible. An
    id therefore comes back as "42.000000". This turns it back into 42.
    """
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


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
    mechanism_id = fields.Many2one(
        "auction.mechanism", required=True, tracking=True,
        domain="[('direction', '=', direction)]",
        help="Filtered by direction. A reverse event cannot be given a "
             "forward mechanism.")
    mechanism = fields.Char(
        related="mechanism_id.code", store=True, readonly=True, index=True,
        help="The registry lookup key. Kept so that engine code addresses "
             "a mechanism by code rather than by record id.")
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

    # Explicit labels. Without them Odoo derives "Bid Open Datetime" and
    # "Comm Open Datetime" from the field names, and the screen then
    # disagrees with every document that calls them Bid Open and Commercial
    # Opening. A trainee reading the manual beside the screen should not
    # have to translate. FIX-012.
    publish_datetime = fields.Datetime(string="Published", tracking=True)
    bid_open_datetime = fields.Datetime(
        string="Bid Open", required=True, tracking=True)
    bid_close_datetime = fields.Datetime(
        string="Bid Close", required=True, index=True, tracking=True)
    tech_close_datetime = fields.Datetime(
        string="Technical Close",
        help="AMD-AUC-001 AMD-01: required where structure is two_envelope "
             "and the commercial stage is live.")
    tech_open_datetime = fields.Datetime(string="Technical Opening")
    comm_open_datetime = fields.Datetime(string="Commercial Opening")

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
    award_ids = fields.One2many("auction.award", "event_id")
    award_count = fields.Integer(compute="_compute_award_count")
    order_count = fields.Integer(compute="_compute_award_count")

    def _compute_award_count(self):
        """FIX-023. Counted with sudo; NAVIGATING to them is not.

        Mapping into order_ids reads purchase.order and sale.order in the
        CALLER's environment. An Event Owner who does not also hold the
        purchase licence therefore raised AccessError on this compute, and
        because the compute backs a field on the event form, the form died
        with it: after the award was generated, the buyer who ran the
        tender could no longer open their own event at all. The web client
        fell back to the event list, which made it look like a search
        filter rather than a permission fault.

        This is the fourth appearance of one root cause (FIX-011, FIX-016,
        FIX-018): the engine reads in the caller's environment. The rule
        FIX-016 settled applies here unchanged -- counting documents
        generated from your own event discloses nothing, while opening one
        needs the licence that reads it, and the stat button already
        carries that group.
        """
        for ev in self:
            ev.award_count = len(ev.award_ids)
            awards = ev.award_ids.sudo()
            ev.order_count = (len(awards.mapped("order_ids"))
                              + len(awards.mapped("sale_order_ids")))

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

    # Dual authorisation for the opening. Two DIFFERENT people must act.
    #
    # This is dual AUTHORISATION, not split-key custody: the data key is
    # recovered from the KEK server side, so somebody holding the KEK and
    # database access could open alone. That matches the SME key custody
    # position already stated in SCP-AUC-001 #3, where the KEK lives in the
    # application host environment and protects against a database
    # administrator rather than against root. The full profile splits the
    # key between the two openers; the crypto for it is already written and
    # tested in services/crypto.py.
    # Optional nomination, the way a tender opening committee is named in
    # advance. Leave both empty and any two distinct authorised openers may
    # act; name them and only those two may. The domain cannot be expressed
    # declaratively against a group xmlid, so it is a constraint.
    opener_a_id = fields.Many2one(
        "res.users", string="First Opener", copy=False)
    opener_b_id = fields.Many2one(
        "res.users", string="Second Opener", copy=False)
    opening_requested_by = fields.Many2one("res.users", readonly=True, copy=False)
    opening_requested_on = fields.Datetime(readonly=True, copy=False)
    opening_ids = fields.One2many("auction.opening", "event_id", readonly=True)

    # Bidder-facing content. Stage 12.
    document_ids = fields.One2many("auction.document", "event_id")
    clarification_ids = fields.One2many("auction.clarification", "event_id")
    corrigendum_ids = fields.One2many("auction.corrigendum", "event_id")
    open_question_count = fields.Integer(compute="_compute_portal_counts")

    def _compute_portal_counts(self):
        for ev in self:
            ev.open_question_count = len(ev.clarification_ids.filtered(
                lambda c: c.state == "asked"))
    evaluation_line_ids = fields.One2many(
        "auction.evaluation.line", "event_id", readonly=True)
    evaluation_count = fields.Integer(compute="_compute_evaluation_count")

    def _compute_evaluation_count(self):
        for ev in self:
            ev.evaluation_count = len(ev.evaluation_line_ids)

    @api.constrains("opener_a_id", "opener_b_id")
    def _check_nominated_openers(self):
        for ev in self:
            pair = ev.opener_a_id | ev.opener_b_id
            if ev.opener_a_id and ev.opener_a_id == ev.opener_b_id:
                raise ValidationError(_(
                    "The two openers must be different people. One person "
                    "acting twice is not a dual control."))
            for user in pair:
                if not user.has_group(
                        "custom_auction_base.group_auction_opener"):
                    raise ValidationError(_(
                        "%s is not an authorised envelope opener.") % user.name)

    def _assert_may_open(self, user):
        """Nominated openers, where any were nominated."""
        self.ensure_one()
        if not user.has_group("custom_auction_base.group_auction_opener"):
            raise UserError(_("You are not an authorised envelope opener."))
        nominated = self.opener_a_id | self.opener_b_id
        if nominated and user not in nominated:
            raise UserError(_(
                "This opening is reserved to the nominated openers: %s.")
                % ", ".join(nominated.mapped("name")))

    # Provenance only. Recorded for reporting and NEVER read at runtime:
    # BR-TPL-009 requires instantiation to copy by value, so a template edit
    # must not be able to reach an event that already exists.
    template_id = fields.Many2one(
        "auction.event.template", readonly=True, ondelete="set null")
    template_version = fields.Integer(readonly=True)

    def action_save_as_template(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Save as Template"),
            "res_model": "auction.template.save.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_event_id": self.id,
                "default_name": self.title,
            },
        }

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

    @api.constrains("mechanism_id", "direction")
    def _check_mechanism_direction(self):
        """Belt and braces. The domain stops it in the interface; this stops
        it from any other route."""
        for ev in self:
            if ev.mechanism_id and ev.mechanism_id.direction != ev.direction:
                raise ValidationError(_(
                    "'%(m)s' is a %(md)s mechanism and this is a %(d)s event.",
                    m=ev.mechanism_id.name, md=ev.mechanism_id.direction,
                    d=ev.direction))

    @api.onchange("direction")
    def _onchange_direction(self):
        """Clear a mechanism that no longer fits, rather than leaving a
        stale value sitting behind a filtered dropdown."""
        for ev in self:
            if ev.mechanism_id and ev.mechanism_id.direction != ev.direction:
                ev.mechanism_id = False

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

    def _bump_content_version(self):
        """Record that what bidders can read has changed.

        Documents, clarifications and corrigenda all move this. A bidder
        who submitted against content_version 2 and an event now at 4 is a
        fact somebody will want afterwards.
        """
        for ev in self:
            ev.sudo().write({"content_version": ev.content_version + 1})

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

    def _stage_evaluation_values(self, bid, plaintext, opening=None):
        """Write the decrypted values into the evaluation working set.

        The ledger envelope is never overwritten and never decrypted in
        place, so the sealed record stays verifiable after the opening.
        BR-SLD-013.
        """
        self.ensure_one()
        payload = json.loads(plaintext.decode("utf-8"))["payload"]
        Evaluation = self.env["auction.evaluation.line"].sudo()
        by_line = {bl.line_id.id: bl for bl in bid.line_ids}

        items = payload.get("lines", [])
        staged = 0
        for item in items:
            # chain.canonical_payload quantizes EVERY numeric leaf to a
            # fixed-precision string, so the record id arrives as the string
            # "42.000000" and not as the integer 42. Looking that up in a
            # dict keyed by integer id misses every time, which would stage
            # nothing at all while the opening reported success.
            bid_line = by_line.get(_payload_id(item.get("line")))
            if not bid_line:
                continue
            price = float(item["price_unit"])
            qty = float(item["qty_offered"])
            staged += 1
            Evaluation.create({
                "event_id": self.id,
                "opening_id": opening.id if opening else False,
                "bid_id": bid.id,
                "bid_line_id": bid_line.id,
                "participant_id": bid.participant_id.id,
                "lot_id": bid.lot_id.id,
                "line_id": bid_line.line_id.id,
                "price_unit": price,
                "qty_offered": qty,
                "norm_value": price,
                "max_deviation_days": bid_line.max_deviation_days,
            })

        if items and not staged:
            # Refuse silently producing nothing. An opening that decrypts a
            # payload and stages zero rows is a defect, not an empty bid:
            # the envelope opened, so the lines were there.
            raise UserError(_(
                "Bid %(ref)s decrypted but none of its %(count)d priced "
                "lines could be matched to the ledger. The opening has been "
                "rolled back; nothing was staged and the envelope is "
                "untouched.",
                ref=bid.reference, count=len(items)))

    def action_open_bidding(self):
        for ev in self:
            ev.write({"state": "live"})
            ev.lot_ids.filtered(lambda l: l.state == "pending").write({
                "state": "open"})

    def action_close_bidding(self):
        """Close the window. Nothing else in the module did this.

        Without it an event could reach ``live`` and stop there for ever:
        opening refuses anything but ``bidding_closed``, so the envelopes
        could never be opened and no award could be made.

        Closing early is a deliberate act and is logged with the shortfall,
        because bringing a deadline forward is the manipulation that
        procurement rules exist to prevent (CVC; GFR 2017 corrigendum
        practice). The rule is not enforced here — an SME buyer closing an
        internal event early is legitimate — but it is never silent.
        """
        for ev in self:
            if ev.state != "live":
                raise UserError(_(
                    "Only a live event can be closed. '%(name)s' is %(state)s.",
                    name=ev.display_name, state=ev.state))
            now = fields.Datetime.now()
            early = bool(ev.bid_close_datetime and now < ev.bid_close_datetime)
            shortfall = (
                int((ev.bid_close_datetime - now).total_seconds() // 60)
                if early else 0)
            ev.lot_ids.filtered(lambda l: l.state == "open").write({
                "state": "closed"})
            ev.write({"state": "bidding_closed"})
            ev.env["auction.audit"].log(
                action="bidding_closed", model=ev._name, res_id=ev.id,
                event_id=ev.id,
                detail={"by": ev.env.user.login,
                        "scheduled": str(ev.bid_close_datetime or ""),
                        "actual": str(now),
                        "early": early,
                        "minutes_early": shortfall})
            if early:
                # _message_log, not message_post, and inside a savepoint.
                #
                # message_post sends a notification, which on an instance
                # with no outgoing mail server configured -- which is most
                # SME instances on day one -- raises "Unable to send
                # message, please configure the sender's email address" and
                # rolls back the entire close. The event then stayed live
                # and the operator saw only a mail error.
                #
                # The audit entry above is the authoritative record. The
                # chatter note is a convenience and must never be able to
                # fail the operation it is describing. FIX-013.
                try:
                    with ev.env.cr.savepoint():
                        ev._message_log(body=_(
                            "Bidding closed %(mins)d minutes before the "
                            "published deadline of %(due)s, by %(user)s.",
                            mins=shortfall, due=ev.bid_close_datetime,
                            user=ev.env.user.name))
                except Exception:
                    _logger.warning(
                        "Chatter note failed on early close of event %s; the "
                        "audit entry is unaffected.", ev.id, exc_info=True)

    @api.model
    def _cron_close_expired_bidding(self):
        """Close events whose published deadline has passed.

        A cron is the right instrument here and was the wrong one for clock
        mechanisms: the one-minute floor cannot drive a 5–30 second extension
        tick, but it is perfectly adequate for a sealed close, where the
        deadline that counts is the one already stamped on each bid by
        ``clock_timestamp()`` at acceptance. This only moves the header
        state; it never decides whether an individual bid was in time.
        """
        candidates = self.sudo().search([
            ("state", "=", "live"),
            ("bid_close_datetime", "!=", False),
            ("bid_close_datetime", "<=", fields.Datetime.now()),
        ])
        # A lot may carry its own later deadline under a staggered close.
        # The event is not closed while any lot is still taking bids,
        # otherwise the header would say closed while a lot accepted a bid.
        due = candidates.filtered(
            lambda e: not e.lot_ids.filtered(lambda l: l.state == "open"))
        for ev in due:
            try:
                with self.env.cr.savepoint():
                    ev.action_close_bidding()
            except Exception:
                # One event failing to close must not strand the rest.
                _logger.exception("Scheduled close failed for event %s", ev.id)
        return len(due)

    def _stage_from_clear(self, bid, opening=None):
        """Stage an unsealed bid. Values are already on the ledger line."""
        self.ensure_one()
        Evaluation = self.env["auction.evaluation.line"].sudo()
        for bid_line in bid.line_ids:
            Evaluation.create({
                "event_id": self.id,
                "opening_id": opening.id if opening else False,
                "bid_id": bid.id,
                "bid_line_id": bid_line.id,
                "participant_id": bid.participant_id.id,
                "lot_id": bid.lot_id.id,
                "line_id": bid_line.line_id.id,
                "price_unit": bid_line.price_unit,
                "qty_offered": bid_line.qty_offered,
                "norm_value": bid_line.norm_value or bid_line.price_unit,
                "max_deviation_days": bid_line.max_deviation_days,
            })

    def action_request_opening(self):
        """Step one of two. The first opener registers their presence."""
        self.ensure_one()
        self._assert_may_open(self.env.user)
        if self.state != "bidding_closed":
            raise UserError(_(
                "Envelopes can only be opened once bidding has closed."))
        if self.comm_open_datetime and \
                fields.Datetime.now() < self.comm_open_datetime:
            raise UserError(_(
                "Opening is scheduled for %s. Envelopes cannot be opened "
                "before that time.") % self.comm_open_datetime)
        if self.opening_requested_by:
            raise UserError(_(
                "%s has already requested the opening. A second, different "
                "opener must now confirm it.")
                % self.opening_requested_by.name)

        self.sudo().write({
            "opening_requested_by": self.env.user.id,
            "opening_requested_on": fields.Datetime.now(),
        })
        self.env["auction.audit"].log(
            action="opening_requested", model=self._name, res_id=self.id,
            event_id=self.id, detail={"by": self.env.user.login})

    def action_confirm_opening(self):
        """Step two. A DIFFERENT opener confirms, and the envelopes open.

        Two distinct identities are the control. One person clicking twice
        is refused, which is the whole point.
        """
        self.ensure_one()
        self._assert_may_open(self.env.user)
        if not self.opening_requested_by:
            raise UserError(_(
                "The opening has not been requested yet. The first opener "
                "must request it before you can confirm."))
        if self.opening_requested_by == self.env.user:
            raise UserError(_(
                "You requested this opening. A second, different opener must "
                "confirm it. That is what makes it a dual control."))

        opening = self.env["auction.opening"].execute_dual(
            event=self,
            opener_a=self.opening_requested_by,
            opener_b=self.env.user,
        )
        # An opener holds observer rights on the event and nothing more, so
        # moving the header state is a sudo. The authority for the act was
        # established above; this is only the bookkeeping that follows it.
        self.sudo().write({"state": "under_evaluation"})
        return {
            "type": "ir.actions.act_window",
            "res_model": "auction.opening",
            "res_id": opening.id,
            "view_mode": "form",
        }

    def action_view_evaluation(self):
        """The opened prices. This is 'see the prices' step."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Opened Bids"),
            "res_model": "auction.evaluation.line",
            "domain": [("event_id", "=", self.id)],
            # pivot included: the menu action offers it and this one did
            # not, so the side-by-side comparison was unreachable from the
            # event itself -- which is where an evaluator starts.
            "view_mode": "list,pivot,form",
            "context": {"search_default_group_line": 1},
        }

    def action_create_award(self):
        """Start an award proposal from the opened bids.

        Minimal award: the winner is chosen by a person reading the opened
        prices. Computed ranking on landed cost is stage 10. The proposal is
        pre-filled with one row per line so the operator adjusts rather than
        types from nothing.
        """
        self.ensure_one()
        if self.state not in ("bidding_closed", "under_evaluation"):
            raise UserError(_(
                "An award can only be proposed once bidding has closed."))
        draft = self.award_ids.filtered(lambda a: a.state == "draft")
        if draft:
            return draft[0]._open_form()

        award = self.env["auction.award"].create({"event_id": self.id})
        self.write({"state": "under_evaluation"})
        self.env["auction.audit"].log(
            action="award_proposal_created", model="auction.award",
            res_id=award.id, event_id=self.id, detail={})
        return award._open_form()

    def action_view_awards(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Awards"),
            "res_model": "auction.award",
            "domain": [("event_id", "=", self.id)],
            "view_mode": "list,form",
            "context": {"default_event_id": self.id},
        }

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
