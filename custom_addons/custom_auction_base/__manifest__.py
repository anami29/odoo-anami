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
* Offline bid security workflow (NEFT/RTGS/cheque/DD/BG/FDR)
* Immutable audit log
* Event templates with locked, immutable field sets
* Phased delivery schedules, required and offered
* Minimal award: read the opened prices, pick the winner, generate the
  purchase or sale order with three-level linkage back to the bid

NOT in this module: live and clock mechanisms, OWL consoles, websocket
transport, landed-cost scoring and the formal comparative statement,
spreadsheet round-trip. Ranking here is on bid price alone; normalisation
for duty, freight and payment terms is the next stage. See SCP-AUC-001.
""",
    "version": "18.0.1.0.8",
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
        "views/auction_menus.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
    "post_init_hook": "post_init_hook",
}
