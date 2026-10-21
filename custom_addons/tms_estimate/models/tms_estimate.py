import json
import math

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.osv import expression
from odoo.tools import float_round

from odoo.addons.tms_core.models.tools import schedule, users_in_group

from .product import COST_BASES

BASIS_UOM = {"unit": "Unit", "km": "km", "unit_km": "Unit-km", "day": "Day", "unit_day": "Unit-day",
             "state": "State", "lump": "Lot"}


class TmsEstimate(models.Model):
    """BR-EST-01..12: one cost estimate per sales order item."""

    _name = "tms.estimate"
    _description = "Cost estimate"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "id desc"
    _check_company_auto = True

    name = fields.Char(compute="_compute_name", store=True)
    sale_line_id = fields.Many2one("sale.order.line", string="Order item", required=True, ondelete="cascade",
                                   index=True)
    order_id = fields.Many2one(related="sale_line_id.order_id", store=True, string="Order")
    partner_id = fields.Many2one(related="order_id.partner_id", store=True, string="Customer")
    company_id = fields.Many2one(related="order_id.company_id", store=True, string="Branch")
    currency_id = fields.Many2one(related="order_id.currency_id")
    date = fields.Date(compute="_compute_date", store=True, readonly=False,
                       help="Costs come from the prices in force on this date (BR-EST-09).")
    origin_id = fields.Many2one("res.partner", string="Origin", domain=[("tms_location", "=", True)], tracking=True)
    destination_id = fields.Many2one("res.partner", string="Destination", domain=[("tms_location", "=", True)],
                                     tracking=True)
    vehicle_class_id = fields.Many2one("tms.vehicle.class", string="Vehicle type", tracking=True)
    equipment_note = fields.Char(string="ODC equipment")
    tms_cargo_type = fields.Selection(related="sale_line_id.tms_cargo_type", store=True)
    distance_km = fields.Float(string="Total distance (km)", tracking=True)
    km_per_day = fields.Float(string="Planned km per day", default=lambda s: s.env.company.tms_default_km_per_day)
    days = fields.Float(compute="_compute_days", store=True, readonly=False)
    state_count = fields.Integer(string="States crossed", default=1)
    line_ids = fields.One2many("tms.estimate.line", "estimate_id", string="Lines", copy=True)
    state = fields.Selection([("draft", "Draft"), ("locked", "Locked"), ("amending", "Amendment pending")],
                             default="draft", tracking=True, copy=False)
    is_contract = fields.Boolean(help="A contract item: priced by the contract, the estimate measures cost only "
                                      "(BR-CON-06).")
    # totals
    estimated_cost = fields.Monetary(compute="_compute_totals", store=True)
    marked_up_total = fields.Monetary(compute="_compute_totals", store=True, string="Marked-up value")
    markup_pct = fields.Float(compute="_compute_totals", store=True, string="Overall markup %")
    rate_per_km = fields.Monetary(compute="_compute_totals", store=True, string="Rate per km")
    amount = fields.Monetary(compute="_compute_totals", store=True, string="Item amount")
    rounding_difference = fields.Monetary(compute="_compute_totals", store=True)
    contribution = fields.Monetary(compute="_compute_totals", store=True,
                                   help="Freight less estimated cost (BR-CON-26).")
    committed = fields.Monetary(compute="_compute_actuals")
    actual = fields.Monetary(compute="_compute_actuals")
    variance = fields.Monetary(compute="_compute_actuals")
    # amendment (BR-EST-12)
    version = fields.Integer(default=1, readonly=True, copy=False)
    baseline_json = fields.Text(readonly=True, copy=False)
    baseline_amount = fields.Monetary(readonly=True, copy=False)
    baseline_cost = fields.Monetary(readonly=True, copy=False)
    amendment_ids = fields.One2many("tms.estimate.amendment", "estimate_id")

    _sql_constraints = [("sale_line_uniq", "unique(sale_line_id)", "An order item has one cost estimate.")]

    @api.depends("sale_line_id.order_id.name", "sale_line_id.tms_item_no")
    def _compute_name(self):
        for est in self:
            est.name = "%s/%s" % (est.sale_line_id.order_id.name or "", est.sale_line_id.tms_item_no or "")

    @api.depends("sale_line_id.order_id.date_order")
    def _compute_date(self):
        for est in self:
            est.date = fields.Date.to_date(est.sale_line_id.order_id.date_order) if est.sale_line_id.order_id \
                .date_order else fields.Date.context_today(est)

    @api.depends("distance_km", "km_per_day")
    def _compute_days(self):
        for est in self:
            est.days = math.ceil(est.distance_km / est.km_per_day) if est.km_per_day and est.distance_km else 0.0

    @api.depends("line_ids.estimated_cost", "line_ids.marked_up", "distance_km")
    def _compute_totals(self):
        """#9.1: M_i = C_i(1+u_i); r = ΣM/d rounded to the paisa; P = r·d; markup = (ΣM−ΣC)/ΣC."""
        for est in self:
            cost = sum(est.line_ids.mapped("estimated_cost"))
            marked = sum(est.line_ids.mapped("marked_up"))
            est.estimated_cost = cost
            est.marked_up_total = marked
            est.markup_pct = ((marked - cost) / cost * 100.0) if cost else 0.0
            rate = float_round(marked / est.distance_km, precision_digits=2) if est.distance_km else 0.0
            est.rate_per_km = rate
            amount = float_round(rate * est.distance_km, precision_digits=2) if est.distance_km else marked
            est.amount = amount
            est.rounding_difference = amount - marked
            est.contribution = amount - cost

    def _compute_actuals(self):
        for est in self:
            est.committed = sum(est.line_ids.mapped("committed"))
            est.actual = sum(est.line_ids.mapped("actual"))
            est.variance = est.actual - est.estimated_cost

    # ------------------------------------------------------------------
    def _tms_check_editable(self):
        for est in self:
            if est.state == "locked" and not self.env.context.get("tms_estimate_unlock"):
                raise UserError(_("Estimate %s is locked; change it through an approved amendment (BR-EST-12).",
                                  est.name))

    def write(self, vals):
        protected = {"distance_km", "km_per_day", "days", "state_count", "origin_id", "destination_id",
                     "vehicle_class_id"}
        if protected & set(vals):
            self._tms_check_editable()
        res = super().write(vals)
        if protected & set(vals):
            self.line_ids._tms_refresh_quantities()
        if {"distance_km", "line_ids"} & set(vals) or protected & set(vals):
            self._tms_push_price()
        return res

    def _tms_push_price(self):
        """BR-QTN-02: the item's rate per km and amount come from its estimate."""
        for est in self:
            line = est.sudo().sale_line_id
            if est.is_contract or line.state in ("cancel",):
                continue
            if est.state == "locked" and not self.env.context.get("tms_estimate_unlock"):
                continue
            vals = {"tms_distance_km": est.distance_km, "tms_rate_per_km": est.rate_per_km}
            if line.order_id.state in ("draft", "sent") or self.env.context.get("tms_estimate_unlock"):
                vals["price_unit"] = est.amount
            line.sudo().with_context(tms_estimate_push=True).write(vals)
            if line.order_id.state in ("draft", "sent"):
                line.sudo()._tms_update_approval()

    def _tms_ensure_misc(self):
        """BR-EST-01: the last line is Misc."""
        misc_product = self.env.ref("tms_estimate.product_cost_misc")
        for est in self:
            last = est.line_ids.sorted("line_no")[-1:]
            if not last or not last.product_id.tms_cost_category_id.is_misc:
                est.line_ids.create({"estimate_id": est.id, "product_id": misc_product.id, "basis": "lump",
                                     "unit_cost": 0.0, "markup_pct": 0.0})

    def action_lock(self):
        """BR-EST-10: confirmation locks every estimate column."""
        for est in self:
            est._tms_ensure_misc()
            est.write({"state": "locked"})
            est.line_ids.write({"locked": True})
        return True

    def action_request_amendment(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Amend estimate"),
            "res_model": "tms.estimate.amendment.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_estimate_id": self.id},
        }

    def _tms_snapshot(self):
        self.ensure_one()
        return json.dumps([{
            "line_no": line.line_no, "product": line.product_id.display_name, "quantity": line.quantity,
            "unit_cost": line.unit_cost, "estimated_cost": line.estimated_cost, "markup_pct": line.markup_pct,
            "marked_up": line.marked_up,
        } for line in self.line_ids.sorted("line_no")] + [{
            "distance_km": self.distance_km, "rate_per_km": self.rate_per_km, "amount": self.amount}])

    def action_open_lines(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "res_model": "tms.estimate", "res_id": self.id,
                "view_mode": "form"}


class TmsEstimateLine(models.Model):
    """BR-EST-02..04, BR-EST-11, BR-ACT-03."""

    _name = "tms.estimate.line"
    _description = "Cost estimate line"
    _order = "estimate_id, line_no"
    _rec_names_search = ["display_name"]

    estimate_id = fields.Many2one("tms.estimate", required=True, ondelete="cascade", index=True)
    sale_line_id = fields.Many2one(related="estimate_id.sale_line_id", store=True, string="Order item")
    order_id = fields.Many2one(related="estimate_id.order_id", store=True)
    partner_id = fields.Many2one(related="estimate_id.partner_id", store=True, string="Customer")
    company_id = fields.Many2one(related="estimate_id.company_id", store=True)
    currency_id = fields.Many2one(related="estimate_id.currency_id")
    order_state = fields.Selection(related="order_id.state", string="Order status")
    line_no = fields.Integer(string="Line", readonly=True, copy=False)
    product_id = fields.Many2one("product.product", string="Cost item", required=True,
                                 domain=[("tms_cost_item", "=", True)])
    name = fields.Char(string="Description")
    category_id = fields.Many2one(related="product_id.tms_cost_category_id", store=True)
    incidental = fields.Boolean(related="category_id.incidental", store=True)
    basis = fields.Selection(COST_BASES, required=True, default="lump")
    uom_label = fields.Char(compute="_compute_uom_label")
    units = fields.Float(default=1.0)
    km = fields.Float()
    days = fields.Float()
    states = fields.Float(string="States")
    quantity = fields.Float(compute="_compute_quantity", store=True)
    vendor_id = fields.Many2one("res.partner", string="Vendor")
    computed_unit_cost = fields.Float(compute="_compute_unit_cost", digits="Product Price")
    unit_cost = fields.Float(digits="Product Price")
    typed_cost_reason = fields.Char(help="A typed cost needs a reason and is tracked (BR-EST-03).")
    estimated_cost = fields.Monetary(compute="_compute_amounts", store=True)
    markup_pct = fields.Float(string="Markup %")
    marked_up = fields.Monetary(compute="_compute_amounts", store=True, string="Marked-up value")
    locked = fields.Boolean(readonly=True, copy=False)
    unplanned = fields.Boolean(readonly=True, copy=False, help="Added after confirmation with a zero estimate.")
    # actuals (BR-ACT-03)
    committed = fields.Monetary(compute="_compute_actual")
    actual = fields.Monetary(compute="_compute_actual")
    document_count = fields.Integer(compute="_compute_actual")
    variance = fields.Monetary(compute="_compute_actual")
    variance_pct = fields.Float(compute="_compute_actual")
    bill_line_ids = fields.One2many("account.move.line", "tms_estimate_line_id")
    po_line_ids = fields.One2many("purchase.order.line", "tms_estimate_line_id")
    expense_ids = fields.One2many("tms.trip.expense", "estimate_line_id")

    @api.depends("estimate_id.name", "line_no", "product_id", "partner_id")
    def _compute_display_name(self):
        """Quoted as S00044/10/02 · Axle XYZ × 14 · Shakti Transformers (BR-NUM-03, #9.2)."""
        for line in self:
            line.display_name = "%s/%02d · %s · %s" % (line.estimate_id.name or "", line.line_no or 0,
                                                       line.name or line.product_id.name or "",
                                                       line.partner_id.name or "")

    @api.model
    def _search_display_name(self, operator, value):
        if operator in ("ilike", "=ilike", "like", "=like") and value:
            parts = [p.strip() for p in value.replace("·", "/").split("/") if p.strip()]
            domains = []
            for part in parts:
                dom = ["|", "|", "|", ("order_id.name", operator, part), ("product_id.name", operator, part),
                       ("partner_id.name", operator, part), ("name", operator, part)]
                if part.isdigit():
                    dom = ["|", "|", ("line_no", "=", int(part)), ("sale_line_id.tms_item_no", "=", int(part))] + dom
                domains.append(dom)
            return expression.AND(domains) if domains else []
        return super()._search_display_name(operator, value)

    @api.depends("basis")
    def _compute_uom_label(self):
        for line in self:
            line.uom_label = BASIS_UOM.get(line.basis, "")

    @api.depends("basis", "units", "km", "days", "states")
    def _compute_quantity(self):
        """#9.1 basis table."""
        for line in self:
            basis = line.basis
            if basis == "unit":
                qty = line.units
            elif basis == "km":
                qty = line.km
            elif basis == "unit_km":
                qty = line.units * line.km
            elif basis == "day":
                qty = line.days
            elif basis == "unit_day":
                qty = line.units * line.days
            elif basis == "state":
                qty = line.units * line.states
            else:
                qty = 1.0
            line.quantity = qty

    @api.depends("product_id", "vendor_id", "quantity", "estimate_id.date")
    def _compute_unit_cost(self):
        for line in self:
            line.computed_unit_cost = line._tms_price()

    def _tms_price(self):
        """BR-EST-03: vendor price, else the cost pricelist; an owned-truck line takes the standard rate."""
        self.ensure_one()
        product = self.product_id
        if not product:
            return 0.0
        date = self.estimate_id.date or fields.Date.context_today(self)
        if product.tms_owned_truck:
            rate = self._tms_standard_rate(product.tms_vehicle_class_id or self.estimate_id.vehicle_class_id, date)
            if rate is not None:
                return rate
        if self.vendor_id:
            seller = product._select_seller(partner_id=self.vendor_id, quantity=self.quantity or 1.0, date=date)
            if seller:
                return seller.price
        pricelist = self.company_id.tms_cost_pricelist_id or self.env.company.tms_cost_pricelist_id
        if pricelist:
            return pricelist._get_product_price(product, self.quantity or 1.0, date=date)
        return product.standard_price

    def _tms_standard_rate(self, vehicle_class, date):
        """Hook: tms_profitability returns running + fixed rate per km (BR-PNL-15)."""
        return None

    @api.depends("quantity", "unit_cost", "markup_pct")
    def _compute_amounts(self):
        for line in self:
            line.estimated_cost = line.quantity * line.unit_cost
            line.marked_up = line.estimated_cost * (1.0 + line.markup_pct / 100.0)

    def _compute_actual(self):
        MoveLine = self.env["account.move.line"].sudo()
        for line in self:
            bills = MoveLine.search([("tms_estimate_line_id", "=", line.id), ("parent_state", "=", "posted"),
                                     ("display_type", "=", "product")])
            actual = sum(bills.mapped("balance"))
            pols = line.sudo().po_line_ids.filtered(lambda p: p.order_id.state in ("purchase", "done"))
            committed = sum((p.product_qty - p.qty_invoiced) * p.price_unit for p in pols)
            expenses = line.sudo().expense_ids.filtered(lambda e: e.state == "approved")
            line.actual = actual
            line.committed = committed
            line.document_count = len(bills.mapped("move_id")) + len(expenses.filtered(lambda e: not e.move_id))
            line.variance = actual - line.estimated_cost
            line.variance_pct = (line.variance / line.estimated_cost * 100.0) if line.estimated_cost else 0.0

    # ------------------------------------------------------------------
    @api.onchange("product_id")
    def _onchange_product(self):
        if self.product_id:
            est = self.estimate_id
            self.basis = self.product_id.tms_cost_basis or "lump"
            self.markup_pct = self.product_id.tms_default_markup or self.product_id.tms_cost_category_id.default_markup
            self.name = self.product_id.name
            self.km = est.distance_km
            self.days = est.days
            self.states = est.state_count or 1
            self.unit_cost = self._tms_price()

    @api.onchange("vendor_id", "units", "km", "days", "states")
    def _onchange_vendor(self):
        if self.product_id and not self.typed_cost_reason:
            self.unit_cost = self._tms_price()

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            est = self.env["tms.estimate"].browse(vals["estimate_id"])
            product = self.env["product.product"].browse(vals.get("product_id"))
            if est.state == "locked" and not self.env.context.get("tms_estimate_unlock"):
                # BR-EST-11: an unforeseen cost gets a new line with a zero estimate
                vals.update({"unplanned": True, "unit_cost": 0.0, "locked": True})
            vals.setdefault("basis", product.tms_cost_basis or "lump")
            vals.setdefault("markup_pct", product.tms_default_markup or product.tms_cost_category_id.default_markup)
            vals.setdefault("name", product.name)
            vals.setdefault("km", est.distance_km)
            vals.setdefault("days", est.days)
            vals.setdefault("states", est.state_count or 1)
            if not vals.get("line_no"):
                vals["line_no"] = max(est.line_ids.mapped("line_no") or [0]) + 1
        lines = super().create(vals_list)
        for line, vals in zip(lines, vals_list):
            if "unit_cost" not in vals:
                line.unit_cost = line._tms_price()
            elif not line.unplanned and line.product_id and line.unit_cost != line._tms_price() \
                    and not vals.get("typed_cost_reason") and not self.env.context.get("tms_allow_typed"):
                raise ValidationError(_("Line %(n)02d: a typed cost needs a reason (BR-EST-03).", n=line.line_no))
        lines.mapped("estimate_id")._tms_push_price()
        return lines

    def write(self, vals):
        if "line_no" in vals:
            raise UserError(_("Estimate line numbers never change (BR-NUM-03)."))
        editable_after_lock = {"name"}
        if set(vals) - editable_after_lock and any(line.locked for line in self) \
                and not self.env.context.get("tms_estimate_unlock"):
            raise UserError(_("Locked estimate lines change only through an approved amendment (BR-EST-10)."))
        if "unit_cost" in vals:
            for line in self:
                if abs(vals["unit_cost"] - line._tms_price()) > 0.001 and not (
                        vals.get("typed_cost_reason") or line.typed_cost_reason):
                    raise ValidationError(_("Line %(n)02d: a typed cost needs a reason (BR-EST-03).",
                                            n=line.line_no))
        res = super().write(vals)
        if "unit_cost" in vals or "typed_cost_reason" in vals:
            for line in self.filtered("typed_cost_reason"):
                self.env["tms.override.log"].log(
                    "typed_cost", line.estimate_id,
                    _("Typed cost %(c)s on %(l)s", c=line.unit_cost, l=line.display_name),
                    reason=line.typed_cost_reason)
        self.mapped("estimate_id")._tms_push_price()
        return res

    def unlink(self):
        if any(line.locked for line in self):
            raise UserError(_("Locked estimate lines are never deleted (BR-AUD-04)."))
        estimates = self.mapped("estimate_id")
        res = super().unlink()
        estimates._tms_push_price()
        return res

    def _tms_refresh_quantities(self):
        for line in self.filtered(lambda x: not x.locked or self.env.context.get("tms_estimate_unlock")):
            est = line.estimate_id
            vals = {}
            if line.basis in ("km", "unit_km"):
                vals["km"] = est.distance_km
            if line.basis in ("day", "unit_day"):
                vals["days"] = est.days
            if line.basis == "state":
                vals["states"] = est.state_count
            if vals:
                super(TmsEstimateLine, line).write(vals)

    def action_view_documents(self):
        self.ensure_one()
        moves = self.bill_line_ids.mapped("move_id")
        return {"type": "ir.actions.act_window", "name": _("Documents"), "res_model": "account.move",
                "view_mode": "list,form", "domain": [("id", "in", moves.ids)]}


class TmsEstimateAmendment(models.Model):
    """BR-EST-12: a sales order amendment keeps the original estimate as baseline."""

    _name = "tms.estimate.amendment"
    _description = "Estimate amendment"
    _inherit = ["mail.thread"]
    _order = "id desc"

    estimate_id = fields.Many2one("tms.estimate", required=True, ondelete="cascade")
    company_id = fields.Many2one(related="estimate_id.company_id", store=True)
    currency_id = fields.Many2one(related="estimate_id.currency_id")
    reason = fields.Text(required=True)
    baseline_json = fields.Text(readonly=True)
    baseline_amount = fields.Monetary(readonly=True)
    new_amount = fields.Monetary(readonly=True)
    state = fields.Selection([("open", "Editing"), ("to_approve", "To approve"), ("approved", "Approved"),
                              ("rejected", "Rejected")], default="open", tracking=True)
    requested_by_id = fields.Many2one("res.users", default=lambda s: s.env.user, readonly=True)
    approved_by_id = fields.Many2one("res.users", readonly=True)

    def action_submit(self):
        for am in self:
            am.write({"state": "to_approve", "new_amount": am.estimate_id.amount})
            users = users_in_group(self.env, "tms_core.group_branch_manager", am.company_id) - am.requested_by_id
            schedule(am.estimate_id, users, _("Estimate amendment to approve"), am.reason)

    def action_approve(self):
        for am in self:
            if am.state != "to_approve":
                raise UserError(_("Submit the amendment for approval first (BR-EST-12)."))
            if not self.env.user.has_group("tms_core.group_branch_manager"):
                raise UserError(_("A Branch Manager approves estimate amendments (BR-EST-12)."))
            if am.requested_by_id == self.env.user:
                raise UserError(_("You cannot approve your own amendment."))
            est = am.estimate_id.with_context(tms_estimate_unlock=True)
            est._tms_push_price()
            est.write({"state": "locked", "version": est.version + 1})
            est.line_ids.write({"locked": True})
            am.write({"state": "approved", "approved_by_id": self.env.user.id, "new_amount": est.amount})
            est.message_post(body=_("Amendment approved (version %(v)s): %(r)s; amount %(a)s → %(b)s",
                                    v=est.version, r=am.reason, a=am.baseline_amount, b=est.amount))
            est._tms_after_amendment(am)

    def action_reject(self):
        for am in self:
            if am.state not in ("open", "to_approve"):
                raise UserError(_("This amendment is already decided."))
            if not self.env.user.has_group("tms_core.group_branch_manager"):
                raise UserError(_("A Branch Manager rejects estimate amendments (BR-EST-12)."))
            est = am.estimate_id.with_context(tms_estimate_unlock=True)
            baseline = json.loads(am.baseline_json or "[]")
            by_no = {b["line_no"]: b for b in baseline if "line_no" in b}
            for line in est.line_ids:
                b = by_no.get(line.line_no)
                if b:
                    line.with_context(tms_allow_typed=True).write({
                        "unit_cost": b["unit_cost"], "markup_pct": b["markup_pct"],
                        "typed_cost_reason": line.typed_cost_reason or _("Restored baseline")})
                elif not line.unplanned:
                    line.write({"locked": False})
                    line.unlink()
            head = [b for b in baseline if "distance_km" in b]
            if head:
                est.write({"distance_km": head[0]["distance_km"]})
            est.write({"state": "locked"})
            est.line_ids.write({"locked": True})
            am.write({"state": "rejected", "approved_by_id": self.env.user.id})


class TmsEstimateHooks(models.Model):
    _inherit = "tms.estimate"

    def _tms_after_amendment(self, amendment):
        """Hook: tms_contract re-checks the balance and logs an amendment reservation (BR-CON-23)."""
        return True
