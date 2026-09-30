# Open decisions encountered during build

Recorded rather than invented. See GRP-AUC-001 and AMD-AUC-002 for the
governing registers.

## Blocking for this module

| Ref | Decision needed | Where it bites |
|-----|-----------------|----------------|
| GAP-016 | Does a material corrigendum void submitted bids? And the sealed branch, where the envelope cannot be inspected. | `auction.event` corrigendum action, not yet written |
| GAP-062 | Debarment / exclusion-list screening at invitation and award. No screening exists — a debarred supplier can be invited, qualified and awarded. | `auction.participant` invitation |
| GAP-068 | Cheque dishonoured after close where that bidder won. No money to forfeit; the forfeiture conditions assume there is. Implemented provisionally as non-responsive + audit, pending ratification. | `auction.bid.security.action_dishonour` |
| GAP-069 | Partial receipt — may a bidder top up, and which date governs the verification deadline? Implemented as override-with-reason, pending ratification. | `auction.bid.security.action_verify` |

## Deferred by scope (v1.0)

GAP-003, GAP-034..GAP-042, GAP-053, GAP-054, GAP-055, GAP-056 are not
applicable while live and clock mechanisms are out of scope. They remain OPEN
against v2.0 and must not be closed as resolved.

## Decisions taken provisionally in code

| Where | Taken as | Authority |
|-------|----------|-----------|
| Bid validity default 120 days | `auction.rule.set.bid_validity_days` | AMD-AUC-002 B-10, CVC-aligned |
| Forfeiture conditions, five, closed set | `forfeit_condition` selection | AMD-AUC-002 B-01, GFR-aligned |
| Refund to source account | `action_refund` requires `refund_ref` | AMD-AUC-002 B-02 |
| Gating: bid on declared, award on verified | `permits_bidding` / `permits_opening` | SCP-AUC-001 #4.3 |
| Grace window 48h, verify lead 24h | `auction.event` defaults | SCP-AUC-001 #4.3 |

Each is a default in code and a position in a register. If a ratifier
replaces the position, the code default changes with it.
