# Copyright 2026 RLFB (Riamona Luxury and Fashion Brands), https://www.riamona.com
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
"""One dialog on the trip that reports a breakdown and resolves it: repair and continue, or replace the
vehicle with a market or an own vehicle, with a live preview of the replacement's compliance gate."""
from datetime import timedelta

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools.misc import format_date, format_datetime

from ..models.tools import inr

CAUSES = [
    ("mechanical", "Mechanical failure"),
    ("tyre", "Tyre burst"),
    ("electrical", "Electrical"),
    ("accident", "Accident"),
    ("other", "Other"),
]
# same keys and order as the breakdown's resolution; the dialog lists market first (see fields_get)
RESOLUTIONS = [
    ("repair", "Repair and continue"),
    ("replace_own", "Own replacement"),
    ("replace_market", "Market replacement"),
]
RESOLUTION_ORDER = ("replace_market", "replace_own", "repair")
BADGE = {"ok": "text-bg-success", "warn": "text-bg-warning", "fail": "text-bg-danger"}


class TmsTripBreakdown(models.Model):
    _inherit = "tms.trip.breakdown"

    cause = fields.Selection(selection=CAUSES)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("name"):
                vals["name"] = self.env["ir.sequence"].next_by_code("tms.trip.breakdown") or False
        return super().create(vals_list)

    @api.depends("trip_id", "reported_at")
    def _compute_name(self):
        numbered = self.filtered(lambda b: (b.name or "").startswith("BD/"))
        for bd in numbered:
            bd.name = bd.name  # the BD/ number never changes
        super(TmsTripBreakdown, self - numbered)._compute_name()


class TmsOrder(models.Model):
    _inherit = "tms.order"

    def action_report_breakdown_wizard(self):
        self.ensure_one()
        if self.state_code not in ("started", "in_transit"):
            raise UserError(_("Breakdowns are reported on trips on the road."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Report breakdown · %s", self.name),
            "res_model": "tms.breakdown.report.wizard",
            "view_mode": "form",
            "views": [(self.env.ref("tms_core.tms_breakdown_report_wizard_form").id, "form")],
            "target": "new",
            "context": {"default_trip_id": self.id},
        }


class TmsBreakdownReportWizard(models.TransientModel):
    """Report and resolve a breakdown in one step; the resolution runs the resolve wizard's own code."""

    _name = "tms.breakdown.report.wizard"
    _inherit = "tms.breakdown.resolve.wizard"
    _description = "Report breakdown"

    trip_id = fields.Many2one("tms.order", related=False, required=True, readonly=False, store=True,
                              ondelete="cascade")
    breakdown_id = fields.Many2one("tms.trip.breakdown", required=False, readonly=True)
    reported_at = fields.Datetime(string="Breakdown at", required=True, default=fields.Datetime.now)
    location = fields.Char(required=True)
    cause = fields.Selection(CAUSES, required=True, default="mechanical")
    description = fields.Text()
    current_assignment_id = fields.Many2one("tms.trip.assignment", compute="_compute_current")
    current_fleet_type = fields.Selection([("owned", "Owned"), ("market", "Market")], compute="_compute_current")
    current_vehicle_id = fields.Many2one("fleet.vehicle", compute="_compute_current", string="Current vehicle")
    current_vehicle_driver = fields.Char(compute="_compute_current", string="Vehicle / driver")
    is_broker_mode = fields.Boolean(compute="_compute_current")
    repair_cost = fields.Float(string="Towing + repair cost")
    resolution = fields.Selection(RESOLUTIONS, string="Resolution", required=True, default="replace_market")
    resumed_at = fields.Datetime(string="Available from")
    market_vehicle_id = fields.Many2one("tms.market.vehicle", string="Market vehicle")
    market_driver_name = fields.Char(string="Driver name")
    market_driver_phone = fields.Char(string="Driver phone")
    market_driver_licence = fields.Char(string="Driver licence no.")
    market_driver_licence_expiry = fields.Date(string="Licence valid until")
    hire_amount = fields.Float(string="Agreed hire")
    vehicle_id = fields.Many2one("fleet.vehicle", string="Vehicle",
                                 domain="[('tms_ownership', '=', 'owned'), ('id', '!=', current_vehicle_id)]")
    driver_id = fields.Many2one("tms.driver", string="Driver")
    # previews
    gate_preview = fields.Html(compute="_compute_preview", sanitize=False, string="Replacement compliance")
    gate_fail = fields.Boolean(compute="_compute_preview")
    gate_message = fields.Text(compute="_compute_preview")
    gate_note = fields.Char(compute="_compute_preview")
    partb_vehicle = fields.Char(compute="_compute_preview", string="Part-B vehicle")
    ewaybill_preview = fields.Html(compute="_compute_preview", sanitize=False, string="Consignments on board")
    creates_summary = fields.Char(compute="_compute_preview")

    # ------------------------------------------------------------------
    @api.model
    def fields_get(self, allfields=None, attributes=None):
        """Market replacement first; broker mode runs no own fleet, so Own replacement is not offered."""
        res = super().fields_get(allfields, attributes)
        if res.get("resolution", {}).get("selection"):
            broker = self.env.company.tms_operation_mode == "broker"
            options = dict((k, v) for k, v in res["resolution"]["selection"])
            res["resolution"]["selection"] = [
                (k, options[k]) for k in RESOLUTION_ORDER if k in options and not (broker and k == "replace_own")]
        return res

    @api.depends("trip_id")
    def _compute_current(self):
        for wiz in self:
            asg = wiz.trip_id.open_assignment_id
            wiz.current_assignment_id = asg
            wiz.current_fleet_type = asg.fleet_type or False
            wiz.current_vehicle_id = asg.vehicle_id
            label = asg.vehicle_number or asg.vehicle_id.license_plate or asg.vehicle_id.name or ""
            if label and asg.fleet_type:
                label = "%s (%s)" % (label, dict(asg._fields["fleet_type"].selection).get(asg.fleet_type).lower())
            driver = asg.driver_id.name or asg.market_driver_name or ""
            wiz.current_vehicle_driver = " · ".join(p for p in (label, driver) if p) or False
            wiz.is_broker_mode = (wiz.trip_id.company_id or self.env.company).tms_operation_mode == "broker"

    def _tms_replacement_label(self):
        self.ensure_one()
        if self.resolution == "replace_market":
            return self.market_vehicle_id.name or ""
        if self.resolution == "replace_own":
            return self.vehicle_id.license_plate or self.vehicle_id.name or ""
        return ""

    def _tms_live_ewaybills(self):
        self.ensure_one()
        links = self.trip_id.consignment_link_ids.filtered(lambda x: x.state != "cancel" and not x.outcome)
        return links.mapped("consignment_id.ewaybill_ids")._is_live()

    def _tms_gate_check(self):
        """The replacement's compliance gate, evaluated exactly as opening the assignment does, without
        writing anything. Returns rows (document, holder, valid until, result, level), the failures, the
        blocking flag and the evaluator's result."""
        self.ensure_one()
        trip = self.trip_id
        rows, failures = [], []
        if self.resolution == "repair" or not trip:
            return {"rows": rows, "failures": failures, "blocking": False, "result": "pass", "chosen": False}
        market = self.market_vehicle_id if self.resolution == "replace_market" else self.env["tms.market.vehicle"]
        vehicle = self.vehicle_id if self.resolution == "replace_own" else self.env["fleet.vehicle"]
        driver = self.driver_id if self.resolution == "replace_own" else self.env["tms.driver"]
        if not (market or vehicle):
            return {"rows": rows, "failures": failures, "blocking": False, "result": "pass", "chosen": False}
        Gate = self.env["tms.compliance.gate"]
        result, failures, warnings, snapshot = Gate.evaluate(
            trip.scheduled_date_end, vehicle=vehicle or None, driver=driver or None,
            market_vehicle=market or None, market_docs=trip.market_document_ids if market else None)
        failures = list(failures)
        end = fields.Date.to_date(trip.scheduled_date_end) if trip.scheduled_date_end else fields.Date.today()
        if market and not driver:
            # the market driver's licence captured on the assignment, as the assignment checks it
            exp = self.market_driver_licence_expiry
            if not self.market_driver_licence or not exp or exp < end:
                failures.append(_("Market driver licence missing or expiring before the scheduled end"))
                result = "fail"
            snapshot.append("Market driver | DL | %s | valid until %s" % (self.market_driver_licence, exp))
        icp = self.env["ir.config_parameter"].sudo()
        driver_days = int(icp.get_param("tms.default_driver_license_security_days") or 0)
        vehicle_days = int(icp.get_param("tms.default_vehicle_insurance_security_days") or 0)
        holders = {}
        if vehicle:
            holders[self.env._("Vehicle %s", vehicle.license_plate or vehicle.name)] = (
                "vehicle", vehicle.license_plate or vehicle.name)
        if market:
            holders[self.env._("Market vehicle %s", market.name)] = ("vehicle", market.name)
        if driver:
            holders[self.env._("Driver %s", driver.name)] = ("driver", driver.name)
        holders["Market driver"] = ("driver", self.market_driver_name or _("Market driver"))
        type_names = {t.code: t.name for t in self.env["tms.compliance.document.type"].search([])}
        today = fields.Date.context_today(self)
        for line in snapshot:
            parts = [p.strip() for p in line.split(" | ")]
            if len(parts) < 3:
                continue
            kind, holder = holders.get(parts[0], ("vehicle", parts[0]))
            doc = type_names.get(parts[1], parts[1])
            until = False
            if len(parts) >= 4:
                try:
                    until = fields.Date.to_date(parts[3].replace("valid until", "").strip())
                except (ValueError, TypeError):
                    until = False
            if len(parts) == 3 or not until:
                rows.append((doc, holder, "-", _("Missing"), "fail"))
                continue
            days = vehicle_days if kind == "vehicle" else (driver_days if parts[1] == "DL" else 0)
            if until < today:
                res, level = _("Expired"), "fail"
            elif until < end:
                res, level = _("Expires before trip end"), "fail"
            elif days and until <= end + timedelta(days=days):
                res, level = _("Expiring"), "warn"
            else:
                res, level = _("Valid"), "ok"
            rows.append((doc, holder, format_date(self.env, until), res, level))
        if market.blacklisted:
            rows.insert(0, (_("Blacklist"), market.name, "-", _("Blacklisted"), "fail"))
        enforced = trip.company_id.tms_enforce_compliance
        blocking = result == "fail" and enforced
        # vehicle and driver must be free, as opening the assignment checks
        try:
            trip._tms_check_vehicle_free(vehicle or None, driver or None, exclude_trip=trip)
        except UserError as err:
            msg = err.args[0] if err.args else str(err)
            failures.append(msg)
            rows.insert(0, (_("Availability"), (vehicle.license_plate or driver.name or ""), "-", _("Not free"),
                            "fail"))
            blocking = True
        return {"rows": rows, "failures": failures, "blocking": blocking, "result": result, "chosen": True,
                "enforced": enforced}

    @api.depends("trip_id", "resolution", "vehicle_id", "driver_id", "market_vehicle_id", "market_driver_name",
                 "market_driver_licence", "market_driver_licence_expiry", "hire_amount", "reposition_cost",
                 "revised_eta", "current_fleet_type")
    def _compute_preview(self):
        for wiz in self:
            check = wiz._tms_gate_check()
            label = wiz._tms_replacement_label()
            # compliance table
            if wiz.resolution == "repair":
                wiz.gate_preview = False
                wiz.gate_note = False
            elif not check["chosen"]:
                wiz.gate_preview = Markup('<p class="text-muted mb-0">%s</p>') % _(
                    "Choose the replacement vehicle to check its documents.")
                wiz.gate_note = False
            else:
                body = Markup("").join(
                    Markup('<tr><td class="fw-bold">%s</td><td>%s</td><td class="%s">%s</td>'
                           '<td><span class="badge rounded-pill %s">%s</span></td></tr>') % (
                        doc, holder, "text-danger fw-bold" if level == "fail" else "", until, BADGE[level], res)
                    for doc, holder, until, res, level in check["rows"])
                wiz.gate_preview = Markup(
                    '<table class="table table-sm o_list_table mb-0"><thead><tr><th>%s</th><th>%s</th>'
                    '<th>%s</th><th>%s</th></tr></thead><tbody>%s</tbody></table>') % (
                    _("Document"), _("Holder"), _("Valid until"), _("Result"), body)
                wiz.gate_note = False if check.get("enforced", True) else _(
                    "The compliance gate is not enforced for this branch; failures are only recorded.")
            wiz.gate_fail = check["blocking"]
            wiz.gate_message = _("%(v)s cannot be assigned: %(why)s. Choose another vehicle.",
                                 v=label or _("The replacement"), why="; ".join(check["failures"])) \
                if check["blocking"] else False
            # e-way bills on board
            bills = wiz._tms_live_ewaybills() if wiz.trip_id else wiz.env["tms.ewaybill"]
            if wiz.resolution == "repair":
                target = _("Not required · same vehicle")
            else:
                target = _("Vehicle no. → %s", label) if label else _("Vehicle no. → (choose the replacement)")
            wiz.partb_vehicle = target
            rows = Markup("")
            for lr in bills.mapped("consignment_id"):
                lr_bills = bills.filtered(lambda b, lr=lr: b.consignment_id == lr)
                rows += Markup('<tr><td class="fw-bold">%s</td><td>%s</td><td class="font-monospace">%s</td>'
                               '<td class="text-muted">%s</td></tr>') % (
                    lr.name or "", lr.consignee_id.display_name or "", ", ".join(lr_bills.mapped("name")), target)
            if rows:
                wiz.ewaybill_preview = Markup(
                    '<table class="table table-sm o_list_table mb-0"><thead><tr><th>%s</th><th>%s</th>'
                    '<th>%s</th><th>%s</th></tr></thead><tbody>%s</tbody></table>') % (
                    _("LR"), _("Consignee"), _("E-way bills"), _("Part-B vehicle update"), rows)
            else:
                wiz.ewaybill_preview = Markup('<p class="text-muted mb-0">%s</p>') % _(
                    "No live e-way bill on board.")
            # what Confirm creates
            parts = [_("breakdown"), _("incident")]
            if wiz.current_fleet_type == "owned":
                parts.append(_("service log"))
            if wiz.resolution == "repair":
                if wiz.revised_eta:
                    parts.append(_("DSR line with revised ETA"))
            else:
                if wiz.resolution == "replace_own" and wiz.reposition_cost:
                    parts.append(_("reposition expense %s", inr(wiz.reposition_cost)))
                if wiz.resolution == "replace_market":
                    parts.append(_("hire memo %s", inr(wiz.hire_amount or 0.0)))
                seq = (max(wiz.trip_id.assignment_ids.mapped("sequence")) + 1) if wiz.trip_id.assignment_ids else 1
                parts.append(_("assignment #%s", seq))
                if bills:
                    parts.append(_("%s Part-B task(s)", len(bills)))
            wiz.creates_summary = _("Creates: %s", ", ".join(parts))

    # ------------------------------------------------------------------
    def action_confirm(self):
        """Create the breakdown and resolve it in the same transaction, through the resolve wizard's code."""
        self.ensure_one()
        trip = self.trip_id
        if trip.state_code not in ("started", "in_transit"):
            raise UserError(_("Breakdowns are reported on trips on the road."))
        if self.resolution == "replace_own" and trip.company_id.tms_operation_mode == "broker":
            raise UserError(_("Own replacement is not available in Broker mode; replace with a market vehicle."))
        if self.resolution == "replace_own" and not (self.vehicle_id and self.driver_id):
            raise UserError(_("Choose the replacement vehicle and driver."))
        if self.resolution == "replace_market" and not (self.market_vehicle_id and self.broker_id):
            raise UserError(_("Choose the market vehicle and its broker."))
        if self.resumed_at and self.reported_at and self.resumed_at < self.reported_at:
            raise UserError(_("The vehicle cannot be available before the breakdown time."))
        check = self._tms_gate_check()
        if check["blocking"]:
            raise UserError(_("The replacement cannot be assigned:\n%s", "\n".join(check["failures"])))
        bd = self.env["tms.trip.breakdown"].create({
            "trip_id": trip.id,
            "reported_at": self.reported_at,
            "location": self.location,
            "cause": self.cause,
            "description": self.description,
            "odometer": self.odometer_at_breakdown,
            "repair_cost": self.repair_cost,
        })
        self.write({"breakdown_id": bd.id, "reason": "accident" if self.cause == "accident" else "breakdown"})
        asg = self._tms_resolve()
        if asg:
            title = _("Vehicle replaced · assignment #%s opened", asg.sequence)
            tasks = trip.ewaybill_update_ids.filtered(lambda t: t.assignment_id == asg and t.state == "pending")
            message = _("%(bd)s resolved; %(n)s Part-B task(s) to update to %(v)s.", bd=bd.name, n=len(tasks),
                        v=asg.vehicle_number or "") if tasks else _("%s resolved.", bd.name)
        else:
            title = _("Repair recorded")
            message = _("%(bd)s: ETA revised to %(eta)s.", bd=bd.name, eta=format_datetime(
                self.env, self.revised_eta, dt_format="dd/MM/yyyy HH:mm")) if self.revised_eta else \
                _("%s: the vehicle continues after repair.", bd.name)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": title,
                "message": message,
                "type": "success",
                "sticky": False,
                # close the dialog: the trip form underneath reloads, with no second breadcrumb
                "next": {"type": "ir.actions.act_window_close"},
            },
        }
