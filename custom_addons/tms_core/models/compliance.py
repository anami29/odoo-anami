from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from .tools import schedule, users_in_group


class TmsComplianceDocumentType(models.Model):
    """BR-MD-06: configurable compliance document types."""

    _name = "tms.compliance.document.type"
    _description = "Compliance document type"
    _order = "sequence, name"

    name = fields.Char(required=True, translate=True)
    code = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    applies_to = fields.Selection(
        [("vehicle", "Vehicle"), ("driver", "Driver"), ("both", "Vehicle and driver")],
        required=True,
        default="vehicle",
    )
    mandatory_for_start = fields.Boolean(default=True, help="BR-CMP-01: checked by the compliance gate.")
    reminder_offsets = fields.Char(help="Days before expiry, comma separated; empty uses the company setting.")
    active = fields.Boolean(default=True)

    _sql_constraints = [("code_uniq", "unique(code)", "The document type code must be unique.")]

    def offsets(self, company):
        self.ensure_one()
        out = [int(p) for p in (self.reminder_offsets or "").split(",") if p.strip().isdigit()]
        return out or company.tms_reminder_offset_list()


class TmsComplianceDocument(models.Model):
    """BR-MD-07: a compliance document held by a vehicle, driver or market vehicle."""

    _name = "tms.compliance.document"
    _description = "Compliance document"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "valid_until"

    type_id = fields.Many2one("tms.compliance.document.type", required=True, tracking=True)
    type_code = fields.Char(related="type_id.code", store=True)
    number = fields.Char(required=True, tracking=True)
    valid_from = fields.Date(tracking=True)
    valid_until = fields.Date(required=True, tracking=True)
    attachment = fields.Binary(string="File", attachment=True)
    attachment_name = fields.Char()
    verified_by_id = fields.Many2one("res.users", string="Verified by", tracking=True, readonly=True)
    verified_on = fields.Datetime(readonly=True)
    vehicle_id = fields.Many2one("fleet.vehicle", ondelete="cascade", index=True)
    driver_id = fields.Many2one("tms.driver", ondelete="cascade", index=True)
    market_vehicle_id = fields.Many2one("tms.market.vehicle", ondelete="cascade", index=True)
    holder_name = fields.Char(compute="_compute_holder", store=True)
    company_id = fields.Many2one("res.company", default=lambda s: s.env.company)
    state = fields.Selection(
        [("valid", "Valid"), ("expiring", "Expiring within 30 days"), ("expired", "Expired")],
        compute="_compute_state",
        search="_search_state",
    )
    active = fields.Boolean(default=True)

    @api.depends("vehicle_id", "driver_id", "market_vehicle_id")
    def _compute_holder(self):
        for doc in self:
            holder = doc.vehicle_id or doc.driver_id or doc.market_vehicle_id
            doc.holder_name = holder.display_name if holder else False

    def _compute_state(self):
        today = fields.Date.context_today(self)
        for doc in self:
            if not doc.valid_until or doc.valid_until < today:
                doc.state = "expired"
            elif doc.valid_until <= today + timedelta(days=30):
                doc.state = "expiring"
            else:
                doc.state = "valid"

    def _search_state(self, operator, value):
        today = fields.Date.context_today(self)
        soon = today + timedelta(days=30)
        domains = {
            "expired": [("valid_until", "<", today)],
            "expiring": [("valid_until", ">=", today), ("valid_until", "<=", soon)],
            "valid": [("valid_until", ">", soon)],
        }
        if operator == "=":
            return domains.get(value, [])
        if operator == "in":
            dom = []
            for v in value:
                dom = (["|"] + dom + domains[v]) if dom else domains[v]
            return dom
        raise ValidationError(_("Unsupported search on document state."))

    @api.constrains("vehicle_id", "driver_id", "market_vehicle_id")
    def _check_holder(self):
        for doc in self:
            if len([h for h in (doc.vehicle_id, doc.driver_id, doc.market_vehicle_id) if h]) != 1:
                raise ValidationError(_("A compliance document belongs to exactly one vehicle, driver or market vehicle."))

    @api.constrains("valid_from", "valid_until")
    def _check_dates(self):
        for doc in self:
            if doc.valid_from and doc.valid_until and doc.valid_until < doc.valid_from:
                raise ValidationError(_("A document cannot expire before it is valid."))

    def action_verify(self):
        for doc in self:
            doc.write({"verified_by_id": self.env.user.id, "verified_on": fields.Datetime.now()})

    @api.depends("type_id", "number", "holder_name")
    def _compute_display_name(self):
        for doc in self:
            doc.display_name = "%s %s (%s)" % (doc.type_id.code or "", doc.number or "", doc.holder_name or "")

    # BR-CMP-10, BR-NTF-01
    @api.model
    def _cron_expiry_reminders(self):
        today = fields.Date.context_today(self)
        docs = self.search([("valid_until", ">=", today), ("valid_until", "<=", today + timedelta(days=60))])
        for doc in docs:
            company = doc.company_id or self.env.company
            days_left = (doc.valid_until - today).days
            if days_left in doc.type_id.offsets(company):
                users = users_in_group(self.env, "tms_core.group_fleet_manager", company)
                schedule(
                    doc,
                    users,
                    _("%(doc)s expires in %(d)s days", doc=doc.display_name, d=days_left),
                )


class TmsVehicleClass(models.Model):
    """BR-MD-15: vehicle class with its planned annual km."""

    _name = "tms.vehicle.class"
    _description = "Vehicle class"
    _order = "name"

    name = fields.Char(required=True)
    code = fields.Char()
    planned_annual_km = fields.Float(required=True, default=72000.0)
    payload_kg = fields.Float()
    active = fields.Boolean(default=True)
    vehicle_ids = fields.One2many("fleet.vehicle", "tms_vehicle_class_id")


class TmsMarketVehicle(models.Model):
    """BR-MD-11: market-vehicle register."""

    _name = "tms.market.vehicle"
    _description = "Market vehicle"
    _inherit = ["mail.thread"]
    _order = "name"

    name = fields.Char(string="Vehicle number", required=True, tracking=True)
    owner_id = fields.Many2one("res.partner", string="Owner", tracking=True)
    broker_id = fields.Many2one("res.partner", string="Broker", tracking=True)
    payload_kg = fields.Float(string="Payload (kg)")
    tms_vehicle_class_id = fields.Many2one("tms.vehicle.class", string="Vehicle class")
    blacklisted = fields.Boolean(tracking=True)
    blacklist_reason = fields.Text(tracking=True)
    compliance_document_ids = fields.One2many("tms.compliance.document", "market_vehicle_id")
    active = fields.Boolean(default=True)
    company_id = fields.Many2one("res.company", default=lambda s: s.env.company)

    _sql_constraints = [("name_uniq", "unique(name)", "This vehicle number is already registered.")]

    vehicle_id = fields.Many2one("fleet.vehicle", string="Fleet record", readonly=True, copy=False,
                                 help="Market fleet record that carries the vehicle on bill lines and P&L.")

    def _tms_fleet_vehicle(self):
        """Market vehicles carry a fleet.vehicle (ownership Market) so cost lines and the Vehicles
        analytic plan name them like owned trucks (BR-LED-01)."""
        self.ensure_one()
        if not self.vehicle_id:
            model = self.env.ref("tms_core.fleet_vehicle_model_market")
            self.sudo().vehicle_id = self.env["fleet.vehicle"].sudo().create({
                "model_id": model.id,
                "license_plate": self.name,
                "tms_ownership": "market",
                "tms_payload_kg": self.payload_kg,
                "tms_vehicle_class_id": self.tms_vehicle_class_id.id,
                "company_id": False,
            })
        return self.vehicle_id

    @api.constrains("blacklisted", "blacklist_reason")
    def _check_reason(self):
        for mv in self:
            if mv.blacklisted and not mv.blacklist_reason:
                raise ValidationError(_("Blacklisting needs a reason."))


class TmsComplianceGate(models.AbstractModel):
    """BR-CMP-01..09: the compliance gate run at trip start and on every new assignment."""

    _name = "tms.compliance.gate"
    _description = "Compliance gate"

    @api.model
    def evaluate(self, end_date, vehicle=None, driver=None, market_vehicle=None, market_docs=None):
        """Return (result, failures, warnings, snapshot lines).

        A document fails when missing or when its valid-until date falls before the trip's
        scheduled end; it warns when it expires within the base module's security days after that end.
        """
        end = fields.Date.to_date(end_date) if end_date else fields.Date.context_today(self)
        icp = self.env["ir.config_parameter"].sudo()
        driver_days = int(icp.get_param("tms.default_driver_license_security_days") or 0)
        vehicle_days = int(icp.get_param("tms.default_vehicle_insurance_security_days") or 0)
        types = self.env["tms.compliance.document.type"].search([("mandatory_for_start", "=", True)])
        failures, warnings, snapshot = [], [], []

        def check(holder_label, docs, doc_type, security_days):
            valid = docs.filtered(lambda d: d.type_id == doc_type)
            best = valid.sorted("valid_until", reverse=True)[:1]
            if not best:
                failures.append(self.env._("%(holder)s: %(type)s missing", holder=holder_label, type=doc_type.name))
                snapshot.append("%s | %s | missing" % (holder_label, doc_type.code))
                return
            snapshot.append(
                "%s | %s | %s | valid until %s" % (holder_label, doc_type.code, best.number, best.valid_until)
            )
            if best.valid_until < end:
                failures.append(
                    self.env._(
                        "%(holder)s: %(type)s %(num)s expires on %(date)s, before the scheduled end %(end)s",
                        holder=holder_label,
                        type=doc_type.name,
                        num=best.number,
                        date=best.valid_until,
                        end=end,
                    )
                )
            elif security_days and best.valid_until <= end + timedelta(days=security_days):
                warnings.append(
                    self.env._("%(holder)s: %(type)s %(num)s expires on %(date)s, within the security days",
                      holder=holder_label, type=doc_type.name, num=best.number, date=best.valid_until)
                )

        if vehicle:
            label = self.env._("Vehicle %s", vehicle.license_plate or vehicle.name)
            for t in types.filtered(lambda t: t.applies_to in ("vehicle", "both")):
                if t.code == "INS":
                    # BR-MD-08, BR-CMP-08: every active policy linked to the vehicle
                    policies = vehicle.insurance_id.filtered(lambda p: p.active and p.state != "cancelled")
                    pol_docs = vehicle.tms_compliance_document_ids.filtered(lambda d: d.type_id == t)
                    if not policies and not pol_docs:
                        failures.append(self.env._("%s: Insurance missing", label))
                        snapshot.append("%s | INS | missing" % label)
                    for pol in policies:
                        snapshot.append("%s | INS | %s | valid until %s" % (label, pol.policy_number, pol.end_date))
                        if pol.end_date < end:
                            failures.append(
                                self.env._("%(v)s: insurance policy %(p)s expires on %(d)s, before the scheduled end",
                                  v=label, p=pol.policy_number, d=pol.end_date)
                            )
                        elif vehicle_days and pol.end_date <= end + timedelta(days=vehicle_days):
                            warnings.append(self.env._("%(v)s: insurance policy %(p)s expires on %(d)s",
                                              v=label, p=pol.policy_number, d=pol.end_date))
                    if not policies and pol_docs:
                        check(label, pol_docs, t, vehicle_days)
                    continue
                check(label, vehicle.tms_compliance_document_ids, t, vehicle_days)
        if market_vehicle:
            label = self.env._("Market vehicle %s", market_vehicle.name)
            if market_vehicle.blacklisted:
                failures.append(self.env._("%(v)s is blacklisted: %(r)s", v=label, r=market_vehicle.blacklist_reason))
            docs = market_vehicle.compliance_document_ids | (market_docs or self.env["tms.compliance.document"])
            for t in types.filtered(lambda t: t.applies_to in ("vehicle", "both")):
                check(label, docs, t, vehicle_days)
        if driver:
            label = self.env._("Driver %s", driver.name)
            for t in types.filtered(lambda t: t.applies_to in ("driver", "both")):
                docs = driver.tms_compliance_document_ids
                if t.code == "DL" and not docs.filtered(lambda d: d.type_id == t) and driver.driver_license_number:
                    # BR-MD-09: licence stays on the base module's fields
                    snapshot.append("%s | DL | %s | valid until %s" % (
                        label, driver.driver_license_number, driver.driver_license_expiration_date))
                    exp = driver.driver_license_expiration_date
                    if not exp or exp < end:
                        failures.append(self.env._("%(d)s: driving licence expires on %(e)s, before the scheduled end",
                                          d=label, e=exp or "-"))
                    elif driver_days and exp <= end + timedelta(days=driver_days):
                        warnings.append(self.env._("%(d)s: driving licence expires on %(e)s", d=label, e=exp))
                    continue
                check(label, docs, t, driver_days if t.code == "DL" else 0)
        result = "fail" if failures else ("warn" if warnings else "pass")
        return result, failures, warnings, snapshot
