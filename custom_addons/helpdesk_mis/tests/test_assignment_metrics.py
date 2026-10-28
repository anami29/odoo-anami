# Copyright 2026 RLFB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""BRD 7.5, 7.6 and FR-18: time held, reassignment, no double counting."""

from odoo.tests.common import tagged

from .common import MisCase


@tagged("post_install", "-at_install")
class TestAssignmentMetrics(MisCase):
    def _rows(self, ticket):
        return self.env["helpdesk.mis.assignment.report"].search(
            [("ticket_id", "=", ticket.id)], order="assigned_date asc, id asc"
        )

    def test_first_assignment_is_not_a_reassignment(self):
        ticket = self._make_ticket("first assignment", user=self.agent_a)
        self.assertEqual(ticket.mis_reassignment_count, 0)
        self.assertFalse(self._report(ticket).is_reassigned)

    def test_each_handover_counts_once(self):
        ticket = self._make_ticket("handovers", user=self.agent_a)
        ticket.write({"user_id": self.agent_b.id})
        ticket.write({"user_id": self.agent_a.id})
        self.assertEqual(ticket.mis_reassignment_count, 2)
        self.assertTrue(self._report(ticket).is_reassigned)
        self.assertEqual(self._report(ticket).reassignment_count, 2)

    def test_intervals_close_when_the_ticket_changes_hands(self):
        ticket = self._make_ticket("intervals", user=self.agent_a)
        ticket.write({"user_id": self.agent_b.id})
        rows = self._rows(ticket)
        self.assertEqual(len(rows), 2)
        self.assertFalse(rows[0].is_active)
        self.assertTrue(rows[1].is_active)
        self.assertEqual(rows[0].end_event, "transfer")
        self.assertEqual(rows[1].end_event, "active")
        self.assertEqual(rows[0].next_user_id, self.agent_b)
        self.assertEqual(rows[1].prev_user_id, self.agent_a)

    def test_interval_ends_when_the_ticket_closes(self):
        ticket = self._make_ticket("closes", user=self.agent_a)
        ticket.write({"stage_id": self.stage_done.id})
        row = self._rows(ticket)[-1]
        self.assertFalse(row.is_active)
        self.assertEqual(row.end_event, "closed")

    def test_time_held_matches_the_interval(self):
        ticket = self._make_ticket("time held", user=self.agent_a)
        history = ticket.assignment_history_ids
        self._set_interval(history, start_hours_ago=10, end_hours_ago=4)
        row = self._rows(ticket)
        self.assertAlmostEqual(row.time_held_hours, 6.0, places=1)

    def test_open_interval_runs_to_now(self):
        """BRD 7.6: an interval that has not ended runs to the report time."""
        ticket = self._make_ticket("still held", user=self.agent_a)
        history = ticket.assignment_history_ids
        self._set_interval(history, start_hours_ago=5)
        row = self._rows(ticket)
        self.assertTrue(row.is_active)
        self.assertAlmostEqual(row.time_held_hours, 5.0, places=1)

    def test_distinct_tickets_are_not_double_counted(self):
        """FR-18 and BRD 7.4, the trap this report is designed to avoid."""
        ticket = self._make_ticket("three hands", user=self.agent_a)
        ticket.write({"user_id": self.agent_b.id})
        ticket.write({"user_id": self.agent_a.id})
        rows = self._rows(ticket)
        self.assertEqual(len(rows), 3)
        # Three intervals, but only one ticket.
        self.assertEqual(sum(rows.mapped("interval_count")), 3)
        self.assertEqual(sum(rows.mapped("ticket_count")), 1)
        # Agent A held it twice but touched one ticket; B touched one.
        self.assertEqual(sum(rows.mapped("ticket_user_count")), 2)

    def test_cumulative_hours_add_up_per_person(self):
        ticket = self._make_ticket("cumulative", user=self.agent_a)
        first = ticket.assignment_history_ids
        self._set_interval(first, 20, 18)
        ticket.write({"user_id": self.agent_b.id})
        second = ticket.assignment_history_ids - first
        self._set_interval(second, 18, 15)
        ticket.write({"user_id": self.agent_a.id})
        third = ticket.assignment_history_ids - first - second
        self._set_interval(third, 15, 12)

        rows = self._rows(ticket)
        agent_a_rows = rows.filtered(lambda r: r.user_id == self.agent_a)
        # 2 hours the first time plus 3 the second.
        self.assertAlmostEqual(
            max(agent_a_rows.mapped("cumulative_user_hours")), 5.0, places=1
        )

    def test_working_hours_are_stored_alongside_elapsed(self):
        ticket = self._make_ticket("working hours", user=self.agent_a)
        history = ticket.assignment_history_ids
        self._set_interval(history, 48, 0)
        history._compute_duration_working()
        # Two whole days elapsed; working hours can only ever be less or equal.
        self.assertAlmostEqual(history.duration, 48.0, places=0)
        self.assertLessEqual(history.duration_working, history.duration + 0.01)

    def test_working_hours_setting_switches_the_reported_measure(self):
        ticket = self._make_ticket("switch measure", user=self.agent_a)
        history = ticket.assignment_history_ids
        self._set_interval(history, 48, 0)
        history._compute_duration_working()

        self.company.hd_mis_working_hours = False
        elapsed = self._rows(ticket).time_held_hours
        self.company.hd_mis_working_hours = True
        working = self._rows(ticket).time_held_hours

        self.assertAlmostEqual(elapsed, 48.0, places=0)
        self.assertAlmostEqual(working, history.duration_working, places=2)

    def test_people_involved_counts_distinct_assignees(self):
        ticket = self._make_ticket("people", user=self.agent_a)
        ticket.write({"user_id": self.agent_b.id})
        ticket.write({"user_id": self.agent_a.id})
        self.assertEqual(ticket.mis_assignee_count, 2)
