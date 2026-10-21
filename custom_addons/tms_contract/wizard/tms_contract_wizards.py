from odoo import _, api, fields, models
from odoo.exceptions import UserError


class TmsContractReleaseWizard(models.TransientModel):
    """BR-CON-10, BR-CON-26: release shows balance, budget, shortfall and contribution."""

    _name = "tms.contract.release.wizard"
    _description = "Release a contract item"

    sale_line_id = fields.Many2one("sale.order.line", string="Item", required=True, readonly=True)
    contract_id = fields.Many2one(related="sale_line_id.tms_contract_id")
    currency_id = fields.Many2one(related="sale_line_id.currency_id")
    freight = fields.Monetary(compute="_compute_figures")
    incidentals = fields.Monetary(compute="_compute_figures", string="Estimated incidentals")
    budget = fields.Monetary(compute="_compute_figures")
    balance = fields.Monetary(compute="_compute_figures", string="Contract balance")
    excess = fields.Monetary(compute="_compute_figures", string="Excess earmarked to this item")
    shortfall = fields.Monetary(compute="_compute_figures")
    contribution = fields.Monetary(compute="_compute_figures")
    markup_pct = fields.Float(compute="_compute_figures", string="Markup on cost %")
    below_floor = fields.Boolean(compute="_compute_figures")
    markup_approved = fields.Boolean(compute="_compute_figures")

    @api.depends("sale_line_id")
    def _compute_figures(self):
        for wiz in self:
            line = wiz.sale_line_id
            if not line:
                wiz.update({"freight": 0.0, "incidentals": 0.0, "budget": 0.0, "balance": 0.0, "excess": 0.0,
                            "shortfall": 0.0, "contribution": 0.0, "markup_pct": 0.0, "below_floor": False,
                            "markup_approved": False})
                continue
            figs = line._tms_item_figures()
            wiz.freight = figs["freight"]
            wiz.incidentals = figs["c_hat"]
            wiz.budget = figs["budget"]
            wiz.balance = figs["balance"]
            wiz.excess = figs["excess"]
            wiz.shortfall = max(figs["budget"] - figs["balance_j"], 0.0)
            wiz.contribution = figs["contribution"]
            wiz.markup_pct = figs["markup_pct"]
            wiz.below_floor = line._tms_below_floor(figs)
            wiz.markup_approved = bool(line.tms_markup_approved_by_id)

    def action_release(self):
        self.ensure_one()
        self.sale_line_id.action_tms_release()
        return {"type": "ir.actions.act_window_close"}

    def action_request_excess(self):
        self.ensure_one()
        return self.sale_line_id.action_tms_request_excess()


class TmsContractExcessWizard(models.TransientModel):
    """BR-CON-18: request an excess over the contract value for one item or one invoice."""

    _name = "tms.contract.excess.wizard"
    _description = "Request contract excess"

    contract_id = fields.Many2one("tms.contract", required=True)
    currency_id = fields.Many2one(related="contract_id.currency_id")
    sale_line_id = fields.Many2one("sale.order.line", string="Item",
                                   domain="[('tms_contract_id', '=', contract_id), ('tms_is_movement', '=', True)]")
    move_id = fields.Many2one("account.move", string="Invoice")
    amount = fields.Monetary(required=True)
    reason = fields.Text(required=True)
    cover = fields.Selection([("extension", "Extension awaited"), ("risk", "At Premier's risk")], required=True,
                             default="extension")
    expected_cover_date = fields.Date()

    def action_request(self):
        self.ensure_one()
        if self.cover == "extension" and not self.expected_cover_date:
            raise UserError(_("Give the date the customer's extension is expected (BR-CON-18)."))
        excess = self.env["tms.contract.excess"].create({
            "contract_id": self.contract_id.id, "sale_line_id": self.sale_line_id.id, "move_id": self.move_id.id,
            "amount": self.amount, "reason": self.reason, "cover": self.cover,
            "expected_cover_date": self.expected_cover_date})
        excess.action_request()
        return {"type": "ir.actions.act_window", "res_model": "tms.contract.excess", "res_id": excess.id,
                "view_mode": "form"}


class TmsContractPartialPostWizard(models.TransientModel):
    """BR-CON-12: the Branch Accountant posts the lines that fit and holds the rest."""

    _name = "tms.contract.partial.post.wizard"
    _description = "Post the lines that fit the contract balance"

    move_id = fields.Many2one("account.move", required=True, readonly=True)
    currency_id = fields.Many2one(related="move_id.currency_id")
    fit_amount = fields.Monetary(related="move_id.tms_contract_fit_amount", string="Posts now")
    shortfall = fields.Monetary(related="move_id.tms_contract_shortfall", string="Held shortfall")
    reason = fields.Text(required=True)

    def action_confirm(self):
        self.ensure_one()
        if not self.env.user.has_group("tms_core.group_branch_accountant"):
            raise UserError(_("The Branch Accountant posts part of a held invoice (BR-CON-12)."))
        new_move = self.move_id._tms_split_held(self.reason)
        return {"type": "ir.actions.act_window", "res_model": "account.move", "res_id": new_move.id,
                "view_mode": "form"}


class TmsContractItemAmendWizard(models.TransientModel):
    """BR-CON-23: amending a released item (km, units, rate lines, equipment substitution)."""

    _name = "tms.contract.item.amend.wizard"
    _description = "Amend a contract item"

    sale_line_id = fields.Many2one("sale.order.line", string="Item", required=True, readonly=True)
    contract_id = fields.Many2one(related="sale_line_id.tms_contract_id")
    currency_id = fields.Many2one(related="sale_line_id.currency_id")
    reason = fields.Text(required=True)
    line_ids = fields.One2many("tms.contract.item.amend.line", "wizard_id")
    old_freight = fields.Monetary(compute="_compute_freight")
    new_freight = fields.Monetary(compute="_compute_freight")

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        line = self.env["sale.order.line"].browse(res.get("sale_line_id") or self.env.context.get(
            "default_sale_line_id"))
        if line and "line_ids" in fields_list:
            res["line_ids"] = [(0, 0, {"use_id": use.id, "rate_id": use.rate_id.id, "units": use.units, "km": use.km,
                                       "days": use.days, "states": use.states, "tonnes": use.tonnes,
                                       "trips": use.trips}) for use in line.tms_contract_rate_ids]
        return res

    @api.depends("line_ids.amount", "sale_line_id")
    def _compute_freight(self):
        for wiz in self:
            wiz.old_freight = wiz.sale_line_id.tms_freight
            wiz.new_freight = sum(wiz.line_ids.mapped("amount"))

    def action_apply(self):
        self.ensure_one()
        if not self.env.user.has_group("tms_core.group_operations_head"):
            raise UserError(_("The Operations Head approves contract item amendments (BR-CON-23)."))
        item = self.sale_line_id.sudo()
        if item.tms_release_state != "released":
            raise UserError(_("Only released items are amended; edit a draft item directly."))
        contract = item.tms_contract_id
        contract._tms_lock()
        before = item._tms_item_figures()
        Use = self.env["tms.contract.item.rate"].sudo().with_context(tms_contract_amend=True)
        kept = Use
        for wline in self.line_ids:
            vals = {"rate_id": wline.rate_id.id, "units": wline.units, "km": wline.km, "days": wline.days,
                    "states": wline.states, "tonnes": wline.tonnes, "trips": wline.trips, "rate": wline.rate}
            if wline.use_id:
                wline.use_id.with_context(tms_contract_amend=True).sudo().write(vals)
                kept |= wline.use_id
            else:
                kept |= Use.create(dict(vals, sale_line_id=item.id))
        (item.tms_contract_rate_ids - kept).with_context(tms_contract_amend=True).unlink()
        item._tms_sync_price()
        after = item._tms_item_figures()
        increase = after["target"] - before["target"]
        if increase > before["balance_j"] + 0.005:
            raise UserError(_("The amendment adds %(a).2f to item %(i)s but the contract balance is %(b).2f: it needs "
                              "an extension or an approved excess first (BR-CON-23).", a=increase, i=item.tms_item_no,
                              b=before["balance_j"]))
        item._tms_sync_reservation("amendment", source=item, note=self.reason)
        item.order_id.message_post(body=_("Item %(i)s amended: freight %(o).2f → %(n).2f. %(r)s", i=item.tms_item_no,
                                          o=before["freight"], n=after["freight"], r=self.reason))
        contract._tms_after_change()
        return {"type": "ir.actions.act_window_close"}


class TmsContractItemAmendLine(models.TransientModel):
    _name = "tms.contract.item.amend.line"
    _description = "Contract item amendment line"

    wizard_id = fields.Many2one("tms.contract.item.amend.wizard", required=True, ondelete="cascade")
    use_id = fields.Many2one("tms.contract.item.rate")
    currency_id = fields.Many2one(related="wizard_id.currency_id")
    rate_id = fields.Many2one("tms.contract.rate", string="Contract rate", required=True)
    basis = fields.Selection(related="rate_id.basis")
    units = fields.Float(default=1.0)
    km = fields.Float()
    days = fields.Float()
    states = fields.Float(default=1.0)
    tonnes = fields.Float()
    trips = fields.Float(default=1.0)
    rate = fields.Monetary(compute="_compute_amount")
    amount = fields.Monetary(compute="_compute_amount")

    @api.depends("rate_id", "units", "km", "days", "states", "tonnes", "trips")
    def _compute_amount(self):
        """New rate lines take the rate in force on the item's release date (BR-CON-16)."""
        for wl in self:
            item = wl.wizard_id.sale_line_id
            if wl.use_id and wl.use_id.rate_id == wl.rate_id:
                rate = wl.use_id.rate
            elif wl.rate_id:
                in_force = item.tms_contract_id._tms_rate_in_force(
                    wl.rate_id, item.tms_released_on or fields.Date.context_today(wl))
                rate = (in_force or wl.rate_id).rate
            else:
                rate = 0.0
            qty = {"unit": wl.units, "km": wl.km, "unit_km": wl.units * wl.km, "day": wl.days,
                   "unit_day": wl.units * wl.days, "state": wl.units * wl.states, "tonne": wl.tonnes,
                   "trip": wl.trips}.get(wl.basis, 1.0)
            wl.rate = rate
            wl.amount = round(rate * qty, 2)
