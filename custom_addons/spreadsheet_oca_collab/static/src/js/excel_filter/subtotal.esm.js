/** @odoo-module **/
/* Copyright 2026 RLFB
 * License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
 *
 * SUBTOTAL, as in Excel: totals that ignore rows hidden by a filter
 * (function_num 1-11) or also rows hidden by hand (101-111), and that skip
 * other SUBTOTAL cells in the range so nested subtotals are not counted twice.
 *
 * o-spreadsheet 18 has no SUBTOTAL; the XLSX importer rewrites it to SUM,
 * which counts filtered rows. The server keeps imported SUBTOTAL formulas
 * (see the import hook) and this function evaluates them.
 */

import * as spreadsheet from "@odoo/o-spreadsheet";
import {_t} from "@web/core/l10n/translation";

const {arg, toNumber, toZone} = spreadsheet.helpers;
const {functionRegistry} = spreadsheet.registries;

/**
 * Getters of the models whose sheets use SUBTOTAL. o-spreadsheet does not
 * re-evaluate formulas when a filter changes; the filter code asks for an
 * evaluation only for these models, so sheets without SUBTOTAL pay nothing.
 */
export const SUBTOTAL_MODELS = new WeakSet();

const NAMES = {
    1: "AVERAGE",
    2: "COUNT",
    3: "COUNTA",
    4: "MAX",
    5: "MIN",
    6: "PRODUCT",
    7: "STDEV",
    8: "STDEVP",
    9: "SUM",
    10: "VAR",
    11: "VARP",
};

function variance(values, sample) {
    const n = values.length;
    if (n === 0 || (sample && n === 1)) {
        return null;
    }
    const mean = values.reduce((a, b) => a + b, 0) / n;
    const ss = values.reduce((a, b) => a + (b - mean) ** 2, 0);
    return ss / (sample ? n - 1 : n);
}

function aggregate(code, numbers, nonEmpty) {
    switch (code) {
        case 1:
            return numbers.length
                ? numbers.reduce((a, b) => a + b, 0) / numbers.length
                : {value: "#DIV/0!", message: _t("No visible numbers to average.")};
        case 2:
            return numbers.length;
        case 3:
            return nonEmpty;
        case 4:
            return numbers.length ? Math.max(...numbers) : 0;
        case 5:
            return numbers.length ? Math.min(...numbers) : 0;
        case 6:
            return numbers.length ? numbers.reduce((a, b) => a * b, 1) : 0;
        case 7:
        case 8:
        case 10:
        case 11: {
            const v = variance(numbers, code === 7 || code === 10);
            if (v === null) {
                return {value: "#DIV/0!", message: _t("Not enough visible numbers.")};
            }
            return code === 7 || code === 8 ? Math.sqrt(v) : v;
        }
        case 9:
            return numbers.reduce((a, b) => a + b, 0);
    }
    return {value: "#VALUE!", message: _t("Unknown SUBTOTAL function number.")};
}

const SUBTOTAL = {
    description: _t(
        "Subtotal of a range, ignoring rows hidden by a filter (and, for 101-111, rows hidden by hand)."
    ),
    args: [
        arg(
            "function_num (number)",
            _t("1-11 ignores filtered rows; 101-111 also ignores hidden rows. 1/101 AVERAGE, 2 COUNT, 3 COUNTA, 4 MAX, 5 MIN, 6 PRODUCT, 7 STDEV, 8 STDEVP, 9 SUM, 10 VAR, 11 VARP.")
        ),
        arg("ref1 (meta)", _t("The first range to subtotal.")),
        arg("ref2 (meta, repeating)", _t("Additional ranges to subtotal.")),
    ],
    compute: function (functionNum, ...refs) {
        const num = Math.trunc(toNumber(functionNum, this.locale));
        const ignoreManual = num > 100;
        const code = ignoreManual ? num - 100 : num;
        if (!NAMES[code]) {
            return {value: "#VALUE!", message: _t("Unknown SUBTOTAL function number.")};
        }
        const getters = this.getters;
        SUBTOTAL_MODELS.add(getters);
        const originSheetId = this.__originSheetId;
        const origin = this.__originCellPosition;
        if (origin) {
            this.updateDependencies?.(origin);
        }
        const numbers = [];
        let nonEmpty = 0;
        let format;
        const ranges = [];
        for (const ref of refs) {
            if (!ref || ref.value === undefined) {
                continue;
            }
            const range = getters.getRangeFromSheetXC(originSheetId, String(ref.value));
            if (!range || range.invalidXc || range.invalidSheetName) {
                return {value: "#REF!", message: _t("Invalid reference.")};
            }
            ranges.push(range);
        }
        if (origin) {
            this.addDependencies?.(origin, ranges);
        }
        for (const range of ranges) {
            const zone = range.zone || toZone(range.value);
            for (let row = zone.top; row <= zone.bottom; row++) {
                if (getters.isRowFiltered(range.sheetId, row)) {
                    continue;
                }
                if (ignoreManual && getters.isRowHiddenByUser(range.sheetId, row)) {
                    continue;
                }
                for (let col = zone.left; col <= zone.right; col++) {
                    const position = {sheetId: range.sheetId, col, row};
                    const cell = getters.getCell(position);
                    if (cell?.isFormula && /^\s*=\s*SUBTOTAL\s*\(/i.test(cell.content)) {
                        continue;
                    }
                    const evaluated = getters.getEvaluatedCell(position);
                    if (evaluated.type === "error") {
                        return evaluated;
                    }
                    if (evaluated.type !== "empty") {
                        nonEmpty++;
                    }
                    if (evaluated.type === "number") {
                        numbers.push(evaluated.value);
                        format ??= evaluated.format;
                    }
                }
            }
        }
        const result = aggregate(code, numbers, nonEmpty);
        if (typeof result === "number" && code !== 2 && code !== 3 && format) {
            return {value: result, format};
        }
        return result;
    },
    isExported: true,
};

functionRegistry.add("SUBTOTAL", SUBTOTAL);
