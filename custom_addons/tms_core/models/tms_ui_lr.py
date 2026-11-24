"""LR screens as drawn in the agreed mockup (TMS-UIG-001, 18.0.1.6.0): list columns, banners, smart buttons."""

from odoo import _, api, fields, models

from .tools import inr

DEAD_EWB = ("cancelled", "expired")


class TmsConsignment(models.Model):
    _inherit = "tms.consignment"

    trip_display = fields.Char(compute="_compute_trip_display", string="Trip")
    ewaybill_lines_needed = fields.Integer(compute="_compute_ewaybill_ui")
    ewaybill_lines_covered = fields.Integer(compute="_compute_ewaybill_ui")
    ewaybill_coverage = fields.Char(compute="_compute_ewaybill_ui", string="E-way bills",
                                    help="Lines with a live e-way bill against the lines that need one.")
    ewaybill_banner = fields.Text(compute="_compute_ewaybill_ui")
    claim_count = fields.Integer(compute="_compute_claim_count")
    invoice_eligible = fields.Boolean(compute="_compute_invoice_eligible")
    invoice_hint = fields.Char(compute="_compute_invoice_eligible")
    plan_banner = fields.Char(compute="_compute_plan_banner")
    lr_series = fields.Char(compute="_compute_lr_series", string="Sequence")

    @api.depends("trip_link_ids.state", "trip_link_ids.trip_id.name")
    def _compute_trip_display(self):
        for lr in self:
            trips = lr.trip_link_ids.filtered(lambda link: link.state != "cancel").mapped("trip_id")
            lr.trip_display = ", ".join(trips.mapped("name"))

    @api.depends("line_ids.ewaybill_id.state", "line_ids.ewaybill_exempt", "line_ids.declared_value",
                 "ewaybill_required", "origin_id.state_id", "destination_id.state_id")
    def _compute_ewaybill_ui(self):
        for lr in self:
            lines = lr.line_ids.filtered(lambda line: not line.ewaybill_exempt) if lr.ewaybill_required else \
                lr.line_ids.browse()
            covered = lines.filtered(lambda line: line.ewaybill_id and line.ewaybill_id.state not in DEAD_EWB)
            lr.ewaybill_lines_needed = len(lines)
            lr.ewaybill_lines_covered = len(covered)
            lr.ewaybill_coverage = "%d / %d" % (len(covered), len(lines)) if lines else ""
            missing = lines - covered
            if not missing or lr.state == "cancel":
                lr.ewaybill_banner = False
                continue
            o_state, d_state = lr.origin_id.state_id, lr.destination_id.state_id
            movement = _("Movement is inter-state (%(o)s → %(d)s)", o=o_state.name, d=d_state.name) \
                if o_state and d_state and o_state != d_state else _("Movement is within %s", o_state.name or _("the state"))
            threshold = lr._tms_ewaybill_threshold()
            parts = []
            for line in missing:
                index = list(lr.line_ids).index(line) + 1 if line in lr.line_ids else 0
                parts.append(_("Line %(n)s (%(c)s, declared %(v)s) has no e-way bill number.", n=index,
                               c=line.commodity or "", v=inr(line.declared_value)))
            tail = _("%(m)s, above the %(t)s threshold.", m=movement, t=inr(threshold)) if threshold else movement + "."
            lr.ewaybill_banner = " ".join(parts) + " " + tail

    @api.depends("claim_ids")
    def _compute_claim_count(self):
        for lr in self:
            lr.claim_count = len(lr.claim_ids)

    @api.depends("state", "freight_basis", "pod_status", "freight_uninvoiced")
    def _compute_invoice_eligible(self):
        Wizard = self.env["tms.invoice.lr.wizard"]
        for lr in self:
            eligible = bool(lr.id) and Wizard._tms_eligible(lr)
            lr.invoice_eligible = eligible
            if eligible or lr.state in ("draft", "cancel", "invoiced"):
                lr.invoice_hint = False
            elif lr.freight_basis == "topay":
                lr.invoice_hint = _("To-Pay freight is invoiced at delivery.")
            elif lr.freight_basis == "tbb":
                lr.invoice_hint = _("Invoiced once the POD is verified.")
            else:
                lr.invoice_hint = False

    @api.depends("state", "trip_link_ids.state", "trip_link_ids.trip_id.state_code", "unplanned_packages")
    def _compute_plan_banner(self):
        for lr in self:
            trips = lr.trip_link_ids.filtered(lambda link: link.state != "cancel").mapped("trip_id")
            if lr.state in ("cancel", "invoiced", "delivered"):
                lr.plan_banner = False
            elif not trips:
                lr.plan_banner = _("Not planned on a trip yet. Confirm the LR, then use Plan Trip.") \
                    if lr.state == "draft" else _("Not planned on a trip yet: use Plan Trip.")
            elif lr.state in ("draft", "confirmed"):
                lr.plan_banner = _("Planned on trip %s. The LR moves to Dispatched when the trip starts.",
                                   ", ".join(trips.mapped("name")))
            else:
                lr.plan_banner = False

    @api.depends("company_id")
    def _compute_lr_series(self):
        for lr in self:
            seq = lr.company_id.tms_lr_sequence_id
            lr.lr_series = "%s%s" % (seq.prefix or "", "0" * (seq.padding - 1) + "1") if seq else False

    def _tms_rate_label(self):
        """How the LR is priced, for the invoice header: the typed rate here; tms_estimate names the order item."""
        self.ensure_one()
        if self.rate_type:
            return "%s %s" % (inr(self.typed_rate),
                              dict(self._fields["rate_type"]._description_selection(self.env))[self.rate_type])
        return False

    def action_plan_trip(self):
        lrs = self.filtered(lambda lr: lr.state == "confirmed" and lr.unplanned_packages > 0)
        return {"type": "ir.actions.act_window", "name": _("Plan trip"), "res_model": "tms.plan.trip.wizard",
                "view_mode": "form", "target": "new",
                "context": {"active_model": self._name, "active_ids": (lrs or self).ids}}

    def action_open_trip(self):
        self.ensure_one()
        trips = self.trip_ids
        if len(trips) == 1:
            return {"type": "ir.actions.act_window", "res_model": "tms.order", "res_id": trips.id,
                    "view_mode": "form"}
        return self.action_view_trips()

    def action_view_ewaybills(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "name": _("E-way bills"), "res_model": "tms.ewaybill",
                "view_mode": "list,form", "domain": [("consignment_id", "=", self.id)],
                "context": {"default_consignment_id": self.id}}

    def action_view_claims(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "name": _("Claims"), "res_model": "tms.damage.claim",
                "view_mode": "list,form", "domain": [("consignment_id", "=", self.id)],
                "context": {"default_consignment_id": self.id}}


class TmsConsignmentLine(models.Model):
    _inherit = "tms.consignment.line"

    ewaybill_needed = fields.Boolean(compute="_compute_ewaybill_needed",
                                     help="The LR needs an e-way bill and this line has no live one.")

    @api.depends("ewaybill_id.state", "ewaybill_exempt", "consignment_id.ewaybill_required")
    def _compute_ewaybill_needed(self):
        for line in self:
            line.ewaybill_needed = bool(line.consignment_id.ewaybill_required and not line.ewaybill_exempt and (
                not line.ewaybill_id or line.ewaybill_id.state in DEAD_EWB))
