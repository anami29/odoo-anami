/** @odoo-module **/
/**
 * Red asterisk on mandatory dynamic fields.
 *
 * Odoo's property definitions accept no 'required' key (see fields.py ALLOWED_KEYS), so
 * the server marks a mandatory field by appending "*" to its label and this colours that
 * asterisk. If it ever stops running the label still reads "Vessel Name *" — the marker
 * is data, the colour is decoration.
 *
 * Scoped to agreement documents; properties elsewhere in the database are untouched.
 */

import { onMounted, onPatched } from "@odoo/owl";
import { patch } from "@web/core/utils/patch";
import { PropertiesField } from "@web/views/fields/properties/properties_field";

const AGREEMENT_MODELS = ["agreement.agreement", "agreement.annexure"];
const MARK_CLASS = "o_agreement_required_mark";

patch(PropertiesField.prototype, {
    setup() {
        super.setup(...arguments);
        if (AGREEMENT_MODELS.includes(this.props.record?.resModel)) {
            onMounted(() => this.highlightAgreementRequired());
            onPatched(() => this.highlightAgreementRequired());
        }
    },

    /** Split the trailing "*" of each label into its own span so it can be coloured. */
    highlightAgreementRequired() {
        const labels = document.querySelectorAll(".o_field_properties .o_field_property_label > span");
        for (const label of labels) {
            if (label.querySelector(`.${MARK_CLASS}`)) {
                continue; // already split; OWL re-renders reset it, hence onPatched
            }
            const text = label.textContent;
            if (!text.endsWith(" *")) {
                continue;
            }
            label.textContent = text.slice(0, -1);
            const mark = document.createElement("span");
            mark.className = MARK_CLASS;
            mark.textContent = "*";
            label.appendChild(mark);
        }
    },
});
