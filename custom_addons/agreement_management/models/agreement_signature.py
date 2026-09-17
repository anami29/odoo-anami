# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .agreement_document_mixin import WORKFLOW_MARK


class AgreementSignature(models.Model):
    """One signature location instance on a confirmed agreement / annexure."""
    _name = 'agreement.signature'
    _description = 'Agreement Signature Area'
    _order = 'sequence, id'

    agreement_id = fields.Many2one('agreement.agreement', ondelete='cascade', index=True)
    annexure_id = fields.Many2one('agreement.annexure', ondelete='cascade', index=True)
    document_name = fields.Char(compute='_compute_document', store=True, string='Document')
    document_state = fields.Selection(related='agreement_id.state', string='Agreement Status')
    company_id = fields.Many2one('res.company', compute='_compute_document', store=True)
    sequence = fields.Integer(default=10)
    code = fields.Char(required=True)
    name = fields.Char(string='Section / Anchor', required=True)
    page = fields.Integer(default=1)
    position = fields.Char()
    party = fields.Selection([('a', 'Party A'), ('b', 'Party B')], required=True, index=True)
    kind = fields.Selection([('signature', 'Full signature'), ('initials', 'Initials')], default='signature', required=True)
    required = fields.Boolean(default=True)
    signatory_id = fields.Many2one('res.partner', compute='_compute_signatory', string='Signatory')
    signature = fields.Binary(string='Signature', attachment=True, copy=False)
    signed_on = fields.Datetime(readonly=True, copy=False)
    signed_by_user_id = fields.Many2one('res.users', string='Captured By', readonly=True, copy=False)
    signer_name = fields.Char(string='Signed By', readonly=True, copy=False)
    signer_ip = fields.Char(string='IP Address', readonly=True, copy=False)
    state = fields.Selection([
        ('pending', 'Not Started'),
        ('ready', 'Awaiting Signature'),
        ('locked', 'Locked'),
        ('signed', 'Signed'),
    ], compute='_compute_state', string='Status')

    @api.depends('agreement_id', 'annexure_id', 'agreement_id.number', 'annexure_id.number')
    def _compute_document(self):
        for sig in self:
            doc = sig.annexure_id or sig.agreement_id
            sig.document_name = doc.number if doc else ''
            sig.company_id = doc.company_id if doc else False

    @api.depends('party', 'agreement_id.signatory_a_id', 'agreement_id.signatory_b_id',
                 'annexure_id.signatory_a_id', 'annexure_id.signatory_b_id')
    def _compute_signatory(self):
        for sig in self:
            doc = sig.annexure_id or sig.agreement_id
            sig.signatory_id = doc._get_signatory(sig.party) if doc else False

    @api.depends('signature', 'party', 'agreement_id.state', 'annexure_id.state')
    def _compute_state(self):
        for sig in self:
            doc = sig.annexure_id or sig.agreement_id
            doc_state = doc.state if doc else 'draft'
            if sig.signature:
                sig.state = 'signed'
            elif (sig.party == 'a' and doc_state == 'pending_a') or (sig.party == 'b' and doc_state == 'pending_b'):
                sig.state = 'ready'
            elif sig.party == 'b' and doc_state == 'pending_a':
                sig.state = 'locked'
            else:
                sig.state = 'pending'

    def _get_document(self):
        self.ensure_one()
        return self.annexure_id or self.agreement_id

    def _wf(self):
        return self.with_context(agreement_workflow=WORKFLOW_MARK)

    def _is_internal_write(self):
        return self.env.su or self.env.context.get('agreement_workflow') == WORKFLOW_MARK

    @api.model_create_multi
    def create(self, vals_list):
        if not self._is_internal_write():
            raise UserError(_("Signature areas are created by the agreement workflow on confirmation."))
        return super().create(vals_list)

    def write(self, vals):
        """Signature areas are written by the signing workflow only (server-side marker or
        superuser); a signed area is immutable even for the workflow."""
        if not self._is_internal_write():
            raise UserError(_("Signature areas are managed by the signing workflow: use the signing screen."))
        for sig in self:
            if sig.signature and ({'signature', 'party', 'required', 'code', 'kind', 'signed_on', 'signer_name'} & set(vals)):
                raise UserError(_("Signature area %s is already signed and cannot be modified.", sig.name))
        return super().write(vals)

    def unlink(self):
        if not self._is_internal_write():
            raise UserError(_("Signature areas are managed by the signing workflow."))
        for sig in self:
            doc = sig._get_document()
            if doc and doc.state not in ('draft', 'cancel'):
                raise UserError(_("Signature areas of a confirmed document cannot be deleted."))
        return super().unlink()

    def action_sign(self):
        """Open the signing screen for the party of this area."""
        self.ensure_one()
        doc = self._get_document()
        return doc._open_sign_wizard(self.party)
