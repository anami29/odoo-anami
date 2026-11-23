from datetime import timedelta

from odoo import _, api, fields, models

ALERT_MODES = [
    ("any", "Any record"),
    ("trend", "Trend only, never red"),
    ("count_above", "Count above the threshold"),
    ("amount_above", "Amount above the threshold"),
    ("amount_below", "Amount below the threshold"),
    ("older_than", "Any record older than the threshold (days)"),
]

# How each KPI turns red when its record carries no alert setting (records loaded before the setting existed).
ALERT_DEFAULTS = {
    "KPI-04": ("trend", 0.0),
    "KPI-07": ("amount_above", 48.0),  # average hours from delivery to POD verification
    "KPI-08": ("older_than", 30.0),  # open claims older than 30 days
    "KPI-10": ("older_than", 30.0),  # one monthly billing cycle
    "KPI-13": ("amount_below", 0.0),  # trip margin below zero
    "KPI-16": ("amount_below", 25.0),  # win rate % below 25
    "KPI-18": ("amount_below", 90.0),  # fleet recovery % below 90
    "KPI-19": ("amount_above", 0.0),  # any unassigned cost in the period
    "KPI-24": ("count_above", 3.0),  # more than three credit overrides in the period
}


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
    alert_mode = fields.Selection(ALERT_MODES, string="Alert evaluation",
                                  help="How the tile turns red. Empty uses the built-in rule for the KPI.")
    alert_threshold = fields.Float(string="Alert threshold",
                                   help="Count, amount, percentage or days the alert evaluation compares against.")
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
            count, amount, model, domain = method()
            kpi.value_count = count
            kpi.value_amount = amount
            kpi.alert = kpi._tms_evaluate_alert(count, amount, model, domain)
            kpi.available = True

    def _tms_alert_setting(self):
        """(mode, threshold) of the KPI: its own setting, else the built-in rule for its code, else Any."""
        self.ensure_one()
        if self.alert_mode:
            return self.alert_mode, self.alert_threshold
        return ALERT_DEFAULTS.get(self.code, ("any", 0.0))

    @api.model
    def _tms_domain_ids(self, domain):
        for leaf in domain or []:
            if isinstance(leaf, (list, tuple)) and len(leaf) == 3 and leaf[0] == "id" and leaf[1] == "in":
                return list(leaf[2])
        return None

    def _tms_evaluate_alert(self, count, amount, model, domain):
        """Red or not, by the KPI's alert evaluation instead of 'has a count'."""
        self.ensure_one()
        mode, threshold = self._tms_alert_setting()
        ids = self._tms_domain_ids(domain)
        has_rows = bool(count) or bool(ids)
        if mode == "trend":
            return False
        if mode == "count_above":
            return count > threshold
        if mode == "amount_above":
            return has_rows and amount > threshold
        if mode == "amount_below":
            return has_rows and amount < threshold
        if mode == "older_than":
            if not has_rows:
                return False
            Model = self.env[model]
            if "create_date" not in Model._fields:
                return bool(count)
            limit = fields.Datetime.now() - timedelta(days=threshold)
            rows = Model.browse(ids) if ids is not None else Model.search(domain)
            return any(r.create_date and r.create_date < limit for r in rows)
        return bool(count)

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
