/** @odoo-module **/
/* Copyright 2026 RLFB
 * License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
 *
 * Per-model store of Excel criteria and the operations on them.
 *
 * Filters stay personal, as o-spreadsheet makes them (each person filters
 * without hiding rows from colleagues editing the same sheet, which is what
 * Excel calls a Sheet View). Unlike stock o-spreadsheet, the criteria are
 * kept: they are saved per user and reapplied when the sheet is reopened.
 */

import * as spreadsheet from "@odoo/o-spreadsheet";
import {distinctItems, hiddenValuesFor} from "./criteria.esm";
import {SUBTOTAL_MODELS} from "./subtotal.esm";

const {numberToLetters} = spreadsheet.helpers;

const STORES = new WeakMap();

function storeOf(model) {
    if (!STORES.has(model)) {
        STORES.set(model, {criteria: new Map(), listeners: new Set()});
    }
    return STORES.get(model);
}

export function onCriteriaChange(model, callback) {
    const store = storeOf(model);
    store.listeners.add(callback);
    return () => store.listeners.delete(callback);
}

function notify(model) {
    for (const callback of storeOf(model).listeners) {
        callback();
    }
}

function key(sheetId, filterId) {
    return `${sheetId}::${filterId}`;
}

export function getFilterContext(model, position) {
    const getters = model.getters;
    const sheetId = position.sheetId || getters.getActiveSheetId();
    const filter = getters.getFilter({sheetId, col: position.col, row: position.row});
    if (!filter) {
        return null;
    }
    const table = getters.getTable({sheetId, col: position.col, row: position.row});
    const header = filter.rangeWithHeaders.zone.top;
    const headerCell = getters.getEvaluatedCell({sheetId, col: filter.col, row: header});
    return {
        sheetId,
        filter,
        table,
        headerRow: header,
        headerLabel:
            headerCell.formattedValue ||
            `Column ${numberToLetters(filter.col)}`,
    };
}

export function getCriteria(model, sheetId, filterId) {
    return storeOf(model).criteria.get(key(sheetId, filterId))?.criteria || null;
}

/** Rows hidden by the other filters of the sheet (Excel lists only those values). */
function rowsHiddenByOthers(model, sheetId, filterId) {
    const getters = model.getters;
    const hidden = new Set();
    for (const other of getters.getFilters(sheetId)) {
        if (other.id === filterId || !other.filteredRange) {
            continue;
        }
        const values = getters.getFilterHiddenValues({
            sheetId,
            col: other.col,
            row: other.rangeWithHeaders.zone.top,
        });
        if (!values.length) {
            continue;
        }
        const set = new Set(values.map((v) => v.toLowerCase()));
        const zone = other.filteredRange.zone;
        for (let row = zone.top; row <= zone.bottom; row++) {
            const value = getters
                .getEvaluatedCell({sheetId, col: other.col, row})
                .formattedValue.toLowerCase();
            if (set.has(value)) {
                hidden.add(row);
            }
        }
    }
    return hidden;
}

/** Cells of a filter column. */
export function columnCells(model, ctx, {respectOtherFilters = false} = {}) {
    const getters = model.getters;
    const zone = ctx.filter.filteredRange?.zone;
    if (!zone) {
        return [];
    }
    const others = respectOtherFilters
        ? rowsHiddenByOthers(model, ctx.sheetId, ctx.filter.id)
        : new Set();
    const cells = [];
    for (let row = zone.top; row <= zone.bottom; row++) {
        if (others.has(row) || getters.isRowHiddenByUser(ctx.sheetId, row)) {
            continue;
        }
        cells.push(getters.getEvaluatedCell({sheetId: ctx.sheetId, col: ctx.filter.col, row}));
    }
    return cells;
}

export function applyCriteria(model, ctx, criteria, {silent = false} = {}) {
    const store = storeOf(model);
    const k = key(ctx.sheetId, ctx.filter.id);
    let hiddenValues = [];
    if (criteria) {
        const items = distinctItems(columnCells(model, ctx));
        hiddenValues = hiddenValuesFor(items, criteria);
        store.criteria.set(k, {
            sheetId: ctx.sheetId,
            col: ctx.filter.col,
            row: ctx.headerRow,
            criteria,
        });
    } else {
        store.criteria.delete(k);
    }
    model.dispatch("UPDATE_FILTER", {
        sheetId: ctx.sheetId,
        col: ctx.filter.col,
        row: ctx.headerRow,
        hiddenValues,
    });
    if (SUBTOTAL_MODELS.has(model.getters)) {
        // SUBTOTAL depends on which rows are visible.
        model.dispatch("EVALUATE_CELLS", {sheetId: ctx.sheetId});
    }
    if (!silent) {
        notify(model);
    }
}

/** Excel "Reapply" (Ctrl+Alt+L): evaluate every stored criteria again. */
export function reapplyAll(model, sheetId) {
    for (const entry of [...storeOf(model).criteria.values()]) {
        if (sheetId && entry.sheetId !== sheetId) {
            continue;
        }
        const ctx = getFilterContext(model, entry);
        if (ctx) {
            applyCriteria(model, ctx, entry.criteria, {silent: true});
        }
    }
    notify(model);
}

/** Excel "Clear" (Data > Clear): show every row, keep the filter buttons. */
export function clearAll(model, sheetId) {
    const getters = model.getters;
    for (const filter of getters.getFilters(sheetId)) {
        const ctx = getFilterContext(model, {
            sheetId,
            col: filter.col,
            row: filter.rangeWithHeaders.zone.top,
        });
        if (ctx) {
            applyCriteria(model, ctx, null, {silent: true});
        }
    }
    notify(model);
}

/** "12 of 340 records found", Excel's status bar text in Filter mode. */
export function filterStats(model, sheetId) {
    const getters = model.getters;
    const filters = getters.getFilters(sheetId);
    let active = 0;
    const seen = new Set();
    let total = 0;
    let visible = 0;
    for (const filter of filters) {
        const pos = {sheetId, col: filter.col, row: filter.rangeWithHeaders.zone.top};
        if (getters.isFilterActive(pos)) {
            active++;
        }
        const zone = filter.filteredRange?.zone;
        if (!zone) {
            continue;
        }
        for (let row = zone.top; row <= zone.bottom; row++) {
            if (seen.has(row)) {
                continue;
            }
            seen.add(row);
            total++;
            if (!getters.isRowFiltered(sheetId, row)) {
                visible++;
            }
        }
    }
    return {active, total, visible};
}

/* ---------------- persistence (per user, per sheet) ---------------- */

export function exportCriteria(model) {
    return [...storeOf(model).criteria.values()].map((entry) => ({...entry}));
}

export function importCriteria(model, entries) {
    for (const entry of entries || []) {
        const ctx = getFilterContext(model, entry);
        if (ctx && entry.criteria) {
            applyCriteria(model, ctx, entry.criteria, {silent: true});
        }
    }
    notify(model);
}

/* ---------------- AutoFilter range, as Excel picks it ---------------- */

/**
 * Range an AutoFilter should cover for a selection.
 *  - one cell: the current region around it;
 *  - one row (several cells): those columns, from that row down to the end
 *    of the data below (selecting only the header row is the common case);
 *  - anything taller: the selection itself.
 */
export function autoFilterZone(getters, sheetId, selection) {
    if (selection.left === selection.right && selection.top === selection.bottom) {
        return getters.getContiguousZone(sheetId, selection);
    }
    if (selection.top === selection.bottom) {
        const region = getters.getContiguousZone(sheetId, selection);
        return {...selection, bottom: Math.max(selection.bottom, region.bottom)};
    }
    return selection;
}

/**
 * Excel re-detects an AutoFilter's range from the data. A filter made on a
 * header row only (o-spreadsheet's "Add filters" with the header row
 * selected) has nothing under it, so its list is empty: extend it over the
 * data below. Only static tables that are AutoFilters (no style) or have no
 * data row at all are touched; styled tables keep their size, as in Excel.
 * @returns {boolean} whether the range changed
 */
export function extendFilterToData(model, sheetId, table) {
    const getters = model.getters;
    if (!table || getters.isReadonly()) {
        return false;
    }
    const core = getters.getCoreTableMatchingTopLeft(sheetId, table.range.zone);
    if (!core || core.type === "dynamic") {
        return false;
    }
    const zone = table.range.zone;
    const headerOnly = zone.bottom - zone.top + 1 <= (table.config.numberOfHeaders || 0);
    if (!headerOnly && table.config.styleId !== "None") {
        return false;
    }
    const region = getters.getContiguousZone(sheetId, zone);
    if (region.bottom <= zone.bottom) {
        return false;
    }
    const result = model.dispatch("UPDATE_TABLE", {
        sheetId,
        zone,
        newTableRange: getters.getRangeDataFromZone(sheetId, {...zone, bottom: region.bottom}),
    });
    return result.isSuccessful;
}
