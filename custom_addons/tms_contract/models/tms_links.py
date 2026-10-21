from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from odoo.addons.tms_core.models.tools import notify_detached, schedule, users_in_group


class TmsConsignment(models.Model):
    """BR-CON-11, BR-CON-15, BR-CON-24."""

    _inherit = "tms.consignment"

    tms_contract_id = fields.Many2one(related="sale_line_id.tms_contract_id", store=True, string="Contract")

    def _tms_active_contract_for_customer(self):
        self.ensure_one()
        partners = (self.billing_partner_id | self.consignor_id).mapped("commercial_partner_id")
        if not partners:
            return self.env["tms.contract"]
        return self.env["tms.contract"].sudo().search([("partner_id", "in", partners.ids), ("state", "=", "active")])

    def _tms_check_typed_rate_contract(self):
        """BR-CON-15: a typed-rate LR for a customer with an Active contract needs Branch Manager approval."""
        res = super()._tms_check_typed_rate_contract()
        for lr in self.filtered(lambda x: not x.typed_rate_approved_by_id):
            contracts = lr._tms_active_contract_for_customer()
            if contracts:
                users = users_in_group(self.env, "tms_core.group_branch_manager", lr.company_id)
                notify_detached(self.env, lr._name, lr.id, users.ids, _("Typed-rate LR for a contract customer"),
                                _("LR %(lr)s, contract %(c)s", lr=lr.name, c=", ".join(contracts.mapped("name"))))
                raise UserError(_("%(p)s holds Active contract %(c)s: book against a released contract item, or have "
                                  "a Branch Manager approve the typed rate with a reason (BR-CON-15).",
                                  p=contracts[:1].partner_id.display_name, c=", ".join(contracts.mapped("name"))))
        return res

    def action_tms_approve_contract_rate(self):
        reason = self.env.context.get("tms_reason")
        if not reason:
            return self.env["tms.reason.wizard"].open_for(self, "action_tms_approve_contract_rate",
                                                          _("Approve typed rate for a contract customer"))
        if not self.env.user.has_group("tms_core.group_branch_manager"):
            raise UserError(_("A Branch Manager approves this (BR-CON-15)."))
        for lr in self:
            if lr.state != "draft":
                raise UserError(_("Approve the typed rate before confirmation."))
            lr.sudo().write({"typed_rate_approved_by_id": self.env.user.id})
            self.env["tms.override.log"].log("contract_customer_rate", lr,
                                             _("Typed rate %(r)s on LR %(lr)s for a contract customer",
                                               r=lr.typed_rate, lr=lr.name),
                                             reason=reason, amount=lr.freight_amount)
        return True

    def _tms_check_contract_booking(self):
        """BR-CON-11, BR-CON-24: LRs book only against a released item, while the contract runs."""
        for lr in self.filtered(lambda x: x.sale_line_id.tms_contract_id and x.state != "cancel"):
            item = lr.sale_line_id.sudo()
            contract = item.tms_contract_id
            if item.tms_release_state != "released":
                raise UserError(_("LRs book only against a released contract item; item %(i)s is %(s)s "
                                  "(BR-CON-11).", i=item.tms_item_no,
                                  s=dict(item._fields["tms_release_state"].selection)[item.tms_release_state]))
            if contract.state in ("draft", "closed"):
                raise UserError(_("Contract %s is not open for booking.", contract.name))
            date = lr.date or fields.Date.context_today(self)
            runon = contract.company_id.tms_contract_runon_days
            if date > contract.date_end + timedelta(days=runon):
                raise UserError(_("Contract %(c)s ended on %(d)s and its %(n)s run-on days are over (BR-CON-11).",
                                  c=contract.name, d=contract.date_end, n=runon))
            if date < contract.date_start:
                raise UserError(_("Contract %s has not started.", contract.name))
            if lr.company_id not in contract.branch_ids:
                raise UserError(_("Branch %(b)s may not book LRs against contract %(c)s (BR-CON-24).",
                                  b=lr.company_id.name, c=contract.name))
            if lr.freight_basis == "topay":
                raise UserError(_("Contract LRs are To-Be-Billed or Paid; To-Pay is refused (BR-CON-24)."))
            party = lr.billing_partner_id
            if party and party not in contract.billing_partner_ids and \
                    party.parent_id not in contract.billing_partner_ids:
                raise UserError(_("%(p)s is not a billing party of contract %(c)s (BR-CON-24).",
                                  p=party.display_name, c=contract.name))

    @api.model_create_multi
    def create(self, vals_list):
        lrs = super().create(vals_list)
        lrs._tms_check_contract_booking()
        return lrs

    def write(self, vals):
        res = super().write(vals)
        if {"sale_line_id", "freight_basis", "billing_partner_id", "company_id", "date", "consignor_id"} & set(vals) \
                and not self.env.context.get("tms_reprice"):
            self._tms_check_contract_booking()
        return res

    def action_confirm(self):
        self.filtered(lambda x: x.state == "draft")._tms_check_contract_booking()
        return super().action_confirm()

    def action_cancel(self):
        items = self.mapped("sale_line_id").filtered("tms_contract_id")
        res = super().action_cancel()
        items._tms_try_auto_close()
        return res

    def _tms_invoice_group_key(self):
        """One invoice per contract (BR-INV-04)."""
        return super()._tms_invoice_group_key() + (self.sale_line_id.tms_contract_id.id or 0,)

    def _tms_invoice_header_vals(self):
        vals = super()._tms_invoice_header_vals()
        contract = self.sale_line_id.tms_contract_id
        if contract and contract.payment_term_id:
            vals["invoice_payment_term_id"] = contract.payment_term_id.id
        return vals


class TmsConsignmentAmendment(models.Model):
    _inherit = "tms.consignment.amendment"

    def _tms_after_approve(self):
        """BR-CON-23: a diversion on a released contract item recomputes its freight and budget."""
        res = super()._tms_after_approve()
        for am in self:
            lr = am.consignment_id
            item = lr.sale_line_id
            if not (item.tms_contract_id and am.is_diversion and am.diversion_km):
                continue
            if not self.env.user.has_group("tms_core.group_operations_head"):
                raise UserError(_("A diversion on contract item %s needs the Operations Head (BR-CON-23).",
                                  item.tms_item_no))
            item = item.sudo()
            figs_before = item._tms_item_figures()
            uses = item.tms_contract_rate_ids.filtered(lambda u: u.basis in ("km", "unit_km"))
            for use in uses:
                use.with_context(tms_contract_amend=True).write({"km": use.km + am.diversion_km})
            item._tms_sync_price()
            figs = item._tms_item_figures()
            increase = figs["target"] - figs_before["target"]
            if increase > figs_before["balance_j"] + 0.005:
                raise UserError(_("The diversion adds %(a).2f to item %(i)s but the contract balance is %(b).2f "
                                  "(BR-CON-23).", a=increase, i=item.tms_item_no, b=figs_before["balance_j"]))
            item._tms_sync_reservation("amendment", source=am, consignment=lr,
                                       note=_("Diversion of %.0f km", am.diversion_km))
            item.tms_contract_id._tms_after_change()
        return res


class TmsEstimate(models.Model):
    _inherit = "tms.estimate"

    def _tms_after_amendment(self, amendment):
        """BR-CON-23: an amended contract item re-checks any increase and logs an amendment line."""
        res = super()._tms_after_amendment(amendment)
        for est in self.filtered("is_contract"):
            item = est.sale_line_id.sudo()
            if item.tms_release_state != "released":
                continue
            if not self.env.user.has_group("tms_core.group_operations_head"):
                raise UserError(_("Amending released contract item %s needs the Operations Head (BR-CON-23).",
                                  item.tms_item_no))
            figs = item._tms_item_figures()
            increase = figs["target"] - figs["reserved"]
            if increase > figs["balance_j"] + 0.005:
                raise UserError(_("The amendment adds %(a).2f to item %(i)s but the contract balance is %(b).2f "
                                  "(BR-CON-23).", a=increase, i=item.tms_item_no, b=figs["balance_j"]))
            item._tms_sync_reservation("amendment", source=amendment, note=amendment.reason)
            item.tms_contract_id._tms_after_change()
        return res


class TmsEstimateLine(models.Model):
    _inherit = "tms.estimate.line"

    @api.model_create_multi
    def create(self, vals_list):
        """BR-CON-06: on a contract item the incidental lines carry their expected cost at 0% markup."""
        for vals in vals_list:
            est = self.env["tms.estimate"].browse(vals.get("estimate_id"))
            product = self.env["product.product"].browse(vals.get("product_id"))
            if est.is_contract and product.tms_cost_category_id.incidental:
                vals["markup_pct"] = 0.0
        return super().create(vals_list)


class TmsTripCharge(models.Model):
    _inherit = "tms.trip.charge"

    tms_cost_line_id = fields.Many2one(
        "account.move.line", string="Cost bill line",
        domain=[("move_id.move_type", "=", "in_invoice"), ("display_type", "=", "product")],
        help="The bill whose posted amount an incidental charge on a contract bills at (BR-CON-07).")
    tms_contract_id = fields.Many2one(related="consignment_id.sale_line_id.tms_contract_id", string="Contract")
    tms_contract_incidental = fields.Boolean(compute="_compute_tms_contract_incidental",
                                             string="Contract incidental")

    @api.depends("consignment_id", "category_id", "recover_from")
    def _compute_tms_contract_incidental(self):
        for charge in self:
            contract = charge.sudo().consignment_id.sale_line_id.tms_contract_id
            charge.tms_contract_incidental = bool(contract) and charge.recover_from == "customer" \
                and charge.category_id in contract.incidental_category_ids

    def _tms_on_contract(self):
        self.ensure_one()
        return self.recover_from == "customer" and bool(self.sudo().consignment_id.sale_line_id.tms_contract_id)

    def _tms_needs_bm_approval(self):
        """BR-CHG-04: every customer charge on a contract item needs Branch Manager approval."""
        return super()._tms_needs_bm_approval() or self._tms_on_contract()

    def _tms_contract_actual_cost(self):
        """BR-CON-07: the amount of the charge's posted bill or expense."""
        self.ensure_one()
        exp = self.sudo().expense_id
        if exp and exp.state == "approved" and exp.move_id.state == "posted":
            return exp.amount
        line = self.sudo().tms_cost_line_id
        if line and line.parent_state == "posted":
            return abs(line.balance)
        return None

    def action_confirm(self):
        for charge in self.filtered(lambda c: c.state == "draft" and c.tms_contract_incidental):
            actual = charge._tms_contract_actual_cost()
            if actual:
                charge.amount = actual
        return super().action_confirm()

    def action_approve(self):
        """BR-CON-07: a charge outside the contract's incidental categories needs the Operations Head."""
        noncontract = self.filtered(lambda c: c.state == "to_approve" and c._tms_on_contract()
                                    and not c.tms_contract_incidental)
        for charge in noncontract:
            if not self.env.user.has_group("tms_core.group_operations_head"):
                raise UserError(_("Charge %s is outside the contract's incidental categories: the Operations Head "
                                  "approves it before it bills under the contract (BR-CON-07).", charge.name))
            if charge.create_uid == self.env.user:
                raise UserError(_("You cannot approve a charge you raised."))
            charge.write({"state": "approved", "approved_by_id": self.env.user.id,
                          "approved_on": fields.Datetime.now()})
            charge.activity_ids.action_feedback(feedback=_("Approved"))
            self.env["tms.override.log"].log(
                "noncontract_charge", charge,
                _("Charge %(c)s (%(cat)s) billed under contract %(k)s", c=charge.name, cat=charge.category_id.name,
                  k=charge.tms_contract_id.name),
                reason=charge.note or _("Approved by the Operations Head"), amount=charge.amount)
            charge._tms_on_approved()
        return super(TmsTripCharge, self - noncontract).action_approve() if self - noncontract else True

    def _tms_on_approved(self):
        """BR-CON-20: an approved customer charge outside the incidentals tops up the item's reservation."""
        res = super()._tms_on_approved()
        for charge in self.filtered(lambda c: c._tms_on_contract()):
            item = charge.sudo().consignment_id.sale_line_id
            item._tms_sync_reservation("charge", source=charge, consignment=charge.consignment_id,
                                       trip=charge.trip_id)
            item.tms_contract_id._tms_after_change()
        return res

    def _tms_contract_resync(self):
        for charge in self.filtered(lambda c: c._tms_on_contract()):
            item = charge.sudo().consignment_id.sale_line_id
            item._tms_sync_reservation("charge", source=charge, consignment=charge.consignment_id,
                                       note=_("Charge %s withdrawn", charge.name))
            item._tms_try_auto_close()

    def action_cancel(self):
        res = super().action_cancel()
        self._tms_contract_resync()
        return res

    def action_waive(self):
        res = super().action_waive()
        self._tms_contract_resync()
        return res


class TmsOrder(models.Model):
    _inherit = "tms.order"

    def _tms_free_hours(self, customer):
        """BR-MD-16: a contract's free hours and detention rate override the customer's."""
        contracts = self.consignment_ids.filtered(
            lambda lr: lr.billing_partner_id.commercial_partner_id == customer).mapped("sale_line_id.tms_contract_id")
        contract = contracts.sudo().filtered(lambda c: c.detention_rate)[:1]
        if contract:
            return contract.free_hours_loading, contract.free_hours_unloading, contract.detention_rate
        return super()._tms_free_hours(customer)


class ResPartner(models.Model):
    _inherit = "res.partner"

    def _tms_open_reservations(self):
        """BR-CRD-01: open contract reservations count in exposure, net of the delivered uninvoiced freight
        that exposure already counts."""
        res = super()._tms_open_reservations()
        partner = self.commercial_partner_id
        contracts = self.env["tms.contract"].sudo().search([("partner_id", "=", partner.id),
                                                            ("state", "not in", ("draft", "closed"))])
        total = 0.0
        for contract in contracts:
            figs = contract._tms_figures()
            for item in contract.item_ids.filtered(lambda i: i.tms_release_state == "released"):
                reserved = figs["res"].get(item.id, 0.0)
                counted = sum(item.tms_lr_ids.filtered(
                    lambda lr: lr.state in ("delivered", "not_delivered") and lr.freight_basis != "paid"
                    and lr.billing_partner_id.commercial_partner_id == partner).mapped("freight_uninvoiced"))
                total += max(reserved - counted, 0.0)
        return res + total


class TmsGtaOption(models.Model):
    _inherit = "tms.gta.option"

    def _tms_restate_contracts(self):
        """BR-CON-01: reservations on a GST-inclusive contract are restated when the year's GTA option changes."""
        companies = self.mapped("company_id")
        if not companies:
            return
        contracts = self.env["tms.contract"].sudo().search([("value_basis", "=", "taxed"),
                                                            ("company_id", "in", companies.ids),
                                                            ("state", "not in", ("draft", "closed"))])
        items = contracts.mapped("item_ids").filtered(lambda i: i.tms_release_state == "released")
        items._tms_sync_reservation("restatement", note=_("GTA option changed"))

    @api.model_create_multi
    def create(self, vals_list):
        options = super().create(vals_list)
        options._tms_restate_contracts()
        return options

    def write(self, vals):
        res = super().write(vals)
        self._tms_restate_contracts()
        return res


class TmsInvoiceLrWizard(models.TransientModel):
    _inherit = "tms.invoice.lr.wizard"

    @api.model
    def _tms_charge_line_vals(self, charge, mode):
        """BR-CON-07: an incidental charge on a contract bills at its posted cost, with no markup."""
        vals = super()._tms_charge_line_vals(charge, mode)
        if charge.tms_contract_incidental:
            actual = charge._tms_contract_actual_cost()
            if actual is None:
                raise UserError(_("Incidental charge %s bills at the amount of its posted bill or expense: post the "
                                  "cost document first (BR-CON-07).", charge.name))
            vals["price_unit"] = actual
            vals["name"] += _(" (at actual)")
        return vals

    def _tms_create_invoices(self, lrs, invoice_date=None, consolidate=True):
        moves = super()._tms_create_invoices(lrs, invoice_date=invoice_date, consolidate=consolidate)
        # BR-CON-07: the receipt goes with the invoice
        Attachment = self.env["ir.attachment"].sudo()
        for move in moves:
            for line in move.invoice_line_ids.filtered(lambda x: x.tms_charge_id.tms_contract_incidental):
                charge = line.tms_charge_id.sudo()
                atts = Attachment
                if charge.expense_id:
                    atts |= Attachment.search([("res_model", "=", "tms.trip.expense"),
                                               ("res_id", "=", charge.expense_id.id), ("res_field", "!=", False)])
                    atts |= Attachment.search([("res_model", "=", "tms.trip.expense"),
                                               ("res_id", "=", charge.expense_id.id)])
                if charge.tms_cost_line_id:
                    atts |= Attachment.search([("res_model", "=", "account.move"),
                                               ("res_id", "=", charge.tms_cost_line_id.move_id.id)])
                for att in atts:
                    att.copy({"res_model": "account.move", "res_id": move.id, "res_field": False,
                              "name": _("Receipt %(c)s - %(n)s", c=charge.name, n=att.name)})
        # BR-NTF-14: invoices that would exceed the balance are flagged at once
        for move in moves.filtered("tms_contract_awaiting"):
            contract = move._tms_contract_lines().mapped("tms_contract_item_id.tms_contract_id")[:1].sudo()
            self.env["tms.contract.hold"]._tms_record("invoice", move, contract, move.amount_untaxed,
                                                      move.tms_contract_shortfall)
            users = contract.user_id | users_in_group(self.env, "tms_core.group_branch_accountant",
                                                      move.company_id)
            schedule(move, users, _("Invoice awaiting contract extension"))
        return moves
