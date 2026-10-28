# Copyright 2026 RLFB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from datetime import datetime, timedelta

from odoo.tests.common import TransactionCase, new_test_user


class MisCase(TransactionCase):
    """Fixtures shared by the MIS tests."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.company.write({
            "hd_mis_handling_basis": "elapsed",
            "hd_mis_team_attribution": "current",
            "hd_mis_person_attribution": "current",
            "hd_mis_reopen_counting": "distinct",
            "hd_mis_working_hours": False,
            "hd_mis_threshold_warn_hours": 24.0,
            "hd_mis_threshold_high_hours": 48.0,
            "hd_mis_threshold_critical_hours": 168.0,
        })

        cls.stage_new = cls.env["helpdesk.ticket.stage"].create({
            "name": "MIS New", "sequence": 1, "closed": False,
        })
        cls.stage_done = cls.env["helpdesk.ticket.stage"].create({
            "name": "MIS Done", "sequence": 90, "closed": True,
        })

        cls.team_a = cls.env["helpdesk.ticket.team"].create({"name": "MIS Team A"})
        cls.team_b = cls.env["helpdesk.ticket.team"].create({"name": "MIS Team B"})
        cls.category = cls.env["helpdesk.ticket.category"].create(
            {"name": "MIS Category"}
        )

        groups = (
            "base.group_user,"
            "helpdesk_mgmt.group_helpdesk_manager,"
            "helpdesk_mis.group_helpdesk_mis_manager,"
            "helpdesk_mis.group_helpdesk_mis_person_data"
        )
        cls.manager = new_test_user(cls.env, "mis_manager", groups=groups)
        cls.agent_a = new_test_user(
            cls.env, "mis_agent_a",
            groups="base.group_user,helpdesk_mgmt.group_helpdesk_user",
        )
        cls.agent_b = new_test_user(
            cls.env, "mis_agent_b",
            groups="base.group_user,helpdesk_mgmt.group_helpdesk_user",
        )
        # Somebody with helpdesk access but no MIS right at all.
        cls.outsider = new_test_user(
            cls.env, "mis_outsider",
            groups="base.group_user,helpdesk_mgmt.group_helpdesk_user",
        )

    # ------------------------------------------------------------------
    @classmethod
    def _make_ticket(cls, name="MIS ticket", team=None, user=None, category=None):
        return cls.env["helpdesk.ticket"].create({
            "name": name,
            "description": "<p>%s</p>" % name,
            "stage_id": cls.stage_new.id,
            "team_id": (team or cls.team_a).id,
            "user_id": user.id if user else False,
            "category_id": (category or cls.category).id,
        })

    def _backdate(self, ticket, hours):
        """Move a ticket's creation into the past, deterministically.

        create_date is a magic column the ORM will not write, so the test
        reaches past it. Ageing is measured against NOW() inside the SQL view,
        which makes this the only way to pin an age down.
        """
        moment = datetime.utcnow() - timedelta(hours=hours)
        self.env.cr.execute(
            "UPDATE helpdesk_ticket SET create_date = %s WHERE id = %s",
            (moment, ticket.id),
        )
        ticket.invalidate_recordset(["create_date"])
        ticket.modified(["create_date"])
        return moment

    def _set_interval(self, history, start_hours_ago, end_hours_ago=None):
        """Pin an assignment interval to exact timestamps."""
        start = datetime.utcnow() - timedelta(hours=start_hours_ago)
        end = (
            datetime.utcnow() - timedelta(hours=end_hours_ago)
            if end_hours_ago is not None
            else None
        )
        history.sudo().write({"assigned_date": start, "unassigned_date": end})
        return start, end

    def _report(self, ticket):
        """The MIS report row for one ticket."""
        return self.env["helpdesk.mis.report"].search([
            ("ticket_id", "=", ticket.id)
        ])
