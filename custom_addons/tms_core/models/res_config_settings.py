from odoo import api, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    tms_operation_mode = fields.Selection(related="company_id.tms_operation_mode", readonly=False)
    tms_mode_locked = fields.Boolean(related="company_id.tms_mode_locked")
    tms_enforce_compliance = fields.Boolean(related="company_id.tms_enforce_compliance", readonly=False)
    tms_enforce_reason = fields.Char(string="Reason for the switch",
                                     help="BR-CFG-06: required when compliance enforcement is switched; logged.")
    tms_strict_branch_accounting = fields.Boolean(related="company_id.tms_strict_branch_accounting", readonly=False)
    tms_hire_tolerance_amount = fields.Monetary(related="company_id.tms_hire_tolerance_amount", readonly=False)
    tms_hire_tolerance_percent = fields.Float(related="company_id.tms_hire_tolerance_percent", readonly=False)
    tms_dsr_cadence_hours = fields.Float(related="company_id.tms_dsr_cadence_hours", readonly=False)
    tms_pod_sla_hours = fields.Float(related="company_id.tms_pod_sla_hours", readonly=False)
    tms_reminder_offsets = fields.Char(related="company_id.tms_reminder_offsets", readonly=False)
    tms_ewaybill_threshold = fields.Monetary(related="company_id.tms_ewaybill_threshold", readonly=False)
    tms_overdue_limit_days = fields.Integer(related="company_id.tms_overdue_limit_days", readonly=False)
    tms_claim_notice_days = fields.Integer(related="company_id.tms_claim_notice_days", readonly=False)
    tms_carrier_liability_limit = fields.Monetary(related="company_id.tms_carrier_liability_limit", readonly=False)
    tms_claim_bm_limit = fields.Monetary(related="company_id.tms_claim_bm_limit", readonly=False)
    tms_charge_self_approval_limit = fields.Monetary(
        related="company_id.tms_charge_self_approval_limit", readonly=False
    )
    tms_godown_demurrage_per_day = fields.Monetary(related="company_id.tms_godown_demurrage_per_day", readonly=False)
    tms_max_advance_percent = fields.Float(related="company_id.tms_max_advance_percent", readonly=False)
    tms_driver_advance_account_id = fields.Many2one(
        related="company_id.tms_driver_advance_account_id", readonly=False
    )
    tms_expense_journal_id = fields.Many2one(related="company_id.tms_expense_journal_id", readonly=False)
    tms_cash_journal_id = fields.Many2one(related="company_id.tms_cash_journal_id", readonly=False)
    tms_bank_journal_id = fields.Many2one(related="company_id.tms_bank_journal_id", readonly=False)
    tms_fuel_card_account_id = fields.Many2one(related="company_id.tms_fuel_card_account_id", readonly=False)
    tms_fastag_account_id = fields.Many2one(related="company_id.tms_fastag_account_id", readonly=False)
    tms_interbranch_account_id = fields.Many2one(related="company_id.tms_interbranch_account_id", readonly=False)
    tms_freight_product_id = fields.Many2one(related="company_id.tms_freight_product_id", readonly=False)
    tms_tds_receivable_account_id = fields.Many2one(
        related="company_id.tms_tds_receivable_account_id", readonly=False
    )

    @api.model_create_multi
    def create(self, vals_list):
        """BR-CFG-06: the reason typed beside the enforcement switch goes to the override log."""
        reason = next((v.get("tms_enforce_reason") for v in vals_list if v.get("tms_enforce_reason")), False)
        if reason:
            self = self.with_context(tms_reason=reason)
        return super().create(vals_list)
