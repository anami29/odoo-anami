# Auction Platform — Working Agreement

Committed at the repository root. This file is what stops an agent reverting
to its training defaults; every invariant below exists because the default is
wrong for this system.

## Read first, every session
- `STATE.md` — what is done, what interfaces are frozen, where to resume
- `GAPS.md` — unresolved decisions. Never invent an answer to one.
- The TSD sections named in the stage brief (BLD-AUC-001 #4)

## Non-negotiable invariants

### Time
- Server time on the bid path is `clock_timestamp()` ONLY, read AFTER the
  lock is acquired.
- NEVER `now()`, `CURRENT_TIMESTAMP`, `transaction_timestamp()` or
  `datetime.utcnow()`. `now()` returns TRANSACTION START time, so a bid that
  waited 2.8s on the lock would be stamped 2.8s in the past — a late bid
  could be accepted and the tie-break could disagree with serialisation order.
- Client-supplied time is never accepted for any decision.

### Transactions
- Retry lives at the REQUEST BOUNDARY on a NEW cursor per attempt.
- NEVER retry a serialisation failure inside `cr.savepoint()`. PostgreSQL
  aborts the whole transaction; a savepoint rollback cannot recover it.
- `55P03` (lock timeout) and `40001`/`40P01` (serialisation, deadlock) are
  handled separately. They are not the same failure.
- Multi-lot locks are acquired in ASCENDING ID ORDER, always, everywhere.

### Inside the lot lock
- No HTTP. No mail. No attachment write. No subprocess. No `sleep`.
- Bus publication and job enqueue go in `cr.postcommit`, never inline.

### Ledger — `auction.bid`, `auction.bid.line`
- `write()` and `unlink()` raise unconditionally. No context key, no group,
  no `SUPERUSER_ID` exception. Do not add an escape hatch; you will want to.
- No `mail.thread`, no `mail.activity.mixin`, no mixin that writes to its own
  record. Chatter goes on a sibling record.
- No stored computed fields. Recomputation is a write.
- `auditlog` is NEVER subscribed to these models.
- `_set_state` is the only mutation path, three columns, and it MUST call
  `invalidate_recordset()` after its raw SQL.

### Types
- Any column that is ranked or compared: `fields.Float(digits=(18,6))` or
  `fields.Monetary`. NEVER a bare `fields.Float()` — that is double precision,
  and mixed-type comparison is non-deterministic at the sixth decimal.
- `float_compare` and `float_round` always. Never `==` or `<` on floats.

### Authorisation
- Per-event bidder roles are DATA on `auction.participant.user`, never
  `res.groups`. Group membership is necessary, never sufficient.
- `auth_signup` is NEVER the registration path.
- Session continuity on a console is a keepalive endpoint, NEVER a timeout
  configuration change.
- No context key ever widens a record rule.

### Disclosure
- Every bidder-facing payload passes through `visible_state`. No controller
  calls `read` or `search_read` on an engine model directly.
- Denylist, checked by test: `estimated_value`, undisclosed `ceiling_price`,
  undisclosed `reserve_price`, `dek_wrapped`, `envelope`, proxy maximum.

### Odoo data files
- To modify a record that belongs to another module (`base.user_admin`,
  `base.group_user`), a `<record>` is SILENTLY SKIPPED on upgrade if that
  record's `ir.model.data` row has noupdate=True. Use a `<function>` tag and
  a model method instead. This cost a full round trip on FIX-001.
- Never put `--` inside an XML comment. It is illegal and the file will not
  parse.

## Working method
- Odoo 18 source is at `./odoo`. GREP IT for API signatures. Do not recall
  them — training data contains Odoo 16 and 17 patterns that no longer apply
  (`SavepointCase`, old `_sendone` signatures, pre-OWL-2 components).
- For suites BID, SLD, TMR and AUD: write the tests from the register BEFORE
  the implementation.
- `TransactionCase` cannot test concurrency or post-commit hooks. Use
  `AuctionConcurrencyCase` and `AuctionCommittedCase` from `tests/common.py`.
  A concurrency test on `TransactionCase` passes while testing nothing.
- One stage per session. When context tightens mid-stage, update `STATE.md`
  with precise resumption state and stop. Do not push through.

## When the spec is silent
If a behaviour you need is in neither the TSD nor JRN-AUC-001 nor
GRP-AUC-001: STOP. Record it in `GAPS.md` with the stage, the decision needed
and the options. Do NOT invent one. Sixty-four gaps were found this way
before build started; the method works.

## Close of session
Update `STATE.md`. Always. It is the only thing the next session reads about
what you did.
