/** @odoo-module **/
/* Copyright 2026 RLFB
 * License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
 */

import * as spreadsheet from "@odoo/o-spreadsheet";
import {Component, onMounted, useRef, useState} from "@odoo/owl";
import {Dialog} from "@web/core/dialog/dialog";
import {_t} from "@web/core/l10n/translation";

const {topbarMenuRegistry} = spreadsheet.registries;

export class NameVersionDialog extends Component {
    static template = "spreadsheet_oca_collab.NameVersionDialog";
    static components = {Dialog};
    static props = {close: Function, onConfirm: Function};

    setup() {
        this.title = _t("Name current version");
        this.state = useState({name: ""});
        const ref = useRef("name");
        onMounted(() => ref.el?.focus());
    }
    onKeydown(ev) {
        if (ev.key === "Enter") {
            this.confirm();
        }
    }
    confirm() {
        const name = this.state.name.trim();
        if (!name) {
            return;
        }
        this.props.onConfirm(name);
        this.props.close();
    }
}

topbarMenuRegistry.addChild(
    "save",
    ["file"],
    {
        name: _t("Save"),
        description: "Ctrl+S",
        sequence: 10,
        execute: (env) => env.saveSpreadsheet(),
        icon: "o-spreadsheet-Icon.DOWNLOAD",
    },
    {force: true}
);
topbarMenuRegistry.addChild("collab_name_version", ["file"], {
    name: _t("Name current version…"),
    sequence: 11,
    isVisible: (env) => Boolean(env.collabSaveVersion) && env.collabHasVersions?.(),
    execute: (env) =>
        env.services.dialog.add(NameVersionDialog, {
            onConfirm: (name) => env.collabSaveVersion(name),
        }),
});
topbarMenuRegistry.addChild("collab_history", ["file"], {
    name: _t("Version history"),
    sequence: 12,
    separator: true,
    isReadonlyAllowed: true,
    isVisible: (env) => Boolean(env.collabOpenHistory) && env.collabHasVersions?.(),
    execute: (env) => env.collabOpenHistory(),
});
