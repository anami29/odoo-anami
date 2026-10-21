from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class FleetVehicle(models.Model):
    _inherit = "fleet.vehicle"

    # BR-MD-05
    tms_payload_kg = fields.Float(string="Payload (kg)", tracking=True)
    tms_vehicle_type = fields.Char(string="Vehicle type")
    tms_vehicle_class_id = fields.Many2one("tms.vehicle.class", string="Vehicle class", tracking=True)
    tms_ownership = fields.Selection(
        [("owned", "Owned"), ("market", "Market")], default="owned", required=True, tracking=True,
        string="Ownership")
    tms_home_branch_id = fields.Many2one(
        "res.company", string="Home branch", default=lambda s: s.env.company, tracking=True
    )
    tms_fuel_norm = fields.Float(string="Fuel norm (km per litre)")
    tms_compliance_document_ids = fields.One2many("tms.compliance.document", "vehicle_id", string="Compliance documents")
    tms_open_assignment_id = fields.Many2one(
        "tms.trip.assignment", compute="_compute_tms_open_assignment", string="Open assignment"
    )
    tms_status = fields.Selection(
        [("free", "Free"), ("on_trip", "On trip"), ("breakdown", "Breakdown"), ("service", "In service")],
        compute="_compute_tms_open_assignment",
        string="Fleet status")
    tms_last_dsr_location = fields.Char(compute="_compute_tms_open_assignment", string="Last DSR location")
    tms_expected_free = fields.Datetime(compute="_compute_tms_open_assignment", string="Expected free at")

    def _compute_tms_open_assignment(self):
        Assignment = self.env["tms.trip.assignment"]
        for vehicle in self:
            asg = Assignment.search([("vehicle_id", "=", vehicle.id), ("state", "=", "open")], limit=1)
            vehicle.tms_open_assignment_id = asg
            status = "free"
            if vehicle._tms_has_open_breakdown():
                status = "breakdown"
            elif vehicle._tms_has_open_service():
                status = "service"
            elif asg:
                status = "on_trip"
            vehicle.tms_status = status
            dsr = asg.trip_id.dsr_ids[:1] if asg else self.env["tms.trip.dsr"]
            vehicle.tms_last_dsr_location = dsr.location if dsr else False
            vehicle.tms_expected_free = asg.trip_id.scheduled_date_end if asg else False

    def _tms_has_open_breakdown(self):
        self.ensure_one()
        return bool(
            self.env["tms.trip.breakdown"].search_count(
                [("vehicle_id", "=", self.id), ("resumed_at", "=", False), ("state", "!=", "cancel")], limit=1
            )
        )

    def _tms_has_open_service(self):
        self.ensure_one()
        return bool(
            self.env["fleet.vehicle.log.services"].search_count(
                [("vehicle_id", "=", self.id), ("state", "in", ("new", "running"))], limit=1
            )
        )

    def tms_last_odometer(self):
        self.ensure_one()
        last = self.env["fleet.vehicle.odometer"].search([("vehicle_id", "=", self.id)], order="value desc", limit=1)
        return last.value if last else 0.0

    def tms_record_odometer(self, value, date=None):
        """BR-PNL-16: a reading below the vehicle's last reading is refused."""
        self.ensure_one()
        last = self.tms_last_odometer()
        if value < last:
            raise ValidationError(
                _("Odometer %(v)s is below the last reading %(l)s of %(veh)s.", v=value, l=last, veh=self.display_name)
            )
        return self.env["fleet.vehicle.odometer"].create(
            {"vehicle_id": self.id, "value": value, "date": date or fields.Date.context_today(self)}
        )


class TmsDriver(models.Model):
    _inherit = "tms.driver"

    tms_compliance_document_ids = fields.One2many("tms.compliance.document", "driver_id", string="Compliance documents")
    tms_home_branch_id = fields.Many2one("res.company", string="Home branch", default=lambda s: s.env.company)
    tms_availability = fields.Selection(
        [("free", "Available"), ("on_trip", "On trip")], compute="_compute_tms_availability",
        string="Availability")

    def _compute_tms_availability(self):
        Assignment = self.env["tms.trip.assignment"]
        for driver in self:
            driver.tms_availability = (
                "on_trip"
                if Assignment.search_count([("driver_id", "=", driver.id), ("state", "=", "open")], limit=1)
                else "free"
            )


class TmsInsurance(models.Model):
    _inherit = "tms.insurance"

    # BR-CLM-12: goods-in-transit and carrier's liability policies held apart from vehicle policies
    tms_policy_type = fields.Selection(
        [("vehicle", "Vehicle"), ("goods_in_transit", "Goods in transit"), ("carrier_liability", "Carrier's liability")],
        default="vehicle",
        required=True,
        string="Policy type")
    tms_insurer_claim_ids = fields.One2many("tms.insurer.claim", "policy_id", string="Insurer claims")

    @api.constrains("tms_policy_type", "vehicle_ids")
    def _check_policy_type(self):
        for pol in self:
            if pol.tms_policy_type != "vehicle" and pol.vehicle_ids:
                raise ValidationError(_("Goods and liability policies are not linked to vehicles."))


class FleetVehicleLogServices(models.Model):
    _inherit = "fleet.vehicle.log.services"

    tms_breakdown_id = fields.Many2one("tms.trip.breakdown", string="Breakdown")
    tms_downtime_hours = fields.Float(string="Downtime (hours)")
