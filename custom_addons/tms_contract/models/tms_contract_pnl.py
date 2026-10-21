from odoo import fields, models, tools


class TmsContractPnl(models.Model):
    """BR-PNL-35: per contract, value, extensions, invoiced, reserved, balance and utilisation beside its order's
    revenue, cost, contribution and margin. Incidentals billed at actual are revenue and trip direct cost alike."""

    _name = "tms.contract.pnl"
    _description = "Contract P&L"
    _auto = False
    _order = "contract_id desc"

    contract_id = fields.Many2one("tms.contract", readonly=True)
    company_id = fields.Many2one("res.company", string="Branch", readonly=True)
    partner_id = fields.Many2one("res.partner", string="Customer", readonly=True)
    order_id = fields.Many2one("sale.order", string="Contract order", readonly=True)
    state = fields.Selection([("draft", "Draft"), ("active", "Active"), ("exhausted", "Exhausted"),
                              ("expired", "Expired"), ("closed", "Closed")], readonly=True)
    value = fields.Float(readonly=True)
    extensions = fields.Float(readonly=True)
    invoiced = fields.Float(readonly=True)
    reserved = fields.Float(readonly=True)
    balance = fields.Float(readonly=True)
    utilisation = fields.Float(string="Utilisation %", readonly=True, aggregator="avg")
    revenue = fields.Float(readonly=True)
    estimated_cost = fields.Float(readonly=True)
    actual = fields.Float(string="Actual cost", readonly=True)
    standard_charge = fields.Float(readonly=True)
    contribution = fields.Float(readonly=True)
    margin_pct = fields.Float(string="Margin %", readonly=True, aggregator="avg")

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        std = self.env["tms.so.pnl"]._tms_standard_charge_sql()
        self.env.cr.execute("""
            CREATE OR REPLACE VIEW %(table)s AS (
            WITH ext AS (
                SELECT contract_id AS cid, SUM(added_value) AS v FROM tms_contract_extension
                 WHERE state = 'approved' GROUP BY contract_id
            ), lines AS (
                SELECT c.id AS cid, am.move_type, aml.tms_line_kind, aml.balance, aml.price_subtotal,
                       aml.price_total, c.value_basis, c.claims_reduce_invoiced
                  FROM account_move_line aml
                  JOIN account_move am ON am.id = aml.move_id
             LEFT JOIN tms_consignment lr ON lr.id = aml.tms_consignment_id
                  JOIN sale_order_line sol ON sol.id = COALESCE(aml.tms_sale_line_id, lr.sale_line_id)
                  JOIN tms_contract c ON c.id = sol.tms_contract_id
                 WHERE am.state = 'posted' AND am.move_type IN ('out_invoice', 'out_refund')
                   AND aml.display_type = 'product'
            ), inv AS (
                SELECT cid,
                       SUM(CASE WHEN move_type = 'out_refund' THEN -1 ELSE 1 END
                           * CASE WHEN value_basis = 'taxed' THEN price_total ELSE price_subtotal END)
                           FILTER (WHERE tms_line_kind IN ('freight', 'charge', 'rate_correction', 'dead_freight',
                                                           'supplementary')
                                   OR (tms_line_kind = 'claim' AND claims_reduce_invoiced)) AS v,
                       SUM(-balance) FILTER (WHERE tms_line_kind IN ('freight', 'charge', 'rate_correction',
                                                                     'dead_freight', 'supplementary')) AS revenue
                  FROM lines GROUP BY cid
            ), res AS (
                SELECT contract_id AS cid, SUM(amount) AS v FROM tms_contract_reservation GROUP BY contract_id
            ), cost AS (
                SELECT sol.tms_contract_id AS cid, SUM(el.estimated_cost) AS est
                  FROM tms_estimate_line el JOIN sale_order_line sol ON sol.id = el.sale_line_id
                 WHERE sol.tms_contract_id IS NOT NULL GROUP BY sol.tms_contract_id
            ), act AS (
                SELECT sol.tms_contract_id AS cid, SUM(aml.balance) AS v
                  FROM account_move_line aml
                  JOIN tms_estimate_line el ON el.id = aml.tms_estimate_line_id
                  JOIN sale_order_line sol ON sol.id = el.sale_line_id
                 WHERE aml.parent_state = 'posted' AND aml.display_type = 'product'
                   AND sol.tms_contract_id IS NOT NULL
              GROUP BY sol.tms_contract_id
            ), std AS (
                SELECT sol.tms_contract_id AS cid, SUM(s.amount) AS v
                  FROM (%(std)s) s JOIN sale_order_line sol ON sol.id = s.sale_line_id
                 WHERE sol.tms_contract_id IS NOT NULL GROUP BY sol.tms_contract_id
            )
            SELECT c.id AS id, c.id AS contract_id, c.company_id, c.partner_id, c.order_id, c.state,
                   c.value, COALESCE(ext.v, 0) AS extensions,
                   COALESCE(c.opening_consumption, 0) + COALESCE(inv.v, 0) AS invoiced,
                   COALESCE(res.v, 0) AS reserved,
                   c.value + COALESCE(ext.v, 0) - COALESCE(c.opening_consumption, 0) - COALESCE(inv.v, 0)
                       - COALESCE(res.v, 0) AS balance,
                   CASE WHEN c.value + COALESCE(ext.v, 0) <> 0
                        THEN (COALESCE(c.opening_consumption, 0) + COALESCE(inv.v, 0) + COALESCE(res.v, 0))
                             / (c.value + COALESCE(ext.v, 0)) * 100 ELSE 0 END AS utilisation,
                   COALESCE(inv.revenue, 0) AS revenue,
                   COALESCE(cost.est, 0) AS estimated_cost,
                   COALESCE(act.v, 0) AS actual,
                   COALESCE(std.v, 0) AS standard_charge,
                   COALESCE(inv.revenue, 0) - COALESCE(act.v, 0) - COALESCE(std.v, 0) AS contribution,
                   CASE WHEN COALESCE(inv.revenue, 0) <> 0
                        THEN (COALESCE(inv.revenue, 0) - COALESCE(act.v, 0) - COALESCE(std.v, 0))
                             / inv.revenue * 100 ELSE 0 END AS margin_pct
              FROM tms_contract c
         LEFT JOIN ext ON ext.cid = c.id
         LEFT JOIN inv ON inv.cid = c.id
         LEFT JOIN res ON res.cid = c.id
         LEFT JOIN cost ON cost.cid = c.id
         LEFT JOIN act ON act.cid = c.id
         LEFT JOIN std ON std.cid = c.id
             WHERE c.state <> 'draft'
            )""" % {"table": self._table, "std": std})


class TmsKpi(models.Model):
    _inherit = "tms.kpi"

    def _kpi_kpi_20(self):
        """Contract utilisation at or above the alert, per Active contract."""
        contracts = self._kpi_search("tms.contract", self._branches() + [("state", "=", "active")])
        hot = contracts.filtered(lambda c: c.utilisation >= c.company_id.tms_contract_util_alert)
        top = max(hot.mapped("utilisation") or [0.0])
        return len(hot), top, "tms.contract", [("id", "in", hot.ids)]

    def _kpi_kpi_21(self):
        """Refused movements and invoices Awaiting extension."""
        items = self._kpi_search("sale.order.line", self._branches() + [
            ("tms_contract_id", "!=", False), ("tms_is_movement", "=", True), ("tms_release_state", "=", "draft")])
        held = items.filtered("tms_held")
        moves = self._kpi_search("account.move", self._branches() + [
            ("state", "=", "draft"), ("move_type", "=", "out_invoice"),
            "|", ("invoice_line_ids.tms_sale_line_id.tms_contract_id", "!=", False),
            ("invoice_line_ids.tms_consignment_id.sale_line_id.tms_contract_id", "!=", False)])
        waiting = moves.filtered("tms_contract_awaiting")
        amount = sum(held.mapped("tms_shortfall")) + sum(waiting.mapped("tms_contract_shortfall"))
        if held or not waiting:
            return len(held) + len(waiting), amount, "sale.order.line", [("id", "in", held.ids)]
        return len(waiting), amount, "account.move", [("id", "in", waiting.ids)]

    def _kpi_kpi_22(self):
        """Over-contract exposure: reserved or invoiced above value plus extensions (BR-CON-19)."""
        contracts = self._kpi_search("tms.contract", self._branches() + [("state", "!=", "draft")])
        over = contracts.filtered(lambda c: c.over_contract > 0.005)
        return len(over), sum(over.mapped("over_contract")), "tms.contract", [("id", "in", over.ids)]
