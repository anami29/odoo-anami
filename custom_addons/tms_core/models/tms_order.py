import math
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .tms_consignment import CARGO_TYPES
from .tools import schedule, users_in_group

FLEET_TYPES = [("owned", "Owned"), ("market", "Market")]


class TmsOrder(models.Model):
    """The trip: OCA tms.order extended (BR-TRP-01)."""

    _name = "tms.order"
    _inherit = ["tms.order", "tms.reason.mixin"]
    _check_company_auto = True

    company_id = fields.Many2one(string="Branch")
    user_id = fields.Many2one("res.users", string="Dispatcher", default=lambda s: s.env.user, tracking=True)
    tms_mode = fields.Selection(
        [("hybrid", "Hybrid"), ("broker", "Broker"), ("consumer", "Consumer")], readonly=True, copy=False,
        string="Operating mode")
    fleet_type = fields.Selection(FLEET_TYPES, default="owned", required=True, tracking=True)
    tms_cargo_type = fields.Selection(CARGO_TYPES, string="Cargo Type", default="regular", required=True, tracking=True)
    empty_trip = fields.Boolean(string="Empty / positioning trip", help="BR-TRP-10: carries no LR.")
    state_code = fields.Selection(related="stage_id.tms_code", store=True, string="Status", index=True)
    stage_id = fields.Many2one(tracking=True)
    # planning (BR-TRP-06) – the trip's vehicle and driver themselves compute from the open assignment
    planned_vehicle_id = fields.Many2one(
        "fleet.vehicle", string="Planned vehicle", domain="[('tms_ownership', '=', 'owned')]", tracking=True
    )
    planned_driver_id = fields.Many2one("tms.driver", string="Planned driver", tracking=True)
    planned_market_vehicle_id = fields.Many2one("tms.market.vehicle", string="Planned market vehicle", tracking=True)
    market_driver_name = fields.Char()
    market_driver_phone = fields.Char()
    market_driver_licence = fields.Char()
    market_driver_licence_expiry = fields.Date()
    broker_id = fields.Many2one("res.partner", string="Broker / transporter", tracking=True)
    planned_hire_amount = fields.Monetary(currency_field="currency_id", string="Agreed hire")
    currency_id = fields.Many2one(related="company_id.currency_id")
    # links and lines
    consignment_link_ids = fields.One2many("tms.trip.consignment", "trip_id", string="Consignments")
    consignment_ids = fields.Many2many("tms.consignment", compute="_compute_consignment_ids", string="LRs")
    assignment_ids = fields.One2many("tms.trip.assignment", "trip_id")
    open_assignment_id = fields.Many2one("tms.trip.assignment", compute="_compute_assignment")
    primary_vehicle_id = fields.Many2one("fleet.vehicle", compute="_compute_primary_vehicle", store=True,
                                         string="Primary vehicle")
    hire_memo_ids = fields.One2many("purchase.order", "tms_trip_id", string="Hire memos")
    hire_memo_count = fields.Integer(compute="_compute_counts")
    dsr_ids = fields.One2many("tms.trip.dsr", "trip_id", string="DSR")
    breakdown_ids = fields.One2many("tms.trip.breakdown", "trip_id")
    incident_ids = fields.One2many("tms.trip.incident", "trip_id")
    charge_ids = fields.One2many("tms.trip.charge", "trip_id")
    expense_ids = fields.One2many("tms.trip.expense", "trip_id")
    advance_ids = fields.One2many("tms.driver.advance", "trip_id")
    settlement_ids = fields.One2many("tms.driver.settlement", "trip_id")
    claim_ids = fields.One2many("tms.damage.claim", "trip_id")
    ewaybill_update_ids = fields.One2many("tms.ewaybill.update", "trip_id", string="E-way bill tasks")
    consolidated_ewaybill = fields.Char(string="Consolidated e-way bill", tracking=True)
    diversion_km = fields.Float(tracking=True)
    # gate and status
    gate_status = fields.Selection(
        [("not_run", "Not run"), ("pass", "Passed"), ("warn", "Passed with warnings"), ("fail", "Failed"),
         ("not_enforced", "Not enforced")],
        compute="_compute_assignment",
        string="Gate",
    )
    started_without_gate = fields.Boolean(readonly=True, copy=False, tracking=True)
    pod_status = fields.Selection(
        [("none", "No POD"), ("pending", "Pending"), ("partial", "Partly verified"), ("verified", "Verified")],
        compute="_compute_pod_status", string="POD status",
        store=True,
    )
    planned_weight = fields.Float(compute="_compute_planned_weight", store=True, string="Planned weight (kg)")
    payload_kg = fields.Float(compute="_compute_payload")
    payload_override_reason = fields.Char(tracking=True, copy=False)
    payload_approved_by_id = fields.Many2one("res.users", readonly=True, copy=False, tracking=True)
    # odometer and km (BR-EXE-03, BR-PNL-16)
    start_odometer = fields.Float(tracking=True, copy=False)
    end_odometer = fields.Float(tracking=True, copy=False)
    own_km = fields.Float(compute="_compute_own_km", store=True, string="Own km")
    delivered_on = fields.Datetime(readonly=True, copy=False, tracking=True)
    done_on = fields.Datetime(readonly=True, copy=False)
    last_dsr_at = fields.Datetime(compute="_compute_dsr", store=True)
    dsr_overdue = fields.Boolean(compute="_compute_dsr_overdue", search="_search_dsr_overdue")
    cancel_reason = fields.Text(copy=False, tracking=True)
    # financial summary (BR-TRP-01, BR-LED-*)
    fin_revenue = fields.Monetary(compute="_compute_financials", string="Revenue")
    fin_direct_cost = fields.Monetary(compute="_compute_financials", string="Trip direct cost")
    fin_recoveries = fields.Monetary(compute="_compute_financials", string="Recoveries")
    fin_contribution = fields.Monetary(compute="_compute_financials", string="Contribution")
    fin_open_commitments = fields.Monetary(compute="_compute_financials", string="Open commitments")
    fuel_kmpl = fields.Float(compute="_compute_fuel", string="km per litre")
    fuel_norm = fields.Float(related="primary_vehicle_id.tms_fuel_norm")
    timeline_ids = fields.One2many("mail.message", compute="_compute_timeline")

    # ------------------------------------------------------------------
    # computes
    # ------------------------------------------------------------------
    @api.depends("consignment_link_ids.state", "consignment_link_ids.consignment_id")
    def _compute_consignment_ids(self):
        for trip in self:
            trip.consignment_ids = trip.consignment_link_ids.filtered(lambda x: x.state != "cancel").mapped(
                "consignment_id"
            )

    @api.depends("assignment_ids.vehicle_id", "assignment_ids.sequence")
    def _compute_primary_vehicle(self):
        """BR-TRP-08, BR-PNL-05: the vehicle of the first assignment, never changing."""
        for trip in self:
            trip.primary_vehicle_id = trip.assignment_ids.sorted("sequence")[:1].vehicle_id

    @api.depends("assignment_ids.state", "assignment_ids.sequence", "assignment_ids.gate_result")
    def _compute_assignment(self):
        for trip in self:
            ordered = trip.assignment_ids.sorted("sequence")
            trip.open_assignment_id = ordered.filtered(lambda a: a.state == "open")[:1]
            last = trip.open_assignment_id or ordered[-1:]
            trip.gate_status = last.gate_result if last and last.gate_result else "not_run"

    @api.depends("hire_memo_ids")
    def _compute_counts(self):
        for trip in self:
            trip.hire_memo_count = len(trip.hire_memo_ids)

    @api.depends("consignment_link_ids.pod_status", "consignment_link_ids.state")
    def _compute_pod_status(self):
        for trip in self:
            links = trip.consignment_link_ids.filtered(lambda x: x.state != "cancel")
            if not links:
                trip.pod_status = "none"
            elif all(link.pod_status == "verified" for link in links):
                trip.pod_status = "verified"
            elif any(link.pod_status == "verified" for link in links):
                trip.pod_status = "partial"
            else:
                trip.pod_status = "pending"

    @api.depends("consignment_link_ids.weight", "consignment_link_ids.state")
    def _compute_planned_weight(self):
        for trip in self:
            trip.planned_weight = sum(trip.consignment_link_ids.filtered(lambda x: x.state != "cancel").mapped("weight"))

    @api.depends("fleet_type", "planned_market_vehicle_id.payload_kg", "planned_vehicle_id.tms_payload_kg")
    def _compute_payload(self):
        for trip in self:
            if trip.fleet_type == "market":
                trip.payload_kg = trip.planned_market_vehicle_id.payload_kg
            else:
                trip.payload_kg = trip.planned_vehicle_id.tms_payload_kg

    @api.depends("assignment_ids.odometer_out", "assignment_ids.odometer_in", "assignment_ids.vehicle_id",
                 "assignment_ids.sequence")
    def _compute_own_km(self):
        """BR-PNL-16, BR-PNL-18: km of the primary truck only; a replacement's km are not charged to it."""
        for trip in self:
            ordered = trip.assignment_ids.sorted("sequence")
            primary = ordered[:1].vehicle_id
            km = 0.0
            for asg in ordered.filtered(lambda a: a.vehicle_id == primary and a.fleet_type == "owned"):
                if asg.odometer_in and asg.odometer_out:
                    km += max(asg.odometer_in - asg.odometer_out, 0.0)
            trip.own_km = km

    @api.depends("dsr_ids.logged_at")
    def _compute_dsr(self):
        for trip in self:
            trip.last_dsr_at = max(trip.dsr_ids.mapped("logged_at")) if trip.dsr_ids else False

    def _compute_dsr_overdue(self):
        now = fields.Datetime.now()
        for trip in self:
            cadence = trip.company_id.tms_dsr_cadence_hours or 6.0
            ref = trip.last_dsr_at or trip.date_start
            trip.dsr_overdue = bool(
                trip.state_code in ("started", "in_transit") and ref and ref + timedelta(hours=cadence) < now
            )

    def _search_dsr_overdue(self, operator, value):
        trips = self.search([("state_code", "in", ("started", "in_transit"))])
        ids = trips.filtered("dsr_overdue").ids
        positive = (operator == "=" and value) or (operator == "!=" and not value)
        return [("id", "in" if positive else "not in", ids)]

    def _compute_financials(self):
        """Posted figures from bill, expense and invoice lines that name the trip (BR-LED-02..06)."""
        MoveLine = self.env["account.move.line"].sudo()
        for trip in self:
            revenue = 0.0
            for link in trip.consignment_link_ids.filtered(lambda x: x.state != "cancel"):
                lr = link.consignment_id
                share = (link.weight / lr.charged_weight) if lr.charged_weight else 1.0
                lines = lr.invoice_line_ids.filtered(
                    lambda line: line.parent_state == "posted" and line.tms_line_kind in (
                        "freight", "rate_correction", "charge")
                    and (not line.tms_trip_id or line.tms_trip_id == trip)
                )
                for line in lines:
                    sign = -1 if line.move_id.move_type == "out_refund" else 1
                    revenue += sign * line.price_subtotal * share
            cost_lines = MoveLine.search([("tms_trip_id", "=", trip.id), ("parent_state", "=", "posted"),
                                          ("display_type", "=", "product")])
            direct = 0.0
            recoveries = 0.0
            for line in cost_lines:
                if line.tms_line_kind == "recovery":
                    recoveries += -line.balance
                elif line.tms_line_kind == "claim":
                    direct += line.balance if line.move_id.move_type != "out_refund" else line.price_subtotal
                elif line.move_id.move_type in ("in_invoice", "in_refund", "entry") and line.tms_cost_class in (
                        "trip_direct", False) and line.balance and line.account_id.account_type in (
                        "expense", "expense_direct_cost"):
                    direct += line.balance
            trip.fin_revenue = revenue
            trip.fin_direct_cost = direct
            trip.fin_recoveries = recoveries
            trip.fin_contribution = revenue + recoveries - direct
            trip.fin_open_commitments = trip._tms_open_commitments()

    def _tms_open_commitments(self):
        """BR-LED-06: unbilled hire, open PO items, approved unposted expenses, unbilled charges, unsettled claims."""
        self.ensure_one()
        total = 0.0
        for po in self.hire_memo_ids.filtered(lambda p: p.state in ("purchase", "done")):
            for line in po.order_line:
                total += max(line.qty_received - line.qty_invoiced, 0.0) * line.price_unit
        total += sum(self.expense_ids.filtered(lambda e: e.state == "approved" and not e.move_id).mapped("amount"))
        total += sum(self.charge_ids.filtered(lambda c: c.state == "approved" and c.recover_from == "customer"
                                              ).mapped("amount")) * 0  # revenue side, shown separately
        total += sum(self.claim_ids.filtered(lambda c: c.state == "approved").mapped("approved_amount"))
        return total

    @api.depends("expense_ids.fuel_litres", "expense_ids.state", "own_km")
    def _compute_fuel(self):
        for trip in self:
            fuel = trip.expense_ids.filtered(lambda e: e.category_id.is_fuel and e.state != "cancel")
            litres = sum(fuel.mapped("fuel_litres"))
            trip.fuel_kmpl = (trip.own_km / litres) if litres and trip.own_km else 0.0

    def _compute_timeline(self):
        """BR-AUD-05: one timeline of the trip and its lines' messages."""
        Message = self.env["mail.message"]
        line_fields = (("tms.trip.assignment", "assignment_ids"), ("tms.trip.incident", "incident_ids"),
                       ("tms.trip.breakdown", "breakdown_ids"), ("tms.trip.charge", "charge_ids"),
                       ("tms.trip.expense", "expense_ids"), ("tms.trip.consignment", "consignment_link_ids"))
        for trip in self:
            dom = [("model", "=", "tms.order"), ("res_id", "=", trip.id)]
            for model, field in line_fields:
                if trip[field]:
                    dom = ["|"] + dom + ["&", ("model", "=", model), ("res_id", "in", trip[field].ids)]
            # the latest 500 events, shown oldest first so the trip reads in time order (UT-AUD-08)
            trip.timeline_ids = Message.search(dom, order="date desc, id desc", limit=500).sorted(
                lambda m: (m.date, m.id))

    # ------------------------------------------------------------------
    # ORM
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            company = self.env["res.company"].browse(vals.get("company_id") or self.env.company.id)
            vals.setdefault("tms_mode", company.tms_operation_mode)  # BR-CFG-03
            if company.tms_operation_mode == "broker":
                vals["fleet_type"] = "market"  # BR-TRP-02
            draft = self.env["tms.stage"].get_stage("draft")
            if draft:
                vals.setdefault("stage_id", draft.id)
        return super().create(vals_list)

    def write(self, vals):
        if "stage_id" in vals and not self.env.context.get("tms_stage_move"):
            raise UserError(_("Trips move between stages only through their buttons (BR-EXE-02)."))
        if "vehicle_id" in vals and not self.env.context.get("tms_assignment"):
            raise UserError(_("The trip's vehicle follows its open assignment and cannot be edited (BR-TRP-08)."))
        if "fleet_type" in vals:
            for trip in self:
                if trip.tms_mode == "broker" and vals["fleet_type"] != "market":
                    raise UserError(_("Broker mode trips are always Market (BR-TRP-02)."))
        return super().write(vals)

    def unlink(self):
        if any(trip.state_code not in ("draft", "cancel") for trip in self):
            raise UserError(_("Started or confirmed trips are never deleted (BR-AUD-04)."))
        if not self.env.user.has_group("tms_core.group_tms_admin"):
            raise UserError(_("Only Administrators delete trips (BR-BRN-08)."))
        return super().unlink()

    def _tms_move_stage(self, code):
        stage = self.env["tms.stage"].get_stage(code)
        if not stage:
            raise UserError(_("Stage %s is missing.", code))
        self.with_context(tms_stage_move=True).write({"stage_id": stage.id})

    # ------------------------------------------------------------------
    # planning and confirmation
    # ------------------------------------------------------------------
    def _tms_check_vehicle_free(self, vehicle, driver, exclude_trip=None):
        """BR-ASG-14."""
        Assignment = self.env["tms.trip.assignment"]
        dom = [("state", "=", "open")]
        if exclude_trip:
            dom.append(("trip_id", "!=", exclude_trip.id))
        if vehicle and vehicle.tms_ownership == "owned":
            if Assignment.search_count(dom + [("vehicle_id", "=", vehicle.id)], limit=1):
                raise UserError(_("%s already has an open assignment on another trip.", vehicle.display_name))
            if vehicle._tms_has_open_breakdown() or vehicle._tms_has_open_service():
                raise UserError(_("%s has an open breakdown or service log and cannot be assigned.",
                                  vehicle.display_name))
        if driver and Assignment.search_count(dom + [("driver_id", "=", driver.id)], limit=1):
            raise UserError(_("Driver %s already has an open assignment on another trip.", driver.name))

    def _tms_check_confirm(self):
        self.ensure_one()
        if not (self.route_id or (self.origin_id and self.destination_id)):
            raise UserError(_("A trip needs a route, or an origin and a destination (BR-TRP-06)."))
        links = self.consignment_link_ids.filtered(lambda x: x.state != "cancel")
        if self.empty_trip and links:
            raise UserError(_("An empty or positioning trip carries no LR (BR-TRP-10)."))
        if not self.empty_trip and not links:
            raise UserError(_("A trip needs at least one consignment link (BR-TRP-06)."))
        if self.fleet_type == "owned":
            if not (self.planned_vehicle_id and self.planned_driver_id):
                raise UserError(_("An owned trip needs a vehicle and a driver (BR-TRP-06)."))
            self._tms_check_vehicle_free(self.planned_vehicle_id, self.planned_driver_id, exclude_trip=self)
        else:
            if not self.planned_market_vehicle_id:
                raise UserError(_("A market trip needs a market vehicle (BR-TRP-06)."))
            if self.planned_market_vehicle_id.blacklisted:
                raise UserError(_("A blacklisted market vehicle cannot be assigned (RL-02)."))
            if not self.hire_memo_ids.filtered(lambda p: p.state in ("purchase", "done")):
                raise UserError(_("A market trip needs a confirmed hire memo (BR-TRP-06, BR-HIR-01)."))
        # BR-TRP-05 payload
        if self.payload_kg and self.planned_weight > self.payload_kg:
            if not (self.payload_approved_by_id and self.payload_override_reason):
                raise UserError(
                    _("Planned weight %(w)s kg exceeds the payload %(p)s kg; the Operations Head must approve "
                      "with a reason (BR-TRP-05).", w=self.planned_weight, p=self.payload_kg)
                )
        self._tms_check_confirm_odc()

    def _tms_check_confirm_odc(self):
        """Hook: tms_odc requires filled equipment lines (BR-ODC-05)."""
        return True

    def action_confirm(self):
        for trip in self:
            if trip.state_code != "draft":
                raise UserError(_("Only draft trips are confirmed."))
            trip._tms_check_confirm()
            trip._tms_move_stage("confirmed")
            trip._tms_create_partb_tasks(vehicle_label=trip._tms_planned_vehicle_label())
            trip._tms_on_confirm()
        return True

    def _tms_on_confirm(self):
        """Hook: tms_profitability creates the trip's analytic account (BR-LED-01)."""
        return True

    def action_approve_payload(self):
        self.ensure_one()
        if not self.env.user.has_group("tms_core.group_operations_head"):
            raise UserError(_("Only the Operations Head approves a payload override."))
        if not self.payload_override_reason:
            raise UserError(_("Enter the reason for the payload override first."))
        self.payload_approved_by_id = self.env.user
        self.env["tms.override.log"].log("payload", self, _("Payload override %s kg", self.planned_weight),
                                         reason=self.payload_override_reason)

    def _tms_planned_vehicle_label(self):
        self.ensure_one()
        if self.fleet_type == "market":
            return self.planned_market_vehicle_id.name or ""
        return self.planned_vehicle_id.license_plate or self.planned_vehicle_id.name or ""

    def _tms_create_partb_tasks(self, vehicle_label, assignment=None):
        """BR-EWB-05, BR-ASG-08: one Part-B task per live e-way bill on board."""
        self.ensure_one()
        links = self.consignment_link_ids.filtered(lambda x: x.state != "cancel" and not x.outcome)
        bills = links.mapped("consignment_id.ewaybill_ids")._is_live()
        Task = self.env["tms.ewaybill.update"]
        for bill in bills:
            Task.create({
                "trip_id": self.id,
                "ewaybill_id": bill.id,
                "assignment_id": assignment.id if assignment else False,
                "kind": "partb",
                "vehicle_number": vehicle_label,
            })

    def action_cancel(self):
        """BR-EXE-07: from Draft or Confirmed only; releases the consignment links."""
        for trip in self:
            if trip.state_code not in ("draft", "confirmed"):
                raise UserError(_("A trip is cancelled only from Draft or Confirmed (BR-EXE-07)."))
            trip.consignment_link_ids.filtered(lambda x: x.state != "cancel").write({"state": "cancel"})
            trip.ewaybill_update_ids.filtered(lambda t: t.state == "pending").unlink()
            trip._tms_move_stage("cancel")
        return True

    def action_cancel_with_reason(self):
        reason = self.env.context.get("tms_reason")
        self.write({"cancel_reason": reason})
        return self.action_cancel()

    def action_reset_draft(self):
        for trip in self:
            if trip.state_code not in ("confirmed", "cancel"):
                raise UserError(_("Only confirmed or cancelled trips return to draft."))
            trip.ewaybill_update_ids.filtered(lambda t: t.state == "pending").unlink()
            trip._tms_move_stage("draft")

    # ------------------------------------------------------------------
    # assignments and the gate (BR-ASG-01..14, BR-CMP-*)
    # ------------------------------------------------------------------
    def _tms_open_assignment(self, vals, enforce=True):
        """Run the gate, close the open line and open the next one (append-only, BR-ASG-02)."""
        self.ensure_one()
        Assignment = self.env["tms.trip.assignment"]
        vehicle = self.env["fleet.vehicle"].browse(vals.get("vehicle_id"))
        market_vehicle = self.env["tms.market.vehicle"].browse(vals.get("market_vehicle_id"))
        driver = self.env["tms.driver"].browse(vals.get("driver_id"))
        if market_vehicle and not vehicle:
            vehicle = market_vehicle._tms_fleet_vehicle()
            vals["vehicle_id"] = vehicle.id
        if market_vehicle.blacklisted:
            raise UserError(_("A blacklisted market vehicle cannot be assigned (RL-02)."))
        self._tms_check_vehicle_free(vehicle if not market_vehicle else None, driver, exclude_trip=self)
        result, failures, warnings, snapshot = self.env["tms.compliance.gate"].evaluate(
            self.scheduled_date_end, vehicle=vehicle if not market_vehicle else None,
            driver=driver or None, market_vehicle=market_vehicle or None,
            market_docs=self.market_document_ids if market_vehicle else None,
        )
        if market_vehicle and not driver:
            # market driver's licence captured on the assignment (BR-CMP-05)
            exp = vals.get("market_driver_licence_expiry")
            end = fields.Date.to_date(self.scheduled_date_end) if self.scheduled_date_end else fields.Date.today()
            if not vals.get("market_driver_licence") or not exp or fields.Date.to_date(exp) < end:
                failures.append(_("Market driver licence missing or expiring before the scheduled end"))
                result = "fail"
            snapshot.append("Market driver | DL | %s | valid until %s" % (vals.get("market_driver_licence"), exp))
        enforcement = self.company_id.tms_enforce_compliance
        if result == "fail" and enforcement and enforce:
            raise UserError(_("Compliance gate failed (BR-CMP-06):\n%s", "\n".join(failures)))
        current = self.open_assignment_id
        if current:
            current.write({"state": "closed", "date_to": fields.Datetime.now(),
                           "odometer_in": vals.pop("previous_odometer_in", current.odometer_in)})
        seq = (max(self.assignment_ids.mapped("sequence")) + 1) if self.assignment_ids else 1
        vals.update({
            "trip_id": self.id,
            "sequence": seq,
            "date_from": fields.Datetime.now(),
            "state": "open",
            "gate_result": result if enforcement else "not_enforced",
            "gate_snapshot": "\n".join(snapshot),
            "gate_messages": "\n".join(failures + warnings),
        })
        asg = Assignment.create(vals)
        self.with_context(tms_assignment=True).write({
            "vehicle_id": vehicle.id or False,
            "driver_id": driver.id or False,
        })
        label = asg.vehicle_number or vehicle.license_plate or vehicle.name or ""
        self.consignment_link_ids.filtered(lambda x: x.state != "cancel" and not x.outcome).write(
            {"assignment_id": asg.id}
        )
        self.consignment_ids.tms_note_vehicle(label)
        if warnings:
            self.message_post(body=_("Compliance warnings:<br/>%s", "<br/>".join(warnings)))
        return asg, result

    # ------------------------------------------------------------------
    # execution (BR-EXE-*)
    # ------------------------------------------------------------------
    def _tms_check_start_ewaybills(self):
        self.ensure_one()
        lrs = self.consignment_ids
        missing = lrs.filtered("ewaybill_missing")
        if missing:
            raise UserError(_("A required e-way bill is missing on %s (BR-EWB-04).", ", ".join(missing.mapped("name"))))
        pending = self.ewaybill_update_ids.filtered(lambda t: t.state == "pending" and t.kind == "partb")
        if pending:
            raise UserError(_("Complete the Part-B tasks with evidence before the trip moves (BR-EWB-05): %s",
                              ", ".join(pending.mapped("ewaybill_id.name"))))

    def _tms_check_start_odc(self):
        """Hook: tms_odc checks permits, route survey and escort (BR-ODC-06)."""
        return True

    def button_start_order(self):
        """START: the compliance gate, e-way bill checks, the first assignment and the opening odometer."""
        for trip in self:
            if trip.state_code != "confirmed":
                raise UserError(_("Only confirmed trips start."))
            trip._tms_check_start_ewaybills()
            trip._tms_check_start_odc()
            vals = {"reason": "start", "fleet_type": trip.fleet_type}
            if trip.fleet_type == "owned":
                if not trip.start_odometer:
                    raise UserError(_("Record the opening odometer before START (BR-EXE-03)."))
                trip.planned_vehicle_id.tms_record_odometer(trip.start_odometer)
                vals.update({
                    "vehicle_id": trip.planned_vehicle_id.id,
                    "driver_id": trip.planned_driver_id.id,
                    "odometer_out": trip.start_odometer,
                })
            else:
                memo = trip.hire_memo_ids.filtered(lambda p: p.state in ("purchase", "done"))[:1]
                vals.update({
                    "market_vehicle_id": trip.planned_market_vehicle_id.id,
                    "vehicle_number": trip.planned_market_vehicle_id.name,
                    "market_driver_name": trip.market_driver_name,
                    "market_driver_phone": trip.market_driver_phone,
                    "market_driver_licence": trip.market_driver_licence,
                    "market_driver_licence_expiry": trip.market_driver_licence_expiry,
                    "broker_id": trip.broker_id.id,
                    "hire_memo_id": memo.id,
                })
            asg, result = trip._tms_open_assignment(vals)
            if trip.fleet_type == "market":
                asg.hire_memo_id.tms_assignment_id = asg
            if not trip.company_id.tms_enforce_compliance:
                # BR-CMP-07: with enforcement off, OCA's own insurance and licence checks still run
                trip.started_without_gate = True
                if len(trip.vehicle_id.insurance_id) <= 1:
                    super(TmsOrder, trip.with_context(tms_assignment=True)).button_start_order()
            trip.write({"date_start": fields.Datetime.now(), "start_trip": True})
            trip._tms_move_stage("started")
            trip.ewaybill_update_ids.filtered(lambda t: t.state == "pending" and not t.assignment_id).write(
                {"assignment_id": asg.id})
            trip.consignment_ids._tms_refresh_state()
            trip._tms_on_start()
        return True

    def _tms_on_start(self):
        """Hook for profitability (standard rate in force on the START date)."""
        return True

    def _tms_planned_km(self):
        """Planned trip km: the OCA route's distance, else the longest e-way bill distance on board;
        tms_estimate uses the order item's distance."""
        self.ensure_one()
        if self.route_id and self.route_id.distance:
            return self.route_id.distance
        bills = self.consignment_ids.mapped("line_ids.ewaybill_id")
        return max(bills.mapped("distance_km") or [0.0])

    def _tms_check_moving(self):
        """WF-04, BR-EWB-05: no delivery while a breakdown is open or Part-B tasks of a change are pending."""
        for trip in self:
            if trip.breakdown_ids.filtered(lambda b: b.state == "open"):
                raise UserError(_("Resolve the open breakdown on %s first (WF-04).", trip.name))
            if trip.ewaybill_update_ids.filtered(lambda t: t.state == "pending" and t.kind == "partb"):
                raise UserError(_("Complete the Part-B tasks with evidence before the goods move on (BR-EWB-05)."))

    def action_depart(self):
        """BR-EXE-04: Started → In Transit; blocked while Part-B tasks of a vehicle change are open."""
        for trip in self:
            if trip.state_code not in ("started", "in_transit"):
                raise UserError(_("Only started trips depart."))
            pending = trip.ewaybill_update_ids.filtered(lambda t: t.state == "pending")
            if pending:
                raise UserError(_("Complete the Part-B tasks with evidence before departure (BR-EWB-05)."))
            if trip.state_code == "started":
                trip._tms_move_stage("in_transit")
        return True

    def button_end_order(self):
        """BR-EXE-05: the End button records the last delivery and the delivery date."""
        for trip in self:
            if trip.state_code not in ("started", "in_transit"):
                raise UserError(_("Only trips on the road can end."))
            for link in trip.consignment_link_ids.filtered(lambda x: x.state != "cancel" and not x.outcome):
                link.action_mark_delivered()
            trip._tms_check_delivered(force=True)
        return True

    def _tms_check_delivered(self, force=False):
        for trip in self:
            if trip.state_code not in ("started", "in_transit"):
                continue
            links = trip.consignment_link_ids.filtered(lambda x: x.state != "cancel")
            if (links and all(link.outcome for link in links)) or (force and trip.empty_trip):
                last = max(links.mapped("delivered_at") or [fields.Datetime.now()])
                trip.delivered_on = last
                if not trip.date_start:
                    trip.date_start = fields.Datetime.now()
                super(TmsOrder, trip.with_context(tms_assignment=True)).button_end_order()
                trip.date_end = last
                trip._tms_move_stage("delivered")
                trip._tms_compute_detention()
                trip._tms_on_delivered()

    def _tms_after_dsr(self, dsr):
        """Hook: tms_odc warns on movement windows and e-way bill expiry (BR-ODC-07)."""
        return True

    def _tms_on_delivered(self):
        """Hook: profitability dates the revenue and the standard charge."""
        return True

    def action_done(self):
        """BR-EXE-06: Done needs verified PODs, no draft expenses, a closed settlement and the closing odometer."""
        for trip in self:
            if trip.state_code != "delivered":
                raise UserError(_("Only delivered trips are closed."))
            links = trip.consignment_link_ids.filtered(lambda x: x.state != "cancel")
            if any(link.pod_status != "verified" for link in links):
                raise UserError(_("Every POD, or its outcome, must be verified first (BR-EXE-06)."))
            if trip.expense_ids.filtered(lambda e: e.state == "draft"):
                raise UserError(_("Approve or cancel the draft expenses first (BR-EXE-06)."))
            needs_settlement = trip.advance_ids.filtered(lambda a: a.state == "posted") or trip.expense_ids.filtered(
                lambda e: e.payment_mode == "driver_cash" and e.state == "approved")
            if needs_settlement and not trip.settlement_ids.filtered(lambda s: s.state == "done"):
                raise UserError(_("Close the driver settlement first (BR-EXE-06)."))
            asg = trip.open_assignment_id
            if trip.primary_vehicle_id.tms_ownership == "owned" and asg.fleet_type == "owned":
                if not trip.end_odometer:
                    raise UserError(_("Record the closing odometer first (BR-EXE-06)."))
                if trip.end_odometer < (asg.odometer_out or 0.0):
                    raise UserError(_("The closing odometer is below the opening reading."))
                asg.vehicle_id.tms_record_odometer(trip.end_odometer)
                asg.odometer_in = trip.end_odometer
            if asg:
                asg.write({"state": "closed", "date_to": fields.Datetime.now()})
            trip.done_on = fields.Datetime.now()
            trip._tms_move_stage("done")
        return True

    # ------------------------------------------------------------------
    # detention (BR-CHG-09)
    # ------------------------------------------------------------------
    def _tms_compute_detention(self):
        """Each LR's stop is timed by the At loading / At unloading DSR lines naming it; lines naming no LR
        count for the trip's first LR. Free hours and the rate are its billing party's or its contract's."""
        category = self.env.ref("tms_core.tms_category_detention", raise_if_not_found=False)
        if not category:
            return
        for trip in self:
            dsr = trip.dsr_ids.sorted("logged_at")
            first = trip.consignment_ids[:1]
            for lr in trip.consignment_ids:
                customer = lr.billing_partner_id.commercial_partner_id
                if not customer:
                    continue
                free_load, free_unload, rate = trip._tms_free_hours(customer)
                if not rate:
                    continue
                mine = dsr.filtered(lambda d: d.consignment_id == lr or (not d.consignment_id and lr == first))
                at_loading = mine.filtered(lambda d: d.status == "at_loading")[:1]
                departed = dsr.filtered(lambda d: d.status == "departed" and at_loading
                                        and d.logged_at > at_loading.logged_at)[:1]
                at_unloading = mine.filtered(lambda d: d.status == "at_unloading")[:1]
                link = trip.consignment_link_ids.filtered(lambda x: x.consignment_id == lr and x.delivered_at)[:1]
                delivered = link.delivered_at or trip.delivered_on
                for kind, start, end, free in (
                    ("loading", at_loading.logged_at if at_loading else False,
                     departed.logged_at if departed else False, free_load),
                    ("unloading", at_unloading.logged_at if at_unloading else False, delivered, free_unload),
                ):
                    if not (start and end):
                        continue
                    excess = (end - start).total_seconds() / 3600.0 - free
                    if excess <= 0:
                        continue
                    # BR-CHG-09 acceptance (#22): detention is charged per whole day beyond the free hours
                    days = int(excess // 24)
                    if days <= 0:
                        continue
                    amount = round(rate * days, 2)
                    note = _("Detention at %(k)s of %(lr)s: %(h).1f h beyond %(f).1f free hours, %(d)s day(s)",
                             k=kind, lr=lr.name, h=excess, f=free, d=days)
                    existing = trip.charge_ids.filtered(
                        lambda c: c.category_id == category and c.detention_kind == kind
                        and c.consignment_id == lr)
                    if existing:
                        if existing.state == "draft":
                            existing.write({"amount": amount, "note": note})
                        continue
                    self.env["tms.trip.charge"].create({
                        "trip_id": trip.id,
                        "consignment_id": lr.id,
                        "category_id": category.id,
                        "amount": amount,
                        "recover_from": "customer",
                        "partner_id": customer.id,
                        "detention_kind": kind,
                        "note": note,
                    })

    def _tms_free_hours(self, customer):
        """Hook: contracts override the customer's free hours (BR-MD-16)."""
        return customer.tms_free_hours_loading, customer.tms_free_hours_unloading, customer.tms_detention_rate

    # ------------------------------------------------------------------
    # crons and smart buttons
    # ------------------------------------------------------------------
    @api.model
    def _cron_dsr_overdue(self):
        """BR-EXE-10, BR-NTF-02."""
        for trip in self.search([("dsr_overdue", "=", True)]):
            users = users_in_group(self.env, "tms_core.group_control_room", trip.company_id)
            schedule(trip, users, _("DSR overdue"))

    @api.model
    def _cron_daily_dsr_email(self):
        """BR-EXE-11, BR-NTF-03: daily email of consignments in transit per billing party."""
        template = self.env.ref("tms_core.mail_template_daily_dsr", raise_if_not_found=False)
        if not template:
            return
        links = self.env["tms.trip.consignment"].search([
            ("state", "!=", "cancel"), ("outcome", "=", False),
            ("trip_id.state_code", "in", ("started", "in_transit"))])
        parties = links.mapped("consignment_id.billing_partner_id.commercial_partner_id")
        for party in parties:
            if party.email:
                template.with_context(tms_links=links.filtered(
                    lambda x, p=party: x.consignment_id.billing_partner_id.commercial_partner_id == p)
                ).send_mail(party.id, force_send=False)

    def action_view_hire_memos(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Hire memos"),
            "res_model": "purchase.order",
            "view_mode": "list,form",
            "domain": [("tms_trip_id", "=", self.id)],
            "context": {"default_tms_trip_id": self.id, "default_tms_is_hire_memo": True,
                        "default_partner_id": self.broker_id.id},
        }

    def action_create_hire_memo(self):
        """BR-HIR-01: one hire memo per market assignment; the agreed hire is item 10."""
        self.ensure_one()
        if self.fleet_type != "market":
            raise UserError(_("Hire memos are for market trips."))
        if not self.broker_id:
            raise UserError(_("Choose the broker or transporter first."))
        product = self.env.ref("tms_core.product_vehicle_hire")
        po = self.env["purchase.order"].create({
            "partner_id": self.broker_id.id,
            "company_id": self.company_id.id,
            "tms_is_hire_memo": True,
            "tms_trip_id": self.id,
            "tms_vehicle_number": self.planned_market_vehicle_id.name,
            "tms_route": "%s → %s" % (self.origin_id.display_name or self.route_origin or "",
                                      self.destination_id.display_name or self.route_destination or ""),
            "tms_advance_percent": min(self.broker_id.tms_advance_percent or 0.0,
                                       self.company_id.tms_max_advance_percent),
            "order_line": [(0, 0, {
                "product_id": product.id,
                "name": _("Vehicle hire %s", self.name),
                "product_qty": 1.0,
                "price_unit": self.planned_hire_amount,
                "tms_hire_element": "hire",
            })],
        })
        return {"type": "ir.actions.act_window", "res_model": "purchase.order", "res_id": po.id, "view_mode": "form"}

    def action_print_trip_sheet(self):
        return self.env.ref("tms_core.action_report_trip_sheet").report_action(self)

    def action_open_breakdown(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Report breakdown"),
            "res_model": "tms.trip.breakdown",
            "view_mode": "form",
            "target": "new",
            "context": {"default_trip_id": self.id},
        }

    def action_new_settlement(self):
        self.ensure_one()
        driver = self.open_assignment_id.driver_id or self.assignment_ids[:1].driver_id or self.planned_driver_id
        settlement = self.settlement_ids.filtered(lambda s: s.state == "draft")[:1] or self.env[
            "tms.driver.settlement"].create({"trip_id": self.id, "driver_id": driver.id})
        return {"type": "ir.actions.act_window", "res_model": "tms.driver.settlement", "res_id": settlement.id,
                "view_mode": "form"}
