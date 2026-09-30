# -*- coding: utf-8 -*-
"""Mechanism strategy registry — TSD-AUC-001 #6.2.

A mechanism is a named configuration of the rules engine, not a parallel
implementation. Adding a mechanism must require no change to
``auction.bid.submit``; if it does, the hook set is incomplete and that is the
defect, not the mechanism.
"""
import logging

_logger = logging.getLogger(__name__)

_REGISTRY = {}


class MechanismError(Exception):
    pass


def register(strategy_cls):
    """Class decorator. Registers a strategy under its ``code``."""
    code = getattr(strategy_cls, "code", None)
    if not code:
        raise MechanismError("strategy %s has no code" % strategy_cls)
    if code in _REGISTRY:
        raise MechanismError("mechanism %s is already registered" % code)
    _REGISTRY[code] = strategy_cls()
    _logger.debug("Auction mechanism registered: %s", code)
    return strategy_cls


def get(code):
    try:
        return _REGISTRY[code]
    except KeyError:
        raise MechanismError("unknown mechanism: %s" % code)


def codes(direction=None):
    """Registered mechanism codes, optionally filtered by direction."""
    return sorted(
        c for c, s in _REGISTRY.items()
        if direction is None or s.direction == direction
    )


def selection(direction=None):
    """Odoo Selection field values."""
    return [(c, get(c).label) for c in codes(direction)]
