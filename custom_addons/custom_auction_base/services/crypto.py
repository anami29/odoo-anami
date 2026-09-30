# -*- coding: utf-8 -*-
"""Sealed envelope cryptography — TSD-AUC-001 #8.

Threat model explicitly includes a database administrator and an Odoo
administrator. Record rules are therefore not relied on for this property:
where an event is represented to bidders as sealed, the commercial values are
cryptographically unavailable until the scheduled opening.

Like ``chain``, this module carries no Odoo import so it can be exercised and
reviewed on its own.

Design
------
* AES-256-GCM. Unique 96-bit nonce per envelope from ``os.urandom``.
* Event id, lot id, participant id and chain_seq are bound as GCM additional
  authenticated data, so an envelope cannot be moved between records without
  the open failing.
* A per-event data key (DEK) is wrapped by a key encryption key (KEK) held
  outside the application database.
* Dual control is a two-of-two XOR split of the unwrap credential: neither
  share alone yields any information about the DEK.
"""
import hashlib
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

DEK_BYTES = 32          # AES-256
NONCE_BYTES = 12        # 96-bit, the GCM recommendation
PBKDF2_ITERATIONS = 600_000


class CryptoError(Exception):
    """Raised on any sealing or opening failure.

    Carries no plaintext and no key material -- TSD #8 requires that a forced
    failure produce nothing sensitive in a traceback or log.
    """


def generate_dek():
    """Fresh per-event data encryption key."""
    return os.urandom(DEK_BYTES)


def build_aad(event_id, lot_id, participant_id, chain_seq):
    """Associated authenticated data binding an envelope to its record.

    Moving ciphertext from one bid row to another changes the AAD and the
    open fails authentication. This is what stops envelope substitution.
    """
    material = "e%d|l%d|p%d|s%d" % (
        int(event_id), int(lot_id), int(participant_id), int(chain_seq)
    )
    return material.encode("utf-8")


def seal(plaintext, dek, aad):
    """Encrypt. Returns ``(ciphertext, nonce)``; the GCM tag is appended to
    the ciphertext by the AESGCM implementation.
    """
    if not isinstance(plaintext, bytes):
        raise CryptoError("plaintext must be bytes")
    if len(dek) != DEK_BYTES:
        raise CryptoError("data key has the wrong length")
    nonce = os.urandom(NONCE_BYTES)
    try:
        ct = AESGCM(dek).encrypt(nonce, plaintext, aad)
    except Exception:
        # Deliberately opaque: no plaintext, no key material in the traceback.
        raise CryptoError("sealing failed")
    return ct, nonce


def open_envelope(ciphertext, nonce, dek, aad):
    """Decrypt and authenticate. Raises ``CryptoError`` on any failure,
    including a tampered tag, a wrong key or mismatched associated data.
    """
    if len(dek) != DEK_BYTES:
        raise CryptoError("data key has the wrong length")
    try:
        return AESGCM(dek).decrypt(nonce, ciphertext, aad)
    except Exception:
        raise CryptoError("envelope failed authentication")


# ---------------------------------------------------------------------------
# Key wrapping and dual control
# ---------------------------------------------------------------------------

def derive_share(passphrase, salt):
    """Derive one opener's share from their credential.

    PBKDF2-HMAC-SHA256. The salt is per-event and stored with the event; it
    is not secret, it exists to stop precomputation across events.
    """
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=DEK_BYTES,
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
    )
    return kdf.derive(passphrase.encode("utf-8"))


def split_secret(secret):
    """Two-of-two split. Returns ``(share_a, share_b)``.

    ``share_a`` is random; ``share_b`` is ``secret XOR share_a``. Either share
    alone is indistinguishable from random, so a single opener learns nothing.
    """
    share_a = os.urandom(len(secret))
    share_b = bytes(x ^ y for x, y in zip(secret, share_a))
    return share_a, share_b


def combine_shares(share_a, share_b):
    """Reconstruct a secret from its two shares."""
    if len(share_a) != len(share_b):
        raise CryptoError("shares are not the same length")
    return bytes(x ^ y for x, y in zip(share_a, share_b))


def wrap_dek(dek, kek):
    """Wrap the event data key under the key encryption key.

    The KEK comes from outside the application database -- an environment
    variable in the SME profile, an external key store in the full profile.
    It is never read from ``ir.config_parameter``.
    """
    if len(kek) != DEK_BYTES:
        raise CryptoError("key encryption key has the wrong length")
    nonce = os.urandom(NONCE_BYTES)
    try:
        ct = AESGCM(kek).encrypt(nonce, dek, b"dek-wrap-v1")
    except Exception:
        raise CryptoError("key wrapping failed")
    return nonce + ct


def unwrap_dek(wrapped, kek):
    """Recover the event data key. Raises on any failure."""
    if len(kek) != DEK_BYTES:
        raise CryptoError("key encryption key has the wrong length")
    nonce, ct = wrapped[:NONCE_BYTES], wrapped[NONCE_BYTES:]
    try:
        return AESGCM(kek).decrypt(nonce, ct, b"dek-wrap-v1")
    except Exception:
        raise CryptoError("key unwrapping failed")


def kek_from_environment(var_name="AUCTION_KEK"):
    """Read the KEK from the process environment.

    SME profile per SCP-AUC-001 #3. The value is a hex-encoded 32-byte key.
    Deliberately NOT sourced from ir.config_parameter: a key in the database
    defeats the property this module exists to provide.
    """
    raw = os.environ.get(var_name)
    if not raw:
        raise CryptoError(
            "key encryption key is not configured; set the %s environment "
            "variable on the application host" % var_name
        )
    try:
        kek = bytes.fromhex(raw.strip())
    except ValueError:
        raise CryptoError("key encryption key is not valid hex")
    if len(kek) != DEK_BYTES:
        raise CryptoError("key encryption key must be 32 bytes (64 hex chars)")
    return kek


def fingerprint(key):
    """Non-reversible identifier for a key, safe to log and store.

    Used to assert at opening that the key reconstructed from shares is the
    one the envelopes were sealed under, without ever logging the key.
    """
    return hashlib.sha256(b"fp-v1" + key).hexdigest()[:16]
