# -*- coding: utf-8 -*-
"""Map existing free-text delivery addresses onto system locations.

Before 18.0.1.1.1 the delivery destination was free text. It is now a
Many2one to stock.location, with the text kept as a fallback for
destinations that are not configured locations.

Nothing is deleted. Text that matches a location by name is ALSO linked;
text that matches nothing stays exactly where it is and keeps working. A
migration that threw away the unmatched cases would have discarded the
project sites and customer addresses the free-text field existed for.
"""
import logging

_logger = logging.getLogger(__name__)

TABLES = (
    ("auction_line", "auction.line"),
    ("auction_line_schedule", "auction.line.schedule"),
    ("auction_event_template_line", "auction.event.template.line"),
)


def migrate(cr, version):
    Location = None
    try:
        from odoo import api, SUPERUSER_ID
        env = api.Environment(cr, SUPERUSER_ID, {})
        Location = env["stock.location"].sudo()
    except Exception:
        _logger.warning("stock.location unavailable; leaving text in place")
        return

    locations = Location.search([("usage", "=", "internal")])
    index = {}
    for loc in locations:
        for key in (loc.complete_name, loc.name):
            if key:
                index.setdefault(key.strip().lower(), loc.id)

    for table, model in TABLES:
        cr.execute(
            "SELECT column_name FROM information_schema.columns "
            " WHERE table_name = %s AND column_name = 'delivery_location'",
            (table,))
        if not cr.fetchone():
            continue
        cr.execute(
            "SELECT id, delivery_location FROM %s "
            " WHERE delivery_location IS NOT NULL "
            "   AND delivery_location <> ''" % table)
        rows = cr.fetchall()
        matched = 0
        for rec_id, text in rows:
            loc_id = index.get((text or "").strip().lower())
            if not loc_id:
                continue
            cr.execute(
                "UPDATE %s SET delivery_location_id = %%s, "
                " delivery_location = NULL WHERE id = %%s" % table,
                (loc_id, rec_id))
            matched += 1
        _logger.info(
            "%s: %d of %d delivery addresses matched a system location; "
            "the rest keep their text.", model, matched, len(rows))
