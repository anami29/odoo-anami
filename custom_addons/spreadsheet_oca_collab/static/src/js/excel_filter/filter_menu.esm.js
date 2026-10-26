/** @odoo-module **/
/* Copyright 2026 RLFB
 * License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
 *
 * Excel-style AutoFilter dropdown, replacing o-spreadsheet's FilterMenu.
 */

import * as spreadsheet from "@odoo/o-spreadsheet";
import {Component, onMounted, useRef, useState} from "@odoo/owl";
import {_t} from "@web/core/l10n/translation";
import {
    DATE_OPERATORS,
    DYNAMIC_PERIODS,
    NUMBER_OPERATORS,
    TEXT_OPERATORS,
    columnKind,
    dateTree,
    describe,
    distinctItems,
    parseOperand,
    searchMatches,
    serialToDate,
} from "./criteria.esm";
import {
    applyCriteria,
    columnCells,
    extendFilterToData,
    getCriteria,
    getFilterContext,
} from "./filter_state.esm";

const {cellPopoverRegistry} = spreadsheet.registries;
const {toNumber} = spreadsheet.helpers;

const SORT_LABELS = {
    text: [_t("Sort A to Z"), _t("Sort Z to A")],
    number: [_t("Sort Smallest to Largest"), _t("Sort Largest to Smallest")],
    date: [_t("Sort Oldest to Newest"), _t("Sort Newest to Oldest")],
};
const KIND_FILTERS = {
    text: _t("Text Filters"),
    number: _t("Number Filters"),
    date: _t("Date Filters"),
};

export class ExcelFilterMenu extends Component {
    static template = "spreadsheet_oca_collab.ExcelFilterMenu";
    static props = {
        filterPosition: Object,
        onClosed: {type: Function, optional: true},
    };

    setup() {
        this.searchRef = useRef("search");
        this.firstInputRef = useRef("firstInput");
        const model = this.env.model;
        const first = getFilterContext(model, this.props.filterPosition);
        if (first && extendFilterToData(model, first.sheetId, first.table)) {
            this.extended = true;
        }
        this.ctx = getFilterContext(model, this.props.filterPosition);
        const existing = this.ctx
            ? getCriteria(model, this.ctx.sheetId, this.ctx.filter.id)
            : null;
        const hiddenNow = new Set(
            this.ctx
                ? model.getters
                      .getFilterHiddenValues({
                          sheetId: this.ctx.sheetId,
                          col: this.ctx.filter.col,
                          row: this.ctx.headerRow,
                      })
                      .map((v) => v.toLowerCase())
                : []
        );
        // Excel lists the values left by the other columns' filters.
        this.items = this.ctx
            ? distinctItems(columnCells(model, this.ctx, {respectOtherFilters: true}))
            : [];
        // Values hidden by this column's filter that no longer appear still
        // belong in the list, unticked (Excel keeps them until Clear).
        for (const value of hiddenNow) {
            if (!this.items.some((i) => i.key === value)) {
                this.items.push({kind: "text", key: value, label: value, value, count: 0});
            }
        }
        this.kind = columnKind(this.items);
        const checked = {};
        for (const item of this.items) {
            checked[item.key] = !hiddenNow.has(item.key);
        }
        this.tree = this.kind === "date" ? dateTree(this.items) : [];
        this.state = useState({
            mode: "list",
            search: "",
            searchChecked: {},
            addToFilter: false,
            checked,
            expanded: {},
            submenu: false,
            cond: this.initialCondition(existing),
            top: existing?.type === "top"
                ? {...existing}
                : {top: true, n: 10, percent: false},
            error: "",
        });
        this.existing = existing;
        onMounted(() => this.searchRef.el?.focus());
    }

    /* ------------------------------ labels ------------------------------ */
    get headerLabel() {
        return this.ctx?.headerLabel || "";
    }
    get criteriaText() {
        return describe(this.existing, _t);
    }
    get isActive() {
        return Boolean(
            this.ctx &&
                this.env.model.getters.isFilterActive({
                    sheetId: this.ctx.sheetId,
                    col: this.ctx.filter.col,
                    row: this.ctx.headerRow,
                })
        );
    }
    get sortLabels() {
        return SORT_LABELS[this.kind];
    }
    get kindFiltersLabel() {
        return KIND_FILTERS[this.kind];
    }
    get isSortable() {
        if (!this.ctx || this.env.model.getters.isReadonly()) {
            return false;
        }
        const table = this.ctx.table;
        const core = this.env.model.getters.getCoreTableMatchingTopLeft(
            this.ctx.sheetId,
            table.range.zone
        );
        return core?.type !== "dynamic";
    }
    get operators() {
        if (this.kind === "number") {
            return NUMBER_OPERATORS;
        }
        if (this.kind === "date") {
            return DATE_OPERATORS;
        }
        return TEXT_OPERATORS;
    }
    get submenuEntries() {
        const op = (key, label) => ({type: "op", key, label});
        if (this.kind === "number") {
            return [
                op("eq", _t("Equals…")),
                op("ne", _t("Does Not Equal…")),
                {sep: true},
                op("gt", _t("Greater Than…")),
                op("gte", _t("Greater Than Or Equal To…")),
                op("lt", _t("Less Than…")),
                op("lte", _t("Less Than Or Equal To…")),
                op("between", _t("Between…")),
                {sep: true},
                {type: "top", label: _t("Top 10…")},
                {type: "avg", above: true, label: _t("Above Average")},
                {type: "avg", above: false, label: _t("Below Average")},
                {sep: true},
                op("custom", _t("Custom Filter…")),
            ];
        }
        if (this.kind === "date") {
            return [
                op("eq", _t("Equals…")),
                op("lt", _t("Before…")),
                op("gt", _t("After…")),
                op("between", _t("Between…")),
                {sep: true},
                ...DYNAMIC_PERIODS.map(([period, label]) => ({
                    type: "period",
                    period,
                    label: _t(label),
                })),
                {sep: true},
                op("custom", _t("Custom Filter…")),
            ];
        }
        return [
            op("eq", _t("Equals…")),
            op("ne", _t("Does Not Equal…")),
            {sep: true},
            op("begins", _t("Begins With…")),
            op("ends", _t("Ends With…")),
            {sep: true},
            op("contains", _t("Contains…")),
            op("notcontains", _t("Does Not Contain…")),
            {sep: true},
            op("custom", _t("Custom Filter…")),
        ];
    }

    /* ------------------------------- list ------------------------------- */
    get visibleItems() {
        return searchMatches(this.items, this.state.search.trim());
    }
    get showTree() {
        return this.kind === "date" && !this.state.search.trim();
    }
    itemLabel(item) {
        if (item.kind === "blank") {
            return _t("(Blanks)");
        }
        return item.label;
    }
    get searching() {
        return Boolean(this.state.search.trim());
    }
    isChecked(item) {
        if (this.searching) {
            return this.state.searchChecked[item.key] ?? true;
        }
        return Boolean(this.state.checked[item.key]);
    }
    setChecked(item, value) {
        if (this.searching) {
            this.state.searchChecked[item.key] = value;
        } else {
            this.state.checked[item.key] = value;
        }
    }
    onSearchInput(ev) {
        // Excel ticks every search result each time the search changes.
        this.state.search = ev.target.value;
        this.state.searchChecked = {};
        this.state.error = "";
    }
    boxState(items) {
        if (!items.length) {
            return "off";
        }
        const values = items.map((i) => this.isChecked(i));
        if (values.every(Boolean)) {
            return "on";
        }
        return values.some(Boolean) ? "mixed" : "off";
    }
    get selectAllState() {
        return this.boxState(this.visibleItems);
    }
    toggleItems(items) {
        const turnOn = this.boxState(items) !== "on";
        for (const item of items) {
            this.setChecked(item, turnOn);
        }
        this.state.error = "";
    }
    toggleItem(item) {
        this.setChecked(item, !this.isChecked(item));
        this.state.error = "";
    }
    yearItems(year) {
        return year.months.flatMap((m) => m.days);
    }
    toggleExpand(key) {
        this.state.expanded[key] = !this.state.expanded[key];
    }
    dayLabel(item) {
        return String(serialToDate(item.value).getUTCDate()).padStart(2, "0");
    }
    get blankAndOtherItems() {
        return this.items.filter((i) => i.kind !== "date");
    }
    onSearchKeydown(ev) {
        if (ev.key === "Enter") {
            ev.preventDefault();
            ev.stopPropagation();
            this.confirm();
        } else if (ev.key === "Escape") {
            ev.preventDefault();
            ev.stopPropagation();
            this.close();
        }
    }
    onPanelKeydown(ev) {
        if (ev.key === "Escape") {
            ev.preventDefault();
            ev.stopPropagation();
            if (this.state.mode !== "list") {
                this.state.mode = "list";
            } else {
                this.close();
            }
        } else if (ev.key === "Enter" && this.state.mode !== "list") {
            ev.preventDefault();
            ev.stopPropagation();
            this.state.mode === "top" ? this.confirmTop() : this.confirmCondition();
        }
    }

    /* ------------------------------ actions ----------------------------- */
    close() {
        this.props.onClosed?.();
    }
    apply(criteria) {
        applyCriteria(this.env.model, this.ctx, criteria);
        this.close();
    }
    confirm() {
        if (!this.ctx) {
            return this.close();
        }
        const search = this.state.search.trim();
        let visible;
        if (search) {
            // Excel: OK with a search keeps only the ticked search results,
            // optionally added to what the filter already shows.
            const matches = new Set(
                this.visibleItems.filter((i) => this.isChecked(i)).map((i) => i.key)
            );
            visible = new Set(matches);
            if (this.state.addToFilter) {
                const hidden = new Set(
                    this.env.model.getters
                        .getFilterHiddenValues({
                            sheetId: this.ctx.sheetId,
                            col: this.ctx.filter.col,
                            row: this.ctx.headerRow,
                        })
                        .map((v) => v.toLowerCase())
                );
                for (const item of this.items) {
                    if (!hidden.has(item.key)) {
                        visible.add(item.key);
                    }
                }
            }
        } else {
            visible = new Set(this.items.filter((i) => this.state.checked[i.key]).map((i) => i.key));
        }
        if (visible.size === 0) {
            this.state.error = _t("Select at least one value.");
            return;
        }
        if (visible.size === this.items.length) {
            return this.apply(null);
        }
        this.apply({type: "values", visible: [...visible]});
    }
    clearFilter() {
        this.apply(null);
    }
    sort(descending) {
        const table = this.ctx.table;
        const zone = table.range.zone;
        const numberOfHeaders = table.config.numberOfHeaders || 0;
        const result = this.env.model.dispatch("XL_SORT", {
            sheetId: this.ctx.sheetId,
            zone: {...zone, top: zone.top + numberOfHeaders},
            keys: [{col: this.ctx.filter.col, descending}],
            hasHeaders: false,
        });
        if (!result.isSuccessful) {
            this.env.raiseError?.(_t("Cells with merges of different sizes can't be sorted."));
        }
        this.close();
    }
    openCustomSort() {
        this.close();
        this.env.openSidePanel("XlCustomSort", {});
    }

    /* --------------------------- submenu entries ------------------------ */
    onSubmenu(entry) {
        this.state.submenu = false;
        if (entry.type === "top") {
            this.state.mode = "top";
            return;
        }
        if (entry.type === "avg") {
            return this.apply({type: "average", above: entry.above});
        }
        if (entry.type === "period") {
            return this.apply({type: "period", period: entry.period});
        }
        const cond = this.state.cond;
        cond.join = "and";
        cond.v1 = "";
        cond.v2 = "";
        cond.op2 = "";
        if (entry.key === "between") {
            cond.op1 = "gte";
            cond.op2 = "lte";
        } else if (entry.key !== "custom") {
            cond.op1 = entry.key;
        }
        this.state.mode = "condition";
        setTimeout(() => this.firstInputRef.el?.focus());
    }

    initialCondition(existing) {
        if (existing?.type === "condition") {
            return {
                op1: existing.c1?.op || "eq",
                v1: existing.c1?.display ?? existing.c1?.operand ?? "",
                op2: existing.c2?.op || "",
                v2: existing.c2?.display ?? existing.c2?.operand ?? "",
                join: existing.join || "and",
            };
        }
        return {op1: "eq", v1: "", op2: "", v2: "", join: "and"};
    }

    parseDate(text) {
        if (!text) {
            return null;
        }
        try {
            const n = toNumber(text, this.env.model.getters.getLocale());
            return Number.isFinite(n) ? Math.floor(n) : null;
        } catch {
            return null;
        }
    }

    makeCondition(op, raw) {
        if (!op) {
            return null;
        }
        const textOps = ["begins", "notbegins", "ends", "notends", "contains", "notcontains"];
        if (this.kind === "text" || textOps.includes(op)) {
            return {op, operand: String(raw ?? ""), display: String(raw ?? "")};
        }
        const operand = parseOperand(raw, this.kind, (t) => this.parseDate(t));
        if (operand === null) {
            // A non-numeric operand on a numeric column compares as text.
            if (op === "eq" || op === "ne") {
                return {op, operand: String(raw ?? ""), display: String(raw ?? "")};
            }
            return undefined;
        }
        return {op, operand, display: String(raw)};
    }

    confirmCondition() {
        const cond = this.state.cond;
        const c1 = this.makeCondition(cond.op1, cond.v1);
        const c2 = cond.op2 ? this.makeCondition(cond.op2, cond.v2) : null;
        if (c1 === undefined || c2 === undefined) {
            this.state.error =
                this.kind === "date" ? _t("Enter a valid date.") : _t("Enter a valid number.");
            return;
        }
        if (!c1) {
            return;
        }
        this.apply({type: "condition", kind: this.kind, c1, c2, join: cond.join});
    }

    confirmTop() {
        const top = this.state.top;
        const n = Math.trunc(Number(top.n));
        const max = top.percent ? 100 : 500;
        if (!Number.isFinite(n) || n < 1 || n > max) {
            this.state.error = top.percent
                ? _t("Enter a percentage between 1 and 100.")
                : _t("Enter a number between 1 and 500.");
            return;
        }
        this.apply({type: "top", top: Boolean(top.top), n, percent: Boolean(top.percent)});
    }

    get suggestions() {
        return this.items.filter((i) => i.kind !== "blank").slice(0, 200).map((i) => i.label);
    }
}

cellPopoverRegistry.add("FilterMenu", {
    onOpen: (position) => ({
        isOpen: true,
        props: {filterPosition: position},
        Component: ExcelFilterMenu,
        cellCorner: "BottomLeft",
    }),
});
