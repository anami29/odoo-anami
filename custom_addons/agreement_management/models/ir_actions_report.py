# -*- coding: utf-8 -*-
import base64
import logging

from odoo import models
from odoo.tools.pdf import merge_pdf

_logger = logging.getLogger(__name__)

# Reports driven by a template version: cover page and per-page signature footer.
AGREEMENT_REPORTS = (
    'agreement_management.report_agreement_document',
    'agreement_management.report_agreement_annexure_document',
    'agreement_management.report_agreement_version_sample',
)


class IrActionsReport(models.Model):
    _inherit = 'ir.actions.report'

    def get_paperformat(self):
        """Deeper bottom margin while rendering a document that signs every page."""
        if self.env.context.get('agreement_sign_footer'):
            paperformat = self.env.ref('agreement_management.paperformat_agreement_sign_footer',
                                       raise_if_not_found=False)
            if paperformat:
                return paperformat
        return super().get_paperformat()

    def _render_qweb_pdf(self, report_ref, res_ids=None, data=None):
        """Apply the version's cover page and signature-footer settings.

        The layout preview, the agreement/annexure preview and the executed PDF all come
        through here, so the three stay identical — and the hash in executed_pdf_sha256
        covers the file people actually receive.
        """
        version = self._agreement_version(report_ref, res_ids)

        # A signature strip needs more room at the foot than the page number alone, and
        # the margin belongs to the paperformat, which is chosen before rendering starts.
        if version and version.sign_every_page and not self.env.context.get('agreement_sign_footer'):
            return self.with_context(agreement_sign_footer=True)._render_qweb_pdf(
                report_ref, res_ids=res_ids, data=data)

        pdf_content, report_type = super()._render_qweb_pdf(report_ref, res_ids=res_ids, data=data)

        if version and version.cover_page and pdf_content:
            try:
                pdf_content = merge_pdf([base64.b64decode(version.cover_page), pdf_content])
            except Exception:
                # An unreadable cover must never block a document: the executed PDF is
                # generated automatically when the last required signature is captured,
                # and an exception there would strand the agreement mid-workflow.
                _logger.warning("Agreement cover page could not be merged for %s; "
                                "producing the document without it.", report_ref, exc_info=True)
        return pdf_content, report_type

    def _agreement_version(self, report_ref, res_ids):
        """Template version behind a single rendered agreement document, or None.

        Skipped when several records print at once: one cover cannot be placed in front
        of each document after the fact, and a batch is never how an executed copy is
        produced.
        """
        if isinstance(res_ids, int):
            res_ids = [res_ids]
        if not res_ids or len(res_ids) != 1:
            return None
        report = self._get_report(report_ref)
        if report.report_name not in AGREEMENT_REPORTS:
            return None
        record = self.env[report.model].browse(res_ids)
        version = record if record._name == 'agreement.template.version' else record.template_version_id
        return version.sudo() or None
