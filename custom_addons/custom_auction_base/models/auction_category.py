# -*- coding: utf-8 -*-
from odoo import fields, models


class AuctionCategory(models.Model):
    _name = "auction.category"
    _description = "Sourcing Category"
    _parent_store = True
    _order = "complete_name"

    name = fields.Char(required=True)
    complete_name = fields.Char(compute="_compute_complete_name",
                                recursive=True, store=True)
    parent_id = fields.Many2one("auction.category", ondelete="restrict", index=True)
    parent_path = fields.Char(index=True, unaccent=False)
    child_ids = fields.One2many("auction.category", "parent_id")
    product_category_id = fields.Many2one("product.category")

    # GAP-052: works and services behave differently enough from goods that
    # the category type drives the default content model, prerequisite set
    # and landed cost formula.
    category_type = fields.Selection(
        [("goods", "Goods"), ("works", "Works"), ("services", "Services")],
        required=True, default="goods")
    company_id = fields.Many2one("res.company", required=True,
                                 default=lambda s: s.env.company)
    active = fields.Boolean(default=True)

    def _compute_complete_name(self):
        for rec in self:
            rec.complete_name = (
                "%s / %s" % (rec.parent_id.complete_name, rec.name)
                if rec.parent_id else rec.name)
