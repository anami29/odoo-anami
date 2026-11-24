from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class TmsDocumentTypeMixin(models.AbstractModel):
    """The Document Type of a sales order, purchase order, invoice or bill (BR-DOC-02, BR-DOC-03, BR-DOC-09)."""

    _name = "tms.document.type.mixin"
    _description = "Document type of a business document"

    tms_document_type_id = fields.Many2one(
        "tms.document.type", string="Document Type", tracking=True, index="btree_not_null",
        domain="[('id', 'in', tms_document_type_allowed_ids)]",
        default=lambda self: self.env["tms.document.type"]._tms_default_for(
            self._tms_doc_applies_to(), transport=self._tms_doc_vals_flow({})))
    tms_document_type_allowed_ids = fields.Many2many("tms.document.type", compute="_compute_tms_doc_allowed")
    tms_document_behaviour = fields.Selection(related="tms_document_type_id.behaviour", store=True,
                                              string="Document behaviour")

    # ------------------------------------------------------------------ per-model hooks
    def _tms_doc_applies_to(self):
        """The applies-to key of this record (or of a new record, from the context)."""
        raise NotImplementedError

    @api.model
    def _tms_doc_applies_to_vals(self, vals):
        return self._tms_doc_applies_to()

    def _tms_doc_is_draft(self):
        raise NotImplementedError

    def _tms_doc_links(self):
        """The transport links this document carries (BR-DOC-06, BR-DOC-09), as readable phrases."""
        return []

    @api.model
    def _tms_doc_vals_flow(self, vals):
        """True when the values come from a transport flow (they carry a transport link)."""
        return False

    @api.model
    def _tms_doc_line_vals(self, vals, *fnames):
        out = []
        for fname in fnames:
            for cmd in vals.get(fname) or []:
                if isinstance(cmd, (list, tuple)) and len(cmd) == 3 and cmd[0] in (0, 1) and isinstance(cmd[2], dict):
                    out.append(cmd[2])
        return out

    # ------------------------------------------------------------------
    def _compute_tms_doc_allowed(self):
        Type = self.env["tms.document.type"]
        for rec in self:
            applies = rec._tms_doc_applies_to()
            rec.tms_document_type_allowed_ids = Type.search([("applies_to", "=", applies)])._tms_allowed() \
                if applies else Type

    def _tms_is_standard(self):
        self.ensure_one()
        return self.tms_document_type_id.behaviour == "standard"

    @api.model
    def _tms_doc_prepare_vals(self, vals_list):
        """Fill the default type; return which records named their type themselves."""
        Type = self.env["tms.document.type"]
        explicit = []
        for vals in vals_list:
            explicit.append(bool(vals.get("tms_document_type_id")))
            if not vals.get("tms_document_type_id"):
                applies = self._tms_doc_applies_to_vals(vals)
                dtype = Type._tms_default_for(applies, transport=self._tms_doc_vals_flow(vals))
                if dtype:
                    vals["tms_document_type_id"] = dtype.id
        return explicit

    def _tms_doc_after_create(self, explicit):
        """A document a transport flow creates with links takes the Transport type, whatever the user's
        personal default (BR-DOC-03); a Standard type named on purpose with links is refused (BR-DOC-06)."""
        Type = self.env["tms.document.type"]
        for rec, named in zip(self, explicit):
            if not rec.tms_document_type_id or not rec._tms_is_standard():
                continue
            links = rec._tms_doc_links()
            if not links:
                continue
            if named and not self.env.context.get("tms_transport_flow"):
                raise ValidationError(_("A Standard document carries no transport link: %s.", ", ".join(links)))
            transport = Type._tms_default_for(rec._tms_doc_applies_to(), transport=True)
            rec.with_context(tms_doc_force=True).tms_document_type_id = transport

    def _tms_doc_check_write(self, vals):
        """BR-DOC-09: the type changes only in draft, and a Transport document with links stays Transport.
        BR-DOC-14: only an allowed type is chosen."""
        if "tms_document_type_id" not in vals or self.env.context.get("tms_doc_force"):
            return
        new = self.env["tms.document.type"].browse(vals["tms_document_type_id"])
        for rec in self:
            old = rec.tms_document_type_id
            if old == new:
                continue
            if not new:
                raise ValidationError(_("%s needs a Document Type.", rec.display_name))
            if old and not rec._tms_doc_is_draft():
                raise UserError(_("The Document Type of %s is locked once it is confirmed or posted.",
                                  rec.display_name))
            if old.behaviour == "transport" and new.behaviour == "standard":
                links = rec._tms_doc_links()
                if links:
                    raise UserError(_("%(d)s carries %(l)s: it stays a Transport document.",
                                      d=rec.display_name, l=", ".join(links)))
            if not new._tms_allowed():
                raise UserError(_("You may not choose the Document Type %s.", new.display_name))
            if old:
                rec.message_post(body=_("Document Type changed: %(o)s → %(n)s", o=old.code, n=new.code))

    def _tms_doc_check_type(self):
        """BR-DOC-02: required, and of the right kind."""
        for rec in self:
            applies = rec._tms_doc_applies_to()
            if not applies:
                continue
            if not rec.tms_document_type_id:
                raise ValidationError(_("%s needs a Document Type.", rec.display_name))
            if rec.tms_document_type_id.applies_to != applies:
                raise ValidationError(_("Document Type %(t)s does not apply to %(d)s.",
                                        t=rec.tms_document_type_id.display_name, d=rec.display_name))

    @staticmethod
    def _tms_doc_check_product(dtype, product, label):
        """BR-DOC-06 transport-only products; BR-DOC-07 allowed categories."""
        if not dtype or not product:
            return
        if dtype.behaviour == "standard" and product.tms_transport_only:
            raise ValidationError(_("%(p)s is for transport documents only (line %(l)s).",
                                    p=product.display_name, l=label))
        cats = dtype.product_category_ids
        if cats and not product.categ_id.filtered_domain([("id", "child_of", cats.ids)]):
            raise ValidationError(_("%(p)s is outside the categories %(t)s allows: %(c)s.", p=product.display_name,
                                    t=dtype.display_name, c=", ".join(cats.mapped("display_name"))))
