# -*- coding: utf-8 -*-
"""Delivery schedules.

A line item may carry a phased delivery schedule: 500 units as 100 in
January, 200 in February and 200 in March, rather than 500 on one date.

Two mirrored models:

  auction.line.schedule       what the buyer REQUIRES. Editable until
                              publication, frozen with the content after it.

  auction.bid.line.schedule   what the bidder OFFERS. Part of the sealed
                              payload, append-only like everything else on
                              the ledger, and therefore evidence.

Keeping the offered schedule separate from the required one is the whole
point. A bidder who cannot meet the required dates says so in their bid
rather than agreeing to something they will miss, and the difference is
visible at evaluation instead of at delivery.

Delivery timing is a cost, not a preference: a tranche that lands two months
late carries inventory, cash and production consequences. Stage 10 will feed
`deviation_days` into the landed cost so that ranking reflects it. Until
then it is recorded and visible but not scored.
"""
from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError
from odoo.tools import float_compare, float_is_zero


class AuctionLineSchedule(models.Model):
    """Buyer's required delivery schedule for one line item."""

    _name = "auction.line.schedule"
    _description = "Required Delivery Schedule"
    _order = "line_id, sequence, required_by"

    line_id = fields.Many2one(
        "auction.line", required=True, index=True, ondelete="cascade")
    sequence = fields.Integer(default=10)
    name = fields.Char(
        string="Tranche", help="Optional label, e.g. 'Phase 1' or 'January'.")

    quantity = fields.Float(digits=(18, 6), required=True)
    required_by = fields.Date(required=True)
    delivery_location = fields.Char(
        help="Leave empty to use the delivery location on the line.")
    notes = fields.Text()

    product_uom_id = fields.Many2one(related="line_id.product_uom_id")
    currency_id = fields.Many2one(related="line_id.lot_id.event_id.currency_id")

    _sql_constraints = [
        ("schedule_qty_positive", "CHECK(quantity > 0)",
         "A delivery tranche must carry a positive quantity."),
    ]

    @api.constrains("quantity", "line_id")
    def _check_total_matches_line(self):
        """Tranche quantities must add up to the line quantity.

        A schedule that does not add up is worse than no schedule: it looks
        authoritative and is wrong, and the error surfaces at delivery.
        """
        precision = self.env["decimal.precision"].precision_get("Product Unit of Measure") or 6
        for line in self.mapped("line_id"):
            if not line.schedule_ids:
                continue
            total = sum(line.schedule_ids.mapped("quantity"))
            if float_compare(total, line.product_qty, precision_digits=precision) != 0:
                raise ValidationError(_(
                    "The delivery schedule for '%(line)s' adds up to "
                    "%(total)s but the line quantity is %(qty)s. Adjust the "
                    "tranches or the line quantity so that they agree.",
                    line=line.name, total=total, qty=line.product_qty))

    @api.constrains("required_by", "line_id")
    def _check_within_event(self):
        for rec in self:
            event = rec.line_id.lot_id.event_id
            if event.bid_close_datetime and \
                    rec.required_by < event.bid_close_datetime.date():
                raise ValidationError(_(
                    "Delivery of '%(t)s' is required on %(d)s, which is "
                    "before bidding closes. Check the date.",
                    t=rec.name or rec.line_id.name, d=rec.required_by))


class AuctionBidLineSchedule(models.Model):
    """Bidder's offered delivery schedule. Append-only, part of the ledger."""

    _name = "auction.bid.line.schedule"
    _description = "Offered Delivery Schedule (append-only)"
    _order = "bid_line_id, sequence, offered_date"

    bid_line_id = fields.Many2one(
        "auction.bid.line", required=True, readonly=True, index=True,
        ondelete="cascade")
    schedule_id = fields.Many2one(
        "auction.line.schedule", readonly=True, ondelete="restrict",
        help="The required tranche this offer answers. Empty where the "
             "bidder proposed a schedule of their own shape.")
    sequence = fields.Integer(readonly=True, default=10)

    quantity = fields.Float(digits=(18, 6), readonly=True)
    offered_date = fields.Date(readonly=True)

    deviation_days = fields.Integer(
        readonly=True,
        help="Days later than required. Negative is early. Computed at "
             "submission and frozen; stage 10 feeds it into landed cost.")

    def write(self, vals):
        raise AccessError(_(
            "Offered delivery schedules are part of the bid ledger and "
            "cannot be modified after submission."))

    def unlink(self):
        raise AccessError(_(
            "Offered delivery schedules are part of the bid ledger and "
            "cannot be deleted."))
