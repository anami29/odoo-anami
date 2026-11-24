/** @odoo-module **/
// Sales › Reporting › Transport › Sales order P&L: planned against actual per sales order, the margin
// moving from plan to actual, and for the chosen order its cost elements and trips.

import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Layout } from "@web/search/layout";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";
import {
    TmsScopeBar,
    downloadCsv,
    fillText,
    inr,
    openTarget,
    pct,
    signed,
    signedPct,
    trendClass,
} from "@tms_core/cockpit/cockpit_utils";

export class TmsSoPnl extends Component {
    static template = "tms_profitability.TmsSoPnl";
    static components = { Layout, TmsScopeBar };
    static props = { ...standardActionServiceProps };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({ data: null, hover: -1, loading: false });
        this.filters = { company_id: false, period: false, order_key: false };
        Object.assign(this, { inr, pct, signed, signedPct, trendClass, fillText });
        onWillStart(() => this.load());
    }

    async load() {
        this.state.loading = true;
        this.state.data = await this.orm.call("tms.cockpit", "get_so_pnl", [], {
            company_id: this.filters.company_id,
            period: this.filters.period,
            order_key: this.filters.order_key,
        });
        this.state.hover = -1;
        this.state.loading = false;
    }

    onScope(change) {
        Object.assign(this.filters, change, { order_key: false });
        this.load();
    }

    pick(row) {
        if (this.state.data.selected === row.key) {
            return;
        }
        this.filters.order_key = row.key;
        this.load();
    }

    open(target) {
        return openTarget(this.action, target);
    }

    get facets() {
        const d = this.state.data;
        return [
            { k: "Trips", v: "Started in " + d.scope.period_label },
            { k: "Group by", v: "Sales Order" },
        ];
    }

    get dumbbell() {
        const rows = this.state.data.rows;
        const vals = rows.flatMap((r) => (r.planned ? [r.m_p, r.m_a] : [r.m_a]));
        const lo = Math.min(-20, Math.floor(Math.min(...vals, 0) / 20) * 20);
        const hi = Math.max(60, Math.ceil(Math.max(...vals, 0) / 20) * 20);
        const X = (v) => ((Math.max(lo, Math.min(hi, v)) - lo) / (hi - lo)) * 100;
        const ticks = [];
        const step = hi - lo > 80 ? 20 : 10;
        for (let t = lo; t <= hi; t += step) {
            ticks.push({ x: X(t), label: t + "%", zero: t === 0 });
        }
        const items = rows.map((r) => {
            const a = X(r.m_a);
            const p = r.planned ? X(r.m_p) : a;
            return {
                key: r.key,
                so: r.so,
                customer: r.customer,
                planned: r.planned,
                a,
                p,
                l: Math.min(a, p),
                w: Math.abs(a - p),
                worse: r.m_a < r.m_p,
                mA: pct(r.m_a),
                mP: r.planned ? pct(r.m_p) : "—",
                on: r.key === this.state.data.selected,
            };
        });
        return { ticks, items, height: rows.length * 38 };
    }

    get bullets() {
        const det = this.state.data.detail;
        if (!det || !det.elements.length) {
            return [];
        }
        const mx = Math.max(...det.elements.map((e) => Math.max(e.planned, e.actual)), 1) * 1.04;
        return det.elements.map((e, i) => {
            const d = e.actual - e.planned;
            return {
                ...e,
                i,
                d,
                aw: (Math.min(e.actual, e.planned) / mx) * 100,
                ox: (e.planned / mx) * 100,
                ow: (Math.max(0, e.actual - e.planned) / mx) * 100,
                px: (e.planned / mx) * 100,
                dcls: trendClass(d, true),
            };
        });
    }

    get hoverBullet() {
        return this.state.hover >= 0 ? this.bullets[this.state.hover] : null;
    }

    exportCsv() {
        const d = this.state.data;
        const rows = d.rows.map((r) => [
            r.so,
            r.customer,
            r.type,
            r.trips,
            r.rev_a.toFixed(2),
            r.planned ? r.rev_p.toFixed(2) : "",
            r.cost_a.toFixed(2),
            r.planned ? r.cost_p.toFixed(2) : "",
            r.planned ? r.var.toFixed(2) : "",
            r.con_a.toFixed(2),
            r.m_a.toFixed(1),
            r.planned ? r.m_p.toFixed(1) : "",
        ]);
        downloadCsv(
            `sales-order-pnl-${d.scope.period.slice(0, 7)}.csv`,
            ["Sales order", "Customer", "Type", "Trips", "Revenue", "Planned revenue", "Direct cost",
                "Planned cost", "Cost variance", "Contribution", "Margin %", "Planned margin %"],
            rows
        );
    }
}

registry.category("actions").add("tms_so_pnl", TmsSoPnl);
