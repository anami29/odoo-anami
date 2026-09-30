# -*- coding: utf-8 -*-
"""Odoo-free execution of the two highest-risk algorithms.

``chain`` and ``crypto`` carry no Odoo import precisely so they can be run
and audited without a database. This script is what a reviewer runs before
trusting anything else in the module.

    python3 tests/standalone_check.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))

import chain as chain_svc          # noqa: E402
import crypto as crypto_svc        # noqa: E402

FAILED = []


def check(name, condition):
    print("  %-58s %s" % (name, "PASS" if condition else "FAIL"))
    if not condition:
        FAILED.append(name)


def main():
    print("\nHash chain")
    recs, prev = [], chain_svc.ZERO64
    for seq in range(1, 21):
        d = chain_svc.payload_digest(
            chain_svc.canonical_payload({"seq": seq, "price": 400 + seq * 0.5}))
        ts = "2026-09-29T10:%02d:00" % seq
        rh = chain_svc.record_hash(prev, d, seq, ts)
        recs.append({"chain_seq": seq, "prev_hash": prev, "payload_digest": d,
                     "record_hash": rh, "server_ts_iso": ts})
        prev = rh

    ok, _f = chain_svc.verify(recs)
    check("clean 20-record chain verifies", ok)

    m = [dict(r) for r in recs]
    m[9]["payload_digest"] = "f" * 64
    ok, f = chain_svc.verify(m)
    check("mutation detected", not ok and any(x["type"] == "hash_mismatch" for x in f))

    d = [dict(r) for r in recs]
    del d[9]
    ok, f = chain_svc.verify(d)
    check("deletion detected as sequence gap",
          not ok and any(x["type"] == "sequence_gap" for x in f))

    fab = [dict(r) for r in recs]
    fab[5]["record_hash"] = "a" * 64
    ok, _f = chain_svc.verify(fab)
    check("fabricated record detected", not ok)

    check("payload key order irrelevant",
          chain_svc.canonical_payload({"a": 1, "b": 2})
          == chain_svc.canonical_payload({"b": 2, "a": 1}))
    check("float never serialised raw",
          b"0.30000000000000004" not in chain_svc.canonical_payload({"v": 0.1 + 0.2}))

    print("\nSealed envelope")
    dek = crypto_svc.generate_dek()
    aad = crypto_svc.build_aad(11, 22, 33, 44)
    plain = b'{"price_unit":"429.000000"}'
    ct, nonce = crypto_svc.seal(plain, dek, aad)

    check("round trip", crypto_svc.open_envelope(ct, nonce, dek, aad) == plain)
    check("no plaintext in ciphertext", b"429" not in ct)

    try:
        crypto_svc.open_envelope(ct, nonce, dek, crypto_svc.build_aad(11, 22, 99, 44))
        check("envelope substitution rejected", False)
    except crypto_svc.CryptoError:
        check("envelope substitution rejected", True)

    try:
        crypto_svc.open_envelope(ct[:-4], nonce, dek, aad)
        check("truncated tag rejected", False)
    except crypto_svc.CryptoError:
        check("truncated tag rejected", True)

    try:
        crypto_svc.open_envelope(ct, nonce, crypto_svc.generate_dek(), aad)
        check("wrong key rejected", False)
    except crypto_svc.CryptoError:
        check("wrong key rejected", True)

    a, b = crypto_svc.split_secret(dek)
    check("single share reveals nothing", a != dek and b != dek)
    check("two shares reconstruct", crypto_svc.combine_shares(a, b) == dek)

    kek = crypto_svc.generate_dek()
    check("wrap / unwrap", crypto_svc.unwrap_dek(crypto_svc.wrap_dek(dek, kek), kek) == dek)

    nonces = {crypto_svc.seal(plain, dek, aad)[1] for _ in range(300)}
    check("300 seals produce 300 distinct nonces", len(nonces) == 300)

    try:
        crypto_svc.seal(plain, b"short", aad)
        check("no plaintext in error path", False)
    except crypto_svc.CryptoError as exc:
        check("no plaintext in error path", "429" not in str(exc))

    print("\n%d checks failed\n" % len(FAILED))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
