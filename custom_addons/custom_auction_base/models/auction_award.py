# -*- coding: utf-8 -*-
"""Award and downstream document generation. BRD group AWD, group INT.

MINIMAL award, per SCP-AUC-001. The winner is chosen by a person looking at
the opened bids, not computed from a landed-cost ranking: evaluation and the
comparative statement are stage 10 and do not exist yet. Everything that
carries over to the full version is here already, so nothing built now gets
thrown away:

  * the three-level linkage that makes an award traceable
  * idempotent generation
  * failure isolation

What triggers generation
------------------------
NOT the auction closing. On a sealed event nobody knows who won at close,
because the envelopes have not been opened. The chain is:

    bidding closed -> envelopes opened -> award proposal -> approval
                   -> generation

Generation hangs off award approval and nothing else. Auto-generating on
close would raise purchase orders against an unopened envelope.
"""
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_compare

_logger = logging.getLogger(__name__)


class AuctionAward(models.Model):
    _name = "auction.award"
    _description = "Auction Award"
    _inherit = ["mail.thread"]
    _order = "id desc"

    name = fields.Char(required=True, readonly=True, copy=False,
                       default=lambda s: _("New"))
    event_id = fields.Many2one(
        "auction.event", required=True, readonly=True, index=True,
        ondelete="restrict")
    direction = fields.Selection(related="event_id.direction", store=True)
    company_id = fields.Many2one(related="event_id.company_id", store=True)
    currency_id = fields.Many2one(related="event_id.currency_id")

    line_ids = fields.One2many("auction.award.line", "award_id")
    total_value = fields.Monetary(compute="_compute_total", store=True)
    vendor_count = fields.Integer(compute="_compute_total", store=True)

    state = fields.Selection(
        [("draft", "Draft"),
         ("approved", "Approved"),
         ("generated", "Documents generated"),
         ("error", "Generation error"),
         ("cancelled", "Cancelled")],
        required=True, default="draft", tracking=True, copy=False)

    approved_by = fields.Many2one("res.users", readonly=True, copy=False)
    approved_on = fields.Datetime(readonly=True, copy=False)
    recommendation = fields.Text(
        help="Internal. Never shown to a bidder.")
    generation_error = fields.Text(readonly=True, copy=False)

    order_ids = fields.One2many("purchase.order", "auction_award_id",
                                readonly=True)
    sale_order_ids = fields.One2many("sale.order", "auction_award_id",
                                     readonly=True)
    document_count = fields.Integer(compute="_compute_documents")

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code(
                    "auction.award") or "/"
        return super().create(vals_list)

    @api.depends("line_ids.awarded_value", "line_ids.participant_id")
    def _compute_total(self):
        for award in self:
            award.total_value = sum(award.line_ids.mapped("awarded_value"))
            award.vendor_count = len(
                award.line_ids.mapped("participant_id.partner_id"))

    def _compute_documents(self):
        """sudo on the COUNT.

        order_ids and sale_order_ids point at purchase.order and sale.order.
        An auction event owner or award approver need not hold a purchase or
        sales licence, and without one, reading those One2many fields raised
        AccessError -- so the award form could not be OPENED at all by the
        very people who approve awards. A count of generated documents is
        not sensitive; the smart button that navigates to them stays
        group-guarded, so nobody is dropped into a view they cannot read.
        FIX-016.
        """
        for award in self:
            a = award.sudo()
            award.document_count = len(a.order_ids) + len(a.sale_order_ids)

    # ------------------------------------------------------------------
    def action_approve(self):
        for award in self:
            if award.state != "draft":
                raise UserError(_("Only a draft award can be approved."))
            if not award.line_ids:
                raise UserError(_("This award has no allocated lines."))
            if award.event_id.owner_id == self.env.user and \
                    not self.env.user.has_group(
                        "custom_auction_base.group_auction_approver"):
                raise UserError(_(
                    "The event owner cannot approve their own award. "
                    "Segregation of duties, BR-AUD-008."))
            award.write({
                "state": "approved",
                "approved_by": self.env.user.id,
                "approved_on": fields.Datetime.now(),
            })
            award.event_id.write({"state": "awarded", "anonymise": False})
            self.env["auction.audit"].log(
                action="award_approved", model=self._name, res_id=award.id,
                event_id=award.event_id.id,
                detail={"total": award.total_value,
                        "vendors": award.vendor_count})

    def action_generate_documents(self):
        """Create the transactional documents.

        Idempotent: re-running returns what already exists rather than
        creating duplicates. Generation touches several vendors and can fail
        part way through, so it must be safe to press twice.

        Failure isolated: a generation failure does NOT roll back the award.
        The award records the error and stays retryable, because losing an
        approved award decision because a purchase order would not save is
        the wrong trade. TSD-AUC-001 #13.4.
        """
        for award in self:
            if award.state not in ("approved", "error"):
                raise UserError(_(
                    "Approve the award before generating documents."))
            try:
                with self.env.cr.savepoint():
                    if award.direction == "reverse":
                        award._generate_purchase_orders()
                    else:
                        award._generate_sale_orders()
                    award.write({"state": "generated",
                                 "generation_error": False})
            except Exception as exc:              # noqa: BLE001
                _logger.exception("Award %s: document generation failed",
                                  award.name)
                award.write({"state": "error",
                             "generation_error": str(exc)})
                self.env["auction.audit"].log(
                    action="award_generation_failed", model=self._name,
                    res_id=award.id, event_id=award.event_id.id,
                    detail={"error": str(exc)})
        return self.action_view_documents()

    def _generate_purchase_orders(self):
        """One draft purchase order per vendor per lot. BR-INT-001."""
        self.ensure_one()
        # The authority for this document is the APPROVED AWARD, not the
        # approver's purchase licence. An SME approver is frequently a
        # director who holds no purchase rights at all. FIX-016.
        Order = self.env["purchase.order"].sudo()

        grouped = {}
        for line in self.line_ids:
            key = (line.participant_id.partner_id.id, line.lot_id.id)
            grouped.setdefault(key, []).append(line)

        for (partner_id, lot_id), lines in grouped.items():
            existing = Order.search([
                ("auction_award_id", "=", self.id),
                ("partner_id", "=", partner_id),
                ("auction_lot_id", "=", lot_id),
            ], limit=1)
            if existing:
                continue                      # idempotent: already generated

            commands = []
            for line in lines:
                commands.extend(
                    (0, 0, vals) for vals in self._purchase_line_values(line))

            order = Order.create({
                "partner_id": partner_id,
                "currency_id": self.currency_id.id,
                "company_id": self.company_id.id,
                "origin": "%s / %s" % (self.event_id.name, self.name),
                "auction_event_id": self.event_id.id,
                "auction_award_id": self.id,
                "auction_lot_id": lot_id,
                "order_line": commands,
            })
            for line in lines:
                line.order_id = order.id
            self._write_supplierinfo(lines, partner_id)

    def _purchase_line_values(self, award_line):
        """One purchase order line per delivery tranche.

        A line with three tranches becomes three purchase order lines with
        different planned dates, not one line with a note. Receipts,
        follow-up and delivery reporting then work without anything custom.
        """
        line = award_line.line_id
        base = {
            "product_id": line.product_id.id or False,
            "name": line.name + (
                "\n" + line.specification if line.specification else ""),
            "price_unit": award_line.price_unit,
            "product_uom": (line.product_uom_id.id
                            or (line.product_id.uom_po_id.id
                                if line.product_id else False)),
            "auction_award_line_id": award_line.id,
            "auction_bid_line_id": award_line.bid_line_id.id or False,
        }
        tranches = line.schedule_ids.sorted("required_by")
        if not tranches:
            return [dict(base,
                         product_qty=award_line.qty_awarded,
                         date_planned=fields.Datetime.to_datetime(
                             line.required_by) or fields.Datetime.now())]

        # Scale each tranche if a partial quantity was awarded.
        ratio = (award_line.qty_awarded / line.product_qty
                 if line.product_qty else 1.0)
        out = []
        for tranche in tranches:
            out.append(dict(
                base,
                name="%s - %s" % (base["name"], tranche.name or tranche.required_by),
                product_qty=tranche.quantity * ratio,
                date_planned=fields.Datetime.to_datetime(tranche.required_by),
            ))
        return out

    def _write_supplierinfo(self, lines, partner_id):
        """Update the vendor pricelist. BR-INT-005.

        Overlapping entries are end-dated, never deleted: a price that
        applied last quarter is a fact about last quarter.
        """
        # sudo, for the same reason as the orders above: product.supplierinfo
        # requires Purchase Administrator, and an award approver is not one.
        # The authority is the approved award. FIX-016.
        Supplier = self.env["product.supplierinfo"].sudo()
        today = fields.Date.today()
        for line in lines:
            product = line.line_id.product_id
            if not product:
                continue
            overlapping = Supplier.search([
                ("partner_id", "=", partner_id),
                ("product_tmpl_id", "=", product.product_tmpl_id.id),
                "|", ("date_end", "=", False), ("date_end", ">=", today),
            ])
            overlapping.write({"date_end": today})
            Supplier.create({
                "partner_id": partner_id,
                "product_tmpl_id": product.product_tmpl_id.id,
                "product_id": product.id,
                "price": line.price_unit,
                "currency_id": self.currency_id.id,
                "min_qty": 0.0,
                "date_start": today,
                "company_id": self.company_id.id,
            })

    def _generate_sale_orders(self):
        """One draft sale order per buyer per lot. BR-INT-006."""
        self.ensure_one()
        Order = self.env["sale.order"].sudo()   # see FIX-016 above
        by_key = {}
        for line in self.line_ids:
            by_key.setdefault(
                (line.participant_id.partner_id.id, line.lot_id.id), []
            ).append(line)

        for (partner_id, lot_id), lines in by_key.items():
            existing = Order.search([
                ("auction_award_id", "=", self.id),
                ("partner_id", "=", partner_id),
                ("auction_lot_id", "=", lot_id),
            ], limit=1)
            if existing:
                continue

            order = Order.create({
                "partner_id": partner_id,
                "currency_id": self.currency_id.id,
                "company_id": self.company_id.id,
                "origin": "%s / %s" % (self.event_id.name, self.name),
                "auction_event_id": self.event_id.id,
                "auction_award_id": self.id,
                "auction_lot_id": lot_id,
            })
            commands = []
            for line in lines:
                src = line.line_id
                commands.append((0, 0, {
                    "product_id": src.product_id.id or False,
                    "name": src.name,
                    "product_uom_qty": line.qty_awarded,
                    "product_uom": src.product_uom_id.id or False,
                    "price_unit": line.price_unit,
                    "auction_award_line_id": line.id,
                    "auction_bid_line_id": line.bid_line_id.id or False,
                }))
            order.write({"order_line": commands})
            for line in lines:
                line.sale_order_id = order.id

    def _open_form(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
        }

    def action_view_documents(self):
        self.ensure_one()
        reverse = self.direction == "reverse"
        return {
            "type": "ir.actions.act_window",
            "name": _("Purchase Orders") if reverse else _("Sale Orders"),
            "res_model": "purchase.order" if reverse else "sale.order",
            "domain": [("auction_award_id", "=", self.id)],
            "view_mode": "list,form",
        }


class AuctionAwardLine(models.Model):
    _name = "auction.award.line"
    _description = "Auction Award Line"
    _order = "award_id, lot_id, line_id"

    award_id = fields.Many2one("auction.award", required=True, index=True,
                               ondelete="cascade")
    event_id = fields.Many2one(related="award_id.event_id", store=True)
    currency_id = fields.Many2one(related="award_id.currency_id")

    lot_id = fields.Many2one("auction.lot", required=True, index=True)
    line_id = fields.Many2one("auction.line", required=True, index=True)
    participant_id = fields.Many2one("auction.participant", required=True,
                                     index=True)
    partner_id = fields.Many2one(related="participant_id.partner_id", store=True)

    # The middle link. A purchase order tells you which event; a purchase
    # order line tells you which bid; THIS tells you why that bid won.
    bid_line_id = fields.Many2one("auction.bid.line", index=True,
                                  ondelete="restrict")
    bid_id = fields.Many2one(related="bid_line_id.bid_id", store=True)

    # How a price gets onto this row at all.
    #
    # A sealed bid line carries price_unit = NULL by design: the value lives
    # inside the envelope until the opening. So the award CANNOT read a price
    # off the ledger, and typing one by hand would break the audit trail --
    # the awarded figure has to be the figure that was opened. Picking an
    # opened bid here is what makes the minimal award traceable.
    evaluation_line_id = fields.Many2one(
        "auction.evaluation.line", string="Opened Bid", index=True,
        ondelete="restrict",
        # The line filter applies only once a line has been chosen. Making it
        # unconditional would show an empty picker on a fresh row, where
        # line_id is still false -- and picking the opened bid FIRST, so it
        # fills the line, is the natural order of work.
        domain="[('event_id', '=', event_id)]"
               " + (line_id and [('line_id', '=', line_id)] or [])",
        help="The opened bid being awarded. Selecting it fills the bidder, "
             "the quantity and the price from the opening.")
    evaluation_rank = fields.Integer(
        related="evaluation_line_id.rank", string="Rank")

    qty_awarded = fields.Float(digits=(18, 6), required=True)
    price_unit = fields.Float(digits=(18, 6), required=True)
    awarded_value = fields.Monetary(compute="_compute_value", store=True)

    @api.onchange("evaluation_line_id")
    def _onchange_evaluation_line(self):
        """Carry the opened figures across. Never invent them."""
        for line in self:
            src = line.evaluation_line_id
            if not src:
                continue
            line.lot_id = src.lot_id
            line.line_id = src.line_id
            line.participant_id = src.participant_id
            line.bid_line_id = src.bid_line_id
            line.price_unit = src.price_unit
            # Default to the full requirement rather than what the bidder
            # happened to offer: a bidder may quote for more than is needed.
            line.qty_awarded = min(src.qty_offered, src.line_id.product_qty) \
                if src.qty_offered else src.line_id.product_qty
            line.is_override = bool(src.rank and src.rank > 1)

    @api.constrains("evaluation_line_id", "price_unit")
    def _check_price_matches_opening(self):
        """The awarded price must be the opened price.

        Without this the whole sealed-bid chain is decorative: someone could
        open the envelopes, then award at a number nobody ever bid. A
        negotiated figure is a different act and belongs to a counter-offer
        round, not to an award line.
        """
        for line in self:
            src = line.evaluation_line_id
            if not src:
                continue
            if float_compare(line.price_unit, src.price_unit,
                             precision_digits=6) != 0:
                raise ValidationError(_(
                    "The awarded price %(awarded)s does not match the opened "
                    "price %(opened)s for %(bidder)s on '%(line)s'. An award "
                    "carries the price that was bid.",
                    awarded=line.price_unit, opened=src.price_unit,
                    bidder=src.participant_id.display_name_computed,
                    line=line.line_id.name))

    is_override = fields.Boolean(
        string="Not the best bid",
        help="Tick where this bidder was not the best responsive offer.")
    justification = fields.Text()

    order_id = fields.Many2one("purchase.order", readonly=True)
    sale_order_id = fields.Many2one("sale.order", readonly=True)

    @api.depends("qty_awarded", "price_unit")
    def _compute_value(self):
        for line in self:
            line.awarded_value = line.qty_awarded * line.price_unit

    @api.constrains("is_override", "justification")
    def _check_justification(self):
        """An override without a reason is the thing an auditor asks about.
        BR-AWD-003."""
        for line in self:
            if line.is_override and not (line.justification or "").strip():
                raise ValidationError(_(
                    "Awarding to a bidder who was not the best offer needs a "
                    "written justification. Line: %s") % line.line_id.name)

    @api.constrains("qty_awarded", "line_id")
    def _check_quantity(self):
        """Allocated quantity may not exceed the required quantity.

        Split allocation across vendors is stage 11 proper; this stops the
        obvious error in the meantime.  BR-AWD-005.
        """
        # Grouped by (award, line). Reading self[0].award_id would check a
        # single award even when the write spans several.
        pairs = {(rec.award_id.id, rec.line_id) for rec in self
                 if rec.award_id and rec.line_id}
        for award_id, line in pairs:
            siblings = self.search([
                ("award_id", "=", award_id),
                ("line_id", "=", line.id),
            ])
            total = sum(siblings.mapped("qty_awarded"))
            if float_compare(total, line.product_qty,
                             precision_digits=6) > 0:
                raise ValidationError(_(
                    "Allocated quantity for '%(line)s' is %(total)s but only "
                    "%(qty)s is required.",
                    line=line.name, total=total, qty=line.product_qty))
