# -*- coding: utf-8 -*-
"""TST-CRY — sealed envelope cryptography.

Threat model includes a database administrator. These tests assert the
ABSENCE of plaintext, not only the presence of ciphertext.
"""
from odoo.tests import tagged

from .common import AuctionCommittedCase
from ..services import crypto as crypto_svc


@tagged("post_install", "-at_install", "auction")
class TestEnvelope(AuctionCommittedCase):

    def setUp(self):
        super().setUp()
        self.dek = crypto_svc.generate_dek()
        self.aad = crypto_svc.build_aad(1, 2, 3, 4)
        self.plain = b'{"lines":[{"line":7,"price_unit":"429.000000"}]}'

    def test_cry_round_trip(self):
        ct, nonce = crypto_svc.seal(self.plain, self.dek, self.aad)
        self.assertNotIn(b"429", ct, "plaintext visible in ciphertext")
        self.assertEqual(
            crypto_svc.open_envelope(ct, nonce, self.dek, self.aad), self.plain)

    def test_cry_009_envelope_substitution_rejected(self):
        """Moving ciphertext between records changes the AAD.

        This is the test that cannot be written by the agent that wrote the
        sealing code: a round-trip test passes happily while the AAD binding
        is absent entirely.
        """
        ct, nonce = crypto_svc.seal(self.plain, self.dek, self.aad)
        other = crypto_svc.build_aad(1, 2, 99, 4)      # different participant
        with self.assertRaises(crypto_svc.CryptoError):
            crypto_svc.open_envelope(ct, nonce, self.dek, other)

    def test_cry_010_truncated_tag_rejected(self):
        ct, nonce = crypto_svc.seal(self.plain, self.dek, self.aad)
        with self.assertRaises(crypto_svc.CryptoError):
            crypto_svc.open_envelope(ct[:-4], nonce, self.dek, self.aad)

    def test_cry_wrong_key_rejected(self):
        ct, nonce = crypto_svc.seal(self.plain, self.dek, self.aad)
        with self.assertRaises(crypto_svc.CryptoError):
            crypto_svc.open_envelope(
                ct, nonce, crypto_svc.generate_dek(), self.aad)

    def test_cry_005_single_share_insufficient(self):
        a, b = crypto_svc.split_secret(self.dek)
        self.assertNotEqual(a, self.dek)
        self.assertNotEqual(b, self.dek)
        self.assertEqual(crypto_svc.combine_shares(a, b), self.dek)

    def test_cry_nonce_is_unique(self):
        nonces = set()
        for _ in range(200):
            _, nonce = crypto_svc.seal(self.plain, self.dek, self.aad)
            nonces.add(nonce)
        self.assertEqual(len(nonces), 200, "nonce reuse under AES-GCM")

    def test_cry_wrap_unwrap(self):
        kek = crypto_svc.generate_dek()
        self.assertEqual(
            crypto_svc.unwrap_dek(crypto_svc.wrap_dek(self.dek, kek), kek),
            self.dek)

    def test_cry_003_no_plaintext_in_exception(self):
        """A forced failure must produce nothing sensitive in a traceback."""
        try:
            crypto_svc.seal(self.plain, b"short-key", self.aad)
        except crypto_svc.CryptoError as exc:
            self.assertNotIn("429", str(exc))
            self.assertNotIn(self.plain.decode(), str(exc))
        else:
            self.fail("expected CryptoError")
