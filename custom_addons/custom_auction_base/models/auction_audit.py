# -*- coding: utf-8 -*-
"""Immutable audit log — TSD-AUC-001 #16.

Written through a service that can open a DEDICATED cursor, so an audit entry
persists where the business transaction rolls back. A rejected late bid or a
refused envelope access is precisely when the audit record has most value.

The service is synchronous and its failure raises: silently losing audit
entries is a worse outcome than failing the action.
"""
import json
import logging

from odoo import _, api, fields, models, registry
from odoo.exceptions import AccessError

_logger = logging.getLogger(__name__)

#: Fields whose VALUES are never recorded, only the fact of change. The fact
#: is recorded as a hash so tampering is still detectable.
DENYLIST = {"envelope", "envelope_nonce", "dek_wrapped", "estimated_value",
            "ceiling_price", "reserve_price"}


class AuctionAudit(models.Model):
    _name = "auction.audit"
    _description = "Auction Audit Entry (immutable)"
    _order = "id desc"

    action = fields.Char(required=True, readonly=True, index=True)
    model = fields.Char(required=True, readonly=True, index=True)
    res_id = fields.Integer(required=True, readonly=True, index=True)
    event_id = fields.Many2one("auction.event", readonly=True, index=True,
                               ondelete="restrict")
    user_id = fields.Many2one("res.users", readonly=True)
    login = fields.Char(readonly=True)
    partner_id = fields.Many2one("res.partner", readonly=True)
    detail_json = fields.Text(readonly=True)
    client_ip = fields.Char(readonly=True)
    user_agent = fields.Char(readonly=True)
    create_date = fields.Datetime(readonly=True, index=True)

    def write(self, vals):
        raise AccessError(_("The audit log is immutable."))

    def unlink(self):
        raise AccessError(_("The audit log is immutable."))

    @api.model
    def _payload(self, action, model, res_id, event_id, detail):
        redacted = {}
        for key, value in (detail or {}).items():
            if key in DENYLIST:
                redacted[key] = "<redacted:%s>" % hash(str(value))
            else:
                redacted[key] = value
        return {
            "action": action,
            "model": model,
            "res_id": res_id,
            "event_id": event_id,
            "user_id": self.env.uid,
            "login": self.env.user.login,
            "partner_id": self.env.user.partner_id.id,
            "detail_json": json.dumps(redacted, default=str, sort_keys=True),
        }

    @api.model
    def log(self, action, model, res_id, event_id=None, detail=None):
        """Ordinary audit entry, inside the current transaction."""
        return super(AuctionAudit, self.sudo()).create(
            self._payload(action, model, res_id, event_id, detail))

    @api.model
    def log_isolated(self, action, model, res_id, event_id=None, detail=None):
        """Audit entry on a SEPARATE cursor, committed independently.

        Used where the calling transaction is about to be rolled back -- a
        late bid rejection, a refused envelope access. Failure to write is
        logged but does not mask the original rejection.
        """
        payload = self._payload(action, model, res_id, event_id, detail)
        try:
            with registry(self.env.cr.dbname).cursor() as cr:
                env = api.Environment(cr, self.env.uid, {})
                env["auction.audit"].sudo().create(payload)
        except Exception:
            _logger.exception("auction.audit: isolated audit write failed")
            raise
