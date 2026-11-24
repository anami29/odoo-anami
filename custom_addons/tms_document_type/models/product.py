from odoo import api, fields, models


class ProductTemplate(models.Model):
    _inherit = "product.template"

    tms_transport_only = fields.Boolean(
        string="Transport only", compute="_compute_tms_transport_only", store=True, readonly=False,
        help="Only transport documents may use this product: cost items, freight and hire services. "
             "Standard sales and purchases refuse it.")

    @api.depends("tms_cost_item", "categ_id")
    def _compute_tms_transport_only(self):
        transport = self.env.ref("tms_document_type.product_category_transport", raise_if_not_found=False)
        for tmpl in self:
            in_cat = bool(transport and tmpl.categ_id and tmpl.categ_id.parent_path
                          and tmpl.categ_id.parent_path.startswith(transport.parent_path))
            tmpl.tms_transport_only = tmpl.tms_transport_only or tmpl.tms_cost_item or in_cat
