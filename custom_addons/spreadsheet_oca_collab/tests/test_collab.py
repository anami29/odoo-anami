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
        # Alice may roll the sheet back; Bob edits it but holds no such right.
        cls.alice = new_test_user(
            cls.env,
            "alice_t",
            groups=groups + ",spreadsheet_oca_collab.group_version_restore",
        )
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

    def test_restore_without_the_right_is_refused(self):
        """Editing a sheet is not the same as discarding everyone's work."""
        head = self._head()
        a = _rev(head, "a")
        self.sheet.with_user(self.alice).send_spreadsheet_message(a)
        self.sheet.with_user(self.alice).send_spreadsheet_message(
            {"type": "SNAPSHOT", "serverRevisionId": a["nextRevisionId"],
             "nextRevisionId": "s1", "data": {"version": 1, "sheets": []}})
        version = self.sheet.collab_version_ids[:1]
        # Bob is a contributor, so he can write to the sheet, and the button is
        # hidden for him - the method must refuse him anyway.
        with self.assertRaises(AccessError):
            version.with_user(self.bob).action_restore()

    def test_user_filters_roundtrip(self):
        entries = [{"sheetId": "sheet1", "col": 1, "row": 0,
                    "criteria": {"type": "top", "top": True, "n": 3, "percent": False}}]
        self.sheet.with_user(self.bob).collab_save_user_filters(entries)
        data = self.sheet.with_user(self.bob).get_spreadsheet_data()
        self.assertEqual(data["collab"]["user_filters"], entries)
        data_alice = self.sheet.with_user(self.alice).get_spreadsheet_data()
        self.assertEqual(data_alice["collab"]["user_filters"], [])

    def test_typing_lock(self):
        Lock = self.env["spreadsheet.oca.cell.lock"]
        cell = {"sheetId": "sheet1", "col": 2, "row": 2}

        def typing(user, client, position):
            return self.sheet.with_user(user).collab_cell_typing(client, position)

        # Being on a cell does not lock it
        self.sheet.with_user(self.alice).send_spreadsheet_message(
            {"type": "CLIENT_MOVED", "version": 1,
             "client": {"id": "a", "name": "A", "position": cell}})
        self.assertFalse(Lock._holders(self.sheet))
        # Alice starts typing: Bob is refused
        self.assertTrue(typing(self.alice, "a", cell)["ok"])
        res = typing(self.bob, "b", cell)
        self.assertFalse(res["ok"])
        self.assertEqual(res["holder"]["clientId"], "a")
        # Bob can type elsewhere at the same time
        self.assertTrue(typing(self.bob, "b", {"sheetId": "sheet1", "col": 4, "row": 2})["ok"])
        # Alice finishes: the cell is free; Bob claiming it releases his other cell
        typing(self.alice, "a", False)
        self.assertTrue(typing(self.bob, "b", cell)["ok"])
        holders = Lock._holders(self.sheet)
        self.assertEqual([(h["col"], h["clientId"]) for h in holders], [(2, "b")])
        # Leaving drops the lock
        self.sheet.with_user(self.bob).send_spreadsheet_message(
            {"type": "CLIENT_LEFT", "clientId": "b", "version": 1})
        self.assertFalse(Lock._holders(self.sheet))

    def test_reader_does_not_lock(self):
        reader = new_test_user(self.env, "reader_t",
                               groups="base.group_user,spreadsheet_oca.group_user")
        self.sheet.with_user(self.alice).write({"reader_ids": [(4, reader.id)]})
        res = self.sheet.with_user(reader).collab_cell_typing(
            "r", {"sheetId": "sheet1", "col": 1, "row": 1})
        self.assertFalse(res["ok"])
        self.assertFalse(self.env["spreadsheet.oca.cell.lock"]._holders(self.sheet))
