/** @odoo-module **/
/* Copyright 2026 RLFB
 * License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
 *
 * Data > Sort > Custom Sort…: Excel's multi-level Sort dialog as a side panel.
 */

import * as spreadsheet from "@odoo/o-spreadsheet";
import {Component, useState} from "@odoo/owl";
import {_t} from "@web/core/l10n/translation";

const {sidePanelRegistry} = spreadsheet.registries;
const {numberToLetters, toXC} = spreadsheet.helpers;

const ORDER = {
    text: [_t("A to Z"), _t("Z to A")],
    number: [_t("Smallest to Largest"), _t("Largest to Smallest")],
    date: [_t("Oldest to Newest"), _t("Newest to Oldest")],
};

let nextId = 1;

export class XlSortPanel extends Component {
    static template = "spreadsheet_oca_collab.XlSortPanel";
    static props = {onCloseSidePanel: Function};

    setup() {
        const getters = this.env.model.getters;
        this.sheetId = getters.getActiveSheetId();
        const {zone, hasHeaders} = this.computeTarget();
        this.zone = zone;
        const anchor = getters.getActivePosition?.() || {col: zone.left};
        const firstCol =
            anchor.col >= zone.left && anchor.col <= zone.right ? anchor.col : zone.left;
        this.state = useState({
            hasHeaders,
            caseSensitive: false,
            selected: 0,
            error: "",
            levels: [{id: nextId++, col: firstCol, descending: false}],
            version: 0,
        });
    }

    /** Excel: a table, else the selection, else the current region. */
    computeTarget() {
        const getters = this.env.model.getters;
        const selection = getters.getSelectedZone();
        const table = getters.getTablesOverlappingZones(this.sheetId, [selection])[0];
        if (table) {
            return {
                zone: table.range.zone,
                hasHeaders: (table.config.numberOfHeaders || 0) > 0,
            };
        }
        let zone = selection;
        if (zone.left === zone.right && zone.top === zone.bottom) {
            zone = getters.getContiguousZone(this.sheetId, zone);
        }
        return {zone, hasHeaders: this.guessHeaders(zone)};
    }

    /** Same idea as Excel: a first row of text above a different type below. */
    guessHeaders(zone) {
        if (zone.top === zone.bottom) {
            return false;
        }
        const getters = this.env.model.getters;
        let differs = false;
        for (let col = zone.left; col <= zone.right; col++) {
            const head = getters.getEvaluatedCell({sheetId: this.sheetId, col, row: zone.top});
            const next = getters.getEvaluatedCell({sheetId: this.sheetId, col, row: zone.top + 1});
            if (head.type === "empty") {
                return false;
            }
            if (next.type !== "empty" && head.type !== next.type) {
                differs = true;
            }
        }
        return differs;
    }

    get rangeLabel() {
        const z = this.zone;
        return `${toXC(z.left, z.top)}:${toXC(z.right, z.bottom)}`;
    }

    get columns() {
        void this.state.version;
        const getters = this.env.model.getters;
        const cols = [];
        for (let col = this.zone.left; col <= this.zone.right; col++) {
            let label = _t("Column %s", numberToLetters(col));
            if (this.state.hasHeaders) {
                const head = getters.getEvaluatedCell({
                    sheetId: this.sheetId,
                    col,
                    row: this.zone.top,
                }).formattedValue;
                if (head) {
                    label = head;
                }
            }
            cols.push({col, label});
        }
        return cols;
    }

    orderLabels(col) {
        const top = this.zone.top + (this.state.hasHeaders ? 1 : 0);
        const kind = this.env.model.getters.getXlSortColumnKind(
            this.sheetId,
            col,
            top,
            this.zone.bottom
        );
        return ORDER[kind];
    }

    refreshColumns() {
        this.state.version++;
    }
    setLevelCol(level, value) {
        level.col = Number(value);
    }
    addLevel() {
        const used = new Set(this.state.levels.map((l) => l.col));
        const free = this.columns.find((c) => !used.has(c.col)) || this.columns[0];
        this.state.levels.push({id: nextId++, col: free.col, descending: false});
        this.state.selected = this.state.levels.length - 1;
    }
    deleteLevel() {
        if (this.state.levels.length <= 1) {
            return;
        }
        this.state.levels.splice(this.state.selected, 1);
        this.state.selected = Math.max(0, this.state.selected - 1);
    }
    copyLevel() {
        const level = this.state.levels[this.state.selected];
        this.state.levels.splice(this.state.selected + 1, 0, {...level, id: nextId++});
        this.state.selected++;
    }
    moveLevel(delta) {
        const i = this.state.selected;
        const j = i + delta;
        if (j < 0 || j >= this.state.levels.length) {
            return;
        }
        const levels = this.state.levels;
        [levels[i], levels[j]] = [levels[j], levels[i]];
        this.state.selected = j;
    }

    apply() {
        const result = this.env.model.dispatch("XL_SORT", {
            sheetId: this.sheetId,
            zone: this.zone,
            hasHeaders: this.state.hasHeaders,
            caseSensitive: this.state.caseSensitive,
            keys: this.state.levels.map((l) => ({col: l.col, descending: l.descending})),
        });
        if (!result.isSuccessful) {
            this.state.error = _t(
                "This range can't be sorted: it contains merged cells of different sizes."
            );
            return;
        }
        this.state.error = "";
        this.props.onCloseSidePanel();
    }
}

sidePanelRegistry.add("XlCustomSort", {
    title: _t("Sort"),
    Body: XlSortPanel,
});
