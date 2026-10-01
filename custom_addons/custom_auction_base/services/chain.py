# -*- coding: utf-8 -*-
"""Bid ledger hash chain — TSD-AUC-001 #7.2.

Deliberately free of any Odoo import. The chain is the integrity property on
which the evidentiary value of the whole platform rests, and keeping it a pure
module means it can be tested and audited without a database.

Canonical payload rules (TSD #7.2):
  * JSON, sorted keys, no whitespace, UTF-8
  * decimals rendered as fixed-precision strings at the event precision,
    never as floats -- float repr is not stable across platforms
  * a payload version marker, so that a schema change adding a field does
    not invalidate hashes computed before that field existed (TSD #17.4)
"""
import hashlib
import json
from decimal import Decimal, ROUND_HALF_UP

ZERO64 = "0" * 64

#: Incremented only when the canonical payload shape changes. Records store
#: the version they were written under. NEVER renumber an existing version.
#:
#: Verification recomputes the chain link from the STORED payload_digest, not
#: by re-serialising the record, so extending the payload never invalidates
#: records written under an earlier version.
#:
#:   1  price_unit and qty_offered per line
#:   2  adds the offered delivery schedule per line
PAYLOAD_VERSION = 2


class ChainError(Exception):
    """Raised when chain verification finds a divergence or a gap."""


def quantize(value, precision=6):
    """Render a numeric value as a stable fixed-precision string.

    Floats are never serialised directly: ``repr(0.1 + 0.2)`` is not a
    property you want inside a tamper-evidence hash.
    """
    if value is None:
        return None
    exp = Decimal(1).scaleb(-precision)
    return str(Decimal(str(value)).quantize(exp, rounding=ROUND_HALF_UP))


def canonical_payload(data, precision=6, version=PAYLOAD_VERSION):
    """Return the canonical JSON serialisation of a bid payload.

    ``data`` is a plain dict. Numeric leaves are quantized; everything else
    is serialised as-is. The version marker is embedded so verification can
    reproduce historical hashes exactly.
    """
    def _norm(obj):
        if isinstance(obj, dict):
            return {k: _norm(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [_norm(v) for v in obj]
        if isinstance(obj, bool):
            return obj
        if isinstance(obj, (int, float, Decimal)):
            return quantize(obj, precision)
        return obj

    envelope = {"_v": version, "payload": _norm(data)}
    return json.dumps(
        envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def payload_digest(canonical_bytes):
    """SHA-256 over the canonical payload, hex lowercase."""
    return hashlib.sha256(canonical_bytes).hexdigest()


def record_hash(prev_hash, digest, chain_seq, server_ts_iso):
    """Chain link. TSD #7.2.

    ``SHA-256(prev_hash || payload_digest || chain_seq || server_ts ISO-8601)``
    """
    if not prev_hash or len(prev_hash) != 64:
        raise ChainError("prev_hash must be 64 hex characters")
    material = "%s%s%d%s" % (prev_hash, digest, int(chain_seq), server_ts_iso)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def verify(records):
    """Walk a lot's ledger and verify the chain.

    ``records`` is an iterable of dicts ordered by ``chain_seq`` ascending,
    each carrying: chain_seq, prev_hash, payload_digest, record_hash,
    server_ts_iso.

    Returns ``(ok, findings)``. Findings report the first divergence per
    class; a gap in chain_seq is reported as a break even where every hash
    is individually valid, because a deleted record leaves valid neighbours.
    """
    findings = []
    expected_seq = 1
    expected_prev = ZERO64

    for rec in records:
        seq = int(rec["chain_seq"])
        if seq != expected_seq:
            findings.append({
                "type": "sequence_gap",
                "at": seq,
                "expected": expected_seq,
                "detail": "ledger sequence is not contiguous",
            })
            expected_seq = seq

        if rec["prev_hash"] != expected_prev:
            findings.append({
                "type": "chain_break",
                "at": seq,
                "expected": expected_prev,
                "found": rec["prev_hash"],
                "detail": "prev_hash does not match the preceding record",
            })

        recomputed = record_hash(
            rec["prev_hash"], rec["payload_digest"], seq, rec["server_ts_iso"]
        )
        if recomputed != rec["record_hash"]:
            findings.append({
                "type": "hash_mismatch",
                "at": seq,
                "expected": recomputed,
                "found": rec["record_hash"],
                "detail": "record_hash does not match its own inputs",
            })

        expected_prev = rec["record_hash"]
        expected_seq = seq + 1

    return (not findings), findings
