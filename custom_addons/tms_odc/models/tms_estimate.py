from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

CARRIER_KINDS = ("axle", "trailer_bed")
ROUTE_KM_TOLERANCE = 0.5


class TmsEstimatePiece(models.Model):
    """BR-ODC-02: cargo pieces with dimensions in m and weight in t."""

    _name = "tms.estimate.piece"
    _description = "Estimate cargo piece"
    _order = "estimate_id, sequence, id"

    estimate_id = fields.Many2one("tms.estimate", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="estimate_id.company_id", store=True)
    sequence = fields.Integer(default=10)
    description = fields.Char(required=True)
    count = fields.Integer(default=1, required=True)
    length = fields.Float(string="Length (m)")
    width = fields.Float(string="Width (m)")
    height = fields.Float(string="Height (m)")
    weight = fields.Float(string="Weight per piece (t)")
    is_odc = fields.Boolean(string="ODC", compute="_compute_is_odc", store=True)
    odc_reason = fields.Char(string="Above threshold", compute="_compute_is_odc", store=True)

    @api.depends("length", "width", "height", "weight", "estimate_id.company_id")
    def _compute_is_odc(self):
        for piece in self:
            company = piece.estimate_id.company_id or self.env.company
            over = company._tms_odc_exceeded(piece.length, piece.width, piece.height, piece.weight)
            piece.is_odc = bool(over)
            piece.odc_reason = ", ".join(over)

    @api.constrains("count", "length", "width", "height", "weight")
    def _check_values(self):
        for piece in self:
            if piece.count < 1 or min(piece.length, piece.width, piece.height, piece.weight) < 0:
                raise ValidationError(_("A piece has a count of at least one and no negative dimension."))

    @api.model_create_multi
    def create(self, vals_list):
        self.env["tms.estimate"].browse([v.get("estimate_id") for v in vals_list])._tms_check_editable()
        return super().create(vals_list)

    def write(self, vals):
        self.mapped("estimate_id")._tms_check_editable()
        return super().write(vals)

    def unlink(self):
        self.mapped("estimate_id")._tms_check_editable()
        return super().unlink()


class TmsEstimateRoute(models.Model):
    """BR-ODC-03: the states crossed with their km; their count drives per-state lines."""

    _name = "tms.estimate.route"
    _description = "Estimate route by state"
    _order = "estimate_id, sequence, id"

    estimate_id = fields.Many2one("tms.estimate", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="estimate_id.company_id", store=True)
    sequence = fields.Integer(default=10)
    state_id = fields.Many2one("res.country.state", string="State", required=True)
    km = fields.Float(string="km in state")
    permit_authority = fields.Char()

    _sql_constraints = [("state_uniq", "unique(estimate_id, state_id)", "A state appears once on a route.")]

    @api.model_create_multi
    def create(self, vals_list):
        estimates = self.env["tms.estimate"].browse([v.get("estimate_id") for v in vals_list])
        estimates._tms_check_editable()
        routes = super().create(vals_list)
        routes.mapped("estimate_id")._tms_sync_state_count()
        return routes

    def write(self, vals):
        self.mapped("estimate_id")._tms_check_editable()
        res = super().write(vals)
        self.mapped("estimate_id")._tms_sync_state_count()
        return res

    def unlink(self):
        estimates = self.mapped("estimate_id")
        estimates._tms_check_editable()
        res = super().unlink()
        estimates.exists()._tms_sync_state_count()
        return res


class TmsEstimate(models.Model):
    _inherit = "tms.estimate"

    piece_ids = fields.One2many("tms.estimate.piece", "estimate_id", string="Cargo pieces", copy=True)
    route_ids = fields.One2many("tms.estimate.route", "estimate_id", string="Route by state", copy=True)
    odc_suggested = fields.Boolean(compute="_compute_odc", string="ODC suggested",
                                   help="A piece exceeds an ODC threshold (BR-ODC-02).")
    route_km = fields.Float(compute="_compute_odc", string="Route km")
    heaviest_piece = fields.Float(compute="_compute_odc", string="Heaviest piece (t)")
    rated_capacity = fields.Float(compute="_compute_odc", string="Axle / bed capacity (t)")
    odc_problems = fields.Text(compute="_compute_odc")

    @api.depends("piece_ids.is_odc", "piece_ids.weight", "route_ids.km", "distance_km", "tms_cargo_type",
                 "line_ids.product_id", "line_ids.units")
    def _compute_odc(self):
        for est in self:
            est.odc_suggested = any(est.piece_ids.mapped("is_odc"))
            est.route_km = sum(est.route_ids.mapped("km"))
            est.heaviest_piece = max(est.piece_ids.mapped("weight") or [0.0])
            est.rated_capacity = est._tms_rated_capacity()
            est.odc_problems = "\n".join(est._tms_odc_problems()) if est.tms_cargo_type == "odc" else False

    def _tms_rated_capacity(self):
        self.ensure_one()
        return sum(line.units * line.product_id.tms_rated_capacity for line in self.line_ids
                   if line.product_id.tms_equipment_kind in CARRIER_KINDS)

    def _tms_odc_problems(self):
        """BR-ODC-03: a Puller line, axles or beds carrying the heaviest piece, and the route by state."""
        self.ensure_one()
        problems = []
        kinds = set(self.line_ids.mapped("product_id.tms_equipment_kind"))
        if not self.piece_ids:
            problems.append(_("Record the cargo pieces."))
        if "puller" not in kinds:
            problems.append(_("Add a Puller line."))
        if not kinds & set(CARRIER_KINDS):
            problems.append(_("Add an Axle line or Trailer bed line."))
        elif self._tms_rated_capacity() < self.heaviest_piece:
            problems.append(_("The axles' rated capacity %(c)s t does not carry the heaviest piece of %(w)s t.",
                              c=self._tms_rated_capacity(), w=self.heaviest_piece))
        if not self.route_ids:
            problems.append(_("Record the route by state."))
        elif abs(sum(self.route_ids.mapped("km")) - self.distance_km) > ROUTE_KM_TOLERANCE:
            problems.append(_("The state km add up to %(r)s, not the total distance of %(d)s km.",
                              r=sum(self.route_ids.mapped("km")), d=self.distance_km))
        return problems

    def _tms_check_odc(self):
        for est in self.filtered(lambda e: e.tms_cargo_type == "odc" and not e.is_contract):
            problems = est._tms_odc_problems()
            if problems:
                raise UserError(_("ODC estimate %(e)s is incomplete (BR-ODC-03):\n%(p)s", e=est.name,
                                  p="\n".join(problems)))

    def _tms_sync_state_count(self):
        """The route's state count drives the per-state lines (#13 Route)."""
        for est in self:
            if est.route_ids and est.state_count != len(est.route_ids):
                est.write({"state_count": len(est.route_ids)})

    def _tms_apply_odc_speed(self):
        for est in self.filtered(lambda e: e.state == "draft" and e.tms_cargo_type == "odc"):
            speed = est.company_id.tms_odc_km_per_day
            if speed and est.km_per_day != speed:
                est.write({"km_per_day": speed})

    @api.model_create_multi
    def create(self, vals_list):
        estimates = super().create(vals_list)
        estimates._tms_apply_odc_speed()  # BR-CFG-13: ODC estimates assume the ODC km per day
        return estimates

    def action_lock(self):
        self._tms_check_odc()
        return super().action_lock()


class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    tms_odc_suggested = fields.Boolean(related="tms_estimate_id.odc_suggested", string="ODC suggested")
    tms_regular_reason = fields.Char(string="Reason to keep Regular", copy=False,
                                     help="A piece exceeds an ODC threshold but the item stays Regular (BR-ODC-02).")

    @api.constrains("tms_cargo_type")
    def _check_odc_enabled(self):
        for line in self:
            if line.tms_cargo_type == "odc" and not line.order_id.company_id.tms_odc_enabled:
                raise ValidationError(_("ODC is switched off for %s (BR-CFG-13).", line.order_id.company_id.name))

    def write(self, vals):
        res = super().write(vals)
        if "tms_cargo_type" in vals:
            self.mapped("tms_estimate_id")._tms_apply_odc_speed()
        if vals.get("tms_regular_reason"):
            for line in self.filtered(lambda x: x.tms_cargo_type == "regular" and x.tms_odc_suggested):
                line._tms_log_regular()
        return res

    def _tms_log_regular(self):
        """BR-AUD-06: Cargo Type kept Regular."""
        self.ensure_one()
        pieces = self.tms_estimate_id.piece_ids.filtered("is_odc")
        self.env["tms.override.log"].log(
            "cargo_regular", self.order_id,
            _("Item %(i)s kept Regular; ODC pieces: %(p)s", i=self.tms_item_no,
              p=", ".join("%s (%s)" % (p.description, p.odc_reason) for p in pieces)),
            reason=self.tms_regular_reason)

    def _tms_check_cargo_type(self):
        for line in self.filtered("tms_estimate_id"):
            if line.tms_cargo_type == "regular" and line.tms_odc_suggested and not line.tms_regular_reason:
                raise UserError(_("Item %s has a piece above an ODC threshold: make it ODC or give a reason to keep "
                                  "it Regular (BR-ODC-02).", line.tms_item_no))
            if line.tms_cargo_type == "odc":
                line.tms_estimate_id._tms_check_odc()


class SaleOrder(models.Model):
    _inherit = "sale.order"

    def _tms_check_items_before_send(self):
        for order in self:
            order.order_line._tms_check_cargo_type()
        return super()._tms_check_items_before_send()
