# Copyright 2026 RLFB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""One row per assignment interval - BRD report A5, "time held by each person".

The grain is deliberately *not* the ticket. A ticket handled by three people
produces three rows, which is what "how long did each person hold it" needs,
and is exactly the shape that double-counts tickets if summed carelessly
(BRD 7.4 and FR-18). Three separate count measures keep that honest:

    interval_count    1 on every row      - how many hand-offs happened
    ticket_count      1 on the first row of each ticket   - distinct tickets
    ticket_user_count 1 on the first row of each ticket+person pair

Group by person and sum ticket_user_count to get "tickets this person touched"
without inflating the organisation-wide total.
"""

from odoo import fields, models, tools


class HelpdeskMisAssignmentReport(models.Model):
    _name = "helpdesk.mis.assignment.report"
    _description = "Helpdesk MIS - Time Held by Person"
    _auto = False
    _rec_name = "ticket_number"
    _order = "assigned_date desc, id desc"

    # --- identity ---------------------------------------------------------
    ticket_id = fields.Many2one("helpdesk.ticket", string="Ticket", readonly=True)
    ticket_number = fields.Char(string="Ticket No.", readonly=True)
    ticket_name = fields.Char(string="Subject", readonly=True)
    partner_id = fields.Many2one("res.partner", string="Customer", readonly=True)
    category_id = fields.Many2one(
        "helpdesk.ticket.category", string="Category", readonly=True
    )
    stage_id = fields.Many2one("helpdesk.ticket.stage", string="Stage", readonly=True)
    company_id = fields.Many2one("res.company", readonly=True)
    priority = fields.Selection(
        [("0", "Low"), ("1", "Medium"), ("2", "High"), ("3", "Very High")],
        readonly=True,
    )

    # --- who held it ------------------------------------------------------
    user_id = fields.Many2one("res.users", string="Assigned Person", readonly=True)
    team_id = fields.Many2one(
        "helpdesk.ticket.team", string="Team During Interval", readonly=True
    )
    prev_user_id = fields.Many2one(
        "res.users", string="Previous Person", readonly=True
    )
    prev_team_id = fields.Many2one(
        "helpdesk.ticket.team", string="Previous Team", readonly=True
    )
    next_user_id = fields.Many2one(
        "res.users", string="Next Person", readonly=True
    )
    next_team_id = fields.Many2one(
        "helpdesk.ticket.team", string="Next Team", readonly=True
    )
    assigned_by_id = fields.Many2one(
        "res.users", string="Assigned By", readonly=True
    )

    # --- when -------------------------------------------------------------
    assigned_date = fields.Datetime(string="Assignment Start", readonly=True)
    unassigned_date = fields.Datetime(string="Assignment End", readonly=True)
    ticket_create_date = fields.Datetime(string="Ticket Created", readonly=True)
    ticket_closed_date = fields.Datetime(string="Ticket Closed", readonly=True)
    is_active = fields.Boolean(string="Still Holding", readonly=True)
    ticket_is_closed = fields.Boolean(string="Ticket Closed?", readonly=True)
    end_event = fields.Selection(
        [
            ("active", "Still assigned"),
            ("transfer", "Transferred"),
            ("closed", "Ticket closed"),
            ("ended", "Unassigned"),
        ],
        string="Ended By",
        readonly=True,
    )

    # --- measures ---------------------------------------------------------
    time_held_hours = fields.Float(
        string="Time Held", readonly=True, aggregator="avg", digits=(16, 2)
    )
    time_held_hours_max = fields.Float(
        string="Longest Time Held", readonly=True, aggregator="max", digits=(16, 2)
    )
    time_held_total = fields.Float(
        string="Total Time Held", readonly=True, aggregator="sum", digits=(16, 2)
    )
    time_held_working_hours = fields.Float(
        string="Time Held (working)",
        readonly=True,
        aggregator="sum",
        digits=(16, 2),
    )
    cumulative_user_hours = fields.Float(
        string="Cumulative on This Ticket",
        readonly=True,
        aggregator="max",
        digits=(16, 2),
        help="Everything this person has held this ticket for, across all "
        "their separate intervals on it.",
    )
    interval_count = fields.Integer(
        string="Assignments", readonly=True, aggregator="sum"
    )
    ticket_count = fields.Integer(
        string="Distinct Tickets",
        readonly=True,
        aggregator="sum",
        help="1 on the first interval of each ticket only, so summing this "
        "never counts a reassigned ticket twice.",
    )
    ticket_user_count = fields.Integer(
        string="Tickets Touched",
        readonly=True,
        aggregator="sum",
        help="1 on the first interval of each ticket-and-person pair.",
    )
    reassignment_count = fields.Integer(
        string="Reassignments", readonly=True, aggregator="sum"
    )
    ticket_reassignment_total = fields.Integer(
        string="Reassignments on Ticket", readonly=True, aggregator="max"
    )

    # ------------------------------------------------------------------
    def init(self):
        """Deferred for the same reason as helpdesk.mis.report: this query
        reads duration_working and mis_reassignment_count, both of which are
        added during this same module load."""
        if hasattr(self.pool, "_post_init_queue"):
            self.pool.post_init(self._create_view)
        else:
            self._create_view()

    def _create_view(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(
            """
CREATE OR REPLACE VIEW %s AS (
WITH rows_raw AS (
    SELECT
        h.id,
        h.ticket_id,
        h.new_user_id,
        h.old_user_id,
        h.new_team_id,
        h.old_team_id,
        h.assigned_by_id,
        h.assigned_date,
        h.unassigned_date,
        COALESCE(h.duration_working, 0.0) AS working_hours,
        EXTRACT(EPOCH FROM (
            COALESCE(h.unassigned_date, NOW() AT TIME ZONE 'UTC') - h.assigned_date
        )) / 3600.0 AS elapsed_hours,
        t.number AS ticket_number,
        t.name AS ticket_name,
        t.partner_id,
        t.category_id,
        t.stage_id,
        t.priority,
        t.company_id,
        t.create_date AS ticket_create_date,
        t.mis_reassignment_count,
        COALESCE(st.closed, FALSE) AS ticket_is_closed,
        CASE WHEN COALESCE(st.closed, FALSE) THEN t.closed_date END
            AS ticket_closed_date,
        COALESCE(co.hd_mis_working_hours, FALSE) AS use_working
    FROM helpdesk_ticket_assignment_history h
    JOIN helpdesk_ticket t ON t.id = h.ticket_id
    LEFT JOIN helpdesk_ticket_stage st ON st.id = t.stage_id
    LEFT JOIN res_company co ON co.id = t.company_id
    WHERE t.active = TRUE
      AND h.assigned_date IS NOT NULL
)
SELECT
    r.id AS id,
    r.ticket_id,
    r.ticket_number,
    r.ticket_name,
    r.partner_id,
    r.category_id,
    r.stage_id,
    r.priority,
    r.company_id,
    r.new_user_id AS user_id,
    r.new_team_id AS team_id,
    r.old_user_id AS prev_user_id,
    r.old_team_id AS prev_team_id,
    LEAD(r.new_user_id) OVER w AS next_user_id,
    LEAD(r.new_team_id) OVER w AS next_team_id,
    r.assigned_by_id,
    r.assigned_date,
    r.unassigned_date,
    r.ticket_create_date,
    r.ticket_closed_date,
    r.ticket_is_closed,
    (r.unassigned_date IS NULL) AS is_active,
    CASE
        WHEN r.unassigned_date IS NULL THEN 'active'
        WHEN LEAD(r.id) OVER w IS NOT NULL THEN 'transfer'
        WHEN r.ticket_is_closed THEN 'closed'
        ELSE 'ended'
    END AS end_event,
    CASE WHEN r.use_working THEN r.working_hours ELSE r.elapsed_hours END
        AS time_held_hours,
    CASE WHEN r.use_working THEN r.working_hours ELSE r.elapsed_hours END
        AS time_held_hours_max,
    CASE WHEN r.use_working THEN r.working_hours ELSE r.elapsed_hours END
        AS time_held_total,
    r.working_hours AS time_held_working_hours,
    SUM(CASE WHEN r.use_working THEN r.working_hours ELSE r.elapsed_hours END)
        OVER (PARTITION BY r.ticket_id, r.new_user_id) AS cumulative_user_hours,
    1 AS interval_count,
    CASE WHEN ROW_NUMBER() OVER w = 1 THEN 1 ELSE 0 END AS ticket_count,
    CASE WHEN ROW_NUMBER() OVER (
            PARTITION BY r.ticket_id, r.new_user_id
            ORDER BY r.assigned_date, r.id
         ) = 1 THEN 1 ELSE 0 END AS ticket_user_count,
    CASE WHEN r.old_user_id IS NOT NULL OR r.old_team_id IS NOT NULL
         THEN 1 ELSE 0 END AS reassignment_count,
    COALESCE(r.mis_reassignment_count, 0) AS ticket_reassignment_total
FROM rows_raw r
WINDOW w AS (PARTITION BY r.ticket_id ORDER BY r.assigned_date, r.id)
)
        """
            % self._table
        )
