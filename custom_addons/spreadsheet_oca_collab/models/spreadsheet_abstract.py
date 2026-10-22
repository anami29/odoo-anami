# Copyright 2026 RLFB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Server-side ordering for spreadsheet_oca collaborative sessions.

o-spreadsheet clients implement operational transformation and expect the
server to do one thing: accept a revision only if it was built on the current
head, and reject it otherwise so the client can transform and resend. Stock
spreadsheet_oca accepts everything. This module adds the check as an atomic
compare-and-set on a head column, backed by a partial unique index.
"""
import base64
import json
import uuid

from odoo import api, fields, models
from odoo.tools import SQL

ORDERED_TYPES = ("REMOTE_REVISION", "REVISION_UNDONE", "REVISION_REDONE")
DEFAULT_REVISION_ID = "START_REVISION"
MESSAGE_VERSION = 1


class SpreadsheetAbstract(models.AbstractModel):
    _inherit = "spreadsheet.abstract"

    collab_head_revision = fields.Char(
        copy=False,
        readonly=True,
        help="Id of the last revision accepted on this sheet. Every incoming "
        "revision must name it as its parent.",
    )

    # ------------------------------------------------------------------
    # Head management
    # ------------------------------------------------------------------
    @api.model
    def _collab_concrete_models(self):
        registry = self.env.registry
        return [
            name
            for name in registry.descendants(["spreadsheet.abstract"], "_inherit")
            if not registry[name]._abstract and not registry[name]._transient
        ]

    def _collab_stored_revision_id(self):
        self.ensure_one()
        raw = self.spreadsheet_raw or {}
        return (raw.get("revisionId") if isinstance(raw, dict) else None) or (
            DEFAULT_REVISION_ID
        )

    def _collab_rebuild_head(self):
        """Walk the stored chain from the snapshot; archive what is off it."""
        Revision = self.env["spreadsheet.oca.revision"].sudo()
        for record in self:
            head = record._collab_stored_revision_id()
            revisions = Revision.search(
                [("model", "=", record._name), ("res_id", "=", record.id)],
                order="id",
            )
            off_chain = Revision
            for revision in revisions:
                if revision.type == "SNAPSHOT" or revision.server_revision_id != head:
                    off_chain |= revision
                    continue
                head = revision.next_revision_id
            off_chain.write({"active": False, "snapshot_uuid": "legacy-off-chain"})
            record._collab_set_head(head)

    def _collab_set_head(self, revision_id):
        self.ensure_one()
        self.env.cr.execute(
            SQL(
                "UPDATE %s SET collab_head_revision = %s WHERE id = %s",
                SQL.identifier(self._table),
                revision_id,
                self.id,
            )
        )
        self.invalidate_recordset(["collab_head_revision"])

    def _collab_accept(self, parent_id, next_id):
        """Atomically move the head from parent_id to next_id.

        Under PostgreSQL REPEATABLE READ a concurrent request updating the same
        row fails with a serialization error; Odoo retries it, and on retry the
        parent no longer matches, so exactly one of two racing revisions wins.
        """
        self.ensure_one()
        if not self.collab_head_revision:
            self.sudo()._collab_rebuild_head()
        # A concurrent accept raises a serialization failure, which Odoo
        # retries; it is expected, so it is not logged as a bad query.
        self.env.cr.execute(
            SQL(
                "UPDATE %s SET collab_head_revision = %s "
                "WHERE id = %s AND collab_head_revision = %s",
                SQL.identifier(self._table),
                next_id,
                self.id,
                parent_id,
            ),
            log_exceptions=False,
        )
        accepted = self.env.cr.rowcount == 1
        self.invalidate_recordset(["collab_head_revision"])
        return accepted

    # ------------------------------------------------------------------
    # Collaboration protocol
    # ------------------------------------------------------------------
    def send_spreadsheet_message(self, message, access_token=None):
        self.ensure_one()
        message_type = message.get("type")
        if message_type in ORDERED_TYPES:
            self._check_access_spreadsheet("write")
            if not self._collab_accept(
                message["serverRevisionId"], message["nextRevisionId"]
            ):
                return False
            self.env["spreadsheet.oca.revision"].sudo().create(
                {
                    "model": self._name,
                    "res_id": self.id,
                    "type": message_type,
                    "client_id": message.get("clientId"),
                    "next_revision_id": message["nextRevisionId"],
                    "server_revision_id": message["serverRevisionId"],
                    "commands": json.dumps(
                        self._build_spreadsheet_revision_commands_data(message)
                    ),
                }
            )
            self._bus_send(
                "notification", dict(message, id=self.id), subchannel="spreadsheet_oca"
            )
            return True
        if message_type == "SNAPSHOT":
            self._check_access_spreadsheet("write")
            return self._collab_snapshot(message)
        return super().send_spreadsheet_message(message, access_token=access_token)

    def _collab_snapshot(self, message):
        """Consolidate the live chain into the stored sheet.

        The snapshot is itself a revision on the chain: it is accepted only if
        it was taken at the current head, so it can never overwrite an edit the
        sender had not yet received.
        """
        parent_id = message["serverRevisionId"]
        snapshot_id = message["nextRevisionId"]
        if not self._collab_accept(parent_id, snapshot_id):
            return False
        data = dict(message.get("data") or {}, revisionId=snapshot_id)
        Revision = self.env["spreadsheet.oca.revision"].sudo()
        live = Revision.search(
            [("model", "=", self._name), ("res_id", "=", self.id)]
        )
        live.write({"active": False, "snapshot_uuid": snapshot_id})
        Revision.create(
            {
                "model": self._name,
                "res_id": self.id,
                "type": "SNAPSHOT_CREATED",
                "active": False,
                "snapshot_uuid": snapshot_id,
                "client_id": message.get("clientId"),
                "next_revision_id": snapshot_id,
                "server_revision_id": parent_id,
                "commands": json.dumps(
                    {"type": "SNAPSHOT_CREATED", "version": MESSAGE_VERSION}
                ),
            }
        )
        self.sudo().with_context(collab_internal=True).write(
            {"spreadsheet_binary_data": self._collab_encode(data)}
        )
        self._collab_record_version(
            data,
            snapshot_id,
            kind=message.get("collabKind") or "snapshot",
            name=message.get("versionName"),
            revision_count=len(live),
        )
        self._bus_send(
            "notification",
            {
                "type": "SNAPSHOT_CREATED",
                "version": MESSAGE_VERSION,
                "serverRevisionId": parent_id,
                "nextRevisionId": snapshot_id,
                "id": self.id,
            },
            subchannel="spreadsheet_oca",
        )
        return True

    def _collab_reset(self, data, kind, name=None):
        """Replace the sheet wholesale (restore, import, legacy save).

        Connected clients cannot transform onto an unrelated state, so they are
        told to reload instead.
        """
        self.ensure_one()
        new_id = str(uuid.uuid4())
        data = dict(data or {}, revisionId=new_id)
        Revision = self.env["spreadsheet.oca.revision"].sudo()
        Revision.search([("model", "=", self._name), ("res_id", "=", self.id)]).write(
            {"active": False, "snapshot_uuid": new_id}
        )
        self.sudo().with_context(collab_internal=True).write(
            {"spreadsheet_binary_data": self._collab_encode(data)}
        )
        self._collab_set_head(new_id)
        self._collab_record_version(data, new_id, kind=kind, name=name)
        self._bus_send(
            "notification",
            {
                "type": "COLLAB_RESET",
                "reason": kind,
                "by": self.env.user.name,
                "id": self.id,
            },
            subchannel="spreadsheet_oca",
        )
        return new_id

    @api.model
    def _collab_encode(self, data):
        return base64.encodebytes(json.dumps(data).encode("UTF-8"))

    def _collab_record_version(self, data, revision_id, kind, name=None, **kw):
        """Hook: concrete models that keep version history override this."""
        return False

    def get_spreadsheet_revisions_after(self, revision_id):
        """Catch-up for a client whose revision was rejected or who reconnected.

        Returns every revision on the chain after ``revision_id`` (consolidated
        ones included, followed by their SNAPSHOT_CREATED marker), so the
        client can transform its pending work and resend. ``reload`` is set
        when ``revision_id`` is not on the chain at all (sheet restored or
        replaced), in which case the client must reload.
        """
        self.ensure_one()
        self._check_access_spreadsheet("read")
        record = self.sudo()
        if not record.collab_head_revision:
            record._collab_rebuild_head()
        head = record.collab_head_revision
        if revision_id == head:
            return {"messages": [], "reload": False, "head": head}
        revisions = (
            self.env["spreadsheet.oca.revision"]
            .sudo()
            .with_context(active_test=False)
            .search(
                [
                    ("model", "=", self._name),
                    ("res_id", "=", self.id),
                    ("snapshot_uuid", "!=", "legacy-off-chain"),
                ],
                order="id",
            )
        )
        messages, cursor = [], revision_id
        for revision in revisions:
            if revision.server_revision_id == cursor:
                messages.append(revision._collab_payload())
                cursor = revision.next_revision_id
        if cursor != head:
            return {"messages": [], "reload": True, "head": head}
        return {"messages": messages, "reload": False, "head": head}

    def get_spreadsheet_data(self):
        result = super().get_spreadsheet_data()
        record = self.sudo()
        if not record.collab_head_revision:
            record._collab_rebuild_head()
        revisions = self.spreadsheet_revision_ids.sorted("id")
        result["revisions"] = [revision._collab_payload() for revision in revisions]
        threshold = int(
            self.env["ir.config_parameter"].sudo().get_param(
                "spreadsheet_oca_collab.snapshot_threshold", 100
            )
        )
        result["collab"] = {
            "enabled": True,
            "head": record.collab_head_revision,
            "snapshot_requested": result.get("mode") == "normal"
            and threshold > 0
            and len(revisions) >= threshold,
            "presence_timeout": int(
                self.env["ir.config_parameter"].sudo().get_param(
                    "spreadsheet_oca_collab.presence_timeout", 90
                )
            ),
            "res_model": self._name,
            "can_restore": result.get("mode") == "normal",
            "has_versions": self._collab_has_versions(),
            "user_filters": self.env["spreadsheet.oca.user.filter"]._get_entries(self),
        }
        return result

    def collab_save_user_filters(self, entries):
        """Remember this user's filter criteria for the sheet (Excel keeps
        filters with the file; here each person keeps their own view)."""
        self.ensure_one()
        self._check_access_spreadsheet("read")
        self.env["spreadsheet.oca.user.filter"]._set_entries(self, entries or [])
        return True

    def _collab_has_versions(self):
        return False

    # ------------------------------------------------------------------
    # Writes that replace the whole sheet
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for record in records:
            record._collab_set_head(record._collab_stored_revision_id())
        return records

    def write(self, vals):
        if "spreadsheet_raw" in vals and not self.env.context.get("collab_internal"):
            # Stock spreadsheet_oca saves by writing the leaver's copy and
            # deleting every revision. Route it through a reset instead: the
            # history is archived (not deleted) and connected clients reload.
            vals = dict(vals)
            raw = vals.pop("spreadsheet_raw")
            result = super().write(vals) if vals else True
            for record in self:
                record._collab_reset(raw, kind="replace")
            return result
        return super().write(vals)
