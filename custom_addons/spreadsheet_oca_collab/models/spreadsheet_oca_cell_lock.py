# Copyright 2026 RLFB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Who is typing in a cell.

A row exists while a person has the cell editor open on a cell, from the
first keystroke until Enter, Tab, Esc or a click elsewhere. While it exists
the cell is read-only for everyone else. A second person can only start typing
in that cell once the row is gone: there is no queue, the next one to type
gets it.

Claims on the same sheet are serialised through a guard row: two concurrent
claims both update it, PostgreSQL makes the second fail with a serialisation
error, Odoo retries it, and the retry sees the first claim, so two people
starting at the same instant still give exactly one holder.

Rows are kept alive by the presence heartbeat and removed when the client
leaves or stays silent past the presence timeout.
"""
from datetime import timedelta

from odoo import api, fields, models
from odoo.tools import SQL


class SpreadsheetOcaCellLock(models.Model):
    _name = "spreadsheet.oca.cell.lock"
    _description = "Spreadsheet cell lock"
    _order = "id"
    _log_access = False

    res_model = fields.Char(required=True, index=True)
    res_id = fields.Integer(required=True, index=True)
    client_id = fields.Char(required=True, index=True)
    sheet_ref = fields.Char(required=True, help="o-spreadsheet sheet id")
    col = fields.Integer(required=True)
    row = fields.Integer(required=True)
    user_id = fields.Many2one("res.users", required=True, ondelete="cascade")
    last_seen = fields.Datetime(required=True, default=fields.Datetime.now)

    _sql_constraints = [
        (
            "uniq_client",
            "unique(res_model, res_id, client_id)",
            "A client is on one cell at a time.",
        )
    ]

    def init(self):
        self.env.cr.execute(
            """CREATE TABLE IF NOT EXISTS spreadsheet_oca_cell_lock_guard (
                   res_model varchar NOT NULL,
                   res_id integer NOT NULL,
                   seq integer NOT NULL DEFAULT 0,
                   PRIMARY KEY (res_model, res_id))"""
        )

    # ------------------------------------------------------------------
    @api.model
    def _enabled(self):
        return (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("spreadsheet_oca_collab.cell_lock", "1")
            not in ("0", "false", "False", "")
        )

    @api.model
    def _timeout(self):
        return int(
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("spreadsheet_oca_collab.presence_timeout", 90)
        )

    @api.model
    def _serialise(self, sheet):
        self.env.cr.execute(
            SQL(
                """INSERT INTO spreadsheet_oca_cell_lock_guard (res_model, res_id, seq)
                   VALUES (%s, %s, 1)
                   ON CONFLICT (res_model, res_id)
                   DO UPDATE SET seq = spreadsheet_oca_cell_lock_guard.seq + 1""",
                sheet._name,
                sheet.id,
            ),
            log_exceptions=False,
        )

    @api.model
    def _rows(self, sheet):
        return self.sudo().search(
            [("res_model", "=", sheet._name), ("res_id", "=", sheet.id)]
        )

    @api.model
    def _purge_expired(self, sheet):
        limit = fields.Datetime.now() - timedelta(seconds=self._timeout())
        expired = self.sudo().search(
            [
                ("res_model", "=", sheet._name),
                ("res_id", "=", sheet.id),
                ("last_seen", "<", limit),
            ]
        )
        expired.unlink()
        return bool(expired)

    @api.model
    def _holders(self, sheet):
        """One entry per occupied cell: the first live arrival."""
        holders = {}
        limit = fields.Datetime.now() - timedelta(seconds=self._timeout())
        for row in self._rows(sheet).filtered(lambda r: r.last_seen >= limit):
            key = (row.sheet_ref, row.col, row.row)
            if key not in holders:
                holders[key] = {
                    "sheetId": row.sheet_ref,
                    "col": row.col,
                    "row": row.row,
                    "clientId": row.client_id,
                    "userId": row.user_id.id,
                    "name": row.user_id.name,
                }
        return list(holders.values())

    # ------------------------------------------------------------------
    def _broadcast(self, sheet):
        holders = self._holders(sheet)
        sheet._bus_send(
            "notification",
            {"type": "CELL_LOCKS", "locks": holders, "id": sheet.id},
            subchannel="spreadsheet_oca",
        )
        return holders

    @api.model
    def _mine(self, sheet, client_id):
        return self.sudo().search(
            [
                ("res_model", "=", sheet._name),
                ("res_id", "=", sheet.id),
                ("client_id", "=", client_id),
            ]
        )

    @api.model
    def _claim(self, sheet, client_id, position):
        """Start (position) or stop (False) typing. Returns the outcome."""
        if not self._enabled():
            return {"ok": True, "locks": []}
        self._serialise(sheet)
        changed = self._purge_expired(sheet)
        mine = self._mine(sheet, client_id)
        if mine:
            mine.unlink()
            changed = True
        holder = None
        if position:
            if not sheet._collab_can_write():
                return {"ok": False, "holder": None, "locks": self._holders(sheet)}
            limit = fields.Datetime.now() - timedelta(seconds=self._timeout())
            other = self.sudo().search(
                [
                    ("res_model", "=", sheet._name),
                    ("res_id", "=", sheet.id),
                    ("sheet_ref", "=", position["sheetId"]),
                    ("col", "=", int(position["col"])),
                    ("row", "=", int(position["row"])),
                    ("last_seen", ">=", limit),
                ],
                limit=1,
            )
            if other:
                holder = other
            else:
                self.sudo().create(
                    {
                        "res_model": sheet._name,
                        "res_id": sheet.id,
                        "client_id": client_id,
                        "sheet_ref": position["sheetId"],
                        "col": int(position["col"]),
                        "row": int(position["row"]),
                        "user_id": self.env.uid,
                    }
                )
                changed = True
        locks = self._broadcast(sheet) if changed else self._holders(sheet)
        return {
            "ok": holder is None,
            "holder": holder and {"clientId": holder.client_id, "name": holder.user_id.name},
            "locks": locks,
        }

    @api.model
    def _on_presence(self, sheet, message):
        """Presence keeps a typing lock alive; leaving drops it."""
        if not self._enabled():
            return None
        if message.get("type") == "CLIENT_LEFT":
            mine = self._mine(sheet, message.get("clientId") or "")
            if mine:
                self._serialise(sheet)
                mine.unlink()
                return self._broadcast(sheet)
            return None
        client_id = (message.get("client") or {}).get("id")
        if client_id:
            mine = self._mine(sheet, client_id)
            if mine:
                mine.last_seen = fields.Datetime.now()
        if self._purge_expired(sheet):
            return self._broadcast(sheet)
        return None
