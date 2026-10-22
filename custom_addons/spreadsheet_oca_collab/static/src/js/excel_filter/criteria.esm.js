/** @odoo-module **/
/* Copyright 2026 RLFB
 * License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
 *
 * Excel AutoFilter semantics, independent of the UI.
 *
 * o-spreadsheet stores a filter as a list of hidden displayed values. Excel
 * stores criteria (values, conditions, Top 10, dynamic dates) and evaluates
 * them when the filter is applied or reapplied. This module evaluates Excel
 * criteria against a column and translates the result into the hidden-value
 * list o-spreadsheet understands, so the native engine still does the row
 * hiding, the XLSX export and the per-user state.
 */

const DAY_MS = 86400000;
const EXCEL_EPOCH = Date.UTC(1899, 11, 30);
const MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
];

/** Date/time number formats: d, m, y, h, s tokens outside quotes/brackets. */
export function isDateFormat(format) {
    if (!format) {
        return false;
    }
    const stripped = format.replace(/"[^"]*"|\[[^\]]*\]|\\./g, "");
    if (/[#0?]/.test(stripped) && !/[dy]/i.test(stripped)) {
        return false;
    }
    return /[dmyhs]/i.test(stripped);
}

export function serialToDate(serial) {
    return new Date(EXCEL_EPOCH + Math.floor(serial) * DAY_MS);
}

function dateToSerial(date) {
    return Math.round(
        (Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate()) -
            EXCEL_EPOCH) /
            DAY_MS
    );
}

function todaySerial(now = new Date()) {
    return Math.round(
        (Date.UTC(now.getFullYear(), now.getMonth(), now.getDate()) - EXCEL_EPOCH) /
            DAY_MS
    );
}

/**
 * Classify an evaluated cell the way Excel's filter list does.
 * @returns {{kind: string, key: string, label: string, value: any}}
 */
export function classify(evaluated) {
    const label = evaluated.formattedValue ?? "";
    const key = label.toLowerCase();
    switch (evaluated.type) {
        case "empty":
            return {kind: "blank", key: "", label: "", value: null};
        case "number":
            if (isDateFormat(evaluated.format)) {
                return {kind: "date", key, label, value: evaluated.value};
            }
            return {kind: "number", key, label, value: evaluated.value};
        case "boolean":
            return {kind: "boolean", key, label, value: evaluated.value};
        case "error":
            return {kind: "error", key, label, value: label};
        default:
            if (label === "") {
                return {kind: "blank", key: "", label: "", value: null};
            }
            return {kind: "text", key, label, value: String(evaluated.value)};
    }
}

const KIND_ORDER = {number: 0, date: 0, text: 1, boolean: 2, error: 3, blank: 4};

let collator = null;
function getCollator(caseSensitive = false) {
    if (!caseSensitive) {
        collator ||= new Intl.Collator(undefined, {sensitivity: "base"});
        return collator;
    }
    return new Intl.Collator(undefined, {sensitivity: "variant", caseFirst: "lower"});
}

/** Excel ascending order: numbers and dates, text, logicals, errors; blanks last. */
export function compareItems(a, b, caseSensitive = false) {
    const ka = KIND_ORDER[a.kind];
    const kb = KIND_ORDER[b.kind];
    if (ka !== kb) {
        return ka - kb;
    }
    if (a.kind === "number" || a.kind === "date") {
        return a.value - b.value;
    }
    if (a.kind === "boolean") {
        return Number(a.value) - Number(b.value);
    }
    if (a.kind === "blank") {
        return 0;
    }
    return getCollator(caseSensitive).compare(a.label, b.label);
}

/** Dominant type of a column, which decides Text / Number / Date Filters. */
export function columnKind(items) {
    const counts = {text: 0, number: 0, date: 0};
    for (const item of items) {
        if (item.kind in counts) {
            counts[item.kind] += item.count || 1;
        }
    }
    if (counts.date && counts.date >= counts.number && counts.date >= counts.text) {
        return "date";
    }
    if (counts.number && counts.number >= counts.text) {
        return "number";
    }
    return "text";
}

/** Distinct values with counts, sorted in Excel order. */
export function distinctItems(cells) {
    const map = new Map();
    for (const evaluated of cells) {
        const item = classify(evaluated);
        const existing = map.get(item.key);
        if (existing) {
            existing.count++;
        } else {
            map.set(item.key, {...item, count: 1});
        }
    }
    return [...map.values()].sort((a, b) => compareItems(a, b));
}

/** Year > month > day tree for the date items of a column. */
export function dateTree(items) {
    const years = new Map();
    for (const item of items) {
        if (item.kind !== "date") {
            continue;
        }
        const date = serialToDate(item.value);
        const year = date.getUTCFullYear();
        const month = date.getUTCMonth();
        if (!years.has(year)) {
            years.set(year, {year, label: String(year), months: new Map()});
        }
        const node = years.get(year);
        if (!node.months.has(month)) {
            node.months.set(month, {month, label: MONTHS[month], days: []});
        }
        node.months.get(month).days.push(item);
    }
    return [...years.values()]
        .sort((a, b) => a.year - b.year)
        .map((y) => ({
            ...y,
            months: [...y.months.values()]
                .sort((a, b) => a.month - b.month)
                .map((m) => ({...m, days: m.days.sort((a, b) => a.value - b.value)})),
        }));
}

/* ------------------------------------------------------------------ */
/* Search box: literal, case-insensitive; * and ? wildcards, ~ escape  */
/* ------------------------------------------------------------------ */
export function wildcardToRegExp(pattern, {anchored = false} = {}) {
    let source = "";
    for (let i = 0; i < pattern.length; i++) {
        const char = pattern[i];
        if (char === "~" && i + 1 < pattern.length && "*?~".includes(pattern[i + 1])) {
            source += "\\" + pattern[++i];
        } else if (char === "*") {
            source += ".*";
        } else if (char === "?") {
            source += ".";
        } else {
            source += char.replace(/[.+^${}()|[\]\\/-]/g, "\\$&");
        }
    }
    return new RegExp(anchored ? `^${source}$` : source, "i");
}

export function searchMatches(items, text) {
    if (!text) {
        return items;
    }
    const re = wildcardToRegExp(text);
    return items.filter((item) => re.test(item.kind === "blank" ? "" : item.label));
}

/* ------------------------------------------------------------------ */
/* Conditions (Custom AutoFilter)                                      */
/* ------------------------------------------------------------------ */
export const TEXT_OPERATORS = [
    ["eq", "equals"],
    ["ne", "does not equal"],
    ["begins", "begins with"],
    ["notbegins", "does not begin with"],
    ["ends", "ends with"],
    ["notends", "does not end with"],
    ["contains", "contains"],
    ["notcontains", "does not contain"],
];
export const NUMBER_OPERATORS = [
    ["eq", "equals"],
    ["ne", "does not equal"],
    ["gt", "is greater than"],
    ["gte", "is greater than or equal to"],
    ["lt", "is less than"],
    ["lte", "is less than or equal to"],
];
export const DATE_OPERATORS = [
    ["eq", "equals"],
    ["ne", "does not equal"],
    ["gt", "is after"],
    ["gte", "is after or equal to"],
    ["lt", "is before"],
    ["lte", "is before or equal to"],
];
export const DYNAMIC_PERIODS = [
    ["tomorrow", "Tomorrow"],
    ["today", "Today"],
    ["yesterday", "Yesterday"],
    ["nextWeek", "Next Week"],
    ["thisWeek", "This Week"],
    ["lastWeek", "Last Week"],
    ["nextMonth", "Next Month"],
    ["thisMonth", "This Month"],
    ["lastMonth", "Last Month"],
    ["nextQuarter", "Next Quarter"],
    ["thisQuarter", "This Quarter"],
    ["lastQuarter", "Last Quarter"],
    ["nextYear", "Next Year"],
    ["thisYear", "This Year"],
    ["lastYear", "Last Year"],
    ["ytd", "Year to Date"],
];

/** Parse what the user typed in a condition box, for a column kind. */
export function parseOperand(raw, kind, parseDate) {
    const text = String(raw ?? "").trim();
    if (kind === "number") {
        const n = Number(text.replace(/,/g, ""));
        return Number.isFinite(n) && text !== "" ? n : null;
    }
    if (kind === "date") {
        const serial = parseDate ? parseDate(text) : null;
        return serial ?? null;
    }
    return text;
}

function testText(item, op, operand) {
    const label = item.kind === "blank" ? "" : item.label;
    const o = String(operand ?? "");
    switch (op) {
        case "eq":
            return wildcardToRegExp(o, {anchored: true}).test(label);
        case "ne":
            return !wildcardToRegExp(o, {anchored: true}).test(label);
        case "begins":
            return wildcardToRegExp(o + "*", {anchored: true}).test(label);
        case "notbegins":
            return !wildcardToRegExp(o + "*", {anchored: true}).test(label);
        case "ends":
            return wildcardToRegExp("*" + o, {anchored: true}).test(label);
        case "notends":
            return !wildcardToRegExp("*" + o, {anchored: true}).test(label);
        case "contains":
            return wildcardToRegExp(o).test(label);
        case "notcontains":
            return !wildcardToRegExp(o).test(label);
    }
    return true;
}

function testNumber(value, op, operand) {
    switch (op) {
        case "eq":
            return value === operand;
        case "ne":
            return value !== operand;
        case "gt":
            return value > operand;
        case "gte":
            return value >= operand;
        case "lt":
            return value < operand;
        case "lte":
            return value <= operand;
    }
    return true;
}

function testCondition(item, condition, kind) {
    if (!condition || !condition.op) {
        return true;
    }
    if (kind === "text" || typeof condition.operand === "string") {
        // Text operators, or a text operand on a numeric column: Excel then
        // compares the displayed text.
        return testText(item, condition.op, condition.operand);
    }
    if (item.kind !== "number" && item.kind !== "date") {
        // Excel: numeric comparisons never match text or blanks, except "≠".
        return condition.op === "ne";
    }
    const value = kind === "date" ? Math.floor(item.value) : item.value;
    return testNumber(value, condition.op, condition.operand);
}

/** [start, end) serial range of a dynamic period, Excel week starting Sunday. */
export function periodRange(period, now = new Date()) {
    const t = todaySerial(now);
    const today = serialToDate(t);
    const y = today.getUTCFullYear();
    const m = today.getUTCMonth();
    const dow = today.getUTCDay();
    const q = Math.floor(m / 3);
    const s = (yy, mm, dd = 1) => dateToSerial(new Date(Date.UTC(yy, mm, dd)));
    switch (period) {
        case "tomorrow":
            return [t + 1, t + 2];
        case "today":
            return [t, t + 1];
        case "yesterday":
            return [t - 1, t];
        case "thisWeek":
            return [t - dow, t - dow + 7];
        case "lastWeek":
            return [t - dow - 7, t - dow];
        case "nextWeek":
            return [t - dow + 7, t - dow + 14];
        case "thisMonth":
            return [s(y, m), s(y, m + 1)];
        case "lastMonth":
            return [s(y, m - 1), s(y, m)];
        case "nextMonth":
            return [s(y, m + 1), s(y, m + 2)];
        case "thisQuarter":
            return [s(y, q * 3), s(y, q * 3 + 3)];
        case "lastQuarter":
            return [s(y, q * 3 - 3), s(y, q * 3)];
        case "nextQuarter":
            return [s(y, q * 3 + 3), s(y, q * 3 + 6)];
        case "thisYear":
            return [s(y, 0), s(y + 1, 0)];
        case "lastYear":
            return [s(y - 1, 0), s(y, 0)];
        case "nextYear":
            return [s(y + 1, 0), s(y + 2, 0)];
        case "ytd":
            return [s(y, 0), t + 1];
    }
    return [-Infinity, Infinity];
}

/**
 * Which items a criteria keeps visible.
 *
 * criteria = {
 *   type: "values", visible: [keys]               // value list (incl. "" for blanks)
 * | type: "condition", kind, c1, c2, join: "and"|"or"
 * | type: "top", top: true|false, n, percent: bool
 * | type: "average", above: bool
 * | type: "period", period
 * }
 */
export function visibleKeys(items, criteria, now = new Date()) {
    if (!criteria) {
        return new Set(items.map((i) => i.key));
    }
    switch (criteria.type) {
        case "values":
            return new Set(criteria.visible);
        case "condition": {
            const keep = new Set();
            for (const item of items) {
                const a = testCondition(item, criteria.c1, criteria.kind);
                const hasSecond = criteria.c2 && criteria.c2.op;
                const b = hasSecond ? testCondition(item, criteria.c2, criteria.kind) : a;
                const ok = !hasSecond ? a : criteria.join === "or" ? a || b : a && b;
                if (ok) {
                    keep.add(item.key);
                }
            }
            return keep;
        }
        case "top": {
            const numeric = [];
            for (const item of items) {
                if (item.kind === "number" || item.kind === "date") {
                    for (let i = 0; i < item.count; i++) {
                        numeric.push(item);
                    }
                }
            }
            numeric.sort((a, b) => (criteria.top ? b.value - a.value : a.value - b.value));
            const n = criteria.percent
                ? Math.max(1, Math.floor((numeric.length * criteria.n) / 100))
                : criteria.n;
            if (!numeric.length || n <= 0) {
                return new Set();
            }
            const cutoff = numeric[Math.min(n, numeric.length) - 1].value;
            const keep = new Set();
            for (const item of items) {
                if (item.kind !== "number" && item.kind !== "date") {
                    continue;
                }
                if (criteria.top ? item.value >= cutoff : item.value <= cutoff) {
                    keep.add(item.key);
                }
            }
            return keep;
        }
        case "average": {
            let sum = 0;
            let count = 0;
            for (const item of items) {
                if (item.kind === "number" || item.kind === "date") {
                    sum += item.value * item.count;
                    count += item.count;
                }
            }
            const avg = count ? sum / count : 0;
            const keep = new Set();
            for (const item of items) {
                if (item.kind !== "number" && item.kind !== "date") {
                    continue;
                }
                if (criteria.above ? item.value > avg : item.value < avg) {
                    keep.add(item.key);
                }
            }
            return keep;
        }
        case "period": {
            const [start, end] = periodRange(criteria.period, now);
            const keep = new Set();
            for (const item of items) {
                if (item.kind === "date") {
                    const d = Math.floor(item.value);
                    if (d >= start && d < end) {
                        keep.add(item.key);
                    }
                }
            }
            return keep;
        }
    }
    return new Set(items.map((i) => i.key));
}

/** The o-spreadsheet hidden-value list for a criteria. */
export function hiddenValuesFor(items, criteria, now) {
    const keep = visibleKeys(items, criteria, now);
    return items.filter((item) => !keep.has(item.key)).map((item) => item.label);
}

const OP_LABELS = Object.fromEntries([
    ...TEXT_OPERATORS,
    ...NUMBER_OPERATORS.map(([k, v]) => [k, v]),
]);

/** Human description shown in the menu header and status bar. */
export function describe(criteria, translate = (s) => s) {
    if (!criteria) {
        return "";
    }
    const _t = translate;
    switch (criteria.type) {
        case "values":
            return criteria.visible.length === 1
                ? `${_t("Equals")} "${criteria.visible[0] || _t("(Blanks)")}"`
                : _t("%s values selected").replace("%s", criteria.visible.length);
        case "condition": {
            const dateOps = Object.fromEntries(DATE_OPERATORS);
            const label = (c) =>
                `${(criteria.kind === "date" ? dateOps[c.op] : OP_LABELS[c.op]) || c.op} "${c.display ?? c.operand}"`;
            let text = label(criteria.c1);
            if (criteria.c2 && criteria.c2.op) {
                text += ` ${criteria.join === "or" ? _t("or") : _t("and")} ${label(criteria.c2)}`;
            }
            return text;
        }
        case "top":
            return `${criteria.top ? _t("Top") : _t("Bottom")} ${criteria.n}${criteria.percent ? "%" : ""}`;
        case "average":
            return criteria.above ? _t("Above Average") : _t("Below Average");
        case "period":
            return Object.fromEntries(DYNAMIC_PERIODS)[criteria.period] || criteria.period;
    }
    return "";
}
