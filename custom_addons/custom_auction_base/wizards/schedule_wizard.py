# -*- coding: utf-8 -*-
"""Generate an evenly spaced delivery schedule.

Typing twelve monthly tranches by hand is how schedules end up not adding
up to the line quantity. This generates them and puts the rounding remainder
on the last tranche so the total always matches exactly.
"""
from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_round


class AuctionScheduleWizard(models.TransientModel):
    _name = "auction.schedule.wizard"
    _description = "Generate Delivery Schedule"

    line_id = fields.Many2one("auction.line", required=True, readonly=True)
    line_qty = fields.Float(related="line_id.product_qty", readonly=True)
    uom_id = fields.Many2one(related="line_id.product_uom_id", readonly=True)

    periods = fields.Integer(required=True, default=12)
    start_date = fields.Date(required=True, default=fields.Date.context_today)
    frequency = fields.Selection(
        [("weekly", "Weekly"),
         ("fortnightly", "Fortnightly"),
         ("monthly", "Monthly"),
         ("quarterly", "Quarterly")],
        required=True, default="monthly")
    split = fields.Selection(
        [("equal", "Equal quantity each period"),
         ("manual", "Equal, then edit by hand")],
        required=True, default="equal")
    replace_existing = fields.Boolean(
        default=True,
        help="Clear any tranches already on this line before generating.")

    @api.constrains("periods")
    def _check_periods(self):
        for wiz in self:
            if wiz.periods < 1 or wiz.periods > 120:
                raise UserError(_("Enter between 1 and 120 periods."))

    def action_generate(self):
        self.ensure_one()
        line = self.line_id
        if self.replace_existing:
            line.schedule_ids.unlink()

        step = {
            "weekly": relativedelta(weeks=1),
            "fortnightly": relativedelta(weeks=2),
            "monthly": relativedelta(months=1),
            "quarterly": relativedelta(months=3),
        }[self.frequency]

        precision = 6
        per = float_round(line.product_qty / self.periods,
                          precision_digits=precision)
        rows, running, when = [], 0.0, self.start_date

        for index in range(self.periods):
            last = index == self.periods - 1
            # The remainder goes on the last tranche so the total is exact.
            qty = float_round(line.product_qty - running,
                              precision_digits=precision) if last else per
            running += qty
            rows.append((0, 0, {
                "sequence": (index + 1) * 10,
                "name": _("Tranche %s") % (index + 1),
                "quantity": qty,
                "required_by": when,
                "delivery_location": line.delivery_location,
                "delivery_location_id": line.delivery_location_id.id,
            }))
            when = when + step

        line.write({"schedule_ids": rows})
        return {"type": "ir.actions.act_window_close"}
