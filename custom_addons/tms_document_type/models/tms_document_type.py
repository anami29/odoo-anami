from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

APPLIES_TO = [
    ("sale", "Sales order"),
    ("purchase", "Purchase order"),
    ("out_invoice", "Customer invoice"),
    ("in_invoice", "Vendor bill"),
]
BEHAVIOURS = [("transport", "Transport"), ("standard", "Standard")]
INVOICE_OF = {"sale": "out_invoice", "purchase": "in_invoice"}
MOVE_APPLIES = {"out_invoice": "out_invoice", "out_refund": "out_invoice", "in_invoice": "in_invoice",
                "in_refund": "in_invoice"}


class TmsDocumentType(models.Model):
    """BR-DOC-01: every sales order, purchase order, customer invoice and vendor bill carries a document type
    whose behaviour decides whether the transport rules apply."""

    _name = "tms.document.type"
    _description = "Document type"
    _inherit = ["mail.thread"]
    _order = "applies_to, sequence, id"

    name = fields.Char(required=True, translate=True, tracking=True)
    code = fields.Char(required=True, tracking=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True, tracking=True)
    applies_to = fields.Selection(APPLIES_TO, string="Applies to", required=True, tracking=True)
    behaviour = fields.Selection(BEHAVIOURS, required=True, default="standard", tracking=True,
                                 help="Transport: every transport rule applies. Standard: the document runs as "
                                      "plain Odoo and carries no trip, LR, estimate, vehicle or cost-class link.")
    is_default = fields.Boolean(string="Default", tracking=True,
                                help="Offered on new documents unless the user has a personal default.")
    invoice_type_id = fields.Many2one("tms.document.type", string="Invoice / bill type", tracking=True,
                                      domain="[('applies_to', 'in', ('out_invoice', 'in_invoice'))]",
                                      help="The type the invoices or bills of these orders take.")
    sequence_id = fields.Many2one("ir.sequence", string="Number series", tracking=True,
                                  help="Orders of this type are numbered from this series.")
    journal_id = fields.Many2one("account.journal", string="Journal", tracking=True, check_company=False,
                                 help="Invoices or bills of this type post in this journal.")
    product_category_ids = fields.Many2many("product.category", string="Allowed product categories",
                                            help="Empty: any category.")
    currency_id = fields.Many2one("res.currency", default=lambda s: s.env.company.currency_id, required=True)
    approval_limit = fields.Monetary(string="Approval limit", tracking=True, aggregator=None,
                                     help="Orders above this amount before tax wait for an approver.")
    approver_group_ids = fields.Many2many("res.groups", "tms_doc_type_approver_rel", "type_id", "group_id",
                                          string="Approvers",
                                          default=lambda s: s.env.ref("tms_core.group_branch_manager"))
    group_ids = fields.Many2many("res.groups", "tms_doc_type_group_rel", "type_id", "group_id",
                                 string="Groups", help="Who may choose this type. Empty: every user.")
    vehicle_disposal = fields.Boolean(string="Vehicle sale",
                                      help="The order names the owned vehicle it sells; the Fleet Manager and the "
                                           "Finance Head approve; delivery moves the vehicle to Sold.")
    is_amc = fields.Boolean(string="Annual maintenance contract",
                            help="A blanket order for office-asset maintenance, billed by period.")
    note = fields.Text()

    _sql_constraints = [("code_uniq", "unique(code)", "A document type code is used once.")]

    @api.depends("name", "code")
    def _compute_display_name(self):
        for dtype in self:
            dtype.display_name = "%s (%s)" % (dtype.name, dtype.code) if dtype.code else dtype.name

    @api.constrains("is_default", "applies_to", "behaviour", "active")
    def _check_default(self):
        for dtype in self.filtered(lambda t: t.is_default and t.active):
            if dtype.behaviour != "transport":
                raise ValidationError(_("The default type is a Transport type, so that the transport rules apply "
                                        "unless a user chooses otherwise."))
            if self.search_count([("applies_to", "=", dtype.applies_to), ("is_default", "=", True),
                                  ("id", "!=", dtype.id)]):
                raise ValidationError(_("%s already has a default type.",
                                        dict(APPLIES_TO)[dtype.applies_to]))

    @api.constrains("invoice_type_id", "applies_to")
    def _check_invoice_type(self):
        for dtype in self.filtered("invoice_type_id"):
            if INVOICE_OF.get(dtype.applies_to) != dtype.invoice_type_id.applies_to:
                raise ValidationError(_("The invoice or bill type of %(t)s must apply to %(a)s.", t=dtype.name,
                                        a=dict(APPLIES_TO).get(INVOICE_OF.get(dtype.applies_to), "")))

    @api.model
    def _tms_default_for(self, applies_to, transport=False):
        """BR-DOC-03: the user's personal default, else the type marked default (a Transport type). Documents the
        transport flows create always take the Transport type."""
        if not applies_to:
            return self.browse()
        # personal defaults are read once the registry is loaded (not while the module installs)
        if not transport and not self.env.context.get("tms_transport_flow") and self.pool.ready:
            personal = self.env.user.tms_document_type_ids.filtered(
                lambda t: t.applies_to == applies_to and t.active)[:1]
            if personal:
                return personal
        return self.sudo().search([("applies_to", "=", applies_to), ("is_default", "=", True)], limit=1) or \
            self.sudo().search([("applies_to", "=", applies_to), ("behaviour", "=", "transport")], limit=1)

    def _tms_allowed(self, user=None):
        """BR-DOC-14: a type with groups is offered only to their members."""
        user = user or self.env.user
        if user._is_superuser() or user._is_system():
            return self
        user_groups = user.groups_id
        return self.filtered(lambda t: not t.group_ids or t.group_ids & user_groups)

    def _tms_user_approves(self, user=None):
        self.ensure_one()
        user = user or self.env.user
        groups = self.approver_group_ids or self.env.ref("tms_core.group_branch_manager")
        return bool(groups & user.groups_id)

    def _tms_next_number(self):
        self.ensure_one()
        return self.sudo().sequence_id.next_by_id() if self.sequence_id else False


class TmsDocumentTypeGuide(models.TransientModel):
    """What the behaviour of a document type decides: the comparison shown from the Document Types list."""

    _name = "tms.document.type.guide"
    _description = "Document type behaviour guide"

    def action_open(self):
        return {"type": "ir.actions.act_window", "name": _("What the behaviour decides"), "res_model": self._name,
                "res_id": self.create({}).id, "view_mode": "form", "target": "new"}


class TmsDocumentTypeList(models.Model):
    _inherit = "tms.document.type"

    def action_tms_behaviour_guide(self):
        return self.env["tms.document.type.guide"].action_open()
