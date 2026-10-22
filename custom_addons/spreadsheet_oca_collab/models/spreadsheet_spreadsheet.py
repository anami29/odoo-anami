# Copyright 2026 RLFB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
import base64
import io
import re
import zipfile

from odoo import _, fields, models
from odoo.exceptions import AccessError

# o-spreadsheet's XLSX loader rewrites SUBTOTAL(n,...) to SUM/AVERAGE/...,
# which then counts filtered rows. Its pattern requires the digits to be
# followed directly by a comma; a space keeps the formula intact (and valid)
# so this module's SUBTOTAL evaluates it.
SUBTOTAL_RE = re.compile(r"(SUBTOTAL\(\s*\d+)\s*,")

SHARING_FIELDS = {
    "owner_id",
    "contributor_ids",
    "contributor_group_ids",
    "reader_ids",
    "reader_group_ids",
    "company_id",
}


class SpreadsheetSpreadsheet(models.Model):
    _inherit = "spreadsheet.spreadsheet"

    collab_version_ids = fields.One2many(
        "spreadsheet.oca.version", "spreadsheet_id", string="Versions"
    )
    collab_version_count = fields.Integer(compute="_compute_collab_version_count")
    collab_source_attachment_id = fields.Many2one(
        "ir.attachment",
        string="Original file",
        readonly=True,
        copy=False,
        help="The Excel file this sheet was created from, kept unchanged.",
    )

    def _compute_collab_version_count(self):
        data = self.env["spreadsheet.oca.version"]._read_group(
            [("spreadsheet_id", "in", self.ids)], ["spreadsheet_id"], ["__count"]
        )
        counts = {sheet.id: count for sheet, count in data}
        for sheet in self:
            sheet.collab_version_count = counts.get(sheet.id, 0)

    # ------------------------------------------------------------------
    # Versions
    # ------------------------------------------------------------------
    def _collab_has_versions(self):
        return True

    def _collab_record_version(self, data, revision_id, kind, name=None, **kw):
        self.ensure_one()
        return (
            self.env["spreadsheet.oca.version"]
            .sudo()
            .create(
                {
                    "spreadsheet_id": self.id,
                    "name": name or False,
                    "kind": kind,
                    "revision_uuid": revision_id,
                    "revision_count": kw.get("revision_count", 0),
                    "data": self._collab_encode(data),
                }
            )
        )

    def _collab_prune_versions(self):
        keep = int(
            self.env["ir.config_parameter"].sudo().get_param(
                "spreadsheet_oca_collab.version_keep", 30
            )
        )
        if keep <= 0:
            return
        Version = self.env["spreadsheet.oca.version"].sudo()
        for sheet in self:
            unnamed = Version.search(
                [("spreadsheet_id", "=", sheet.id), ("name", "=", False)],
                order="create_date desc, id desc",
                offset=keep,
            )
            unnamed.unlink()

    def action_open_versions(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "spreadsheet_oca_collab.spreadsheet_oca_version_act_window"
        )
        action["domain"] = [("spreadsheet_id", "=", self.id)]
        action["context"] = {"default_spreadsheet_id": self.id}
        action["name"] = _("Version history: %s", self.name)
        return action

    # ------------------------------------------------------------------
    # Sharing belongs to the owner
    # ------------------------------------------------------------------
    def write(self, vals):
        if SHARING_FIELDS & set(vals) and not self.env.su:
            if not self.env.user.has_group("spreadsheet_oca.group_manager"):
                not_owned = self.filtered(lambda s: s.owner_id != self.env.user)
                if not_owned:
                    raise AccessError(
                        _(
                            "Only the owner of “%s” can change who has access to "
                            "it or transfer it.",
                            not_owned[0].name,
                        )
                    )
        return super().write(vals)

    # ------------------------------------------------------------------
    # Excel import keeps the original file
    # ------------------------------------------------------------------
    def create_document_from_attachment(self, attachment_ids):
        attachments = self.env["ir.attachment"].browse(attachment_ids)
        originals = [
            attachment.copy({"res_model": False, "res_id": False})
            for attachment in attachments
        ]
        for attachment in attachments:
            attachment.datas = self._collab_keep_subtotal(attachment.datas)
        before = set(self.search([]).ids)
        action = super().create_document_from_attachment(attachment_ids)
        created = self.search([("id", "not in", list(before))], order="id")
        for sheet, original in zip(created, originals, strict=False):
            original.write({"res_model": sheet._name, "res_id": sheet.id})
            sheet.sudo().collab_source_attachment_id = original
        return action

    @staticmethod
    def _collab_keep_subtotal(datas):
        try:
            source = zipfile.ZipFile(io.BytesIO(base64.b64decode(datas)))
        except (zipfile.BadZipFile, ValueError, TypeError):
            return datas
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
            for info in source.infolist():
                content = source.read(info.filename)
                if info.filename.startswith("xl/worksheets/") and info.filename.endswith(
                    ".xml"
                ):
                    text = content.decode("utf-8")
                    content = SUBTOTAL_RE.sub(r"\1 ,", text).encode("utf-8")
                target.writestr(info, content)
        return base64.b64encode(out.getvalue())
