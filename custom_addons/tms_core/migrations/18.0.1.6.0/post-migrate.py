from odoo import SUPERUSER_ID, api

from odoo.addons.tms_core.hooks import tms_indian_locale


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    tms_indian_locale(env)
    # incidents get their INC/YYYY/NNNN reference, oldest first
    Seq = env["ir.sequence"]
    for inc in env["tms.trip.incident"].search([("reference", "=", False)], order="id"):
        inc.reference = Seq.next_by_code("tms.trip.incident")
    # damage claims are numbered DC/YYYY/NNNN from now on (the mockup's series); issued numbers stay
    seq = env.ref("tms_core.seq_tms_damage_claim", raise_if_not_found=False)
    if seq and seq.prefix == "CLM/%(year)s/":
        seq.prefix = "DC/%(year)s/"
