# Copyright 2026 RLFB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
import uuid

from odoo.exceptions import AccessError
from odoo.tests.common import TransactionCase, new_test_user, tagged


def _rev(parent, content="x", col=0, row=0):
    return {
        "type": "REMOTE_REVISION",
        "version": 1,
        "serverRevisionId": parent,
        "nextRevisionId": str(uuid.uuid4()),
        "clientId": "c-" + content,
        "commands": [
            {"type": "UPDATE_CELL", "sheetId": "sheet1", "col": col, "row": row,
             "content": content}
        ],
    }


@tagged("post_install", "-at_install")
class TestSpreadsheetCollab(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        groups = "base.group_user,spreadsheet_oca.group_user"
        cls.alice = new_test_user(cls.env, "alice_t", groups=groups)
        cls.bob = new_test_user(cls.env, "bob_t", groups=groups)
        cls.sheet = (
            cls.env["spreadsheet.spreadsheet"]
            .with_user(cls.alice)
            .create({"name": "T", "contributor_ids": [(6, 0, [cls.bob.id])]})
        )

    def _head(self):
        self.sheet.invalidate_recordset()
        return self.sheet.sudo().collab_head_revision

    def test_same_parent_only_one_accepted(self):
        head = self._head()
        self.assertEqual(head, "START_REVISION")
        a, b = _rev(head, "a"), _rev(head, "b", col=1)
        self.assertTrue(self.sheet.with_user(self.alice).send_spreadsheet_message(a))
        self.assertFalse(self.sheet.with_user(self.bob).send_spreadsheet_message(b))
        self.assertEqual(self._head(), a["nextRevisionId"])

    def test_catch_up_and_resend(self):
        head = self._head()
        a, b = _rev(head, "a"), _rev(head, "b", col=1)
        self.sheet.with_user(self.alice).send_spreadsheet_message(a)
        self.sheet.with_user(self.bob).send_spreadsheet_message(b)
        missed = self.sheet.with_user(self.bob).get_spreadsheet_revisions_after(head)
        self.assertFalse(missed["reload"])
        self.assertEqual([m["nextRevisionId"] for m in missed["messages"]],
                         [a["nextRevisionId"]])
        self.assertEqual(missed["messages"][0]["clientId"], "c-a")
        b["serverRevisionId"] = missed["head"]
        self.assertTrue(self.sheet.with_user(self.bob).send_spreadsheet_message(b))

    def test_snapshot_at_head_only(self):
        head = self._head()
        a = _rev(head, "a")
        self.sheet.with_user(self.alice).send_spreadsheet_message(a)
        stale = {"type": "SNAPSHOT", "serverRevisionId": head, "nextRevisionId": "s0",
                 "data": {"sheets": []}}
        self.assertFalse(self.sheet.with_user(self.bob).send_spreadsheet_message(stale))
        good = {"type": "SNAPSHOT", "serverRevisionId": a["nextRevisionId"],
                "nextRevisionId": "s1", "versionName": "v1",
                "data": {"version": 1, "sheets": [{"id": "sheet1", "name": "S",
                                                   "cells": {"A1": {"content": "a"}}}]}}
        self.assertTrue(self.sheet.with_user(self.bob).send_spreadsheet_message(good))
        self.sheet.invalidate_recordset()
        self.assertEqual(self.sheet.spreadsheet_raw["revisionId"], "s1")
        self.assertFalse(self.sheet.spreadsheet_revision_ids)
        self.assertEqual(self.sheet.collab_version_ids.name, "v1")

    def test_full_write_archives_instead_of_deleting(self):
        head = self._head()
        self.sheet.with_user(self.alice).send_spreadsheet_message(_rev(head, "a"))
        self.sheet.with_user(self.bob).write({"spreadsheet_raw": {"sheets": []}})
        archived = self.env["spreadsheet.oca.revision"].with_context(
            active_test=False).search([("res_id", "=", self.sheet.id),
                                       ("model", "=", self.sheet._name)])
        self.assertTrue(archived)
        self.assertFalse(any(archived.mapped("active")))
        self.assertTrue(self.sheet.get_spreadsheet_revisions_after(head)["reload"])

    def test_only_owner_changes_sharing(self):
        with self.assertRaises(AccessError):
            self.sheet.with_user(self.bob).write({"reader_ids": [(4, self.bob.id)]})
        self.sheet.with_user(self.alice).write({"reader_ids": [(4, self.bob.id)]})

    def test_restore_version(self):
        head = self._head()
        a = _rev(head, "a")
        self.sheet.with_user(self.alice).send_spreadsheet_message(a)
        self.sheet.with_user(self.alice).send_spreadsheet_message(
            {"type": "SNAPSHOT", "serverRevisionId": a["nextRevisionId"],
             "nextRevisionId": "s1", "data": {"version": 1, "sheets": []}})
        version = self.sheet.collab_version_ids[:1]
        self.sheet.with_user(self.alice).send_spreadsheet_message(_rev("s1", "later"))
        version.with_user(self.alice).action_restore()
        self.assertNotEqual(self._head(), "s1")
        self.assertFalse(self.sheet.spreadsheet_revision_ids)

    def test_user_filters_roundtrip(self):
        entries = [{"sheetId": "sheet1", "col": 1, "row": 0,
                    "criteria": {"type": "top", "top": True, "n": 3, "percent": False}}]
        self.sheet.with_user(self.bob).collab_save_user_filters(entries)
        data = self.sheet.with_user(self.bob).get_spreadsheet_data()
        self.assertEqual(data["collab"]["user_filters"], entries)
        data_alice = self.sheet.with_user(self.alice).get_spreadsheet_data()
        self.assertEqual(data_alice["collab"]["user_filters"], [])
