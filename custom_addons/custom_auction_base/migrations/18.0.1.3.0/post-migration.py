# -*- coding: utf-8 -*-
"""FIX-025. Fill in the sourcing category names that were never computed.

complete_name is a stored computed field that, until now, carried no
@api.depends and was therefore never scheduled for computation at all:
every row has held NULL since it was created. Adding the depends fixes
new and edited records; existing rows need this.

Recomputed parents first, because a child's name is built from its
parent's.
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    Category = env["auction.category"].with_context(active_test=False).sudo()
    categories = Category.search([], order="parent_path")
    if not categories:
        return
    env.add_to_compute(Category._fields["complete_name"], categories)
    categories.flush_recordset(["complete_name"])
    blank = categories.filtered(lambda c: not c.complete_name)
    if blank:
        # Nothing should land here; say so loudly rather than leaving a
        # blank column for somebody to rediscover.
        env["ir.logging"].sudo().create({
            "name": "custom_auction_base",
            "type": "server",
            "level": "WARNING",
            "dbname": cr.dbname,
            "message": "FIX-025: %d sourcing categories still have no "
                       "complete_name after recompute: %s"
                       % (len(blank), blank.mapped("name")),
            "path": "migrations/18.0.1.3.0",
            "func": "migrate",
            "line": "0",
        })
