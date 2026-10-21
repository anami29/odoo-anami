from odoo import _, api, fields, models
from odoo.exceptions import UserError

from odoo.addons.tms_core.models.tools import notify_detached, users_in_group


KM_BASES = ("km", "unit_km")


class SaleOrder(models.Model):
    _inherit = "sale.order"

    tms_contract_id = fields.Many2one("tms.contract", string="Contract", readonly=True, copy=False,
                                      index="btree_not_null")
    tms_contract_approved_by_id = fields.Many2one(
        "res.users", string="Contract customer approval", readonly=True, copy=False,
        help="An ordinary order for a customer with an Active contract needs Branch Manager approval (BR-CON-15).")

    def _tms_is_contract_order(self):
        return bool(self.tms_contract_id) or super()._tms_is_contract_order()

    def _tms_active_contracts(self):
        self.ensure_one()
        partner = self.partner_id.commercial_partner_id
        return self.env["tms.contract"].sudo().search([("partner_id", "=", partner.id), ("state", "=", "active")])

    def _tms_check_contract_customer(self):
        """BR-CON-15: contract business cannot bypass the balance through an ordinary order."""
        res = super()._tms_check_contract_customer()
        for order in self.filtered(lambda o: not o.tms_contract_id and not o.tms_contract_approved_by_id):
            contracts = order._tms_active_contracts()
            if contracts:
                users = users_in_group(self.env, "tms_core.group_branch_manager", order.company_id)
                notify_detached(self.env, order._name, order.id, users.ids,
                                _("Order for a contract customer needs approval"),
                                _("Customer %s holds Active contract(s) %s.") % (
                                    order.partner_id.display_name, ", ".join(contracts.mapped("name"))))
                raise UserError(_("%(c)s holds Active contract %(k)s: an ordinary order needs Branch Manager approval "
                                  "with a reason (BR-CON-15).", c=order.partner_id.display_name,
                                  k=", ".join(contracts.mapped("name"))))
        return res

    def action_tms_approve_contract_customer(self):
        reason = self.env.context.get("tms_reason")
        if not reason:
            return self.env["tms.reason.wizard"].open_for(self, "action_tms_approve_contract_customer",
                                                          _("Approve order for a contract customer"))
        if not self.env.user.has_group("tms_core.group_branch_manager"):
            raise UserError(_("A Branch Manager approves this (BR-CON-15)."))
        for order in self:
            order.sudo().tms_contract_approved_by_id = self.env.user
            self.env["tms.override.log"].log("contract_customer_rate", order,
                                             _("Ordinary order %s for a contract customer", order.name),
                                             reason=reason, amount=order.amount_untaxed)
        return True

    def action_open_contract(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "res_model": "tms.contract", "res_id": self.tms_contract_id.id,
                "view_mode": "form"}


class SaleOrderLine(models.Model):
    """BR-CON-05..12: a contract item is a numbered movement line of the contract order."""

    _inherit = "sale.order.line"

    tms_contract_id = fields.Many2one("tms.contract", string="Contract", readonly=True, index="btree_not_null",
                                      copy=False)
    tms_contract_rate_ids = fields.One2many("tms.contract.item.rate", "sale_line_id", string="Rate lines used")
    tms_release_state = fields.Selection(
        [("draft", "Draft"), ("released", "Released"), ("closed", "Closed for billing"), ("cancel", "Cancelled")],
        string="Release", default="draft", copy=False, readonly=True, index=True)
    tms_released_on = fields.Date(readonly=True, copy=False, string="Released on")
    tms_released_by_id = fields.Many2one("res.users", readonly=True, copy=False, string="Released by")
    tms_freight = fields.Monetary(compute="_compute_tms_freight", store=True, currency_field="currency_id",
                                  string="Contract freight", help="Σ rate × quantity (BR-CON-05).")
    tms_budget_reserved = fields.Monetary(readonly=True, copy=False, currency_field="currency_id",
                                          string="Budget at release")
    tms_supplementary_amount = fields.Monetary(readonly=True, copy=False, currency_field="currency_id",
                                               string="Escalation supplementary")
    tms_markup_approved_by_id = fields.Many2one("res.users", readonly=True, copy=False,
                                                string="Below-floor release approved by")
    tms_close_reason = fields.Text(readonly=True, copy=False, string="Closing reason")
    tms_reservation_ids = fields.One2many("tms.contract.reservation", "sale_line_id", string="Reservation lines")
    tms_excess_ids = fields.One2many("tms.contract.excess", "sale_line_id", string="Excess approvals")
    # #23.7, read live
    tms_incidental_estimate = fields.Monetary(compute="_compute_tms_contract", currency_field="currency_id",
                                              string="Estimated incidentals")
    tms_incidental_actual = fields.Monetary(compute="_compute_tms_contract", currency_field="currency_id",
                                            string="Posted incidentals")
    tms_other_charges = fields.Monetary(compute="_compute_tms_contract", currency_field="currency_id",
                                        string="Charges outside the incidentals")
    tms_budget = fields.Monetary(compute="_compute_tms_contract", currency_field="currency_id", string="Budget")
    tms_expected = fields.Monetary(compute="_compute_tms_contract", currency_field="currency_id",
                                   string="Expected billing")
    tms_item_invoiced = fields.Monetary(compute="_compute_tms_contract", currency_field="currency_id",
                                        string="Invoiced")
    tms_open_reservation = fields.Monetary(compute="_compute_tms_contract", currency_field="currency_id",
                                           string="Open reservation")
    tms_balance_available = fields.Monetary(compute="_compute_tms_contract", currency_field="currency_id",
                                            string="Balance for this item")
    tms_shortfall = fields.Monetary(compute="_compute_tms_contract", currency_field="currency_id",
                                    string="Shortfall")
    tms_held = fields.Boolean(compute="_compute_tms_contract", string="Held")
    tms_contribution = fields.Monetary(compute="_compute_tms_contract", currency_field="currency_id",
                                       string="Contribution")
    tms_contribution_pct = fields.Float(compute="_compute_tms_contract", string="Markup on cost %")

    @api.depends("tms_contract_rate_ids.amount")
    def _compute_tms_freight(self):
        for line in self:
            line.tms_freight = sum(line.tms_contract_rate_ids.mapped("amount"))

    @api.depends("tms_release_state", "tms_freight", "tms_reservation_ids.amount",
                 "tms_estimate_id.line_ids.estimated_cost", "tms_lr_ids.freight_invoiced")
    def _compute_tms_contract(self):
        empty = {"tms_incidental_estimate": 0.0, "tms_incidental_actual": 0.0, "tms_other_charges": 0.0,
                 "tms_budget": 0.0, "tms_expected": 0.0, "tms_item_invoiced": 0.0, "tms_open_reservation": 0.0,
                 "tms_balance_available": 0.0, "tms_shortfall": 0.0, "tms_held": False, "tms_contribution": 0.0,
                 "tms_contribution_pct": 0.0}
        cache = {}
        for line in self:
            if not (line.tms_contract_id and line.tms_is_movement and line.id):
                line.update(empty)
                continue
            contract = line.tms_contract_id
            if contract.id not in cache:
                cache[contract.id] = contract.sudo()._tms_figures()
            figs = line._tms_item_figures(cache[contract.id])
            line.update({
                "tms_incidental_estimate": figs["c_hat"], "tms_incidental_actual": figs["c_act"],
                "tms_other_charges": figs["h"], "tms_budget": figs["budget"], "tms_expected": figs["expected"],
                "tms_item_invoiced": figs["invoiced"], "tms_open_reservation": figs["reserved"],
                "tms_balance_available": figs["balance_j"],
                "tms_shortfall": max(figs["budget"] - figs["balance_j"], 0.0) if line.tms_release_state == "draft"
                else 0.0,
                "tms_held": line.tms_release_state == "draft" and figs["budget"] > figs["balance_j"] + 0.005
                and bool(line.tms_freight),
                "tms_contribution": figs["contribution"], "tms_contribution_pct": figs["markup_pct"],
            })

    # ------------------------------------------------------------------ figures
    def _tms_contract_charges(self):
        """Approved or billed customer charges on the item's LRs."""
        self.ensure_one()
        return self.env["tms.trip.charge"].sudo().search([
            ("consignment_id.sale_line_id", "=", self.id), ("recover_from", "=", "customer"),
            ("state", "in", ("approved", "billed"))])

    def _tms_incidental_cost(self):
        """Ĉ and C: estimated and posted cost of the estimate's incidental lines (BR-CON-06, BR-CON-09)."""
        self.ensure_one()
        est = self.sudo().tms_estimate_id
        lines = est.line_ids.filtered("incidental")
        c_hat = sum(lines.mapped("estimated_cost"))
        c_act = 0.0
        if lines:
            self.env["account.move.line"].flush_model()
            self.env.cr.execute("""
                SELECT COALESCE(SUM(aml.balance), 0) FROM account_move_line aml
                 WHERE aml.tms_estimate_line_id IN %s AND aml.parent_state = 'posted'
                   AND aml.display_type = 'product'""", (tuple(lines.ids),))
            c_act = self.env.cr.fetchone()[0] or 0.0
        return c_hat, c_act, sum(est.line_ids.mapped("estimated_cost"))

    def _tms_item_figures(self, figures=None):
        """#23.7 for one item: B_j, E_j, I_j, R_j, its target reservation and Balance_j."""
        self.ensure_one()
        line = self.sudo()
        contract = line.tms_contract_id
        figs = figures or contract._tms_figures()
        c_hat, c_act, est_cost = line._tms_incidental_cost()
        incidental_cats = contract.incidental_category_ids
        h = sum(line._tms_contract_charges().filtered(lambda c: c.category_id not in incidental_cats).mapped("amount"))
        gross = contract._tms_gross_factor(line.tms_released_on or fields.Date.context_today(self))
        freight = line.tms_freight
        budget = (freight + c_hat) * gross
        expected = (freight + line.tms_supplementary_amount + max(c_hat, c_act) + h) * gross
        invoiced = figs["inv"].get(line.id, 0.0)
        reserved = figs["res"].get(line.id, 0.0)
        target = max(expected - invoiced, 0.0) if line.tms_release_state == "released" else 0.0
        excess = contract._tms_open_excess(items=line)
        # BR-CON-26: contribution = freight less estimated cost; incidentals pass through at cost
        contribution = freight + c_hat - est_cost
        markup = contribution / est_cost * 100.0 if est_cost else 0.0
        return {"c_hat": c_hat, "c_act": c_act, "h": h, "freight": freight, "budget": budget, "expected": expected,
                "invoiced": invoiced, "reserved": reserved, "target": target, "excess": excess,
                "balance": figs["balance"], "balance_j": figs["balance"] + excess, "contribution": contribution,
                "markup_pct": markup, "est_cost": est_cost}

    def _tms_sync_reservation(self, event, source=None, consignment=None, trip=None, note=None):
        """BR-CON-20: bring R_j to max(E_j − I_j, 0) (0 once closed) with one dated ledger line."""
        Reservation = self.env["tms.contract.reservation"].sudo()
        for line in self.filtered(lambda x: x.tms_contract_id and x.tms_is_movement):
            contract = line.tms_contract_id.sudo()
            contract._tms_lock()
            figs = line._tms_item_figures()
            delta = round(figs["target"] - figs["reserved"], 2)
            if abs(delta) < 0.01:
                continue
            lr = consignment or (line.tms_lr_ids.filtered(lambda x: x.state != "cancel")[:1]
                                 if len(line.tms_lr_ids) == 1 else self.env["tms.consignment"])
            Reservation.create({
                "contract_id": contract.id,
                "sale_line_id": line.id,
                "consignment_id": lr.id if lr else False,
                "trip_id": (trip or (lr.trip_ids[:1] if lr else False) or self.env["tms.order"]).id or False,
                "event": event,
                "amount": delta,
                "source": "%s,%s" % (source._name, source.id) if source else "sale.order.line,%s" % line.id,
                "note": note,
            })
        self.invalidate_recordset(["tms_open_reservation", "tms_balance_available", "tms_expected",
                                   "tms_item_invoiced", "tms_shortfall", "tms_held"])

    # ------------------------------------------------------------------ CRUD
    @api.model_create_multi
    def create(self, vals_list):
        movement = self.env.ref("tms_estimate.product_movement", raise_if_not_found=False)
        for vals in vals_list:
            if vals.get("tms_contract_id") and not vals.get("order_id"):
                contract = self.env["tms.contract"].browse(vals["tms_contract_id"])
                if not contract.order_id:
                    raise UserError(_("Activate the contract first: items are lines of its contract order."))
                if contract.state == "closed":
                    raise UserError(_("Contract %s is closed.", contract.name))
                vals["order_id"] = contract.order_id.id
                vals.setdefault("tms_is_movement", True)
                if movement:
                    vals.setdefault("product_id", movement.id)
                vals.setdefault("product_uom_qty", 1.0)
                vals.setdefault("price_unit", 0.0)
                vals.setdefault("name", _("Contract movement"))
            elif vals.get("order_id") and not vals.get("tms_contract_id"):
                order = self.env["sale.order"].browse(vals["order_id"])
                if order.tms_contract_id:
                    vals["tms_contract_id"] = order.tms_contract_id.id
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.context.get("tms_contract_amend"):
            lane = {"tms_distance_km", "tms_origin_id", "tms_destination_id", "tms_vehicle_class_id"}
            if lane & set(vals) and any(line.tms_release_state != "draft" for line in self
                                        if line.tms_contract_id):
                raise UserError(_("A released contract item changes only through an amendment approved by the "
                                  "Operations Head (BR-CON-23, OP-18)."))
            if "price_unit" in vals and not self.env.context.get("tms_estimate_push"):
                for line in self.filtered(lambda x: x.tms_contract_id and x.tms_is_movement):
                    if abs(vals["price_unit"] - line.tms_freight) > 0.005:
                        raise UserError(_("Item %s takes its freight from the contract rates (BR-CON-05).",
                                          line.tms_item_no))
        res = super().write(vals)
        if "tms_distance_km" in vals:
            for line in self.filtered(lambda x: x.tms_contract_id and x.tms_release_state == "draft"):
                line.tms_contract_rate_ids.filtered(lambda r: r.basis in KM_BASES).write({"km": line.tms_distance_km})
        return res

    def unlink(self):
        if any(line.tms_contract_id and line.tms_release_state != "draft" for line in self):
            raise UserError(_("Released contract items are cancelled or closed, never deleted (BR-AUD-04)."))
        return super().unlink()

    def _tms_movement_amount(self):
        """BR-CON-05, BR-CON-11: a contract item's LRs share its contract freight."""
        self.ensure_one()
        if self.tms_contract_id and self.tms_is_movement:
            return self.tms_freight
        return super()._tms_movement_amount()

    def _tms_sync_price(self):
        for line in self.filtered(lambda x: x.tms_contract_id and x.tms_is_movement):
            if abs(line.price_unit - line.tms_freight) > 0.005:
                line.sudo().with_context(tms_estimate_push=True).write({"price_unit": line.tms_freight})

    # ------------------------------------------------------------------ release (BR-CON-08, 10, 26)
    def _tms_check_contract_item(self):
        for line in self:
            if not (line.tms_contract_id and line.tms_is_movement):
                raise UserError(_("Only contract items are released."))

    def _tms_refresh_rates(self, date):
        """BR-CON-05: the contract rates in force on the release date."""
        for line in self:
            contract = line.tms_contract_id.sudo()
            for use in line.sudo().tms_contract_rate_ids:
                rate = contract._tms_rate_in_force(use.rate_id, date)
                if not rate:
                    raise UserError(_("No rate for %(r)s is in force on %(d)s.", r=use.rate_id.name, d=date))
                if rate != use.rate_id or abs(rate.rate - use.rate) > 0.0001:
                    use.with_context(tms_contract_amend=True).write({"rate_id": rate.id, "rate": rate.rate})
            line._tms_sync_price()

    def _tms_below_floor(self, figs):
        self.ensure_one()
        floor = self.order_id.company_id.tms_markup_floor
        return bool(floor and figs["est_cost"] and figs["markup_pct"] < floor)

    def action_tms_release(self):
        """BR-CON-10: release iff B_j ≤ Balance_j; the budget is reserved and the estimate locks."""
        migration = self.env.context.get("tms_contract_migration")
        today = fields.Date.context_today(self)
        for line in self:
            line._tms_check_contract_item()
            if line.tms_release_state != "draft":
                raise UserError(_("Item %s is not in Draft.", line.tms_item_no))
            contract = line.tms_contract_id.sudo()
            if not migration:
                contract._tms_check_can_release(today)
            if not line.tms_contract_rate_ids:
                raise UserError(_("Item %s uses no contract rate line (BR-CON-05).", line.tms_item_no))
            contract._tms_lock()
            line._tms_refresh_rates(today)
            figs = line._tms_item_figures()
            if not migration and line._tms_below_floor(figs) and not line.tms_markup_approved_by_id:
                users = users_in_group(self.env, "tms_core.group_branch_manager", contract.company_id)
                notify_detached(self.env, line.order_id._name, line.order_id.id, users.ids,
                                _("Contract item below the markup floor"),
                                _("Item %(i)s: markup %(m).1f%% on cost", i=line.tms_item_no, m=figs["markup_pct"]))
                raise UserError(_("Item %(i)s earns %(m).1f%% on its estimated cost, below the markup floor: a Branch "
                                  "Manager approves before release (BR-CON-26).", i=line.tms_item_no,
                                  m=figs["markup_pct"]))
            shortfall = figs["budget"] - figs["balance_j"]
            if shortfall > 0.005 and not migration:
                users = contract.user_id | users_in_group(self.env, "tms_core.group_sales_executive",
                                                          contract.company_id).filtered(lambda u: u == contract.user_id)
                self.env["tms.contract.hold"]._tms_record("release", line, contract, figs["budget"], shortfall,
                                                          detached=True)
                notify_detached(self.env, contract._name, contract.id, users.ids,
                                _("Release held: item %s", line.tms_item_no),
                                _("Balance %(b).2f, budget %(B).2f, shortfall %(s).2f. Seek an extension or request "
                                  "an excess.", b=figs["balance_j"], B=figs["budget"], s=shortfall))
                raise UserError(_("Release of item %(i)s is held (BR-CON-10): balance %(b).2f, budget %(B).2f, "
                                  "shortfall %(s).2f. Only a customer extension or an approved excess covers it.",
                                  i=line.tms_item_no, b=figs["balance_j"], B=figs["budget"], s=shortfall))
            if not migration:
                contract.partner_id.tms_check_credit(figs["budget"], contract, _("Contract item release"))
            line.sudo().tms_estimate_id.action_lock()
            line.sudo().with_context(tms_estimate_push=True).write({
                "tms_release_state": "released", "tms_released_on": today, "tms_released_by_id": self.env.user.id,
                "tms_budget_reserved": figs["budget"]})
            line._tms_sync_reservation("migration" if migration else "release", source=line,
                                       note=_("Budget %.2f reserved", figs["budget"]))
            self.env["tms.contract.hold"]._tms_clear(line)
            line.order_id.message_post(body=_("Item %(i)s released: budget %(b).2f reserved, balance %(r).2f.",
                                              i=line.tms_item_no, b=figs["budget"],
                                              r=figs["balance_j"] - figs["budget"]))
            contract._tms_after_change()
        return True

    def action_tms_open_release(self):
        self.ensure_one()
        self._tms_check_contract_item()
        return {"type": "ir.actions.act_window", "name": _("Release item"),
                "res_model": "tms.contract.release.wizard", "view_mode": "form", "target": "new",
                "context": {"default_sale_line_id": self.id}}

    def action_tms_approve_markup(self):
        """BR-CON-26: a Branch Manager approves an item below the markup floor before release."""
        reason = self.env.context.get("tms_reason")
        if not reason:
            return self.env["tms.reason.wizard"].open_for(self, "action_tms_approve_markup",
                                                          _("Approve release below the markup floor"))
        if not self.env.user.has_group("tms_core.group_branch_manager"):
            raise UserError(_("A Branch Manager approves this (BR-CON-26)."))
        for line in self:
            if line.tms_estimate_id.create_uid == self.env.user:
                raise UserError(_("Whoever built the estimate cannot approve it (BR-BRN-09)."))
            line.sudo().tms_markup_approved_by_id = self.env.user
            line.order_id.sudo().message_post(body=_("Item %(i)s: release below the markup floor approved. %(r)s",
                                                     i=line.tms_item_no, r=reason))
        return True

    # ------------------------------------------------------------------ close and cancel
    def _tms_try_auto_close(self):
        """BR-CON-09: an item closes when every LR is Invoiced or Cancelled and no approved charge is unbilled."""
        for line in self.filtered(lambda x: x.tms_contract_id and x.tms_release_state == "released"):
            lrs = line.sudo().tms_lr_ids
            if not lrs or all(lr.state == "cancel" for lr in lrs):
                continue
            if any(lr.state not in ("invoiced", "cancel") for lr in lrs):
                continue
            if line._tms_contract_charges().filtered(lambda c: c.state == "approved"):
                continue
            if line.tms_supplementary_amount and line._tms_supplementary_unbilled() > 0.005:
                continue
            line._tms_close(_("Every LR invoiced or cancelled"))

    def _tms_close(self, reason, event="closure"):
        for line in self:
            state = "cancel" if event == "cancel" else "closed"
            line.sudo().write({"tms_release_state": state, "tms_close_reason": reason})
            line._tms_sync_reservation(event, source=line, note=reason)
            line.sudo().tms_excess_ids._tms_lapse()
            line.tms_contract_id._tms_update_state()
            line.tms_contract_id._tms_after_change()

    def action_tms_close_item(self):
        """BR-CON-09: the Sales Executive closes an item for billing with a reason; what remains is released."""
        reason = self.env.context.get("tms_reason")
        if not reason:
            return self.env["tms.reason.wizard"].open_for(self, "action_tms_close_item", _("Close item for billing"))
        if not (self.env.user.has_group("tms_core.group_sales_executive")
                or self.env.user.has_group("tms_core.group_branch_manager")):
            raise UserError(_("The Sales Executive closes contract items."))
        for line in self:
            if line.tms_release_state != "released":
                raise UserError(_("Only released items close for billing."))
            line._tms_close(reason)
        return True

    def action_tms_cancel_item(self):
        """UC-36 A3: a movement cancelled before any LR returns its reservation to the balance."""
        for line in self:
            line._tms_check_contract_item()
            if line.tms_release_state not in ("draft", "released"):
                raise UserError(_("Item %s is already closed.", line.tms_item_no))
            if line.tms_lr_ids.filtered(lambda lr: lr.state != "cancel"):
                raise UserError(_("Item %s has LRs: close it for billing instead (BR-CON-23).", line.tms_item_no))
            line._tms_close(_("Cancelled"), event="cancel")
        return True

    # ------------------------------------------------------------------ escalation (BR-CON-22)
    def _tms_compute_supplementary(self, date):
        for line in self:
            contract = line.tms_contract_id.sudo()
            diff = 0.0
            for use in line.sudo().tms_contract_rate_ids:
                rate = contract._tms_rate_in_force(use.rate_id, date)
                if rate:
                    diff += (rate.rate - use.rate) * use.quantity
            line.sudo().tms_supplementary_amount = round(diff, 2)

    def _tms_supplementary_unbilled(self):
        self.ensure_one()
        lines = self.env["account.move.line"].sudo().search([
            ("tms_sale_line_id", "=", self.id), ("tms_line_kind", "=", "supplementary"),
            ("parent_state", "!=", "cancel")])
        billed = sum(lines.mapped(lambda x: -x.balance))
        return self.tms_supplementary_amount - billed

    def action_tms_invoice_supplementary(self):
        """BR-CON-22: the escalation difference on a released item bills as a supplementary line."""
        self.ensure_one()
        amount = self._tms_supplementary_unbilled()
        if amount <= 0.005:
            raise UserError(_("No supplementary amount is due on this item."))
        contract = self.tms_contract_id.sudo()
        lr = self.tms_lr_ids.filtered(lambda x: x.state != "cancel")[:1]
        partner = lr.billing_partner_id.commercial_partner_id or contract.billing_partner_ids[:1] or contract.partner_id
        move = self.env["account.move"].sudo().with_company(contract.company_id).create({
            "move_type": "out_invoice",
            "partner_id": partner.id,
            "journal_id": contract.company_id.tms_branch_journal("sale").id,
            "invoice_origin": "%s/%s" % (contract.name, self.tms_item_no),
            "invoice_payment_term_id": contract.payment_term_id.id,
            "invoice_line_ids": [(0, 0, {
                "product_id": self.env.ref("tms_core.product_freight").id,
                "name": _("Diesel escalation, contract %(c)s item %(i)s", c=contract.name, i=self.tms_item_no),
                "quantity": 1, "price_unit": amount, "tms_sale_line_id": self.id,
                "tms_line_kind": "supplementary"})],
        })
        return {"type": "ir.actions.act_window", "res_model": "account.move", "res_id": move.id, "view_mode": "form"}

    def action_tms_open_amendment(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "name": _("Amend item"),
                "res_model": "tms.contract.item.amend.wizard", "view_mode": "form", "target": "new",
                "context": {"default_sale_line_id": self.id}}

    def action_tms_request_excess(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "name": _("Request excess"),
                "res_model": "tms.contract.excess.wizard", "view_mode": "form", "target": "new",
                "context": {"default_sale_line_id": self.id, "default_contract_id": self.tms_contract_id.id,
                            "default_amount": self.tms_shortfall}}


class TmsContractItemRate(models.Model):
    """BR-CON-05: the rate lines an item uses with their units (eight axle lines = 8)."""

    _name = "tms.contract.item.rate"
    _description = "Contract item rate line"
    _order = "sale_line_id, id"

    sale_line_id = fields.Many2one("sale.order.line", string="Item", required=True, ondelete="cascade", index=True)
    contract_id = fields.Many2one(related="sale_line_id.tms_contract_id", store=True)
    company_id = fields.Many2one(related="sale_line_id.company_id", store=True)
    currency_id = fields.Many2one(related="sale_line_id.currency_id")
    rate_id = fields.Many2one("tms.contract.rate", string="Contract rate", required=True,
                              domain="[('contract_id', '=', contract_id)]")
    name = fields.Char(related="rate_id.name")
    basis = fields.Selection(related="rate_id.basis")
    units = fields.Float(default=1.0)
    km = fields.Float()
    days = fields.Float()
    states = fields.Float(default=1.0)
    tonnes = fields.Float()
    trips = fields.Float(default=1.0)
    quantity = fields.Float(compute="_compute_quantity", store=True)
    rate = fields.Monetary()
    amount = fields.Monetary(compute="_compute_amount", store=True)

    @api.depends("basis", "units", "km", "days", "states", "tonnes", "trips")
    def _compute_quantity(self):
        """BR-CON-05: km, unit-km, tonnes, trips, days, states or 1."""
        for use in self:
            basis = use.basis
            qty = {
                "unit": use.units, "km": use.km, "unit_km": use.units * use.km, "day": use.days,
                "unit_day": use.units * use.days, "state": use.units * use.states, "tonne": use.tonnes,
                "trip": use.trips,
            }.get(basis, 1.0)
            use.quantity = qty

    @api.depends("quantity", "rate")
    def _compute_amount(self):
        for use in self:
            use.amount = round(use.quantity * use.rate, 2)

    @api.onchange("rate_id")
    def _onchange_rate_id(self):
        if self.rate_id and self.sale_line_id.tms_contract_id:
            rate = self.sale_line_id.tms_contract_id._tms_rate_in_force(self.rate_id, fields.Date.context_today(self))
            self.rate = (rate or self.rate_id).rate
            self.km = self.km or self.sale_line_id.tms_distance_km

    @api.model_create_multi
    def create(self, vals_list):
        today = fields.Date.context_today(self)
        for vals in vals_list:
            line = self.env["sale.order.line"].browse(vals["sale_line_id"])
            if line.tms_release_state != "draft" and not self.env.context.get("tms_contract_amend"):
                raise UserError(_("A released item's rate lines change only through an amendment (BR-CON-23)."))
            rate = self.env["tms.contract.rate"].browse(vals["rate_id"])
            if rate.contract_id != line.tms_contract_id:
                raise UserError(_("Use the rates of the item's own contract (SD-16)."))
            vals.setdefault("km", line.tms_distance_km)
            est = line.tms_estimate_id
            vals.setdefault("days", est.days if est else 0.0)
            if "rate" not in vals:
                in_force = line.tms_contract_id._tms_rate_in_force(rate, line.tms_released_on or today)
                vals["rate"] = (in_force or rate).rate
        uses = super().create(vals_list)
        uses.mapped("sale_line_id")._tms_sync_price()
        return uses

    def write(self, vals):
        if not self.env.context.get("tms_contract_amend") and any(
                use.sale_line_id.tms_release_state != "draft" for use in self):
            raise UserError(_("A released item's rate lines change only through an amendment (BR-CON-23)."))
        res = super().write(vals)
        if "rate_id" in vals and "rate" not in vals:
            today = fields.Date.context_today(self)
            for use in self:
                in_force = use.contract_id._tms_rate_in_force(use.rate_id, use.sale_line_id.tms_released_on or today)
                super(TmsContractItemRate, use).write({"rate": (in_force or use.rate_id).rate})
        self.mapped("sale_line_id")._tms_sync_price()
        return res

    def unlink(self):
        if not self.env.context.get("tms_contract_amend") and any(
                use.sale_line_id.tms_release_state != "draft" for use in self):
            raise UserError(_("A released item's rate lines change only through an amendment (BR-CON-23)."))
        lines = self.mapped("sale_line_id")
        res = super().unlink()
        lines._tms_sync_price()
        return res
