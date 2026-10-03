# STATE

Last updated: 2026-10-02   Stage: 2, 4, 6, 7, 10-min, 11-min, 12, 13, 15   Version: 18.0.1.1.0

## Install status

FIRST INSTALL SUCCEEDED on Odoo 18 CE, 2026-09-30. The module loads, the
post-install numeric-type assertion passes, and the Auctions app appears.

Defect found on first install, fixed in `security/auction_groups.xml`:

  FIX-001  No user held any auction group after install, so every ACL denied
           and Odoo hid every menu except Audit Log, visible only because
           access_auction_audit_system also grants base.group_system.
           `implied_ids` runs the other way (observer implies base.group_user,
           not the reverse), so membership must be granted explicitly.
           Attempted resolution: a `<record id="base.user_admin">` adding the
           groups. DID NOT WORK.

  FIX-002  Why FIX-001 failed: `base.user_admin` is declared in the base
           module inside a noupdate block, so its ir.model.data row carries
           noupdate=True and Odoo SILENTLY SKIPS a record written against it
           on upgrade. No error, no change. Superseded by a `<function>` tag,
           which is not a record and therefore runs on install and on every
           update. It calls `res.users._auction_grant_initial_access`, which
           targets every active system administrator rather than one xmlid,
           so it also works where the administrator is a different user.
           Opener and Approver remain unassigned by design, per BR-AUD-008.

           Lesson for the build: to grant access to a pre-existing base
           record from a module, use a function tag, never a record.
           CONFIRMED WORKING 2026-09-30.

  FIX-003  Two models required to create an event (auction.category and
           auction.rule.set, both required=True) had no menu or action, so
           an event could not be created through the UI at all. Line items
           were also unreachable: the lot list inside the event form was
           editable="bottom" with no lot form view, so line_ids could not be
           opened. Added views/auction_config_views.xml (category, rule set,
           lot form with line items, envelope openings), a Configuration
           submenu, and an Envelope Openings menu. The event form's lot list
           now opens the lot form instead of editing inline.

           Found by auditing which models have no action before writing the
           user manual, not by running the module.

  FEAT-001 Phased delivery schedules. auction.line carried one required_by
           date, so a line could not express 500 units as 100/200/200 across
           three months. Added:
             auction.line.schedule      buyer's required tranches, with a
                                        constraint that they sum to the line
                                        quantity
             auction.bid.line.schedule  bidder's offered tranches,
                                        append-only, in the sealed payload
             auction.schedule.wizard    generates N periodic tranches, with
                                        the rounding remainder on the last
                                        so the total is always exact
           deviation_days is computed and frozen at submission. Stage 10
           will feed it into landed cost; until then it is recorded and
           visible but not scored.

           PAYLOAD_VERSION raised to 2. This does NOT invalidate earlier
           records: verify() recomputes the chain link from the stored
           payload_digest and never re-serialises, so a payload shape change
           is backward compatible by construction.

  FEAT-002 Product selection on a line item was a dead field: product_id
           existed with no onchange, so picking a product populated nothing
           and the description, unit and HSN were still typed by hand. Added
           an onchange that fills name, UoM, specification, HSN (where the
           Indian localisation is installed) and, on reverse events only,
           base_price from cost. It fills only what is empty, so a hand
           edited description survives re-selection. The picker narrows to
           the sourcing category's product category when one is configured.

  FIX-004  Mechanism dropdown offered forward mechanisms on a reverse event
           and the reverse. Root cause: `mechanism` was a Selection field,
           and Odoo cannot filter Selection options by domain. The existing
           constraint caught a wrong pick on SAVE, which is not the same as
           not offering it. Converted to a Many2one against a new
           auction.mechanism model synced from the strategy registry, so
           domain="[('direction','=',direction)]" works natively and updates
           live. `mechanism` survives as a stored related Char so engine code
           still addresses a mechanism by code. Migration 18.0.1.0.7
           backfills mechanism_id from the old varchar.

  FIX-005  The award loop was broken in four places at once, and every one
           of them would have presented as "the platform runs and produces
           nothing". Found by tracing the loop hop by hop against the
           source, not by re-reading it.

           (a) NOTHING ever set state `bidding_closed`. An event could reach
               `live` and stop there permanently: opening refuses any other
               state, so no envelope could ever be opened and no award could
               ever be made. Added `action_close_bidding` with a button, and
               `_cron_close_expired_bidding`, which waits until no lot is
               still taking bids so a staggered close settles first. Closing
               ahead of the published deadline is permitted for an internal
               SME event but is never silent: the shortfall in minutes goes
               to the audit log and to the chatter.

           (b) `auction.opening.execute(event_id, share_a, share_b)` could
               never succeed. It called `combine_shares()` on two key
               shares, and nothing in the module ever produced a share:
               `_generate_dek` wraps the key under the KEK and does not
               split it. Replaced by `execute_dual(event, opener_a,
               opener_b)`. This is dual AUTHORISATION, not split-key
               custody, and the code says so where it matters.

           (c) `_stage_evaluation_values` discarded the plaintext. It logged
               the byte count. Implemented properly against a new
               auction.evaluation.line working set.

           (d) No UI reached any of it. No opening button, no ACL row and no
               view for the opened values, and no way for an award line to
               see a price. A sealed bid line carries price_unit = NULL by
               design, so the award grid was asking for a bid whose price
               could not be read.

  FIX-006  `_stage_evaluation_values`, once written, staged NOTHING while
           reporting success. `chain.canonical_payload` quantizes every
           numeric leaf to a fixed-precision string so the tamper-evidence
           hash is reproducible, so a line id arrives as the string
           "42.000000". `by_line.get(item["line"])` on a dict keyed by
           integer id missed every row, every time.

           Caught by decoding a real payload rather than reasoning about
           the format. Fixed with `_payload_id`, and the opening now RAISES
           where a payload had priced lines and none matched — a silent zero
           is worse than a crash. Guarded by seven checks in
           `tests/standalone_check.py`, including a static one that fails if
           the staging code reverts to a bare lookup.

  FIX-007  An Envelope Opener holds observer rights on the event and nothing
           more, so `action_confirm_opening` writing the header state would
           have raised AccessError for exactly the user it is built for.
           Moved to sudo, after the authority check, not before it.

  FEAT-005 Minimal award made reachable end to end. `auction.evaluation.line`
           with a computed rank, a list/pivot/search set, an "Award to this
           bidder" button on each opened price, and `evaluation_line_id` on
           the award line whose onchange carries the bidder, quantity and
           price across. `_check_price_matches_opening` refuses an award at
           a figure nobody bid: without it the sealed chain is decorative.
           Optional opener nomination on the event, enforced where set.

  FEAT-004 Minimal award and downstream generation, stages 11 partial.
           auction.award / auction.award.line, PO and SO generation, and the
           three-level linkage:
             purchase.order.auction_event_id          which event
             purchase.order.line.auction_bid_line_id  which bid set the price
             auction.award.line                       WHY that bid won
           Generation hangs off award APPROVAL, never off auction close: on
           a sealed event nobody knows the winner at close because the
           envelopes are not open. Idempotent (re-running returns existing
           documents) and failure isolated (a generation failure records an
           error and stays retryable, it does not roll back the award).
           Phased delivery tranches become separate PO lines with distinct
           date_planned, so receipts and follow-up work unchanged.
           Winner selection is MANUAL. Computed ranking on landed cost is
           stage 10 and is not built.

           Manifest now depends on purchase, sale_management and stock. The
           specification splits these into direction addons so procurement
           can deploy without disposal; that split is a manifest change
           later, not a rewrite, because the award code is already separated
           by direction.

  FEAT-003 Event templates, BRD group TPL. auction.event.template with lot
           and line skeletons, a default participant panel, and timing held
           as OFFSETS rather than dates so a template cannot go stale.
           Instantiation copies BY VALUE per BR-TPL-009: template_id on the
           event is provenance only and is never read at runtime, so editing
           a template cannot reach an event that already exists. Versioning
           via action_new_version. Save an existing event back as a template
           per BR-TPL-012, with bids and submissions excluded.

## First RUNTIME install, 2026-10-02

Everything before this date was verified statically. On 2026-10-02 the module
was installed into a real Odoo 18.0 Community checkout (commit 32a1f1eb) with
PostgreSQL 16, seeded with a full worked event, and driven through the entire
flow in a browser. Ten defects surfaced that no amount of reading had found.
Every one of them is listed below, because the pattern matters more than the
individual fixes: NONE of them were logic errors. They were all places where
the code met Odoo, or met a user who did not hold every permission.

  FIX-008  `ir.cron.numbercall` was removed from the 18.0 branch partway
           through its life. Declaring it broke the install outright on a
           current checkout. It is omitted from the XML now, and
           `hooks.py` restores it where the column still exists -- a build
           that HAS the column defaults it to 1 on some versions, which is
           a cron that runs once and then stops, silently. One package,
           both builds.

  FIX-009  `parent_path` was declared with `unaccent=False`, which is not a
           valid parameter there. Three warnings on every boot.

  FIX-010  An MSE or DPIIT-exempt bidder COULD NOT BID AND COULD NOT BE
           AWARDED. `permits_bidding` wanted `declared` and
           `permits_opening` wanted `verified`, and nothing moved an
           exemption to either, so the only route through was for finance
           to record receipt and verification of money that was never
           required -- a false entry in the audit trail. Added an
           `exempted` state and `action_grant_exemption`, a finance action
           because GFR Rule 170 requires the claim to be evidenced rather
           than self-declared. This is a legal obligation for Indian
           deployments, not a preference.

  FIX-011  NO PORTAL BIDDER COULD EVER SUBMIT A BID. `_insert_ledger` read
           the sequence through `self.env`, outside the sudo on the create,
           and a portal user has no ACL on `ir.sequence`. Every submission
           died with AccessError on 'Sequence'. It survived because no
           portal user had reached that line: the portal bid form is stage
           13, and internal staff doing offline entry hold sequence access
           through `base.group_user`.

  FIX-012  Odoo derived "Bid Open Datetime" and "Comm Open Datetime" from
           the field names, so the screen disagreed with every document
           that calls them Bid Open and Commercial Opening. Explicit labels.

  FIX-013  Closing an event early called `message_post`, which sends a
           notification, which on an instance with no outgoing mail server
           -- most SME instances on day one -- raised a mail error and
           ROLLED BACK THE CLOSE. The event stayed live and the operator
           saw only "please configure the sender's email address". Now
           `_message_log` inside a savepoint; the audit entry is the
           authoritative record and the chatter note can never fail the
           operation it describes.

  FIX-014  EVERY OBSERVER-DERIVED ROLE WAS LOCKED OUT OF THE EVENT FORM.
           `auction.lot`, `auction.line`, `auction.section`,
           `auction.participant` and three others had ACL rows for event
           owners and bidders and none for observers -- and Envelope
           Opener, Bid Security Finance, Award Approver and Evaluator all
           imply Observer and nothing more. The event form shows the Lots
           tab, so it raised AccessError for all four. The dual-control
           opening was unreachable by the only people allowed to perform
           it. Ten read-only rows added.

  FIX-015  The Opened Bids list summed `price_unit` across bidders in its
           group headers, because Odoo aggregates a Float column by default
           and the default grouping is by line. The column headed "Unit
           price" showed five bidders' unit prices added together.
           `aggregator=None` on the columns where a sum means nothing;
           `line_value` keeps its aggregator because the pivot needs a
           measure and each pivot cell is a single bid.

  FIX-016  An award approver who held no purchase licence could not OPEN
           the award form, because `_compute_documents` read a One2many to
           `purchase.order`. Generation then failed on
           `product.supplierinfo`, which needs Purchase Administrator. The
           authority for these documents is the approved award, not the
           approver's purchase rights: counts and creation are sudo, and
           the smart button that navigates to the orders is group-guarded
           so nobody is dropped into a view they cannot read.

  FIX-017  `_rec_name` pointed at a NON-STORED computed field on
           `auction.participant`, `auction.evaluation.line` and
           `auction.bid.security`. A non-stored field cannot be searched,
           so typing a bidder's name or a UTR into any search box raised
           "Non-stored field ... cannot be searched" and the view fell
           over. All three are stored now; every dependency was already
           stored, and lifting anonymity at award recomputes correctly.

  FEAT-006 Display precision. Prices and quantities are stored
           `numeric(18,6)` per TSD #3.3 and were being RENDERED at six
           decimals, so a ceiling price read "2,400.000000". Storage is
           unchanged; the views now ask for two.

### What this says about the method

Sixty-four gaps were found before build by walking journeys and worked
cases. Three defects were found by static analysis. These ten were found by
installing it and pressing the buttons, and nine of the ten are permission
or framework-integration faults that no amount of reading the code would
have surfaced, because the code is correct in isolation. A module that has
never been run as a NON-ADMINISTRATOR has not been tested.

## Bidder portal, 2026-10-02 (stages 12 and 13)

Built and exercised against the live instance by a real portal user. Three
models, one controller, one template file, one disclosure boundary.

  FEAT-007 Stage 12. `/my/auctions` and `/my/auction/<id>`: invitations,
           tender terms, lots, line items with their required delivery
           phases, published documents with an upload form, clarifications,
           corrigenda, and the bidder's own submission history.

  FEAT-008 Stage 13. The sealed bid form: a price and quantity per line and
           a date per delivery tranche, pre-filled with the required dates
           so a bidder changes only what they cannot meet. Every validation
           failure is reported at once and against the row that caused it,
           per BR-BID-010. A receipt carrying the server timestamp, the
           ledger position and the record fingerprint. Withdrawal before
           close, which is a ledger state change and never a deletion.

  FEAT-009 Bidder-declared bid security. The bidder enters their own UTR or
           cheque number and it lands in finance's queue at Declared, which
           already permits bidding. Nothing in the portal decides whether
           the money arrived.

  FEAT-010 Clarifications. A bidder asks; the buyer answers; the answer is
           published to EVERY bidder and never names who asked. Verified
           from a second bidder's session. A question may be withheld, and
           the reason is recorded, because a question that simply vanishes
           is what bidders complain about.

  FEAT-011 Corrigenda. A material corrigendum voids the bids already
           received, because they were made against different information,
           and records how many. It may extend the deadline and can never
           bring it forward -- a constraint, not a warning. Where a material
           corrigendum extends by fewer than ten days, the CVC expectation
           is noted on the record rather than enforced, since an SME event
           under no statutory obligation may legitimately correct a typo.

### The disclosure boundary

`models/auction_portal_payload.py` is the only place that decides what a
bidder may see. Controllers and templates receive PLAIN DICTS, never engine
recordsets, because field omission in a template is not a security control:
the next person to add a column has no way of knowing the field they reach
for was deliberately left out.

`tests/portal_disclosure_check.py` asserts it against a running instance.
Seventeen checks pass today: eight denylist keys absent, the internal
estimate and the undisclosed ceiling absent, identifier probing returns 404
rather than another bidder's tender, and nothing crosses between two
bidders' sessions.

### Two more runtime defects

  FIX-018  NO PORTAL BIDDER COULD SUBMIT. `lot._resolve_rules()` read
           `auction.rule.set` in the caller's environment and a portal user
           holds no ACL on it, so every submission died with "You are not
           allowed to access 'Auction Rule Set'". Same class as FIX-011 and
           FIX-016: the engine must not depend on the caller's permissions
           to read its own configuration. Granting bidders blanket read
           would have been wider -- a rule set is shared across events and
           snapshots of other events sit in the same table -- so the read is
           sudo. `auction.mechanism` had the same problem on the same path.

  FIX-019  The receipt page 404'd for every bid. The route took the bid
           REFERENCE, which looks like BID/2026/0000012, and a <string:...>
           URL converter does not match a slash. Routed by id instead, with
           the bid re-checked against the session's participant.

Both were found by driving the browser, and both were invisible to every
static check: the first because the code is correct in isolation, the
second because the route is correct until a real reference reaches it.

## Menu, dashboard and reporting, 2026-10-03

Restructured the flat eleven-item menu bar into six sections following the
lifecycle of an event, and added the reporting layer the module had none of.

### What was built

  `auction.analysis`   a PostgreSQL view, `_auto = False`, grain ONE ROW PER
                       OPENED BID LINE. Ranks each line with window
                       functions, LEFT JOINs the award outcome, and carries
                       savings against the line's base price. Read-only to
                       every internal role; NO ACL row for bidders, plus a
                       defence-in-depth record rule in case one is ever
                       added.
  `auction.dashboard`  a transient model behind the landing screen. Every
                       tile's domain is declared ONCE in `TILES`, and both
                       the number and the list the tile opens read it from
                       there, so they cannot drift apart.
  Five report screens  Event Pipeline (reads `auction.event`, because the
                       question is about events not yet opened), Bid
                       Analysis, Award and Savings, Bidder Participation,
                       Bid Security Ageing.
  Two order lists      every purchase and sale order traceable to a tender.
  Event search view    the module shipped without one, so an operator with
                       forty events could neither filter nor group them.

### Defects found, all three by looking rather than by testing

  FIX-020  THE DASHBOARD SHOWED EIGHT ZEROS while the database said three,
           nine and one. An `act_window` on a transient model opens an
           UNSAVED record and the web onchange round trip does not carry
           these computed values back to the client. Not for the obvious
           reason: the compute runs correctly on an unsaved record --
           `.new({})` in a shell returns the right figures -- and adding a
           field dependency changes nothing. Both were tried and both still
           rendered zero. Fixed by making the menu run a server action that
           creates the record first.

           Worth recording precisely: EVERY SHELL CHECK PASSED AGAINST BOTH
           BROKEN VERSIONS, because `create({})` in a shell produces the one
           thing the form never makes -- a record that exists. Fifty-seven
           checks, zero failures, and the screen was blank. It was found by
           opening the screenshot and counting the tiles.

  FIX-021  BID SECURITY VANISHED FROM THE MENU and a Manager could not see
           the queue they are accountable for. A `groups=` left OFF a
           `menuitem` does not clear a group the menu already has: Odoo
           writes only the fields the tag carries, so an omitted one keeps
           its stored value through every upgrade. The rewritten file
           therefore said one thing and the database another. Found by
           counting the menus a buyer could see (27 of 28), not by reading
           the XML, where it is invisible.

  FIX-022  The year-to-date totals summed `awarded_value` across every
           event and printed the result in COMPANY currency, while the
           analysis view carries each EVENT's currency. One rupee event and
           one dollar event would have been added together under a single
           symbol. Scoped to the company currency. Multi-currency tendering
           is out of scope for v1.0; multi-currency arithmetic going
           unnoticed is not.

  FIX-023  AFTER AWARDING, THE BUYER WHO RAN THE TENDER COULD NO LONGER
           OPEN THEIR OWN EVENT. `_compute_award_count` maps into
           `award_ids.order_ids` and `sale_order_ids`, which reads
           purchase.order and sale.order in the CALLER's environment. An
           Event Owner without the purchase licence raised AccessError, and
           because that compute backs a field on the event form, the form
           died with it. The web client then fell back to the event list,
           which made it look like a search filter rather than a permission
           fault; the Access Error dialog behind the list was the only
           thing that said otherwise.

           The FOURTH appearance of one root cause (FIX-011, FIX-016,
           FIX-018): the engine reads in the caller's environment. Counted
           with sudo now. The rule FIX-016 settled is unchanged — counting
           documents generated from your own event discloses nothing,
           opening one needs the licence that reads it, and the stat button
           already carries that group.

           Predates this session; reachable only once an event is awarded
           AND the viewer lacks a purchase licence. Found while chasing
           something else entirely.

  FIX-024  THE TENDER DOCUMENT FIELD WAS A PICKER OVER EVERY ATTACHMENT IN
           THE DATABASE. `attachment_id` was a bare Many2one to
           ir.attachment with no domain, rendered as an autocomplete, so
           its name_search returned the whole table.

           Visible symptom: compiled asset bundles -- web.assets_web.min.js
           and siblings -- offered as tender documents, because Odoo stores
           those as ir.attachment. Those rows are DELETED AND RECREATED on
           every asset rebuild, and the field cascades, so a document
           pointing at one would disappear from the event together with its
           place in the audit trail, silently, and be missed only when
           somebody went looking for what was issued.

           The serious half is the other half. The same dropdown offered
           every unrelated business file in the database -- an executed
           agreement, an invoice, an HR document -- on the BIDDER-FACING
           tab, with Is Published a few columns away. One mis-click in an
           autocomplete was the whole distance between another client's
           contract and a room full of competing bidders.

           Documents are now UPLOADED, never picked: a binary field builds
           a private attachment owned by the document itself, hashed on
           save as before. attachment_id is read-only with a domain
           confined to this module's own attachments, as defence behind
           the view. Reachable rows went from 25, six of them asset
           bundles, to zero.

           The portal path was never affected -- bidders always uploaded,
           and their attachment was always private. Only the backend form
           exposed the picker.

           Existing rows are repaired by a post-migration that COPIES a
           borrowed file into a document-owned attachment and leaves the
           original record's attachment untouched. Idempotent; digests
           unchanged, so the integrity record still holds. post_init_hook
           alone would not have done it, because that runs on install only.

Two further things the browser corrected that the shell had no view of: the
`COUNT(DISTINCT x) OVER (...)` in the first draft of the view is not
implemented by PostgreSQL at all (moved to an aggregate CTE, which is also
exact rather than relying on one-row-per-bidder being true), and a direct
action URL gives the web client no menu to anchor the app to, so the first
capture run photographed a navbar belonging to Discuss.

### Still true after this change

No seeded auction operator holds `purchase.group_purchase_user`, so the two
order menus are correctly hidden from all of them. That is a DEPLOYMENT
NOTE, not a defect: an Event Owner who will raise orders has to be granted
the purchase licence as well, or they will generate documents they cannot
then open.

The dashboard shows a disabled `1 / 1` pager, an unavoidable consequence of
opening a real record. Removing it needs an OWL class, which SCP-AUC-001
keeps out of this module.

## Completed

| Stage | Name | Status |
|-------|------|--------|
| 1 | Complete schema | Partial — engine, evaluation, award and downstream models present; comparative and landed-cost models not created |
| 2 | Ledger and hash chain | Complete — append-only, chain, verification |
| 4 | Bid acceptance critical path | Complete — lock, clock_timestamp, boundary retry, idempotency |
| 5 | Rules engine and strategy registry | Partial — registry and 3 sealed strategies; hooks frozen |
| 7 | Sealed envelope and opening | Complete — AES-GCM with AAD, dual control, minutes, staging to the evaluation set |
| 6 | Closure | Complete for sealed — manual close plus a 2-minute cron that waits for every lot. Verified at runtime |
| 10 | Evaluation | MINIMAL — opened values land in auction.evaluation.line, ranked on bid price. Landed cost NOT built |
| 11 | Award and downstream | MINIMAL — manual winner selection, PO/SO generation, three-level linkage |
| 15 | Bid security offline workflow | Complete, including statutory exemption (FIX-010) |
| 12 | Bidder portal | Complete for sealed — invitations, documents, clarifications, corrigenda, bidder-declared security, bid form, receipts, withdrawal |
| 13 | Portal content | Complete — disclosure boundary is the payload layer, verified from two bidder sessions |
| -- | Menu, dashboard, reporting | Complete — six-section menu, landing dashboard, five report screens, two order lists (FIX-020/021/022/023/024) |

## NOT started

Stages 0 (harness scripts), 3 (walking skeleton test), 14 (spreadsheet
round-trip), 16 (notifications, audit pack, hardening).

Stages 12 and 13 are now built. What is still missing from the portal:
registration and onboarding of a NEW bidder (the buyer creates the portal
user today), two-envelope technical submission as a separate upload step,
and email notification of an invitation, a corrigendum or a published
answer -- a bidder has to visit to find out, which is stage 16.

Stages 10 and 11 are MINIMAL, not complete. What is missing from them:

  * Landed-cost normalisation. `norm_value` is the bid price. Duty, freight,
    payment terms and the delivery slip already recorded in
    `max_deviation_days` are NOT scored. A bidder offering a worse price on
    better terms therefore ranks below one who is cheaper and later.
  * The formal comparative statement. The pivot view answers "who quoted
    what", which is not the same document.
  * Split allocation across vendors on one line. The quantity constraint
    permits it arithmetically; there is no UI for it.
  * Counter-offer and negotiation rounds. An award carries the opened price
    and nothing else, enforced by `_check_price_matches_opening`.

## Frozen interfaces

Changing any of these is a TSD amendment, not a code change.

- `auction.bid.submit_request(vals) -> receipt` — frozen
- `auction.bid.submit(vals) -> receipt` — frozen
- Strategy hooks: `default_rules`, `validate_entry`, `rank_basis`,
  `rank_direction`, `visible_state`, `on_clock_tick`, `on_close`,
  `award_plan` — frozen
- `auction.lot._resolve_rules() -> rule set` — frozen
- `auction.opening.execute_dual(event, opener_a, opener_b, envelope_type)`
  — frozen. REPLACES `execute(event_id, share_a, share_b, envelope_type)`,
  which was unreachable: it expected two key shares and nothing in the
  module ever produced any. `_generate_dek` wraps the key under the KEK and
  does not split it. The split-key primitives in `services/crypto.py` are
  written and tested but deliberately NOT wired — see the note in
  `execute_dual`.
- `auction.event._stage_evaluation_values(bid, plaintext, opening)` — frozen
- `auction.evaluation.line` field set — frozen; stage 10 replaces the
  MEANING of `norm_value`, not its name or type
- Canonical payload serialisation — frozen, `PAYLOAD_VERSION = 2`

## Next action

Upgrade to 18.0.1.0.8 on the live instance and walk the loop end to end:
publish, open bidding, submit two sealed bids, close bidding, request and
confirm the opening as two DIFFERENT users in the Envelope Opener group,
read the prices, award, approve, generate.

Two things can only fail at runtime and are the first suspects:

  1. `AUCTION_KEK` must be the same value the event was published under.
     `execute_dual` checks the recovered key against `dek_fingerprint` and
     refuses rather than producing garbage, so the symptom will be a clear
     message, not corrupt data.
  2. Two users must actually hold `group_auction_opener`. One person cannot
     complete an opening alone, by design.

Then stage 10: landed cost into `norm_value`.

## Static checks

Run before any upgrade. Both read the actual files; both exist because
reasoning about the code instead of reading it shipped FIX-003 and FIX-004.

    python3 tests/standalone_check.py    # chain + crypto + payload decode
    python3 tests/static_validate.py     # model/field/method/action coherence
    python3 tests/static_trace.py        # every hop of the award loop exists

And against a RUNNING instance, which is the only way to test the portal:

    python3 tests/portal_disclosure_check.py --base http://localhost:8069 \
        --event <id> --bidder login:pw --other login:pw \
        --secret <internal estimate> --secret <undisclosed ceiling>

And against a running instance, as a REAL OPERATOR rather than as the
administrator:

    odoo-bin shell -d <db> < tests/runtime_reports_check.py   # 57 checks: analysis view,
                                               # read_group, tile/list
                                               # agreement, menu resolution

All pass. They are necessary and, as the defects above show, NOT
sufficient. FIX-020 in particular passed all fifty-seven of them while the
screen it tested showed nothing but zeros. A check that exercises the ORM
does not exercise the form; if a screen matters, OPEN IT AND LOOK AT IT.

Before any release, install into a real Odoo and walk the flow
as each role -- in particular as a user who is ONLY an Envelope Opener and
as a user who is ONLY an approver. Six of the ten were invisible to an
administrator.

## Deviations from spec

| TSD ref | Built as | Reason |
|---------|----------|--------|
| #6.3 | 3 of 13 mechanisms | SCP-AUC-001 scope |
| #8 | KEK from environment variable | SME profile, SCP-AUC-001 #3 |
| #10 | No bus transport | No live mechanisms in v1.0 |
| #8 | Dual authorisation, not split-key custody | SME profile. Two distinct identities are recorded; the key is recovered server side from the KEK, so KEK plus database access could open alone. SCP-AUC-001 #3 |
| #13.1 | `rank` computed, never stored | A stored rank is wrong the moment a sibling row changes and nothing recomputes it |
