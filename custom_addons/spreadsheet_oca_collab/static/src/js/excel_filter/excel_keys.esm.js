/** @odoo-module **/
/* Copyright 2026 RLFB
 * License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
 *
 * Excel keyboard and menu behaviour for filters and sorting:
 *   Ctrl+Shift+L  turn AutoFilter on/off (o-spreadsheet: align left)
 *   Alt+Down      open the filter dropdown of the active header cell
 *   Ctrl+Alt+L    reapply all filters of the sheet
 * and an AutoFilter that does not restyle the range as a coloured table.
 */

import * as spreadsheet from "@odoo/o-spreadsheet";
import {_t} from "@web/core/l10n/translation";
import {patch} from "@web/core/utils/patch";
import {applyCriteria, clearAll, getFilterContext, reapplyAll} from "./filter_state.esm";

const {Grid} = spreadsheet.components;
const {topbarMenuRegistry} = spreadsheet.registries;

const AUTOFILTER_CONFIG = {
    hasFilters: true,
    totalRow: false,
    firstColumn: false,
    lastColumn: false,
    numberOfHeaders: 1,
    bandedRows: false,
    bandedColumns: false,
    automaticAutofill: true,
    styleId: "None",
};

function clearTableCriteria(model, sheetId, table) {
    for (const filter of table.filters || []) {
        const ctx = getFilterContext(model, {
            sheetId,
            col: filter.col,
            row: filter.rangeWithHeaders.zone.top,
        });
        if (ctx) {
            applyCriteria(model, ctx, null, {silent: true});
        }
    }
}

export function toggleAutoFilter(env) {
    const model = env.model;
    const getters = model.getters;
    if (getters.isReadonly()) {
        return;
    }
    const sheetId = getters.getActiveSheetId();
    const table = getters.getFirstTableInSelection();
    if (table && table.config.hasFilters) {
        clearTableCriteria(model, sheetId, table);
        if (table.config.styleId === "None") {
            // Created by AutoFilter: remove it entirely, as Excel does.
            model.dispatch("REMOVE_TABLE", {sheetId, target: [table.range.zone]});
        } else {
            model.dispatch("UPDATE_TABLE", {
                sheetId,
                zone: table.range.zone,
                config: {hasFilters: false},
            });
        }
        return;
    }
    if (table) {
        model.dispatch("UPDATE_TABLE", {
            sheetId,
            zone: table.range.zone,
            config: {hasFilters: true},
        });
        return;
    }
    let zone = getters.getSelectedZone();
    if (zone.left === zone.right && zone.top === zone.bottom) {
        zone = getters.getContiguousZone(sheetId, zone);
    }
    if (zone.top === zone.bottom) {
        env.raiseError?.(_t("Select a range with a header row and at least one row of data."));
        return;
    }
    const result = model.dispatch("CREATE_TABLE", {
        sheetId,
        ranges: [getters.getRangeDataFromZone(sheetId, zone)],
        config: AUTOFILTER_CONFIG,
        tableType: "static",
    });
    if (!result.isSuccessful) {
        env.raiseError?.(_t("A filter can't be added here: the range overlaps another table."));
    }
}

patch(Grid.prototype, {
    setup() {
        super.setup(...arguments);
        const env = this.env;
        Object.assign(this.keyDownMapping, {
            "Ctrl+Shift+L": () => toggleAutoFilter(env),
            "Ctrl+Alt+L": () => reapplyAll(env.model, env.model.getters.getActiveSheetId()),
            "Alt+ArrowDown": () => {
                const getters = env.model.getters;
                const pos = getters.getActivePosition();
                const filter = getters.getFilter(pos);
                if (filter && filter.rangeWithHeaders.zone.top === pos.row) {
                    this.cellPopovers.open({col: pos.col, row: pos.row}, "FilterMenu");
                }
            },
        });
    },
});

/* ------------------------------- menus ------------------------------- */
topbarMenuRegistry.addChild(
    "add_remove_data_filter",
    ["data"],
    {
        name: (env) =>
            env.model.getters.getFirstTableInSelection()?.config.hasFilters
                ? _t("Remove filter")
                : _t("Filter"),
        description: "Ctrl+Shift+L",
        sequence: 40,
        separator: false,
        execute: (env) => toggleAutoFilter(env),
        isVisible: (env) => !env.model.getters.isReadonly(),
        icon: "o-spreadsheet-Icon.FILTER_ICON_ACTIVE",
    },
    {force: true}
);
topbarMenuRegistry.addChild("xl_clear_filters", ["data"], {
    name: _t("Clear filters"),
    sequence: 41,
    isReadonlyAllowed: true,
    execute: (env) => clearAll(env.model, env.model.getters.getActiveSheetId()),
});
topbarMenuRegistry.addChild("xl_reapply_filters", ["data"], {
    name: _t("Reapply filters"),
    description: "Ctrl+Alt+L",
    sequence: 42,
    separator: true,
    isReadonlyAllowed: true,
    execute: (env) => reapplyAll(env.model, env.model.getters.getActiveSheetId()),
});
topbarMenuRegistry.addChild("xl_custom_sort", ["data", "sort_range"], {
    name: _t("Custom sort…"),
    sequence: 30,
    execute: (env) => env.openSidePanel("XlCustomSort", {}),
});
