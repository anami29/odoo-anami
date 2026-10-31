# Premier Transport TMS for Odoo 18 Community

These addons implement BRD-TMS-001 v1.2 (*Transport Management System on Odoo 18 CE*) for Premier Transport Limited. They are built on the community `tms` module 18.0.1.1.0.

| | |
|---|---|
| Release | 18.0.1.1.1 (menus grouped by activity; see #10) |
| Author | Ranadive Consultancy Services |
| Licence | LGPL-3. Two dependencies are AGPL-3; see #9. |
| Tests | 94 automated tests across the five modules, all passing with every module installed together |

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
third_party/
  stock-logistics-transport/tms       community tms 18.0.1.1.0, pinned (commit a9b47c8), unmodified (SD-01)
  server-tools/auditlog               community auditlog 18.0.2.0.9, pinned (commit 126afd2), unmodified (SD-08)
README.md
```

Dependency chain:

```
tms_core  ← tms_estimate ← tms_odc
                         ← tms_profitability
                         ← tms_contract
```

`tms_core` depends on `tms`, `auditlog`, `account_fleet`, `purchase`, `l10n_in`, `l10n_in_withholding`, `mail` and `auth_totp_mail_enforce`. `tms_estimate` adds `crm`, `sale_management` and `sale_crm`. Every module other than `tms` and `auditlog` is standard Odoo 18 CE. No extra Python packages are needed.

The BRD names four modules. `tms_core` is the fifth: it carries the operations layer (LR to invoice) that the other four extend through hooks.

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
3. On a database whose main company is in India (so the Indian chart loads), install **TMS Sales Contracts**, **TMS ODC** and **TMS Profitability**. Odoo installs `tms_core`, `tms_estimate`, `tms` and `auditlog` with them. From the command line:
   ```
   odoo-bin -c odoo.conf -d <db> -i tms_contract,tms_odc,tms_profitability --stop-after-init
   ```
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
| 13 | Settings › Technical › Scheduled actions | Check the 12 `TMS:` crons are active: compliance reminders, DSR overdue, daily customer DSR (09:00 IST), POD past SLA, e-way bill expiry, godown demurrage, blocked hire bills, quotation expiry, ODC ETA, prepaid release, contract alerts, driver anonymisation. |

## 5. Where things are

| Menu | Content |
|---|---|
| CRM | Odoo's own CRM app: leads and opportunities with transport lanes, pipeline (Sales › My Pipeline), pipeline analysis (Reporting › Pipeline) |
| Sales › Orders | Quotations and orders (Odoo's own), cost estimates |
| Sales › Contracts | Contracts, contract items, extensions, excess approvals, reservation ledger, diesel escalation |
| Sales › Reporting › Transport | Sales order P&L, estimate against actual, contract P&L |
| Transport › Operations | **Booking:** consignment notes (LR), contract items (Booking Clerk), e-way bills, e-way bill tasks. **Trips:** all trips, planning board, DSR log, POD verification. **Exceptions:** incidents, breakdowns, damage and shortage claims. **ODC:** permits, equipment. **Fleet:** odometers |
| Transport › Trip money | **Charges:** charges and recoveries, trip expenses. **Driver:** driver advances, driver settlements. **Hire:** hire memos, three-way matches. **Period close:** month-end close, prepaid truck costs |
| Transport › Dashboard | Exception dashboard (KPI tiles that drill down), filtered dashboard (branch, customer, vehicle, period), bills and expenses awaiting variance approval |
| Transport › Reporting | **Registers:** LR register, DSR log, claims register, compliance expiry, override log. **Ageing and payables:** recovery ageing, receivables ageing, hire payables, TDS on hire (declarations). **Fleet:** trips, vehicle utilisation, costs. **Profitability:** trip, leg, customer, truck, fleet, vendor, branch, company, recovery statement, WIP, ledger |

In Operations, Trip money and Reporting the bold names are group headings: they show as captions inside the menu and hide themselves when a user can open none of their items.

The prints of BRD #21 are on the document's **Print** menu: the LR (4 copies), trip sheet (with an ODC section), POD form, DSR, claim note, quotation with cost sheet and contract statement. The hire memo prints from its **Print hire memo** button, and the customer DSR goes out only as the attachment of the daily DSR email. With Marathi or Hindi set as a branch's second print language, the LR and POD form print each label in English and that language (BR-PRN-08).

## 6. Running the tests

```
odoo-bin -c odoo.conf -d tms_test -i tms_contract,tms_odc,tms_profitability \
  --test-enable --test-tags /tms_core,/tms_estimate,/tms_odc,/tms_profitability,/tms_contract \
  --stop-after-init
```

Expected result: `0 failed, 0 error(s) of 94 tests`. The test companies have no mail server, so the log shows "failed sending mail … sender address" lines; they don't indicate a failure.

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
| BR-DSH-04 spreadsheet dashboard (Should) | The exception dashboard is an Odoo kanban/list of `tms.kpi`, with drill-down and filters, rather than `spreadsheet_dashboard` data. |
| BR-NFR-13 driver PII (Should) | Licence number and file are restricted to the Fleet Manager, Dispatchers and managers, and former drivers are anonymised after the retention period (`tms_core.driver_retention_years`, default 8). A driver's phone and address sit on the shared contact record and are not restricted per field. |
| BR-HIR-15 cancelled hire memo | The advance already paid stays as a debit balance on the vendor's payable, and the accountants get an activity to recover it or net it against the next bill. No vendor refund entry is raised, because that would count the advance twice. |
| E-way bills (SD-10) | Phase 1 is manual, with evidence; GSP APIs come in phase 2. |
| Profitability | The reconciliation check blocks the close at company level. Branch differences from cross-branch trips show on the branch view only. Ratio columns average when grouped. |
| Contracts | With a GST-inclusive value basis, pure-agent incidentals are grossed up as well. KPI-21 counts items held live. |
| Updating `tms` | On a database with CRM or Calendar, `-u tms` and `-u all` fail validating the base driver form (`tms.view_tms_driver_form_inherit`), which copies the contact form and its Meetings and Opportunities buttons; the error is "schedule_meeting is not a valid action on tms.driver". This is in the base `tms` 18.0 module itself (no upstream fix at commit a9b47c8). Update the TMS by module name: `-u tms_core,tms_estimate,tms_odc,tms_profitability,tms_contract`, which runs clean. A one-line upstream fix is to clear the button box in that base view. |

## 9. Licences

The five `tms_*` addons are LGPL-3. `tms` and `auditlog` are AGPL-3, and their licence texts are in `third_party/*/LICENSE`. Because `tms_core` depends on them, a distribution of the combined work must meet AGPL-3: offer the source to users of a network deployment.

## 10. Changes in 18.0.1.1.1

| Area | Change | Cases |
|---|---|---|
| Menus | Operations, Trip money and Reporting group their items by activity (#5). Claims move from Trip money to Operations › Exceptions as *Damage and shortage claims*; the Claims register stays in Reporting. No item changes role or access | UT-CFG-23 (new) |
| Trips | The LR field on a trip's Consignments tab explains why the list can be empty: only confirmed LRs with packages not yet planned are offered | — |

Upgrade from 18.0.1.1.0 by module name: `-u tms_core,tms_estimate,tms_odc,tms_profitability,tms_contract`.

## 11. Changes in 18.0.1.1.0

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
