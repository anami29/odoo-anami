# Copyright 2026 RLFB (Riamona Luxury and Fashion Brands), https://www.riamona.com
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
{
    "name": "TMS ODC (Premier Transport)",
    "summary": "Over-dimensional cargo: cargo pieces and ODC suggestion, route by state, puller and axle "
    "checks, trip equipment, state permits, route survey, escort and the extended START gate",
    "version": "18.0.1.2.0",
    "category": "TMS",
    "license": "LGPL-3",
    "author": "RLFB (Riamona Luxury and Fashion Brands)",
    "maintainers": ["RLFB"],
    "website": "https://www.riamona.com",
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
