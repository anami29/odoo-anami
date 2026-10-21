from odoo import fields, models

PNL_ACCOUNTS = {
    # key: (field, code, name, account type)
    "prepaid": ("tms_prepaid_account_id", "128900", "Prepaid truck costs (TMS)", "asset_prepayments"),
    "accrued_revenue": ("tms_accrued_revenue_account_id", "128910", "Unbilled freight accrued (TMS)",
                        "asset_current"),
    "accrued_cost": ("tms_accrued_cost_account_id", "218910", "Hire and trip costs accrued (TMS)",
                     "liability_current"),
}


class ResCompany(models.Model):
    _inherit = "res.company"

    # BR-CFG-14: Settings › Transport › Profitability
    tms_capitalisation_threshold = fields.Monetary(
        string="Capitalisation threshold", default=50000.0,
        help="BR-PNL-11: a truck repair at or above this amount is spread over its useful life. Zero disables.")
    tms_revenue_dating = fields.Selection(
        [("delivery", "Delivery date"), ("invoice", "Invoice date")], string="Revenue dating basis",
        default="delivery", required=True,
        help="OP-14, BR-PNL-23: the date that puts a trip's revenue, direct cost and standard charge in a period.")
    tms_empty_return_in_km = fields.Boolean(
        string="Empty return counts in trip km", default=True,
        help="#18.3: when off, the empty return km entered at close are left out of the charged km.")
    tms_rate_review_months = fields.Integer(string="Rate review interval (months)", default=3)
    tms_review_flag_months = fields.Integer(
        string="Review flag after adverse months", default=3,
        help="BR-PNL-21: consecutive closed months of adverse running spending variance that flag a truck.")
    tms_margin_target_pct = fields.Float(string="Trip margin target %", help="KPI-13: the kickoff target.")
    tms_prepaid_account_id = fields.Many2one("account.account", string="Prepaid truck costs account")
    tms_accrued_revenue_account_id = fields.Many2one("account.account", string="Accrued freight account")
    tms_accrued_cost_account_id = fields.Many2one("account.account", string="Accrued trip costs account")
    tms_head_office = fields.Boolean(
        string="Head office", help="BR-PNL-32: overhead of this company is head-office cost in the company P&L.")
    tms_analytic_account_id = fields.Many2one("account.analytic.account", string="Branch analytic account",
                                              readonly=True, copy=False)

    def _tms_analytic_account(self):
        """BR-LED-01: each branch is an account in plan "Branch"."""
        self.ensure_one()
        if not self.tms_analytic_account_id:
            plan = self.env.ref("tms_profitability.analytic_plan_branch")
            self.sudo().tms_analytic_account_id = self.env["account.analytic.account"].sudo().create({
                "name": self.name, "code": self.tms_branch_code, "plan_id": plan.id, "company_id": False})
        return self.tms_analytic_account_id

    def _tms_pnl_account(self, key):
        """Profitability balance-sheet accounts, created on first use when not configured."""
        self.ensure_one()
        field, code, name, account_type = PNL_ACCOUNTS[key]
        account = self[field]
        if account:
            return account
        Account = self.env["account.account"].sudo().with_company(self)
        account = Account.search(Account._check_company_domain(self) + [("code", "=", code)], limit=1)
        if not account:
            account = Account.create({"code": code, "name": name, "account_type": account_type,
                                      "company_ids": [(6, 0, self.root_id.ids)]})
        self.sudo()[field] = account
        return account


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    tms_capitalisation_threshold = fields.Monetary(related="company_id.tms_capitalisation_threshold",
                                                   readonly=False)
    tms_revenue_dating = fields.Selection(related="company_id.tms_revenue_dating", readonly=False)
    tms_empty_return_in_km = fields.Boolean(related="company_id.tms_empty_return_in_km", readonly=False)
    tms_rate_review_months = fields.Integer(related="company_id.tms_rate_review_months", readonly=False)
    tms_review_flag_months = fields.Integer(related="company_id.tms_review_flag_months", readonly=False)
    tms_margin_target_pct = fields.Float(related="company_id.tms_margin_target_pct", readonly=False)
    tms_prepaid_account_id = fields.Many2one(related="company_id.tms_prepaid_account_id", readonly=False)
    tms_accrued_revenue_account_id = fields.Many2one(related="company_id.tms_accrued_revenue_account_id",
                                                     readonly=False)
    tms_accrued_cost_account_id = fields.Many2one(related="company_id.tms_accrued_cost_account_id",
                                                  readonly=False)
    tms_head_office = fields.Boolean(related="company_id.tms_head_office", readonly=False)
