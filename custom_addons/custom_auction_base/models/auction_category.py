# -*- coding: utf-8 -*-
from odoo import api, fields, models


class AuctionCategory(models.Model):
    _name = "auction.category"
    _description = "Sourcing Category"
    _parent_store = True
    _order = "complete_name"

    name = fields.Char(required=True)
    complete_name = fields.Char(compute="_compute_complete_name",
                                recursive=True, store=True)
    parent_id = fields.Many2one("auction.category", ondelete="restrict", index=True)
    # Core declares this as fields.Char(index=True) and nothing else;
    # unaccent= is not a valid parameter here and logs a warning on every
    # boot. FIX-009.
    parent_path = fields.Char(index=True)
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

    # FIX-025. Without these depends the field is NEVER computed.
    #
    # A stored computed field with no dependencies is not scheduled for
    # computation at all, so the column stayed NULL for the life of the
    # record -- on create, on rename, and for children. Two consequences,
    # and the quiet one is worse:
    #
    #   the Sourcing Categories list showed a blank Complete Name column
    #   for every row, which is what user testing reported; and
    #
    #   _order = "complete_name" was ordering the whole model on a NULL
    #   column, so the list came back in arbitrary order and looked merely
    #   unsorted rather than broken.
    #
    # It survived this long because _rec_name falls back to `name`, so
    # every Many2one to a category displayed correctly. Only the list of
    # categories themselves ever showed the stored value.
    @api.depends("name", "parent_id", "parent_id.complete_name")
    def _compute_complete_name(self):
        for rec in self:
            rec.complete_name = (
                "%s / %s" % (rec.parent_id.complete_name, rec.name)
                if rec.parent_id else rec.name)
