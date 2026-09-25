# -*- coding: utf-8 -*-
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """Refresh stored property definitions so mandatory fields gain their marker.

    properties_definition is a stored compute: changing the compute code does not rewrite
    what is already stored, and an Active version is locked against ordinary writes. A
    migration runs as superuser, which the lock deliberately lets through.
    """
    env = api.Environment(cr, SUPERUSER_ID, {})
    versions = env['agreement.template.version'].search([])
    if not versions:
        return
    versions._compute_properties_definition()
    versions.flush_recordset(['properties_definition', 'annexure_properties_definition'])
