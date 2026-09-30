# -*- coding: utf-8 -*-
"""Post-install assertions.

TSD-AUC-001 #3.3: every column that participates in ranking or arithmetic
comparison must be PostgreSQL ``numeric``, never ``double precision``.
Mixed-type comparison produces non-deterministic ordering at the sixth
decimal place, which surfaces first as intermittently failing tie-break
tests and eventually as a disputed award.

This hook fails the install rather than letting that reach production.
"""
import logging

_logger = logging.getLogger(__name__)

NUMERIC_COLUMNS = [
    ("auction_bid_line", "norm_value"),
    ("auction_bid_line", "price_base"),
    ("auction_bid_line", "landed_value"),
    ("auction_bid_line", "composite_score"),
    ("auction_bid_line", "price_unit"),
]


def post_init_hook(env):
    """Verify numeric typing and required indexes after install."""
    failures = []
    for table, column in NUMERIC_COLUMNS:
        env.cr.execute(
            """
            SELECT data_type FROM information_schema.columns
             WHERE table_name = %s AND column_name = %s
            """,
            (table, column),
        )
        row = env.cr.fetchone()
        if not row:
            failures.append("%s.%s is missing" % (table, column))
        elif row[0] != "numeric":
            failures.append(
                "%s.%s is %s, expected numeric (TSD #3.3)" % (table, column, row[0])
            )
    if failures:
        raise AssertionError(
            "Auction engine post-install check failed:\n  " + "\n  ".join(failures)
        )
    _logger.info("Auction engine: numeric typing verified on %d columns",
                 len(NUMERIC_COLUMNS))
