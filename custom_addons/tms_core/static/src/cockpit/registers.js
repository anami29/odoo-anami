/** @odoo-module **/
// Master Data › Compliance register and Trip money › Charges & expenses register: one page each, as drawn in the
// agreed mockup (TMS-UIG-001, 18.0.1.6.0).

import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Layout } from "@web/search/layout";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";
import { TmsScopeBar, inr, openTarget } from "./cockpit_utils";

const CELL_BADGE = { valid: "ok", expiring: "warn", expired: "bad", missing: "bad" };
const GATE_BADGE = { valid: "b-success", expiring: "b-warning", blocked: "b-danger" };

export class TmsComplianceRegister extends Component {
    static template = "tms_core.TmsComplianceRegister";
    static components = { Layout };
    static props = { ...standardActionServiceProps };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({ data: null, filter: "all", loading: false });
        this.companyId = false;
        this.cellClass = (s) => CELL_BADGE[s] || "";
        this.gateClass = (s) => GATE_BADGE[s] || "b-muted";
        onWillStart(() => this.load());
    }

    async load() {
        this.state.loading = true;
        this.state.data = await this.orm.call("tms.cockpit", "get_compliance_register", [], {
            company_id: this.companyId,
        });
        this.state.loading = false;
    }

    onBranch(ev) {
        this.companyId = parseInt(ev.target.value, 10);
        this.load();
    }

    keep(row) {
        if (this.state.filter === "expired") {
            return ["expired", "missing"].includes(row.worst) || row.gate_state === "blocked";
        }
        if (this.state.filter === "expiring") {
            return row.worst === "expiring";
        }
        return true;
    }

    get vehicles() {
        return this.state.data.vehicles.filter((r) => this.keep(r));
    }

    get drivers() {
        return this.state.data.drivers.filter((r) => this.keep(r));
    }

    open(target) {
        return openTarget(this.action, target);
    }

    async renew(model, row) {
        const act = await this.orm.call(model, "action_renew_document", [[row.id]], { type_code: row.renew });
        await this.action.doAction(act, { onClose: () => this.load() });
    }
}

export class TmsChargesRegister extends Component {
    static template = "tms_core.TmsChargesRegister";
    static components = { Layout, TmsScopeBar };
    static props = { ...standardActionServiceProps };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({ data: null, loading: false });
        this.filters = { company_id: false, period: false, pending_only: false };
        this.inr = inr;
        onWillStart(() => this.load());
    }

    async load() {
        this.state.loading = true;
        this.state.data = await this.orm.call("tms.cockpit", "get_charges_register", [], { ...this.filters });
        this.state.loading = false;
    }

    onScope(change) {
        Object.assign(this.filters, change);
        this.load();
    }

    togglePending() {
        this.filters.pending_only = !this.filters.pending_only;
        this.load();
    }

    open(target) {
        return openTarget(this.action, target);
    }

    tileValue(tile) {
        return tile.count ? String(tile.value) + (tile.value === 1 ? " line" : " lines") : inr(tile.value);
    }

    billingClass(key) {
        return { to_invoice: "b-info", to_deduct: "b-info", on_draft: "b-warning", invoiced: "b-success",
                 deducted: "b-success", waived: "b-muted" }[key] || "b-muted";
    }
}

registry.category("actions").add("tms_compliance_register", TmsComplianceRegister);
registry.category("actions").add("tms_charges_register", TmsChargesRegister);
