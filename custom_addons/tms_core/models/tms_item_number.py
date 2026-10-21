from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class ResCompanyItemIncrement(models.Model):
    _inherit = "res.company"

    tms_item_increment = fields.Integer(string="Item increment", default=10, help="BR-CFG-12, BR-NUM-01")


class TmsItemNumberMixin(models.AbstractModel):
    """BR-NUM-01, BR-NUM-02: item numbers 10, 20, 30 … set at creation and never changed.

    Deleting a line leaves its number unused; a user may type an unused number between two items."""

    _name = "tms.item.number.mixin"
    _description = "Numbered order items"
    _tms_order_field = "order_id"

    tms_item_no = fields.Integer(string="Item", copy=False, index=True)

    def _tms_item_lines_domain(self, order):
        return [(self._tms_order_field, "=", order.id)]

    @api.model_create_multi
    def create(self, vals_list):
        records = self.browse()
        for vals in vals_list:
            if vals.get("display_type"):
                records |= super().create([vals])
                continue
            order = self.env[self._fields[self._tms_order_field].comodel_name].browse(vals.get(self._tms_order_field))
            increment = (order.company_id.tms_item_increment if order and "company_id" in order._fields else 0) or 10
            if not vals.get("tms_item_no") and order:
                last = max(order.tms_last_item_no, max(self.search(self._tms_item_lines_domain(order)).mapped(
                    "tms_item_no") or [0]))
                vals["tms_item_no"] = (last // increment + 1) * increment
            rec = super().create([vals])
            if order:
                if rec.tms_item_no and self.search_count(self._tms_item_lines_domain(order) + [
                        ("tms_item_no", "=", rec.tms_item_no), ("id", "!=", rec.id)]):
                    raise ValidationError(_("Item number %s is already used on this order (BR-NUM-02).",
                                            rec.tms_item_no))
                if rec.tms_item_no > order.tms_last_item_no:
                    order.sudo().tms_last_item_no = rec.tms_item_no
            records |= rec
        return records

    def write(self, vals):
        if "tms_item_no" in vals and any(line.tms_item_no and line.tms_item_no != vals["tms_item_no"] for line in self):
            raise UserError(_("An item number never changes (BR-NUM-02)."))
        return super().write(vals)

    def _tms_item_label(self):
        self.ensure_one()
        order = self[self._tms_order_field]
        return "%s/%s" % (order.name, self.tms_item_no)


class PurchaseOrder(models.Model):
    _inherit = "purchase.order"

    tms_last_item_no = fields.Integer(copy=False, readonly=True, string="Last item number")


class PurchaseOrderLine(models.Model):
    _name = "purchase.order.line"
    _inherit = ["purchase.order.line", "tms.item.number.mixin"]
