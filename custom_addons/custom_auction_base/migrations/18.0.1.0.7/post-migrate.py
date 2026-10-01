# -*- coding: utf-8 -*-
"""Backfill mechanism_id from the old mechanism code column.

The mechanism moved from a Selection to a Many2one so that the dropdown can
be filtered by direction. Existing events and templates hold the code in the
varchar column; this maps it to the new record.

Runs post-migrate so auction.mechanism rows exist by the time it looks for
them: the sync function runs during data loading, which is earlier.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    for table, column in (("auction_event", "mechanism_id"),
                          ("auction_event_template", "mechanism_id")):
        cr.execute("""
            SELECT column_name FROM information_schema.columns
             WHERE table_name = %s AND column_name = 'mechanism'
        """, (table,))
        if not cr.fetchone():
            continue

        cr.execute("""
            UPDATE {table} t
               SET {column} = m.id
              FROM auction_mechanism m
             WHERE m.code = t.mechanism
               AND t.{column} IS NULL
        """.format(table=table, column=column))
        _logger.info("Auction migration: linked %d row(s) in %s",
                     cr.rowcount, table)

        cr.execute("""
            SELECT COUNT(*) FROM {table}
             WHERE {column} IS NULL AND mechanism IS NOT NULL
        """.format(table=table, column=column))
        orphans = cr.fetchone()[0]
        if orphans:
            _logger.warning(
                "Auction migration: %d row(s) in %s reference a mechanism "
                "code with no matching record. Set the mechanism by hand.",
                orphans, table)
