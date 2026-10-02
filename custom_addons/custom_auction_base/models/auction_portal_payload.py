# -*- coding: utf-8 -*-
"""The disclosure boundary for everything a bidder is shown.

TSD-AUC-001 #11.1 and BR-SLD-020. A portal controller never serialises an
engine model, and a QWeb template never receives one. Both receive PLAIN
DICTS built here, by one method, which is the only place that decides what
a bidder may see.

The reason is not tidiness. Field omission in a template is not a security
control: the next person to add a column to that table has no way of
knowing that the field they reach for was deliberately left out. Returning
dicts makes the denylist a property of the data rather than a property of
every template that touches it, and `tests/test_disclosure.py` asserts it
over a real payload.

NEVER present in a payload, at any depth:

    estimated_value          the buyer's own number
    ceiling_price            unless the lot sets disclose_limit
    reserve_price            unless the lot sets disclose_limit
    dek_wrapped, dek_salt    the key material
    envelope, envelope_nonce another bidder's sealed envelope
    proxy_max                a proxy maximum, on mechanisms that have one
    norm_value               another bidder's normalised value

A bidder's OWN prices are returned, because they are theirs.
"""
from odoo import _, api, fields, models
from odoo.tools.misc import format_date, format_datetime

# Asserted by the disclosure test. Add to it before adding a field, not
# after somebody notices.
DENIED_KEYS = (
    "estimated_value", "dek_wrapped", "dek_salt", "dek_fingerprint",
    "envelope", "envelope_nonce", "proxy_max", "payload_digest",
)


class AuctionParticipant(models.Model):
    _inherit = "auction.participant"


    # ------------------------------------------------------------------
    # Formatting
    #
    # The payload carries DISPLAY STRINGS, not date objects, because a
    # QWeb t-field needs a recordset and these templates are given dicts.
    # Formatting here rather than in the template also means a bidder in
    # Mumbai reads Mumbai time while the ledger keeps UTC, which is the
    # behaviour you want and the one nobody thinks to ask for.
    # ------------------------------------------------------------------
    def _dt(self, value):
        if not value:
            return ""
        return format_datetime(self.env, value, dt_format="dd MMM yyyy, HH:mm")

    def _d(self, value):
        if not value:
            return ""
        return format_date(self.env, value, date_format="dd MMM yyyy")

    @staticmethod
    def _iso(value):
        """Raw ISO for an <input type=\"date\"> value."""
        return fields.Date.to_string(value) if value else ""

    def portal_payload(self, include_bid=True):
        """Everything this bidder may see about their event, as dicts."""
        self.ensure_one()
        p = self.sudo()
        event = p.event_id
        return {
            "participant": self._portal_participant(p),
            "event": self._portal_event(event),
            "lots": [self._portal_lot(lot, event) for lot in event.lot_ids],
            "security": self._portal_security(p),
            "own_bid": self._portal_own_bid(p) if include_bid else None,
            "bid_history": self._portal_bid_history(p),
            "documents": self._portal_documents(p),
            "clarifications": self._portal_clarifications(p, event),
            "corrigenda": self._portal_corrigenda(event),
            "can_bid": self._portal_can_bid(p, event),
        }

    # ------------------------------------------------------------------
    def _portal_participant(self, p):
        return {
            "id": p.id,
            "alias": p.alias or "",
            "partner_name": p.partner_id.display_name,
            "state": p.state,
            "state_label": dict(
                p._fields["state"].selection).get(p.state, p.state),
            "classification": p.classification or "",
            "classification_label": dict(
                p._fields["classification"].selection
            ).get(p.classification, "") if p.classification else "",
            "technically_qualified": p.technically_qualified,
            "prerequisites_accepted": p.prerequisites_accepted,
        }

    def _portal_event(self, event):
        """Header fields only. The internal estimate is NOT among them."""
        return {
            "id": event.id,
            "name": event.name,
            "title": event.title,
            "direction": event.direction,
            "is_reverse": event.direction == "reverse",
            "structure": event.structure,
            "two_envelope": event.structure == "two_envelope",
            "mechanism": event.mechanism_id.display_name or "",
            "mechanism_code": event.mechanism_id.code or "",
            "state": event.state,
            "currency_id": event.currency_id.id,
            "currency_symbol": event.currency_id.symbol or "",
            "bid_open": self._dt(event.bid_open_datetime),
            "bid_close": self._dt(event.bid_close_datetime),
            "tech_close": self._dt(event.tech_close_datetime),
            "tech_open": self._dt(event.tech_open_datetime),
            "comm_open": self._dt(event.comm_open_datetime),
            "content_version": event.content_version,
            "require_bid_security": event.require_bid_security,
            "bid_security_amount": event.bid_security_amount,
            "security_verify_lead_hours": event.security_verify_lead_hours,
            "is_open": event.state == "live",
            "category": event.category_id.display_name or "",
            "sections": [{"name": s.name, "description": s.description or ""}
                         for s in event.section_ids]
            if "section_ids" in event._fields else [],
        }

    def _portal_lot(self, lot, event):
        """A lot and its lines. Price limits appear ONLY where the buyer
        chose to disclose them; where they did not, the bidder is told a
        limit exists and never what it is."""
        disclosed = bool(lot.disclose_limit)
        return {
            "id": lot.id,
            "name": lot.name,
            "state": lot.state,
            "is_open": lot.state == "open",
            "award_basis": lot.award_basis,
            "all_or_nothing": lot.all_or_nothing,
            "close_datetime": self._dt(lot.close_datetime),
            "has_limit": bool(lot.ceiling_price or lot.reserve_price),
            "limit_disclosed": disclosed,
            "ceiling_price": lot.ceiling_price if disclosed else None,
            "reserve_price": lot.reserve_price if disclosed else None,
            "lines": [self._portal_line(l) for l in lot.line_ids],
        }

    def _portal_line(self, line):
        return {
            "id": line.id,
            "name": line.name,
            "sequence": line.sequence,
            "product_code": line.product_code or "",
            "specification": line.specification or "",
            "product_qty": line.product_qty,
            "uom": line.product_uom_id.display_name or "",
            "line_type": line.line_type,
            "hsn_sac": line.hsn_sac or "",
            "delivery_location": line.delivery_location or "",
            "required_by": self._d(line.required_by),
            "required_by_iso": self._iso(line.required_by),
            "delivery_summary": line.delivery_summary or "",
            "schedule": [{
                "id": s.id,
                "name": s.name or "",
                "sequence": s.sequence,
                "quantity": s.quantity,
                "required_by": self._d(s.required_by),
                "required_by_iso": self._iso(s.required_by),
                "delivery_location": s.delivery_location or "",
            } for s in line.schedule_ids.sorted("sequence")],
        }

    def _portal_security(self, p):
        sec = p.security_id
        if not sec:
            return {"exists": False,
                    "required": p.event_id.require_bid_security,
                    "amount_required": p.event_id.bid_security_amount}
        return {
            "exists": True,
            "required": p.event_id.require_bid_security,
            "id": sec.id,
            "mode": sec.mode,
            "mode_label": dict(sec._fields["mode"].selection).get(sec.mode, ""),
            "state": sec.state,
            "state_label": dict(
                sec._fields["state"].selection).get(sec.state, ""),
            "amount_required": sec.amount_required,
            "amount_declared": sec.amount_declared,
            "instrument_ref": sec.instrument_ref or "",
            "instrument_date": self._d(sec.instrument_date),
            "bank_name": sec.bank_name or "",
            "exemption_basis": sec.exemption_basis or "",
            "permits_bidding": sec.permits_bidding(),
            "permits_award": sec.permits_opening(),
        }

    def _portal_own_bid(self, p):
        """The bidder's own live bid. Their own prices, nobody else's."""
        bid = self.env["auction.bid"].sudo().search([
            ("participant_id", "=", p.id), ("state", "=", "active"),
        ], order="chain_seq desc", limit=1)
        if not bid:
            return None
        opened = bool(p.event_id.opening_ids)
        return {
            "id": bid.id,
            "reference": bid.reference,
            "server_ts": self._dt(bid.server_ts),
            "chain_seq": bid.chain_seq,
            "record_hash": bid.record_hash,
            "state": bid.state,
            "sealed": bool(bid.envelope),
            "opened": opened,
            "round_no": bid.round_no,
            "lines": [{
                "line_id": l.line_id.id,
                "line_name": l.line_id.name,
                # A sealed bid stores no clear price, so there is nothing
                # to show until the opening -- not even to its own author.
                "price_unit": l.price_unit,
                "qty_offered": l.qty_offered,
                "max_deviation_days": l.max_deviation_days,
                "schedule": [{
                    "schedule_id": s.schedule_id.id,
                    "quantity": s.quantity,
                    "offered_date": self._d(s.offered_date),
                    "deviation_days": s.deviation_days,
                } for s in l.schedule_ids.sorted("sequence")],
            } for l in bid.line_ids],
        }

    def _portal_bid_history(self, p):
        """Every bid this bidder has made, including superseded ones.

        Shown deliberately. A bidder who can see their own superseded
        submissions can satisfy themselves that the one that counts is the
        one they intended, which is the commonest support question.
        """
        bids = self.env["auction.bid"].sudo().search(
            [("participant_id", "=", p.id)], order="chain_seq desc")
        return [{
            "reference": b.reference,
            "server_ts": self._dt(b.server_ts),
            "chain_seq": b.chain_seq,
            "record_hash": b.record_hash,
            "state": b.state,
            "state_label": dict(
                b._fields["state"].selection).get(b.state, b.state),
            "round_no": b.round_no,
        } for b in bids]

    def _portal_documents(self, p):
        docs = self.env["auction.document"].visible_to(p)
        return [{
            "id": d.id,
            "name": d.name,
            "doc_type": d.doc_type,
            "doc_type_label": dict(
                d._fields["doc_type"].selection).get(d.doc_type, ""),
            "file_name": d.file_name or "",
            "file_size": d.file_size,
            "sha256": d.sha256 or "",
            "uploaded_on": self._dt(d.uploaded_on),
            "from_bidder": d.is_from_bidder,
            "attachment_id": d.attachment_id.id,
        } for d in docs]

    def _portal_clarifications(self, p, event):
        """Published answers from everyone, plus this bidder's own threads.

        A published answer NEVER carries the asker. One bidder learning
        that a competitor is confused about clause 4 is itself commercial
        information, and in a small supplier pool it is enough to identify
        them.
        """
        published = self.env["auction.clarification"].published_for(event)
        own = self.env["auction.clarification"].sudo().search([
            ("event_id", "=", event.id), ("participant_id", "=", p.id),
        ], order="asked_on desc")
        return {
            "published": published,
            "own": [{
                "name": c.name,
                "question": c.question,
                "reference_clause": c.reference_clause or "",
                "asked_on": self._dt(c.asked_on),
                "state": c.state,
                "state_label": dict(
                    c._fields["state"].selection).get(c.state, ""),
                "answer": c.answer if c.state == "published" else "",
            } for c in own],
        }

    def _portal_corrigenda(self, event):
        recs = self.env["auction.corrigendum"].sudo().search([
            ("event_id", "=", event.id), ("state", "=", "issued"),
        ], order="issued_on desc")
        return [{
            "name": c.name,
            "description": c.description,
            "is_material": c.is_material,
            "issued_on": self._dt(c.issued_on),
            "previous_close": self._dt(c.previous_close),
            "new_close_datetime": self._dt(c.new_close_datetime),
            "bids_voided": c.bids_voided,
        } for c in recs]

    def _portal_can_bid(self, p, event):
        """Why the bid button is or is not there, in the bidder's words.

        Returned as a reason rather than a bare boolean so the portal can
        say WHY. "Submit is greyed out" is the support call this avoids.
        """
        if event.state != "live":
            return {"ok": False, "reason": _(
                "This event is not accepting bids at the moment.")}
        if p.state in ("excluded", "withdrawn", "disqualified"):
            return {"ok": False, "reason": _(
                "Your organisation is not able to bid on this event.")}
        link = p.user_ids.filtered(lambda u: u.user_id == self.env.user)
        if not link or link[0].role != "authorised_bidder":
            return {"ok": False, "reason": _(
                "You are not the authorised bidder for this event. Your "
                "document coordinator can assign the role.")}
        if not p._bid_security_permits_bidding():
            return {"ok": False, "reason": _(
                "Declare your bid security before bidding. Verification by "
                "the buyer's finance team may follow afterwards.")}
        if not event.lot_ids.filtered(lambda l: l.state == "open"):
            return {"ok": False, "reason": _("No lot is open for bidding.")}
        return {"ok": True, "reason": ""}

    # ------------------------------------------------------------------
    @api.model
    def portal_for_user(self, event_id, user=None):
        """Resolve the participant for a logged-in bidder.

        Routing is by EVENT id, and the participant is resolved from the
        session rather than taken from the URL, so there is no identifier a
        bidder could change to read somebody else's event.
        """
        user = user or self.env.user
        commercial = user.partner_id.commercial_partner_id
        return self.sudo().search([
            ("event_id", "=", int(event_id)),
            ("commercial_partner_id", "=", commercial.id),
        ], limit=1)

    @api.model
    def portal_list_for_user(self, user=None):
        """Every event this bidder is invited to, newest first."""
        user = user or self.env.user
        commercial = user.partner_id.commercial_partner_id
        if not commercial:
            return self.browse([])
        return self.sudo().search([
            ("commercial_partner_id", "=", commercial.id),
            ("event_id.state", "not in", ("draft", "under_approval")),
        ], order="id desc")
