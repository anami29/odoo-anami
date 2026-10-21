from collections import OrderedDict

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.service.model import get_public_method


class TmsReasonWizard(models.TransientModel):
    """Asks for the reason every override, rejection, removal or waiver logs (BR-AUD-06)."""

    _name = "tms.reason.wizard"
    _description = "Reason"

    res_model = fields.Char(required=True)
    res_ids = fields.Char(required=True)
    method = fields.Char(required=True)
    reason = fields.Text(required=True)
    title = fields.Char()
    show_waive = fields.Boolean()
    waive = fields.Boolean(string="Waive the resulting charge")

    def action_confirm(self):
        self.ensure_one()
        ids = [int(x) for x in self.res_ids.split(",") if x]
        if not self.method.startswith("action_") or self.res_model not in self.env:
            raise UserError(_("No action to run."))
        records = self.env[self.res_model].browse(ids).with_context(tms_reason=self.reason)
        get_public_method(records, self.method)  # never a private method
        method = getattr(records, self.method)
        if self.show_waive:
            return method(reason=self.reason, waive=self.waive) or {"type": "ir.actions.act_window_close"}
        return method() or {"type": "ir.actions.act_window_close"}

    @api.model
    def open_for(self, records, method, title, show_waive=False):
        return {
            "type": "ir.actions.act_window",
            "name": title,
            "res_model": self._name,
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_res_model": records._name,
                "default_res_ids": ",".join(str(i) for i in records.ids),
                "default_method": method,
                "default_title": title,
                "default_show_waive": show_waive,
            },
        }


class TmsPlanTripWizard(models.TransientModel):
    """BR-TRP-07: Plan Trip on selected confirmed LRs creates a draft trip with their links."""

    _name = "tms.plan.trip.wizard"
    _description = "Plan trip"

    consignment_ids = fields.Many2many("tms.consignment", string="LRs")
    fleet_type = fields.Selection([("owned", "Owned"), ("market", "Market")], default="owned", required=True)
    planned_vehicle_id = fields.Many2one("fleet.vehicle", domain=[("tms_ownership", "=", "owned")])
    planned_driver_id = fields.Many2one("tms.driver")
    planned_market_vehicle_id = fields.Many2one("tms.market.vehicle")
    broker_id = fields.Many2one("res.partner")
    planned_hire_amount = fields.Float()
    origin_id = fields.Many2one("res.partner", domain=[("tms_location", "=", True)])
    destination_id = fields.Many2one("res.partner", domain=[("tms_location", "=", True)])
    scheduled_date_start = fields.Datetime(default=fields.Datetime.now, required=True)
    scheduled_duration = fields.Float(default=24.0, string="Scheduled duration (hours)")

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        ids = self.env.context.get("active_ids") or []
        if self.env.context.get("active_model") == "tms.consignment" and ids:
            lrs = self.env["tms.consignment"].browse(ids)
            res["consignment_ids"] = [(6, 0, lrs.ids)]
            res.setdefault("origin_id", lrs[:1].origin_id.id)
            res.setdefault("destination_id", lrs[-1:].destination_id.id)
            if self.env.company.tms_operation_mode == "broker":
                res["fleet_type"] = "market"
        return res

    def action_plan(self):
        self.ensure_one()
        bad = self.consignment_ids.filtered(lambda lr: lr.state != "confirmed" or lr.unplanned_packages <= 0)
        if bad:
            raise UserError(_("Only confirmed LRs with unplanned packages can be planned (BR-TRP-04): %s",
                              ", ".join(bad.mapped("name"))))
        cargo = "odc" if "odc" in self.consignment_ids.mapped("tms_cargo_type") else "regular"
        trip = self.env["tms.order"].create({
            "fleet_type": self.fleet_type,
            "planned_vehicle_id": self.planned_vehicle_id.id,
            "planned_driver_id": self.planned_driver_id.id,
            "planned_market_vehicle_id": self.planned_market_vehicle_id.id,
            "broker_id": self.broker_id.id,
            "planned_hire_amount": self.planned_hire_amount,
            "origin_id": self.origin_id.id,
            "destination_id": self.destination_id.id,
            "scheduled_date_start": self.scheduled_date_start,
            "scheduled_duration": self.scheduled_duration,
            "tms_cargo_type": cargo,
            "consignment_link_ids": [(0, 0, {
                "consignment_id": lr.id,
                "packages": lr.unplanned_packages,
                "weight": lr.charged_weight * (lr.unplanned_packages / lr.total_packages if lr.total_packages else 1),
                "drop_sequence": (i + 1) * 10,
            }) for i, lr in enumerate(self.consignment_ids)],
        })
        return {"type": "ir.actions.act_window", "res_model": "tms.order", "res_id": trip.id, "view_mode": "form"}


class TmsBreakdownResolveWizard(models.TransientModel):
    """BR-ASG-05..13: repair and continue, or replace with an own or a market vehicle."""

    _name = "tms.breakdown.resolve.wizard"
    _description = "Resolve breakdown"

    breakdown_id = fields.Many2one("tms.trip.breakdown", required=True)
    trip_id = fields.Many2one(related="breakdown_id.trip_id")
    resolution = fields.Selection(
        [("repair", "Repair and continue"), ("replace_own", "Replace with own vehicle"),
         ("replace_market", "Replace with market vehicle")],
        required=True,
        default="repair",
    )
    resumed_at = fields.Datetime(default=fields.Datetime.now)
    repair_cost = fields.Float()
    reason = fields.Selection([("breakdown", "Breakdown"), ("accident", "Accident")], default="breakdown")
    odometer_at_breakdown = fields.Float(help="Closing reading of the broken-down owned vehicle.")
    vehicle_id = fields.Many2one("fleet.vehicle", domain=[("tms_ownership", "=", "owned")])
    driver_id = fields.Many2one("tms.driver")
    replacement_odometer = fields.Float()
    market_vehicle_id = fields.Many2one("tms.market.vehicle")
    market_driver_name = fields.Char()
    market_driver_phone = fields.Char()
    market_driver_licence = fields.Char()
    market_driver_licence_expiry = fields.Date()
    broker_id = fields.Many2one("res.partner")
    hire_amount = fields.Float(string="Hire for the remaining leg")
    km_run = fields.Float(string="Km run by the replaced market vehicle")
    km_planned = fields.Float(string="Planned km of the replaced market vehicle")

    def action_resolve(self):
        self.ensure_one()
        bd = self.breakdown_id
        trip = bd.trip_id
        if bd.state != "open":
            raise UserError(_("This breakdown is already resolved."))
        bd.write({"resolution": self.resolution, "repair_cost": self.repair_cost or bd.repair_cost})
        if self.resolution == "repair":
            bd._tms_close(self.resumed_at)
            return {"type": "ir.actions.act_window_close"}
        old = trip.open_assignment_id
        vals = {"reason": self.reason}
        if old.fleet_type == "owned" and self.odometer_at_breakdown:
            old.vehicle_id.tms_record_odometer(self.odometer_at_breakdown)
            vals["previous_odometer_in"] = self.odometer_at_breakdown
        if self.resolution == "replace_own":
            if not (self.vehicle_id and self.driver_id):
                raise UserError(_("Choose the replacement vehicle and driver."))
            if self.replacement_odometer:
                self.vehicle_id.tms_record_odometer(self.replacement_odometer)
            vals.update({"fleet_type": "owned", "vehicle_id": self.vehicle_id.id, "driver_id": self.driver_id.id,
                         "odometer_out": self.replacement_odometer})
            asg, _result = trip._tms_open_assignment(vals)  # BR-ASG-06: the gate runs first
        else:
            if not (self.market_vehicle_id and self.broker_id):
                raise UserError(_("Choose the market vehicle and its broker."))
            vals.update({
                "fleet_type": "market",
                "market_vehicle_id": self.market_vehicle_id.id,
                "vehicle_number": self.market_vehicle_id.name,
                "market_driver_name": self.market_driver_name,
                "market_driver_phone": self.market_driver_phone,
                "market_driver_licence": self.market_driver_licence,
                "market_driver_licence_expiry": self.market_driver_licence_expiry,
                "broker_id": self.broker_id.id,
            })
            asg, _result = trip._tms_open_assignment(vals)
            # BR-ASG-07: its own hire memo for the remaining leg
            product = self.env.ref("tms_core.product_vehicle_hire")
            memo = self.env["purchase.order"].create({
                "partner_id": self.broker_id.id,
                "company_id": trip.company_id.id,
                "tms_is_hire_memo": True,
                "tms_trip_id": trip.id,
                "tms_assignment_id": asg.id,
                "tms_vehicle_number": self.market_vehicle_id.name,
                "tms_driver_name": self.market_driver_name,
                "tms_route": _("Replacement leg from %s", bd.location),
                "order_line": [(0, 0, {"product_id": product.id, "name": _("Replacement hire %s", trip.name),
                                       "product_qty": 1, "price_unit": self.hire_amount,
                                       "tms_hire_element": "hire"})],
            })
            asg.hire_memo_id = memo
        # BR-ASG-11: a replaced market vehicle's hire is settled pro rata
        if old.fleet_type == "market" and old.hire_memo_id:
            memo = old.hire_memo_id
            if memo.tms_full_hire_on_replacement:
                memo.tms_pro_rata_fraction = 1.0  # the memo's terms pay the full hire
                memo._tms_receive_hire()
            else:
                km_run = self.km_run or old.km_run
                km_planned = self.km_planned or trip._tms_planned_km()
                if not km_planned:
                    raise UserError(_("Enter the planned km of the replaced vehicle's leg to settle its hire pro "
                                      "rata (BR-ASG-11)."))
                memo.tms_settle_pro_rata(km_run, km_planned)
        # BR-ASG-08: Part-B tasks for every bill on board
        trip._tms_create_partb_tasks(asg.vehicle_number or "", assignment=asg)
        # BR-ASG-12: Vehicle Replacement incident
        category = self.env.ref("tms_core.tms_category_vehicle_replacement")
        self.env["tms.trip.incident"].create({
            "trip_id": trip.id,
            "category_id": category.id,
            "location": bd.location,
            "description": _("Replaced %(old)s with %(new)s", old=old.vehicle_number, new=asg.vehicle_number),
            "breakdown_id": bd.id,
        })
        bd.replacement_assignment_id = asg
        bd._tms_close(self.resumed_at)
        return {"type": "ir.actions.act_window_close"}


class TmsInvoiceLrWizard(models.TransientModel):
    """BR-INV-01..06: freight is invoiced from LRs only."""

    _name = "tms.invoice.lr.wizard"
    _description = "Invoice LRs"

    consignment_ids = fields.Many2many("tms.consignment", string="LRs")
    invoice_date = fields.Date(default=fields.Date.context_today, required=True)
    consolidate = fields.Boolean(string="One invoice per billing party", default=True)

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        if self.env.context.get("active_model") == "tms.consignment":
            res["consignment_ids"] = [(6, 0, self.env.context.get("active_ids") or [])]
        return res

    def action_open(self, lrs):
        wiz = self.create({"consignment_ids": [(6, 0, lrs.ids)]})
        return {"type": "ir.actions.act_window", "res_model": self._name, "res_id": wiz.id, "view_mode": "form",
                "target": "new"}

    @api.model
    def _tms_eligible(self, lr):
        if lr.state in ("draft", "cancel") or lr.freight_uninvoiced <= 0.005:
            return False
        if lr.freight_basis == "paid":
            return True  # BR-INV-01: at booking
        if lr.freight_basis == "topay":
            return lr.state in ("dispatched", "delivered", "not_delivered")  # BR-INV-02: at delivery
        return lr.state in ("delivered", "not_delivered") and lr.pod_status == "verified"  # BR-INV-03

    @api.model
    def _tms_taxes(self, company, mode, treatment="gta"):
        """BR-INV-05, BR-INV-09: forward charge at 5% or 18%, reverse charge without tax, pure agent without tax."""
        if mode in ("rcm", "exempt") or treatment == "pure_agent":
            return self.env["account.tax"]
        amount = 5.0 if mode == "fcm5" else 18.0
        # the intra-state CGST+SGST group of the Indian chart; the inter-state fiscal position maps it to IGST
        tax = self.env["account.chart.template"].with_company(company).ref(
            "sgst_sale_%d" % amount, raise_if_not_found=False)
        if tax:
            return tax
        Tax = self.env["account.tax"]
        domain = [*Tax._check_company_domain(company), ("type_tax_use", "=", "sale"), ("amount", "=", amount)]
        return Tax.search(domain + [("amount_type", "=", "group")], limit=1) or Tax.search(
            domain + [("amount_type", "=", "percent")], limit=1)

    @api.model
    def _tms_fiscal_position(self, company, partner):
        """BR-INV-05: place of supply decides CGST+SGST or IGST through the l10n_in fiscal positions."""
        return self.env["account.fiscal.position"].with_company(company)._get_fiscal_position(partner)

    @api.model
    def _tms_map_line_taxes(self, line_vals, fpos):
        if not fpos:
            return line_vals
        Tax = self.env["account.tax"]
        for command in line_vals:
            vals = command[2]
            for tax_command in vals.get("tax_ids") or []:
                if tax_command[0] == 6:
                    vals["tax_ids"] = [(6, 0, fpos.map_tax(Tax.browse(tax_command[2])).ids)]
        return line_vals

    @api.model
    def _tms_gst_mode(self, lr):
        option = lr.company_id.tms_get_gta_option(lr.date)
        if option != "rcm":
            return option
        party = lr.billing_partner_id.commercial_partner_id
        return "rcm" if party.vat and not party.tms_unregistered else "exempt"

    @api.model
    def _tms_freight_line_vals(self, lr, taxes):
        product = lr.company_id.tms_freight_product_id or self.env.ref("tms_core.product_freight")
        label = _("LR %(no)s dt %(date)s, %(o)s → %(d)s, %(p)s pkgs, %(w)s kg",
                  no=lr.name, date=lr.date, o=lr.origin_id.city or lr.origin_id.name or "",
                  d=lr.destination_id.city or lr.destination_id.name or "", p=lr.total_packages,
                  w=lr.charged_weight)
        vals = {
            "product_id": product.id,
            "name": label,
            "quantity": 1,
            "price_unit": lr.freight_uninvoiced,
            "tax_ids": [(6, 0, taxes.ids)],
            "tms_consignment_id": lr.id,
            "tms_line_kind": "freight",
            "tms_trip_id": lr.trip_ids[:1].id if len(lr.trip_ids) == 1 else False,
        }
        if not lr._tms_has_order_item() and lr.rate_type:
            vals["name"] += _(", typed rate %(r)s %(t)s", r=lr.typed_rate,
                              t=dict(lr._fields["rate_type"].selection)[lr.rate_type])
        return vals

    @api.model
    def _tms_charge_line_vals(self, charge, mode):
        taxes = self._tms_taxes(charge.company_id, mode, charge.category_id.gst_treatment)
        return {
            "product_id": charge.category_id.product_id.id or False,
            "name": _("%(c)s %(cat)s on LR %(lr)s", c=charge.name, cat=charge.category_id.name,
                      lr=charge.consignment_id.name),
            "quantity": 1,
            "price_unit": charge.amount,
            "account_id": charge._tms_income_account().id,
            "tax_ids": [(6, 0, taxes.ids)],
            "tms_consignment_id": charge.consignment_id.id,
            "tms_charge_id": charge.id,
            "tms_trip_id": charge.trip_id.id,
            "tms_line_kind": "dead_freight" if charge.category_id.code == "DEADFREIGHT" else "charge",
        }

    def action_create_invoices(self):
        self.ensure_one()
        moves = self._tms_create_invoices(self.consignment_ids, self.invoice_date, self.consolidate)
        return {"type": "ir.actions.act_window", "name": _("Invoices"), "res_model": "account.move",
                "view_mode": "list,form", "domain": [("id", "in", moves.ids)]}

    def _tms_create_invoices(self, lrs, invoice_date=None, consolidate=True):
        eligible = lrs.filtered(self._tms_eligible)
        if not eligible:
            raise UserError(_("None of the selected LRs can be invoiced now (BR-INV-01..03)."))
        groups = OrderedDict()
        for lr in eligible:
            key = (lr.company_id.id, lr.billing_partner_id.commercial_partner_id.id, self._tms_gst_mode(lr),
                   lr._tms_invoice_group_key())
            if not consolidate:
                key += (lr.id,)
            groups.setdefault(key, self.env["tms.consignment"])
            groups[key] |= lr
        moves = self.env["account.move"]
        for (company_id, partner_id, mode, *_rest), group in groups.items():
            company = self.env["res.company"].browse(company_id)
            env_model = self.env["account.move"]
            # BR-INV-02: the delivery branch's clerk invoices To-Pay LRs in the booking branch's journal
            if company not in self.env.user.company_ids:
                env_model = env_model.sudo()
            env_model = env_model.with_company(company)
            line_vals = []
            for lr in group:
                taxes = self._tms_taxes(company, mode)
                line_vals.append((0, 0, self._tms_freight_line_vals(lr, taxes)))
                for charge in lr.charge_ids.filtered(lambda c: c.recover_from == "customer" and c.state == "approved"
                                                     and not c.move_line_id):
                    line_vals.append((0, 0, self._tms_charge_line_vals(charge, mode)))
            partner = self.env["res.partner"].browse(partner_id)
            fpos = self._tms_fiscal_position(company, group[:1].billing_partner_id or partner)
            self._tms_map_line_taxes(line_vals, fpos)
            move = env_model.create({
                "move_type": "out_invoice",
                "partner_id": partner_id,
                "fiscal_position_id": fpos.id,
                "invoice_date": invoice_date or fields.Date.context_today(self),
                "journal_id": company.tms_branch_journal("sale").id,
                "tms_gst_mode": mode,
                "invoice_origin": ", ".join(group.mapped("name")),
                "invoice_line_ids": line_vals,
                **group[:1]._tms_invoice_header_vals(),
            })
            for line in move.invoice_line_ids.filtered("tms_charge_id"):
                line.tms_charge_id.sudo().move_line_id = line
            moves |= move
        return moves


class TmsLrAmendmentWizard(models.TransientModel):
    """BR-LR-17."""

    _name = "tms.lr.amendment.wizard"
    _description = "LR amendment request"

    consignment_id = fields.Many2one("tms.consignment", required=True)
    reason = fields.Text(required=True)
    new_consignee_id = fields.Many2one("res.partner")
    new_destination_id = fields.Many2one("res.partner", domain=[("tms_location", "=", True)])
    is_diversion = fields.Boolean()
    diversion_km = fields.Float()
    new_actual_weight = fields.Float()
    new_packages = fields.Integer()

    def action_request(self):
        self.ensure_one()
        if self.consignment_id.state in ("draft", "cancel"):
            raise UserError(_("Draft LRs are edited directly."))
        self.env["tms.consignment.amendment"].create({
            "consignment_id": self.consignment_id.id,
            "reason": self.reason,
            "new_consignee_id": self.new_consignee_id.id,
            "new_destination_id": self.new_destination_id.id,
            "is_diversion": self.is_diversion,
            "diversion_km": self.diversion_km,
            "new_actual_weight": self.new_actual_weight,
            "new_packages": self.new_packages,
        })
        return {"type": "ir.actions.act_window_close"}
