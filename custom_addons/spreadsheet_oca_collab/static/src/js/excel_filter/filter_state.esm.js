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
