/** @odoo-module **/
/**
 * The two MIS dashboards (BRD sections 7 and 8).
 *
 * All arithmetic lives in helpdesk.mis.dashboard on the server, so these
 * screens, the pivot views and the exports can never disagree about what a
 * number means. This file only draws what it is handed.
 */

import {
    Component,
    onMounted,
    onPatched,
    onWillStart,
    onWillUnmount,
    useRef,
    useState,
} from "@odoo/owl";
import {registry} from "@web/core/registry";
import {useService} from "@web/core/utils/hooks";
import {loadBundle} from "@web/core/assets";
import {_t} from "@web/core/l10n/translation";

const PALETTE = [
    "#714B67", "#017E84", "#F0AD4E", "#D9534F", "#5BC0DE",
    "#5CB85C", "#8E6C8A", "#2C8EC0", "#C7763B", "#7B9E3F",
];

/** Hours as something a person reads: 2h 30m, 3d 4h. */
export function formatHours(value) {
    const hours = Number(value) || 0;
    if (!hours) {
        return "0m";
    }
    const totalMinutes = Math.round(Math.abs(hours) * 60);
    const days = Math.floor(totalMinutes / 1440);
    const hrs = Math.floor((totalMinutes % 1440) / 60);
    const mins = totalMinutes % 60;
    const parts = [];
    if (days) {
        parts.push(`${days}d`);
    }
    if (hrs || days) {
        parts.push(`${hrs}h`);
    }
    if (!days) {
        parts.push(`${mins}m`);
    }
    return (hours < 0 ? "-" : "") + parts.join(" ");
}

/** One canvas, one Chart.js instance, torn down with the component. */
class MisChart extends Component {
    static template = "helpdesk_mis.Chart";
    static props = {spec: Object};

    setup() {
        this.canvasRef = useRef("canvas");
        onMounted(() => this._draw());
        onPatched(() => this._draw());
        onWillUnmount(() => this._destroy());
    }

    _destroy() {
        if (this.chart) {
            this.chart.destroy();
            this.chart = null;
        }
    }

    _draw() {
        this._destroy();
        const canvas = this.canvasRef.el;
        if (!canvas || !window.Chart) {
            return;
        }
        const spec = this.props.spec;
        const datasets = (spec.datasets || []).map((dataset, index) => ({
            label: dataset.label || spec.title,
            data: dataset.data || [],
            backgroundColor:
                spec.type === "line"
                    ? PALETTE[index % PALETTE.length]
                    : (spec.labels || []).map(
                          (_label, i) => PALETTE[i % PALETTE.length]
                      ),
            borderColor: PALETTE[index % PALETTE.length],
            borderWidth: spec.type === "line" ? 2 : 0,
            fill: false,
            tension: 0.25,
        }));
        this.chart = new window.Chart(canvas, {
            type: spec.type || "bar",
            data: {labels: spec.labels || [], datasets},
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {display: datasets.length > 1},
                    tooltip: {enabled: true},
                },
                scales: {y: {beginAtZero: true}},
            },
        });
    }
}

export class HelpdeskMisDashboard extends Component {
    static template = "helpdesk_mis.Dashboard";
    static components = {MisChart};
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.dashboard =
            (this.props.action &&
                this.props.action.context &&
                this.props.action.context.mis_dashboard) ||
            "performance";

        const today = new Date();
        const firstOfMonth = new Date(today.getFullYear(), today.getMonth(), 1);
        this.state = useState({
            loading: true,
            error: false,
            data: null,
            options: {teams: [], categories: [], users: []},
            filters: {
                date_from: this._toDate(firstOfMonth),
                date_to: this._toDate(today),
                date_basis: "create",
                granularity: "day",
                team_ids: [],
                category_ids: [],
                user_ids: [],
                priorities: [],
                state: "all",
                partner_id: false,
            },
        });

        onWillStart(async () => {
            await loadBundle("web.chartjs_lib");
            await this._loadOptions();
            await this.load();
        });
    }

    _toDate(date) {
        return date.toISOString().slice(0, 10);
    }

    get isOpenDashboard() {
        return this.dashboard === "open";
    }

    get title() {
        return this.isOpenDashboard
            ? _t("Electrocare Helpdesk - Open Tickets MIS")
            : _t("Electrocare Helpdesk - Ticket Performance MIS");
    }

    async _loadOptions() {
        const [teams, categories] = await Promise.all([
            this.orm.searchRead(
                "helpdesk.ticket.team", [], ["display_name"], {limit: 200}
            ),
            this.orm.searchRead(
                "helpdesk.ticket.category", [], ["display_name"], {limit: 500}
            ),
        ]);
        this.state.options.teams = teams;
        this.state.options.categories = categories;
        try {
            this.state.options.users = await this.orm.searchRead(
                "res.users",
                [["share", "=", false]],
                ["display_name"],
                {limit: 300}
            );
        } catch {
            // A reader without person-level rights simply gets no person list.
            this.state.options.users = [];
        }
    }

    async load() {
        this.state.loading = true;
        this.state.error = false;
        try {
            this.state.data = await this.orm.call(
                "helpdesk.mis.dashboard",
                "get_dashboard_data",
                [this.dashboard, {...this.state.filters}]
            );
        } catch (error) {
            this.state.error =
                (error && error.data && error.data.message) ||
                _t("The dashboard could not be loaded.");
        } finally {
            this.state.loading = false;
        }
    }

    onFilterChange(key, event) {
        const target = event.target;
        if (target.multiple) {
            const values = Array.from(target.selectedOptions).map((option) =>
                key === "priorities" ? option.value : Number(option.value)
            );
            this.state.filters[key] = values;
        } else {
            this.state.filters[key] = target.value;
        }
        this.load();
    }

    resetFilters() {
        const today = new Date();
        const firstOfMonth = new Date(today.getFullYear(), today.getMonth(), 1);
        Object.assign(this.state.filters, {
            date_from: this._toDate(firstOfMonth),
            date_to: this._toDate(today),
            date_basis: "create",
            granularity: "day",
            team_ids: [],
            category_ids: [],
            user_ids: [],
            priorities: [],
            state: "all",
            partner_id: false,
        });
        this.load();
    }

    formatValue(value, type) {
        if (type === "hours") {
            return formatHours(value);
        }
        if (type === "float") {
            return (Number(value) || 0).toFixed(2);
        }
        return value === false || value === null || value === undefined
            ? ""
            : value;
    }

    formatKpi(kpi) {
        return kpi.unit === "hours" ? formatHours(kpi.value) : kpi.value;
    }

    async drillDown(domain, title, model) {
        const action = await this.orm.call(
            "helpdesk.mis.dashboard",
            "action_drill_down",
            [domain, title, model || "helpdesk.mis.report"]
        );
        this.action.doAction(action);
    }

    onKpiClick(kpi) {
        this.drillDown(kpi.domain, kpi.label);
    }

    onRowClick(table, row) {
        if (row.ticket_id) {
            this.action.doAction({
                type: "ir.actions.act_window",
                res_model: "helpdesk.ticket",
                res_id: row.ticket_id,
                views: [[false, "form"]],
                target: "current",
            });
            return;
        }
        if (!table.group_field) {
            return;
        }
        const base = this.state.data.kpis.length
            ? this.state.data.kpis[0].domain
            : [];
        const domain = base.concat([[table.group_field, "=", row.id || false]]);
        this.drillDown(domain, `${table.title}: ${row.name}`);
    }

    openTable(table) {
        const model = table.model || "helpdesk.mis.report";
        const base = this.state.data.kpis.length
            ? this.state.data.kpis[0].domain
            : [];
        this.drillDown(
            model === "helpdesk.mis.report" ? base : [],
            table.title,
            model
        );
    }

    tableList(tables) {
        return Object.keys(tables || {}).map((key) => [key, tables[key]]);
    }

    chartList(charts) {
        return Object.keys(charts || {}).map((key) => [key, charts[key]]);
    }
}

registry
    .category("actions")
    .add("helpdesk_mis_dashboard", HelpdeskMisDashboard);
