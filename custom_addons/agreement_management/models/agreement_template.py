# -*- coding: utf-8 -*-
import base64
import re

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .agreement_document_mixin import render_document_html

VERSION_STATES = [
    ('draft', 'Draft'),
    ('active', 'Active'),
    ('superseded', 'Superseded'),
]

# Template-field types exposed to the Agreement Manager (BR-FLD-002) and their
# mapping onto Odoo property types.
# Appended to the label of a mandatory dynamic field; styled red by agreement_properties.js.
REQUIRED_MARK = '*'

# A label that already names a currency is left alone when tagging a monetary field.
CURRENCY_IN_LABEL_RE = re.compile(r'[$\u20ac\u00a3\u00a5\u20b9\u00a2]|\b(?:USD|EUR|GBP|INR|JPY|AED|SGD|AUD|CAD)\b',
                                  re.IGNORECASE)

FIELD_TYPES = [
    ('char', 'Text'),
    ('text', 'Long Text'),
    ('integer', 'Number (integer)'),
    ('float', 'Number (decimal)'),
    ('monetary', 'Currency'),
    ('date', 'Date'),
    ('datetime', 'Date & Time'),
    ('boolean', 'Boolean'),
    ('selection', 'Selection'),
    ('partner', 'Contact / Partner'),
    ('tags', 'Tags'),
]
FIELD_TYPE_TO_PROPERTY = {
    'char': 'char', 'text': 'char', 'integer': 'integer', 'float': 'float', 'monetary': 'float',
    'date': 'date', 'datetime': 'datetime', 'boolean': 'boolean', 'selection': 'selection',
    'partner': 'many2one', 'tags': 'tags',
}
PROPERTY_TO_FIELD_TYPE = {
    'char': 'char', 'integer': 'integer', 'float': 'float', 'date': 'date', 'datetime': 'datetime',
    'boolean': 'boolean', 'selection': 'selection', 'many2one': 'partner', 'tags': 'tags',
}

DEFAULT_AGREEMENT_CONTENT = """
<h2 style="text-align:center">AGREEMENT</h2>
<p style="text-align:center">Agreement No. {{agreement_number}} · Template {{template_code}} v{{template_version}}</p>
<p>This Agreement is made on {{effective_from}} between <strong>{{party_a}}</strong>, {{party_a_address}} ("Party A"),
and <strong>{{party_b}}</strong>, {{party_b_address}} ("Party B").</p>
<p><strong>1. Term.</strong> This Agreement is effective from {{effective_from}} until {{effective_to}}.</p>
<p><strong>2. Terms.</strong> Insert the agreement clauses here. Dynamic values can be inserted with placeholders such as
{{agreement_number}}, {{party_a}}, {{signatory_b}} or the technical name of any field defined on this template version.</p>
{{page_break}}
<h3>EXECUTION</h3>
<p>IN WITNESS WHEREOF the parties have executed this Agreement electronically on the dates stated against their respective signatures.</p>
<p>{{sig:A1}} {{sig:B1}}</p>
"""

DEFAULT_ANNEXURE_CONTENT = """
<h2 style="text-align:center">SUPPLEMENTARY ANNEXURE</h2>
<p style="text-align:center">Annexure No. {{agreement_number}} · to Agreement {{parent_number}} ({{template_code}} v{{template_version}})</p>
<p>This Supplementary Annexure is made on {{effective_from}} between <strong>{{party_a}}</strong> ("Party A") and
<strong>{{party_b}}</strong> ("Party B") and forms part of, without modifying the original executed text of, Agreement No. {{parent_number}}.</p>
<p><strong>1. Amendment.</strong> Describe the mutually agreed changes here.</p>
<p><strong>2. Effect.</strong> Save as expressly amended by this Annexure, the Agreement continues in full force.</p>
<p>{{sig:A1}} {{sig:B1}}</p>
"""


class AgreementTemplate(models.Model):
    _name = 'agreement.template'
    _description = 'Agreement Template'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'name'
    _check_company_auto = True

    name = fields.Char(string='Template Name', required=True, tracking=True)
    code = fields.Char(string='Template Code', required=True, tracking=True, copy=False,
                       help="Unique code, e.g. NDA, SERVICE-AGREEMENT, EMPANELMENT.")
    description = fields.Text()
    type_id = fields.Many2one('agreement.type', string='Category / Type', tracking=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    active = fields.Boolean(default=True)
    version_ids = fields.One2many('agreement.template.version', 'template_id', string='Versions')
    version_count = fields.Integer(compute='_compute_version_info')
    active_version_id = fields.Many2one(
        'agreement.template.version', string='Active Version', compute='_compute_active_version', store=True)
    draft_version_id = fields.Many2one('agreement.template.version', compute='_compute_version_info')
    agreement_ids = fields.One2many('agreement.agreement', 'template_id', string='Agreements')
    agreement_count = fields.Integer(compute='_compute_agreement_count')
    status = fields.Selection([
        ('no_version', 'No Version'),
        ('draft_only', 'Draft Only'),
        ('active', 'Active'),
    ], compute='_compute_active_version', store=True)

    _sql_constraints = [
        ('code_company_uniq', 'unique(code, company_id)', 'The template code must be unique per company.'),
    ]

    @api.depends('version_ids.state')
    def _compute_active_version(self):
        for template in self:
            active = template.version_ids.filtered(lambda v: v.state == 'active')[:1]
            template.active_version_id = active
            if active:
                template.status = 'active'
            elif template.version_ids:
                template.status = 'draft_only'
            else:
                template.status = 'no_version'

    @api.depends('version_ids', 'version_ids.state')
    def _compute_version_info(self):
        for template in self:
            template.version_count = len(template.version_ids)
            template.draft_version_id = template.version_ids.filtered(lambda v: v.state == 'draft')[:1]

    @api.depends('agreement_ids')
    def _compute_agreement_count(self):
        data = self.env['agreement.agreement']._read_group(
            [('template_id', 'in', self.ids)], ['template_id'], ['__count'])
        counts = {t.id: count for t, count in data}
        for template in self:
            template.agreement_count = counts.get(template.id, 0)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('code'):
                vals['code'] = vals['code'].strip().upper()
        templates = super().create(vals_list)
        # A template always starts with a first Draft version (BR-VER-001).
        for template in templates:
            if not template.version_ids and not self.env.context.get('agreement_skip_default_version') \
                    and not self.env.context.get('install_xmlid'):
                self.env['agreement.template.version'].create({
                    'template_id': template.id,
                    'name': '1.0',
                    'change_note': _('Initial version'),
                })
        return templates

    def write(self, vals):
        if vals.get('code'):
            vals['code'] = vals['code'].strip().upper()
        return super().write(vals)

    def _next_version_name(self):
        """Suggest the next major version number, e.g. 1.0 → 2.0 (BR-VER-001)."""
        self.ensure_one()
        majors = []
        for version in self.version_ids:
            match = re.match(r'^(\d+)', version.name or '')
            if match:
                majors.append(int(match.group(1)))
        return '%d.0' % ((max(majors) + 1) if majors else 1)

    def action_new_version(self):
        """Create a new Draft version copied from the active (or latest) version."""
        self.ensure_one()
        source = self.active_version_id or self.version_ids.sorted('id')[-1:]
        if source:
            version = source.with_context(agreement_version_copy=True).copy({
                'name': self._next_version_name(),
                'state': 'draft',
                'date_effective': False,
                'activated_on': False,
                'activated_by_id': False,
                'superseded_on': False,
                'superseded_by_id': False,
                'change_note': _('Copied from version %s', source.name),
            })
        else:
            version = self.env['agreement.template.version'].create({
                'template_id': self.id,
                'name': self._next_version_name(),
            })
        return version._get_form_action()

    def action_view_versions(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('agreement_management.action_agreement_template_version')
        action['domain'] = [('template_id', '=', self.id)]
        action['context'] = {'default_template_id': self.id, 'search_default_template_id': self.id}
        return action

    def action_view_agreements(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('agreement_management.action_agreement')
        action['domain'] = [('template_id', '=', self.id)]
        action['context'] = {'default_template_id': self.id}
        return action


class AgreementTemplateVersion(models.Model):
    _name = 'agreement.template.version'
    _description = 'Agreement Template Version'
    _inherit = ['mail.thread']
    _order = 'template_id, id desc'
    _rec_name = 'name'

    template_id = fields.Many2one('agreement.template', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='template_id.company_id', store=True)
    name = fields.Char(string='Version Number', required=True, default='1.0', tracking=True)
    state = fields.Selection(VERSION_STATES, default='draft', required=True, tracking=True, copy=False, index=True)
    date_effective = fields.Date(string='Effective Date', tracking=True,
                                 help="Date from which this version is applicable (BR-VER-005).")
    change_note = fields.Text(string='Change Note')
    sign_every_page = fields.Boolean(
        string='Signature on Every Page',
        help="Print a signature strip in the footer of every page: Party A on the left, Party B on the right, "
             "each above a rule carrying the party's name. Off by default — the page footer then shows only the "
             "page number.")
    cover_page = fields.Binary(
        string='Cover Page', attachment=True,
        help="Optional PDF placed in front of the rendered document — in the layout preview, in agreement "
             "previews and in the executed PDF. Locked with the version like the rest of the document.")
    cover_page_filename = fields.Char(string='Cover Page Filename')
    activated_on = fields.Datetime(readonly=True, copy=False)
    activated_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    superseded_on = fields.Datetime(readonly=True, copy=False)
    superseded_by_id = fields.Many2one('agreement.template.version', string='Superseded By', readonly=True, copy=False)
    content = fields.Html(
        string='Agreement Content', sanitize_attributes=False, sanitize_form=False,
        default=lambda self: DEFAULT_AGREEMENT_CONTENT,
        help="Document-style content. Placeholders: {{agreement_number}}, {{party_a}}, {{party_b}}, {{signatory_a}}, "
             "{{signatory_b}}, {{effective_from}}, {{effective_to}}, {{template_code}}, {{template_version}}, "
             "{{<technical name of a dynamic field>}}, {{page_break}} and {{sig:<area code>}} for a signature location.")
    annexure_content = fields.Html(
        string='Annexure Content Template', sanitize_attributes=False, sanitize_form=False,
        default=lambda self: DEFAULT_ANNEXURE_CONTENT,
        help="Default content proposed when a Supplementary Annexure is created for an agreement of this version.")
    field_ids = fields.One2many('agreement.template.field', 'version_id', string='Dynamic Fields', copy=True)
    agreement_field_ids = fields.One2many('agreement.template.field', 'version_id', domain=[('applies_to', '=', 'agreement')])
    annexure_field_ids = fields.One2many('agreement.template.field', 'version_id', domain=[('applies_to', '=', 'annexure')])
    area_ids = fields.One2many('agreement.signature.area', 'version_id', string='Signature Locations', copy=True)
    agreement_area_ids = fields.One2many('agreement.signature.area', 'version_id', domain=[('applies_to', '=', 'agreement')])
    annexure_area_ids = fields.One2many('agreement.signature.area', 'version_id', domain=[('applies_to', '=', 'annexure')])
    properties_definition = fields.PropertiesDefinition(
        string='Agreement Field Definitions', compute='_compute_properties_definition',
        inverse='_inverse_properties_definition', store=True)
    annexure_properties_definition = fields.PropertiesDefinition(
        string='Annexure Field Definitions', compute='_compute_properties_definition',
        inverse='_inverse_annexure_properties_definition', store=True)
    agreement_ids = fields.One2many('agreement.agreement', 'template_version_id', string='Agreements')
    agreement_count = fields.Integer(compute='_compute_agreement_count')
    field_count = fields.Integer(compute='_compute_config_counts')
    area_count = fields.Integer(compute='_compute_config_counts')
    is_locked = fields.Boolean(compute='_compute_is_locked')

    _sql_constraints = [
        ('name_template_uniq', 'unique(template_id, name)', 'This version number already exists for the template.'),
    ]

    # ------------------------------------------------------------------ compute
    @api.depends('template_id.name', 'name')
    def _compute_display_name(self):
        for version in self:
            version.display_name = '%s v%s' % (version.template_id.name or '', version.name or '')

    @api.model
    def _search_display_name(self, operator, value):
        if operator in ('ilike', 'like', '=', '=ilike', '=like') and isinstance(value, str):
            return ['|', '|', ('name', operator, value), ('template_id.name', operator, value),
                    ('template_id.code', operator, value)]
        return super()._search_display_name(operator, value)

    def _state_label(self):
        self.ensure_one()
        return dict(self._fields['state']._description_selection(self.env)).get(self.state, '')

    @api.depends('field_ids.name', 'field_ids.key', 'field_ids.field_type', 'field_ids.applies_to',
                 'field_ids.selection_values', 'field_ids.default_value', 'field_ids.sequence',
                 'field_ids.show_in_cards', 'field_ids.required')
    def _compute_properties_definition(self):
        for version in self:
            fields_ = version.field_ids.sorted(lambda f: (f.sequence, f.id))
            version.properties_definition = [
                f._to_property_definition() for f in fields_ if f.applies_to == 'agreement']
            version.annexure_properties_definition = [
                f._to_property_definition() for f in fields_ if f.applies_to == 'annexure']

    def _inverse_properties_definition(self):
        for version in self:
            version._sync_fields_from_definition('agreement', version.properties_definition or [])

    def _inverse_annexure_properties_definition(self):
        for version in self:
            version._sync_fields_from_definition('annexure', version.annexure_properties_definition or [])

    def _sync_fields_from_definition(self, applies_to, definition):
        """Keep agreement.template.field lines in sync when a manager edits the
        properties definition directly from an agreement form."""
        self.ensure_one()
        Field = self.env['agreement.template.field']
        existing = {f.key: f for f in self.field_ids.filtered(lambda f: f.applies_to == applies_to)}
        seen = set()
        for seq, prop in enumerate(definition, start=1):
            key = prop.get('name')
            if not key:
                continue
            seen.add(key)
            vals = {
                'sequence': seq * 10,
                'name': prop.get('string') or key,
                'field_type': PROPERTY_TO_FIELD_TYPE.get(prop.get('type'), 'char'),
                'selection_values': '\n'.join(label for _value, label in (prop.get('selection') or [])),
                'default_value': str(prop['default']) if prop.get('default') not in (None, False, '') else False,
                'show_in_cards': bool(prop.get('view_in_cards')),
            }
            if key in existing:
                line = existing[key]
                if line.field_type == 'text' and vals['field_type'] == 'char':
                    vals['field_type'] = 'text'
                if line.field_type == 'monetary' and vals['field_type'] == 'float':
                    vals['field_type'] = 'monetary'
                line.with_context(agreement_skip_definition_sync=True).write(vals)
            else:
                vals.update({'version_id': self.id, 'applies_to': applies_to, 'key': key})
                Field.with_context(agreement_skip_definition_sync=True).create(vals)
        for key, line in existing.items():
            if key not in seen:
                line.with_context(agreement_skip_definition_sync=True).unlink()

    @api.depends('agreement_ids')
    def _compute_agreement_count(self):
        data = self.env['agreement.agreement']._read_group(
            [('template_version_id', 'in', self.ids)], ['template_version_id'], ['__count'])
        counts = {v.id: count for v, count in data}
        for version in self:
            version.agreement_count = counts.get(version.id, 0)

    @api.depends('field_ids', 'area_ids')
    def _compute_config_counts(self):
        for version in self:
            version.field_count = len(version.field_ids)
            version.area_count = len(version.area_ids)

    @api.depends('state')
    def _compute_is_locked(self):
        for version in self:
            version.is_locked = version.state != 'draft'

    # -------------------------------------------------------------- constraints
    @api.constrains('state', 'template_id')
    def _check_single_active_version(self):
        """BR-VER-003: only one Active version per template."""
        for version in self:
            if version.state == 'active':
                others = self.search_count([
                    ('template_id', '=', version.template_id.id),
                    ('state', '=', 'active'),
                    ('id', '!=', version.id),
                ])
                if others:
                    raise ValidationError(_(
                        "Template %s already has an Active version. Only one version may be Active at a time.",
                        version.template_id.display_name))

    # ------------------------------------------------------------------ CRUD
    LOCKED_VERSION_FIELDS = {
        'name', 'content', 'annexure_content', 'field_ids', 'area_ids', 'agreement_field_ids', 'annexure_field_ids',
        'agreement_area_ids', 'annexure_area_ids', 'properties_definition', 'annexure_properties_definition',
    }

    def write(self, vals):
        if not self.env.su:
            locked_touch = set(vals) & self.LOCKED_VERSION_FIELDS
            if locked_touch:
                for version in self.filtered(lambda v: v.state != 'draft'):
                    raise UserError(_(
                        "Version %s is %s and locked. Content, dynamic fields and signature locations can only be "
                        "changed on a Draft version — create a new version instead.",
                        version.display_name, dict(VERSION_STATES).get(version.state)))
        return super().write(vals)

    def unlink(self):
        for version in self:
            if version.state != 'draft':
                raise UserError(_("Only Draft versions can be deleted. %s is %s.",
                                  version.display_name, dict(VERSION_STATES).get(version.state)))
            if version.agreement_ids:
                raise UserError(_("Version %s is referenced by existing agreements and cannot be deleted.",
                                  version.display_name))
        return super().unlink()

    def copy_data(self, default=None):
        default = dict(default or {})
        vals_list = super().copy_data(default=default)
        for vals in vals_list:
            if 'name' not in default:
                vals['name'] = self.template_id._next_version_name()
        return vals_list

    # ---------------------------------------------------------------- actions
    def action_activate(self):
        """BR-VER-004: activate a Draft; the previous Active version becomes
        Superseded automatically. No approval workflow."""
        for version in self:
            if version.state != 'draft':
                raise UserError(_("Only a Draft version can be activated."))
            version._check_configuration()
            previous = version.template_id.version_ids.filtered(lambda v: v.state == 'active' and v != version)
            now = fields.Datetime.now()
            previous.write({
                'state': 'superseded',
                'superseded_on': now,
                'superseded_by_id': version.id,
            })
            version.write({
                'state': 'active',
                'activated_on': now,
                'activated_by_id': self.env.user.id,
                'date_effective': version.date_effective or fields.Date.context_today(self),
            })
            body = _("Version %s activated by %s.", version.name, self.env.user.name)
            if previous:
                body += ' ' + _("Version %s superseded. Existing agreements keep the version they were created from.",
                                ', '.join(previous.mapped('name')))
            version.template_id.message_post(body=body)
            version.message_post(body=body)
        return True

    def _check_configuration(self):
        """BR-SIG-005: every signature area must carry a valid party assignment
        and a unique code; placeholders in the content must exist."""
        self.ensure_one()
        for applies_to in ('agreement', 'annexure'):
            areas = self.area_ids.filtered(lambda a: a.applies_to == applies_to)
            codes = areas.mapped('code')
            if len(codes) != len(set(codes)):
                raise UserError(_("Signature location codes must be unique within a version (%s).", applies_to))
            if applies_to == 'agreement':
                if not areas.filtered(lambda a: a.party == 'a' and a.required):
                    raise UserError(_("Configure at least one required Party A signature location before activating."))
                if not areas.filtered(lambda a: a.party == 'b' and a.required):
                    raise UserError(_("Configure at least one required Party B signature location before activating."))
        content = self.content or ''
        for match in re.finditer(r'\{\{\s*sig:([A-Za-z0-9_\-]+)\s*\}\}', content):
            code = match.group(1).upper()
            if code not in self.agreement_area_ids.mapped('code'):
                raise UserError(_("The content references signature location {{sig:%s}} which is not configured.", code))

    def action_set_draft(self):
        """Allow a Superseded version with no agreements to go back to Draft for rework."""
        for version in self:
            if version.state == 'active':
                raise UserError(_("An Active version cannot be reset. Activate another version first."))
            if version.agreement_ids:
                raise UserError(_("Version %s is referenced by agreements and cannot be reset to Draft.",
                                  version.display_name))
            version.write(
                {'state': 'draft', 'superseded_on': False, 'superseded_by_id': False})
        return True

    def action_preview(self):
        """Render the version content with placeholder labels (no agreement data)."""
        self.ensure_one()
        return self.env.ref('agreement_management.action_report_agreement_version').report_action(self)

    def action_view_agreements(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('agreement_management.action_agreement')
        action['domain'] = [('template_version_id', '=', self.id)]
        return action

    def _get_form_action(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'agreement.template.version',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'current',
        }

    # ---------------------------------------------------------------- helpers
    def _get_areas(self, applies_to='agreement'):
        self.ensure_one()
        return self.area_ids.filtered(lambda a: a.applies_to == applies_to).sorted(lambda a: (a.sequence, a.id))

    def _get_fields(self, applies_to='agreement'):
        self.ensure_one()
        return self.field_ids.filtered(lambda f: f.applies_to == applies_to).sorted(lambda f: (f.sequence, f.id))

    def _render_sample_html(self):
        """Layout preview of the content with placeholder labels."""
        self.ensure_one()
        slots = [{
            'code': area.code, 'name': area.name, 'page': area.page, 'party': area.party,
            'kind': area.kind, 'required': area.required, 'signature': None, 'signed_on': None,
            'signer_name': 'Signatory %s' % area.party.upper(), 'party_name': 'Party %s' % area.party.upper(),
            'signer_title': '',
        } for area in self._get_areas('agreement')]
        return render_document_html(self.content or '', {}, slots, 'preview', label_placeholders=True)

    def _page_footer_parties(self):
        """Footer preview for a template version: the layout, with no signatures yet."""
        self.ensure_one()
        return {
            'a': {'signature': False, 'name': 'For Party A, Signatory A', 'signed_on': ''},
            'b': {'signature': False, 'name': 'For Party B, Signatory B', 'signed_on': ''},
        }

    @api.constrains('cover_page')
    def _check_cover_page_is_pdf(self):
        for version in self:
            if version.cover_page and not base64.b64decode(version.cover_page).startswith(b'%PDF'):
                raise ValidationError(_("The cover page must be a PDF file (%s is not one).",
                                        version.cover_page_filename or _('the uploaded file')))


class AgreementTemplateField(models.Model):
    _name = 'agreement.template.field'
    _description = 'Template Dynamic Field Definition'
    _order = 'version_id, applies_to, sequence, id'

    version_id = fields.Many2one('agreement.template.version', required=True, ondelete='cascade', index=True)
    applies_to = fields.Selection([('agreement', 'Agreement'), ('annexure', 'Annexure')],
                                  default='agreement', required=True)
    sequence = fields.Integer(default=10)
    name = fields.Char(string='Field Label', required=True)
    key = fields.Char(string='Technical Name', required=True,
                      help="Used as placeholder in the content, e.g. {{contract_value}}. Lowercase letters, digits, underscore.")
    field_type = fields.Selection(FIELD_TYPES, default='char', required=True)
    required = fields.Boolean(string='Mandatory',
                              help="The agreement cannot be confirmed until this field is completed (BR-FLD-003).")
    editable = fields.Boolean(default=True, help="Manually populated by the user during agreement creation (BR-FLD-005).")
    searchable = fields.Boolean(default=True,
                                help="Dynamic fields are indexed as Odoo properties and available in search, filters and grouping.")
    show_in_cards = fields.Boolean(string='Show in Kanban Cards')
    selection_values = fields.Text(help="One value per line (Selection type).")
    default_value = fields.Char()
    help = fields.Char(string='Help Text')

    _sql_constraints = [
        ('key_version_uniq', 'unique(version_id, applies_to, key)',
         'The technical name must be unique per version and document type.'),
    ]

    @api.onchange('name')
    def _onchange_name(self):
        if self.name and not self.key:
            self.key = self._slugify(self.name)

    @staticmethod
    def _slugify(value):
        key = re.sub(r'[^a-z0-9]+', '_', (value or '').strip().lower()).strip('_')
        if key and key[0].isdigit():
            key = 'f_' + key
        return key[:60] or 'field'

    @api.constrains('key')
    def _check_key(self):
        for line in self:
            if not re.match(r'^[a-z][a-z0-9_]*$', line.key or ''):
                raise ValidationError(_(
                    "Technical name '%s' is invalid: use lowercase letters, digits and underscores, starting with a letter.",
                    line.key))
            if line.key in RESERVED_PLACEHOLDERS:
                raise ValidationError(_("'%s' is a reserved placeholder name.", line.key))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('key'):
                vals['key'] = self._slugify(vals['key'])
            elif vals.get('name'):
                vals['key'] = self._slugify(vals['name'])
        return super().create(vals_list)

    def _selection_pairs(self):
        self.ensure_one()
        pairs = []
        for raw in (self.selection_values or '').splitlines():
            label = raw.strip()
            if label:
                pairs.append([self._slugify(label), label])
        return pairs

    def _to_property_definition(self):
        self.ensure_one()
        ptype = FIELD_TYPE_TO_PROPERTY.get(self.field_type, 'char')
        # Odoo's property definitions allow only name, string, type, comodel, default,
        # selection, tags, domain and view_in_cards — there is no 'required', so a
        # mandatory field is marked in its label instead. _check_before_confirm is what
        # actually enforces it.
        label = self.name
        # Tag a monetary field with the company currency, unless the label already names
        # one — an agreement priced in US$ should not be labelled with the company's ₹.
        symbol = self.version_id.company_id.currency_id.symbol
        if self.field_type == 'monetary' and symbol and not CURRENCY_IN_LABEL_RE.search(label):
            label += ' (%s)' % symbol
        if self.required:
            label += ' %s' % REQUIRED_MARK
        definition = {
            'name': self.key,
            'string': label,
            'type': ptype,
            'view_in_cards': bool(self.show_in_cards),
        }
        if ptype == 'selection':
            definition['selection'] = self._selection_pairs()
        elif ptype == 'many2one':
            definition['comodel'] = 'res.partner'
        elif ptype == 'tags':
            definition['tags'] = [[pair[0], pair[1], (i % 11)] for i, pair in enumerate(self._selection_pairs())]
        default = self.default_value
        if default not in (None, False, ''):
            try:
                if ptype == 'integer':
                    definition['default'] = int(default)
                elif ptype == 'float':
                    definition['default'] = float(default)
                elif ptype == 'boolean':
                    definition['default'] = default.strip().lower() in ('1', 'true', 'yes')
                elif ptype in ('char', 'date', 'datetime'):
                    definition['default'] = default
                elif ptype == 'selection':
                    definition['default'] = self._slugify(default)
            except (TypeError, ValueError):
                pass
        return definition


class AgreementSignatureArea(models.Model):
    _name = 'agreement.signature.area'
    _description = 'Template Signature Location'
    _order = 'version_id, applies_to, sequence, id'

    version_id = fields.Many2one('agreement.template.version', required=True, ondelete='cascade', index=True)
    applies_to = fields.Selection([('agreement', 'Agreement'), ('annexure', 'Annexure')],
                                  default='agreement', required=True)
    sequence = fields.Integer(default=10)
    code = fields.Char(required=True, help="Unique code used in the content as {{sig:CODE}}, e.g. A1, B1, A2.")
    name = fields.Char(string='Section / Anchor', required=True,
                       help="Where the signature appears, e.g. 'Execution block – Party A'.")
    page = fields.Integer(default=1, help="Page on which the location is expected (documentation of the layout).")
    position = fields.Char(help="Free-text position hint, e.g. 'Below Schedule 1, left'.")
    party = fields.Selection([('a', 'Party A / Signatory A'), ('b', 'Party B / Signatory B')], required=True,
                             help="Each area is assigned specifically to one party (BR-SIG-003).")
    kind = fields.Selection([('signature', 'Full signature'), ('initials', 'Initials')], default='signature', required=True)
    required = fields.Boolean(default=True, help="Required areas must be signed before the party can complete signing (BR-SIG-004).")
    placeholder = fields.Char(compute='_compute_placeholder')

    _sql_constraints = [
        ('code_version_uniq', 'unique(version_id, applies_to, code)',
         'The signature location code must be unique per version and document type.'),
    ]

    @api.depends('code')
    def _compute_placeholder(self):
        for area in self:
            area.placeholder = '{{sig:%s}}' % (area.code or '')

    @api.onchange('party')
    def _onchange_party(self):
        if self.party and not self.code:
            existing = self.version_id.area_ids.filtered(lambda a: a.party == self.party and a.applies_to == self.applies_to)
            self.code = '%s%d' % (self.party.upper(), len(existing) + 1)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('code'):
                vals['code'] = vals['code'].strip().upper()
        return super().create(vals_list)

    def write(self, vals):
        if vals.get('code'):
            vals['code'] = vals['code'].strip().upper()
        return super().write(vals)


RESERVED_PLACEHOLDERS = {
    'agreement_number', 'parent_number', 'template_code', 'template_name', 'template_version', 'agreement_type',
    'party_a', 'party_a_address', 'party_a_email', 'party_b', 'party_b_address', 'party_b_email',
    'signatory_a', 'signatory_a_title', 'signatory_a_email', 'signatory_b', 'signatory_b_title', 'signatory_b_email',
    'effective_from', 'effective_to', 'execution_date', 'company', 'company_address', 'today', 'page_break',
}
