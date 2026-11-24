"""Sales screens as drawn in the agreed mockup (TMS-UIG-001, 18.0.1.6.0): pipeline cards and list, the opportunity
(customer match, lanes, lost reasons, quotation from lanes), the freight quotation (rate approval and rejection,
acceptance, customer master, booked LRs, reverse charge) and the cost estimate (summary, item switcher, booking)."""

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import format_date

from odoo.addons.tms_core.models.tools import inr

STAGE_PROBABILITY = {
    "crm.stage_lead1": 10.0,
    "crm.stage_lead2": 40.0,
    "crm.stage_lead3": 60.0,
    "tms_estimate.crm_stage_negotiation": 80.0,
}


def _badge(text, tone):
    return Markup('<span class="badge rounded-pill text-bg-%s">%s</span>') % (tone, text)


def _city(partner):
    return partner.city or partner.name or ""


# ====================================================================== CRM
class CrmLead(models.Model):
    _inherit = "crm.lead"

    tms_lane_summary = fields.Char(compute="_compute_tms_lane_summary", string="Lanes")
    tms_lane_loads = fields.Float(compute="_compute_tms_lane_summary", string="Loads a month")
    tms_lane_formula = fields.Char(compute="_compute_tms_lane_summary")
    tms_order_id = fields.Many2one("sale.order", compute="_compute_tms_order", string="Quotation")
    tms_order_badge = fields.Char(compute="_compute_tms_order", string="Quotation status")
    tms_match_note = fields.Char(compute="_compute_tms_match_note", string="Customer match")

    @api.depends("tms_lane_ids.origin_id", "tms_lane_ids.destination_id", "tms_lane_ids.loads_per_month",
                 "tms_lane_ids.monthly_value")
    def _compute_tms_lane_summary(self):
        for lead in self:
            lanes = lead.tms_lane_ids
            lead.tms_lane_loads = sum(lanes.mapped("loads_per_month"))
            if not lanes:
                lead.tms_lane_summary = False
                lead.tms_lane_formula = False
                continue
            origins = list(dict.fromkeys(_city(lane.origin_id) for lane in lanes))
            dests = list(dict.fromkeys(_city(lane.destination_id) for lane in lanes))
            count = _("1 lane") if len(lanes) == 1 else _("%s lanes", len(lanes))
            route = "%s → %s" % (origins[0], ", ".join(dests)) if len(origins) == 1 else ", ".join(dests)
            lead.tms_lane_summary = "%s · %s" % (count, route)
            monthly = sum(lanes.mapped("monthly_value"))
            lead.tms_lane_formula = _("Expected revenue = Σ (loads a month × target rate) × 12 = %(m)s × 12 = "
                                      "%(y)s", m=inr(monthly), y=inr(monthly * 12))

    def _compute_tms_order(self):
        states = dict(self.env["sale.order"]._fields["state"]._description_selection(self.env))
        for lead in self:
            orders = lead.order_ids.filtered(lambda o: o.state != "cancel").sorted("id", reverse=True)
            order = orders[:1]
            lead.tms_order_id = order
            lead.tms_order_badge = "%s · %s" % (order.name, states.get(order.state, "")) if order else False

    @api.depends("partner_id", "tms_vat", "tms_pan")
    def _compute_tms_match_note(self):
        for lead in self:
            partner = lead.partner_id.commercial_partner_id
            vat, pan = lead._tms_lead_tax_ids() if lead.id or lead.tms_vat or lead.tms_pan else ("", "")
            if not partner or not (vat or pan):
                lead.tms_match_note = False
            elif vat and (partner.vat or "").upper() == vat:
                lead.tms_match_note = _("Existing customer · matched by GSTIN")
            elif pan and (partner.l10n_in_pan or "").upper() == pan:
                lead.tms_match_note = _("Existing customer · matched by PAN")
            else:
                lead.tms_match_note = False

    @api.model_create_multi
    def create(self, vals_list):
        leads = super().create(vals_list)
        for lead, vals in zip(leads, vals_list):
            if lead.tms_lane_ids:
                lead.expected_revenue = lead.tms_lane_revenue
            prob = "probability" not in vals and lead.type == "opportunity" and \
                self._tms_stage_probability(lead.stage_id)
            if prob is not False and prob is not None:
                lead.probability = prob
        return leads

    def _tms_stage_probability(self, stage):
        for xmlid, prob in STAGE_PROBABILITY.items():
            ref = self.env.ref(xmlid, raise_if_not_found=False)
            if ref and ref == stage:
                return prob
        return None

    def write(self, vals):
        """Probability follows the stage: New 10 %, Qualified 40 %, Quoted 60 %, Negotiation 80 %."""
        if vals.get("stage_id") and "probability" not in vals:
            prob = self._tms_stage_probability(self.env["crm.stage"].browse(vals["stage_id"]))
            if prob is not None:
                vals = dict(vals, probability=prob)
        return super().write(vals)

    def _prepare_opportunity_quotation_context(self):
        """The quotation starts with one movement item per enquired lane."""
        ctx = super()._prepare_opportunity_quotation_context()
        product = self.env.ref("tms_estimate.product_movement", raise_if_not_found=False)
        if product and self.tms_lane_ids:
            ctx["default_order_line"] = [(0, 0, {
                "product_id": product.id,
                "name": "%s → %s" % (lane.origin_id.name, lane.destination_id.name),
                "tms_is_movement": True,
                "tms_origin_id": lane.origin_id.id,
                "tms_destination_id": lane.destination_id.id,
                "tms_vehicle_class_id": lane.vehicle_class_id.id,
                "tms_cargo_type": lane.tms_cargo_type,
                "product_uom_qty": lane.loads_per_month or 1.0,
            }) for lane in self.tms_lane_ids]
        return ctx


class CrmLeadConvert(models.TransientModel):
    """BR-CRM-05: the conversion links the customer registered under the lead's GSTIN or PAN; no duplicate is
    created."""

    _inherit = "crm.lead2opportunity.partner"

    tms_match_partner_id = fields.Many2one("res.partner", compute="_compute_tms_match", string="Matched customer")
    tms_match_text = fields.Char(compute="_compute_tms_match")

    @api.depends("lead_id")
    def _compute_tms_match(self):
        for wiz in self:
            lead = wiz.lead_id
            match = lead._tms_match_partner_by_tax_id() if lead else self.env["res.partner"]
            wiz.tms_match_partner_id = match
            if not match:
                wiz.tms_match_text = False
                continue
            vat, pan = lead._tms_lead_tax_ids()
            if vat and (match.vat or "").upper() == vat:
                wiz.tms_match_text = _("GSTIN %(v)s belongs to %(p)s: the opportunity links to that customer and no "
                                       "duplicate is created.", v=vat, p=match.display_name)
            else:
                wiz.tms_match_text = _("The match ran on PAN %(pan)s: it belongs to %(p)s (GSTIN %(v)s). The "
                                       "opportunity links to that customer and no duplicate is created.",
                                       pan=pan, p=match.display_name, v=match.vat or "-")

    # "Create a new customer" on a matched lead still links the existing customer (CrmLead._handle_partner_assignment)


class CrmLeadLost(models.TransientModel):
    _inherit = "crm.lead.lost"

    def action_lost_reason_apply(self):
        if not self.lost_reason_id:
            raise UserError(_("An opportunity is marked lost only with its reason."))
        return super().action_lost_reason_apply()


# ====================================================================== quotation
class SaleOrder(models.Model):
    _inherit = "sale.order"

    tms_rate_approval = fields.Selection(
        [("none", "Not needed"), ("required", "Required"), ("pending", "Waiting for approval"),
         ("rejected", "Rejected"), ("approved", "Approved")],
        compute="_compute_tms_rate_approval", string="Rate approval")
    tms_approval_requested = fields.Boolean(copy=False, readonly=True)
    tms_approval_banner = fields.Char(compute="_compute_tms_rate_approval")
    tms_reject_reason = fields.Text(copy=False, readonly=True, string="Rejection reason")
    tms_rejected_by_id = fields.Many2one("res.users", copy=False, readonly=True)
    tms_master_status = fields.Char(compute="_compute_tms_master_status", string="Customer master")
    tms_master_ok = fields.Boolean(compute="_compute_tms_master_status")
    tms_master_summary = fields.Char(compute="_compute_tms_master_status")
    tms_freight_invoiced_total = fields.Monetary(compute="_compute_tms_booked", string="Freight invoiced")
    tms_booked_html = fields.Html(compute="_compute_tms_booked", sanitize=False, string="Booked on these rates")
    tms_rcm_note = fields.Char(compute="_compute_tms_rcm_note")
    tms_acceptance_from = fields.Char(string="Acceptance from", copy=False)
    tms_acceptance_date = fields.Date(string="Acceptance date", copy=False)
    tms_opportunity_stage = fields.Char(related="opportunity_id.stage_id.name", string="Opportunity stage")

    # ------------------------------------------------------------------ approval
    @api.depends("order_line.tms_approval_state", "tms_approval_requested", "order_line.tms_approved_by_id")
    def _compute_tms_rate_approval(self):
        for order in self:
            states = set(order.order_line.mapped("tms_approval_state")) - {False, "not_needed"}
            if "rejected" in states:
                order.tms_rate_approval = "rejected"
                order.tms_approval_banner = _("%(u)s rejected the rates: “%(r)s”. Change the rates and request "
                                              "approval again.", u=order.tms_rejected_by_id.name or _("The approver"),
                                              r=order.tms_reject_reason or "")
            elif "to_approve" in states:
                lines = order.order_line.filtered(lambda line: line.tms_approval_state == "to_approve")
                roles = set(lines.mapped("tms_approval_role"))
                who = _("the Operations Head") if "oh" in roles else _("the Branch Manager")
                if order.tms_approval_requested:
                    order.tms_rate_approval = "pending"
                    order.tms_approval_banner = _("Rates are with %(w)s and stay locked until approved or rejected. "
                                                  "Whoever built the estimate cannot approve it.", w=who)
                else:
                    order.tms_rate_approval = "required"
                    order.tms_approval_banner = _("Items %(i)s are below the markup floor or above the value limit: "
                                                  "request %(w)s's approval before sending.",
                                                  i=", ".join(str(n) for n in lines.mapped("tms_item_no")), w=who)
            elif "approved" in states:
                approvers = order.order_line.mapped("tms_approved_by_id")
                order.tms_rate_approval = "approved"
                order.tms_approval_banner = _("Rates approved by %s. The quotation can now be sent.",
                                              ", ".join(approvers.mapped("name")))
            else:
                order.tms_rate_approval = "none"
                order.tms_approval_banner = False

    def action_tms_request_approval(self):
        for order in self:
            order._tms_refresh_approvals()
            if not order.order_line.filtered(lambda line: line.tms_approval_state == "to_approve"):
                raise UserError(_("No item of %s needs approval.", order.name))
            order.tms_approval_requested = True
            order.message_post(body=_("Rate approval requested."))
        return True

    def _tms_refresh_approvals(self):
        for line in self.order_line.filtered("tms_estimate_id"):
            line.tms_estimate_id._tms_ensure_misc()
            line._tms_update_approval()

    def action_tms_approve_rates(self):
        for order in self:
            lines = order.order_line.filtered(lambda line: line.tms_approval_state == "to_approve")
            if not lines:
                raise UserError(_("No rate of %s waits for approval.", order.name))
            lines.action_tms_approve()
            order.message_post(body=_("Rates approved by %s.", self.env.user.name))
        return True

    def action_tms_reject_rates(self):
        return self.env["tms.reason.wizard"].open_for(self, "action_tms_reject_with_reason", _("Reject the rates"))

    def action_tms_reject_with_reason(self):
        reason = self.env.context.get("tms_reason")
        if not reason:
            raise UserError(_("Give the reason for the rejection."))
        for order in self:
            lines = order.order_line.filtered(lambda line: line.tms_approval_state == "to_approve")
            if not lines:
                raise UserError(_("No rate of %s waits for approval.", order.name))
            for line in lines:
                group = "tms_core.group_branch_manager" if line.tms_approval_role == "bm" \
                    else "tms_core.group_operations_head"
                if not self.env.user.has_group(group):
                    raise UserError(_("Only the approver may reject these rates."))
            lines.write({"tms_approval_state": "rejected", "tms_rejected_amount": 0.0})
            for line in lines:
                line.tms_rejected_amount = line.tms_estimate_id.amount
            order.write({"tms_reject_reason": reason, "tms_rejected_by_id": self.env.user.id,
                         "tms_approval_requested": False})
            order.message_post(body=_("Rates rejected by %(u)s: %(r)s", u=self.env.user.name, r=reason))
            self.env["tms.override.log"].log("rate_rejected", order, _("Rates of %s rejected", order.name),
                                              reason=reason)
        return True

    # ------------------------------------------------------------------ customer master
    def _tms_customer_master_missing(self):
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
        return missing

    @api.depends("partner_id")
    def _compute_tms_master_status(self):
        for order in self:
            if not order.partner_id:
                order.tms_master_status = order.tms_master_summary = False
                order.tms_master_ok = False
                continue
            partner = order.partner_id.commercial_partner_id
            missing = order._tms_customer_master_missing()
            order.tms_master_ok = not missing
            order.tms_master_status = _("Complete") if not missing else _("Missing: %s", ", ".join(missing))
            basis = dict(partner._fields["tms_freight_basis"]._description_selection(self.env)).get(
                partner.tms_freight_basis, "")
            cycle = dict(partner._fields["tms_billing_cycle"]._description_selection(self.env)).get(
                partner.tms_billing_cycle, "")
            parts = [p for p in (_("GSTIN") if partner.vat else "", _("PAN") if partner.l10n_in_pan else "",
                                 basis, cycle.lower(),
                                 _("%s limit", inr(partner.tms_credit_limit).split(".")[0])
                                 if partner.tms_credit_limit else "") if p]
            order.tms_master_summary = " · ".join(parts)

    def _tms_check_customer_master(self):
        """BR-QTN-07."""
        self.ensure_one()
        missing = self._tms_customer_master_missing()
        if missing:
            raise UserError(_("Customer %(c)s lacks %(m)s.", c=self.partner_id.commercial_partner_id.display_name,
                              m=", ".join(missing)))

    # ------------------------------------------------------------------ booked LRs, reverse charge
    def _compute_tms_booked(self):
        for order in self:
            lines = order.order_line.filtered(lambda line: line.tms_lr_ids)
            order.tms_freight_invoiced_total = sum(order.order_line.mapped("tms_freight_invoiced"))
            if not lines:
                order.tms_booked_html = False
                continue
            rows = []
            tot = [0, 0.0, 0.0, 0.0]
            for line in lines:
                lrs = line.tms_lr_ids.filtered(lambda lr: lr.state != "cancel")
                freight = sum(lrs.mapped("freight_amount"))
                invoiced = sum(lrs.mapped("freight_invoiced"))
                open_lrs = lrs.filtered(lambda lr: lr.freight_uninvoiced > 0.005)
                pending = ", ".join(open_lrs.mapped("name")) or "—"
                rows.append(Markup("<tr><td>%s</td><td class='text-end'>%s</td><td class='text-end'>%s kg</td>"
                                   "<td class='text-end'>%s</td><td class='text-end'>%s</td><td>%s</td></tr>") % (
                    line.tms_lane_display or line.name, len(lrs), "{:,.0f}".format(sum(lrs.mapped("charged_weight"))),
                    inr(freight), inr(invoiced), pending))
                tot[0] += len(lrs)
                tot[1] += sum(lrs.mapped("charged_weight"))
                tot[2] += freight
                tot[3] += invoiced
            rows.append(Markup("<tr class='fw-bold'><td>%s</td><td class='text-end'>%s</td><td class='text-end'>%s kg"
                               "</td><td class='text-end'>%s</td><td class='text-end'>%s</td><td></td></tr>") % (
                _("Total"), tot[0], "{:,.0f}".format(tot[1]), inr(tot[2]), inr(tot[3])))
            head = Markup("<thead><tr><th>%s</th><th class='text-end'>%s</th><th class='text-end'>%s</th>"
                          "<th class='text-end'>%s</th><th class='text-end'>%s</th><th>%s</th></tr></thead>") % (
                _("Lane"), _("LRs"), _("Charged weight"), _("Freight on LRs"), _("Invoiced"), _("Not yet invoiced"))
            order.tms_booked_html = Markup("<table class='table table-sm'>%s<tbody>%s</tbody></table>") % (
                head, Markup("").join(rows))

    @api.depends("company_id", "partner_id", "amount_untaxed", "date_order")
    def _compute_tms_rcm_note(self):
        for order in self:
            if not order.order_line.filtered("tms_is_movement") or not order.company_id:
                order.tms_rcm_note = False
                continue
            date = fields.Date.to_date(order.date_order) if order.date_order else fields.Date.today()
            option = order.company_id.tms_get_gta_option(date)
            partner = order.partner_id.commercial_partner_id
            if option == "rcm" and partner.vat and not partner.tms_unregistered:
                order.tms_rcm_note = _("Tax payable on reverse charge: Yes · GST 5%% (GTA) = %s payable by the "
                                       "recipient", inr(order.amount_untaxed * 0.05))
            else:
                order.tms_rcm_note = False

    # ------------------------------------------------------------------ confirmation with acceptance
    def _tms_needs_acceptance(self):
        self.ensure_one()
        return bool(self.order_line.filtered(lambda line: line.tms_estimate_id or line.tms_is_movement)) \
            and not self._tms_is_contract_order() and not self.signature and not self.tms_acceptance_file

    def action_tms_confirm(self):
        """Confirm, asking first for the customer's written acceptance when there is no portal signature."""
        self.ensure_one()
        if self._tms_needs_acceptance():
            return {"type": "ir.actions.act_window", "name": _("Customer acceptance"),
                    "res_model": "tms.quotation.acceptance.wizard", "view_mode": "form", "target": "new",
                    "context": {"default_order_id": self.id}}
        return self.action_confirm()

    def action_tms_view_opportunity(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "res_model": "crm.lead", "res_id": self.opportunity_id.id,
                "view_mode": "form"}


class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    tms_approval_state = fields.Selection(selection_add=[("rejected", "Rejected")],
                                          ondelete={"rejected": "set default"})
    tms_rejected_amount = fields.Monetary(readonly=True, copy=False, currency_field="currency_id")
    tms_lane_display = fields.Char(compute="_compute_tms_lane_display", string="Lane")

    @api.depends("tms_origin_id", "tms_destination_id", "tms_vehicle_class_id", "tms_cargo_type")
    def _compute_tms_lane_display(self):
        cargo = dict(self._fields["tms_cargo_type"]._description_selection(self.env))
        for line in self:
            if not (line.tms_origin_id or line.tms_destination_id):
                line.tms_lane_display = False
                continue
            parts = ["%s → %s" % (_city(line.tms_origin_id), _city(line.tms_destination_id))]
            if line.tms_cargo_type:
                parts.append(cargo.get(line.tms_cargo_type, ""))
            if line.tms_vehicle_class_id:
                parts.append(line.tms_vehicle_class_id.name)
            line.tms_lane_display = " · ".join(parts)

    def _tms_update_approval(self):
        """A rejected rate stays rejected until its amount changes; a rate that newly needs approval needs a new
        request."""
        keep = self.filtered(lambda line: line.tms_approval_state == "rejected" and line.tms_estimate_id
                             and abs(line.tms_rejected_amount - line.tms_estimate_id.amount) < 0.005)
        rest = self - keep
        before = {line.id: line.tms_approval_state for line in rest}
        res = super(SaleOrderLine, rest)._tms_update_approval()
        renewed = rest.filtered(lambda line: line.tms_approval_state == "to_approve"
                                and before.get(line.id) != "to_approve")
        renewed.mapped("order_id").write({"tms_approval_requested": False})
        return res


class SaleOrderNeedsApproval(models.Model):
    _inherit = "sale.order"

    @api.depends("order_line.tms_approval_state")
    def _compute_tms_needs_approval(self):
        for order in self:
            order.tms_needs_approval = any(line.tms_approval_state in ("to_approve", "rejected")
                                           for line in order.order_line)


class TmsQuotationAcceptanceWizard(models.TransientModel):
    """BR-QTN-06: no portal signature, so the customer's written acceptance is attached before confirming."""

    _name = "tms.quotation.acceptance.wizard"
    _description = "Customer acceptance"

    order_id = fields.Many2one("sale.order", required=True)
    file = fields.Binary(string="Written acceptance", required=True)
    file_name = fields.Char()
    accepted_by = fields.Char(string="From", help="Who accepted, e.g. the customer's purchase manager.")
    accepted_on = fields.Date(string="Date", default=fields.Date.context_today, required=True)
    master_status = fields.Char(related="order_id.tms_master_status")
    master_ok = fields.Boolean(related="order_id.tms_master_ok")

    def action_attach_confirm(self):
        self.ensure_one()
        self.order_id.write({"tms_acceptance_file": self.file, "tms_acceptance_name": self.file_name or "acceptance",
                             "tms_acceptance_from": self.accepted_by, "tms_acceptance_date": self.accepted_on})
        return self.order_id.action_confirm()


# ====================================================================== cost estimate
class TmsEstimate(models.Model):
    _inherit = "tms.estimate"

    locked_on = fields.Datetime(readonly=True, copy=False, string="Locked on")
    summary_html = fields.Html(compute="_compute_summary_html", sanitize=False)
    sibling_html = fields.Html(compute="_compute_summary_html", sanitize=False)
    subtitle = fields.Char(compute="_compute_summary_html")
    order_state = fields.Selection(related="order_id.state", string="Order status")
    document_line_count = fields.Integer(compute="_compute_estimate_counts", string="Bill & expense lines")
    purchase_count = fields.Integer(compute="_compute_estimate_counts", string="Purchase orders")

    def action_lock(self):
        res = super().action_lock()
        self.filtered(lambda e: not e.locked_on).write({"locked_on": fields.Datetime.now()})
        return res

    def _compute_estimate_counts(self):
        for est in self:
            lines = est.sudo().line_ids
            est.document_line_count = len(lines.mapped("bill_line_ids")) + len(lines.mapped("expense_ids"))
            est.purchase_count = len(lines.mapped("po_line_ids.order_id"))

    def _compute_summary_html(self):
        for est in self:
            line = est.sale_line_id
            sub = [(line.name or "").split("\n")[0], est.partner_id.name or "",
                   "%s → %s" % (est.origin_id.name or "", est.destination_id.name or "")]
            if est.locked_on:
                sub.append(_("Estimate locked %s", format_date(self.env, est.locked_on, date_format="dd/MM/yyyy")))
            est.subtitle = " · ".join(p for p in sub if p)
            days = est.days or 0.0
            tiles = [
                (_("Total distance"), "%s km" % "{:,.0f}".format(est.distance_km or 0.0),
                 (_("1 state") if est.state_count == 1 else _("%s states", est.state_count)) if est.state_count else ""),
                (_("Estimated cost"), inr(est.estimated_cost),
                 _("%(d)s days at %(k)s km a day", d="{:g}".format(days), k="{:g}".format(est.km_per_day or 0.0))),
                (_("Marked-up total · markup"), inr(est.marked_up_total), _("markup %.1f%% on cost", est.markup_pct)),
                (_("Rate per km · to the quotation"), _("%s / km", inr(est.rate_per_km)),
                 _("item amount %(a)s · rounding %(r)s", a=inr(est.amount), r=inr(est.rounding_difference))),
                (_("Actual cost · variance"), inr(est.actual),
                 _("%s against the estimate", inr(est.variance)) if est.actual else _("nothing booked yet")),
            ]
            over = est.actual and est.variance > 0.005
            cells = []
            for index, (label, value, note) in enumerate(tiles):
                tone = "text-danger" if over and index == len(tiles) - 1 else ""
                cells.append(Markup('<div class="col border-end p-2"><div class="text-muted small">%s</div>'
                                    '<div class="fs-5 %s">%s</div><div class="text-muted small">%s</div></div>') % (
                    label, tone, value, note))
            est.summary_html = Markup('<div class="row g-0 border rounded mb-3">%s</div>') % Markup("").join(cells)
            siblings = self.search([("order_id", "=", est.order_id.id)], order="name") if est.order_id else est
            chips = []
            for sib in siblings:
                text = "%s %s" % (sib.sale_line_id.tms_item_no or "", (sib.sale_line_id.name or "").split("\n")[0][:40])
                if sib == est:
                    chips.append(Markup('<span class="badge rounded-pill text-bg-primary me-1">%s</span>') % text)
                else:
                    chips.append(Markup('<a class="badge rounded-pill text-bg-light border me-1" '
                                        'href="/odoo/action-tms_estimate.action_tms_estimate/%s">%s</a>') % (
                        sib.id, text))
            est.sibling_html = Markup("").join(chips) if len(siblings) > 1 else False

    def action_tms_view_documents(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "name": _("Bill and expense lines"),
                "res_model": "account.move.line", "view_mode": "list",
                "domain": [("tms_estimate_line_id", "in", self.line_ids.ids)]}

    def action_tms_view_purchases(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "name": _("Purchase orders"), "res_model": "purchase.order",
                "view_mode": "list,form", "domain": [("id", "in", self.line_ids.mapped("po_line_ids.order_id").ids)]}

    def action_tms_view_so_pnl(self):
        self.ensure_one()
        action = self.env["ir.actions.actions"]._for_xml_id("tms_estimate.action_tms_so_pnl")
        action["domain"] = [("order_id", "=", self.order_id.id)]
        return action

    def action_tms_print_cost_sheet(self):
        self.ensure_one()
        report = self.env.ref("tms_estimate.action_report_cost_sheet", raise_if_not_found=False)
        if not report:
            raise UserError(_("The customer cost sheet report is not installed."))
        return report.report_action(self)

    def action_tms_book_cost(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "name": _("Book a cost to an estimate line"),
                "res_model": "tms.estimate.book.wizard", "view_mode": "form", "target": "new",
                "context": {"default_estimate_id": self.id}}


class TmsEstimateLine(models.Model):
    _inherit = "tms.estimate.line"

    remaining = fields.Monetary(compute="_compute_remaining", string="Left before the estimate")
    booked_html = fields.Html(compute="_compute_booked_html", sanitize=False)

    def _compute_remaining(self):
        for line in self:
            line.remaining = line.estimated_cost - line.actual - line.committed

    @api.depends_context("tms_show_remaining")
    def _compute_display_name(self):
        super()._compute_display_name()
        if self.env.context.get("tms_show_remaining"):
            for line in self:
                left = line.estimated_cost - line.actual - line.committed
                note = _("%s left", inr(left)) if left >= 0 else _("%s over", inr(-left))
                line.display_name = "%s · %s" % (line.display_name, note)

    def _compute_booked_html(self):
        for line in self:
            rows = []
            src = line.sudo()
            for bl in src.bill_line_ids.filtered(lambda x: x.display_type == "product"):
                rows.append((bl.move_id.name or _("Draft bill"), bl.move_id.invoice_date or bl.date,
                             bl.move_id.partner_id.name or "", bl.purchase_line_id.order_id.name or "—",
                             bl.balance, bl.move_id.state))
            for exp in src.expense_ids.filtered(lambda e: e.state != "cancel"):
                rows.append((exp.name, exp.date, exp.paid_to_id.name or "", "—", exp.amount, exp.state))
            for pol in src.po_line_ids.filtered(lambda p: p.order_id.state != "cancel"):
                rows.append((pol.order_id.name, fields.Date.to_date(pol.order_id.date_order),
                             pol.order_id.partner_id.name or "", "%s/%s" % (pol.order_id.name, pol.sequence),
                             pol.price_subtotal, pol.order_id.state))
            if not rows:
                line.booked_html = Markup('<p class="text-muted">%s</p>') % _(
                    "Nothing is booked to this line yet. Costs are booked from a purchase order item that names the "
                    "line, or by picking the line on a bill or expense.")
                continue
            body = Markup("").join(
                Markup("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td class='text-end'>%s</td><td>%s</td></tr>")
                % (r[0], format_date(self.env, r[1], date_format="dd/MM/yyyy") if r[1] else "", r[2], r[3],
                   inr(r[4]), r[5]) for r in rows)
            total = sum(r[4] for r in rows)
            line.booked_html = Markup(
                "<table class='table table-sm'><thead><tr><th>%s</th><th>%s</th><th>%s</th><th>%s</th>"
                "<th class='text-end'>%s</th><th>%s</th></tr></thead><tbody>%s</tbody></table>"
                "<div class='small text-muted'>%s</div>") % (
                _("Document"), _("Date"), _("Partner"), _("PO item"), _("Amount"), _("Status"), body,
                _("%(n)s documents · estimate %(e)s · booked %(b)s", n=len(rows), e=inr(line.estimated_cost),
                  b=inr(total)))

    def action_tms_open_booked(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "name": _("Booked to · %s", self.name or self.product_id.name),
                "res_model": self._name, "res_id": self.id, "view_mode": "form", "target": "new",
                "views": [(self.env.ref("tms_estimate.tms_estimate_line_booked_form").id, "form")]}


class TmsEstimateBookWizard(models.TransientModel):
    """Book a draft vendor bill line or a trip expense to an estimate line, seeing what the line has left."""

    _name = "tms.estimate.book.wizard"
    _description = "Book a cost to an estimate line"

    estimate_id = fields.Many2one("tms.estimate")
    company_id = fields.Many2one(related="estimate_id.company_id")
    currency_id = fields.Many2one(related="estimate_id.currency_id")
    source = fields.Selection([("bill", "Vendor bill line"), ("expense", "Trip expense")], default="bill",
                              required=True)
    move_line_id = fields.Many2one("account.move.line", string="Bill line",
                                   domain="[('move_id.move_type', '=', 'in_invoice'), ('parent_state', '=', 'draft'), "
                                          "('display_type', '=', 'product'), ('tms_estimate_line_id', '=', False)]")
    expense_id = fields.Many2one("tms.trip.expense", string="Expense",
                                 domain="[('state', '!=', 'cancel'), ('estimate_line_id', '=', False)]")
    amount = fields.Monetary(compute="_compute_amount")
    estimate_line_id = fields.Many2one("tms.estimate.line", string="Estimate line", required=True,
                                       domain="[('order_state', '=', 'sale'), ('company_id', '=', company_id)]",
                                       context={"tms_show_remaining": True})
    warning = fields.Char(compute="_compute_warning")

    @api.depends("move_line_id", "expense_id", "source")
    def _compute_amount(self):
        for wiz in self:
            wiz.amount = wiz.move_line_id.balance if wiz.source == "bill" else wiz.expense_id.amount

    @api.depends("estimate_line_id", "amount")
    def _compute_warning(self):
        for wiz in self:
            line = wiz.estimate_line_id
            if not line:
                wiz.warning = False
                continue
            after = line.actual + line.committed + wiz.amount
            if after > line.estimated_cost + 0.005:
                over = after - line.estimated_cost
                tol = line.company_id.tms_estimate_variance_tolerance
                beyond = not line.estimated_cost or over / line.estimated_cost * 100.0 > (tol or 0.0)
                wiz.warning = _("Booking %(a)s here takes the line %(o)s over its estimate of %(e)s%(t)s",
                                a=inr(wiz.amount), o=inr(over), e=inr(line.estimated_cost),
                                t=_(", beyond the variance tolerance: the document waits for the Branch Manager "
                                    "before it posts.") if beyond else ".")
            else:
                wiz.warning = False

    def action_book(self):
        self.ensure_one()
        if self.source == "bill":
            if not self.move_line_id:
                raise UserError(_("Pick the bill line."))
            self.move_line_id.tms_estimate_line_id = self.estimate_line_id
            doc = self.move_line_id.move_id.display_name
        else:
            if not self.expense_id:
                raise UserError(_("Pick the expense."))
            self.expense_id.estimate_line_id = self.estimate_line_id
            doc = self.expense_id.name
        self.estimate_line_id.estimate_id.message_post(body=_("%(d)s booked to %(l)s (%(a)s).", d=doc,
                                                              l=self.estimate_line_id.display_name, a=inr(self.amount)))
        return {"type": "ir.actions.client", "tag": "display_notification",
                "params": {"title": _("Booked to %s", self.estimate_line_id.display_name),
                           "message": self.warning or _("%s booked.", doc), "type": "warning" if self.warning
                           else "success", "next": {"type": "ir.actions.act_window_close"}}}
