# -*- coding: utf-8 -*-
from odoo import _, models
from odoo.exceptions import UserError

PROTECTED_MODELS = ('agreement.agreement', 'agreement.annexure')
CONTENT_FIELDS = {'datas', 'raw', 'db_datas', 'store_fname', 'checksum', 'mimetype', 'name', 'res_model', 'res_id',
                  'res_field', 'type', 'url', 'public'}


class IrAttachment(models.Model):
    _inherit = 'ir.attachment'

    def _agreement_protected(self):
        """Attachments that are the official executed copy of an agreement or annexure."""
        candidates = self.filtered(lambda a: a.res_model in PROTECTED_MODELS)
        if not candidates or self.env.su:
            return self.browse()
        protected = self.browse()
        for model in PROTECTED_MODELS:
            docs = self.env[model].sudo().search([('executed_pdf_attachment_id', 'in', candidates.ids)])
            protected |= docs.mapped('executed_pdf_attachment_id')
        return protected

    def write(self, vals):
        if set(vals) & CONTENT_FIELDS:
            protected = self._agreement_protected()
            if protected:
                raise UserError(_("%s is the official executed copy of an agreement and cannot be modified.",
                                  ', '.join(protected.mapped('name'))))
        return super().write(vals)

    def unlink(self):
        protected = self._agreement_protected()
        if protected:
            raise UserError(_("%s is the official executed copy of an agreement and cannot be deleted.",
                              ', '.join(protected.mapped('name'))))
        return super().unlink()
