# -*- coding: utf-8 -*-
"""TST-LDG — ledger append-only and hash chain.

The integrity property on which the evidentiary value of the platform rests.
"""
from odoo import SUPERUSER_ID, api
from odoo.exceptions import AccessError
from odoo.tests import tagged

from .common import AuctionCommittedCase
from ..services import chain as chain_svc


@tagged("post_install", "-at_install", "auction")
class TestLedgerAppendOnly(AuctionCommittedCase):

    def _a_bid(self):
        return self.env["auction.bid"].search([], limit=1)

    def test_ldg_001_write_refused_normal_user(self):
        bid = self._a_bid()
        if bid:
            with self.assertRaises(AccessError):
                bid.write({"state": "void"})

    def test_ldg_002_write_refused_admin(self):
        """Administrator is not exempt."""
        bid = self._a_bid()
        if bid:
            admin = self.env.ref("base.user_admin")
            with self.assertRaises(AccessError):
                bid.with_user(admin).write({"state": "void"})

    def test_ldg_003_write_refused_superuser(self):
        """No SUPERUSER_ID bypass exists."""
        bid = self._a_bid()
        if bid:
            with self.assertRaises(AccessError):
                bid.with_user(SUPERUSER_ID).write({"state": "void"})

    def test_ldg_004_write_refused_with_context_key(self):
        """No context key is honoured. Developers will look for one."""
        bid = self._a_bid()
        if bid:
            for key in ("bypass_append_only", "force_write", "install_mode",
                        "tracking_disable"):
                with self.assertRaises(AccessError):
                    bid.with_context(**{key: True}).write({"state": "void"})

    def test_ldg_005_unlink_refused(self):
        bid = self._a_bid()
        if bid:
            with self.assertRaises(AccessError):
                bid.unlink()

    def test_ldg_006_line_unlink_refused(self):
        line = self.env["auction.bid.line"].search([], limit=1)
        if line:
            with self.assertRaises(AccessError):
                line.unlink()

    def test_ldg_009_set_state_allowlist_enforced(self):
        """_set_state is the only mutation path and its allowlist is real,
        not documented."""
        from odoo.exceptions import ValidationError
        bid = self._a_bid()
        if bid:
            with self.assertRaises(ValidationError):
                bid._set_state("arbitrary_state")

    def test_ldg_016_no_mail_thread_on_ledger(self):
        """mail.thread writes message_main_attachment_id on its own record
        and would raise against our own guard. Static check."""
        self.assertNotIn("mail.thread", self.env["auction.bid"]._inherit or [])
        self.assertFalse(
            hasattr(self.env["auction.bid"], "message_main_attachment_id"),
            "The ledger must not carry a mail mixin.")

    def test_ldg_017_no_stored_computes_on_ledger(self):
        """Recomputation is a write."""
        for model in ("auction.bid", "auction.bid.line"):
            for name, field in self.env[model]._fields.items():
                self.assertFalse(
                    field.compute and field.store,
                    "%s.%s is a stored compute on the ledger" % (model, name))

    def test_ldg_018_numeric_column_types(self):
        """TSD #3.3 — never double precision on a ranked column."""
        self.env.cr.execute("""
            SELECT column_name, data_type FROM information_schema.columns
             WHERE table_name = 'auction_bid_line'
               AND column_name IN ('norm_value','price_unit','landed_value',
                                   'price_base','composite_score')
        """)
        for column, dtype in self.env.cr.fetchall():
            self.assertEqual(dtype, "numeric",
                             "%s is %s, expected numeric" % (column, dtype))


@tagged("post_install", "-at_install", "auction")
class TestHashChain(AuctionCommittedCase):
    """Chain verification against mutation, deletion and fabrication."""

    def _records(self, n=5):
        out, prev = [], chain_svc.ZERO64
        for seq in range(1, n + 1):
            digest = chain_svc.payload_digest(
                chain_svc.canonical_payload({"seq": seq, "price": 100 + seq}))
            ts = "2026-09-29T10:%02d:00" % seq
            rh = chain_svc.record_hash(prev, digest, seq, ts)
            out.append({"chain_seq": seq, "prev_hash": prev,
                        "payload_digest": digest, "record_hash": rh,
                        "server_ts_iso": ts})
            prev = rh
        return out

    def test_ldg_010_clean_chain_verifies(self):
        ok, findings = chain_svc.verify(self._records())
        self.assertTrue(ok)
        self.assertFalse(findings)

    def test_ldg_011_mutation_detected(self):
        recs = self._records()
        recs[2]["payload_digest"] = "f" * 64
        ok, findings = chain_svc.verify(recs)
        self.assertFalse(ok)
        self.assertTrue(any(f["type"] == "hash_mismatch" for f in findings))

    def test_ldg_012_deletion_detected_as_gap(self):
        """A deleted record leaves valid neighbours; the gap is the evidence."""
        recs = self._records()
        del recs[2]
        ok, findings = chain_svc.verify(recs)
        self.assertFalse(ok)
        self.assertTrue(any(f["type"] == "sequence_gap" for f in findings))

    def test_ldg_013_fabrication_detected(self):
        recs = self._records()
        recs[3]["record_hash"] = "a" * 64
        ok, findings = chain_svc.verify(recs)
        self.assertFalse(ok)

    def test_ldg_014_payload_key_order_irrelevant(self):
        a = chain_svc.canonical_payload({"x": 1, "y": 2})
        b = chain_svc.canonical_payload({"y": 2, "x": 1})
        self.assertEqual(a, b)
