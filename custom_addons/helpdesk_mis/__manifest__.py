# Copyright 2026 Riamona Luxury and Fashion Brands (RLFB)
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
{
    "name": "Helpdesk MIS - Dashboards and Reporting",
    "summary": "Ticket Performance MIS and Open Tickets MIS dashboards, "
    "per-person time-held reporting, closure-event history and "
    "configurable KPI definitions",
    "version": "18.0.1.0.0",
    "category": "Services/Helpdesk",
    "license": "AGPL-3",
    "author": "RLFB",
    "website": "https://github.com/rlfb",
    "depends": ["helpdesk_mgmt", "resource"],
    "data": [
        "security/helpdesk_mis_security.xml",
        "security/ir.model.access.csv",
        "data/helpdesk_mis_cron.xml",
        "views/helpdesk_mis_report_views.xml",
        "views/helpdesk_mis_assignment_report_views.xml",
        "views/helpdesk_mis_closure_event_views.xml",
        "views/helpdesk_ticket_views.xml",
        "views/res_config_settings_views.xml",
        "views/helpdesk_mis_dashboard_views.xml",
        "views/helpdesk_mis_menus.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "helpdesk_mis/static/src/scss/mis_dashboard.scss",
            "helpdesk_mis/static/src/js/mis_dashboard.esm.js",
            "helpdesk_mis/static/src/xml/mis_dashboard.xml",
        ],
    },
    "post_init_hook": "post_init_hook",
    "installable": True,
    "application": False,
}
