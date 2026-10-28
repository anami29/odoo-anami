# Copyright 2026 RLFB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).


def post_init_hook(env):
    """Give the new stage subtypes to people who already follow.

    A new default subtype only reaches followers added from now on. Internal
    users already following a team, or an existing ticket, would otherwise
    hear nothing about stage changes until they re-follow.
    """
    Followers = env["mail.followers"].sudo()
    for model, xmlid in (
        ("helpdesk.ticket.team", "helpdesk_notify_both.hlp_tck_team_stage"),
        ("helpdesk.ticket", "helpdesk_notify_both.hlp_tck_stage"),
    ):
        subtype = env.ref(xmlid)
        followers = Followers.search(
            [("res_model", "=", model), ("partner_id.partner_share", "=", False)]
        )
        if followers:
            followers.write({"subtype_ids": [(4, subtype.id)]})
