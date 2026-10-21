from odoo import fields, models

COST_CLASSES = [
    ("trip_direct", "Trip direct"),
    ("truck_running", "Truck running"),
    ("truck_fixed", "Truck fixed"),
    ("overhead", "Overhead"),
]


class TmsCategory(models.Model):
    """BR-INC-02, BR-CHG-02, BR-EXP-02: the shared category list of incidents, charges and expenses."""

    _name = "tms.category"
    _description = "Incident, charge and expense category"
    _order = "sequence, name"

    name = fields.Char(required=True, translate=True)
    code = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    use_incident = fields.Boolean(string="Incidents")
    use_charge = fields.Boolean(string="Charges")
    use_expense = fields.Boolean(string="Expenses")
    incident_moves_money = fields.Boolean(string="Incident moves money by default")
    default_cost_class = fields.Selection(COST_CLASSES, default="trip_direct")
    is_fuel = fields.Boolean()
    # BR-INV-09: GST treatment set with the tax advisor
    gst_treatment = fields.Selection(
        [
            ("gta", "Part of the GTA supply"),
            ("freight", "Taxed as freight"),
            ("pure_agent", "Pure-agent reimbursement (no GST)"),
        ],
        default="gta",
        string="GST treatment",
    )
    product_id = fields.Many2one("product.product", string="Invoice / bill product")
    account_id = fields.Many2one("account.account", string="Expense account")
    active = fields.Boolean(default=True)

    _sql_constraints = [("code_uniq", "unique(code)", "The category code must be unique.")]
