# STATE

Last updated: 2026-09-30   Stage: 1-2 partial, 4 partial, 7   Commit: initial+fix1

## Install status

FIRST INSTALL SUCCEEDED on Odoo 18 CE, 2026-09-30. The module loads, the
post-install numeric-type assertion passes, and the Auctions app appears.

Defect found on first install, fixed in `security/auction_groups.xml`:

  FIX-001  No user held any auction group after install, so every ACL denied
           and Odoo hid every menu except Audit Log -- visible only because
           access_auction_audit_system also grants base.group_system.
           `implied_ids` runs the other way (observer implies base.group_user,
           not the reverse), so membership must be granted explicitly.
           Resolution: base.user_admin granted group_auction_admin and
           group_auction_finance. Opener and Approver left unassigned by
           design, per BR-AUD-008.

## Completed

| Stage | Name | Status |
|-------|------|--------|
| 1 | Complete schema | Partial — engine models present; evaluation, award and comparative models not yet created |
| 2 | Ledger and hash chain | Complete — append-only, chain, verification |
| 4 | Bid acceptance critical path | Complete — lock, clock_timestamp, boundary retry, idempotency |
| 5 | Rules engine and strategy registry | Partial — registry and 3 sealed strategies; hooks frozen |
| 7 | Sealed envelope and opening | Complete — AES-GCM with AAD, dual control, minutes |
| 15 | Bid security offline workflow | Complete |

## NOT started

Stages 0 (harness scripts), 3 (walking skeleton test), 6 (closure beyond the
cron), 10 (evaluation and comparative), 11 (award and downstream), 12 (portal
foundation beyond two routes), 13 (portal bid forms), 14 (spreadsheet),
16 (notifications, audit pack, hardening).

## Frozen interfaces

Changing any of these is a TSD amendment, not a code change.

- `auction.bid.submit_request(vals) -> receipt` — frozen
- `auction.bid.submit(vals) -> receipt` — frozen
- Strategy hooks: `default_rules`, `validate_entry`, `rank_basis`,
  `rank_direction`, `visible_state`, `on_clock_tick`, `on_close`,
  `award_plan` — frozen
- `auction.lot._resolve_rules() -> rule set` — frozen
- `auction.opening.execute(event_id, share_a, share_b, envelope_type)` — frozen
- Canonical payload serialisation — frozen, `PAYLOAD_VERSION = 1`

## Next action

Stand up the Odoo 18 + PostgreSQL 16 environment, install the module, and fix
what the first install reveals. This module has NEVER been executed against an
Odoo runtime — see README.md "Status".

## Deviations from spec

| TSD ref | Built as | Reason |
|---------|----------|--------|
| #6.3 | 3 of 13 mechanisms | SCP-AUC-001 scope |
| #8 | KEK from environment variable | SME profile, SCP-AUC-001 #3 |
| #10 | No bus transport | No live mechanisms in v1.0 |
