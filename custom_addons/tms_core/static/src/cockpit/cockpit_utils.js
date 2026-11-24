/** @odoo-module **/
// Shared by the exception dashboard and the P&L pages: Indian-grouped rupees, signed figures,
// the branch and month scope bar, and opening the records the pages link to.

import { Component, useState } from "@odoo/owl";

const MINUS = "−";

export function inr(value, { decimals = 0 } = {}) {
    const v = Number(value) || 0;
    const abs = Math.abs(v).toLocaleString("en-IN", {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
    });
    return (v < 0 && Math.round(Math.abs(v)) !== 0 ? MINUS : "") + "₹" + abs;
}

export function signed(value) {
    const v = Math.round(Number(value) || 0);
    if (v === 0) {
        return "±₹0";
    }
    return (v < 0 ? MINUS : "+") + "₹" + Math.abs(v).toLocaleString("en-IN");
}

export function negative(value) {
    const v = Math.round(Number(value) || 0);
    return v === 0 ? "₹0" : MINUS + "₹" + Math.abs(v).toLocaleString("en-IN");
}

export function pct(value) {
    const v = Number(value) || 0;
    return (v < 0 ? MINUS : "") + Math.abs(v).toFixed(1) + "%";
}

export function signedPct(value) {
    const v = Number(value) || 0;
    if (Math.abs(v) < 0.05) {
        return "±0.0%";
    }
    return (v < 0 ? MINUS : "+") + Math.abs(v).toFixed(1) + "%";
}

/** Server texts carry {{amount:123.4}} and {{signed:-50}} for figures; the page formats them. */
export function fillText(text) {
    return (text || "")
        .replace(/\{\{amount:(-?[\d.eE+-]+)\}\}/g, (_m, v) => inr(parseFloat(v)))
        .replace(/\{\{signed:(-?[\d.eE+-]+)\}\}/g, (_m, v) => signed(parseFloat(v)));
}

/** Cost-like figures read bad when they go up; revenue-like figures read bad when they go down. */
export function trendClass(delta, costLike) {
    if (Math.abs(delta) < 0.5) {
        return "flat";
    }
    return delta > 0 === costLike ? "up" : "dn";
}

export function resultClass(value) {
    const v = Math.round(Number(value) || 0);
    return v === 0 ? "text-muted" : v < 0 ? "o_tms_neg" : "o_tms_pos";
}

export function openTarget(action, target) {
    if (!target) {
        return;
    }
    if (target.id) {
        return action.doAction({
            type: "ir.actions.act_window",
            res_model: target.model,
            res_id: target.id,
            views: [[false, "form"]],
            target: "current",
        });
    }
    return action.doAction({
        type: "ir.actions.act_window",
        name: target.name,
        res_model: target.model,
        domain: target.domain || [],
        views: [
            [false, "list"],
            [false, "form"],
        ],
        target: "current",
    });
}

export function downloadCsv(filename, header, rows) {
    const esc = (v) => {
        const s = v === null || v === undefined ? "" : String(v);
        return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
    };
    const body = [header, ...rows].map((r) => r.map(esc).join(",")).join("\n");
    const blob = new Blob(["﻿" + body], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
}

/** Branch and month facets in the control panel, styled like Odoo's search facets. */
export class TmsScopeBar extends Component {
    static template = "tms_core.TmsScopeBar";
    static props = {
        scope: Object,
        extra: { type: Array, optional: true },
        onChange: Function,
    };

    setup() {
        this.state = useState({ editing: false });
    }

    get month() {
        return (this.props.scope.period || "").slice(0, 7);
    }

    onBranch(ev) {
        this.props.onChange({ company_id: parseInt(ev.target.value, 10) });
    }

    onMonth(ev) {
        if (ev.target.value) {
            this.props.onChange({ period: ev.target.value + "-01" });
        }
    }

    shift(step) {
        const [y, m] = this.month.split("-").map((x) => parseInt(x, 10));
        const d = new Date(Date.UTC(y, m - 1 + step, 1));
        const iso = d.toISOString().slice(0, 7);
        this.props.onChange({ period: iso + "-01" });
    }
}
