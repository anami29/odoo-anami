# -*- coding: utf-8 -*-
"""Rules engine — TSD-AUC-001 #6.1.

Resolution order: lot override, event snapshot, category default, system
default. A published event resolves against its SNAPSHOT and never against
the live rule set record, so editing a rule set never mutates a running or
historical event.
"""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class AuctionRuleSet(models.Model):
    _name = "auction.rule.set"
    _description = "Auction Rule Set"

    name = fields.Char(required=True)
    is_snapshot = fields.Boolean(readonly=True,
                                 help="Frozen copy taken at publication.")

    visibility = fields.Selection(
        [("none", "Sealed — nothing disclosed"),
         ("rank", "Rank only"),
         ("rank_band", "Rank band (traffic light)"),
         ("best", "Best price only"),
         ("full", "Full price and rank")],
        required=True, default="none")
    entry_mode = fields.Selection(
        [("free", "Free entry"),
         ("step_locked", "Step locked"),
         ("accept_decline", "Accept or decline")],
        required=True, default="free")

    min_improvement = fields.Float(digits=(18, 6), default=0.0)
    min_improvement_basis = fields.Selection(
        [("absolute", "Absolute"), ("percent", "Percent of current best")],
        default="absolute")
    max_improvement = fields.Float(
        digits=(18, 6), default=0.0,
        help="Guards against keying errors and bid dumping. Zero disables.")
    self_improvement = fields.Boolean(
        default=False,
        help="May a bidder already at rank one improve their own bid?")
    tie_break = fields.Selection(
        [("earliest", "Earliest server timestamp"),
         ("pro_rata", "Pro-rata split"),
         ("tie_round", "Tie-break round")],
        required=True, default="earliest")
    limit_enforcement = fields.Selection(
        [("reject", "Reject at entry"),
         ("accept_flag", "Accept and flag"),
         ("warn", "Warn only")],
        required=True, default="reject")
    partial_qty = fields.Boolean(default=False)
    all_or_nothing = fields.Boolean(default=False)

    anti_snipe = fields.Boolean(default=False)
    anti_snipe_window_min = fields.Integer(default=5)
    anti_snipe_extension_min = fields.Integer(default=5)
    anti_snipe_max_extensions = fields.Integer(default=6)

    rank_basis = fields.Selection(
        [("unit_price", "Unit price"),
         ("line_total", "Line total"),
         ("landed_cost", "Landed cost"),
         ("composite", "Composite score")],
        required=True, default="landed_cost")
    precision_digits = fields.Integer(required=True, default=2)

    # Bid validity: 120 days is the stated norm for works and services in the
    # CVC-aligned guidelines. AMD-AUC-002 B-10.
    bid_validity_days = fields.Integer(required=True, default=120)

    @api.constrains("min_improvement", "max_improvement")
    def _check_improvement(self):
        for rec in self:
            if rec.max_improvement and rec.min_improvement and \
                    rec.max_improvement < rec.min_improvement:
                raise ValidationError(_(
                    "Maximum improvement cannot be below the minimum."))

    def snapshot(self):
        """Freeze by value at publication. BR-RUL-020."""
        self.ensure_one()
        return self.copy({
            "name": "%s (snapshot)" % self.name,
            "is_snapshot": True,
        })
