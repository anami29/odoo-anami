# -*- coding: utf-8 -*-
"""Sealed reverse mechanisms — BR-MCH-005, BR-MCH-006."""
from ..registry import register
from .base import Strategy, VIS_NONE, ENTRY_FREE


@register
class SealedReverse(Strategy):
    """Sealed single envelope, revisable until close.

    Only the final active submission per bidder is evaluated; every superseded
    submission stays in the ledger.
    """
    code = "sealed_rev"
    label = "Sealed Reverse — revisable"
    direction = "reverse"
    sealed = True
    revisable = True
    supports_anti_snipe = True

    def visible_state(self, participant, lot, own_bid, ranking):
        # Nothing about the field is disclosed before opening -- not the
        # bidder count, not a rank, not a band. BR-BID-022 withholds the
        # count from the buyer as well.
        return {
            "lot_id": lot.id,
            "state": lot.state,
            "close_datetime": lot.close_datetime,
            "own_bid": own_bid,
            "visibility": VIS_NONE,
            "entry_mode": ENTRY_FREE,
            "revisable": True,
        }


@register
class SealedReverseNoRevision(SealedReverse):
    """Sealed single envelope, one submission only. BR-MCH-005.

    Strictest form of tender and the least forgiving of bidder error. Expect
    a higher rate of submission errors and later submissions; the confirmation
    friction in the portal is not optional for this mechanism.
    """
    code = "sealed_rev_nr"
    label = "Sealed Reverse — single submission"
    revisable = False
    supports_anti_snipe = False     # nothing to extend for

    def validate_entry(self, bid_vals, rules, current_best):
        errors = super().validate_entry(bid_vals, rules, current_best)
        if bid_vals.get("_has_prior_submission"):
            errors.append((
                "bid",
                "This event permits one submission only. Your first "
                "submission stands and cannot be replaced.",
            ))
        return errors

    def visible_state(self, participant, lot, own_bid, ranking):
        state = super().visible_state(participant, lot, own_bid, ranking)
        state["revisable"] = False
        return state
