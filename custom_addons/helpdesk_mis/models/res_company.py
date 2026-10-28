# Copyright 2026 RLFB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""The ten reporting decisions the BRD leaves open (BRD section 18).

Each one is a setting rather than a hard-coded choice. The MIS SQL views read
these columns directly, so changing a setting changes every KPI on the next
query - no upgrade, no view rebuild, no recomputation.
"""

from odoo import api, fields, models

# BRD section 8.2 leaves the ageing thresholds to be agreed. These are the
# values the document itself suggests as examples, used until someone changes
# them in Settings.
DEFAULT_WARN_HOURS = 24.0
DEFAULT_HIGH_HOURS = 48.0
DEFAULT_CRITICAL_HOURS = 168.0


class ResCompany(models.Model):
    _inherit = "res.company"

    # --- 18.1 handling time ---------------------------------------------
    hd_mis_handling_basis = fields.Selection(
        selection=[
            ("elapsed", "Elapsed resolution time (creation to closure)"),
            ("assignment", "Assignment duration (time the ticket was held)"),
            ("active", "Active work time (needs a work-time source)"),
        ],
        default="elapsed",
        required=True,
        string="Handling time basis",
        help="Which measure the 'Handling Time' KPI reports. Elapsed includes "
        "waiting time; assignment counts only time a person or team held the "
        "ticket; active work needs timesheets and reports nothing without them.",
    )
    # --- 18.2 team attribution -------------------------------------------
    hd_mis_team_attribution = fields.Selection(
        selection=[
            ("current", "Team that holds the ticket now"),
            ("creation", "Team the ticket was first raised with"),
            ("closing", "Team that closed the ticket"),
            ("all", "Every team that handled it (assignment report)"),
        ],
        default="current",
        required=True,
        string="Team attribution",
        help="Which team a ticket counts towards in team summaries. All four "
        "are always available as separate columns; this picks the default.",
    )
    # --- 18.3 person attribution -----------------------------------------
    hd_mis_person_attribution = fields.Selection(
        selection=[
            ("current", "Person the ticket is assigned to now"),
            ("assignment", "Person it was first assigned to"),
            ("closure", "Person who closed it"),
            ("interval", "Every person who held it (assignment report)"),
        ],
        default="current",
        required=True,
        string="Person attribution",
    )
    # --- 18.4 reopened tickets -------------------------------------------
    hd_mis_reopen_counting = fields.Selection(
        selection=[
            ("distinct", "Count each ticket once, however often it closes"),
            ("events", "Count every closure event"),
        ],
        default="distinct",
        required=True,
        string="Closure counting",
        help="Whether 'Tickets Closed' counts distinct tickets or closure "
        "events. Both numbers exist; this picks what the KPI shows.",
    )
    # --- 18.5 / 18.6 date basis ------------------------------------------
    hd_mis_date_basis = fields.Selection(
        selection=[
            ("create", "Creation date"),
            ("close", "Closure date"),
            ("open", "Currently open, ignore the period"),
        ],
        default="create",
        required=True,
        string="Default date basis",
        help="Which date the period filter applies to when a dashboard opens. "
        "The user can change it on the dashboard at any time.",
    )
    hd_mis_open_date_basis = fields.Selection(
        selection=[
            ("create", "Creation date"),
            ("last_assignment", "Last assignment date"),
        ],
        default="create",
        required=True,
        string="Open dashboard date basis",
    )
    # --- 18.7 working hours ----------------------------------------------
    hd_mis_working_hours = fields.Boolean(
        string="Measure durations in working hours",
        default=False,
        help="Off: durations are plain elapsed time, including nights, "
        "weekends and holidays. On: durations count only hours inside the "
        "working-hours calendar below.",
    )
    hd_mis_calendar_id = fields.Many2one(
        "resource.calendar",
        string="Reporting calendar",
        help="The working-hours calendar used when durations are measured in "
        "working hours. Defaults to the company calendar.",
    )
    # --- 18.8 ageing thresholds ------------------------------------------
    hd_mis_threshold_warn_hours = fields.Float(
        string="Ageing: warning (hours)", default=DEFAULT_WARN_HOURS
    )
    hd_mis_threshold_high_hours = fields.Float(
        string="Ageing: high (hours)", default=DEFAULT_HIGH_HOURS
    )
    hd_mis_threshold_critical_hours = fields.Float(
        string="Ageing: critical (hours)", default=DEFAULT_CRITICAL_HOURS
    )
    # --- 18.9 timezone ----------------------------------------------------
    hd_mis_timezone = fields.Selection(
        selection="_tz_get",
        string="Reporting timezone",
        help="The timezone that governs day, week and month boundaries in MIS "
        "reports. Empty means the reading user's own timezone.",
    )

    @api.model
    def _tz_get(self):
        return self.env["res.partner"]._tz_get()

    def _hd_mis_calendar(self):
        """The calendar working-hour durations are measured against."""
        self.ensure_one()
        return self.hd_mis_calendar_id or self.resource_calendar_id
