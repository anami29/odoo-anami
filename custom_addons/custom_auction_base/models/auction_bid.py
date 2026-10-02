# -*- coding: utf-8 -*-
"""Bid ledger and the acceptance critical path.

TSD-AUC-001 #5 and #7. This is the correctness core of the platform.

Three properties constrain every line in this file and are not open to
implementation judgement:

1. Bid acceptance is serialisable and correct under concurrency.
2. The ledger is append-only and tamper-evident.
3. The server clock is the sole authority for every time-dependent decision.

Two defects corrected in TSD v1.1 are implemented here and are easy to
reintroduce by habit:

  C-01  Server time is ``clock_timestamp()``, read AFTER the lock is
        acquired. ``now()`` returns TRANSACTION START time, so a bid that
        waited 2.8s on the lock would be stamped 2.8s in the past -- a late
        bid could be accepted and the earliest-timestamp tie-break could
        disagree with actual serialisation order.

  C-02  Retry lives at the REQUEST BOUNDARY on a fresh cursor. A
        serialisation failure aborts the whole transaction; ROLLBACK TO
        SAVEPOINT cannot recover it, so retrying inside cr.savepoint() only
        appears to work under read-committed and breaks silently the moment
        isolation is raised.
"""
import json
import logging
import random
import time

from odoo import _, api, fields, models, registry
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import float_compare

from ..services import chain as chain_svc
from ..services import crypto as crypto_svc
from ..services import registry as mech_registry

_logger = logging.getLogger(__name__)

#: Bounded wait, preferred over FOR UPDATE NOWAIT. Under 200 concurrent
#: bidders on one lot, NOWAIT converts contention into immediate client
#: failure and produces retry storms. TSD #5.2.
LOCK_TIMEOUT_MS = 3000
MAX_ATTEMPTS = 4
BACKOFF_BASE_MS = 50
BACKOFF_JITTER_MS = 40

#: PostgreSQL SQLSTATEs. The two classes behave differently and must be
#: handled separately -- TSD #5.2.
PG_LOCK_NOT_AVAILABLE = "55P03"
PG_SERIALIZATION_FAILURE = "40001"
PG_DEADLOCK_DETECTED = "40P01"
PG_RETRY_CODES = (PG_SERIALIZATION_FAILURE, PG_DEADLOCK_DETECTED)

REASON_NOT_OPEN = "not_open"
REASON_LATE = "late"
REASON_PAUSED = "paused"
REASON_EXCLUDED = "excluded"
REASON_SECURITY = "bid_security"


class BidRejected(UserError):
    """A business rejection. Not retried -- the transaction is intact."""

    def __init__(self, reason, message):
        self.reason = reason
        super().__init__(message)


def _pgcode(exc):
    return getattr(exc, "pgcode", None) or getattr(
        getattr(exc, "orig", None), "pgcode", None
    )


def _backoff(attempt):
    return (BACKOFF_BASE_MS * (2 ** attempt)
            + random.randint(0, BACKOFF_JITTER_MS)) / 1000.0


class AuctionBid(models.Model):
    """Append-only bid ledger.

    NOT inheriting mail.thread, and this is deliberate: mail.thread writes
    ``message_main_attachment_id`` on its own record and would raise against
    the guard below. Chatter, where needed, belongs on a sibling record.

    No stored computed field may be declared here either -- recomputation is
    a write.
    """

    _name = "auction.bid"
    _description = "Auction Bid (append-only ledger)"
    _order = "lot_id, chain_seq"
    _rec_name = "reference"

    reference = fields.Char(required=True, readonly=True, copy=False, index=True)
    event_id = fields.Many2one(
        "auction.event", required=True, readonly=True, index=True, ondelete="restrict")
    lot_id = fields.Many2one(
        "auction.lot", required=True, readonly=True, index=True, ondelete="restrict")
    participant_id = fields.Many2one(
        "auction.participant", required=True, readonly=True, index=True,
        ondelete="restrict")
    round_no = fields.Integer(required=True, readonly=True, default=1)

    # -- chain ---------------------------------------------------------------
    chain_seq = fields.Integer(required=True, readonly=True, index=True)
    payload_version = fields.Integer(required=True, readonly=True,
                                     default=chain_svc.PAYLOAD_VERSION)
    payload_digest = fields.Char(size=64, required=True, readonly=True)
    prev_hash = fields.Char(size=64, required=True, readonly=True)
    record_hash = fields.Char(size=64, required=True, readonly=True, index=True)

    # -- authority -----------------------------------------------------------
    server_ts = fields.Datetime(
        required=True, readonly=True, index=True,
        help="Server receipt time from clock_timestamp(), read under the lot "
             "lock. No client-supplied time is accepted for any purpose.")
    idempotency_key = fields.Char(size=64, required=True, readonly=True, index=True)

    # -- sealed envelope -----------------------------------------------------
    envelope = fields.Binary(
        attachment=False, readonly=True,
        groups="custom_auction_base.group_auction_opener",
        help="AES-256-GCM ciphertext. Never decrypted in place; never read "
             "outside auction.opening.execute.")
    envelope_nonce = fields.Binary(
        attachment=False, readonly=True,
        groups="custom_auction_base.group_auction_opener")

    state = fields.Selection(
        [("active", "Active"),
         ("superseded", "Superseded"),
         ("withdrawn", "Withdrawn"),
         ("void", "Void"),
         ("void_corrigendum", "Void (corrigendum)"),
         ("excluded", "Excluded"),
         ("late_rejected", "Rejected (late)"),
         ("non_responsive", "Non-responsive")],
        required=True, readonly=True, default="active", index=True)
    supersedes_id = fields.Many2one("auction.bid", readonly=True, ondelete="restrict")

    source = fields.Selection(
        [("portal", "Portal"),
         ("spreadsheet", "Spreadsheet"),
         ("offline_entry", "Offline entry by buyer"),
         ("proxy", "Proxy"),
         ("clock_accept", "Clock acceptance")],
        required=True, readonly=True, default="portal")
    offline_justification = fields.Text(readonly=True)
    client_ip = fields.Char(readonly=True)
    user_agent = fields.Char(readonly=True)

    line_ids = fields.One2many("auction.bid.line", "bid_id", readonly=True)

    _sql_constraints = [
        ("bid_chain_uq", "unique(lot_id, chain_seq)",
         "Ledger sequence must be unique within a lot."),
        ("bid_hash_uq", "unique(record_hash)",
         "Ledger record hash must be unique."),
        ("bid_idem_uq", "unique(event_id, participant_id, idempotency_key)",
         "Duplicate submission key."),
        ("bid_chain_seq_positive", "CHECK(chain_seq > 0)",
         "Chain sequence must be positive."),
        ("bid_hash_len",
         "CHECK(char_length(record_hash) = 64 AND char_length(prev_hash) = 64)",
         "Hashes must be 64 hex characters."),
    ]

    # ------------------------------------------------------------------
    # Append-only enforcement -- TSD #7.1
    # ------------------------------------------------------------------
    def write(self, vals):
        """Unconditionally refused.

        No context key, no group check, no SUPERUSER_ID exception. Do not add
        an escape hatch here; the absence of one IS the property. State
        transitions go through _set_state, which is the only mutation path.
        """
        raise AccessError(_(
            "The bid ledger is append-only. Bids cannot be modified after "
            "submission. A correction is recorded as a new bid that "
            "supersedes the previous one."))

    def unlink(self):
        raise AccessError(_(
            "The bid ledger is append-only. Bids cannot be deleted."))

    def _set_state(self, new_state, reason=None):
        """The ONLY mutation path. Raw SQL against a three-column allowlist.

        Raw SQL bypasses the ORM cache, so invalidate_recordset is mandatory
        -- without it a same-transaction read returns the pre-update value.
        The database is correct and the application is not, and only a test
        that reads back inside the same transaction catches it.
        """
        allowed = {"active", "superseded", "withdrawn", "void",
                   "void_corrigendum", "excluded", "late_rejected",
                   "non_responsive"}
        if new_state not in allowed:
            raise ValidationError(
                _("Ledger state %s is not permitted.") % new_state)
        if not self:
            return
        self.env.cr.execute(
            "UPDATE auction_bid SET state = %s WHERE id IN %s",
            (new_state, tuple(self.ids)),
        )
        self.invalidate_recordset(["state"])
        for bid in self:
            self.env["auction.audit"].log(
                action="bid_state_change", model="auction.bid", res_id=bid.id,
                event_id=bid.event_id.id,
                detail={"new_state": new_state, "reason": reason},
            )

    # ------------------------------------------------------------------
    # Request boundary -- retry lives HERE, on a fresh cursor (C-02)
    # ------------------------------------------------------------------
    @api.model
    def submit_request(self, vals):
        """Entry point from the controller.

        Each attempt runs on its own cursor and its own transaction. A
        serialisation failure aborts the transaction outright, so retrying it
        inside a savepoint on the same cursor cannot work; every subsequent
        statement on an aborted cursor raises InFailedSqlTransaction.
        """
        dbname = self.env.cr.dbname
        uid, context = self.env.uid, dict(self.env.context)
        last_error = None

        for attempt in range(MAX_ATTEMPTS):
            try:
                with registry(dbname).cursor() as cr:
                    env = api.Environment(cr, uid, context)
                    receipt = env["auction.bid"].submit(vals)
                    # Context manager commits on clean exit, which is what
                    # fires cr.postcommit -- bus publication and notification
                    # enqueue. Never inside the lock.
                    return receipt
            except BidRejected:
                # Business rejection. The transaction is intact and the late
                # bid audit row was written on its own cursor. Not retried.
                raise
            except Exception as exc:
                code = _pgcode(exc)
                if code in PG_RETRY_CODES or code == PG_LOCK_NOT_AVAILABLE:
                    last_error = exc
                    _logger.info(
                        "auction.bid: contention on attempt %d/%d (%s)",
                        attempt + 1, MAX_ATTEMPTS, code)
                    time.sleep(_backoff(attempt))
                    continue
                raise

        _logger.warning("auction.bid: retry exhausted: %s", last_error)
        raise UserError(_(
            "Your bid could not be accepted because the system was busy. "
            "NOTHING was recorded. Please submit again."))

    # ------------------------------------------------------------------
    # Model layer
    # ------------------------------------------------------------------
    @api.model
    def submit(self, vals):
        """Phase A -- no lock held."""
        participant = self._authorise(vals)
        self._rate_limit(participant)

        existing = self._idempotent_lookup(vals, participant)
        if existing:
            return existing._receipt()          # replay, no side effects

        return self._submit_locked(vals, participant)

    @api.model
    def _authorise(self, vals):
        """Per-event role check -- AMD-AUC-001 AMD-07.

        Roles are DATA on auction.participant.user, never res.groups: the same
        person may legitimately be authorised bidder on one event and observer
        on another, and a group cannot express that. Group membership is
        necessary, never sufficient.
        """
        Participant = self.env["auction.participant"]
        participant = Participant.browse(vals.get("participant_id")).exists()
        if not participant:
            raise AccessError(_("Unknown participant."))

        commercial = self.env.user.partner_id.commercial_partner_id
        if participant.partner_id.commercial_partner_id != commercial:
            raise AccessError(_("You may not bid on behalf of another bidder."))

        link = participant.user_ids.filtered(lambda u: u.user_id == self.env.user)
        if not link or link[0].role != "authorised_bidder":
            raise AccessError(_(
                "Your user is not designated as the authorised bidder for "
                "this event. Ask your document coordinator to assign the role."))

        if participant.state in ("excluded", "withdrawn", "disqualified"):
            raise BidRejected(REASON_EXCLUDED, _(
                "You are not able to bid on this event."))

        if not participant._bid_security_permits_bidding():
            raise BidRejected(REASON_SECURITY, _(
                "Bid security must be declared before you can submit. "
                "Verification by our finance team may follow afterwards."))
        return participant

    @api.model
    def _rate_limit(self, participant):
        """Per-participant submission rate limit -- BR-SEC-009."""
        window = fields.Datetime.subtract(fields.Datetime.now(), seconds=10)
        recent = self.search_count([
            ("participant_id", "=", participant.id),
            ("create_date", ">=", window),
        ])
        if recent >= 10:
            raise UserError(_(
                "Too many submissions in a short period. Please wait a "
                "moment and try again."))

    @api.model
    def _idempotent_lookup(self, vals, participant):
        """Client-generated key deduplication -- BR-BID-006.

        A retry must not create a second ledger record. The unique constraint
        is the backstop; this lookup is the mechanism.
        """
        key = vals.get("idempotency_key")
        if not key:
            raise UserError(_("Submission key missing."))
        return self.search([
            ("event_id", "=", vals.get("event_id")),
            ("participant_id", "=", participant.id),
            ("idempotency_key", "=", key),
        ], limit=1)

    @api.model
    def _submit_locked(self, vals, participant):
        """Phase B -- the lot row lock is held for the remainder.

        The critical section contains no HTTP call, no mail dispatch, no
        attachment write, no subprocess and no sleep. Anything added here that
        performs I/O is a defect irrespective of test results, because its
        failure mode under load is not reproducible in a unit test.
        """
        cr = self.env.cr
        self.env.flush_all()                    # ORM cache to DB before raw SQL

        cr.execute("SET LOCAL lock_timeout = %s", ("%dms" % LOCK_TIMEOUT_MS,))
        cr.execute(
            """
            SELECT id, close_datetime, state, chain_seq, chain_head_hash,
                   event_id, precision_digits
              FROM auction_lot
             WHERE id = %s
               FOR UPDATE
            """,
            (vals["lot_id"],),
        )
        lot_row = cr.dictfetchone()
        if not lot_row:
            raise UserError(_("Unknown lot."))

        # C-01: clock_timestamp(), NOT now(). now() is transaction start time.
        cr.execute("SELECT (clock_timestamp() AT TIME ZONE 'UTC')")
        server_ts = cr.fetchone()[0]

        if lot_row["state"] == "paused":
            raise BidRejected(REASON_PAUSED, _(
                "This event is paused. The clock is frozen and bids cannot "
                "be submitted until it resumes."))
        if lot_row["state"] != "open":
            raise BidRejected(REASON_NOT_OPEN, _(
                "This lot is not open for bidding."))
        if server_ts >= lot_row["close_datetime"]:
            self._audit_late(vals, lot_row, server_ts)
            raise BidRejected(REASON_LATE, _(
                "Bidding closed at %(close)s (server time). Your submission "
                "reached us at %(got)s and cannot be accepted.",
                close=lot_row["close_datetime"], got=server_ts))

        lot = self.env["auction.lot"].browse(lot_row["id"])
        rules = lot._resolve_rules()
        strategy = mech_registry.get(lot.event_id.mechanism)

        prior = self._prior_active(participant, lot)
        vals = dict(vals, _has_prior_submission=bool(prior))

        lines = self._validate_lines(vals, lot, rules, strategy)
        errors = strategy.validate_entry(vals, rules, None)
        if errors:
            raise ValidationError("\n".join("%s: %s" % e for e in errors))

        seq = (lot_row["chain_seq"] or 0) + 1
        precision = lot_row["precision_digits"] or 6
        payload = self._canonical_payload(vals, lines, server_ts, participant, lot)
        canonical = chain_svc.canonical_payload(payload, precision=precision)
        digest = chain_svc.payload_digest(canonical)
        prev_hash = lot_row["chain_head_hash"] or chain_svc.ZERO64
        rec_hash = chain_svc.record_hash(
            prev_hash, digest, seq, server_ts.isoformat())

        envelope = nonce = None
        if strategy.sealed:
            dek = lot.event_id._dek()
            aad = crypto_svc.build_aad(lot.event_id.id, lot.id, participant.id, seq)
            envelope, nonce = crypto_svc.seal(canonical, dek, aad)

        bid = self._insert_ledger(
            vals, lot, participant, seq, digest, prev_hash, rec_hash,
            envelope, nonce, server_ts, lines, strategy)

        if prior:
            prior._set_state("superseded", reason="replaced by %s" % bid.reference)

        cr.execute(
            "UPDATE auction_lot SET chain_seq = %s, chain_head_hash = %s "
            " WHERE id = %s",
            (seq, rec_hash, lot.id),
        )
        lot.invalidate_recordset(["chain_seq", "chain_head_hash"])

        # Phase C -- deferred. Fires only on the real commit at the boundary.
        cr.postcommit.add(lambda: self._notify_receipt(bid.id))
        return bid._receipt()

    # ------------------------------------------------------------------
    def _prior_active(self, participant, lot):
        return self.search([
            ("lot_id", "=", lot.id),
            ("participant_id", "=", participant.id),
            ("state", "=", "active"),
        ], limit=1)

    def _validate_lines(self, vals, lot, rules, strategy):
        """Field-level validation reporting EVERY failure, not the first.

        BR-BID-010: a bidder correcting one error at a time across four
        round trips is how a submission window gets missed.
        """
        errors, lines = [], []
        supplied = {l["line_id"]: l for l in vals.get("lines", [])}

        for line in lot.line_ids:
            given = supplied.get(line.id)
            if not given:
                if lot.all_or_nothing:
                    errors.append(("line_%d" % line.id, _(
                        "This lot must be bid in full. '%s' is missing.")
                        % line.name))
                continue
            price = given.get("price_unit")
            if price is None or price <= 0:
                errors.append(("line_%d" % line.id,
                               _("A positive price is required.")))
                continue
            qty = given.get("qty_offered") or line.product_qty
            if not rules.partial_qty and qty != line.product_qty:
                errors.append(("line_%d" % line.id, _(
                    "Partial quantities are not accepted on this event. "
                    "Offer the full quantity of %s.") % line.product_qty))
            if lot.ceiling_price and strategy.direction == "reverse" \
                    and price > lot.ceiling_price:
                # Message does not disclose the ceiling where undisclosed.
                errors.append(("line_%d" % line.id, _(
                    "Your price exceeds the maximum acceptable for this line.")))
            if lot.reserve_price and strategy.direction == "forward" \
                    and price < lot.reserve_price and lot.disclose_limit:
                errors.append(("line_%d" % line.id, _(
                    "Your bid is below the reserve of %s.") % lot.reserve_price))
            schedule, deviation = self._validate_schedule(
                line, given.get("schedule") or [], errors)

            lines.append({
                "line_id": line.id,
                "price_unit": price,
                "qty_offered": qty,
                "norm_value": price,     # landed cost normalisation: stage 10
                "schedule": schedule,
                "max_deviation_days": deviation,
            })

        if not lines:
            errors.append(("bid", _("No priced lines were submitted.")))
        if errors:
            raise ValidationError("\n".join("%s: %s" % e for e in errors))
        return lines

    def _validate_schedule(self, line, offered, errors):
        """Validate an offered delivery schedule against the required one.

        Returns ``(rows, max_deviation_days)``. An empty offer means the
        bidder accepted the required schedule as stated, which is the normal
        case and is not an error.
        """
        if not offered:
            return [], 0

        rows, worst = [], 0
        total = 0.0
        required = {s.id: s for s in line.schedule_ids}

        for index, item in enumerate(offered):
            qty = item.get("quantity")
            when = item.get("offered_date")
            if not qty or qty <= 0:
                errors.append(("line_%d" % line.id, _(
                    "Delivery tranche %s has no quantity.") % (index + 1)))
                continue
            if not when:
                errors.append(("line_%d" % line.id, _(
                    "Delivery tranche %s has no date.") % (index + 1)))
                continue

            offered_date = fields.Date.to_date(when)
            tranche = required.get(item.get("schedule_id"))
            # Where the bidder proposed their own shape, measure against the
            # final required date rather than refusing the bid outright.
            benchmark = (tranche.required_by if tranche
                         else (line.schedule_ids.sorted("required_by")[-1].required_by
                               if line.schedule_ids else line.required_by))
            deviation = (offered_date - benchmark).days if benchmark else 0
            worst = max(worst, deviation)
            total += qty

            rows.append({
                "schedule_id": tranche.id if tranche else False,
                "sequence": (index + 1) * 10,
                "quantity": qty,
                "offered_date": offered_date,
                "deviation_days": deviation,
            })

        if rows and float_compare(total, line.product_qty,
                                  precision_digits=6) != 0:
            errors.append(("line_%d" % line.id, _(
                "Your delivery schedule for '%(line)s' adds up to %(total)s "
                "but the line quantity is %(qty)s.",
                line=line.name, total=total, qty=line.product_qty)))

        return rows, worst

    def _canonical_payload(self, vals, lines, server_ts, participant, lot):
        return {
            "event": lot.event_id.id,
            "lot": lot.id,
            "participant": participant.id,
            "round": vals.get("round_no", 1),
            "currency": vals.get("currency_id"),
            "validity_date": str(vals.get("validity_date") or ""),
            "lines": [
                {"line": l["line_id"],
                 "price_unit": l["price_unit"],
                 "qty_offered": l["qty_offered"],
                 # PAYLOAD_VERSION 2 added the schedule. Older records keep
                 # their stored digest, so verification is unaffected.
                 "schedule": [
                     {"required": sc["schedule_id"] or 0,
                      "qty": sc["quantity"],
                      "date": str(sc["offered_date"])}
                     for sc in l.get("schedule") or []
                 ]}
                for l in sorted(lines, key=lambda x: x["line_id"])
            ],
            "server_ts": server_ts.isoformat(),
        }

    def _insert_ledger(self, vals, lot, participant, seq, digest, prev_hash,
                       rec_hash, envelope, nonce, server_ts, lines, strategy):
        """Create through super() -- write() is blocked but create() is not."""
        sealed = strategy.sealed
        bid = super(AuctionBid, self.sudo()).create({
            # sudo() on the SEQUENCE, not just on the create. A portal bidder
            # has no ACL on ir.sequence, and this dict is evaluated in the
            # caller's environment before the sudoed create ever runs, so
            # without this every portal submission died with AccessError on
            # 'Sequence'. It survived because no portal user had yet reached
            # this line: the portal bid form is stage 13, and internal staff
            # doing offline entry hold sequence access through
            # base.group_user. FIX-011.
            "reference": self.env["ir.sequence"].sudo().next_by_code(
                "auction.bid") or "/",
            "event_id": lot.event_id.id,
            "lot_id": lot.id,
            "participant_id": participant.id,
            "round_no": vals.get("round_no", 1),
            "chain_seq": seq,
            "payload_digest": digest,
            "prev_hash": prev_hash,
            "record_hash": rec_hash,
            "server_ts": server_ts,
            "idempotency_key": vals["idempotency_key"],
            "envelope": envelope,
            "envelope_nonce": nonce,
            "state": "active",
            "source": vals.get("source", "portal"),
            "offline_justification": vals.get("offline_justification"),
            "client_ip": vals.get("client_ip"),
            "user_agent": vals.get("user_agent"),
            "line_ids": [
                (0, 0, {
                    "line_id": l["line_id"],
                    # For a sealed event the clear values are NOT stored;
                    # they live only inside the envelope until opening.
                    "price_unit": None if sealed else l["price_unit"],
                    "qty_offered": l["qty_offered"],
                    "norm_value": None if sealed else l["norm_value"],
                    # Dates are not commercially sensitive in the way price
                    # is, and evaluation needs them to assess feasibility
                    # before the commercial opening. They stay in clear.
                    "max_deviation_days": l.get("max_deviation_days", 0),
                    "schedule_ids": [
                        (0, 0, {
                            "schedule_id": sc["schedule_id"],
                            "sequence": sc["sequence"],
                            "quantity": sc["quantity"],
                            "offered_date": sc["offered_date"],
                            "deviation_days": sc["deviation_days"],
                        }) for sc in l.get("schedule") or []
                    ],
                })
                for l in lines
            ],
        })
        self.env["auction.audit"].log(
            action="bid_submitted", model="auction.bid", res_id=bid.id,
            event_id=lot.event_id.id,
            detail={"chain_seq": seq, "sealed": sealed,
                    "record_hash": rec_hash},
        )
        return bid

    def _audit_late(self, vals, lot_row, server_ts):
        """Write the late-bid record on a SEPARATE cursor.

        The business transaction is about to be rolled back by the rejection,
        and this is precisely the moment the audit entry has most value.
        """
        self.env["auction.audit"].log_isolated(
            action="bid_rejected_late",
            model="auction.lot", res_id=lot_row["id"],
            event_id=lot_row["event_id"],
            detail={"participant_id": vals.get("participant_id"),
                    "server_ts": server_ts.isoformat(),
                    "close_datetime": lot_row["close_datetime"].isoformat()},
        )

    def _notify_receipt(self, bid_id):
        """Post-commit hook. Never called inside the lock."""
        _logger.info("auction.bid: receipt notification queued for %s", bid_id)

    def _receipt(self):
        """What the bidder gets back and screenshots. BR-BID-011."""
        self.ensure_one()
        return {
            "reference": self.reference,
            "server_ts": fields.Datetime.to_string(self.server_ts),
            "timezone": "UTC",
            "chain_seq": self.chain_seq,
            "record_hash": self.record_hash,
            "sealed": bool(self.envelope),
            "lines": len(self.line_ids),
        }

    # ------------------------------------------------------------------
    def verify_chain(self):
        """Walk this lot's ledger and verify integrity. BR-SLD-006."""
        lots = self.mapped("lot_id") or self.env["auction.lot"].browse([])
        report = {}
        for lot in lots:
            records = self.search([("lot_id", "=", lot.id)], order="chain_seq")
            ok, findings = chain_svc.verify([{
                "chain_seq": r.chain_seq,
                "prev_hash": r.prev_hash,
                "payload_digest": r.payload_digest,
                "record_hash": r.record_hash,
                "server_ts_iso": r.server_ts.isoformat(),
            } for r in records])
            report[lot.id] = {"ok": ok, "records": len(records),
                              "findings": findings}
        return report


class AuctionBidLine(models.Model):
    _name = "auction.bid.line"
    _description = "Auction Bid Line (append-only)"
    _order = "bid_id, line_id"

    bid_id = fields.Many2one("auction.bid", required=True, readonly=True,
                             index=True, ondelete="cascade")
    line_id = fields.Many2one("auction.line", required=True, readonly=True,
                              index=True, ondelete="restrict")

    # digits= forces PostgreSQL numeric. A bare fields.Float() gives
    # double precision, and mixed-type comparison produces non-deterministic
    # ordering at the sixth decimal -- flaky tie-break tests first, a disputed
    # award eventually. TSD #3.3, asserted at install by hooks.py.
    price_unit = fields.Float(digits=(18, 6), readonly=True)
    qty_offered = fields.Float(digits=(18, 6), readonly=True)
    price_base = fields.Float(digits=(18, 6), readonly=True)
    landed_value = fields.Float(digits=(18, 6), readonly=True)
    norm_value = fields.Float(digits=(18, 6), readonly=True, index=True)
    composite_score = fields.Float(digits=(18, 6), readonly=True)

    # Offered delivery schedule. Empty means the bidder accepted the
    # required schedule as stated, or the line carries no schedule.
    schedule_ids = fields.One2many(
        "auction.bid.line.schedule", "bid_line_id", readonly=True)
    max_deviation_days = fields.Integer(
        readonly=True,
        help="Worst slippage across the offered tranches, frozen at "
             "submission. Stage 10 feeds this into landed cost.")

    _sql_constraints = [
        ("bid_line_qty_positive", "CHECK(qty_offered > 0)",
         "Offered quantity must be positive."),
    ]

    def write(self, vals):
        raise AccessError(_("Bid lines are append-only."))

    def unlink(self):
        raise AccessError(_("Bid lines are append-only."))
