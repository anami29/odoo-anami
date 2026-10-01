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
