# Copyright 2026 RLFB (Riamona Luxury and Fashion Brands), https://www.riamona.com
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
"""Compliance register: one row per owned vehicle and per driver, a cell per mandatory document.

The cells follow the compliance gate's own reading of the documents (latest valid-until per type,
insurance from the vehicle's policies, the driving licence from the driver's fields when no licence
document is held) but are evaluated against today with the 30-day warning window used on documents.
Everything here is computed on read and has no side effects.
"""
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools.misc import format_date

WARNING_DAYS = 30
# register columns: field prefix -> document type code
VEHICLE_COLUMNS = (("rc", "RC"), ("fit", "FIT"), ("permit", "PERMIT"), ("ins", "INS"), ("puc", "PUC"))
CELL_STATES = [("missing", "Missing"), ("expired", "Expired"), ("expiring", "Expiring"), ("valid", "Valid")]
GATE_STATES = [("blocked", "Blocked"), ("expiring", "Expiring"), ("valid", "Valid")]
# worst first
_RANK = {"missing": 0, "expired": 1, "expiring": 2, "valid": 3}


def _cell(env, until, today):
    """(state, text) for one register cell."""
    if not until:
        return "missing", env._("Missing")
    shown = format_date(env, until)
    if until < today:
        return "expired", env._("%s · expired", shown)
    days = (until - today).days
    if days <= WARNING_DAYS:
        return "expiring", env._("%(date)s · %(days)s d", date=shown, days=days)
    return "valid", shown


def _gate(env, states):
    """Gate column from the states of the holder's mandatory documents."""
    if not states:
        return "valid", env._("Valid")
    if any(s in ("missing", "expired") for s in states):
        return "blocked", env._("Blocked")
    if "expiring" in states:
        return "expiring", env._("Expiring")
    return "valid", env._("Valid %(n)s/%(n)s", n=len(states))


def _search_gate(records, operator, value, domain):
    if operator not in ("=", "!=", "in", "not in"):
        raise UserError(records.env._("Unsupported search on the compliance gate."))
    values = {value} if isinstance(value, str) else set(value or ())
    holders = records.search(domain)
    ids = holders.filtered(lambda h: h.tms_cmp_gate_state in values).ids
    return [("id", "in" if operator in ("=", "in") else "not in", ids)]


class TmsComplianceDocument(models.Model):
    _inherit = "tms.compliance.document"

    tms_days_left = fields.Integer(string="Days to expiry", compute="_compute_tms_days_left")
    tms_days_left_text = fields.Char(string="Days left", compute="_compute_tms_days_left")

    @api.depends("valid_until")
    def _compute_tms_days_left(self):
        today = fields.Date.context_today(self)
        for doc in self:
            if not doc.valid_until:
                doc.tms_days_left, doc.tms_days_left_text = 0, False
                continue
            days = (doc.valid_until - today).days
            doc.tms_days_left = days
            if days < 0:
                doc.tms_days_left_text = _("Expired %s d ago", -days)
            elif days == 0:
                doc.tms_days_left_text = _("Expires today")
            else:
                doc.tms_days_left_text = _("%s d", days)

    def action_tms_renew(self):
        """Open a new document of the same type for the same holder."""
        self.ensure_one()
        holder_ctx = {
            "default_vehicle_id": self.vehicle_id.id,
            "default_driver_id": self.driver_id.id,
            "default_market_vehicle_id": self.market_vehicle_id.id,
        }
        title = _("Renew %(type)s · %(holder)s", type=self.type_id.code, holder=self.holder_name or "")
        return _renew_action(self.env, self.type_id, title, {k: v for k, v in holder_ctx.items() if v})


class FleetVehicle(models.Model):
    _inherit = "fleet.vehicle"

    tms_cmp_rc_until = fields.Date(string="RC valid until", compute="_compute_tms_cmp")
    tms_cmp_rc_state = fields.Selection(CELL_STATES, string="RC status", compute="_compute_tms_cmp")
    tms_cmp_rc_text = fields.Char(string="Registration (RC)", compute="_compute_tms_cmp")
    tms_cmp_fit_until = fields.Date(string="Fitness valid until", compute="_compute_tms_cmp")
    tms_cmp_fit_state = fields.Selection(CELL_STATES, string="Fitness status", compute="_compute_tms_cmp")
    tms_cmp_fit_text = fields.Char(string="Fitness", compute="_compute_tms_cmp")
    tms_cmp_permit_until = fields.Date(string="Permit valid until", compute="_compute_tms_cmp")
    tms_cmp_permit_state = fields.Selection(CELL_STATES, string="Permit status", compute="_compute_tms_cmp")
    tms_cmp_permit_text = fields.Char(string="Permit", compute="_compute_tms_cmp")
    tms_cmp_ins_until = fields.Date(string="Insurance valid until", compute="_compute_tms_cmp")
    tms_cmp_ins_state = fields.Selection(CELL_STATES, string="Insurance status", compute="_compute_tms_cmp")
    tms_cmp_ins_text = fields.Char(string="Insurance validity", compute="_compute_tms_cmp")
    tms_cmp_puc_until = fields.Date(string="PUC valid until", compute="_compute_tms_cmp")
    tms_cmp_puc_state = fields.Selection(CELL_STATES, string="PUC status", compute="_compute_tms_cmp")
    tms_cmp_puc_text = fields.Char(string="PUC", compute="_compute_tms_cmp")
    tms_cmp_gate_state = fields.Selection(
        GATE_STATES, string="Gate status", compute="_compute_tms_cmp", search="_search_tms_cmp_gate_state")
    tms_cmp_gate = fields.Char(string="Gate", compute="_compute_tms_cmp")
    tms_cmp_renew_code = fields.Char(string="Document to renew", compute="_compute_tms_cmp")
    tms_cmp_trip_id = fields.Many2one("tms.order", string="Trip", compute="_compute_tms_cmp_trip")

    def _compute_tms_cmp_trip(self):
        for vehicle in self:
            vehicle.tms_cmp_trip_id = vehicle.tms_open_assignment_id.trip_id

    def _tms_cmp_until(self, doc_type):
        """Valid-until date the gate would use for this document type (False when missing)."""
        self.ensure_one()
        if doc_type.code == "INS":
            policies = self.insurance_id.filtered(lambda p: p.active and p.state != "cancelled")
            if policies:
                # the gate checks every policy, so the earliest end date decides
                return min(policies.mapped("end_date"))
        docs = self.tms_compliance_document_ids.filtered(lambda d: d.type_id == doc_type)
        return max(docs.mapped("valid_until")) if docs else False

    @api.depends(
        "tms_compliance_document_ids.valid_until", "tms_compliance_document_ids.type_id",
        "insurance_id.end_date", "insurance_id.state", "insurance_id.active",
    )
    def _compute_tms_cmp(self):
        today = fields.Date.context_today(self)
        Type = self.env["tms.compliance.document.type"]
        by_code = {t.code: t for t in Type.search([("code", "in", [c for _p, c in VEHICLE_COLUMNS])])}
        mandatory = Type.search([("mandatory_for_start", "=", True), ("applies_to", "in", ("vehicle", "both"))])
        for vehicle in self:
            for prefix, code in VEHICLE_COLUMNS:
                doc_type = by_code.get(code)
                until = vehicle._tms_cmp_until(doc_type) if doc_type else False
                state, text = _cell(self.env, until, today)
                vehicle["tms_cmp_%s_until" % prefix] = until
                vehicle["tms_cmp_%s_state" % prefix] = state
                vehicle["tms_cmp_%s_text" % prefix] = text
            cells = [(t, _cell(self.env, vehicle._tms_cmp_until(t), today)[0]) for t in mandatory]
            vehicle.tms_cmp_gate_state, vehicle.tms_cmp_gate = _gate(self.env, [s for _t, s in cells])
            worst = sorted((c for c in cells if c[1] != "valid"), key=lambda c: (_RANK[c[1]], c[0].sequence))
            vehicle.tms_cmp_renew_code = worst[0][0].code if worst else False

    def _search_tms_cmp_gate_state(self, operator, value):
        return _search_gate(self, operator, value, [("tms_ownership", "=", "owned")])

    def action_tms_compliance_register_switch(self):
        """List header toggle: from the vehicle register to the driver register."""
        return self.env["ir.actions.act_window"]._for_xml_id("tms_core.action_tms_compliance_register_driver")

    def action_renew_document(self, type_code=None):
        """Open a new compliance document prefilled for this vehicle; the gate takes the latest one."""
        self.ensure_one()
        code = type_code or self.env.context.get("renew_type_code") or self.tms_cmp_renew_code or "PUC"
        doc_type = self.env["tms.compliance.document.type"].search([("code", "=", code)], limit=1)
        if not doc_type:
            raise UserError(_("No compliance document type with code %s.", code))
        plate = self.license_plate or self.name
        if code == "INS" and self.insurance_id.filtered(lambda p: p.active and p.state != "cancelled"):
            # policies are kept on the insurance register; archive or cancel the old one after renewal
            return {
                "type": "ir.actions.act_window",
                "name": _("Renew insurance · %s", plate),
                "res_model": "tms.insurance",
                "view_mode": "form",
                "views": [(False, "form")],
                "target": "new",
                "context": {
                    "default_vehicle_ids": [self.id],
                    "default_tms_policy_type": "vehicle",
                    "default_state": "active",
                    "default_start_date": fields.Date.context_today(self),
                },
            }
        return _renew_action(self.env, doc_type, _("Renew %(type)s · %(holder)s", type=code, holder=plate),
                             {"default_vehicle_id": self.id})


class TmsDriver(models.Model):
    _inherit = "tms.driver"

    tms_cmp_dl_until = fields.Date(string="Licence expiry date", compute="_compute_tms_cmp")
    tms_cmp_dl_state = fields.Selection(CELL_STATES, string="Licence status", compute="_compute_tms_cmp")
    tms_cmp_dl_text = fields.Char(string="Driving licence valid until", compute="_compute_tms_cmp")
    tms_cmp_gate_state = fields.Selection(
        GATE_STATES, string="Gate status", compute="_compute_tms_cmp", search="_search_tms_cmp_gate_state")
    tms_cmp_gate = fields.Char(string="Gate", compute="_compute_tms_cmp")
    tms_cmp_renew_code = fields.Char(string="Document to renew", compute="_compute_tms_cmp")
    tms_cmp_trip_id = fields.Many2one("tms.order", string="Trip", compute="_compute_tms_cmp_trip")

    def _tms_cmp_until(self, doc_type):
        """Valid-until date the gate would use for this document type (False when missing)."""
        self.ensure_one()
        docs = self.tms_compliance_document_ids.filtered(lambda d: d.type_id == doc_type)
        if docs:
            return max(docs.mapped("valid_until"))
        # the licence number is restricted to some roles; only its presence matters here
        if doc_type.code == "DL" and self.sudo().driver_license_number:
            return self.driver_license_expiration_date or False
        return False

    @api.depends(
        "tms_compliance_document_ids.valid_until", "tms_compliance_document_ids.type_id",
        "driver_license_number", "driver_license_expiration_date",
    )
    def _compute_tms_cmp(self):
        today = fields.Date.context_today(self)
        Type = self.env["tms.compliance.document.type"]
        dl_type = Type.search([("code", "=", "DL")], limit=1)
        mandatory = Type.search([("mandatory_for_start", "=", True), ("applies_to", "in", ("driver", "both"))])
        for driver in self:
            until = driver._tms_cmp_until(dl_type) if dl_type else False
            driver.tms_cmp_dl_until = until
            driver.tms_cmp_dl_state, driver.tms_cmp_dl_text = _cell(self.env, until, today)
            cells = [(t, _cell(self.env, driver._tms_cmp_until(t), today)[0]) for t in mandatory]
            driver.tms_cmp_gate_state, driver.tms_cmp_gate = _gate(self.env, [s for _t, s in cells])
            worst = sorted((c for c in cells if c[1] != "valid"), key=lambda c: (_RANK[c[1]], c[0].sequence))
            driver.tms_cmp_renew_code = worst[0][0].code if worst else False

    def _compute_tms_cmp_trip(self):
        Assignment = self.env["tms.trip.assignment"]
        for driver in self:
            asg = Assignment.search([("driver_id", "=", driver.id), ("state", "=", "open")], limit=1)
            driver.tms_cmp_trip_id = asg.trip_id

    def _search_tms_cmp_gate_state(self, operator, value):
        return _search_gate(self, operator, value, [])

    def action_tms_compliance_register_switch(self):
        """List header toggle: from the driver register to the vehicle register."""
        return self.env["ir.actions.act_window"]._for_xml_id("tms_core.action_tms_compliance_register")

    def action_renew_document(self, type_code=None):
        """Open a new compliance document prefilled for this driver; the gate takes the latest one."""
        self.ensure_one()
        code = type_code or self.env.context.get("renew_type_code") or self.tms_cmp_renew_code or "DL"
        doc_type = self.env["tms.compliance.document.type"].search([("code", "=", code)], limit=1)
        if not doc_type:
            raise UserError(_("No compliance document type with code %s.", code))
        return _renew_action(self.env, doc_type, _("Renew %(type)s · %(holder)s", type=code, holder=self.name),
                             {"default_driver_id": self.id})


def _renew_action(env, doc_type, title, holder_ctx):
    ctx = {
        "default_type_id": doc_type.id,
        "default_valid_from": fields.Date.to_string(fields.Date.context_today(env["tms.compliance.document"])),
        "tms_renew": True,
    }
    ctx.update(holder_ctx)
    return {
        "type": "ir.actions.act_window",
        "name": title,
        "res_model": "tms.compliance.document",
        "view_mode": "form",
        "views": [(env.ref("tms_core.tms_compliance_document_renew_form").id, "form")],
        "target": "new",
        "context": ctx,
    }
