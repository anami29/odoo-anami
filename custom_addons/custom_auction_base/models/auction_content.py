# -*- coding: utf-8 -*-
"""Content model — sections, lots, lines, attributes. BRD group CNT."""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class AuctionSection(models.Model):
    _name = "auction.section"
    _description = "Auction Section"
    _order = "event_id, sequence"

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    event_id = fields.Many2one("auction.event", required=True, index=True,
                               ondelete="cascade")
    description = fields.Html()
    weight = fields.Float(digits=(18, 6), default=0.0)


class AuctionLot(models.Model):
    """The lock granularity, and therefore the concurrency unit.

    All mutable auction state that must be serialised lives here and nowhere
    else. Every field written under the lot lock is marked as such below.
    """
    _name = "auction.lot"
    _description = "Auction Lot"
    _order = "event_id, sequence"

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    event_id = fields.Many2one("auction.event", required=True, index=True,
                               ondelete="cascade")
    section_id = fields.Many2one("auction.section", ondelete="set null")
    line_ids = fields.One2many("auction.line", "lot_id")

    award_basis = fields.Selection([("lot", "Whole lot"), ("line", "Per line")],
                                   required=True, default="lot")
    all_or_nothing = fields.Boolean(
        default=False, help="A bid must cover every line in the lot.")

    reserve_price = fields.Float(digits=(18, 6),
                                 help="Forward events. Never serialised to "
                                      "the portal unless disclosed.")
    ceiling_price = fields.Float(digits=(18, 6), help="Reverse events.")
    disclose_limit = fields.Boolean(default=False)

    rule_set_id = fields.Many2one("auction.rule.set",
                                  help="Optional lot-level override.")
    precision_digits = fields.Integer(required=True, default=2)

    # -- written ONLY inside the lot lock ----------------------------------
    close_datetime = fields.Datetime(required=True, index=True)
    extension_count = fields.Integer(required=True, default=0, readonly=True)
    chain_seq = fields.Integer(required=True, default=0, readonly=True)
    chain_head_hash = fields.Char(size=64, readonly=True)

    state = fields.Selection(
        [("pending", "Pending"), ("open", "Open"), ("paused", "Paused"),
         ("closed", "Closed"), ("unsold", "Unsold"), ("awarded", "Awarded")],
        required=True, default="pending", index=True)

    _sql_constraints = [
        ("lot_extension_positive", "CHECK(extension_count >= 0)",
         "Extension count cannot be negative."),
        ("lot_chain_seq_positive", "CHECK(chain_seq >= 0)",
         "Chain sequence cannot be negative."),
    ]

    @api.constrains("reserve_price", "ceiling_price")
    def _check_limits(self):
        for lot in self:
            if lot.reserve_price and lot.ceiling_price:
                raise ValidationError(_(
                    "A lot carries a reserve (forward) or a ceiling "
                    "(reverse), never both. Lot: %s") % lot.name)

    def _resolve_rules(self):
        """Effective rule set. Resolution order per TSD #6.1.

        A published event resolves against its snapshot; the live rule set
        record is never consulted for a running event.
        """
        self.ensure_one()
        # sudo throughout. A rule set is the TERMS of the tender, which a
        # bidder is entitled to be bound by, but a portal user holds no ACL
        # on auction.rule.set -- so every portal submission died with
        # "You are not allowed to access 'Auction Rule Set'" at the point
        # the engine read its own configuration. The engine must not depend
        # on the caller's permissions to read the rules it is enforcing.
        # Granting bidders blanket read on rule sets would be wider: a rule
        # set is shared across events, and snapshots of other events sit in
        # the same table. FIX-018.
        lot = self.sudo()
        if lot.rule_set_id:
            return lot.rule_set_id
        if lot.event_id.rule_snapshot_id:
            return lot.event_id.rule_snapshot_id
        return lot.event_id.rule_set_id

    def _close(self, reason=None):
        """Idempotent closure. Safe to call from cron, from lazy evaluation
        and from the console simultaneously. TSD #9.2."""
        for lot in self:
            lot.env.cr.execute(
                "SELECT state FROM auction_lot WHERE id = %s FOR UPDATE",
                (lot.id,))
            row = lot.env.cr.fetchone()
            if not row or row[0] not in ("open", "paused"):
                continue                       # already closed -- no-op
            has_bid = bool(lot.env["auction.bid"].search_count([
                ("lot_id", "=", lot.id), ("state", "=", "active")]))
            lot.write({"state": "closed" if has_bid else "unsold"})
            lot.env["auction.audit"].log(
                action="lot_closed", model="auction.lot", res_id=lot.id,
                event_id=lot.event_id.id,
                detail={"reason": reason, "responsive_bids": has_bid})

    @api.model
    def _cron_close_due(self):
        """Scheduled closure. Supplemented by lazy close-on-access so that a
        delayed or stopped cron cannot leave a lot incorrectly open."""
        due = self.search([
            ("state", "=", "open"),
            ("close_datetime", "<=", fields.Datetime.now()),
        ], order="close_datetime")
        due._close(reason="scheduled")


class AuctionLine(models.Model):
    _name = "auction.line"
    _description = "Auction Line Item"
    _order = "lot_id, sequence"

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    lot_id = fields.Many2one("auction.lot", required=True, index=True,
                             ondelete="cascade")
    product_id = fields.Many2one(
        "product.product",
        help="Optional. Works and services often have no product record; "
             "the description alone is enough.")
    product_code = fields.Char(related="product_id.default_code", readonly=True)
    # Soft filter: set a Product Category on the sourcing category and the
    # product picker narrows to it. Leave it empty for no filter.
    product_category_id = fields.Many2one(
        related="lot_id.event_id.category_id.product_category_id",
        readonly=True)
    specification = fields.Text()
    product_qty = fields.Float(digits=(18, 6), required=True, default=1.0)
    product_uom_id = fields.Many2one("uom.uom")
    delivery_location = fields.Char()
    required_by = fields.Date()

    base_price = fields.Float(digits=(18, 6),
                              help="Denominator for savings computation.")
    start_price = fields.Float(digits=(18, 6))
    hsn_sac = fields.Char()

    # GAP-044: works quantities are tendered as estimates and re-measured on
    # completion. A firm line is fixed; a re-measurable line is ranked on
    # estimated quantity times rate and re-computed on certified measurement.
    line_type = fields.Selection(
        [("firm", "Firm"),
         ("remeasurable", "Re-measurable"),
         ("provisional", "Provisional sum")],
        required=True, default="firm")

    # Phased delivery. Empty means a single delivery on required_by.
    schedule_ids = fields.One2many("auction.line.schedule", "line_id")
    schedule_count = fields.Integer(compute="_compute_schedule")
    delivery_summary = fields.Char(compute="_compute_schedule")

    def _compute_schedule(self):
        for line in self:
            tranches = line.schedule_ids.sorted("required_by")
            line.schedule_count = len(tranches)
            if not tranches:
                line.delivery_summary = (
                    fields.Date.to_string(line.required_by)
                    if line.required_by else "")
            elif len(tranches) == 1:
                line.delivery_summary = "%s on %s" % (
                    tranches.quantity, tranches.required_by)
            else:
                line.delivery_summary = "%d tranches, %s to %s" % (
                    len(tranches), tranches[0].required_by,
                    tranches[-1].required_by)

    def action_open_schedule_wizard(self):
        """Generate evenly spaced tranches instead of typing twelve rows."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Generate Delivery Schedule"),
            "res_model": "auction.schedule.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_line_id": self.id},
        }

    _sql_constraints = [
        ("line_qty_positive", "CHECK(product_qty > 0)",
         "Quantity must be positive."),
    ]

    @api.onchange("product_id")
    def _onchange_product_id(self):
        """Populate the line from the product.

        Only fills what is empty, so a description already edited by hand is
        never overwritten when the product is re-selected.
        """
        for line in self:
            product = line.product_id
            if not product:
                continue

            if not line.name:
                line.name = product.display_name
            if not line.product_uom_id:
                line.product_uom_id = product.uom_id
            if not line.specification:
                line.specification = (
                    product.description_purchase or product.description or "")

            # HSN only exists where the Indian localisation is installed.
            if not line.hsn_sac:
                for field in ("l10n_in_hsn_code",):
                    if field in product._fields:
                        line.hsn_sac = product[field] or ""
                        break

            # Base price is the denominator for savings reporting. Cost is a
            # sensible default when buying; on a disposal event the book cost
            # says nothing about what scrap will fetch, so leave it alone.
            direction = line.lot_id.event_id.direction
            if direction == "reverse" and not line.base_price:
                line.base_price = product.standard_price
