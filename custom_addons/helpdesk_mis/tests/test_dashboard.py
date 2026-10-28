# Copyright 2026 RLFB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""BRD FR-01, FR-02, FR-03, FR-05, FR-12: the two dashboards."""

from datetime import date, timedelta

from odoo.tests.common import tagged

from .common import MisCase


@tagged("post_install", "-at_install")
class TestDashboard(MisCase):
    @property
    def dashboard(self):
        return self.env["helpdesk.mis.dashboard"].with_user(self.manager)

    def _filters(self, **overrides):
        today = date.today()
        values = {
            "date_from": str(today - timedelta(days=30)),
            "date_to": str(today + timedelta(days=1)),
            "date_basis": "create",
            "team_ids": [],
            "category_ids": [],
            "user_ids": [],
            "priorities": [],
            "state": "all",
        }
        values.update(overrides)
        return values

    def test_performance_dashboard_shape(self):
        self._make_ticket("shape")
        data = self.dashboard.get_dashboard_data("performance", self._filters())
        self.assertEqual(data["dashboard"], "performance")
        self.assertEqual(len(data["kpis"]), 6, "BRD 7.1 lists six KPI cards")
        keys = [kpi["key"] for kpi in data["kpis"]]
        self.assertEqual(
            keys,
            ["opened", "closed", "open", "handling", "max_duration",
             "reassigned"],
        )
        for table in ("a1_team", "a2_category", "a3_person", "a4_outliers",
                      "a5_time_held"):
            self.assertIn(table, data["tables"])
        for chart in ("flow", "by_team", "by_category", "by_duration"):
            self.assertIn(chart, data["charts"])

    def test_open_dashboard_shape(self):
        self._make_ticket("open shape")
        data = self.dashboard.get_dashboard_data("open", self._filters())
        self.assertEqual(len(data["kpis"]), 6, "BRD 8.2 lists six KPI cards")
        for table in ("b1_team", "b2_category", "b3_person", "b4_detail"):
            self.assertIn(table, data["tables"])
        self.assertIn("ageing", data["charts"])

    def test_every_kpi_declares_its_date_basis(self):
        """BRD 7.1 insists the screen says what a number refers to."""
        data = self.dashboard.get_dashboard_data("performance", self._filters())
        for kpi in data["kpis"]:
            self.assertTrue(kpi["basis"], "%s has no basis label" % kpi["key"])
            self.assertIsInstance(kpi["domain"], list)

    def test_opened_and_closed_are_different_sets(self):
        """BRD section 6: the two must never be conflated."""
        old = self._make_ticket("old, closed now")
        self._backdate(old, 24 * 90)
        old.write({"stage_id": self.stage_done.id})
        self._make_ticket("new, still open")

        data = self.dashboard.get_dashboard_data("performance", self._filters())
        kpis = {kpi["key"]: kpi["value"] for kpi in data["kpis"]}
        # The old ticket was created outside the window but closed inside it.
        self.assertEqual(kpis["opened"], 1)
        self.assertEqual(kpis["closed"], 1)
        self.assertGreaterEqual(kpis["open"], 1)

    def test_filters_narrow_the_result(self):
        self._make_ticket("team a ticket", team=self.team_a)
        self._make_ticket("team b ticket", team=self.team_b)
        everything = self.dashboard.get_dashboard_data(
            "performance", self._filters()
        )
        just_a = self.dashboard.get_dashboard_data(
            "performance", self._filters(team_ids=[self.team_a.id])
        )
        total = {k["key"]: k["value"] for k in everything["kpis"]}["opened"]
        subset = {k["key"]: k["value"] for k in just_a["kpis"]}["opened"]
        self.assertGreater(total, subset)
        self.assertEqual(subset, 1)

    def test_threshold_kpi_follows_settings(self):
        ticket = self._make_ticket("ageing kpi")
        self._backdate(ticket, 30)
        self.company.hd_mis_threshold_warn_hours = 24.0
        data = self.dashboard.get_dashboard_data("open", self._filters())
        past = {k["key"]: k["value"] for k in data["kpis"]}["past_threshold"]
        self.assertGreaterEqual(past, 1)

        self.company.hd_mis_threshold_warn_hours = 1000.0
        data = self.dashboard.get_dashboard_data("open", self._filters())
        past = {k["key"]: k["value"] for k in data["kpis"]}["past_threshold"]
        self.assertEqual(past, 0)

    def test_settings_banner_reports_the_live_rules(self):
        self.company.hd_mis_handling_basis = "assignment"
        data = self.dashboard.get_dashboard_data("performance", self._filters())
        self.assertIn("Assignment duration", data["settings"]["handling_basis"])

    def test_drill_down_returns_the_tickets_behind_a_kpi(self):
        """FR-12."""
        ticket = self._make_ticket("drill")
        data = self.dashboard.get_dashboard_data("performance", self._filters())
        opened = next(k for k in data["kpis"] if k["key"] == "opened")
        action = self.dashboard.action_drill_down(opened["domain"], "Opened")
        self.assertEqual(action["res_model"], "helpdesk.ticket")
        domain_ids = action["domain"][0][2]
        self.assertIn(ticket.id, domain_ids)

    def test_drill_down_into_the_assignment_report(self):
        action = self.dashboard.action_drill_down(
            [], "Held", "helpdesk.mis.assignment.report"
        )
        self.assertEqual(
            action["res_model"], "helpdesk.mis.assignment.report"
        )

    def test_person_tables_hidden_without_the_right(self):
        viewer = self.env["res.users"].create({
            "name": "MIS viewer no people",
            "login": "mis_viewer_nopeople",
            "groups_id": [
                (4, self.env.ref("base.group_user").id),
                (4, self.env.ref("helpdesk_mgmt.group_helpdesk_user").id),
                (4, self.env.ref("helpdesk_mis.group_helpdesk_mis_user").id),
            ],
        })
        data = self.env["helpdesk.mis.dashboard"].with_user(
            viewer
        ).get_dashboard_data("performance", self._filters())
        self.assertFalse(data["may_see_people"])
        self.assertNotIn("a3_person", data["tables"])
        self.assertNotIn("a5_time_held", data["tables"])
        self.assertNotIn("by_person", data["charts"])
