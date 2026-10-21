from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from odoo.addons.tms_core.models.tms_consignment import CARGO_TYPES
from odoo.addons.tms_core.models.tools import notify_detached, schedule, users_in_group


class SaleOrder(models.Model):
    _inherit = "sale.order"

    tms_attach_cost_sheet = fields.Boolean(string="Attach cost sheet", help="BR-QTN-13, BR-EST-07")
    tms_last_item_no = fields.Integer(copy=False, readonly=True, string="Last item number")
    tms_acceptance_file = fields.Binary(string="Written acceptance", attachment=True, copy=False)
    tms_acceptance_name = fields.Char(copy=False, string="Acceptance file name")
    tms_expired = fields.Boolean(compute="_compute_tms_expired", search="_search_tms_expired", string="Expired")
    tms_needs_approval = fields.Boolean(compute="_compute_tms_needs_approval", string="Needs approval")
    tms_estimated_cost = fields.Monetary(compute="_compute_tms_cost", string="Estimated cost")
    tms_markup_pct = fields.Float(compute="_compute_tms_cost", string="Overall markup %")
    tms_lr_count = fields.Integer(compute="_compute_tms_lr_count", string="LRs")

    @api.depends("validity_date", "state")
    def _compute_tms_expired(self):
        today = fields.Date.context_today(self)
        for order in self:
            order.tms_expired = order.state in ("draft", "sent") and order.validity_date \
                and order.validity_date < today

    def _search_tms_expired(self, operator, value):
        today = fields.Date.context_today(self)
        dom = [("state", "in", ("draft", "sent")), ("validity_date", "<", today)]
        if (operator == "=" and value) or (operator == "!=" and not value):
            return dom
        return ["!", "&"] + dom

    @api.depends("order_line.tms_approval_state")
    def _compute_tms_needs_approval(self):
        for order in self:
            order.tms_needs_approval = any(line.tms_approval_state == "to_approve" for line in order.order_line)

    @api.depends("order_line.tms_estimate_id.estimated_cost", "order_line.price_subtotal")
    def _compute_tms_cost(self):
        for order in self:
            cost = sum(order.order_line.mapped("tms_estimate_id.estimated_cost"))
            revenue = sum(order.order_line.filtered("tms_estimate_id").mapped("price_subtotal"))
            order.tms_estimated_cost = cost
            order.tms_markup_pct = ((revenue - cost) / cost * 100.0) if cost else 0.0

    def _compute_tms_lr_count(self):
        for order in self:
            order.tms_lr_count = self.env["tms.consignment"].search_count([("sale_line_id.order_id", "=", order.id)])

    @api.depends("company_id")
    def _compute_validity_date(self):
        """BR-QTN-01, BR-CFG-12: validity from the Transport setting (default 15 days)."""
        super()._compute_validity_date()
        today = fields.Date.context_today(self)
        for order in self:
            days = order.company_id.tms_quotation_validity_days
            if days and order.state in ("draft", "sent"):
                order.validity_date = today + timedelta(days)

    # ------------------------------------------------------------------
    def _tms_check_customer_master(self):
        """BR-QTN-07."""
        self.ensure_one()
        partner = self.partner_id.commercial_partner_id
        missing = []
        if not partner.vat and not partner.tms_unregistered:
            missing.append(_("GSTIN or the unregistered flag"))
        if not partner.l10n_in_pan:
            missing.append(_("PAN"))
        if not partner.tms_freight_basis:
            missing.append(_("default freight basis"))
        if not partner.tms_billing_cycle:
            missing.append(_("billing cycle"))
        if not partner.tms_credit_limit:
            missing.append(_("credit limit"))
        if missing:
            raise UserError(_("Customer %(c)s lacks %(m)s (BR-QTN-07).", c=partner.display_name, m=", ".join(missing)))

    def _tms_check_items_before_send(self):
        for order in self:
            for line in order.order_line.filtered("tms_estimate_id"):
                line.tms_estimate_id._tms_ensure_misc()
                line._tms_update_approval()
            if order.tms_needs_approval:
                raise UserError(_("Items below the markup floor or above the value limit need approval before the "
                                  "quotation is sent (BR-QTN-05, BR-EST-08)."))

    def action_quotation_send(self):
        self._tms_check_items_before_send()
        return super().action_quotation_send()

    def action_quotation_sent(self):
        self._tms_check_items_before_send()
        res = super().action_quotation_sent()
        self._tms_crm_stage("quoted")
        return res

    def message_post(self, **kwargs):
        """BR-CRM-06: sending the quotation by email (mark_so_as_sent) also moves the opportunity to Quoted."""
        sending = self.env.context.get("mark_so_as_sent")
        drafts = self.filtered(lambda o: o.state == "draft") if sending else self.browse()
        res = super().message_post(**kwargs)
        if drafts:
            drafts._tms_crm_stage("quoted")
        return res

    def _tms_is_contract_order(self):
        """Hook: tms_contract marks its contract orders."""
        return False

    def _confirmation_error_message(self):
        msg = super()._confirmation_error_message()
        if msg or self._tms_is_contract_order():
            return msg
        if self.tms_needs_approval:
            return _("Items awaiting markup or value approval cannot be confirmed (BR-QTN-05).")
        return False

    def action_confirm(self):
        for order in self.filtered(lambda o: not o._tms_is_contract_order()):
            tms_lines = order.order_line.filtered(lambda line: line.tms_estimate_id or line.tms_is_movement)
            if not tms_lines:
                continue
            order._tms_check_customer_master()
            missing = order.order_line.filtered(lambda line: line.tms_is_movement and not line.tms_estimate_id)
            if missing:
                raise UserError(_("Every order item has a cost estimate (BR-EST-01): %s",
                                  ", ".join(str(n) for n in missing.mapped("tms_item_no"))))
            # BR-QTN-12: an expired quotation does not confirm; copy it with fresh costs
            if order.tms_expired:
                raise UserError(_("Quotation %s has expired; copy it with fresh costs (BR-QTN-12).", order.name))
            # BR-QTN-06: portal signature or written acceptance, always
            if not order.signature and not order.tms_acceptance_file \
                    and not self.env.context.get("tms_skip_acceptance"):
                raise UserError(_("Confirm only with the customer's portal signature or the attached written "
                                  "acceptance (BR-QTN-06)."))
            order._tms_check_items_before_send()
            order._tms_check_contract_customer()
            # BR-CRD-02 quotation confirmation
            order.partner_id.tms_check_credit(order.amount_untaxed, order, _("Quotation confirmation"))
        res = super().action_confirm()
        for order in self:
            # BR-QTN-08, BR-EST-10: lock every estimate; nothing is written to a rate card
            order.order_line.mapped("tms_estimate_id").filtered(lambda e: not e.is_contract).action_lock()
        self._tms_crm_stage("won")
        return res

    def _tms_check_contract_customer(self):
        """Hook: tms_contract needs Branch Manager approval for an Active contract customer (BR-CON-15)."""
        return True

    def _action_cancel(self):
        res = super()._action_cancel()
        for order in self:
            lead = order.opportunity_id
            if lead and not lead.order_ids.filtered(lambda o: o.state in ("draft", "sent", "sale")):
                order._tms_crm_stage("negotiation")
        return res

    def _tms_crm_stage(self, code):
        """BR-CRM-06."""
        for order in self.filtered("opportunity_id"):
            xmlid = {"new": "crm.stage_lead1", "qualified": "crm.stage_lead2", "quoted": "crm.stage_lead3",
                     "negotiation": "tms_estimate.crm_stage_negotiation", "won": "crm.stage_lead4"}[code]
            stage = self.env.ref(xmlid, raise_if_not_found=False)
            lead = order.opportunity_id
            if not stage or not lead.active:
                continue
            if code == "quoted" and lead.stage_id.sequence >= stage.sequence:
                continue
            if code == "won":
                lead.action_set_won()
            lead.stage_id = stage

    # BR-QTN-10: sales orders never invoice
    def _create_invoices(self, grouped=False, final=False, date=None):
        if not self.env.context.get("tms_allow_so_invoice"):
            raise UserError(_("Freight and charges are invoiced only from LRs (BR-QTN-10)."))
        return super()._create_invoices(grouped=grouped, final=final, date=date)

    @api.depends("order_line.invoice_status")
    def _compute_invoice_status(self):
        super()._compute_invoice_status()
        for order in self:
            if order.order_line.filtered("tms_estimate_id") or order._tms_is_contract_order():
                order.invoice_status = "no"

    def action_tms_copy_fresh(self):
        """BR-EST-09, BR-QTN-12: an expired quotation is copied with fresh costs."""
        self.ensure_one()
        new = self.copy()
        key = lambda line: (line.sequence, line.id)  # noqa: E731
        olds = self.order_line.filtered("tms_estimate_id").sorted(key)
        skip = {"sale_line_id", "name", "state", "version", "date", "days", "is_contract"}
        for i, line in enumerate(new.order_line.filtered("tms_estimate_id").sorted(key)):
            est = line.tms_estimate_id
            old = olds[i] if i < len(olds) else False
            if old and not est.line_ids:
                # carry the lane, pieces, route and cost lines of the original estimate (BR-QTN-12)
                vals = {k: v for k, v in old.tms_estimate_id.copy_data()[0].items() if k not in skip}
                est.with_context(tms_allow_typed=True).write(vals)
            for eline in est.line_ids:
                eline.with_context(tms_allow_typed=True).write({"unit_cost": eline._tms_price(),
                                                                 "typed_cost_reason": False})
            est._tms_push_price()
        return {"type": "ir.actions.act_window", "res_model": "sale.order", "res_id": new.id, "view_mode": "form"}

    def action_view_lrs(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "name": _("LRs"), "res_model": "tms.consignment",
                "view_mode": "list,form", "domain": [("sale_line_id.order_id", "=", self.id)]}

    @api.model
    def _cron_quotation_expiry(self):
        """BR-NTF-11: sent quotations expiring within 3 days."""
        today = fields.Date.context_today(self)
        for order in self.search([("state", "=", "sent"), ("validity_date", ">=", today),
                                  ("validity_date", "<=", today + timedelta(days=3))]):
            schedule(order, order.user_id, _("Quotation expires on %s", order.validity_date))

    def action_open_credit_override(self):
        """BR-CRD-03: the Branch Manager or Finance Head confirms a credit-blocked quotation with a reason."""
        return self.env["tms.reason.wizard"].open_for(self, "action_confirm_credit_override",
                                                      _("Override the credit block"))

    def action_confirm_credit_override(self):
        reason = self.env.context.get("tms_reason")
        if not reason:
            raise UserError(_("A credit override needs a reason (BR-CRD-03)."))
        return self.with_context(tms_credit_override_reason=reason).action_confirm()


class SaleOrderLine(models.Model):
    _name = "sale.order.line"
    _inherit = ["sale.order.line", "tms.item.number.mixin"]

    tms_is_movement = fields.Boolean(string="Movement item", help="Priced from its cost estimate.")
    tms_cargo_type = fields.Selection(CARGO_TYPES, string="Cargo Type", default="regular")
    tms_estimate_id = fields.Many2one("tms.estimate", string="Estimate", copy=False, readonly=True)
    tms_origin_id = fields.Many2one("res.partner", string="Origin", domain=[("tms_location", "=", True)])
    tms_destination_id = fields.Many2one("res.partner", string="Destination", domain=[("tms_location", "=", True)])
    tms_vehicle_class_id = fields.Many2one("tms.vehicle.class", string="Vehicle type")
    tms_distance_km = fields.Float(string="Distance (km)")
    tms_rate_per_km = fields.Monetary(string="Rate per km", currency_field="currency_id")
    tms_estimated_cost = fields.Monetary(related="tms_estimate_id.estimated_cost", string="Estimated cost")
    tms_marked_up = fields.Monetary(related="tms_estimate_id.marked_up_total", string="Marked-up value")
    tms_markup_pct = fields.Float(related="tms_estimate_id.markup_pct", string="Markup %")
    tms_approval_state = fields.Selection(
        [("not_needed", "Not needed"), ("to_approve", "To approve"), ("approved", "Approved")],
        default="not_needed", copy=False, readonly=True, string="Approval")
    tms_approval_role = fields.Selection([("bm", "Branch Manager"), ("oh", "Operations Head")], readonly=True,
                                         copy=False,
        string="Approval role")
    tms_approved_by_id = fields.Many2one("res.users", readonly=True, copy=False, string="Approved by")
    tms_approved_amount = fields.Monetary(readonly=True, copy=False, currency_field="currency_id", string="Approved amount")
    tms_charge_id = fields.Many2one("tms.trip.charge", string="From charge", readonly=True, copy=False)
    tms_lr_ids = fields.One2many("tms.consignment", "sale_line_id", string="LRs")
    tms_lr_charged_weight = fields.Float(compute="_compute_tms_lr", string="LR charged weight")
    tms_freight_invoiced = fields.Monetary(compute="_compute_tms_lr", string="Freight invoiced",
                                           currency_field="currency_id")
    tms_label = fields.Char(compute="_compute_tms_label", string="Item reference")

    @api.depends("order_id.name", "tms_item_no")
    def _compute_tms_label(self):
        for line in self:
            line.tms_label = "%s/%s" % (line.order_id.name or "", line.tms_item_no or "")

    def _compute_tms_lr(self):
        for line in self:
            lrs = line.tms_lr_ids.filtered(lambda lr: lr.state != "cancel")
            line.tms_lr_charged_weight = sum(lrs.mapped("charged_weight"))
            line.tms_freight_invoiced = sum(lrs.mapped("freight_invoiced"))

    @api.depends("order_id.name", "tms_item_no", "name")
    def _compute_display_name(self):
        super()._compute_display_name()
        for line in self.filtered("tms_item_no"):
            line.display_name = "%s/%s %s" % (line.order_id.name, line.tms_item_no,
                                              (line.name or "").split("\n")[0][:60])

    def _compute_price_unit(self):
        """Movement items keep the amount their estimate (or contract) gives them."""
        tms_lines = self.filtered(lambda line: line.tms_estimate_id or line.tms_charge_id)
        for line in tms_lines.filtered(lambda line: line.tms_estimate_id and not line.tms_estimate_id.is_contract):
            line.price_unit = line.tms_estimate_id.amount
        return super(SaleOrderLine, self - tms_lines)._compute_price_unit()

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        for line in lines.filtered(lambda line: line.tms_is_movement and not line.tms_estimate_id):
            line._tms_create_estimate()
        return lines

    def _tms_create_estimate(self):
        self.ensure_one()
        est = self.env["tms.estimate"].create({
            "sale_line_id": self.id,
            "origin_id": self.tms_origin_id.id,
            "destination_id": self.tms_destination_id.id,
            "vehicle_class_id": self.tms_vehicle_class_id.id,
            "distance_km": self.tms_distance_km,
            "is_contract": self.order_id._tms_is_contract_order(),
        })
        self.with_context(tms_estimate_push=True).tms_estimate_id = est
        return est

    def write(self, vals):
        lane = {"tms_origin_id", "tms_destination_id", "tms_vehicle_class_id", "tms_distance_km"}
        if not self.env.context.get("tms_estimate_push"):
            if "price_unit" in vals:
                for line in self.filtered(lambda line: line.tms_estimate_id and not line.tms_estimate_id.is_contract):
                    if abs(vals["price_unit"] - line.tms_estimate_id.amount) > 0.005:
                        raise UserError(_("Item %s takes its amount from its cost estimate (BR-QTN-02).",
                                          line.tms_item_no))
        res = super().write(vals)
        if lane & set(vals) and not self.env.context.get("tms_estimate_push"):
            for line in self.filtered("tms_estimate_id"):
                est = line.tms_estimate_id
                est.write({"origin_id": line.tms_origin_id.id, "destination_id": line.tms_destination_id.id,
                           "vehicle_class_id": line.tms_vehicle_class_id.id,
                           "distance_km": line.tms_distance_km})
        if {"price_unit", "product_uom_qty", "tms_estimate_id"} & set(vals):
            self.filtered(lambda line: line.tms_approval_state != "not_needed")._tms_update_approval()
        if "price_unit" in vals:
            self.mapped("tms_lr_ids")._tms_reprice_movement()
        return res

    def unlink(self):
        if any(line.tms_lr_ids for line in self):
            raise UserError(_("Items with LRs are never deleted."))
        return super().unlink()

    def _tms_update_approval(self):
        """BR-EST-08: below the markup floor → Branch Manager; above the value limit → Operations Head.
        Reopening an item cancels its approval (BR-QTN-05)."""
        for line in self:
            est = line.tms_estimate_id
            if not est or est.is_contract:
                line.tms_approval_state = "not_needed"
                continue
            company = line.order_id.company_id
            role = False
            if est.amount > company.tms_value_limit:
                role = "oh"
            elif est.estimated_cost and est.markup_pct < company.tms_markup_floor:
                role = "bm"
            if not role:
                line.write({"tms_approval_state": "not_needed", "tms_approval_role": False})
                continue
            if line.tms_approval_state == "approved" and abs(line.tms_approved_amount - est.amount) < 0.005:
                continue
            line.write({"tms_approval_state": "to_approve", "tms_approval_role": role, "tms_approved_by_id": False})
            group = "tms_core.group_branch_manager" if role == "bm" else "tms_core.group_operations_head"
            users = users_in_group(self.env, group, company) - est.create_uid
            schedule(line.order_id, users, _("Item %s needs markup/value approval", line.tms_item_no))  # BR-NTF-09

    def action_tms_approve(self):
        for line in self:
            if line.tms_approval_state != "to_approve":
                continue
            group = "tms_core.group_branch_manager" if line.tms_approval_role == "bm" \
                else "tms_core.group_operations_head"
            if not self.env.user.has_group(group):
                raise UserError(_("This item needs the %s's approval (BR-EST-08).",
                                  dict(line._fields["tms_approval_role"].selection)[line.tms_approval_role]))
            if line.tms_estimate_id.create_uid == self.env.user or line.tms_estimate_id.line_ids.filtered(
                    lambda e: e.create_uid == self.env.user):
                raise UserError(_("Whoever built the estimate cannot approve it (BR-EST-08, BR-BRN-09)."))
            line.write({"tms_approval_state": "approved", "tms_approved_by_id": self.env.user.id,
                        "tms_approved_amount": line.tms_estimate_id.amount})
        return True

    def action_tms_open_estimate(self):
        self.ensure_one()
        est = self.tms_estimate_id or self._tms_create_estimate()
        return {"type": "ir.actions.act_window", "res_model": "tms.estimate", "res_id": est.id, "view_mode": "form"}

    def _tms_movement_amount(self):
        """Amount of one movement of this item (BR-QTN-03): the line's unit price."""
        self.ensure_one()
        return self.price_unit
