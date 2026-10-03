# -*- coding: utf-8 -*-
"""FIX-024. Give every tender document its own copy of its file.

post_init_hook runs on INSTALL only, so an existing deployment would never
reach the re-owning step without this. Documents created before the upload
fix may point at an attachment another record owns -- in the worst case a
compiled asset bundle, which Odoo deletes and recreates on every asset
rebuild, taking the document with it because the field cascades.
"""
from odoo import SUPERUSER_ID, api

from odoo.addons.custom_auction_base.hooks import _reown_borrowed_attachments


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    _reown_borrowed_attachments(env)
