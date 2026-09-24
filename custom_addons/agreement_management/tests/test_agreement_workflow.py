# -*- coding: utf-8 -*-
from unittest.mock import patch

from odoo.addons.agreement_management.models.agreement_document_mixin import (
    BLANK_PAGE_MARKER, render_document_html)
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

# 1x1 transparent PNG
PNG_B64 = b'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=='
FAKE_PDF = (b'%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF', 'pdf')


@tagged('post_install', '-at_install', 'agreement')
class TestAgreementWorkflow(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.company.write({'agreement_auto_email': True, 'agreement_allow_facilitated_signing': True})
        cls.manager = cls.env['res.users'].create({
            'name': 'Agreement Manager',
            'login': 'agr_manager',
            'email': 'agr.manager@example.com',
            'groups_id': [(6, 0, [cls.env.ref('base.group_user').id,
                                  cls.env.ref('agreement_management.group_agreement_manager').id])],
        })
        cls.party_a = cls.company.partner_id
        cls.signatory_a = cls.env['res.partner'].create({
            'name': 'Anita Deshmukh', 'parent_id': cls.party_a.id, 'type': 'contact',
            'email': 'anita@example.com', 'function': 'Director'})
        cls.party_b = cls.env['res.partner'].create({
            'name': 'Nexus Logistics LLP', 'is_company': True, 'email': 'legal@nexus.example.com'})
        cls.signatory_b = cls.env['res.partner'].create({
            'name': 'Farhan Qureshi', 'parent_id': cls.party_b.id, 'type': 'contact',
            'email': 'farhan@nexus.example.com', 'function': 'Managing Partner'})

        cls.template = cls.env['agreement.template'].with_user(cls.manager).create({
            'name': 'Master Service Agreement', 'code': 'msa',
        })
        cls.v1 = cls.template.version_ids
        assert cls.v1, "A first draft version must be created with the template"
        cls.v1.write({
            'content': '<h2>MSA</h2><p>No. {{agreement_number}} between {{party_a}} and {{party_b}}. '
                       'Value {{contract_value}} · {{payment_terms}}.</p><p>{{sig:A1}} {{sig:B1}}</p>',
            'field_ids': [
                (0, 0, {'name': 'Contract Value', 'key': 'contract_value', 'field_type': 'monetary', 'required': True}),
                (0, 0, {'name': 'Payment Terms', 'key': 'payment_terms', 'field_type': 'selection',
                        'selection_values': 'Net 30\nNet 45'}),
                (0, 0, {'name': 'Revised Value', 'key': 'revised_value', 'field_type': 'float',
                        'applies_to': 'annexure'}),
            ],
            'area_ids': [
                (0, 0, {'code': 'A1', 'name': 'Execution – Party A', 'party': 'a', 'page': 2}),
                (0, 0, {'code': 'B1', 'name': 'Execution – Party B', 'party': 'b', 'page': 2}),
                (0, 0, {'code': 'A2', 'name': 'Initials – Schedule', 'party': 'a', 'kind': 'initials', 'required': False}),
                (0, 0, {'code': 'A1', 'name': 'Annexure – Party A', 'party': 'a', 'applies_to': 'annexure'}),
                (0, 0, {'code': 'B1', 'name': 'Annexure – Party B', 'party': 'b', 'applies_to': 'annexure'}),
            ],
        })
        cls.v1.action_activate()

    # ------------------------------------------------------------- helpers
    def _create_agreement(self, **vals):
        values = {
            'template_id': self.template.id,
            'partner_a_id': self.party_a.id,
            'signatory_a_id': self.signatory_a.id,
            'partner_b_id': self.party_b.id,
            'signatory_b_id': self.signatory_b.id,
            'date_from': '2026-01-01',
            'date_to': '2027-09-30',
            'agreement_properties': {'contract_value': 1850000.0, 'payment_terms': 'net_30'},
        }
        values.update(vals)
        return self.env['agreement.agreement'].with_user(self.manager).create(values)

    def _sign(self, doc, party):
        values = {sig.id: PNG_B64 for sig in doc.signature_ids.filtered(lambda s: s.party == party)}
        with patch.object(type(self.env['ir.actions.report']), '_render_qweb_pdf', return_value=FAKE_PDF):
            doc._register_signatures(party, values, signer_ip='127.0.0.1')

    def _executed_agreement(self):
        agreement = self._create_agreement()
        agreement.action_confirm()
        self._sign(agreement, 'a')
        self._sign(agreement, 'b')
        return agreement

    # --------------------------------------------------------------- tests
    def test_01_template_versioning(self):
        self.assertEqual(self.template.code, 'MSA')
        self.assertEqual(self.template.active_version_id, self.v1)
        self.assertEqual(self.v1.state, 'active')
        definition = {p['name']: p for p in self.v1.properties_definition}
        self.assertIn('contract_value', definition)
        self.assertEqual(definition['payment_terms']['type'], 'selection')
        self.assertNotIn('revised_value', definition)
        self.assertIn('revised_value', {p['name'] for p in self.v1.annexure_properties_definition})
        # locked once active
        with self.assertRaises(UserError):
            self.v1.write({'content': '<p>changed</p>'})
        # new draft, activation supersedes
        action = self.template.action_new_version()
        v2 = self.env['agreement.template.version'].browse(action['res_id'])
        self.assertEqual(v2.name, '2.0')
        self.assertEqual(v2.state, 'draft')
        self.assertEqual(len(v2.area_ids), 5)
        v2.action_activate()
        self.assertEqual(self.v1.state, 'superseded')
        self.assertEqual(self.v1.superseded_by_id, v2)
        self.assertEqual(self.template.active_version_id, v2)
        # only one active version
        with self.assertRaises(ValidationError):
            self.v1.with_context(agreement_version_unlock=True).write({'state': 'active'})

    def test_02_confirm_validation_and_numbering(self):
        agreement = self._create_agreement(signatory_b_id=False, agreement_properties={})
        self.assertFalse(agreement.number)
        self.assertEqual(agreement.template_version_id, self.template.active_version_id)
        with self.assertRaisesRegex(UserError, 'Signatory B'):
            agreement.action_confirm()
        agreement.signatory_b_id = self.signatory_b
        with self.assertRaisesRegex(UserError, 'mandatory information'):
            agreement.action_confirm()
        agreement.agreement_properties = {'contract_value': 500.0}
        agreement.action_confirm()
        self.assertEqual(agreement.state, 'pending_a')
        self.assertRegex(agreement.number, r'^AGR/\d{4}/\d{5}$')
        self.assertTrue(agreement.locked_content)
        self.assertEqual(len(agreement.signature_ids), 3)
        self.assertEqual(agreement.signature_required_count, 2)
        # version locked: activating a new version does not move the agreement
        action = self.template.action_new_version()
        v2 = self.env['agreement.template.version'].browse(action['res_id'])
        v2.action_activate()
        agreement.invalidate_recordset()
        self.assertEqual(agreement.template_version_id, self.v1)
        # a draft references the active version only
        draft = self._create_agreement()
        self.assertEqual(draft.template_version_id, v2)

    def test_03_sequential_signing_and_execution(self):
        agreement = self._create_agreement()
        agreement.action_confirm()
        # Party B cannot sign before Party A
        with self.assertRaises(UserError):
            agreement._open_sign_wizard('b')
        # Party A must complete all required areas
        with self.assertRaisesRegex(UserError, 'Party A must complete'):
            agreement._register_signatures('a', {})
        self.assertEqual(agreement.state, 'pending_a')
        self._sign(agreement, 'a')
        self.assertEqual(agreement.state, 'pending_b')
        self.assertTrue(agreement.signed_a_on)
        b_areas = agreement.signature_ids.filtered(lambda s: s.party == 'b')
        self.assertEqual(b_areas.mapped('state'), ['ready'])
        self._sign(agreement, 'b')
        self.assertEqual(agreement.state, 'executed')
        self.assertTrue(agreement.executed_on)
        self.assertTrue(agreement.executed_pdf_attachment_id)
        self.assertEqual(agreement.executed_pdf_attachment_id.mimetype, 'application/pdf')
        self.assertTrue(agreement.executed_hash)
        self.assertEqual(agreement.signing_progress, 100.0)
        # automatic distribution logged
        self.assertEqual(len(agreement.distribution_ids), 1)
        self.assertEqual(agreement.distribution_ids.mode, 'auto')
        self.assertIn(self.party_b, agreement.distribution_ids.partner_ids)
        self.assertEqual(agreement.distribution_ids.attachment_id, agreement.executed_pdf_attachment_id)
        # rendered document embeds signatures
        html = str(agreement._render_document_html())
        self.assertIn('data:image/png;base64', html)
        self.assertIn(agreement.number, html)

    def test_04_executed_is_locked(self):
        agreement = self._executed_agreement()
        with self.assertRaises(UserError):
            agreement.write({'date_to': '2030-01-01'})
        with self.assertRaises(UserError):
            agreement.write({'partner_b_id': self.party_a.id})
        with self.assertRaises(UserError):
            agreement.unlink()
        # allowed: responsible user
        agreement.write({'user_id': self.manager.id})
        # print action points at the stored attachment
        action = agreement.action_print_executed()
        self.assertIn('/web/content/%s' % agreement.executed_pdf_attachment_id.id, action['url'])
        # manual email composer carries the stored PDF
        composer_action = agreement.action_email_executed()
        self.assertEqual(composer_action['res_model'], 'mail.compose.message')
        self.assertEqual(composer_action['context']['agreement_forced_attachment_ids'],
                         [agreement.executed_pdf_attachment_id.id])

    def test_05_annexure_workflow(self):
        agreement = self._executed_agreement()
        draft = self._create_agreement()
        with self.assertRaises(UserError):
            self.env['agreement.annexure'].create({'agreement_id': draft.id})
        action = agreement.action_create_annexure()
        annexure = self.env['agreement.annexure'].browse(action['res_id'])
        self.assertEqual(annexure.partner_b_id, self.party_b)
        self.assertEqual(annexure.template_version_id, agreement.template_version_id)
        self.assertTrue(annexure.content)
        annexure.write({'date_effective': '2027-01-01', 'summary': 'Scope addition',
                        'annexure_properties': {'revised_value': 2000000.0}})
        annexure.action_confirm()
        self.assertEqual(annexure.number, '%s-ANN-01' % agreement.number)
        self.assertEqual(len(annexure.signature_ids), 2)
        self._sign(annexure, 'a')
        self.assertEqual(annexure.state, 'pending_b')
        self._sign(annexure, 'b')
        self.assertEqual(annexure.state, 'executed')
        self.assertTrue(annexure.executed_pdf_attachment_id)
        # original unchanged and second annexure numbering
        self.assertEqual(agreement.state, 'executed')
        self.assertEqual(agreement.annexure_count, 1)
        second = self.env['agreement.annexure'].create({'agreement_id': agreement.id, 'date_effective': '2027-02-01'})
        second.action_confirm()
        self.assertEqual(second.number, '%s-ANN-02' % agreement.number)

    def test_06_party_and_date_constraints(self):
        with self.assertRaises(ValidationError):
            self._create_agreement(partner_b_id=self.party_a.id, signatory_b_id=self.signatory_a.id)
        with self.assertRaises(ValidationError):
            self._create_agreement(date_to='2025-12-31')
        with self.assertRaises(ValidationError):
            self._create_agreement(signatory_b_id=self.signatory_a.id)
        self.company.agreement_allow_same_party = True
        same = self._create_agreement(partner_b_id=self.party_a.id, signatory_b_id=self.signatory_a.id)
        self.assertTrue(same)

    def test_07_signature_write_guard(self):
        agreement = self._create_agreement()
        agreement.action_confirm()
        area = agreement.signature_ids.filtered(lambda s: s.party == 'a')[:1]
        with self.assertRaises(UserError):
            area.write({'signature': PNG_B64})
        with self.assertRaises(UserError):
            area.unlink()

    def test_08_tampering_is_blocked(self):
        """Server-side locks hold regardless of view attributes or client-supplied context."""
        agreement = self._create_agreement()
        agreement.action_confirm()
        # workflow fields cannot be written directly, even by a manager, even with spoofed flags
        for vals in ({'state': 'executed'}, {'number': 'AGR/2026/99999'}, {'executed_on': '2026-01-01 00:00:00'},
                     {'locked_content': '<p>changed</p>'}, {'document_fingerprint': 'x'}):
            with self.assertRaises(UserError):
                agreement.with_context(agreement_workflow=True, agreement_force_write=True, agreement_internal=True).write(vals)
        # business fields are frozen from confirmation
        for vals in ({'partner_b_id': self.party_a.id}, {'date_to': '2030-01-01'},
                     {'agreement_properties': {'contract_value': 1.0}}, {'signatory_b_id': self.signatory_a.id}):
            with self.assertRaises(UserError):
                agreement.write(vals)
        agreement.write({'user_id': self.manager.id})  # still allowed
        # signature areas: no direct signature, no required-flag change, no deletion
        area_b = agreement.signature_ids.filtered(lambda s: s.party == 'b')[:1]
        for vals in ({'signature': PNG_B64}, {'required': False}, {'party': 'a'}, {'sequence': 99}):
            with self.assertRaises(UserError):
                area_b.with_context(agreement_signing=True, agreement_workflow=True).write(vals)
        with self.assertRaises(UserError):
            area_b.unlink()
        with self.assertRaises(UserError):
            self.env['agreement.signature'].with_user(self.manager).create({'agreement_id': agreement.id, 'code': 'X1', 'name': 'x', 'party': 'b'})
        # the document renders from the frozen values: a superuser-level partner rename does not change it
        before = str(agreement._render_document_html())
        self.party_b.sudo().write({'name': 'Renamed Counterparty'})
        self.assertEqual(str(agreement._render_document_html()), before)
        self.assertTrue(agreement._integrity_check())
        # a change made outside the workflow is detected before any further signing
        agreement.sudo().with_context(agreement_workflow=True).write({'locked_content': '<p>tampered {{sig:A1}} {{sig:B1}}</p>'})
        agreement.invalidate_recordset()
        self.assertFalse(agreement._integrity_check(raise_error=False))
        with self.assertRaisesRegex(UserError, 'Integrity check failed'):
            self._sign(agreement, 'a')

    def test_08b_create_accepts_the_form_defaults(self):
        """The web client posts the form's defaults on create; the status bar sends
        state='draft'. That must not be mistaken for tampering (BR-INT-001)."""
        agreement = self._create_agreement(state='draft', number=False, executed_on=False)
        self.assertEqual(agreement.state, 'draft')
        self.assertFalse(agreement.number)
        # a real value for a workflow field is still refused
        for bad in ({'state': 'executed'}, {'number': 'AGR/2026/99999'}, {'document_fingerprint': 'x'}):
            with self.assertRaises(UserError):
                self._create_agreement(**bad)

    def test_08c_blank_page_placeholder(self):
        """{{insert_blank_page}} yields a page that is deliberately empty, in any casing."""
        for token in ('{{insert_blank_page}}', '{{ insert_blank_page }}', '{{INSERT_BLANK_PAGE}}'):
            html = str(render_document_html('<p>A</p>%s<p>B</p>' % token, {}, [], 'preview'))
            self.assertIn('page-break-before:always', html, token)
            self.assertIn('page-break-after:always', html, token)
            # carries the marker the PDF layer looks for when emptying the page
            self.assertIn(BLANK_PAGE_MARKER, html, token)
            # not mistaken for an unknown placeholder
            self.assertNotIn('[insert blank page]', html)

    def test_09_executed_pdf_is_protected(self):
        agreement = self._executed_agreement()
        attachment = agreement.executed_pdf_attachment_id
        self.assertEqual(attachment.mimetype, 'application/pdf')
        self.assertTrue(agreement.integrity_ok)
        with self.assertRaises(UserError):
            attachment.with_user(self.manager).write({'datas': PNG_B64})
        with self.assertRaises(UserError):
            attachment.with_user(self.manager).unlink()
        # a superuser-level replacement is at least detected
        attachment.sudo().write({'raw': b'%PDF-1.4 replaced'})
        agreement.invalidate_recordset()
        self.assertFalse(agreement._integrity_check(raise_error=False))
        action = agreement.action_verify_integrity()
        self.assertEqual(action['params']['type'], 'danger')

