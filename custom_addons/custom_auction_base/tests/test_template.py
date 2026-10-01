# -*- coding: utf-8 -*-
"""Template locking and archiving. BR-TPL-007, BR-TPL-009.

The lock is the property that lets an event answer what it was built from.
A guard on the parent that does not reach its children is the same defect
with a longer route to it, so the children are tested explicitly.
"""
from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import AuctionCommittedCase


@tagged("post_install", "-at_install", "auction")
class TestTemplateLock(AuctionCommittedCase):

    def setUp(self):
        super().setUp()
        self.category = self.env["auction.category"].create({
            "name": "Test Category", "category_type": "goods"})
        self.rules = self.env.ref("custom_auction_base.rule_set_sealed_default")
        self.template = self.env["auction.event.template"].create({
            "name": "Test Template",
            "direction": "reverse",
            "mechanism": "sealed_rev",
            "category_id": self.category.id,
            "rule_set_id": self.rules.id,
            "security_basis": "percent",
            "security_percent": 2.0,
        })
        self.lot = self.env["auction.event.template.lot"].create({
            "template_id": self.template.id, "name": "Lot 1"})
        self.line = self.env["auction.event.template.line"].create({
            "template_lot_id": self.lot.id, "name": "Item 1", "product_qty": 10})

    # -- draft ----------------------------------------------------------
    def test_tpl_001_draft_is_editable(self):
        self.template.bid_window_days = 14
        self.assertEqual(self.template.bid_window_days, 14)

    def test_tpl_002_draft_cannot_create_events(self):
        with self.assertRaises(UserError):
            self.template.action_create_event()

    def test_tpl_003_incomplete_template_cannot_lock(self):
        self.line.unlink()
        self.lot.unlink()
        with self.assertRaises(UserError):
            self.template.action_lock()

    # -- locked ---------------------------------------------------------
    def test_tpl_004_lock_succeeds_when_complete(self):
        self.template.action_lock()
        self.assertEqual(self.template.state, "locked")

    def test_tpl_005_locked_template_refuses_edit(self):
        self.template.action_lock()
        with self.assertRaises(UserError):
            self.template.bid_window_days = 30

    def test_tpl_006_locked_template_refuses_mechanism_change(self):
        self.template.action_lock()
        with self.assertRaises(UserError):
            self.template.mechanism = "sealed_rev_nr"

    def test_tpl_007_lock_reaches_lots(self):
        """The guard on the parent must reach its children."""
        self.template.action_lock()
        with self.assertRaises(UserError):
            self.lot.name = "Renamed"

    def test_tpl_008_lock_reaches_lines(self):
        self.template.action_lock()
        with self.assertRaises(UserError):
            self.line.product_qty = 999

    def test_tpl_009_lock_blocks_new_lines(self):
        self.template.action_lock()
        with self.assertRaises(UserError):
            self.env["auction.event.template.line"].create({
                "template_lot_id": self.lot.id, "name": "Sneaked in",
                "product_qty": 1})

    def test_tpl_010_lock_blocks_deleting_lines(self):
        self.template.action_lock()
        with self.assertRaises(UserError):
            self.line.unlink()

    def test_tpl_011_bookkeeping_still_writable_when_locked(self):
        """usage_count, last_used and active must stay writable or the
        template could never be used or archived."""
        self.template.action_lock()
        self.template.usage_count = 3
        self.template.active = False
        self.assertEqual(self.template.usage_count, 3)
        self.assertFalse(self.template.active)

    def test_tpl_012_no_unlock_action_exists(self):
        """Unlocking is deliberately not offered. New Version is the route."""
        self.assertFalse(hasattr(self.template, "action_unlock"))

    # -- versioning -----------------------------------------------------
    def test_tpl_013_new_version_is_a_draft(self):
        self.template.action_lock()
        action = self.template.action_new_version()
        draft = self.env["auction.event.template"].browse(action["res_id"])
        self.assertEqual(draft.state, "draft")
        self.assertEqual(draft.version, self.template.version + 1)
        self.assertEqual(draft.usage_count, 0)

    def test_tpl_014_new_version_is_editable(self):
        self.template.action_lock()
        draft = self.env["auction.event.template"].browse(
            self.template.action_new_version()["res_id"])
        draft.bid_window_days = 21
        self.assertEqual(draft.bid_window_days, 21)

    def test_tpl_015_original_untouched_by_new_version(self):
        self.template.action_lock()
        original_window = self.template.bid_window_days
        draft = self.env["auction.event.template"].browse(
            self.template.action_new_version()["res_id"])
        draft.bid_window_days = 99
        self.assertEqual(self.template.bid_window_days, original_window)

    def test_tpl_016_version_copies_content(self):
        self.template.action_lock()
        draft = self.env["auction.event.template"].browse(
            self.template.action_new_version()["res_id"])
        self.assertEqual(draft.lot_count, self.template.lot_count)
        self.assertEqual(draft.line_count, self.template.line_count)

    def test_tpl_017_version_name_does_not_stack(self):
        """v2 taken from v2 is v3, not 'Name (v2) (v3)'."""
        self.template.action_lock()
        v2 = self.env["auction.event.template"].browse(
            self.template.action_new_version()["res_id"])
        v2.action_lock()
        v3 = self.env["auction.event.template"].browse(
            v2.action_new_version()["res_id"])
        self.assertNotIn("(v2) (v3)", v3.name)

    # -- archiving ------------------------------------------------------
    def test_tpl_018_archive_a_locked_template(self):
        self.template.action_lock()
        self.template.action_archive_template()
        self.assertFalse(self.template.active)

    def test_tpl_019_archived_template_cannot_create_events(self):
        self.template.action_lock()
        self.template.action_archive_template()
        with self.assertRaises(UserError):
            self.template.action_create_event()

    def test_tpl_020_unarchive_restores_use(self):
        self.template.action_lock()
        self.template.action_archive_template()
        self.template.action_unarchive_template()
        self.assertTrue(self.template.active)

    def test_tpl_021_used_template_cannot_be_deleted(self):
        """Archiving is the retirement route; deletion would orphan the
        provenance on events already created."""
        self.template.action_lock()
        self.template.usage_count = 1
        with self.assertRaises(UserError):
            self.template.unlink()

    def test_tpl_022_unused_template_can_be_deleted(self):
        self.assertTrue(self.template.unlink())
