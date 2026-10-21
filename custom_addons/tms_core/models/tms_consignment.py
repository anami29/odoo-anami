import math
from datetime import datetime, time, timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .res_partner import FREIGHT_BASIS
from .tools import roundup10, users_in_group, schedule

LR_STATES = [
    ("draft", "Draft"),
    ("confirmed", "Confirmed"),
    ("dispatched", "Dispatched"),
    ("delivered", "Delivered"),
    ("not_delivered", "Not delivered"),
    ("invoiced", "Invoiced"),
    ("cancel", "Cancelled"),
]
CARGO_TYPES = [("regular", "Regular"), ("odc", "ODC")]


class TmsConsignment(models.Model):
    """The consignment note (LR), BR-LR-01..18."""

    _name = "tms.consignment"
    _description = "Consignment note (LR)"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date desc, id desc"
    _check_company_auto = True

    name = fields.Char(string="LR number", required=True, copy=False, default="/", tracking=True, index=True)
    manual_number = fields.Boolean(help="Number typed from a printed LR book (BR-MD-12).")
    date = fields.Date(required=True, default=fields.Date.context_today, tracking=True)
    company_id = fields.Many2one(
        "res.company", string="Booking branch", required=True, default=lambda s: s.env.company, tracking=True
    )
    currency_id = fields.Many2one(related="company_id.currency_id")
    delivery_branch_id = fields.Many2one("res.company", string="Delivery branch", tracking=True)
    tms_mode = fields.Selection(
        [("hybrid", "Hybrid"), ("broker", "Broker"), ("consumer", "Consumer")], readonly=True, copy=False,
        string="Operating mode")
    consignor_id = fields.Many2one("res.partner", required=True, tracking=True)
    consignee_id = fields.Many2one("res.partner", required=True, tracking=True)
    billing_partner_id = fields.Many2one(
        "res.partner", string="Billing party", compute="_compute_billing_partner", store=True, readonly=False,
        tracking=True,
    )
    freight_basis = fields.Selection(FREIGHT_BASIS, required=True, default="tbb", tracking=True)
    gst_payer = fields.Selection(
        [("consignor", "Consignor"), ("consignee", "Consignee"), ("gta", "GTA")],
        string="GST payer",
        compute="_compute_gst_payer",
        store=True,
        readonly=False,
        tracking=True,
    )
    origin_id = fields.Many2one("res.partner", string="Origin", domain=[("tms_location", "=", True)], tracking=True)
    destination_id = fields.Many2one(
        "res.partner", string="Destination", domain=[("tms_location", "=", True)], tracking=True
    )
    service_type = fields.Selection(
        [("ftl", "Full truck load"), ("ptl", "Part load"), ("express", "Express")], default="ftl", required=True
    )
    risk_basis = fields.Selection(
        [("carrier", "Carrier's risk"), ("owner", "Owner's risk")], default="carrier", required=True, tracking=True
    )
    tms_cargo_type = fields.Selection(CARGO_TYPES, string="Cargo Type", default="regular", required=True, tracking=True)
    # BR-MD-04 typed rate for LRs booked without an order item
    rate_type = fields.Selection(
        [("per_kg", "Per kg"), ("per_package", "Per package"), ("per_trip", "Per trip")], tracking=True
    )
    typed_rate = fields.Monetary(tracking=True)
    typed_min_weight = fields.Float(string="Minimum chargeable weight (kg)")
    typed_rate_reason = fields.Char(tracking=True)
    typed_rate_approved_by_id = fields.Many2one("res.users", readonly=True, copy=False, tracking=True)
    # lines and totals
    line_ids = fields.One2many("tms.consignment.line", "consignment_id", copy=True)
    ewaybill_ids = fields.One2many("tms.ewaybill", "consignment_id")
    total_packages = fields.Integer(compute="_compute_totals", store=True)
    total_actual_weight = fields.Float(string="Actual weight (kg)", compute="_compute_totals", store=True)
    computed_charged_weight = fields.Float(compute="_compute_totals", store=True)
    charged_weight_override = fields.Float(string="Charged weight override (kg)", tracking=True)
    charged_weight_reason = fields.Char(tracking=True)
    charged_weight = fields.Float(string="Charged weight (kg)", compute="_compute_charged_weight", store=True)
    declared_value = fields.Monetary(compute="_compute_totals", store=True)
    freight_amount = fields.Monetary(compute="_compute_freight", store=True, tracking=True)
    state = fields.Selection(LR_STATES, default="draft", required=True, tracking=True, copy=False, index=True)
    # trips
    trip_link_ids = fields.One2many("tms.trip.consignment", "consignment_id", string="Trip links")
    trip_ids = fields.Many2many("tms.order", compute="_compute_trip_ids", string="Trips")
    trip_count = fields.Integer(compute="_compute_trip_ids")
    unplanned_packages = fields.Integer(compute="_compute_unplanned", store=True)
    vehicle_number = fields.Char(string="Vehicle number", readonly=True, copy=False, help="Taken from the trip at dispatch (BR-LR-13).")
    vehicle_history = fields.Text(readonly=True, copy=False)
    vehicle_placed = fields.Boolean(compute="_compute_vehicle_placed")
    # money
    invoice_line_ids = fields.One2many("account.move.line", "tms_consignment_id", string="Invoice lines")
    invoice_ids = fields.Many2many("account.move", compute="_compute_invoice_ids", string="Invoices",
                                   compute_sudo=True)
    freight_invoiced = fields.Monetary(compute="_compute_invoicing", store=True)
    freight_uninvoiced = fields.Monetary(compute="_compute_invoicing", store=True, string="Freight not invoiced")
    collected = fields.Boolean(compute="_compute_invoicing", store=True, help="To-Pay freight collected (RL-06).")
    charge_ids = fields.One2many("tms.trip.charge", "consignment_id")
    claim_ids = fields.One2many("tms.damage.claim", "consignment_id")
    amendment_ids = fields.One2many("tms.consignment.amendment", "consignment_id")
    delivered_on = fields.Datetime(copy=False, readonly=True)
    pod_verified_on = fields.Datetime(compute="_compute_pod", store=True)
    pod_status = fields.Selection(
        [("none", "No POD"), ("pending", "Pending"), ("partial", "Partly verified"), ("verified", "Verified")],
        compute="_compute_pod", string="POD status",
        store=True,
    )
    cancel_reason = fields.Text(copy=False, tracking=True)
    ewaybill_required = fields.Boolean(compute="_compute_ewaybill_required")
    ewaybill_missing = fields.Boolean(compute="_compute_ewaybill_required")
    godown_hold = fields.Boolean(readonly=True, copy=False, help="Refused goods held at the delivery branch (BR-POD-09).")
    godown_since = fields.Date(readonly=True, copy=False)
    tbb_overdue = fields.Boolean(compute="_compute_tbb_overdue", search="_search_tbb_overdue")

    _sql_constraints = [
        ("name_company_uniq", "unique(name, company_id)", "This LR number is already used in this branch."),
    ]

    # ------------------------------------------------------------------
    # computes
    # ------------------------------------------------------------------
    @api.depends("freight_basis", "consignor_id", "consignee_id")
    def _compute_billing_partner(self):
        for lr in self:
            if lr.freight_basis == "topay":
                lr.billing_partner_id = lr.consignee_id
            else:
                lr.billing_partner_id = lr.consignor_id

    @api.depends("billing_partner_id", "date", "company_id", "consignor_id", "consignee_id")
    def _compute_gst_payer(self):
        """BR-LR-03: default from the company's GTA option and the billing party's registration."""
        for lr in self:
            option = lr.company_id.tms_get_gta_option(lr.date) if lr.company_id else "rcm"
            party = lr.billing_partner_id.commercial_partner_id
            registered = bool(party.vat) and not party.tms_unregistered
            if option == "rcm" and registered:
                lr.gst_payer = "consignee" if lr.billing_partner_id == lr.consignee_id else "consignor"
            else:
                lr.gst_payer = "gta"

    @api.depends("line_ids.packages", "line_ids.actual_weight", "line_ids.charged_weight", "line_ids.declared_value",
                 "typed_min_weight")
    def _compute_totals(self):
        for lr in self:
            lr.total_packages = sum(lr.line_ids.mapped("packages"))
            lr.total_actual_weight = sum(lr.line_ids.mapped("actual_weight"))
            lr.declared_value = sum(lr.line_ids.mapped("declared_value"))
            charged = sum(lr.line_ids.mapped("charged_weight"))
            lr.computed_charged_weight = max(charged, lr.typed_min_weight or 0.0)

    @api.depends("computed_charged_weight", "charged_weight_override")
    def _compute_charged_weight(self):
        for lr in self:
            lr.charged_weight = lr.charged_weight_override or lr.computed_charged_weight

    @api.depends("rate_type", "typed_rate", "charged_weight", "total_packages")
    def _compute_freight(self):
        """BR-LR-07, #23.1 typed-rate freight; tms_estimate prices LRs booked against an item."""
        for lr in self:
            lr.freight_amount = lr._tms_typed_freight()

    def _tms_typed_freight(self):
        self.ensure_one()
        if self.rate_type == "per_kg":
            return self.charged_weight * self.typed_rate
        if self.rate_type == "per_package":
            return self.total_packages * self.typed_rate
        if self.rate_type == "per_trip":
            return self.typed_rate
        return 0.0

    @api.depends("trip_link_ids.state", "trip_link_ids.trip_id")
    def _compute_trip_ids(self):
        for lr in self:
            trips = lr.trip_link_ids.filtered(lambda link: link.state != "cancel").mapped("trip_id")
            lr.trip_ids = trips
            lr.trip_count = len(trips)

    @api.depends("total_packages", "trip_link_ids.packages", "trip_link_ids.state")
    def _compute_unplanned(self):
        for lr in self:
            planned = sum(lr.trip_link_ids.filtered(lambda link: link.state != "cancel").mapped("packages"))
            lr.unplanned_packages = max(lr.total_packages - planned, 0)

    @api.depends("trip_link_ids.state", "trip_link_ids.trip_id.assignment_ids")
    def _compute_vehicle_placed(self):
        for lr in self:
            lr.vehicle_placed = any(
                link.state != "cancel" and link.trip_id.assignment_ids for link in lr.trip_link_ids
            )

    @api.depends(
        "invoice_line_ids.parent_state",
        "invoice_line_ids.price_subtotal",
        "invoice_line_ids.tms_line_kind",
        "invoice_line_ids.move_id.payment_state",
        "freight_amount",
    )
    def _compute_invoicing(self):
        for lr in self:
            lines = lr.invoice_line_ids.filtered(
                lambda line: line.parent_state == "posted" and line.tms_line_kind in ("freight", "rate_correction")
            )
            invoiced = 0.0
            for line in lines:
                sign = -1 if line.move_id.move_type == "out_refund" else 1
                invoiced += sign * line.price_subtotal
            lr.freight_invoiced = invoiced
            lr.freight_uninvoiced = max(lr.freight_amount - invoiced, 0.0)
            freight_moves = lines.mapped("move_id").filtered(lambda m: m.move_type == "out_invoice")
            lr.collected = bool(freight_moves) and all(
                m.payment_state in ("paid", "in_payment") for m in freight_moves
            )

    @api.depends("invoice_line_ids.move_id")
    def _compute_invoice_ids(self):
        for lr in self:
            lr.invoice_ids = lr.invoice_line_ids.mapped("move_id")

    @api.depends("trip_link_ids.pod_status", "trip_link_ids.verified_on", "trip_link_ids.state")
    def _compute_pod(self):
        for lr in self:
            links = lr.trip_link_ids.filtered(lambda link: link.state != "cancel")
            if not links:
                lr.pod_status = "none"
                lr.pod_verified_on = False
            elif all(link.pod_status == "verified" for link in links):
                lr.pod_status = "verified"
                lr.pod_verified_on = max(links.mapped("verified_on"))
            elif any(link.pod_status == "verified" for link in links):
                lr.pod_status = "partial"
                lr.pod_verified_on = False
            else:
                lr.pod_status = "pending"
                lr.pod_verified_on = False

    @api.depends("line_ids.declared_value", "line_ids.ewaybill_exempt", "line_ids.ewaybill_id.state",
                 "company_id.tms_ewaybill_threshold", "origin_id.state_id", "destination_id.state_id")
    def _compute_ewaybill_required(self):
        """BR-EWB-03: required above the threshold unless the goods are exempt."""
        for lr in self:
            threshold = lr._tms_ewaybill_threshold()
            lines = lr.line_ids.filtered(lambda line: not line.ewaybill_exempt)
            required = sum(lines.mapped("declared_value")) > threshold if threshold else bool(lines)
            lr.ewaybill_required = required
            lr.ewaybill_missing = required and any(
                not line.ewaybill_id or line.ewaybill_id.state in ("cancelled", "expired") for line in lines
            )

    def _compute_tbb_overdue(self):
        today = fields.Date.context_today(self)
        from .res_partner import CYCLE_DAYS

        for lr in self:
            due = False
            if lr.freight_basis == "tbb" and lr.state == "delivered" and lr.pod_status == "verified":
                days = CYCLE_DAYS.get(lr.billing_partner_id.commercial_partner_id.tms_billing_cycle or "lr", 0)
                due = lr.pod_verified_on and lr.pod_verified_on.date() + timedelta(days=days) < today
            lr.tbb_overdue = bool(due)

    def _search_tbb_overdue(self, operator, value):
        lrs = self.search([("freight_basis", "=", "tbb"), ("state", "=", "delivered"), ("pod_status", "=", "verified")])
        ids = lrs.filtered("tbb_overdue").ids
        positive = (operator == "=" and value) or (operator == "!=" and not value)
        return [("id", "in" if positive else "not in", ids)]

    # ------------------------------------------------------------------
    # ORM
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            company = self.env["res.company"].browse(vals.get("company_id") or self.env.company.id)
            vals.setdefault("tms_mode", company.tms_operation_mode)  # BR-CFG-03
            if vals.get("name", "/") in ("/", False, ""):
                vals["name"] = company._tms_lr_sequence().next_by_id()
                vals["manual_number"] = False
            elif not company.tms_allow_manual_lr:
                raise UserError(_("Branch %s does not allow manual LR numbers.", company.name))
            else:
                vals["manual_number"] = True
        return super().create(vals_list)

    @api.constrains("name", "date", "company_id", "manual_number")
    def _check_manual_number(self):
        for lr in self.filtered("manual_number"):
            year_start = lr.date.replace(month=1, day=1)
            year_end = lr.date.replace(month=12, day=31)
            dup = self.search_count(
                [
                    ("id", "!=", lr.id),
                    ("name", "=", lr.name),
                    ("company_id", "=", lr.company_id.id),
                    ("date", ">=", year_start),
                    ("date", "<=", year_end),
                ]
            )
            if dup:
                raise ValidationError(_("LR number %s is already used in this branch and year.", lr.name))

    @api.constrains("charged_weight_override", "charged_weight_reason")
    def _check_override_reason(self):
        for lr in self:
            if lr.charged_weight_override and not lr.charged_weight_reason:
                raise ValidationError(_("A charged weight override needs a reason (BR-LR-06)."))

    def write(self, vals):
        locked = {"consignee_id", "destination_id"}
        if locked & set(vals) and not self.env.context.get("tms_amendment"):
            for lr in self:
                if lr.state not in ("draft",):
                    raise UserError(
                        _("After confirmation the consignee, destination, weight and packages change only "
                          "through an approved LR amendment (BR-LR-17).")
                    )
        if "charged_weight_override" in vals:
            for lr in self:
                if vals["charged_weight_override"] and vals["charged_weight_override"] != lr.charged_weight_override:
                    self.env["tms.override.log"].log(
                        "charged_weight", lr, _("Charged weight set to %s kg", vals["charged_weight_override"]),
                        reason=vals.get("charged_weight_reason") or lr.charged_weight_reason,
                    )
        return super().write(vals)

    def unlink(self):
        # BR-AUD-04
        if any(lr.state != "draft" for lr in self):
            raise UserError(_("Confirmed LRs are never deleted; cancel them instead."))
        return super().unlink()

    # ------------------------------------------------------------------
    # actions
    # ------------------------------------------------------------------
    def _tms_has_order_item(self):
        """Hook: tms_estimate returns True when the LR names a sales order item."""
        return False

    def _tms_check_confirm(self):
        self.ensure_one()
        missing = []
        for fname in ("consignor_id", "consignee_id", "billing_partner_id", "origin_id", "destination_id",
                      "freight_basis", "gst_payer"):
            if not self[fname]:
                missing.append(self._fields[fname].string)
        if not self.line_ids:
            missing.append(_("at least one commodity line"))
        if not self._tms_has_order_item() and not (self.rate_type and self.typed_rate):
            missing.append(_("a sales order item or a typed rate"))
        if missing:
            raise UserError(_("LR %(lr)s cannot be confirmed; missing: %(m)s (BR-LR-08).", lr=self.name,
                              m=", ".join(missing)))
        if not self._tms_has_order_item() and not self.typed_rate_reason:
            raise UserError(_("A typed rate needs a reason (BR-MD-04)."))

    def _tms_credit_amount(self):
        return self.freight_amount

    def action_confirm(self):
        for lr in self:
            if lr.state != "draft":
                continue
            lr._tms_check_confirm()
            if not lr._tms_has_order_item():
                lr._tms_check_typed_rate_contract()
            # BR-CRD-02, BR-CRD-04: Paid LRs are outside the check; To-Pay counts on the consignee
            if lr.freight_basis != "paid":
                lr.billing_partner_id.tms_check_credit(lr._tms_credit_amount(), lr, _("LR confirmation"))
            if not lr._tms_has_order_item():
                self.env["tms.override.log"].log(
                    "typed_rate", lr, _("Typed rate %(r)s %(t)s", r=lr.typed_rate, t=lr.rate_type),
                    reason=lr.typed_rate_reason, amount=lr.freight_amount,
                )
            lr.state = "confirmed"
            if lr.ewaybill_missing:  # BR-EWB-04: warn at confirmation
                lr.message_post(body=_("Warning: a required e-way bill is missing; the trip cannot start without it."))
        return True

    def _tms_check_typed_rate_contract(self):
        """Hook: tms_contract demands Branch Manager approval for an Active contract customer (BR-CON-15)."""
        return True

    def action_cancel(self):
        """BR-LR-11: cancel only from Draft or Confirmed, and not once the vehicle is placed."""
        for lr in self:
            if lr.state not in ("draft", "confirmed"):
                raise UserError(_("An LR is cancelled only from Draft or Confirmed (BR-LR-11)."))
            if lr.vehicle_placed:
                raise UserError(
                    _("The vehicle for LR %s is placed: remove its trip link with a reason instead (BR-LR-18).",
                      lr.name)
                )
            lr.trip_link_ids.filtered(lambda link: link.state != "cancel").write({"state": "cancel"})
            lr.state = "cancel"
        return True

    def action_draft(self):
        for lr in self:
            if lr.state != "cancel":
                raise UserError(_("Only cancelled LRs return to draft."))
            lr.state = "draft"

    def action_open_credit_override(self):
        """BR-CRD-03: the Branch Manager or Finance Head confirms a credit-blocked LR with a logged reason."""
        return self.env["tms.reason.wizard"].open_for(self, "action_confirm_credit_override",
                                                      _("Override the credit block"))

    def action_confirm_credit_override(self):
        reason = self.env.context.get("tms_reason")
        if not reason:
            raise UserError(_("A credit override needs a reason (BR-CRD-03)."))
        return self.with_context(tms_credit_override_reason=reason).action_confirm()

    def _tms_refresh_state(self):
        """BR-LR-10: Dispatched, Delivered, Not delivered and Invoiced follow the trip links and invoices.
        System-driven: a trip of another branch may move this LR (BR-BRN-10)."""
        for lr in self.sudo():
            if lr.state in ("draft", "cancel"):
                continue
            links = lr.trip_link_ids.filtered(lambda link: link.state != "cancel")
            new = lr.state
            if links and all(link.outcome for link in links):
                if any(link.outcome == "not_delivered" and link.pod_status == "verified" for link in links):
                    new = "not_delivered"
                elif all(link.outcome == "delivered" for link in links) and lr.unplanned_packages == 0:
                    new = "delivered"
                elif any(link.outcome == "refused" and link.pod_status == "verified" for link in links):
                    new = "not_delivered"
            elif any(link.trip_id.state_code in ("started", "in_transit", "delivered", "done") for link in links):
                new = "dispatched"
            if new in ("delivered", "not_delivered") and lr.freight_amount and lr.freight_uninvoiced <= 0.005 \
                    and lr.freight_invoiced > 0:
                new = "invoiced"
            if new == "delivered" and not lr.delivered_on:
                lr.delivered_on = max(links.mapped("delivered_at") or [fields.Datetime.now()])
            if new != lr.state:
                lr.state = new

    def _tms_refresh_invoiced(self):
        for lr in self.sudo():
            if lr.state in ("delivered", "not_delivered", "dispatched", "confirmed") and lr.freight_amount \
                    and lr.freight_invoiced > 0 and lr.freight_uninvoiced <= 0.005:
                if lr.state in ("delivered", "not_delivered"):
                    lr.state = "invoiced"
            elif lr.state == "invoiced" and lr.freight_uninvoiced > 0.005:
                lr.state = "delivered"
                lr._tms_refresh_state()

    def tms_note_vehicle(self, vehicle_label):
        """BR-LR-13: the LR takes the trip's vehicle number; replacements are recorded (BR-BRN-10: sudo)."""
        for lr in self.sudo():
            if lr.vehicle_number and lr.vehicle_number != vehicle_label:
                history = (lr.vehicle_history or "") + "%s: %s -> %s\n" % (
                    fields.Datetime.now(), lr.vehicle_number, vehicle_label)
                lr.write({"vehicle_history": history, "vehicle_number": vehicle_label})
            elif not lr.vehicle_number:
                lr.vehicle_number = vehicle_label

    def action_view_trips(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Trips"),
            "res_model": "tms.order",
            "view_mode": "list,form",
            "domain": [("id", "in", self.trip_ids.ids)],
        }

    def action_view_invoices(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Invoices"),
            "res_model": "account.move",
            "view_mode": "list,form",
            "domain": [("id", "in", self.invoice_ids.ids)],
        }

    def _tms_invoice_group_key(self):
        """Hook: LRs that may share one invoice (tms_contract keeps contracts apart)."""
        return ()

    def _tms_invoice_header_vals(self):
        """Hook: header values of the invoice (tms_contract adds the customer's PO reference)."""
        return {}

    def action_invoice(self):
        """BR-INV-01..03 entry point from the LR form."""
        return self.env["tms.invoice.lr.wizard"].with_context(active_ids=self.ids,
                                                             active_model=self._name).action_open(self)

    def action_print_lr(self):
        return self.env.ref("tms_core.action_report_lr").report_action(self)

    def action_open_amendment(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("LR amendment"),
            "res_model": "tms.lr.amendment.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_consignment_id": self.id},
        }

    def _tms_raise_godown_demurrage(self):
        """BR-POD-09 daily: refused goods held in the godown accrue demurrage as a customer charge."""
        category = self.env.ref("tms_core.tms_category_godown_demurrage", raise_if_not_found=False)
        for lr in self.filtered("godown_hold"):
            rate = lr.delivery_branch_id.tms_godown_demurrage_per_day or lr.company_id.tms_godown_demurrage_per_day
            if not rate or not category or not lr.godown_since:
                continue
            days = (fields.Date.context_today(self) - lr.godown_since).days
            charge = lr.charge_ids.filtered(lambda c: c.category_id == category and c.state == "draft")[:1]
            link = lr.trip_link_ids.filtered(lambda x: x.outcome == "refused")[:1]
            if charge:
                charge.amount = rate * max(days, 0)
            elif days > 0:
                self.env["tms.trip.charge"].create({
                    "trip_id": link.trip_id.id,
                    "consignment_id": lr.id,
                    "category_id": category.id,
                    "amount": rate * days,
                    "recover_from": "customer",
                    "partner_id": lr.billing_partner_id.id,
                    "note": _("Godown demurrage since %s", lr.godown_since),
                })

    @api.model
    def _cron_godown_demurrage(self):
        self.search([("godown_hold", "=", True)])._tms_raise_godown_demurrage()


class TmsConsignmentLine(models.Model):
    """BR-LR-04: commodity lines."""

    _name = "tms.consignment.line"
    _description = "LR commodity line"
    _order = "sequence, id"

    consignment_id = fields.Many2one("tms.consignment", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="consignment_id.company_id", store=True)
    currency_id = fields.Many2one(related="consignment_id.currency_id")
    sequence = fields.Integer(default=10)
    commodity = fields.Char(required=True)
    packaging_type = fields.Selection(
        [("bags", "Bags"), ("pallets", "Pallets"), ("boxes", "Boxes"), ("loose", "Loose"), ("container", "Container")],
        default="boxes",
        required=True,
    )
    packages = fields.Integer(required=True, default=1)
    actual_weight = fields.Float(string="Actual weight (kg)", required=True)
    charged_weight = fields.Float(string="Charged weight (kg)", compute="_compute_charged", store=True, readonly=False)
    declared_value = fields.Monetary()
    ewaybill_id = fields.Many2one(
        "tms.ewaybill", string="E-way bill", domain="[('consignment_id', '=', consignment_id)]"
    )
    ewaybill_exempt = fields.Boolean(string="Exempt from e-way bill")
    hsn_code = fields.Char(string="HSN")
    # BR-ODC-04
    piece_length = fields.Float(string="Length (m)")
    piece_width = fields.Float(string="Width (m)")
    piece_height = fields.Float(string="Height (m)")

    @api.depends("actual_weight")
    def _compute_charged(self):
        for line in self:
            line.charged_weight = roundup10(line.actual_weight)

    def write(self, vals):
        protected = {"packages", "actual_weight", "charged_weight", "commodity"}
        if protected & set(vals) and not self.env.context.get("tms_amendment"):
            if any(line.consignment_id.state != "draft" for line in self):
                raise UserError(_("Weight and packages change after confirmation only through an approved "
                                  "LR amendment (BR-LR-17)."))
        return super().write(vals)

    @api.constrains("packages", "actual_weight")
    def _check_positive(self):
        for line in self:
            if line.packages < 0 or line.actual_weight < 0:
                raise ValidationError(_("Packages and weight cannot be negative."))


class TmsEwaybill(models.Model):
    """BR-EWB-01..07."""

    _name = "tms.ewaybill"
    _description = "E-way bill"
    _inherit = ["mail.thread"]
    _order = "valid_until"

    name = fields.Char(string="E-way bill number", required=True, tracking=True)
    consignment_id = fields.Many2one("tms.consignment", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="consignment_id.company_id", store=True)
    currency_id = fields.Many2one(related="consignment_id.currency_id")
    consignor_invoice_number = fields.Char()
    consignor_invoice_date = fields.Date()
    consignor_invoice_value = fields.Monetary()
    generated_on = fields.Datetime(required=True, default=fields.Datetime.now)
    valid_until = fields.Datetime(tracking=True)
    distance_km = fields.Float()
    is_odc = fields.Boolean(string="Over Dimensional Cargo")
    partb_vehicle = fields.Char(string="Part-B vehicle", tracking=True)
    state = fields.Selection(
        [("active", "Active"), ("extended", "Extended"), ("expired", "Expired"), ("cancelled", "Cancelled")],
        default="active",
        tracking=True,
    )
    attachment = fields.Binary(string="File", attachment=True)
    attachment_name = fields.Char()
    line_ids = fields.One2many("tms.consignment.line", "ewaybill_id")
    update_ids = fields.One2many("tms.ewaybill.update", "ewaybill_id")
    expected_valid_until = fields.Datetime(compute="_compute_expected_validity")
    validity_mismatch = fields.Boolean(compute="_compute_expected_validity")

    @api.constrains("name")
    def _check_number(self):
        for bill in self:
            if not (bill.name.isdigit() and len(bill.name) == 12):
                raise ValidationError(_("An e-way bill number has 12 digits."))

    @api.depends("generated_on", "distance_km", "is_odc", "valid_until")
    def _compute_expected_validity(self):
        """#23.5: ceil(d/200) days, ceil(d/20) for ODC, to midnight at the end of the D-th day."""
        for bill in self:
            if not bill.generated_on or not bill.distance_km:
                bill.expected_valid_until = False
                bill.validity_mismatch = False
                continue
            per_day = 20.0 if bill.is_odc else 200.0
            days = max(int(math.ceil(bill.distance_km / per_day)), 1)
            end_day = bill.generated_on.date() + timedelta(days=days)
            bill.expected_valid_until = datetime.combine(end_day, time(23, 59, 59))
            bill.validity_mismatch = bool(
                bill.valid_until and abs((bill.valid_until - bill.expected_valid_until).total_seconds()) > 86400
            )

    @api.model_create_multi
    def create(self, vals_list):
        bills = super().create(vals_list)
        for bill in bills.filtered(lambda b: not b.valid_until):
            bill.valid_until = bill.expected_valid_until
        return bills

    def _is_live(self):
        now = fields.Datetime.now()
        return self.filtered(lambda b: b.state in ("active", "extended") and (not b.valid_until or b.valid_until >= now))

    @api.model
    def _cron_expiry_alert(self):
        """BR-EWB-06, BR-NTF-04: bills on active trips expiring within 8 hours."""
        now = fields.Datetime.now()
        bills = self.search(
            [("state", "in", ("active", "extended")), ("valid_until", ">=", now - timedelta(hours=8)),
             ("valid_until", "<=", now + timedelta(hours=8))]
        )
        for bill in bills:
            trips = bill.consignment_id.trip_link_ids.filtered(
                lambda link: link.state != "cancel" and link.outcome is False
            ).mapped("trip_id").filtered(lambda t: t.state_code in ("started", "in_transit"))
            for trip in trips:
                users = trip.user_id or users_in_group(self.env, "tms_core.group_dispatcher", trip.company_id)
                summary = _("E-way bill %(b)s expires at %(t)s: extend it", b=bill.name, t=bill.valid_until)
                if trip.activity_ids.filtered(lambda a, s=summary: a.summary == s):
                    continue
                schedule(trip, users, summary)
                # BR-NTF-04: activity and email
                partners = users.mapped("partner_id").filtered("email")
                if partners:
                    trip.sudo().message_post(subject=summary, body=summary, partner_ids=partners.ids,
                                             message_type="comment", subtype_xmlid="mail.mt_comment")
            if bill.valid_until < now and bill.state != "expired":
                bill.state = "expired"


class TmsEwaybillUpdate(models.Model):
    """BR-EWB-05..07: Part-B, extension and consolidation tasks with evidence."""

    _name = "tms.ewaybill.update"
    _description = "E-way bill update task"
    _order = "id desc"

    ewaybill_id = fields.Many2one("tms.ewaybill", ondelete="cascade")
    trip_id = fields.Many2one("tms.order", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="trip_id.company_id", store=True)
    assignment_id = fields.Many2one("tms.trip.assignment")
    kind = fields.Selection(
        [("partb", "Part-B update"), ("extension", "Extension"), ("consolidation", "Consolidated bill")],
        required=True,
        default="partb",
    )
    vehicle_number = fields.Char()
    consolidated_number = fields.Char()
    new_valid_until = fields.Datetime()
    evidence = fields.Binary(attachment=True)
    evidence_name = fields.Char()
    evidence_reference = fields.Char()
    state = fields.Selection([("pending", "Pending"), ("done", "Done")], default="pending", required=True)
    done_by_id = fields.Many2one("res.users", readonly=True)
    done_at = fields.Datetime(readonly=True)

    def action_done(self):
        for task in self:
            if not (task.evidence or task.evidence_reference):
                raise UserError(_("Attach the portal evidence or its reference first (BR-EWB-05)."))
            vals = {"state": "done", "done_by_id": self.env.user.id, "done_at": fields.Datetime.now()}
            task.write(vals)
            if task.kind == "partb" and task.ewaybill_id:
                task.ewaybill_id.partb_vehicle = task.vehicle_number
            if task.kind == "extension" and task.ewaybill_id and task.new_valid_until:
                task.ewaybill_id.write({"valid_until": task.new_valid_until, "state": "extended"})
            if task.kind == "consolidation":
                task.trip_id.consolidated_ewaybill = task.consolidated_number
        return True


class TmsConsignmentAmendment(models.Model):
    """BR-LR-17: changes after confirmation need Branch Manager approval."""

    _name = "tms.consignment.amendment"
    _description = "LR amendment"
    _inherit = ["mail.thread"]
    _order = "id desc"

    consignment_id = fields.Many2one("tms.consignment", required=True, ondelete="cascade")
    company_id = fields.Many2one(related="consignment_id.company_id", store=True)
    reason = fields.Text(required=True)
    new_consignee_id = fields.Many2one("res.partner")
    new_destination_id = fields.Many2one("res.partner", domain=[("tms_location", "=", True)])
    is_diversion = fields.Boolean(help="Destination change in transit; adds km to the trip.")
    diversion_km = fields.Float()
    new_actual_weight = fields.Float(string="New actual weight (kg)", help="New total actual weight; applied to the first line.")
    new_packages = fields.Integer(help="New total packages; applied to the first line.")
    state = fields.Selection(
        [("to_approve", "To approve"), ("approved", "Approved"), ("rejected", "Rejected")],
        default="to_approve",
        tracking=True,
    )
    requested_by_id = fields.Many2one("res.users", default=lambda s: s.env.user, readonly=True)
    approved_by_id = fields.Many2one("res.users", readonly=True)
    summary = fields.Text(readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        recs = super().create(vals_list)
        for rec in recs:
            users = users_in_group(self.env, "tms_core.group_branch_manager", rec.company_id)
            schedule(rec.consignment_id, users, _("LR amendment to approve"), rec.reason)
        return recs

    def action_approve(self):
        for am in self:
            if am.state != "to_approve":
                raise UserError(_("Only an amendment awaiting approval is approved."))
            if not self.env.user.has_group("tms_core.group_branch_manager"):
                raise UserError(_("Only a Branch Manager approves an LR amendment."))
            if am.requested_by_id == self.env.user:
                raise UserError(_("You cannot approve your own amendment."))
            lr = am.consignment_id.with_context(tms_amendment=True)
            vals, changes = {}, []
            if am.new_consignee_id:
                vals["consignee_id"] = am.new_consignee_id.id
                changes.append(_("consignee → %s", am.new_consignee_id.display_name))
            if am.new_destination_id:
                vals["destination_id"] = am.new_destination_id.id
                changes.append(_("destination → %s", am.new_destination_id.display_name))
            if vals:
                lr.write(vals)
            line = lr.line_ids[:1]
            if line and (am.new_actual_weight or am.new_packages):
                rest_w = sum((lr.line_ids - line).mapped("actual_weight"))
                rest_p = sum((lr.line_ids - line).mapped("packages"))
                lvals = {}
                if am.new_actual_weight:
                    lvals["actual_weight"] = max(am.new_actual_weight - rest_w, 0.0)
                    changes.append(_("weight → %s kg", am.new_actual_weight))
                if am.new_packages:
                    lvals["packages"] = max(am.new_packages - rest_p, 0)
                    changes.append(_("packages → %s", am.new_packages))
                line.with_context(tms_amendment=True).write(lvals)
            if am.is_diversion and am.diversion_km:
                for trip in lr.trip_ids.filtered(lambda t: t.state_code in ("started", "in_transit")):
                    trip.diversion_km += am.diversion_km
                changes.append(_("diversion +%s km", am.diversion_km))
            for bill in lr.ewaybill_ids._is_live():
                bill.message_post(body=_("Update the e-way bill for the LR amendment: %s", ", ".join(changes)))
            am.write({"state": "approved", "approved_by_id": self.env.user.id, "summary": ", ".join(changes)})
            lr.message_post(body=_("Amendment approved: %(c)s. Reason: %(r)s", c=", ".join(changes), r=am.reason))
            am._tms_after_approve()
        return True

    def _tms_after_approve(self):
        """Hook: tms_contract amends the contract item for a diversion (BR-CON-23)."""
        return True

    def action_reject(self):
        for am in self:
            if am.state != "to_approve":
                raise UserError(_("Only an amendment awaiting approval is rejected."))
            if not self.env.user.has_group("tms_core.group_branch_manager"):
                raise UserError(_("Only a Branch Manager rejects an LR amendment (BR-LR-17)."))
            am.write({"state": "rejected", "approved_by_id": self.env.user.id})
        return True
