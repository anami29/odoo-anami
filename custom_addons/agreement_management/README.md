# Agreement Management — Odoo 18 Community

Standalone addon (`agreement_management`) implementing the *Business Requirements — Agreement Management Module*:
template-versioned agreements with template-specific dynamic fields, configurable signature locations,
sequential Party A → Party B electronic signing, an automatically generated and stored executed PDF,
email distribution with audit, and Supplementary Annexures linked to executed agreements.

No OCA or Enterprise dependency. Depends on `base`, `mail`, `contacts`, `web`.

Verified on Odoo 18.0 (Community, source of 2026-09) + PostgreSQL 16 + wkhtmltopdf 0.12.6:
module installs with demo data, 9/9 unit tests pass (workflow, constraints, tampering attempts), web client screens and the signing flow exercised end-to-end.

---

## 1. Installation

1. Copy `agreement_management/` into an addons path and restart Odoo (`-u base` not required).
2. *Apps → Update Apps List → Agreement Management → Activate* (or `-i agreement_management`).
3. wkhtmltopdf must be available to the Odoo server (it renders the preview and the executed PDF).
4. Optional demo data (`--with-demo`): two activated templates (NDA, Master Service Agreement), demo partners,
   two draft agreements and one confirmed agreement awaiting Party A.

Run the tests with `odoo-bin -d <db> -i agreement_management --test-enable --test-tags=/agreement_management --stop-after-init`.

## 2. Security groups

| Group | Access |
|---|---|
| **Agreements / User** | Create agreements and annexures, complete information, preview, confirm, sign where designated (or facilitate tablet signing), download / email executed PDFs. Read-only on templates, versions, types. Record rule: sees agreements they are responsible for, created, follow or sign (`security/agreement_security.xml`, adjustable). |
| **Agreements / Manager** | Everything: templates, versions, activation, dynamic fields, signature locations, settings, delete. Sees all agreements. |
| Administrator (`base.group_system`) | *Configuration → Settings*. |

Multi-company record rules are applied to agreements, annexures, templates, versions, signatures and the distribution log.

## 3. Configuration (Agreements → Configuration → Settings)

| Setting | Effect |
|---|---|
| Automatically Email Executed Documents | Send the executed PDF right after execution (BR-EMAIL-007). |
| Default recipients | Party A, Party B, Signatory A, Signatory B, additional partners (BR-EMAIL-002/008). |
| Email templates | Executed agreement / annexure templates (module templates are used when empty). |
| Allow Party A and Party B to be identical | Default off (BR-PAR-005). |
| Tablet signing captured by internal users | Any Agreement User may open the signing screen so the designated signatory signs in person; when off, only the signatory's own user or a Manager can. |

Numbering: sequence `agreement.agreement` → `AGR/%(year)s/00001` (edit under *Settings → Technical → Sequences*).
Annexures: `<parent number>-ANN-01`, `-ANN-02`, … per parent agreement.

## 4. Setting up a template (Manager)

1. *Templates → Agreement Templates → New*: name, unique code, type. A first Draft version `1.0` is created.
2. Open the version:
   * **Content** — document-style HTML. Placeholders: `{{agreement_number}}`, `{{party_a}}`, `{{party_a_address}}`,
     `{{party_b}}`, `{{party_b_address}}`, `{{signatory_a}}`, `{{signatory_a_title}}`, `{{signatory_b}}`, `{{signatory_b_title}}`,
     `{{effective_from}}`, `{{effective_to}}`, `{{execution_date}}`, `{{template_code}}`, `{{template_version}}`,
     `{{agreement_type}}`, `{{company}}`, `{{today}}`, the technical name of any dynamic field (`{{contract_value}}`),
     `{{page_break}}`, `{{insert_blank_page}}` for a deliberately blank page, and `{{sig:CODE}}` for a signature location. Locations not placed in the content are appended in
     an *Execution* block.
   * **Dynamic Fields** — label, technical name, type (Text, Long Text, Number, Currency, Date, Boolean, Selection,
     Contact, Tags), Mandatory, Editable, Searchable, default. Stored as Odoo *Properties* on the agreement
     (native widgets, searchable / groupable).
   * **Signature Locations** — code, section/anchor, page, position, party (A/B), kind (signature / initials), Required.
   * **Annexure Setup** — default annexure content, annexure-specific fields and signature locations.
3. **Activate Version**: the previous Active version becomes Superseded automatically; no approval workflow;
   agreements already confirmed keep their version. Activation validates that every required location has a valid
   party assignment and that all `{{sig:…}}` references exist.
4. Later changes: **New Version** on the template (copies the active version as a Draft `n.0`).

## 5. Agreement workflow

| Step | What happens |
|---|---|
| Create | Select a template (only templates with an Active version). The Active version is applied automatically and shown read-only. Select Party A / Signatory A / Party B / Signatory B (signatories must be contacts of their party), effective dates, and complete *Other Information*. |
| Preview | Document-style PDF with a *PREVIEW – NOT EXECUTED* watermark. |
| Confirm | Validation messages per §33 (missing signatory, mandatory information, dates, parties). Number assigned, template version and content snapshot locked, signature records created from the version's locations, status *Pending Party A Signature*, Signatory A notified (email + activity). |
| Sign as Party A | Signing screen (wizard): draw / type the adopted signature and apply it to all areas, or open each area to sign it individually (touch, stylus, mouse). *Complete Signing* is available only when every required Party A area is signed. Status → *Pending Party B Signature*; Party B areas unlock; Signatory B notified. |
| Sign as Party B | Same screen; Party B areas were locked until now. Completion → *Fully Signed / Executed*, execution timestamp, execution record. |
| Execution | The executed PDF (content with embedded signatures + electronic execution record + signature-area table + SHA-256 hash) is generated once, stored as an attachment on the record and never regenerated. *Print / Download Signed Agreement* serves that file. |
| Email | Automatic distribution to the default recipients (if enabled) and/or *Email Executed Agreement* → Odoo composer with the stored PDF attached and default recipients pre-filled. Every send is logged (date/time, sender, recipients, subject, attachment, delivery status) under *Distribution* and *Reporting → Distribution Log*. |
| Locked | An executed agreement cannot be edited (only responsible user, chatter, activities and annexures). |
| Supplementary Annexure | *Create Supplementary Annexure* on an executed agreement: parties, signatories, template and version inherited and read-only; own type, effective date, content, annexure fields; same signing, PDF and distribution behaviour; own number `AGR/2026/00125-ANN-01`. The original agreement is never modified. |

Cancel is possible while no signature has been captured; cancelled documents can be reset to draft.

## 6. Menus

*Agreements* (Agreements, Supplementary Annexures) · *Templates* (Agreement Templates, Template Versions) ·
*Signing* (Pending My Signature, In Signing, Signature Areas) · *Reporting* (Execution Records, Distribution Log) ·
*Configuration* (Settings, Agreement Types, Annexure Types, Email Templates).

List views follow §27; the search view offers the §24 filters (status, pending my signature, in signing, my agreements,
effective this year, expiring in 90 days, expired, has annexures, executed this year) and the §26 group-bys
(template, version, status, Party A, Party B, effective year, type, responsible); dynamic fields are available as
optional list columns, custom filters and custom groups.

## 7. Models

| Model | Purpose |
|---|---|
| `agreement.template` | Template (code unique per company, type); `active_version_id` computed. |
| `agreement.template.version` | Draft → Active → Superseded; content, annexure content, field lines, signature locations, properties definitions (computed from the lines, kept in sync when edited from an agreement). Locked when not Draft. |
| `agreement.template.field` | Dynamic-field definition (BR-FLD). |
| `agreement.signature.area` | Signature location definition (BR-SIG). |
| `agreement.document.mixin` | Shared workflow: confirmation/numbering, sequential signing, execution, PDF, distribution, notifications, rendering, executed-record lock. |
| `agreement.agreement` | Agreement (parties, signatories, dates, `agreement_properties`, annexures). |
| `agreement.annexure` | Supplementary Annexure (parent executed agreement, own content / fields / date / numbering). |
| `agreement.signature` | One signature area instance on a document: image, signer, timestamp, IP, status. |
| `agreement.distribution` | Email audit line (mode, recipients, subject, attachment, mail status). |
| `agreement.sign.wizard` (+ `.line`) | Signing screen. |
| `agreement.type`, `agreement.annexure.type` | Classification. |
| `res.company` / `res.config.settings` | Settings above. |
| `mail.compose.message` | Keeps the stored executed PDF attached when the composer is opened from a document. |

Reports: `action_report_agreement`, `action_report_agreement_annexure` (preview / executed), `action_report_agreement_version` (layout preview).
Mail templates: executed agreement / annexure, ready-for-signature notifications (A/B) for both document types.

## 8. BRD traceability

| BRD section | Requirements | Implementation |
|---|---|---|
| §5–6 Templates | BR-TMP-001…004 | `agreement.template`, unique code, type, versions, Manager group |
| §7 Versioning | BR-VER-001…007 | version states, single Active constraint, `action_activate` supersedes, locked versions, snapshot on confirmation |
| §8 Content | BR-CNT-001…003 | HTML content + placeholder renderer (`render_document_html`), page breaks |
| §9 Dynamic fields | BR-FLD-001…006 | field lines → Odoo Properties (`agreement_properties`), mandatory check at confirmation, *Other Information* tab, searchable/groupable |
| §10–11 Agreement creation | BR-AGR-001…003, BR-PAR-001…005 | template domain (active version), auto version, parties/signatories with domains and constraints, same-party setting |
| §12 Dates | BR-DATE-001…003 | `date_from`/`date_to`, validation |
| §13 Preview | BR-PRV-001…003 | *Preview* PDF report with watermark; *Document* tab |
| §14 Confirmation | BR-CON-001…004 | `action_confirm`: number, lock, signature records, notifications |
| §15 Signature locations | BR-SIG-001…005 | `agreement.signature.area`, `{{sig:CODE}}` placement, activation validation |
| §16–18 Signing | BR-SIGN-A-001…008, BR-SIGN-B-001…005, BR-EXEC-001…004 | signing wizard, per-party areas, required gating, Party B locked until A completes, execution timestamp/record |
| §19 Executed PDF | BR-PDF-001…006 | `_generate_executed_pdf` (stored attachment, hash), print/download action |
| §20 Email | BR-EMAIL-001…009 | composer with stored PDF, default recipients, auto-send, `agreement.distribution` audit |
| §21 Integrity | BR-INT-001…003 | executed-record write/unlink guards, signature immutability |
| §22 Annexures | BR-ANN-001…010 | `agreement.annexure` |
| §23–28 History / search / lists / form | — | chatter tracking, History tab, search/list/kanban/form views |
| §29–30 Notifications / audit | — | mail templates + activities, chatter messages for every event |
| §31 Security | — | groups, access rights, record rules |
| §33 Messages | — | exact validation texts in `_check_before_confirm` / signing methods |

## 9. Integrity and tamper resistance

All rules are enforced in the ORM (`write`, `create`, `unlink`), never only in the views, so they hold for any
request — browser dev tools, edited RPC calls, imports or scripts:

* **Workflow-only fields** (`state`, `number`, timestamps, `locked_content`, `locked_values`, `document_fingerprint`,
  hashes, `executed_pdf_attachment_id`) can only be written by the workflow itself. The marker used for that is
  generated server-side per process and cannot be supplied by a client; plain context flags are not accepted.
* **Confirmation freezes the business fields** (template, version, parties, signatories, dates, dynamic fields —
  annexure: parent, type, date, content, fields, summary) and takes a snapshot of every placeholder value
  (`locked_values`). The document renders from that snapshot, so a later change to a partner, a date or a property
  cannot change what is being signed.
* **Fingerprint**: the confirmed document (content + values + signature configuration, without signatures) is hashed
  on confirmation and re-verified before every signing step and before the executed PDF is generated; a mismatch
  blocks the workflow with an explicit error. **Verify integrity now** on the Execution tab re-runs the check at any time.
* **Signature areas** are created and written by the workflow only (users have read access); a signed area is
  immutable; `required`, `party` and `code` cannot be edited after confirmation.
* **Executed PDF**: the attachment cannot be modified or deleted (also protected against users with write access on
  the agreement); its SHA-256 (`executed_pdf_sha256`) is recorded and checked by the integrity verification.
* **Distribution logs** are read-only for all groups (written by the workflow).

What this does not cover: a database administrator or someone with shell access to the server can alter anything —
the fingerprint and hashes then make the alteration detectable, but not preventable. Cryptographic proof that a given
person signed requires PKI (DSC / Aadhaar eSign), which is outside this scope. Deploy behind HTTPS, restrict database
and filestore access, and keep backups.

## 10. Assumptions and limitations

* Signing happens inside Odoo (backend signing screen on a touch/stylus device); external portals and PKI-based
  signatures are out of scope (§40 Future Enhancements). A signatory who has an Odoo user (internal or portal user
  granted the Agreements / User group) signs with their own login; otherwise an internal user opens the screen for an
  in-person tablet session (configurable).
* Long Text and Currency dynamic fields are stored as Odoo property types `char` and `float` (currency symbol shown in
  the label and applied in the rendered document).
* The record rule restricting Agreement Users to agreements they are involved in is a default; adjust
  `rule_agreement_user` / `rule_annexure_user` if all users should see all agreements.
* Editing a version's properties definition directly from an agreement form is only allowed while the version is Draft
  (Odoo's *Add property* on the child record) — otherwise create a new version.
