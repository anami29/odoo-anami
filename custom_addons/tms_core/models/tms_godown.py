"""BR-LR-10, BR-POD-09: refused goods held at the delivery branch's godown are delivered later or returned
to origin on a new LR billed to the billing party."""
from odoo import _, fields, models
from odoo.exceptions import UserError


class TmsConsignment(models.Model):
    _inherit = "tms.consignment"

    godown_delivered_on = fields.Datetime(readonly=True, copy=False,
                                          help="Refused goods delivered later from the godown (BR-LR-10).")
    return_of_id = fields.Many2one("tms.consignment", string="Return of", readonly=True, copy=False, index=True)
    return_lr_ids = fields.One2many("tms.consignment", "return_of_id", string="Return LRs")

    def _tms_check_godown(self):
        for lr in self:
            if not lr.godown_hold:
                raise UserError(_("LR %s has no refused goods held in the godown (BR-POD-09).", lr.name))

    def action_deliver_from_godown(self):
        """BR-LR-10: a refused LR moves on to Delivered once its goods are accepted."""
        self._tms_check_godown()
        for lr in self:
            if lr.freight_basis == "topay" and not lr.collected:
                raise UserError(_("To-Pay LR %s is released only after its freight is collected (RL-06).", lr.name))
            lr._tms_raise_godown_demurrage()
            lr.sudo().write({"godown_hold": False, "godown_delivered_on": fields.Datetime.now(),
                             "delivered_on": lr.delivered_on or fields.Datetime.now()})
            lr.message_post(body=_("Refused goods delivered from the godown."))
            lr._tms_refresh_state()
        return True

    def action_return_to_origin(self):
        """BR-POD-09: return the refused goods to origin on a new LR billed to the billing party."""
        self._tms_check_godown()
        new_lrs = self.env["tms.consignment"]
        for lr in self:
            lr._tms_raise_godown_demurrage()
            new = lr.copy({
                "return_of_id": lr.id,
                "consignor_id": lr.consignee_id.id,
                "consignee_id": lr.consignor_id.id,
                "billing_partner_id": lr.billing_partner_id.id,
                "origin_id": lr.destination_id.id,
                "destination_id": lr.origin_id.id,
                "company_id": (lr.delivery_branch_id or lr.company_id).id,
                "delivery_branch_id": lr.company_id.id,
                "freight_basis": "tbb",
            })
            new.line_ids.filtered("ewaybill_id").write({"ewaybill_id": False})  # a new bill moves the return
            if "sale_line_id" in new._fields and new.sale_line_id:
                new.sale_line_id = False  # the return is billed at its own rate, not the order item's
            lr.sudo().write({"godown_hold": False})
            lr.message_post(body=_("Refused goods returned to origin on LR %s.", new.name))
            new_lrs |= new
        return {"type": "ir.actions.act_window", "res_model": "tms.consignment", "view_mode": "form",
                "res_id": new_lrs[:1].id} if len(new_lrs) == 1 else True

    def _tms_refresh_state(self):
        res = super()._tms_refresh_state()
        for lr in self.sudo().filtered(lambda r: r.godown_delivered_on and r.state == "not_delivered"):
            lr.state = "delivered"
            lr._tms_refresh_invoiced()
        return res
