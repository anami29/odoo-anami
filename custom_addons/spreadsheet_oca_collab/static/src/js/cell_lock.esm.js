/** @odoo-module **/
/* Copyright 2026 RLFB
 * License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
 *
 * Typing lock: while someone is typing in a cell, that cell is read-only for
 * everyone else, and a "<name> is typing" tag shows on it.
 *
 * The browser claims the cell from the server (spreadsheet.oca.cell.lock)
 * when its cell editor opens and releases it when the editor closes; the
 * server decides who started first and broadcasts the holders. Here the
 * browser:
 *  - refuses to open the cell editor on a cell held by someone else;
 *  - refuses any local edit (paste, delete, format, sort, fill, row/column
 *    removal, ...) that would change such a cell;
 *  - never touches edits arriving from other people: those do not go
 *    through model.dispatch.
 */

import * as spreadsheet from "@odoo/o-spreadsheet";
import {Component} from "@odoo/owl";
import {patch} from "@web/core/utils/patch";

const {DispatchResult} = spreadsheet;
const {CellComposerStore} = spreadsheet.stores;
const {Grid} = spreadsheet.components;
const {toXC} = spreadsheet.helpers;

const STATES = new WeakMap(); // getters -> state

export function lockState(getters) {
    return STATES.get(getters);
}

export function initLockState(model, {enabled, me, notifyBlocked, onComposer, locks}) {
    const state = {
        enabled: Boolean(enabled),
        me,
        notifyBlocked,
        onComposer,
        locks: new Map(),
    };
    STATES.set(model.getters, state);
    setLocks(model, locks || []);
    return state;
}

const key = (sheetId, col, row) => `${sheetId}|${col}|${row}`;

export function setLocks(model, list) {
    const state = STATES.get(model.getters);
    if (!state) {
        return;
    }
    state.locks = new Map(list.map((l) => [key(l.sheetId, l.col, l.row), l]));
}

/** Holder of a cell if it is someone other than this browser, else null. */
export function lockedBy(getters, sheetId, col, row) {
    const state = STATES.get(getters);
    if (!state || !state.enabled) {
        return null;
    }
    const holder = state.locks.get(key(sheetId, col, row));
    return holder && holder.clientId !== state.me() ? holder : null;
}

export function describeLock(holder) {
    return `${toXC(holder.col, holder.row)}`;
}

/* ------------------------------------------------------------------ */
/* Which cells a local command would change                            */
/* ------------------------------------------------------------------ */
const cell = (p) => [{left: p.col, right: p.col, top: p.row, bottom: p.row}];
const targets = (p) => p.target || [];
const BIG = 1e7;

const AFFECTED = {
    UPDATE_CELL: cell,
    CLEAR_CELL: cell,
    CLEAR_CELLS: targets,
    DELETE_CONTENT: targets,
    SET_FORMATTING: targets,
    SET_FORMATTING_WITH_PIVOT: targets,
    CLEAR_FORMATTING: targets,
    SET_ZONE_BORDERS: targets,
    SET_BORDERS_ON_TARGET: targets,
    SET_DECIMAL: targets,
    ADD_MERGE: targets,
    REMOVE_MERGE: targets,
    PASTE: targets,
    PASTE_FROM_OS_CLIPBOARD: targets,
    INSERT_CELL: (p) =>
        p.shiftDimension === "COL"
            ? [{...p.zone, right: BIG}]
            : [{...p.zone, bottom: BIG}],
    DELETE_CELL: (p) =>
        p.shiftDimension === "COL"
            ? [{...p.zone, right: BIG}]
            : [{...p.zone, bottom: BIG}],
    SORT_CELLS: (p) => [p.zone],
    // A sort with headers never rewrites the header row.
    XL_SORT: (p) => [{...p.zone, top: p.zone.top + (p.hasHeaders ? 1 : 0)}],
    REMOVE_COLUMNS_ROWS: (p) =>
        p.elements.map((i) =>
            p.dimension === "COL"
                ? {left: i, right: i, top: 0, bottom: BIG}
                : {left: 0, right: BIG, top: i, bottom: i}
        ),
    DELETE_SHEET: () => [{left: 0, right: BIG, top: 0, bottom: BIG}],
};
// Commands that act on the current selection rather than a payload target.
const ON_SELECTION = new Set(["CUT", "AUTOFILL", "AUTOFILL_AUTO"]);

function firstLockIn(getters, sheetId, zones) {
    const state = STATES.get(getters);
    if (!state || !state.enabled || !state.locks.size) {
        return null;
    }
    const me = state.me();
    for (const holder of state.locks.values()) {
        if (holder.clientId === me || holder.sheetId !== sheetId) {
            continue;
        }
        for (const z of zones) {
            if (
                z &&
                holder.col >= z.left &&
                holder.col <= z.right &&
                holder.row >= z.top &&
                holder.row <= z.bottom
            ) {
                return holder;
            }
        }
    }
    return null;
}

/**
 * Put a lock check in front of model.dispatch for edits started by this
 * user. Plugins keep the original dispatch (captured at construction), and
 * remote revisions are applied without dispatch, so neither is affected.
 */
export function installLockGuard(model) {
    const original = model.dispatch;
    model.dispatch = (type, payload = {}) => {
        if (model.status === 0) {
            const getters = model.getters;
            const sheetId = payload.sheetId || getters.getActiveSheetId();
            let zones = null;
            if (AFFECTED[type]) {
                zones = AFFECTED[type](payload);
            }
            if (ON_SELECTION.has(type)) {
                zones = [...(zones || []), ...getters.getSelectedZones()];
            }
            if (type === "AUTOFILL_SELECT") {
                const z = getters.getSelectedZone();
                zones = [
                    {
                        left: Math.min(z.left, payload.col),
                        right: Math.max(z.right, payload.col),
                        top: Math.min(z.top, payload.row),
                        bottom: Math.max(z.bottom, payload.row),
                    },
                ];
            }
            if (zones) {
                const holder = firstLockIn(getters, sheetId, zones);
                if (holder) {
                    STATES.get(getters).notifyBlocked?.(holder);
                    model.trigger("update");
                    return new DispatchResult("CellLockedByOtherUser");
                }
            }
        }
        return original(type, payload);
    };
}

/* Typing, F2, Enter, double-click and the formula bar all open the editor here. */
patch(CellComposerStore.prototype, {
    startEdition(text, selection) {
        const getters = this.getters;
        const pos = getters.getActivePosition();
        const holder = lockedBy(getters, pos.sheetId, pos.col, pos.row);
        if (holder) {
            STATES.get(getters).notifyBlocked?.(holder);
            return;
        }
        const result = super.startEdition(text, selection);
        STATES.get(getters)?.onComposer?.();
        return result;
    },
    stopEdition(direction) {
        const result = super.stopEdition(direction);
        STATES.get(this.getters)?.onComposer?.();
        return result;
    },
    cancelEdition() {
        const result = super.cancelEdition();
        STATES.get(this.getters)?.onComposer?.();
        return result;
    },
});

/* ------------------------------------------------------------------ */
/* "<name> is typing" tag above a cell someone else is typing in       */
/* ------------------------------------------------------------------ */
const FALLBACK_COLOR = "#b3261e";

/** Typing holders other than this browser, on the active sheet, with colours. */
export function typingTags(getters) {
    const state = STATES.get(getters);
    if (!state || !state.enabled || !state.locks.size) {
        return [];
    }
    const me = state.me();
    const sheetId = getters.getActiveSheetId();
    const colors = {};
    for (const client of getters.getClientsToDisplay()) {
        colors[client.id] = client.color;
    }
    const tags = [];
    for (const holder of state.locks.values()) {
        if (holder.clientId !== me && holder.sheetId === sheetId) {
            tags.push({...holder, color: colors[holder.clientId] || FALLBACK_COLOR});
        }
    }
    return tags;
}

export class TypingTag extends Component {
    static template = "spreadsheet_oca_collab.TypingTag";
    static props = {tag: Object};

    get rect() {
        const {col, row} = this.props.tag;
        return this.env.model.getters.getVisibleRect({left: col, right: col, top: row, bottom: row});
    }
    get visible() {
        const {width, height} = this.rect;
        return width > 0 && height > 0;
    }
    get style() {
        const {height} = this.env.model.getters.getSheetViewDimensionWithHeaders();
        const {x, y} = this.rect;
        const color = this.props.tag.color;
        return [
            `bottom: ${height - y + 15}px`,
            `left: ${x - 1}px`,
            `border-color: ${color}`,
            `background-color: ${color}`,
        ].join(";");
    }
    get cellStyle() {
        // getVisibleRect already includes the row and column header offsets.
        const {x, y, width, height} = this.rect;
        return [
            `left: ${x}px`,
            `top: ${y}px`,
            `width: ${width}px`,
            `height: ${height}px`,
            `--o-typing-c: ${this.props.tag.color}`,
        ].join(";");
    }
}

Grid.components = {...Grid.components, TypingTag};
