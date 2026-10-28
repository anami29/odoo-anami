# Copyright 2026 RLFB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""MIS counters on the ticket, and the hook that records closure events.

Everything here is stored, because the MIS reports are SQL views: a computed
field that is not stored has no column for the view to read.
"""

import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class HelpdeskTicket(models.Model):
    _inherit = "helpdesk.ticket"

    mis_closure_event_ids = fields.One2many(
        "helpdesk.ticket.closure.event",
        "ticket_id",
        string="Closure History",
        readonly=True,
    )
    mis_first_closed_date = fields.Datetime(
        string="First closed on",
        compute="_compute_mis_closure_figures",
        store=True,
        readonly=True,
        help="When this ticket was closed the first time. Stays put when the "
        "ticket is reopened and closed again.",
    )
    mis_closure_count = fields.Integer(
        string="Times closed",
        compute="_compute_mis_closure_figures",
        store=True,
        readonly=True,
    )
    mis_reopen_count = fields.Integer(
        string="Times reopened",
        compute="_compute_mis_closure_figures",
        store=True,
        readonly=True,
    )
    mis_first_resolution_hours = fields.Float(
        string="First resolution (hours)",
        compute="_compute_mis_closure_figures",
        store=True,
        readonly=True,
        digits=(16, 2),
        aggregator="avg",
    )
    mis_reassignment_count = fields.Integer(
        string="Reassignments",
        compute="_compute_mis_assignment_figures",
        store=True,
        readonly=True,
        help="How many times the ticket changed hands. The first assignment "
        "when the ticket was raised is not a reassignment.",
    )
    mis_assignee_count = fields.Integer(
        string="People involved",
        compute="_compute_mis_assignment_figures",
        store=True,
        readonly=True,
    )
    mis_total_held_hours = fields.Float(
        string="Total time held (hours)",
        compute="_compute_mis_assignment_figures",
        store=True,
        readonly=True,
        digits=(16, 2),
        aggregator="sum",
    )
    mis_resolution_working_hours = fields.Float(
        string="Resolution (working hours)",
        compute="_compute_mis_resolution_working_hours",
        store=True,
        readonly=True,
        digits=(16, 2),
        aggregator="avg",
        help="Creation to closure, counting only hours inside the reporting "
        "calendar. Always kept up to date so the working-hours setting can be "
        "switched on and off without recomputing anything.",
    )
    mis_active_work_hours = fields.Float(
        string="Active work (hours)",
        default=0.0,
        digits=(16, 2),
        aggregator="sum",
        help="Actual time worked on this ticket. BRD option C. Odoo has no "
        "such source on its own - a timesheet or timer module must fill this "
        "in. It stays zero until something does.",
    )

    # ------------------------------------------------------------------
    # computed figures
    # ------------------------------------------------------------------
    @api.depends(
        "mis_closure_event_ids.event_type",
        "mis_closure_event_ids.event_date",
        "create_date",
    )
    def _compute_mis_closure_figures(self):
        for ticket in self:
            closes = ticket.mis_closure_event_ids.filtered(
                lambda e: e.event_type == "close"
            ).sorted("event_date")
            reopens = ticket.mis_closure_event_ids.filtered(
                lambda e: e.event_type == "reopen"
            )
            ticket.mis_closure_count = len(closes)
            ticket.mis_reopen_count = len(reopens)
            first = closes[:1].event_date
            ticket.mis_first_closed_date = first or False
            if first and ticket.create_date:
                delta = (first - ticket.create_date).total_seconds() / 3600.0
                ticket.mis_first_resolution_hours = round(max(0.0, delta), 2)
            else:
                ticket.mis_first_resolution_hours = 0.0

    @api.depends(
        "assignment_history_ids.old_user_id",
        "assignment_history_ids.old_team_id",
        "assignment_history_ids.new_user_id",
        "assignment_history_ids.duration",
    )
    def _compute_mis_assignment_figures(self):
        for ticket in self:
            history = ticket.assignment_history_ids
            # The row written when the ticket is raised has no previous owner;
            # it is the first assignment, not a reassignment.
            ticket.mis_reassignment_count = len(
                history.filtered(lambda h: h.old_user_id or h.old_team_id)
            )
            ticket.mis_assignee_count = len(
                history.mapped("new_user_id").filtered(lambda u: u)
            )
            ticket.mis_total_held_hours = round(
                sum(history.mapped("duration")), 2
            )

    @api.depends("create_date", "closed_date", "stage_id.closed")
    def _compute_mis_resolution_working_hours(self):
        for ticket in self:
            start = ticket.create_date
            end = ticket.closed_date if ticket.stage_id.closed else None
            if not start or not end or end <= start:
                ticket.mis_resolution_working_hours = 0.0
                continue
            ticket.mis_resolution_working_hours = round(
                ticket._mis_working_hours_between(start, end), 2
            )

    def _mis_working_hours_between(self, start, end):
        """Working hours between two naive UTC datetimes, per the calendar."""
        self.ensure_one()
        calendar = self.company_id._hd_mis_calendar()
        if not calendar:
            # No calendar configured anywhere: fall back to elapsed time rather
            # than silently reporting zero.
            return (end - start).total_seconds() / 3600.0
        try:
            return calendar.get_work_hours_count(start, end)
        except Exception:  # pragma: no cover - calendar misconfiguration
            _logger.warning(
                "Helpdesk MIS: could not read working hours from calendar %s "
                "for ticket %s; falling back to elapsed time.",
                calendar.display_name,
                self.id,
            )
            return (end - start).total_seconds() / 3600.0

    # ------------------------------------------------------------------
    # closure events
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        tickets = super().create(vals_list)
        for ticket in tickets:
            # Rare, but a ticket can be created straight into a closed stage.
            if ticket.stage_id.closed:
                self.env["helpdesk.ticket.closure.event"]._record(ticket, "close")
        return tickets

    def write(self, vals):
        if "stage_id" not in vals:
            return super().write(vals)
        # Read the state before the write: helpdesk_mgmt's own write sets
        # closed_date as a side effect, so comparing afterwards is too late.
        before = {t.id: (t.stage_id, bool(t.stage_id.closed)) for t in self}
        res = super().write(vals)
        Event = self.env["helpdesk.ticket.closure.event"]
        for ticket in self:
            from_stage, was_closed = before.get(ticket.id, (None, False))
            is_closed = bool(ticket.stage_id.closed)
            if is_closed == was_closed:
                continue
            Event._record(
                ticket,
                "close" if is_closed else "reopen",
                from_stage=from_stage,
            )
        return res

    # ------------------------------------------------------------------
    # maintenance
    # ------------------------------------------------------------------
    @api.model
    def _mis_refresh_live_durations(self, limit=None):
        """Keep working-hour figures current on tickets that are still open.

        Elapsed durations are calculated live in SQL, but working hours need
        the calendar, which SQL cannot read. A ticket that is still open has a
        working-hour figure that grows with the working day, so it is refreshed
        on a schedule rather than only when someone edits the ticket.
        """
        open_tickets = self.search([("closed", "=", False)], limit=limit)
        open_tickets.invalidate_recordset(["mis_resolution_working_hours"])
        self.env["helpdesk.ticket.assignment.history"]._mis_refresh_working_hours()
        _logger.info(
            "Helpdesk MIS: refreshed live durations for %s open tickets.",
            len(open_tickets),
        )
        return True
