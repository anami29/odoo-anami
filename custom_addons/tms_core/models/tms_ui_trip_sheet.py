"""Trip sheet as drawn in the agreed mockup (TMS-UIG-001, 18.0.1.6.0): Route & schedule lines, the compliance
gatekeeper table of the current assignment and the Financials ledger."""

from datetime import timedelta

from markupsafe import Markup, escape

from odoo import _, api, fields, models
from odoo.tools import format_date

from .tms_hire_memo import HIRE_ELEMENTS
from .tms_ui_trip import _dt
from .tools import inr


def _hours(value):
    """36.5 -> "36:30"."""
    if value is None:
        return ""
    sign = "−" if value < 0 else ""
    minutes = int(round(abs(value) * 60))
    return "%s%d:%02d" % (sign, minutes // 60, minutes % 60)


def _badge(text, tone):
    return Markup('<span class="badge rounded-pill text-bg-%s">%s</span>') % (tone, text)


class TmsTripConsignment(models.Model):
    _inherit = "tms.trip.consignment"

    lr_actual_weight = fields.Float(related="consignment_id.total_actual_weight", string="Actual wt (kg)")
    lr_charged_weight = fields.Float(related="consignment_id.charged_weight", string="Charged wt (kg)")
    lr_ewaybill_coverage = fields.Char(related="consignment_id.ewaybill_coverage", string="E-way bills")


class TmsOrder(models.Model):
    _inherit = "tms.order"

    schedule_display = fields.Char(compute="_compute_schedule_display", string="Scheduled")
    actual_display = fields.Char(compute="_compute_schedule_display", string="Actual")
    route_detail = fields.Char(compute="_compute_schedule_display")
    gate_table = fields.Html(compute="_compute_gate_table", sanitize=False, string="Compliance gatekeeper")
    ledger_html = fields.Html(compute="_compute_ledger_html", sanitize=False, string="Financials")

    @api.depends("scheduled_date_start", "scheduled_date_end", "scheduled_duration", "date_start", "date_end",
                 "duration", "origin_id", "destination_id", "route_id")
    def _compute_schedule_display(self):
        for trip in self:
            if trip.scheduled_date_start:
                trip.schedule_display = "%s → %s  ·  %s h" % (
                    _dt(self.env, trip.scheduled_date_start), _dt(self.env, trip.scheduled_date_end),
                    _hours(trip.scheduled_duration))
            else:
                trip.schedule_display = False
            if trip.date_start:
                trip.actual_display = "%s → %s  ·  %s h" % (
                    _dt(self.env, trip.date_start), _dt(self.env, trip.date_end) or _("on the road"),
                    _hours(trip.duration))
            else:
                trip.actual_display = False
            detail = []
            km = trip._tms_route_km()
            if km:
                detail.append(_("%s km", "{:,.0f}".format(km)))
            stops = trip.consignment_link_ids.filtered(lambda x: x.state != "cancel").mapped(
                "consignment_id.consignee_id.city")
            stops = [s for s in dict.fromkeys(stops) if s]
            if stops:
                detail.append(_("stops: %s", ", ".join(stops)))
            trip.route_detail = " · ".join(detail) or False

    def _tms_route_km(self):
        """Planned distance of the trip: the route's, else the km run so far (tms_estimate adds the estimate's)."""
        self.ensure_one()
        if self.route_id and "distance" in self.route_id._fields and self.route_id.distance:
            return self.route_id.distance
        return self._tms_planned_km() or self.own_km or 0.0

    def action_print_dsr(self):
        """The DSR of the selected trips, or of every trip on the road when none is selected."""
        trips = self or self.search([("state_code", "in", ("started", "in_transit"))])
        return self.env.ref("tms_core.action_report_dsr").report_action(trips)

    # ------------------------------------------------------------------ compliance gatekeeper
    def _compute_gate_table(self):
        types = {t.code: t.name for t in self.env["tms.compliance.document.type"].sudo().search([])}
        names = dict(DL=_("Driving Licence (DL)"))
        icp = self.env["ir.config_parameter"].sudo()
        driver_days = int(icp.get_param("tms.default_driver_license_security_days") or 0)
        vehicle_days = int(icp.get_param("tms.default_vehicle_insurance_security_days") or 0)
        reasons = dict(self.env["tms.trip.assignment"]._fields["reason"]._description_selection(self.env))
        results = dict(self.env["tms.trip.assignment"]._fields["gate_result"]._description_selection(self.env))
        for trip in self:
            asg = trip.sudo().current_assignment_id
            if not asg or not asg.gate_snapshot:
                trip.gate_table = False
                continue
            end = fields.Date.to_date(trip.scheduled_date_end) if trip.scheduled_date_end else fields.Date.today()
            rows = []
            for raw in asg.gate_snapshot.splitlines():
                parts = [p.strip() for p in raw.split("|")]
                if len(parts) < 3:
                    continue
                holder, code = parts[0], parts[1]
                for prefix in ("Market vehicle ", "Vehicle ", "Driver ", "Market driver"):
                    if holder.startswith(prefix):
                        holder = holder[len(prefix):].strip() or holder
                        break
                doc = names.get(code) or types.get(code) or code
                if parts[2] == "missing":
                    rows.append((doc, "—", holder, "—", _badge(_("Missing"), "danger")))
                    continue
                number = parts[2]
                valid = parts[3].replace("valid until", "").strip() if len(parts) > 3 else ""
                date = fields.Date.to_date(valid) if valid and valid not in ("False", "None") else False
                days = driver_days if code == "DL" else vehicle_days
                if not date:
                    status = _badge(_("Missing date"), "danger")
                elif date < end:
                    status = _badge(_("Expires before the trip ends"), "danger")
                elif days and date <= end + timedelta(days=days):
                    status = _badge(_("Expiring"), "warning")
                else:
                    status = _badge(_("Valid"), "success")
                rows.append((doc, number, holder, format_date(self.env, date, date_format="dd/MM/yyyy") if date else "—",
                             status))
            body = Markup("").join(
                Markup("<tr><td>%s</td><td><code>%s</code></td><td>%s</td><td>%s</td><td>%s</td></tr>") % row
                for row in rows)
            head = Markup("<thead><tr><th>%s</th><th>%s</th><th>%s</th><th>%s</th><th>%s</th></tr></thead>") % (
                _("Document"), _("Reference"), _("Holder"), _("Valid until"), _("Status"))
            note = _("Checked %(when)s at %(reason)s (assignment #%(n)s): %(result)s for %(veh)s / %(drv)s.",
                     when=_dt(self.env, asg.date_from), reason=reasons.get(asg.reason, "").lower(), n=asg.sequence,
                     result=results.get(asg.gate_result, "").lower(),
                     veh=asg.vehicle_number or asg.vehicle_id.license_plate or "—",
                     drv=asg.driver_id.name or asg.market_driver_name or "—")
            trip.gate_table = Markup(
                '<table class="table table-sm o_tms_gate mb-1">%s<tbody>%s</tbody></table>'
                '<div class="text-muted small">%s</div>') % (head, body, escape(note))

    # ------------------------------------------------------------------ financials
    def _tms_ledger_rows(self):
        """(section, label, source, party, status, tone, amount) rows: revenue, costs, claims and recoveries."""
        self.ensure_one()
        trip = self.sudo()
        rows = []
        for link in trip.consignment_link_ids.filtered(lambda x: x.state != "cancel"):
            lr = link.consignment_id
            share = (link.weight / lr.charged_weight) if lr.charged_weight and link.weight else 1.0
            if lr.freight_uninvoiced <= 0.005 and lr.freight_amount:
                status, tone = _("Invoiced"), "success"
            elif lr.freight_basis == "tbb" and lr.pod_status != "verified":
                status, tone = _("Awaits POD"), "secondary"
            else:
                status, tone = _("To invoice"), "info"
            rows.append(("revenue", _("Freight · %(lr)s (%(kg)s kg)", lr=lr.name, kg="{:,.0f}".format(
                lr.charged_weight * share)), lr.name, lr.billing_partner_id.name or "", status, tone,
                lr.freight_amount * share))
        for charge in trip.charge_ids.filtered(lambda c: c.recover_from == "customer" and c.state not in (
                "draft", "cancel", "waived")):
            billed = charge.state in ("billed", "deducted")
            rows.append(("revenue", _("Customer charge · %s", charge.category_id.name), charge.name,
                         charge.partner_id.name or "", _("Billed") if billed else _("Approved · unbilled"),
                         "success" if billed else "info", charge.amount))
        expenses = trip.expense_ids.filtered(lambda e: e.state != "cancel")
        if expenses:
            cats = ", ".join(dict.fromkeys(expenses.mapped("category_id.name"))).lower()
            names = expenses.sorted("name").mapped("name")
            source = names[0] if len(names) == 1 else "%s – %s" % (names[0], names[-1].rsplit("/", 1)[-1])
            payees = len(set(expenses.mapped(lambda e: e.paid_to_id.id or e.payment_mode)))
            booked = all(e.state == "approved" for e in expenses)
            rows.append(("cost", _("Trip expenses · %s", cats), source,
                         _("%s payees", payees) if payees > 1 else (expenses[:1].paid_to_id.name or ""),
                         _("Booked") if booked else _("Draft"), "success" if booked else "secondary",
                         sum(expenses.mapped("amount"))))
        elements = dict(HIRE_ELEMENTS)
        memos = trip.hire_memo_ids.filtered(lambda p: p.state != "cancel") if "hire_memo_ids" in trip else []
        bill_tone = {"blocked": (_("Bill on hold"), "warning"), "passed": (_("Bill passed"), "info"),
                     "overridden": (_("Bill overridden"), "info"), "posted": (_("Bill posted"), "success")}
        for memo in memos:
            match = memo.tms_match_ids.sorted("id", reverse=True)[:1]
            status, tone = bill_tone.get(match.state, (_("No bill"), "secondary"))
            for line in memo.order_line.filtered(lambda line: not line.display_type and line.price_subtotal):
                if line.tms_hire_element == "hire":
                    label = _("Market hire · %s", memo.tms_route or memo.tms_vehicle_number or "")
                    source = memo.name
                else:
                    label = _("%s payable to the transporter", elements.get(line.tms_hire_element, line.name))
                    source = "%s · %s" % (memo.name, _("line %s", line.sequence // 10 or line.sequence))
                rows.append(("cost", label, source, memo.partner_id.name or "", status, tone, line.price_subtotal))
        bills = self.env["account.move.line"].sudo().search([
            ("tms_trip_id", "=", trip.id), ("parent_state", "=", "posted"), ("display_type", "=", "product"),
            ("move_id.move_type", "in", ("in_invoice", "in_refund")), ("tms_line_kind", "not in", ("recovery", "claim"))])
        for line in bills.filtered(lambda line: not (line.purchase_line_id.order_id.tms_is_hire_memo)
                                   and line.tms_cost_class in ("trip_direct", False)):
            rows.append(("cost", line.name or line.product_id.name or "", line.move_id.name,
                         line.move_id.partner_id.name or "", _("Posted"), "success", line.balance))
        claim_states = dict(self.env["tms.damage.claim"]._fields["state"]._description_selection(self.env))
        damage = dict(self.env["tms.damage.claim"]._fields["damage_type"]._description_selection(self.env))
        for claim in trip.claim_ids.filtered(lambda c: c.state not in ("draft", "rejected", "cancel")):
            rows.append(("claim", _("Customer claim · %s", damage.get(claim.damage_type, "").lower()), claim.name,
                         claim.claimant_id.name or claim.consignment_id.billing_partner_id.name or "",
                         claim_states.get(claim.state, ""), "secondary",
                         -(claim.approved_amount or claim.estimated_amount)))
        for charge in trip.charge_ids.filtered(lambda c: c.recover_from == "vendor" and c.state not in (
                "draft", "cancel", "waived")):
            rows.append(("claim", _("Vendor recovery · %s", charge.category_id.name), charge.name,
                         charge.partner_id.name or "", _("Deducted") if charge.state == "deducted" else
                         _("To deduct"), "success" if charge.state == "deducted" else "info", charge.amount))
        return rows

    def _compute_ledger_html(self):
        titles = {"revenue": _("Revenue"), "cost": _("Transportation costs"), "claim": _("Claims & recoveries")}
        for trip in self:
            if not trip.id:
                trip.ledger_html = False
                continue
            rows = trip._tms_ledger_rows()
            revenue = sum(r[6] for r in rows if r[0] == "revenue")
            cost = sum(r[6] for r in rows if r[0] == "cost")
            claims = sum(r[6] for r in rows if r[0] == "claim")
            net = revenue - cost + claims
            margin = (net / revenue * 100.0) if revenue else 0.0
            tiles = [(_("Gross revenue"), inr(revenue), ""), (_("Transportation costs"), inr(cost), ""),
                     (_("Claims − recoveries"), inr(claims), ""),
                     (_("Net contribution"), inr(net), "%.1f%%" % margin)]
            strip = Markup("").join(
                Markup('<div class="col border-end p-3"><div class="text-muted small">%s</div>'
                       '<div class="fs-4 %s">%s <span class="fs-6 text-muted">%s</span></div></div>') % (
                    label, "text-danger" if value.startswith("-") else "", value.replace("-", "−"), extra)
                for label, value, extra in tiles)
            body = []
            for key in ("revenue", "cost", "claim"):
                part = [r for r in rows if r[0] == key]
                if not part:
                    continue
                body.append(Markup('<tr class="table-light"><td colspan="5" class="fw-bold small text-uppercase">%s'
                                   '</td></tr>') % titles[key])
                for _k, label, source, party, status, tone, amount in part:
                    sign = amount if key != "cost" else amount
                    body.append(Markup('<tr><td>%s</td><td><code>%s</code></td><td>%s</td><td>%s</td>'
                                       '<td class="text-end">%s</td></tr>') % (
                        label, source, party, _badge(status, tone), inr(sign).replace("-", "−")))
                if key == "revenue":
                    body.append(Markup('<tr class="fw-bold"><td colspan="4">%s</td><td class="text-end">%s</td></tr>')
                                % (_("Gross revenue"), inr(revenue)))
                elif key == "cost":
                    body.append(Markup('<tr class="fw-bold"><td colspan="4">%s</td><td class="text-end">%s</td></tr>')
                                % (_("Total costs"), inr(cost)))
            body.append(Markup('<tr class="fw-bold border-top border-dark"><td colspan="4">%s</td>'
                               '<td class="text-end">%s</td></tr>') % (_("Net contribution margin"),
                                                                     inr(net).replace("-", "−")))
            head = Markup('<thead><tr><th>%s</th><th>%s</th><th>%s</th><th>%s</th><th class="text-end">%s</th>'
                          '</tr></thead>') % (_("Line"), _("Source document"), _("Party"), _("Status"), _("Amount"))
            trip.ledger_html = Markup('<div class="row g-0 border rounded mb-3">%s</div>'
                                      '<table class="table table-sm o_tms_ledger">%s<tbody>%s</tbody></table>'
                                      '<div class="text-muted small">%s</div>') % (
                strip, head, Markup("").join(body),
                _("Includes open items: freight awaiting its POD, approved charges not yet billed and hire not yet "
                  "billed. The Trip P&L report counts posted documents only."))
