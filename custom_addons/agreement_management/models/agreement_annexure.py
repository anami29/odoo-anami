# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class AgreementAnnexure(models.Model):
    _name = 'agreement.annexure'
    _description = 'Supplementary Annexure'
    _inherit = ['agreement.document.mixin', 'mail.thread.main.attachment', 'mail.activity.mixin']
    _order = 'id desc'
    _rec_name = 'number'
    _check_company_auto = True

    number = fields.Char(string='Annexure Number', readonly=True, copy=False, index=True, tracking=True,
                         help="Generated on confirmation as <parent number>-ANN-<nn> (BR-ANN-004).")
    agreement_id = fields.Many2one(
        'agreement.agreement', string='Parent Agreement', required=True, ondelete='restrict', index=True, tracking=True,
        domain="[('state', '=', 'executed')]",
        help="Each annexure is linked to exactly one Fully Signed / Executed agreement (BR-ANN-002).")
    company_id = fields.Many2one(related='agreement_id.company_id', store=True, index=True)
    currency_id = fields.Many2one(related='company_id.currency_id')
    user_id = fields.Many2one('res.users', string='Responsible', default=lambda self: self.env.user, tracking=True)
    annexure_type_id = fields.Many2one('agreement.annexure.type', string='Annexure Type', tracking=True, index=True)
    template_id = fields.Many2one(related='agreement_id.template_id', store=True, index=True)
    template_version_id = fields.Many2one(
        'agreement.template.version', string='Template Version', compute='_compute_template_version_id',
        store=True, precompute=True, readonly=True, index=True)
    template_version_name = fields.Char(related='template_version_id.name', string='Version', store=True)
    type_id = fields.Many2one(related='agreement_id.type_id', store=True)
    partner_a_id = fields.Many2one(related='agreement_id.partner_a_id', store=True, index=True)
    signatory_a_id = fields.Many2one(related='agreement_id.signatory_a_id', store=True, index=True)
    partner_b_id = fields.Many2one(related='agreement_id.partner_b_id', store=True, index=True)
    signatory_b_id = fields.Many2one(related='agreement_id.signatory_b_id', store=True, index=True)
    parent_date_from = fields.Date(related='agreement_id.date_from', string='Parent Effective From')
    parent_date_to = fields.Date(related='agreement_id.date_to', string='Parent Effective To')
    date_effective = fields.Date(string='Effective Date', required=True, tracking=True, index=True,
                                 default=fields.Date.context_today, help="Each annexure has its own effective date (BR-ANN-008).")
    content = fields.Html(string='Annexure Content', sanitize_attributes=False, sanitize_form=False,
                          help="Amended clauses, revised terms, additional scope… (BR-ANN-006). Same placeholders as templates.")
    annexure_properties = fields.Properties(
        string='Annexure Information', definition='template_version_id.annexure_properties_definition', copy=True)
    signature_ids = fields.One2many('agreement.signature', 'annexure_id', string='Signature Areas', copy=False)
    distribution_ids = fields.One2many('agreement.distribution', 'annexure_id', string='Distribution History', copy=False)
    sequence_number = fields.Integer(string='Annexure #', readonly=True, copy=False)
    summary = fields.Char(string='Summary of Changes', tracking=True)

    _sql_constraints = [
        ('number_uniq', 'unique(number, company_id)', 'The annexure number must be unique.'),
    ]

    @api.depends('agreement_id.template_version_id')
    def _compute_template_version_id(self):
        for rec in self:
            rec.template_version_id = rec.agreement_id.template_version_id

    @api.depends('number', 'agreement_id.number')
    def _compute_display_name(self):
        for rec in self:
            if rec.number:
                rec.display_name = rec.number
            else:
                rec.display_name = _('New annexure to %s', rec.agreement_id.number or '')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            parent = self.env['agreement.agreement'].browse(vals.get('agreement_id'))
            if parent.state != 'executed':
                raise UserError(_("A Supplementary Annexure can only be created from a Fully Signed / Executed agreement."))
            if not vals.get('content'):
                vals['content'] = parent.template_version_id.annexure_content
            vals.setdefault('template_version_id', parent.template_version_id.id)
            if not vals.get('date_effective'):
                today = fields.Date.context_today(self)
                vals['date_effective'] = max(today, parent.date_from) if parent.date_from else today
        annexures = super().create(vals_list)
        for annexure in annexures:
            annexure.agreement_id.message_post(body=_(
                "Supplementary Annexure created (%s) — the original executed agreement remains unchanged.",
                annexure.display_name))
        return annexures

    @api.constrains('date_effective', 'agreement_id')
    def _check_dates(self):
        for rec in self:
            if rec.date_effective and rec.agreement_id.date_from and rec.date_effective < rec.agreement_id.date_from:
                raise ValidationError(_("The annexure effective date cannot be earlier than the parent agreement's effective date."))

    # --------------------------------------------------------- mixin hooks
    def _document_label(self):
        return _('Supplementary Annexure')

    def _get_locked_fields(self):
        return {'agreement_id', 'annexure_type_id', 'date_effective', 'content', 'annexure_properties', 'summary'}

    def _get_party_partner(self, party):
        return self.agreement_id.partner_a_id if party == 'a' else self.agreement_id.partner_b_id

    def _get_signatory(self, party):
        return self.agreement_id.signatory_a_id if party == 'a' else self.agreement_id.signatory_b_id

    def _get_template_version(self):
        return self.agreement_id.template_version_id

    def _get_areas_applies_to(self):
        return 'annexure'

    def _get_signature_document_field(self):
        return 'annexure_id'

    def _get_content_source(self):
        return self.content

    def _get_properties_field(self):
        return 'annexure_properties'

    def _get_report_ref(self):
        return 'agreement_management.action_report_agreement_annexure'

    def _get_executed_mail_template(self):
        return self.company_id.agreement_annexure_template_id or \
            self.env.ref('agreement_management.mail_template_annexure_executed', raise_if_not_found=False)

    def _get_notification_mail_template(self, party):
        xmlid = 'agreement_management.mail_template_annexure_ready_party_%s' % party
        return self.env.ref(xmlid, raise_if_not_found=False)

    def _get_parent_agreement(self):
        return self.agreement_id

    def _get_extra_render_context(self):
        return {'summary': self.summary or ''}

    def _assign_number(self):
        self.ensure_one()
        if self.number:
            return self.number
        siblings = self.search([('agreement_id', '=', self.agreement_id.id), ('sequence_number', '>', 0)])
        next_no = (max(siblings.mapped('sequence_number')) if siblings else 0) + 1
        self._wf().write({'sequence_number': next_no})
        return '%s-ANN-%02d' % (self.agreement_id.number, next_no)

    def _check_before_confirm(self):
        super()._check_before_confirm()
        if self.agreement_id.state != 'executed':
            raise UserError(_("The parent agreement must be Fully Signed / Executed."))
        if not (self.content or '').strip():
            raise UserError(_("Please enter the annexure content before confirming."))

    def action_view_parent(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'agreement.agreement',
            'res_id': self.agreement_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_view_signatures(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('agreement_management.action_agreement_signature')
        action['domain'] = [('annexure_id', '=', self.id)]
        return action

    def action_view_distribution(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('agreement_management.action_agreement_distribution')
        action['domain'] = [('annexure_id', '=', self.id)]
        return action
