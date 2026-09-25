# -*- coding: utf-8 -*-
import binascii

from odoo import _, http
from odoo.exceptions import AccessError, MissingError, UserError
from odoo.http import request

from odoo.addons.portal.controllers.portal import CustomerPortal, pager as portal_pager


class AgreementCustomerPortal(CustomerPortal):
    """Agreements on the customer portal, so a Party B signatory who has no access to
    the backend can review and sign from the website alone."""

    # ------------------------------------------------------------------ home
    def _prepare_home_portal_values(self, counters):
        values = super()._prepare_home_portal_values(counters)
        if 'agreement_count' in counters:
            values['agreement_count'] = request.env['agreement.agreement'].search_count(
                self._agreement_portal_domain())
        return values

    def _agreement_portal_domain(self):
        """Agreements this portal user is a party to or signs for."""
        partner = request.env.user.partner_id
        partners = partner | partner.child_ids | partner.commercial_partner_id
        return [
            '|', '|', '|',
            ('signatory_a_id', 'in', partners.ids), ('signatory_b_id', 'in', partners.ids),
            ('partner_a_id', 'in', partners.ids), ('partner_b_id', 'in', partners.ids),
        ]

    # ------------------------------------------------------------------ list
    @http.route(['/my/agreements', '/my/agreements/page/<int:page>'], type='http', auth='user', website=True)
    def portal_my_agreements(self, page=1, **kw):
        Agreement = request.env['agreement.agreement']
        domain = self._agreement_portal_domain()
        total = Agreement.search_count(domain)
        pager = portal_pager(
            url='/my/agreements', total=total, page=page, step=self._items_per_page,
        )
        agreements = Agreement.search(domain, order='create_date desc',
                                      limit=self._items_per_page, offset=pager['offset'])
        request.session['my_agreements_history'] = agreements.ids[:100]
        return request.render('agreement_management.portal_my_agreements', {
            'agreements': agreements.sudo(),
            'page_name': 'agreement',
            'pager': pager,
            'default_url': '/my/agreements',
        })

    # ------------------------------------------------------------- document
    @http.route(['/my/agreements/<int:agreement_id>'], type='http', auth='public', website=True)
    def portal_agreement_page(self, agreement_id, access_token=None, **kw):
        try:
            agreement = self._document_check_access('agreement.agreement', agreement_id, access_token)
        except (AccessError, MissingError):
            return request.redirect('/my')
        return request.render('agreement_management.portal_agreement_page', {
            'agreement': agreement,
            'document_html': agreement._render_document_html(
                'signed' if agreement.state == 'executed' else 'preview'),
            'signing_party': self._portal_signing_party(agreement),
            'page_name': 'agreement',
            'token': access_token,
            'report_type': 'html',
        })

    def _portal_signing_party(self, agreement):
        """'a' or 'b' when this visitor may sign right now, otherwise None.

        Reuses the backend's own rule — the designated signatory's user, or a manager —
        so the portal cannot sign anything the backend would refuse.
        """
        party = {'pending_a': 'a', 'pending_b': 'b'}.get(agreement.state)
        if not party:
            return None
        return party if agreement.sudo()._user_can_sign(party) else None

    # -------------------------------------------------------------- download
    @http.route(['/my/agreements/<int:agreement_id>/download'], type='http', auth='public', website=True)
    def portal_agreement_download(self, agreement_id, access_token=None, **kw):
        """Serve the executed PDF through the portal.

        /web/content cannot be used: it checks ir.attachment access directly, which a
        portal user does not have on the agreement's attachment, and answers 404. Access
        to the agreement is what should decide this, so check that and stream as sudo.
        """
        try:
            agreement = self._document_check_access('agreement.agreement', agreement_id, access_token)
        except (AccessError, MissingError):
            return request.redirect('/my')
        attachment = agreement.executed_pdf_attachment_id.sudo()
        if not attachment:
            return request.redirect(agreement.get_portal_url())
        return request.env['ir.binary']._get_stream_from(attachment).get_response(as_attachment=True)

    # --------------------------------------------------------------- signing
    @http.route(['/my/agreements/<int:agreement_id>/sign'], type='json', auth='public', website=True)
    def portal_agreement_sign(self, agreement_id, access_token=None, name=None, signature=None, **kw):
        access_token = access_token or request.httprequest.args.get('access_token')
        try:
            agreement = self._document_check_access('agreement.agreement', agreement_id, access_token)
        except (AccessError, MissingError):
            return {'error': _("This agreement is no longer available.")}

        party = self._portal_signing_party(agreement)
        if not party:
            return {'error': _("This agreement is not awaiting your signature.")}
        if not signature:
            return {'error': _("Please draw or type your signature before signing.")}

        # One captured signature fills every area assigned to the party, which is how the
        # backend signing screen's "apply to all remaining areas" behaves.
        areas = agreement.signature_ids.filtered(lambda s: s.party == party)
        values = {area.id: signature for area in areas}
        try:
            agreement._register_signatures(
                party, values, signer_ip=request.httprequest.remote_addr)
        except (TypeError, binascii.Error):
            return {'error': _("That signature could not be read. Please try again.")}
        except UserError as error:
            return {'error': error.args[0] if error.args else _("The signature could not be recorded.")}

        return {'force_refresh': True, 'redirect_url': agreement.get_portal_url()}
