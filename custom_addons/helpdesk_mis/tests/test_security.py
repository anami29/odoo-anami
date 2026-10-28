# Copyright 2026 RLFB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""BRD section 4 and FR-14: reporting must not leak restricted tickets."""

from odoo.exceptions import AccessError
from odoo.tests.common import tagged

from .common import MisCase


@tagged("post_install", "-at_install")
class TestMisSecurity(MisCase):
    def test_dashboard_refuses_a_user_without_the_mis_right(self):
        with self.assertRaises(AccessError):
            self.env["helpdesk.mis.dashboard"].with_user(
                self.outsider
            ).get_dashboard_data("performance", {})

    def test_report_model_is_not_readable_without_the_mis_right(self):
        self._make_ticket("hidden from outsiders")
        with self.assertRaises(AccessError):
            self.env["helpdesk.mis.report"].with_user(
                self.outsider
            ).search([])

    def test_manager_reads_the_report(self):
        ticket = self._make_ticket("visible to manager")
        rows = self.env["helpdesk.mis.report"].with_user(self.manager).search([
            ("ticket_id", "=", ticket.id)
        ])
        self.assertTrue(rows)

    def test_personal_right_limits_the_rows(self):
        """An agent limited to personal tickets sees only their own."""
        mine = self._make_ticket("mine", user=self.agent_a)
        theirs = self._make_ticket("theirs", user=self.agent_b)
        agent = self.env["res.users"].create({
            "name": "MIS own only",
            "login": "mis_own_only",
            "groups_id": [
                (4, self.env.ref("base.group_user").id),
                (4, self.env.ref("helpdesk_mgmt.group_helpdesk_user_own").id),
                (4, self.env.ref("helpdesk_mis.group_helpdesk_mis_user").id),
            ],
        })
        mine.write({"user_id": agent.id})
        visible = self.env["helpdesk.mis.report"].with_user(agent).search([])
        ticket_ids = visible.mapped("ticket_id").ids
        self.assertIn(mine.id, ticket_ids)
        self.assertNotIn(theirs.id, ticket_ids)

    def test_mis_reader_gets_a_helpdesk_floor(self):
        """The MIS right implies at least the personal helpdesk right.

        Without a floor the record rules would not match at all and the
        reader would see every row - the opposite of BRD section 4.
        """
        group = self.env.ref("helpdesk_mis.group_helpdesk_mis_user")
        own = self.env.ref("helpdesk_mgmt.group_helpdesk_user_own")
        self.assertIn(own, group.trans_implied_ids)

    def test_manager_right_implies_all_tickets(self):
        group = self.env.ref("helpdesk_mis.group_helpdesk_mis_manager")
        self.assertIn(
            self.env.ref("helpdesk_mgmt.group_helpdesk_user"),
            group.trans_implied_ids,
        )
