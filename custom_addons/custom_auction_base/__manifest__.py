# -*- coding: utf-8 -*-
{
    "name": "Auction and Sealed Bid Platform — Engine",
    "summary": "Sealed-bid e-auction engine for procurement and disposal (SME Edition v1.0)",
    "description": """
Engine core for the Electronic Auction and Sealed Bid Platform.

Implements SCP-AUC-001 (SME Edition v1.0) against BRD-AUC-001 and
TSD-AUC-001 v1.1 as amended by AMD-AUC-001 and AMD-AUC-002.

Scope of this module
--------------------
* Append-only bid ledger with SHA-256 hash chain and verification
* AES-256-GCM sealed envelope with associated-data binding
* Dual-control opening ceremony
* Bid acceptance critical path: row lock, clock_timestamp(), idempotency
* Rules engine and mechanism strategy registry
* Three sealed mechanisms: sealed_rev, sealed_rev_nr, sealed_fwd
* Offline bid security workflow (NEFT/RTGS/cheque/DD/BG/FDR), including
  statutory exemptions for MSE and DPIIT-recognised bidders
* Immutable audit log
* Event templates with locked, immutable field sets
* Phased delivery schedules, required and offered
* Minimal award: opened prices, manual winner selection, purchase or sale
  order generation with three-level linkage back to the bid
* Bidder portal: invitations, tender documents with upload, clarifications
  published to all bidders without naming the asker, corrigenda, bidder
  self-declared bid security, the sealed bid form with phased delivery
  dates, submission receipts and withdrawal

NOT in this module: live and clock mechanisms, OWL consoles, websocket
transport, landed-cost scoring and the formal comparative statement,
spreadsheet round-trip. Ranking here is on bid price alone; normalisation
for duty, freight and payment terms is the next stage. See SCP-AUC-001.
""",
    "version": "18.0.1.1.0",
    "category": "Purchases",
    "author": "Riamona Luxury and Fashion Brands",
    "website": "https://www.riamona.com",
    "license": "OPL-1",
    "depends": [
        "base",
        "purchase",
        "sale_management",
        "stock",
        "mail",
        "portal",
        "product",
        "uom",
        "account",
    ],
    "external_dependencies": {
        "python": ["cryptography"],
    },
    "data": [
        "security/auction_groups.xml",
        "security/ir.model.access.csv",
        "security/auction_rules.xml",
        "data/ir_sequence.xml",
        "data/auction_data.xml",
        "views/auction_config_views.xml",
        "views/auction_evaluation_views.xml",
        "views/auction_award_views.xml",
        "views/auction_template_views.xml",
        "views/auction_event_views.xml",
        "views/auction_bid_views.xml",
        "views/auction_bid_security_views.xml",
        "views/auction_participant_views.xml",
        "views/auction_portal_backend_views.xml",
        "views/auction_menus.xml",
        "views/portal_templates.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
    "post_init_hook": "post_init_hook",
}
