# Copyright 2026 RLFB (Riamona Luxury and Fashion Brands), https://www.riamona.com
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
from odoo import _, api, fields, models

from .tools import inr

RECOVERY_KINDS = ("recovery", "replacement")


class AccountMove(models.Model):
    """The hire bill shows its three-way match: hold badge, match lines, totals and the tolerance rule."""

    _inherit = "account.move"

    tms_match_state = fields.Selection(
        [("hold", "On hold · three-way match"), ("passed", "Match passed")],
        compute="_compute_tms_match_summary", string="Three-way match status")
    tms_match_line_ids = fields.Many2many(
        "tms.vendor.bill.match.line", compute="_compute_tms_match_summary", string="Match lines")
    tms_match_approved = fields.Monetary(compute="_compute_tms_match_summary", string="Approved commitment")
    tms_match_recoveries = fields.Monetary(compute="_compute_tms_match_summary", string="Recoveries")
    tms_match_net_payable = fields.Monetary(compute="_compute_tms_match_summary", string="Net payable")
    tms_match_bill_amount = fields.Monetary(compute="_compute_tms_match_summary", string="Bill amount")
    tms_match_variance = fields.Monetary(compute="_compute_tms_match_summary", string="Variance")
    tms_match_pod_ok = fields.Boolean(compute="_compute_tms_match_summary", string="PODs verified")
    tms_match_tolerance_note = fields.Char(compute="_compute_tms_match_summary", string="Tolerance rule")
    tms_hire_memo_refs = fields.Char(compute="_compute_tms_match_summary", string="Hire memo")
    tms_ui_trip_count = fields.Integer(compute="_compute_tms_match_summary", string="Trip count")
    tms_ui_hire_memo_count = fields.Integer(compute="_compute_tms_match_summary", string="Hire memo count")

    @api.depends("move_type", "amount_untaxed", "company_id", "invoice_line_ids.purchase_line_id",
                 "invoice_line_ids.tms_trip_id", "tms_match_ids.state", "tms_match_ids.pod_ok",
                 "tms_match_ids.line_ids.approved", "tms_match_ids.line_ids.result")
    def _compute_tms_match_summary(self):
        for move in self:
            matches = move.tms_match_ids
            lines = matches.mapped("line_ids")
            memos = move.tms_hire_memo_ids
            company = move.company_id or self.env.company
            if not matches:
                state = False
            elif any(m.state == "blocked" for m in matches):
                state = "hold"
            else:
                state = "passed"
            approved = sum(lines.filtered(lambda line: line.kind not in RECOVERY_KINDS).mapped("approved"))
            recoveries = sum(lines.filtered(lambda line: line.kind in RECOVERY_KINDS).mapped("approved"))
            net = approved + recoveries
            bill = move.amount_untaxed if move.is_invoice(include_receipts=True) else 0.0
            move.tms_match_state = state
            move.tms_match_line_ids = lines
            move.tms_match_approved = approved
            move.tms_match_recoveries = recoveries
            move.tms_match_net_payable = net
            move.tms_match_bill_amount = bill
            move.tms_match_variance = bill - net if matches else 0.0
            move.tms_match_pod_ok = bool(matches) and all(matches.mapped("pod_ok"))
            move.tms_match_tolerance_note = _(
                "Tolerance per element: %(amt)s or %(pct)s%% of the approved amount, whichever is lower",
                amt=inr(company.tms_hire_tolerance_amount),
                pct=("%.2f" % company.tms_hire_tolerance_percent).rstrip("0").rstrip("."))
            move.tms_hire_memo_refs = " · ".join(
                " · ".join(filter(None, [po.name, po.tms_vehicle_number, po.tms_route])) for po in memos) or False
            move.tms_ui_trip_count = len(move._tms_ui_trips())
            move.tms_ui_hire_memo_count = len(memos)

    def _tms_ui_trips(self):
        self.ensure_one()
        return self.tms_trip_ids | self.tms_hire_memo_ids.mapped("tms_trip_id") | self.tms_match_ids.mapped("trip_id")

    def action_tms_rerun_match(self):
        """Re-run the three-way match of each hire memo billed on this bill."""
        Match = self.env["tms.vendor.bill.match"]
        for move in self.filtered(lambda m: m.move_type == "in_invoice"):
            if move.state == "draft":
                for memo in move.tms_hire_memo_ids:
                    if not move.tms_match_ids.filtered(lambda m, po=memo: m.purchase_id == po):
                        Match.create({"move_id": move.id, "purchase_id": memo.id})
            move.tms_match_ids.action_refresh()
        return True

    def action_tms_apply_recoveries(self):
        """Approved vendor recoveries enter the draft bill as deduction lines."""
        self.mapped("tms_match_ids").action_apply_recoveries()
        return True

    def action_tms_view_trips(self):
        self.ensure_one()
        trips = self._tms_ui_trips()
        action = {"type": "ir.actions.act_window", "name": _("Trips"), "res_model": "tms.order",
                  "view_mode": "list,form", "domain": [("id", "in", trips.ids)]}
        if len(trips) == 1:
            action.update({"view_mode": "form", "res_id": trips.id})
        return action

    def action_tms_view_hire_memos(self):
        self.ensure_one()
        memos = self.tms_hire_memo_ids
        action = {"type": "ir.actions.act_window", "name": _("Hire memos"), "res_model": "purchase.order",
                  "view_mode": "list,form", "domain": [("id", "in", memos.ids)]}
        if len(memos) == 1:
            action.update({"view_mode": "form", "res_id": memos.id})
        return action
