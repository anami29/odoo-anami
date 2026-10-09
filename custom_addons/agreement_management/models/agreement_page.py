# -*- coding: utf-8 -*-
"""Page-wise signing.

A document has no pages until it is rendered — the content is continuous HTML and
pagination happens inside wkhtmltopdf. So "a signature on every page" needs the
document rendered first, its pages counted, and a signature area created against
each real page.

That is done once, when Party A finishes signing: the document is then fixed for
Party B, who signs it page by page in the portal against exactly the pages stored
here. Re-rendering later could repaginate and leave signatures attesting to pages
that no longer exist.
"""
import base64
import logging
import subprocess
import tempfile
from pathlib import Path

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

RENDER_DPI = 110          # legible on screen without producing very large images
RENDER_TIMEOUT = 120      # seconds; a long contract is still only a few seconds


class AgreementPage(models.Model):
    _name = 'agreement.page'
    _description = 'Agreement Document Page'
    _order = 'agreement_id, annexure_id, number'

    agreement_id = fields.Many2one('agreement.agreement', ondelete='cascade', index=True)
    annexure_id = fields.Many2one('agreement.annexure', ondelete='cascade', index=True)
    number = fields.Integer(string='Page', required=True)
    total = fields.Integer(string='Of', required=True)
    image = fields.Binary(string='Page Image', attachment=True, readonly=True)
    signature_id = fields.Many2one('agreement.signature', string='Signature Area',
                                   ondelete='set null', readonly=True)
    signed = fields.Boolean(compute='_compute_signed', string='Signed')

    @api.depends('signature_id.signature')
    def _compute_signed(self):
        # a related Boolean on a Binary field would coerce the image itself
        for page in self:
            page.signed = bool(page.signature_id.signature)

    _sql_constraints = [
        ('agreement_number_uniq', 'unique(agreement_id, number)',
         'That page already exists for this agreement.'),
        ('annexure_number_uniq', 'unique(annexure_id, number)',
         'That page already exists for this annexure.'),
    ]

    def name_get(self):
        return [(page.id, _('Page %(n)s of %(t)s', n=page.number, t=page.total)) for page in self]

    @api.model
    def _pdf_to_page_images(self, pdf_content):
        """Split a PDF into one PNG per page, in order.

        Uses poppler's pdftoppm, which the Dockerfile installs. Returns [] and logs if
        it is unavailable, so a missing binary degrades to "no page-wise signing"
        rather than stranding an agreement mid-workflow.
        """
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'document.pdf'
            source.write_bytes(pdf_content)
            try:
                subprocess.run(
                    ['pdftoppm', '-png', '-r', str(RENDER_DPI), str(source), str(Path(tmp) / 'page')],
                    check=True, capture_output=True, timeout=RENDER_TIMEOUT)
            except FileNotFoundError:
                _logger.warning("pdftoppm is not installed; page-wise signing is unavailable")
                return []
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                _logger.warning("Could not split the document into pages: %s", exc)
                return []
            # pdftoppm pads the page number, so a plain sort is page order
            return [p.read_bytes() for p in sorted(Path(tmp).glob('page-*.png'))]
