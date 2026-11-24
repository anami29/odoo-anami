import re

from lxml import etree

from odoo.tests import TransactionCase, tagged

TMS_MODULES = ("tms_core", "tms_estimate", "tms_odc", "tms_profitability", "tms_contract", "tms_document_type")
# requirement codes (BR-QTN-07, KPI-21, OP-14, WF-11 ...), BRD references and section references (#9.1)
CODE = re.compile(r"\b(?:BR|KPI|OP|WF|RL|UC|SD|CR|AS)-(?:[A-Z]{2,4}-)?\d+|\bBRD\b|#\d+\.\d+")
VIEW_ATTRS = ("string", "help", "placeholder", "title", "confirm", "sum", "aria-label")


@tagged("post_install", "-at_install")
class TestNoRequirementCodes(TransactionCase):
    """Decision 10: users see no requirement code; the manual and the test specification keep them."""

    def _records(self, model):
        data = self.env["ir.model.data"].search([("module", "in", TMS_MODULES), ("model", "=", model)])
        return self.env[model].browse(data.mapped("res_id")).exists()

    def _hits(self, where, text):
        return ["%s: %s" % (where, text)] if text and CODE.search(text) else []

    def test_fields_selections_menus_actions(self):
        hits = []
        for fld in self._records("ir.model.fields"):
            hits += self._hits("%s.%s string" % (fld.model, fld.name), fld.field_description)
            hits += self._hits("%s.%s help" % (fld.model, fld.name), fld.help)
        for sel in self._records("ir.model.fields.selection"):
            hits += self._hits("%s.%s selection" % (sel.field_id.model, sel.field_id.name), sel.name)
        for menu in self._records("ir.ui.menu"):
            hits += self._hits("menu", menu.name)
        for model in ("ir.actions.act_window", "ir.actions.client", "ir.actions.server", "ir.actions.report"):
            for action in self._records(model):
                hits += self._hits(model, action.name)
                hits += self._hits(model + " help", getattr(action, "help", False))
        for mod in self.env["ir.module.module"].search([("name", "in", TMS_MODULES)]):
            hits += self._hits("module " + mod.name, mod.summary)
        self.assertFalse(hits, "Requirement codes on screen:\n" + "\n".join(hits))

    def test_views(self):
        hits = []
        for view in self._records("ir.ui.view"):
            root = etree.fromstring(view.arch_db.encode())
            for el in root.iter():
                if not isinstance(el.tag, str):  # comments and processing instructions
                    if el.tail:
                        hits += self._hits(view.xml_id, el.tail)
                    continue
                for attr in VIEW_ATTRS:
                    hits += self._hits("%s @%s" % (view.xml_id, attr), el.get(attr))
                hits += self._hits(view.xml_id, el.text)
                hits += self._hits(view.xml_id, el.tail)
        self.assertFalse(hits, "Requirement codes on screen:\n" + "\n".join(hits))

    def test_model_descriptions(self):
        hits = []
        for model in self._records("ir.model"):
            hits += self._hits("model " + model.model, model.name)
        self.assertFalse(hits, "Requirement codes on screen:\n" + "\n".join(hits))
