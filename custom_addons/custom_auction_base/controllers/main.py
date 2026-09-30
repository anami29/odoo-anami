# -*- coding: utf-8 -*-
"""Portal controllers.

TSD-AUC-001 #11.1: a controller authorises, deserialises, delegates to the
model layer and serialises the result. It performs NO business validation,
and it never calls read() or search_read() on an engine model directly --
field omission in a view is not a security control.
"""
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class AuctionPortal(http.Controller):

    @http.route("/auction/bid/submit", type="json", auth="user",
                methods=["POST"], csrf=True)
    def submit_bid(self, **payload):
        """Thin wrapper over auction.bid.submit_request.

        Retry and locking live in the model layer at the request boundary.
        """
        try:
            return {
                "ok": True,
                "receipt": request.env["auction.bid"].submit_request(payload),
            }
        except Exception as exc:
            # The model layer raises user-facing messages already; this
            # returns them without leaking a traceback to the bidder.
            _logger.info("auction.bid: submission refused: %s", exc)
            return {"ok": False, "error": str(exc)}

    @http.route("/auction/console/keepalive", type="json", auth="user",
                methods=["POST"])
    def keepalive(self, lot_id=None, **kw):
        """AMD-AUC-001 AMD-08.

        Odoo session expiry is middleware-level and applies to the whole
        session, not per route. Implementing the live-console exception as a
        timeout configuration change would weaken the timeout for every
        portal route the user holds, including the sealed-bid pages -- the
        opposite of the intent. An explicit keepalive keeps the policy intact
        and stops the moment the lot closes.
        """
        lot = request.env["auction.lot"].browse(int(lot_id or 0)).exists()
        if not lot or lot.state != "open":
            return {"ok": False, "code": 410, "reason": "lot_closed"}
        request.env.cr.execute("SELECT (clock_timestamp() AT TIME ZONE 'UTC')")
        return {
            "ok": True,
            "server_now": request.env.cr.fetchone()[0].isoformat(),
            "lot_state": lot.state,
        }
