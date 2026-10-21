"""TDS and GST on hire bills (BR-HIR-10 to BR-HIR-14).

* TDS: after a hire bill posts, the hire items carry the vendor's TDS tax (vendor override, else the
  branch default) and the broker commission item the commission tax. A transporter declaration on file
  for the bill date exempts the hire items. TDS is deducted only above the section's per-payment or
  annual threshold (l10n_in.section.alert limits, aggregated per PAN over the fiscal year as
  l10n_in_withholding does) and the withholding entry is created through l10n_in_withholding's wizard.
* GST: the vendor's GTA status sets the tax on hire items: none for a non-GTA truck owner, 5% reverse
  charge for a GTA vendor (CGST+SGST or IGST by the vendor's state), the billed tax for a GTA charging
  forward.

The default sections are placeholders until the tax advisor confirms them (OP-22).
"""
from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError


class ResCompany(models.Model):
    _inherit = "res.company"

    tms_hire_tds_tax_id = fields.Many2one(
        "account.tax", string="TDS on hire", domain=[("l10n_in_tds_tax_type", "=", "purchase")],
        help="BR-HIR-10: default TDS tax for hire items; a vendor may carry its own.")
    tms_commission_tds_tax_id = fields.Many2one(
        "account.tax", string="TDS on broker commission", domain=[("l10n_in_tds_tax_type", "=", "purchase")],
        help="BR-HIR-13")

    def tms_hire_tds_tax(self, partner=None):
        self.ensure_one()
        tax = self.env["account.tax"]
        if partner:
            # company-dependent: read it in the bill's branch, then up the branch tree
            for company in self | self.parent_ids[::-1]:
                tax = partner.commercial_partner_id.with_company(company).tms_tds_tax_id
                if tax:
                    break
        return tax or self.tms_hire_tds_tax_id or self.parent_id.tms_hire_tds_tax_id \
            or self.root_id.tms_hire_tds_tax_id or self._tms_chart_tax("tds_2_us_393_1_6_i_b")

    def tms_commission_tds_tax(self):
        self.ensure_one()
        return self.tms_commission_tds_tax_id or self.parent_id.tms_commission_tds_tax_id \
            or self.root_id.tms_commission_tds_tax_id or self._tms_chart_tax("tds_2_us_393_1_1_ii")

    def _tms_chart_tax(self, xmlid):
        """OP-22 placeholders: contract payments (393(1) Sl.6) and commission (393(1) Sl.1(ii))."""
        self.ensure_one()
        return self.env["account.chart.template"].with_company(self).ref(xmlid, raise_if_not_found=False) \
            or self.env["account.tax"]


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    tms_hire_tds_tax_id = fields.Many2one(related="company_id.tms_hire_tds_tax_id", readonly=False)
    tms_commission_tds_tax_id = fields.Many2one(related="company_id.tms_commission_tds_tax_id", readonly=False)


class ResPartner(models.Model):
    _inherit = "res.partner"

    tms_tds_tax_id = fields.Many2one(
        "account.tax", string="TDS tax on hire", company_dependent=True,
        domain=[("l10n_in_tds_tax_type", "=", "purchase")],
        help="BR-HIR-10: this vendor's section and rate (e.g. 1% for an individual contractor). "
             "Empty uses the branch default.")


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    tms_tds_section_id = fields.Many2one("l10n_in.section.alert", string="TDS section", copy=False, index=True)
    tms_vendor_pan = fields.Char(related="move_id.commercial_partner_id.l10n_in_pan", string="Vendor PAN")
    tms_tds_declaration_ref = fields.Char(related="move_id.tms_tds_declaration_ref", string="Declaration")
    tms_tds_declaration_exempt = fields.Boolean(related="move_id.tms_tds_declaration_exempt",
                                                string="Paid under declaration")


class AccountMove(models.Model):
    _inherit = "account.move"

    def _tms_after_post(self):
        res = super()._tms_after_post()
        self._tms_apply_hire_tds()
        return res

    def _tms_hire_lines(self):
        self.ensure_one()
        return self.invoice_line_ids.filtered(
            lambda line: line.purchase_line_id.order_id.tms_is_hire_memo and line.purchase_line_id.tms_hire_element)

    def _tms_tds_threshold_crossed(self, section, base):
        """BR-HIR-12: the section's per-payment limit, or its aggregate per PAN for the period."""
        self.ensure_one()
        if not section:
            return True
        if section.is_per_transaction_limit and base > section.per_transaction_limit:
            return True
        if not section.is_aggregate_limit:
            return not section.is_per_transaction_limit
        partner = self.commercial_partner_id
        dates = self.company_id.sudo().compute_fiscalyear_dates(self.date)
        date_from, date_to = dates["date_from"], dates["date_to"]
        if section.aggregate_period == "monthly":
            date_from = self.date.replace(day=1)
            date_to = fields.Date.end_of(self.date, "month")
        domain = [
            ("tms_tds_section_id", "=", section.id),
            ("parent_state", "=", "posted"),
            ("company_id", "child_of", self.company_id.root_id.id),
            ("date", ">=", date_from), ("date", "<=", date_to),
        ]
        if partner.l10n_in_pan:
            domain.append(("move_id.commercial_partner_id.l10n_in_pan", "=", partner.l10n_in_pan))
        else:
            domain.append(("move_id.commercial_partner_id", "=", partner.id))
        total = sum(self.env["account.move.line"].sudo().search(domain).mapped("balance"))
        return total > section.aggregate_limit

    def _tms_apply_hire_tds(self):
        """BR-HIR-10, BR-HIR-12, BR-HIR-13."""
        for move in self.filtered(lambda m: m.move_type == "in_invoice" and m.state == "posted"):
            lines = move._tms_hire_lines()
            if not lines or move.l10n_in_withhold_move_ids.filtered(lambda w: w.state == "posted"):
                continue
            company = move.company_id
            commission = lines.filtered(lambda line: line.purchase_line_id.tms_hire_element == "commission")
            hire = lines - commission
            groups = []
            if hire and not move.tms_tds_declaration_exempt:
                groups.append((company.tms_hire_tds_tax(move.partner_id), hire))
            if commission:
                groups.append((company.tms_commission_tds_tax(), commission))
            withhold_lines = []
            for tax, group in groups:
                if not tax:
                    continue
                group.sudo().write({"tms_tds_section_id": tax.l10n_in_section_id.id})
                base = sum(group.mapped("balance"))
                if base > 0 and move._tms_tds_threshold_crossed(tax.l10n_in_section_id, base):
                    withhold_lines.append(Command.create({"tax_id": tax.id, "base": base}))
            if not withhold_lines:
                continue
            if not company.l10n_in_withholding_account_id:
                account = (company.parent_ids - company).l10n_in_withholding_account_id[-1:]
                if not account:
                    raise UserError(_("Configure the TDS account in the accounting settings (BR-HIR-10)."))
                company.sudo().l10n_in_withholding_account_id = account
            wizard = self.env["l10n_in.withhold.wizard"].sudo().with_company(company).with_context(
                active_model="account.move", active_ids=move.ids).create({"withhold_line_ids": withhold_lines})
            withhold = wizard.action_create_and_post_withhold()
            move.message_post(body=_("TDS deducted: %s", withhold.name))


class PurchaseOrderLine(models.Model):
    _inherit = "purchase.order.line"

    def _tms_hire_gst_taxes(self, default_taxes):
        """BR-HIR-11: GST on a hire item follows the vendor's GTA status."""
        self.ensure_one()
        vendor = self.order_id.partner_id.commercial_partner_id
        status = vendor.tms_gta_status
        if status == "non_gta":
            return self.env["account.tax"]
        if status == "gta_rcm":
            company = self.order_id.company_id
            same_state = not vendor.state_id or vendor.state_id == company.state_id
            xmlid = "sgst_purchase_5_rc" if same_state else "igst_purchase_5_rc"
            tax = self.env["account.chart.template"].with_company(company).ref(xmlid, raise_if_not_found=False)
            return tax or default_taxes
        return default_taxes

    def _prepare_account_move_line(self, move=False):
        res = super()._prepare_account_move_line(move=move)
        if self.order_id.tms_is_hire_memo and self.tms_hire_element:
            taxes = self._tms_hire_gst_taxes(self.taxes_id)
            res["tax_ids"] = [Command.set(taxes.ids)]
        return res
