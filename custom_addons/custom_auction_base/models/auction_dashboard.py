# -*- coding: utf-8 -*-
"""The landing screen: what is waiting on somebody, this morning.

Deliberately NOT an OWL console. SCP-AUC-001 keeps OWL out of this module,
and a dashboard is the easiest place for that line to be crossed by
accident. This is a plain form on a transient record, which costs one
render and nothing else, works on every Odoo 18 CE install without an
asset bundle, and cannot break the bid path.

The design point worth keeping: every tile's domain is declared ONCE, in
TILES below, and both the number on the tile and the list the tile opens
read it from there. The failure this prevents is the one every hand-built
dashboard eventually has — the tile says four, the list shows seven, and
nobody can say which is the truth. Here they cannot disagree, because
there is only one domain.

Counts are taken in the CALLER's environment. An operator is told about
the records their own record rules let them see, and a tile whose model
they cannot read is hidden rather than shown as zero: a zero would read
as "nothing to do", which is a different and much worse statement than
"not your desk".
"""
from odoo import _, api, fields, models


# name -> (model, domain, label)
TILES = {
    "live": (
        "auction.event", [("state", "=", "live")],
        "Live events"),
    "closing_soon": (
        "auction.event",
        [("state", "=", "live"),
         ("bid_close_datetime", "<=", "__now_plus_48h__")],
        "Closing within 48 hours"),
    "awaiting_opening": (
        "auction.event", [("state", "=", "bidding_closed")],
        "Awaiting envelope opening"),
    "under_evaluation": (
        "auction.event", [("state", "=", "under_evaluation")],
        "Under evaluation"),
    "awards_to_approve": (
        "auction.award", [("state", "=", "draft")],
        "Awards awaiting approval"),
    "awards_to_generate": (
        "auction.award", [("state", "in", ("approved", "error"))],
        "Approved, documents not yet generated"),
    "security_pending": (
        "auction.bid.security",
        [("state", "in", ("declared", "received", "in_clearing"))],
        "Bid security awaiting verification"),
    "questions_open": (
        "auction.clarification", [("state", "=", "asked")],
        "Bidder questions unanswered"),
}


class AuctionDashboard(models.TransientModel):
    _name = "auction.dashboard"
    _description = "Auction Dashboard"

    # --- the queue
    count_live = fields.Integer(compute="_compute_counts")
    count_closing_soon = fields.Integer(compute="_compute_counts")
    count_awaiting_opening = fields.Integer(compute="_compute_counts")
    count_under_evaluation = fields.Integer(compute="_compute_counts")
    count_awards_to_approve = fields.Integer(compute="_compute_counts")
    count_awards_to_generate = fields.Integer(compute="_compute_counts")
    count_security_pending = fields.Integer(compute="_compute_counts")
    count_questions_open = fields.Integer(compute="_compute_counts")

    # A tile the operator has no licence to read is hidden, not zeroed.
    show_security = fields.Boolean(compute="_compute_counts")
    show_awards = fields.Boolean(compute="_compute_counts")
    show_questions = fields.Boolean(compute="_compute_counts")

    # --- the year so far, from the analysis view
    company_id = fields.Many2one(
        "res.company", default=lambda self: self.env.company)
    currency_id = fields.Many2one(related="company_id.currency_id")
    awarded_value_ytd = fields.Monetary(
        string="Awarded this year", compute="_compute_year")
    saving_value_ytd = fields.Monetary(
        string="Saving against base", compute="_compute_year")
    events_awarded_ytd = fields.Integer(
        string="Events awarded", compute="_compute_year")
    bids_opened_ytd = fields.Integer(
        string="Bids opened", compute="_compute_year")
    avg_bidders = fields.Float(
        string="Average bidders per line", digits=(6, 1),
        compute="_compute_year")

    # ------------------------------------------------------------------
    def _compute_display_name(self):
        """Without this the breadcrumb reads 'auction.dashboard,10'.

        A transient record has no name to fall back on, and the id is a
        detail of the plumbing that nobody opening a dashboard needs.
        """
        for record in self:
            record.display_name = _("Dashboard")

    @api.model
    def action_open(self):
        """Open the dashboard on a REAL record.

        Pointing a plain act_window at this model opens an unsaved record,
        and the form then shows eight tiles reading zero while the database
        says three, nine and one.

        Not for the reason it first appears. The compute runs perfectly
        well on an unsaved record — .new({}) in a shell returns the right
        figures — and adding a field dependency to it changes nothing;
        both were tried. What fails is the web onchange round trip for a
        new record, which does not carry these values back to the client.
        Creating the record first sidesteps the question entirely.

        Worth stating plainly: every shell check passed against both
        broken versions, because create({}) in a shell produces the one
        thing the form never makes — a record that exists. This was found
        by opening the screenshot and counting the tiles.
        """
        record = self.create({})
        return {
            "type": "ir.actions.act_window",
            "name": _("Dashboard"),
            "res_model": self._name,
            "res_id": record.id,
            "view_mode": "form",
            "views": [(self.env.ref(
                "custom_auction_base.view_auction_dashboard_form").id,
                "form")],
            "target": "inline",
        }

    def _tile_domain(self, key):
        """Resolve a declared tile domain, substituting the clock late.

        The 48-hour window has to be computed at read time, not at import
        time, or a worker that has been up for a week answers with last
        week's cutoff.
        """
        _model, domain, _label = TILES[key]
        resolved = []
        for leaf in domain:
            if (isinstance(leaf, (list, tuple)) and len(leaf) == 3
                    and leaf[2] == "__now_plus_48h__"):
                cutoff = fields.Datetime.add(fields.Datetime.now(), hours=48)
                resolved.append((leaf[0], leaf[1], cutoff))
            else:
                resolved.append(leaf)
        return resolved

    @api.depends("company_id")
    @api.depends_context("uid", "allowed_company_ids")
    def _compute_counts(self):
        readable = {}
        for key, (model, _d, _l) in TILES.items():
            if model not in readable:
                readable[model] = self.env[model].has_access("read")
        for rec in self:
            for key in TILES:
                model = TILES[key][0]
                value = 0
                if readable.get(model):
                    value = self.env[model].search_count(
                        rec._tile_domain(key))
                rec["count_%s" % key] = value
            rec.show_security = readable.get("auction.bid.security", False)
            rec.show_awards = readable.get("auction.award", False)
            rec.show_questions = readable.get("auction.clarification", False)

    @api.depends("company_id")
    @api.depends_context("uid", "allowed_company_ids")
    def _compute_year(self):
        Analysis = self.env["auction.analysis"]
        year_start = fields.Date.start_of(fields.Date.context_today(self),
                                          "year")
        domain = [("close_date", ">=", fields.Datetime.to_datetime(year_start))]
        for rec in self:
            # Restricted to events priced in the company currency. The
            # analysis view carries each EVENT's currency, so an unfiltered
            # sum would add rupees to dollars and print the result under
            # one symbol. Out of scope for SME v1.0 is multi-currency
            # tendering, not multi-currency arithmetic going unnoticed.
            scoped = domain + [("currency_id", "=", rec.currency_id.id)]
            rec.awarded_value_ytd = 0.0
            rec.saving_value_ytd = 0.0
            rec.events_awarded_ytd = 0
            rec.bids_opened_ytd = 0
            rec.avg_bidders = 0.0
            if not Analysis.has_access("read"):
                continue
            # _read_group, not the deprecated read_group: one row, no
            # groupby, four aggregates, returned as a plain tuple.
            rows = Analysis._read_group(
                scoped, [],
                ["awarded_value:sum", "saving_value:sum",
                 "bid_count:sum", "bidders_on_line:avg"])
            if rows:
                awarded, saving, bids, bidders = rows[0]
                rec.awarded_value_ytd = awarded or 0.0
                rec.saving_value_ytd = saving or 0.0
                rec.bids_opened_ytd = int(bids or 0)
                rec.avg_bidders = bidders or 0.0
            # Scoped to the same year and currency as the figures beside
            # it. An all-time count sitting under a heading that says
            # "this year" is a wrong number, not a generous one.
            rec.events_awarded_ytd = self.env["auction.event"].search_count(
                [("state", "=", "awarded"),
                 ("currency_id", "=", rec.currency_id.id),
                 ("bid_close_datetime", ">=",
                  fields.Datetime.to_datetime(year_start))])

    # ------------------------------------------------------------------
    def _open(self, key):
        """The list behind a tile. Same domain as the count, by construction."""
        model, _domain, label = TILES[key]
        return {
            "type": "ir.actions.act_window",
            "name": _(label),
            "res_model": model,
            "domain": self._tile_domain(key),
            "view_mode": "list,form",
            "target": "current",
        }

    def action_open_live(self):
        return self._open("live")

    def action_open_closing_soon(self):
        return self._open("closing_soon")

    def action_open_awaiting_opening(self):
        return self._open("awaiting_opening")

    def action_open_under_evaluation(self):
        return self._open("under_evaluation")

    def action_open_awards_to_approve(self):
        return self._open("awards_to_approve")

    def action_open_awards_to_generate(self):
        return self._open("awards_to_generate")

    def action_open_security_pending(self):
        return self._open("security_pending")

    def action_open_questions_open(self):
        return self._open("questions_open")
