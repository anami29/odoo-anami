"""Strict branch accounting (BR-CFG-07, BR-BRN-04).

With ``tms_strict_branch_accounting`` on, TMS invoices, bills and entries post in the document branch's
own journals. Odoo 18 branches share their parent's journals by default, so the branch's sale, purchase
and miscellaneous journals are created on first use as copies of the parent's; cash and bank journals
must be set per branch (or fall back to the parent's, since they hold the bank account). Posting a TMS
document of a branch in another company's journal is refused.
"""
from odoo import _, models
from odoo.exceptions import UserError

AUTO_TYPES = {"sale": "S", "purchase": "P", "general": "M"}


class ResCompany(models.Model):
    _inherit = "res.company"

    def tms_branch_journal(self, journal_type):
        """BR-BRN-04: with strict branch accounting, post in the branch's own journal."""
        self.ensure_one()
        Journal = self.env["account.journal"].sudo()
        journal = Journal.search([("company_id", "=", self.id), ("type", "=", journal_type)], limit=1)
        if journal:
            return journal
        if self.parent_id and self.tms_strict_branch_accounting and journal_type in AUTO_TYPES:
            return self._tms_create_branch_journal(journal_type)
        if self.parent_id:
            return self.parent_id.tms_branch_journal(journal_type)
        return journal

    def _tms_create_branch_journal(self, journal_type):
        self.ensure_one()
        Journal = self.env["account.journal"].sudo()
        source = Journal.search([("company_id", "in", self.parent_ids.ids), ("type", "=", journal_type)],
                                order="company_id desc", limit=1)
        if not source:
            raise UserError(_("No %(type)s journal to copy for branch %(branch)s (BR-BRN-04).",
                              type=journal_type, branch=self.name))
        prefix = (self.tms_branch_code or str(self.id))[:4].upper()
        code = prefix + AUTO_TYPES[journal_type]
        n = 1
        while Journal.search_count([("code", "=", code), ("company_id", "in", (self.root_id | self.child_ids
                                                                               | self.parent_ids).ids)]):
            code = (prefix[:3] + AUTO_TYPES[journal_type] + str(n))[:5]
            n += 1
        vals = {
            "company_id": self.id,
            "code": code,
            "name": "%s (%s)" % (source.name, self.tms_branch_code or self.name),
        }
        for fname in ("default_account_id", "suspense_account_id", "profit_account_id", "loss_account_id"):
            if fname in source._fields and source[fname]:
                vals[fname] = source[fname].id
        return source.with_company(self).copy(vals)


class AccountMove(models.Model):
    _inherit = "account.move"

    def _tms_document_company(self):
        """The branch a TMS posting belongs to: the LR's booking branch for customer documents (BR-INV-02),
        the hire memo's or trip's branch for vendor bills and entries."""
        self.ensure_one()
        lines = self.line_ids
        lrs = lines.mapped("tms_consignment_id.company_id")
        trips = lines.mapped("tms_trip_id.company_id")
        memos = lines.mapped("purchase_line_id.order_id").filtered("tms_is_hire_memo").mapped("company_id")
        if self.move_type in ("out_invoice", "out_refund"):
            return (lrs or trips)[:1]
        return (memos or trips or lrs)[:1]

    def _tms_check_post(self):
        res = super()._tms_check_post()
        for move in self:
            company = move._tms_document_company()
            if not company or not (company.parent_id and company.tms_strict_branch_accounting):
                continue
            if move.journal_id.company_id != company:
                raise UserError(_(
                    "Strict branch accounting: %(doc)s belongs to %(branch)s and must post in its journal, "
                    "not %(journal)s (BR-BRN-04).",
                    doc=move.display_name, branch=company.name, journal=move.journal_id.display_name))
        return res


class PurchaseOrder(models.Model):
    _inherit = "purchase.order"

    def _prepare_invoice(self):
        res = super()._prepare_invoice()
        if self.tms_is_hire_memo and self.company_id.parent_id and self.company_id.tms_strict_branch_accounting:
            res["journal_id"] = self.company_id.tms_branch_journal("purchase").id
        return res
