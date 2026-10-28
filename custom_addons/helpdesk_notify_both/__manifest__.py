# Copyright 2026 RLFB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
{
    "name": "Helpdesk Notifications: Odoo and Email",
    "summary": "Ticket notifications reach internal users both in Odoo (inbox and "
    "desktop pop-up) and by email, and followers are told when a ticket changes stage",
    "description": """
Helpdesk Notifications: Odoo and Email
======================================

Two additions to helpdesk_mgmt:

1. Stage changes notify followers. A new "Stage Changed" subtype on tickets,
   and "Ticket Stage Changed" on teams, so following a team is enough to hear
   when any of its tickets moves to In Progress, Done, Cancelled and so on.
   Internal only: customers keep getting the stage e-mail templates, not this.

2. Internal users set to "Handle in Odoo" also receive the e-mail copy of
   ticket notifications. Odoo itself sends a notification either to the
   inbox (with the desktop pop-up) or by e-mail, never both; on helpdesk
   tickets this module adds the e-mail for inbox users.

See HELPDESK_NOTIFICATIONS.md at the root of the repository.
""",
    "version": "18.0.1.2.0",
    "category": "After-Sales",
    "license": "AGPL-3",
    "author": "RLFB",
    "depends": ["helpdesk_mgmt"],
    "data": ["data/mail_message_subtype_data.xml"],
    "assets": {
        "web.assets_backend": [
            "helpdesk_notify_both/static/src/js/out_of_focus_patch.esm.js",
        ],
    },
    "post_init_hook": "post_init_hook",
    "installable": True,
}
