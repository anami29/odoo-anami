# Premier Transport TMS for Odoo 18 Community

These addons implement BRD-TMS-001 v1.2 (*Transport Management System on Odoo 18 CE*) for Premier Transport Limited. They are built on the community `tms` module 18.0.1.1.0.

| | |
|---|---|
| Release | 18.0.1.5.0 (TMS-UIG-001 #7 closed: Document Types, breakdown repair cost as a trip expense, the Trip money menu kept, bundled base modules' texts; see #10). 18.0.1.4.0 removed requirement codes from screens (#11); 18.0.1.3.0 built the one-page Exception dashboard, Sales order P&L and Truck P&L to the agreed mockup TMS-OD18-001 (#12) |
| Author | RLFB (Riamona Luxury and Fashion Brands), [www.riamona.com](https://www.riamona.com) |
| Licence | LGPL-3. Two dependencies are AGPL-3; see #9. |
| Tests | 177 automated tests across the six modules, all passing with every module installed together |

## 1. Package contents

```
addons/
  tms_core            LR booking, trips, compliance gate, POD, charges, expenses, claims,
                      hire memo + three-way match, LR invoicing, credit control, TDS/GST on hire,
                      audit, exception dashboard (BRD #6–#8.3, #10–#12, #14–#17, #19–#21)
  tms_estimate        CRM lanes, cost items, per-km quotations from cost estimates, item numbering,
                      estimate lock/amendment, actual against estimate, sales order P&L (#8.1–#8.2, #9)
  tms_odc             Over-dimensional cargo: pieces, route by state, equipment, permits,
                      route survey, escort, extended START gate (#13)
  tms_profitability   Cost classes, standard rates, standard charge (analytic), truck pools,
                      prepaid release, month-end close with accruals, P&L views (#18)
  tms_contract        Value-capped sales contracts, reservations, invoice gate, extensions,
                      excess approvals, escalation, contract P&L (#8.4, WF-11, #23.7)
  tms_document_type   Document Types on sales orders, purchase orders, invoices and bills: Transport
                      or Standard; scrap and vehicle sales, office AMCs (CR-04, BRD #7.4)
third_party/
  stock-logistics-transport/tms       community tms 18.0.1.1.0, pinned (commit a9b47c8); code unmodified,
                                      descriptive texts edited (see #9) (SD-01)
  server-tools/auditlog               community auditlog 18.0.2.0.9, pinned (commit 126afd2); code unmodified,
                                      descriptive texts edited (see #9) (SD-08)
README.md
```

Dependency chain:

```
tms_core  ← tms_estimate ← tms_odc            ┐
                         ← tms_profitability  ├ ← tms_document_type
                         ← tms_contract       ┘
```

`tms_core` depends on `tms`, `auditlog`, `account_fleet`, `purchase`, `l10n_in`, `l10n_in_withholding`, `mail` and `auth_totp_mail_enforce`. `tms_estimate` adds `crm`, `sale_management` and `sale_crm`; `tms_document_type` adds `purchase_requisition`. Every module other than `tms` and `auditlog` is standard Odoo 18 CE. No extra Python packages are needed.

The BRD names four modules. `tms_core` is the fifth: it carries the operations layer (LR to invoice) that the other four extend through hooks. `tms_document_type` is the sixth: change request CR-04, kept apart so that the transport modules stay unchanged for Transport documents.

## 2. Tested environment

| Component | Version |
|---|---|
| Odoo | 18.0 Community, commit 6a57ec6 (19 Oct 2026) |
| Python | 3.11 |
| PostgreSQL | 16 |
| Chart of accounts | Indian (`l10n_in`), with GST taxes and fiscal positions |

## 3. Installation

1. Copy `addons/*` and `third_party/*/*` into directories on the server's `addons_path`, for example:
   ```
   addons_path = /opt/odoo/odoo/addons,/opt/odoo/addons,/opt/tms/third_party/stock-logistics-transport,/opt/tms/third_party/server-tools,/opt/tms/addons
   ```
2. Restart Odoo and update the Apps list.
3. On a database whose main company is in India (so the Indian chart loads), install **TMS Document Types**. Odoo installs the other five TMS modules, `tms` and `auditlog` with it. From the command line:
   ```
   odoo-bin -c odoo.conf -d <db> -i tms_document_type --stop-after-init
   ```
   Installing it on a database that already holds orders, invoices and bills gives each of them its Transport type.
4. Set an outgoing mail server and `mail.default.from`. Activities work without one, but the notification emails of #20 need it.

Installation leaves two settings that affect every user:

- Two-factor sign-in becomes mandatory for internal users (`auth_totp.policy = employee_required`, BR-NFR-05). Until a user sets up an authenticator app, every login asks for a 6-digit code sent by email, so an outgoing mail server and an email address on each user are needed before the first login. Remote calls (XML-RPC, JSON-RPC) need an API key.
- Status and approval fields can then change only through their action buttons. Direct `write`, `create`, `copy`, form saves, XML-RPC/JSON-RPC calls and imports cannot set them (BR-AUD-06, see #7).

## 4. Configuration checklist

Work through the list in this order. The values marked OP-x are placeholders until the open point is closed.

| # | Where | What |
|---|---|---|
| 1 | Settings › Companies | Create each branch as a child company of the main company. Give it a **branch code** (prefix of its LR numbers), state and GSTIN. Contract numbers come from the branch's own contract series, CON/<year>/nnnn. Odoo loads the branch's chart and journals. |
| 2 | Settings › Users | Assign the 14 roles in *Transport Roles* (Sales Executive … Auditor), plus *Contract Excess Approver* or *Director* where needed (BR-BRN-07). Give each user only their branches. The TMS Administrator also needs *Administration › Settings*: Odoo saves settings only for that group. |
| 3 | Settings › Transport › Operating mode and controls | Operating mode (locked after the first LR), compliance enforcement (TMS Administrator only; the switch asks for a reason, which is logged), tolerances, DSR cadence, POD SLA, reminder offsets, e-way bill threshold, charge self-approval limit. |
| 4 | Transport › Configuration › E-way bill state thresholds | Per-state intra-state thresholds, if any (BR-EWB-03). |
| 5 | Settings › Transport › Trip accounting | Driver advance account, expense, cash and bank journals, fuel card and FASTag accounts, inter-branch clearing account (BR-BRN-06), freight product, customer TDS receivable, **TDS on hire and on commission** (OP-22). |
| 6 | Transport › Configuration › GTA GST options (Finance Head) | GTA GST option per financial year: forward 5%, forward 18% or reverse charge (BR-CFG-10). |
| 7 | Contacts | Customers: freight basis, billing cycle, credit limit, free hours and detention rate. Transporters: GTA status (sets GST on hire), own TDS tax if it differs from the branch default, TDS declarations per year. |
| 8 | Fleet / Transport › Master data | Vehicle classes, vehicles (ownership, payload, class, fuel norm), drivers, compliance documents, market-vehicle register. |
| 9 | Transport › Configuration | Cost categories and cost items with their bases and markups; cost pricelist (Settings › Estimates). |
| 10 | Settings › Transport › ODC | Turn ODC on per branch; set the width, height, length and weight thresholds and ODC km per day (OP-04, OP-05). |
| 11 | Settings › Transport › Profitability | Capitalisation threshold, revenue dating (OP-14), empty-return rule, review interval and flag. Then enter standard rates per vehicle class (Finance Head) and truck cost categories. |
| 12 | Settings › Transport › Contracts | Contract series, utilisation and expiry alerts, extension limit, excess ceiling, run-on days. |
| 13 | Transport › Configuration › Document types | Check the eleven seeded types; set the approval limit of Scrap sale (₹50,000 proposed, OP-23) and the journal of any type that posts in its own journal. Give head-office purchasers a personal default (Settings › Users › Document types) if they mostly buy for the office. Put products only transport may use in *Transport services*, or tick *Transport only* on them; cost items, freight and hire products carry it already. |
| 14 | Settings › Technical › Scheduled actions | Check the 12 `TMS:` crons are active: compliance reminders, DSR overdue, daily customer DSR (09:00 IST), POD past SLA, e-way bill expiry, godown demurrage, blocked hire bills, quotation expiry, ODC ETA, prepaid release, contract alerts, driver anonymisation. |

## 5. Where things are

| Menu | Content |
|---|---|
| CRM | Odoo's own CRM app: leads and opportunities with transport lanes, pipeline (Sales › My Pipeline), pipeline analysis (Reporting › Pipeline) |
| Sales › Orders | Quotations and orders (Odoo's own), cost estimates |
| Sales › Contracts | Contracts, contract items, extensions, excess approvals, reservation ledger, diesel escalation |
| Sales › Reporting › Transport | Sales order P&L (one page), Sales order P&L analysis (list, pivot, chart), estimate against actual, contract P&L |
| Transport › Operations | **Booking:** consignment notes (LR), contract items (Booking Clerk), e-way bills, e-way bill tasks. **Trips:** all trips (list, kanban), planning board, daily status report, POD verification. **Exceptions:** incidents, breakdowns, damage and shortage claims. **ODC:** permits, equipment. **Fleet:** odometers |
| Transport › Trip money | **Invoices:** freight invoices. **Charges:** charges and recoveries, trip expenses. **Driver:** driver advances, driver settlements. **Hire:** hire memos, three-way matches. **Period close:** month-end close, prepaid truck costs |
| Transport › Master Data | Vehicles, market vehicles, insurance policies, drivers, locations, routes; **Compliance register:** vehicles and drivers (one row per holder, a column per document, gate status, Renew), compliance documents |
| Transport › Dashboard | Exception dashboard (one page: six tiles, exceptions requiring action, trip contribution, unbilled charges and pending recoveries, the month's breakdowns and replacements), filtered dashboard (branch, customer, vehicle, period), KPI board (KPI cards that drill down), bills and expenses awaiting variance approval |
| Transport › Reporting | **Registers:** LR register, DSR log, claims register, compliance expiry, override log. **Ageing and payables:** recovery ageing, receivables ageing, hire payables, TDS on hire (declarations). **Fleet:** trips, vehicle utilisation, costs. **Profitability:** trip, leg, customer, truck (one page) and truck analysis, fleet, vendor, branch, company, recovery statement, WIP, ledger (Trip and Truck P&L also as bar charts) |
| Transport › Configuration | Settings, stages, master data types, standard rates, **Document types** (TMS Administrator maintains them), **KPI alert rules** (TMS Administrator: when each dashboard card turns red) |
| Purchase › Orders › Purchase agreements | Office-asset maintenance contracts: blanket orders of type Annual maintenance contract, with **Bill next period** |

In Operations, Trip money and Reporting the bold names are group headings: they show as captions inside the menu and hide themselves when a user can open none of their items.

The prints of BRD #21 are on the document's **Print** menu: the LR (4 copies), trip sheet (with an ODC section), POD form, DSR, claim note, quotation with cost sheet and contract statement. The hire memo prints from its **Print hire memo** button, and the customer DSR goes out only as the attachment of the daily DSR email. With Marathi or Hindi set as a branch's second print language, the LR and POD form print each label in English and that language (BR-PRN-08).

## 6. Running the tests

```
odoo-bin -c odoo.conf -d tms_test -i tms_document_type \
  --test-enable --test-tags /tms_core,/tms_estimate,/tms_odc,/tms_profitability,/tms_contract,/tms_document_type \
  --stop-after-init
```

Expected result: `0 failed, 0 error(s) of 177 tests`. The test companies have no mail server, so the log shows "failed sending mail … sender address" lines; they don't indicate a failure.

## 7. Controls worth knowing

- **Status and approval fields.** A guard checks every remote call: the web client, XML-RPC and JSON-RPC. It refuses a status or approval field sent through `write`, `create`, `copy`, a form save (including inside one2many lines) or an import. It also refuses to create a document from the client without the role #4 gives that right, whether by form, quick-create, duplicate, one2many line or import. Only the TMS Administrator deletes TMS documents, and that rule is enforced in the model, so it also covers one2many deletes (BR-AUD-06, BR-BRN-07, BR-BRN-08). Internal override keys in a request's context, such as a credit-override reason, are dropped. Buttons, wizards and crons are unaffected. Each module adds its own fields through `tms.rpc.guard._tms_guarded_fields()`.
- **Overrides.** Every override, waiver, removal and rejection asks for a reason and writes an entry in the read-only override log (BR-AUD-06).
- **Audit.** `auditlog` rules cover the TMS, quoting, contract, ODC and profitability models. Audit entries and chatter tracking are read-only and kept 8 years (BR-AUD-02, BR-AUD-03).
- **Posting.** The TMS checks before posting and the follow-ups after it run on every posting path: the Post button, bulk *Post entries* and auto-post.
- **Strict branch accounting.** This is on by default. TMS invoices, bills and entries post only in the document branch's own journals (BR-BRN-04). Where a branch has no sale, purchase or general journal, one is created as a copy of its parent's.
- **TDS on hire.** When a hire bill posts, TDS is withheld through `l10n_in_withholding` if the section's per-payment or annual limit per PAN is crossed. Hire items use the vendor's or the branch's tax; commission uses the commission tax. A valid transporter declaration exempts the hire, and the bill then appears in *TDS on hire (declarations)* with the vendor's PAN and the declaration reference (BR-HIR-10 to BR-HIR-14).
- **GST.** Freight is taxed CGST+SGST or IGST by place of supply, through the `l10n_in` fiscal positions. Hire GST follows the vendor's GTA status: none, 5% reverse charge, or as billed (BR-INV-05, BR-HIR-11).

## 8. Open points and deviations

| Item | State |
|---|---|
| OP-22 TDS sections | Defaults are 2% under 393(1) Sl.6 for contract payments (non-individual) and 2% under 393(1) Sl.1(ii) for commission (Income-tax Act 2025). Set a vendor-level tax for individuals (1%). When the annual aggregate is crossed, TDS is deducted on the bill that crosses it, not retrospectively on earlier bills of the year. The tax advisor must confirm both points before go-live. |
| OP-04, OP-05, OP-14, OP-19, OP-21 | ODC thresholds, revenue dating, run-on days and claim limits are settings, seeded with the BRD's working values. |
| BR-INT-01 bank statement import (Should) | Not bundled. Add a bank-statement file import and a reconciliation widget for Odoo 18.0 Community. |
| BR-DSH-04 spreadsheet dashboard (Should) | The exception dashboard is a one-page Odoo screen whose tiles and rows open the records behind them; the KPI cards stay on the KPI board. Neither uses `spreadsheet_dashboard`. |
| BR-NFR-13 driver PII (Should) | Licence number and file are restricted to the Fleet Manager, Dispatchers and managers, and former drivers are anonymised after the retention period (`tms_core.driver_retention_years`, default 8). A driver's phone and address sit on the shared contact record and are not restricted per field. |
| BR-HIR-15 cancelled hire memo | The advance already paid stays as a debit balance on the vendor's payable, and the accountants get an activity to recover it or net it against the next bill. No vendor refund entry is raised, because that would count the advance twice. |
| E-way bills (SD-10) | Phase 1 is manual, with evidence; GSP APIs come in phase 2. |
| Standard invoices and e-way bills (BR-DOC-12) | A Standard invoice raises no TMS e-way bill. Odoo offers the e-way bill on the invoice once `l10n_in_edi_ewaybill` is installed and connected to the GST portal; it is not bundled. |
| OP-23 scrap approval limit | Scrap sale is seeded with ₹50,000, the BRD's proposed value. Confirm it. |
| KPI thresholds | The alert rules carry the BRD's working values; the client confirms them. |
| Profitability | The reconciliation check blocks the close at company level. Branch differences from cross-branch trips show on the branch view only. |
| Contracts | With a GST-inclusive value basis, pure-agent incidentals are grossed up as well. KPI-21 counts items held live. |
| Updating `tms` | On a database with CRM or Calendar, `-u tms` and `-u all` fail validating the base driver form (`tms.view_tms_driver_form_inherit`), which copies the contact form and its Meetings and Opportunities buttons; the error is "schedule_meeting is not a valid action on tms.driver". This is in the base `tms` 18.0 module itself (no upstream fix at commit a9b47c8). Update the TMS by module name: `-u tms_core,tms_estimate,tms_odc,tms_profitability,tms_contract`, which runs clean. A one-line upstream fix is to clear the button box in that base view. |

## 9. Licences

The six `tms_*` addons are LGPL-3. `tms` and `auditlog` are AGPL-3, and their licence texts are in `third_party/*/LICENSE`. Because `tms_core` depends on them, a distribution of the combined work must meet AGPL-3: offer the source to users of a network deployment.

The bundled copies of `tms` and `auditlog` are edited in their descriptive texts only (TMS-UIG-001 #7 item 9): the author is the original company (Open Source Integrators for `tms`, ABF OSIELL for `auditlog`); the upstream project's badges, issue-tracker and maintainer sections and its credits are removed from `README.rst`, `readme/` and the Apps page; translator credits in the `.po` headers are cleared. The code, the licence files and every copyright notice are unchanged.

## 10. Changes in 18.0.1.5.0

The open items of TMS-UIG-001 #7 are closed: item 1 (the Trip money menu keeps its name), item 2 (Document Types, change request CR-04), item 4 (breakdown repair cost) and item 9 (the bundled base modules' texts).

| Area | Change |
|---|---|
| Document Types (new module `tms_document_type`) | Every sales order, purchase order, customer invoice and vendor bill carries a Document Type (`tms_document_type_id`, model `tms.document.type`). A **Transport** type keeps every rule of the TMS; a **Standard** type runs as plain Odoo: no cost estimate, item approval, contract check, LR, estimate line, vehicle, cost class, variance or three-way match, and *Create Invoice* works on the order. Eleven types are seeded: TRN-SO, GEN-SO, SCR-SO, VEH-SO, TRN-PO, GEN-PO, AMC-PO, TRN-INV, GEN-INV, TRN-BILL, GEN-BILL. Only the TMS Administrator maintains them |
| Defaults | New documents take the Transport type, or the user's personal default. Documents the transport flows create (quotations from opportunities, contract orders, hire memos, LR invoices, trip and hire bills) always take the Transport type |
| Rules on Standard documents | A Standard document refuses any transport link (trip, LR, estimate line, vehicle, cost class, hire memo, contract) and any product marked *Transport only* (cost items, freight and hire services, the *Transport services* category). A type may limit the product categories (Scrap sale: Scrap only). The type is chosen in draft only, and a Transport document with links stays Transport; every change is noted in the chatter. Invoices, bills, credit notes and down payments take the type their order or original names |
| Numbering and approvals | Scrap sale and Vehicle sale number from their own series (SCR/YYYY/NNNN, VEH/YYYY/NNNN); a type may name the journal its invoices post in. Above a type's approval limit an order waits for an approver (Scrap sale: Branch Manager above ₹50,000). Odoo's purchase approval also accepts the Branch Manager |
| Accounting of Standard documents | A Standard bill line books to its own branch's analytic account. Its lines post as branch overhead; Standard invoice lines post as **Other income**, a new column of the branch and company P&L. Neither reaches a trip, order, customer, truck, fleet, vendor or contract view |
| Vehicle sale | Names the owned vehicle sold; the Fleet Manager and the Finance Head approve; refused while the vehicle has an open trip or assignment. On delivery the vehicle moves to the **Sold** stage (no longer assignable to a trip or as a replacement) and its unreleased prepaid costs release in the month of the sale |
| Office maintenance contracts | A purchase agreement of type Annual maintenance contract carries the assets covered, the cover period, the value and the billing frequency, with a renewal activity 30 days before the end. **Bill next period** raises the period's Standard bill; a cover of more than a month goes to prepaid and releases monthly to branch overhead (prepaid schedules now take branch overhead as well as trucks). A truck AMC stays a Transport purchase spread on each truck's fixed pool |
| Breakdown repair cost (item 4) | The towing and repair cost entered on a breakdown becomes a draft trip expense of type *Roadside repair*, linked to the breakdown, its incident and the fleet service log (*Repair expense*). A corrected cost updates the draft; a cost recoverable from the vendor is marked recoverable |
| Trip money menu (item 1) | Kept as delivered: Transport › Trip money |
| Bundled base modules (item 9) | Descriptive texts edited; see #9 |
| Groups | Branch Accountant and Fleet Manager get Sales rights (standard sales, vehicle-sale approval); Branch Manager and Finance Head get Purchase rights (approvals) |

Tests: 21 added. `tms_document_type`: 19 (`TestDocumentType`, `TestDocumentTypePnl`), one per group of cases UT-DOC-01 to UT-DOC-36. `tms_core`: 2 in `TestUiBreakdown` (repair cost booked as an expense; none when the cost is zero). The no-code scan now covers `tms_document_type` too.

Upgrade from 18.0.1.4.0: update the five modules and install the sixth: `-u tms_core,tms_estimate,tms_odc,tms_profitability,tms_contract -i tms_document_type`. Installing it gives every existing order, invoice and bill its Transport type.

## 11. Changes in 18.0.1.4.0

Decision 10 of TMS-UIG-001 #7 is closed: users no longer see requirement codes. Codes such as `BR-…`, `KPI-…`, `OP-…`, `WF-…` and BRD section references are removed from every field label, help tooltip, placeholder, selection value, button, menu, error and warning message, chatter note and Apps page. The code stays only where a tester or developer looks for it: this README, the tester's manual TMS-UM-001, the test specification UTS-TMS-001, code comments and the tests.

| Area | Change |
|---|---|
| Messages | About 370 texts across the five modules lose their code, for example "Audit records are read-only (BR-AUD-03)." now reads "Audit records are read-only." The wording is otherwise unchanged |
| Tooltips | A help text that held only codes is removed, so the field shows no tooltip |
| KPI board | The KPI `code` is hidden on the KPI cards, the KPI list and the alert-rule list. It stays on the record as a technical key; the seeded KPI data is unchanged |
| Apps pages | The module descriptions drop their BRD references and show version 18.0.1.4.0 |

No data model change. The 153 existing tests pass unchanged, since none of them matched a message by its code.

Tests: 3 added in `tms_core` (`TestNoRequirementCodes`, post-install). With all five modules installed, they scan every field label and tooltip, selection value, menu, action, model name, module summary and view of the five modules (labels, tooltips, placeholders, titles, confirmations and visible text, not XML comments) and fail on any requirement code or BRD section reference. The scan found 13 texts the first pass had missed (codes on a continuation line of a message, and three `#18.3` tooltips); they are fixed.

Upgrade from 18.0.1.3.0 by module name: `-u tms_core,tms_estimate,tms_odc,tms_profitability,tms_contract`. Labels and help texts update on upgrade; no migration script is needed.

## 12. Changes in 18.0.1.3.0

The Exception dashboard, Sales order P&L and Truck P&L are now the one-page screens of the agreed mockup TMS-OD18-001 (TMS-UIG-001 #7, decision 3: one page, replacing the KPI board as the first screen). Each page has a Branch and Period filter, Refresh, and an "As of" time; the P&L pages also export their main table as CSV. Every tile, row and link opens the records behind it, under the user's own access rights.

| Area | Change |
|---|---|
| Exception dashboard | Transport › Dashboard › Exception dashboard. Six tiles: active breakdowns, vehicle replacements this month, open damage claims with their exposure, unbilled charges and recoveries (customer and vendor), PODs awaiting verification (with the vendor bill they hold), trips delayed in transit. **Exceptions requiring action**, most urgent first: breakdowns on the road, unverified PODs, hire bills held by the three-way match, claims to approve or complete, expired compliance documents (with the trip they block), documents expiring within 30 days, delays; each with its **Next action**. **Trip contribution** of the last eight delivered trips (open trips hatched, with approved commitments and unbilled charges; totals count closed trips only). **Unbilled charges and pending recoveries**. **Breakdowns and replacements** of the month as cards |
| KPI board | The KPI cards move to Transport › Dashboard › KPI board; alert rules and drill-downs are unchanged |
| Sales order P&L | Sales › Reporting › Transport › Sales order P&L. Tiles: revenue, direct cost and contribution against plan, and orders over the variance tolerance. One row per sales order with trips started in the month, plus one row for LRs without an order (walk-in, To-Pay); revenue, direct cost, cost variance and contribution, each with its plan. **Margin · planned to actual** chart. For the chosen order: why actual differs from plan, **cost by element** (planned, actual, over plan; estimate lines by cost item, or item/line for ODC) and the trips on the order with their cost plan spread by charged weight. Direct cost includes the standard charge of owned trucks. The list, pivot and chart stay as Sales order P&L analysis |
| Truck P&L | Transport › Reporting › Profitability › Truck P&L. Tiles: truck revenue, trip expenses (with replacement add-ons), truck expenses, operating result and the trucks below zero. One row per truck with its operating result bar. For the chosen truck: the period statement (revenue by trip, trip expenses and add-ons, truck expenses, operating result, and any standard charge not yet matched), its trip P&L, and its bills and expenses filtered All / Trip / Truck or by trip. Figures tie to the truck P&L view. The list, pivot and chart stay as Truck P&L analysis |
| Defect | Posting a vendor bill line that names a truck (every trip bill line does) created a fleet service log in state New, which then counted as an open workshop job and refused the truck on its next trip ("has an open breakdown or service log"). Service logs created from bill lines no longer block a truck; workshop jobs and breakdown service logs still do |

Tests: 6 added in `tms_profitability` (`TestUiCockpit`, `TestUiCockpitPages`). They seed one branch month (closed and open trips, a held hire bill, a breakdown on the road, a market replacement, a delay, a claim, unbilled charges, an expired and an expiring document), check each page's figures against the P&L views as a branch manager, and check that a bill-created service log does not block a truck. `TestUiCockpitPages` opens the three pages in a browser when `TMS_SHOT_DIR` and `TMS_SHOT_SCRIPT` are set; otherwise it is skipped.

Upgrade from 18.0.1.2.0 by module name: `-u tms_core,tms_estimate,tms_odc,tms_profitability,tms_contract`. The menus move to the new pages on upgrade.

## 13. Changes in 18.0.1.2.0

Screens aligned with the agreed mockup TMS-OD18-001, from the gap analysis TMS-UIG-001. Items that wait for a client decision are listed at the end.

| Area | Change |
|---|---|
| Trip form | POD tab with Upload / Verify per LR and a header **Verify POD** (a different POD Verifier than the uploader); POD-pending banner; smart buttons Consignments, Incidents, Claims, Expenses, Vendor bill, LRs to invoice; subtitle (route · LRs · weight); Current assignment group (shows a market replacement's vehicle, broker, hire and driver); schedule group with variance; Status log tab with coloured badges; **Report incident**, **Log status**, **Mark done**, **Print trip sheet** |
| Market documents | The market-vehicle document tab shows whenever a market vehicle may be assigned (market trip, trip on the road, open breakdown), so a replacement by a market vehicle can pass the gate |
| Trips | List and kanban; route, vehicle, driver or broker, scheduled start, LR count, pre-start compliance (Valid 6/6 / PUC expired / Pending), latest status badge, single stage badge; row buttons Start / Depart / End / POD pending by stage; default filter Not cancelled |
| Daily status report | One row per trip on the road, grouped by latest status: customer, last update, location, remark, ETA, delay vs plan, next update due; **Log status** per row. Operations › Trips. The line log stays in Reporting › Registers › DSR log |
| Breakdown | One dialog reports and resolves: cause, place, repair cost; market replacement, own replacement (hidden in Broker mode, reposition cost booked as an expense, driver from the vehicle) or repair with a revised ETA (writes a status line); live compliance check of the replacement (Confirm blocked while it fails); e-way bills on board with their Part-B target; breakdowns numbered BD/yyyy/nnnn |
| Compliance register | Master Data › Compliance register: one row per owned vehicle (RC, Fitness, Permit, Insurance, PUC) and per driver (licence), each cell dated and coloured, gate status, **Renew** |
| Vendor bill | Three-way match on the bill: hold badge, match page (elements, variance, tolerance, totals), **Re-run match**, **Apply recoveries**, Trip and Hire memo buttons |
| Freight invoices | Trip money › Invoices › Freight invoices lists customer invoices raised from LRs |
| Charts and P&L | Bar charts for Trip, Sales order and Truck P&L; Truck P&L opens on this month by truck with a Below-zero filter; Sales order P&L groups by order. Ratio columns (margin %, recovery %, per-km, utilisation) on group rows are now sum over sum, not averages |
| Defects | GSTIN, then PAN, customer match on lead conversion; KPI cards turn red by each card's own rule (Configuration › KPI alert rules); compliance security days default 15 on new databases; Consumer mode no longer offered; Return to origin hidden (phase 2); claim credit note linked to the LR invoice; incident claims linked to their POD line; bill-only columns hidden on customer invoices; Approve variance hidden on customer invoices; Hire payables and Bills awaiting variance open the bill list; amounts in hire-bill messages in Indian format |

Waiting for a client decision (TMS-UIG-001 #7) at this release (all closed in 18.0.1.5.0, see #10): Document Types, the one-page exception dashboard (built in 18.0.1.3.0, see #12), the name of the Trip money menu, how the breakdown repair cost is booked, requirement codes in screen labels (closed in 18.0.1.4.0, see #11), and the base module's author text in Apps.

Upgrade from 18.0.1.1.x by module name: `-u tms_core,tms_estimate,tms_odc,tms_profitability,tms_contract`. Existing databases keep their compliance security days and KPI alert settings; new databases get 15 days and the rules above.

## 14. Changes in 18.0.1.1.1 and 18.0.1.1.2

| Area | Change | Cases |
|---|---|---|
| Module info (18.0.1.1.2) | Author RLFB (Riamona Luxury and Fashion Brands), website www.riamona.com, and a description page on each module's Apps entry | — |
| Menus | Operations, Trip money and Reporting group their items by activity (#5). Claims move from Trip money to Operations › Exceptions as *Damage and shortage claims*; the Claims register stays in Reporting. No item changes role or access | UT-CFG-23 (new) |
| Trips | The LR field on a trip's Consignments tab explains why the list can be empty: only confirmed LRs with packages not yet planned are offered | — |

Upgrade from 18.0.1.1.0 or 18.0.1.1.1 by module name: `-u tms_core,tms_estimate,tms_odc,tms_profitability,tms_contract`.

## 15. Changes in 18.0.1.1.0

Fixes found while writing the tester manual (TMS-UM-001). Each has a regression test; the suite is now 93 tests.

| Area | Change | Cases |
|---|---|---|
| Quotations | **Copy with fresh costs** carries the original estimate's lane, states, pieces, route and cost lines, reprices them at today's cost and clears typed-cost reasons | UT-QTN-28 |
| Hire memo | The Branch Manager's **Approve extra** no longer stops on purchase-line access rights | UT-HIR-03, UT-CHG-21 |
| Master data | The Fleet Manager creates routes | UT-MD-03 |
| Configuration | New menu Transport › Configuration › GTA GST options for the Finance Head and TMS Administrator | UT-CFG-22 |
| Configuration | Switching compliance enforcement asks for a reason ("Reason for the switch" in Settings); the reason is logged | UT-CFG-14 |
| Claims | **Accept owner's risk** and **Admit late notice** open a reason dialog; the reason is logged | UT-CLM-15, UT-CLM-16 |
| Detention | Charged per LR to that LR's billing party; DSR lines carry an optional LR (stop) | UT-CHG-22 (new) |
| Driver settlement | Only the Branch Accountant settles | UT-CHG-04 |
| Prints | LR and POD form print bilingual labels when the branch's second print language is Marathi or Hindi; a Marathi language record is included | UT-PRN-09 |
| Dashboard | The customer and vehicle filters narrow every tile's amount as well as its count; KPI-02 follows the branch filter; KPI-18 reads the month the filter date falls in | UT-DSH-02 |
| P&L views | Customer, Fleet, Market vendor, Branch and Company P&L rows drill to their source rows | UT-PNL-56 |
| Audit | The trip Timeline reads oldest first (latest 500 events) | UT-AUD-08 |
| Credit control | Refusal messages print amounts in Indian grouping (₹4,80,000.00) | UT-CRD-03 |
| E-way bill tasks | A task can be created from the menu: the Trip column is editable on new lines | — |
| Upgrade | Updating the modules on a database with CRM or Calendar failed validating the driver form, which inherits the contact form's smart buttons (Meetings, Opportunities). The driver form now drops the contact's smart buttons, and an update from 18.0.1.0.0 runs clean | — |
| Menus | Sales work moved out of the Transport app into Odoo's own apps: opportunities and pipeline in CRM; quotations, cost estimates, contracts and the sales reports in Sales. Transport › Sales is gone; the Booking Clerk keeps Transport › Operations › Contract items | — |
| Labels | About 70 fields that showed generated labels ("Tms Mode", "Tms Exposure", "Pod Status") now have proper ones | — |
