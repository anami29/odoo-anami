from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .tms_money import _default_account
from .tools import schedule, users_in_group

DAMAGE_TYPES = [
    ("damaged_goods", "Damaged goods"),
    ("partial_damage", "Partial damage"),
    ("shortage", "Shortage"),
    ("leakage", "Leakage"),
    ("packaging_damage", "Packaging damage"),
    ("wet_damage", "Wet damage"),
]


class TmsDamageClaim(models.Model):
    """BR-CLM-01..12."""

    _name = "tms.damage.claim"
    _description = "Damage and shortage claim"
    _inherit = ["mail.thread", "mail.activity.mixin", "tms.reason.mixin"]
    _order = "id desc"

    name = fields.Char(default="/", copy=False, readonly=True)
    consignment_id = fields.Many2one("tms.consignment", string="LR", required=True, index=True, tracking=True)
    trip_id = fields.Many2one("tms.order", index=True)
    company_id = fields.Many2one(related="consignment_id.company_id", store=True, string="Branch")
    currency_id = fields.Many2one(related="company_id.currency_id")
    incident_id = fields.Many2one("tms.trip.incident")
    pod_link_id = fields.Many2one("tms.trip.consignment", string="POD line")
    damage_type = fields.Selection(DAMAGE_TYPES, required=True, default="damaged_goods", tracking=True)
    affected_qty = fields.Integer(string="Affected packages")
    estimated_amount = fields.Monetary(tracking=True)
    approved_amount = fields.Monetary(tracking=True, copy=False)
    claimant_id = fields.Many2one("res.partner", tracking=True)
    responsible = fields.Selection(
        [("company", "Company"), ("vendor", "Vendor"), ("driver", "Driver"), ("third_party", "Third party")],
        default="company",
        required=True,
        tracking=True,
    )
    responsible_partner_id = fields.Many2one("res.partner")
    responsible_driver_id = fields.Many2one("tms.driver")
    recovery_amount = fields.Monetary(help="Share borne by the responsible party (BR-CLM-06).")
    evidence = fields.Binary(attachment=True)
    evidence_name = fields.Char()
    notice_date = fields.Date(string="Customer notice date", tracking=True)
    time_barred = fields.Boolean(compute="_compute_checks")
    owner_risk = fields.Boolean(compute="_compute_checks")
    cap_amount = fields.Monetary(compute="_compute_checks", string="Liability cap")
    owner_risk_accepted_by_id = fields.Many2one("res.users", readonly=True, tracking=True)
    timebar_admitted_by_id = fields.Many2one("res.users", readonly=True, tracking=True)
    cap_override_by_id = fields.Many2one("res.users", readonly=True, tracking=True)
    cap_override_reason = fields.Text(tracking=True)
    state = fields.Selection(
        [("draft", "Draft"), ("under_review", "Under Review"), ("approved", "Approved"), ("settled", "Settled"),
         ("rejected", "Rejected")],
        default="draft",
        tracking=True,
        copy=False,
    )
    approved_by_id = fields.Many2one("res.users", readonly=True, copy=False)
    reject_reason = fields.Text(tracking=True)
    credit_note_id = fields.Many2one("account.move", readonly=True, copy=False)
    recovery_charge_ids = fields.One2many("tms.trip.charge", "claim_id")
    insurer_claim_ids = fields.One2many("tms.insurer.claim", "claim_id")

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "/") == "/":
                vals["name"] = self.env["ir.sequence"].next_by_code("tms.damage.claim") or "/"
        return super().create(vals_list)

    @api.depends("consignment_id.declared_value", "consignment_id.risk_basis", "consignment_id.delivered_on",
                 "notice_date", "pod_link_id.delivered_at", "company_id.tms_carrier_liability_limit")
    def _compute_checks(self):
        for claim in self:
            lr = claim.consignment_id
            company = claim.company_id
            caps = [lr.declared_value] if lr.declared_value else []
            if company.tms_carrier_liability_limit:
                caps.append(company.tms_carrier_liability_limit)
            claim.cap_amount = min(caps) if caps else 0.0
            claim.owner_risk = lr.risk_basis == "owner"
            ref_date = (lr.delivered_on or (claim.pod_link_id.delivered_at if claim.pod_link_id else False))
            barred = False
            if claim.notice_date and ref_date and company.tms_claim_notice_days:
                barred = (claim.notice_date - ref_date.date()).days > company.tms_claim_notice_days
            claim.time_barred = barred

    def unlink(self):
        if any(c.state not in ("draft",) for c in self):
            raise UserError(_("Only draft claims are deleted."))
        return super().unlink()

    def action_submit(self):
        for claim in self:
            if claim.state != "draft":
                continue
            claim.state = "under_review"
            group = "tms_core.group_branch_manager" if claim.estimated_amount <= claim.company_id.tms_claim_bm_limit \
                else "tms_core.group_operations_head"
            schedule(claim, users_in_group(self.env, group, claim.company_id) - claim.create_uid,
                     _("Claim %s awaits approval", claim.name))  # BR-NTF-06
        return True

    def _is_oh(self):
        return self.env.user.has_group("tms_core.group_operations_head")

    def action_open_accept_owner_risk(self):
        """BR-CLM-09, BR-AUD-06: the acceptance asks for its reason."""
        if not self._is_oh():
            raise UserError(_("Only the Operations Head accepts carrier liability on an owner's-risk LR."))
        return self.env["tms.reason.wizard"].open_for(self, "action_accept_owner_risk",
                                                      _("Accept carrier liability on an owner's-risk LR"))

    def action_open_admit_late_notice(self):
        """BR-CLM-11, BR-AUD-06: the admission asks for its reason."""
        if not self._is_oh():
            raise UserError(_("Only the Operations Head admits a late notice."))
        return self.env["tms.reason.wizard"].open_for(self, "action_admit_late_notice",
                                                      _("Admit a time-barred notice"))

    def action_accept_owner_risk(self):
        """BR-CLM-09."""
        reason = self.env.context.get("tms_reason")
        for claim in self:
            if not self._is_oh():
                raise UserError(_("Only the Operations Head accepts carrier liability on an owner's-risk LR."))
            if not reason:
                raise UserError(_("Give the reason for accepting carrier liability (BR-AUD-06)."))
            claim.owner_risk_accepted_by_id = self.env.user
            self.env["tms.override.log"].log("claim", claim, _("Owner's-risk liability accepted"), reason=reason)

    def action_admit_late_notice(self):
        """BR-CLM-11."""
        reason = self.env.context.get("tms_reason")
        for claim in self:
            if not self._is_oh():
                raise UserError(_("Only the Operations Head admits a late notice."))
            if not reason:
                raise UserError(_("Give the reason for admitting the late notice (BR-AUD-06)."))
            claim.timebar_admitted_by_id = self.env.user
            self.env["tms.override.log"].log("claim", claim, _("Time-barred notice admitted"), reason=reason)

    def action_override_cap(self):
        """BR-CLM-10."""
        for claim in self:
            if not self._is_oh():
                raise UserError(_("Only the Operations Head approves an amount above the cap."))
            if not claim.cap_override_reason:
                raise UserError(_("Enter the reason for exceeding the cap."))
            claim.cap_override_by_id = self.env.user
            self.env["tms.override.log"].log("claim", claim, _("Claim cap exceeded"),
                                             reason=claim.cap_override_reason, amount=claim.approved_amount)

    def action_approve(self):
        for claim in self:
            if claim.state != "under_review":
                raise UserError(_("Submit the claim for review first."))
            if claim.create_uid == self.env.user:
                raise UserError(_("Whoever raised a claim cannot approve it (BR-CLM-04)."))
            amount = claim.approved_amount or claim.estimated_amount
            if not amount:
                raise UserError(_("Enter the approved amount."))
            limit = claim.company_id.tms_claim_bm_limit
            if amount > limit and not self._is_oh():
                raise UserError(_("Claims above %s are approved by the Operations Head (BR-CLM-04).", limit))
            if not self.env.user.has_group("tms_core.group_branch_manager") and not self._is_oh():
                raise UserError(_("A Branch Manager or the Operations Head approves claims (BR-CLM-04)."))
            lr = claim.consignment_id
            # the claimed trip link's POD or outcome, else every POD of the LR
            verified = claim.pod_link_id.pod_status == "verified" if claim.pod_link_id \
                else lr.pod_status == "verified"
            if not verified:
                raise UserError(_("The LR's POD or outcome must be verified first (BR-CLM-04, BR-POD-08)."))
            if claim.owner_risk and not claim.owner_risk_accepted_by_id:
                raise UserError(_("An owner's-risk LR needs the Operations Head to accept liability (BR-CLM-09)."))
            if claim.time_barred and not claim.timebar_admitted_by_id:
                raise UserError(_("The notice is time-barred; the Operations Head must admit it (BR-CLM-11)."))
            if claim.cap_amount and amount > claim.cap_amount and not claim.cap_override_by_id:
                raise UserError(_("The amount exceeds the declared value or the carrier liability limit "
                                  "(%s); the Operations Head must approve with a reason (BR-CLM-10).", claim.cap_amount))
            claim.write({"approved_amount": amount, "state": "approved", "approved_by_id": self.env.user.id})
            claim._tms_create_credit_note()
            claim._tms_create_recovery()
        return True

    def action_reject(self):
        reason = self.env.context.get("tms_reason")
        if not reason:
            raise UserError(_("A rejection needs a reason."))
        self.write({"state": "rejected", "reject_reason": reason})
        return True

    def _tms_claim_account(self):
        self.ensure_one()
        cat = self.env.ref("tms_core.tms_category_damage")
        return cat.account_id or _default_account(self.env, self.company_id, "expense")

    def _tms_create_credit_note(self):
        """BR-CLM-05, BR-INV-10: a financial credit note without GST, linked to the LR."""
        self.ensure_one()
        lr = self.consignment_id
        move = self.env["account.move"].sudo().with_company(self.company_id).create({
            "move_type": "out_refund",
            "partner_id": (self.claimant_id or lr.billing_partner_id).id,
            "journal_id": self.company_id.tms_branch_journal("sale").id,
            "tms_credit_reason": "claim",
            "tms_claim_id": self.id,
            "ref": _("Claim %(c)s on LR %(lr)s", c=self.name, lr=lr.name),
            "invoice_line_ids": [(0, 0, {
                "name": _("Claim settlement %(c)s, LR %(lr)s", c=self.name, lr=lr.name),
                "quantity": 1,
                "price_unit": self.approved_amount,
                "account_id": self._tms_claim_account().id,
                "tax_ids": [(5, 0, 0)],
                "tms_consignment_id": lr.id,
                "tms_trip_id": self.trip_id.id,
                "tms_claim_id": self.id,
                "tms_line_kind": "claim",
                "tms_cost_class": "trip_direct",
                "vehicle_id": self.trip_id.primary_vehicle_id.id,
            })],
        })
        self.credit_note_id = move.id

    def _tms_create_recovery(self):
        """BR-CLM-06: the responsible party's share becomes a recovery charge."""
        self.ensure_one()
        if self.responsible == "company" or not self.recovery_amount:
            return
        cat = self.env.ref("tms_core.tms_category_%s_recovery" % (
            "shortage" if self.damage_type == "shortage" else "damage"))
        self.env["tms.trip.charge"].create({
            "trip_id": self.trip_id.id,
            "consignment_id": self.consignment_id.id,
            "category_id": cat.id,
            "amount": self.recovery_amount,
            "recover_from": self.responsible,
            "partner_id": self.responsible_partner_id.id or False,
            "driver_id": self.responsible_driver_id.id or False,
            "claim_id": self.id,
            "incident_id": self.incident_id.id,
            "note": _("Recovery of claim %s", self.name),
        }).action_confirm()

    def _tms_check_settled(self):
        """BR-CLM-08: Settled once the credit note and every recovery are posted."""
        for claim in self.filtered(lambda c: c.state == "approved"):
            if claim.credit_note_id.state != "posted":
                continue
            if all(c.state in ("billed", "deducted", "waived", "cancel") for c in claim.recovery_charge_ids):
                claim.state = "settled"

    def action_print_claim_note(self):
        return self.env.ref("tms_core.action_report_claim_note").report_action(self)


class TmsInsurerClaim(models.Model):
    """BR-CLM-07, BR-CLM-12: insurer claims tracked from filing to receipt."""

    _name = "tms.insurer.claim"
    _description = "Insurer claim"
    _inherit = ["mail.thread"]

    claim_id = fields.Many2one("tms.damage.claim", required=True, ondelete="cascade")
    company_id = fields.Many2one(related="claim_id.company_id", store=True)
    currency_id = fields.Many2one(related="claim_id.currency_id")
    policy_id = fields.Many2one("tms.insurance", required=True,
                                domain="[('tms_policy_type', 'in', ('goods_in_transit', 'carrier_liability'))]")
    reference = fields.Char()
    filed_on = fields.Date(default=fields.Date.context_today)
    amount_claimed = fields.Monetary()
    amount_received = fields.Monetary(tracking=True)
    received_on = fields.Date(tracking=True)
    state = fields.Selection(
        [("filed", "Filed"), ("survey", "Under survey"), ("received", "Received"), ("rejected", "Rejected")],
        default="filed",
        tracking=True,
    )

    @api.model_create_multi
    def create(self, vals_list):
        recs = super().create(vals_list)
        for rec in recs:
            if rec.claim_id.state not in ("approved", "settled"):
                raise UserError(_("An insurer claim is raised from an approved claim (BR-CLM-07)."))
        return recs
