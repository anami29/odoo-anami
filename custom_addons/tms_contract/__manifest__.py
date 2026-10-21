# Copyright 2026 Ranadive Consultancy Services
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
{
    "name": "TMS Sales Contracts (Premier Transport)",
    "summary": "Value-capped sales contracts: km rates per vehicle class and accessory, items released against the "
    "balance, reservations for running trips, incidentals at actual, extensions, excess approvals, escalation, "
    "the invoice gate and the contract P&L (BRD #8.4, WF-11, #23.7)",
    "version": "18.0.1.1.0",
    "category": "TMS",
    "license": "LGPL-3",
    "author": "Ranadive Consultancy Services",
    "website": "https://www.ranadive.in",
    "depends": ["tms_estimate"],
    "data": [
        "security/tms_contract_groups.xml",
        "security/ir.model.access.csv",
        "security/tms_contract_rules.xml",
        "data/ir_sequence_data.xml",
        "data/ir_cron_data.xml",
        "wizard/tms_contract_wizard_views.xml",
        "views/tms_contract_views.xml",
        "views/tms_contract_item_views.xml",
        "views/tms_contract_misc_views.xml",
        "views/sale_order_views.xml",
        "views/account_move_views.xml",
        "views/tms_consignment_views.xml",
        "views/tms_trip_charge_views.xml",
        "views/res_config_settings_views.xml",
        "views/tms_contract_pnl_views.xml",
        "views/menus.xml",
        "report/report_contract_statement.xml",
    ],
    "post_init_hook": "post_init_hook",
    "installable": True,
}
