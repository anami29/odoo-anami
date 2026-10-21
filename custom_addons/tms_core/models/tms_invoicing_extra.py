"""Invoicing follow-ups.

* BR-INV-13: credit days count from the submission date when the customer's terms say so.
* BR-CHG-08: posting an LR's last invoice warns when approved customer charges on it remain unbilled.
* BR-BRN-06: To-Pay freight collected at the delivery branch posts to the inter-branch clearing account
  and settles to the booking branch's invoice.
* BR-PRN-06: the daily customer DSR goes as email body and PDF.
"""
import base64

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError

from .tools import schedule


class AccountMove(models.Model):
    _inherit = "account.move"

    def write(self, vals):
        before = {m.id: m.tms_submission_date for m in self} if "tms_submission_date" in vals else {}
        res = super().write(vals)
        if before:
            self._tms_due_from_submission(before)
        return res

    def _tms_due_from_submission(self, before):
        """BR-INV-13: shift the due dates so the credit days run from the submission date; a corrected
        submission date moves them by the correction only."""
        for move in self.filtered(lambda m: m.move_type == "out_invoice" and m.tms_submission_date
                                  and m.commercial_partner_id.tms_credit_from_submission and m.invoice_date):
            shift = move.tms_submission_date - (before.get(move.id) or move.invoice_date)
            if not shift:
                continue
            terms = move.line_ids.filtered(lambda line: line.display_type == "payment_term")
            for line in terms:
                base = line.date_maturity or move.invoice_date
                line.sudo().with_context(check_move_validity=False).date_maturity = base + shift
            if terms:
                move.sudo().invoice_date_due = max(terms.mapped("date_maturity"))
            move.message_post(body=_("Credit days run from the submission date %s (BR-INV-13).",
                                     move.tms_submission_date))

    def _tms_after_post(self):
        res = super()._tms_after_post()
        for move in self.filtered(lambda m: m.move_type == "out_invoice"):
            lrs = move.line_ids.mapped("tms_consignment_id")
            for lr in lrs.filtered(lambda r: r.freight_uninvoiced <= 0.005):
                unbilled = lr.charge_ids.filtered(lambda c: c.recover_from == "customer" and c.state == "approved"
                                                  and not c.move_line_id)
                if unbilled:
                    msg = _("LR %(lr)s is fully invoiced but approved customer charges remain unbilled: %(c)s "
                            "(BR-CHG-08).", lr=lr.name, c=", ".join(unbilled.mapped("name")))
                    move.message_post(body=msg)
                    schedule(move, self.env.user, _("Unbilled charges on %s", lr.name), msg)
        return res

    def action_tms_collect_at_delivery(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Collect at delivery branch"),
            "res_model": "tms.topay.collect.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_move_id": self.id},
        }


class TmsTopayCollectWizard(models.TransientModel):
    """BR-BRN-06, BR-INV-02: the delivery branch collects a To-Pay invoice of the booking branch."""

    _name = "tms.topay.collect.wizard"
    _description = "Collect To-Pay freight at the delivery branch"

    move_id = fields.Many2one("account.move", required=True, readonly=True)
    delivery_branch_id = fields.Many2one("res.company", compute="_compute_branch")
    journal_id = fields.Many2one("account.journal", required=True,
                                 domain="[('type', 'in', ('cash', 'bank')), ('company_id', 'parent_of', delivery_branch_id)]")
    amount = fields.Monetary(required=True)
    currency_id = fields.Many2one(related="move_id.currency_id")
    date = fields.Date(default=fields.Date.context_today, required=True)

    @api.depends("move_id")
    def _compute_branch(self):
        for wiz in self:
            lr = wiz.move_id.line_ids.mapped("tms_consignment_id")[:1]
            wiz.delivery_branch_id = lr.delivery_branch_id or wiz.move_id.company_id

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        move = self.env["account.move"].browse(res.get("move_id"))
        if move:
            res["amount"] = move.amount_residual
            lr = move.line_ids.mapped("tms_consignment_id")[:1]
            branch = lr.delivery_branch_id or move.company_id
            res["journal_id"] = (branch.tms_cash_journal_id or branch.tms_branch_journal("cash")).id
        return res

    def action_collect(self):
        self.ensure_one()
        move = self.move_id
        if move.move_type != "out_invoice" or move.state != "posted" or move.payment_state in ("paid", "in_payment"):
            raise UserError(_("Only an unpaid posted invoice is collected."))
        if self.amount <= 0 or self.amount > move.amount_residual + 0.005:
            raise UserError(_("The amount must be positive and within the amount due."))
        booking = move.company_id
        delivery = self.delivery_branch_id
        clearing = delivery.tms_interbranch_account_id or booking.tms_interbranch_account_id \
            or booking.root_id.tms_interbranch_account_id
        if not clearing:
            raise UserError(_("Configure the inter-branch clearing account (BR-BRN-06)."))
        cash_account = self.journal_id.default_account_id
        partner = move.commercial_partner_id
        Move = self.env["account.move"].sudo()
        ref = _("To-Pay %s collected at %s", move.name, delivery.name)
        collected = Move.with_company(delivery).create({
            "move_type": "entry", "journal_id": self.journal_id.id, "date": self.date, "ref": ref,
            "line_ids": [
                Command.create({"name": ref, "account_id": cash_account.id, "partner_id": partner.id,
                                "debit": self.amount, "credit": 0.0}),
                Command.create({"name": ref, "account_id": clearing.id, "partner_id": partner.id,
                                "debit": 0.0, "credit": self.amount}),
            ],
        })
        collected.action_post()
        receivable = move.line_ids.filtered(lambda line: line.account_id.account_type == "asset_receivable"
                                            and not line.reconciled)[:1]
        settled = Move.with_company(booking).create({
            "move_type": "entry", "journal_id": booking.tms_branch_journal("general").id, "date": self.date,
            "ref": _("Settlement of %s from %s", move.name, delivery.name),
            "line_ids": [
                Command.create({"name": ref, "account_id": clearing.id, "partner_id": partner.id,
                                "debit": self.amount, "credit": 0.0}),
                Command.create({"name": ref, "account_id": receivable.account_id.id, "partner_id": partner.id,
                                "debit": 0.0, "credit": self.amount}),
            ],
        })
        settled.action_post()
        (receivable | settled.line_ids.filtered(lambda line: line.account_id == receivable.account_id)).reconcile()
        if clearing.reconcile:
            (collected.line_ids | settled.line_ids).filtered(lambda line: line.account_id == clearing).reconcile()
        move.message_post(body=_("Collected at %(b)s: %(c)s, settled by %(s)s (BR-BRN-06).",
                                 b=delivery.name, c=collected.name, s=settled.name))
        move.line_ids.mapped("tms_consignment_id")._tms_refresh_invoiced()
        return {"type": "ir.actions.act_window_close"}


class TmsOrder(models.Model):
    _inherit = "tms.order"

    @api.model
    def _cron_daily_dsr_email(self):
        """BR-PRN-06: the daily DSR email carries the same table as a PDF."""
        template = self.env.ref("tms_core.mail_template_daily_dsr", raise_if_not_found=False)
        report = self.env.ref("tms_core.action_report_customer_dsr", raise_if_not_found=False)
        if not template:
            return
        links = self.env["tms.trip.consignment"].search([
            ("state", "!=", "cancel"), ("outcome", "=", False),
            ("trip_id.state_code", "in", ("started", "in_transit"))])
        parties = links.mapped("consignment_id.billing_partner_id.commercial_partner_id")
        for party in parties.filtered("email"):
            party_links = links.filtered(
                lambda x, p=party: x.consignment_id.billing_partner_id.commercial_partner_id == p)
            mail_id = template.with_context(tms_links=party_links).send_mail(party.id, force_send=False)
            if report and mail_id:
                pdf, _fmt = self.env["ir.actions.report"].sudo()._render_qweb_pdf(
                    report.report_name, res_ids=party.ids, data={"link_ids": party_links.ids})
                attachment = self.env["ir.attachment"].sudo().create({
                    "name": "DSR %s %s.pdf" % (party.name, fields.Date.context_today(self)),
                    "datas": base64.b64encode(pdf),
                    "res_model": "mail.mail", "res_id": mail_id, "mimetype": "application/pdf",
                })
                self.env["mail.mail"].sudo().browse(mail_id).attachment_ids = [Command.link(attachment.id)]
