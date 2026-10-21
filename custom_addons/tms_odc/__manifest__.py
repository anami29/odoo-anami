# Copyright 2026 Ranadive Consultancy Services
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
{
    "name": "TMS ODC (Premier Transport)",
    "summary": "Over-dimensional cargo: cargo pieces and ODC suggestion, route by state, puller and axle "
    "checks, trip equipment, state permits, route survey, escort and the extended START gate",
    "version": "18.0.1.1.0",
    "category": "TMS",
    "license": "LGPL-3",
    "author": "Ranadive Consultancy Services",
    "website": "https://www.ranadive.in",
    "depends": ["tms_estimate"],
    "data": [
        "security/ir.model.access.csv",
        "security/tms_odc_rules.xml",
        "data/ir_cron_data.xml",
        "views/res_config_settings_views.xml",
        "views/tms_estimate_views.xml",
        "views/sale_order_views.xml",
        "views/tms_consignment_views.xml",
        "views/tms_order_views.xml",
        "views/tms_odc_permit_views.xml",
        "views/menus.xml",
        "report/report_trip_sheet.xml",
    ],
    "post_init_hook": "post_init_hook",
    "installable": True,
}
