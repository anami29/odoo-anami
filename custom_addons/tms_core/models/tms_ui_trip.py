# Copyright 2026 RLFB (Riamona Luxury and Fashion Brands), https://www.riamona.com
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
"""Trip screens: the trip form (POD page, current assignment, smart buttons), the trips list and kanban,
and the daily status report built on the trip."""
from datetime import datetime, timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import format_datetime

from .tms_trip_lines import DSR_STATUSES

GATE_PREVIEW = [
    ("valid", "Valid"),
    ("warning", "Valid, expiring soon"),
    ("failed", "Failed"),
    ("pending", "Pending"),
    ("not_enforced", "Not enforced"),
]
VENDOR_BILL_STATES = [
    ("none", "No bill"),
    ("blocked", "On hold"),
    ("passed", "Passed"),
    ("overridden", "Overridden"),
    ("posted", "Posted"),
]
ROAD_STATES = ("started", "in_transit")


def _dt(env, value):
    return format_datetime(env, value, dt_format="dd/MM/yyyy HH:mm") if value else ""


class TmsTripConsignment(models.Model):
    _inherit = "tms.trip.consignment"

    lr_parties = fields.Char(compute="_compute_lr_parties", string="Consignor → Consignee")
    lr_state = fields.Selection(related="consignment_id.state", string="LR status")

    @api.depends("consignment_id.consignor_id", "consignment_id.consignee_id")
    def _compute_lr_parties(self):
        for link in self:
            lr = link.consignment_id
            link.lr_parties = "%s → %s" % (lr.consignor_id.name or "", lr.consignee_id.name or "") if lr else ""

    def action_open_link(self):
        """Open the link's own form (delivery, POD upload, verify, reject) from the trip."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.consignment_id.name,
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "views": [(self.env.ref("tms_core.tms_trip_consignment_form").id, "form")],
            "target": "current",
        }


class TmsTripAssignment(models.Model):
    _inherit = "tms.trip.assignment"

    currency_id = fields.Many2one(related="company_id.currency_id")
    hire_amount = fields.Monetary(compute="_compute_hire_amount", currency_field="currency_id", string="Hire")

    @api.depends("hire_memo_id.order_line.price_subtotal", "hire_memo_id.order_line.tms_hire_element")
    def _compute_hire_amount(self):
        for asg in self:
            memo = asg.hire_memo_id
            hire = memo.order_line.filtered(lambda line: line.tms_hire_element == "hire")
            asg.hire_amount = sum(hire.mapped("price_subtotal")) if hire else memo.amount_untaxed


class TmsOrder(models.Model):
    _inherit = "tms.order"

    # ---- header
    route_display = fields.Char(compute="_compute_route_display", string="Origin → destination")
    trip_subtitle = fields.Char(compute="_compute_trip_subtitle")
    lr_count = fields.Integer(compute="_compute_lr_count", store=True, string="LR count")
    # ---- current assignment (the open one, else the last one)
    current_assignment_id = fields.Many2one("tms.trip.assignment", compute="_compute_current_assignment",
                                            string="Current assignment")
    current_fleet_type = fields.Selection([("owned", "Owned"), ("market", "Market")],
                                          compute="_compute_current_assignment", string="Fleet")
    current_vehicle_number = fields.Char(compute="_compute_current_assignment", string="Vehicle number")
    current_broker_id = fields.Many2one("res.partner", compute="_compute_current_assignment", string="Current broker")
    current_hire_memo_id = fields.Many2one("purchase.order", compute="_compute_current_assignment",
                                           string="Current hire memo")
    current_hire_amount = fields.Monetary(compute="_compute_current_assignment", string="Current hire",
                                          currency_field="currency_id")
    current_driver_label = fields.Char(compute="_compute_current_assignment", string="Current driver")
    current_position = fields.Char(compute="_compute_current_assignment", string="Assignment line")
    current_note = fields.Char(compute="_compute_current_assignment")
    vehicle_display = fields.Char(compute="_compute_current_assignment", string="Current vehicle")
    driver_display = fields.Char(compute="_compute_current_assignment", string="Driver / broker")
    # ---- smart buttons
    incident_count = fields.Integer(compute="_compute_ui_counts")
    claim_count = fields.Integer(compute="_compute_ui_counts")
    expense_total = fields.Monetary(compute="_compute_ui_counts", currency_field="currency_id")
    lr_to_invoice_count = fields.Integer(compute="_compute_ui_counts")
    vendor_bill_state = fields.Selection(VENDOR_BILL_STATES, compute="_compute_vendor_bill_state",
                                         string="Vendor bill")
    # ---- POD
    pod_link_ids = fields.One2many("tms.trip.consignment", "trip_id", domain=[("state", "=", "active")],
                                   string="PODs")
    pod_banner = fields.Char(compute="_compute_pod_banner")
    # ---- market vehicle documents
    show_market_documents = fields.Boolean(
        compute="_compute_show_market_documents",
        help="The compliance check reads the documents captured on the trip whenever a market vehicle is "
             "assigned: at the start of a market trip and when a broken-down vehicle is replaced by a market "
             "vehicle while the trip is on the road.")
    # ---- pre-start compliance
    gate_preview = fields.Selection(GATE_PREVIEW, compute="_compute_gate_preview", string="Compliance status")
    gate_preview_text = fields.Char(compute="_compute_gate_preview", string="Compliance")
    # ---- daily status report
    last_dsr_status = fields.Selection(DSR_STATUSES, compute="_compute_last_dsr_line", store=True,
                                       string="Latest status")
    last_dsr_location = fields.Char(compute="_compute_last_dsr_line", store=True, string="Location")
    last_dsr_remark = fields.Text(compute="_compute_last_dsr_line", store=True, string="Latest remark")
    current_eta = fields.Datetime(compute="_compute_last_dsr_line", store=True, string="ETA")
    delay_hours = fields.Float(compute="_compute_delay_hours", store=True, string="Delay vs plan (h)", aggregator=None,
                               help="ETA of the latest status line minus the scheduled end, in hours.")
    next_dsr_due = fields.Datetime(compute="_compute_next_dsr_due", store=True, string="Next update due")
    dsr_customer_id = fields.Many2one("res.partner", compute="_compute_dsr_customer", store=True,
                                      string="Customer", help="Billing party of the trip's first LR.")
    kanban_flag = fields.Char(compute="_compute_kanban_flag")
    kanban_flag_level = fields.Selection([("danger", "Danger"), ("warning", "Warning")],
                                         compute="_compute_kanban_flag")

    # ------------------------------------------------------------------
    # computes
    # ------------------------------------------------------------------
    @api.depends("route_id", "route_origin", "route_destination", "origin_id.name", "destination_id.name")
    def _compute_route_display(self):
        for trip in self:
            origin = trip.origin_id.name or trip.route_origin or ""
            dest = trip.destination_id.name or trip.route_destination or ""
            trip.route_display = "%s → %s" % (origin, dest) if (origin or dest) else ""

    @api.depends("route_display", "lr_count", "planned_weight")
    def _compute_trip_subtitle(self):
        for trip in self:
            parts = [trip.route_display] if trip.route_display else []
            parts.append(_("1 LR") if trip.lr_count == 1 else _("%s LRs", trip.lr_count))
            parts.append(_("%s kg", "{:,.0f}".format(trip.planned_weight or 0.0)))
            trip.trip_subtitle = " · ".join(parts)

    @api.depends("consignment_link_ids.state", "consignment_link_ids.consignment_id")
    def _compute_lr_count(self):
        for trip in self:
            trip.lr_count = len(trip.consignment_link_ids.filtered(lambda x: x.state != "cancel").consignment_id)

    @api.depends("fleet_type", "assignment_ids.state", "assignment_ids.sequence", "assignment_ids.fleet_type",
                 "assignment_ids.vehicle_number", "assignment_ids.driver_id", "assignment_ids.market_driver_name",
                 "assignment_ids.broker_id", "assignment_ids.hire_memo_id", "planned_vehicle_id", "planned_driver_id", "planned_market_vehicle_id",
                 "broker_id", "market_driver_name")
    def _compute_current_assignment(self):
        for trip in self:
            ordered = trip.assignment_ids.sorted("sequence")
            asg = ordered.filtered(lambda a: a.state == "open")[:1] or ordered[-1:]
            trip.current_assignment_id = asg
            trip.current_fleet_type = asg.fleet_type or trip.fleet_type
            trip.current_vehicle_number = asg.vehicle_number or asg.vehicle_id.license_plate or False
            trip.current_broker_id = asg.broker_id
            trip.current_hire_memo_id = asg.hire_memo_id
            trip.current_hire_amount = asg.hire_amount if asg else 0.0
            if asg:
                market = asg.fleet_type == "market"
                driver = asg.driver_id.name or asg.market_driver_name or ""
                trip.current_driver_label = (_("%s (market driver)", driver) if market and driver else driver)
                trip.current_position = _("#%(n)s of %(m)s · %(state)s", n=asg.sequence, m=len(ordered),
                                          state=_("open") if asg.state == "open" else _("closed"))
                previous = ordered.filtered(lambda a: a.sequence < asg.sequence)[-1:]
                trip.current_note = _("since %(when)s, replacing %(old)s (%(fleet)s)", when=_dt(self.env, asg.date_from),
                                      old=previous.vehicle_number or "",
                                      fleet=dict(previous._fields["fleet_type"].selection).get(previous.fleet_type)
                                      ) if previous else _("since %s", _dt(self.env, asg.date_from))
                trip.vehicle_display = asg.vehicle_number or asg.vehicle_id.license_plate or ""
                trip.driver_display = (asg.broker_id.name or asg.market_driver_name or "") if market else (
                    asg.driver_id.name or "")
            else:
                trip.current_driver_label = trip.current_position = trip.current_note = False
                if trip.fleet_type == "market":
                    trip.vehicle_display = trip.planned_market_vehicle_id.name or _("Not assigned")
                    trip.driver_display = trip.broker_id.name or trip.market_driver_name or ""
                else:
                    trip.vehicle_display = (trip.planned_vehicle_id.license_plate or trip.planned_vehicle_id.name
                                            or _("Not assigned"))
                    trip.driver_display = trip.planned_driver_id.name or ""

    @api.depends("incident_ids", "claim_ids", "expense_ids.amount", "expense_ids.state", "consignment_link_ids.state")
    def _compute_ui_counts(self):
        Wizard = self.env["tms.invoice.lr.wizard"]
        for trip in self:
            trip.incident_count = len(trip.incident_ids)
            trip.claim_count = len(trip.claim_ids)
            trip.expense_total = sum(trip.expense_ids.filtered(lambda e: e.state != "cancel").mapped("amount"))
            trip.lr_to_invoice_count = len(trip.consignment_ids.filtered(Wizard._tms_eligible))

    def _compute_vendor_bill_state(self):
        ids = [trip._origin.id for trip in self if trip._origin.id]
        matches = self.env["tms.vendor.bill.match"].sudo().search([("trip_id", "in", ids)], order="id desc") \
            if ids else self.env["tms.vendor.bill.match"]
        latest = {}
        for match in matches:
            latest.setdefault(match.trip_id.id, match.state)
        for trip in self:
            trip.vendor_bill_state = latest.get(trip._origin.id, "none")

    @api.depends("state_code", "consignment_link_ids.pod_status", "consignment_link_ids.outcome",
                 "consignment_link_ids.state", "consignment_link_ids.uploaded_by_id",
                 "consignment_link_ids.uploaded_on")
    def _compute_pod_banner(self):
        for trip in self:
            links = trip.consignment_link_ids.filtered(
                lambda x: x.state != "cancel" and x.outcome and x.pod_status != "verified")
            if trip.state_code not in ("started", "in_transit", "delivered") or not links:
                trip.pod_banner = False
                continue
            parts = []
            uploaded = links.filtered(lambda x: x.pod_status == "uploaded")
            if uploaded:
                last = max(uploaded.mapped("uploaded_on") or [False]) if all(uploaded.mapped("uploaded_on")) else False
                rule = _("A POD Verifier, the uploader included, can verify it.") \
                    if trip.company_id.tms_pod_self_verify else \
                    _("Only a POD Verifier other than the uploader can verify it.")
                parts.append(_(
                    "POD uploaded%(when)s by %(who)s, verification pending. %(rule)s", rule=rule,
                    when=(" " + _dt(self.env, last)) if last else "",
                    who=", ".join(uploaded.mapped("uploaded_by_id.name")) or _("an unknown user")))
            waiting = links - uploaded
            if waiting:
                parts.append(_("POD not yet uploaded for %s.", ", ".join(waiting.mapped("consignment_id.name"))))
            parts.append(_("The trip cannot be marked done until every POD is verified."))
            trip.pod_banner = " ".join(parts)

    @api.depends("fleet_type", "state_code", "assignment_ids.fleet_type", "breakdown_ids.state",
                 "market_document_ids")
    def _compute_show_market_documents(self):
        """The gate adds the trip's captured documents to the register whenever the assignment it opens is a
        market vehicle: the start of a market trip, or a market replacement of a broken-down vehicle, which can be
        assigned at any time while the trip is on the road (the breakdown dialog reports and replaces in one
        step). The page shows while that can happen and once it has happened."""
        for trip in self:
            trip.show_market_documents = bool(
                trip.fleet_type == "market"
                or trip.state_code in ROAD_STATES
                or "market" in trip.assignment_ids.mapped("fleet_type")
                or trip.breakdown_ids.filtered(lambda b: b.state == "open")
                or trip.market_document_ids
            )

    @api.depends("state_code", "fleet_type", "planned_vehicle_id", "planned_driver_id", "planned_market_vehicle_id",
                 "market_document_ids", "market_driver_licence", "market_driver_licence_expiry",
                 "scheduled_date_end", "assignment_ids.gate_result", "assignment_ids.gate_snapshot")
    def _compute_gate_preview(self):
        for trip in self:
            trip.gate_preview, trip.gate_preview_text = trip._tms_gate_preview()

    def _tms_gate_preview(self):
        """Before START: the gate evaluated on the planned vehicle, driver and market documents (read only).
        After START: the result recorded on the current assignment."""
        self.ensure_one()
        if self.state_code == "cancel":
            return False, False
        end = fields.Date.to_date(self.scheduled_date_end) if self.scheduled_date_end else fields.Date.context_today(self)
        if self.state_code in ("draft", "confirmed", False):
            Gate = self.env["tms.compliance.gate"]
            if self.fleet_type == "market":
                if not self.planned_market_vehicle_id:
                    return "pending", _("Pending")
                result, failures, _warnings, snapshot = Gate.evaluate(
                    self.scheduled_date_end, market_vehicle=self.planned_market_vehicle_id,
                    market_docs=self.market_document_ids)
                exp = self.market_driver_licence_expiry
                if not self.market_driver_licence or not exp or exp < end:
                    result = "fail"
                    snapshot.append("Market driver | DL | missing" if not (self.market_driver_licence and exp)
                                    else "Market driver | DL | %s | valid until %s" % (self.market_driver_licence, exp))
                else:
                    snapshot.append("Market driver | DL | %s | valid until %s" % (self.market_driver_licence, exp))
            else:
                if not (self.planned_vehicle_id and self.planned_driver_id):
                    return "pending", _("Pending")
                result, failures, _warnings, snapshot = Gate.evaluate(
                    self.scheduled_date_end, vehicle=self.planned_vehicle_id, driver=self.planned_driver_id)
        else:
            asg = self.current_assignment_id
            if not asg or not asg.gate_result:
                return "pending", _("Pending")
            if asg.gate_result == "not_enforced":
                return "not_enforced", _("Not enforced")
            result = asg.gate_result
            snapshot = [line for line in (asg.gate_snapshot or "").splitlines() if line.strip()]
        return self._tms_gate_summary(result, snapshot, end)

    @api.model
    def _tms_gate_summary(self, result, snapshot, end):
        bad = []
        for line in snapshot:
            parts = [p.strip() for p in line.split("|")]
            if len(parts) < 3:
                continue
            code = parts[1]
            if parts[2] == "missing":
                bad.append(_("%s missing", code))
                continue
            until = parts[-1].replace("valid until", "").strip()
            try:
                until = fields.Date.to_date(until)
            except ValueError:
                until = False
            if not until:
                bad.append(_("%s missing", code))
            elif until < end:
                bad.append(_("%s expired", code))
        total = len(snapshot)
        if result == "fail" or bad:
            if len(bad) == 1:
                return "failed", bad[0]
            if bad:
                return "failed", _("%(n)s of %(t)s failing", n=len(bad), t=total)
            return "failed", _("Gate fails")
        if result == "warn":
            return "warning", _("Valid %(n)s/%(t)s, expiring soon", n=total, t=total)
        return "valid", _("Valid %(n)s/%(t)s", n=total, t=total)

    @api.depends("dsr_ids.logged_at", "dsr_ids.status", "dsr_ids.location", "dsr_ids.eta", "dsr_ids.remark")
    def _compute_last_dsr_line(self):
        def key(dsr):
            return dsr.logged_at or datetime.min, dsr._origin.id or 0

        for trip in self:
            lines = trip.dsr_ids.sorted(key)
            last = lines[-1:]
            trip.last_dsr_status = last.status or False
            trip.last_dsr_location = last.location or False
            trip.last_dsr_remark = last.remark or False
            # a line without an ETA keeps the last ETA reported
            trip.current_eta = lines.filtered("eta")[-1:].eta or False

    @api.depends("current_eta", "scheduled_date_end")
    def _compute_delay_hours(self):
        for trip in self:
            if trip.current_eta and trip.scheduled_date_end:
                trip.delay_hours = round((trip.current_eta - trip.scheduled_date_end).total_seconds() / 3600.0, 2)
            else:
                trip.delay_hours = 0.0

    @api.depends("last_dsr_at", "date_start", "company_id.tms_dsr_cadence_hours")
    def _compute_next_dsr_due(self):
        for trip in self:
            ref = trip.last_dsr_at or trip.date_start
            cadence = trip.company_id.tms_dsr_cadence_hours or 6.0
            trip.next_dsr_due = ref + timedelta(hours=cadence) if ref else False

    @api.depends("consignment_link_ids.state", "consignment_link_ids.drop_sequence",
                 "consignment_link_ids.consignment_id.billing_partner_id")
    def _compute_dsr_customer(self):
        for trip in self:
            links = trip.consignment_link_ids.filtered(lambda x: x.state != "cancel").sorted(
                lambda x: (x.drop_sequence, x._origin.id or 0))
            trip.dsr_customer_id = links[:1].consignment_id.billing_partner_id

    @api.depends("state_code", "gate_preview", "gate_preview_text", "last_dsr_status", "pod_status")
    def _compute_kanban_flag(self):
        labels = dict(self._fields["last_dsr_status"]._description_selection(self.env))
        for trip in self:
            flag, level = False, False
            if trip.state_code in ("draft", "confirmed") and trip.gate_preview == "failed":
                flag, level = trip.gate_preview_text, "danger"
            elif trip.state_code in ROAD_STATES and (trip.last_dsr_status or "").startswith("delayed"):
                flag = labels.get(trip.last_dsr_status, "").replace("Delayed – ", _("Delay · "))
                level = "danger" if trip.last_dsr_status == "delayed_breakdown" else "warning"
            elif trip.state_code == "delivered" and trip.pod_status != "verified":
                flag, level = _("POD pending"), "warning"
            trip.kanban_flag, trip.kanban_flag_level = flag, level

    # ------------------------------------------------------------------
    # buttons
    # ------------------------------------------------------------------
    def action_verify_pods(self):
        """Verify every uploaded POD on the trip; the uploader still cannot verify their own upload."""
        for trip in self:
            links = trip.consignment_link_ids.filtered(lambda x: x.state != "cancel" and x.pod_status == "uploaded")
            if not links:
                raise UserError(_("No uploaded POD is waiting for verification on %s. Upload the POD on the "
                                  "POD tab first.", trip.name))
            links.action_verify_pod()
        return True

    def action_report_incident(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Report incident"),
            "res_model": "tms.trip.incident",
            "view_mode": "form",
            "views": [(self.env.ref("tms_core.tms_trip_incident_form").id, "form")],
            "target": "new",
            "context": {"default_trip_id": self.id},
        }

    def action_log_status(self):
        self.ensure_one()
        if self.state_code not in ROAD_STATES:
            raise UserError(_("Status is logged only on trips on the road."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Log status · %s", self.name),
            "res_model": "tms.trip.dsr",
            "view_mode": "form",
            "views": [(self.env.ref("tms_core.tms_trip_dsr_form_log").id, "form")],
            "target": "new",
            "context": {"default_trip_id": self.id, "default_location": self.last_dsr_location,
                        "default_eta": self.current_eta},
        }

    def action_open_trip(self):
        """Row button of the trips list: open the trip itself (its POD tab carries the pending PODs)."""
        self.ensure_one()
        return {"type": "ir.actions.act_window", "res_model": "tms.order", "res_id": self.id, "view_mode": "form",
                "views": [(False, "form")], "target": "current"}

    def _tms_list_action(self, name, model, domain, context=None):
        return {
            "type": "ir.actions.act_window",
            "name": name,
            "res_model": model,
            "view_mode": "list,form",
            "views": [(False, "list"), (False, "form")],
            "domain": domain,
            "context": context or {},
        }

    def action_view_lrs(self):
        self.ensure_one()
        return self._tms_list_action(_("Consignments of %s", self.name), "tms.consignment",
                                     [("id", "in", self.consignment_ids.ids)], {"create": False})

    def action_view_incidents(self):
        self.ensure_one()
        return self._tms_list_action(_("Incidents of %s", self.name), "tms.trip.incident",
                                     [("trip_id", "=", self.id)], {"default_trip_id": self.id})

    def action_view_claims(self):
        self.ensure_one()
        return self._tms_list_action(_("Claims of %s", self.name), "tms.damage.claim",
                                     [("trip_id", "=", self.id)], {"default_trip_id": self.id})

    def action_view_expenses(self):
        self.ensure_one()
        return self._tms_list_action(_("Expenses of %s", self.name), "tms.trip.expense",
                                     [("trip_id", "=", self.id)], {"default_trip_id": self.id})

    def action_view_vendor_bill(self):
        self.ensure_one()
        return self._tms_list_action(_("Vendor bill match of %s", self.name), "tms.vendor.bill.match",
                                     [("trip_id", "=", self.id)], {"create": False})

    def action_view_lrs_to_invoice(self):
        self.ensure_one()
        eligible = self.consignment_ids.filtered(self.env["tms.invoice.lr.wizard"]._tms_eligible)
        return self._tms_list_action(_("LRs to invoice on %s", self.name), "tms.consignment",
                                     [("id", "in", eligible.ids)], {"create": False})
