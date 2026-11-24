from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

FREQUENCY_MONTHS = {"monthly": 1, "quarterly": 3, "half_yearly": 6, "yearly": 12}


class PurchaseOrder(models.Model):
    _name = "purchase.order"
    _inherit = ["purchase.order", "tms.document.type.mixin"]

    def _tms_doc_applies_to(self):
        return "purchase"

    def _tms_doc_is_draft(self):
        self.ensure_one()
        return self.state in ("draft", "sent")

    def _tms_doc_links(self):
        self.ensure_one()
        links = []
        if self.tms_is_hire_memo:
            links.append(_("a hire memo"))
        if self.tms_trip_id:
            links.append(_("a trip"))
        if self.order_line.filtered("tms_estimate_line_id"):
            links.append(_("an estimate-line link"))
        return links

    @api.model
    def _tms_doc_vals_flow(self, vals):
        # hire memos and estimate-line purchases are transport documents whatever the personal default
        if vals.get("tms_is_hire_memo") or vals.get("tms_trip_id") or self.env.context.get("default_tms_trip_id"):
            return True
        return any(line.get("tms_estimate_line_id") for line in self._tms_doc_line_vals(vals, "order_line"))

    @api.model_create_multi
    def create(self, vals_list):
        explicit = self._tms_doc_prepare_vals(vals_list)
        Type = self.env["tms.document.type"]
        for vals in vals_list:
            dtype = Type.browse(vals.get("tms_document_type_id"))
            if dtype.sequence_id and vals.get("name", "New") == "New":
                vals["name"] = dtype._tms_next_number()
        orders = super().create(vals_list)
        orders._tms_doc_after_create(explicit)
        return orders

    def write(self, vals):
        self._tms_doc_check_write(vals)
        return super().write(vals)

    @api.constrains("tms_document_type_id")
    def _check_tms_document_type(self):
        self._tms_doc_check_type()
        for order in self:
            order.order_line._tms_doc_check_line()

    @api.constrains("tms_is_hire_memo", "tms_trip_id", "tms_document_type_id")
    def _check_tms_doc_hire(self):
        for order in self.filtered(lambda o: o.tms_document_behaviour == "standard"):
            if order.tms_is_hire_memo or order.tms_trip_id:
                raise ValidationError(_("A Standard document carries no transport link: %s.",
                                        _("a hire memo") if order.tms_is_hire_memo else _("a trip")))

    def _prepare_invoice(self):
        res = super()._prepare_invoice()
        dtype = self.tms_document_type_id
        bill_type = dtype.invoice_type_id or self.env["tms.document.type"]._tms_default_for(
            "in_invoice", transport=dtype.behaviour != "standard")
        res["tms_document_type_id"] = bill_type.id
        if bill_type.journal_id:
            res["journal_id"] = bill_type.journal_id.id
        if self.requisition_id and dtype.is_amc:
            res["tms_amc_requisition_id"] = self.requisition_id.id
        return res

    # BR-DOC-13: a type's approval limit, approved by its approvers; otherwise Odoo's purchase approval
    def _approval_allowed(self):
        self.ensure_one()
        dtype = self.tms_document_type_id
        if dtype.approval_limit:
            amount = self.currency_id._convert(self.amount_untaxed, dtype.currency_id, self.company_id,
                                               self.date_order or fields.Date.today())
            return amount <= dtype.approval_limit or dtype._tms_user_approves()
        # the Branch Manager approves purchases above Odoo's purchase approval amount
        return super()._approval_allowed() or self.env.user.has_group("tms_core.group_branch_manager")

    def button_approve(self, force=False):
        for order in self.filtered(lambda o: o.state == "to approve"):
            user = self.env.user
            dtype = order.tms_document_type_id
            allowed = user.has_group("purchase.group_purchase_manager") or (
                dtype._tms_user_approves() if dtype else user.has_group("tms_core.group_branch_manager"))
            if not allowed and not self.env.su:
                raise UserError(_("You may not approve %s.", order.name))
        return super().button_approve(force=force)


class PurchaseOrderLine(models.Model):
    _inherit = "purchase.order.line"

    def _tms_doc_check_line(self):
        for line in self.filtered(lambda line: not line.display_type):
            dtype = line.order_id.tms_document_type_id
            if dtype.behaviour == "standard" and line.tms_estimate_line_id:
                raise ValidationError(_("A Standard document carries no transport link: an estimate-line link."))
            self.env["tms.document.type.mixin"]._tms_doc_check_product(dtype, line.product_id,
                                                                        line.name or line.product_id.name)

    @api.constrains("product_id", "tms_estimate_line_id")
    def _check_tms_doc_line(self):
        self._tms_doc_check_line()


class PurchaseRequisition(models.Model):
    """BR-DOC-16: an office-asset AMC is a blanket order of type AMC purchase, billed by period on branch
    overhead, with a renewal reminder 30 days before its end."""

    _inherit = "purchase.requisition"

    tms_document_type_id = fields.Many2one("tms.document.type", string="Document Type",
                                           domain="[('applies_to', '=', 'purchase'), ('is_amc', '=', True)]")
    tms_is_amc = fields.Boolean(related="tms_document_type_id.is_amc")
    tms_amc_assets = fields.Text(string="Assets covered")
    tms_amc_value = fields.Monetary(string="Contract value", currency_field="currency_id")
    tms_amc_frequency = fields.Selection(
        [("monthly", "Monthly"), ("quarterly", "Quarterly"), ("half_yearly", "Half-yearly"),
         ("yearly", "Yearly")], string="Billing frequency", default="quarterly")
    tms_amc_bill_ids = fields.One2many("account.move", "tms_amc_requisition_id", string="AMC bills")

    @api.model_create_multi
    def create(self, vals_list):
        recs = super().create(vals_list)
        recs._tms_amc_schedule_renewal()
        return recs

    def write(self, vals):
        res = super().write(vals)
        if {"date_end", "tms_document_type_id"} & set(vals):
            self._tms_amc_schedule_renewal()
        return res

    def _tms_amc_schedule_renewal(self):
        summary = _("Renew the maintenance contract")
        for req in self.filtered(lambda r: r.tms_is_amc and r.date_end):
            req.activity_ids.filtered(lambda a: a.summary == summary).unlink()
            req.activity_schedule("mail.mail_activity_data_todo", date_deadline=req.date_end - relativedelta(days=30),
                                  user_id=(req.user_id or self.env.user).id, summary=summary,
                                  note=_("%(n)s ends on %(d)s.", n=req.name, d=req.date_end))

    def _tms_amc_next_period(self):
        self.ensure_one()
        months = FREQUENCY_MONTHS[self.tms_amc_frequency or "quarterly"]
        billed = self.tms_amc_bill_ids.filtered(lambda m: m.state != "cancel")
        start = self.date_start + relativedelta(months=months * len(billed))
        end = start + relativedelta(months=months, days=-1)
        if self.date_end and end > self.date_end:
            end = self.date_end
        return start, end

    def action_tms_amc_bill(self):
        """The next period's bill: a Standard bill on the branch's analytic account with its cover period."""
        self.ensure_one()
        if not self.tms_is_amc:
            raise UserError(_("%s is not a maintenance contract.", self.name))
        if not (self.vendor_id and self.date_start and self.date_end and self.tms_amc_value):
            raise UserError(_("Give the vendor, the cover period and the contract value first."))
        start, end = self._tms_amc_next_period()
        if start > self.date_end:
            raise UserError(_("Every period of %s is billed.", self.name))
        total_months = ((self.date_end.year - self.date_start.year) * 12 + self.date_end.month
                        - self.date_start.month + 1)
        months = (end.year - start.year) * 12 + end.month - start.month + 1
        amount = self.currency_id.round(self.tms_amc_value * months / total_months)
        dtype = self.tms_document_type_id
        bill_type = dtype.invoice_type_id or self.env["tms.document.type"]._tms_default_for("in_invoice")
        company = self.company_id
        line = self.line_ids[:1]
        account = (line.product_id.property_account_expense_id
                   or line.product_id.categ_id.property_account_expense_categ_id
                   or self.env["account.account"].search([("company_ids", "in", company.id),
                                                          ("account_type", "=", "expense")], limit=1))
        analytic = company._tms_analytic_account()
        bill = self.env["account.move"].with_company(company).create({
            "move_type": "in_invoice",
            "partner_id": self.vendor_id.id,
            "invoice_date": fields.Date.context_today(self),
            "tms_document_type_id": bill_type.id,
            "tms_amc_requisition_id": self.id,
            "ref": "%s %s–%s" % (self.name, start.strftime("%d/%m/%Y"), end.strftime("%d/%m/%Y")),
            "invoice_line_ids": [(0, 0, {
                "name": _("Maintenance of %(a)s, %(f)s to %(t)s", a=self.tms_amc_assets or self.name,
                          f=start.strftime("%d/%m/%Y"), t=end.strftime("%d/%m/%Y")),
                "product_id": line.product_id.id or False,
                "account_id": account.id,
                "quantity": 1.0,
                "price_unit": amount,
                "tax_ids": [(6, 0, [])],
                "analytic_distribution": {str(analytic.id): 100.0},
                "tms_cover_from": start,
                "tms_cover_to": end,
            })],
        })
        return {"type": "ir.actions.act_window", "res_model": "account.move", "res_id": bill.id,
                "view_mode": "form"}
