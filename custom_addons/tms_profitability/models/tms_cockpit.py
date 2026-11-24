"""Data for the one-page P&L screens (Sales order P&L, Truck P&L) and the dashboard's contribution chart.

The figures come from the P&L views (tms.so.pnl, tms.so.pnl.line, tms.trip.leg, tms.trip.pnl, tms.truck.pnl,
tms.pnl.line), so the pages and the list, pivot and graph views always agree."""

from collections import defaultdict

from odoo import _, api, fields, models

STAGE_BADGE = {"done": "muted", "delivered": "success", "started": "warning", "in_transit": "warning",
               "confirmed": "info", "draft": "muted"}


class TmsCockpit(models.AbstractModel):
    _inherit = "tms.cockpit"

    # ------------------------------------------------------------------ shared
    @api.model
    def _tms_route(self, trip):
        def short(partner, text):
            return (partner.city or partner.name or text or "").strip() if partner else (text or "")
        return "%s → %s" % (short(trip.origin_id, trip.route_origin), short(trip.destination_id, trip.route_destination))

    @api.model
    def _tms_stage(self, trip):
        sel = dict(trip._fields["state_code"]._description_selection(self.env))
        return {"label": sel.get(trip.state_code, ""), "badge": STAGE_BADGE.get(trip.state_code, "muted")}

    # ------------------------------------------------------------------ dashboard contribution
    @api.model
    def _tms_dash_contribution(self, company, start, end):
        lo, hi = self._tms_bounds(start, end)
        trips = self.env["tms.order"].search([
            ("company_id", "=", company.id), ("state_code", "in", ("delivered", "done")),
            ("delivered_on", "<", hi)], order="delivered_on desc", limit=8)
        rows = {r.trip_id.id: r for r in self.env["tms.trip.pnl"].search([("trip_id", "in", trips.ids)])}
        bars = []
        for trip in reversed(trips):
            row = rows.get(trip.id)
            if not row:
                continue
            is_open = trip.state_code != "done"
            cost = row.direct_cost + row.std_charge + (row.open_commitments if is_open else 0.0)
            revenue = row.revenue + (row.unbilled_charges if is_open else 0.0)
            value = revenue + row.recoveries - cost
            bars.append({
                "trip": trip.name, "route": self._tms_route(trip), "open": is_open, "value": value,
                "revenue": revenue, "cost": cost, "recoveries": row.recoveries,
                "trip_open": self._tms_open("tms.order", trip.id),
            })
        closed = [b for b in bars if not b["open"]]
        total = sum(b["value"] for b in closed)
        return {"available": True, "bars": bars, "closed": len(closed), "open": len(bars) - len(closed),
                "total": total, "average": total / len(closed) if closed else 0.0,
                "ledger": {"model": "tms.pnl.line", "name": _("P&L ledger"),
                           "domain": [("trip_id", "in", [b["trip_open"]["id"] for b in bars])]}}

    # ------------------------------------------------------------------ sales order P&L
    @api.model
    def get_so_pnl(self, company_id=False, period=False, order_key=False):
        company, start, end = self._tms_scope(company_id, period)
        lo, hi = self._tms_bounds(start, end)
        links = self.env["tms.trip.consignment"].search([
            ("state", "=", "active"), ("trip_id.company_id", "=", company.id),
            ("trip_id.date_start", ">=", lo), ("trip_id.date_start", "<", hi)])
        orders = links.mapped("consignment_id.sale_line_id.order_id")
        loose = links.filtered(lambda link: not link.consignment_id.sale_line_id)
        tolerance = company.tms_estimate_variance_tolerance or 0.0
        Pnl = self.env["tms.so.pnl"]
        items = Pnl.search([("order_id", "in", orders.ids)])
        rows = []
        for order in orders.sorted(lambda o: o.name, reverse=True):
            mine = items.filtered(lambda r, o=order: r.order_id == o)
            trips = self.env["tms.trip.consignment"].search(
                [("state", "=", "active"), ("consignment_id.sale_line_id.order_id", "=", order.id)]).mapped("trip_id")
            rev_p = sum(mine.mapped("planned_revenue"))
            rev_a = sum(mine.mapped("revenue"))
            cost_p = sum(mine.mapped("estimated_cost"))
            cost_a = sum(mine.mapped("actual")) + sum(mine.mapped("standard_charge"))
            odc = "odc" in mine.mapped("tms_cargo_type")
            rows.append(self._tms_so_row(
                key=str(order.id), so=order.name, customer=order.partner_id.display_name,
                type_label=_("ODC") if odc else _("Regular"), type_badge="warning" if odc else "info",
                trips=len(trips), rev_a=rev_a, rev_p=rev_p, cost_a=cost_a, cost_p=cost_p, planned=True,
                over=bool(cost_p) and (cost_a - cost_p) / cost_p * 100.0 > tolerance))
        if loose:
            legs = self._tms_loose_legs(loose)
            rows.append(self._tms_so_row(
                key="none", so=_("No sales order"), customer=_("Walk-in and To-Pay LRs"),
                type_label=_("No order"), type_badge="muted", trips=len(loose.mapped("trip_id")),
                rev_a=sum(lg["revenue"] for lg in legs), rev_p=0.0,
                cost_a=sum(lg["cost"] for lg in legs), cost_p=0.0, planned=False, over=False))
        real = [r for r in rows if r["planned"]]
        tot = {k: sum(r[k] for r in rows) for k in ("trips", "rev_a", "rev_p", "cost_a", "cost_p")}
        plan_rows = {k: sum(r[k] for r in real) for k in ("rev_p", "cost_p")}
        tot.update(self._tms_so_ratios(tot["rev_a"], tot["cost_a"], plan_rows["rev_p"], plan_rows["cost_p"]))
        tot["var"] = sum(r["cost_a"] for r in real) - plan_rows["cost_p"]
        tot["var_pct"] = tot["var"] / plan_rows["cost_p"] * 100.0 if plan_rows["cost_p"] else 0.0
        selected = next((r for r in rows if r["key"] == str(order_key)), rows[0] if rows else None)
        detail = self._tms_so_detail(selected, links, company) if selected else False
        over = [r["so"] for r in real if r["over"]]
        return {
            "scope": self._tms_scope_payload(company, start),
            "tolerance": tolerance,
            "tiles": {
                "rev_a": tot["rev_a"], "rev_p": plan_rows["rev_p"],
                "cost_a": tot["cost_a"], "cost_p": plan_rows["cost_p"],
                "con_a": tot["con_a"], "con_p": tot["con_p"], "m_a": tot["m_a"], "m_p": tot["m_p"],
                "over": len(over), "orders": len(real), "over_list": ", ".join(over),
            },
            "rows": rows, "totals": tot, "selected": selected and selected["key"], "detail": detail,
        }

    @api.model
    def _tms_so_ratios(self, rev_a, cost_a, rev_p, cost_p):
        return {"con_a": rev_a - cost_a, "con_p": rev_p - cost_p,
                "m_a": (rev_a - cost_a) / rev_a * 100.0 if rev_a else 0.0,
                "m_p": (rev_p - cost_p) / rev_p * 100.0 if rev_p else 0.0}

    @api.model
    def _tms_so_row(self, key, so, customer, type_label, type_badge, trips, rev_a, rev_p, cost_a, cost_p,
                    planned, over):
        row = {"key": key, "so": so, "customer": customer, "type": type_label, "type_badge": type_badge,
               "trips": trips, "rev_a": rev_a, "rev_p": rev_p, "cost_a": cost_a, "cost_p": cost_p,
               "planned": planned, "over": over}
        row.update(self._tms_so_ratios(rev_a, cost_a, rev_p, cost_p))
        row["var"] = cost_a - cost_p if planned else 0.0
        row["var_pct"] = row["var"] / cost_p * 100.0 if planned and cost_p else 0.0
        return row

    @api.model
    def _tms_loose_legs(self, loose):
        """Legs of LRs booked without an order item: revenue and the leg's share of trip cost."""
        legs = self.env["tms.trip.leg"].search([
            ("trip_id", "in", loose.mapped("trip_id").ids), ("consignment_id", "in", loose.mapped("consignment_id").ids)])
        out = defaultdict(lambda: {"revenue": 0.0, "cost": 0.0, "share": 0.0, "weight": 0.0})
        for leg in legs:
            acc = out[(leg.trip_id.id, leg.consignment_id.id)]
            acc["revenue"] += leg.revenue
            acc["cost"] += leg.direct_cost + leg.std_charge
            acc["share"] = max(acc["share"], leg.share)
            acc["weight"] = max(acc["weight"], leg.weight)
            acc["trip"] = leg.trip_id
        return list(out.values())

    @api.model
    def _tms_so_detail(self, row, scope_links, company):
        Leg = self.env["tms.trip.leg"]
        if row["key"] == "none":
            loose = scope_links.filtered(lambda link: not link.consignment_id.sale_line_id)
            trips = loose.mapped("trip_id")
            legs = Leg.search([("trip_id", "in", trips.ids), ("consignment_id", "in", loose.mapped("consignment_id").ids)])
            elements = self._tms_loose_elements(legs)
            basis = _("LRs booked without a sales order item · rate typed per LR")
            plan_by_leg = {}
        else:
            order = self.env["sale.order"].browse(int(row["key"]))
            trips = self.env["tms.trip.consignment"].search(
                [("state", "=", "active"), ("consignment_id.sale_line_id.order_id", "=", order.id)]).mapped("trip_id")
            legs = Leg.search([("order_id", "=", order.id)])
            elements = self._tms_so_elements(order, row["type"] == _("ODC"))
            items = order.order_line.filtered("tms_is_movement")
            locked = order.order_line.mapped("tms_estimate_id").filtered(lambda e: e.state == "locked")
            basis = " · ".join(filter(None, [
                _("%s items", len(items)) if len(items) != 1 else _("1 item"),
                _("estimates locked at confirmation, %s", order.date_order.strftime("%d/%m/%Y"))
                if locked and order.date_order else ""]))
            plan_by_leg = self._tms_plan_by_leg(order, legs)
        trip_rows = []
        notes = []
        for trip in trips.sorted("name"):
            tlegs = legs.filtered(lambda leg, t=trip: leg.trip_id == t)
            revenue = sum(tlegs.mapped("revenue"))
            cost = sum(tlegs.mapped("direct_cost")) + sum(tlegs.mapped("std_charge"))
            share = max(tlegs.mapped("share") or [1.0])
            plan = sum(plan_by_leg.get(leg.id, 0.0) for leg in tlegs) if plan_by_leg else None
            share_note = ""
            if share < 0.999:
                share_note = _("%s%% by charged weight", round(share * 100))
                others = self.env["tms.trip.consignment"].search([("trip_id", "=", trip.id), ("state", "=", "active")])
                mine = sum(link.weight for link in others if (link.consignment_id.sale_line_id.order_id.id or False)
                           == (int(row["key"]) if row["key"] != "none" else False))
                total = sum(others.mapped("weight"))
                other_orders = others.mapped("consignment_id.sale_line_id.order_id").filtered(
                    lambda o: str(o.id) != row["key"]).mapped("name")
                notes.append(_("%(trip)s is shared%(others)s: revenue by LR, cost split by charged weight "
                               "(%(mine)s of %(total)s kg).", trip=trip.name,
                               others=_(" with %s", ", ".join(other_orders)) if other_orders else "",
                               mine="{:,.0f}".format(mine), total="{:,.0f}".format(total)))
            elif trip.state_code in ("started", "in_transit"):
                share_note = _("open")
            if trip.state_code in ("started", "in_transit", "confirmed"):
                notes.append(_("%s is open: its actuals are to date plus approved commitments.", trip.name))
            trip_rows.append({
                "trip": trip.name, "trip_open": self._tms_open("tms.order", trip.id), "route": self._tms_route(trip),
                "stage": self._tms_stage(trip), "share": share_note, "revenue": revenue,
                "cost_plan": plan, "cost_actual": cost, "contribution": revenue - cost,
            })
        drivers = self._tms_so_drivers(elements, trip_rows)
        return {"key": row["key"], "so": row["so"], "customer": row["customer"], "type": row["type"],
                "type_badge": row["type_badge"], "basis": basis, "elements": elements, "trips": trip_rows,
                "drivers": drivers, "trip_note": " ".join(notes),
                "element_note": _("estimate lines, item/line") if row["type"] == _("ODC")
                else (_("ledger lines by account") if row["key"] == "none" else _("estimate lines summed by cost item")),
                "order_open": self._tms_open("sale.order", int(row["key"])) if row["key"] != "none" else False}

    @api.model
    def _tms_plan_by_leg(self, order, legs):
        """Each item's estimated cost spread over its trips by charged weight."""
        est = {r.sale_line_id.id: r.estimated_cost for r in self.env["tms.so.pnl"].search([("order_id", "=", order.id)])}
        by_line = defaultdict(list)
        seen = set()
        for leg in legs:
            key = (leg.trip_id.id, leg.sale_line_id.id)
            if key in seen:
                continue
            seen.add(key)
            by_line[leg.sale_line_id.id].append(leg)
        out = {}
        for line_id, lines in by_line.items():
            weight = sum(leg.weight for leg in lines)
            for leg in lines:
                part = (leg.weight / weight) if weight else 1.0 / len(lines)
                out[leg.id] = est.get(line_id, 0.0) * part
        return out

    @api.model
    def _tms_so_elements(self, order, odc):
        lines = self.env["tms.so.pnl.line"].search([("order_id", "=", order.id)])
        docs = defaultdict(list)
        for aml in self.env["account.move.line"].search([
                ("tms_estimate_line_id", "in", lines.mapped("estimate_line_id").ids), ("parent_state", "=", "posted")]):
            docs[aml.tms_estimate_line_id.id].append(aml.move_id.name)
        out = []
        if odc:
            for line in lines:
                names = sorted(set(docs.get(line.estimate_line_id.id, [])))
                out.append({"name": "%s/%02d %s" % (line.item_no, line.line_no, line.product_id.name or ""),
                            "planned": line.estimated_cost, "actual": line.actual, "committed": line.committed,
                            "src": ", ".join(names[:3]) + (" …" if len(names) > 3 else "")})
        else:
            grouped = defaultdict(lambda: {"planned": 0.0, "actual": 0.0, "committed": 0.0, "docs": set()})
            for line in lines:
                acc = grouped[line.product_id.name or _("Other")]
                acc["planned"] += line.estimated_cost
                acc["actual"] += line.actual
                acc["committed"] += line.committed
                acc["docs"].update(docs.get(line.estimate_line_id.id, []))
            for name, acc in grouped.items():
                if acc["planned"] or acc["actual"]:
                    names = sorted(acc["docs"])
                    out.append({"name": name, "planned": acc["planned"], "actual": acc["actual"],
                                "committed": acc["committed"],
                                "src": ", ".join(names[:3]) + (" …" if len(names) > 3 else "")})
        std = sum(self.env["tms.so.pnl"].search([("order_id", "=", order.id)]).mapped("standard_charge"))
        if std:
            out.append({"name": _("Standard charge, owned trucks"), "planned": 0.0, "actual": std,
                        "src": _("Km run × standard rate of the vehicle class")})
        return out

    @api.model
    def _tms_loose_elements(self, legs):
        """Cost of LRs without an order item, by ledger account, at each leg's weight share."""
        share = defaultdict(float)
        for leg in legs:
            share[leg.trip_id.id] = max(share[leg.trip_id.id], leg.share)
        lines = self.env["tms.pnl.line"].search([("trip_id", "in", list(share)),
                                                 ("kind", "in", ("direct", "accrued_cost", "std_charge"))])
        grouped = defaultdict(float)
        for line in lines:
            label = (line.move_line_id.product_id.name or line.account_id.name
                     or dict(line._fields["kind"].selection).get(line.kind))
            grouped[label] += -line.amount * share[line.trip_id.id]
        return [{"name": k, "planned": 0.0, "actual": v, "src": ""} for k, v in grouped.items() if v]

    @api.model
    def _tms_so_drivers(self, elements, trip_rows):
        moved = sorted([e for e in elements if abs(e["actual"] - e["planned"]) >= 1.0],
                       key=lambda e: -abs(e["actual"] - e["planned"]))[:3]
        parts = []
        for e in moved:
            diff = e["actual"] - e["planned"]
            if not e["planned"]:
                parts.append(_("%(name)s {{amount:%(v)s}} was not in the plan.", name=e["name"], v=diff))
            else:
                if diff > 0:
                    parts.append(_("%(name)s came in {{amount:%(v)s}} over plan.", name=e["name"], v=diff))
                elif e.get("committed", 0.0) >= 1.0:
                    parts.append(_("%(name)s: {{amount:%(v)s}} is committed on an open purchase or hire memo and "
                                   "not yet billed.", name=e["name"], v=e["committed"]))
                else:
                    parts.append(_("%(name)s came in {{amount:%(v)s}} under plan.", name=e["name"], v=-diff))
        open_label = _("open")
        open_trips = [t["trip"] for t in trip_rows if t["share"] == open_label]
        if open_trips:
            parts.append(_("%s is still on the road, so its actuals are not final.", ", ".join(open_trips)))
        return " ".join(parts) or _("Actual cost matches the plan so far.")

    # ------------------------------------------------------------------ truck P&L
    @api.model
    def get_truck_pnl(self, company_id=False, period=False, vehicle_id=False):
        company, start, end = self._tms_scope(company_id, period)
        rows = self.env["tms.truck.pnl"].search([("company_id", "=", company.id), ("period", "=", start)])
        Line = self.env["tms.pnl.line"]
        lines = Line.search([("vehicle_id", "in", rows.mapped("vehicle_id").ids), ("period", "=", start)])
        out = []
        for row in rows.sorted(lambda r: (r.fleet_type != "owned", r.vehicle_id.sudo().license_plate or "")):
            vlines = lines.filtered(lambda line, v=row.vehicle_id: line.vehicle_id == v)
            addon = -sum(vlines.filtered(lambda line: line.kind == "direct" and line.is_replacement).mapped("amount"))
            truck = row.running_actual + row.fixed_actual
            trip_exp = row.direct_cost
            plain = row.revenue + row.recoveries - trip_exp - truck
            veh = row.vehicle_id.sudo()  # display only: fleet officers may open only their own vehicle
            if row.fleet_type == "owned":
                who = veh.tms_driver_id.name or veh.driver_id.name or ""
            else:
                who = self.env["tms.market.vehicle"].sudo().search(
                    [("vehicle_id", "=", veh.id)], limit=1).owner_id.display_name or ""
            out.append({
                "id": veh.id, "reg": veh.license_plate or veh.name, "who": " · ".join(filter(None, [
                    who or "", veh.tms_vehicle_class_id.name or ""])),
                "fleet": _("Owned") if row.fleet_type == "owned" else _("Market"), "owned": row.fleet_type == "owned",
                "trips": row.trip_count, "revenue": row.revenue, "recoveries": row.recoveries,
                "trip_exp": trip_exp, "addon": addon, "truck": truck,
                "std_gap": row.operating_result - plain, "result": row.operating_result,
            })
        tot = {k: sum(r[k] for r in out) for k in ("trips", "revenue", "recoveries", "trip_exp", "addon", "truck", "result")}
        selected = next((r for r in out if r["id"] == int(vehicle_id or 0)), out[0] if out else None)
        detail = self._tms_truck_detail(selected, start, lines) if selected else False
        in_transit = self.env["tms.order"].search([
            ("company_id", "=", company.id), ("state_code", "in", ("started", "in_transit"))], limit=6)
        return {
            "scope": self._tms_scope_payload(company, start),
            "rows": out, "totals": tot, "selected": selected and selected["id"], "detail": detail,
            "below_zero": len([r for r in out if r["result"] < 0]),
            "transit_note": ", ".join("%s (%s)" % (t.name, t.sudo().vehicle_display or "") for t in in_transit),
        }

    @api.model
    def _tms_truck_detail(self, row, start, lines):
        vehicle = self.env["fleet.vehicle"].browse(row["id"])
        vlines = lines.filtered(lambda line: line.vehicle_id == vehicle)
        trip_pnl = self.env["tms.trip.pnl"].search([("vehicle_id", "=", vehicle.id), ("period", "=", start)],
                                                   order="date")
        trips = []
        stmt = [{"kind": "sec", "label": _("Revenue")}]
        for tp in trip_pnl:
            trip = tp.trip_id
            orders = self.env["tms.trip.consignment"].search(
                [("trip_id", "=", trip.id), ("state", "=", "active")]).mapped("consignment_id.sale_line_id.order_id")
            refs = ", ".join(orders.mapped("name")) or _("LR without an order")
            repl_lines = vlines.filtered(lambda line, t=trip: line.trip_id == t and line.kind == "direct"
                                         and line.is_replacement)
            addon = -sum(repl_lines.mapped("amount"))
            repl_veh = ", ".join(sorted(set(filter(None, repl_lines.sudo().mapped(
                "move_line_id.tms_replacement_vehicle_id.license_plate")))))
            exp = tp.direct_cost - addon
            trips.append({
                "trip": trip.name, "trip_open": self._tms_open("tms.order", trip.id), "id": trip.id, "so": refs,
                "route": self._tms_route(trip),
                "delivered": trip.delivered_on and self._tms_dt(trip.delivered_on, "%d/%m/%Y") or "",
                "revenue": tp.revenue, "recoveries": tp.recoveries, "exp": exp, "addon": addon,
                "addon_vehicle": repl_veh, "result": tp.revenue + tp.recoveries - tp.direct_cost,
            })
            stmt.append({"label": "%s %s" % (trip.name, self._tms_route(trip)),
                         "ref": _("%(so)s · delivered %(d)s", so=refs, d=trips[-1]["delivered"]),
                         "value": tp.revenue})
        if not trips:
            stmt.append({"label": _("No trip delivered in the period"), "value": 0.0, "muted": True})
        stmt.append({"kind": "sum", "label": _("Truck revenue"), "value": row["revenue"]})
        if row["recoveries"]:
            stmt.append({"label": _("Recoveries from vendors and drivers"), "value": row["recoveries"]})
        stmt.append({"kind": "sec", "label": _("Trip expenses")})
        for t in trips:
            stmt.append({"label": _("%s trip expenses", t["trip"]), "value": -t["exp"]})
            if t["addon"]:
                stmt.append({"label": _("%s replacement add-on", t["trip"]), "addon": True, "value": -t["addon"],
                             "ref": _("%s · hire and extras of the replacement vehicle", t["addon_vehicle"] or "")})
        if not trips:
            stmt.append({"label": _("None"), "value": 0.0, "muted": True})
        stmt.append({"kind": "sum", "label": _("Trip expenses"), "value": -(row["trip_exp"])})
        stmt.append({"kind": "sec", "label": _("Truck expenses")})
        truck_lines = vlines.filtered(lambda line: line.kind in ("truck_running", "truck_fixed"))
        for line in truck_lines.sorted("date"):
            aml = line.move_line_id
            cat = aml.tms_truck_category_id.name if aml and "tms_truck_category_id" in aml._fields else ""
            stmt.append({"label": aml.name or line.account_id.name or "",
                         "ref": " · ".join(filter(None, [cat or dict(line._fields["kind"].selection).get(line.kind),
                                                         aml.move_id.name if aml else ""])),
                         "value": line.amount})
        if not truck_lines:
            stmt.append({"label": _("None: market truck") if not row["owned"] else _("None booked"), "value": 0.0,
                         "muted": True})
        stmt.append({"kind": "sum", "label": _("Truck expenses"), "value": -row["truck"]})
        if abs(row["std_gap"]) >= 0.5:
            stmt.append({"label": _("Standard charge not matched in the period"),
                         "ref": _("charged to trips less recovered by the truck pool"), "value": row["std_gap"]})
        stmt.append({"kind": "tot", "label": _("Operating result"), "value": row["result"],
                     "margin": row["result"] / row["revenue"] * 100.0 if row["revenue"] else None})
        # bills and expenses
        bills = []
        kinds = dict(self.env["tms.pnl.line"]._fields["kind"].selection)
        for line in vlines.filtered(lambda line: line.move_line_id and line.kind in (
                "direct", "accrued_cost", "truck_running", "truck_fixed")).sorted("date"):
            aml = line.move_line_id
            is_trip = line.kind in ("direct", "accrued_cost")
            cls = _("Trip · add-on") if line.is_replacement else (_("Trip") if is_trip else _("Truck"))
            cat = aml.tms_truck_category_id.name if "tms_truck_category_id" in aml._fields else ""
            bills.append({
                "doc": aml.move_id.name, "doc_open": self._tms_open("account.move", aml.move_id.id),
                "date": aml.date and aml.date.strftime("%d/%m/%Y") or "",
                "label": aml.name or "", "payee": line.vendor_id.display_name or aml.partner_id.display_name or "",
                "cls": cls, "cls_key": "add" if line.is_replacement else ("trip" if is_trip else "truck"),
                "to": line.trip_id.name if is_trip else (cat or kinds.get(line.kind, "")),
                "trip_id": line.trip_id.id if is_trip else False, "amount": -line.amount,
            })
        note = False
        if row["addon"]:
            note = {"head": _("Replacement add-on."),
                    "text": _("A replacement vehicle finished a trip of this truck. Its hire and extras are booked to "
                              "this truck, so the trip result matches the trip ledger.")}
        elif not trips and row["truck"]:
            note = {"head": _("No trip delivered in the period."),
                    "text": _("The truck carries only its own costs this month.")}
        return {"id": row["id"], "reg": row["reg"], "who": row["who"], "fleet": row["fleet"], "owned": row["owned"],
                "statement": stmt, "trips": trips, "bills": bills, "note": note,
                "vehicle_open": self._tms_open("fleet.vehicle", vehicle.id) if vehicle._filtered_access("read") else False,
                "trip_totals": {"revenue": sum(t["revenue"] for t in trips), "exp": sum(t["exp"] for t in trips),
                                "addon": sum(t["addon"] for t in trips), "result": sum(t["result"] for t in trips)}}
