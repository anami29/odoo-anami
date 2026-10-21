from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from odoo.addons.tms_core.models.tools import notify_detached, users_in_group

ESTIMATE_LINE_DOMAIN = ("[('order_state', '=', 'sale'), ('company_id', '=', company_id)]")


class PurchaseOrderLine(models.Model):
    """BR-ACT-01: purchase order items name the estimate line they buy."""

    _inherit = "purchase.order.line"

    tms_estimate_line_id = fields.Many2one(
        "tms.estimate.line", string="Estimate line", index="btree_not_null",
        domain="[('order_state', '=', 'sale'), ('company_id', '=', company_id)]",
    )
    tms_sale_line_id = fields.Many2one(related="tms_estimate_line_id.sale_line_id", string="Order item served")

    def _prepare_account_move_line(self, move=False):
        """BR-ACT-02: a bill line created from a PO item inherits its estimate line."""
        res = super()._prepare_account_move_line(move=move)
        if self.tms_estimate_line_id:
            res["tms_estimate_line_id"] = self.tms_estimate_line_id.id
        return res


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    tms_estimate_line_id = fields.Many2one(
        "tms.estimate.line", string="Estimate line", index="btree_not_null",
        domain="[('order_state', '=', 'sale'), ('company_id', '=', company_id)]",
    )
    tms_sale_line_id = fields.Many2one("sale.order.line", string="Order item", index="btree_not_null",
                                       help="The order item an invoice line bills (BR-NUM-05).")


class AccountMove(models.Model):
    _inherit = "account.move"

    tms_variance_approved_by_id = fields.Many2one("res.users", string="Variance approved by", readonly=True,
                                                  copy=False)

    def _tms_cost_lines(self):
        self.ensure_one()
        if self.move_type not in ("in_invoice", "in_refund", "entry"):
            return self.env["account.move.line"]
        return self.line_ids.filtered(
            lambda line: line.display_type in ("product", False) and line.tms_trip_id
            and line.tms_line_kind not in ("recovery", "accrual")
            and line.account_id.account_type in ("expense", "expense_direct_cost") and line.debit)

    def _tms_check_post(self):
        res = super()._tms_check_post()
        for move in self:
            for line in move._tms_cost_lines():
                # BR-ACT-05, RL-08: a cost line on a trip that moves an order item needs its estimate line
                if not line.tms_estimate_line_id and not line.tms_overhead \
                        and line.tms_trip_id._tms_moves_order_item() and line.tms_cost_class in (
                            "trip_direct", False):
                    raise UserError(_("Line '%s' names a trip but no estimate line: choose the estimate line or mark "
                                      "it overhead with a reason (BR-ACT-05).", line.name))
            move._tms_check_variance()
        return res

    def _tms_check_variance(self):
        """BR-ACT-06: above the variance tolerance the Branch Manager approves before posting."""
        self.ensure_one()
        if self.tms_variance_approved_by_id or self.move_type not in ("in_invoice", "entry"):
            return
        expenses = self.line_ids.mapped("tms_expense_id")
        if expenses and all(expenses.mapped("variance_approved_by_id")):
            return  # the trip expense's own variance approval covers its entry
        if expenses and not self.line_ids.filtered(lambda line: line.tms_estimate_line_id
                                                   and not line.tms_expense_id):
            return  # expense entries are checked on the expense (tms.trip.expense._tms_check_before_post)
        over = self._tms_lines_over_tolerance()
        if over:
            msg = _("Over the estimate by more than the tolerance (BR-ACT-06): %s. A Branch Manager approves first.",
                    ", ".join(over))
            company = self.company_id
            users = users_in_group(self.env, "tms_core.group_branch_manager", company)
            notify_detached(self.env, self._name, self.id, users.ids, _("Variance approval needed"), msg)  # NTF-10
            raise UserError(msg)

    def _tms_lines_over_tolerance(self):
        over = []
        for eline, lines in self._tms_group_by_estimate_line().items():
            tol = eline.company_id.tms_estimate_variance_tolerance
            already = eline.actual
            new = sum(lines.mapped("balance"))
            if eline.estimated_cost and already + new > eline.estimated_cost * (1 + tol / 100.0) + 0.005:
                over.append(eline.display_name)
            elif not eline.estimated_cost and eline.unplanned and new > 0 and not eline.incidental:
                over.append(eline.display_name)
        return over

    def _tms_group_by_estimate_line(self):
        groups = {}
        for line in self.line_ids.filtered(lambda line: line.tms_estimate_line_id and line.debit):
            groups.setdefault(line.tms_estimate_line_id, self.env["account.move.line"])
            groups[line.tms_estimate_line_id] |= line
        return groups

    def action_tms_approve_variance(self):
        for move in self:
            if not self.env.user.has_group("tms_core.group_branch_manager"):
                raise UserError(_("A Branch Manager approves variances over the estimate (BR-ACT-06)."))
            move.sudo().tms_variance_approved_by_id = self.env.user
            move.sudo().message_post(body=_("Variance over the estimate approved."))


class TmsTripExpense(models.Model):
    _inherit = "tms.trip.expense"

    estimate_line_id = fields.Many2one(
        "tms.estimate.line", string="Estimate line",
        domain="[('order_state', '=', 'sale'), ('company_id', '=', company_id)]",
    )
    variance_approved_by_id = fields.Many2one("res.users", readonly=True, copy=False)

    def _tms_expense_line_vals(self):
        vals = super()._tms_expense_line_vals()
        vals["tms_estimate_line_id"] = self.estimate_line_id.id
        return vals

    def _tms_check_before_post(self):
        res = super()._tms_check_before_post()
        for exp in self:
            if exp.trip_id and exp.tms_cost_class == "trip_direct" and not exp.estimate_line_id \
                    and not exp.tms_overhead_reason and exp.trip_id._tms_moves_order_item():
                raise UserError(_("Expense %s names a trip but no estimate line: choose it or mark the expense "
                                  "overhead with a reason (BR-ACT-05).", exp.name))
            eline = exp.estimate_line_id
            if eline and not exp.variance_approved_by_id:
                tol = eline.company_id.tms_estimate_variance_tolerance
                if eline.estimated_cost and eline.actual + exp.amount > eline.estimated_cost * (1 + tol / 100.0):
                    msg = _("Expense %(e)s takes %(l)s over its estimate by more than %(t)s%%; a Branch "
                            "Manager approves first (BR-ACT-06).", e=exp.name, l=eline.display_name, t=tol)
                    users = users_in_group(self.env, "tms_core.group_branch_manager",
                                           eline.company_id or exp.company_id)
                    notify_detached(self.env, exp._name, exp.id, users.ids, _("Variance approval needed"),
                                    msg)  # BR-NTF-10
                    raise UserError(msg)
        return res

    def action_tms_approve_variance(self):
        for exp in self:
            if not self.env.user.has_group("tms_core.group_branch_manager"):
                raise UserError(_("A Branch Manager approves variances over the estimate (BR-ACT-06)."))
            exp.variance_approved_by_id = self.env.user


class TmsOrder(models.Model):
    _inherit = "tms.order"

    def _tms_planned_km(self):
        km = max(self.consignment_ids.mapped("sale_line_id.tms_distance_km") or [0.0])
        return km or super()._tms_planned_km()

    tms_sale_line_ids = fields.Many2many("sale.order.line", compute="_compute_tms_sale_lines",
                                         string="Order items moved")

    @api.depends("consignment_link_ids.consignment_id.sale_line_id")
    def _compute_tms_sale_lines(self):
        for trip in self:
            trip.tms_sale_line_ids = trip.consignment_ids.mapped("sale_line_id")

    def _tms_moves_order_item(self):
        self.ensure_one()
        return bool(self.consignment_ids.filtered("sale_line_id"))


class TmsConsignment(models.Model):
    """BR-EST-13, BR-LR-07: an LR booked against an order item carries its movement's amount."""

    _inherit = "tms.consignment"

    sale_line_id = fields.Many2one(
        "sale.order.line", string="Order item", tracking=True, index="btree_not_null",
        domain="[('order_id.state', '=', 'sale'), ('tms_is_movement', '=', True), "
               "('order_id.partner_id', 'in', [consignor_id, billing_partner_id])]",
    )
    sale_order_id = fields.Many2one(related="sale_line_id.order_id", store=True, string="Sales order")
    movement_no = fields.Integer(string="Movement", default=1, tracking=True)
    freight_frozen = fields.Boolean(readonly=True, copy=False,
                                    help="The split freezes when the movement closes or its first LR is invoiced.")
    frozen_freight = fields.Monetary(readonly=True, copy=False)

    def _tms_has_order_item(self):
        return bool(self.sale_line_id) or super()._tms_has_order_item()

    item_freight = fields.Monetary(readonly=True, copy=False,
                                   help="This LR's share of its movement's amount, by charged weight (#23.1).")

    @api.depends("sale_line_id", "item_freight", "freight_frozen", "frozen_freight", "state")
    def _compute_freight(self):
        """#23.1: F_ℓ = P_movement × W_ℓ / Σ W_k over the movement's LRs."""
        priced = self.filtered("sale_line_id")
        for lr in priced:
            if lr.freight_frozen:
                lr.freight_amount = lr.frozen_freight
            elif lr.state == "cancel":
                lr.freight_amount = 0.0
            else:
                lr.freight_amount = lr.item_freight
        return super(TmsConsignment, self - priced)._compute_freight()

    def _tms_movement_lrs(self):
        self.ensure_one()
        lrs = self.search([("sale_line_id", "=", self.sale_line_id.id), ("movement_no", "=", self.movement_no),
                           ("state", "!=", "cancel")])
        if self.state != "cancel":
            lrs |= self
        return lrs

    def _tms_reprice_movement(self):
        movements = {(lr.sale_line_id, lr.movement_no) for lr in self if lr.sale_line_id}
        for sale_line, movement in movements:
            lrs = self.search([("sale_line_id", "=", sale_line.id), ("movement_no", "=", movement)])
            live = lrs.filtered(lambda lr: lr.state != "cancel" and not lr.freight_frozen)
            frozen_amount = sum(lrs.filtered("freight_frozen").mapped("frozen_freight"))
            amount = sale_line._tms_movement_amount() - frozen_amount
            total = sum(live.mapped("charged_weight"))
            for lr in lrs:
                if lr in live:
                    share = amount * lr.charged_weight / total if total else amount / max(len(live), 1)
                else:
                    share = 0.0
                if abs(lr.item_freight - share) > 0.001:
                    lr.with_context(tms_reprice=True).write({"item_freight": share})

    @api.constrains("sale_line_id", "movement_no", "freight_basis", "consignor_id", "billing_partner_id")
    def _check_movement(self):
        for lr in self.filtered("sale_line_id"):
            if lr.movement_no < 1 or lr.movement_no > max(int(lr.sale_line_id.product_uom_qty), 1) \
                    and not lr.sale_line_id.order_id._tms_is_contract_order():
                raise ValidationError(_("Item %(i)s has %(n)s movements.", i=lr.sale_line_id.tms_item_no,
                                        n=int(lr.sale_line_id.product_uom_qty)))
            siblings = lr._tms_movement_lrs() - lr
            if siblings and (lr.freight_basis == "paid" or "paid" in siblings.mapped("freight_basis")):
                raise ValidationError(_("A Paid LR cannot share a movement (BR-EST-13)."))
            customer = lr.sale_line_id.order_id.partner_id.commercial_partner_id
            if customer not in (lr.consignor_id.commercial_partner_id, lr.billing_partner_id.commercial_partner_id):
                raise ValidationError(_("The LR's consignor or billing party must be the order's customer."))

    @api.onchange("sale_line_id")
    def _onchange_sale_line(self):
        line = self.sale_line_id
        if line:
            self.tms_cargo_type = line.tms_cargo_type
            self.origin_id = line.tms_origin_id or self.origin_id
            self.destination_id = line.tms_destination_id or self.destination_id
            self.rate_type = False
            self.typed_rate = 0.0

    @api.model_create_multi
    def create(self, vals_list):
        lrs = super().create(vals_list)
        for lr in lrs.filtered("sale_line_id"):
            if lr.sale_line_id.tms_cargo_type and lr.tms_cargo_type != lr.sale_line_id.tms_cargo_type:
                lr.tms_cargo_type = lr.sale_line_id.tms_cargo_type
        lrs._tms_check_movement_open()
        lrs._tms_reprice_movement()
        return lrs

    def _tms_check_movement_open(self):
        for lr in self.filtered("sale_line_id"):
            frozen = (lr._tms_movement_lrs() - lr).filtered("freight_frozen")
            if frozen:
                raise UserError(_("Movement %(m)s of item %(i)s is closed for booking.", m=lr.movement_no,
                                  i=lr.sale_line_id.tms_item_no))
            if lr.sale_line_id.order_id.state != "sale":
                raise UserError(_("LRs book against confirmed order items only."))

    def write(self, vals):
        reprice = {"sale_line_id", "movement_no", "charged_weight_override", "state", "line_ids"} & set(vals)
        old = self.filtered("sale_line_id") if reprice else self.browse()
        old_keys = [(lr.sale_line_id, lr.movement_no) for lr in old]
        res = super().write(vals)
        if reprice and not self.env.context.get("tms_reprice"):
            (old | self)._tms_reprice_movement()
            for sale_line, movement in old_keys:
                self.search([("sale_line_id", "=", sale_line.id), ("movement_no", "=", movement)],
                            limit=1)._tms_reprice_movement()
        return res

    def action_tms_close_movement(self):
        """BR-EST-13: the split freezes when the movement closes for booking."""
        for lr in self.filtered("sale_line_id"):
            for sibling in lr._tms_movement_lrs():
                sibling.write({"frozen_freight": sibling.freight_amount, "freight_frozen": True})

    def _tms_refresh_invoiced(self):
        res = super()._tms_refresh_invoiced()
        # the split also freezes when the movement's first LR is invoiced
        for lr in self.filtered(lambda x: x.sale_line_id and x.freight_invoiced and not x.freight_frozen):
            lr.action_tms_close_movement()
        return res

    def _tms_credit_amount(self):
        return self.freight_amount


class TmsInvoiceLrWizard(models.TransientModel):
    _inherit = "tms.invoice.lr.wizard"

    @api.model
    def _tms_freight_line_vals(self, lr, taxes):
        """BR-INV-04, BR-NUM-05: invoice lines name the order item they bill."""
        vals = super()._tms_freight_line_vals(lr, taxes)
        if lr.sale_line_id:
            vals["tms_sale_line_id"] = lr.sale_line_id.id
            vals["name"] += _(", item %(i)s/%(n)s", i=lr.sale_line_id.order_id.name, n=lr.sale_line_id.tms_item_no)
        return vals


class TmsTripCharge(models.Model):
    _inherit = "tms.trip.charge"

    sale_line_id = fields.Many2one("sale.order.line", string="Order item", readonly=True, copy=False)

    def _tms_on_approved(self):
        """BR-CHG-05: a customer charge on an LR booked against an order becomes a new order item."""
        res = super()._tms_on_approved()
        for charge in self.filtered(lambda c: c.recover_from == "customer" and c.consignment_id.sale_line_id
                                    and not c.sale_line_id):
            order = charge.consignment_id.sale_line_id.order_id
            product = charge.category_id.product_id or self.env.ref("tms_core.product_freight")
            line = self.env["sale.order.line"].sudo().with_context(tms_estimate_push=True).create({
                "order_id": order.id,
                "product_id": product.id,
                "name": _("%(c)s %(cat)s on LR %(lr)s", c=charge.name, cat=charge.category_id.name,
                          lr=charge.consignment_id.name),
                "product_uom_qty": 1.0,
                "price_unit": charge.amount,
                "tms_charge_id": charge.id,
            })
            charge.sale_line_id = line
        return res


class TmsConsignmentLine(models.Model):
    _inherit = "tms.consignment.line"

    def write(self, vals):
        res = super().write(vals)
        if {"actual_weight", "charged_weight", "packages"} & set(vals):
            self.mapped("consignment_id")._tms_reprice_movement()
        return res
