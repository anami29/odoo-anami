from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

MODES = [("hybrid", "Hybrid"), ("broker", "Broker"), ("consumer", "Consumer")]
GTA_OPTIONS = [
    ("fcm5", "Forward charge at 5%"),
    ("fcm18", "Forward charge at 18%"),
    ("rcm", "Reverse charge"),
]


class ResCompany(models.Model):
    _inherit = "res.company"

    # ---- BR-CFG-01..04 operating mode
    tms_operation_mode = fields.Selection(MODES, default="hybrid", required=True, string="Operating mode")
    tms_mode_locked = fields.Boolean(
        compute="_compute_tms_mode_locked",
        help="The mode locks once a confirmed LR or trip exists (BR-CFG-04).",
        string="Mode locked")
    tms_mode_unlocked_until_change = fields.Boolean(
        help="Set by the TMS Administrator's logged unlock; cleared by the next mode change.",
        string="Unlocked until the next change")
    # ---- BR-CFG-06, 07
    tms_enforce_compliance = fields.Boolean(default=True, string="Enforce compliance gate")
    tms_strict_branch_accounting = fields.Boolean(default=True, string="Strict branch accounting")
    # ---- BR-CFG-08 hire variance tolerance
    tms_hire_tolerance_amount = fields.Monetary(default=250.0, currency_field="currency_id", string="Hire tolerance (₹)")
    tms_hire_tolerance_percent = fields.Float(default=0.5, string="Hire tolerance (%)")
    # ---- BR-CFG-09
    tms_dsr_cadence_hours = fields.Float(default=6.0, string="DSR cadence (hours)")
    tms_pod_sla_hours = fields.Float(default=48.0, string="POD SLA (hours)")
    tms_reminder_offsets = fields.Char(default="30,15,7", string="Document reminder offsets (days)")
    # ---- BR-CFG-10 GTA option per financial year
    tms_gta_option_ids = fields.One2many("tms.gta.option", "company_id", string="GTA GST options")
    # ---- BR-MD-12 LR numbering per branch
    tms_branch_code = fields.Char(help="Short branch code used as the LR and contract prefix.", string="Branch code")
    tms_allow_manual_lr = fields.Boolean(string="Allow manual LR numbers")
    tms_lr_sequence_id = fields.Many2one("ir.sequence", copy=False, string="LR number series")
    # ---- BR-EWB-03
    tms_ewaybill_threshold = fields.Monetary(default=50000.0, currency_field="currency_id", string="E-way bill threshold")
    # ---- BR-CFG-16 credit and claims
    tms_overdue_limit_days = fields.Integer(default=30, string="Overdue limit (days)")
    tms_claim_notice_days = fields.Integer(default=30, string="Claim notice period (days)")
    tms_carrier_liability_limit = fields.Monetary(currency_field="currency_id", string="Carrier liability limit")
    tms_claim_bm_limit = fields.Monetary(default=25000.0, currency_field="currency_id", string="Branch Manager claim limit")
    # ---- BR-CHG-04
    tms_charge_self_approval_limit = fields.Monetary(default=5000.0, currency_field="currency_id", string="Charge self-approval limit")
    # ---- BR-POD-09
    tms_godown_demurrage_per_day = fields.Monetary(currency_field="currency_id", string="Godown demurrage per day")
    # ---- BR-HIR-04
    tms_max_advance_percent = fields.Float(default=80.0, string="Maximum hire advance (%)")
    # ---- accounts used by trip money
    tms_driver_advance_account_id = fields.Many2one("account.account", string="Driver advance account")
    tms_expense_journal_id = fields.Many2one("account.journal", string="Trip expense journal")
    tms_cash_journal_id = fields.Many2one("account.journal", string="Cash journal")
    tms_bank_journal_id = fields.Many2one("account.journal", string="Bank journal")
    tms_fuel_card_account_id = fields.Many2one("account.account", string="Fuel card provider account")
    tms_fastag_account_id = fields.Many2one("account.account", string="FASTag provider account")
    tms_interbranch_account_id = fields.Many2one("account.account", string="Inter-branch clearing account")
    tms_freight_product_id = fields.Many2one("product.product", string="Freight product")
    tms_tds_receivable_account_id = fields.Many2one("account.account", string="Customer TDS receivable")
    tms_second_language_id = fields.Many2one("res.lang", string="Second print language",
                                             context={"active_test": False},
                                             help="BR-PRN-08: Marathi and Hindi labels are built in.")

    def _compute_tms_mode_locked(self):
        for company in self:
            company.tms_mode_locked = bool(
                not company.tms_mode_unlocked_until_change
                and (
                    self.env["tms.consignment"].sudo().search_count(
                        [("company_id", "=", company.id), ("state", "not in", ("draft", "cancel"))], limit=1
                    )
                    or self.env["tms.order"].sudo().search_count(
                        [("company_id", "=", company.id), ("state_code", "not in", ("draft", "cancel"))], limit=1
                    )
                )
            )

    def write(self, vals):
        if "tms_operation_mode" in vals:
            self.invalidate_recordset(["tms_mode_locked"])
            for company in self:
                if company.tms_operation_mode == vals["tms_operation_mode"]:
                    continue
                if company.tms_mode_locked:
                    raise UserError(
                        _("The operating mode is locked because confirmed LRs or trips exist. "
                          "The TMS Administrator must unlock it with a reason first (BR-CFG-04).")
                    )
                if vals["tms_operation_mode"] == "consumer":
                    raise UserError(_("Consumer mode is delivered in phase 2 (BR-CFG-05)."))
            vals.setdefault("tms_mode_unlocked_until_change", False)
        if "tms_enforce_compliance" in vals and not self.env.su:
            if not self.env.user.has_group("tms_core.group_tms_admin"):
                raise UserError(_("Only the TMS Administrator changes compliance enforcement (BR-CFG-06)."))
            if not self.env.context.get("tms_reason") and any(
                    c.tms_enforce_compliance != bool(vals["tms_enforce_compliance"]) for c in self):
                raise UserError(_("Give the reason for switching compliance enforcement (BR-CFG-06)."))
        old = {c.id: c.tms_enforce_compliance for c in self}
        res = super().write(vals)
        if "tms_operation_mode" in vals:
            self._tms_sync_mode_menus()
        if "tms_enforce_compliance" in vals:
            for company in self:
                if old[company.id] != company.tms_enforce_compliance:
                    self.env["tms.override.log"].log(
                        "enforcement",
                        company,
                        _("Compliance enforcement switched %s", "on" if company.tms_enforce_compliance else "off"),
                        reason=self.env.context.get("tms_reason") or _("Changed by the system"),
                    )
        return res

    def _tms_sync_mode_menus(self):
        """BR-CFG-02: owned-fleet menus show while any branch runs Hybrid mode."""
        staff = self.env.ref("tms_core.group_tms_staff", raise_if_not_found=False)
        menus = self.env.ref("tms_core.group_owned_fleet_menus", raise_if_not_found=False)
        if not staff or not menus:
            return
        hybrid = bool(self.sudo().search_count([("tms_operation_mode", "=", "hybrid")]))
        if hybrid and menus not in staff.implied_ids:
            staff.sudo().write({"implied_ids": [(4, menus.id)]})
        elif not hybrid and menus in staff.implied_ids:
            staff.sudo().write({"implied_ids": [(3, menus.id)]})
            menus.sudo().write({"users": [(5, 0, 0)]})

    def action_open_unlock_wizard(self):
        return self.env["tms.reason.wizard"].open_for(self, "action_tms_unlock_mode", _("Unlock operating mode"))

    def action_tms_unlock_mode(self, reason=None):
        self.ensure_one()
        reason = reason or self.env.context.get("tms_reason")
        if not self.env.user.has_group("tms_core.group_tms_admin"):
            raise UserError(_("Only the TMS Administrator unlocks the operating mode."))
        self.sudo().tms_mode_unlocked_until_change = True
        self.env["tms.override.log"].log("mode_unlock", self, _("Operating mode unlocked"), reason=reason)

    # ------------------------------------------------------------------
    # helpers used across the TMS
    # ------------------------------------------------------------------
    def tms_get_gta_option(self, date=None):
        self.ensure_one()
        date = date or fields.Date.context_today(self)
        opt = self.tms_gta_option_ids.filtered(lambda o: o.date_from <= date <= o.date_to)[:1]
        if not opt and self.parent_id:
            return self.parent_id.tms_get_gta_option(date)
        return opt.option if opt else "rcm"

    def tms_hire_tolerance(self, amount):
        """BR-HIR-07: the lower of the amount and the percent of |amount|."""
        self.ensure_one()
        return min(self.tms_hire_tolerance_amount, abs(amount) * self.tms_hire_tolerance_percent / 100.0)

    def tms_reminder_offset_list(self):
        self.ensure_one()
        out = []
        for part in (self.tms_reminder_offsets or "").split(","):
            part = part.strip()
            if part.isdigit():
                out.append(int(part))
        return sorted(set(out), reverse=True) or [30, 15, 7]

    def _tms_lr_sequence(self):
        self.ensure_one()
        if not self.tms_lr_sequence_id:
            code = (self.tms_branch_code or self.name[:3] or "BR").upper()
            seq = self.env["ir.sequence"].sudo().create(
                {
                    "name": _("LR %s", self.name),
                    "code": "tms.consignment.%s" % self.id,
                    "prefix": "%s/LR/%%(year)s/" % code,
                    "padding": 5,
                    "company_id": self.id,
                    "use_date_range": True,
                }
            )
            self.sudo().tms_lr_sequence_id = seq
        return self.tms_lr_sequence_id

    # tms_branch_journal: see tms_branch.py (BR-BRN-04)


class TmsGtaOption(models.Model):
    _name = "tms.gta.option"
    _description = "GTA GST option per financial year"
    _order = "date_from desc"

    company_id = fields.Many2one("res.company", required=True, ondelete="cascade", default=lambda s: s.env.company)
    date_from = fields.Date(required=True)
    date_to = fields.Date(required=True)
    option = fields.Selection(GTA_OPTIONS, required=True, default="fcm5")

    @api.constrains("date_from", "date_to", "company_id")
    def _check_dates(self):
        for rec in self:
            if rec.date_to < rec.date_from:
                raise ValidationError(_("The financial year ends before it starts."))
            overlap = self.search_count(
                [
                    ("id", "!=", rec.id),
                    ("company_id", "=", rec.company_id.id),
                    ("date_from", "<=", rec.date_to),
                    ("date_to", ">=", rec.date_from),
                ]
            )
            if overlap:
                raise ValidationError(_("GTA options of one company cannot overlap."))
