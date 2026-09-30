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
    def execute(self, event_id, share_a, share_b, envelope_type="commercial"):
        """Dual-control opening.

        Requires two distinct authorised identities. Neither share alone
        yields any information about the data key, and a single opener
        supplying both is refused.
        """
        event = self.env["auction.event"].browse(event_id).exists()
        if not event:
            raise UserError(_("Unknown event."))
        if not self.env.user.has_group(
                "custom_auction_base.group_auction_opener"):
            raise AccessError(_("You are not an authorised envelope opener."))

        scheduled = (event.comm_open_datetime if envelope_type == "commercial"
                     else event.tech_open_datetime)
        if scheduled and fields.Datetime.now() < scheduled:
            raise UserError(_(
                "Opening is scheduled for %s. Envelopes cannot be opened "
                "before that time.") % scheduled)

        opener_a = self.env.context.get("opener_a_id")
        opener_b = self.env.context.get("opener_b_id")
        if not opener_a or not opener_b or opener_a == opener_b:
            raise UserError(_(
                "Two distinct authorised openers are required. A single user "
                "cannot supply both shares."))

        dek = crypto_svc.combine_shares(share_a, share_b)
        if crypto_svc.fingerprint(dek) != event.dek_fingerprint:
            raise UserError(_(
                "The reconstructed key does not match this event. Check that "
                "both shares belong to this event."))

        opened = withheld = 0
        Bid = self.env["auction.bid"].sudo()
        for bid in Bid.search([("event_id", "=", event.id),
                               ("state", "=", "active")]):
            if envelope_type == "commercial" and \
                    not bid.participant_id.technically_qualified:
                withheld += 1
                continue
            aad = crypto_svc.build_aad(
                event.id, bid.lot_id.id, bid.participant_id.id, bid.chain_seq)
            plaintext = crypto_svc.open_envelope(
                bid.envelope, bid.envelope_nonce, dek, aad)
            # Decrypted values go into the evaluation working set. The ledger
            # envelope column is NEVER overwritten and never decrypted in
            # place. BR-SLD-013.
            event._stage_evaluation_values(bid, plaintext)
            opened += 1

        record = super(AuctionOpening, self.sudo()).create({
            "event_id": event.id,
            "envelope_type": envelope_type,
            "opener_a_id": opener_a,
            "opener_b_id": opener_b,
            "opened_at": fields.Datetime.now(),
            "key_fingerprint": crypto_svc.fingerprint(dek),
            "envelopes_opened": opened,
            "envelopes_withheld": withheld,
            "minutes": record_minutes(event, opened, withheld),
        })
        self.env["auction.audit"].log(
            action="envelopes_opened", model="auction.event", res_id=event.id,
            event_id=event.id,
            detail={"type": envelope_type, "opened": opened,
                    "withheld": withheld, "openers": [opener_a, opener_b]})
        return record


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
