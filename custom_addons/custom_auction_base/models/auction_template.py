# -*- coding: utf-8 -*-
"""Event templates. BRD group TPL.

A category manager should be able to launch a recurring event without
rebuilding its rules, content skeleton and participant panel every time.
Without templates, every event is assembled from scratch and adoption dies
in the second month.

The governing rule, BR-TPL-009: instantiation copies BY VALUE, never by
reference. Once an event exists it holds its own lots, lines and settings,
and editing the template afterwards cannot reach it. A template that stayed
linked would let somebody alter a live or historical event by editing
something that looks like configuration.

Dates are stored as OFFSETS, not absolute values. A template that carried
real dates would be stale the day after it was made.
"""
import re

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_round


class AuctionEventTemplate(models.Model):
    _name = "auction.event.template"
    _description = "Auction Event Template"
    _order = "name"

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    description = fields.Text(help="When to use this template.")

    state = fields.Selection(
        [("draft", "Draft"), ("locked", "Locked")],
        required=True, default="draft", readonly=True, copy=False,
        help="A locked template cannot be edited. BR-TPL-007: an event must "
             "always be able to answer what it was built from, and a "
             "template edited after the fact destroys that answer.")
    company_id = fields.Many2one("res.company", required=True,
                                 default=lambda s: s.env.company)

    # -- header defaults --------------------------------------------------
    direction = fields.Selection(
        [("reverse", "Reverse - procurement"),
         ("forward", "Forward - disposal")],
        required=True, default="reverse")
    mechanism_id = fields.Many2one(
        "auction.mechanism", required=True,
        domain="[('direction', '=', direction)]")
    mechanism = fields.Char(related="mechanism_id.code", store=True,
                            readonly=True)
    structure = fields.Selection(
        [("single", "Single stage"), ("two_envelope", "Two envelope")],
        required=True, default="single")
    category_id = fields.Many2one("auction.category", required=True)
    rule_set_id = fields.Many2one("auction.rule.set", required=True)
    currency_id = fields.Many2one(
        "res.currency", default=lambda s: s.env.company.currency_id)
    anonymise = fields.Boolean(default=True)

    # -- timing, as offsets ------------------------------------------------
    bid_window_days = fields.Integer(
        required=True, default=7,
        help="Days from bidding opening to bidding closing.")
    publish_lead_days = fields.Integer(
        required=True, default=2,
        help="Days between publication and bidding opening.")
    comm_open_offset_hours = fields.Integer(
        required=True, default=24,
        help="Hours after bidding closes before envelopes may be opened.")
    tech_open_offset_hours = fields.Integer(
        default=2, help="Two-envelope only. Hours after close for the "
                        "technical opening.")

    # -- bid security ------------------------------------------------------
    require_bid_security = fields.Boolean(default=True)
    security_basis = fields.Selection(
        [("fixed", "Fixed amount"),
         ("percent", "Percent of estimated value")],
        required=True, default="percent")
    security_amount = fields.Monetary()
    security_percent = fields.Float(
        digits=(5, 2), default=2.0,
        help="GFR Rule 170 sets bid security between two and five percent "
             "of estimated value for procurement. Disposal is a sale and is "
             "not governed by that rule; ten percent of reserve is the "
             "working figure there.")
    security_verify_lead_hours = fields.Integer(default=24)
    security_grace_hours = fields.Integer(default=48)

    # -- default panel and skeleton ---------------------------------------
    partner_ids = fields.Many2many(
        "res.partner", string="Default Participants",
        domain="[('is_company', '=', True)]")
    lot_ids = fields.One2many("auction.event.template.lot", "template_id")

    # -- provenance --------------------------------------------------------
    version = fields.Integer(required=True, default=1, readonly=True)
    usage_count = fields.Integer(readonly=True, default=0)
    last_used = fields.Datetime(readonly=True)
    lot_count = fields.Integer(compute="_compute_counts")
    line_count = fields.Integer(compute="_compute_counts")

    #: The only fields a locked template may still change. Everything that
    #: defines the event shape is frozen; these are bookkeeping.
    MUTABLE_WHEN_LOCKED = {"state", "active", "usage_count", "last_used"}

    def write(self, vals):
        """Refuse edits to a locked template. BR-TPL-007.

        Unlocking is deliberately not offered. The route to a changed
        template is New Version, which leaves the original and everything
        built from it intact.
        """
        locked = self.filtered(lambda t: t.state == "locked")
        if locked:
            blocked = set(vals) - self.MUTABLE_WHEN_LOCKED
            if blocked:
                raise UserError(_(
                    "'%(name)s' is locked and cannot be edited. Use New "
                    "Version to make a draft copy, change that, and lock it. "
                    "Events already created from this template keep the "
                    "version they were built from.",
                    name=locked[0].name))
        return super().write(vals)

    def unlink(self):
        used = self.filtered(lambda t: t.usage_count)
        if used:
            raise UserError(_(
                "'%(name)s' has been used to create %(n)s event(s) and "
                "cannot be deleted. Archive it instead.",
                name=used[0].name, n=used[0].usage_count))
        return super().unlink()

    @api.returns("self", lambda value: value.id)
    def copy(self, default=None):
        """A copy is always a fresh draft, never a locked twin."""
        self.ensure_one()
        default = dict(default or {})
        default.setdefault("name", _("%s (copy)") % self.name)
        default.setdefault("state", "draft")
        default.setdefault("usage_count", 0)
        default.setdefault("last_used", False)
        default.setdefault("version", self.version + 1)
        return super().copy(default)

    def action_archive_template(self):
        """Retire a template that is no longer in use.

        Archiving never deletes. Events created from this template keep
        their provenance, so an archived template still answers the question
        of what an old event was built from.
        """
        for tpl in self:
            tpl.active = False
        return True

    def action_unarchive_template(self):
        for tpl in self:
            tpl.active = True
        return True

    def action_lock(self):
        """Freeze the template so it can be used to create events."""
        for tpl in self:
            if tpl.state == "locked":
                continue
            failures = tpl._completeness_failures()
            if failures:
                raise UserError(_(
                    "This template is not ready to lock:\n  * %s")
                    % "\n  * ".join(failures))
            tpl.state = "locked"

    def _completeness_failures(self):
        self.ensure_one()
        out = []
        if not self.lot_ids:
            out.append(_("No lots have been defined."))
        if not any(lot.line_ids for lot in self.lot_ids):
            out.append(_("No line items have been defined."))
        if self.require_bid_security:
            if self.security_basis == "fixed" and not self.security_amount:
                out.append(_("Bid security is a fixed amount but no amount is set."))
            if self.security_basis == "percent" and not self.security_percent:
                out.append(_("Bid security is a percentage but no percentage is set."))
        if self.bid_window_days < 1:
            out.append(_("The bidding window must be at least one day."))
        return out

    @api.depends("lot_ids", "lot_ids.line_ids")
    def _compute_counts(self):
        for tpl in self:
            tpl.lot_count = len(tpl.lot_ids)
            tpl.line_count = sum(len(l.line_ids) for l in tpl.lot_ids)

    @api.constrains("mechanism_id", "direction")
    def _check_mechanism_direction(self):
        for tpl in self:
            if tpl.mechanism_id and tpl.mechanism_id.direction != tpl.direction:
                raise UserError(_(
                    "'%(m)s' is a %(md)s mechanism and this template is "
                    "%(d)s.", m=tpl.mechanism_id.name,
                    md=tpl.mechanism_id.direction, d=tpl.direction))

    @api.onchange("direction")
    def _onchange_direction(self):
        for tpl in self:
            if tpl.mechanism_id and tpl.mechanism_id.direction != tpl.direction:
                tpl.mechanism_id = False

    # ------------------------------------------------------------------
    def action_create_event(self):
        """Open the wizard that turns this template into an event."""
        self.ensure_one()
        if not self.active:
            raise UserError(_(
                "'%(name)s' is archived and cannot be used for new events. "
                "Unarchive it, or take a New Version from it.",
                name=self.name))
        if self.state != "locked":
            raise UserError(_(
                "Lock this template before creating events from it. Locking "
                "is what makes it possible to say afterwards exactly what an "
                "event was built from."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Create Event from Template"),
            "res_model": "auction.template.apply.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_template_id": self.id},
        }

    def _instantiate(self, title, bid_open, owner=None, estimated_value=0.0):
        """Build an event from this template. Everything is copied by value.

        The returned event holds no live link back here. ``template_id`` is
        recorded for reporting only and is never read at runtime.
        """
        self.ensure_one()
        bid_close = bid_open + relativedelta(days=self.bid_window_days)
        publish = bid_open - relativedelta(days=self.publish_lead_days)

        security = 0.0
        if self.require_bid_security:
            security = (self.security_amount if self.security_basis == "fixed"
                        else float_round(
                            estimated_value * self.security_percent / 100.0,
                            precision_digits=2))

        event = self.env["auction.event"].create({
            "title": title,
            "direction": self.direction,
            "mechanism_id": self.mechanism_id.id,
            "structure": self.structure,
            "category_id": self.category_id.id,
            "rule_set_id": self.rule_set_id.id,
            "currency_id": (self.currency_id or self.env.company.currency_id).id,
            "anonymise": self.anonymise,
            "owner_id": (owner or self.env.user).id,
            "estimated_value": estimated_value,
            "publish_datetime": publish,
            "bid_open_datetime": bid_open,
            "bid_close_datetime": bid_close,
            "tech_open_datetime": (
                bid_close + relativedelta(hours=self.tech_open_offset_hours)
                if self.structure == "two_envelope" else False),
            "comm_open_datetime": (
                bid_close + relativedelta(hours=self.comm_open_offset_hours)),
            "require_bid_security": self.require_bid_security,
            "bid_security_amount": security,
            "security_verify_lead_hours": self.security_verify_lead_hours,
            "security_grace_hours": self.security_grace_hours,
            "template_id": self.id,
            "template_version": self.version,
        })

        for tlot in self.lot_ids:
            lot = self.env["auction.lot"].create({
                "event_id": event.id,
                "name": tlot.name,
                "sequence": tlot.sequence,
                "award_basis": tlot.award_basis,
                "all_or_nothing": tlot.all_or_nothing,
                "disclose_limit": tlot.disclose_limit,
                "precision_digits": tlot.precision_digits,
                "close_datetime": bid_close,
            })
            for tline in tlot.line_ids:
                line = self.env["auction.line"].create({
                    "lot_id": lot.id,
                    "name": tline.name,
                    "sequence": tline.sequence,
                    "product_id": tline.product_id.id or False,
                    "product_qty": tline.product_qty,
                    "product_uom_id": tline.product_uom_id.id or False,
                    "specification": tline.specification,
                    "hsn_sac": tline.hsn_sac,
                    "line_type": tline.line_type,
                    "delivery_location": tline.delivery_location,
                    "required_by": (
                        bid_close.date()
                        + relativedelta(days=tline.delivery_offset_days)),
                })
                tline._generate_schedule(line, bid_close)

        for partner in self.partner_ids:
            self.env["auction.participant"].create({
                "event_id": event.id,
                "partner_id": partner.id,
            })

        self.sudo().write({
            "usage_count": self.usage_count + 1,
            "last_used": fields.Datetime.now(),
        })
        return event

    def action_new_version(self):
        """Draft copy of a locked template, ready to edit and lock again.

        This is the only way to change a template that is in use. The
        original stays exactly as it was, so every event built from it
        remains answerable.
        """
        self.ensure_one()
        base = re.sub(r"\s*\(v\d+\)$", "", self.name)
        draft = self.copy({
            "name": "%s (v%d)" % (base, self.version + 1),
        })
        return {
            "type": "ir.actions.act_window",
            "name": _("New Version"),
            "res_model": self._name,
            "res_id": draft.id,
            "view_mode": "form",
        }


class AuctionEventTemplateLot(models.Model):
    _name = "auction.event.template.lot"
    _description = "Template Lot"
    _order = "template_id, sequence"

    template_id = fields.Many2one(
        "auction.event.template", required=True, ondelete="cascade", index=True)
    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    award_basis = fields.Selection(
        [("lot", "Whole lot"), ("line", "Per line")],
        required=True, default="lot")
    all_or_nothing = fields.Boolean(default=False)
    disclose_limit = fields.Boolean(default=False)
    precision_digits = fields.Integer(required=True, default=2)
    line_ids = fields.One2many("auction.event.template.line", "template_lot_id")

    def _locked_template(self):
        self.ensure_one()
        tpl = self.template_id
        return tpl if tpl.state == "locked" else False

    def _assert_parent_unlocked(self):
        """A lock on the parent must reach its children.

        Guarding only the template would leave the lots and line items
        editable, which is the same defect with a longer route to it.
        """
        for rec in self:
            template = rec._locked_template()
            if template:
                raise UserError(_(
                    "'%(name)s' is locked. Use New Version to make a draft "
                    "copy and change that instead.", name=template.name))

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._assert_parent_unlocked()
        return records

    def write(self, vals):
        self._assert_parent_unlocked()
        return super().write(vals)

    def unlink(self):
        self._assert_parent_unlocked()
        return super().unlink()



class AuctionEventTemplateLine(models.Model):
    _name = "auction.event.template.line"
    _description = "Template Line Item"
    _order = "template_lot_id, sequence"

    template_lot_id = fields.Many2one(
        "auction.event.template.lot", required=True, ondelete="cascade",
        index=True)
    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    product_id = fields.Many2one("product.product")
    product_code = fields.Char(related="product_id.default_code", readonly=True)
    product_qty = fields.Float(digits=(18, 6), required=True, default=1.0)
    product_uom_id = fields.Many2one("uom.uom")
    specification = fields.Text()
    hsn_sac = fields.Char()
    line_type = fields.Selection(
        [("firm", "Firm"), ("remeasurable", "Re-measurable"),
         ("provisional", "Provisional sum")],
        required=True, default="firm")
    delivery_location = fields.Char()

    delivery_offset_days = fields.Integer(
        default=30,
        help="Days after bidding closes by which delivery is required. "
             "Relative, so the template never goes stale.")
    schedule_periods = fields.Integer(
        default=0,
        help="Leave at zero for a single delivery. Set it to generate a "
             "phased schedule when the event is created.")
    schedule_frequency = fields.Selection(
        [("weekly", "Weekly"), ("fortnightly", "Fortnightly"),
         ("monthly", "Monthly"), ("quarterly", "Quarterly")],
        default="monthly")

    def _locked_template(self):
        self.ensure_one()
        tpl = self.template_lot_id.template_id
        return tpl if tpl.state == "locked" else False

    def _assert_parent_unlocked(self):
        """A lock on the parent must reach its children.

        Guarding only the template would leave the lots and line items
        editable, which is the same defect with a longer route to it.
        """
        for rec in self:
            template = rec._locked_template()
            if template:
                raise UserError(_(
                    "'%(name)s' is locked. Use New Version to make a draft "
                    "copy and change that instead.", name=template.name))

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._assert_parent_unlocked()
        return records

    def write(self, vals):
        self._assert_parent_unlocked()
        return super().write(vals)

    def unlink(self):
        self._assert_parent_unlocked()
        return super().unlink()

    @api.onchange("product_id")
    def _onchange_product_id(self):
        for line in self:
            if not line.product_id:
                continue
            if not line.name:
                line.name = line.product_id.display_name
            if not line.product_uom_id:
                line.product_uom_id = line.product_id.uom_id

    def _generate_schedule(self, line, bid_close):
        """Create phased delivery tranches on a freshly built line."""
        self.ensure_one()
        if self.schedule_periods < 2:
            return
        step = {
            "weekly": relativedelta(weeks=1),
            "fortnightly": relativedelta(weeks=2),
            "monthly": relativedelta(months=1),
            "quarterly": relativedelta(months=3),
        }[self.schedule_frequency or "monthly"]

        per = float_round(line.product_qty / self.schedule_periods,
                          precision_digits=6)
        when = bid_close.date() + relativedelta(days=self.delivery_offset_days)
        running, rows = 0.0, []
        for index in range(self.schedule_periods):
            last = index == self.schedule_periods - 1
            qty = float_round(line.product_qty - running,
                              precision_digits=6) if last else per
            running += qty
            rows.append((0, 0, {
                "sequence": (index + 1) * 10,
                "name": _("Tranche %s") % (index + 1),
                "quantity": qty,
                "required_by": when,
                "delivery_location": line.delivery_location,
            }))
            when = when + step
        line.write({"schedule_ids": rows})
