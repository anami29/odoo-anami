# Copyright 2026 RLFB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""One row per ticket: the backbone of both MIS dashboards.

The view joins res_company so that the reporting decisions (BRD section 18)
are read at query time. Changing a setting in Settings changes every KPI on
the next refresh - nothing is baked into the view at install time except the
shape of the query.
"""

from odoo import fields, models, tools

HOURS = "EXTRACT(EPOCH FROM (%s - %s)) / 3600.0"


class HelpdeskMisReport(models.Model):
    _name = "helpdesk.mis.report"
    _description = "Helpdesk MIS - Ticket Performance"
    _auto = False
    _rec_name = "number"
    _order = "create_date desc"

    # --- identity ---------------------------------------------------------
    ticket_id = fields.Many2one("helpdesk.ticket", readonly=True)
    number = fields.Char(string="Ticket", readonly=True)
    name = fields.Char(string="Subject", readonly=True)
    partner_id = fields.Many2one("res.partner", string="Customer", readonly=True)
    company_id = fields.Many2one("res.company", readonly=True)
    category_id = fields.Many2one(
        "helpdesk.ticket.category", string="Category", readonly=True
    )
    stage_id = fields.Many2one("helpdesk.ticket.stage", string="Stage", readonly=True)
    priority = fields.Selection(
        [("0", "Low"), ("1", "Medium"), ("2", "High"), ("3", "Very High")],
        readonly=True,
    )

    # --- attribution (BRD 9.4: all four are available at once) ------------
    team_id = fields.Many2one(
        "helpdesk.ticket.team", string="Current Team", readonly=True
    )
    user_id = fields.Many2one("res.users", string="Current Assignee", readonly=True)
    create_team_id = fields.Many2one(
        "helpdesk.ticket.team", string="Originating Team", readonly=True
    )
    create_user_id = fields.Many2one(
        "res.users", string="First Assignee", readonly=True
    )
    close_team_id = fields.Many2one(
        "helpdesk.ticket.team", string="Closing Team", readonly=True
    )
    close_user_id = fields.Many2one(
        "res.users", string="Closed By", readonly=True
    )
    attributed_team_id = fields.Many2one(
        "helpdesk.ticket.team",
        string="Team",
        readonly=True,
        help="The team this ticket counts towards, following the attribution "
        "rule set in Settings.",
    )
    attributed_user_id = fields.Many2one(
        "res.users", string="Person", readonly=True
    )

    # --- dates ------------------------------------------------------------
    create_date = fields.Datetime(string="Created", readonly=True)
    closed_date = fields.Datetime(string="Closed", readonly=True)
    first_closed_date = fields.Datetime(string="First Closed", readonly=True)
    last_assignment_date = fields.Datetime(
        string="Last Assigned", readonly=True
    )

    # --- state ------------------------------------------------------------
    is_closed = fields.Boolean(string="Closed", readonly=True)
    is_open = fields.Boolean(string="Open", readonly=True)
    is_unassigned = fields.Boolean(string="Unassigned", readonly=True)
    is_reassigned = fields.Boolean(string="Reassigned", readonly=True)
    past_threshold = fields.Boolean(string="Past Threshold", readonly=True)
    age_band = fields.Selection(
        [
            ("closed", "Closed"),
            ("ok", "Within threshold"),
            ("warn", "Ageing"),
            ("high", "Overdue"),
            ("critical", "Critical"),
        ],
        string="Ageing",
        readonly=True,
    )

    # --- counts (measures) ------------------------------------------------
    ticket_count = fields.Integer(string="Tickets", readonly=True, aggregator="sum")
    opened_count = fields.Integer(
        string="Opened", readonly=True, aggregator="sum"
    )
    closed_count = fields.Integer(
        string="Closed Count",
        readonly=True,
        aggregator="sum",
        help="Follows the closure-counting rule in Settings: distinct tickets, "
        "or one per closure event.",
    )
    open_count = fields.Integer(
        string="Currently Open", readonly=True, aggregator="sum"
    )
    unassigned_count = fields.Integer(
        string="Unassigned", readonly=True, aggregator="sum"
    )
    reassignment_count = fields.Integer(
        string="Reassignments", readonly=True, aggregator="sum"
    )
    reassigned_ticket_count = fields.Integer(
        string="Reassigned Tickets", readonly=True, aggregator="sum"
    )
    assignee_count = fields.Integer(
        string="People Involved", readonly=True, aggregator="avg"
    )
    closure_event_count = fields.Integer(
        string="Closure Events", readonly=True, aggregator="sum"
    )
    reopen_count = fields.Integer(
        string="Reopens", readonly=True, aggregator="sum"
    )

    # --- durations (measures) --------------------------------------------
    resolution_hours = fields.Float(
        string="Resolution Time", readonly=True, aggregator="avg", digits=(16, 2)
    )
    resolution_hours_max = fields.Float(
        string="Max Resolution Time",
        readonly=True,
        aggregator="max",
        digits=(16, 2),
    )
    first_resolution_hours = fields.Float(
        string="First Resolution Time",
        readonly=True,
        aggregator="avg",
        digits=(16, 2),
    )
    open_age_hours = fields.Float(
        string="Open Age", readonly=True, aggregator="avg", digits=(16, 2)
    )
    open_age_hours_max = fields.Float(
        string="Max Open Age", readonly=True, aggregator="max", digits=(16, 2)
    )
    total_elapsed_hours = fields.Float(
        string="Total Elapsed", readonly=True, aggregator="avg", digits=(16, 2)
    )
    total_elapsed_hours_max = fields.Float(
        string="Max Total Elapsed",
        readonly=True,
        aggregator="max",
        digits=(16, 2),
    )
    assignment_hours = fields.Float(
        string="Assignment Duration",
        readonly=True,
        aggregator="avg",
        digits=(16, 2),
    )
    max_assignment_hours = fields.Float(
        string="Longest Single Assignment",
        readonly=True,
        aggregator="max",
        digits=(16, 2),
    )
    current_assignment_hours = fields.Float(
        string="Current Assignment Duration",
        readonly=True,
        aggregator="avg",
        digits=(16, 2),
    )
    handling_hours = fields.Float(
        string="Handling Time",
        readonly=True,
        aggregator="avg",
        digits=(16, 2),
        help="The measure chosen by the handling-time basis in Settings.",
    )
    hours_beyond_threshold = fields.Float(
        string="Hours Beyond Threshold",
        readonly=True,
        aggregator="avg",
        digits=(16, 2),
    )

    # ------------------------------------------------------------------
    def init(self):
        """Build the view only once every table it reads exists.

        Odoo creates tables and views in a single interleaved loop
        (``_auto_init()`` then ``init()``, model by model), so a view defined
        here could be built before helpdesk.ticket has gained the mis_*
        columns this query selects, or before the assignment ledger has
        duration_working. The post-init queue runs after every table in the
        module is in place, which makes the model order irrelevant.
        """
        if hasattr(self.pool, "_post_init_queue"):
            self.pool.post_init(self._create_view)
        else:
            # Reached when Odoo rebuilds a missing table outside module load.
            self._create_view()

    def _create_view(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(
            """
CREATE OR REPLACE VIEW %s AS (
WITH assign AS (
    SELECT h.ticket_id,
           SUM(EXTRACT(EPOCH FROM (COALESCE(h.unassigned_date, NOW() AT TIME ZONE 'UTC')
                                   - h.assigned_date)) / 3600.0) AS elapsed_hours,
           MAX(EXTRACT(EPOCH FROM (COALESCE(h.unassigned_date, NOW() AT TIME ZONE 'UTC')
                                   - h.assigned_date)) / 3600.0) AS max_hours,
           SUM(COALESCE(h.duration_working, 0.0)) AS working_hours,
           MAX(COALESCE(h.duration_working, 0.0)) AS max_working_hours,
           COUNT(*) FILTER (
               WHERE h.old_user_id IS NOT NULL OR h.old_team_id IS NOT NULL
           ) AS reassignments,
           COUNT(DISTINCT h.new_user_id) FILTER (
               WHERE h.new_user_id IS NOT NULL
           ) AS people,
           MAX(h.assigned_date) AS last_assigned,
           MAX(h.assigned_date) FILTER (
               WHERE h.unassigned_date IS NULL
           ) AS current_start
    FROM helpdesk_ticket_assignment_history h
    GROUP BY h.ticket_id
),
first_assign AS (
    SELECT DISTINCT ON (h.ticket_id)
           h.ticket_id, h.new_team_id, h.new_user_id
    FROM helpdesk_ticket_assignment_history h
    ORDER BY h.ticket_id, h.assigned_date ASC, h.id ASC
),
closures AS (
    SELECT ce.ticket_id,
           COUNT(*) FILTER (WHERE ce.event_type = 'close') AS closes,
           COUNT(*) FILTER (WHERE ce.event_type = 'reopen') AS reopens
    FROM helpdesk_ticket_closure_event ce
    GROUP BY ce.ticket_id
),
last_close AS (
    SELECT DISTINCT ON (ce.ticket_id)
           ce.ticket_id, ce.team_id, ce.user_id
    FROM helpdesk_ticket_closure_event ce
    WHERE ce.event_type = 'close'
    ORDER BY ce.ticket_id, ce.event_date DESC, ce.id DESC
),
base AS (
    SELECT
        t.id,
        t.number,
        t.name,
        t.partner_id,
        t.company_id,
        t.category_id,
        t.stage_id,
        t.priority,
        t.team_id,
        t.user_id,
        t.create_date,
        t.mis_first_closed_date,
        t.mis_resolution_working_hours,
        t.mis_active_work_hours,
        COALESCE(st.closed, FALSE) AS is_closed,
        CASE WHEN COALESCE(st.closed, FALSE) THEN t.closed_date END AS closed_date,
        fa.new_team_id AS create_team_id,
        fa.new_user_id AS create_user_id,
        lc.team_id AS close_team_id,
        lc.user_id AS close_user_id,
        COALESCE(a.elapsed_hours, 0.0) AS assign_elapsed,
        COALESCE(a.working_hours, 0.0) AS assign_working,
        COALESCE(a.max_hours, 0.0) AS assign_max,
        COALESCE(a.max_working_hours, 0.0) AS assign_max_working,
        COALESCE(a.reassignments, 0) AS reassignments,
        COALESCE(a.people, 0) AS people,
        a.last_assigned,
        a.current_start,
        COALESCE(cl.closes, 0) AS closes,
        COALESCE(cl.reopens, 0) AS reopens,
        co.hd_mis_handling_basis,
        co.hd_mis_team_attribution,
        co.hd_mis_person_attribution,
        co.hd_mis_reopen_counting,
        COALESCE(co.hd_mis_working_hours, FALSE) AS use_working,
        COALESCE(co.hd_mis_threshold_warn_hours, 24.0) AS th_warn,
        COALESCE(co.hd_mis_threshold_high_hours, 48.0) AS th_high,
        COALESCE(co.hd_mis_threshold_critical_hours, 168.0) AS th_crit
    FROM helpdesk_ticket t
    LEFT JOIN helpdesk_ticket_stage st ON st.id = t.stage_id
    LEFT JOIN res_company co ON co.id = t.company_id
    LEFT JOIN assign a ON a.ticket_id = t.id
    LEFT JOIN first_assign fa ON fa.ticket_id = t.id
    LEFT JOIN closures cl ON cl.ticket_id = t.id
    LEFT JOIN last_close lc ON lc.ticket_id = t.id
    WHERE t.active = TRUE
),
calc AS (
    SELECT b.*,
        CASE WHEN b.is_closed AND b.closed_date IS NOT NULL
             THEN EXTRACT(EPOCH FROM (b.closed_date - b.create_date)) / 3600.0
        END AS resolution_h,
        CASE WHEN NOT b.is_closed
             THEN EXTRACT(EPOCH FROM ((NOW() AT TIME ZONE 'UTC') - b.create_date)) / 3600.0
        END AS open_age_h,
        EXTRACT(EPOCH FROM (
            COALESCE(b.closed_date, NOW() AT TIME ZONE 'UTC') - b.create_date
        )) / 3600.0 AS elapsed_h
    FROM base b
)
SELECT
    c.id AS id,
    c.id AS ticket_id,
    c.number,
    c.name,
    c.partner_id,
    c.company_id,
    c.category_id,
    c.stage_id,
    c.priority,
    c.team_id,
    c.user_id,
    c.create_team_id,
    c.create_user_id,
    c.close_team_id,
    c.close_user_id,
    CASE c.hd_mis_team_attribution
        WHEN 'creation' THEN c.create_team_id
        WHEN 'closing'  THEN c.close_team_id
        ELSE c.team_id
    END AS attributed_team_id,
    CASE c.hd_mis_person_attribution
        WHEN 'assignment' THEN c.create_user_id
        WHEN 'closure'    THEN c.close_user_id
        ELSE c.user_id
    END AS attributed_user_id,
    c.create_date,
    c.closed_date,
    c.mis_first_closed_date AS first_closed_date,
    c.last_assigned AS last_assignment_date,
    c.is_closed,
    (NOT c.is_closed) AS is_open,
    (c.user_id IS NULL AND NOT c.is_closed) AS is_unassigned,
    (c.reassignments > 0) AS is_reassigned,
    (NOT c.is_closed AND COALESCE(c.open_age_h, 0.0) >= c.th_warn) AS past_threshold,
    CASE
        WHEN c.is_closed THEN 'closed'
        WHEN COALESCE(c.open_age_h, 0.0) >= c.th_crit THEN 'critical'
        WHEN COALESCE(c.open_age_h, 0.0) >= c.th_high THEN 'high'
        WHEN COALESCE(c.open_age_h, 0.0) >= c.th_warn THEN 'warn'
        ELSE 'ok'
    END AS age_band,
    1 AS ticket_count,
    1 AS opened_count,
    CASE
        WHEN c.hd_mis_reopen_counting = 'events' THEN c.closes
        WHEN c.is_closed THEN 1
        ELSE 0
    END AS closed_count,
    CASE WHEN c.is_closed THEN 0 ELSE 1 END AS open_count,
    CASE WHEN c.user_id IS NULL AND NOT c.is_closed THEN 1 ELSE 0 END AS unassigned_count,
    c.reassignments AS reassignment_count,
    CASE WHEN c.reassignments > 0 THEN 1 ELSE 0 END AS reassigned_ticket_count,
    c.people AS assignee_count,
    c.closes AS closure_event_count,
    c.reopens AS reopen_count,
    c.resolution_h AS resolution_hours,
    c.resolution_h AS resolution_hours_max,
    CASE WHEN c.mis_first_closed_date IS NOT NULL
         THEN EXTRACT(EPOCH FROM (c.mis_first_closed_date - c.create_date)) / 3600.0
    END AS first_resolution_hours,
    c.open_age_h AS open_age_hours,
    c.open_age_h AS open_age_hours_max,
    c.elapsed_h AS total_elapsed_hours,
    c.elapsed_h AS total_elapsed_hours_max,
    CASE WHEN c.use_working THEN c.assign_working ELSE c.assign_elapsed END
        AS assignment_hours,
    CASE WHEN c.use_working THEN c.assign_max_working ELSE c.assign_max END
        AS max_assignment_hours,
    CASE WHEN c.current_start IS NOT NULL
         THEN EXTRACT(EPOCH FROM ((NOW() AT TIME ZONE 'UTC') - c.current_start)) / 3600.0
    END AS current_assignment_hours,
    CASE c.hd_mis_handling_basis
        WHEN 'assignment' THEN
            CASE WHEN c.use_working THEN c.assign_working ELSE c.assign_elapsed END
        WHEN 'active' THEN c.mis_active_work_hours
        ELSE
            CASE WHEN c.use_working
                 THEN NULLIF(c.mis_resolution_working_hours, 0.0)
                 ELSE c.resolution_h
            END
    END AS handling_hours,
    CASE
        WHEN NOT c.is_closed AND COALESCE(c.open_age_h, 0.0) >= c.th_warn
        THEN c.open_age_h - c.th_warn
        ELSE 0.0
    END AS hours_beyond_threshold
FROM calc c
)
        """
            % self._table
        )
