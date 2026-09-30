# -*- coding: utf-8 -*-
"""Strategy base — the seven hooks from TSD-AUC-001 #6.2.

A strategy declares behaviour. It never reimplements submission, ranking or
closure; those stay generic so that a defect is fixed once.
"""

VIS_NONE = "none"
VIS_RANK = "rank"
VIS_RANK_BAND = "rank_band"
VIS_BEST = "best"
VIS_FULL = "full"
VIS_STEP = "step"

ENTRY_FREE = "free"
ENTRY_STEP_LOCKED = "step_locked"
ENTRY_ACCEPT_DECLINE = "accept_decline"


class Strategy(object):
    """Base mechanism strategy."""

    code = None
    label = None
    direction = "reverse"          # reverse | forward
    sealed = False                 # commercial values encrypted until opening
    revisable = True               # may a bidder replace a submitted bid
    supports_anti_snipe = True
    supports_partial_qty = False
    clock_driven = False

    # -- hooks --------------------------------------------------------------

    def default_rules(self):
        """Rule values this mechanism implies. Used to pre-populate and to
        validate an operator override."""
        return {
            "visibility": VIS_NONE if self.sealed else VIS_RANK,
            "entry_mode": ENTRY_FREE,
            "anti_snipe": self.supports_anti_snipe,
            "partial_qty": self.supports_partial_qty,
        }

    def validate_entry(self, bid_vals, rules, current_best):
        """Mechanism-specific validation beyond the generic improvement check.

        Return a list of ``(field, message)``. An empty list means accepted.
        """
        return []

    def rank_basis(self):
        """Column that ranking reads. Ranking itself stays generic."""
        return "norm_value"

    def rank_direction(self):
        """ASC for reverse (lowest wins), DESC for forward (highest wins)."""
        return "ASC" if self.direction == "reverse" else "DESC"

    def visible_state(self, participant, lot, own_bid, ranking):
        """The single point at which disclosure policy is enforced.

        Everything a bidder is shown passes through here. A controller that
        serialises an engine model directly is a defect, because field
        omission in a view is not a security control.
        """
        state = {
            "lot_id": lot.id,
            "state": lot.state,
            "close_datetime": lot.close_datetime,
            "own_bid": own_bid,
            "visibility": self.default_rules()["visibility"],
        }
        return state

    def on_clock_tick(self, lot):
        """Implemented only by clock-driven mechanisms."""
        raise NotImplementedError("%s is not clock driven" % self.code)

    def on_close(self, lot, ranking):
        """Determine the winner set at close."""
        if not ranking:
            return []
        return [ranking[0]]

    def award_plan(self, lot, ranking):
        """Proposed allocation. The award module presents this; it does not
        commit it."""
        winners = self.on_close(lot, ranking)
        return [{"bid_line_id": w["bid_line_id"], "qty": lot_line_qty(lot, w)}
                for w in winners]


def lot_line_qty(lot, winner):
    """Full required quantity for a single-winner mechanism."""
    line = lot.line_ids.filtered(lambda l: l.id == winner.get("line_id"))
    return line.product_qty if line else 0.0
