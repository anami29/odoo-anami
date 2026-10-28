# Copyright 2026 RLFB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
import base64
import json

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError


class SpreadsheetOcaVersion(models.Model):
    _name = "spreadsheet.oca.version"
    _description = "Spreadsheet version"
    _order = "create_date desc, id desc"
    _rec_name = "display_label"

    spreadsheet_id = fields.Many2one(
        "spreadsheet.spreadsheet", required=True, ondelete="cascade", index=True
    )
    name = fields.Char(
        string="Version name",
        help="Named versions are kept permanently; unnamed ones are pruned.",
    )
    kind = fields.Selection(
        [
            ("snapshot", "Auto-saved"),
            ("checkpoint", "Saved by user"),
            ("restore", "Restored"),
            ("replace", "Replaced"),
            ("import", "Imported"),
        ],
        required=True,
        default="snapshot",
    )
    revision_uuid = fields.Char(readonly=True, index=True)
    revision_count = fields.Integer(
        readonly=True, help="Edits consolidated into this version."
    )
    data = fields.Binary(attachment=True, readonly=True, copy=False)
    user_id = fields.Many2one(
        "res.users", default=lambda s: s.env.user, readonly=True, string="By"
    )
    display_label = fields.Char(compute="_compute_display_label")
    is_current = fields.Boolean(compute="_compute_is_current")

    @api.depends("name", "kind", "create_date")
    def _compute_display_label(self):
        kinds = dict(self._fields["kind"].selection)
        for version in self:
            version.display_label = version.name or "%s, %s" % (
                kinds.get(version.kind),
                fields.Datetime.to_string(version.create_date) or "",
            )

    def _compute_is_current(self):
        for version in self:
            version.is_current = (
                version.revision_uuid
                and version.revision_uuid
                == version.spreadsheet_id.sudo().collab_head_revision
            )

    @api.model_create_multi
    def create(self, vals_list):
        versions = super().create(vals_list)
        versions.spreadsheet_id._collab_prune_versions()
        return versions

    def action_restore(self):
        """Make this version the current sheet for everyone."""
        self.ensure_one()
        # The button is hidden without this right, but hiding a button is not a
        # permission: the method is still reachable over RPC.
        if not self.env.user.has_group(
            "spreadsheet_oca_collab.group_version_restore"
        ):
            raise AccessError(
                _(
                    "Restoring a version is limited to people holding the "
                    "Restore button right. Ask an administrator for it."
                )
            )
        sheet = self.spreadsheet_id
        sheet.check_access("write")
        if not self.data:
            raise UserError(_("This version has no stored content."))
        data = json.loads(base64.b64decode(self.data).decode("UTF-8"))
        label = self.name or self.display_label
        sheet._collab_reset(
            data, kind="restore", name=_("Restored from: %s", label)
        )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Version restored"),
                "message": _(
                    "“%s” is now the current version. Everyone editing the "
                    "sheet has been reloaded onto it.",
                    label,
                ),
                "type": "success",
                "next": {"type": "ir.actions.act_window_close"},
            },
        }

    def action_open_sheet_at_version(self):
        """Download this version as JSON (inspect without restoring)."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_url",
            "url": "/web/content/spreadsheet.oca.version/%s/data/%s.json?download=true"
            % (self.id, (self.spreadsheet_id.name or "sheet").replace("/", "-")),
            "target": "self",
        }
