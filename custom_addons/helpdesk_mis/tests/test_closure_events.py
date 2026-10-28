# Copyright 2026 RLFB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""BRD FR-16 and 9.2: reopened tickets."""

from odoo.tests.common import tagged

from .common import MisCase


@tagged("post_install", "-at_install")
class TestClosureEvents(MisCase):
    def test_closing_records_one_event(self):
        ticket = self._make_ticket("closes once")
        self.assertFalse(ticket.mis_closure_event_ids)
        ticket.write({"stage_id": self.stage_done.id})
        self.assertEqual(len(ticket.mis_closure_event_ids), 1)
        event = ticket.mis_closure_event_ids
        self.assertEqual(event.event_type, "close")
        self.assertEqual(event.occurrence, 1)
        self.assertEqual(ticket.mis_closure_count, 1)
        self.assertEqual(ticket.mis_reopen_count, 0)

    def test_reopening_is_recorded_and_does_not_count_as_a_closure(self):
        ticket = self._make_ticket("reopens")
        ticket.write({"stage_id": self.stage_done.id})
        ticket.write({"stage_id": self.stage_new.id})
        self.assertEqual(ticket.mis_closure_count, 1)
        self.assertEqual(ticket.mis_reopen_count, 1)
        self.assertEqual(
            ticket.mis_closure_event_ids.mapped("event_type"),
            ["close", "reopen"],
        )

    def test_first_closure_survives_a_later_one(self):
        """The whole reason this model exists.

        helpdesk_mgmt overwrites closed_date on the second close, so without
        the event ledger the first closure would be gone.
        """
        ticket = self._make_ticket("closes twice")
        self._backdate(ticket, 100)
        ticket.write({"stage_id": self.stage_done.id})
        first_closed = ticket.mis_first_closed_date
        self.assertTrue(first_closed)
        first_resolution = ticket.mis_first_resolution_hours
        self.assertGreater(first_resolution, 0.0)

        ticket.write({"stage_id": self.stage_new.id})
        ticket.write({"stage_id": self.stage_done.id})

        self.assertEqual(ticket.mis_closure_count, 2)
        self.assertEqual(ticket.mis_reopen_count, 1)
        # First closure is untouched...
        self.assertEqual(ticket.mis_first_closed_date, first_closed)
        self.assertAlmostEqual(
            ticket.mis_first_resolution_hours, first_resolution, places=2
        )
        # ...while closed_date has moved on to the latest one.
        self.assertGreater(ticket.closed_date, first_closed)

    def test_occurrence_numbers_increment(self):
        ticket = self._make_ticket("counts occurrences")
        for _i in range(3):
            ticket.write({"stage_id": self.stage_done.id})
            ticket.write({"stage_id": self.stage_new.id})
        closes = ticket.mis_closure_event_ids.filtered(
            lambda e: e.event_type == "close"
        ).sorted("event_date")
        self.assertEqual(closes.mapped("occurrence"), [1, 2, 3])

    def test_stage_change_that_does_not_cross_the_line_records_nothing(self):
        other_open = self.env["helpdesk.ticket.stage"].create({
            "name": "MIS In Progress", "sequence": 5, "closed": False,
        })
        ticket = self._make_ticket("moves between open stages")
        ticket.write({"stage_id": other_open.id})
        self.assertFalse(ticket.mis_closure_event_ids)

    def test_event_captures_team_and_person_at_the_time(self):
        ticket = self._make_ticket("captures context", team=self.team_a,
                                   user=self.agent_a)
        ticket.write({"stage_id": self.stage_done.id})
        event = ticket.mis_closure_event_ids
        self.assertEqual(event.team_id, self.team_a)
        self.assertEqual(event.user_id, self.agent_a)
        # Moving the ticket afterwards must not rewrite history.
        ticket.write({"team_id": self.team_b.id, "user_id": self.agent_b.id})
        self.assertEqual(event.team_id, self.team_a)
        self.assertEqual(event.user_id, self.agent_a)

    def test_closed_count_follows_the_counting_setting(self):
        ticket = self._make_ticket("counting rule")
        ticket.write({"stage_id": self.stage_done.id})
        ticket.write({"stage_id": self.stage_new.id})
        ticket.write({"stage_id": self.stage_done.id})

        self.company.hd_mis_reopen_counting = "distinct"
        self.assertEqual(self._report(ticket).closed_count, 1)

        self.company.hd_mis_reopen_counting = "events"
        self.assertEqual(self._report(ticket).closed_count, 2)
