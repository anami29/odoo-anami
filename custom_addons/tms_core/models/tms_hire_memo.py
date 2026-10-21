from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .tools import notify_detached, schedule, users_in_group

HIRE_ELEMENTS = [
    ("hire", "Agreed hire"),
    ("halting", "Halting"),
    ("detention", "Detention"),
    ("extra", "Other approved extra"),
    ("commission", "Broker commission"),
    ("compensation", "Cancellation compensation"),
]


class PurchaseOrder(models.Model):
    """BR-HIR-01..16: a hire memo is a purchase order flagged as hire memo."""

    _name = "purchase.order"
    _inherit = ["purchase.order", "tms.reason.mixin"]

    tms_is_hire_memo = fields.Boolean(string="Hire memo", tracking=True)
    tms_trip_id = fields.Many2one("tms.order", string="Trip", index=True, tracking=True)
    tms_assignment_id = fields.Many2one("tms.trip.assignment", string="Assignment")
    tms_vehicle_number = fields.Char(string="Vehicle")
    tms_driver_name = fields.Char(string="Driver")
    tms_broker_id = fields.Many2one("res.partner", string="Broker")
    tms_route = fields.Char(string="Route")
    tms_halting_rate = fields.Monetary(string="Halting rate per day")
    tms_advance_percent = fields.Float(string="Advance %")
    tms_vendor_liable_breakdown = fields.Boolean(
        string="Vendor liable for breakdowns", help="BR-PNL-07: replacement add-ons are deducted from the hire bill."
    )
    tms_tds_treatment = fields.Selection(
        [("section", "TDS under the vendor's section"), ("declaration", "Exempt: transporter declaration on file")],
        compute="_compute_tms_tds_treatment",
        string="TDS treatment")
    tms_hire_amount = fields.Monetary(compute="_compute_tms_hire", string="Agreed hire")
    tms_advance_ids = fields.One2many("tms.hire.advance", "purchase_id", string="Advances")
    tms_advance_total = fields.Monetary(compute="_compute_tms_hire", string="Advances paid")
    tms_advance_cap = fields.Monetary(compute="_compute_tms_hire", string="Advance cap")
    tms_balance = fields.Monetary(compute="_compute_tms_hire", string="Balance")
    tms_match_ids = fields.One2many("tms.vendor.bill.match", "purchase_id", string="Three-way matches")
    tms_cancel_approved_by_id = fields.Many2one("res.users", readonly=True, copy=False, string="Cancellation approved by")
    tms_cancel_reason = fields.Text(copy=False, string="Cancellation reason")
    tms_full_hire_on_replacement = fields.Boolean(
        string="Full hire if replaced", help="BR-ASG-11: the memo's terms pay the full hire after a breakdown.")
    tms_pro_rata_fraction = fields.Float(default=1.0, copy=False,
                                         help="Share of the hire payable after a replacement (BR-ASG-11).",
        string="Pro-rata fraction")

    @api.depends("partner_id", "date_order")
    def _compute_tms_tds_treatment(self):
        for po in self:
            date = fields.Date.to_date(po.date_order) if po.date_order else None
            po.tms_tds_treatment = "declaration" if po.partner_id and po.partner_id.commercial_partner_id \
                .tms_has_valid_tds_declaration(date) else "section"

    @api.depends("order_line.price_subtotal", "order_line.tms_hire_element", "tms_advance_ids.state",
                 "tms_advance_ids.amount", "tms_advance_percent", "company_id")
    def _compute_tms_hire(self):
        for po in self:
            hire = sum(po.order_line.filtered(lambda line: line.tms_hire_element == "hire").mapped("price_subtotal"))
            po.tms_hire_amount = hire
            po.tms_advance_total = sum(po.tms_advance_ids.filtered(lambda a: a.state == "posted").mapped("amount"))
            pct = min(po.tms_advance_percent or 0.0, po.company_id.tms_max_advance_percent or 80.0, 80.0)
            po.tms_advance_cap = hire * pct / 100.0
            po.tms_balance = hire - po.tms_advance_total

    def button_confirm(self):
        """BR-HIR-03: confirming a hire memo needs the vendor's PAN and bank details."""
        for po in self.filtered("tms_is_hire_memo"):
            vendor = po.partner_id.commercial_partner_id
            if not vendor.l10n_in_pan:
                raise UserError(_("Vendor %s has no PAN (BR-HIR-03).", vendor.display_name))
            if not vendor.bank_ids:
                raise UserError(_("Vendor %s has no bank details (BR-HIR-03).", vendor.display_name))
            if not po.order_line.filtered(lambda line: line.tms_hire_element == "hire"):
                raise UserError(_("The agreed hire must be item 10 of the hire memo (BR-HIR-02)."))
        return super().button_confirm()

    def button_cancel(self):
        """BR-HIR-15: cancelling a confirmed hire memo needs Branch Manager approval."""
        for po in self.filtered(lambda p: p.tms_is_hire_memo and p.state in ("purchase", "done")):
            if not po.tms_cancel_approved_by_id:
                raise UserError(_("A Branch Manager must approve the cancellation first (BR-HIR-15)."))
            advances = po.tms_advance_ids.filtered(lambda a: a.state == "posted")
            compensation = po.order_line.filtered(lambda line: line.tms_hire_element == "compensation")
            if advances and not compensation:
                po._tms_advance_debit_note(sum(advances.mapped("amount")) )
        return super().button_cancel()

    def action_tms_approve_cancel(self):
        for po in self:
            if not self.env.user.has_group("tms_core.group_branch_manager"):
                raise UserError(_("Only a Branch Manager approves a hire memo cancellation."))
            reason = self.env.context.get("tms_reason") or po.tms_cancel_reason
            if not reason:
                raise UserError(_("A cancellation needs a reason."))
            # the Branch Manager approves without purchase rights of their own
            po.sudo().write({"tms_cancel_approved_by_id": self.env.user.id, "tms_cancel_reason": reason})
            po.sudo().message_post(body=_("Cancellation approved: %s", reason),
                                   author_id=self.env.user.partner_id.id)

    def _tms_advance_debit_note(self, amount):
        """BR-HIR-15: the advance paid stays a debit balance on the vendor's payable account, which is the
        recovery the debit note claims; raising a vendor refund on top would count it twice. The accountants
        get an activity to recover it (or net it against the vendor's next bill)."""
        self.ensure_one()
        msg = _("Hire memo %(po)s cancelled: advance %(amt).2f is recoverable from %(v)s (debit balance on payable).",
                po=self.name, amt=amount, v=self.partner_id.display_name)
        self.message_post(body=msg)
        accountants = users_in_group(self.env, "tms_core.group_branch_accountant", self.company_id)
        schedule(self, accountants, _("Recover the advance of %s", self.name), msg)
        return True

    def _tms_receive_hire(self):
        """BR-HIR-05: received quantity comes only from POD verification or pro-rata settlement."""
        for po in self.sudo().filtered(lambda p: p.state in ("purchase", "done")):
            for line in po.order_line.filtered(lambda line: line.tms_hire_element in ("hire", "commission")):
                qty = line.product_qty * (po.tms_pro_rata_fraction if line.tms_hire_element == "hire" else 1.0)
                line.with_context(tms_receive=True).qty_received = qty
            for line in po.order_line.filtered(
                    lambda line: line.tms_hire_element in ("halting", "detention", "extra", "compensation")
                    and line.tms_extra_approved):
                line.with_context(tms_receive=True).qty_received = line.product_qty
            po.message_post(body=_("Hire received from verified PODs."))

    def tms_settle_pro_rata(self, km_run, km_planned):
        """BR-ASG-11, #23.2: H_paid = H × k_run / k_planned."""
        for po in self:
            fraction = (km_run / km_planned) if km_planned else 1.0
            po.tms_pro_rata_fraction = max(min(fraction, 1.0), 0.0)
            po._tms_receive_hire()
            po.message_post(body=_("Hire settled pro rata: %(r).1f of %(p).1f km.", r=km_run, p=km_planned))

    def action_create_invoice(self):
        """Every hire bill gets its three-way match as soon as it exists (BR-HIR-06)."""
        res = super().action_create_invoice()
        Match = self.env["tms.vendor.bill.match"]
        for po in self.filtered("tms_is_hire_memo"):
            for bill in po.invoice_ids.filtered(lambda m: m.state == "draft" and m.move_type == "in_invoice"):
                if not bill.tms_match_ids.filtered(lambda m, po=po: m.purchase_id == po):
                    Match.create({"move_id": bill.id, "purchase_id": po.id})._tms_refresh()
        return res

    def action_view_matches(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "res_model": "tms.vendor.bill.match", "view_mode": "list,form",
                "domain": [("purchase_id", "=", self.id)]}

    def action_print_hire_memo(self):
        return self.env.ref("tms_core.action_report_hire_memo").report_action(self)


class PurchaseOrderLine(models.Model):
    _inherit = "purchase.order.line"

    tms_hire_element = fields.Selection(HIRE_ELEMENTS, string="Hire element")
    tms_extra_approved = fields.Boolean(string="Extra approved", readonly=True, copy=False)
    tms_extra_approved_by_id = fields.Many2one("res.users", readonly=True, copy=False, string="Extra approved by")
    tms_waiver_reason = fields.Char(string="Customer charge waiver reason")
    tms_charge_id = fields.Many2one("tms.trip.charge", readonly=True, copy=False, string="Customer charge")
    tms_trip_id = fields.Many2one(related="order_id.tms_trip_id", store=True)

    def write(self, vals):
        if "qty_received" in vals and not self.env.context.get("tms_receive"):
            if any(line.order_id.tms_is_hire_memo and line.tms_hire_element for line in self):
                raise UserError(_("Hire received quantity comes only from POD verification or pro-rata settlement "
                                  "(BR-HIR-05)."))
        return super().write(vals)

    def action_tms_approve_extra(self):
        """BR-HIR-02, BR-CHG-09: payable extras count once approved; halting or detention raises a
        draft customer charge unless a waiver reason is logged."""
        category = self.env.ref("tms_core.tms_category_detention")
        for line in self:
            if not self.env.user.has_group("tms_core.group_branch_manager"):
                raise UserError(_("A Branch Manager approves hire memo extras."))
            # the Branch Manager reads purchase lines only; the approval itself is checked above
            line = line.sudo()
            line.write({"tms_extra_approved": True, "tms_extra_approved_by_id": self.env.user.id})
            if line.tms_hire_element in ("halting", "detention"):
                trip = line.order_id.tms_trip_id
                if line.tms_waiver_reason:
                    self.env["tms.override.log"].log("waiver", line.order_id,
                                                     _("Customer charge waived for %s", line.name),
                                                     reason=line.tms_waiver_reason, amount=line.price_subtotal)
                elif trip:
                    lr = trip.consignment_ids[:1]
                    line.tms_charge_id = self.env["tms.trip.charge"].with_user(self.env.user).create({
                        "trip_id": trip.id,
                        "consignment_id": lr.id,
                        "category_id": category.id,
                        "amount": line.price_subtotal,
                        "recover_from": "customer",
                        "partner_id": lr.billing_partner_id.id,
                        "detention_kind": "hire",
                        "note": _("From hire memo %(po)s item %(i)s", po=line.order_id.name, i=line.tms_item_no),
                    })
            if line.order_id.state in ("purchase", "done") and line.order_id.tms_trip_id.pod_status == "verified":
                line.with_context(tms_receive=True).qty_received = line.product_qty
        return True

    def _prepare_account_move_line(self, move=False):
        """The hire bill line names the trip, its primary truck and the Trip direct class (#16, BR-ASG-13)."""
        res = super()._prepare_account_move_line(move=move)
        order = self.order_id
        if order.tms_is_hire_memo and order.tms_trip_id:
            trip = order.tms_trip_id
            primary = trip.primary_vehicle_id
            asg_vehicle = order.tms_assignment_id.vehicle_id
            res.update({
                "tms_trip_id": trip.id,
                "tms_cost_class": "trip_direct",
                "tms_line_kind": "hire",
                "vehicle_id": primary.id or asg_vehicle.id,
                "tms_replacement_vehicle_id": asg_vehicle.id if asg_vehicle and primary and asg_vehicle != primary
                else False,
            })
        return res


class TmsHireAdvance(models.Model):
    """BR-HIR-04, BR-HIR-16: advances at loading, cash or diesel, capped at the advance percent (≤ 80%)."""

    _name = "tms.hire.advance"
    _description = "Hire memo advance"
    _order = "date, id"

    purchase_id = fields.Many2one("purchase.order", required=True, ondelete="cascade",
                                  domain=[("tms_is_hire_memo", "=", True)])
    company_id = fields.Many2one(related="purchase_id.company_id", store=True)
    currency_id = fields.Many2one(related="purchase_id.currency_id")
    kind = fields.Selection([("cash", "Cash"), ("bank", "Bank"), ("diesel", "Diesel (pump or fuel card)")],
                            default="bank", required=True)
    amount = fields.Monetary(required=True)
    date = fields.Date(required=True, default=fields.Date.context_today)
    fuel_station = fields.Char()
    fuel_litres = fields.Float()
    state = fields.Selection([("draft", "Draft"), ("posted", "Posted")], default="draft")
    payment_id = fields.Many2one("account.payment", readonly=True)
    move_id = fields.Many2one("account.move", readonly=True)

    def action_post(self):
        for adv in self:
            po = adv.purchase_id
            if po.state not in ("purchase", "done"):
                raise UserError(_("Confirm the hire memo before paying an advance."))
            if po.tms_advance_total + adv.amount > po.tms_advance_cap + 0.005:
                raise UserError(_("Advances %(t).2f would exceed the cap %(c).2f (BR-HIR-04, BR-HIR-16).",
                                  t=po.tms_advance_total + adv.amount, c=po.tms_advance_cap))
            company = po.company_id
            if adv.kind in ("cash", "bank"):
                journal = (company.tms_cash_journal_id or company.tms_branch_journal("cash")) if adv.kind == "cash" \
                    else (company.tms_bank_journal_id or company.tms_branch_journal("bank"))
                payment = self.env["account.payment"].with_company(company).create({
                    "payment_type": "outbound",
                    "partner_type": "supplier",
                    "partner_id": po.partner_id.commercial_partner_id.id,
                    "amount": adv.amount,
                    "date": adv.date,
                    "journal_id": journal.id,
                    "memo": _("Advance on hire memo %s", po.name),
                })
                payment.action_post()
                adv.write({"payment_id": payment.id, "move_id": payment.move_id.id, "state": "posted"})
            else:
                if not company.tms_fuel_card_account_id:
                    raise UserError(_("Configure the fuel card provider account first."))
                payable = po.partner_id.commercial_partner_id.with_company(company).property_account_payable_id
                move = self.env["account.move"].with_company(company).create({
                    "move_type": "entry",
                    "journal_id": company.tms_branch_journal("general").id,
                    "date": adv.date,
                    "ref": _("Diesel advance on hire memo %s", po.name),
                    "line_ids": [
                        (0, 0, {"name": _("Diesel advance %s", po.name), "account_id": payable.id,
                                "partner_id": po.partner_id.commercial_partner_id.id, "debit": adv.amount,
                                "credit": 0.0}),
                        (0, 0, {"name": _("Diesel advance %s", po.name),
                                "account_id": company.tms_fuel_card_account_id.id, "debit": 0.0,
                                "credit": adv.amount}),
                    ],
                })
                move.action_post()
                adv.write({"move_id": move.id, "state": "posted"})
        return True


class TmsVendorBillMatch(models.Model):
    """BR-HIR-06..09: the three-way match of hire memo, verified POD and vendor bill."""

    _name = "tms.vendor.bill.match"
    _description = "Hire bill three-way match"
    _inherit = ["mail.thread", "mail.activity.mixin", "tms.reason.mixin"]
    _order = "id desc"

    name = fields.Char(compute="_compute_name", store=True)
    move_id = fields.Many2one("account.move", string="Vendor bill", required=True, ondelete="cascade", index=True)
    purchase_id = fields.Many2one("purchase.order", string="Hire memo", required=True, index=True)
    trip_id = fields.Many2one(related="purchase_id.tms_trip_id", store=True)
    company_id = fields.Many2one(related="move_id.company_id", store=True)
    currency_id = fields.Many2one(related="move_id.currency_id")
    line_ids = fields.One2many("tms.vendor.bill.match.line", "match_id")
    pod_ok = fields.Boolean(string="PODs verified", readonly=True)
    tolerance_ok = fields.Boolean(readonly=True)
    state = fields.Selection(
        [("blocked", "Blocked"), ("passed", "Passed"), ("overridden", "Overridden"), ("posted", "Posted")],
        default="blocked",
        tracking=True,
    )
    override_by_id = fields.Many2one("res.users", readonly=True, tracking=True)
    override_reason = fields.Text(tracking=True)

    @api.depends("move_id", "purchase_id")
    def _compute_name(self):
        for match in self:
            match.name = "%s ↔ %s" % (match.purchase_id.name or "", match.move_id.name or _("draft bill"))

    def _tms_refresh(self):
        Line = self.env["tms.vendor.bill.match.line"]
        for match in self:
            po, bill = match.purchase_id, match.move_id
            match.line_ids.unlink()
            vals = []
            for pol in po.order_line.filtered(lambda line: not line.display_type):
                approved = pol.price_unit * pol.qty_received
                if pol.tms_hire_element in ("halting", "detention", "extra", "compensation") \
                        and not pol.tms_extra_approved:
                    approved = 0.0
                billed_lines = self.env["account.move.line"].search([
                    ("purchase_line_id", "=", pol.id), ("move_id.move_type", "=", "in_invoice"),
                    "|", ("move_id", "=", bill.id), ("parent_state", "=", "posted")])
                billed = sum(billed_lines.mapped("price_subtotal"))
                if not approved and not billed:
                    continue
                vals.append({"match_id": match.id, "kind": "extra" if pol.tms_hire_element not in ("hire",) else "hire",
                             "label": "%s/%s %s" % (po.name, pol.tms_item_no, pol.name or ""),
                             "approved": approved, "billed": billed})
            charges = self.env["tms.trip.charge"].search([
                ("trip_id", "=", po.tms_trip_id.id), ("recover_from", "=", "vendor"),
                ("partner_id", "child_of", po.partner_id.commercial_partner_id.id),
                ("state", "in", ("approved", "deducted"))]) if po.tms_trip_id else self.env["tms.trip.charge"]
            for charge in charges:
                if charge.state == "deducted" and charge.move_line_id.move_id != bill:
                    continue
                billed = sum(bill.invoice_line_ids.filtered(lambda line, c=charge: line.tms_charge_id == c).mapped(
                    "price_subtotal"))
                kind = "replacement" if charge.category_id.code == "REPLACEMENT" else "recovery"
                vals.append({"match_id": match.id, "kind": kind, "label": "%s %s" % (charge.name,
                                                                                       charge.category_id.name),
                             "approved": -charge.amount, "billed": billed, "charge_id": charge.id})
            Line.create(vals)
            links = self.env["tms.trip.consignment"].search([("assignment_id.hire_memo_id", "=", po.id),
                                                             ("state", "=", "active")])
            if not links and po.tms_trip_id:
                links = po.tms_trip_id.consignment_link_ids.filtered(lambda x: x.state == "active")
            pod_ok = bool(links) and all(link.pod_status == "verified" for link in links)
            tol_ok = all(line.result == "pass" for line in match.line_ids)
            state = match.state
            if state not in ("overridden", "posted"):
                state = "passed" if pod_ok and tol_ok else "blocked"
            match.write({"pod_ok": pod_ok, "tolerance_ok": tol_ok, "state": state})

    def action_refresh(self):
        self._tms_refresh()

    def action_apply_recoveries(self):
        """BR-CHG-06, BR-HIR-09: approved vendor recoveries enter the bill as deductions."""
        for match in self:
            bill = match.move_id
            if bill.state != "draft":
                raise UserError(_("Recoveries are added to a draft bill; use a debit note after posting."))
            for mline in match.line_ids.filtered(lambda line: line.kind in ("recovery", "replacement")
                                                 and line.charge_id and not line.billed):
                charge = mline.charge_id
                bill.write({"invoice_line_ids": [(0, 0, {
                    "name": _("Recovery %(c)s %(cat)s", c=charge.name, cat=charge.category_id.name),
                    "quantity": 1,
                    "price_unit": -charge.amount,
                    "account_id": charge._tms_income_account().id,
                    "tms_charge_id": charge.id,
                    "tms_trip_id": charge.trip_id.id,
                    "tms_line_kind": "recovery",
                    "vehicle_id": charge.trip_id.primary_vehicle_id.id,
                    "tax_ids": [(5, 0, 0)],
                })]})
                charge.move_line_id = bill.invoice_line_ids.filtered(lambda line, c=charge: line.tms_charge_id == c)[:1]
            match._tms_refresh()
        return True

    def action_override(self):
        """BR-HIR-08: only the Finance Head overrides a tolerance breach, with a reason."""
        reason = self.env.context.get("tms_reason")
        for match in self:
            if not self.env.user.has_group("tms_core.group_finance_head"):
                raise UserError(_("Only the Finance Head overrides a tolerance breach (BR-HIR-08)."))
            if not reason:
                raise UserError(_("An override needs a reason."))
            if not match.pod_ok:
                raise UserError(_("PODs must be verified; the override covers tolerance only (BR-HIR-07)."))
            match.write({"state": "overridden", "override_by_id": self.env.user.id, "override_reason": reason})
            self.env["tms.override.log"].log("tolerance", match, _("Hire tolerance overridden on %s", match.name),
                                             reason=reason)
        return True

    def _tms_check_or_raise(self):
        for match in self:
            match._tms_refresh()
            if match.state in ("passed", "overridden"):
                continue
            problems = []
            if not match.pod_ok:
                problems.append(_("not every POD on the leg is verified"))
            for line in match.line_ids.filtered(lambda line: line.result != "pass"):
                problems.append(_("%(l)s: approved %(a).2f, billed %(b).2f, tolerance %(t).2f",
                                  l=line.label, a=line.approved, b=line.billed, t=line.tolerance))
            msg = _("Hire bill blocked by the three-way match (BR-HIR-07):\n%s", "\n".join(problems))
            users = users_in_group(self.env, "tms_core.group_branch_accountant", match.company_id)
            notify_detached(self.env, match._name, match.id, users.ids, _("Hire bill blocked"), msg)  # BR-NTF-07
            raise UserError(msg)

    @api.model
    def _cron_escalate_blocked(self):
        """BR-NTF-07: blocked matches escalate to the Finance Head after a day."""
        old = self.search([("state", "=", "blocked"),
                           ("create_date", "<", fields.Datetime.subtract(fields.Datetime.now(), days=1))])
        for match in old:
            schedule(match, users_in_group(self.env, "tms_core.group_finance_head", match.company_id),
                     _("Hire bill blocked for more than a day"))


class TmsVendorBillMatchLine(models.Model):
    _name = "tms.vendor.bill.match.line"
    _description = "Hire bill match element"

    match_id = fields.Many2one("tms.vendor.bill.match", required=True, ondelete="cascade")
    currency_id = fields.Many2one(related="match_id.currency_id")
    kind = fields.Selection([("hire", "Hire"), ("extra", "Approved extra"), ("recovery", "Vendor recovery"),
                             ("replacement", "Replacement cost")], required=True)
    label = fields.Char()
    charge_id = fields.Many2one("tms.trip.charge")
    approved = fields.Monetary()
    billed = fields.Monetary()
    variance = fields.Monetary(compute="_compute_result", store=True)
    tolerance = fields.Monetary(compute="_compute_result", store=True)
    result = fields.Selection([("pass", "Within tolerance"), ("fail", "Outside tolerance")],
                              compute="_compute_result", store=True)

    @api.depends("approved", "billed")
    def _compute_result(self):
        """#23.2: T_e = min(250, 0.005 × |A_e|); pass ⇔ |B_e − A_e| ≤ T_e."""
        for line in self:
            company = line.match_id.company_id or self.env.company
            line.variance = line.billed - line.approved
            line.tolerance = company.tms_hire_tolerance(line.approved)
            line.result = "pass" if abs(line.variance) <= line.tolerance + 1e-6 else "fail"
