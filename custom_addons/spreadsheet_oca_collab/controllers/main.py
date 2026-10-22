# Copyright 2026 RLFB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
import json

from odoo.exceptions import AccessError
from odoo.http import Controller, request, route


class SpreadsheetCollabController(Controller):
    @route(
        "/spreadsheet_oca_collab/leave",
        type="http",
        auth="user",
        methods=["POST"],
        csrf=False,
    )
    def leave(self, **kw):
        """Presence beacon sent when a browser tab closes.

        navigator.sendBeacon cannot carry a CSRF token; the endpoint only
        announces that a client left, after checking read access.
        """
        try:
            payload = json.loads(request.httprequest.get_data(as_text=True) or "{}")
            model = payload.get("model")
            res_id = int(payload.get("res_id") or 0)
            client_id = str(payload.get("clientId") or "")[:64]
        except (ValueError, TypeError):
            return request.make_response("", status=400)
        Abstract = request.env["spreadsheet.abstract"]
        if model not in Abstract._collab_concrete_models() or not client_id:
            return request.make_response("", status=400)
        record = request.env[model].browse(res_id).exists()
        if not record:
            return request.make_response("", status=404)
        try:
            record.send_spreadsheet_message(
                {"type": "CLIENT_LEFT", "clientId": client_id, "version": 1}
            )
        except AccessError:
            return request.make_response("", status=403)
        return request.make_response("", status=204)
