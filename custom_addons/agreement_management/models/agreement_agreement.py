# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class Agreement(models.Model):
    _name = 'agreement.agreement'
    _description = 'Agreement'
    _inherit = ['agreement.document.mixin', 'mail.thread.main.attachment', 'mail.activity.mixin',
                'portal.mixin']
    _order = 'id desc'
    _rec_name = 'number'
    _check_company_auto = True

    number = fields.Char(string='Agreement Number', readonly=True, copy=False, index=True, tracking=True,
                         help="Generated automatically on confirmation (BR-CON-003); drafts have no number.")
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company, index=True)
    currency_id = fields.Many2one(related='company_id.currency_id')
    user_id = fields.Many2one('res.users', string='Responsible', default=lambda self: self.env.user, tracking=True)
    template_id = fields.Many2one(
        'agreement.template', string='Template', required=True, tracking=True, index=True,
        domain="[('active_version_id', '!=', False), ('company_id', '=', company_id)]",
        help="Only templates with an Active version can be selected (BR-AGR-002).")
    template_version_id = fields.Many2one(
        'agreement.template.version', string='Template Version', compute='_compute_template_version_id',
        store=True, precompute=True, readonly=True, index=True,
        help="The Active version at creation time. Locked on confirmation (BR-VER-006/007).")
    template_version_name = fields.Char(related='template_version_id.name', string='Version', store=True)
    type_id = fields.Many2one('agreement.type', string='Agreement Type', compute='_compute_type_id', store=True,
                              readonly=False, tracking=True, index=True)
    partner_a_id = fields.Many2one(
        'res.partner', string='Party A', required=True, tracking=True, index=True,
        default=lambda self: self.env.company.partner_id,
        domain="['|', ('company_id', '=', False), ('company_id', '=', company_id)]")
    signatory_a_id = fields.Many2one(
        'res.partner', string='Signatory A', tracking=True, index=True,
        domain="['|', ('id', '=', partner_a_id), ('id', 'child_of', partner_a_id)]",
        help="Person authorised to sign on behalf of Party A (BR-PAR-002).")
    partner_b_id = fields.Many2one(
        'res.partner', string='Party B', required=True, tracking=True, index=True,
        domain="['|', ('company_id', '=', False), ('company_id', '=', company_id)]")
    signatory_b_id = fields.Many2one(
        'res.partner', string='Signatory B', tracking=True, index=True,
        domain="['|', ('id', '=', partner_b_id), ('id', 'child_of', partner_b_id)]",
        help="Person authorised to sign on behalf of Party B (BR-PAR-004).")
    date_from = fields.Date(string='Effective From', required=True, tracking=True, index=True,
                            default=fields.Date.context_today)
    date_to = fields.Date(string='Effective To / Expiry', tracking=True, index=True)
    agreement_properties = fields.Properties(
        string='Other Information', definition='template_version_id.properties_definition', copy=True)
    signature_ids = fields.One2many('agreement.signature', 'agreement_id', string='Signature Areas', copy=False)
    distribution_ids = fields.One2many('agreement.distribution', 'agreement_id', string='Distribution History', copy=False)
    annexure_ids = fields.One2many('agreement.annexure', 'agreement_id', string='Supplementary Annexures', copy=False)
    annexure_count = fields.Integer(compute='_compute_annexure_count', store=True)
    executed_annexure_count = fields.Integer(compute='_compute_annexure_count', store=True)
    note = fields.Html(string='Internal Notes')

    _sql_constraints = [
        ('number_company_uniq', 'unique(number, company_id)', 'The agreement number must be unique.'),
    ]

    # ---------------------------------------------------------------- compute
    @api.depends('number', 'template_id.name')
    def _compute_display_name(self):
        for rec in self:
            if rec.number:
                rec.display_name = rec.number
            else:
                rec.display_name = _('New %s', rec.template_id.name) if rec.template_id else _('New')

    @api.depends('template_id')
    def _compute_template_version_id(self):
        """The active version is identified automatically (BR-AGR-002). The stored
        value is only ever recomputed while the agreement is a draft, so activating
        a new version never changes existing agreements (BR-VER-006)."""
        for rec in self:
            if rec.state and rec.state != 'draft' and rec.template_version_id:
                rec.template_version_id = rec.template_version_id
            else:
                rec.template_version_id = rec.template_id.active_version_id

    @api.depends('template_id')
    def _compute_type_id(self):
        for rec in self:
            if rec.template_id and (not rec.type_id or rec.state == 'draft'):
                rec.type_id = rec.template_id.type_id or rec.type_id
            else:
                rec.type_id = rec.type_id

    @api.depends('annexure_ids.state')
    def _compute_annexure_count(self):
        for rec in self:
            rec.annexure_count = len(rec.annexure_ids.filtered(lambda a: a.state != 'cancel'))
            rec.executed_annexure_count = len(rec.annexure_ids.filtered(lambda a: a.state == 'executed'))

    # ---------------------------------------------------------------- CRUD
    @api.model_create_multi
    def create(self, vals_list):
        # The Properties field only keeps the values passed at creation when the
        # definition record is part of the values: resolve the active version here.
        for vals in vals_list:
            if vals.get('template_id') and not vals.get('template_version_id'):
                template = self.env['agreement.template'].browse(vals['template_id'])
                vals['template_version_id'] = template.active_version_id.id
        return super().create(vals_list)

    # ------------------------------------------------------------ onchange
    @api.model
    def _signatory_belongs_to(self, signatory, partner):
        """A signatory must be the party itself or one of its contacts."""
        if not signatory or not partner:
            return True
        return signatory == partner or signatory.parent_id == partner or \
            signatory.commercial_partner_id == partner.commercial_partner_id

    @api.onchange('partner_a_id')
    def _onchange_partner_a_id(self):
        if not self._signatory_belongs_to(self.signatory_a_id, self.partner_a_id):
            self.signatory_a_id = False
        if self.partner_a_id and not self.signatory_a_id:
            self.signatory_a_id = self._default_signatory(self.partner_a_id)

    @api.onchange('partner_b_id')
    def _onchange_partner_b_id(self):
        if not self._signatory_belongs_to(self.signatory_b_id, self.partner_b_id):
            self.signatory_b_id = False
        if self.partner_b_id and not self.signatory_b_id:
            self.signatory_b_id = self._default_signatory(self.partner_b_id)

    @api.model
    def _default_signatory(self, partner):
        """Propose the party itself when it is an individual, otherwise nothing —
        the user must select the authorised signatory."""
        if partner and not partner.is_company:
            return partner
        return self.env['res.partner']

    # ---------------------------------------------------------- constraints
    @api.constrains('partner_a_id', 'partner_b_id')
    def _check_parties(self):
        """BR-PAR-005"""
        for rec in self:
            if rec.partner_a_id and rec.partner_b_id and rec.partner_a_id == rec.partner_b_id \
                    and not rec.company_id.agreement_allow_same_party:
                raise ValidationError(_("Party A and Party B cannot be the same party unless the business "
                                        "configuration explicitly permits it."))

    @api.constrains('signatory_a_id', 'partner_a_id', 'signatory_b_id', 'partner_b_id')
    def _check_signatories(self):
        for rec in self:
            for party, signatory in (('A', rec.signatory_a_id), ('B', rec.signatory_b_id)):
                partner = rec.partner_a_id if party == 'A' else rec.partner_b_id
                if not rec._signatory_belongs_to(signatory, partner):
                    raise ValidationError(_("Signatory %s (%s) must be a contact of Party %s (%s).",
                                            party, signatory.name, party, partner.name))

    @api.constrains('date_from', 'date_to')
    def _check_dates(self):
        """BR-DATE-003"""
        for rec in self:
            if rec.date_from and rec.date_to and rec.date_to < rec.date_from:
                raise ValidationError(_("The expiry date cannot be earlier than the effective date."))

    # --------------------------------------------------------- mixin hooks
    def _document_label(self):
        return _('Agreement')

    def _compute_access_url(self):
        """Portal address of an agreement, used by the My Account pages and the
        tokenised link a signatory can be sent."""
        super()._compute_access_url()
        for agreement in self:
            agreement.access_url = '/my/agreements/%s' % agreement.id

    def _get_locked_fields(self):
        return {'template_id', 'template_version_id', 'type_id', 'company_id', 'partner_a_id', 'signatory_a_id',
                'partner_b_id', 'signatory_b_id', 'date_from', 'date_to', 'agreement_properties'}

    def _get_party_partner(self, party):
        return self.partner_a_id if party == 'a' else self.partner_b_id

    def _get_signatory(self, party):
        return self.signatory_a_id if party == 'a' else self.signatory_b_id

    def _get_template_version(self):
        return self.template_version_id

    def _get_content_source(self):
        return self.template_version_id.content

    def _get_report_ref(self):
        return 'agreement_management.action_report_agreement'

    def _get_executed_mail_template(self):
        return self.company_id.agreement_executed_template_id or \
            self.env.ref('agreement_management.mail_template_agreement_executed', raise_if_not_found=False)

    def _get_notification_mail_template(self, party):
        xmlid = 'agreement_management.mail_template_agreement_ready_party_%s' % party
        return self.env.ref(xmlid, raise_if_not_found=False)

    def _assign_number(self):
        self.ensure_one()
        if self.number:
            return self.number
        return self.env['ir.sequence'].with_company(self.company_id).next_by_code('agreement.agreement')

    def _check_before_confirm(self):
        super()._check_before_confirm()
        if self.template_version_id != self.template_id.active_version_id:
            raise UserError(_("Only the current Active template version can be used for new agreements. "
                              "This draft references version %s; the active version is %s. Re-select the template.",
                              self.template_version_id.name, self.template_id.active_version_id.name or '-'))

    # ---------------------------------------------------------------- actions
    def action_create_annexure(self):
        """BR-ANN-001/003: create a draft annexure inheriting the parent's data."""
        self.ensure_one()
        if self.state != 'executed':
            raise UserError(_("A Supplementary Annexure can only be created from a Fully Signed / Executed agreement."))
        annexure = self.env['agreement.annexure'].create({
            'agreement_id': self.id,
            'content': self.template_version_id.annexure_content,
        })
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'agreement.annexure',
            'res_id': annexure.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_view_annexures(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('agreement_management.action_agreement_annexure')
        action['domain'] = [('agreement_id', '=', self.id)]
        action['context'] = {'default_agreement_id': self.id}
        return action

    def action_view_signatures(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('agreement_management.action_agreement_signature')
        action['domain'] = [('agreement_id', '=', self.id)]
        return action

    def action_view_distribution(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('agreement_management.action_agreement_distribution')
        action['domain'] = [('agreement_id', '=', self.id)]
        return action

    def action_reselect_active_version(self):
        """Refresh a draft to the current Active version of its template."""
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_("Only drafts can be moved to the current active version."))
            rec.template_version_id = rec.template_id.active_version_id
        return True
