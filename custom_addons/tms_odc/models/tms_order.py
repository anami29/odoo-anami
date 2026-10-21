import re
from datetime import timedelta

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from odoo.addons.tms_core.models.tools import schedule, users_in_group

EQUIPMENT_KINDS = [("puller", "Puller"), ("axle", "Axle line"), ("trailer_bed", "Trailer bed"),
                   ("escort", "Escort")]
DEFAULT_TZ = "Asia/Kolkata"


def _plate(value):
    return re.sub(r"[^A-Z0-9]", "", (value or "").upper())


def _float_hours(value):
    hours = int(value)
    return "%02d:%02d" % (hours, round((value - hours) * 60))


class TmsTripEquipment(models.Model):
    """BR-ODC-05: equipment lines copied from the estimate and filled by owned assets or PO items."""

    _name = "tms.trip.equipment"
    _description = "ODC trip equipment"
    _order = "trip_id, sequence, id"

    trip_id = fields.Many2one("tms.order", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="trip_id.company_id", store=True, string="Branch")
    trip_state = fields.Selection(related="trip_id.state_code")
    sequence = fields.Integer(default=10)
    estimate_line_id = fields.Many2one("tms.estimate.line", string="Estimate line", readonly=True)
    kind = fields.Selection(EQUIPMENT_KINDS, string="Planned type", required=True)
    required_count = fields.Float(string="Planned count", readonly=True)
    actual_kind = fields.Selection(EQUIPMENT_KINDS, string="Type used")
    actual_count = fields.Float(string="Count used")
    source = fields.Selection([("owned", "Owned asset"), ("purchase", "Purchase order item")], default="owned",
                              required=True)
    vehicle_id = fields.Many2one("fleet.vehicle", string="Owned asset", domain=[("tms_ownership", "=", "owned")])
    asset_ref = fields.Char(string="Asset reference", help="Owned axle set or trailer bed without a vehicle record.")
    purchase_line_id = fields.Many2one("purchase.order.line", string="PO item",
                                       domain=[("order_id.state", "in", ("purchase", "done"))])
    registration = fields.Char(string="Registration", help="Number of a hired puller or escort vehicle.")
    filled = fields.Boolean(compute="_compute_filled")
    substituted = fields.Boolean(compute="_compute_filled", string="Different type or count")
    substitution_reason = fields.Char(copy=False)
    approved_by_id = fields.Many2one("res.users", string="Approved by", readonly=True, copy=False)
    approved_on = fields.Datetime(readonly=True, copy=False)

    @api.depends("source", "vehicle_id", "asset_ref", "purchase_line_id.order_id.state", "kind", "actual_kind",
                 "required_count", "actual_count")
    def _compute_filled(self):
        for line in self:
            if line.source == "owned":
                line.filled = bool(line.vehicle_id or line.asset_ref)
            else:
                line.filled = bool(line.purchase_line_id and line.purchase_line_id.order_id.state in
                                   ("purchase", "done"))
            line.substituted = (line.actual_kind or line.kind) != line.kind or \
                abs(line.actual_count - line.required_count) > 0.001

    @api.onchange("vehicle_id")
    def _onchange_vehicle(self):
        if self.vehicle_id:
            self.registration = self.vehicle_id.license_plate

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            vals.setdefault("actual_kind", vals.get("kind"))
            vals.setdefault("actual_count", vals.get("required_count", 0.0))
        lines = super().create(vals_list)
        lines._tms_check_trip_open()
        return lines

    def write(self, vals):
        self._tms_check_trip_open()
        if {"actual_kind", "actual_count", "kind"} & set(vals) and not self.env.context.get("tms_odc_keep_approval"):
            vals = dict(vals, approved_by_id=False, approved_on=False)
        return super().write(vals)

    def unlink(self):
        self._tms_check_trip_open()
        if self.filtered("estimate_line_id") and not self.env.context.get("tms_odc_reload"):
            raise UserError(_("Equipment lines from the estimate are kept; record a different type or count with "
                              "the Operations Head's approval instead (BR-ODC-05)."))
        return super().unlink()

    def _tms_check_trip_open(self):
        if any(line.trip_id.state_code not in ("draft", "confirmed") for line in self):
            raise UserError(_("Equipment lines change only before the trip starts."))

    def action_approve_substitution(self):
        """BR-ODC-05: a different type or count needs the Operations Head's approval with a reason."""
        if not self.env.user.has_group("tms_core.group_operations_head"):
            raise UserError(_("The Operations Head approves ODC equipment substitutions (BR-ODC-05)."))
        for line in self.filtered("substituted"):
            if not line.substitution_reason:
                raise UserError(_("Give the reason for the substitution first."))
            line.with_context(tms_odc_keep_approval=True).write(
                {"approved_by_id": self.env.user.id, "approved_on": fields.Datetime.now()})
            kinds = dict(EQUIPMENT_KINDS)
            self.env["tms.override.log"].log(
                "equipment", line.trip_id,
                _("%(k1)s × %(c1)s planned, %(k2)s × %(c2)s used", k1=kinds[line.kind], c1=line.required_count,
                  k2=kinds[line.actual_kind or line.kind], c2=line.actual_count),
                reason=line.substitution_reason)
        return True


class TmsOdcPermit(models.Model):
    """BR-ODC-06: a permit per state with validity, puller, movement window and escort."""

    _name = "tms.odc.permit"
    _description = "ODC permit"
    _inherit = ["mail.thread"]
    _order = "trip_id, valid_from, id"

    name = fields.Char(string="Permit number", required=True, tracking=True)
    trip_id = fields.Many2one("tms.order", required=True, ondelete="cascade", index=True,
                              domain=[("tms_cargo_type", "=", "odc")])
    company_id = fields.Many2one(related="trip_id.company_id", store=True, string="Branch")
    state_id = fields.Many2one("res.country.state", string="State", required=True, tracking=True)
    authority = fields.Char(string="Issuing authority")
    valid_from = fields.Date(required=True, tracking=True)
    valid_to = fields.Date(required=True, tracking=True)
    vehicle_id = fields.Many2one("fleet.vehicle", string="Puller")
    puller_plate = fields.Char(string="Puller number", required=True, tracking=True)
    window_from = fields.Float(string="Movement from", default=0.0, help="Local time, hours.")
    window_to = fields.Float(string="Movement to", default=24.0, help="Local time, hours; before From means overnight.")
    escort_required = fields.Boolean(tracking=True)
    escort_count = fields.Integer(default=1)
    file = fields.Binary(attachment=True)
    file_name = fields.Char()

    @api.onchange("vehicle_id", "trip_id")
    def _onchange_puller(self):
        if self.vehicle_id:
            self.puller_plate = self.vehicle_id.license_plate
        elif self.trip_id and not self.puller_plate:
            self.puller_plate = self.trip_id.odc_puller_plate

    @api.constrains("valid_from", "valid_to", "window_from", "window_to", "escort_count")
    def _check_values(self):
        for permit in self:
            if permit.valid_to < permit.valid_from:
                raise ValidationError(_("A permit ends after it starts."))
            if not (0 <= permit.window_from <= 24 and 0 <= permit.window_to <= 24):
                raise ValidationError(_("Movement window hours run from 0 to 24."))
            if permit.escort_required and permit.escort_count < 1:
                raise ValidationError(_("A required escort counts at least one vehicle."))

    @api.depends("name", "state_id")
    def _compute_display_name(self):
        for permit in self:
            permit.display_name = "%s · %s" % (permit.name or "", permit.state_id.name or "")

    def _tms_window_label(self):
        self.ensure_one()
        if self._tms_any_time():
            return _("Any time")
        return "%s–%s" % (_float_hours(self.window_from), _float_hours(self.window_to))

    def _tms_any_time(self):
        return self.window_from == self.window_to or (self.window_from <= 0 and self.window_to >= 24)

    def _tms_in_window(self, hour):
        self.ensure_one()
        if self._tms_any_time():
            return True
        if self.window_from < self.window_to:
            return self.window_from <= hour < self.window_to
        return hour >= self.window_from or hour < self.window_to


class TmsTripConsignment(models.Model):
    _inherit = "tms.trip.consignment"

    @api.model_create_multi
    def create(self, vals_list):
        """BR-ODC-01: Cargo Type is copied to the trip."""
        links = super().create(vals_list)
        for link in links:
            trip = link.trip_id
            if link.consignment_id.tms_cargo_type == "odc" and trip.tms_cargo_type != "odc" \
                    and trip.state_code in ("draft", False):
                trip.tms_cargo_type = "odc"
        return links


class TmsOrder(models.Model):
    _inherit = "tms.order"

    equipment_ids = fields.One2many("tms.trip.equipment", "trip_id", string="ODC equipment")
    permit_ids = fields.One2many("tms.odc.permit", "trip_id", string="ODC permits")
    odc_state_ids = fields.Many2many("res.country.state", "tms_order_odc_state_rel", "trip_id", "state_id",
                                     string="States on the route", help="From the estimates' route by state.")
    odc_puller_plate = fields.Char(compute="_compute_odc_puller_plate", string="Puller")
    survey_file = fields.Binary(string="Route survey", attachment=True, copy=False)
    survey_file_name = fields.Char(copy=False)
    survey_date = fields.Date(string="Survey date", copy=False)
    survey_by = fields.Char(string="Surveyed by", copy=False)
    survey_approved_by_id = fields.Many2one("res.users", string="Survey approved by", readonly=True, copy=False,
                                            tracking=True)
    survey_approved_on = fields.Datetime(readonly=True, copy=False)
    survey_approved = fields.Boolean(compute="_compute_survey_approved")
    odc_start_gaps = fields.Text(compute="_compute_odc_start_gaps", string="ODC gaps")

    @api.depends("equipment_ids.actual_kind", "equipment_ids.vehicle_id", "equipment_ids.registration",
                 "planned_vehicle_id", "planned_market_vehicle_id", "fleet_type")
    def _compute_odc_puller_plate(self):
        for trip in self:
            puller = trip.equipment_ids.filtered(lambda e: (e.actual_kind or e.kind) == "puller")[:1]
            plate = puller.vehicle_id.license_plate or puller.registration
            if not plate:
                plate = trip.planned_vehicle_id.license_plate if trip.fleet_type == "owned" \
                    else trip.planned_market_vehicle_id.name
            trip.odc_puller_plate = plate or False

    @api.depends("survey_approved_by_id")
    def _compute_survey_approved(self):
        for trip in self:
            trip.survey_approved = bool(trip.survey_approved_by_id)

    @api.depends("tms_cargo_type", "state_code", "permit_ids", "equipment_ids", "odc_state_ids",
                 "survey_approved_by_id", "scheduled_date_start", "scheduled_date_end")
    def _compute_odc_start_gaps(self):
        for trip in self:
            gaps = trip._tms_odc_start_gaps() if trip.tms_cargo_type == "odc" and trip.state_code in (
                "draft", "confirmed") else []
            trip.odc_start_gaps = "\n".join(gaps) or False

    def _compute_payload(self):
        """An ODC load rides on its axles or beds: their rated capacity is the payload (BR-TRP-05)."""
        res = super()._compute_payload()
        for trip in self.filtered(lambda t: t.tms_cargo_type == "odc"):
            carriers = trip.equipment_ids.filtered(lambda e: (e.actual_kind or e.kind) in ("axle", "trailer_bed"))
            capacity = sum(e.actual_count * e.estimate_line_id.product_id.tms_rated_capacity for e in carriers)
            if capacity:
                trip.payload_kg = capacity * 1000.0
        return res

    @api.constrains("tms_cargo_type")
    def _check_odc_enabled(self):
        for trip in self:
            if trip.tms_cargo_type == "odc" and not trip.company_id.tms_odc_enabled:
                raise ValidationError(_("ODC is switched off for %s (BR-CFG-13).", trip.company_id.name))

    def write(self, vals):
        if "survey_file" in vals and not self.env.context.get("tms_odc_survey"):
            vals = dict(vals, survey_approved_by_id=False, survey_approved_on=False)
        return super().write(vals)

    # ------------------------------------------------------------------
    # estimate → trip
    # ------------------------------------------------------------------
    def _tms_odc_estimates(self):
        self.ensure_one()
        lrs = self.consignment_link_ids.filtered(lambda x: x.state != "cancel").mapped("consignment_id")
        return lrs.mapped("sale_line_id.tms_estimate_id")

    def action_load_odc_from_estimate(self):
        """BR-ODC-05: equipment lines come from the estimate's Puller, Axle, Trailer bed and Escort lines."""
        for trip in self:
            if trip.state_code not in ("draft", False):
                raise UserError(_("Equipment loads into draft trips."))
            estimates = trip._tms_odc_estimates()
            loaded = trip.equipment_ids.mapped("estimate_line_id")
            kinds = dict(EQUIPMENT_KINDS)
            vals_list = []
            for eline in estimates.mapped("line_ids").sorted(lambda x: (x.estimate_id.id, x.line_no)):
                kind = eline.product_id.tms_equipment_kind
                if kind not in kinds or eline in loaded:
                    continue
                vals = {"trip_id": trip.id, "estimate_line_id": eline.id, "kind": kind,
                        "required_count": eline.units or 1.0, "sequence": eline.line_no}
                if kind == "puller" and trip.fleet_type == "owned" and trip.planned_vehicle_id:
                    vals.update({"source": "owned", "vehicle_id": trip.planned_vehicle_id.id,
                                 "registration": trip.planned_vehicle_id.license_plate})
                vals_list.append(vals)
            if vals_list:
                self.env["tms.trip.equipment"].create(vals_list)
            if not trip.odc_state_ids:
                trip.odc_state_ids = estimates.mapped("route_ids.state_id")
        return True

    def _tms_odc_equipment_gaps(self):
        self.ensure_one()
        gaps = []
        lines = self.equipment_ids
        if not lines:
            gaps.append(_("No equipment lines: load them from the estimate."))
        kinds = dict(EQUIPMENT_KINDS)
        for line in lines.filtered(lambda x: not x.filled):
            gaps.append(_("%(k)s × %(c)s is not filled by an owned asset or a confirmed PO item.",
                          k=kinds[line.actual_kind or line.kind], c=line.actual_count))
        for line in lines.filtered(lambda x: x.substituted and not x.approved_by_id):
            gaps.append(_("%s: the different type or count needs the Operations Head's approval.",
                          kinds[line.kind]))
        pullers = lines.filtered(lambda x: (x.actual_kind or x.kind) == "puller")
        if lines and not pullers:
            gaps.append(_("No puller line."))
        if self.fleet_type == "owned" and pullers and self.planned_vehicle_id \
                and pullers[:1].vehicle_id != self.planned_vehicle_id:
            gaps.append(_("The puller must be the trip's vehicle %s, so that its costs name it.",
                          self.planned_vehicle_id.license_plate))
        return gaps

    def _tms_check_confirm(self):
        if self.tms_cargo_type == "odc" and not self.equipment_ids:
            self.action_load_odc_from_estimate()  # before the payload check, which reads the axles
        return super()._tms_check_confirm()

    def _tms_check_confirm_odc(self):
        res = super()._tms_check_confirm_odc()
        lrs = self.consignment_link_ids.filtered(lambda x: x.state != "cancel").mapped("consignment_id")
        if self.tms_cargo_type != "odc":
            if "odc" in lrs.mapped("tms_cargo_type"):
                raise UserError(_("A trip carrying an ODC LR is an ODC trip (BR-ODC-01)."))
            return res
        if not self.equipment_ids:
            self.action_load_odc_from_estimate()
        gaps = self._tms_odc_equipment_gaps()
        if gaps:
            raise UserError(_("ODC trip %(t)s cannot be confirmed (BR-ODC-05):\n%(g)s", t=self.name,
                              g="\n".join(gaps)))
        return res

    # ------------------------------------------------------------------
    # START (BR-ODC-06)
    # ------------------------------------------------------------------
    def _tms_trip_dates(self):
        start = self.scheduled_date_start or fields.Datetime.now()
        end = self.scheduled_date_end or start
        tz = pytz.timezone(self.env.user.tz or DEFAULT_TZ)
        return (pytz.utc.localize(start).astimezone(tz).date(), pytz.utc.localize(end).astimezone(tz).date())

    def _tms_odc_start_gaps(self):
        self.ensure_one()
        gaps = self._tms_odc_equipment_gaps()
        if not self.odc_state_ids:
            gaps.append(_("Record the states on the route."))
        start, end = self._tms_trip_dates()
        puller = _plate(self.odc_puller_plate)
        missing, wrong_dates, wrong_puller = [], [], []
        for state in self.odc_state_ids:
            permits = self.permit_ids.filtered(lambda p, s=state: p.state_id == s)
            if not permits:
                missing.append(state.name)
                continue
            dated = permits.filtered(lambda p: p.valid_from <= start and p.valid_to >= end)
            if not dated:
                wrong_dates.append(state.name)
            elif not dated.filtered(lambda p: _plate(p.puller_plate) == puller):
                wrong_puller.append(state.name)
        if missing:
            gaps.append(_("No permit for %s.", ", ".join(missing)))
        if wrong_dates:
            gaps.append(_("The permit for %(s)s does not cover the trip dates %(a)s to %(b)s.",
                          s=", ".join(wrong_dates), a=start, b=end))
        if wrong_puller:
            gaps.append(_("The permit for %(s)s names another puller than %(p)s.", s=", ".join(wrong_puller),
                          p=self.odc_puller_plate or "-"))
        if not self.survey_approved_by_id:
            gaps.append(_("The route survey is not approved."))
        needed = max([p.escort_count for p in self.permit_ids if p.escort_required] or [0])
        escorts = self.equipment_ids.filtered(lambda e: (e.actual_kind or e.kind) == "escort" and e.filled)
        if needed and sum(escorts.mapped("actual_count")) < needed:
            gaps.append(_("The permits require %(n)s escort vehicle(s); %(h)s are filled.", n=needed,
                          h=int(sum(escorts.mapped("actual_count")))))
        lrs = self.consignment_link_ids.filtered(lambda x: x.state != "cancel").mapped("consignment_id")
        bills = lrs.filtered(lambda lr: lr.tms_cargo_type == "odc").mapped("ewaybill_ids").filtered(
            lambda b: not b.is_odc and b.state != "cancelled")
        if bills:
            gaps.append(_("E-way bills not generated as ODC: %s.", ", ".join(bills.mapped("name"))))
        return gaps

    def _tms_check_start_odc(self):
        """No override: the gate applies even with compliance enforcement off."""
        res = super()._tms_check_start_odc()
        if self.tms_cargo_type == "odc":
            gaps = self._tms_odc_start_gaps()
            if gaps:
                raise UserError(_("ODC trip %(t)s cannot start (BR-ODC-06):\n%(g)s", t=self.name,
                                  g="\n".join(gaps)))
        return res

    def action_approve_route_survey(self):
        for trip in self:
            if not (self.env.user.has_group("tms_core.group_operations_head")
                    or self.env.user.has_group("tms_core.group_fleet_manager")):
                raise UserError(_("The Fleet Manager or the Operations Head approves the route survey."))
            if not trip.survey_file:
                raise UserError(_("Attach the route survey first."))
            trip.with_context(tms_odc_survey=True).write({"survey_approved_by_id": self.env.user.id,
                                                          "survey_approved_on": fields.Datetime.now()})
            trip.message_post(body=_("Route survey approved."))
        return True

    # ------------------------------------------------------------------
    # execution (BR-ODC-07, BR-NTF-04)
    # ------------------------------------------------------------------
    def _tms_after_dsr(self, dsr):
        res = super()._tms_after_dsr(dsr)
        if self.tms_cargo_type == "odc":
            if dsr.status == "departed":
                self._tms_odc_check_window(dsr)
            if dsr.eta:
                self._tms_odc_eta_alert()
        return res

    def _tms_odc_check_window(self, dsr):
        """A Departed status outside a permit's movement window warns."""
        tz = pytz.timezone(self.env.user.tz or DEFAULT_TZ)
        local = pytz.utc.localize(dsr.logged_at).astimezone(tz)
        hour = local.hour + local.minute / 60.0
        outside = self.permit_ids.filtered(lambda p: not p._tms_in_window(hour))
        if outside:
            note = _("Departed at %(t)s, outside the movement window of %(p)s (BR-ODC-07).",
                     t=local.strftime("%H:%M"),
                     p=", ".join("%s %s" % (p.state_id.name, p._tms_window_label()) for p in outside))
            self.message_post(body=note)
            users = self.user_id or users_in_group(self.env, "tms_core.group_dispatcher", self.company_id)
            schedule(self, users, _("Departed outside the ODC movement window"), note)
        return bool(outside)

    def _tms_odc_live_bills(self):
        self.ensure_one()
        lrs = self.consignment_link_ids.filtered(lambda x: x.state != "cancel" and not x.outcome).mapped(
            "consignment_id")
        return lrs.mapped("ewaybill_ids").filtered(lambda b: b.state in ("active", "extended") and b.valid_until)

    def _tms_odc_eta_alert(self):
        """BR-ODC-07, BR-NTF-04: the latest ETA within a day of the e-way bill's expiry."""
        alerted = self.browse()
        for trip in self.filtered(lambda t: t.tms_cargo_type == "odc"):
            latest = trip.dsr_ids.filtered("eta").sorted(lambda d: (d.logged_at, d.id))[-1:]
            if not latest:
                continue
            for bill in trip._tms_odc_live_bills():
                if latest.eta < bill.valid_until - timedelta(days=1):
                    continue
                summary = _("ETA within a day of e-way bill %s expiry", bill.name)
                note = _("Latest ETA %(e)s; e-way bill %(b)s valid until %(v)s. Extend it (BR-NTF-04).",
                         e=latest.eta, b=bill.name, v=bill.valid_until)
                users = trip.user_id or users_in_group(self.env, "tms_core.group_dispatcher", trip.company_id)
                if not trip.activity_ids.filtered(lambda a, s=summary: a.summary == s):
                    trip.sudo().message_notify(partner_ids=users.mapped("partner_id").ids, subject=summary,
                                               body=note)
                schedule(trip, users, summary, note)
                alerted |= trip
        return alerted

    @api.model
    def _cron_odc_eta_alert(self):
        trips = self.search([("tms_cargo_type", "=", "odc"), ("state_code", "in", ("started", "in_transit"))])
        return trips._tms_odc_eta_alert()
