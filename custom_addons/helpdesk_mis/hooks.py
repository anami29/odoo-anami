# Copyright 2026 RLFB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Backfill what can honestly be backfilled, and say what cannot.

BRD section 15 asks that gaps in historical data be documented rather than
guessed at. Two things are recoverable from what helpdesk_mgmt already stores:

* a ticket that is closed now has a ``closed_date``, so one closing event can
  be reconstructed from it;
* assignment intervals are already complete from the day helpdesk_mgmt began
  recording them.

Nothing else is. Earlier closures of a ticket that was reopened, and any
assignment that happened before the ledger existed, left no trace and are not
invented here.
"""

import logging

_logger = logging.getLogger(__name__)


def post_init_hook(env):
    _backfill_closure_events(env)
    _recompute_stored_measures(env)


def _backfill_closure_events(env):
    """One closing event per ticket that is closed and has no history yet."""
    Event = env["helpdesk.ticket.closure.event"]
    tickets = env["helpdesk.ticket"].with_context(active_test=False).search([
        ("closed", "=", True),
        ("closed_date", "!=", False),
        ("mis_closure_event_ids", "=", False),
    ])
    values = [{
        "ticket_id": ticket.id,
        "event_type": "close",
        "event_date": ticket.closed_date,
        "stage_id": ticket.stage_id.id or False,
        "from_stage_id": False,
        "team_id": ticket.team_id.id or False,
        "user_id": ticket.user_id.id or False,
        "changed_by_id": env.user.id,
        "occurrence": 1,
        "hours_from_create": round(
            max(
                0.0,
                (ticket.closed_date - ticket.create_date).total_seconds() / 3600.0,
            ),
            2,
        ) if ticket.create_date else 0.0,
    } for ticket in tickets]
    if values:
        Event.sudo().create(values)
    _logger.info(
        "helpdesk_mis: reconstructed %s closing events from closed_date. "
        "Any earlier closure of a reopened ticket is not recoverable.",
        len(values),
    )
    open_without_history = env["helpdesk.ticket"].search_count([
        ("closed", "=", False),
        ("assignment_history_ids", "=", False),
    ])
    if open_without_history:
        _logger.warning(
            "helpdesk_mis: %s open tickets have no assignment history at all. "
            "Their time-held figures will be empty until they next change "
            "hands - this predates the assignment ledger and cannot be "
            "reconstructed.",
            open_without_history,
        )


def _recompute_stored_measures(env):
    """Fill the new stored columns for records that already existed."""
    tickets = env["helpdesk.ticket"].with_context(active_test=False).search([])
    if tickets:
        tickets._compute_mis_closure_figures()
        tickets._compute_mis_assignment_figures()
        tickets._compute_mis_resolution_working_hours()
    history = env["helpdesk.ticket.assignment.history"].search([])
    if history:
        history._compute_duration_working()
    _logger.info(
        "helpdesk_mis: computed MIS measures for %s tickets and %s "
        "assignment intervals.",
        len(tickets),
        len(history),
    )
