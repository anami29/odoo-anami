from odoo import api, fields, models

COST_BASES = [
    ("unit", "Per unit"),
    ("km", "Per km"),
    ("unit_km", "Per unit-km"),
    ("day", "Per day"),
    ("unit_day", "Per unit-day"),
    ("state", "Per state"),
    ("lump", "Lump sum"),
]
EQUIPMENT_KINDS = [
    ("puller", "Puller"),
    ("axle", "Axle line"),
    ("trailer_bed", "Trailer bed"),
    ("escort", "Escort"),
    ("vehicle", "Vehicle"),
    ("other", "Other"),
]


class TmsCostCategory(models.Model):
    """BR-MD-13."""

    _name = "tms.cost.category"
    _description = "Cost category"
    _order = "sequence, name"

    name = fields.Char(required=True, translate=True)
    code = fields.Char()
    sequence = fields.Integer(default=10)
    default_basis = fields.Selection(COST_BASES, default="lump", required=True)
    default_markup = fields.Float(string="Default markup %")
    incidental = fields.Boolean(help="Billed at actual on a contract (BR-CON-07).")
    charge_category_id = fields.Many2one(
        "tms.category", string="Charge category", domain=[("use_charge", "=", True)],
        help="The charge category an incidental is billed under at actual.",
    )
    is_misc = fields.Boolean(string="Misc. and contingency")
    active = fields.Boolean(default=True)


class ProductTemplate(models.Model):
    """BR-MD-03: cost items are service products flagged tms_cost_item."""

    _inherit = "product.template"

    tms_cost_item = fields.Boolean(string="TMS cost item")
    tms_cost_category_id = fields.Many2one("tms.cost.category", string="Cost category")
    tms_cost_basis = fields.Selection(COST_BASES, string="Pricing basis", compute="_compute_tms_cost_basis",
                                      store=True, readonly=False)
    tms_default_markup = fields.Float(string="Default markup %", compute="_compute_tms_default_markup",
                                      store=True, readonly=False)
    tms_rated_capacity = fields.Float(string="Rated capacity per unit (t)",
                                      help="For axles and trailer beds (BR-ODC-03).")
    tms_equipment_kind = fields.Selection(EQUIPMENT_KINDS, string="Equipment kind")
    tms_owned_truck = fields.Boolean(string="Owned-truck running",
                                     help="Priced at the standard rate per km in force on the estimate date "
                                          "(BR-EST-03, BR-PNL-15).")
    tms_vehicle_class_id = fields.Many2one("tms.vehicle.class", string="Vehicle class")

    @api.depends("tms_cost_category_id")
    def _compute_tms_cost_basis(self):
        for tmpl in self:
            if tmpl.tms_cost_category_id and not tmpl.tms_cost_basis:
                tmpl.tms_cost_basis = tmpl.tms_cost_category_id.default_basis
            elif not tmpl.tms_cost_basis:
                tmpl.tms_cost_basis = "lump"

    @api.depends("tms_cost_category_id")
    def _compute_tms_default_markup(self):
        for tmpl in self:
            if tmpl.tms_cost_category_id and not tmpl.tms_default_markup:
                tmpl.tms_default_markup = tmpl.tms_cost_category_id.default_markup
            elif not tmpl.tms_default_markup:
                tmpl.tms_default_markup = 0.0


class ResCompany(models.Model):
    _inherit = "res.company"

    # BR-CFG-12
    tms_markup_floor = fields.Float(string="Markup floor %", default=10.0)
    tms_value_limit = fields.Monetary(string="Item value limit", currency_field="currency_id", default=1000000.0)
    tms_estimate_variance_tolerance = fields.Float(string="Estimate variance tolerance %", default=5.0)
    tms_quotation_validity_days = fields.Integer(string="Quotation validity (days)", default=15)
    tms_cost_pricelist_id = fields.Many2one("product.pricelist", string="Cost pricelist",
                                            help="Cost of owned equipment and internal items (BR-MD-14).")
    tms_default_km_per_day = fields.Float(string="Planned km per day", default=300.0)


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    tms_item_increment = fields.Integer(related="company_id.tms_item_increment", readonly=False)
    tms_markup_floor = fields.Float(related="company_id.tms_markup_floor", readonly=False)
    tms_value_limit = fields.Monetary(related="company_id.tms_value_limit", readonly=False)
    tms_estimate_variance_tolerance = fields.Float(related="company_id.tms_estimate_variance_tolerance",
                                                   readonly=False)
    tms_quotation_validity_days = fields.Integer(related="company_id.tms_quotation_validity_days", readonly=False)
    tms_cost_pricelist_id = fields.Many2one(related="company_id.tms_cost_pricelist_id", readonly=False)
    tms_default_km_per_day = fields.Float(related="company_id.tms_default_km_per_day", readonly=False)
