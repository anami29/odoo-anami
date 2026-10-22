/** @odoo-module **/
/* Copyright 2026 RLFB
 * License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
 *
 * Excel sort: several levels in one operation, Excel's type order (numbers
 * and dates, text, logicals, errors) with blank cells always last in both
 * directions, optional case sensitivity, stable for equal keys.
 *
 * o-spreadsheet's SORT_CELLS sorts by one column and, from the filter menu,
 * treats blanks as zero. XL_SORT is a UI command whose cell updates are
 * dispatched from inside the handler, so the whole sort is one revision for
 * collaborators and one step for Undo.
 */

import * as spreadsheet from "@odoo/o-spreadsheet";
import {classify, compareItems} from "./criteria.esm";

const {UIPlugin} = spreadsheet;
const {featurePluginRegistry} = spreadsheet.registries;

export class XlSortPlugin extends UIPlugin {
    static getters = ["getXlSortColumnKind"];

    allowDispatch(cmd) {
        if (cmd.type !== "XL_SORT") {
            return "Success";
        }
        if (!cmd.keys || !cmd.keys.length) {
            return "InvalidSortZone";
        }
        if (this.getters.doesIntersectMerge(cmd.sheetId, cmd.zone)) {
            return "InvalidSortZone";
        }
        for (const key of cmd.keys) {
            if (key.col < cmd.zone.left || key.col > cmd.zone.right) {
                return "InvalidSortZone";
            }
        }
        return "Success";
    }

    handle(cmd) {
        if (cmd.type === "XL_SORT") {
            this.sort(cmd);
        }
    }

    /** number | date | text, from the data rows of a column. */
    getXlSortColumnKind(sheetId, col, top, bottom) {
        const counts = {number: 0, date: 0, text: 0};
        for (let row = top; row <= bottom; row++) {
            const kind = classify(this.getters.getEvaluatedCell({sheetId, col, row})).kind;
            if (kind in counts) {
                counts[kind]++;
            }
        }
        if (counts.date && counts.date >= counts.number && counts.date >= counts.text) {
            return "date";
        }
        return counts.number && counts.number >= counts.text ? "number" : "text";
    }

    sort({sheetId, zone, keys, hasHeaders, caseSensitive}) {
        const top = zone.top + (hasHeaders ? 1 : 0);
        if (top > zone.bottom) {
            return;
        }
        const rows = [];
        for (let row = top; row <= zone.bottom; row++) {
            rows.push(row);
        }
        const keyItems = keys.map((key) => {
            const items = new Map();
            for (const row of rows) {
                items.set(
                    row,
                    classify(this.getters.getEvaluatedCell({sheetId, col: key.col, row}))
                );
            }
            return items;
        });
        const order = rows
            .map((row, index) => ({row, index}))
            .sort((a, b) => {
                for (let k = 0; k < keys.length; k++) {
                    const ia = keyItems[k].get(a.row);
                    const ib = keyItems[k].get(b.row);
                    // Blanks last whatever the direction, as in Excel.
                    if (ia.kind === "blank" || ib.kind === "blank") {
                        if (ia.kind !== ib.kind) {
                            return ia.kind === "blank" ? 1 : -1;
                        }
                        continue;
                    }
                    const diff = compareItems(ia, ib, caseSensitive);
                    if (diff) {
                        return keys[k].descending ? -diff : diff;
                    }
                }
                return a.index - b.index;
            })
            .map((entry) => entry.row);

        if (order.every((row, i) => row === rows[i])) {
            return;
        }
        const updates = [];
        for (let col = zone.left; col <= zone.right; col++) {
            order.forEach((sourceRow, i) => {
                const targetRow = top + i;
                const cell = this.getters.getCell({sheetId, col, row: sourceRow});
                const payload = {sheetId, col, row: targetRow, content: ""};
                if (cell) {
                    let content = cell.content;
                    if (cell.isFormula) {
                        content = this.getters.getTranslatedCellFormula(
                            sheetId,
                            0,
                            targetRow - sourceRow,
                            cell.compiledFormula.tokens
                        );
                    }
                    payload.content = content;
                    payload.style = cell.style;
                    payload.format = cell.format;
                }
                updates.push(payload);
            });
        }
        for (const payload of updates) {
            this.dispatch("UPDATE_CELL", payload);
        }
    }
}

featurePluginRegistry.add("xl_sort", XlSortPlugin);
