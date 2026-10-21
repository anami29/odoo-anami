from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .tms_category import COST_CLASSES

LINE_KINDS = [
    ("freight", "Freight"),
    ("charge", "Customer charge"),
    ("rate_correction", "Rate correction"),
    ("claim", "Claim settlement"),
    ("deduction", "Customer deduction"),
    ("dead_freight", "Dead freight"),
    ("supplementary", "Supplementary (escalation)"),
    ("hire", "Hire"),
    ("expense", "Trip expense"),
    ("recovery", "Recovery"),
    ("accrual", "Accrual"),
]


class AccountMove(models.Model):
    _inherit = "account.move"

    tms_consignment_ids = fields.Many2many("tms.consignment", compute="_compute_tms_links", string="LRs")
    tms_trip_ids = fields.Many2many("tms.order", compute="_compute_tms_links", string="Trips")
    tms_match_ids = fields.One2many("tms.vendor.bill.match", "move_id", string="Three-way match")
    tms_hire_memo_ids = fields.Many2many("purchase.order", compute="_compute_tms_links", string="Hire memos")
    tms_claim_id = fields.Many2one("tms.damage.claim", readonly=True, copy=False, string="Claim")
    tms_credit_reason = fields.Selection(
        [("rate_correction", "Rate correction"), ("claim", "Claim settlement"), ("deduction", "Customer deduction"),
         ("other", "Other")],
        string="Credit note reason",
    )
    tms_is_accrual = fields.Boolean(readonly=True, copy=False, string="Accrual entry")
    # BR-INV-13
    tms_submission_date = fields.Date(string="Submitted on")
    tms_pod_copies_sent = fields.Boolean(string="POD copies sent")
    tms_acknowledgement = fields.Char(string="Acknowledgement / GRN reference")
    # BR-INV-12
    tms_customer_tds_amount = fields.Monetary(string="TDS deducted by customer", copy=False)
    tms_tds_move_id = fields.Many2one("account.move", readonly=True, copy=False, string="TDS entry")
    # BR-INV-05
    tms_gst_mode = fields.Selection(
        [("fcm5", "Forward charge 5%"), ("fcm18", "Forward charge 18%"), ("rcm", "Reverse charge"),
         ("exempt", "Exempt")],
        string="GTA GST", copy=False,
    )
    tms_rcm_legend = fields.Char(compute="_compute_tms_rcm_legend", string="Reverse charge legend")
    # BR-HIR-10, BR-HIR-14
    tms_tds_declaration_exempt = fields.Boolean(string="Hire paid without TDS under declaration", copy=False)
    tms_tds_declaration_ref = fields.Char(copy=False, string="TDS declaration reference")

    @api.depends("invoice_line_ids.tms_consignment_id", "invoice_line_ids.tms_trip_id",
                 "invoice_line_ids.purchase_line_id")
    def _compute_tms_links(self):
        for move in self:
            lines = move.invoice_line_ids
            move.tms_consignment_ids = lines.mapped("tms_consignment_id")
            move.tms_trip_ids = lines.mapped("tms_trip_id")
            move.tms_hire_memo_ids = lines.mapped("purchase_line_id.order_id").filtered("tms_is_hire_memo")

    @api.depends("tms_gst_mode")
    def _compute_tms_rcm_legend(self):
        for move in self:
            move.tms_rcm_legend = _("GST on this supply is payable by the recipient under reverse charge.") \
                if move.tms_gst_mode == "rcm" else False

    # ------------------------------------------------------------------
    def _tms_check_post(self):
        """Hook for module checks before posting (estimate lines, cost classes, contract balance)."""
        return True

    def _post(self, soft=True):
        """Every posting path (the Post button, bulk "Post entries", auto-post) runs the TMS checks before and
        the TMS follow-ups after."""
        self.filtered(lambda m: m.state == "draft")._tms_before_post()
        posted = super()._post(soft=soft)
        posted._tms_after_post()
        return posted

    def _tms_before_post(self):
        for move in self:
            if move.move_type == "in_invoice":
                memos = move.invoice_line_ids.mapped("purchase_line_id.order_id").filtered("tms_is_hire_memo")
                for memo in memos:
                    match = move.tms_match_ids.filtered(lambda m, po=memo: m.purchase_id == po)
                    if not match:
                        match = self.env["tms.vendor.bill.match"].create({"move_id": move.id, "purchase_id": memo.id})
                    match._tms_check_or_raise()
                    memo_partner = memo.partner_id.commercial_partner_id
                    if memo_partner.tms_has_valid_tds_declaration(move.invoice_date):
                        decl = memo_partner.tms_tds_declaration_ids.filtered(
                            lambda d: d.date_from <= (move.invoice_date or fields.Date.today()) <= d.date_to)[:1]
                        move.write({"tms_tds_declaration_exempt": True, "tms_tds_declaration_ref": decl.reference})
            move._tms_check_post()
        return True

    def _tms_after_post(self):
        for move in self:
            move.tms_match_ids.filtered(lambda m: m.state in ("passed", "overridden")).write({"state": "posted"})
            charges = move.line_ids.mapped("tms_charge_id")
            charges._tms_mark_settled_from_moves()
            lrs = move.line_ids.mapped("tms_consignment_id")
            if lrs:
                lrs._tms_refresh_invoiced()
            if move.tms_claim_id:
                move.tms_claim_id._tms_check_settled()
            claims = move.line_ids.mapped("tms_charge_id.claim_id")
            claims._tms_check_settled()
            # BR-HIR-09: reconcile hire advances with the posted hire bill
            if move.move_type == "in_invoice":
                memos = move.invoice_line_ids.mapped("purchase_line_id.order_id").filtered("tms_is_hire_memo")
                adv_moves = memos.mapped("tms_advance_ids").filtered(lambda a: a.state == "posted").mapped("move_id")
                payable_type = "liability_payable"
                to_rec = (move.line_ids | adv_moves.mapped("line_ids")).filtered(
                    lambda line: line.account_id.account_type == payable_type and not line.reconciled
                    and line.partner_id.commercial_partner_id == move.partner_id.commercial_partner_id)
                if len(to_rec) > 1 and len(to_rec.mapped("account_id")) == 1:
                    to_rec.reconcile()

    def write(self, vals):
        res = super().write(vals)
        if "invoice_line_ids" in vals or "line_ids" in vals:
            self.mapped("tms_match_ids").filtered(lambda m: m.state not in ("posted", "overridden"))._tms_refresh()
        return res

    def button_draft(self):
        res = super().button_draft()
        for move in self:
            move.tms_match_ids.filtered(lambda m: m.state == "posted").write({"state": "blocked"})
            move.line_ids.mapped("tms_consignment_id")._tms_refresh_invoiced()
        return res

    def action_tms_record_customer_tds(self):
        """BR-INV-12: TDS the customer deducts posts to a TDS receivable per invoice."""
        for move in self:
            if move.move_type != "out_invoice" or move.state != "posted":
                raise UserError(_("Customer TDS is recorded on posted customer invoices."))
            if not move.tms_customer_tds_amount:
                raise UserError(_("Enter the TDS amount the customer deducted."))
            account = move.company_id.tms_tds_receivable_account_id
            if not account:
                raise UserError(_("Configure the customer TDS receivable account."))
            receivable = move.line_ids.filtered(lambda line: line.account_id.account_type == "asset_receivable")[:1]
            tds = self.create({
                "move_type": "entry",
                "journal_id": move.journal_id.id,
                "date": fields.Date.context_today(self),
                "ref": _("Customer TDS on %s", move.name),
                "line_ids": [
                    (0, 0, {"name": _("TDS receivable %s", move.name), "account_id": account.id,
                            "partner_id": move.partner_id.id, "debit": move.tms_customer_tds_amount, "credit": 0.0}),
                    (0, 0, {"name": _("TDS deducted on %s", move.name), "account_id": receivable.account_id.id,
                            "partner_id": move.partner_id.id, "debit": 0.0,
                            "credit": move.tms_customer_tds_amount}),
                ],
            })
            tds.action_post()
            (receivable | tds.line_ids.filtered(lambda line: line.account_id == receivable.account_id)).reconcile()
            move.tms_tds_move_id = tds
        return True


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    tms_consignment_id = fields.Many2one("tms.consignment", string="LR", index="btree_not_null")
    tms_trip_id = fields.Many2one("tms.order", string="Trip", index="btree_not_null")
    tms_charge_id = fields.Many2one("tms.trip.charge", string="Charge", index="btree_not_null")
    tms_claim_id = fields.Many2one("tms.damage.claim", string="Claim")
    tms_expense_id = fields.Many2one("tms.trip.expense", string="Trip expense")
    tms_line_kind = fields.Selection(LINE_KINDS, string="TMS line kind", index="btree_not_null")
    tms_cost_class = fields.Selection(COST_CLASSES, string="Cost class")
    tms_overhead = fields.Boolean(string="Overhead")
    tms_overhead_reason = fields.Char(string="Overhead reason")
    tms_replacement_vehicle_id = fields.Many2one("fleet.vehicle", string="Replacement vehicle")

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        for line in lines:
            move = line.move_id
            if move.move_type == "out_refund" and line.tms_consignment_id and move.tms_credit_reason in (
                    "claim", "deduction") and line.tms_line_kind in ("freight", "charge", False):
                line.tms_line_kind = move.tms_credit_reason
        return lines

    def write(self, vals):
        if "tms_overhead" in vals and vals["tms_overhead"]:
            for line in self:
                if not line.tms_overhead:
                    if not (vals.get("tms_overhead_reason") or line.tms_overhead_reason):
                        raise UserError(_("Marking a line overhead needs a reason (BR-ACT-05)."))
                    self.env["tms.override.log"].log(
                        "overhead", line.move_id, _("Line %s marked overhead", line.name or line.id),
                        reason=vals.get("tms_overhead_reason") or line.tms_overhead_reason, amount=line.balance)
        res = super().write(vals)
        if {"price_unit", "quantity", "discount", "price_subtotal"} & set(vals):
            self.mapped("move_id.tms_match_ids").filtered(
                lambda m: m.state not in ("posted", "overridden"))._tms_refresh()
        return res
