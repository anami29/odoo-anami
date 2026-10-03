# -*- coding: utf-8 -*-
"""Reporting over opened bids and awards.

A PostgreSQL view, not a table. Every figure here already exists on a
record somewhere; duplicating them into stored columns would mean a second
set of numbers to keep in step, and the first time they disagreed nobody
would know which was right.

The grain is ONE ROW PER OPENED BID LINE: a bidder's price for one line
item on one event, with the award outcome joined on where there is one.
That grain answers the questions people actually ask of a tender:

    how many bidders did we attract, and on which categories
    what was the spread between best and worst on each line
    how often do we award to someone other than the best bid, and why
    what did we save against our own base price
    which bidders keep bidding and never win

It cannot answer anything about a SEALED event before its opening,
because before the opening those numbers do not exist. Events still
bidding therefore contribute nothing here, which is correct, and is worth
saying out loud when somebody asks why a report looks empty. The pipeline
and bid-security screens read their own models for that reason.

Monetary columns carry the EVENT currency. Summing them across events in
more than one currency adds unlike things; the pivot cannot know that, so
group by currency before trusting a total.
"""
from odoo import api, fields, models, tools


class AuctionAnalysis(models.Model):
    _name = "auction.analysis"
    _description = "Auction Analysis"
    _auto = False
    _rec_name = "event_id"
    _order = "close_date desc, event_id, line_id"

    # ------------------------------------------------------------------
    # dimensions
    # ------------------------------------------------------------------
    event_id = fields.Many2one("auction.event", string="Event", readonly=True)
    company_id = fields.Many2one("res.company", readonly=True)
    direction = fields.Selection(
        [("reverse", "Purchase"), ("forward", "Disposal")], readonly=True)
    event_state = fields.Selection(
        [("draft", "Draft"), ("under_approval", "Under approval"),
         ("approved", "Approved"), ("published", "Published"),
         ("live", "Live"), ("bidding_closed", "Bidding closed"),
         ("under_evaluation", "Under evaluation"),
         ("award_pending", "Award pending approval"), ("awarded", "Awarded"),
         ("unsuccessful", "Unsuccessful"), ("cancelled", "Cancelled")],
        string="Event Status", readonly=True)
    category_id = fields.Many2one("auction.category", string="Category",
                                  readonly=True)
    lot_id = fields.Many2one("auction.lot", string="Lot", readonly=True)
    line_id = fields.Many2one("auction.line", string="Line Item",
                              readonly=True)
    product_id = fields.Many2one("product.product", string="Product",
                                 readonly=True)
    participant_id = fields.Many2one("auction.participant", string="Bidder",
                                     readonly=True)
    partner_id = fields.Many2one("res.partner", string="Bidder Organisation",
                                 readonly=True)
    classification = fields.Selection(
        [("mse", "Micro or Small Enterprise"),
         ("dpiit", "DPIIT-recognised startup"), ("oem", "OEM"),
         ("dealer", "Authorised dealer"), ("trader", "Trader"),
         ("other", "Other")], string="Bidder Class", readonly=True)
    currency_id = fields.Many2one("res.currency", readonly=True)

    publish_date = fields.Datetime(string="Published", readonly=True)
    close_date = fields.Datetime(string="Bid Close", readonly=True)

    # Groupable outcome flags. Booleans cannot be pivot measures, so the
    # counts below carry the same facts in a form read_group can add up.
    is_best = fields.Boolean(string="Best Price", readonly=True)
    is_awarded = fields.Boolean(string="Awarded", readonly=True)
    is_override = fields.Boolean(string="Not the Best Bid", readonly=True)

    # ------------------------------------------------------------------
    # measures
    # ------------------------------------------------------------------
    rank = fields.Integer(string="Rank on Line", readonly=True,
                          aggregator="min")
    price_unit = fields.Float(string="Bid Price", digits=(18, 6),
                              readonly=True, aggregator="avg")
    qty_offered = fields.Float(string="Qty Offered", digits=(18, 6),
                               readonly=True)
    bid_value = fields.Monetary(string="Bid Value", readonly=True)
    base_price = fields.Float(string="Base Price", digits=(18, 6),
                              readonly=True, aggregator="avg")
    base_value = fields.Monetary(string="Base Value", readonly=True)
    slip_days = fields.Integer(string="Delivery Slip (days)", readonly=True,
                               aggregator="max")

    bid_count = fields.Integer(string="Bids", readonly=True,
                               aggregator="sum")
    award_count = fields.Integer(string="Lines Awarded", readonly=True,
                                 aggregator="sum")
    override_count = fields.Integer(string="Awarded Off Best", readonly=True,
                                    aggregator="sum")
    qty_awarded = fields.Float(string="Qty Awarded", digits=(18, 6),
                               readonly=True)
    awarded_value = fields.Monetary(string="Awarded Value", readonly=True)
    # Positive means we paid less than our own base price. Reported only on
    # the row that was actually awarded, because a saving against a bid we
    # did not take is not a saving. Zero where no base price was recorded,
    # which is honest: no baseline, no measurable saving.
    saving_value = fields.Monetary(string="Saving vs Base", readonly=True)
    bidders_on_line = fields.Integer(string="Bidders on Line", readonly=True,
                                     aggregator="max")

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute("""
            CREATE OR REPLACE VIEW %s AS (
            -- PostgreSQL has no COUNT(DISTINCT x) OVER (...), so the
            -- distinct bidder count is a plain aggregate joined back on.
            -- Counting rows in the window instead would be right only while
            -- no participant ever has two opened lines on the same item,
            -- which is not an invariant this model is entitled to assume.
            WITH per_line AS (
                SELECT line_id, COUNT(DISTINCT participant_id) AS bidders
                FROM auction_evaluation_line
                GROUP BY line_id
            ),
            ranked AS (
                SELECT
                    e.id                      AS eval_id,
                    e.event_id,
                    e.lot_id,
                    e.line_id,
                    e.participant_id,
                    e.partner_id,
                    e.price_unit,
                    e.qty_offered,
                    e.max_deviation_days,
                    ROW_NUMBER() OVER (
                        PARTITION BY e.line_id
                        ORDER BY e.norm_value ASC, e.id ASC)   AS rank_asc,
                    ROW_NUMBER() OVER (
                        PARTITION BY e.line_id
                        ORDER BY e.norm_value DESC, e.id ASC)  AS rank_desc
                FROM auction_evaluation_line e
            )
            SELECT
                r.eval_id                               AS id,
                r.event_id,
                ev.company_id,
                ev.direction,
                ev.state                                AS event_state,
                ev.category_id,
                ev.currency_id,
                ev.publish_datetime                     AS publish_date,
                ev.bid_close_datetime                   AS close_date,
                r.lot_id,
                r.line_id,
                ln.product_id,
                ln.base_price,
                (COALESCE(ln.base_price, 0.0) * COALESCE(ln.product_qty, 0.0))
                                                        AS base_value,
                r.participant_id,
                r.partner_id,
                p.classification,
                -- A disposal is ranked high price first; a purchase low
                -- price first. One column, so a report never has to know
                -- which way round the event ran.
                (CASE WHEN ev.direction = 'forward'
                      THEN r.rank_desc ELSE r.rank_asc END)::integer
                                                        AS rank,
                (CASE WHEN ev.direction = 'forward'
                      THEN r.rank_desc ELSE r.rank_asc END = 1)
                                                        AS is_best,
                r.price_unit,
                r.qty_offered,
                (r.price_unit * r.qty_offered)          AS bid_value,
                COALESCE(r.max_deviation_days, 0)       AS slip_days,
                pl.bidders::integer                     AS bidders_on_line,
                1                                       AS bid_count,
                (aw.id IS NOT NULL)                     AS is_awarded,
                COALESCE(aw.is_override, FALSE)         AS is_override,
                (CASE WHEN aw.id IS NULL THEN 0 ELSE 1 END)
                                                        AS award_count,
                (CASE WHEN aw.id IS NOT NULL AND aw.is_override
                      THEN 1 ELSE 0 END)                AS override_count,
                COALESCE(aw.qty_awarded, 0.0)           AS qty_awarded,
                COALESCE(aw.awarded_value, 0.0)         AS awarded_value,
                CASE
                    WHEN aw.id IS NULL THEN 0.0
                    WHEN COALESCE(ln.base_price, 0.0) = 0.0 THEN 0.0
                    WHEN ev.direction = 'forward'
                        THEN (aw.price_unit - ln.base_price) * aw.qty_awarded
                    ELSE (ln.base_price - aw.price_unit) * aw.qty_awarded
                END                                     AS saving_value
            FROM ranked r
            JOIN per_line pl           ON pl.line_id = r.line_id
            JOIN auction_event ev      ON ev.id = r.event_id
            JOIN auction_line ln       ON ln.id = r.line_id
            JOIN auction_participant p ON p.id  = r.participant_id
            LEFT JOIN auction_award_line aw
                   ON aw.evaluation_line_id = r.eval_id
            )
        """ % self._table)

    # ------------------------------------------------------------------
    # dashboard
    # ------------------------------------------------------------------
    @api.model
    def pending_action_counts(self):
        """What is waiting on somebody.

        Deliberately a handful of counts rather than KPI tiles wired to
        stored fields: each one is a question an operator asks every
        morning, and each has a menu that answers it. Counted in the
        CALLER's environment, so an operator is told about the events
        their record rules let them see and no others.
        """
        Event = self.env["auction.event"]
        Security = self.env["auction.bid.security"]
        Clar = self.env["auction.clarification"]
        Award = self.env["auction.award"]
        return {
            "live": Event.search_count([("state", "=", "live")]),
            "awaiting_opening": Event.search_count(
                [("state", "=", "bidding_closed")]),
            "under_evaluation": Event.search_count(
                [("state", "=", "under_evaluation")]),
            "security_pending": Security.search_count(
                [("state", "in", ("declared", "received", "in_clearing"))]),
            "questions_open": Clar.search_count([("state", "=", "asked")]),
            "awards_to_approve": Award.search_count(
                [("state", "=", "draft")]),
            "awards_to_generate": Award.search_count(
                [("state", "in", ("approved", "error"))]),
        }
