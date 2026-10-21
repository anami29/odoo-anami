"""BR-PNL-26..34, #18.4: the P&L views, SQL views over posted move lines and standard-charge analytic lines.

Amounts in the ledger are signed by their effect on profit: revenue and recoveries positive, costs negative.
The views show costs as positive figures."""

from odoo import _, api, fields, models, tools

from .analytic import POOLS

KINDS = [
    ("revenue", "Revenue"),
    ("accrued_revenue", "Accrued revenue"),
    ("direct", "Trip direct cost"),
    ("accrued_cost", "Accrued trip cost"),
    ("recovery", "Recovery"),
    ("std_charge", "Standard charge (trip)"),
    ("std_recovered", "Standard charge recovered (truck pool)"),
    ("truck_running", "Truck running cost"),
    ("truck_fixed", "Truck fixed cost"),
    ("overhead", "Branch overhead"),
    ("head_office", "Head-office cost"),
]
FLEET_TYPES = [("owned", "Owned"), ("market", "Market")]
CARGO_TYPES = [("regular", "Regular"), ("odc", "ODC")]

LEDGER_COLS = ["id", "kind", "amount", "date", "company_id", "trip_id", "consignment_id", "sale_line_id",
               "lr_sale_line_id", "partner_id", "vendor_id", "vehicle_id", "vehicle_class_id", "fleet_type",
               "cargo_type", "cost_pool", "is_replacement", "move_line_id", "analytic_line_id", "account_id",
               "line_kind"]


LEDGER_TYPES = {"id": "bigint", "amount": "numeric", "date": "date", "is_replacement": "boolean", "kind": "varchar",
                "fleet_type": "varchar", "cargo_type": "varchar", "cost_pool": "varchar", "line_kind": "varchar"}


def _select(exprs, rest):
    return "SELECT %s %s" % (", ".join("(%s)::%s AS %s" % (exprs.get(col, "NULL"), LEDGER_TYPES.get(col, "integer"), col)
                                       for col in LEDGER_COLS), rest)


def _ledger_sql():
    trip_date = "COALESCE(b.tms_pnl_late_date, t.tms_pnl_date)"
    revenue = _select({
        "id": "b.id::bigint * 1000000 + COALESCE(l.id, 0)",
        "kind": "'revenue'",
        "amount": "-b.balance * COALESCE(l.share, 1.0)",
        "date": "CASE WHEN t.id IS NULL THEN b.date ELSE %s END" % trip_date,
        "company_id": "b.company_id",
        "trip_id": "t.id",
        "consignment_id": "b.tms_consignment_id",
        "sale_line_id": "COALESCE(b.tms_sale_line_id, lr.sale_line_id)",
        "lr_sale_line_id": "lr.sale_line_id",
        "partner_id": "b.move_partner_id",
        "vehicle_id": "t.primary_vehicle_id",
        "vehicle_class_id": "t.class_id",
        "fleet_type": "t.fleet_type",
        "cargo_type": "COALESCE(t.tms_cargo_type, lr.tms_cargo_type)",
        "is_replacement": "FALSE",
        "move_line_id": "b.id",
        "account_id": "b.account_id",
        "line_kind": "b.tms_line_kind",
    }, """FROM base b
          JOIN tms_consignment lr ON lr.id = b.tms_consignment_id
     LEFT JOIN link l ON l.consignment_id = b.tms_consignment_id
                     AND (b.tms_trip_id IS NULL OR l.trip_id = b.tms_trip_id)
     LEFT JOIN trip t ON t.id = COALESCE(l.trip_id, b.tms_trip_id)
         WHERE b.move_type IN ('out_invoice', 'out_refund')
           AND b.tms_line_kind IN ('freight', 'rate_correction', 'charge', 'supplementary', 'dead_freight',
                                   'deduction')""")
    accrual = _select({
        "id": "b.id::bigint * 1000000",
        "kind": "CASE WHEN b.account_type IN ('income', 'income_other') THEN 'accrued_revenue' "
                "ELSE 'accrued_cost' END",
        "amount": "-b.balance",
        "date": "b.date",
        "company_id": "CASE WHEN b.account_type IN ('income', 'income_other') THEN b.company_id "
                      "ELSE t.company_id END",
        "trip_id": "t.id",
        "consignment_id": "b.tms_consignment_id",
        "sale_line_id": "COALESCE(b.tms_sale_line_id, lr.sale_line_id)",
        "lr_sale_line_id": "lr.sale_line_id",
        "partner_id": "CASE WHEN b.account_type IN ('income', 'income_other') THEN bp.commercial_partner_id END",
        "vehicle_id": "t.primary_vehicle_id",
        "vehicle_class_id": "t.class_id",
        "fleet_type": "t.fleet_type",
        "cargo_type": "t.tms_cargo_type",
        "is_replacement": "FALSE",
        "move_line_id": "b.id",
        "account_id": "b.account_id",
        "line_kind": "b.tms_line_kind",
    }, """FROM base b
          JOIN trip t ON t.id = b.tms_trip_id
     LEFT JOIN tms_consignment lr ON lr.id = b.tms_consignment_id
     LEFT JOIN res_partner bp ON bp.id = lr.billing_partner_id
         WHERE b.tms_line_kind = 'accrual'
           AND b.account_type IN ('income', 'income_other', 'expense', 'expense_direct_cost')""")
    direct = _select({
        "id": "b.id::bigint * 1000000",
        "kind": "'direct'",
        "amount": "-b.balance",
        "date": trip_date,
        "company_id": "t.company_id",
        "trip_id": "t.id",
        "consignment_id": "b.tms_consignment_id",
        "vendor_id": "CASE WHEN b.move_type IN ('in_invoice', 'in_refund') THEN b.move_partner_id END",
        "vehicle_id": "t.primary_vehicle_id",
        "vehicle_class_id": "t.class_id",
        "fleet_type": "t.fleet_type",
        "cargo_type": "t.tms_cargo_type",
        "is_replacement": "b.tms_replacement_vehicle_id IS NOT NULL",
        "move_line_id": "b.id",
        "account_id": "b.account_id",
        "line_kind": "b.tms_line_kind",
    }, """FROM base b
          JOIN trip t ON t.id = b.tms_trip_id
         WHERE COALESCE(b.tms_line_kind, '') NOT IN ('accrual', 'recovery')
           AND NOT COALESCE(b.tms_overhead, FALSE)
           AND COALESCE(b.tms_cost_class, 'trip_direct') = 'trip_direct'
           AND (b.tms_line_kind = 'claim'
                OR (b.move_type IN ('in_invoice', 'in_refund', 'entry')
                    AND b.account_type IN ('expense', 'expense_direct_cost')))""")
    recovery = _select({
        "id": "b.id::bigint * 1000000",
        "kind": "'recovery'",
        "amount": "-b.balance",
        "date": trip_date,
        "company_id": "t.company_id",
        "trip_id": "t.id",
        "consignment_id": "b.tms_consignment_id",
        "vendor_id": "CASE WHEN b.move_type IN ('in_invoice', 'in_refund') THEN b.move_partner_id END",
        "vehicle_id": "t.primary_vehicle_id",
        "vehicle_class_id": "t.class_id",
        "fleet_type": "t.fleet_type",
        "cargo_type": "t.tms_cargo_type",
        "is_replacement": "FALSE",
        "move_line_id": "b.id",
        "account_id": "b.account_id",
        "line_kind": "b.tms_line_kind",
    }, """FROM base b
          JOIN trip t ON t.id = b.tms_trip_id
         WHERE b.tms_line_kind = 'recovery'""")
    std = _select({
        "id": "-aal.id::bigint",
        "kind": "CASE aal.tms_std_side WHEN 'trip' THEN 'std_charge' ELSE 'std_recovered' END",
        "amount": "aal.amount",
        "date": "aal.date",
        "company_id": "aal.company_id",
        "trip_id": "aal.tms_trip_id",
        "vehicle_id": "aal.tms_vehicle_id",
        "vehicle_class_id": "v.tms_vehicle_class_id",
        "fleet_type": "v.tms_ownership",
        "cargo_type": "t.tms_cargo_type",
        "cost_pool": "aal.tms_std_pool",
        "is_replacement": "FALSE",
        "analytic_line_id": "aal.id",
    }, """FROM account_analytic_line aal
     LEFT JOIN fleet_vehicle v ON v.id = aal.tms_vehicle_id
     LEFT JOIN trip t ON t.id = aal.tms_trip_id
         WHERE aal.tms_std_side IS NOT NULL""")
    truck = _select({
        "id": "b.id::bigint * 1000000",
        "kind": "b.tms_cost_class",
        "amount": "-b.balance",
        "date": "b.date",
        "company_id": "COALESCE(v.tms_home_branch_id, b.company_id)",
        "vendor_id": "CASE WHEN b.move_type IN ('in_invoice', 'in_refund') THEN b.move_partner_id END",
        "vehicle_id": "b.vehicle_id",
        "vehicle_class_id": "v.tms_vehicle_class_id",
        "fleet_type": "v.tms_ownership",
        "cost_pool": "CASE b.tms_cost_class WHEN 'truck_running' THEN 'running' ELSE 'fixed' END",
        "is_replacement": "FALSE",
        "move_line_id": "b.id",
        "account_id": "b.account_id",
        "line_kind": "b.tms_line_kind",
    }, """FROM base b
     LEFT JOIN fleet_vehicle v ON v.id = b.vehicle_id
         WHERE b.tms_cost_class IN ('truck_running', 'truck_fixed') AND b.tms_trip_id IS NULL
           AND b.account_type IN ('expense', 'expense_direct_cost')
           AND COALESCE(b.tms_line_kind, '') != 'accrual'""")
    overhead = _select({
        "id": "b.id::bigint * 1000000",
        "kind": "CASE WHEN c.tms_head_office THEN 'head_office' ELSE 'overhead' END",
        "amount": "-b.balance",
        "date": "b.date",
        "company_id": "b.company_id",
        "vendor_id": "CASE WHEN b.move_type IN ('in_invoice', 'in_refund') THEN b.move_partner_id END",
        "is_replacement": "FALSE",
        "move_line_id": "b.id",
        "account_id": "b.account_id",
        "line_kind": "b.tms_line_kind",
    }, """FROM base b
          JOIN res_company c ON c.id = b.company_id
         WHERE (b.tms_cost_class = 'overhead' OR COALESCE(b.tms_overhead, FALSE))
           AND b.account_type IN ('expense', 'expense_direct_cost')
           AND COALESCE(b.tms_line_kind, '') != 'accrual'""")
    return """
        WITH trip AS (
            SELECT t.id, t.company_id, t.tms_pnl_date, t.primary_vehicle_id, t.fleet_type, t.tms_cargo_type,
                   pv.tms_vehicle_class_id AS class_id
              FROM tms_order t
         LEFT JOIN fleet_vehicle pv ON pv.id = t.primary_vehicle_id
        ), link AS (
            SELECT l.id, l.trip_id, l.consignment_id,
                   CASE WHEN SUM(l.weight) OVER w > 0 THEN l.weight / SUM(l.weight) OVER w
                        ELSE 1.0 / COUNT(*) OVER w END AS share
              FROM tms_trip_consignment l
             WHERE l.state != 'cancel'
            WINDOW w AS (PARTITION BY l.consignment_id)
        ), base AS (
            SELECT aml.id, aml.balance, aml.date, aml.company_id, aml.account_id, aml.tms_trip_id,
                   aml.tms_consignment_id, aml.tms_sale_line_id, aml.tms_line_kind, aml.tms_cost_class,
                   aml.tms_overhead, aml.vehicle_id, aml.tms_replacement_vehicle_id, aml.tms_pnl_late_date,
                   aa.account_type, m.move_type, m.commercial_partner_id AS move_partner_id
              FROM account_move_line aml
              JOIN account_move m ON m.id = aml.move_id
              JOIN account_account aa ON aa.id = aml.account_id
             WHERE aml.parent_state = 'posted' AND aml.display_type = 'product'
               AND aa.account_type != 'expense_depreciation' AND NOT COALESCE(aa.tms_pnl_exclude, FALSE)
        ), rows AS (
            %s
            UNION ALL %s
            UNION ALL %s
            UNION ALL %s
            UNION ALL %s
            UNION ALL %s
            UNION ALL %s
        )
        SELECT rows.*, (rows.trip_id IS NOT NULL AND rows.date IS NULL) AS wip,
               date_trunc('month', rows.date)::date AS period
          FROM rows
    """ % (revenue, accrual, direct, recovery, std, truck, overhead)


class TmsPnlLine(models.Model):
    """BR-LED-08, BR-PNL-33: the P&L ledger; every figure of every view drills to these lines."""

    _name = "tms.pnl.line"
    _description = "TMS P&L ledger line"
    _auto = False
    _order = "date desc, id"

    kind = fields.Selection(KINDS, readonly=True)
    amount = fields.Float(readonly=True, help="Signed by its effect on profit.")
    date = fields.Date(string="P&L date", readonly=True)
    period = fields.Date(readonly=True)
    company_id = fields.Many2one("res.company", string="Branch", readonly=True)
    trip_id = fields.Many2one("tms.order", string="Trip", readonly=True)
    consignment_id = fields.Many2one("tms.consignment", string="LR", readonly=True)
    sale_line_id = fields.Many2one("sale.order.line", string="Order item", readonly=True)
    lr_sale_line_id = fields.Many2one("sale.order.line", string="LR order item", readonly=True)
    partner_id = fields.Many2one("res.partner", string="Customer", readonly=True)
    vendor_id = fields.Many2one("res.partner", string="Vendor", readonly=True)
    vehicle_id = fields.Many2one("fleet.vehicle", string="Truck", readonly=True)
    vehicle_class_id = fields.Many2one("tms.vehicle.class", string="Vehicle class", readonly=True)
    fleet_type = fields.Selection(FLEET_TYPES, readonly=True)
    cargo_type = fields.Selection(CARGO_TYPES, string="Cargo Type", readonly=True)
    cost_pool = fields.Selection(POOLS, string="Pool", readonly=True)
    is_replacement = fields.Boolean(string="Replacement add-on", readonly=True)
    move_line_id = fields.Many2one("account.move.line", string="Journal item", readonly=True)
    analytic_line_id = fields.Many2one("account.analytic.line", string="Analytic entry", readonly=True)
    account_id = fields.Many2one("account.account", readonly=True)
    line_kind = fields.Selection(selection=lambda s: s.env["account.move.line"]._fields["tms_line_kind"].selection,
                                 string="TMS line kind", readonly=True)
    wip = fields.Boolean(string="Work in progress", readonly=True)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute("CREATE OR REPLACE VIEW %s AS (%s)" % (self._table, _ledger_sql()))

    def action_open_source(self):
        self.ensure_one()
        if self.move_line_id:
            return {"type": "ir.actions.act_window", "res_model": "account.move", "view_mode": "form",
                    "res_id": self.move_line_id.move_id.id}
        return {"type": "ir.actions.act_window", "res_model": "account.analytic.line", "view_mode": "form",
                "res_id": self.analytic_line_id.id}


class TmsTripLeg(models.Model):
    """BR-PNL-25: a trip moving several order items splits into legs; an LR without an item is its own leg.
    Each leg carries the revenue of its LRs and a share of the trip's direct cost, recoveries and standard
    charge by charged weight (OP-09)."""

    _name = "tms.trip.leg"
    _description = "Trip leg P&L"
    _auto = False
    _order = "trip_id, sale_line_id, consignment_id, date"

    trip_id = fields.Many2one("tms.order", string="Trip", readonly=True)
    sale_line_id = fields.Many2one("sale.order.line", string="Order item", readonly=True)
    order_id = fields.Many2one("sale.order", string="Sales order", readonly=True)
    consignment_id = fields.Many2one("tms.consignment", string="LR without an item", readonly=True)
    partner_id = fields.Many2one("res.partner", string="Customer", readonly=True)
    company_id = fields.Many2one("res.company", string="Branch", readonly=True)
    date = fields.Date(string="P&L date", readonly=True)
    period = fields.Date(readonly=True)
    wip = fields.Boolean(string="Work in progress", readonly=True)
    vehicle_id = fields.Many2one("fleet.vehicle", string="Truck", readonly=True)
    vehicle_class_id = fields.Many2one("tms.vehicle.class", string="Vehicle class", readonly=True)
    fleet_type = fields.Selection(FLEET_TYPES, readonly=True)
    cargo_type = fields.Selection(CARGO_TYPES, string="Cargo Type", readonly=True)
    weight = fields.Float(string="Charged weight", readonly=True, aggregator="max")
    share = fields.Float(string="Weight share", readonly=True, aggregator="max", digits=(16, 4))
    km = fields.Float(readonly=True)
    revenue = fields.Float(readonly=True)
    charges = fields.Float(string="Charges billed", readonly=True)
    direct_cost = fields.Float(string="Trip direct (share)", readonly=True)
    recoveries = fields.Float(readonly=True)
    claims = fields.Float(readonly=True)
    contribution = fields.Float(readonly=True)
    std_charge = fields.Float(string="Standard charge (share)", readonly=True)
    leg_margin = fields.Float(readonly=True)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute("""
            CREATE OR REPLACE VIEW %s AS (
            WITH lnk AS (
                SELECT l.trip_id, l.consignment_id, lr.sale_line_id, l.weight,
                       COALESCE(so.partner_id, bp.commercial_partner_id, bp.id) AS partner_id
                  FROM tms_trip_consignment l
                  JOIN tms_consignment lr ON lr.id = l.consignment_id
             LEFT JOIN res_partner bp ON bp.id = lr.billing_partner_id
             LEFT JOIN sale_order_line sol ON sol.id = lr.sale_line_id
             LEFT JOIN sale_order so ON so.id = sol.order_id
                 WHERE l.state != 'cancel'
            ), legs AS (
                SELECT trip_id, sale_line_id, CASE WHEN sale_line_id IS NULL THEN consignment_id END AS lr_id,
                       MIN(partner_id) AS partner_id, SUM(weight) AS weight
                  FROM lnk GROUP BY 1, 2, 3
            ), tot AS (
                SELECT trip_id, SUM(weight) AS w, COUNT(*) AS n FROM legs GROUP BY trip_id
            ), leg AS (
                SELECT legs.trip_id, legs.sale_line_id, legs.lr_id, legs.partner_id, legs.weight,
                       CASE WHEN tot.w > 0 THEN legs.weight / tot.w ELSE 1.0 / tot.n END AS share
                  FROM legs JOIN tot ON tot.trip_id = legs.trip_id
                UNION ALL
                SELECT t.id, NULL, NULL, NULL, 0.0, 1.0
                  FROM tms_order t
                 WHERE NOT EXISTS (SELECT 1 FROM lnk WHERE lnk.trip_id = t.id)
            ), rev AS (
                SELECT p.trip_id, p.lr_sale_line_id AS sale_line_id,
                       CASE WHEN p.lr_sale_line_id IS NULL THEN p.consignment_id END AS lr_id,
                       p.partner_id, p.date, p.company_id,
                       SUM(p.amount) AS revenue,
                       COALESCE(SUM(p.amount) FILTER (WHERE p.line_kind = 'charge'), 0) AS charges
                  FROM tms_pnl_line p
                 WHERE p.trip_id IS NOT NULL AND p.kind IN ('revenue', 'accrued_revenue')
              GROUP BY 1, 2, 3, 4, 5, 6
            ), cost AS (
                SELECT p.trip_id, p.date, p.company_id,
                       COALESCE(-SUM(p.amount) FILTER (WHERE p.kind IN ('direct', 'accrued_cost')), 0) AS direct,
                       COALESCE(SUM(p.amount) FILTER (WHERE p.kind = 'recovery'), 0) AS recoveries,
                       COALESCE(-SUM(p.amount) FILTER (WHERE p.kind = 'std_charge'), 0) AS std,
                       COALESCE(-SUM(p.amount) FILTER (WHERE p.line_kind = 'claim'), 0) AS claims
                  FROM tms_pnl_line p
                 WHERE p.trip_id IS NOT NULL AND p.kind IN ('direct', 'accrued_cost', 'recovery', 'std_charge')
              GROUP BY 1, 2, 3
            ), rows AS (
                SELECT r.trip_id, r.sale_line_id, r.lr_id, COALESCE(lg.partner_id, r.partner_id) AS partner_id,
                       r.date, r.company_id, r.revenue, r.charges, 0.0 AS direct, 0.0 AS recoveries, 0.0 AS std,
                       0.0 AS claims, 0.0 AS km
                  FROM rev r
             LEFT JOIN leg lg ON lg.trip_id = r.trip_id AND lg.sale_line_id IS NOT DISTINCT FROM r.sale_line_id
                             AND lg.lr_id IS NOT DISTINCT FROM r.lr_id
                UNION ALL
                SELECT c.trip_id, lg.sale_line_id, lg.lr_id, lg.partner_id, c.date, c.company_id, 0.0, 0.0,
                       c.direct * lg.share, c.recoveries * lg.share, c.std * lg.share, c.claims * lg.share, 0.0
                  FROM cost c JOIN leg lg ON lg.trip_id = c.trip_id
                UNION ALL
                SELECT t.id, lg.sale_line_id, lg.lr_id, lg.partner_id, t.tms_pnl_date, t.company_id, 0.0, 0.0,
                       0.0, 0.0, 0.0, 0.0, COALESCE(t.tms_pnl_km, 0) * lg.share
                  FROM tms_order t JOIN leg lg ON lg.trip_id = t.id
                 WHERE COALESCE(t.tms_pnl_km, 0) != 0
            ), agg AS (
                SELECT trip_id, sale_line_id, lr_id, partner_id, date, company_id,
                       SUM(revenue) AS revenue, SUM(charges) AS charges, SUM(direct) AS direct,
                       SUM(recoveries) AS recoveries, SUM(std) AS std, SUM(claims) AS claims, SUM(km) AS km
                  FROM rows GROUP BY 1, 2, 3, 4, 5, 6
            )
            SELECT ROW_NUMBER() OVER (ORDER BY a.trip_id, a.sale_line_id, a.lr_id, a.partner_id, a.date,
                                      a.company_id) AS id,
                   a.trip_id, a.sale_line_id, sol.order_id, a.lr_id AS consignment_id, a.partner_id, a.company_id,
                   a.date, date_trunc('month', a.date)::date AS period, a.date IS NULL AS wip,
                   t.primary_vehicle_id AS vehicle_id, pv.tms_vehicle_class_id AS vehicle_class_id,
                   t.fleet_type, t.tms_cargo_type AS cargo_type, lg.weight, lg.share, a.km,
                   a.revenue, a.charges, a.direct AS direct_cost, a.recoveries, a.claims,
                   a.revenue + a.recoveries - a.direct AS contribution, a.std AS std_charge,
                   a.revenue + a.recoveries - a.direct - a.std AS leg_margin
              FROM agg a
              JOIN tms_order t ON t.id = a.trip_id
         LEFT JOIN fleet_vehicle pv ON pv.id = t.primary_vehicle_id
         LEFT JOIN sale_order_line sol ON sol.id = a.sale_line_id
         LEFT JOIN leg lg ON lg.trip_id = a.trip_id AND lg.sale_line_id IS NOT DISTINCT FROM a.sale_line_id
                         AND lg.lr_id IS NOT DISTINCT FROM a.lr_id
             WHERE t.state_code IS DISTINCT FROM 'cancel'
            )""" % self._table)

    def action_open_lines(self):
        self.ensure_one()
        domain = [("trip_id", "=", self.trip_id.id)]
        return {"type": "ir.actions.act_window", "name": _("Ledger lines"), "res_model": "tms.pnl.line",
                "view_mode": "list,pivot", "domain": domain}


class TmsTripPnl(models.Model):
    """#18.4 Trip P&L (trip ledger): one row per trip; the standard charge is provisional at the START-date
    rate until the month-end close posts it."""

    _name = "tms.trip.pnl"
    _description = "Trip P&L"
    _auto = False
    _order = "date desc, trip_id desc"

    trip_id = fields.Many2one("tms.order", string="Trip", readonly=True)
    company_id = fields.Many2one("res.company", string="Branch", readonly=True)
    partner_id = fields.Many2one("res.partner", string="Customer", readonly=True)
    vendor_id = fields.Many2one("res.partner", string="Market vendor", readonly=True)
    origin_id = fields.Many2one("res.partner", string="From", readonly=True)
    destination_id = fields.Many2one("res.partner", string="To", readonly=True)
    state_code = fields.Selection(selection=lambda s: s.env["tms.order"]._fields["state_code"].selection,
                                  string="Status", readonly=True)
    date = fields.Date(string="P&L date", readonly=True)
    period = fields.Date(readonly=True)
    wip = fields.Boolean(string="Work in progress", readonly=True)
    vehicle_id = fields.Many2one("fleet.vehicle", string="Primary truck", readonly=True)
    vehicle_class_id = fields.Many2one("tms.vehicle.class", string="Vehicle class", readonly=True)
    fleet_type = fields.Selection(FLEET_TYPES, readonly=True)
    cargo_type = fields.Selection(CARGO_TYPES, string="Cargo Type", readonly=True)
    km = fields.Float(readonly=True)
    revenue = fields.Float(readonly=True)
    direct_cost = fields.Float(string="Trip direct", readonly=True)
    replacement_cost = fields.Float(string="Replacement add-on", readonly=True)
    recoveries = fields.Float(readonly=True)
    contribution = fields.Float(readonly=True)
    std_charge = fields.Float(string="Standard charge", readonly=True)
    std_provisional = fields.Boolean(string="Charge provisional", readonly=True)
    trip_margin = fields.Float(readonly=True)
    margin_pct = fields.Float(string="Margin %", readonly=True, aggregator="avg")
    open_commitments = fields.Float(readonly=True)
    unbilled_charges = fields.Float(string="Approved unbilled charges", readonly=True)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute("""
            CREATE OR REPLACE VIEW %s AS (
            WITH agg AS (
                SELECT p.trip_id,
                       COALESCE(SUM(p.amount) FILTER (WHERE p.kind IN ('revenue', 'accrued_revenue')), 0) AS revenue,
                       COALESCE(-SUM(p.amount) FILTER (WHERE p.kind IN ('direct', 'accrued_cost')), 0) AS direct,
                       COALESCE(-SUM(p.amount) FILTER (WHERE p.kind = 'direct' AND p.is_replacement), 0) AS repl,
                       COALESCE(SUM(p.amount) FILTER (WHERE p.kind = 'recovery'), 0) AS recoveries,
                       -SUM(p.amount) FILTER (WHERE p.kind = 'std_charge') AS std
                  FROM tms_pnl_line p WHERE p.trip_id IS NOT NULL
              GROUP BY p.trip_id
            ), hire AS (
                SELECT po.tms_trip_id AS trip_id,
                       SUM(GREATEST(pol.product_qty - pol.qty_invoiced, 0) * pol.price_unit) AS amount
                  FROM purchase_order_line pol JOIN purchase_order po ON po.id = pol.order_id
                 WHERE po.state IN ('purchase', 'done') AND po.tms_trip_id IS NOT NULL
              GROUP BY 1
            ), exp AS (
                SELECT trip_id, SUM(amount) AS amount FROM tms_trip_expense
                 WHERE state = 'approved' AND move_id IS NULL AND trip_id IS NOT NULL GROUP BY 1
            ), clm AS (
                SELECT trip_id, SUM(approved_amount) AS amount FROM tms_damage_claim
                 WHERE state = 'approved' AND trip_id IS NOT NULL GROUP BY 1
            ), chg AS (
                SELECT trip_id, SUM(amount) AS amount FROM tms_trip_charge
                 WHERE state = 'approved' AND recover_from = 'customer' AND move_line_id IS NULL
                   AND trip_id IS NOT NULL GROUP BY 1
            ), cust AS (
                SELECT DISTINCT ON (l.trip_id) l.trip_id, COALESCE(bp.commercial_partner_id, bp.id) AS partner_id
                  FROM tms_trip_consignment l
                  JOIN tms_consignment lr ON lr.id = l.consignment_id
             LEFT JOIN res_partner bp ON bp.id = lr.billing_partner_id
                 WHERE l.state != 'cancel'
              ORDER BY l.trip_id, l.id
            ), vend AS (
                SELECT DISTINCT ON (po.tms_trip_id) po.tms_trip_id AS trip_id, po.partner_id
                  FROM purchase_order po WHERE po.tms_trip_id IS NOT NULL AND po.state IN ('purchase', 'done')
              ORDER BY po.tms_trip_id, po.id
            ), base AS (
                SELECT t.id AS trip_id, t.company_id, cust.partner_id,
                       CASE WHEN t.fleet_type = 'market' THEN COALESCE(vend.partner_id, t.broker_id) END AS vendor_id,
                       t.origin_id, t.destination_id, t.state_code, t.tms_pnl_date AS date,
                       t.primary_vehicle_id AS vehicle_id, pv.tms_vehicle_class_id AS vehicle_class_id,
                       t.fleet_type, t.tms_cargo_type AS cargo_type, COALESCE(t.tms_pnl_km, 0) AS km,
                       COALESCE(a.revenue, 0) AS revenue, COALESCE(a.direct, 0) AS direct,
                       COALESCE(a.repl, 0) AS repl, COALESCE(a.recoveries, 0) AS recoveries,
                       CASE WHEN a.std IS NOT NULL THEN a.std
                            WHEN t.fleet_type = 'owned' THEN COALESCE(t.tms_std_km, 0) * COALESCE(r.standard_rate, 0)
                            ELSE 0 END AS std,
                       a.std IS NULL AND t.fleet_type = 'owned' AS std_provisional,
                       COALESCE(hire.amount, 0) + COALESCE(exp.amount, 0) + COALESCE(clm.amount, 0) AS commitments,
                       COALESCE(chg.amount, 0) AS unbilled_charges
                  FROM tms_order t
             LEFT JOIN fleet_vehicle pv ON pv.id = t.primary_vehicle_id
             LEFT JOIN tms_std_rate r ON r.id = t.tms_std_rate_id
             LEFT JOIN agg a ON a.trip_id = t.id
             LEFT JOIN hire ON hire.trip_id = t.id
             LEFT JOIN exp ON exp.trip_id = t.id
             LEFT JOIN clm ON clm.trip_id = t.id
             LEFT JOIN chg ON chg.trip_id = t.id
             LEFT JOIN cust ON cust.trip_id = t.id
             LEFT JOIN vend ON vend.trip_id = t.id
                 WHERE t.state_code IS DISTINCT FROM 'cancel'
            )
            SELECT trip_id AS id, trip_id, company_id, partner_id, vendor_id, origin_id, destination_id, state_code,
                   date, date_trunc('month', date)::date AS period,
                   date IS NULL AND state_code IS DISTINCT FROM 'draft' AS wip,
                   vehicle_id, vehicle_class_id, fleet_type, cargo_type, km, revenue, direct AS direct_cost,
                   repl AS replacement_cost, recoveries, revenue + recoveries - direct AS contribution,
                   std AS std_charge, std_provisional, revenue + recoveries - direct - std AS trip_margin,
                   CASE WHEN revenue != 0 THEN (revenue + recoveries - direct - std) / revenue * 100 ELSE 0 END
                       AS margin_pct,
                   commitments AS open_commitments, unbilled_charges
              FROM base
            )""" % self._table)

    def action_open_lines(self):
        self.ensure_one()
        return self.trip_id.action_tms_view_pnl_lines()


class TmsCustomerPnl(models.Model):
    """BR-PNL-27: the customer's legs and the LRs it booked without a trip, per period."""

    _name = "tms.customer.pnl"
    _description = "Customer P&L"
    _auto = False
    _order = "period desc, partner_id"

    partner_id = fields.Many2one("res.partner", string="Customer", readonly=True)
    company_id = fields.Many2one("res.company", string="Branch", readonly=True)
    period = fields.Date(readonly=True)
    vehicle_class_id = fields.Many2one("tms.vehicle.class", string="Vehicle class", readonly=True)
    fleet_type = fields.Selection(FLEET_TYPES, readonly=True)
    cargo_type = fields.Selection(CARGO_TYPES, string="Cargo Type", readonly=True)
    order_count = fields.Integer(string="Orders", readonly=True)
    trip_count = fields.Integer(string="Trips", readonly=True)
    km = fields.Float(readonly=True)
    revenue = fields.Float(readonly=True)
    direct_cost = fields.Float(string="Trip direct", readonly=True)
    recoveries = fields.Float(readonly=True)
    std_charge = fields.Float(string="Standard charge", readonly=True)
    trip_margin = fields.Float(readonly=True)
    margin_pct = fields.Float(string="Margin %", readonly=True, aggregator="avg")
    revenue_per_km = fields.Float(string="Revenue per km", readonly=True, aggregator="avg")
    charges = fields.Float(string="Charges billed", readonly=True)
    claims = fields.Float(readonly=True)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute("""
            CREATE OR REPLACE VIEW %s AS (
            WITH src AS (
                SELECT l.partner_id, l.company_id, l.period, l.vehicle_class_id, l.fleet_type, l.cargo_type,
                       l.order_id, l.trip_id, l.km, l.revenue, l.direct_cost, l.recoveries, l.std_charge,
                       l.charges, l.claims
                  FROM tms_trip_leg l WHERE l.period IS NOT NULL
                UNION ALL
                SELECT p.partner_id, p.company_id, p.period, NULL, NULL, p.cargo_type, sol.order_id, NULL, 0.0,
                       p.amount, 0.0, 0.0, 0.0,
                       CASE WHEN p.line_kind = 'charge' THEN p.amount ELSE 0.0 END, 0.0
                  FROM tms_pnl_line p
             LEFT JOIN sale_order_line sol ON sol.id = p.sale_line_id
                 WHERE p.trip_id IS NULL AND p.kind IN ('revenue', 'accrued_revenue')
            )
            SELECT ROW_NUMBER() OVER (ORDER BY partner_id, company_id, period, vehicle_class_id, fleet_type,
                                      cargo_type) AS id,
                   partner_id, company_id, period, vehicle_class_id, fleet_type, cargo_type,
                   COUNT(DISTINCT order_id) AS order_count, COUNT(DISTINCT trip_id) AS trip_count,
                   SUM(km) AS km, SUM(revenue) AS revenue, SUM(direct_cost) AS direct_cost,
                   SUM(recoveries) AS recoveries, SUM(std_charge) AS std_charge,
                   SUM(revenue + recoveries - direct_cost - std_charge) AS trip_margin,
                   CASE WHEN SUM(revenue) != 0
                        THEN SUM(revenue + recoveries - direct_cost - std_charge) / SUM(revenue) * 100 ELSE 0 END
                       AS margin_pct,
                   CASE WHEN SUM(km) != 0 THEN SUM(revenue) / SUM(km) ELSE 0 END AS revenue_per_km,
                   SUM(charges) AS charges, SUM(claims) AS claims
              FROM src
          GROUP BY partner_id, company_id, period, vehicle_class_id, fleet_type, cargo_type
            )""" % self._table)

    def action_open_lines(self):
        """BR-PNL-33: the customer's legs of the period; each leg drills on to its ledger lines."""
        self.ensure_one()
        return {"type": "ir.actions.act_window", "name": _("Legs of %s", self.partner_id.display_name),
                "res_model": "tms.trip.leg", "view_mode": "list,pivot",
                "domain": [("partner_id", "=", self.partner_id.id), ("company_id", "=", self.company_id.id),
                           ("period", "=", self.period), ("vehicle_class_id", "=", self.vehicle_class_id.id),
                           ("fleet_type", "=", self.fleet_type)]}

class TmsTruckPnl(models.Model):
    """BR-PNL-19, BR-PNL-28, BR-PNL-37: the truck P&L per period, ending in recovery variance and operating
    result. Recovery variance is never allocated to trips, legs, orders or customers (BR-PNL-20)."""

    _name = "tms.truck.pnl"
    _description = "Truck P&L"
    _auto = False
    _order = "period desc, vehicle_id"

    vehicle_id = fields.Many2one("fleet.vehicle", string="Truck", readonly=True)
    company_id = fields.Many2one("res.company", string="Home branch", readonly=True)
    period = fields.Date(readonly=True)
    vehicle_class_id = fields.Many2one("tms.vehicle.class", string="Vehicle class", readonly=True)
    fleet_type = fields.Selection(FLEET_TYPES, readonly=True)
    trip_count = fields.Integer(string="Trips", readonly=True)
    revenue = fields.Float(readonly=True)
    direct_cost = fields.Float(string="Trip direct", readonly=True)
    recoveries = fields.Float(readonly=True)
    contribution = fields.Float(readonly=True)
    std_charge = fields.Float(string="Standard charge", readonly=True)
    trip_margin = fields.Float(readonly=True)
    running_charged = fields.Float(readonly=True)
    fixed_charged = fields.Float(readonly=True)
    running_actual = fields.Float(string="Actual running", readonly=True)
    fixed_actual = fields.Float(string="Actual fixed", readonly=True)
    recovery_variance = fields.Float(readonly=True)
    running_variance = fields.Float(string="Running spending variance", readonly=True)
    fixed_variance = fields.Float(string="Fixed variance", readonly=True)
    volume_variance = fields.Float(string="Fixed volume variance", readonly=True)
    fixed_spending_variance = fields.Float(readonly=True)
    fixed_other_variance = fields.Float(string="Km gap and rate change", readonly=True)
    operating_result = fields.Float(readonly=True)
    charged_km = fields.Float(readonly=True)
    odometer_km = fields.Float(string="Actual km (odometer)", readonly=True)
    gap_km = fields.Float(string="Km between trips", readonly=True)
    planned_km = fields.Float(string="Planned km (period)", readonly=True)
    fixed_budget = fields.Float(string="Fixed budget (period)", readonly=True)
    idle_days = fields.Integer(readonly=True)
    recovery_pct = fields.Float(string="Recovery %", readonly=True, aggregator="avg")

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute("""
            CREATE OR REPLACE VIEW %s AS (
            WITH agg AS (
                SELECT p.vehicle_id, p.period,
                       COALESCE(SUM(p.amount) FILTER (WHERE p.kind IN ('revenue', 'accrued_revenue')), 0) AS revenue,
                       COALESCE(-SUM(p.amount) FILTER (WHERE p.kind IN ('direct', 'accrued_cost')), 0) AS direct,
                       COALESCE(SUM(p.amount) FILTER (WHERE p.kind = 'recovery'), 0) AS recoveries,
                       COALESCE(-SUM(p.amount) FILTER (WHERE p.kind = 'std_charge'), 0) AS std,
                       COALESCE(SUM(p.amount) FILTER (WHERE p.kind = 'std_recovered' AND p.cost_pool = 'running'), 0)
                           AS running_charged,
                       COALESCE(SUM(p.amount) FILTER (WHERE p.kind = 'std_recovered' AND p.cost_pool = 'fixed'), 0)
                           AS fixed_charged,
                       COALESCE(-SUM(p.amount) FILTER (WHERE p.kind = 'truck_running'), 0) AS running_actual,
                       COALESCE(-SUM(p.amount) FILTER (WHERE p.kind = 'truck_fixed'), 0) AS fixed_actual,
                       COUNT(DISTINCT p.trip_id) FILTER (WHERE p.kind IN ('revenue', 'direct', 'std_charge'))
                           AS trips
                  FROM tms_pnl_line p
                 WHERE p.vehicle_id IS NOT NULL AND p.period IS NOT NULL
              GROUP BY 1, 2
            ), ckm AS (
                SELECT aal.tms_vehicle_id AS vehicle_id, date_trunc('month', aal.date)::date AS period,
                       SUM(aal.tms_km) AS km
                  FROM account_analytic_line aal
                 WHERE aal.tms_std_side = 'truck' AND aal.tms_std_pool = 'fixed'
              GROUP BY 1, 2
            ), base AS (
                SELECT a.*, COALESCE(ckm.km, 0) AS charged_km,
                       (a.period + interval '1 month' - interval '1 day')::date AS period_end
                  FROM agg a LEFT JOIN ckm ON ckm.vehicle_id = a.vehicle_id AND ckm.period = a.period
            ), calc AS (
                SELECT b.*, v.tms_ownership AS fleet_type, v.tms_vehicle_class_id AS vehicle_class_id,
                       COALESCE(v.tms_home_branch_id, v.company_id) AS company_id,
                       COALESCE(r.planned_km, 0) / 12.0 AS planned_km,
                       COALESCE(r.fixed_budget, 0) / 12.0 AS fixed_budget,
                       COALESCE(r.fixed_rate, 0) AS fixed_rate,
                       COALESCE(
                           (SELECT MAX(o.value) FROM fleet_vehicle_odometer o
                             WHERE o.vehicle_id = b.vehicle_id AND o.date <= b.period_end)
                           - COALESCE(
                               (SELECT MAX(o.value) FROM fleet_vehicle_odometer o
                                 WHERE o.vehicle_id = b.vehicle_id AND o.date < b.period),
                               (SELECT MIN(o.value) FROM fleet_vehicle_odometer o
                                 WHERE o.vehicle_id = b.vehicle_id AND o.date >= b.period
                                   AND o.date <= b.period_end)), 0) AS odometer_km,
                       CASE WHEN v.tms_ownership = 'owned' THEN (
                           SELECT COUNT(*) FROM generate_series(b.period::timestamp,
                                    LEAST(b.period_end, CURRENT_DATE)::timestamp, interval '1 day') d
                            WHERE NOT EXISTS (
                                SELECT 1 FROM tms_trip_assignment asg
                                 WHERE asg.vehicle_id = b.vehicle_id AND asg.date_from::date <= d::date
                                   AND COALESCE(asg.date_to, now())::date >= d::date))
                       ELSE 0 END AS idle_days
                  FROM base b
                  JOIN fleet_vehicle v ON v.id = b.vehicle_id
             LEFT JOIN LATERAL (
                    SELECT r.* FROM tms_std_rate r
                     WHERE r.vehicle_class_id = v.tms_vehicle_class_id AND r.date_from <= b.period_end
                       AND (r.company_id IS NULL OR r.company_id = COALESCE(v.tms_home_branch_id, v.company_id))
                  ORDER BY r.date_from DESC, r.company_id NULLS LAST LIMIT 1) r ON v.tms_ownership = 'owned'
            ), fin AS (
                SELECT c.*,
                       c.fixed_charged - c.fixed_actual AS fixed_var,
                       CASE WHEN c.fleet_type = 'owned' THEN (c.odometer_km - c.planned_km) * c.fixed_rate ELSE 0 END
                           AS volume,
                       CASE WHEN c.fleet_type = 'owned' THEN c.fixed_budget - c.fixed_actual ELSE 0 END AS spending
                  FROM calc c
            )
            SELECT f.vehicle_id::bigint * 100000 + EXTRACT(YEAR FROM f.period)::int * 12
                       + EXTRACT(MONTH FROM f.period)::int AS id,
                   f.vehicle_id, f.company_id, f.period, f.vehicle_class_id, f.fleet_type, f.trips AS trip_count,
                   f.revenue, f.direct AS direct_cost, f.recoveries,
                   f.revenue + f.recoveries - f.direct AS contribution, f.std AS std_charge,
                   f.revenue + f.recoveries - f.direct - f.std AS trip_margin,
                   f.running_charged, f.fixed_charged, f.running_actual, f.fixed_actual,
                   f.running_charged + f.fixed_charged - f.running_actual - f.fixed_actual AS recovery_variance,
                   f.running_charged - f.running_actual AS running_variance,
                   f.fixed_var AS fixed_variance, f.volume AS volume_variance,
                   f.spending AS fixed_spending_variance,
                   CASE WHEN f.fleet_type = 'owned' THEN f.fixed_var - f.volume - f.spending ELSE 0 END
                       AS fixed_other_variance,
                   f.revenue + f.recoveries - f.direct - f.std
                       + f.running_charged + f.fixed_charged - f.running_actual - f.fixed_actual AS operating_result,
                   f.charged_km, f.odometer_km, GREATEST(f.odometer_km - f.charged_km, 0) AS gap_km,
                   f.planned_km, f.fixed_budget, f.idle_days,
                   CASE WHEN f.running_actual + f.fixed_actual != 0
                        THEN (f.running_charged + f.fixed_charged) / (f.running_actual + f.fixed_actual) * 100
                        ELSE 0 END AS recovery_pct
              FROM fin f
            )""" % self._table)

    def action_open_lines(self):
        self.ensure_one()
        end = fields.Date.end_of(self.period, "month")
        return {"type": "ir.actions.act_window", "name": _("Ledger lines"), "res_model": "tms.pnl.line",
                "view_mode": "list,pivot",
                "domain": [("vehicle_id", "=", self.vehicle_id.id), ("date", ">=", self.period), ("date", "<=", end)]}


class TmsRecoveryStatement(models.Model):
    """#18.4 Recovery statement: truck × pool – charged, actual, variance and its split, planned and actual km;
    km between trips are flagged (BR-PNL-37)."""

    _name = "tms.recovery.statement"
    _description = "Recovery statement"
    _auto = False
    _order = "period desc, vehicle_id, cost_pool"

    vehicle_id = fields.Many2one("fleet.vehicle", string="Truck", readonly=True)
    company_id = fields.Many2one("res.company", string="Home branch", readonly=True)
    period = fields.Date(readonly=True)
    vehicle_class_id = fields.Many2one("tms.vehicle.class", string="Vehicle class", readonly=True)
    cost_pool = fields.Selection(POOLS, string="Pool", readonly=True)
    charged = fields.Float(readonly=True)
    actual = fields.Float(readonly=True)
    variance = fields.Float(readonly=True)
    volume_variance = fields.Float(readonly=True)
    spending_variance = fields.Float(readonly=True)
    other_variance = fields.Float(string="Km gap and rate change", readonly=True)
    planned_km = fields.Float(string="Planned km", readonly=True)
    charged_km = fields.Float(readonly=True)
    odometer_km = fields.Float(string="Actual km (odometer)", readonly=True)
    gap_km = fields.Float(string="Km between trips", readonly=True)
    gap_flag = fields.Boolean(string="Km gap", readonly=True)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute("""
            CREATE OR REPLACE VIEW %s AS (
            SELECT t.id * 2 AS id, t.vehicle_id, t.company_id, t.period, t.vehicle_class_id, 'running' AS cost_pool,
                   t.running_charged AS charged, t.running_actual AS actual, t.running_variance AS variance,
                   0.0 AS volume_variance, t.running_variance AS spending_variance, 0.0 AS other_variance,
                   t.planned_km, t.charged_km, t.odometer_km, t.gap_km, t.gap_km > 0.5 AS gap_flag
              FROM tms_truck_pnl t WHERE t.fleet_type = 'owned'
            UNION ALL
            SELECT t.id * 2 + 1, t.vehicle_id, t.company_id, t.period, t.vehicle_class_id, 'fixed',
                   t.fixed_charged, t.fixed_actual, t.fixed_variance, t.volume_variance, t.fixed_spending_variance,
                   t.fixed_other_variance, t.planned_km, t.charged_km, t.odometer_km, t.gap_km, t.gap_km > 0.5
              FROM tms_truck_pnl t WHERE t.fleet_type = 'owned'
            )""" % self._table)

    def action_open_lines(self):
        self.ensure_one()
        end = fields.Date.end_of(self.period, "month")
        return {"type": "ir.actions.act_window", "name": _("Pool lines"), "res_model": "tms.pnl.line",
                "view_mode": "list,pivot",
                "domain": [("vehicle_id", "=", self.vehicle_id.id), ("cost_pool", "=", self.cost_pool),
                           ("date", ">=", self.period), ("date", "<=", end)]}


class TmsFleetPnl(models.Model):
    """BR-PNL-29: owned and market subtotals; full cost per km owned (direct + standard charge) against hire
    plus direct cost per km market, by lane."""

    _name = "tms.fleet.pnl"
    _description = "Fleet P&L by lane"
    _auto = False
    _order = "period desc, fleet_type"

    fleet_type = fields.Selection(FLEET_TYPES, readonly=True)
    origin_id = fields.Many2one("res.partner", string="From", readonly=True)
    destination_id = fields.Many2one("res.partner", string="To", readonly=True)
    company_id = fields.Many2one("res.company", string="Branch", readonly=True)
    period = fields.Date(readonly=True)
    vehicle_class_id = fields.Many2one("tms.vehicle.class", string="Vehicle class", readonly=True)
    cargo_type = fields.Selection(CARGO_TYPES, string="Cargo Type", readonly=True)
    trip_count = fields.Integer(string="Trips", readonly=True)
    km = fields.Float(readonly=True)
    revenue = fields.Float(readonly=True)
    direct_cost = fields.Float(string="Trip direct (incl. hire)", readonly=True)
    std_charge = fields.Float(string="Standard charge", readonly=True)
    full_cost = fields.Float(readonly=True)
    trip_margin = fields.Float(readonly=True)
    revenue_per_km = fields.Float(readonly=True, aggregator="avg")
    cost_per_km = fields.Float(string="Full cost per km", readonly=True, aggregator="avg")

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute("""
            CREATE OR REPLACE VIEW %s AS (
            SELECT ROW_NUMBER() OVER (ORDER BY fleet_type, origin_id, destination_id, company_id, period,
                                      vehicle_class_id, cargo_type) AS id,
                   fleet_type, origin_id, destination_id, company_id, period, vehicle_class_id, cargo_type,
                   COUNT(*) AS trip_count, SUM(km) AS km, SUM(revenue) AS revenue, SUM(direct_cost) AS direct_cost,
                   SUM(std_charge) AS std_charge, SUM(direct_cost + std_charge) AS full_cost,
                   SUM(trip_margin) AS trip_margin,
                   CASE WHEN SUM(km) != 0 THEN SUM(revenue) / SUM(km) ELSE 0 END AS revenue_per_km,
                   CASE WHEN SUM(km) != 0 THEN SUM(direct_cost + std_charge) / SUM(km) ELSE 0 END AS cost_per_km
              FROM tms_trip_pnl
             WHERE period IS NOT NULL
          GROUP BY fleet_type, origin_id, destination_id, company_id, period, vehicle_class_id, cargo_type
            )""" % self._table)

    def action_open_lines(self):
        """BR-PNL-33: the trips behind the row."""
        self.ensure_one()
        return {"type": "ir.actions.act_window", "name": _("Trips"), "res_model": "tms.trip.pnl",
                "view_mode": "list,pivot",
                "domain": [("fleet_type", "=", self.fleet_type), ("origin_id", "=", self.origin_id.id),
                           ("destination_id", "=", self.destination_id.id), ("company_id", "=", self.company_id.id),
                           ("period", "=", self.period), ("vehicle_class_id", "=", self.vehicle_class_id.id),
                           ("cargo_type", "=", self.cargo_type)]}

class TmsVendorPnl(models.Model):
    """BR-PNL-30: market trips grouped by vendor with hire, deductions, margin and claims."""

    _name = "tms.vendor.pnl"
    _description = "Market vendor P&L"
    _auto = False
    _order = "period desc, vendor_id"

    vendor_id = fields.Many2one("res.partner", string="Vendor", readonly=True)
    company_id = fields.Many2one("res.company", string="Branch", readonly=True)
    period = fields.Date(readonly=True)
    trip_count = fields.Integer(string="Trips", readonly=True)
    vehicle_count = fields.Integer(string="Vehicles", readonly=True)
    revenue = fields.Float(readonly=True)
    hire = fields.Float(readonly=True)
    other_cost = fields.Float(string="Our trip costs", readonly=True)
    deductions = fields.Float(readonly=True)
    claims = fields.Float(readonly=True)
    margin = fields.Float(readonly=True)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute("""
            CREATE OR REPLACE VIEW %s AS (
            WITH lines AS (
                SELECT tp.vendor_id, tp.company_id, tp.period, tp.trip_id, tp.vehicle_id,
                       COALESCE(SUM(p.amount) FILTER (WHERE p.kind IN ('revenue', 'accrued_revenue')), 0) AS revenue,
                       COALESCE(-SUM(p.amount) FILTER (WHERE p.kind IN ('direct', 'accrued_cost')
                                                     AND p.line_kind = 'hire'), 0) AS hire,
                       COALESCE(-SUM(p.amount) FILTER (WHERE p.kind IN ('direct', 'accrued_cost')
                                                     AND p.line_kind IS DISTINCT FROM 'hire'), 0) AS other_cost,
                       COALESCE(SUM(p.amount) FILTER (WHERE p.kind = 'recovery'), 0) AS deductions,
                       COALESCE(-SUM(p.amount) FILTER (WHERE p.line_kind = 'claim'), 0) AS claims
                  FROM tms_trip_pnl tp
                  JOIN tms_pnl_line p ON p.trip_id = tp.trip_id
                 WHERE tp.fleet_type = 'market' AND tp.period IS NOT NULL
              GROUP BY 1, 2, 3, 4, 5
            )
            SELECT ROW_NUMBER() OVER (ORDER BY vendor_id, company_id, period) AS id,
                   vendor_id, company_id, period, COUNT(DISTINCT trip_id) AS trip_count,
                   COUNT(DISTINCT vehicle_id) AS vehicle_count, SUM(revenue) AS revenue, SUM(hire) AS hire,
                   SUM(other_cost) AS other_cost, SUM(deductions) AS deductions, SUM(claims) AS claims,
                   SUM(revenue - hire - other_cost + deductions) AS margin
              FROM lines
          GROUP BY vendor_id, company_id, period
            )""" % self._table)

    def action_open_lines(self):
        """BR-PNL-33: the vendor's trips of the period."""
        self.ensure_one()
        return {"type": "ir.actions.act_window", "name": _("Trips of %s", self.vendor_id.display_name),
                "res_model": "tms.trip.pnl", "view_mode": "list,pivot",
                "domain": [("vendor_id", "=", self.vendor_id.id), ("company_id", "=", self.company_id.id),
                           ("period", "=", self.period)]}

class TmsBranchPnl(models.Model):
    """BR-PNL-31, BR-PNL-32: branch result = customer trip margins + recovery variance − branch overhead, checked
    against the truck view; the company P&L is the branch results less head-office cost. Trips that cross
    branches (BR-BRN-10) reconcile at company level, each branch showing its share."""

    _name = "tms.branch.pnl"
    _description = "Branch and company P&L"
    _auto = False
    _order = "period desc, company_id"

    company_id = fields.Many2one("res.company", string="Branch", readonly=True)
    period = fields.Date(readonly=True)
    trip_margin = fields.Float(string="Trip margin (customer view)", readonly=True)
    recovery_variance = fields.Float(readonly=True)
    overhead = fields.Float(string="Branch overhead", readonly=True)
    branch_result = fields.Float(readonly=True)
    truck_view = fields.Float(string="Truck view operating result", readonly=True)
    check_difference = fields.Float(string="Reconciliation difference", readonly=True)
    head_office_cost = fields.Float(readonly=True)
    operating_result = fields.Float(readonly=True)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute("""
            CREATE OR REPLACE VIEW %s AS (
            WITH cust AS (
                SELECT company_id, period, SUM(trip_margin) AS margin FROM tms_customer_pnl GROUP BY 1, 2
            ), led AS (
                SELECT company_id, period,
                       COALESCE(SUM(amount) FILTER (WHERE kind IN ('std_recovered', 'truck_running',
                                                                    'truck_fixed')), 0) AS recovery,
                       COALESCE(-SUM(amount) FILTER (WHERE kind = 'overhead'), 0) AS overhead,
                       COALESCE(-SUM(amount) FILTER (WHERE kind = 'head_office'), 0) AS head_office
                  FROM tms_pnl_line WHERE period IS NOT NULL GROUP BY 1, 2
            ), trk AS (
                SELECT company_id, period, SUM(operating_result) AS result FROM tms_truck_pnl GROUP BY 1, 2
            ), keys AS (
                SELECT company_id, period FROM cust UNION SELECT company_id, period FROM led
                UNION SELECT company_id, period FROM trk
            )
            SELECT ROW_NUMBER() OVER (ORDER BY k.company_id, k.period) AS id, k.company_id, k.period,
                   COALESCE(c.margin, 0) AS trip_margin, COALESCE(l.recovery, 0) AS recovery_variance,
                   COALESCE(l.overhead, 0) AS overhead,
                   COALESCE(c.margin, 0) + COALESCE(l.recovery, 0) - COALESCE(l.overhead, 0) AS branch_result,
                   COALESCE(t.result, 0) AS truck_view,
                   COALESCE(c.margin, 0) + COALESCE(l.recovery, 0) - COALESCE(t.result, 0) AS check_difference,
                   COALESCE(l.head_office, 0) AS head_office_cost,
                   COALESCE(c.margin, 0) + COALESCE(l.recovery, 0) - COALESCE(l.overhead, 0)
                       - COALESCE(l.head_office, 0) AS operating_result
              FROM keys k
         LEFT JOIN cust c ON c.company_id = k.company_id AND c.period = k.period
         LEFT JOIN led l ON l.company_id = k.company_id AND l.period = k.period
         LEFT JOIN trk t ON t.company_id = k.company_id AND t.period = k.period
             WHERE k.period IS NOT NULL
            )""" % self._table)

    def action_open_lines(self):
        """BR-PNL-33: the branch's ledger lines of the period."""
        self.ensure_one()
        end = fields.Date.end_of(self.period, "month")
        return {"type": "ir.actions.act_window", "name": _("Ledger lines"), "res_model": "tms.pnl.line",
                "view_mode": "list,pivot", "context": {"search_default_group_kind": 1},
                "domain": [("company_id", "=", self.company_id.id), ("date", ">=", self.period),
                           ("date", "<=", end)]}

class TmsSoPnl(models.Model):
    _inherit = "tms.so.pnl"

    @api.model
    def _tms_standard_charge_sql(self):
        """BR-PNL-26: the standard charge of an order item is the share its legs carry."""
        return ("SELECT l.sale_line_id, SUM(l.std_charge)::numeric AS amount FROM tms_trip_leg l "
                "WHERE l.sale_line_id IS NOT NULL GROUP BY l.sale_line_id")
