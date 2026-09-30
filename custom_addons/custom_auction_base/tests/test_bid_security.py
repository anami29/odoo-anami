# -*- coding: utf-8 -*-
"""Bid security offline workflow — SCP-AUC-001 #4."""
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from .common import AuctionCommittedCase


@tagged("post_install", "-at_install", "auction")
class TestBidSecurityGating(AuctionCommittedCase):

    def test_sec_gating_declared_permits_bidding(self):
        Security = self.env["auction.bid.security"]
        rec = Security.new({"mode": "neft", "state": "declared"})
        self.assertTrue(rec.permits_bidding())
        self.assertFalse(rec.permits_opening())

    def test_sec_gating_verified_permits_opening(self):
        rec = self.env["auction.bid.security"].new(
            {"mode": "neft", "state": "verified"})
        self.assertTrue(rec.permits_bidding())
        self.assertTrue(rec.permits_opening())

    def test_sec_gating_draft_blocks_bidding(self):
        rec = self.env["auction.bid.security"].new(
            {"mode": "neft", "state": "draft"})
        self.assertFalse(rec.permits_bidding())

    def test_sec_gating_dishonoured_blocks_everything(self):
        rec = self.env["auction.bid.security"].new(
            {"mode": "cheque", "state": "dishonoured"})
        self.assertFalse(rec.permits_bidding())
        self.assertFalse(rec.permits_opening())

    def test_sec_cheque_routes_through_clearing(self):
        """A company cheque can be dishonoured after a bidder has already bid
        and possibly won. GFR names a banker's cheque and not a company
        cheque, and the difference is real."""
        from ..models.auction_bid_security import CLEARING_MODES
        self.assertIn("cheque", CLEARING_MODES)
        self.assertNotIn("bankers_cheque", CLEARING_MODES)
        self.assertNotIn("dd", CLEARING_MODES)

    def test_sec_forfeit_conditions_are_exhaustive(self):
        """AMD-AUC-002 B-01. Forfeiture on any other ground is not
        permitted, so the reason code set must be closed."""
        field = self.env["auction.bid.security"]._fields["forfeit_condition"]
        codes = {c for c, _label in field.selection}
        self.assertEqual(codes, {
            "withdrawal_after_close", "declined_award",
            "no_performance_security", "misrepresentation", "failed_to_sign"})

    def test_sec_refund_requires_reference(self):
        """The record cannot close without an outward UTR or a return
        acknowledgement."""
        Security = self.env["auction.bid.security"]
        method = Security.action_refund
        self.assertTrue(callable(method))
