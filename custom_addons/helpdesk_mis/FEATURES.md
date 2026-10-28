# Helpdesk MIS — feature by feature

Every item the Electrocare BRD asks for, what was built for it, and how to
check it yourself. Nothing here is aspirational: if something was not built,
it says so and says why.

- **Module:** `helpdesk_mis`
- **Depends on:** `helpdesk_mgmt`, `resource`
- **Status of the tests:** written, **not yet executed** — see
  [Running the tests](#2-running-the-tests).

---

## Contents

1. [Install it](#1-install-it)
2. [Running the tests](#2-running-the-tests)
3. [The ten decisions, as settings](#3-the-ten-decisions-as-settings)
4. [New data the module records](#4-new-data-the-module-records)
5. [Dashboard A — Ticket Performance MIS](#5-dashboard-a--ticket-performance-mis)
6. [Dashboard B — Open Tickets MIS](#6-dashboard-b--open-tickets-mis)
7. [Filters and controls](#7-filters-and-controls)
8. [Detailed reports A1–A5 and B1–B4](#8-detailed-reports-a1a5-and-b1b4)
9. [Measures: what each number means](#9-measures-what-each-number-means)
10. [Security and access](#10-security-and-access)
11. [Export and drill-down](#11-export-and-drill-down)
12. [Background jobs and install behaviour](#12-background-jobs-and-install-behaviour)
13. [BRD traceability: FR-01 to FR-18](#13-brd-traceability-fr-01-to-fr-18)
14. [What is deliberately not built](#14-what-is-deliberately-not-built)
15. [Troubleshooting](#15-troubleshooting)

---

## 1. Install it

**Steps**

1. The module lives at `addons/helpdesk_mis`, already on the addons path
   beside `helpdesk_mgmt`.
2. Restart Odoo so the new module is discovered.
3. **Apps → Update Apps List**.
4. Search for *Helpdesk MIS* and press **Activate**.

Or from the command line:

```bash
odoo -d <database> -i helpdesk_mis --stop-after-init
```

To pick up later changes:

```bash
odoo -d <database> -u helpdesk_mis --stop-after-init
```

**What installing does**

- Creates one new table, `helpdesk_ticket_closure_event`.
- Adds stored columns to `helpdesk_ticket` and
  `helpdesk_ticket_assignment_history`.
- Adds the MIS settings to `res_company`.
- Creates two PostgreSQL views, `helpdesk_mis_report` and
  `helpdesk_mis_assignment_report`.
- Runs `post_init_hook`, which backfills history — see
  [section 12](#12-background-jobs-and-install-behaviour).

**How to test**

After installing, open **Helpdesk → Reporting → MIS**. You should see
*Ticket Performance MIS*, *Open Tickets MIS* and *Detailed reports*.

If the menu is missing, you do not hold the MIS right — see
[section 10](#10-security-and-access).

---

## 2. Running the tests

```bash
odoo -d <database> -i helpdesk_mis --test-enable --test-tags /helpdesk_mis --stop-after-init
```

One file at a time:

```bash
odoo -d <database> --test-enable --test-tags /helpdesk_mis:TestClosureEvents --stop-after-init
```

| File | Covers |
|---|---|
| `tests/test_closure_events.py` | Closure and reopen history, first vs final resolution |
| `tests/test_assignment_metrics.py` | Time held, reassignment counting, no double counting |
| `tests/test_mis_report.py` | Ageing bands, thresholds, attribution, handling basis |
| `tests/test_dashboard.py` | KPI payloads, filters, drill-down, person-level hiding |
| `tests/test_security.py` | Who can read what |

> **Honest status.** These tests have **not been run**. The machine this was
> written on has no usable Odoo runtime — Python 3.14 (Odoo 18 supports up to
> 3.12), no `psycopg2`, and Docker needs elevated access. What *was* verified
> statically: every Python file compiles, every XML file parses, every
> `ref=` resolves, both SQL views parse as valid PostgreSQL, and every column
> the SQL reads is a real stored column. Run the suite before trusting any
> number in production.

---

## 3. The ten decisions, as settings

BRD section 18 lists ten decisions it says must be closed before development.
Rather than guess on Electrocare's behalf, each became a **setting with a
documented default**. Change it in **Settings → Helpdesk MIS**; every figure
follows on the next refresh. Nothing needs rebuilding, because the SQL views
join `res_company` and read these columns at query time.

Find them all in `models/res_company.py`.

### 3.1 Handling time basis — BRD 18.1

| | |
|---|---|
| **Setting** | Handling time basis |
| **Options** | Elapsed resolution / Assignment duration / Active work |
| **Default** | Elapsed resolution |
| **Field** | `hd_mis_handling_basis` |

**How it is built.** The `handling_hours` column in `helpdesk_mis_report` is a
`CASE` over this setting. Elapsed is creation→closure, assignment is the sum
of assignment intervals, active reads `mis_active_work_hours`.

**How to test it.** Open a ticket, close it, then switch the setting and
reload *A1 — Tickets by Team*. The **Handling Time** measure changes; the
other measures do not. Automated: `test_handling_basis_switches_the_measure`.

> Choosing **Active work** reports nothing, because Odoo has no work-time
> source. That is deliberate — an empty column is honest, an invented one is
> not. See [section 14](#14-what-is-deliberately-not-built).

### 3.2 Team attribution — BRD 18.2

`hd_mis_team_attribution` — *Current* (default) / *Originating* / *Closing* /
*All*.

**How it is built.** All four are always present as their own columns:
`team_id`, `create_team_id`, `close_team_id`, and the assignment report for
"every team that handled it". `attributed_team_id` is whichever the setting
names, and it is what the dashboards group by.

**How to test it.** Raise a ticket in Team A, close it, reopen it, move it to
Team B. In *A1* add the columns: originating = A, closing = A, current = B.
Switch the setting and watch the grouping move. Automated:
`test_team_attribution_switches`.

### 3.3 Person attribution — BRD 18.3

`hd_mis_person_attribution` — *Current* (default) / *First assigned* /
*Closed by* / *Every person*.

Same design as team attribution. Automated:
`test_person_attribution_switches`.

### 3.4 Closure counting — BRD 18.4

`hd_mis_reopen_counting` — *Distinct tickets* (default) / *Closure events*.

**How it is built.** `closed_count` is `1` per closed ticket under *distinct*,
or the closure-event count under *events*. Both numbers always exist:
`closure_event_count` is always the raw event count.

**How to test it.** Close a ticket, reopen it, close it again. Under
*distinct* it counts 1; under *events* it counts 2. Automated:
`test_closed_count_follows_the_counting_setting`.

### 3.5 and 3.6 Date basis — BRD 18.5, 18.6

`hd_mis_date_basis` (*Creation* / *Closure* / *Currently open*) and
`hd_mis_open_date_basis` (*Creation* / *Last assignment*).

These set what the dashboards open with. The reader can change it on screen at
any time. See [section 7](#7-filters-and-controls).

### 3.7 Working hours — BRD 18.7

`hd_mis_working_hours` (off by default) and `hd_mis_calendar_id`.

**How it is built.** This one cannot be done in SQL, because working hours
need `resource.calendar`. So it is computed in Python and **stored**:
`duration_working` on every assignment interval, and
`mis_resolution_working_hours` on every ticket. Both are kept current whether
or not the setting is on, so flipping the toggle needs no recomputation. The
SQL views then pick the column the setting asks for.

**How to test it.** Find an interval spanning a weekend. With the setting off,
*Time Held* counts the whole weekend. Turn it on and the figure drops to
working hours only. Automated:
`test_working_hours_setting_switches_the_reported_measure`.

> If no calendar is configured anywhere, durations fall back to elapsed time
> rather than silently reporting zero.

### 3.8 Ageing thresholds — BRD 18.8

`hd_mis_threshold_warn_hours` (24), `_high_hours` (48),
`_critical_hours` (168).

**How it is built.** The views join `res_company` and compare each open
ticket's age against these three columns to produce `age_band`
(*ok / warn / high / critical*), `past_threshold` and
`hours_beyond_threshold`.

**How to test it.** Leave a ticket open for a day; it shows *Ageing*. Raise
the warning threshold to 1000 hours and it becomes *Within threshold*
immediately, with no upgrade. Automated:
`test_age_bands_follow_the_configured_thresholds`.

### 3.9 Reporting timezone — BRD 18.9

`hd_mis_timezone`. Empty means each reader's own timezone.

**How it is built.** `_mis_env()` puts the configured timezone into the
context before any grouping runs, so day, week and month boundaries follow it.

**How to test it.** Set it to `Asia/Kolkata`, group *A1* by *Created: month*,
and check that a ticket raised at 00:30 IST falls in the right day.

### 3.10 Who may export and see person data — BRD 18.10

Two separate rights rather than a setting. See
[section 10](#10-security-and-access).

---

## 4. New data the module records

### 4.1 Closure and reopen events — fixes FR-16

**The problem.** `helpdesk_mgmt` overwrites `closed_date` every time a ticket
closes. A ticket closed, reopened and closed again keeps only the last
timestamp, so BRD 9.2's *Time to First Closure* and *Final Resolution Time*
were simply unanswerable. This was the only gap in the whole BRD that needed a
schema change rather than a new view.

**How it is built.** A new model, `helpdesk.ticket.closure.event`
(`models/helpdesk_ticket_closure_event.py`), with one row per event:

| Field | Meaning |
|---|---|
| `event_type` | `close` or `reopen` |
| `event_date` | When |
| `occurrence` | 1 for the first closure, 2 for the second… |
| `from_stage_id` / `stage_id` | The stage move that caused it |
| `team_id` / `user_id` | Who held it **at that moment** |
| `changed_by_id` | Who made the change |
| `hours_from_create` | Age at the event |

Rows are appended by a `write()` override on `helpdesk.ticket` that compares
the stage's `closed` flag before and after. Reading the state *before* calling
`super()` matters, because `helpdesk_mgmt`'s own `write()` sets `closed_date`
as a side effect.

**How to test it.** Open a ticket, close it, reopen it, close it again. The
**Closure History** tab lists three events with occurrences 1, reopen, 2.
*First closed on* keeps the original date while *Final closure* moves.
Automated: `test_first_closure_survives_a_later_one`.

**Also check:** moving between two open stages records nothing
(`test_stage_change_that_does_not_cross_the_line_records_nothing`), and moving
a ticket after closing it does not rewrite who closed it
(`test_event_captures_team_and_person_at_the_time`).

### 4.2 Working-hour durations

`duration_working` on every assignment interval, beside the elapsed
`duration` that `helpdesk_mgmt` already stored. Visible on the ticket's
**Assignment History** tab.

### 4.3 Per-ticket counters

Stored on `helpdesk.ticket`, because the MIS views are SQL and a non-stored
field has no column to read:

| Field | Meaning |
|---|---|
| `mis_reassignment_count` | Times it changed hands. The first assignment is **not** a reassignment |
| `mis_assignee_count` | Distinct people who held it |
| `mis_total_held_hours` | Sum of every interval |
| `mis_first_closed_date` | First closure, kept forever |
| `mis_first_resolution_hours` | Creation → first closure |
| `mis_closure_count` / `mis_reopen_count` | Event counts |
| `mis_resolution_working_hours` | Creation → closure, working hours only |
| `mis_active_work_hours` | Always 0 unless a timesheet module fills it |

**How to test.** Reassign a ticket twice: the counter reads 2, not 3.
Automated: `test_first_assignment_is_not_a_reassignment`,
`test_each_handover_counts_once`.

---

## 5. Dashboard A — Ticket Performance MIS

**Where:** Helpdesk → Reporting → MIS → *Ticket Performance MIS*
**Built from:** `static/src/js/mis_dashboard.esm.js`,
`static/src/xml/mis_dashboard.xml`, served by `helpdesk.mis.dashboard`.

All arithmetic is on the server, so the dashboard, the pivot views and the
exports cannot drift apart.

### 5.1 The six KPI cards — BRD 7.1

| Card | Measured on | Notes |
|---|---|---|
| Tickets Opened | Created in the period | |
| Tickets Closed | **Closed** in the period | A different set of tickets |
| Currently Open | Right now, period ignored | |
| Avg Handling Time | The selected period | Basis from [3.1](#31-handling-time-basis--brd-181) |
| Max Ticket Duration | The selected period | |
| Tickets Reassigned | The selected period | Distinct tickets, not events |

**The detail BRD 7.1 insists on:** every card prints its own date basis
underneath — *"created in the period"*, *"closed in the period"*, *"open right
now, period ignored"*. Tickets created in a period and tickets closed in that
period are different sets, and the screen says which one it means.

**How to test it.** Create a ticket 90 days ago and close it today, then raise
a fresh one. With a 30-day window: *Opened* = 1, *Closed* = 1, and they are
different tickets. Automated: `test_opened_and_closed_are_different_sets`.

### 5.2 Charts — BRD 13.1

Opened vs closed over time (day / week / month), volume by team, by category,
by person, and average handling time by team. Chart.js, loaded on demand via
`loadBundle("web.chartjs_lib")`.

### 5.3 The settings banner

A strip under the filters naming the live rules: handling basis, team and
person attribution, closure counting, working hours, timezone. So nobody reads
a number without knowing which definition produced it.

**How to test it.** Change the handling basis in Settings and reload — the
banner text changes. Automated:
`test_settings_banner_reports_the_live_rules`.

---

## 6. Dashboard B — Open Tickets MIS

**Where:** Helpdesk → Reporting → MIS → *Open Tickets MIS*

### 6.1 The six KPI cards — BRD 8.2

| Card | Meaning |
|---|---|
| Total Open Tickets | Every ticket in a non-closed stage |
| Open, Created in Period | Open now *and* raised in the window |
| Open Past Threshold | Older than the warning threshold |
| Average Open Age | Creation → now |
| Maximum Open Age | The oldest one |
| Open Tickets Reassigned | Changed hands at least once |

### 6.2 Ageing distribution

A bar chart over `age_band`: *Within threshold / Ageing / Overdue /
Critical*, with the boundaries from [3.8](#38-ageing-thresholds--brd-188).

### 6.3 Age and current hold are kept apart — BRD 8.7

The BRD is explicit: *"A ticket may be old overall but recently assigned to
its current agent."* So `open_age_hours` (creation → now) and
`current_assignment_hours` (current interval start → now) are separate
columns and never mixed.

**How to test it.** Take a 200-hour-old ticket and reassign it. Total age
stays ~200h; current hold resets to ~0. Automated:
`test_current_assignment_duration_is_separate_from_age`.

---

## 7. Filters and controls

Every control BRD section 6 lists, on both dashboards:

| Filter | Built | Note |
|---|---|---|
| Date From / Date To | ✅ | Real date pickers |
| **Date Basis** | ✅ | Created / Closed / Currently open |
| Helpdesk Team | ✅ | Multi-select |
| Ticket Category | ✅ | Multi-select |
| Assigned Person | ✅ | Hidden without the person-data right |
| Ticket Status | ✅ | All / Open / Closed / Unassigned |
| Ticket Priority | ✅ | Multi-select |
| Customer | ✅ | On the report search view |
| Sorting | ✅ | Every column in list and pivot |
| Reset Filters | ✅ | Button |
| Export | ✅ | [Section 11](#11-export-and-drill-down) |
| Trend granularity | ✅ | Day / week / month (extra) |

The same filters exist in the search view of every detailed report, including
three separate period filters — *Created in period*, *Closed in period*,
*Assigned in period* — so the date basis is never ambiguous.

**How to test it.** Filter Dashboard A to one team: the *Opened* KPI drops to
that team's share. Automated: `test_filters_narrow_the_result`.

---

## 8. Detailed reports A1–A5 and B1–B4

All under **Helpdesk → Reporting → MIS → Detailed reports**, each a real Odoo
list/pivot/graph, so sorting, grouping and export come for free.

| Report | Menu | Grouped by | Key measures |
|---|---|---|---|
| A1 | Tickets by Team | `attributed_team_id` | Opened, closed, open now, avg handling, max duration, reassigned, avg reassignments |
| A2 | Tickets by Category | `category_id` | As A1 |
| A3 | Tickets by Person | `attributed_user_id` | As A1 plus tickets touched, avg/max time held, reassigned in/out |
| A4 | Longest Running Tickets | — | Sorted longest first, all 14 BRD 7.5 columns |
| A5 | **Time Held by Each Person** | `user_id` | One row per interval |
| B1 | Open by Team | `attributed_team_id` | Open, unassigned, avg/max age, avg current hold, opened/closed in period |
| B2 | Open by Category | `category_id` | As B1 |
| B3 | Open by Person | `attributed_user_id` | As B1 |
| B4 | Open Ticket Detail | — | Sorted oldest first, all 16 BRD 8.6 columns |
| — | Closure & Reopen Events | — | The raw event ledger |

### 8.1 A5 in detail — the flagship report

BRD 7.6 calls this *"a key requirement for Electrocare"*. The data existed
already; there was no report over it. Now there is:
`helpdesk.mis.assignment.report`, one row per assignment interval.

Columns: ticket number and subject, category, priority, team during the
interval, person, start, end, time held, cumulative time that person has held
that ticket, how the interval ended (*still assigned / transferred / ticket
closed / unassigned*), next person, and the ticket's total reassignments.

**The trap it avoids — FR-18 and BRD 7.4.** A ticket handled by three people
produces three rows. Summing naively would count that ticket three times. So
there are three different count measures:

| Measure | 1 on | Use it for |
|---|---|---|
| `interval_count` | every row | How many hand-offs happened |
| `ticket_count` | the first row of each ticket | **Distinct tickets** |
| `ticket_user_count` | the first row of each ticket+person pair | Tickets a person touched |

**How to test it.** Assign a ticket to A, then B, then back to A. A5 shows
three rows. Add the three measures to the pivot: intervals = 3, distinct
tickets = 1, tickets touched = 2. Automated:
`test_distinct_tickets_are_not_double_counted`.

**Cumulative per person.** A held it twice — 2h then 3h — so A's cumulative
figure on that ticket reads 5h, not 2h or 3h. Automated:
`test_cumulative_hours_add_up_per_person`.

**Open intervals.** An interval with no end runs to the moment you look, per
BRD 7.6. Automated: `test_open_interval_runs_to_now`.

---

## 9. Measures: what each number means

From BRD section 9, all on `helpdesk.mis.report`.

| Measure | Definition |
|---|---|
| `opened_count` | 1 per ticket, counted on creation date |
| `closed_count` | Per the closure-counting setting |
| `open_count` | 1 if the stage is not closed |
| `unassigned_count` | 1 if open with nobody assigned |
| `resolution_hours` | Creation → final closure. Empty while open |
| `first_resolution_hours` | Creation → **first** closure |
| `open_age_hours` | Creation → now. Empty once closed |
| `total_elapsed_hours` | Creation → closure, or → now if still open |
| `assignment_hours` | Sum of intervals; working or elapsed per setting |
| `max_assignment_hours` | The single longest hold |
| `current_assignment_hours` | Current interval → now |
| `handling_hours` | Whichever basis is configured |
| `reassignment_count` | Hand-offs; the first assignment excluded |
| `reassigned_ticket_count` | 1 if it ever changed hands |
| `closure_event_count` / `reopen_count` | Raw event counts |
| `hours_beyond_threshold` | How far past the warning threshold |
| `age_band` | ok / warn / high / critical / closed |

**Unassigned tickets — FR-15.** They are flagged `is_unassigned` and counted
separately, while still being part of the open total. The BRD is explicit that
they must be *"displayed separately rather than excluded"*. Automated:
`test_unassigned_is_separate_but_still_counted`.

---

## 10. Security and access

Four independent rights, in a new **Helpdesk MIS** section on the user form.
They are checkboxes, not a dropdown, because they are orthogonal.

| Right | Grants |
|---|---|
| **MIS: read reports** | The dashboards and reports |
| **MIS: manage reporting** | Everything, across all teams, plus the Settings page |
| **MIS: see person-level figures** | The by-person reports and charts |
| **MIS: export** | Excel/CSV export |

**Setting them:** Settings → Users → pick a user → **Helpdesk MIS** → tick →
Save. This section is visible without developer mode.

### How visibility is enforced — BRD section 4

BRD section 4 requires that *"users should not see restricted teams or tickets
merely because they appear in aggregated reporting."*

Record rules on both MIS views mirror `helpdesk_mgmt`'s own ticket rules
exactly — personal, team, all — plus a company rule. A person limited to their
own tickets sees MIS built only from their own tickets.

**The subtle part.** Odoo applies group-based record rules only for groups the
user actually holds. A reader in *none* of the helpdesk groups would match no
rule and therefore see **everything**. To close that, *MIS: read reports*
implies `helpdesk_mgmt.group_helpdesk_user_own` as a floor, so a rule always
matches. *MIS: manage reporting* implies the All Tickets right, which is the
Senior Management case from BRD section 4.

**How to test it.** Give someone *User: Personal tickets* plus *MIS: read
reports*. They see MIS for their own tickets only. Automated:
`test_personal_right_limits_the_rows`, `test_mis_reader_gets_a_helpdesk_floor`,
`test_report_model_is_not_readable_without_the_mis_right`.

**Person-level data.** Without that right, A3, A5, B3 and every by-person
chart disappear from both dashboards — server-side, not merely hidden.
Automated: `test_person_tables_hidden_without_the_right`.

---

## 11. Export and drill-down

### Export — FR-13

Every MIS report is an ordinary Odoo list, so **⚙ → Export** gives XLSX or
CSV, and it honours record rules: an exporter gets exactly the rows they can
see on screen. The dashboards carry an **Open full list / export** button on
each table that lands on the same filtered list.

**How to test it.** As a team-limited user, export A1 and confirm no other
team's tickets appear in the file.

### Drill-down — FR-12

- **A KPI card** → the tickets behind that exact number, with that card's own
  date basis.
- **A table row** → that group's tickets.
- **A row with a ticket** (A4, A5, B4) → straight to the ticket form.

Built by `action_drill_down`, which resolves the MIS domain to ticket ids and
opens a real `helpdesk.ticket` list, so everything you can normally do with a
ticket still works.

**How to test it.** Click *Tickets Closed*; the list that opens contains only
tickets closed in the window, not tickets created in it. Automated:
`test_drill_down_returns_the_tickets_behind_a_kpi`.

---

## 12. Background jobs and install behaviour

### 12.1 The backfill — BRD section 15

`hooks.py` runs once on install and reconstructs **one closing event per
ticket that is currently closed**, from its `closed_date`.

What it does **not** do, and will not pretend to: earlier closures of a ticket
that was reopened before this module existed left no trace. They are gone, and
the log says so rather than inventing them. The same log warns how many open
tickets have no assignment history at all, which predates `helpdesk_mgmt`'s
ledger.

**How to test it.** After installing, check the log for
`helpdesk_mis: reconstructed N closing events` and any warning about missing
assignment history. Then open an old closed ticket — its Closure History tab
has one event.

### 12.2 The hourly job

`ir_cron_helpdesk_mis_refresh` recomputes working-hour durations on open
tickets and active intervals. Elapsed durations are live in SQL and need no
job; working hours need `resource.calendar`, which SQL cannot read, and they
grow through the working day.

**How to test it.** Settings → Technical → Scheduled Actions → *Helpdesk MIS:
refresh working-hour durations* → **Run Manually**.

### 12.3 A note on how the views are created

Odoo creates tables and views in one interleaved loop — `_auto_init()` then
`init()`, model by model. A view defined the usual way could be built before
`helpdesk.ticket` has gained the columns it selects. Both MIS views therefore
defer creation to `registry.post_init()`, which drains after every table in
the module exists. This is why installation order cannot break them.

---

## 13. BRD traceability: FR-01 to FR-18

| ID | Requirement | Status | Where |
|---|---|---|---|
| FR-01 | Ticket Performance MIS dashboard | ✅ | `mis_dashboard.esm.js`, `_performance_dashboard` |
| FR-02 | Open Tickets MIS dashboard | ✅ | `_open_dashboard` |
| FR-03 | Date range filter | ✅ | Filter bar + `_period_domain` |
| FR-04 | Team, category, person, status, priority | ✅ | `_common_domain` + search views |
| FR-05 | Opened **and** closed in period | ✅ | `_basis_domain`, separate bases |
| FR-06 | Counts by team, category, person | ✅ | A1, A2, A3 |
| FR-07 | Average **and maximum** duration | ✅ | `*_max` measures with `aggregator="max"` |
| FR-08 | Open age and current assignment duration | ✅ | `open_age_hours`, `current_assignment_hours` |
| FR-09 | Assignment history and time held | ✅ | A5 |
| FR-10 | Reassignment counts, previous/next assignee | ✅ | `prev_user_id`, `next_user_id`, counters |
| FR-11 | Sort by max duration and avg handling | ✅ | List + pivot measures |
| FR-12 | Drill-down | ✅ | `action_drill_down` |
| FR-13 | Export to Excel/CSV | ✅ | Odoo export + export right |
| FR-14 | Access rights and record rules | ✅ | `helpdesk_mis_security.xml` |
| FR-15 | Unassigned shown separately | ✅ | `is_unassigned`, `unassigned_count` |
| FR-16 | Reopened / closure-event reporting | ✅ | `helpdesk.ticket.closure.event` |
| FR-17 | Configurable ageing thresholds | ✅ | Three company settings |
| FR-18 | No double counting of reassigned tickets | ✅ | Three count measures in A5 |

Section 18's ten decisions: all ten are settings —
[section 3](#3-the-ten-decisions-as-settings).

---

## 14. What is deliberately not built

Being straight about this matters more than a full tick sheet.

1. **Active work duration (BRD 9.1 option C).** Odoo has no record of time
   actually worked. The column `mis_active_work_hours` exists and stays at
   zero until a timesheet or timer module fills it. Selecting that basis
   reports nothing rather than passing elapsed time off as work time. The BRD
   itself recommends this: *"Active work duration should only be reported if
   Electrocare has a reliable source of actual work-time data."*

2. **History from before the ledger existed.** Assignment intervals only go
   back to when `helpdesk_mgmt` started recording them, and closures before
   this module can only be reconstructed as one event from `closed_date`.
   Gaps are logged, not filled in.

3. **Out-of-scope items from BRD 3.2.** CSAT, SLA prediction, AI
   categorisation, automatic performance scoring, payroll/utilisation,
   customer-facing portals and external BI are all excluded, as the BRD asks.

4. **Performance at Electrocare's real volume is unmeasured.** The views are
   indexed through the underlying tables and group in PostgreSQL rather than
   Python, but BRD section 11 asks for validation against real data and that
   has not happened.

---

## 15. Troubleshooting

**The MIS menu is missing.** You lack *MIS: read reports*. Settings → Users →
Helpdesk MIS.

**Every duration reads zero.** Working hours are on with no calendar that has
any working time. Either configure the calendar or turn the setting off.

**Avg Handling Time is empty.** The basis is set to *Active work* and nothing
populates `mis_active_work_hours`. See [section 14](#14-what-is-deliberately-not-built).

**Time-held figures are blank on old tickets.** They have no assignment
history — it predates the ledger. The install log counts them.

**A reopened ticket shows the wrong resolution time.** Check which measure you
are reading: `first_resolution_hours` is to the first closure,
`resolution_hours` to the last.

**Numbers differ between two reports.** Check the settings banner on the
dashboard. Team attribution and handling basis change what the same word
means, and the banner always names the rule in force.

**The dashboard will not load after an upgrade.** Hard-refresh the browser to
clear the asset bundle, then `-u helpdesk_mis`.
