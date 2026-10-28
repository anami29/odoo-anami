# Copyright 2026 RLFB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    hd_mis_handling_basis = fields.Selection(
        related="company_id.hd_mis_handling_basis", readonly=False
    )
    hd_mis_team_attribution = fields.Selection(
        related="company_id.hd_mis_team_attribution", readonly=False
    )
    hd_mis_person_attribution = fields.Selection(
        related="company_id.hd_mis_person_attribution", readonly=False
    )
    hd_mis_reopen_counting = fields.Selection(
        related="company_id.hd_mis_reopen_counting", readonly=False
    )
    hd_mis_date_basis = fields.Selection(
        related="company_id.hd_mis_date_basis", readonly=False
    )
    hd_mis_open_date_basis = fields.Selection(
        related="company_id.hd_mis_open_date_basis", readonly=False
    )
    hd_mis_working_hours = fields.Boolean(
        related="company_id.hd_mis_working_hours", readonly=False
    )
    hd_mis_calendar_id = fields.Many2one(
        related="company_id.hd_mis_calendar_id", readonly=False
    )
    hd_mis_threshold_warn_hours = fields.Float(
        related="company_id.hd_mis_threshold_warn_hours", readonly=False
    )
    hd_mis_threshold_high_hours = fields.Float(
        related="company_id.hd_mis_threshold_high_hours", readonly=False
    )
    hd_mis_threshold_critical_hours = fields.Float(
        related="company_id.hd_mis_threshold_critical_hours", readonly=False
    )
    hd_mis_timezone = fields.Selection(
        related="company_id.hd_mis_timezone", readonly=False
    )
