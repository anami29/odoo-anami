# -*- coding: utf-8 -*-
"""Linkage on the transactional documents. BR-INT-002, BR-INT-003.

Three levels, deliberately:

  purchase.order.auction_event_id        which event this came from
  purchase.order.line.auction_bid_line_id  which bid set this price
  auction.award.line                     WHY that bid won

Two levels is not enough. Event to order gives provenance but not the price
basis; order line to bid line gives the price but not the reasoning. The
award line is what survives a challenge, because it carries the
justification for awarding to a bidder who was not the best offer.
"""
from odoo import _, fields, models


class PurchaseOrder(models.Model):
    _inherit = "purchase.order"

    auction_event_id = fields.Many2one(
        "auction.event", readonly=True, index=True, ondelete="restrict",
        copy=False)
    auction_award_id = fields.Many2one(
        "auction.award", readonly=True, index=True, ondelete="restrict",
        copy=False)
    auction_lot_id = fields.Many2one(
        "auction.lot", readonly=True, index=True, ondelete="restrict",
        copy=False)
    # The destination this order was raised for. An award whose tranches go
    # to two plants produces two orders, and this says which is which
    # without reading the picking type.
    auction_delivery_location_id = fields.Many2one(
        "stock.location", string="Tendered Delivery Location", readonly=True,
        index=True, ondelete="restrict", copy=False)
    auction_delivery_note = fields.Char(
        string="Tendered Delivery Address", readonly=True, copy=False,
        help="Where the tender named a destination that is not a "
             "configured location.")

    def action_view_auction_event(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "auction.event",
            "res_id": self.auction_event_id.id,
            "view_mode": "form",
        }


class PurchaseOrderLine(models.Model):
    _inherit = "purchase.order.line"

    auction_award_line_id = fields.Many2one(
        "auction.award.line", readonly=True, index=True, ondelete="restrict",
        copy=False)
    auction_bid_line_id = fields.Many2one(
        "auction.bid.line", readonly=True, index=True, ondelete="restrict",
        copy=False)
    # Carried from the tender rather than recomputed from the product: a
    # line may be classified differently from its product, and the figure
    # that was tendered is the one the order has to show. purchase.order.line
    # carries no HSN of its own in Odoo 18 -- l10n_in puts it on the invoice
    # line -- so this is the only place the tendered code can live.
    auction_hsn_sac = fields.Char(
        string="HSN/SAC (tendered)", readonly=True, copy=False)
    auction_delivery_location_id = fields.Many2one(
        "stock.location", string="Tendered Delivery Location", readonly=True,
        index=True, ondelete="restrict", copy=False)

    def _prepare_stock_move_vals(self, picking, price_unit, product_uom_qty,
                                 product_uom):
        """Send the receipt to the location the tender named.

        Recording the destination on the order is not enough on its own:
        without this the goods still land in whatever the operation type's
        default destination is, and a tender that promised delivery to the
        Ranjangaon gate would quietly receive at central stores. The
        destination was part of what bidders priced, so it has to drive the
        move, not just annotate it.

        Only ever NARROWS: the override applies when the tendered location
        sits under the operation type's destination, so it refines where
        inside the warehouse the goods land. A tendered location in a
        different warehouse is left alone and the operation type wins,
        because silently moving stock into another warehouse on the
        strength of a text field is not a trade this should make.
        """
        vals = super()._prepare_stock_move_vals(
            picking, price_unit, product_uom_qty, product_uom)
        tendered = self.auction_delivery_location_id
        if not tendered:
            return vals
        current = self.env["stock.location"].browse(
            vals.get("location_dest_id"))
        if current and tendered._child_of(current):
            vals["location_dest_id"] = tendered.id
        return vals


class SaleOrder(models.Model):
    _inherit = "sale.order"

    auction_event_id = fields.Many2one(
        "auction.event", readonly=True, index=True, ondelete="restrict",
        copy=False)
    auction_award_id = fields.Many2one(
        "auction.award", readonly=True, index=True, ondelete="restrict",
        copy=False)
    auction_lot_id = fields.Many2one(
        "auction.lot", readonly=True, index=True, ondelete="restrict",
        copy=False)

    def action_view_auction_event(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "auction.event",
            "res_id": self.auction_event_id.id,
            "view_mode": "form",
        }


class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    auction_award_line_id = fields.Many2one(
        "auction.award.line", readonly=True, index=True, ondelete="restrict",
        copy=False)
    auction_bid_line_id = fields.Many2one(
        "auction.bid.line", readonly=True, index=True, ondelete="restrict",
        copy=False)
    auction_hsn_sac = fields.Char(
        string="HSN/SAC (tendered)", readonly=True, copy=False)
    auction_delivery_location_id = fields.Many2one(
        "stock.location", string="Tendered Collection Location", readonly=True,
        index=True, ondelete="restrict", copy=False,
        help="On a disposal, where the material is lifted from.")
