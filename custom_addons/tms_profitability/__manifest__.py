# Copyright 2026 Ranadive Consultancy Services
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
{
    "name": "TMS Profitability (Premier Transport)",
    "summary": "Cost classes, standard rates per km, standard charge as analytic entries, truck pools and "
    "recovery variance, prepaid release, month-end close with accruals, P&L by trip, leg, customer, truck, "
    "fleet, vendor, branch and company",
    "version": "18.0.1.1.0",
    "category": "TMS",
    "license": "LGPL-3",
    "author": "Ranadive Consultancy Services",
    "website": "https://www.ranadive.in",
    "depends": ["tms_estimate"],
    "data": [
        "security/ir.model.access.csv",
        "security/tms_profitability_rules.xml",
        "data/account_analytic_plan_data.xml",
        "data/tms_truck_category_data.xml",
        "data/ir_cron_data.xml",
        "views/tms_std_rate_views.xml",
        "views/tms_truck_category_views.xml",
        "views/account_move_views.xml",
        "views/tms_trip_expense_views.xml",
        "views/fleet_vehicle_views.xml",
        "views/tms_order_views.xml",
        "views/tms_prepaid_schedule_views.xml",
        "views/tms_pnl_close_views.xml",
        "views/tms_pnl_views.xml",
        "views/res_config_settings_views.xml",
        "views/menus.xml",
    ],
    "post_init_hook": "post_init_hook",
    "installable": True,
}
