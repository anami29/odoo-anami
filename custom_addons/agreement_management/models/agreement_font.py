# -*- coding: utf-8 -*-
import base64
import os

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

# wkhtmltopdf's engine reads TrueType and OpenType reliably; the web font formats are
# only understood by the browser, so a .woff2 upload shows in the editor and silently
# falls back in the PDF. Keep both, warn about the difference on the field.
FONT_FORMATS = {
    '.ttf': ('font/ttf', 'truetype'),
    '.otf': ('font/otf', 'opentype'),
    '.woff': ('font/woff', 'woff'),
    '.woff2': ('font/woff2', 'woff2'),
}


class AgreementFont(models.Model):
    _name = 'agreement.font'
    _description = 'Agreement Document Font'
    _order = 'sequence, name'

    name = fields.Char(
        required=True, translate=False,
        help="Name shown in the font dropdown of the agreement content editor.")
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    font_family = fields.Char(
        string='CSS Font Family', compute='_compute_font_family', store=True, readonly=False, required=True,
        help="The CSS font stack applied to the text, e.g. \"'Liberation Serif', 'Times New Roman', serif\". "
             "Filled in from the name; edit it to add fallbacks.")
    font_file = fields.Binary(
        string='Font File', attachment=True,
        help="Optional. Upload a .ttf or .otf to use a font that is not installed on the server — it is embedded "
             "in the PDF. .woff and .woff2 work in the editor but not in the generated PDF.")
    font_file_filename = fields.Char(string='Font Filename')
    company_id = fields.Many2one('res.company', string='Company', default=lambda self: self.env.company)

    _sql_constraints = [
        ('name_company_uniq', 'unique(name, company_id)', 'A font with this name already exists.'),
    ]

    @api.depends('name')
    def _compute_font_family(self):
        for font in self:
            if not font.font_family and font.name:
                font.font_family = "'%s', sans-serif" % font.name

    @api.constrains('font_file_filename', 'font_file')
    def _check_font_file(self):
        for font in self:
            if font.font_file and os.path.splitext(font.font_file_filename or '')[1].lower() not in FONT_FORMATS:
                raise ValidationError(_(
                    "%s is not a font file. Upload a .ttf, .otf, .woff or .woff2.",
                    font.font_file_filename or _('The uploaded file')))

    def _font_face_css(self):
        """@font-face rules for the uploaded font files, as data: URIs.

        Embedded rather than linked so the rules work identically in the browser editor
        and in wkhtmltopdf, which fetches nothing from the Odoo session.
        """
        rules = []
        for font in self:
            extension = os.path.splitext(font.font_file_filename or '')[1].lower()
            if not font.font_file or extension not in FONT_FORMATS:
                continue
            mimetype, css_format = FONT_FORMATS[extension]
            data = font.font_file if isinstance(font.font_file, str) else font.font_file.decode()
            rules.append(
                "@font-face { font-family: '%s'; src: url(data:%s;base64,%s) format('%s'); "
                "font-weight: normal; font-style: normal; }" % (font.name, mimetype, data, css_format))
        return Markup('\n'.join(rules))

    @api.model
    def _document_fonts(self):
        return self.sudo().search([
            '|', ('company_id', '=', False), ('company_id', 'in', self.env.companies.ids),
        ])

    @api.model
    def get_editor_fonts(self):
        """Font list and @font-face rules for the agreement content editor."""
        fonts = self._document_fonts()
        return {
            'fonts': [{'name': font.name, 'family': font.font_family} for font in fonts],
            'css': str(fonts._font_face_css()),
        }
