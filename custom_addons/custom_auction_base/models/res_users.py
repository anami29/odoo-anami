# -*- coding: utf-8 -*-
"""Initial auction access grant.

FIX-002 supersedes FIX-001, which did not work.

FIX-001 used ``<record id="base.user_admin">`` to add groups. That fails
silently. ``base.user_admin`` is declared in the ``base`` module inside a
``noupdate="1"`` block, so its ``ir.model.data`` row carries noupdate=True.
On ``-u`` Odoo looks up that row, sees the flag and SKIPS the write. No
error is raised and nothing changes -- which is exactly what it looked like.

A ``<function>`` tag is not a record, so noupdate does not apply to it and it
runs on install and on every update. This method is what it calls.

It also targets every active system administrator rather than the
``base.user_admin`` xmlid specifically, so it works on a database whose
administrator is a different user.
"""
import logging

from odoo import api, models

_logger = logging.getLogger(__name__)

#: Granted automatically so the menus are reachable after install.
#:
#: Envelope Opener and Award Approver are deliberately NOT here. TSD-AUC-001
#: #15.2 and BR-AUD-008 enforce segregation of duties, and auto-granting them
#: to every administrator defeats the control on day one. Assign those to
#: distinct named users.
INITIAL_GROUPS = (
    "custom_auction_base.group_auction_admin",
    "custom_auction_base.group_auction_finance",
)


class ResUsers(models.Model):
    _inherit = "res.users"

    @api.model
    def _auction_grant_initial_access(self):
        """Grant auction access to active system administrators.

        Idempotent: ``(4, id)`` is add-if-absent, so repeated upgrades are
        harmless and a group removed by hand is restored on the next upgrade.
        """
        system = self.env.ref("base.group_system", raise_if_not_found=False)
        if not system:
            _logger.warning(
                "Auction engine: base.group_system not found; assign the "
                "auction groups manually.")
            return

        targets = system.users.filtered("active")
        if not targets:
            _logger.warning(
                "Auction engine: no active system administrator found; "
                "assign the auction groups manually.")
            return

        commands = []
        for xmlid in INITIAL_GROUPS:
            group = self.env.ref(xmlid, raise_if_not_found=False)
            if group:
                commands.append((4, group.id))
            else:
                _logger.error("Auction engine: group %s is missing", xmlid)

        if not commands:
            return

        targets.sudo().write({"groups_id": commands})
        _logger.info(
            "Auction engine: auction access granted to %s",
            ", ".join(targets.mapped("login")))
