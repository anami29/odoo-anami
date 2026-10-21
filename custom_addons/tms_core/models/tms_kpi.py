from datetime import timedelta

from odoo import _, api, fields, models


class TmsKpi(models.Model):
    """BR-DSH-01..06: the exception dashboard. One tile per KPI of #19.5; every tile drills to its records.

    Values are computed for the user's allowed branches, so branch users see their branches only (BR-DSH-06).
    Modules add KPIs by defining ``_kpi_<code>`` returning (count, amount, model, domain)."""

    _name = "tms.kpi"
    _description = "TMS exception KPI"
    _order = "sequence, code"

    code = fields.Char(required=True)
    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    definition = fields.Text(translate=True)
    alert_rule = fields.Char(string="Alert when", translate=True)
    group = fields.Selection([("exception", "Exceptions"), ("margin", "Margin"), ("sales", "Sales"),
                              ("contract", "Contracts"), ("compliance", "Compliance")], default="exception")
    value_count = fields.Integer(compute="_compute_value", string="Count")
    value_amount = fields.Float(compute="_compute_value", string="Amount")
    alert = fields.Boolean(compute="_compute_value")
    available = fields.Boolean(compute="_compute_value")
    active = fields.Boolean(default=True)

    _sql_constraints = [("code_uniq", "unique(code)", "KPI codes are unique.")]

    def _method(self):
        return "_kpi_%s" % self.code.lower().replace("-", "_")

    def _compute_value(self):
        for kpi in self:
            method = getattr(kpi, kpi._method(), None)
            if not method:
                kpi.value_count, kpi.value_amount, kpi.alert, kpi.available = 0, 0.0, False, False
                continue
            count, amount, _model, _domain = method()
            kpi.value_count = count
            kpi.value_amount = amount
            kpi.alert = bool(count)
            kpi.available = True

    def action_drill(self):
        self.ensure_one()
        method = getattr(self, self._method(), None)
        if not method:
            return False
        _count, _amount, model, domain = method()
        return {
            "type": "ir.actions.act_window",
            "name": self.name,
            "res_model": model,
            "view_mode": "list,form",
            "domain": domain,
            "context": {"create": False},
        }

    # ------------------------------------------------------------------ helpers
    def _branches(self):
        return [("company_id", "in", self.env.companies.ids)]

    def _period_start(self):
        today = fields.Date.context_today(self)
        return today.replace(day=1)

    def _kpi_scope(self, model):
        """Hook: the dashboard filter's customer and vehicle terms for ``model`` (BR-DSH-01)."""
        return []

    def _kpi_search(self, model, domain):
        """Every KPI searches through here, so the filter narrows its count and its amount alike."""
        return self.env[model].search(domain + self._kpi_scope(model))

    def _count(self, model, domain, amount_field=None):
        records = self._kpi_search(model, domain)
        amount = sum(records.mapped(amount_field)) if amount_field else 0.0
        return len(records), amount, model, [("id", "in", records.ids)]

    # ------------------------------------------------------------------ core KPIs
    def _kpi_kpi_01(self):
        return self._count("tms.order", self._branches() + [("started_without_gate", "=", True)])

    def _kpi_kpi_02(self):
        soon = fields.Date.context_today(self) + timedelta(days=30)
        return self._count("tms.compliance.document", self._branches() + [
            ("valid_until", "<=", soon), ("type_id.mandatory_for_start", "=", True),
            "|", ("vehicle_id.active", "=", True), ("driver_id.is_active", "=", True)])

    def _kpi_kpi_03(self):
        return self._count("tms.trip.breakdown", self._branches() + [("resumed_at", "=", False),
                                                                   ("state", "!=", "cancel")], "repair_cost")

    def _kpi_kpi_04(self):
        return self._count("tms.trip.assignment", self._branches() + [
            ("reason", "in", ("breakdown", "accident")), ("date_from", ">=", self._period_start())])

    def _kpi_kpi_05(self):
        return self._count("tms.order", self._branches() + [("dsr_overdue", "=", True)])

    def _kpi_kpi_06(self):
        return self._count("tms.trip.consignment", self._branches() + [("pod_overdue", "=", True)])

    def _kpi_kpi_07(self):
        links = self._kpi_search("tms.trip.consignment", self._branches() + [
            ("pod_status", "=", "verified"), ("verified_on", ">=", self._period_start())])
        hours = [(link.verified_on - link.delivered_at).total_seconds() / 3600.0 for link in links
                 if link.delivered_at and link.verified_on]
        avg = sum(hours) / len(hours) if hours else 0.0
        return len(links), avg, "tms.trip.consignment", [("id", "in", links.ids)]

    def _kpi_kpi_08(self):
        return self._count("tms.damage.claim", self._branches() + [("state", "not in", ("settled", "rejected"))],
                           "estimated_amount")

    def _kpi_kpi_09(self):
        return self._count("tms.trip.charge", self._branches() + [("recover_from", "=", "customer"),
                                                                 ("state", "=", "approved")], "amount")

    def _kpi_kpi_10(self):
        return self._count("tms.trip.charge", self._branches() + [("recover_from", "!=", "customer"),
                                                                 ("state", "=", "approved")], "amount")

    def _kpi_kpi_11(self):
        matches = self._kpi_search("tms.vendor.bill.match", self._branches() + [("state", "=", "blocked")])
        amount = sum(matches.mapped("move_id.amount_total"))
        return len(matches), amount, "tms.vendor.bill.match", [("id", "in", matches.ids)]

    def _kpi_kpi_12(self):
        """Hire bills posted without verified PODs, or over tolerance without an override."""
        matches = self._kpi_search("tms.vendor.bill.match", self._branches() + [("move_id.state", "=", "posted")])
        bad = matches.filtered(lambda m: not m.pod_ok or (not m.tolerance_ok and not m.override_by_id))
        moves = bad.mapped("move_id")
        return len(moves), sum(moves.mapped("amount_total")), "account.move", [("id", "in", moves.ids)]

    def _kpi_kpi_15(self):
        trips = self._kpi_search("tms.order", self._branches() + [
            ("delivered_on", ">=", self._period_start()), ("fleet_type", "=", "owned")])
        low = trips.filtered(lambda t: t.fuel_kmpl and t.fuel_norm and t.fuel_kmpl < t.fuel_norm)
        vehicles = low.mapped("primary_vehicle_id")
        return len(vehicles), 0.0, "tms.order", [("id", "in", low.ids)]

    def _kpi_kpi_19(self):
        lines = self._kpi_search("account.move.line", self._branches() + [
            ("tms_overhead", "=", True), ("parent_state", "=", "posted"), ("date", ">=", self._period_start())])
        return len(lines), sum(lines.mapped("balance")), "account.move.line", [("id", "in", lines.ids)]

    def _kpi_kpi_23(self):
        return self._count("tms.consignment", self._branches() + [("tbb_overdue", "=", True)], "freight_amount")

    def _kpi_kpi_24(self):
        logs = self._kpi_search("tms.override.log", self._branches() + [("kind", "=", "credit"),
                                                                       ("create_date", ">=", self._period_start())])
        return len(logs), sum(logs.mapped("amount")), "tms.override.log", [("id", "in", logs.ids)]

    @api.model
    def action_open_dashboard(self):
        return {
            "type": "ir.actions.act_window",
            "name": _("Exception dashboard"),
            "res_model": "tms.kpi",
            "view_mode": "kanban,list",
            "context": {"create": False},
        }
