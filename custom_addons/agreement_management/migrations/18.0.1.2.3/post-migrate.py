# -*- coding: utf-8 -*-
"""Refresh the fingerprint of documents whose page areas were added after it.

_prepare_page_signing adds a signature area per page when Party A completes, but did
not refresh document_fingerprint, which covers the signature configuration. Those
documents then failed their own integrity check and Party B could not sign a single
page.

Deliberately narrow: only documents that have page records and are still awaiting
Party B. Recomputing a fingerprint erases the evidence it exists to preserve, so it
is done only for the set this defect created, and each one is named in the log.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    for model, field in (('agreement.agreement', 'agreement_id'),
                         ('agreement.annexure', 'annexure_id')):
        paged = env['agreement.page'].search([(field, '!=', False)]).mapped(field)
        for doc in paged.filtered(lambda d: d.state == 'pending_b' and d.document_fingerprint):
            if doc._integrity_check(raise_error=False):
                continue                      # already consistent, leave it alone
            doc._wf().write({'document_fingerprint': doc._document_fingerprint()})
            _logger.info("Agreement: fingerprint of %s refreshed to cover its page "
                         "signature areas", doc.number)
