# -*- coding: utf-8 -*-
from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    agreement_auto_email = fields.Boolean(
        string='Automatically Email Executed Agreement', default=True,
        help="Send the final executed PDF to the default recipients as soon as an agreement or annexure "
             "becomes Fully Signed / Executed (BR-EMAIL-007).")
    agreement_email_party_a = fields.Boolean(string='Email Party A', default=True)
    agreement_email_party_b = fields.Boolean(string='Email Party B', default=True)
    agreement_email_signatory_a = fields.Boolean(string='Email Signatory A', default=False)
    agreement_email_signatory_b = fields.Boolean(string='Email Signatory B', default=False)
    agreement_email_extra_partner_ids = fields.Many2many(
        'res.partner', 'agreement_company_extra_recipient_rel', string='Additional Recipients',
        help="Internal users or contacts that always receive executed documents (BR-EMAIL-008).")
    agreement_executed_template_id = fields.Many2one(
        'mail.template', string='Executed Agreement Email Template',
        domain="[('model', '=', 'agreement.agreement')]")
    agreement_annexure_template_id = fields.Many2one(
        'mail.template', string='Executed Annexure Email Template',
        domain="[('model', '=', 'agreement.annexure')]")
    agreement_allow_same_party = fields.Boolean(
        string='Allow Party A and Party B to be identical', default=False,
        help="By default the system prevents Party A and Party B from being the same partner (BR-PAR-005).")
    agreement_allow_facilitated_signing = fields.Boolean(
        string='Allow tablet signing captured by internal users', default=False,
        help="Party A only. When enabled, any Agreement User can open the signing screen on a shared "
             "tablet so the designated signatory signs in person. Off by default, because it lets "
             "someone other than the signatory capture the signature. When disabled, only the "
             "signatory's own user can sign — a manager cannot sign on their behalf. Party B always "
             "signs from the customer portal and is unaffected by this setting.")
