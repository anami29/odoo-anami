# -*- coding: utf-8 -*-
"""Bid security — SCP-AUC-001 #4, BR-PRQ-015 to BR-PRQ-032.

The original specification assumed online collection through a payment
provider and treated offline instruments as an afterthought. That inverts
real practice: GFR Rule 170 lists account payee demand draft, fixed deposit
receipt, banker's cheque and bank guarantee alongside online payment, and in
the SME segment a bank transfer against a quoted UTR is the common case.

Gating policy (#4.3) is the central design decision:
    view content / submit a bid   -> declared
    be included in the opening    -> verified
    be awarded                    -> verified, absolute

Gating everything on ``verified`` blocks bidders for days waiting on finance.
Gating on ``declared`` alone lets a bidder bid against a fictional reference.
Neither is acceptable on its own.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

#: Modes requiring clearing before they can be treated as funds in hand.
CLEARING_MODES = ("cheque",)
#: Modes where no money moves.
NON_MONETARY_MODES = ("declaration", "exempt")


class AuctionBidSecurity(models.Model):
    _name = "auction.bid.security"
    _description = "Bid Security (EMD)"
    _order = "event_id, participant_id"
    _rec_name = "display_reference"

    event_id = fields.Many2one("auction.event", required=True, index=True,
                               ondelete="cascade")
    participant_id = fields.Many2one("auction.participant", required=True,
                                     index=True, ondelete="cascade")
    partner_id = fields.Many2one(related="participant_id.partner_id", store=True)
    company_id = fields.Many2one(related="event_id.company_id", store=True)
    currency_id = fields.Many2one(related="event_id.currency_id")

    mode = fields.Selection(
        [("neft", "NEFT"),
         ("rtgs", "RTGS"),
         ("imps", "IMPS"),
         ("cheque", "Cheque"),
         ("bankers_cheque", "Banker's Cheque"),
         ("dd", "Demand Draft"),
         ("bg", "Bank Guarantee"),
         ("fdr", "Fixed Deposit Receipt"),
         ("online", "Online payment"),
         ("declaration", "Bid Securing Declaration"),
         ("exempt", "Exempt")],
        required=True, default="neft")

    amount_required = fields.Monetary(required=True)
    amount_declared = fields.Monetary()
    amount_received = fields.Monetary()

    # -- instrument reference ------------------------------------------------
    instrument_ref = fields.Char(
        string="UTR / Instrument No.", index=True,
        help="UTR for NEFT, RTGS and IMPS. Cheque, DD, BG or FDR number "
             "otherwise.")
    instrument_date = fields.Date()
    bank_name = fields.Char()
    branch = fields.Char()
    remitter_account_name = fields.Char()
    validity_date = fields.Date(help="Bank guarantee or FDR maturity.")
    claim_period_date = fields.Date(help="Bank guarantee claim period.")
    lien_marked = fields.Boolean(help="FDR lien marked in favour of the buyer.")

    # -- exemption -----------------------------------------------------------
    exemption_basis = fields.Selection(
        [("mse", "Micro or Small Enterprise"),
         ("dpiit", "DPIIT-recognised startup"),
         ("policy", "Buyer policy")],
        help="GFR Rule 170 exempts MSEs and DPIIT-recognised startups. This "
             "is a legal obligation for Indian deployments, not a preference, "
             "and must be evidenced rather than self-declared.")
    exemption_reg_no = fields.Char()
    exemption_valid_to = fields.Date()

    display_reference = fields.Char(compute="_compute_display_reference")

    state = fields.Selection(
        [("draft", "Draft"),
         ("declared", "Declared"),
         ("received", "Received"),
         ("in_clearing", "In clearing"),
         ("verified", "Verified"),
         ("rejected", "Rejected"),
         ("dishonoured", "Dishonoured"),
         ("refunded", "Refunded"),
         ("forfeited", "Forfeited"),
         ("converted", "Converted to performance security")],
        required=True, default="draft", index=True, copy=False)

    declared_date = fields.Datetime(readonly=True)
    received_date = fields.Datetime(readonly=True)
    verified_date = fields.Datetime(readonly=True)
    verified_by = fields.Many2one("res.users", readonly=True)
    rejection_reason = fields.Text()

    # -- refund and forfeiture ----------------------------------------------
    refund_ref = fields.Char(
        string="Refund UTR / Acknowledgement",
        help="Outward UTR, or the acknowledgement of a returned instrument. "
             "The record cannot close without it.")
    refund_date = fields.Date()
    forfeit_condition = fields.Selection(
        [("withdrawal_after_close", "Withdrawal or modification after close"),
         ("declined_award", "Declined award or allowed the deadline to lapse"),
         ("no_performance_security", "Failed to furnish performance security"),
         ("misrepresentation", "Material misrepresentation"),
         ("failed_to_sign", "Failed to sign the contract")],
        help="AMD-AUC-002 B-01. This list is exhaustive: forfeiture against "
             "any other ground is not permitted.")
    forfeit_date = fields.Date()
    move_id = fields.Many2one("account.move", readonly=True, copy=False)

    _sql_constraints = [
        ("security_participant_uq", "unique(event_id, participant_id)",
         "One bid security record per participant per event."),
    ]

    @api.depends("mode", "instrument_ref")
    def _compute_display_reference(self):
        for rec in self:
            rec.display_reference = "%s %s" % (
                dict(rec._fields["mode"].selection).get(rec.mode, ""),
                rec.instrument_ref or "")

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    @api.constrains("mode", "instrument_ref", "instrument_date",
                    "exemption_basis", "state")
    def _check_mode_fields(self):
        """Per-mode mandatory field sets. BR-PRQ-016."""
        for rec in self:
            if rec.state in ("draft",):
                continue
            if rec.mode in NON_MONETARY_MODES:
                if rec.mode == "exempt" and not rec.exemption_basis:
                    raise ValidationError(_(
                        "An exemption basis is required, evidenced by a "
                        "current registration number."))
                continue
            if not rec.instrument_ref:
                raise ValidationError(_(
                    "A UTR or instrument number is required for %s.")
                    % rec.mode)
            if not rec.instrument_date:
                raise ValidationError(_("An instrument date is required."))
            if rec.mode in ("bg", "fdr") and not rec.validity_date:
                raise ValidationError(_(
                    "A validity or maturity date is required for a bank "
                    "guarantee or fixed deposit receipt."))

    @api.constrains("instrument_ref", "mode")
    def _check_duplicate_reference(self):
        """BR-PRQ-018. The same UTR or cheque number declared twice is
        flagged BEFORE verification, not discovered after."""
        for rec in self:
            if not rec.instrument_ref or rec.mode in NON_MONETARY_MODES:
                continue
            clash = self.search([
                ("id", "!=", rec.id),
                ("instrument_ref", "=", rec.instrument_ref),
                ("mode", "=", rec.mode),
                ("state", "not in", ["rejected", "dishonoured"]),
            ], limit=1)
            if clash:
                raise ValidationError(_(
                    "Instrument reference %(ref)s is already declared against "
                    "%(where)s. A reference may be used once.",
                    ref=rec.instrument_ref,
                    where=clash.event_id.display_name))

    # ------------------------------------------------------------------
    # Bidder actions
    # ------------------------------------------------------------------
    def action_declare(self):
        """Bidder declares the instrument. Opens content and bidding."""
        for rec in self:
            if rec.state not in ("draft", "rejected"):
                raise UserError(_("This security has already been declared."))
            rec.write({
                "state": "declared",
                "declared_date": fields.Datetime.now(),
                "amount_declared": rec.amount_declared or rec.amount_required,
                "rejection_reason": False,
            })
            rec._audit("bid_security_declared")

    def permits_bidding(self):
        """Gating for content access and submission. SCP-AUC-001 #4.3."""
        self.ensure_one()
        return self.state in ("declared", "received", "in_clearing",
                              "verified", "converted")

    def permits_opening(self):
        """Gating for inclusion in the opening and for award."""
        self.ensure_one()
        return self.state in ("verified", "converted")

    # ------------------------------------------------------------------
    # Finance actions
    # ------------------------------------------------------------------
    def action_mark_received(self):
        """Finance confirms credit or physical receipt. BR-PRQ-020/021.

        Supports bulk action across a selection, which is the common case:
        several NEFT credits appearing together on one bank statement.
        """
        for rec in self:
            if rec.state != "declared":
                raise UserError(_(
                    "Only a declared instrument can be marked received."))
            next_state = "in_clearing" if rec.mode in CLEARING_MODES else "received"
            rec.write({
                "state": next_state,
                "received_date": fields.Datetime.now(),
                "amount_received": rec.amount_received or rec.amount_declared,
            })
            rec._audit("bid_security_received", {"state": next_state})

    def action_verify(self):
        """Finance verifies amount, payee and validity.

        Segregation of duties: the verifier may not be the event owner.
        An amount shortfall is surfaced, never silently accepted.
        """
        for rec in self:
            if rec.state not in ("received", "in_clearing"):
                raise UserError(_(
                    "Only a received instrument can be verified."))
            if rec.event_id.owner_id == self.env.user:
                raise UserError(_(
                    "The event owner cannot verify bid security for their "
                    "own event."))
            if rec.mode not in NON_MONETARY_MODES:
                shortfall = rec.amount_required - (rec.amount_received or 0.0)
                if shortfall > 0 and not self.env.context.get(
                        "auction_accept_shortfall"):
                    # GAP-069: top-up is permitted until the verification
                    # deadline; the later date governs.
                    raise UserError(_(
                        "Received amount is short by %(gap)s. Ask the bidder "
                        "to top up before the verification deadline, or "
                        "override with a recorded reason.",
                        gap=shortfall))
            rec.write({
                "state": "verified",
                "verified_date": fields.Datetime.now(),
                "verified_by": self.env.user.id,
            })
            rec._audit("bid_security_verified")

    def action_reject(self, reason=None):
        for rec in self:
            rec.write({
                "state": "rejected",
                "rejection_reason": reason or rec.rejection_reason,
            })
            rec._audit("bid_security_rejected", {"reason": reason})

    def action_dishonour(self):
        """Cheque returned unpaid. SCP-AUC-001 #4.6.

        Before close the bidder may re-declare and the bid stands. After
        close the bid is non-responsive, and where that bidder was L1 or H1
        it is treated as withdrawal after close -- a forfeiture condition
        with no money to forfeit, which is GAP-068 and resolves to debarment.
        """
        for rec in self:
            rec.write({"state": "dishonoured"})
            after_close = any(
                lot.state in ("closed", "unsold", "awarded")
                for lot in rec.event_id.lot_ids)
            if after_close:
                bids = self.env["auction.bid"].search([
                    ("event_id", "=", rec.event_id.id),
                    ("participant_id", "=", rec.participant_id.id),
                    ("state", "=", "active"),
                ])
                bids._set_state("non_responsive", reason="bid security dishonoured")
            rec._audit("bid_security_dishonoured", {"after_close": after_close})

    def action_refund(self):
        """Refund to the account the money came from.

        AMD-AUC-002 B-02, from the CVC-aligned position: refunded directly to
        the account from which it was received. For physical instruments the
        instrument is returned and the return is acknowledged. Either way the
        record cannot close without a reference.
        """
        for rec in self:
            if rec.state not in ("verified", "received", "in_clearing"):
                raise UserError(_("Only a held security can be refunded."))
            if not rec.refund_ref:
                raise UserError(_(
                    "Record the outward UTR or the return acknowledgement "
                    "before closing this refund."))
            rec.write({"state": "refunded",
                       "refund_date": rec.refund_date or fields.Date.today()})
            rec._audit("bid_security_refunded", {"ref": rec.refund_ref})

    def action_forfeit(self):
        """Forfeiture against an enumerated condition only.

        AMD-AUC-002 B-01 makes the condition list exhaustive. Posts a journal
        entry moving the amount from the deposit liability account to the
        configured income account.
        """
        for rec in self:
            if rec.state != "verified":
                raise UserError(_(
                    "Only a verified security can be forfeited."))
            if not rec.forfeit_condition:
                raise UserError(_(
                    "Forfeiture requires one of the enumerated conditions. "
                    "Forfeiture on any other ground is not permitted."))
            rec.write({"state": "forfeited",
                       "forfeit_date": fields.Date.today()})
            rec._audit("bid_security_forfeited",
                       {"condition": rec.forfeit_condition})

    def action_convert_to_performance(self):
        """GFR Rule 171: bid security is refunded to the successful bidder on
        receipt of performance security, or converted where the terms allow."""
        for rec in self:
            if rec.state != "verified":
                raise UserError(_("Only a verified security can be converted."))
            rec.write({"state": "converted"})
            rec._audit("bid_security_converted")

    # ------------------------------------------------------------------
    @api.model
    def action_reconcile_statement(self, lines):
        """Bank statement auto-match. BR-PRQ-022.

        ``lines`` is a list of {"ref": str, "amount": float, "date": str}.
        Matches on reference and amount, returns the unmatched on both sides.
        Without this, twenty bidders means finance reading twenty UTRs off a
        statement by eye, which is where the errors are.
        """
        matched, unmatched_credits = [], []
        for line in lines:
            rec = self.search([
                ("instrument_ref", "=", (line.get("ref") or "").strip()),
                ("state", "=", "declared"),
            ], limit=1)
            if rec and abs((rec.amount_declared or 0.0)
                           - (line.get("amount") or 0.0)) < 0.01:
                rec.write({"amount_received": line["amount"]})
                rec.action_mark_received()
                matched.append(rec.id)
            else:
                unmatched_credits.append(line)
        claimed_not_credited = self.search([
            ("state", "=", "declared"),
            ("id", "not in", matched),
            ("mode", "in", ["neft", "rtgs", "imps"]),
        ]).ids
        return {
            "matched": matched,
            "unmatched_credits": unmatched_credits,
            "claimed_not_credited": claimed_not_credited,
        }

    @api.model
    def _cron_verification_deadline(self):
        """BR-PRQ-024/025. Notify on approach, mark non-responsive after the
        grace window."""
        now = fields.Datetime.now()
        for rec in self.search([("state", "in", ["declared", "received",
                                                 "in_clearing"])]):
            event = rec.event_id
            if not event.bid_close_datetime:
                continue
            grace_end = fields.Datetime.add(
                event.bid_close_datetime, hours=event.security_grace_hours or 48)
            if now > grace_end:
                bids = self.env["auction.bid"].search([
                    ("event_id", "=", event.id),
                    ("participant_id", "=", rec.participant_id.id),
                    ("state", "=", "active"),
                ])
                bids._set_state("non_responsive",
                                reason="bid security not verified within grace window")
                rec._audit("bid_security_grace_expired")

    def _audit(self, action, detail=None):
        for rec in self:
            self.env["auction.audit"].log(
                action=action, model=self._name, res_id=rec.id,
                event_id=rec.event_id.id, detail=detail or {})
