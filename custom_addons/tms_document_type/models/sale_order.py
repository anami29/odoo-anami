from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError


class SaleOrder(models.Model):
    _name = "sale.order"
    _inherit = ["sale.order", "tms.document.type.mixin"]

    tms_document_type_id = fields.Many2one(readonly=False)
    tms_vehicle_disposal = fields.Boolean(related="tms_document_type_id.vehicle_disposal")
    # BR-DOC-13
    tms_doc_approval_state = fields.Selection(
        [("none", "Not needed"), ("to_approve", "To approve"), ("approved", "Approved")],
        string="Type approval", default="none", copy=False, readonly=True, tracking=True)
    tms_doc_approved_by_id = fields.Many2one("res.users", string="Approved by", readonly=True, copy=False)
    # BR-DOC-15
    tms_disposed_vehicle_id = fields.Many2one(
        "fleet.vehicle", string="Vehicle sold", tracking=True, copy=False,
        domain="[('tms_ownership', '=', 'owned'), ('tms_sold', '=', False)]")
    tms_fleet_approved_by_id = fields.Many2one("res.users", string="Fleet Manager approval", readonly=True,
                                               copy=False, tracking=True)
    tms_finance_approved_by_id = fields.Many2one("res.users", string="Finance Head approval", readonly=True,
                                                 copy=False, tracking=True)

    def _tms_doc_applies_to(self):
        return "sale"

    def _tms_doc_is_draft(self):
        self.ensure_one()
        return self.state in ("draft", "sent")

    def _tms_doc_links(self):
        self.ensure_one()
        links = []
        lines = self.order_line
        if lines.filtered(lambda line: line.tms_estimate_id or line.tms_is_movement):
            links.append(_("a cost estimate"))
        if lines.mapped("tms_lr_ids"):
            links.append(_("LRs"))
        if lines.filtered("tms_charge_id"):
            links.append(_("a trip charge"))
        if self.tms_contract_id:
            links.append(_("a contract"))
        return links

    @api.model
    def _tms_doc_vals_flow(self, vals):
        ctx = self.env.context
        if vals.get("opportunity_id") or vals.get("tms_contract_id") or ctx.get("default_opportunity_id") \
                or ctx.get("default_tms_contract_id"):
            return True
        return any(line.get("tms_is_movement") or line.get("tms_estimate_id") or line.get("tms_charge_id")
                   for line in self._tms_doc_line_vals(vals, "order_line"))

    @api.model_create_multi
    def create(self, vals_list):
        explicit = self._tms_doc_prepare_vals(vals_list)
        Type = self.env["tms.document.type"]
        for vals in vals_list:
            dtype = Type.browse(vals.get("tms_document_type_id"))
            # BR-DOC-10: an order of a type with its own series takes its number from it
            if dtype.sequence_id and vals.get("name", _("New")) == _("New"):
                vals["name"] = dtype._tms_next_number()
        orders = super().create(vals_list)
        orders._tms_doc_after_create(explicit)
        return orders

    def _tms_doc_check_clerk(self):
        """BR-DOC-14: a Booking Clerk reads Standard sales orders but does not change them."""
        user = self.env.user
        if self.env.su or not user.has_group("tms_core.group_booking_clerk"):
            return
        if any(user.has_group(g) for g in ("tms_core.group_branch_accountant", "tms_core.group_branch_manager",
                                           "tms_core.group_finance_head", "tms_core.group_tms_admin")):
            return
        if self.filtered(lambda o: o.tms_document_behaviour == "standard"):
            raise AccessError(_("Standard sales orders are read-only for a Booking Clerk."))

    def unlink(self):
        self._tms_doc_check_clerk()
        return super().unlink()

    def write(self, vals):
        self._tms_doc_check_clerk()
        self._tms_doc_check_write(vals)
        renumber = self.browse()
        if "tms_document_type_id" in vals:
            new = self.env["tms.document.type"].browse(vals["tms_document_type_id"])
            if new.sequence_id:
                renumber = self.filtered(lambda o: o.tms_document_type_id != new and o.state in ("draft", "sent"))
        res = super().write(vals)
        for order in renumber:
            order.name = order.tms_document_type_id._tms_next_number()
        return res

    @api.constrains("tms_document_type_id")
    def _check_tms_document_type(self):
        self._tms_doc_check_type()
        for order in self:
            for line in order.order_line:
                line._tms_doc_check_line()

    @api.constrains("tms_contract_id", "tms_document_type_id")
    def _check_tms_doc_contract(self):
        for order in self.filtered(lambda o: o.tms_contract_id and o.tms_document_behaviour == "standard"):
            raise ValidationError(_("A Standard document carries no transport link: a contract."))

    @api.constrains("tms_disposed_vehicle_id", "tms_document_type_id")
    def _check_tms_disposed_vehicle(self):
        for order in self.filtered("tms_disposed_vehicle_id"):
            if not order.tms_document_type_id.vehicle_disposal:
                raise ValidationError(_("Only a vehicle sale names the vehicle it sells."))

    # ------------------------------------------------------------------ invoicing (BR-DOC-05, BR-DOC-08)
    def _prepare_invoice(self):
        res = super()._prepare_invoice()
        dtype = self.tms_document_type_id
        inv_type = dtype.invoice_type_id or self.env["tms.document.type"]._tms_default_for(
            "out_invoice", transport=dtype.behaviour != "standard")
        res["tms_document_type_id"] = inv_type.id
        if inv_type.journal_id:
            res["journal_id"] = inv_type.journal_id.id
        return res

    def _create_invoices(self, grouped=False, final=False, date=None):
        if self and all(order.tms_document_behaviour == "standard" for order in self):
            self = self.with_context(tms_allow_so_invoice=True)
        return super(SaleOrder, self)._create_invoices(grouped=grouped, final=final, date=date)

    # ------------------------------------------------------------------ confirmation (BR-DOC-13, BR-DOC-15)
    def action_confirm(self):
        held = self.browse()
        for order in self.filtered(lambda o: o.tms_document_behaviour == "standard"):
            dtype = order.tms_document_type_id
            if dtype.vehicle_disposal:
                order._tms_check_vehicle_sale()
                continue
            if dtype.approval_limit and order.amount_untaxed > dtype.approval_limit \
                    and order.tms_doc_approval_state != "approved":
                if dtype._tms_user_approves():
                    order.write({"tms_doc_approval_state": "approved", "tms_doc_approved_by_id": self.env.uid})
                else:
                    order._tms_request_type_approval()
                    held |= order
        todo = self - held
        return super(SaleOrder, todo).action_confirm() if todo else True

    def _tms_request_type_approval(self):
        for order in self:
            dtype = order.tms_document_type_id
            order.tms_doc_approval_state = "to_approve"
            users = self.env["res.users"]
            for group in dtype.approver_group_ids:
                users |= group.users.filtered(lambda u, c=order.company_id: c in u.company_ids)
            for user in users:
                order.activity_schedule("mail.mail_activity_data_todo", user_id=user.id,
                                        summary=_("Approve %s", order.name),
                                        note=_("%(t)s above its approval limit of %(l)s.", t=dtype.display_name,
                                               l=dtype.approval_limit))
            order.message_post(body=_("Above the approval limit of %(t)s: waiting for approval.",
                                      t=dtype.display_name))

    def action_tms_doc_approve(self):
        for order in self:
            if order.tms_doc_approval_state != "to_approve":
                raise UserError(_("%s is not waiting for approval.", order.name))
            if not order.tms_document_type_id._tms_user_approves():
                raise UserError(_("You may not approve %s.", order.tms_document_type_id.display_name))
            order.write({"tms_doc_approval_state": "approved", "tms_doc_approved_by_id": self.env.uid})
            order.activity_ids.filtered(lambda a: a.summary == _("Approve %s", order.name)).action_feedback(
                feedback=_("Approved"))
        return self.action_confirm()

    def _tms_vehicle_open_work(self, vehicle):
        """An open trip or assignment of the vehicle."""
        Assignment = self.env["tms.trip.assignment"].sudo()
        open_asg = Assignment.search_count([("vehicle_id", "=", vehicle.id), ("state", "=", "open"),
                                            ("trip_id.state_code", "not in", ("done", "cancel"))])
        trips = self.env["tms.order"].sudo().search_count([
            "|", ("planned_vehicle_id", "=", vehicle.id), ("primary_vehicle_id", "=", vehicle.id),
            ("state_code", "in", ("confirmed", "started", "in_transit"))])
        return open_asg or trips

    def _tms_check_vehicle_sale(self):
        for order in self:
            vehicle = order.tms_disposed_vehicle_id
            if not vehicle:
                raise UserError(_("A vehicle sale names the owned vehicle it sells."))
            if order._tms_vehicle_open_work(vehicle):
                raise UserError(_("%s has an open trip or assignment; it cannot be sold yet.",
                                  vehicle.display_name))
            if not order.tms_fleet_approved_by_id:
                raise UserError(_("Vehicle sale %s awaits the Fleet Manager's approval.", order.name))
            if not order.tms_finance_approved_by_id:
                raise UserError(_("Vehicle sale %s awaits the Finance Head's approval.", order.name))

    def action_tms_approve_vehicle_fleet(self):
        if not self.env.user.has_group("tms_core.group_fleet_manager"):
            raise UserError(_("The Fleet Manager approves vehicle sales."))
        self.filtered("tms_vehicle_disposal").write({"tms_fleet_approved_by_id": self.env.uid})
        return True

    def action_tms_approve_vehicle_finance(self):
        if not self.env.user.has_group("tms_core.group_finance_head"):
            raise UserError(_("The Finance Head approves vehicle sales."))
        self.filtered("tms_vehicle_disposal").write({"tms_finance_approved_by_id": self.env.uid})
        return True

    def _tms_vehicle_delivered(self):
        """BR-DOC-15: on delivery the vehicle moves to Sold and its unreleased prepaid costs release."""
        for order in self.filtered(lambda o: o.tms_vehicle_disposal and o.state == "sale"):
            vehicle = order.tms_disposed_vehicle_id.sudo()
            if not vehicle or vehicle.tms_sold:
                continue
            lines = order.order_line.filtered(lambda line: not line.display_type)
            if any(line.qty_delivered < line.product_uom_qty for line in lines):
                continue
            vehicle._tms_mark_sold(order)


class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    def _tms_doc_check_line(self):
        for line in self.filtered(lambda line: not line.display_type):
            dtype = line.order_id.tms_document_type_id
            if dtype.behaviour == "standard":
                if line.tms_is_movement or line.tms_estimate_id:
                    raise ValidationError(_("A Standard document carries no transport link: a movement item "
                                            "with its cost estimate."))
                if line.tms_charge_id:
                    raise ValidationError(_("A Standard document carries no transport link: a trip charge."))
            self.env["tms.document.type.mixin"]._tms_doc_check_product(dtype, line.product_id,
                                                                        line.name or line.product_id.name)

    @api.constrains("product_id", "tms_is_movement", "tms_estimate_id", "tms_charge_id")
    def _check_tms_doc_line(self):
        self._tms_doc_check_line()

    @api.model_create_multi
    def create(self, vals_list):
        orders = self.env["sale.order"].browse([vals.get("order_id") for vals in vals_list if vals.get("order_id")])
        if orders.filtered(lambda o: o.tms_document_behaviour == "standard"):
            for vals in vals_list:
                order = self.env["sale.order"].browse(vals.get("order_id"))
                if order.tms_document_behaviour == "standard" and vals.get("tms_is_movement"):
                    raise ValidationError(_("A Standard document carries no transport link: a movement item "
                                            "with its cost estimate."))
        return super().create(vals_list)

    def write(self, vals):
        res = super().write(vals)
        if "qty_delivered" in vals:
            self.mapped("order_id")._tms_vehicle_delivered()
        return res
