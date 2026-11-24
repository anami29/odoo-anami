"""Exception screens as drawn in the agreed mockup (TMS-UIG-001, 18.0.1.6.0): the incident (reference, commercial
treatment, prefilled charge and claim), the damage claim (smart buttons, banner, settlement legs, evidence files) and
the charges and expenses registers (billing and recovery status)."""

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import format_datetime

from .tools import inr

TREATMENTS = [
    ("none", "No money"),
    ("absorb", "Absorb (own company)"),
    ("charge_customer", "Charge customer"),
    ("recover_vendor", "Recover from transporter"),
    ("charge_and_recover", "Charge customer and recover"),
    ("claim", "Customer claim"),
]
BILLING_DETAIL = [
    ("na", "-"),
    ("to_invoice", "To invoice"),
    ("on_draft", "On a draft invoice or bill"),
    ("invoiced", "Invoiced"),
    ("to_deduct", "To deduct"),
    ("deducted", "Deducted"),
    ("waived", "Waived"),
]


def _badge(text, tone):
    return Markup('<span class="badge rounded-pill text-bg-%s">%s</span>') % (tone, text)


class TmsTripIncident(models.Model):
    _inherit = "tms.trip.incident"

    reference = fields.Char(readonly=True, copy=False, index=True)
    category_code = fields.Char(related="category_id.code")
    responsible = fields.Selection(
        [("company", "Own company"), ("customer", "Customer"), ("vendor", "Transporter / broker"),
         ("driver", "Driver"), ("third_party", "Third party")], string="Responsible party")
    subtitle = fields.Char(compute="_compute_subtitle")
    cost_amount = fields.Monetary(compute="_compute_treatment", string="Cost amount", currency_field="currency_id")
    customer_charge = fields.Monetary(compute="_compute_treatment", string="Customer charge",
                                      currency_field="currency_id")
    vendor_recovery = fields.Monetary(compute="_compute_treatment", string="Vendor recovery",
                                      currency_field="currency_id")
    treatment = fields.Selection(TREATMENTS, compute="_compute_treatment")
    realised_via = fields.Char(compute="_compute_treatment", string="Realised via")

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("reference"):
                vals["reference"] = self.env["ir.sequence"].next_by_code("tms.trip.incident") or False
        return super().create(vals_list)

    @api.depends("category_id", "trip_id", "reference")
    def _compute_name(self):
        super()._compute_name()
        for inc in self.filtered("reference"):
            inc.name = inc.reference

    @api.depends("category_id", "occurred_at", "location")
    def _compute_subtitle(self):
        for inc in self:
            parts = [inc.category_id.name or "", format_datetime(self.env, inc.occurred_at, dt_format="dd/MM/yyyy HH:mm")
                     if inc.occurred_at else "", inc.location or ""]
            inc.subtitle = " · ".join(p for p in parts if p)

    @api.depends("expense_ids.amount", "expense_ids.state", "charge_ids.amount", "charge_ids.state",
                 "charge_ids.recover_from", "claim_id", "responsible")
    def _compute_treatment(self):
        for inc in self:
            expenses = inc.expense_ids.filtered(lambda e: e.state != "cancel")
            charges = inc.charge_ids.filtered(lambda c: c.state not in ("cancel", "waived"))
            cust = charges.filtered(lambda c: c.recover_from == "customer")
            vend = charges.filtered(lambda c: c.recover_from != "customer")
            inc.cost_amount = sum(expenses.mapped("amount"))
            inc.customer_charge = sum(cust.mapped("amount"))
            inc.vendor_recovery = sum(vend.mapped("amount"))
            if inc.claim_id:
                inc.treatment = "claim"
            elif cust and vend:
                inc.treatment = "charge_and_recover"
            elif cust:
                inc.treatment = "charge_customer"
            elif vend:
                inc.treatment = "recover_vendor"
            elif expenses or inc.responsible == "company":
                inc.treatment = "absorb"
            else:
                inc.treatment = "none"
            docs = (charges.mapped("name") + expenses.mapped("name") + ([inc.claim_id.name] if inc.claim_id else []))
            inc.realised_via = ", ".join(d for d in docs if d and d != "/") or False

    def _tms_charge_defaults(self):
        self.ensure_one()
        recover = {"customer": "customer", "vendor": "vendor", "driver": "driver"}.get(self.responsible, "customer")
        partner = self.responsible_partner_id
        if not partner and recover == "customer":
            partner = self.consignment_id.billing_partner_id or self.trip_id.consignment_ids[:1].billing_partner_id
        if not partner and recover == "vendor":
            partner = self.trip_id.current_broker_id if "current_broker_id" in self.trip_id else partner
        category = self.category_id if self.category_id.use_charge else self.env.ref(
            "tms_core.tms_category_detention", raise_if_not_found=False)
        return {"trip_id": self.trip_id.id, "consignment_id": self.consignment_id.id, "incident_id": self.id,
                "category_id": category.id if category else False, "amount": self.estimated_amount,
                "recover_from": recover, "partner_id": partner.id or False,
                "driver_id": self.trip_id.current_assignment_id.driver_id.id if recover == "driver" else False}

    def action_create_charge(self):
        """Create the charge prefilled from the incident; with a party and an amount it is confirmed at once (and
        approved within the self-approval limit, which resolves the incident)."""
        self.ensure_one()
        vals = self._tms_charge_defaults()
        if vals["category_id"] and vals["amount"] > 0 and (vals["partner_id"] or vals["recover_from"] == "driver"):
            charge = self.env["tms.trip.charge"].create(vals)
            charge.action_confirm()
            self.message_post(body=_("Charge %(c)s created: %(a)s, %(s)s.", c=charge.name, a=inr(charge.amount),
                                     s=dict(charge._fields["state"]._description_selection(self.env))[charge.state]))
            return {"type": "ir.actions.act_window", "res_model": "tms.trip.charge", "res_id": charge.id,
                    "view_mode": "form"}
        return {"type": "ir.actions.act_window", "res_model": "tms.trip.charge", "view_mode": "form",
                "context": {"default_%s" % k: v for k, v in vals.items() if v}}

    def action_create_claim(self):
        res = super().action_create_claim()
        for inc in self.filtered("claim_id"):
            claim = inc.claim_id
            if claim.state == "draft" and not claim.estimated_amount and inc.estimated_amount:
                claim.estimated_amount = inc.estimated_amount
            if claim.state == "draft" and claim.estimated_amount:
                try:
                    with self.env.cr.savepoint():
                        claim.action_submit()
                except UserError:
                    pass  # left in draft: the claim form says what is missing
        return res

    def _tms_resolve_after_charge(self):
        for inc in self.filtered(lambda i: i.state == "open"):
            if inc.category_id.code in ("DAMAGE", "SHORTAGE") and not inc.claim_id:
                continue
            inc.state = "resolved"
            inc.message_post(body=_("Resolved: realised by %s.", inc.realised_via or "-"))

    def action_view_trip(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "res_model": "tms.order", "res_id": self.trip_id.id,
                "view_mode": "form"}

    def action_view_lr(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "res_model": "tms.consignment", "res_id": self.consignment_id.id,
                "view_mode": "form"}

    def action_view_claim(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "res_model": "tms.damage.claim", "res_id": self.claim_id.id,
                "view_mode": "form"}


class TmsTripCharge(models.Model):
    _inherit = "tms.trip.charge"

    billing_detail = fields.Selection(BILLING_DETAIL, compute="_compute_billing_detail", string="Billing status")
    incurred_amount = fields.Monetary(compute="_compute_billing_detail", string="Incurred",
                                      help="What the company paid for the event (the linked expense or hire memo line).")
    customer_amount = fields.Monetary(compute="_compute_billing_detail", string="Customer charge")
    vendor_amount = fields.Monetary(compute="_compute_billing_detail", string="Vendor recovery")
    treatment_label = fields.Char(compute="_compute_billing_detail", string="Treatment")

    @api.depends("state", "move_line_id.parent_state", "recover_from", "amount", "expense_id.amount")
    def _compute_billing_detail(self):
        for charge in self:
            customer = charge.recover_from == "customer"
            if charge.state == "waived":
                detail = "waived"
            elif charge.move_line_id and charge.move_line_id.parent_state == "draft":
                detail = "on_draft"
            elif charge.state == "billed":
                detail = "invoiced"
            elif charge.state == "deducted":
                detail = "deducted"
            elif charge.state == "approved":
                detail = "to_invoice" if customer else "to_deduct"
            else:
                detail = "na"
            charge.billing_detail = detail
            charge.incurred_amount = charge.expense_id.amount if charge.expense_id else (
                charge.incident_id.cost_amount if charge.incident_id else 0.0)
            charge.customer_amount = charge.amount if customer else 0.0
            charge.vendor_amount = 0.0 if customer else charge.amount
            charge.treatment_label = _("Charge customer") if customer else (
                _("Recover from driver") if charge.recover_from == "driver" else _("Recover from transporter"))

    def _tms_on_approved(self):
        res = super()._tms_on_approved()
        self.mapped("incident_id")._tms_resolve_after_charge()
        return res


class TmsTripExpense(models.Model):
    _inherit = "tms.trip.expense"

    recovery_status = fields.Char(compute="_compute_recovery_status", string="Recovery status")

    @api.depends("recoverable_from", "charge_id.state")
    def _compute_recovery_status(self):
        states = dict(self.env["tms.trip.charge"]._fields["state"]._description_selection(self.env))
        for exp in self:
            if exp.recoverable_from in (False, "none"):
                exp.recovery_status = False
            elif exp.charge_id:
                exp.recovery_status = "%s · %s" % (exp.charge_id.name, states.get(exp.charge_id.state, ""))
            else:
                exp.recovery_status = _("Recovery charge not raised")


class TmsDamageClaim(models.Model):
    _inherit = "tms.damage.claim"

    approved_on = fields.Datetime(readonly=True, copy=False)
    evidence_ids = fields.Many2many("ir.attachment", "tms_claim_evidence_rel", "claim_id", "attachment_id",
                                    string="Evidence files")
    evidence_count = fields.Integer(compute="_compute_claim_ui")
    claim_banner = fields.Char(compute="_compute_claim_ui")
    pod_status = fields.Char(compute="_compute_claim_ui", string="POD")
    settlement_html = fields.Html(compute="_compute_claim_ui", sanitize=False)
    recovery_bill_id = fields.Many2one("account.move", compute="_compute_claim_ui", string="Vendor bill")

    def action_approve(self):
        res = super().action_approve()
        self.filtered(lambda c: c.state == "approved" and not c.approved_on).write(
            {"approved_on": fields.Datetime.now()})
        return res

    def _compute_claim_ui(self):
        for claim in self:
            src = claim.sudo()
            claim.evidence_count = len(src.evidence_ids) + (1 if src.evidence else 0)
            links = src.pod_link_id or src.consignment_id.trip_link_ids.filtered(lambda x: x.state != "cancel")
            verified = links.filtered(lambda x: x.pod_status == "verified")
            if links and verified == links:
                last = verified.sorted("verified_on")[-1:]
                claim.pod_status = _("Verified · %(u)s · %(d)s", u=last.verified_by_id.name or "",
                                     d=format_datetime(self.env, last.verified_on, dt_format="dd/MM/yyyy HH:mm"))
            elif links.filtered(lambda x: x.pod_status == "uploaded"):
                claim.pod_status = _("Uploaded, not verified")
            else:
                claim.pod_status = _("Not uploaded")
            limit = src.company_id.tms_claim_bm_limit
            amount = src.approved_amount or src.estimated_amount
            if src.state == "under_review":
                who = _("The Branch Manager") if amount <= limit else _("The Operations Head")
                pod = _("POD verified") if verified and verified == links else _("waiting for the POD verification")
                claim.claim_banner = _("Under review, %(p)s. %(w)s approves claims of %(a)s (Branch Manager up to "
                                       "%(l)s). The raiser, %(r)s, cannot approve it.", p=pod, w=who,
                                       a=inr(amount), l=inr(limit), r=src.create_uid.name or "")
            elif src.state == "approved":
                claim.claim_banner = _("Approved by %(u)s. Settlement is pending until the credit note and every "
                                       "recovery are posted.", u=src.approved_by_id.name or "")
            else:
                claim.claim_banner = False
            recovery_line = src.recovery_charge_ids.mapped("move_line_id")
            claim.recovery_bill_id = recovery_line.mapped("move_id")[:1] or src.recovery_charge_ids.mapped(
                "recovery_move_id")[:1]
            # settlement legs
            cn = src.credit_note_id
            cust_status = (_("Posted") if cn.state == "posted" else _("Draft")) if cn else _("To create on approval")
            cust_doc = cn.name if cn and cn.name != "/" else (_("Credit note (draft)") if cn else _("Credit note"))
            rows = [(_("Customer leg · pay the claimant"), src.claimant_id.name or src.consignment_id.billing_partner_id
                     .name or "", cust_doc, cust_status, -(src.approved_amount or src.estimated_amount))]
            for charge in src.recovery_charge_ids:
                rows.append((_("Vendor leg · recover from the responsible party"), charge.partner_id.name or "",
                             charge.move_id.name or charge.name,
                             dict(charge._fields["state"]._description_selection(self.env)).get(charge.state, ""),
                             charge.amount))
            if src.responsible != "company" and not src.recovery_charge_ids and src.recovery_amount:
                rows.append((_("Vendor leg · recover from the responsible party"),
                             src.responsible_partner_id.name or src.responsible_driver_id.name or "",
                             _("Recovery charge"), _("To create on approval"), src.recovery_amount))
            net = sum(r[4] for r in rows)
            body = Markup("").join(
                Markup("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td class='text-end'>%s</td></tr>") % (
                    r[0], r[1], r[2], r[3], inr(r[4]).replace("-", "−")) for r in rows)
            claim.settlement_html = Markup(
                "<table class='table table-sm'><thead><tr><th>%s</th><th>%s</th><th>%s</th><th>%s</th>"
                "<th class='text-end'>%s</th></tr></thead><tbody>%s<tr class='fw-bold border-top'><td colspan='4'>%s"
                "</td><td class='text-end'>%s</td></tr></tbody></table>") % (
                _("Leg"), _("Party"), _("Document"), _("Status"), _("Amount"), body,
                _("Net impact on trip %s contribution", src.trip_id.name or ""), inr(net).replace("-", "−"))

    def action_view_trip(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "res_model": "tms.order", "res_id": self.trip_id.id,
                "view_mode": "form"}

    def action_view_lr(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "res_model": "tms.consignment", "res_id": self.consignment_id.id,
                "view_mode": "form"}

    def action_view_credit_note(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "res_model": "account.move", "res_id": self.credit_note_id.id,
                "view_mode": "form"}

    def action_view_recovery_bill(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "res_model": "account.move", "res_id": self.recovery_bill_id.id,
                "view_mode": "form"}
