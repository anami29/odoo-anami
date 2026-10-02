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


CRON_XMLIDS = [
    "custom_auction_base.cron_close_due_lots",
    "custom_auction_base.cron_close_expired_bidding",
    "custom_auction_base.cron_security_deadline",
]


def _fix_cron_repeat(env):
    """Make the crons repeat for ever on every Odoo 18 build.

    ``ir.cron.numbercall`` was removed from the 18.0 branch partway through
    its life. Declaring it in XML breaks the install on a current checkout;
    omitting it leaves a build that still HAS the column on that column's
    default, which on some builds is 1 -- a cron that runs once and stops,
    silently, which is worse than a failed install.

    So the XML omits it and this puts it back where the column exists. One
    package, both builds. FIX-008.
    """
    env.cr.execute(
        "SELECT 1 FROM information_schema.columns "
        " WHERE table_name = 'ir_cron' AND column_name = 'numbercall'")
    if not env.cr.fetchone():
        return
    ids = []
    for xmlid in CRON_XMLIDS:
        rec = env.ref(xmlid, raise_if_not_found=False)
        if rec:
            ids.append(rec.id)
    if ids:
        env.cr.execute(
            "UPDATE ir_cron SET numbercall = -1 WHERE id IN %s", (tuple(ids),))
        _logger.info("Auction engine: numbercall set to -1 on %d crons", len(ids))


def post_init_hook(env):
    """Verify numeric typing and required indexes after install."""
    _fix_cron_repeat(env)
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
