/** @odoo-module **/
// Transport › Dashboard › Exception dashboard: tiles, exceptions to act on, trip contribution,
// unbilled charges and recoveries, the month's breakdowns. Replaces the KPI board (TMS-UIG-001 #7, decision 3).

import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Layout } from "@web/search/layout";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";
import { TmsScopeBar, fillText, inr, openTarget, signed } from "./cockpit_utils";

/** Round axis ticks for a value range that always includes zero. */
export function niceAxis(values) {
    const lo = Math.min(0, ...values);
    const hi = Math.max(0, ...values);
    const span = hi - lo || 1;
    const raw = span / 4;
    const mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) || mag * 10;
    const min = Math.floor(lo / step) * step;
    const max = Math.ceil(hi / step) * step || step;
    const ticks = [];
    for (let t = min; t <= max + step / 2; t += step) {
        ticks.push(t);
    }
    return { min, max, ticks };
}

export function shortK(v) {
    if (v === 0) {
        return "0";
    }
    const a = Math.abs(v);
    const s = a >= 100000 ? +(a / 100000).toFixed(1) + "L" : a >= 1000 ? +(a / 1000).toFixed(1) + "k" : String(a);
    return (v < 0 ? "−" : "") + s;
}

export class TmsExceptionDashboard extends Component {
    static template = "tms_core.TmsExceptionDashboard";
    static components = { Layout, TmsScopeBar };
    static props = { ...standardActionServiceProps };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({ data: null, hover: -1, loading: false });
        this.filters = { company_id: false, period: false };
        this.inr = inr;
        this.signed = signed;
        this.fillText = fillText;
        onWillStart(() => this.load());
    }

    async load() {
        this.state.loading = true;
        this.state.data = await this.orm.call("tms.cockpit", "get_exception_dashboard", [], {
            company_id: this.filters.company_id,
            period: this.filters.period,
        });
        this.state.loading = false;
    }

    onScope(change) {
        Object.assign(this.filters, change);
        this.load();
    }

    open(target) {
        return openTarget(this.action, target);
    }

    tileValue(tile) {
        return tile.is_amount ? inr(tile.value) : String(tile.value);
    }

    tileSub(tile) {
        if (tile.key === "claims" && tile.sub_prefix_amount) {
            return `${inr(tile.amount_sub)} exposure · ${tile.sub}`;
        }
        if (tile.key === "unbilled") {
            return `${inr(tile.customer)} customer charges · ${inr(tile.vendor)} vendor recoveries`;
        }
        if (tile.key === "pod" && tile.held_amount) {
            return `${tile.sub} ${inr(tile.held_amount)}`;
        }
        return tile.sub;
    }

    get chart() {
        const c = this.state.data.contribution;
        if (!c || !c.bars.length) {
            return null;
        }
        const axis = niceAxis(c.bars.map((b) => b.value));
        const span = axis.max - axis.min;
        const x = (v) => ((v - axis.min) / span) * 100;
        const zero = x(0);
        const bars = c.bars.map((b, i) => {
            const w = Math.max(0.6, Math.abs(x(b.value) - zero));
            const neg = b.value < 0;
            return {
                ...b,
                i,
                left: neg ? zero - w : zero,
                width: w,
                cls: (neg ? "neg" : "pos") + (b.open ? " open" : ""),
                label: signed(b.value) + (b.open ? " · open" : ""),
            };
        });
        return {
            bars,
            zero,
            ticks: axis.ticks.map((t) => ({ x: x(t), label: shortK(t), zero: t === 0 })),
        };
    }

    get hovered() {
        const c = this.chart;
        return c && this.state.hover >= 0 ? c.bars[this.state.hover] : null;
    }
}

registry.category("actions").add("tms_exception_dashboard", TmsExceptionDashboard);
