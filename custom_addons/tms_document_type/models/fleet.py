from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from odoo.addons.tms_profitability.models.account_move import month_end


class FleetVehicle(models.Model):
    _inherit = "fleet.vehicle"

    tms_sold = fields.Boolean(string="Sold", readonly=True, copy=False, tracking=True,
                              help="Sold on a vehicle sale; no longer offered for trips or as a replacement.")
    tms_sale_order_id = fields.Many2one("sale.order", string="Vehicle sale", readonly=True, copy=False)

    def _tms_mark_sold(self, order):
        """BR-DOC-15: the vehicle moves to the Sold stage, keeps its trips, logs and P&L history, and its
        unreleased prepaid costs release to the month of the sale."""
        sold = self.env.ref("tms_document_type.fleet_vehicle_state_sold")
        today = fields.Date.context_today(self)
        for vehicle in self:
            vehicle.write({"state_id": sold.id, "tms_sold": True, "tms_sale_order_id": order.id})
            schedules = self.env["tms.prepaid.schedule"].sudo().search([("vehicle_id", "=", vehicle.id)])
            planned = schedules.mapped("line_ids").filtered(lambda line: line.state == "planned")
            if planned:
                planned.write({"date": month_end(today)})
                planned._tms_release()
            vehicle.message_post(body=_("Sold on %s.", order.name))


class TmsTripAssignment(models.Model):
    _inherit = "tms.trip.assignment"

    @api.constrains("vehicle_id")
    def _check_tms_sold_vehicle(self):
        for asg in self.filtered(lambda a: a.vehicle_id.tms_sold):
            raise ValidationError(_("%s is sold and cannot be assigned.", asg.vehicle_id.display_name))


class TmsOrder(models.Model):
    _inherit = "tms.order"

    @api.constrains("planned_vehicle_id")
    def _check_tms_sold_vehicle(self):
        for trip in self.filtered(lambda t: t.planned_vehicle_id.tms_sold):
            raise ValidationError(_("%s is sold and cannot be assigned.", trip.planned_vehicle_id.display_name))
