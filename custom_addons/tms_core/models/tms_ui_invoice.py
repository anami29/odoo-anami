"""Freight invoice and vendor bill as drawn in the agreed mockup (TMS-UIG-001, 18.0.1.6.0): smart buttons, billing
basis, unbilled-charge guard, reverse-charge note, TDS status on draft bills, the POD element and row action of the
three-way match, and match messages on the bill."""

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import format_datetime

from .tools import inr


class AccountMove(models.Model):
    _inherit = "account.move"

    tms_inv_lr_ids = fields.Many2many("tms.consignment", compute="_compute_tms_invoice_ui", string="LRs billed")
    tms_inv_lr_count = fields.Integer(compute="_compute_tms_invoice_ui", string="Consignments")
    tms_unbilled_charge_ids = fields.Many2many("tms.trip.charge", compute="_compute_tms_invoice_ui",
                                               string="Approved charges not on an invoice")
    tms_unbilled_charge_count = fields.Integer(compute="_compute_tms_invoice_ui", string="Unbilled trip charges")
    tms_unbilled_note = fields.Char(compute="_compute_tms_invoice_ui")
    tms_billing_basis = fields.Char(compute="_compute_tms_invoice_ui", string="Billing basis")
    tms_rate_line = fields.Char(compute="_compute_tms_invoice_ui", string="Rate")
    tms_rcm_note = fields.Char(compute="_compute_tms_invoice_ui")
    tms_tds_note = fields.Char(compute="_compute_tms_bill_ui", string="TDS")
    tms_pod_element = fields.Html(compute="_compute_tms_bill_ui", sanitize=False)
    tms_ui_claim_ids = fields.Many2many("tms.damage.claim", compute="_compute_tms_bill_ui")
    tms_ui_claim_count = fields.Integer(compute="_compute_tms_bill_ui", string="Recovery")

    # ------------------------------------------------------------------ customer invoice
    @api.depends("move_type", "invoice_line_ids.tms_consignment_id", "invoice_line_ids.tms_trip_id",
                 "invoice_line_ids.tms_charge_id", "partner_id", "amount_untaxed", "tms_gst_mode")
    def _compute_tms_invoice_ui(self):
        Charge = self.env["tms.trip.charge"].sudo()
        for move in self:
            out = move.move_type in ("out_invoice", "out_refund")
            lrs = move.invoice_line_ids.mapped("tms_consignment_id") if out else self.env["tms.consignment"]
            trips = (move.invoice_line_ids.mapped("tms_trip_id") | lrs.mapped("trip_ids")) if out else \
                self.env["tms.order"]
            move.tms_inv_lr_ids = lrs
            move.tms_inv_lr_count = len(lrs)
            charges = Charge.browse()
            if out and move.move_type == "out_invoice" and (trips or lrs):
                partner = move.partner_id.commercial_partner_id
                charges = Charge.search([
                    "|", ("trip_id", "in", trips.ids), ("consignment_id", "in", lrs.ids),
                    ("recover_from", "=", "customer"), ("state", "=", "approved"), ("move_line_id", "=", False),
                    ("partner_id", "child_of", partner.id)]) if partner else Charge.browse()
            move.tms_unbilled_charge_ids = charges
            move.tms_unbilled_charge_count = len(charges)
            if charges:
                move.tms_unbilled_note = _(
                    "%(t)s has %(n)s approved customer charges (%(a)s) not on any invoice: %(l)s.",
                    t=", ".join(trips.mapped("name")) or _("The trip"), n=len(charges), a=inr(sum(charges.mapped("amount"))),
                    l=", ".join("%s %s %s" % (c.name, c.category_id.name.lower(), inr(c.amount)) for c in charges))
            else:
                move.tms_unbilled_note = False
            move.tms_billing_basis = _("Billing party on LR %s", ", ".join(lrs.mapped("name"))) if lrs else False
            rates = []
            for lr in lrs[:3]:
                rates.append(lr._tms_rate_label())
            move.tms_rate_line = " · ".join(r for r in rates if r) or False
            if out and lrs and move.tms_gst_mode == "rcm":
                move.tms_rcm_note = _("Tax payable on reverse charge: Yes · GST 5%% (GTA) = %s payable by the "
                                      "recipient", inr(move.amount_untaxed * 0.05))
            else:
                move.tms_rcm_note = False

    def action_tms_add_unbilled_charges(self):
        """Add the trip's approved customer charges to this draft freight invoice."""
        Wizard = self.env["tms.invoice.lr.wizard"]
        for move in self:
            if move.state != "draft" or move.move_type != "out_invoice":
                raise UserError(_("Charges are added to a draft customer invoice."))
            charges = move.tms_unbilled_charge_ids
            if not charges:
                raise UserError(_("No approved charge of these trips is waiting to be billed."))
            mode = move.tms_gst_mode or "rcm"
            vals = [(0, 0, Wizard._tms_charge_line_vals(charge, mode)) for charge in charges]
            Wizard._tms_map_line_taxes(vals, move.fiscal_position_id)
            move.write({"invoice_line_ids": vals})
            for line in move.invoice_line_ids.filtered(lambda line: line.tms_charge_id in charges):
                line.tms_charge_id.sudo().move_line_id = line
            move.message_post(body=_("Approved trip charges added: %s.", ", ".join(charges.mapped("name"))))
        return True

    def action_tms_view_lrs(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "name": _("Consignments"), "res_model": "tms.consignment",
                "view_mode": "list,form", "domain": [("id", "in", self.tms_inv_lr_ids.ids)]}

    def action_tms_view_unbilled_charges(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "name": _("Trip charges"), "res_model": "tms.trip.charge",
                "view_mode": "list,form", "domain": [("id", "in", self.tms_unbilled_charge_ids.ids)]}

    def _tms_ui_trips(self):
        trips = super()._tms_ui_trips()
        if self.move_type in ("out_invoice", "out_refund"):
            trips |= self.invoice_line_ids.mapped("tms_trip_id") | self.invoice_line_ids.mapped(
                "tms_consignment_id.trip_ids")
        return trips

    # ------------------------------------------------------------------ vendor bill
    @api.depends("move_type", "partner_id", "invoice_date", "tms_match_ids.pod_ok", "tms_match_ids.state")
    def _compute_tms_bill_ui(self):
        for move in self:
            bill = move.move_type in ("in_invoice", "in_refund")
            memos = move.tms_hire_memo_ids if bill else self.env["purchase.order"]
            if bill and memos and move.partner_id:
                vendor = move.partner_id.commercial_partner_id
                date = move.invoice_date or fields.Date.context_today(move)
                move.tms_tds_note = _("Nil · transporter declaration on file") \
                    if vendor.tms_has_valid_tds_declaration(date) else _("Deducted at posting under the vendor's section")
            else:
                move.tms_tds_note = False
            charges = move.invoice_line_ids.mapped("tms_charge_id") if bill else self.env["tms.trip.charge"]
            claims = charges.mapped("claim_id")
            move.tms_ui_claim_ids = claims
            move.tms_ui_claim_count = len(claims)
            if not (bill and move.tms_match_ids):
                move.tms_pod_element = False
                continue
            links = self.env["tms.trip.consignment"].sudo()
            for match in move.tms_match_ids:
                found = links.search([("assignment_id.hire_memo_id", "=", match.purchase_id.id),
                                      ("state", "=", "active")])
                if not found and match.purchase_id.tms_trip_id:
                    found = match.purchase_id.tms_trip_id.consignment_link_ids.filtered(lambda x: x.state == "active")
                links |= found
            verified = links.filtered(lambda x: x.pod_status == "verified")
            ok = bool(links) and verified == links
            last = verified.sorted("verified_on")[-1:]
            detail = _("verified · %(u)s, %(d)s", u=last.verified_by_id.name or "",
                       d=format_datetime(self.env, last.verified_on, dt_format="dd/MM HH:mm")) if ok else \
                _("%(v)s of %(n)s verified", v=len(verified), n=len(links))
            files = len(links.filtered("pod_file"))
            badge = Markup('<span class="badge rounded-pill text-bg-%s">%s</span>') % (
                "success" if ok else "danger", _("Verified") if ok else _("Not verified"))
            move.tms_pod_element = Markup(
                '<table class="table table-sm mb-2"><tbody><tr><td class="fw-bold">%s</td><td>%s</td>'
                '<td class="text-muted">%s</td><td class="text-end">%s</td></tr></tbody></table>') % (
                _("Verified digital POD"), ", ".join(links.mapped("trip_id.name")) + (
                    _(" · %s files", files) if files else ""), detail, badge)

    def action_tms_view_claims(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "name": _("Recovery"), "res_model": "tms.damage.claim",
                "view_mode": "list,form", "domain": [("id", "in", self.tms_ui_claim_ids.ids)]}


class TmsVendorBillMatchLine(models.Model):
    _inherit = "tms.vendor.bill.match.line"

    result_label = fields.Char(compute="_compute_result_label", string="Check")

    @api.depends("kind", "result", "billed", "approved")
    def _compute_result_label(self):
        for line in self:
            if line.kind in ("recovery", "replacement"):
                line.result_label = _("Deducted") if line.billed else _("To deduct")
            elif line.result == "pass":
                line.result_label = _("Matched") if line.kind == "hire" else _("Within tolerance")
            else:
                line.result_label = _("Over tolerance") if line.billed > line.approved else _("Under tolerance")

    def action_use_approved(self):
        """Bring the billed amount of an element down (or up) to its approved amount."""
        for mline in self:
            match, label, approved = mline.match_id, mline.label, mline.approved
            bill = match.move_id
            if bill.state != "draft":
                raise UserError(_("Only a draft bill is corrected."))
            lines = bill.invoice_line_ids.filtered(lambda line, p=mline.purchase_line_id: p and line.purchase_line_id == p)
            if not lines:
                raise UserError(_("This element has no line on the bill to correct."))
            first = lines[0]
            rest = sum((lines - first).mapped("price_subtotal"))
            qty = first.quantity or 1.0
            first.price_unit = (approved - rest) / qty
            bill.message_post(body=_("%(l)s set to the approved %(a)s.", l=label, a=inr(approved)))
            if match.exists():
                match._tms_refresh()
        return True


class TmsVendorBillMatch(models.Model):
    _inherit = "tms.vendor.bill.match"

    def _tms_refresh(self):
        before = {m.id: m.state for m in self}
        res = super()._tms_refresh()
        for match in self:
            if before.get(match.id) != match.state and match.move_id:
                problems = [l.label for l in match.line_ids.filtered(lambda line: line.result != "pass")]
                body = _("Three-way match %(m)s: %(s)s.", m=match.purchase_id.name,
                         s=dict(match._fields["state"]._description_selection(self.env))[match.state])
                if not match.pod_ok:
                    body += " " + _("POD not verified.")
                if problems:
                    body += " " + _("Outside tolerance: %s.", ", ".join(problems))
                match.move_id.sudo().message_post(body=body)
        return res
