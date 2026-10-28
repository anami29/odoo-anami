# Copyright 2026 RLFB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from odoo import fields, models


class ResUsers(models.Model):
    _inherit = "res.users"

    # Same values as mail's field; only the wording changes. "inbox" now also
    # sends the e-mail copy on helpdesk tickets (see helpdesk_ticket.py), so
    # the label says so rather than leaving people to guess which to pick.
    notification_type = fields.Selection(
        selection=[
            ("email", "Handle by Emails"),
            ("inbox", "Handle in Odoo and by Email (Helpdesk)"),
        ],
        help="Policy on how to handle Chatter notifications:\n"
        "- Handle by Emails: notifications are sent to your email address\n"
        "- Handle in Odoo and by Email (Helpdesk): notifications appear in "
        "your Odoo Inbox and as a desktop pop-up; for helpdesk tickets an "
        "email copy is sent as well",
    )
