# Copyright 2026 Riamona Luxury and Fashion Brands (RLFB)
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
{
    "name": "Spreadsheet OCA - Collaboration and Excel Filters",
    "summary": "Google-Sheets-style multi-user editing and Excel-style "
    "filtering and sorting for spreadsheet_oca",
    "version": "18.0.1.0.0",
    "category": "Productivity",
    "license": "AGPL-3",
    "author": "RLFB",
    "depends": ["spreadsheet_oca"],
    "data": [
        "security/ir.model.access.csv",
        "security/security.xml",
        "data/ir_config_parameter.xml",
        "data/ir_cron.xml",
        "views/spreadsheet_oca_version_views.xml",
        "views/spreadsheet_spreadsheet_views.xml",
    ],
    "assets": {
        "spreadsheet.o_spreadsheet": [
            "spreadsheet_oca_collab/static/src/js/excel_filter/criteria.esm.js",
            "spreadsheet_oca_collab/static/src/js/excel_filter/filter_state.esm.js",
            "spreadsheet_oca_collab/static/src/js/excel_filter/xl_sort_plugin.esm.js",
            "spreadsheet_oca_collab/static/src/js/excel_filter/subtotal.esm.js",
            "spreadsheet_oca_collab/static/src/js/excel_filter/filter_menu.esm.js",
            "spreadsheet_oca_collab/static/src/js/excel_filter/sort_panel.esm.js",
            "spreadsheet_oca_collab/static/src/js/excel_filter/excel_keys.esm.js",
            "spreadsheet_oca_collab/static/src/js/collab_transport.esm.js",
            "spreadsheet_oca_collab/static/src/js/collab_status.esm.js",
            "spreadsheet_oca_collab/static/src/js/renderer_patch.esm.js",
            "spreadsheet_oca_collab/static/src/js/menu.esm.js",
            "spreadsheet_oca_collab/static/src/xml/collab.xml",
            "spreadsheet_oca_collab/static/src/xml/excel_filter.xml",
        ],
        "web.assets_backend": [
            "spreadsheet_oca_collab/static/src/scss/collab.scss",
        ],
    },
    "post_init_hook": "post_init_hook",
    "installable": True,
}
