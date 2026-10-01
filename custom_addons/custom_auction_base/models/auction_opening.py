# -*- coding: utf-8 -*-
"""Envelope opening ceremony — TSD-AUC-001 #8, BR-SLD-007 to BR-SLD-013."""
from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from ..services import crypto as crypto_svc
from ..services import chain as chain_svc


class AuctionOpening(models.Model):
    _name = "auction.opening"
    _description = "Envelope Opening"
    _order = "id desc"

    event_id = fields.Many2one("auction.event", required=True, readonly=True,
                               index=True, ondelete="restrict")
    envelope_type = fields.Selection(
        [("technical", "Technical"), ("commercial", "Commercial")],
        required=True, readonly=True, default="commercial")
    opener_a_id = fields.Many2one("res.users", required=True, readonly=True)
    opener_b_id = fields.Many2one("res.users", required=True, readonly=True)
    opened_at = fields.Datetime(required=True, readonly=True)
    key_fingerprint = fields.Char(readonly=True)
    envelopes_opened = fields.Integer(readonly=True)
    envelopes_withheld = fields.Integer(
        readonly=True,
        help="Technically disqualified bidders whose commercial envelope was "
             "never decrypted. BR-MCH-009.")
    minutes = fields.Html(readonly=True)
    client_ip = fields.Char(readonly=True)

    def write(self, vals):
        raise AccessError(_("An opening record is immutable."))

    def unlink(self):
        raise AccessError(_("An opening record is immutable."))

    @api.model
    def execute_dual(self, event, opener_a, opener_b, envelope_type="commercial"):
        """Open under dual authorisation.

        The earlier ``execute(event_id, share_a, share_b)`` could never
        succeed: it expected two key shares, and nothing in the module ever
        produced them. ``_generate_dek`` wraps the data key under the KEK and
        does not split it. This method is the honest version of that control
        for the SME profile.

        The control here is that two DIFFERENT authorised people acted, and
        that both identities are recorded in an immutable record. The key
        itself is recovered from the KEK server side. Split-key custody,
        where neither opener alone can reconstruct the key, is the full
        profile; the primitives are written and tested in services/crypto.py
        and are not wired here.
        """
        if opener_a == opener_b:
            raise UserError(_("Two distinct openers are required."))
        for user in (opener_a, opener_b):
            if not user.has_group("custom_auction_base.group_auction_opener"):
                raise AccessError(_(
                    "%s is not an authorised envelope opener.") % user.name)

        scheduled = (event.comm_open_datetime if envelope_type == "commercial"
                     else event.tech_open_datetime)
        if scheduled and fields.Datetime.now() < scheduled:
            raise UserError(_(
                "Opening is scheduled for %s and cannot be brought forward.")
                % scheduled)

        existing = self.search([("event_id", "=", event.id),
                                ("envelope_type", "=", envelope_type)], limit=1)
        if existing:
            raise UserError(_(
                "These envelopes were already opened on %s. An opening "
                "happens once.") % existing.opened_at)

        dek = event._dek()
        if crypto_svc.fingerprint(dek) != event.dek_fingerprint:
            raise UserError(_(
                "The recovered key does not match this event. Check that "
                "AUCTION_KEK is the same key the event was published under."))

        record = super(AuctionOpening, self.sudo()).create({
            "event_id": event.id,
            "envelope_type": envelope_type,
            "opener_a_id": opener_a.id,
            "opener_b_id": opener_b.id,
            "opened_at": fields.Datetime.now(),
            "key_fingerprint": crypto_svc.fingerprint(dek),
            "envelopes_opened": 0,
            "envelopes_withheld": 0,
        })

        opened = withheld = 0
        Bid = self.env["auction.bid"].sudo()
        for bid in Bid.search([("event_id", "=", event.id),
                               ("state", "=", "active")]):
            if envelope_type == "commercial" and \
                    event.structure == "two_envelope" and \
                    not bid.participant_id.technically_qualified:
                # Never decrypted, and the fact is recorded. BR-MCH-009.
                withheld += 1
                continue
            if not bid.envelope:
                # Open event: values are already in clear on the ledger.
                event._stage_from_clear(bid, record)
                opened += 1
                continue
            aad = crypto_svc.build_aad(
                event.id, bid.lot_id.id, bid.participant_id.id, bid.chain_seq)
            plaintext = crypto_svc.open_envelope(
                bid.envelope, bid.envelope_nonce, dek, aad)
            event._stage_evaluation_values(bid, plaintext, opening=record)
            opened += 1

        record._finalise(opened, withheld)
        self.env["auction.audit"].log(
            action="envelopes_opened", model="auction.event", res_id=event.id,
            event_id=event.id,
            detail={"type": envelope_type, "opened": opened,
                    "withheld": withheld,
                    "openers": [opener_a.login, opener_b.login]})
        return record

    def _finalise(self, opened, withheld):
        """Counts and minutes are written once, by raw SQL, because the
        model refuses ordinary writes."""
        self.ensure_one()
        self.env.cr.execute(
            "UPDATE auction_opening SET envelopes_opened = %s, "
            "envelopes_withheld = %s, minutes = %s WHERE id = %s",
            (opened, withheld, record_minutes(self.event_id, opened, withheld),
             self.id))
        self.invalidate_recordset(
            ["envelopes_opened", "envelopes_withheld", "minutes"])


def record_minutes(event, opened, withheld):
    """Opening minutes. BR-SLD-012."""
    rows = "".join(
        "<tr><td>%s</td><td>%s</td><td>%s</td></tr>" % (
            b.participant_id.alias or b.participant_id.display_name,
            b.server_ts, b.record_hash[:16])
        for b in event.bid_ids.filtered(lambda b: b.state == "active")
    )
    return (
        "<h3>Opening Minutes — %s</h3>"
        "<p>Envelopes opened: %d. Withheld undecrypted: %d.</p>"
        "<table><tr><th>Bidder</th><th>Submitted (server)</th>"
        "<th>Envelope hash</th></tr>%s</table>"
    ) % (event.display_name, opened, withheld, rows)
