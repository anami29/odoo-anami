# -*- coding: utf-8 -*-
"""Sealed forward mechanism — BR-MCH-022.

The default for scrap and asset disposal in this edition. MEC-AUC-002 #5.1
established that against a local trader pool sealed tender outperforms open
ascending bidding, because a ring depends on being able to verify that its
members held back, and concealment removes that ability.
"""
from ..registry import register
from .base import Strategy, VIS_NONE, ENTRY_FREE


@register
class SealedForward(Strategy):
    code = "sealed_fwd"
    label = "Sealed Tender — forward (disposal)"
    direction = "forward"
    sealed = True
    revisable = True
    supports_anti_snipe = True

    def rank_direction(self):
        return "DESC"               # highest responsive bid wins

    def visible_state(self, participant, lot, own_bid, ranking):
        return {
            "lot_id": lot.id,
            "state": lot.state,
            "close_datetime": lot.close_datetime,
            "own_bid": own_bid,
            "visibility": VIS_NONE,
            "entry_mode": ENTRY_FREE,
            "revisable": True,
            # Reserve is never disclosed unless explicitly configured.
            # BR-MCH-025: an unsold lot does not reveal why.
            "reserve_disclosed": bool(lot.disclose_limit),
            "reserve_price": lot.reserve_price if lot.disclose_limit else None,
        }

    def on_close(self, lot, ranking):
        """Highest bid at or above reserve. Below reserve the lot is unsold
        and the reserve value is still not disclosed."""
        if not ranking:
            return []
        top = ranking[0]
        if lot.reserve_price and top["norm_value"] < lot.reserve_price:
            return []
        return [top]
