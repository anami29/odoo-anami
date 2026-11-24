"""Exception dashboard (TMS-UIG-001 decision 3): one page read by managers first.

``tms.cockpit`` is a service model. ``get_exception_dashboard`` returns everything the page draws: six tiles,
the exceptions that need someone to act, the trip contribution chart (filled by tms_profitability), unbilled
charges and pending recoveries, and the month's breakdowns. Every figure is read under the user's own access
rights, so the page shows only what the user may open."""

from datetime import datetime, time, timedelta

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models

DELAYED = ("delayed_traffic", "delayed_other")
ON_ROAD = ("started", "in_transit")


class TmsCockpit(models.AbstractModel):
    _name = "tms.cockpit"
    _description = "TMS cockpit data service"

    # ------------------------------------------------------------------ helpers
    @api.model
    def _tms_scope(self, company_id=False, period=False):
        """Branch and month the page is filtered by; defaults are the current branch and this month."""
        allowed = self.env.user.company_ids
        company = self.env["res.company"].browse(int(company_id)) if company_id else self.env.company
        if company not in allowed:
            company = self.env.company
        if period:
            start = fields.Date.to_date(period).replace(day=1)
        else:
            start = fields.Date.context_today(self).replace(day=1)
        end = start + relativedelta(months=1)
        return company, start, end

    @api.model
    def _tms_dt(self, value, fmt="%d/%m %H:%M"):
        if not value:
            return ""
        if isinstance(value, datetime):
            return fields.Datetime.context_timestamp(self, value).strftime(fmt)
        return value.strftime(fmt if "%H" not in fmt else "%d/%m")

    @api.model
    def _tms_bounds(self, start, end):
        """Datetime bounds of a month, for datetime fields."""
        return datetime.combine(start, time.min), datetime.combine(end, time.min)

    @api.model
    def _tms_open(self, model, res_id, name=None):
        """A small descriptor the page turns into a doAction on a record form."""
        return {"model": model, "id": res_id, "name": name or ""}

    @api.model
    def _tms_scope_payload(self, company, start):
        branches = [{"id": c.id, "name": c.name} for c in self.env.user.company_ids.sorted("name")]
        return {
            "company_id": company.id,
            "company_name": company.name,
            "branches": branches,
            "period": fields.Date.to_string(start),
            "period_label": start.strftime("%B %Y"),
            "as_of": fields.Datetime.context_timestamp(self, fields.Datetime.now()).strftime("%d/%m/%Y %H:%M"),
            "currency_symbol": company.currency_id.symbol or "₹",
        }

    # ------------------------------------------------------------------ page
    @api.model
    def get_exception_dashboard(self, company_id=False, period=False):
        company, start, end = self._tms_scope(company_id, period)
        data = {"scope": self._tms_scope_payload(company, start)}
        data["tiles"] = self._tms_dash_tiles(company, start, end)
        data["exceptions"] = self._tms_dash_exceptions(company, start, end)
        data["contribution"] = self._tms_dash_contribution(company, start, end)
        data["recoveries"] = self._tms_dash_recoveries(company)
        data["breakdowns"] = self._tms_dash_breakdowns(company, start, end)
        return data

    # ------------------------------------------------------------------ tiles
    @api.model
    def _tms_dash_open_breakdowns(self, company):
        return self.env["tms.trip.breakdown"].search(
            [("company_id", "=", company.id), ("state", "=", "open")], order="reported_at desc")

    @api.model
    def _tms_dash_replacements(self, company, start, end):
        lo, hi = self._tms_bounds(start, end)
        return self.env["tms.trip.assignment"].search([
            ("company_id", "=", company.id), ("reason", "in", ("breakdown", "accident")),
            ("date_from", ">=", lo), ("date_from", "<", hi)], order="date_from desc")

    @api.model
    def _tms_dash_open_claims(self, company):
        return self.env["tms.damage.claim"].search([
            ("company_id", "=", company.id), ("state", "in", ("draft", "under_review", "approved"))],
            order="create_date desc")

    @api.model
    def _tms_dash_unbilled(self, company):
        return self.env["tms.trip.charge"].search([
            ("company_id", "=", company.id), ("state", "=", "approved"), ("billing_status", "=", "unbilled")],
            order="approved_on desc, id desc")

    @api.model
    def _tms_dash_pod_links(self, company):
        return self.env["tms.trip.consignment"].search([
            ("company_id", "=", company.id), ("state", "=", "active"), ("pod_status", "=", "uploaded")],
            order="uploaded_on")

    @api.model
    def _tms_dash_delayed_trips(self, company):
        return self.env["tms.order"].search([
            ("company_id", "=", company.id), ("state_code", "in", ON_ROAD),
            "|", ("last_dsr_status", "in", DELAYED + ("delayed_breakdown",)), ("delay_hours", ">", 0)],
            order="next_dsr_due")

    @api.model
    def _tms_dash_blocked_matches(self, company):
        return self.env["tms.vendor.bill.match"].search(
            [("company_id", "=", company.id), ("state", "=", "blocked"), ("move_id.state", "!=", "cancel")],
            order="create_date")

    @api.model
    def _tms_dash_tiles(self, company, start, end):
        tiles = []
        bds = self._tms_dash_open_breakdowns(company)
        first = bds[:1]
        tiles.append({
            "key": "breakdowns", "label": _("Active breakdowns"), "icon": "wrench", "value": len(bds),
            "sub": (_("%(trip)s · %(loc)s, since %(at)s", trip=first.trip_id.name, loc=first.location,
                      at=self._tms_dt(first.reported_at))
                    + (_(" · +%s more", len(bds) - 1) if len(bds) > 1 else "")) if first else _("None open"),
            "action": {"model": "tms.trip.breakdown", "name": _("Active breakdowns"), "domain": [("id", "in", bds.ids)]},
        })
        reps = self._tms_dash_replacements(company, start, end)
        rep = reps[:1]
        bd_of_rep = self.env["tms.trip.breakdown"].search([("replacement_assignment_id", "in", rep.ids)], limit=1)
        tiles.append({
            "key": "replacements", "label": _("Vehicle replacements"), "icon": "swap", "value": len(reps),
            "sub": (_("This month · %(trip)s at %(loc)s", trip=rep.trip_id.name, loc=bd_of_rep.location or "")
                    if rep else _("None this month")),
            "action": {"model": "tms.trip.assignment", "name": _("Vehicle replacements"),
                       "domain": [("id", "in", reps.ids)]},
        })
        claims = self._tms_dash_open_claims(company)
        exposure = sum(c.approved_amount or c.estimated_amount for c in claims)
        review = len(claims.filtered(lambda c: c.state == "under_review"))
        tiles.append({
            "key": "claims", "label": _("Open damage claims"), "icon": "clipboard", "value": len(claims),
            "amount_sub": exposure,
            "sub": _("%(n)s under review", n=review) if claims else _("None open"),
            "sub_prefix_amount": bool(claims),
            "action": {"model": "tms.damage.claim", "name": _("Open damage claims"),
                       "domain": [("id", "in", claims.ids)]},
        })
        charges = self._tms_dash_unbilled(company)
        cust = sum(charges.filtered(lambda c: c.recover_from == "customer").mapped("amount"))
        other = sum(charges.filtered(lambda c: c.recover_from != "customer").mapped("amount"))
        tiles.append({
            "key": "unbilled", "label": _("Unbilled charges & recoveries"), "icon": "undo",
            "value": cust + other, "is_amount": True, "customer": cust, "vendor": other,
            "action": {"model": "tms.trip.charge", "name": _("Unbilled charges and recoveries"),
                       "domain": [("id", "in", charges.ids)]},
        })
        links = self._tms_dash_pod_links(company)
        trips = links.mapped("trip_id")
        held = self._tms_dash_blocked_matches(company).filtered(lambda m: m.trip_id in trips)
        sub = trips[:1].name or _("None waiting")
        if held:
            sub = _("%(trip)s · holds vendor bill", trip=held[0].trip_id.name)
        tiles.append({
            "key": "pod", "label": _("Awaiting POD verification"), "icon": "file-check", "value": len(trips),
            "sub": sub, "held_amount": held[0].move_id.amount_untaxed if held else 0.0,
            "action": {"model": "tms.order", "name": _("Awaiting POD verification"),
                       "domain": [("id", "in", trips.ids)]},
        })
        delayed = self._tms_dash_delayed_trips(company)
        tiles.append({
            "key": "delayed", "label": _("Delayed in transit"), "icon": "clock", "value": len(delayed),
            "sub": (", ".join(delayed[:2].mapped("name")) + (_(" +%s", len(delayed) - 2) if len(delayed) > 2 else "")
                    + _(" · latest DSR")) if delayed else _("All on schedule"),
            "action": {"model": "tms.order", "name": _("Delayed in transit"), "domain": [("id", "in", delayed.ids)]},
        })
        return tiles

    # ------------------------------------------------------------------ exceptions
    @api.model
    def _tms_dash_exceptions(self, company, start, end):
        rows = []
        tz_now = fields.Datetime.now()
        # breakdowns on the road
        for bd in self._tms_dash_open_breakdowns(company):
            veh = bd.vehicle_id.sudo().license_plate or bd.assignment_id.vehicle_number or ""
            res = dict(bd._fields["resolution"].selection).get(bd.resolution) if bd.resolution else _("resolution pending")
            cause = dict(bd._fields["cause"].selection).get(bd.cause, "")
            rows.append({
                "rank": 10, "kind": _("Breakdown"), "badge": "danger",
                "ref": bd.trip_id.name, "open": self._tms_open("tms.order", bd.trip_id.id),
                "detail": " · ".join(filter(None, [veh, "%s, %s" % (cause.lower(), bd.location), res.lower()])),
                "since": self._tms_dt(bd.reported_at), "sort": bd.reported_at or tz_now,
                "next": _("Update ETA"), "next_open": self._tms_open("tms.order", bd.trip_id.id),
            })
        # PODs uploaded and waiting for the verifier
        links = self._tms_dash_pod_links(company)
        for trip in links.mapped("trip_id"):
            tlinks = links.filtered(lambda link, t=trip: link.trip_id == t)
            delivered = max(tlinks.mapped("delivered_at") or [False]) or trip.delivered_on
            uploaded = min(tlinks.mapped("uploaded_on") or [False])
            rows.append({
                "rank": 20, "kind": _("POD unverified"), "badge": "warning",
                "ref": trip.name, "open": self._tms_open("tms.order", trip.id),
                "detail": _("Delivered %(d)s · %(n)s POD uploaded %(u)s, trip cannot be closed",
                            d=self._tms_dt(delivered), n=len(tlinks), u=self._tms_dt(uploaded, "%H:%M")),
                "since": self._tms_dt(uploaded), "sort": uploaded or tz_now,
                "next": _("Verify POD"), "next_open": self._tms_open("tms.order", trip.id),
            })
        # vendor bills held by the three-way match
        for match in self._tms_dash_blocked_matches(company):
            move = match.move_id
            reasons = []
            if not match.pod_ok:
                reasons.append(_("POD unverified"))
            if not match.tolerance_ok:
                reasons.append(_("outside tolerance"))
            rows.append({
                "rank": 30, "kind": _("Bill on hold"), "badge": "warning",
                "ref": move.name if move.name and move.name != "/" else _("Draft bill"),
                "open": self._tms_open("account.move", move.id),
                "detail": " · ".join(filter(None, [
                    move.partner_id.display_name,
                    _("%(bill)s vs commitment %(net)s", bill="{{amount:%s}}" % move.amount_untaxed,
                      net="{{amount:%s}}" % move.tms_match_net_payable),
                    ", ".join(reasons)])),
                "since": self._tms_dt(move.create_date), "sort": move.create_date or tz_now,
                "next": _("Review match"), "next_open": self._tms_open("account.move", move.id),
            })
        # claims
        types = dict(self.env["tms.damage.claim"]._fields["damage_type"].selection)
        for claim in self._tms_dash_open_claims(company).filtered(lambda c: c.state in ("draft", "under_review")):
            to_approve = claim.state == "under_review"
            party = claim.responsible_partner_id.display_name or claim.responsible_driver_id.name or ""
            rows.append({
                "rank": 40 if to_approve else 70,
                "kind": _("Claim to approve") if to_approve else _("Claim review"),
                "badge": "info" if to_approve else "muted",
                "ref": claim.name, "open": self._tms_open("tms.damage.claim", claim.id),
                "detail": " · ".join(filter(None, [
                    claim.trip_id.name,
                    "%s%s" % (types.get(claim.damage_type, ""),
                              _(", %s packages", claim.affected_qty) if claim.affected_qty else ""),
                    _("estimate %s", "{{amount:%s}}" % claim.estimated_amount),
                    _("from %s", party) if party else ""])),
                "since": self._tms_dt(claim.create_date), "sort": claim.create_date or tz_now,
                "next": _("Review claim") if to_approve else _("Complete claim"),
                "next_open": self._tms_open("tms.damage.claim", claim.id),
            })
        rows += self._tms_dash_compliance_rows(company)
        # delays
        status = dict(self.env["tms.order"]._fields["last_dsr_status"].selection)
        for trip in self._tms_dash_delayed_trips(company).filtered(lambda t: t.last_dsr_status != "delayed_breakdown"):
            rows.append({
                "rank": 80, "kind": _("Delay"), "badge": "muted",
                "ref": trip.name, "open": self._tms_open("tms.order", trip.id),
                "detail": " · ".join(filter(None, [
                    trip.sudo().vehicle_display or "", status.get(trip.last_dsr_status, ""),
                    _("ETA %s", self._tms_dt(trip.current_eta)) if trip.current_eta else "",
                    _("%s h late", round(trip.delay_hours, 1)) if trip.delay_hours else ""])),
                "since": self._tms_dt(trip.last_dsr_at), "sort": trip.last_dsr_at or tz_now,
                "next": _("Log status"), "next_open": self._tms_open("tms.order", trip.id),
            })
        rows.sort(key=lambda r: (r["rank"], r["sort"]))
        for row in rows:
            row.pop("sort")
        return rows

    @api.model
    def _tms_dash_latest_docs(self, company):
        """The latest document of each holder and type in the branch: a renewed document supersedes the old one.
        Read as the system, because a manager may not open every truck (fleet officers see their own vehicle);
        each row opens the document, which the user's own rights then govern."""
        Doc = self.env["tms.compliance.document"].sudo()
        docs = Doc.search([
            ("type_id.mandatory_for_start", "=", True),
            "|", ("company_id", "=", company.id), ("company_id", "=", False),
            "|", ("vehicle_id", "=", False), "&", ("vehicle_id.active", "=", True),
            ("vehicle_id.company_id", "in", (company.id, False)),
            "|", ("driver_id", "=", False), ("driver_id.active", "=", True)], order="valid_until desc")
        latest = {}
        for doc in docs:
            key = (doc.type_id.id, doc.vehicle_id.id, doc.driver_id.id, doc.market_vehicle_id.id)
            latest.setdefault(key, doc)
        return Doc.concat(*latest.values()) if latest else Doc.browse()

    @api.model
    def _tms_holder_label(self, doc):
        """A truck by its number plate, a driver by name, as people say them."""
        if doc.vehicle_id:
            return doc.vehicle_id.sudo().license_plate or doc.holder_name or ""
        return doc.driver_id.sudo().name or doc.holder_name or ""

    @api.model
    def _tms_dash_compliance_rows(self, company):
        rows = []
        today = fields.Date.context_today(self)
        latest = self._tms_dash_latest_docs(company).filtered(lambda d: not d.market_vehicle_id)
        Trip = self.env["tms.order"]
        for doc in latest.filtered(lambda d: d.valid_until < today).sorted("valid_until"):
            blocked = Trip.search([("state_code", "=", "confirmed"), "|", ("planned_vehicle_id", "=", doc.vehicle_id.id or -1),
                                   ("planned_driver_id", "=", doc.driver_id.id or -1)], limit=1)
            rows.append({
                "rank": 50, "kind": _("Compliance"), "badge": "danger",
                "ref": self._tms_holder_label(doc),
                "open": self._tms_open("tms.compliance.document", doc.id),
                "detail": " · ".join(filter(None, [
                    _("%(type)s expired %(d)s", type=doc.type_id.name, d=doc.valid_until.strftime("%d/%m/%Y")),
                    _("%s blocked at start", blocked.name) if blocked else ""])),
                "since": doc.valid_until.strftime("%d/%m"), "sort": datetime.combine(doc.valid_until, time.min),
                "next": _("Renew %s", doc.type_id.code or doc.type_id.name),
                "next_open": self._tms_open("tms.compliance.document", doc.id),
            })
        soon = latest.filtered(lambda d: today <= d.valid_until <= today + timedelta(days=30)).sorted("valid_until")
        if soon:
            parts = ["%s %s %s" % (self._tms_holder_label(d), (d.type_id.name or "").lower(),
                                   d.valid_until.strftime("%d/%m")) for d in soon[:3]]
            rows.append({
                "rank": 60, "kind": _("Expiring"), "badge": "warning",
                "ref": _("%s documents", len(soon)) if len(soon) > 1 else self._tms_holder_label(soon),
                "open": {"model": "tms.compliance.document", "name": _("Expiring within 30 days"),
                         "domain": [("id", "in", soon.ids)]},
                "detail": _("Within 30 days: %s", " · ".join(parts)) + (_(" · +%s more", len(soon) - 3) if len(soon) > 3 else ""),
                "since": (soon[0].valid_until - timedelta(days=30)).strftime("%d/%m"),
                "sort": datetime.combine(soon[0].valid_until, time.min),
                "next": _("Review expiring"),
                "next_open": {"model": "tms.compliance.document", "name": _("Expiring within 30 days"),
                              "domain": [("id", "in", soon.ids)]},
            })
        return rows

    # ------------------------------------------------------------------ contribution (tms_profitability)
    @api.model
    def _tms_dash_contribution(self, company, start, end):
        """Hook: tms_profitability returns the last delivered trips with their contribution."""
        return {"available": False, "bars": [], "closed": 0, "open": 0, "total": 0.0, "average": 0.0}

    # ------------------------------------------------------------------ recoveries
    @api.model
    def _tms_dash_recoveries(self, company):
        charges = self._tms_dash_unbilled(company)
        sel = dict(self.env["tms.trip.charge"]._fields["recover_from"].selection)
        status = {"customer": _("Approved · unbilled"), "vendor": _("Approved · to deduct")}
        rows = [{
            "trip": c.trip_id.name or c.consignment_id.name or "",
            "trip_open": self._tms_open("tms.order", c.trip_id.id) if c.trip_id else False,
            "source": c.category_id.name or "", "doc": c.name,
            "doc_open": self._tms_open("tms.trip.charge", c.id),
            "party": c.partner_id.display_name or c.driver_id.name or "",
            "from": sel.get(c.recover_from, ""), "from_key": c.recover_from,
            "amount": c.amount, "status": status.get(c.recover_from, _("Approved · to recover")),
        } for c in charges]
        cust = sum(r["amount"] for r in rows if r["from_key"] == "customer")
        return {"rows": rows, "count": len(rows), "customer": cust,
                "other": sum(r["amount"] for r in rows) - cust, "total": sum(r["amount"] for r in rows)}

    # ------------------------------------------------------------------ breakdowns
    @api.model
    def _tms_dash_breakdowns(self, company, start, end):
        lo, hi = self._tms_bounds(start, end)
        bds = self.env["tms.trip.breakdown"].search([
            ("company_id", "=", company.id), ("state", "!=", "cancel"),
            "|", ("state", "=", "open"), "&", ("reported_at", ">=", lo), ("reported_at", "<", hi)],
            order="reported_at desc", limit=6)
        causes = dict(self.env["tms.trip.breakdown"]._fields["cause"].selection)
        fleets = {"owned": _("owned"), "market": _("market")}
        cards = []
        for bd in bds:
            rep = bd.replacement_assignment_id
            veh = bd.vehicle_id.sudo().license_plate or bd.assignment_id.vehicle_number or ""
            if bd.state == "open":
                badge, label = "danger", _("Open")
            elif rep:
                badge, label = "success", _("Replaced")
            else:
                badge, label = "info", _("Repaired")
            line1 = "%s · %s · %s" % (self._tms_dt(bd.reported_at), bd.location or "",
                                      _("%(cause)s on %(veh)s (%(fleet)s)", cause=causes.get(bd.cause, "").lower(),
                                        veh=veh, fleet=fleets.get(bd.fleet_type, "")))
            if rep:
                who = rep.broker_id.display_name or rep.driver_id.name or ""
                line2 = _("→ %(veh)s (%(fleet)s%(who)s) loaded %(at)s", veh=rep.vehicle_number or rep.vehicle_id.sudo().license_plate or "",
                          fleet=fleets.get(rep.fleet_type, ""), who=", %s" % who if who else "",
                          at=self._tms_dt(rep.date_from, "%H:%M"))
            elif bd.resolution == "repair":
                eta = bd.trip_id.current_eta
                line2 = _("Repair and continue · no replacement") + (
                    _(" · ETA %s", self._tms_dt(eta)) if eta else "")
            else:
                line2 = _("Awaiting a resolution")
            if bd.state == "open":
                hours = (fields.Datetime.now() - bd.reported_at).total_seconds() / 3600.0 if bd.reported_at else 0
                head = _("Open %s", self._tms_hm(hours))
            else:
                head = _("Downtime %s", self._tms_hm(bd.downtime_hours))
            moved = len(rep.trip_id.consignment_link_ids.filtered(lambda link, r=rep: link.assignment_id == r)) if rep else 0
            cards.append({
                "id": bd.id, "name": bd.name, "trip": bd.trip_id.name, "badge": badge, "label": label,
                "open": self._tms_open("tms.trip.breakdown", bd.id), "linkable": True,
                "line1": line1, "line2": line2, "foot_head": head,
                "repair": bd.repair_cost, "hire": rep.hire_amount if rep else 0.0,
                "moved": _("%s LRs re-assigned", moved) if moved else "",
            })
        return cards

    @api.model
    def _tms_hm(self, hours):
        minutes = int(round((hours or 0.0) * 60))
        return _("%(h)s h %(m)s m", h=minutes // 60, m=minutes % 60)

    # ------------------------------------------------------------------ shared
    @api.model
    def tms_open_action(self, model, name, domain):
        """A list action for the page's links; access rules still apply when it opens."""
        return {"type": "ir.actions.act_window", "name": name, "res_model": model,
                "views": [(False, "list"), (False, "form")], "domain": domain, "target": "current"}
