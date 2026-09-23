/** @odoo-module **/
/**
 * Word-like formatting for the agreement document editors.
 *
 * Odoo 18 ships the alignment behaviour (html_editor/main/align_plugin.js) but never
 * puts it on the toolbar, and has no font-family format at all. This plugin adds both,
 * and is attached only to the fields where a document is actually written — see
 * AGREEMENT_EDITOR_FIELDS. Every other rich-text field in the database keeps the
 * standard toolbar.
 */

import { onWillStart, reactive } from "@odoo/owl";
import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";

import { Plugin } from "@html_editor/plugin";
import { HtmlField } from "@html_editor/fields/html_field";
import { FontSelector } from "@html_editor/main/font/font_selector";
import { closestElement } from "@html_editor/utils/dom_traversal";
import { formatsSpecs } from "@html_editor/utils/formatting";
import { withSequence } from "@html_editor/utils/resource";

/**
 * The fields this toolbar applies to: {model: [field, ...]}.
 * Internal notes and the read-only rendered document are deliberately absent.
 */
const AGREEMENT_EDITOR_FIELDS = {
    "agreement.template.version": ["content", "annexure_content"],
    "agreement.annexure": ["content"],
};

/**
 * Shown when no font has been configured yet. Everything else comes from
 * agreement.font — Agreements > Configuration > Fonts.
 */
const DEFAULT_FONT_ITEM = { name: _t("Document default"), family: "" };

/** Put the configured @font-face rules in the page so the editor renders uploads too. */
function injectFontFaceCss(css) {
    const id = "agreement_management_font_faces";
    let style = document.getElementById(id);
    if (!css) {
        style?.remove();
        return;
    }
    if (!style) {
        style = document.createElement("style");
        style.id = id;
        document.head.appendChild(style);
    }
    style.textContent = css;
}

/**
 * Odoo 18 supports italic, bold, underline, strikeThrough, fontSize,
 * setFontSizeClassName and switchDirection — there is no font-family format. Register
 * one so the selection-level plumbing in FormatPlugin can apply it, the same way it
 * already applies font size.
 */
if (!formatsSpecs.fontFamily) {
    formatsSpecs.fontFamily = {
        isFormatted: (node) => Boolean(closestElement(node, (el) => Boolean(el.style?.fontFamily))),
        hasStyle: (node) => Boolean(node.style && node.style["font-family"]),
        addStyle: (node, props) => {
            node.style["font-family"] = props.fontFamily;
        },
        removeStyle: (node) => {
            node.style["font-family"] = null;
            if (node.getAttribute("style") === "") {
                node.removeAttribute("style");
            }
        },
    };
}

export class AgreementEditorPlugin extends Plugin {
    static id = "agreementEditor";
    static dependencies = ["format", "selection"];

    resources = {
        toolbar_groups: [
            withSequence(28, { id: "agreement_font_family" }),
            withSequence(46, { id: "agreement_align" }),
        ],
        toolbar_items: [
            {
                id: "agreement_font_family",
                groupId: "agreement_font_family",
                title: _t("Font family"),
                Component: FontSelector,
                props: {
                    getItems: () => this.fontItems,
                    getDisplay: () => this.fontFamily,
                    onSelected: (item) => this.setFontFamily(item),
                },
            },
            // `title` and `icon` given here win over the user command's, which carries
            // neither; `run` still comes from Odoo's own AlignPlugin.
            {
                id: "agreement_align_left",
                groupId: "agreement_align",
                commandId: "alignLeft",
                title: _t("Align left"),
                icon: "fa-align-left",
            },
            {
                id: "agreement_align_center",
                groupId: "agreement_align",
                commandId: "alignCenter",
                title: _t("Align centre"),
                icon: "fa-align-center",
            },
            {
                id: "agreement_align_right",
                groupId: "agreement_align",
                commandId: "alignRight",
                title: _t("Align right"),
                icon: "fa-align-right",
            },
            {
                id: "agreement_align_justify",
                groupId: "agreement_align",
                commandId: "justify",
                title: _t("Justify"),
                icon: "fa-align-justify",
            },
        ],
        selectionchange_handlers: this.updateFontFamilyDisplay.bind(this),
    };

    setup() {
        this.fontFamily = reactive({ displayName: this.fontItems[0].name });
    }

    /** Fonts configured on agreement.font, passed in by the field below. */
    get fontItems() {
        const fonts = this.config.agreementFonts;
        return fonts?.length ? fonts : [DEFAULT_FONT_ITEM];
    }

    setFontFamily(item) {
        this.dependencies.format.formatSelection("fontFamily", {
            formatProps: { fontFamily: item.family },
            applyStyle: Boolean(item.family),
        });
        this.updateFontFamilyDisplay();
    }

    /** Show the font of whatever is selected, so the dropdown reads like Word's. */
    updateFontFamilyDisplay() {
        let current = "";
        const node = this.dependencies.selection.getEditableSelection()?.anchorNode;
        if (node) {
            const styled = closestElement(node, (el) => Boolean(el.style?.fontFamily));
            current = styled?.style?.fontFamily || "";
        }
        const items = this.fontItems;
        const match = items.find((font) => font.family && font.family === current);
        this.fontFamily.displayName = match ? match.name : items[0].name;
    }
}

patch(HtmlField.prototype, {
    setup() {
        super.setup(...arguments);
        const fields = AGREEMENT_EDITOR_FIELDS[this.props.record?.resModel];
        this.isAgreementEditor = Boolean(fields?.includes(this.props.name));
        this.agreementFonts = [];
        if (this.isAgreementEditor) {
            const orm = useService("orm");
            onWillStart(async () => {
                const data = await orm.call("agreement.font", "get_editor_fonts", []);
                this.agreementFonts = data.fonts || [];
                injectFontFaceCss(data.css);
            });
        }
    },

    getConfig() {
        const config = super.getConfig(...arguments);
        if (this.isAgreementEditor) {
            config.agreementFonts = this.agreementFonts;
            config.Plugins = [...config.Plugins, AgreementEditorPlugin];
        }
        return config;
    },
});
