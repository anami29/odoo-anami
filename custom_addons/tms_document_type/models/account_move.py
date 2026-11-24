from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from odoo.addons.tms_profitability.models.account_move import cover_months

from .tms_document_type import MOVE_APPLIES


class AccountMove(models.Model):
    _name = "account.move"
    _inherit = ["account.move", "tms.document.type.mixin"]

    tms_amc_requisition_id = fields.Many2one("purchase.requisition", string="Maintenance contract",
                                             readonly=True, copy=False, index="btree_not_null")

    def _tms_doc_applies_to(self):
        if not self:
            return MOVE_APPLIES.get(self.env.context.get("default_move_type"))
        return MOVE_APPLIES.get(self.move_type)

    @api.model
    def _tms_doc_applies_to_vals(self, vals):
        return MOVE_APPLIES.get(vals.get("move_type") or self.env.context.get("default_move_type"))

    @api.onchange("move_type")
    def _onchange_tms_document_type_move_type(self):
        applies = MOVE_APPLIES.get(self.move_type)
        if applies and self.tms_document_type_id.applies_to != applies:
            self.tms_document_type_id = self.env["tms.document.type"]._tms_default_for(applies)
        elif not applies:
            self.tms_document_type_id = False

    def _tms_doc_is_draft(self):
        self.ensure_one()
        return self.state == "draft"

    def _tms_doc_links(self):
        self.ensure_one()
        links = []
        lines = self.line_ids
        checks = (
            ("tms_consignment_id", _("an LR")),
            ("tms_trip_id", _("a trip")),
            ("tms_estimate_line_id", _("an estimate-line link")),
            ("tms_charge_id", _("a trip charge")),
            ("tms_expense_id", _("a trip expense")),
            ("vehicle_id", _("a vehicle")),
        )
        for fname, label in checks:
            if lines.filtered(fname):
                links.append(label)
        if lines.filtered(lambda line: line.tms_cost_class not in (False, "overhead")):
            links.append(_("a cost class"))
        if lines.mapped("purchase_line_id.order_id").filtered("tms_is_hire_memo"):
            links.append(_("a hire memo"))
        if self.tms_claim_id:
            links.append(_("a claim"))
        return links

    @api.model
    def _tms_doc_vals_flow(self, vals):
        if vals.get("tms_claim_id"):
            return True
        keys = ("tms_consignment_id", "tms_trip_id", "tms_charge_id", "tms_expense_id", "tms_estimate_line_id",
                "vehicle_id", "tms_line_kind")
        for line in self._tms_doc_line_vals(vals, "line_ids", "invoice_line_ids"):
            if any(line.get(k) for k in keys) or line.get("tms_cost_class") not in (None, False, "overhead"):
                return True
            if line.get("purchase_line_id"):
                pol = self.env["purchase.order.line"].browse(line["purchase_line_id"])
                if pol.order_id.tms_document_behaviour != "standard":
                    return True
        return False

    @api.model_create_multi
    def create(self, vals_list):
        explicit = self._tms_doc_prepare_vals(vals_list)
        Type = self.env["tms.document.type"]
        for vals in vals_list:
            dtype = Type.browse(vals.get("tms_document_type_id"))
            if dtype.journal_id and not vals.get("journal_id"):
                vals["journal_id"] = dtype.journal_id.id
        moves = super().create(vals_list)
        moves._tms_doc_after_create(explicit)
        return moves

    def write(self, vals):
        self._tms_doc_check_write(vals)
        return super().write(vals)

    @api.constrains("tms_document_type_id", "move_type")
    def _check_tms_document_type(self):
        self._tms_doc_check_type()

    # ------------------------------------------------------------------ posting (BR-DOC-04, BR-DOC-05)
    def _tms_before_post(self):
        """Transport documents run every transport check; Standard documents run plain Odoo plus the branch
        analytic rule (BR-DOC-11), and their lines land in branch overhead or other income."""
        standard = self.filtered(lambda m: m.tms_document_behaviour == "standard")
        standard._tms_doc_before_post_standard()
        return super(AccountMove, self - standard)._tms_before_post()

    def _tms_after_post(self):
        standard = self.filtered(lambda m: m.tms_document_behaviour == "standard")
        standard._tms_doc_after_post_standard()
        return super(AccountMove, self - standard)._tms_after_post()

    def _tms_doc_before_post_standard(self):
        for move in self:
            lines = move.invoice_line_ids.filtered(lambda line: line.display_type == "product")
            for line in lines:
                line._tms_doc_check_line()
            if move.move_type in ("in_invoice", "in_refund"):
                move._tms_doc_check_branch_analytic(lines)
                for line in lines.filtered(lambda line: line.account_id.account_type in (
                        "expense", "expense_direct_cost")):
                    vals = {"tms_cost_class": "overhead"}
                    # an office AMC or other cover longer than a month posts to prepaid and releases monthly
                    if line.tms_cover_from and line.tms_cover_to and line.balance > 0 \
                            and cover_months(line.tms_cover_from, line.tms_cover_to) > 1:
                        vals.update({"tms_expense_account_id": line.account_id.id,
                                     "account_id": line.company_id._tms_pnl_account("prepaid").id})
                    line.with_context(tms_doc_force=True).write(vals)
            elif move.move_type in ("out_invoice", "out_refund"):
                lines.filtered(lambda line: line.account_id.account_type in ("income", "income_other")).with_context(
                    tms_doc_force=True).write({"tms_line_kind": "other_income"})

    def _tms_doc_check_branch_analytic(self, lines):
        """BR-DOC-11: a Standard bill line books to its own branch's analytic account."""
        Analytic = self.env["account.analytic.account"]
        branch_accounts = self.env["res.company"].sudo().search([]).mapped("tms_analytic_account_id")
        for move in self:
            own = move.company_id._tms_analytic_account()
            for line in lines:
                keys = set()
                for key in (line.analytic_distribution or {}):
                    keys |= {int(k) for k in str(key).split(",") if k}
                named = Analytic.browse(keys)
                others = named & branch_accounts - own
                if others:
                    raise UserError(_("Line '%(l)s' names the analytic account of another branch (%(o)s); a "
                                      "Standard bill of %(b)s books to its own branch.", l=line.name or "",
                                      o=", ".join(others.mapped("name")), b=move.company_id.name))
                if own not in named:
                    raise UserError(_("Line '%(l)s' of a Standard bill needs the branch analytic account %(a)s.",
                                      l=line.name or "", a=own.name))

    def _tms_doc_after_post_standard(self):
        Schedule = self.env["tms.prepaid.schedule"].sudo()
        for move in self.filtered(lambda m: m.move_type == "in_invoice"):
            for line in move.invoice_line_ids.filtered(lambda line: line.tms_expense_account_id
                                                       and not line.tms_prepaid_schedule_id):
                sch = Schedule.create({
                    "company_id": line.company_id.id,
                    "kind": "overhead",
                    "move_line_id": line.id,
                    "amount": line.balance,
                    "date_from": line.tms_cover_from.replace(day=1),
                    "date_to": line.tms_cover_to,
                    "months": max(cover_months(line.tms_cover_from, line.tms_cover_to), 1),
                    "expense_account_id": line.tms_expense_account_id.id,
                    "prepaid_account_id": line.account_id.id,
                })
                sch.action_plan()
                line.with_context(tms_doc_force=True).tms_prepaid_schedule_id = sch


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    def _tms_doc_check_line(self):
        """BR-DOC-06: a Standard document carries no transport link; BR-DOC-17: a truck AMC is a Transport
        purchase."""
        mixin = self.env["tms.document.type.mixin"]
        for line in self.filtered(lambda line: line.display_type == "product"):
            move = line.move_id
            dtype = move.tms_document_type_id
            if not dtype:
                continue
            if dtype.behaviour == "standard":
                found = []
                for fname, label in (("tms_trip_id", _("a trip")), ("tms_estimate_line_id", _("an estimate line")),
                                     ("tms_consignment_id", _("an LR")), ("tms_charge_id", _("a trip charge")),
                                     ("tms_expense_id", _("a trip expense"))):
                    if line[fname]:
                        found.append(label)
                if line.vehicle_id:
                    if move.tms_amc_requisition_id or line.purchase_line_id.order_id.tms_document_type_id.is_amc:
                        raise ValidationError(_("Line '%s' names a vehicle: a truck AMC is a Transport purchase, "
                                                "with the vehicle and truck cost category on each line.",
                                                line.name or ""))
                    found.append(_("a vehicle"))
                if line.tms_cost_class not in (False, "overhead"):
                    found.append(_("a cost class"))
                if line.tms_line_kind not in (False, "other_income"):
                    found.append(_("a transport line kind"))
                if found:
                    raise ValidationError(_("A Standard document carries no transport link: %(l)s on line "
                                            "'%(n)s'.", l=", ".join(found), n=line.name or ""))
            mixin._tms_doc_check_product(dtype, line.product_id, line.name or line.product_id.name or "")

    @api.constrains("tms_trip_id", "tms_estimate_line_id", "tms_consignment_id", "tms_charge_id", "tms_expense_id",
                    "vehicle_id", "tms_cost_class", "tms_line_kind", "product_id")
    def _check_tms_doc_line(self):
        if self.env.context.get("tms_doc_force"):
            return
        self._tms_doc_check_line()
