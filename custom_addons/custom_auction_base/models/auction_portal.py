# -*- coding: utf-8 -*-
"""Bidder-facing content: documents, clarifications and corrigenda.

Stage 12. These three models exist because a tender is not only a price:
it is a document set that bidders must be able to read, a question they
must be able to ask, and an amendment they must be told about. Running
those three things over email is where most disputes actually originate,
because none of them leaves a record that says who knew what and when.

Disclosure rules enforced here, not in a view:

  * A buyer document is visible to every invited bidder once published.
  * A bidder document is visible to that bidder and to the buying team,
    and to nobody else, ever.
  * A published clarification answer goes to EVERY bidder and NEVER names
    who asked. One bidder learning that a competitor is confused about
    clause 4 is itself commercial information.
"""
import hashlib
import logging

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

_logger = logging.getLogger(__name__)


class AuctionDocument(models.Model):
    """A file attached to an event, by either side."""

    _name = "auction.document"
    _description = "Auction Document"
    _order = "event_id, sequence, id"

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    event_id = fields.Many2one("auction.event", required=True, index=True,
                               ondelete="cascade")
    company_id = fields.Many2one(related="event_id.company_id", store=True)

    # Empty participant means the BUYER issued it. Set means a bidder
    # uploaded it, and it is then visible only to them and to the buying
    # team. This single field carries the whole visibility rule.
    participant_id = fields.Many2one(
        "auction.participant", index=True, ondelete="cascade",
        help="Empty for a document issued by the buyer. Set for one "
             "uploaded by that bidder.")
    partner_id = fields.Many2one(related="participant_id.partner_id",
                                 store=True)
    is_from_bidder = fields.Boolean(compute="_compute_origin", store=True,
                                    index=True)

    doc_type = fields.Selection(
        [("tender", "Tender document"),
         ("drawing", "Drawing or specification"),
         ("boq", "Bill of quantities"),
         ("corrigendum", "Corrigendum"),
         ("clarification", "Clarification"),
         # bidder side
         ("technical", "Technical submission"),
         ("credential", "Credential or registration"),
         ("security_proof", "Bid security proof"),
         ("other", "Other")],
        required=True, default="tender")

    # A document is UPLOADED, never picked. FIX-024.
    #
    # This was a bare Many2one to ir.attachment rendered as a picker, which
    # offered a name_search over every attachment row in the database. Two
    # things come out of that, and neither is cosmetic:
    #
    #   Odoo stores compiled asset bundles as ir.attachment, so the list
    #   filled up with web.assets_web.min.js and its siblings. Those are
    #   DELETED AND RECREATED on every asset rebuild, and this field
    #   cascades, so a tender document pointing at one would silently
    #   vanish from the event along with its audit trail.
    #
    #   Worse, it also offered every unrelated business file in the
    #   database -- an executed agreement, an invoice, an HR document --
    #   on the bidder-facing tab, with Is Published in the same row. One
    #   wrong click in a dropdown is not an acceptable distance between
    #   another client's contract and a room full of competing bidders.
    #
    # The domain is defence in depth behind a field the backend views now
    # render read-only: even reached directly, the picker can only see
    # attachments this module owns.
    upload_file = fields.Binary(
        string="Upload", attachment=False,
        help="Choose a file from your computer. It is copied into this "
             "event and hashed on save.")
    upload_name = fields.Char(string="Upload Filename")
    attachment_id = fields.Many2one(
        "ir.attachment", required=True, ondelete="cascade", readonly=True,
        domain="[('res_model', 'in', ['auction.document',"
               " 'auction.participant'])]")
    file_name = fields.Char(string="File", related="attachment_id.name",
                            readonly=True)
    file_size = fields.Integer(related="attachment_id.file_size",
                               readonly=True)
    mimetype = fields.Char(related="attachment_id.mimetype", readonly=True)

    # Integrity. A bidder who later says "that is not the drawing you sent
    # me" is answered with a digest computed at upload, not with a memory.
    sha256 = fields.Char(readonly=True, index=True,
                         help="Digest of the file at upload. BR-DOC-004.")

    is_published = fields.Boolean(
        default=False, index=True,
        help="Buyer documents are visible to bidders only once published. "
             "Bidder uploads are always visible to the buying team.")
    uploaded_by = fields.Many2one("res.users", readonly=True,
                                  default=lambda s: s.env.user)
    uploaded_on = fields.Datetime(readonly=True,
                                  default=lambda s: fields.Datetime.now())
    notes = fields.Text()

    _sql_constraints = [
        ("document_sha_event_uq",
         "unique(event_id, participant_id, sha256, doc_type)",
         "That exact file is already attached to this event under the same "
         "document type."),
    ]

    @api.depends("participant_id")
    def _compute_origin(self):
        for doc in self:
            doc.is_from_bidder = bool(doc.participant_id)

    def _attachment_from_upload(self, vals):
        """Turn an uploaded file into an attachment this module owns.

        Resolved BEFORE super().create(), because attachment_id is
        required and the NOT NULL constraint fires before any post-create
        hook could fill it. The attachment is created unowned and adopted
        by the document immediately afterwards, since the document has no
        id yet at this point.
        """
        data = vals.pop("upload_file", None)
        name = (vals.pop("upload_name", None) or "").strip()
        if not data or vals.get("attachment_id"):
            return None
        attachment = self.env["ir.attachment"].sudo().create({
            "name": name or vals.get("name") or _("Document"),
            "datas": data,
            "public": False,
        })
        vals["attachment_id"] = attachment.id
        return attachment

    @api.model_create_multi
    def create(self, vals_list):
        adopted = []
        for vals in vals_list:
            adopted.append(self._attachment_from_upload(vals))
        docs = super().create(vals_list)
        for doc, attachment in zip(docs, adopted):
            if attachment:
                # Owned by this document, so it is reachable through the
                # module's own access rules and nothing else's.
                attachment.sudo().write({"res_model": self._name,
                                         "res_id": doc.id})
            doc._compute_digest()
            doc.event_id and self.env["auction.audit"].log(
                action="document_uploaded", model=self._name, res_id=doc.id,
                event_id=doc.event_id.id,
                detail={"type": doc.doc_type,
                        "from_bidder": doc.is_from_bidder,
                        "sha256": doc.sha256})
        return docs

    def write(self, vals):
        """Replacing the file re-points the attachment and re-hashes."""
        if vals.get("upload_file"):
            for doc in self:
                single = dict(vals)
                attachment = doc._attachment_from_upload(single)
                if attachment:
                    attachment.sudo().write({"res_model": self._name,
                                             "res_id": doc.id})
                    super(AuctionDocument, doc).write(single)
                    doc._compute_digest()
            return True
        vals.pop("upload_file", None)
        vals.pop("upload_name", None)
        return super().write(vals)

    def _compute_digest(self):
        for doc in self:
            raw = doc.attachment_id.sudo().raw or b""
            doc.sudo().write({"sha256": hashlib.sha256(raw).hexdigest()})

    def action_publish(self):
        """Make a buyer document visible to invited bidders."""
        for doc in self:
            if doc.is_from_bidder:
                raise UserError(_(
                    "A bidder's own upload is not published. It is visible "
                    "to the buying team already and to nobody else."))
            doc.write({"is_published": True})
            doc.event_id._bump_content_version()
            self.env["auction.audit"].log(
                action="document_published", model=self._name, res_id=doc.id,
                event_id=doc.event_id.id, detail={"name": doc.name})

    @api.model
    def visible_to(self, participant):
        """Everything this bidder may see. The portal calls ONLY this.

        A controller that searches auction.document directly is a defect:
        field omission in a template is not a security control.
        """
        if not participant:
            return self.browse([])
        return self.sudo().search([
            ("event_id", "=", participant.event_id.id),
            "|",
            "&", ("participant_id", "=", False), ("is_published", "=", True),
            ("participant_id", "=", participant.id),
        ])


class AuctionClarification(models.Model):
    """A bidder's question and the buyer's published answer."""

    _name = "auction.clarification"
    _description = "Auction Clarification"
    _order = "event_id, published_on desc, id desc"
    _inherit = ["mail.thread"]

    name = fields.Char(readonly=True, copy=False, default=lambda s: _("New"))
    event_id = fields.Many2one("auction.event", required=True, index=True,
                               ondelete="cascade")
    company_id = fields.Many2one(related="event_id.company_id", store=True)

    # Who asked. NEVER disclosed to other bidders -- see _portal_values.
    participant_id = fields.Many2one("auction.participant", required=True,
                                     readonly=True, index=True,
                                     ondelete="restrict")
    asked_by = fields.Many2one("res.users", readonly=True)
    question = fields.Text(required=True, readonly=True)
    asked_on = fields.Datetime(readonly=True,
                               default=lambda s: fields.Datetime.now())

    reference_clause = fields.Char(
        help="The clause, drawing or line the question is about.")

    answer = fields.Text(tracking=True)
    answered_by = fields.Many2one("res.users", readonly=True, copy=False)
    answered_on = fields.Datetime(readonly=True, copy=False)
    published_on = fields.Datetime(readonly=True, copy=False, index=True)

    state = fields.Selection(
        [("asked", "Awaiting answer"),
         ("answered", "Answered, not yet published"),
         ("published", "Published to all bidders"),
         ("withheld", "Withheld")],
        required=True, default="asked", index=True, tracking=True)

    withheld_reason = fields.Text(
        help="Recorded where a question is not answered. A question that "
             "simply disappears is the thing a bidder complains about.")

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = self.env["ir.sequence"].sudo().next_by_code(
                    "auction.clarification") or "/"
        recs = super().create(vals_list)
        for rec in recs:
            self.env["auction.audit"].log(
                action="clarification_asked", model=self._name,
                res_id=rec.id, event_id=rec.event_id.id,
                detail={"participant": rec.participant_id.id})
        return recs

    def action_publish(self):
        """Publish the answer to EVERY bidder, naming nobody.

        Answering one bidder privately is how a tender gets challenged: the
        others can argue they bid against different information. The answer
        goes to all of them or it is withheld and the reason recorded.
        """
        for rec in self:
            if not (rec.answer or "").strip():
                raise UserError(_(
                    "Write the answer before publishing it."))
            rec.write({
                "state": "published",
                "answered_by": rec.answered_by.id or self.env.user.id,
                "answered_on": rec.answered_on or fields.Datetime.now(),
                "published_on": fields.Datetime.now(),
            })
            self.env["auction.audit"].log(
                action="clarification_published", model=self._name,
                res_id=rec.id, event_id=rec.event_id.id, detail={})

    def action_withhold(self):
        for rec in self:
            if not (rec.withheld_reason or "").strip():
                raise UserError(_(
                    "Record why the question is not being answered."))
            rec.write({"state": "withheld"})
            self.env["auction.audit"].log(
                action="clarification_withheld", model=self._name,
                res_id=rec.id, event_id=rec.event_id.id,
                detail={"reason": rec.withheld_reason})

    @api.model
    def published_for(self, event):
        """Published Q&A for an event, with the asker stripped."""
        recs = self.sudo().search([
            ("event_id", "=", event.id),
            ("state", "=", "published"),
        ], order="published_on desc")
        return [{
            "name": r.name,
            "reference_clause": r.reference_clause or "",
            "question": r.question,
            "answer": r.answer,
            "published_on": r.published_on,
            "published_on_display": r.published_on and r.published_on.strftime(
                "%d %b %Y, %H:%M") or "",
        } for r in recs]


class AuctionCorrigendum(models.Model):
    """A formal amendment to a published event."""

    _name = "auction.corrigendum"
    _description = "Auction Corrigendum"
    _order = "event_id, id desc"
    _inherit = ["mail.thread"]

    name = fields.Char(readonly=True, copy=False, default=lambda s: _("New"))
    event_id = fields.Many2one("auction.event", required=True, readonly=True,
                               index=True, ondelete="restrict")
    company_id = fields.Many2one(related="event_id.company_id", store=True)
    description = fields.Html(required=True)

    is_material = fields.Boolean(
        string="Material change",
        help="A change to scope, quantity, specification or commercial "
             "terms. A material corrigendum VOIDS bids already received, "
             "because they were made against different information.")

    previous_close = fields.Datetime(readonly=True)
    new_close_datetime = fields.Datetime(
        string="Extended bid close",
        help="Leave empty to leave the deadline unchanged.")

    bids_voided = fields.Integer(readonly=True)
    issued_by = fields.Many2one("res.users", readonly=True, copy=False)
    issued_on = fields.Datetime(readonly=True, copy=False)
    state = fields.Selection([("draft", "Draft"), ("issued", "Issued")],
                             required=True, default="draft", tracking=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = self.env["ir.sequence"].sudo().next_by_code(
                    "auction.corrigendum") or "/"
        return super().create(vals_list)

    @api.constrains("new_close_datetime", "event_id")
    def _check_extension(self):
        """A corrigendum may extend a deadline. It may never bring one
        forward, which is the manipulation the rule exists to stop."""
        for rec in self:
            if not rec.new_close_datetime:
                continue
            current = rec.event_id.bid_close_datetime
            if current and rec.new_close_datetime < current:
                raise ValidationError(_(
                    "A corrigendum may extend the bid close date and may "
                    "never bring it forward. The current deadline is "
                    "%(now)s and you entered %(new)s.",
                    now=current, new=rec.new_close_datetime))

    def action_issue(self):
        """Publish the amendment, void affected bids, extend the deadline.

        CVC e-procurement guidance expects a material corrigendum to carry a
        meaningful extension -- ten days is the figure in common use -- so
        that bidders can actually respond to it. The rule is NOT enforced
        here, because an SME event under no statutory obligation may
        legitimately issue a one-day correction of a typo. It is warned
        about, and the warning is recorded.
        """
        for rec in self:
            if rec.state == "issued":
                raise UserError(_("This corrigendum has already been issued."))
            event = rec.event_id
            if event.state not in ("published", "live"):
                raise UserError(_(
                    "A corrigendum applies to a published or live event. "
                    "'%(name)s' is %(state)s.",
                    name=event.display_name, state=event.state))

            voided = 0
            if rec.is_material:
                live_bids = event.bid_ids.filtered(
                    lambda b: b.state == "active")
                voided = len(live_bids)
                live_bids._set_state(
                    "void_corrigendum",
                    reason="material corrigendum %s" % rec.name)

            vals = {"state": "issued",
                    "issued_by": self.env.user.id,
                    "issued_on": fields.Datetime.now(),
                    "bids_voided": voided,
                    "previous_close": event.bid_close_datetime}
            rec.write(vals)

            if rec.new_close_datetime:
                event.sudo().write(
                    {"bid_close_datetime": rec.new_close_datetime})
                event.lot_ids.filtered(
                    lambda l: l.state in ("pending", "open")).sudo().write(
                    {"close_datetime": rec.new_close_datetime})

            event._bump_content_version()
            self.env["auction.audit"].log(
                action="corrigendum_issued", model=self._name, res_id=rec.id,
                event_id=event.id,
                detail={"material": rec.is_material,
                        "bids_voided": voided,
                        "previous_close": str(rec.previous_close or ""),
                        "new_close": str(rec.new_close_datetime or ""),
                        "extension_days": rec._extension_days()})
            rec._warn_short_extension()

    def _extension_days(self):
        self.ensure_one()
        if not (self.new_close_datetime and self.previous_close):
            return 0
        return (self.new_close_datetime - self.previous_close).days

    def _warn_short_extension(self):
        self.ensure_one()
        if not self.is_material:
            return
        days = self._extension_days()
        if days < 10:
            _logger.info(
                "Corrigendum %s is material and extends the deadline by %d "
                "days. CVC practice expects at least 10.", self.name, days)
            self.message_post(body=_(
                "This is a material corrigendum extending the deadline by "
                "%(days)d day(s). Practice under the CVC e-procurement "
                "guidelines expects at least ten days for a material "
                "change, so that bidders can respond to it. Recorded for "
                "the file.", days=days))
