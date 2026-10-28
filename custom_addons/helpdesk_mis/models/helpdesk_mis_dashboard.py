# Copyright 2026 RLFB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Everything the two MIS dashboards display.

The browser sends a filter dictionary and gets back KPI cards, chart series
and table rows. Keeping the arithmetic here rather than in JavaScript means
the dashboards, the pivot views and the exports all read the same SQL views
and cannot drift apart.

A note on BRD 7.1: every KPI carries the date basis it was measured on
("created in period", "closed in period", "currently open"). Tickets created
in a period and tickets closed in that period are different sets, and the
document is explicit that the screen must say which one a number refers to.
"""

from odoo import _, api, fields, models
from odoo.exceptions import AccessError

# How many rows a dashboard table shows before the user drills down.
TABLE_LIMIT = 100
# Longest-running tickets shown on report A4.
OUTLIER_LIMIT = 20


class HelpdeskMisDashboard(models.AbstractModel):
    _name = "helpdesk.mis.dashboard"
    _description = "Helpdesk MIS Dashboard Data"

    # ------------------------------------------------------------------
    # context
    # ------------------------------------------------------------------
    def _mis_company(self):
        return self.env.company

    def _mis_env(self):
        """Run every query in the configured reporting timezone (BRD 9.3)."""
        tz = self._mis_company().hd_mis_timezone
        return self.with_context(tz=tz) if tz else self

    def _mis_check_access(self):
        if not self.env.user.has_group("helpdesk_mis.group_helpdesk_mis_user"):
            raise AccessError(
                _("You do not have access to Helpdesk MIS reporting.")
            )

    def _may_see_people(self):
        return self.env.user.has_group(
            "helpdesk_mis.group_helpdesk_mis_person_data"
        )

    # ------------------------------------------------------------------
    # filters -> domain
    # ------------------------------------------------------------------
    @api.model
    def _normalise_filters(self, filters):
        company = self._mis_company()
        filters = dict(filters or {})
        filters.setdefault("date_basis", company.hd_mis_date_basis)
        filters.setdefault("date_from", False)
        filters.setdefault("date_to", False)
        for key in ("team_ids", "category_ids", "user_ids", "priorities"):
            value = filters.get(key) or []
            filters[key] = [v for v in value if v]
        filters.setdefault("partner_id", False)
        filters.setdefault("state", "all")
        return filters

    @api.model
    def _period_domain(self, filters, field):
        domain = []
        if filters.get("date_from"):
            domain.append((field, ">=", filters["date_from"]))
        if filters.get("date_to"):
            domain.append((field, "<=", filters["date_to"]))
        return domain

    @api.model
    def _common_domain(self, filters):
        """Everything except the date period, which varies per KPI."""
        domain = [("company_id", "=", self._mis_company().id)]
        if filters.get("team_ids"):
            domain.append(("attributed_team_id", "in", filters["team_ids"]))
        if filters.get("category_ids"):
            domain.append(("category_id", "in", filters["category_ids"]))
        if filters.get("user_ids"):
            domain.append(("attributed_user_id", "in", filters["user_ids"]))
        if filters.get("priorities"):
            domain.append(("priority", "in", filters["priorities"]))
        if filters.get("partner_id"):
            domain.append(("partner_id", "=", filters["partner_id"]))
        state = filters.get("state")
        if state == "open":
            domain.append(("is_open", "=", True))
        elif state == "closed":
            domain.append(("is_closed", "=", True))
        elif state == "unassigned":
            domain.append(("is_unassigned", "=", True))
        return domain

    @api.model
    def _basis_domain(self, filters, basis=None):
        """The full domain for a given date basis (BRD section 6)."""
        basis = basis or filters.get("date_basis") or "create"
        domain = self._common_domain(filters)
        if basis == "create":
            domain += self._period_domain(filters, "create_date")
        elif basis == "close":
            domain += [("is_closed", "=", True)]
            domain += self._period_domain(filters, "closed_date")
        elif basis == "last_assignment":
            domain += self._period_domain(filters, "last_assignment_date")
        elif basis == "open":
            domain += [("is_open", "=", True)]
        return domain

    # ------------------------------------------------------------------
    # entry point
    # ------------------------------------------------------------------
    @api.model
    def get_dashboard_data(self, dashboard="performance", filters=None):
        self._mis_check_access()
        self = self._mis_env()
        filters = self._normalise_filters(filters)
        company = self._mis_company()
        payload = {
            "dashboard": dashboard,
            "filters": filters,
            "settings": self._settings_banner(company),
            "may_see_people": self._may_see_people(),
        }
        if dashboard == "open":
            payload.update(self._open_dashboard(filters))
        else:
            payload.update(self._performance_dashboard(filters))
        return payload

    def _settings_banner(self, company):
        basis = dict(
            company._fields["hd_mis_handling_basis"].selection
        ).get(company.hd_mis_handling_basis)
        team = dict(
            company._fields["hd_mis_team_attribution"].selection
        ).get(company.hd_mis_team_attribution)
        person = dict(
            company._fields["hd_mis_person_attribution"].selection
        ).get(company.hd_mis_person_attribution)
        closure = dict(
            company._fields["hd_mis_reopen_counting"].selection
        ).get(company.hd_mis_reopen_counting)
        return {
            "handling_basis": basis,
            "team_attribution": team,
            "person_attribution": person,
            "closure_counting": closure,
            "working_hours": company.hd_mis_working_hours,
            "timezone": company.hd_mis_timezone or _("user's own"),
            "threshold_warn": company.hd_mis_threshold_warn_hours,
            "threshold_high": company.hd_mis_threshold_high_hours,
            "threshold_critical": company.hd_mis_threshold_critical_hours,
        }

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _agg(self, domain, aggregates):
        """One aggregate row over the ticket report."""
        Report = self.env["helpdesk.mis.report"]
        result = Report._read_group(domain, [], aggregates)
        return result[0] if result else tuple(0 for _a in aggregates)

    def _group(self, domain, groupby, aggregates, order=None, limit=None):
        return self.env["helpdesk.mis.report"]._read_group(
            domain, groupby, aggregates, order=order, limit=limit
        )

    @staticmethod
    def _m2o(value):
        """_read_group gives back a recordset for a many2one grouping key."""
        if not value:
            return {"id": False, "name": _("Unassigned")}
        return {"id": value.id, "name": value.display_name}

    # ------------------------------------------------------------------
    # Dashboard A - Ticket Performance MIS (BRD section 7)
    # ------------------------------------------------------------------
    def _performance_dashboard(self, filters):
        created = self._basis_domain(filters, "create")
        closed = self._basis_domain(filters, "close")
        open_now = self._basis_domain(filters, "open")
        selected = self._basis_domain(filters)

        opened_total = self._agg(created, ["opened_count:sum"])[0] or 0
        closed_total = self._agg(closed, ["closed_count:sum"])[0] or 0
        open_total = self._agg(open_now, ["open_count:sum"])[0] or 0
        handling_avg, elapsed_max = self._agg(
            selected, ["handling_hours:avg", "total_elapsed_hours:max"]
        )
        reassigned = self._agg(selected, ["reassigned_ticket_count:sum"])[0] or 0

        kpis = [
            self._kpi("opened", _("Tickets Opened"), opened_total, "count",
                      _("created in the period"), created),
            self._kpi("closed", _("Tickets Closed"), closed_total, "count",
                      _("closed in the period"), closed),
            self._kpi("open", _("Currently Open"), open_total, "count",
                      _("open right now, period ignored"), open_now),
            self._kpi("handling", _("Avg Handling Time"), handling_avg or 0.0,
                      "hours", _("basis set in Settings"), selected),
            self._kpi("max_duration", _("Max Ticket Duration"),
                      elapsed_max or 0.0, "hours",
                      _("longest in the selection"), selected),
            self._kpi("reassigned", _("Tickets Reassigned"), reassigned, "count",
                      _("changed hands at least once"), selected),
        ]
        return {
            "kpis": kpis,
            "charts": self._performance_charts(filters, created, closed, selected),
            "tables": self._performance_tables(filters, selected),
        }

    def _kpi(self, key, label, value, unit, basis, domain):
        return {
            "key": key,
            "label": label,
            "value": round(value, 2) if isinstance(value, float) else value,
            "unit": unit,
            "basis": basis,
            "domain": domain,
        }

    def _performance_charts(self, filters, created, closed, selected):
        granularity = filters.get("granularity") or "day"
        opened_series = self._group(
            created, [f"create_date:{granularity}"], ["opened_count:sum"],
            order=f"create_date:{granularity}",
        )
        closed_series = self._group(
            closed, [f"closed_date:{granularity}"], ["closed_count:sum"],
            order=f"closed_date:{granularity}",
        )
        buckets = {}
        for period, total in opened_series:
            buckets.setdefault(str(period), [0, 0])[0] = total or 0
        for period, total in closed_series:
            buckets.setdefault(str(period), [0, 0])[1] = total or 0
        labels = sorted(buckets)

        def by(field, domain, measure="ticket_count:sum"):
            rows = self._group(domain, [field], [measure], order=None)
            rows = sorted(rows, key=lambda r: -(r[1] or 0))[:12]
            return {
                "labels": [self._m2o(r[0])["name"] for r in rows],
                "datasets": [{"label": "", "data": [r[1] or 0 for r in rows]}],
            }

        charts = {
            "flow": {
                "title": _("Tickets opened versus closed"),
                "type": "line",
                "labels": labels,
                "datasets": [
                    {"label": _("Opened"), "data": [buckets[k][0] for k in labels]},
                    {"label": _("Closed"), "data": [buckets[k][1] for k in labels]},
                ],
            },
            "by_team": dict(
                by("attributed_team_id", selected),
                title=_("Ticket volume by team"), type="bar",
            ),
            "by_category": dict(
                by("category_id", selected),
                title=_("Ticket volume by category"), type="bar",
            ),
            "by_duration": dict(
                by("attributed_team_id", selected, "handling_hours:avg"),
                title=_("Average handling time by team"), type="bar",
            ),
        }
        if self._may_see_people():
            charts["by_person"] = dict(
                by("attributed_user_id", selected),
                title=_("Ticket volume by person"), type="bar",
            )
        return charts

    def _performance_tables(self, filters, selected):
        created = self._basis_domain(filters, "create")
        closed = self._basis_domain(filters, "close")
        open_now = self._basis_domain(filters, "open")
        tables = {
            "a1_team": self._volume_table(
                "attributed_team_id", _("A1 - Tickets by team"),
                created, closed, open_now, selected, _("Team"),
            ),
            "a2_category": self._volume_table(
                "category_id", _("A2 - Tickets by category"),
                created, closed, open_now, selected, _("Category"),
            ),
            "a4_outliers": self._outlier_table(selected),
        }
        if self._may_see_people():
            tables["a3_person"] = self._person_table(
                created, closed, open_now, selected, filters
            )
            tables["a5_time_held"] = self._time_held_table(filters)
        return tables

    def _volume_table(self, field, title, created, closed, open_now, selected, label):
        opened = {
            self._m2o(k)["id"]: v
            for k, v in self._group(created, [field], ["opened_count:sum"])
        }
        shut = {
            self._m2o(k)["id"]: v
            for k, v in self._group(closed, [field], ["closed_count:sum"])
        }
        opens = {
            self._m2o(k)["id"]: v
            for k, v in self._group(open_now, [field], ["open_count:sum"])
        }
        stats = self._group(
            selected, [field],
            [
                "handling_hours:avg",
                "total_elapsed_hours:max",
                "reassigned_ticket_count:sum",
                "reassignment_count:sum",
                "ticket_count:sum",
            ],
        )
        rows = []
        seen = set()
        for key, avg_h, max_h, reassigned, reassign_events, tickets in stats:
            info = self._m2o(key)
            seen.add(info["id"])
            rows.append({
                "id": info["id"],
                "name": info["name"],
                "opened": opened.get(info["id"], 0),
                "closed": shut.get(info["id"], 0),
                "open": opens.get(info["id"], 0),
                "avg_handling": round(avg_h or 0.0, 2),
                "max_duration": round(max_h or 0.0, 2),
                "reassigned": reassigned or 0,
                "avg_reassignments": round(
                    (reassign_events or 0) / tickets, 2
                ) if tickets else 0.0,
            })
        for key, total in opened.items():
            if key not in seen:
                rows.append({
                    "id": key, "name": _("Unassigned"), "opened": total,
                    "closed": shut.get(key, 0), "open": opens.get(key, 0),
                    "avg_handling": 0.0, "max_duration": 0.0,
                    "reassigned": 0, "avg_reassignments": 0.0,
                })
        rows.sort(key=lambda r: -r["opened"])
        return {
            "title": title,
            "group_field": field,
            "columns": [
                {"name": "name", "label": label, "type": "char"},
                {"name": "opened", "label": _("Opened"), "type": "int"},
                {"name": "closed", "label": _("Closed"), "type": "int"},
                {"name": "open", "label": _("Open now"), "type": "int"},
                {"name": "avg_handling", "label": _("Avg handling"), "type": "hours"},
                {"name": "max_duration", "label": _("Max duration"), "type": "hours"},
                {"name": "reassigned", "label": _("Reassigned"), "type": "int"},
                {"name": "avg_reassignments", "label": _("Avg reassign."), "type": "float"},
            ],
            "rows": rows[:TABLE_LIMIT],
        }

    def _person_table(self, created, closed, open_now, selected, filters):
        base = self._volume_table(
            "attributed_user_id", _("A3 - Tickets by person"),
            created, closed, open_now, selected, _("Person"),
        )
        Assign = self.env["helpdesk.mis.assignment.report"]
        adom = self._assignment_domain(filters)
        held = {
            (k.id if k else False): (avg or 0.0, mx or 0.0, touched or 0)
            for k, avg, mx, touched in Assign._read_group(
                adom, ["user_id"],
                ["time_held_hours:avg", "time_held_hours_max:max",
                 "ticket_user_count:sum"],
            )
        }
        moved_in = {
            (k.id if k else False): v or 0
            for k, v in Assign._read_group(
                adom, ["user_id"], ["reassignment_count:sum"]
            )
        }
        moved_out = {
            (k.id if k else False): v or 0
            for k, v in Assign._read_group(
                adom, ["prev_user_id"], ["interval_count:sum"]
            )
        }
        for row in base["rows"]:
            avg, mx, touched = held.get(row["id"], (0.0, 0.0, 0))
            row["avg_held"] = round(avg, 2)
            row["max_held"] = round(mx, 2)
            row["touched"] = touched
            row["reassigned_in"] = moved_in.get(row["id"], 0)
            row["reassigned_out"] = moved_out.get(row["id"], 0)
        base["columns"] += [
            {"name": "touched", "label": _("Tickets touched"), "type": "int"},
            {"name": "avg_held", "label": _("Avg time held"), "type": "hours"},
            {"name": "max_held", "label": _("Max time held"), "type": "hours"},
            {"name": "reassigned_in", "label": _("Reassigned in"), "type": "int"},
            {"name": "reassigned_out", "label": _("Reassigned out"), "type": "int"},
        ]
        return base

    def _assignment_domain(self, filters):
        domain = [("company_id", "=", self._mis_company().id)]
        if filters.get("team_ids"):
            domain.append(("team_id", "in", filters["team_ids"]))
        if filters.get("category_ids"):
            domain.append(("category_id", "in", filters["category_ids"]))
        if filters.get("user_ids"):
            domain.append(("user_id", "in", filters["user_ids"]))
        if filters.get("priorities"):
            domain.append(("priority", "in", filters["priorities"]))
        if filters.get("date_from"):
            domain.append(("assigned_date", ">=", filters["date_from"]))
        if filters.get("date_to"):
            domain.append(("assigned_date", "<=", filters["date_to"]))
        return domain

    def _time_held_table(self, filters):
        Assign = self.env["helpdesk.mis.assignment.report"]
        records = Assign.search(
            self._assignment_domain(filters),
            order="time_held_hours desc, id desc",
            limit=TABLE_LIMIT,
        )
        end_labels = dict(Assign._fields["end_event"].selection)
        rows = [{
            "id": rec.id,
            "ticket_id": rec.ticket_id.id,
            "ticket": rec.ticket_number or "",
            "subject": rec.ticket_name or "",
            "person": rec.user_id.display_name or _("Unassigned"),
            "team": rec.team_id.display_name or "",
            "start": fields.Datetime.to_string(rec.assigned_date) or "",
            "end": fields.Datetime.to_string(rec.unassigned_date) or "",
            "held": round(rec.time_held_hours, 2),
            "cumulative": round(rec.cumulative_user_hours, 2),
            "event": end_labels.get(rec.end_event, ""),
            "next_person": rec.next_user_id.display_name or "",
            "reassignments": rec.ticket_reassignment_total,
        } for rec in records]
        return {
            "title": _("A5 - Time held by each person"),
            "model": "helpdesk.mis.assignment.report",
            "columns": [
                {"name": "ticket", "label": _("Ticket"), "type": "char"},
                {"name": "subject", "label": _("Subject"), "type": "char"},
                {"name": "person", "label": _("Person"), "type": "char"},
                {"name": "team", "label": _("Team"), "type": "char"},
                {"name": "start", "label": _("From"), "type": "datetime"},
                {"name": "end", "label": _("To"), "type": "datetime"},
                {"name": "held", "label": _("Time held"), "type": "hours"},
                {"name": "cumulative", "label": _("Cumulative"), "type": "hours"},
                {"name": "event", "label": _("Ended by"), "type": "char"},
                {"name": "next_person", "label": _("Next person"), "type": "char"},
                {"name": "reassignments", "label": _("Reassignments"), "type": "int"},
            ],
            "rows": rows,
        }

    def _outlier_table(self, selected):
        Report = self.env["helpdesk.mis.report"]
        records = Report.search(
            selected, order="total_elapsed_hours desc, id desc", limit=OUTLIER_LIMIT
        )
        rows = [{
            "id": rec.id,
            "ticket_id": rec.ticket_id.id,
            "ticket": rec.number or "",
            "subject": rec.name or "",
            "customer": rec.partner_id.display_name or "",
            "category": rec.category_id.display_name or "",
            "priority": dict(Report._fields["priority"].selection).get(
                rec.priority, ""
            ),
            "stage": rec.stage_id.display_name or "",
            "created": fields.Datetime.to_string(rec.create_date) or "",
            "closed": fields.Datetime.to_string(rec.closed_date) or "",
            "team": rec.team_id.display_name or "",
            "assignee": rec.user_id.display_name or _("Unassigned"),
            "elapsed": round(rec.total_elapsed_hours, 2),
            "handling": round(rec.handling_hours, 2),
            "reassignments": rec.reassignment_count,
            "max_assignment": round(rec.max_assignment_hours, 2),
        } for rec in records]
        return {
            "title": _("A4 - Longest running tickets"),
            "model": "helpdesk.mis.report",
            "columns": [
                {"name": "ticket", "label": _("Ticket"), "type": "char"},
                {"name": "subject", "label": _("Subject"), "type": "char"},
                {"name": "customer", "label": _("Customer"), "type": "char"},
                {"name": "category", "label": _("Category"), "type": "char"},
                {"name": "priority", "label": _("Priority"), "type": "char"},
                {"name": "stage", "label": _("Status"), "type": "char"},
                {"name": "created", "label": _("Created"), "type": "datetime"},
                {"name": "closed", "label": _("Closed"), "type": "datetime"},
                {"name": "team", "label": _("Team"), "type": "char"},
                {"name": "assignee", "label": _("Assignee"), "type": "char"},
                {"name": "elapsed", "label": _("Total elapsed"), "type": "hours"},
                {"name": "handling", "label": _("Handling"), "type": "hours"},
                {"name": "reassignments", "label": _("Reassigns"), "type": "int"},
                {"name": "max_assignment", "label": _("Longest hold"), "type": "hours"},
            ],
            "rows": rows,
        }

    # ------------------------------------------------------------------
    # Dashboard B - Open Tickets MIS (BRD section 8)
    # ------------------------------------------------------------------
    def _open_dashboard(self, filters):
        company = self._mis_company()
        open_now = self._basis_domain(filters, "open")
        basis = company.hd_mis_open_date_basis
        field = "create_date" if basis == "create" else "last_assignment_date"
        in_period = open_now + self._period_domain(filters, field)
        beyond = open_now + [("past_threshold", "=", True)]

        total = self._agg(open_now, ["open_count:sum"])[0] or 0
        created_in = self._agg(in_period, ["open_count:sum"])[0] or 0
        past = self._agg(beyond, ["open_count:sum"])[0] or 0
        avg_age, max_age = self._agg(
            open_now, ["open_age_hours:avg", "open_age_hours_max:max"]
        )
        reassigned = self._agg(open_now, ["reassigned_ticket_count:sum"])[0] or 0

        kpis = [
            self._kpi("open_total", _("Total Open Tickets"), total, "count",
                      _("open right now"), open_now),
            self._kpi("open_in_period", _("Open, Created in Period"), created_in,
                      "count", _("open now and raised in the period"), in_period),
            self._kpi("past_threshold", _("Open Past Threshold"), past, "count",
                      _("older than %.0f hours")
                      % company.hd_mis_threshold_warn_hours, beyond),
            self._kpi("avg_age", _("Average Open Age"), avg_age or 0.0, "hours",
                      _("creation to now"), open_now),
            self._kpi("max_age", _("Maximum Open Age"), max_age or 0.0, "hours",
                      _("oldest open ticket"), open_now),
            self._kpi("open_reassigned", _("Open Tickets Reassigned"), reassigned,
                      "count", _("changed hands at least once"), open_now),
        ]
        return {
            "kpis": kpis,
            "charts": self._open_charts(filters, open_now),
            "tables": self._open_tables(filters, open_now),
        }

    def _open_charts(self, filters, open_now):
        def by(field, measure="open_count:sum"):
            rows = self._group(open_now, [field], [measure])
            rows = sorted(rows, key=lambda r: -(r[1] or 0))[:12]
            return {
                "labels": [self._m2o(r[0])["name"] for r in rows],
                "datasets": [{"label": "", "data": [round(r[1] or 0, 2) for r in rows]}],
            }

        bands = dict(
            self.env["helpdesk.mis.report"]._fields["age_band"].selection
        )
        band_rows = self._group(open_now, ["age_band"], ["open_count:sum"])
        order = ["ok", "warn", "high", "critical"]
        counts = {k: v for k, v in band_rows}
        charts = {
            "open_by_team": dict(
                by("attributed_team_id"), title=_("Open tickets by team"), type="bar"
            ),
            "open_by_category": dict(
                by("category_id"), title=_("Open tickets by category"), type="bar"
            ),
            "ageing": {
                "title": _("Open-ticket ageing distribution"),
                "type": "bar",
                "labels": [bands[k] for k in order],
                "datasets": [
                    {"label": _("Tickets"),
                     "data": [counts.get(k, 0) for k in order]}
                ],
            },
        }
        if self._may_see_people():
            charts["open_by_person"] = dict(
                by("attributed_user_id"),
                title=_("Open tickets by person"), type="bar",
            )
            charts["current_assignment"] = dict(
                by("attributed_user_id", "current_assignment_hours:avg"),
                title=_("Average current assignment duration"), type="bar",
            )
        return charts

    def _open_tables(self, filters, open_now):
        tables = {
            "b1_team": self._open_group_table(
                "attributed_team_id", _("B1 - Open tickets by team"),
                open_now, filters, _("Team"),
            ),
            "b2_category": self._open_group_table(
                "category_id", _("B2 - Open tickets by category"),
                open_now, filters, _("Category"),
            ),
            "b4_detail": self._open_detail_table(open_now),
        }
        if self._may_see_people():
            tables["b3_person"] = self._open_group_table(
                "attributed_user_id", _("B3 - Open tickets by person"),
                open_now, filters, _("Person"),
            )
        return tables

    def _open_group_table(self, field, title, open_now, filters, label):
        created = self._basis_domain(filters, "create")
        closed = self._basis_domain(filters, "close")
        opened_period = {
            self._m2o(k)["id"]: v
            for k, v in self._group(created, [field], ["opened_count:sum"])
        }
        closed_period = {
            self._m2o(k)["id"]: v
            for k, v in self._group(closed, [field], ["closed_count:sum"])
        }
        stats = self._group(
            open_now, [field],
            [
                "open_count:sum",
                "open_age_hours:avg",
                "open_age_hours_max:max",
                "current_assignment_hours:avg",
                "reassigned_ticket_count:sum",
                "unassigned_count:sum",
            ],
        )
        rows = []
        for key, total, avg_age, max_age, cur, reassigned, unassigned in stats:
            info = self._m2o(key)
            rows.append({
                "id": info["id"],
                "name": info["name"],
                "open": total or 0,
                "avg_age": round(avg_age or 0.0, 2),
                "max_age": round(max_age or 0.0, 2),
                "avg_current": round(cur or 0.0, 2),
                "reassigned": reassigned or 0,
                "unassigned": unassigned or 0,
                "opened_period": opened_period.get(info["id"], 0),
                "closed_period": closed_period.get(info["id"], 0),
            })
        rows.sort(key=lambda r: -r["open"])
        return {
            "title": title,
            "group_field": field,
            "columns": [
                {"name": "name", "label": label, "type": "char"},
                {"name": "open", "label": _("Open"), "type": "int"},
                {"name": "unassigned", "label": _("Unassigned"), "type": "int"},
                {"name": "avg_age", "label": _("Avg age"), "type": "hours"},
                {"name": "max_age", "label": _("Max age"), "type": "hours"},
                {"name": "avg_current", "label": _("Avg current hold"), "type": "hours"},
                {"name": "reassigned", "label": _("Reassigned"), "type": "int"},
                {"name": "opened_period", "label": _("Opened in period"), "type": "int"},
                {"name": "closed_period", "label": _("Closed in period"), "type": "int"},
            ],
            "rows": rows[:TABLE_LIMIT],
        }

    def _open_detail_table(self, open_now):
        Report = self.env["helpdesk.mis.report"]
        records = Report.search(
            open_now, order="open_age_hours desc, id desc", limit=TABLE_LIMIT
        )
        bands = dict(Report._fields["age_band"].selection)
        rows = [{
            "id": rec.id,
            "ticket_id": rec.ticket_id.id,
            "ticket": rec.number or "",
            "subject": rec.name or "",
            "customer": rec.partner_id.display_name or "",
            "category": rec.category_id.display_name or "",
            "priority": dict(Report._fields["priority"].selection).get(
                rec.priority, ""
            ),
            "stage": rec.stage_id.display_name or "",
            "created": fields.Datetime.to_string(rec.create_date) or "",
            "team": rec.team_id.display_name or "",
            "assignee": rec.user_id.display_name or _("Unassigned"),
            "last_assigned": fields.Datetime.to_string(rec.last_assignment_date) or "",
            "current_hold": round(rec.current_assignment_hours, 2),
            "age": round(rec.open_age_hours, 2),
            "reassignments": rec.reassignment_count,
            "band": bands.get(rec.age_band, ""),
            "beyond": round(rec.hours_beyond_threshold, 2),
        } for rec in records]
        return {
            "title": _("B4 - Open ticket detail"),
            "model": "helpdesk.mis.report",
            "columns": [
                {"name": "ticket", "label": _("Ticket"), "type": "char"},
                {"name": "subject", "label": _("Subject"), "type": "char"},
                {"name": "customer", "label": _("Customer"), "type": "char"},
                {"name": "category", "label": _("Category"), "type": "char"},
                {"name": "priority", "label": _("Priority"), "type": "char"},
                {"name": "stage", "label": _("Status"), "type": "char"},
                {"name": "created", "label": _("Created"), "type": "datetime"},
                {"name": "team", "label": _("Team"), "type": "char"},
                {"name": "assignee", "label": _("Assignee"), "type": "char"},
                {"name": "last_assigned", "label": _("Last assigned"), "type": "datetime"},
                {"name": "current_hold", "label": _("Current hold"), "type": "hours"},
                {"name": "age", "label": _("Total age"), "type": "hours"},
                {"name": "reassignments", "label": _("Reassigns"), "type": "int"},
                {"name": "band", "label": _("Ageing"), "type": "char"},
                {"name": "beyond", "label": _("Beyond threshold"), "type": "hours"},
            ],
            "rows": rows,
        }

    # ------------------------------------------------------------------
    # drill-down (BRD FR-12)
    # ------------------------------------------------------------------
    @api.model
    def action_drill_down(self, domain, title=None, model="helpdesk.mis.report"):
        """Open the tickets behind a KPI card, chart bar or table row."""
        self._mis_check_access()
        if model == "helpdesk.mis.assignment.report":
            return {
                "type": "ir.actions.act_window",
                "name": title or _("Assignment intervals"),
                "res_model": model,
                "domain": domain or [],
                "view_mode": "list,pivot,graph",
                "target": "current",
            }
        ticket_ids = self.env["helpdesk.mis.report"].search(
            domain or []
        ).mapped("ticket_id").ids
        return {
            "type": "ir.actions.act_window",
            "name": title or _("Tickets"),
            "res_model": "helpdesk.ticket",
            "domain": [("id", "in", ticket_ids)],
            "view_mode": "list,kanban,form",
            "target": "current",
            "context": {"create": False},
        }
