from odoo import _, api, fields, models
from odoo.exceptions import UserError

from odoo.addons.tms_core.models.tools import notify_detached, users_in_group

from .tms_contract import INVOICED_KINDS


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    tms_contract_item_id = fields.Many2one("sale.order.line", compute="_compute_tms_contract_item",
                                           string="Contract item")

    @api.depends("tms_sale_line_id", "tms_consignment_id.sale_line_id")
    def _compute_tms_contract_item(self):
        for line in self:
            item = line.sudo().tms_sale_line_id or line.sudo().tms_consignment_id.sale_line_id
            line.tms_contract_item_id = item if item.tms_contract_id and item.tms_is_movement else False


class AccountMove(models.Model):
    _inherit = "account.move"

    tms_contract_awaiting = fields.Boolean(compute="_compute_tms_contract_awaiting", string="Awaiting extension",
                                           help="The invoice exceeds its contract's balance plus the item's own "
                                                "reservation and waits in draft (BR-CON-12).")
    tms_contract_shortfall = fields.Monetary(compute="_compute_tms_contract_awaiting", string="Contract shortfall")
    tms_contract_fit_amount = fields.Monetary(compute="_compute_tms_contract_awaiting",
                                              string="Lines that fit the contract balance")

    @api.depends("state", "invoice_line_ids.price_subtotal", "invoice_line_ids.tms_sale_line_id",
                 "invoice_line_ids.tms_consignment_id")
    def _compute_tms_contract_awaiting(self):
        for move in self:
            awaiting, shortfall, fit_amount = False, 0.0, 0.0
            if move.state == "draft" and move.move_type == "out_invoice" and move.id:
                lines = move._tms_contract_lines()
                if lines:
                    fit, held, shortfall = move._tms_contract_fit(lines)
                    awaiting = bool(held)
                    fit_amount = sum(move._tms_basis_amount(line) for line in fit)
            move.tms_contract_awaiting = awaiting
            move.tms_contract_shortfall = shortfall
            move.tms_contract_fit_amount = fit_amount

    # ------------------------------------------------------------------ BR-CON-12
    def _tms_contract_lines(self):
        self.ensure_one()
        return self.invoice_line_ids.filtered(
            lambda line: line.display_type == "product" and line.tms_contract_item_id
            and line.tms_line_kind in INVOICED_KINDS)

    def _tms_basis_amount(self, line):
        contract = line.tms_contract_item_id.sudo().tms_contract_id
        return line.price_total if contract.value_basis == "taxed" else line.price_subtotal

    def _tms_contract_fit(self, lines):
        """#23.7: post invoice n on item j iff n ≤ Balance_j + R_j, applied line by line.

        Returns (lines that fit, lines held, shortfall of the held lines)."""
        self.ensure_one()
        fit = self.env["account.move.line"]
        held = self.env["account.move.line"]
        shortfall = 0.0
        by_contract = {}
        for line in lines.sorted(lambda x: (x.sequence, x.id)):
            by_contract.setdefault(line.tms_contract_item_id.sudo().tms_contract_id, []).append(line)
        for contract, clines in by_contract.items():
            figs = contract._tms_figures()
            balance = figs["balance"]
            reserved = dict(figs["res"])
            x_move = contract._tms_open_excess(moves=self)
            x_items = {}
            for line in clines:
                item = line.tms_contract_item_id
                if item.id not in x_items:
                    x_items[item.id] = contract._tms_open_excess(items=item)
                n = self._tms_basis_amount(line)
                r_j = max(reserved.get(item.id, 0.0), 0.0)
                room = balance + x_items[item.id] + x_move + r_j
                if n <= room + 0.005:
                    fit |= line
                    over = max(n - r_j, 0.0)
                    reserved[item.id] = max(r_j - n, 0.0)
                    use = min(over, x_items[item.id])
                    x_items[item.id] -= use
                    over -= use
                    use = min(over, x_move)
                    x_move -= use
                    over -= use
                    balance -= over
                else:
                    held |= line
                    shortfall += n - max(room, 0.0)
        return fit, held, shortfall

    def _tms_check_post(self):
        res = super()._tms_check_post()
        for move in self.filtered(lambda m: m.move_type == "out_invoice"):
            lines = move._tms_contract_lines()
            if not lines:
                continue
            contracts = lines.mapped("tms_contract_item_id").sudo().mapped("tms_contract_id")
            contracts._tms_lock()
            _fit, held, shortfall = move._tms_contract_fit(lines)
            if held:
                users = contracts.mapped("user_id") | users_in_group(self.env, "tms_core.group_branch_accountant",
                                                                    move.company_id)
                notify_detached(self.env, move._name, move.id, users.ids, _("Invoice awaiting contract extension"),
                                _("Shortfall %.2f against contract %s") % (shortfall,
                                                                          ", ".join(contracts.mapped("name"))))
                self.env["tms.contract.hold"]._tms_record("invoice", move, contracts[:1], move.amount_untaxed,
                                                          shortfall, detached=True)
                raise UserError(_("The invoice exceeds the balance of contract %(c)s by %(s).2f: it stays in draft, "
                                  "Awaiting extension (BR-CON-12). The Branch Accountant may post the lines that "
                                  "fit; the rest waits for an extension or an approved excess.",
                                  c=", ".join(contracts.mapped("name")), s=shortfall))
        return res

    def _tms_after_post(self):
        res = super()._tms_after_post()
        self._tms_contract_resync()
        return res

    def button_draft(self):
        res = super().button_draft()
        self._tms_contract_resync(note=_("Reset to draft"))
        return res

    def _tms_contract_resync(self, note=None):
        """BR-CON-20: invoices relieve the reservation; incidental cost above the estimate tops it up."""
        for move in self.sudo():
            items = move.line_ids.mapped("tms_contract_item_id")
            incidental_items = move.line_ids.filtered(
                lambda line: line.tms_estimate_line_id.incidental and line.tms_estimate_line_id.estimate_id.is_contract
            ).mapped("tms_estimate_line_id.sale_line_id")
            if not (items or incidental_items):
                continue
            if move.move_type == "out_invoice":
                event = "invoice"
            elif move.move_type == "out_refund":
                event = "credit_note"
            else:
                event = "incidental"
            touched = (items | incidental_items).sudo()
            touched._tms_sync_reservation(event, source=move, note=note)
            touched._tms_try_auto_close()
            if move.state == "posted":
                move.env["tms.contract.hold"]._tms_clear(move)
                move.env["tms.contract.excess"].sudo().search([("move_id", "=", move.id),
                                                               ("state", "=", "approved")])._tms_lapse()
            contracts = touched.mapped("tms_contract_id")
            contracts._tms_update_state()
            contracts._tms_after_change()
            contracts.invalidate_recordset()

    # ------------------------------------------------------------------ partial posting (BR-CON-12)
    def action_tms_open_partial_post(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "name": _("Post the lines that fit"),
                "res_model": "tms.contract.partial.post.wizard", "view_mode": "form", "target": "new",
                "context": {"default_move_id": self.id}}

    def action_tms_request_excess(self):
        self.ensure_one()
        contract = self._tms_contract_lines().mapped("tms_contract_item_id.tms_contract_id")[:1]
        return {"type": "ir.actions.act_window", "name": _("Request excess"),
                "res_model": "tms.contract.excess.wizard", "view_mode": "form", "target": "new",
                "context": {"default_move_id": self.id, "default_contract_id": contract.id,
                            "default_amount": self.tms_contract_shortfall}}

    def _tms_split_held(self, reason):
        """Move the lines that do not fit to a new draft invoice and post the rest (BR-CON-12)."""
        self.ensure_one()
        lines = self._tms_contract_lines()
        _fit, held, _shortfall = self._tms_contract_fit(lines)
        if not held:
            raise UserError(_("Every line fits the contract balance: post the invoice."))
        if held == self.invoice_line_ids.filtered(lambda x: x.display_type == "product"):
            raise UserError(_("No line fits the contract balance."))
        copy_fields = ["product_id", "name", "quantity", "price_unit", "discount", "account_id",
                       "tms_consignment_id", "tms_trip_id", "tms_charge_id", "tms_line_kind", "tms_sale_line_id",
                       "tms_cost_class", "analytic_distribution"]
        new_lines = []
        charges = {}
        for line in held:
            vals = {}
            for fname in copy_fields:
                if fname not in line._fields:
                    continue
                value = line[fname]
                vals[fname] = value.id if isinstance(value, models.BaseModel) else value
            vals["tax_ids"] = [(6, 0, line.tax_ids.ids)]
            new_lines.append((0, 0, vals))
            if line.tms_charge_id:
                charges[line.tms_charge_id.id] = line.name
        held_amount = sum(held.mapped("price_subtotal"))
        new_move = self.create({
            "move_type": self.move_type, "partner_id": self.partner_id.id, "journal_id": self.journal_id.id,
            "invoice_date": self.invoice_date, "invoice_origin": self.invoice_origin, "ref": self.ref,
            "tms_gst_mode": self.tms_gst_mode, "invoice_payment_term_id": self.invoice_payment_term_id.id,
            "company_id": self.company_id.id, "invoice_line_ids": new_lines})
        for line in new_move.invoice_line_ids.filtered("tms_charge_id"):
            line.tms_charge_id.sudo().move_line_id = line
        self.write({"invoice_line_ids": [(2, line.id) for line in held]})
        self.env["tms.override.log"].log(
            "partial_posting", self,
            _("Held lines moved to %(n)s; %(a).2f posted", n=new_move.display_name, a=self.amount_untaxed),
            reason=reason, amount=held_amount)
        self.action_post()
        contract = held.exists().mapped("tms_contract_item_id.tms_contract_id")[:1] or \
            new_move._tms_contract_lines().mapped("tms_contract_item_id.tms_contract_id")[:1]
        self.env["tms.contract.hold"]._tms_record("invoice", new_move, contract.sudo(), new_move.amount_untaxed,
                                                  new_move.tms_contract_shortfall)
        new_move.message_post(body=_("Lines held from %(m)s, awaiting a contract extension: %(r)s",
                                     m=self.name, r=reason))
        return new_move
