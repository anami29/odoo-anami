# Copyright 2026 RLFB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Working-hour durations on each assignment interval.

helpdesk_mgmt already records every interval and its elapsed duration. BRD
18.7 asks whether time held should include nights, weekends and holidays. That
cannot be answered in SQL - it needs resource.calendar - so the working-hour
figure is computed in Python and stored, and the MIS views pick whichever
column the company setting asks for.
"""

import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class HelpdeskTicketAssignmentHistory(models.Model):
    _inherit = "helpdesk.ticket.assignment.history"

    duration_working = fields.Float(
        string="Time Held (working hours)",
        compute="_compute_duration_working",
        store=True,
        readonly=True,
        digits=(16, 2),
        aggregator="sum",
        help="The same interval as Duration, counting only hours inside the "
        "reporting calendar.",
    )
    company_id = fields.Many2one(
        "res.company",
        related="ticket_id.company_id",
        store=True,
        readonly=True,
        index=True,
    )

    @api.depends("assigned_date", "unassigned_date", "ticket_id")
    def _compute_duration_working(self):
        now = fields.Datetime.now()
        for record in self:
            start = record.assigned_date
            if not start:
                record.duration_working = 0.0
                continue
            end = record.unassigned_date or now
            if end <= start:
                record.duration_working = 0.0
                continue
            calendar = record.company_id._hd_mis_calendar()
            if not calendar:
                record.duration_working = round(
                    (end - start).total_seconds() / 3600.0, 2
                )
                continue
            try:
                record.duration_working = round(
                    calendar.get_work_hours_count(start, end), 2
                )
            except Exception:  # pragma: no cover - calendar misconfiguration
                _logger.warning(
                    "Helpdesk MIS: calendar %s could not measure interval %s; "
                    "falling back to elapsed time.",
                    calendar.display_name,
                    record.id,
                )
                record.duration_working = round(
                    (end - start).total_seconds() / 3600.0, 2
                )

    @api.model
    def _mis_refresh_working_hours(self, limit=None):
        """Recompute open intervals, whose working time is still growing."""
        active = self.search([("unassigned_date", "=", False)], limit=limit)
        if active:
            active.invalidate_recordset(["duration_working", "duration"])
            active.modified(["assigned_date", "unassigned_date"])
            active._compute_duration_working()
        return len(active)
