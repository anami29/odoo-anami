/** @odoo-module **/
// Transport › Reporting › Profitability › Truck P&L: one row per truck for the month with its operating
// result; for the chosen truck its period statement, its trips and the bills behind them.

import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Layout } from "@web/search/layout";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";
import {
    TmsScopeBar,
    downloadCsv,
    inr,
    negative,
    openTarget,
    pct,
    resultClass,
} from "@tms_core/cockpit/cockpit_utils";

const CAP = 50000;

function sg(v) {
    const r = Math.round(Number(v) || 0);
    return r === 0 ? "₹0" : (r < 0 ? "−₹" : "+₹") + Math.abs(r).toLocaleString("en-IN");
}

export class TmsTruckPnl extends Component {
    static template = "tms_profitability.TmsTruckPnl";
    static components = { Layout, TmsScopeBar };
    static props = { ...standardActionServiceProps };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({ data: null, loading: false, cf: "all", trip: false });
        this.filters = { company_id: false, period: false, vehicle_id: false };
        Object.assign(this, { inr, negative, pct, resultClass, sg });
        onWillStart(() => this.load());
    }

    async load() {
        this.state.loading = true;
        this.state.data = await this.orm.call("tms.cockpit", "get_truck_pnl", [], {
            company_id: this.filters.company_id,
            period: this.filters.period,
            vehicle_id: this.filters.vehicle_id,
        });
        this.state.cf = "all";
        this.state.trip = false;
        this.state.loading = false;
    }

    onScope(change) {
        Object.assign(this.filters, change, { vehicle_id: false });
        this.load();
    }

    pick(row) {
        if (this.state.data.selected === row.id) {
            return;
        }
        this.filters.vehicle_id = row.id;
        this.load();
    }

    pickTrip(trip) {
        this.state.trip = this.state.trip === trip.id ? false : trip.id;
        this.state.cf = "trip";
    }

    setFilter(cf) {
        this.state.cf = cf;
        if (cf !== "trip") {
            this.state.trip = false;
        }
    }

    open(target) {
        return openTarget(this.action, target);
    }

    get facets() {
        return [{ k: "Group by", v: "Truck" }];
    }

    get rows() {
        return this.state.data.rows.map((r) => {
            const v = Math.max(-CAP, Math.min(CAP, r.result));
            const w = (Math.abs(v) / CAP) * 50;
            return {
                ...r,
                bl: v < 0 ? 50 - w : 50,
                bw: w,
                bc: v < 0 ? "#eb6834" : "#2a78d6",
                capped: Math.abs(r.result) > CAP,
                on: r.id === this.state.data.selected,
            };
        });
    }

    get tiles() {
        const t = this.state.data.totals;
        return {
            ...t,
            margin: t.revenue ? (t.result / t.revenue) * 100 : 0,
            trucks: this.state.data.rows.length,
        };
    }

    get bills() {
        const det = this.state.data.detail;
        if (!det) {
            return { rows: [], all: 0 };
        }
        let rows = det.bills;
        if (this.state.cf === "trip") {
            rows = rows.filter((b) => b.cls_key !== "truck" && (!this.state.trip || b.trip_id === this.state.trip));
        } else if (this.state.cf === "truck") {
            rows = rows.filter((b) => b.cls_key === "truck");
        }
        return { rows, all: det.bills.length };
    }

    get billNote() {
        const b = this.bills;
        if (!b.all) {
            return "No bills or expenses in the period.";
        }
        const trip = this.state.trip && this.state.data.detail.trips.find((t) => t.id === this.state.trip);
        return (trip ? `Showing ${trip.trip} only. ` : "") + `${b.rows.length} of ${b.all} lines.`;
    }

    exportCsv() {
        const d = this.state.data;
        downloadCsv(
            `truck-pnl-${d.scope.period.slice(0, 7)}.csv`,
            ["Truck", "Fleet", "Trips", "Revenue", "Trip expenses", "Replacement add-on", "Truck expenses",
                "Operating result"],
            d.rows.map((r) => [r.reg, r.fleet, r.trips, r.revenue.toFixed(2), r.trip_exp.toFixed(2),
                r.addon.toFixed(2), r.truck.toFixed(2), r.result.toFixed(2)])
        );
    }
}

registry.category("actions").add("tms_truck_pnl", TmsTruckPnl);
