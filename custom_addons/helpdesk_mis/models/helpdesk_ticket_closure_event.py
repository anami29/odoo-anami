# Copyright 2026 RLFB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""A permanent record of every time a ticket closed or reopened.

Stock helpdesk_mgmt overwrites ``closed_date`` on each close, so a ticket that
is closed, reopened and closed again keeps only the last timestamp. BRD section
9.2 asks for "time to first closure" and "final resolution time" as separate
measures, which that single column cannot answer. One row per event here makes
both recoverable, and makes closure-event counts possible alongside
distinct-ticket counts (BRD 18.4).
"""

from odoo import api, fields, models


class HelpdeskTicketClosureEvent(models.Model):
    _name = "helpdesk.ticket.closure.event"
    _description = "Helpdesk Ticket Closure Event"
    _order = "event_date asc, id asc"
    _rec_name = "ticket_id"

    ticket_id = fields.Many2one(
        "helpdesk.ticket",
        required=True,
        ondelete="cascade",
        index=True,
        readonly=True,
    )
    event_type = fields.Selection(
        selection=[("close", "Closed"), ("reopen", "Reopened")],
        required=True,
        readonly=True,
        index=True,
    )
    event_date = fields.Datetime(
        required=True, default=fields.Datetime.now, readonly=True, index=True
    )
    stage_id = fields.Many2one(
        "helpdesk.ticket.stage", string="Stage reached", readonly=True
    )
    from_stage_id = fields.Many2one(
        "helpdesk.ticket.stage", string="Previous stage", readonly=True
    )
    # Captured at the moment of the event: the current ticket values drift
    # afterwards, and a closure report must say who closed it *then*.
    team_id = fields.Many2one(
        "helpdesk.ticket.team", string="Team at the time", readonly=True
    )
    user_id = fields.Many2one(
        "res.users", string="Assignee at the time", readonly=True
    )
    changed_by_id = fields.Many2one(
        "res.users", string="Changed by", readonly=True, default=lambda s: s.env.user
    )
    occurrence = fields.Integer(
        string="Occurrence",
        readonly=True,
        help="1 for the first closure, 2 for the second, and so on.",
    )
    hours_from_create = fields.Float(
        string="Hours since creation",
        readonly=True,
        digits=(16, 2),
        aggregator="avg",
    )
    company_id = fields.Many2one(
        "res.company", related="ticket_id.company_id", store=True, readonly=True
    )

    @api.model
    def _record(self, ticket, event_type, from_stage=None, when=None):
        """Append one event. Called from helpdesk.ticket.write."""
        when = when or fields.Datetime.now()
        occurrence = self.search_count([
            ("ticket_id", "=", ticket.id),
            ("event_type", "=", event_type),
        ]) + 1
        hours = 0.0
        if ticket.create_date:
            hours = (when - ticket.create_date).total_seconds() / 3600.0
        return self.sudo().create({
            "ticket_id": ticket.id,
            "event_type": event_type,
            "event_date": when,
            "stage_id": ticket.stage_id.id or False,
            "from_stage_id": from_stage.id if from_stage else False,
            "team_id": ticket.team_id.id or False,
            "user_id": ticket.user_id.id or False,
            "changed_by_id": self.env.user.id,
            "occurrence": occurrence,
            "hours_from_create": round(max(0.0, hours), 2),
        })
