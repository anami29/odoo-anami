from odoo import _, api, fields, models, tools

# ratio columns: on a group row each is recomputed from the group's sums, never averaged
SO_PNL_RATIOS = {
    "margin_pct": ("contribution", "revenue"),
    "planned_margin_pct": ("planned_contribution", "planned_revenue"),
    "variance_pct": ("variance", "estimated_cost"),
}
SO_PNL_LINE_RATIOS = {"variance_pct": ("variance", "estimated_cost")}


def _ratio_read_group(parent, ratios, domain, fields, groupby, **kw):
    """Run read_group with the sums behind each requested ratio column, then set the ratio of every group
    to sum(numerator) ÷ sum(denominator) × 100 instead of the average of its rows."""
    wanted = {name: ratios[name] for name in {spec.split(":")[0] for spec in fields} if name in ratios}
    sums = sorted({fname for pair in wanted.values() for fname in pair})
    specs = list(fields) + ["tms_ratio_%s:sum(%s)" % (fname, fname) for fname in sums]
    groups = parent.read_group(domain, specs, groupby, **kw)
    for group in groups:
        for name, (num, den) in wanted.items():
            numerator = group.get("tms_ratio_%s" % num) or 0.0
            denominator = group.get("tms_ratio_%s" % den) or 0.0
            group[name] = numerator / denominator * 100.0 if denominator else 0.0
        for fname in sums:
            group.pop("tms_ratio_%s" % fname, None)
    return groups


class TmsSoPnlLine(models.Model):
    """BR-ACT-07, BR-PNL-26: per estimate line – estimated, committed and actual cost, variance."""

    _name = "tms.so.pnl.line"
    _description = "Sales order P&L by estimate line"
    _auto = False
    _order = "order_id, item_no, line_no"

    estimate_line_id = fields.Many2one("tms.estimate.line", readonly=True)
    estimate_id = fields.Many2one("tms.estimate", readonly=True)
    sale_line_id = fields.Many2one("sale.order.line", string="Order item", readonly=True)
    order_id = fields.Many2one("sale.order", readonly=True)
    partner_id = fields.Many2one("res.partner", string="Customer", readonly=True)
    company_id = fields.Many2one("res.company", string="Branch", readonly=True)
    item_no = fields.Integer(readonly=True)
    line_no = fields.Integer(readonly=True)
    product_id = fields.Many2one("product.product", string="Cost item", readonly=True)
    estimated_cost = fields.Float(readonly=True)
    committed = fields.Float(readonly=True)
    actual = fields.Float(readonly=True)
    variance = fields.Float(readonly=True)
    variance_pct = fields.Float(string="Variance %", readonly=True, aggregator="avg")

    @api.model
    def read_group(self, domain, fields, groupby, offset=0, limit=None, orderby=False, lazy=True):
        return _ratio_read_group(super(), SO_PNL_LINE_RATIOS, domain, fields, groupby, offset=offset,
                                 limit=limit, orderby=orderby, lazy=lazy)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute("""
            CREATE OR REPLACE VIEW %s AS (
            WITH act AS (
                SELECT aml.tms_estimate_line_id AS eid, SUM(aml.balance) AS amount
                  FROM account_move_line aml
                 WHERE aml.parent_state = 'posted' AND aml.tms_estimate_line_id IS NOT NULL
                   AND aml.display_type = 'product'
              GROUP BY aml.tms_estimate_line_id
            ), com AS (
                SELECT pol.tms_estimate_line_id AS eid,
                       SUM((pol.product_qty - pol.qty_invoiced) * pol.price_unit) AS amount
                  FROM purchase_order_line pol
                  JOIN purchase_order po ON po.id = pol.order_id
                 WHERE po.state IN ('purchase', 'done') AND pol.tms_estimate_line_id IS NOT NULL
              GROUP BY pol.tms_estimate_line_id
            )
            SELECT el.id AS id, el.id AS estimate_line_id, el.estimate_id, el.sale_line_id, el.order_id,
                   el.partner_id, el.company_id, sol.tms_item_no AS item_no, el.line_no, el.product_id,
                   el.estimated_cost, COALESCE(com.amount, 0) AS committed, COALESCE(act.amount, 0) AS actual,
                   COALESCE(act.amount, 0) - el.estimated_cost AS variance,
                   CASE WHEN el.estimated_cost <> 0
                        THEN (COALESCE(act.amount, 0) - el.estimated_cost) / el.estimated_cost * 100 ELSE 0 END
                       AS variance_pct
              FROM tms_estimate_line el
              JOIN sale_order_line sol ON sol.id = el.sale_line_id
         LEFT JOIN act ON act.eid = el.id
         LEFT JOIN com ON com.eid = el.id
            )""" % self._table)

    def action_open_documents(self):
        self.ensure_one()
        return self.estimate_line_id.action_view_documents()


class TmsSoPnl(models.Model):
    """BR-ACT-07, BR-PNL-26: per order item – revenue, estimated, committed and actual cost,
    standard charge of owned trucks, contribution, margin and variance."""

    _name = "tms.so.pnl"
    _description = "Sales order P&L by item"
    _auto = False
    _order = "order_id, item_no"

    sale_line_id = fields.Many2one("sale.order.line", string="Order item", readonly=True)
    order_id = fields.Many2one("sale.order", readonly=True)
    partner_id = fields.Many2one("res.partner", string="Customer", readonly=True)
    company_id = fields.Many2one("res.company", string="Branch", readonly=True)
    item_no = fields.Integer(readonly=True)
    tms_cargo_type = fields.Selection([("regular", "Regular"), ("odc", "ODC")], string="Cargo Type", readonly=True)
    planned_revenue = fields.Float(readonly=True)
    revenue = fields.Float(readonly=True)
    estimated_cost = fields.Float(readonly=True)
    committed = fields.Float(readonly=True)
    actual = fields.Float(readonly=True)
    standard_charge = fields.Float(readonly=True)
    contribution = fields.Float(readonly=True)
    margin_pct = fields.Float(string="Margin %", readonly=True, aggregator="avg",
                              help="Contribution ÷ revenue; on a group row, the group's contribution ÷ its revenue.")
    planned_contribution = fields.Float(readonly=True, help="Planned revenue less the estimated cost.")
    planned_margin_pct = fields.Float(string="Planned margin %", readonly=True, aggregator="avg",
                                      help="Planned contribution ÷ planned revenue.")
    variance = fields.Float(readonly=True)
    variance_pct = fields.Float(string="Cost variance %", readonly=True, aggregator="avg",
                                help="Actual less estimated cost, as a share of the estimated cost.")
    date_order = fields.Datetime(readonly=True)

    @api.model
    def read_group(self, domain, fields, groupby, offset=0, limit=None, orderby=False, lazy=True):
        return _ratio_read_group(super(), SO_PNL_RATIOS, domain, fields, groupby, offset=offset, limit=limit,
                                 orderby=orderby, lazy=lazy)

    def action_open_estimate_lines(self):
        """The order's estimate lines with their committed and actual cost."""
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id("tms_estimate.action_tms_so_pnl_line")
        action.update({
            "name": _("Estimate against actual · %s", self.order_id.name),
            "domain": [("order_id", "=", self.order_id.id)],
        })
        return action

    @api.model
    def _tms_standard_charge_sql(self):
        """Hook: tms_profitability returns the standard charge per order item."""
        return "SELECT NULL::integer AS sale_line_id, 0::numeric AS amount WHERE FALSE"

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute("""
            CREATE OR REPLACE VIEW %(table)s AS (
            WITH rev AS (
                SELECT COALESCE(aml.tms_sale_line_id, chg.sale_line_id) AS sale_line_id, SUM(-aml.balance) AS amount
                  FROM account_move_line aml
             LEFT JOIN tms_trip_charge chg ON chg.id = aml.tms_charge_id
                 WHERE aml.parent_state = 'posted' AND aml.display_type = 'product'
                   AND aml.tms_line_kind IN ('freight', 'charge', 'rate_correction', 'supplementary')
                   AND COALESCE(aml.tms_sale_line_id, chg.sale_line_id) IS NOT NULL
              GROUP BY 1
            ), cost AS (
                SELECT p.sale_line_id, SUM(p.estimated_cost) AS est, SUM(p.committed) AS com, SUM(p.actual) AS act
                  FROM tms_so_pnl_line p GROUP BY p.sale_line_id
            ), std AS (
                SELECT s.sale_line_id, SUM(s.amount) AS amount FROM (%(std)s) s GROUP BY s.sale_line_id
            )
            SELECT sol.id AS id, sol.id AS sale_line_id, so.id AS order_id, so.partner_id, so.company_id,
                   so.date_order, sol.tms_item_no AS item_no, sol.tms_cargo_type,
                   sol.price_subtotal AS planned_revenue,
                   COALESCE(rev.amount, 0) AS revenue,
                   COALESCE(cost.est, 0) AS estimated_cost, COALESCE(cost.com, 0) AS committed,
                   COALESCE(cost.act, 0) AS actual, COALESCE(std.amount, 0) AS standard_charge,
                   COALESCE(rev.amount, 0) - COALESCE(cost.act, 0) - COALESCE(std.amount, 0) AS contribution,
                   CASE WHEN COALESCE(rev.amount, 0) <> 0
                        THEN (COALESCE(rev.amount, 0) - COALESCE(cost.act, 0) - COALESCE(std.amount, 0))
                             / rev.amount * 100 ELSE 0 END AS margin_pct,
                   sol.price_subtotal - COALESCE(cost.est, 0) AS planned_contribution,
                   CASE WHEN COALESCE(sol.price_subtotal, 0) <> 0
                        THEN (sol.price_subtotal - COALESCE(cost.est, 0)) / sol.price_subtotal * 100
                        ELSE 0 END AS planned_margin_pct,
                   COALESCE(cost.act, 0) - COALESCE(cost.est, 0) AS variance,
                   CASE WHEN COALESCE(cost.est, 0) <> 0
                        THEN (COALESCE(cost.act, 0) - cost.est) / cost.est * 100 ELSE 0 END AS variance_pct
              FROM sale_order_line sol
              JOIN sale_order so ON so.id = sol.order_id
         LEFT JOIN rev ON rev.sale_line_id = sol.id
         LEFT JOIN cost ON cost.sale_line_id = sol.id
         LEFT JOIN std ON std.sale_line_id = sol.id
             WHERE so.state = 'sale' AND (sol.tms_is_movement OR sol.tms_charge_id IS NOT NULL)
            )""" % {"table": self._table, "std": self._tms_standard_charge_sql()})


class TmsKpi(models.Model):
    _inherit = "tms.kpi"

    def _kpi_kpi_16(self):
        """Quotation win rate: confirmed as a share of quotations sent."""
        orders = self._kpi_search("sale.order", self._branches() + [
            ("state", "in", ("sent", "sale", "cancel")), ("create_date", ">=", self._period_start())])
        won = orders.filtered(lambda o: o.state == "sale")
        rate = (len(won) / len(orders) * 100.0) if orders else 0.0
        return len(won), rate, "sale.order", [("id", "in", orders.ids)]

    def _kpi_kpi_17(self):
        """Orders whose actual cost exceeds estimate by more than the variance tolerance."""
        rows = self._kpi_search("tms.so.pnl", self._branches())
        bad = rows.filtered(lambda r: r.estimated_cost and r.actual > r.estimated_cost * (
            1 + r.company_id.tms_estimate_variance_tolerance / 100.0))
        return len(bad.mapped("order_id")), sum(bad.mapped("variance")), "tms.so.pnl", [("id", "in", bad.ids)]
