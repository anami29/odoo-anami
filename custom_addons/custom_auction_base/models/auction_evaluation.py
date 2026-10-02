# -*- coding: utf-8 -*-
"""Evaluation working set. BR-SLD-013.

Decrypted commercial values land HERE, never back on the ledger. The ledger
record keeps its envelope in the original encrypted form, so the sealed
evidence survives the opening intact and can still be verified afterwards.

This is also what makes the award possible: a sealed bid line carries a NULL
price by design, so without a working set there is nowhere for an opened
price to be read from.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class AuctionEvaluationLine(models.Model):
    _name = "auction.evaluation.line"
    _description = "Opened Bid Value"
    _order = "line_id, norm_value"
    _rec_name = "display_name_computed"

    event_id = fields.Many2one("auction.event", required=True, index=True,
                               ondelete="cascade")
    opening_id = fields.Many2one("auction.opening", readonly=True, index=True,
                                 ondelete="set null")
    bid_id = fields.Many2one("auction.bid", required=True, readonly=True,
                             index=True, ondelete="restrict")
    bid_line_id = fields.Many2one("auction.bid.line", required=True,
                                  readonly=True, index=True,
                                  ondelete="restrict")
    participant_id = fields.Many2one("auction.participant", required=True,
                                     readonly=True, index=True)
    partner_id = fields.Many2one(related="participant_id.partner_id", store=True)
    lot_id = fields.Many2one("auction.lot", required=True, readonly=True,
                             index=True)
    line_id = fields.Many2one("auction.line", required=True, readonly=True,
                              index=True)
    currency_id = fields.Many2one(related="event_id.currency_id")

    # aggregator=None throughout. Odoo sums a Float column by default in a
    # grouped list, and the default grouping here is BY LINE -- so the
    # column headed "Unit price" showed the five bidders' unit prices added
    # together, and the footer showed every bid on the event summed into one
    # total. Both are numbers that mean nothing and read as if they mean
    # something. You buy one of these offers, never their sum. FIX-015.
    price_unit = fields.Float(digits=(18, 6), readonly=True, aggregator=None)
    qty_offered = fields.Float(digits=(18, 6), readonly=True, aggregator=None)
    # line_value KEEPS its aggregator, unlike the columns above. The pivot
    # declares it as its measure, and a measure with no aggregator leaves
    # the pivot with nothing to compute -- it fell over client side with a
    # bare "Something went wrong". In the pivot each cell is a single bid,
    # and a column total is that bidder's total quote, which is exactly the
    # number an evaluator wants. In the grouped list the group total is the
    # sum of the bids received on that line; it is labelled "Bid value" so
    # it does not read as the cost of the line.
    line_value = fields.Monetary(string="Bid value",
                                 compute="_compute_value", store=True)
    norm_value = fields.Float(digits=(18, 6), readonly=True, index=True,
                              aggregator=None,
                              help="What ranking reads. Stage 10 replaces "
                                   "this with landed cost.")
    max_deviation_days = fields.Integer(readonly=True, aggregator=None)

    rank = fields.Integer(compute="_compute_rank", aggregator=None,
                          help="Position on this line. Computed on read, "
                               "never stored: a stored rank goes stale the "
                               "moment anything around it changes.")
    # Stored for the same reason as auction.participant. FIX-017.
    display_name_computed = fields.Char(compute="_compute_display", store=True)

    @api.depends("price_unit", "qty_offered")
    def _compute_value(self):
        for rec in self:
            rec.line_value = rec.price_unit * rec.qty_offered

    @api.depends("participant_id.display_name_computed", "line_id.name")
    def _compute_display(self):
        for rec in self:
            rec.display_name_computed = "%s - %s" % (
                rec.participant_id.display_name_computed, rec.line_id.name)

    @api.depends("norm_value", "line_id")
    def _compute_rank(self):
        """Rank within the line: ascending for reverse, descending for
        forward. Never stored — a stored rank is wrong the moment a row
        beside it changes, and nothing would recompute it.

        Positions are worked out over every row on the line, but assigned
        only to records in ``self``. Writing a computed field onto records
        outside the recordset leaves the ORM believing they are still
        pending and is not a contract the framework guarantees.
        """
        for line in self.mapped("line_id"):
            mine = self.filtered(lambda r: r.line_id == line)
            siblings = self.search([("line_id", "=", line.id)])
            descending = line.lot_id.event_id.direction == "forward"
            ordered = siblings.sorted("norm_value", reverse=descending)
            positions = {rec.id: n for n, rec in enumerate(ordered, start=1)}
            for rec in mine:
                rec.rank = positions.get(rec.id, 0)

    def action_award_this(self):
        """Pick this bidder. The 'by hand' in minimal award.

        Opens or reuses the draft award for the event and puts this opened
        bid on it. One click per winning line, which is how an SME actually
        awards: read down the grid, click the row you want.
        """
        self.ensure_one()
        award = self.env["auction.award"].search(
            [("event_id", "=", self.event_id.id), ("state", "=", "draft")],
            limit=1)
        if not award:
            award = self.env["auction.award"].create(
                {"event_id": self.event_id.id})
            self.event_id.write({"state": "under_evaluation"})

        existing = award.line_ids.filtered(lambda l: l.line_id == self.line_id)
        if existing:
            raise UserError(_(
                "'%(line)s' is already on award %(award)s, going to "
                "%(bidder)s. Remove that row first if you want to award it "
                "to somebody else.",
                line=self.line_id.name, award=award.name,
                bidder=existing[0].participant_id.display_name_computed))

        self.env["auction.award.line"].create({
            "award_id": award.id,
            "evaluation_line_id": self.id,
            "lot_id": self.lot_id.id,
            "line_id": self.line_id.id,
            "participant_id": self.participant_id.id,
            "bid_line_id": self.bid_line_id.id,
            "price_unit": self.price_unit,
            "qty_awarded": (min(self.qty_offered, self.line_id.product_qty)
                            if self.qty_offered else self.line_id.product_qty),
            "is_override": bool(self.rank and self.rank > 1),
            "justification": _(
                "Awarded at rank %d. Reason required.") % self.rank
            if self.rank and self.rank > 1 else False,
        })
        return award._open_form()
