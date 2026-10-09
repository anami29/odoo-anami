# -*- coding: utf-8 -*-
"""Turn off tablet signing on existing companies.

The setting defaulted to True, which let any Agreement User capture Party A's
signature, and a manager could sign anything regardless. Signing is now the
designated signatory's alone, so an existing company carrying the old default
would keep the looser behaviour the new rule exists to remove. Re-enable it
deliberately in Agreements > Configuration > Settings if tablet signing is
genuinely wanted.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    companies = env['res.company'].search([('agreement_allow_facilitated_signing', '=', True)])
    if companies:
        companies.write({'agreement_allow_facilitated_signing': False})
        _logger.info("Agreement: tablet signing disabled on %s company(ies); re-enable it "
                     "deliberately if it is wanted", len(companies))
