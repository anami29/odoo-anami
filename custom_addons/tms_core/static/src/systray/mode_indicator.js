/** @odoo-module **/
// The branch's operating mode, shown in the top bar ("Mode: Hybrid").
import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

export class TmsModeIndicator extends Component {
    static template = "tms_core.ModeIndicator";
    static props = {};

    setup() {
        this.orm = useService("orm");
        this.company = useService("company");
        this.state = useState({ label: "" });
        onWillStart(async () => {
            try {
                this.state.label = (await this.orm.call("res.company", "tms_mode_label",
                    [[this.company.currentCompany.id]])) || "";
            } catch {
                this.state.label = "";
            }
        });
    }
}

registry.category("systray").add("tms_core.mode_indicator", { Component: TmsModeIndicator }, { sequence: 95 });
