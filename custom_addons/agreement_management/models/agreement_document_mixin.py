# -*- coding: utf-8 -*-
import hashlib
import json
import logging
import re
import secrets

from markupsafe import Markup, escape

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools.misc import format_date, format_datetime, formatLang

_logger = logging.getLogger(__name__)

DOCUMENT_STATES = [
    ('draft', 'Draft'),
    ('pending_a', 'Pending Party A Signature'),
    ('pending_b', 'Pending Party B Signature'),
    ('executed', 'Fully Signed / Executed'),
    ('cancel', 'Cancelled'),
]

PLACEHOLDER_RE = re.compile(r'\{\{\s*([A-Za-z0-9_\.]+(?::[A-Za-z0-9_\-]+)?)\s*\}\}')

PARTY_LABEL = {'a': 'Party A', 'b': 'Party B'}

# Server-side marker for workflow writes. It is generated per process and can only be
# placed in the context by server code, never by a client request, so it cannot be
# spoofed through RPC (unlike plain boolean context flags).
WORKFLOW_MARK = secrets.token_hex(16)


# --------------------------------------------------------------------------- #
# Pure rendering helpers (also used by the template version sample preview)
# --------------------------------------------------------------------------- #
def _signature_block_html(slot, mode, label_placeholders=False):
    """HTML for one signature location. ``slot`` is a dict:
    code, name, party ('a'/'b'), kind, required, signature (base64 or None), signed_on,
    signer_name, signer_title, party_name."""
    party = slot['party']
    color = '#714B67' if party == 'a' else '#0F4C6B'
    party_name = slot.get('party_name') or PARTY_LABEL[party]
    signer = slot.get('signer_name') or ''
    title = slot.get('signer_title') or ''
    if slot['kind'] == 'initials':
        if slot.get('signature'):
            return Markup(
                '<span style="display:inline-block;vertical-align:middle;border:1px solid #198754;padding:2px 6px;margin:0 6px;">'
                '<img src="data:image/png;base64,%s" style="height:22px;vertical-align:middle;"/>'
                '<span style="font-size:8px;color:#555;margin-left:4px;">%s</span></span>'
            ) % (slot['signature'].decode() if isinstance(slot['signature'], bytes) else slot['signature'],
                 escape(signer))
        return Markup(
            '<span style="display:inline-block;vertical-align:middle;border:1px dashed %s;color:%s;border-radius:3px;'
            'padding:2px 8px;margin:0 6px;font-size:9px;font-family:sans-serif;">Initials · %s%s</span>'
        ) % (color, color, escape(party_name), ' (required)' if slot['required'] else '')
    if slot.get('signature'):
        return Markup(
            '<div class="agr-sig agr-sig-signed" style="display:inline-block;vertical-align:top;width:46%%;min-width:240px;'
            'border:1px solid #198754;border-radius:3px;padding:6px 10px;margin:8px 2%% 8px 0;font-family:sans-serif;font-size:10px;">'
            '<div style="font-weight:bold;color:#222;">For and on behalf of %s (%s)</div>'
            '<img src="data:image/png;base64,%s" style="max-height:64px;max-width:240px;display:block;margin:4px 0;"/>'
            '<div style="font-weight:bold;">%s</div>%s'
            '<div style="color:#666;">Signed electronically on %s</div></div>'
        ) % (escape(party_name), PARTY_LABEL[party],
             slot['signature'].decode() if isinstance(slot['signature'], bytes) else slot['signature'],
             escape(signer), Markup('<div style="color:#444;">%s</div>') % escape(title) if title else Markup(''),
             escape(slot.get('signed_on') or ''))
    return Markup(
        '<div class="agr-sig agr-sig-placeholder" style="display:inline-block;vertical-align:top;width:46%%;min-width:240px;min-height:84px;'
        'border:1.5px dashed %s;border-radius:3px;padding:6px 10px;margin:8px 2%% 8px 0;font-family:sans-serif;font-size:10px;color:%s;">'
        '<div style="font-weight:bold;">Signature area · %s — %s</div>'
        '<div>%s%s</div><div style="font-size:9px;color:#666;">%s%s</div></div>'
    ) % (color, color, PARTY_LABEL[party], escape(party_name), escape(signer),
         ' · required' if slot['required'] else ' · optional', escape(slot.get('name') or ''),
         (' · page %s' % slot['page']) if slot.get('page') else '')


def render_document_html(content, context, slots, mode, label_placeholders=False):
    """Replace {{placeholders}} in ``content`` and inject the signature slots.

    :param content: html (str / Markup) of the template version or the locked snapshot
    :param context: dict placeholder -> display value
    :param slots: list of slot dicts (see _signature_block_html); slots not referenced in the
                  content are appended in an execution block at the end (BR-SIG-002)
    :param mode: 'preview' or 'signed' (captured signatures are always shown; the mode drives the report chrome)
    :param label_placeholders: render unresolved placeholders as labels (template preview)
    """
    slots_by_code = {s['code']: s for s in slots}
    used = set()

    def repl(match):
        key = match.group(1)
        lkey = key.lower()
        if lkey == 'page_break':
            return Markup('<div style="page-break-after:always;"></div>')
        if lkey.startswith('sig:'):
            code = key[4:].upper()
            slot = slots_by_code.get(code)
            if not slot:
                return Markup('<span style="background:#f8d7da;">[unknown signature location %s]</span>') % escape(code)
            used.add(code)
            return _signature_block_html(slot, mode, label_placeholders)
        if key in context and context[key] not in (None, False):
            return Markup('<span class="agr-ph">%s</span>') % escape(str(context[key]))
        if label_placeholders:
            return Markup('<span style="background:#fff3cd;">[%s]</span>') % escape(key.replace('_', ' '))
        return Markup('<span style="background:#fff3cd;">[%s]</span>') % escape(key.replace('_', ' '))

    html = Markup(PLACEHOLDER_RE.sub(repl, str(content or '')))
    remaining = [s for s in slots if s['code'] not in used]
    if remaining:
        blocks = Markup('').join(_signature_block_html(s, mode, label_placeholders) for s in remaining)
        html += Markup('<div class="agr-execution-block" style="margin-top:18px;page-break-inside:avoid;">'
                       '<h4 style="font-family:sans-serif;font-size:11px;margin:12px 0 4px;">EXECUTION</h4>%s</div>') % blocks
    return Markup(html)


class AgreementDocumentMixin(models.AbstractModel):
    """Behaviour shared by agreements and supplementary annexures:
    confirmation & numbering, sequential Party A → Party B signing, execution,
    executed PDF, distribution, notifications, rendering and locking."""
    _name = 'agreement.document.mixin'
    _description = 'Signable Agreement Document'

    state = fields.Selection(DOCUMENT_STATES, default='draft', required=True, tracking=True, copy=False, index=True)
    confirmed_on = fields.Datetime(readonly=True, copy=False)
    confirmed_by_id = fields.Many2one('res.users', string='Confirmed By', readonly=True, copy=False)
    signed_a_on = fields.Datetime(string='Party A Signed On', readonly=True, copy=False, tracking=True)
    signed_b_on = fields.Datetime(string='Party B Signed On', readonly=True, copy=False, tracking=True)
    executed_on = fields.Datetime(string='Execution Date / Time', readonly=True, copy=False, tracking=True, index=True)
    locked_content = fields.Html(string='Locked Content', sanitize=False, readonly=True, copy=False,
                                 help="Snapshot of the template content taken on confirmation (BR-VER-007).")
    executed_hash = fields.Char(string='Document Hash', readonly=True, copy=False,
                                help="SHA-256 of the rendered executed document.")
    executed_pdf_sha256 = fields.Char(string='Executed PDF SHA-256', readonly=True, copy=False,
                                      help="SHA-256 of the stored executed PDF file, taken when it was generated.")
    locked_values = fields.Json(string='Locked Values', readonly=True, copy=False,
                                help="Placeholder values frozen on confirmation; the document renders from these, "
                                     "not from the live fields.")
    document_fingerprint = fields.Char(string='Document Fingerprint', readonly=True, copy=False,
                                       help="SHA-256 of the confirmed document (content and values, without signatures). "
                                            "Re-checked at every signing step and at execution.")
    integrity_ok = fields.Boolean(compute='_compute_integrity_ok', string='Integrity Verified')
    executed_pdf_attachment_id = fields.Many2one('ir.attachment', string='Executed PDF', readonly=True, copy=False,
                                                 ondelete='restrict')
    executed_pdf = fields.Binary(related='executed_pdf_attachment_id.datas', string='Executed PDF File')
    executed_pdf_filename = fields.Char(related='executed_pdf_attachment_id.name')
    document_html = fields.Html(string='Document', compute='_compute_document_html', sanitize=False)
    signature_required_count = fields.Integer(compute='_compute_signature_progress')
    signature_signed_count = fields.Integer(compute='_compute_signature_progress')
    signature_count = fields.Integer(compute='_compute_signature_progress')
    signing_progress = fields.Float(compute='_compute_signature_progress', string='Signing Progress')
    signature_status_summary = fields.Char(compute='_compute_signature_progress')
    distribution_count = fields.Integer(compute='_compute_distribution_count')
    can_sign_a = fields.Boolean(compute='_compute_can_sign')
    can_sign_b = fields.Boolean(compute='_compute_can_sign')
    is_locked = fields.Boolean(compute='_compute_is_locked')

    # Fields that may still be written once the document is executed (BR-INT-001).
    _EXECUTED_WRITABLE_FIELDS = {
        'state', 'executed_on', 'executed_hash', 'executed_pdf_sha256', 'executed_pdf_attachment_id', 'signed_b_on',
        'message_main_attachment_id', 'message_ids', 'message_follower_ids', 'message_partner_ids',
        'activity_ids', 'activity_user_id', 'activity_type_id', 'activity_date_deadline', 'activity_summary',
        'website_message_ids', 'rating_ids', 'access_token', 'annexure_ids', 'distribution_ids', 'signature_ids',
        'annexure_count', 'user_id', 'display_name',
    }
    # Fields only the workflow may write (never a direct write, whatever the user's rights).
    _WORKFLOW_FIELDS = {
        'state', 'number', 'confirmed_on', 'confirmed_by_id', 'signed_a_on', 'signed_b_on', 'executed_on',
        'locked_content', 'locked_values', 'document_fingerprint', 'executed_hash', 'executed_pdf_sha256',
        'executed_pdf_attachment_id', 'sequence_number',
    }

    def _get_locked_fields(self):
        """Business fields frozen from confirmation onwards (overridden per document type)."""
        return set()

    def _is_internal_write(self):
        return self.env.su or self.env.context.get('agreement_workflow') == WORKFLOW_MARK

    def _wf(self):
        """Recordset carrying the workflow marker for server-side writes."""
        return self.with_context(agreement_workflow=WORKFLOW_MARK)

    # ------------------------------------------------------------- abstract API
    def _document_label(self):
        return _('Agreement')

    def _get_party_partner(self, party):
        raise NotImplementedError()

    def _get_signatory(self, party):
        raise NotImplementedError()

    def _get_template_version(self):
        raise NotImplementedError()

    def _get_areas_applies_to(self):
        return 'agreement'

    def _get_signature_document_field(self):
        return 'agreement_id'

    def _get_content_source(self):
        raise NotImplementedError()

    def _get_properties_field(self):
        return 'agreement_properties'

    def _get_report_ref(self):
        raise NotImplementedError()

    def _get_executed_mail_template(self):
        raise NotImplementedError()

    def _get_notification_mail_template(self, party):
        raise NotImplementedError()

    def _assign_number(self):
        raise NotImplementedError()

    def _get_parent_agreement(self):
        return self.browse()

    def _get_extra_render_context(self):
        return {}

    # ---------------------------------------------------------------- compute
    def _state_label(self):
        self.ensure_one()
        return dict(self._fields['state']._description_selection(self.env)).get(self.state, '')

    def _compute_is_locked(self):
        for doc in self:
            doc.is_locked = doc.state != 'draft'

    @api.depends('document_fingerprint', 'locked_content', 'locked_values', 'executed_pdf_attachment_id',
                 'executed_pdf_sha256', 'signature_ids.required', 'signature_ids.party', 'signature_ids.code')
    def _compute_integrity_ok(self):
        for doc in self:
            try:
                doc.integrity_ok = bool(doc.id) and doc._integrity_check(raise_error=False)
            except Exception:  # pragma: no cover - defensive
                doc.integrity_ok = False

    @api.depends('signature_ids.signature', 'signature_ids.required', 'state')
    def _compute_signature_progress(self):
        for doc in self:
            required = doc.signature_ids.filtered('required')
            signed = required.filtered('signature')
            doc.signature_count = len(doc.signature_ids)
            doc.signature_required_count = len(required)
            doc.signature_signed_count = len(signed)
            doc.signing_progress = (100.0 * len(signed) / len(required)) if required else 0.0
            a_req = required.filtered(lambda s: s.party == 'a')
            b_req = required.filtered(lambda s: s.party == 'b')
            doc.signature_status_summary = _('A: %(a_done)s/%(a_total)s · B: %(b_done)s/%(b_total)s') % {
                'a_done': len(a_req.filtered('signature')), 'a_total': len(a_req),
                'b_done': len(b_req.filtered('signature')), 'b_total': len(b_req),
            }

    @api.depends('distribution_ids')
    def _compute_distribution_count(self):
        for doc in self:
            doc.distribution_count = len(doc.distribution_ids)

    @api.depends('state')
    @api.depends_context('uid')
    def _compute_can_sign(self):
        for doc in self:
            doc.can_sign_a = doc.state == 'pending_a' and doc._user_can_sign('a')
            doc.can_sign_b = doc.state == 'pending_b' and doc._user_can_sign('b')

    @api.depends('state', 'locked_content', 'signature_ids.signature')
    def _compute_document_html(self):
        for doc in self:
            try:
                doc.document_html = doc._render_document_html() if doc.id else ''
            except Exception as exc:  # never break the form because of a rendering issue
                _logger.warning("Agreement document rendering failed for %s: %s", doc, exc)
                doc.document_html = Markup('<p class="text-danger">%s</p>') % escape(str(exc))

    # -------------------------------------------------------------- rendering
    def _format_partner_address(self, partner):
        if not partner:
            return ''
        try:
            address = partner._display_address(without_company=True)
        except Exception:  # pragma: no cover - defensive
            address = partner.contact_address or ''
        return ', '.join(line.strip() for line in (address or '').splitlines() if line.strip())

    def _property_values(self, field_name=None):
        """Return {technical_name: raw_value} for the document's dynamic fields."""
        self.ensure_one()
        field_name = field_name or self._get_properties_field()
        value = self[field_name] if field_name in self._fields else False
        if isinstance(value, dict):
            return dict(value)
        result = {}
        for prop in (value or []):
            if isinstance(prop, dict) and prop.get('name'):
                result[prop['name']] = prop.get('value')
        return result

    def _property_definitions(self):
        self.ensure_one()
        version = self._get_template_version()
        if not version:
            return []
        if self._get_areas_applies_to() == 'annexure':
            return version.annexure_properties_definition or []
        return version.properties_definition or []

    def _format_property_value(self, definition, value, field_type=None):
        """Display value of a property for placeholders (BR-CNT-002)."""
        ptype = definition.get('type')
        if value in (None, False, '', []):
            return '' if ptype != 'boolean' else _('No')
        if ptype == 'boolean':
            return _('Yes') if value else _('No')
        if ptype in ('integer', 'float'):
            if field_type == 'monetary':
                return formatLang(self.env, float(value), currency_obj=self.company_id.currency_id)
            return formatLang(self.env, value, digits=2 if ptype == 'float' else 0)
        if ptype == 'date':
            return format_date(self.env, value)
        if ptype == 'datetime':
            return format_datetime(self.env, value, dt_format='medium')
        if ptype == 'selection':
            mapping = dict(definition.get('selection') or [])
            return mapping.get(value, str(value))
        if ptype == 'many2one':
            rec_id = value[0] if isinstance(value, (list, tuple)) else value
            comodel = definition.get('comodel')
            if comodel and rec_id:
                return self.env[comodel].browse(rec_id).display_name
            return str(value)
        if ptype in ('many2many', 'tags'):
            if ptype == 'tags':
                mapping = {tag[0]: tag[1] for tag in (definition.get('tags') or [])}
                return ', '.join(mapping.get(v, str(v)) for v in value)
            comodel = definition.get('comodel')
            ids = [v[0] if isinstance(v, (list, tuple)) else v for v in value]
            return ', '.join(self.env[comodel].browse(ids).mapped('display_name')) if comodel else str(value)
        return str(value)

    def _get_render_context(self):
        """Placeholder values. After confirmation the frozen snapshot is used, so changes to
        partners, dates or dynamic fields can no longer alter the document."""
        self.ensure_one()
        if self.locked_values and self.state not in ('draft', 'cancel'):
            context = dict(self.locked_values)
            context['execution_date'] = format_datetime(self.env, self.executed_on, dt_format='medium') if self.executed_on else ''
            context['today'] = format_date(self.env, fields.Date.context_today(self))
            return context
        return self._compute_render_context()

    def _compute_render_context(self):
        self.ensure_one()
        env = self.env
        parent = self._get_parent_agreement()
        version = self._get_template_version()
        template = version.template_id
        party_a, party_b = self._get_party_partner('a'), self._get_party_partner('b')
        sig_a, sig_b = self._get_signatory('a'), self._get_signatory('b')
        number = self.number or _('[assigned on confirmation]')
        context = {
            'agreement_number': number,
            'parent_number': parent.number if parent else '',
            'template_code': template.code or '',
            'template_name': template.name or '',
            'template_version': version.name or '',
            'agreement_type': self.type_id.name if 'type_id' in self._fields and self.type_id else '',
            'party_a': party_a.display_name if party_a else '',
            'party_a_address': self._format_partner_address(party_a),
            'party_a_email': party_a.email or '' if party_a else '',
            'party_b': party_b.display_name if party_b else '',
            'party_b_address': self._format_partner_address(party_b),
            'party_b_email': party_b.email or '' if party_b else '',
            'signatory_a': sig_a.name if sig_a else '',
            'signatory_a_title': sig_a.function or '' if sig_a else '',
            'signatory_a_email': sig_a.email or '' if sig_a else '',
            'signatory_b': sig_b.name if sig_b else '',
            'signatory_b_title': sig_b.function or '' if sig_b else '',
            'signatory_b_email': sig_b.email or '' if sig_b else '',
            'effective_from': format_date(env, self._get_effective_from()) if self._get_effective_from() else '',
            'effective_to': format_date(env, self._get_effective_to()) if self._get_effective_to() else '',
            'execution_date': format_datetime(env, self.executed_on, dt_format='medium') if self.executed_on else '',
            'company': self.company_id.name,
            'company_address': self._format_partner_address(self.company_id.partner_id),
            'today': format_date(env, fields.Date.context_today(self)),
        }
        values = self._property_values()
        field_types = {f.key: f.field_type for f in version._get_fields(self._get_areas_applies_to())} if version else {}
        for definition in self._property_definitions():
            key = definition.get('name')
            if key:
                context[key] = self._format_property_value(definition, values.get(key), field_types.get(key))
        context.update(self._get_extra_render_context())
        return context

    def _get_effective_from(self):
        return self.date_from if 'date_from' in self._fields else self.date_effective

    def _get_effective_to(self):
        if 'date_to' in self._fields:
            return self.date_to
        parent = self._get_parent_agreement()
        return parent.date_to if parent else False

    def _get_signature_slots(self):
        """Uniform description of the signature locations: from the signature
        records once confirmed, from the template version configuration before."""
        self.ensure_one()
        slots = []
        if self.signature_ids:
            # names come from the values frozen on confirmation, not from the live partners
            ctx = self._get_render_context()
            for sig in self.signature_ids.sorted(lambda s: (s.sequence, s.id)):
                slots.append({
                    'code': sig.code, 'name': sig.name, 'page': sig.page, 'party': sig.party, 'kind': sig.kind,
                    'required': sig.required, 'signature': sig.signature or None,
                    'signed_on': format_datetime(self.env, sig.signed_on, dt_format='medium') if sig.signed_on else '',
                    'signer_name': sig.signer_name or ctx.get('signatory_%s' % sig.party, ''),
                    'signer_title': ctx.get('signatory_%s_title' % sig.party, ''),
                    'party_name': ctx.get('party_%s' % sig.party) or PARTY_LABEL[sig.party],
                })
            return slots
        version = self._get_template_version()
        if not version:
            return slots
        for area in version._get_areas(self._get_areas_applies_to()):
            partner = self._get_party_partner(area.party)
            signer = self._get_signatory(area.party)
            slots.append({
                'code': area.code, 'name': area.name, 'page': area.page, 'party': area.party, 'kind': area.kind,
                'required': area.required, 'signature': None, 'signed_on': '',
                'signer_name': signer.name if signer else '', 'signer_title': signer.function if signer else '',
                'party_name': partner.display_name if partner else PARTY_LABEL[area.party],
            })
        return slots

    def _render_document_html(self, mode=None):
        """Full document HTML (BR-PRV-002 / BR-PDF-002)."""
        self.ensure_one()
        mode = mode or ('signed' if self.state == 'executed' else 'preview')
        content = self.locked_content or self._get_content_source() or ''
        return render_document_html(content, self._get_render_context(), self._get_signature_slots(), mode)

    # ------------------------------------------------------------- integrity
    def _document_fingerprint(self):
        """SHA-256 of the confirmed document rendered without signatures and without the
        volatile placeholders (today, execution date). Stable from confirmation to execution."""
        self.ensure_one()
        context = dict(self._get_render_context())
        context['today'] = ''
        context['execution_date'] = ''
        slots = []
        for sig in self.signature_ids.sorted(lambda s: (s.sequence, s.id)):
            slots.append({
                'code': sig.code, 'name': sig.name, 'page': sig.page, 'party': sig.party, 'kind': sig.kind,
                'required': sig.required, 'signature': None, 'signed_on': '',
                'signer_name': context.get('signatory_%s' % sig.party, ''), 'signer_title': '',
                'party_name': context.get('party_%s' % sig.party, ''),
            })
        html = str(render_document_html(self.locked_content or '', context, slots, 'preview'))
        return hashlib.sha256(html.encode('utf-8')).hexdigest()

    def _integrity_check(self, raise_error=True):
        """Verify that content, values and signature configuration are unchanged since
        confirmation, and that the stored executed PDF matches its recorded hash."""
        self.ensure_one()
        problems = []
        if self.document_fingerprint and self.state not in ('draft', 'cancel'):
            if self._document_fingerprint() != self.document_fingerprint:
                problems.append(_("the document content, values or signature configuration changed after confirmation"))
        if self.executed_pdf_attachment_id and self.executed_pdf_sha256:
            data = self.executed_pdf_attachment_id.sudo().raw or b''
            if hashlib.sha256(data).hexdigest() != self.executed_pdf_sha256:
                problems.append(_("the stored executed PDF does not match its recorded SHA-256"))
        if problems and raise_error:
            raise UserError(_("Integrity check failed for %s %s: %s. The document cannot proceed; contact an "
                              "Agreement Manager.", self._document_label(), self.number or '', '; '.join(problems)))
        return not problems

    def action_verify_integrity(self):
        """Manual verification: fingerprint of the confirmed document and hash of the executed PDF."""
        self.ensure_one()
        ok = self._integrity_check(raise_error=False)
        body = _("Integrity verification passed: document fingerprint %s%s.") % (
            (self.document_fingerprint or '')[:16],
            (_(" and executed PDF SHA-256 %s") % self.executed_pdf_sha256[:16]) if self.executed_pdf_sha256 else '') \
            if ok else _("Integrity verification FAILED — the confirmed document or the executed PDF was modified outside the workflow.")
        self.message_post(body=body)
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {'type': 'success' if ok else 'danger', 'title': _('Integrity check'), 'message': body, 'sticky': not ok},
        }

    # ------------------------------------------------------------ validation
    def _check_before_confirm(self):
        self.ensure_one()
        version = self._get_template_version()
        if not version:
            raise UserError(_("Please select an active agreement template."))
        if self._get_areas_applies_to() == 'agreement' and version.state != 'active':
            raise UserError(_("Please select an active agreement template. Version %s of %s is %s.",
                              version.name, version.template_id.name, version.state))
        party_a, party_b = self._get_party_partner('a'), self._get_party_partner('b')
        if not party_a:
            raise UserError(_("Please select Party A before proceeding."))
        if not party_b:
            raise UserError(_("Please select Party B before proceeding."))
        if not self._get_signatory('a'):
            raise UserError(_("Please select a Signatory A before proceeding."))
        if not self._get_signatory('b'):
            raise UserError(_("Please select a Signatory B before proceeding."))
        if party_a == party_b and not self.company_id.agreement_allow_same_party:
            raise UserError(_("Party A and Party B cannot be the same party unless the business configuration "
                              "explicitly permits it (Configuration › Settings)."))
        if not self._get_effective_from():
            raise UserError(_("Please enter the effective date before confirming the %s.", self._document_label().lower()))
        missing = []
        values = self._property_values()
        required_keys = {f.key for f in version._get_fields(self._get_areas_applies_to()) if f.required}
        for definition in self._property_definitions():
            key = definition.get('name')
            if key in required_keys and definition.get('type') != 'boolean':
                value = values.get(key)
                if value in (None, False, '', []):
                    missing.append(definition.get('string') or key)
        if missing:
            raise UserError(_("Please complete all mandatory information before confirming the %s. Missing: %s.",
                              self._document_label().lower(), ', '.join(missing)))
        areas = version._get_areas(self._get_areas_applies_to())
        if not areas.filtered(lambda a: a.party == 'a' and a.required) or not areas.filtered(lambda a: a.party == 'b' and a.required):
            raise UserError(_("The template version has no required signature location for both parties. "
                              "Please ask an Agreement Manager to configure the signature locations."))

    # ---------------------------------------------------------------- actions
    def action_confirm(self):
        """BR-CON-001: assign number, lock version & content, open signing."""
        for doc in self:
            if doc.state != 'draft':
                raise UserError(_("Only draft documents can be confirmed."))
            doc._check_before_confirm()
            version = doc._get_template_version()
            number = doc._assign_number()
            values = doc._compute_render_context()
            values['agreement_number'] = number
            doc._wf().write({
                'number': number,
                'state': 'pending_a',
                'confirmed_on': fields.Datetime.now(),
                'confirmed_by_id': self.env.user.id,
                'locked_content': doc._get_content_source() or '',
                'locked_values': json.loads(json.dumps(values, default=str)),
            })
            doc._create_signature_records()
            doc._wf().write({'document_fingerprint': doc._document_fingerprint()})
            doc.message_post(body=_(
                "%(label)s confirmed. Number %(number)s assigned; template %(template)s version %(version)s locked to "
                "this record. Signing workflow opened — Party A signs first.",
                label=doc._document_label(), number=number, template=version.template_id.code, version=version.name))
            doc._notify_signatory('a')
        return True

    def _create_signature_records(self):
        self.ensure_one()
        version = self._get_template_version()
        field_name = self._get_signature_document_field()
        vals_list = []
        for area in version._get_areas(self._get_areas_applies_to()):
            vals_list.append({
                field_name: self.id,
                'sequence': area.sequence,
                'code': area.code,
                'name': area.name,
                'page': area.page,
                'position': area.position,
                'party': area.party,
                'kind': area.kind,
                'required': area.required,
            })
        self.env['agreement.signature'].sudo()._wf().create(vals_list)

    def action_preview(self):
        """BR-PRV-001: document-style preview (PDF, watermarked until executed)."""
        self.ensure_one()
        return self.env.ref(self._get_report_ref()).report_action(self)

    def action_sign_party_a(self):
        return self._open_sign_wizard('a')

    def action_sign_party_b(self):
        return self._open_sign_wizard('b')

    def _user_can_sign(self, party):
        """Manager, the designated signatory's user, or an internal user capturing the
        signature on a tablet session (if allowed by the company settings)."""
        self.ensure_one()
        user = self.env.user
        if user.has_group('agreement_management.group_agreement_manager'):
            return True
        signatory = self._get_signatory(party)
        if signatory and signatory.user_ids and user in signatory.user_ids:
            return True
        return bool(self.company_id.agreement_allow_facilitated_signing and
                    user.has_group('agreement_management.group_agreement_user'))

    def _open_sign_wizard(self, party):
        self.ensure_one()
        expected = 'pending_a' if party == 'a' else 'pending_b'
        if self.state != expected:
            if party == 'b' and self.state == 'pending_a':
                raise UserError(_("Party A must complete all required signatures before Party B can sign."))
            raise UserError(_("This %s is not awaiting the signature of %s.",
                              self._document_label().lower(), PARTY_LABEL[party]))
        if not self._user_can_sign(party):
            raise AccessError(_("Only the designated %s signatory (or an Agreement Manager) can sign this %s.",
                                PARTY_LABEL[party], self._document_label().lower()))
        wizard = self.env['agreement.sign.wizard'].create({
            'res_model': self._name,
            'res_id': self.id,
            'party': party,
        })
        return wizard._get_action()

    def _register_signatures(self, party, signature_values, signer_ip=None):
        """Write captured signatures. ``signature_values`` maps agreement.signature id → base64 png."""
        self.ensure_one()
        expected = 'pending_a' if party == 'a' else 'pending_b'
        if self.state != expected:
            raise UserError(_("Signing is not open for %s on this document.", PARTY_LABEL[party]))
        self._integrity_check()
        now = fields.Datetime.now()
        signatory = self._get_signatory(party)
        party_signatures = self.signature_ids.filtered(lambda s: s.party == party)
        for signature in party_signatures:
            value = signature_values.get(signature.id)
            if value:
                signature.sudo()._wf().write({
                    'signature': value,
                    'signed_on': now,
                    'signed_by_user_id': self.env.user.id,
                    'signer_ip': signer_ip or False,
                    'signer_name': signatory.name if signatory else '',
                })
        missing = party_signatures.filtered(lambda s: s.required and not s.signature)
        if missing:
            if party == 'a':
                raise UserError(_("Party A must complete all required signatures before Party B can sign. "
                                  "Missing: %s.", ', '.join(missing.mapped('name'))))
            raise UserError(_("All required Party B signatures must be completed before the agreement can be "
                              "executed. Missing: %s.", ', '.join(missing.mapped('name'))))
        self._after_party_signed(party)
        return True

    def _after_party_signed(self, party):
        self.ensure_one()
        now = fields.Datetime.now()
        done = len(self.signature_ids.filtered(lambda s: s.party == party and s.required and s.signature))
        signatory = self._get_signatory(party)
        if party == 'a':
            self._wf().write({'state': 'pending_b', 'signed_a_on': now})
            self.message_post(body=_(
                "Party A signing completed by %(who)s — %(done)s required signature area(s) signed. "
                "Party B signature areas are now available.", who=signatory.name if signatory else '', done=done))
            self._notify_signatory('b')
        else:
            self._wf().write({'state': 'executed', 'signed_b_on': now, 'executed_on': now})
            self.message_post(body=_(
                "Party B signing completed by %(who)s — %(done)s required signature area(s) signed. "
                "%(label)s is now Fully Signed / Executed; signing is closed.",
                who=signatory.name if signatory else '', done=done, label=self._document_label()))
            self._generate_executed_pdf()
            self._notify_execution()
            if self.company_id.agreement_auto_email:
                self._auto_email_executed()

    # ---------------------------------------------------------------- PDF
    def _generate_executed_pdf(self):
        """BR-PDF-001/003: render the executed document and store it as the official copy."""
        self.ensure_one()
        self._integrity_check()
        html = str(self._render_document_html('signed'))
        self._wf().write({'executed_hash': hashlib.sha256(html.encode('utf-8')).hexdigest()})
        report = self.env.ref(self._get_report_ref())
        pdf_content, _ext = self.env['ir.actions.report'].with_context(agreement_signed_mode=True)._render_qweb_pdf(
            report.id, res_ids=self.ids)
        filename = '%s-Executed.pdf' % (self.number or self._document_label()).replace('/', '-')
        attachment = self.env['ir.attachment'].create({
            'name': filename,
            'type': 'binary',
            'raw': pdf_content,
            'mimetype': 'application/pdf',
            'res_model': self._name,
            'res_id': self.id,
            'description': _('Official executed copy generated on execution — do not modify.'),
        })
        self._wf().write({
            'executed_pdf_attachment_id': attachment.id,
            'executed_pdf_sha256': hashlib.sha256(pdf_content).hexdigest(),
            'message_main_attachment_id': attachment.id,
        })
        self.message_post(
            body=_("Executed PDF generated and stored as the official executed document (SHA-256 %s).",
                   self.executed_hash[:16]),
            attachment_ids=[attachment.id])
        return attachment

    def action_print_executed(self):
        """BR-PDF-006: view / print / download the stored executed PDF (never regenerated)."""
        self.ensure_one()
        if not self.executed_pdf_attachment_id:
            raise UserError(_("The executed PDF is generated automatically once all required signatures are completed."))
        return {
            'type': 'ir.actions.act_url',
            'url': '/web/content/%s?download=true' % self.executed_pdf_attachment_id.id,
            'target': 'new',
        }

    def action_view_executed_pdf(self):
        self.ensure_one()
        if not self.executed_pdf_attachment_id:
            raise UserError(_("The executed PDF is generated automatically once all required signatures are completed."))
        return {
            'type': 'ir.actions.act_url',
            'url': '/web/content/%s' % self.executed_pdf_attachment_id.id,
            'target': 'new',
        }

    # --------------------------------------------------------------- email
    def _get_default_distribution_partners(self):
        """BR-EMAIL-002/008: default recipients from the company configuration."""
        self.ensure_one()
        company = self.company_id
        partners = self.env['res.partner']
        if company.agreement_email_party_a:
            partners |= self._get_party_partner('a')
        if company.agreement_email_party_b:
            partners |= self._get_party_partner('b')
        if company.agreement_email_signatory_a:
            partners |= self._get_signatory('a')
        if company.agreement_email_signatory_b:
            partners |= self._get_signatory('b')
        partners |= company.agreement_email_extra_partner_ids
        return partners.filtered('email')

    def action_email_executed(self):
        """BR-EMAIL-001/003/004/005: open the Odoo composer with the stored PDF attached."""
        self.ensure_one()
        if self.state != 'executed' or not self.executed_pdf_attachment_id:
            raise UserError(_("The executed document can be emailed once the %s is Fully Signed / Executed.",
                              self._document_label().lower()))
        template = self._get_executed_mail_template()
        compose_form = self.env.ref('mail.email_compose_message_wizard_form', raise_if_not_found=False)
        ctx = {
            'default_model': self._name,
            'default_res_ids': self.ids,
            'default_composition_mode': 'comment',
            'default_template_id': template.id if template else False,
            'default_partner_ids': self._get_default_distribution_partners().ids,
            'default_subject': _('Fully Executed %s – %s', self._document_label(), self.number),
            'agreement_forced_attachment_ids': [self.executed_pdf_attachment_id.id],
            'agreement_distribution': 'manual',
            'force_email': True,
            'mail_post_autofollow': False,
        }
        return {
            'type': 'ir.actions.act_window',
            'name': _('Email Executed %s', self._document_label()),
            'res_model': 'mail.compose.message',
            'view_mode': 'form',
            'views': [(compose_form.id if compose_form else False, 'form')],
            'target': 'new',
            'context': ctx,
        }

    def _auto_email_executed(self):
        """BR-EMAIL-007: automatic distribution after execution."""
        self.ensure_one()
        template = self._get_executed_mail_template()
        partners = self._get_default_distribution_partners()
        if not template or not partners or not self.executed_pdf_attachment_id:
            self.message_post(body=_("Automatic email distribution skipped: no template or no recipient with an email address."))
            return False
        try:
            mail_id = template.send_mail(
                self.id, force_send=True, raise_exception=False,
                email_values={
                    'recipient_ids': [(6, 0, partners.ids)],
                    'attachment_ids': [(4, self.executed_pdf_attachment_id.id)],
                    'auto_delete': False,
                })
            mail = self.env['mail.mail'].browse(mail_id)
            self._log_distribution(mode='auto', partners=partners, mail=mail, subject=mail.subject)
            self.message_post(body=_("Executed %s emailed automatically to %s.",
                                     self._document_label().lower(), ', '.join(partners.mapped('display_name'))))
        except Exception as exc:  # distribution failure must never roll back the execution
            _logger.exception("Automatic distribution failed for %s", self)
            self.message_post(body=_("The executed document could not be emailed automatically: %s", exc))
        return True

    def _log_distribution(self, mode, partners, subject, mail=None, message=None):
        self.ensure_one()
        recipients = partners.mapped('email')
        return self.env['agreement.distribution'].sudo().create({
            self._get_signature_document_field(): self.id,
            'mode': mode,
            'sender_id': self.env.user.id if mode == 'manual' else self.env.ref('base.user_root').id,
            'partner_ids': [(6, 0, partners.ids)],
            'email_to': ', '.join(e for e in recipients if e),
            'subject': subject or '',
            'attachment_id': self.executed_pdf_attachment_id.id,
            'mail_mail_id': mail.id if mail else False,
            'mail_message_id': message.id if message else (mail.mail_message_id.id if mail else False),
        })

    def message_post(self, **kwargs):
        message = super().message_post(**kwargs)
        # Log manual distribution done through the composer (BR-EMAIL-009).
        if (self.env.context.get('agreement_distribution') == 'manual'
                and message.message_type == 'comment'
                and self.executed_pdf_attachment_id
                and self.executed_pdf_attachment_id in message.attachment_ids):
            self._log_distribution(mode='manual', partners=message.partner_ids, subject=message.subject, message=message)
        return message

    # --------------------------------------------------------- notifications
    def _notify_signatory(self, party):
        """§29: notify the signatory whose turn it is (email + activity)."""
        self.ensure_one()
        signatory = self._get_signatory(party)
        template = self._get_notification_mail_template(party)
        if signatory and signatory.email and template:
            try:
                template.send_mail(self.id, force_send=True, raise_exception=False,
                                   email_values={'recipient_ids': [(6, 0, signatory.ids)], 'auto_delete': False})
            except Exception:  # pragma: no cover - defensive
                _logger.exception("Signing notification failed for %s", self)
        summary = _('Sign as %s', PARTY_LABEL[party])
        note = _('%(label)s %(number)s is ready for the signature of %(who)s.',
                 label=self._document_label(), number=self.number, who=signatory.name if signatory else PARTY_LABEL[party])
        users = signatory.user_ids[:1] if signatory else self.env['res.users']
        if not users:
            users = self.user_id if 'user_id' in self._fields and self.user_id else self.env.user
            summary = _('Facilitate %s signing', PARTY_LABEL[party])
        self.activity_schedule('mail.mail_activity_data_todo', user_id=users.id, summary=summary, note=note)
        self.message_post(body=_("Notification sent to %(who)s — “%(label)s %(number)s is ready for your signature.”",
                                 who=signatory.name if signatory else PARTY_LABEL[party],
                                 label=self._document_label(), number=self.number))
        return True

    def _notify_execution(self):
        self.ensure_one()
        self.activity_ids.filtered(lambda a: a.summary and 'Sign' in a.summary).action_feedback(
            feedback=_('All required signatures completed.'))
        self.message_post(body=_("%s %s has been fully executed.", self._document_label(), self.number))
        return True

    def action_resend_notification(self):
        for doc in self:
            if doc.state == 'pending_a':
                doc._notify_signatory('a')
            elif doc.state == 'pending_b':
                doc._notify_signatory('b')
            else:
                raise UserError(_("No signature is pending on this document."))
        return True

    # ----------------------------------------------------------- cancel/reset
    def action_cancel(self):
        for doc in self:
            if doc.state == 'executed':
                raise UserError(_("An executed %s cannot be cancelled.", doc._document_label().lower()))
            if doc.signature_ids.filtered('signature'):
                raise UserError(_("Signatures have already been captured; the document cannot be cancelled."))
            doc._wf().write({'state': 'cancel'})
            doc.activity_ids.unlink()
        return True

    def action_reset_draft(self):
        for doc in self:
            if doc.state != 'cancel':
                raise UserError(_("Only cancelled documents can be reset to draft."))
            doc.signature_ids.sudo()._wf().unlink()
            doc._wf().write({'state': 'draft', 'number': False, 'locked_content': False, 'locked_values': False,
                             'document_fingerprint': False, 'confirmed_on': False, 'confirmed_by_id': False})
        return True

    # ------------------------------------------------------------ integrity
    @api.model_create_multi
    def create(self, vals_list):
        if not self._is_internal_write():
            for vals in vals_list:
                bad = set(vals) & self._WORKFLOW_FIELDS
                if bad:
                    raise UserError(_("These fields are set by the agreement workflow and cannot be entered directly: %s.",
                                      ', '.join(sorted(bad))))
        return super().create(vals_list)

    def write(self, vals):
        """Server-side locks (BR-INT-001 and the signing phase). View attributes are not a
        security boundary, so every rule is enforced here regardless of how the write arrives."""
        if not self._is_internal_write():
            bad = set(vals) & self._WORKFLOW_FIELDS
            if bad:
                raise UserError(_("These fields are managed by the agreement workflow and cannot be written directly: %s. "
                                  "Use Confirm, the signing screen, Cancel or Reset to Draft.", ', '.join(sorted(bad))))
            locked = set(vals) & self._get_locked_fields()
            if locked:
                for doc in self.filtered(lambda d: d.state not in ('draft', 'cancel')):
                    raise UserError(_(
                        "%s %s is confirmed: %s can no longer be changed. Cancel and reset it to draft (possible only "
                        "before any signature is captured) or record the change through a Supplementary Annexure.",
                        doc._document_label(), doc.number or '', ', '.join(sorted(locked))))
            touched = set(vals) - self._EXECUTED_WRITABLE_FIELDS
            if touched:
                for doc in self.filtered(lambda d: d.state == 'executed'):
                    raise UserError(_(
                        "%s %s is fully executed and locked. Changes during its validity period must be recorded "
                        "through a Supplementary Annexure. (Fields: %s)",
                        doc._document_label(), doc.number, ', '.join(sorted(touched))))
        return super().write(vals)

    def unlink(self):
        for doc in self:
            if doc.state not in ('draft', 'cancel'):
                raise UserError(_("Only draft or cancelled documents can be deleted. %s is %s.",
                                  doc.number, dict(DOCUMENT_STATES).get(doc.state)))
        return super().unlink()
