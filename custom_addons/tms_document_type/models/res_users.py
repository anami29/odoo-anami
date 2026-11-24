from odoo import fields, models


class ResUsers(models.Model):
    _inherit = "res.users"

    tms_document_type_ids = fields.Many2many(
        "tms.document.type", "res_users_tms_document_type_rel", "user_id", "type_id",
        string="Personal default document types",
        help="One per kind of document; documents the transport flows create still take the Transport type.")
