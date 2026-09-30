# custom_auction_base

Engine core for the Electronic Auction and Sealed Bid Platform, SME Edition
v1.0. Odoo 18 Community.

## Status — read this first

**This module has never been executed against an Odoo runtime.** It was
written against the specification set and validated as far as is possible
without a database: Python syntax, XML well-formedness, ACL consistency, and
full execution of the two Odoo-independent services (`services/chain.py` and
`services/crypto.py`, 16 checks, all passing).

Expect errors on first install. It is a foundation to iterate from, not a
finished module.

## What is here

| Area | State |
|------|-------|
| Append-only ledger, hash chain, verification | Implemented |
| AES-256-GCM sealed envelope with AAD binding | Implemented and executed |
| Dual-control opening, minutes | Implemented |
| Bid acceptance: row lock, `clock_timestamp()`, boundary retry, idempotency | Implemented |
| Rules engine and strategy registry | Implemented |
| Three sealed mechanisms | Implemented |
| Offline bid security workflow | Implemented |
| Immutable audit log | Implemented |
| Security groups, ACLs, record rules | Implemented |
| Evaluation, comparative, award, downstream | **Not built** |
| Portal bid forms, spreadsheet round-trip | **Not built** |

## Install

```bash
# 1. Key encryption key -- 32 bytes, hex. NEVER in ir.config_parameter.
export AUCTION_KEK=$(python3 -c "import os;print(os.urandom(32).hex())")

# 2. Dependency
pip install cryptography

# 3. Install
odoo -d yourdb -i custom_auction_base --stop-after-init
```

The post-install hook asserts that every ranked column is PostgreSQL
`numeric` and fails the install if not. That is deliberate.

## Verify before trusting anything else

```bash
python3 custom_auction_base/tests/standalone_check.py
```

Sixteen checks against the hash chain and the envelope, with no Odoo and no
database. This is what a reviewer runs first.

## Run the Odoo test suite

```bash
odoo -d yourdb -i custom_auction_base --test-enable \
     --test-tags auction --stop-after-init
```

## Deployment

Single server, 2–4 workers, PostgreSQL 14+. No gevent worker and no
websocket proxy configuration: v1.0 has no live mechanisms, so closure at
one-minute cron granularity plus lazy close-on-access is sufficient.

`AUCTION_KEK` lives in the process environment on the application host. This
protects submitted bids against a database administrator, which is the threat
TSD-AUC-001 #8 is written against. It does not protect against root on that
host. For the full profile the key moves to an external key store.

## Governing documents

BRD-AUC-001, TSD-AUC-001 v1.1, AMD-AUC-001, AMD-AUC-002, GRP-AUC-001,
SCP-AUC-001, BLD-AUC-001. `CLAUDE.md` carries the invariants; `GAPS.md`
carries what is still undecided; `STATE.md` carries where to resume.

## Licence

OPL-1. Supplied under RLFB-EULA-001.
