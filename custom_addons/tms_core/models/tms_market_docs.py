"""BR-CMP-05: a market vehicle passes the gate on documents in the market-vehicle register or captured on
the trip for that assignment."""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class TmsComplianceDocument(models.Model):
    _inherit = "tms.compliance.document"

    trip_id = fields.Many2one("tms.order", string="Captured on trip", ondelete="cascade", index=True,
                              help="Market vehicle document captured on the assignment (BR-CMP-05).")

    @api.constrains("vehicle_id", "driver_id", "market_vehicle_id", "trip_id")
    def _check_holder(self):
        for doc in self:
            if len([h for h in (doc.vehicle_id, doc.driver_id, doc.market_vehicle_id, doc.trip_id) if h]) != 1:
                raise ValidationError(_("A compliance document belongs to exactly one vehicle, driver, market "
                                        "vehicle or trip."))


class TmsOrder(models.Model):
    _inherit = "tms.order"

    market_document_ids = fields.One2many("tms.compliance.document", "trip_id",
                                          string="Market vehicle documents")
