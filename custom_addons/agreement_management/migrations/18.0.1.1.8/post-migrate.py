# -*- coding: utf-8 -*-
"""Clean markers that leaked from the property label back into the field name.

The definition's `string` carries presentation, and _sync_fields_from_definition
wrote `string` straight onto agreement.template.field.name, so every round trip
appended another ' *' — fields were showing two asterisks, and would have grown a
third. Strip them back to the human label and rebuild the definitions.
"""
import logging

from odoo import SUPERUSER_ID, api

from odoo.addons.agreement_management.models.agreement_template import strip_label_decorations

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    cleaned = 0
    for field in env['agreement.template.field'].search([]):
        symbol = field.version_id.company_id.currency_id.symbol
        name = strip_label_decorations(field.name, symbol)
        if name and name != field.name:
            field.name = name
            cleaned += 1
    if cleaned:
        _logger.info("Agreement: stripped leaked label markers from %s field(s)", cleaned)

    versions = env['agreement.template.version'].search([])
    if versions:
        versions._compute_properties_definition()
        versions.flush_recordset(['properties_definition', 'annexure_properties_definition'])
