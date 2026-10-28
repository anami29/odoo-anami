# Copyright 2026 RLFB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""BRD sections 6, 7, 8, 9 and 18: the ticket-grain report."""

from odoo.tests.common import tagged

from .common import MisCase


@tagged("post_install", "-at_install")
class TestMisReport(MisCase):
    def test_open_and_closed_are_mutually_exclusive(self):
        open_ticket = self._make_ticket("open one")
        closed_ticket = self._make_ticket("closed one")
        closed_ticket.write({"stage_id": self.stage_done.id})

        self.assertTrue(self._report(open_ticket).is_open)
        self.assertFalse(self._report(open_ticket).is_closed)
        self.assertTrue(self._report(closed_ticket).is_closed)
        self.assertFalse(self._report(closed_ticket).is_open)

    def test_open_age_is_measured_and_closed_tickets_have_none(self):
        """BRD 8.7: age applies to open tickets, not finished ones."""
        ticket = self._make_ticket("ageing")
        self._backdate(ticket, 30)
        row = self._report(ticket)
        self.assertAlmostEqual(row.open_age_hours, 30.0, places=0)

        ticket.write({"stage_id": self.stage_done.id})
        self.assertFalse(self._report(ticket).open_age_hours)

    def test_age_bands_follow_the_configured_thresholds(self):
        """FR-17: the thresholds are a setting, not a constant."""
        ticket = self._make_ticket("bands")
        self._backdate(ticket, 30)
        self.assertEqual(self._report(ticket).age_band, "warn")
        self.assertTrue(self._report(ticket).past_threshold)

        self.company.hd_mis_threshold_warn_hours = 100.0
        self.company.hd_mis_threshold_high_hours = 200.0
        self.company.hd_mis_threshold_critical_hours = 300.0
        self.assertEqual(self._report(ticket).age_band, "ok")
        self.assertFalse(self._report(ticket).past_threshold)

        self.company.hd_mis_threshold_warn_hours = 1.0
        self.company.hd_mis_threshold_high_hours = 2.0
        self.company.hd_mis_threshold_critical_hours = 3.0
        self.assertEqual(self._report(ticket).age_band, "critical")

    def test_hours_beyond_threshold(self):
        ticket = self._make_ticket("beyond")
        self._backdate(ticket, 30)
        self.company.hd_mis_threshold_warn_hours = 24.0
        self.assertAlmostEqual(
            self._report(ticket).hours_beyond_threshold, 6.0, places=0
        )

    def test_unassigned_is_separate_but_still_counted(self):
        """FR-15: unassigned tickets are visible, not quietly dropped."""
        ticket = self._make_ticket("nobody", user=None)
        row = self._report(ticket)
        self.assertTrue(row.is_unassigned)
        self.assertEqual(row.unassigned_count, 1)
        self.assertEqual(row.open_count, 1, "still part of the open total")

    def test_resolution_and_total_elapsed(self):
        ticket = self._make_ticket("durations")
        self._backdate(ticket, 10)
        row = self._report(ticket)
        self.assertAlmostEqual(row.total_elapsed_hours, 10.0, places=0)
        self.assertFalse(row.resolution_hours, "not closed, so no resolution")

        ticket.write({"stage_id": self.stage_done.id})
        row = self._report(ticket)
        self.assertAlmostEqual(row.resolution_hours, 10.0, places=0)
        self.assertAlmostEqual(row.total_elapsed_hours, 10.0, places=0)

    def test_handling_basis_switches_the_measure(self):
        """BRD 9.1 and 18.1: the three options are a setting."""
        ticket = self._make_ticket("basis", user=self.agent_a)
        self._backdate(ticket, 20)
        history = ticket.assignment_history_ids
        self._set_interval(history, 6, 0)
        ticket.write({"stage_id": self.stage_done.id})

        self.company.hd_mis_handling_basis = "elapsed"
        elapsed = self._report(ticket).handling_hours
        self.company.hd_mis_handling_basis = "assignment"
        assignment = self._report(ticket).handling_hours
        self.company.hd_mis_handling_basis = "active"
        active = self._report(ticket).handling_hours

        self.assertAlmostEqual(elapsed, 20.0, places=0)
        self.assertGreater(elapsed, assignment)
        self.assertFalse(active, "no work-time source, so nothing is invented")

    def test_team_attribution_switches(self):
        """BRD 9.4: original, closing and current team are all available."""
        ticket = self._make_ticket("attribution", team=self.team_a,
                                   user=self.agent_a)
        ticket.write({"stage_id": self.stage_done.id})
        ticket.write({"stage_id": self.stage_new.id})
        ticket.write({"team_id": self.team_b.id})

        row = self._report(ticket)
        self.assertEqual(row.create_team_id, self.team_a)
        self.assertEqual(row.close_team_id, self.team_a)
        self.assertEqual(row.team_id, self.team_b)

        self.company.hd_mis_team_attribution = "current"
        self.assertEqual(self._report(ticket).attributed_team_id, self.team_b)
        self.company.hd_mis_team_attribution = "creation"
        self.assertEqual(self._report(ticket).attributed_team_id, self.team_a)
        self.company.hd_mis_team_attribution = "closing"
        self.assertEqual(self._report(ticket).attributed_team_id, self.team_a)

    def test_person_attribution_switches(self):
        ticket = self._make_ticket("person attribution", user=self.agent_a)
        ticket.write({"stage_id": self.stage_done.id})
        ticket.write({"stage_id": self.stage_new.id})
        ticket.write({"user_id": self.agent_b.id})

        self.company.hd_mis_person_attribution = "current"
        self.assertEqual(self._report(ticket).attributed_user_id, self.agent_b)
        self.company.hd_mis_person_attribution = "closure"
        self.assertEqual(self._report(ticket).attributed_user_id, self.agent_a)
        self.company.hd_mis_person_attribution = "assignment"
        self.assertEqual(self._report(ticket).attributed_user_id, self.agent_a)

    def test_first_resolution_differs_from_final(self):
        ticket = self._make_ticket("first vs final")
        self._backdate(ticket, 50)
        ticket.write({"stage_id": self.stage_done.id})
        first = self._report(ticket).first_resolution_hours
        ticket.write({"stage_id": self.stage_new.id})
        ticket.write({"stage_id": self.stage_done.id})
        row = self._report(ticket)
        self.assertAlmostEqual(row.first_resolution_hours, first, places=2)
        self.assertGreaterEqual(row.resolution_hours, row.first_resolution_hours)

    def test_current_assignment_duration_is_separate_from_age(self):
        """BRD 8.7: old ticket, recently reassigned."""
        ticket = self._make_ticket("old but fresh", user=self.agent_a)
        self._backdate(ticket, 200)
        ticket.write({"user_id": self.agent_b.id})
        current = ticket.assignment_history_ids.filtered(
            lambda h: not h.unassigned_date
        )
        self._set_interval(current, 2)
        row = self._report(ticket)
        self.assertAlmostEqual(row.open_age_hours, 200.0, places=0)
        self.assertAlmostEqual(row.current_assignment_hours, 2.0, places=0)

    def test_grouping_by_team_sums_without_duplication(self):
        for index in range(3):
            self._make_ticket("grouped %s" % index, team=self.team_a)
        groups = self.env["helpdesk.mis.report"]._read_group(
            [("attributed_team_id", "=", self.team_a.id)],
            ["attributed_team_id"],
            ["opened_count:sum", "ticket_count:sum"],
        )
        self.assertTrue(groups)
        _team, opened, tickets = groups[0]
        self.assertEqual(opened, tickets, "one row per ticket, always")
