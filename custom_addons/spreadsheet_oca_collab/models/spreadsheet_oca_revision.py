# Copyright 2026 RLFB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
import json
from datetime import timedelta

from odoo import api, fields, models

UNIQUE_INDEX = "spreadsheet_oca_revision_parent_uniq"


class SpreadsheetOcaRevision(models.Model):
    _inherit = "spreadsheet.oca.revision"
    _order = "id"

    active = fields.Boolean(default=True, index=True)
    snapshot_uuid = fields.Char(
        index=True,
        help="Snapshot (or reset) that consolidated this revision into the "
        "stored sheet. Empty while the revision is part of the live chain.",
    )
    server_revision_id = fields.Char(index=True)

    def _collab_payload(self):
        """Message as the o-spreadsheet session expects it on replay.

        Stock spreadsheet_oca drops clientId on reload; it is restored here so
        that a client receiving its own revision during catch-up recognises it
        as an acknowledgement instead of applying it a second time.
        """
        self.ensure_one()
        payload = json.loads(self.commands or "{}")
        payload.update(
            nextRevisionId=self.next_revision_id,
            serverRevisionId=self.server_revision_id,
        )
        if self.client_id:
            payload["clientId"] = self.client_id
        return payload

    @api.model
    def _collab_create_unique_index(self):
        """Database-level guarantee: one child per parent in the live chain."""
        self.env.cr.execute(
            f"""CREATE UNIQUE INDEX IF NOT EXISTS {UNIQUE_INDEX}
                ON spreadsheet_oca_revision (model, res_id, server_revision_id)
                WHERE active"""
        )

    @api.model
    def _collab_gc_archived(self):
        """Cron: drop consolidated revisions past the retention window."""
        days = int(
            self.env["ir.config_parameter"].sudo().get_param(
                "spreadsheet_oca_collab.archived_revision_days", 90
            )
        )
        if days <= 0:
            return
        limit = fields.Datetime.now() - timedelta(days=days)
        self.sudo().with_context(active_test=False).search(
            [("active", "=", False), ("create_date", "<", limit)]
        ).unlink()
