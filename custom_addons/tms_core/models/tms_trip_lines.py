from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .tools import schedule, users_in_group

DSR_STATUSES = [
    ("on_schedule", "On Schedule"),
    ("departed", "Departed"),
    ("at_loading", "At Loading"),
    ("at_unloading", "At Unloading"),
    ("night_halt", "Night Halt"),
    ("delayed_breakdown", "Delayed – Breakdown"),
    ("delayed_traffic", "Delayed – Traffic/Weather"),
    ("delayed_other", "Delayed – Other"),
    ("halted_window", "Halted – Movement window"),
    ("halted_clearance", "Halted – Clearance"),
]
ODC_DSR = ("halted_window", "halted_clearance")
ASSIGNMENT_REASONS = [
    ("start", "Start"),
    ("breakdown", "Breakdown"),
    ("accident", "Accident"),
    ("driver_change", "Driver change"),
    ("transhipment", "Transhipment"),
    ("other", "Other"),
]
POD_STATUSES = [("pending", "Pending"), ("uploaded", "Uploaded"), ("verified", "Verified"), ("rejected", "Rejected")]
OUTCOMES = [("delivered", "Delivered"), ("not_delivered", "Not delivered"), ("refused", "Refused")]


class TmsTripConsignment(models.Model):
    """BR-TRP-03, BR-POD-01..09: a trip's link to an LR, carrying its delivery and POD."""

    _name = "tms.trip.consignment"
    _description = "Trip consignment link and POD"
    _inherit = ["mail.thread", "mail.activity.mixin", "tms.reason.mixin"]
    _order = "trip_id, drop_sequence, id"
    _rec_name = "consignment_id"

    trip_id = fields.Many2one("tms.order", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="trip_id.company_id", store=True, string="Trip branch")
    consignment_id = fields.Many2one(
        "tms.consignment", string="LR", required=True, index=True,
        domain="[('state', '=', 'confirmed'), ('unplanned_packages', '>', 0)]",
    )
    lr_company_id = fields.Many2one(related="consignment_id.company_id", store=True, string="Booking branch")
    delivery_branch_id = fields.Many2one(related="consignment_id.delivery_branch_id", store=True)
    assignment_id = fields.Many2one("tms.trip.assignment", string="Assignment (leg)")
    packages = fields.Integer(required=True)
    weight = fields.Float(string="Charged weight (kg)")
    drop_sequence = fields.Integer(default=10)
    state = fields.Selection([("active", "Active"), ("cancel", "Removed")], default="active", tracking=True)
    removal_reason = fields.Text(tracking=True)
    # delivery
    delivered_at = fields.Datetime(tracking=True)
    received_by = fields.Char()
    packages_received = fields.Integer()
    shortage_qty = fields.Integer(string="Shortage (packages)")
    damage_qty = fields.Integer(string="Damaged (packages)")
    remarks = fields.Text()
    outcome = fields.Selection(OUTCOMES, tracking=True, copy=False)
    outcome_evidence = fields.Binary(attachment=True, help="FIR, surveyor report or the consignee's refusal note.")
    outcome_evidence_name = fields.Char()
    # POD
    pod_file = fields.Binary(string="POD", attachment=True)
    pod_file_name = fields.Char()
    pod_status = fields.Selection(POD_STATUSES, default="pending", required=True, tracking=True, string="POD status")
    uploaded_by_id = fields.Many2one("res.users", readonly=True, tracking=True)
    uploaded_on = fields.Datetime(readonly=True)
    verified_by_id = fields.Many2one("res.users", readonly=True, tracking=True)
    verified_on = fields.Datetime(readonly=True)
    reject_reason = fields.Text(tracking=True)
    incident_id = fields.Many2one("tms.trip.incident", readonly=True)
    claim_id = fields.Many2one("tms.damage.claim", readonly=True)
    pod_overdue = fields.Boolean(compute="_compute_pod_overdue", search="_search_pod_overdue")

    @api.constrains("consignment_id", "packages", "state")
    def _check_link(self):
        """BR-TRP-04: only Confirmed LRs with unplanned packages can be linked."""
        for link in self.filtered(lambda x: x.state == "active"):
            lr = link.consignment_id
            if lr.state not in ("confirmed", "dispatched"):
                raise ValidationError(_("Only confirmed LRs can be linked to a trip (BR-TRP-04)."))
            planned = sum(lr.trip_link_ids.filtered(lambda x: x.state == "active").mapped("packages"))
            if link.packages <= 0 or planned > lr.total_packages:
                raise ValidationError(
                    _("LR %(lr)s has only %(n)s unplanned packages (BR-TRP-04, BR-LR-14).", lr=lr.name,
                      n=lr.total_packages - planned + link.packages)
                )

    @api.onchange("consignment_id")
    def _onchange_consignment(self):
        if self.consignment_id:
            lr = self.consignment_id
            self.packages = lr.unplanned_packages
            share = (lr.unplanned_packages / lr.total_packages) if lr.total_packages else 1.0
            self.weight = lr.charged_weight * share

    @api.model_create_multi
    def create(self, vals_list):
        links = super().create(vals_list)
        for link in links:
            if not link.weight:
                lr = link.consignment_id
                share = (link.packages / lr.total_packages) if lr.total_packages else 1.0
                link.weight = lr.charged_weight * share
        return links

    def unlink(self):
        if any(link.trip_id.state_code not in ("draft", False) for link in self):
            raise UserError(_("Remove a link with a reason instead of deleting it (BR-LR-18)."))
        return super().unlink()

    def _compute_pod_overdue(self):
        now = fields.Datetime.now()
        for link in self:
            sla = link.company_id.tms_pod_sla_hours or 48.0
            link.pod_overdue = bool(
                link.state == "active" and link.delivered_at and link.pod_status != "verified"
                and link.delivered_at + timedelta(hours=sla) < now
            )

    def _search_pod_overdue(self, operator, value):
        links = self.search([("state", "=", "active"), ("delivered_at", "!=", False), ("pod_status", "!=", "verified")])
        ids = links.filtered("pod_overdue").ids
        positive = (operator == "=" and value) or (operator == "!=" and not value)
        return [("id", "in" if positive else "not in", ids)]

    # ---------------------------------------------------------------- delivery
    def action_mark_delivered(self):
        for link in self:
            if link.trip_id.state_code not in ("started", "in_transit"):
                raise UserError(_("Deliveries are recorded on trips on the road."))
            link.trip_id._tms_check_moving()
            lr = link.consignment_id
            if lr.freight_basis == "topay" and not lr.collected:
                raise UserError(_("To-Pay LR %s is released only after its freight is collected (RL-06).", lr.name))
            link.write({
                "outcome": "delivered",
                "delivered_at": link.delivered_at or fields.Datetime.now(),
                "packages_received": link.packages_received or (link.packages - link.shortage_qty),
            })
            if lr.godown_hold:
                lr.write({"godown_hold": False})
        self._tms_after_outcome()
        return True

    def action_mark_not_delivered(self):
        return self._tms_set_outcome("not_delivered")

    def action_mark_refused(self):
        return self._tms_set_outcome("refused")

    def _tms_set_outcome(self, outcome):
        """BR-POD-08: Not delivered (loss) or Refused, on evidence."""
        for link in self:
            if not link.outcome_evidence:
                raise UserError(_("Attach the evidence first: FIR, surveyor report or refusal note (BR-POD-08)."))
            link.write({"outcome": outcome, "delivered_at": link.delivered_at or fields.Datetime.now(),
                        "pod_status": "uploaded", "uploaded_by_id": self.env.user.id,
                        "uploaded_on": fields.Datetime.now()})
            if outcome == "refused":
                link.consignment_id.write({"godown_hold": True, "godown_since": fields.Date.context_today(self)})
        self._tms_after_outcome()
        return True

    def _tms_after_outcome(self):
        trips = self.mapped("trip_id")
        trips._tms_check_delivered()
        self.mapped("consignment_id")._tms_refresh_state()

    def action_remove_from_trip(self, reason=None, waive=False):
        """BR-LR-18: the Dispatcher removes a placed LR's link with a reason; dead freight is raised."""
        reason = reason or self.env.context.get("tms_reason")
        if not reason:
            raise UserError(_("A reason is required to remove an LR from a trip."))
        category = self.env.ref("tms_core.tms_category_dead_freight", raise_if_not_found=False)
        for link in self:
            if link.trip_id.state_code not in ("draft", "confirmed"):
                raise UserError(_("Links are removed only before the trip starts."))
            link.write({"state": "cancel", "removal_reason": reason})
            lr = link.consignment_id
            if link.trip_id.state_code == "confirmed" and category and not waive:
                self.env["tms.trip.charge"].create({
                    "trip_id": link.trip_id.id,
                    "consignment_id": lr.id,
                    "category_id": category.id,
                    "amount": lr.freight_amount,
                    "recover_from": "customer",
                    "partner_id": lr.billing_partner_id.id,
                    "note": _("Dead freight: %s", reason),
                })
            elif waive:
                if not self.env.user.has_group("tms_core.group_branch_manager"):
                    raise UserError(_("Only a Branch Manager waives dead freight."))
                self.env["tms.override.log"].log("waiver", lr, _("Dead freight waived"), reason=reason)
        return True

    # ---------------------------------------------------------------- POD
    def action_upload_pod(self):
        for link in self:
            if not link.pod_file and not link.outcome_evidence:
                raise UserError(_("Attach the POD file first."))
            if not link.outcome:
                raise UserError(_("Record the delivery first."))
            link.write({"pod_status": "uploaded", "uploaded_by_id": self.env.user.id,
                        "uploaded_on": fields.Datetime.now(), "reject_reason": False})
        return True

    def write(self, vals):
        if "pod_file" in vals and vals.get("pod_file"):
            for link in self:
                if link.pod_status in ("rejected", "pending") and link.outcome:
                    vals.setdefault("pod_status", "uploaded")
                    vals.setdefault("uploaded_by_id", self.env.user.id)
                    vals.setdefault("uploaded_on", fields.Datetime.now())
        return super().write(vals)

    def _tms_check_verifier(self):
        user = self.env.user
        if not user.has_group("tms_core.group_pod_verifier"):
            raise UserError(_("Only a POD Verifier verifies or rejects PODs (BR-POD-03)."))
        for link in self:
            if link.uploaded_by_id == user:
                raise UserError(_("You cannot verify a POD you uploaded (BR-POD-03, BR-BRN-09)."))

    def action_verify_pod(self):
        self._tms_check_verifier()
        for link in self:
            if link.pod_status != "uploaded":
                raise UserError(_("Only uploaded PODs are verified."))
            link.write({"pod_status": "verified", "verified_by_id": self.env.user.id,
                        "verified_on": fields.Datetime.now()})
            if link.shortage_qty or link.damage_qty:
                link._tms_link_claim()  # BR-POD-04
        self.mapped("consignment_id")._tms_refresh_state()
        self._tms_mark_hire_received()  # BR-POD-06
        return True

    def action_reject_pod(self):
        reason = self.env.context.get("tms_reason")
        if not reason:
            raise UserError(_("A rejection needs a reason (BR-POD-02)."))
        self._tms_check_verifier()
        self.write({"pod_status": "rejected", "reject_reason": reason})
        return True

    def _tms_link_claim(self):
        """BR-POD-04: link to the LR's Shortage or Damage incident and claim, or create them."""
        self.ensure_one()
        lr = self.consignment_id
        kind = "shortage" if self.shortage_qty else "damaged_goods"
        cat = self.env.ref("tms_core.tms_category_%s" % ("shortage" if self.shortage_qty else "damage"))
        claim = lr.claim_ids.filtered(lambda c: c.state not in ("rejected",))[:1]
        incident = claim.incident_id or self.env["tms.trip.incident"].search(
            [("consignment_id", "=", lr.id), ("category_id", "=", cat.id), ("state", "!=", "cancel")], limit=1)
        if not incident:
            incident = self.env["tms.trip.incident"].create({
                "trip_id": self.trip_id.id,
                "consignment_id": lr.id,
                "category_id": cat.id,
                "occurred_at": self.delivered_at or fields.Datetime.now(),
                "description": _("Recorded at POD: shortage %(s)s, damage %(d)s packages",
                                 s=self.shortage_qty, d=self.damage_qty),
                "moves_money": True,
            })
        if not claim:
            claim = self.env["tms.damage.claim"].create({
                "consignment_id": lr.id,
                "trip_id": self.trip_id.id,
                "incident_id": incident.id,
                "pod_link_id": self.id,
                "damage_type": kind,
                "affected_qty": self.shortage_qty + self.damage_qty,
                "claimant_id": lr.billing_partner_id.id,
            })
        incident.claim_id = claim
        self.write({"incident_id": incident.id, "claim_id": claim.id})

    def _tms_mark_hire_received(self):
        """BR-POD-06, BR-HIR-05: verifying the last POD on a hire memo's leg receives its hire line."""
        memos = self.mapped("assignment_id.hire_memo_id")
        for memo in memos:
            leg_links = self.search([("assignment_id.hire_memo_id", "=", memo.id), ("state", "=", "active")])
            if leg_links and all(link.pod_status == "verified" for link in leg_links):
                memo._tms_receive_hire()

    @api.model
    def _cron_pod_sla(self):
        """BR-POD-05, BR-NTF-05."""
        for link in self.search([("pod_overdue", "=", True)]):
            branch = link.delivery_branch_id or link.company_id
            users = users_in_group(self.env, "tms_core.group_pod_verifier", branch)
            schedule(link, users, _("POD past SLA: %s", link.consignment_id.name))

    def action_print_pod_form(self):
        return self.env.ref("tms_core.action_report_pod_form").report_action(self)


class TmsTripAssignment(models.Model):
    """BR-ASG-01..03, BR-CMP-09: append-only assignment lines."""

    _name = "tms.trip.assignment"
    _description = "Trip assignment"
    _inherit = ["mail.thread"]
    _order = "trip_id, sequence"

    trip_id = fields.Many2one("tms.order", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="trip_id.company_id", store=True)
    sequence = fields.Integer(required=True, default=1)
    date_from = fields.Datetime(required=True, default=fields.Datetime.now)
    date_to = fields.Datetime()
    fleet_type = fields.Selection([("owned", "Owned"), ("market", "Market")], required=True, default="owned")
    vehicle_id = fields.Many2one("fleet.vehicle", index=True)
    market_vehicle_id = fields.Many2one("tms.market.vehicle")
    vehicle_number = fields.Char()
    driver_id = fields.Many2one("tms.driver", index=True)
    market_driver_name = fields.Char()
    market_driver_phone = fields.Char()
    market_driver_licence = fields.Char()
    market_driver_licence_expiry = fields.Date()
    broker_id = fields.Many2one("res.partner")
    hire_memo_id = fields.Many2one("purchase.order", string="Hire memo")
    reason = fields.Selection(ASSIGNMENT_REASONS, required=True, default="start")
    gate_result = fields.Selection(
        [("pass", "Passed"), ("warn", "Passed with warnings"), ("fail", "Failed"), ("not_enforced", "Not enforced")],
        readonly=True,
    )
    gate_snapshot = fields.Text(readonly=True, help="Documents checked and their dates (BR-CMP-09).")
    gate_messages = fields.Text(readonly=True)
    odometer_out = fields.Float()
    odometer_in = fields.Float()
    km_run = fields.Float(compute="_compute_km_run", store=True)
    state = fields.Selection([("open", "Open"), ("closed", "Closed")], default="open", required=True)

    @api.depends("odometer_out", "odometer_in")
    def _compute_km_run(self):
        for asg in self:
            asg.km_run = max(asg.odometer_in - asg.odometer_out, 0.0) if asg.odometer_in and asg.odometer_out else 0.0

    @api.depends("trip_id", "sequence", "vehicle_number", "vehicle_id")
    def _compute_display_name(self):
        for asg in self:
            asg.display_name = "%s #%s %s" % (asg.trip_id.name or "", asg.sequence,
                                               asg.vehicle_number or asg.vehicle_id.license_plate or "")

    @api.constrains("trip_id", "state")
    def _check_one_open(self):
        for asg in self.filtered(lambda a: a.state == "open"):
            if self.search_count([("trip_id", "=", asg.trip_id.id), ("state", "=", "open"), ("id", "!=", asg.id)]):
                raise ValidationError(_("A trip has at most one open assignment (BR-ASG-02)."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("vehicle_id") and not vals.get("vehicle_number"):
                vehicle = self.env["fleet.vehicle"].browse(vals["vehicle_id"])
                vals["vehicle_number"] = vehicle.license_plate or vehicle.name
        return super().create(vals_list)

    def write(self, vals):
        immutable = {"trip_id", "sequence", "vehicle_id", "market_vehicle_id", "driver_id", "reason", "date_from",
                     "fleet_type"}
        if immutable & set(vals):
            raise UserError(_("Assignment lines are append-only (BR-ASG-02)."))
        if any(asg.state == "closed" for asg in self) and set(vals) - {"odometer_in", "date_to", "state"}:
            raise UserError(_("Closed assignment lines cannot change (BR-ASG-02)."))
        return super().write(vals)

    def unlink(self):
        raise UserError(_("Assignment lines are never deleted (BR-ASG-02)."))


class TmsTripDsr(models.Model):
    """BR-EXE-08, BR-EXE-09: daily status report lines."""

    _name = "tms.trip.dsr"
    _description = "Trip DSR line"
    _order = "logged_at desc, id desc"

    trip_id = fields.Many2one("tms.order", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="trip_id.company_id", store=True)
    logged_at = fields.Datetime(required=True, default=fields.Datetime.now)
    location = fields.Char(required=True)
    status = fields.Selection(DSR_STATUSES, required=True, default="on_schedule")
    consignment_id = fields.Many2one(
        "tms.consignment", string="LR (stop)", domain="[('trip_link_ids.trip_id', '=', trip_id)]",
        help="The LR whose loading or unloading stop this line times; detention is charged to its billing party "
             "(BR-CHG-09). Empty: the trip's first LR.")
    eta = fields.Datetime(string="ETA")
    remark = fields.Text()
    source = fields.Selection([("manual", "Control Room"), ("driver", "Driver call"), ("gps", "GPS")],
                              default="manual", required=True)
    logged_by_id = fields.Many2one("res.users", default=lambda s: s.env.user, readonly=True)

    @api.constrains("status", "remark", "trip_id")
    def _check_status(self):
        for dsr in self:
            if dsr.status.startswith("delayed") and not dsr.remark:
                raise ValidationError(_("A Delayed DSR status needs a remark (RL-03)."))
            if dsr.status in ODC_DSR and dsr.trip_id.tms_cargo_type != "odc":
                raise ValidationError(_("Halted statuses apply to ODC trips only (BR-EXE-09)."))

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        for dsr in lines:
            trip = dsr.trip_id
            if trip.state_code not in ("started", "in_transit"):
                raise UserError(_("DSR lines are logged on trips on the road."))
            if dsr.status == "departed":
                trip.action_depart()  # BR-EXE-04
            trip._tms_after_dsr(dsr)
        return lines

    def write(self, vals):
        raise UserError(_("DSR lines are a log and are not edited; log a new line instead."))

    def unlink(self):
        raise UserError(_("DSR lines are never deleted."))


class TmsTripBreakdown(models.Model):
    """BR-ASG-04..13."""

    _name = "tms.trip.breakdown"
    _description = "Trip breakdown"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "reported_at desc"

    name = fields.Char(compute="_compute_name", store=True)
    trip_id = fields.Many2one("tms.order", required=True, ondelete="cascade", index=True,
                              domain="[('state_code', 'in', ('started', 'in_transit'))]")
    company_id = fields.Many2one(related="trip_id.company_id", store=True)
    assignment_id = fields.Many2one("tms.trip.assignment", readonly=True)
    vehicle_id = fields.Many2one("fleet.vehicle", readonly=True, store=True, compute="_compute_vehicle")
    fleet_type = fields.Selection(related="assignment_id.fleet_type", store=True)
    reported_at = fields.Datetime(required=True, default=fields.Datetime.now, tracking=True)
    location = fields.Char(required=True)
    cause = fields.Selection(
        [("mechanical", "Mechanical"), ("tyre", "Tyre"), ("electrical", "Electrical"), ("accident", "Accident"),
         ("other", "Other")],
        required=True,
        default="mechanical",
    )
    description = fields.Text()
    odometer = fields.Float()
    resolution = fields.Selection(
        [("repair", "Repair and continue"), ("replace_own", "Replace with own vehicle"),
         ("replace_market", "Replace with market vehicle")],
        tracking=True,
    )
    resumed_at = fields.Datetime(tracking=True)
    downtime_hours = fields.Float(compute="_compute_downtime", store=True)
    repair_cost = fields.Monetary(currency_field="currency_id")
    currency_id = fields.Many2one(related="company_id.currency_id")
    cost_responsibility = fields.Selection(
        [("company", "Company"), ("vendor", "Vendor"), ("driver", "Driver"), ("third_party", "Third party")],
        default="company",
        required=True,
    )
    service_log_id = fields.Many2one("fleet.vehicle.log.services", readonly=True)
    incident_id = fields.Many2one("tms.trip.incident", readonly=True)
    replacement_assignment_id = fields.Many2one("tms.trip.assignment", readonly=True)
    state = fields.Selection([("open", "Open"), ("resolved", "Resolved"), ("cancel", "Cancelled")], default="open",
                             tracking=True)

    @api.depends("trip_id", "reported_at")
    def _compute_name(self):
        for bd in self:
            bd.name = _("Breakdown %(t)s %(d)s", t=bd.trip_id.name or "", d=bd.reported_at or "")

    @api.depends("assignment_id")
    def _compute_vehicle(self):
        for bd in self:
            bd.vehicle_id = bd.assignment_id.vehicle_id

    @api.depends("reported_at", "resumed_at")
    def _compute_downtime(self):
        for bd in self:
            bd.downtime_hours = ((bd.resumed_at - bd.reported_at).total_seconds() / 3600.0
                                 if bd.resumed_at and bd.reported_at else 0.0)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            trip = self.env["tms.order"].browse(vals["trip_id"])
            if trip.state_code not in ("started", "in_transit"):
                raise UserError(_("Breakdowns are reported on trips on the road."))
            vals["assignment_id"] = trip.open_assignment_id.id
        records = super().create(vals_list)
        category = self.env.ref("tms_core.tms_category_vehicle_breakdown")
        for bd in records:
            # BR-ASG-12
            bd.incident_id = self.env["tms.trip.incident"].create({
                "trip_id": bd.trip_id.id,
                "category_id": category.id,
                "occurred_at": bd.reported_at,
                "location": bd.location,
                "description": bd.description or dict(bd._fields["cause"].selection).get(bd.cause),
                "breakdown_id": bd.id,
            })
            # BR-ASG-09
            if bd.vehicle_id and bd.vehicle_id.tms_ownership == "owned":
                service_type = self.env.ref("tms_core.fleet_service_type_breakdown")
                bd.service_log_id = self.env["fleet.vehicle.log.services"].create({
                    "vehicle_id": bd.vehicle_id.id,
                    "service_type_id": service_type.id,
                    "description": _("Breakdown on %s", bd.trip_id.name),
                    "date": fields.Date.context_today(self),
                    "state": "running",
                    "tms_breakdown_id": bd.id,
                    "odometer": bd.odometer or 0.0,
                })
            # BR-NTF-08
            users = users_in_group(self.env, "tms_core.group_branch_manager", bd.company_id) | users_in_group(
                self.env, "tms_core.group_fleet_manager", bd.company_id)
            schedule(bd, users, _("Breakdown reported on %s", bd.trip_id.name), bd.description)
            bd._tms_email_users(users, _("Breakdown reported on %s", bd.trip_id.name),
                                _("%(cause)s at %(loc)s. %(desc)s", cause=dict(bd._fields["cause"].selection)
                                  .get(bd.cause), loc=bd.location, desc=bd.description or ""))
            bd.trip_id.message_post(body=_("Breakdown reported at %s", bd.location))
            # WF-04: the trip stops; a Delayed - Breakdown DSR line is logged and departure is needed again,
            # blocked until the Part-B tasks of any replacement hold evidence (BR-EWB-05)
            self.env["tms.trip.dsr"].sudo().create({
                "trip_id": bd.trip_id.id,
                "location": bd.location,
                "status": "delayed_breakdown",
                "logged_at": bd.reported_at,
                "remark": _("Breakdown: %s", bd.description or dict(bd._fields["cause"].selection).get(bd.cause)),
            })
            if bd.trip_id.state_code == "in_transit":
                bd.trip_id._tms_move_stage("started")
        return records

    def _tms_email_users(self, users, subject, body):
        """BR-NTF-08: email beside the activity."""
        partners = users.mapped("partner_id").filtered("email")
        if partners:
            self.message_post(subject=subject, body=body, partner_ids=partners.ids,
                              message_type="comment", subtype_xmlid="mail.mt_comment")

    def action_resolve(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Resolve breakdown"),
            "res_model": "tms.breakdown.resolve.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_breakdown_id": self.id},
        }

    def _tms_close(self, resumed_at=None):
        for bd in self:
            bd.write({"resumed_at": resumed_at or fields.Datetime.now(), "state": "resolved"})
            if bd.service_log_id:
                bd.service_log_id.write({"state": "done", "tms_downtime_hours": bd.downtime_hours,
                                         "amount": bd.repair_cost})
            if bd.incident_id and bd.incident_id.state == "open" and not bd.incident_id.moves_money:
                bd.incident_id.state = "resolved"


class TmsTripIncident(models.Model):
    """BR-INC-01..05: incidents record events; they never carry money themselves."""

    _name = "tms.trip.incident"
    _description = "Trip incident"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "occurred_at desc"

    name = fields.Char(compute="_compute_name", store=True)
    trip_id = fields.Many2one("tms.order", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="trip_id.company_id", store=True)
    consignment_id = fields.Many2one("tms.consignment", string="LR")
    category_id = fields.Many2one("tms.category", string="Type", required=True, domain=[("use_incident", "=", True)])
    occurred_at = fields.Datetime(required=True, default=fields.Datetime.now)
    location = fields.Char()
    description = fields.Text()
    responsible = fields.Selection(
        [("company", "Company"), ("customer", "Customer"), ("vendor", "Vendor"), ("driver", "Driver"),
         ("third_party", "Third party")],
        string="Responsible party",
    )
    responsible_partner_id = fields.Many2one("res.partner")
    estimated_amount = fields.Monetary(currency_field="currency_id",
                                       help="For the record only; no ledger or P&L reads it (BR-INC-03).")
    currency_id = fields.Many2one(related="company_id.currency_id")
    moves_money = fields.Boolean(compute="_compute_moves_money", store=True, readonly=False)
    reported_by_id = fields.Many2one("res.users", default=lambda s: s.env.user, readonly=True)
    state = fields.Selection([("open", "Open"), ("resolved", "Resolved"), ("cancel", "Cancelled")], default="open",
                             tracking=True)
    expense_ids = fields.One2many("tms.trip.expense", "incident_id")
    charge_ids = fields.One2many("tms.trip.charge", "incident_id")
    claim_id = fields.Many2one("tms.damage.claim")
    breakdown_id = fields.Many2one("tms.trip.breakdown")

    _sql_constraints = [("claim_uniq", "unique(claim_id)", "A claim belongs to one incident (BR-INC-05).")]

    @api.depends("category_id", "trip_id")
    def _compute_name(self):
        for inc in self:
            inc.name = "%s / %s" % (inc.trip_id.name or "", inc.category_id.name or "")

    @api.depends("category_id")
    def _compute_moves_money(self):
        for inc in self:
            inc.moves_money = inc.category_id.incident_moves_money

    def action_resolve(self):
        """BR-INC-04, RL-04: an incident that moves money resolves only with its realising document."""
        for inc in self:
            if inc.moves_money and not (inc.expense_ids or inc.charge_ids or inc.claim_id):
                raise UserError(_("Link or create the expense, charge or claim that realises this incident first "
                                  "(BR-INC-04)."))
            if inc.category_id.code in ("DAMAGE", "SHORTAGE") and not inc.claim_id:
                raise UserError(_("Damage and Shortage incidents link to exactly one claim (BR-INC-05)."))
            inc.state = "resolved"
        return True

    def action_cancel(self):
        self.write({"state": "cancel"})

    def action_create_expense(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "res_model": "tms.trip.expense", "view_mode": "form",
                "context": {"default_trip_id": self.trip_id.id, "default_incident_id": self.id,
                            "default_consignment_id": self.consignment_id.id}}

    def action_create_charge(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "res_model": "tms.trip.charge", "view_mode": "form",
                "context": {"default_trip_id": self.trip_id.id, "default_incident_id": self.id,
                            "default_consignment_id": self.consignment_id.id}}

    def action_create_claim(self):
        self.ensure_one()
        if self.claim_id:
            return {"type": "ir.actions.act_window", "res_model": "tms.damage.claim", "res_id": self.claim_id.id,
                    "view_mode": "form"}
        claim = self.env["tms.damage.claim"].create({
            "consignment_id": self.consignment_id.id,
            "trip_id": self.trip_id.id,
            "incident_id": self.id,
            "damage_type": "shortage" if self.category_id.code == "SHORTAGE" else "damaged_goods",
            "claimant_id": self.consignment_id.billing_partner_id.id,
        })
        self.claim_id = claim
        return {"type": "ir.actions.act_window", "res_model": "tms.damage.claim", "res_id": claim.id,
                "view_mode": "form"}
