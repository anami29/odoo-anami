# Copyright 2026 RLFB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
import json

from odoo import api, fields, models


class SpreadsheetOcaUserFilter(models.Model):
    """Each user's AutoFilter criteria on a sheet, reapplied when they reopen it."""

    _name = "spreadsheet.oca.user.filter"
    _description = "Spreadsheet filter criteria per user"

    res_model = fields.Char(required=True, index=True)
    res_id = fields.Integer(required=True, index=True)
    user_id = fields.Many2one(
        "res.users", required=True, index=True, ondelete="cascade"
    )
    data = fields.Text()

    _sql_constraints = [
        (
            "uniq_user_sheet",
            "unique(res_model, res_id, user_id)",
            "One filter state per user and sheet.",
        )
    ]

    @api.model
    def _get_entries(self, sheet):
        record = self.sudo().search(
            [
                ("res_model", "=", sheet._name),
                ("res_id", "=", sheet.id),
                ("user_id", "=", self.env.uid),
            ],
            limit=1,
        )
        try:
            return json.loads(record.data) if record.data else []
        except ValueError:
            return []

    @api.model
    def _set_entries(self, sheet, entries):
        record = self.sudo().search(
            [
                ("res_model", "=", sheet._name),
                ("res_id", "=", sheet.id),
                ("user_id", "=", self.env.uid),
            ],
            limit=1,
        )
        data = json.dumps(entries[:200])
        if record:
            record.data = data
        else:
            self.sudo().create(
                {
                    "res_model": sheet._name,
                    "res_id": sheet.id,
                    "user_id": self.env.uid,
                    "data": data,
                }
            )
