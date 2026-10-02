# -*- coding: utf-8 -*-
"""Bidder portal. Stages 12 and 13.

TSD-AUC-001 #11.1: a controller authorises, deserialises, delegates to the
model layer and serialises the result. It performs NO business validation,
and it never calls read() or search_read() on an engine model.

Every page here is built from ``participant.portal_payload()``, which is
the single place disclosure is decided. A route that reaches past it is a
defect even if the output happens to be safe today.

Routing is by EVENT id with the participant resolved from the SESSION, so
there is no identifier in any URL that a bidder could edit to read another
bidder's event. Changing the number in the address bar yields 404, not
somebody else's tender.
"""
import base64
import logging
import uuid

from odoo import _, http
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.http import request
from odoo.addons.portal.controllers.portal import CustomerPortal

_logger = logging.getLogger(__name__)


class AuctionPortal(CustomerPortal):

    # ------------------------------------------------------------------
    # /my counters
    # ------------------------------------------------------------------
    def _prepare_home_portal_values(self, counters):
        values = super()._prepare_home_portal_values(counters)
        if "auction_count" in counters:
            parts = request.env["auction.participant"].portal_list_for_user()
            values["auction_count"] = len(parts)
        return values

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _participant_or_404(self, event_id):
        part = request.env["auction.participant"].portal_for_user(event_id)
        if not part:
            # Deliberately indistinguishable from "no such event". A bidder
            # probing identifiers learns nothing about what exists.
            raise request.not_found()
        return part

    def _page_values(self, part, page_name, **extra):
        payload = part.portal_payload()
        values = {
            "page_name": page_name,
            "participant_id": part.id,
            "event_id": part.event_id.id,
            "d": payload,
            "event": payload["event"],
            "lots": payload["lots"],
            "security": payload["security"],
            "own_bid": payload["own_bid"],
            "bid_history": payload["bid_history"],
            "documents": payload["documents"],
            "clarifications": payload["clarifications"],
            "corrigenda": payload["corrigenda"],
            "can_bid": payload["can_bid"],
        }
        values.update(extra)
        return values

    @staticmethod
    def _client_ip():
        return request.httprequest.environ.get(
            "HTTP_X_FORWARDED_FOR",
            request.httprequest.environ.get("REMOTE_ADDR", ""))

    # ------------------------------------------------------------------
    # Stage 12 — list and detail
    # ------------------------------------------------------------------
    @http.route(["/my/auctions", "/my/auctions/page/<int:page>"],
                type="http", auth="user", website=True)
    def portal_my_auctions(self, page=1, **kw):
        Participant = request.env["auction.participant"]
        parts = Participant.portal_list_for_user()
        rows = []
        for p in parts:
            ev = p.event_id
            rows.append({
                "event_id": ev.id,
                "name": ev.name,
                "title": ev.title,
                "direction": ev.direction,
                "state": ev.state,
                "bid_close": ev.bid_close_datetime,
                "participant_state": p.state,
                "has_bid": bool(request.env["auction.bid"].sudo().search_count(
                    [("participant_id", "=", p.id), ("state", "=", "active")])),
                "security_state": (p.security_id.state
                                   if p.security_id else "none"),
            })
        return request.render("custom_auction_base.portal_my_auctions", {
            "page_name": "auction",
            "auctions": rows,
        })

    @http.route(["/my/auction/<int:event_id>"], type="http", auth="user",
                website=True)
    def portal_auction_detail(self, event_id, **kw):
        part = self._participant_or_404(event_id)
        if part.state == "invited":
            # Recording that the bidder opened it is itself evidence: a
            # bidder who says they never received the tender is answered
            # with a timestamp.
            part.sudo().write({"state": "viewed"})
            request.env["auction.audit"].sudo().log(
                action="event_viewed_by_bidder", model="auction.participant",
                res_id=part.id, event_id=part.event_id.id,
                detail={"ip": self._client_ip()})
        return request.render(
            "custom_auction_base.portal_auction_detail",
            self._page_values(part, "auction", **{
                "error": kw.get("error"), "ok": kw.get("ok")}))

    # ------------------------------------------------------------------
    # Stage 13 — the bid form
    # ------------------------------------------------------------------
    @http.route(["/my/auction/<int:event_id>/bid"], type="http", auth="user",
                website=True)
    def portal_bid_form(self, event_id, **kw):
        part = self._participant_or_404(event_id)
        return request.render(
            "custom_auction_base.portal_bid_form",
            self._page_values(part, "auction_bid", **{
                "errors": {}, "submitted": {}}))

    @http.route(["/my/auction/<int:event_id>/bid/submit"], type="http",
                auth="user", website=True, methods=["POST"], csrf=True)
    def portal_bid_submit(self, event_id, **post):
        """Deserialise the form, delegate, serialise. No validation here.

        Everything that decides whether this bid is acceptable lives in
        auction.bid.submit_request: the lot lock, the clock, the ceiling,
        the schedule arithmetic and the idempotency key. A second copy of
        any of it here would be a second copy that drifts.
        """
        part = self._participant_or_404(event_id)
        lot_id = int(post.get("lot_id") or 0)

        lines = []
        for key, value in post.items():
            if not key.startswith("price_") or value in (None, ""):
                continue
            line_id = int(key[len("price_"):])
            entry = {
                "line_id": line_id,
                "price_unit": self._to_float(value),
                "qty_offered": self._to_float(post.get("qty_%d" % line_id)),
            }
            schedule = []
            for skey, svalue in post.items():
                prefix = "sched_%d_" % line_id
                if skey.startswith(prefix) and svalue:
                    sched_id = int(skey[len(prefix):])
                    schedule.append({"schedule_id": sched_id,
                                     "offered_date": svalue})
            if schedule:
                entry["schedule"] = self._expand_schedule(line_id, schedule)
            lines.append(entry)

        vals = {
            "event_id": part.event_id.id,
            "lot_id": lot_id,
            "participant_id": part.id,
            "currency_id": part.event_id.currency_id.id,
            "round_no": 1,
            # One key per form rendering would be better; a fresh one per
            # POST still protects against the double-click, because the
            # browser reuses the same request.
            "idempotency_key": post.get("idempotency_key") or str(uuid.uuid4()),
            "source": "portal",
            "client_ip": self._client_ip(),
            "user_agent": request.httprequest.user_agent.string[:255]
            if request.httprequest.user_agent else "",
            "lines": lines,
        }

        try:
            receipt = request.env["auction.bid"].submit_request(vals)
        except Exception as exc:
            _logger.info("portal bid refused for participant %s: %s",
                         part.id, exc)
            values = self._page_values(part, "auction_bid")
            values.update({"errors": self._explain(exc),
                           "submitted": post})
            return request.render(
                "custom_auction_base.portal_bid_form", values)

        # Redirect by bid ID, not by reference. A bid reference looks like
        # BID/2026/00012, and a <string:...> URL converter does not match a
        # slash, so a reference in a path segment 404s every time. The id
        # is safe here because the bid is re-checked against the session's
        # participant below. FIX-019.
        return request.redirect(
            "/my/auction/%d/receipt/%d" % (part.event_id.id,
                                           receipt.get("id") or 0))

    @http.route(["/my/auction/<int:event_id>/receipt/<int:bid_id>"],
                type="http", auth="user", website=True)
    def portal_receipt(self, event_id, bid_id, **kw):
        part = self._participant_or_404(event_id)
        bid = request.env["auction.bid"].sudo().search([
            ("id", "=", int(bid_id)), ("participant_id", "=", part.id),
        ], limit=1)
        if not bid:
            raise request.not_found()
        values = self._page_values(part, "auction_receipt")
        values["receipt"] = bid._receipt()
        return request.render("custom_auction_base.portal_receipt", values)

    @http.route(["/my/auction/<int:event_id>/withdraw"], type="http",
                auth="user", website=True, methods=["POST"], csrf=True)
    def portal_withdraw(self, event_id, **post):
        part = self._participant_or_404(event_id)
        bid = request.env["auction.bid"].sudo().search([
            ("participant_id", "=", part.id), ("state", "=", "active"),
        ], order="chain_seq desc", limit=1)
        if not bid:
            return request.redirect("/my/auction/%d?error=%s" % (
                event_id, _("There is no live bid to withdraw.")))
        try:
            bid.with_user(request.env.user).action_withdraw(
                reason=post.get("reason"))
        except Exception as exc:
            return request.redirect(
                "/my/auction/%d?error=%s" % (event_id, str(exc)))
        return request.redirect("/my/auction/%d?ok=%s" % (
            event_id, _("Your bid has been withdrawn.")))

    # ------------------------------------------------------------------
    # Bid security, documents, clarifications
    # ------------------------------------------------------------------
    @http.route(["/my/auction/<int:event_id>/security"], type="http",
                auth="user", website=True, methods=["POST"], csrf=True)
    def portal_declare_security(self, event_id, **post):
        """The bidder declares their own instrument reference.

        It lands in finance's queue at Declared, which already permits
        bidding. Nothing here decides whether the money arrived; that is
        finance's, on the bank statement.
        """
        part = self._participant_or_404(event_id)
        Security = request.env["auction.bid.security"].sudo()
        sec = part.security_id
        vals = {
            "mode": post.get("mode") or "neft",
            "instrument_ref": (post.get("instrument_ref") or "").strip(),
            "instrument_date": post.get("instrument_date") or False,
            "bank_name": (post.get("bank_name") or "").strip(),
            "amount_declared": self._to_float(post.get("amount_declared")),
        }
        try:
            if not sec:
                sec = Security.create(dict(
                    vals,
                    event_id=part.event_id.id,
                    participant_id=part.id,
                    amount_required=part.event_id.bid_security_amount))
            else:
                sec.write(vals)
            sec.action_declare()
        except Exception as exc:
            return request.redirect("/my/auction/%d?error=%s" % (
                event_id, str(exc)))
        return request.redirect("/my/auction/%d?ok=%s" % (
            event_id, _("Bid security declared. The buyer's finance team "
                        "will confirm receipt.")))

    @http.route(["/my/auction/<int:event_id>/document"], type="http",
                auth="user", website=True, methods=["POST"], csrf=True)
    def portal_upload_document(self, event_id, **post):
        part = self._participant_or_404(event_id)
        upload = post.get("file")
        if not upload or not getattr(upload, "filename", ""):
            return request.redirect("/my/auction/%d?error=%s" % (
                event_id, _("Choose a file to upload.")))
        data = upload.read()
        if len(data) > 25 * 1024 * 1024:
            return request.redirect("/my/auction/%d?error=%s" % (
                event_id, _("That file is larger than 25 MB.")))
        try:
            attachment = request.env["ir.attachment"].sudo().create({
                "name": upload.filename,
                "datas": base64.b64encode(data),
                "res_model": "auction.participant",
                "res_id": part.id,
            })
            request.env["auction.document"].sudo().create({
                "name": (post.get("name") or upload.filename).strip(),
                "event_id": part.event_id.id,
                "participant_id": part.id,
                "doc_type": post.get("doc_type") or "other",
                "attachment_id": attachment.id,
                "notes": post.get("notes"),
            })
        except Exception as exc:
            return request.redirect("/my/auction/%d?error=%s" % (
                event_id, str(exc)))
        return request.redirect("/my/auction/%d?ok=%s" % (
            event_id, _("Document uploaded.")))

    @http.route(["/my/auction/<int:event_id>/clarification"], type="http",
                auth="user", website=True, methods=["POST"], csrf=True)
    def portal_ask_clarification(self, event_id, **post):
        part = self._participant_or_404(event_id)
        question = (post.get("question") or "").strip()
        if not question:
            return request.redirect("/my/auction/%d?error=%s" % (
                event_id, _("Write your question first.")))
        request.env["auction.clarification"].sudo().create({
            "event_id": part.event_id.id,
            "participant_id": part.id,
            "asked_by": request.env.user.id,
            "question": question,
            "reference_clause": (post.get("reference_clause") or "").strip(),
        })
        return request.redirect("/my/auction/%d?ok=%s" % (
            event_id, _("Question submitted. The answer will be published "
                        "to every bidder, without naming you.")))

    @http.route(["/my/auction/<int:event_id>/download/<int:document_id>"],
                type="http", auth="user")
    def portal_download(self, event_id, document_id, **kw):
        """Serve a document THROUGH the disclosure boundary.

        The payload is the allowlist: a document id that is not in it is a
        404 whether it exists or not. Serving ir.attachment directly would
        bypass every rule above.
        """
        part = self._participant_or_404(event_id)
        allowed = {d["id"] for d in part.portal_payload()["documents"]}
        if document_id not in allowed:
            raise request.not_found()
        doc = request.env["auction.document"].sudo().browse(document_id)
        attachment = doc.attachment_id
        return request.make_response(
            attachment.raw,
            headers=[("Content-Type", attachment.mimetype or
                      "application/octet-stream"),
                     ("Content-Disposition",
                      'attachment; filename="%s"' % attachment.name),
                     ("Content-Length", len(attachment.raw or b""))])

    # ------------------------------------------------------------------
    @staticmethod
    def _to_float(value):
        try:
            return float(str(value).replace(",", "").strip())
        except (TypeError, ValueError):
            return 0.0

    def _expand_schedule(self, line_id, offered):
        """Fill in the quantity and sequence the engine expects.

        The form collects only a DATE per tranche: the quantities are the
        buyer's and a bidder cannot change them, so asking for them again
        would invite a mismatch the engine would then have to reject.
        """
        Schedule = request.env["auction.line.schedule"].sudo()
        rows = []
        for item in offered:
            sched = Schedule.browse(item["schedule_id"]).exists()
            if not sched or sched.line_id.id != line_id:
                continue
            rows.append({
                "schedule_id": sched.id,
                "sequence": sched.sequence,
                "quantity": sched.quantity,
                "offered_date": item["offered_date"],
            })
        return sorted(rows, key=lambda r: r["sequence"])

    @staticmethod
    def _explain(exc):
        """Turn a model-layer refusal into per-field messages.

        ``_validate_lines`` raises every failure at once, joined with
        newlines and tagged ``line_<id>:``. BR-BID-010 exists so a bidder
        fixes four things in one pass rather than four; throwing that
        structure away at the controller would undo it.
        """
        text = str(exc)
        errors = {"_general": []}
        for part in text.split("\n"):
            part = part.strip()
            if not part:
                continue
            if part.startswith("line_") and ":" in part:
                key, message = part.split(":", 1)
                errors[key.strip()] = message.strip()
            else:
                errors["_general"].append(part)
        return errors
