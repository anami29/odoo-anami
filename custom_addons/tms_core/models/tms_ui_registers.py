"""One-page registers as drawn in the agreed mockup (TMS-UIG-001, 18.0.1.6.0): the compliance register (owned
vehicles and drivers on one page) and the charges and expenses register (pending recoveries, absorbed cost and the
leakage guard). Figures are read under the user's own access rights."""

from odoo import _, api, fields, models

DL_GROUPS = ("tms_core.group_fleet_manager", "tms_core.group_dispatcher", "tms_core.group_branch_manager",
             "tms_core.group_operations_head", "tms_core.group_tms_admin")


class TmsCockpit(models.AbstractModel):
    _inherit = "tms.cockpit"

    # ------------------------------------------------------------------ compliance register
    @api.model
    def get_compliance_register(self, company_id=False):
        company, start, _end = self._tms_scope(company_id, False)
        vehicles = self.env["fleet.vehicle"].search([("tms_ownership", "=", "owned"),
                                                     ("company_id", "in", (False, company.id))], order="license_plate")
        drivers = self.env["tms.driver"].search([("company_id", "in", (False, company.id))], order="name") \
            if "company_id" in self.env["tms.driver"]._fields else self.env["tms.driver"].search([], order="name")
        show_dl = any(self.env.user.has_group(g) for g in DL_GROUPS)
        vrows = []
        for v in vehicles:
            cells = {p: {"text": v["tms_cmp_%s_text" % p], "state": v["tms_cmp_%s_state" % p]}
                     for p in ("rc", "fit", "permit", "ins", "puc")}
            trip = v.tms_cmp_trip_id
            vrows.append({
                "id": v.id, "plate": v.license_plate or v.name, "model": v.model_id.display_name or "",
                "cells": cells, "gate": v.tms_cmp_gate, "gate_state": v.tms_cmp_gate_state,
                "trip": trip.name or "", "status": dict(v._fields["tms_availability"]._description_selection(
                    self.env)).get(v.tms_availability, "") if "tms_availability" in v._fields else "",
                "trip_open": self._tms_open("tms.order", trip.id, trip.name) if trip else False,
                "renew": v.tms_cmp_renew_code or False,
                "worst": min((c["state"] for c in cells.values()), key=lambda s: ["missing", "expired", "expiring",
                                                                                   "valid"].index(s)),
            })
        drows = []
        dl_types = dict(self.env["tms.driver"]._fields["driver_license_type"]._description_selection(self.env)) \
            if "driver_license_type" in self.env["tms.driver"]._fields and \
            self.env["tms.driver"]._fields["driver_license_type"].type == "selection" else {}
        for d in drivers:
            trip = d.tms_cmp_trip_id
            dtype = d.driver_license_type
            drows.append({
                "id": d.id, "name": d.name, "dl": (d.sudo().driver_license_number or "") if show_dl else "•••",
                "dl_type": dl_types.get(dtype, dtype) if dtype else "",
                "dl_text": d.tms_cmp_dl_text, "dl_state": d.tms_cmp_dl_state, "gate": d.tms_cmp_gate,
                "gate_state": d.tms_cmp_gate_state, "trip": trip.name or "",
                "trip_open": self._tms_open("tms.order", trip.id, trip.name) if trip else False,
                "renew": d.tms_cmp_renew_code or False, "worst": d.tms_cmp_dl_state,
            })
        types = self.env["tms.compliance.document.type"].search([("mandatory_for_start", "=", True)])
        icp = self.env["ir.config_parameter"].sudo()
        return {
            "scope": self._tms_scope_payload(company, start),
            "as_of": fields.Date.context_today(self).strftime("%d/%m/%Y"),
            "vehicles": vrows,
            "drivers": drows,
            "legend": _("The gate checks %(n)s documents: %(d)s. Expired blocks the trip start; expiring = within 30 "
                        "days, a warning. At the start, a document expiring within %(s)s security days after the "
                        "trip's scheduled end warns without blocking. Market vehicles carry their documents on each "
                        "assignment, not in this register.", n=len(types), d=", ".join(types.mapped("name")),
                        s=icp.get_param("tms.default_vehicle_insurance_security_days") or "0"),
        }

    # ------------------------------------------------------------------ charges and expenses register
    @api.model
    def get_charges_register(self, company_id=False, period=False, pending_only=False):
        company, start, end = self._tms_scope(company_id, period)
        Charge = self.env["tms.trip.charge"]
        Expense = self.env["tms.trip.expense"]
        dstart, dend = self._tms_bounds(start, end)
        pending = [("company_id", "=", company.id), ("state", "=", "approved")]
        cust = Charge.search(pending + [("recover_from", "=", "customer")])
        vend = Charge.search(pending + [("recover_from", "!=", "customer")])
        cdomain = [("company_id", "=", company.id), ("state", "not in", ("draft", "cancel"))]
        if pending_only:
            cdomain += [("state", "=", "approved")]
        else:
            cdomain += ["|", ("state", "=", "approved"), "&", ("create_date", ">=", dstart), ("create_date", "<", dend)]
        charges = Charge.search(cdomain, order="create_date desc", limit=200)
        month_exp = Expense.search([("company_id", "=", company.id), ("state", "!=", "cancel"),
                                    ("date", ">=", start), ("date", "<", end)])
        absorbed = month_exp.filtered(lambda e: e.recoverable_from in (False, "none")
                                      and e.incident_id and e.incident_id.responsible == "company")
        recoverable = Expense.search([("company_id", "=", company.id), ("state", "!=", "cancel"),
                                      ("recoverable_from", "not in", (False, "none"))], order="date desc", limit=200)
        if not pending_only:
            recoverable = recoverable.filtered(lambda e: e.date >= start and e.date < end or (
                e.charge_id.state in ("draft", "to_approve", "approved") if e.charge_id else True))
        leaks = recoverable.filtered(lambda e: not e.charge_id)
        states = dict(Charge._fields["state"]._description_selection(self.env))
        billing = dict(Charge._fields["billing_detail"]._description_selection(self.env))
        crows = [{
            "id": c.id, "ref": c.name, "open": self._tms_open("tms.trip.charge", c.id, c.name),
            "trip": c.trip_id.name or "", "trip_open": self._tms_open("tms.order", c.trip_id.id, c.trip_id.name)
            if c.trip_id else False, "category": c.category_id.name or "", "incurred": c.incurred_amount,
            "customer": c.customer_amount, "vendor": c.vendor_amount, "treatment": c.treatment_label,
            "recover_from": c.recover_from, "billing": billing.get(c.billing_detail, ""),
            "billing_key": c.billing_detail, "state": states.get(c.state, ""),
        } for c in charges]
        erows = [{
            "id": e.id, "ref": e.name, "open": self._tms_open("tms.trip.expense", e.id, e.name),
            "type": e.category_id.name or "", "paid_to": e.paid_to_id.name or dict(
                e._fields["payment_mode"]._description_selection(self.env)).get(e.payment_mode, ""),
            "amount": e.amount, "customer": e.recoverable_amount if e.recoverable_from == "customer" else 0.0,
            "vendor": e.recoverable_amount if e.recoverable_from not in ("customer", False, "none") else 0.0,
            "status": e.recovery_status or "", "leak": not e.charge_id,
        } for e in recoverable]
        return {
            "scope": self._tms_scope_payload(company, start),
            "pending_only": bool(pending_only),
            "tiles": [
                {"key": "customers", "label": _("Pending from customers"), "value": sum(cust.mapped("amount")),
                 "sub": (_("1 line") if len(cust) == 1 else _("%s lines", len(cust))) + " · " + _(
                     "bill on the next freight invoice"),
                 "action": {"model": "tms.trip.charge", "name": _("Pending from customers"),
                            "domain": [("id", "in", cust.ids)]}},
                {"key": "vendors", "label": _("Pending from vendors"), "value": sum(vend.mapped("amount")),
                 "sub": _("1 charge to deduct") if len(vend) == 1 else _("%s charges to deduct", len(vend)),
                 "action": {"model": "tms.trip.charge", "name": _("Pending from vendors"),
                            "domain": [("id", "in", vend.ids)]}},
                {"key": "absorbed", "label": _("Absorbed this month"), "value": sum(absorbed.mapped("amount")),
                 "sub": _("own-company responsibility"),
                 "action": {"model": "tms.trip.expense", "name": _("Absorbed this month"),
                            "domain": [("id", "in", absorbed.ids)]}},
                {"key": "leakage", "label": _("Leakage guard"), "value": len(leaks), "count": True,
                 "sub": _("recoverable but no recovery charge raised"),
                 "action": {"model": "tms.trip.expense", "name": _("Leakage guard"),
                            "domain": [("id", "in", leaks.ids)]}},
            ],
            "charges": crows,
            "expenses": erows,
        }
