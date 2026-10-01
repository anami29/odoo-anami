# -*- coding: utf-8 -*-
"""Mechanisms as records.

The mechanism was a Selection field, and Odoo cannot filter Selection
options by domain: domains apply to Many2one only. So a reverse event
offered forward mechanisms in the dropdown, and the mistake was caught by a
constraint on save rather than prevented at the point of choosing. Catching
a wrong choice after the user has made it is not the same as not offering it.

As records, the domain `[('direction','=',direction)]` works natively and
updates the moment direction changes.

These are NOT the source of truth. The strategy registry in
`services/registry.py` is; these rows are synced from it, so registering a
new strategy makes it selectable without a data file.
"""
import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class AuctionMechanism(models.Model):
    _name = "auction.mechanism"
    _description = "Auction Mechanism"
    _order = "direction, sequence, name"

    code = fields.Char(required=True, index=True, readonly=True)
    name = fields.Char(required=True, readonly=True)
    sequence = fields.Integer(default=10)
    direction = fields.Selection(
        [("reverse", "Reverse - procurement"),
         ("forward", "Forward - disposal")],
        required=True, index=True, readonly=True)

    sealed = fields.Boolean(readonly=True)
    revisable = fields.Boolean(readonly=True)
    supports_anti_snipe = fields.Boolean(readonly=True)
    supports_partial_qty = fields.Boolean(readonly=True)
    clock_driven = fields.Boolean(readonly=True)
    active = fields.Boolean(default=True)
    note = fields.Text()

    _sql_constraints = [
        ("mechanism_code_uq", "unique(code)", "Mechanism codes are unique."),
    ]

    @api.model
    def _sync_from_registry(self):
        """Mirror the strategy registry into records.

        Runs on install and on every upgrade, like the access grant. A
        strategy that disappears from the registry is archived rather than
        deleted, because events may still reference it.
        """
        from ..services import registry as mech_registry

        seen = []
        for index, code in enumerate(mech_registry.codes()):
            strategy = mech_registry.get(code)
            values = {
                "code": code,
                "name": strategy.label,
                "sequence": (index + 1) * 10,
                "direction": strategy.direction,
                "sealed": strategy.sealed,
                "revisable": strategy.revisable,
                "supports_anti_snipe": strategy.supports_anti_snipe,
                "supports_partial_qty": strategy.supports_partial_qty,
                "clock_driven": strategy.clock_driven,
                "active": True,
            }
            existing = self.search([("code", "=", code)], limit=1)
            if existing:
                existing.write(values)
            else:
                self.create(values)
            seen.append(code)

        stale = self.search([("code", "not in", seen), ("active", "=", True)])
        if stale:
            stale.write({"active": False})
            _logger.info("Auction: archived %d mechanism(s) no longer "
                         "registered: %s", len(stale),
                         ", ".join(stale.mapped("code")))
        _logger.info("Auction: %d mechanism(s) synced from the registry",
                     len(seen))
