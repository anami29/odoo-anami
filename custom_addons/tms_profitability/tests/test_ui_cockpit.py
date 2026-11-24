"""The one-page Exception dashboard, Sales order P&L and Truck P&L (TMS-UIG-001 decision 3, 18.0.1.3.0).

One branch month is seeded the way the mockup tells it: delivered and closed trips with their bills, a trip whose
POD waits for the verifier, a hire bill held by the three-way match, a breakdown on the road, a breakdown solved
with a market replacement, a delayed trip, a damage claim, unbilled charges, an expired PUC and an expiring licence.
The data services must report each of them, tie to the P&L views, and the pages must render without errors."""

import os
import resource
import subprocess
import sys
from datetime import timedelta
from unittest.mock import patch

from odoo import Command, fields
from odoo.tests import HttpCase, new_test_user, tagged

from .test_tms_profitability import PnlCommon


class CockpitScenario(PnlCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.loc_surat = cls.env["res.partner"].create({"name": "Sachin GIDC", "tms_location": True, "city": "Surat",
                                                     "state_id": cls.env.ref("base.state_in_gj").id})
        cls.customer2 = cls.env["res.partner"].create({
            "name": "Deccan Steel Tubes", "vat": "27AAACD1234B1Z1", "l10n_in_pan": "AAACD1234B",
            "state_id": cls.env.ref("base.state_in_mh").id, "country_id": cls.env.ref("base.in").id,
            "l10n_in_gst_treatment": "regular", "tms_freight_basis": "tbb", "tms_billing_cycle": "monthly",
            "tms_credit_limit": 1000000.0})

        def item(name, cost):
            return cls.env["product.product"].create({
                "name": name, "type": "service", "tms_cost_item": True, "tms_cost_basis": "unit",
                "standard_price": cost, "tms_default_markup": 20, "purchase_ok": True})
        cls.p_fuel = item("Fuel", 14800)
        cls.p_tolls = item("Toll", 2600)
        cls.p_driver = item("Driver allowance", 1600)
        cls.p_loading = item("Loading and unloading", 2800)
        cls.p_hire = item("Hire", 30000)
        cls.vehicle3 = cls._make_vehicle("MH04JK3308")
        cls.driver3 = cls._make_driver("Gurpreet Singh", "MH0420110012347")

    # ------------------------------------------------------------------ helpers
    def _manager(self):
        """A branch manager who also heads operations and finance: the dashboard's first reader."""
        groups = ",".join([
            "base.group_user", "tms_core.group_tms_admin", "tms_core.group_branch_manager",
            "tms_core.group_operations_head", "tms_core.group_finance_head", "tms_core.group_branch_accountant",
            "account.group_account_manager", "sales_team.group_sale_manager"])
        user = new_test_user(self.env, login="boss", password="boss-pass-2026", name="Anita Kulkarni",
                             groups=groups, company_id=self.company.id, company_ids=[Command.set(self.company.ids)])
        self.env.flush_all()
        return user

    def _so(self, partner, products, km=590.0, units=None, movements=3):
        order = self._order(km=km, partner_id=partner.id)
        est = order.order_line.tms_estimate_id
        units = units or {}
        lines = {p: self.env["tms.estimate.line"].create({"estimate_id": est.id, "product_id": p.id,
                                                          "units": units.get(p, 3)}) for p in products}
        order.order_line.product_uom_qty = movements  # one LR per movement
        order.action_confirm()
        return order, lines

    def _order_lr(self, order, weight=9000.0, partner=None):
        movement = self.env["tms.consignment"].search_count([("sale_line_id", "=", order.order_line.id)]) + 1
        return self._lr(confirm=True, rate_type=False, typed_rate=0.0, typed_rate_reason=False, weight=weight,
                        sale_line_id=order.order_line.id, consignor_id=(partner or order.partner_id).id,
                        movement_no=movement)

    def _book(self, trip, est_lines, amounts, partner=None, replacement=None):
        lines = []
        for product, amount in amounts.items():
            vals = {"name": product.name, "price_unit": amount, "tms_trip_id": trip.id,
                    "tms_cost_class": "trip_direct", "product_id": product.id}
            if est_lines and product in est_lines:
                vals["tms_estimate_line_id"] = est_lines[product].id
            if replacement:
                vals["tms_replacement_vehicle_id"] = replacement.id
            lines.append(vals)
        bill = self._bill(lines, partner=partner)
        bill.action_post()
        return bill

    def _scenario(self):
        today = fields.Date.today()
        self.vehicle2.tms_driver_id = self.driver2
        self.vehicle.tms_driver_id = self.driver
        # ---- order 1: two trips; the first closed, the second waits for its POD verification
        # three movements planned; one trip's driver allowance and two loadings
        order1, el1 = self._so(self.customer, [self.p_fuel, self.p_tolls, self.p_driver, self.p_loading, self.p_hire],
                               units={self.p_driver: 1, self.p_loading: 2, self.p_hire: 0})
        lr_a = self._order_lr(order1)
        trip_a = self._trip(lr_a)
        self._deliver_and_verify(trip_a)
        trip_a.end_odometer = 10590.0
        self._invoice(lr_a)
        self._book(trip_a, el1, {self.p_fuel: 15300.0, self.p_tolls: 2640.0, self.p_driver: 1600.0,
                                 self.p_loading: 2820.0})
        trip_a.action_done()
        lr_b = self._order_lr(order1, weight=6000.0)
        trip_b = self._trip(lr_b, planned_vehicle_id=self.vehicle2.id, planned_driver_id=self.driver2.id)
        link = trip_b.consignment_link_ids
        link.action_mark_delivered()
        link.write({"pod_file": b"UE9E", "pod_file_name": "pod.pdf"})
        link.with_user(self.u_dispatcher).action_upload_pod()  # not invoiced: the POD is not verified yet
        self._book(trip_b, el1, {self.p_fuel: 9800.0, self.p_tolls: 2100.0})
        # ---- order 2: a market trip whose hire bill is held by the three-way match
        order2, el2 = self._so(self.customer2, [self.p_hire, self.p_tolls], units={self.p_hire: 1, self.p_tolls: 1},
                               movements=1)
        lr_c = self._order_lr(order2, weight=12000.0)
        trip_c = self._trip(lr_c, fleet="market")
        self._deliver_and_verify(trip_c)
        self._invoice(lr_c)
        memo = trip_c.hire_memo_ids
        memo.action_create_invoice()
        held = memo.invoice_ids
        held.invoice_date = today
        held.invoice_line_ids.price_unit = 30500.0
        # ---- a walk-in LR on the road with a breakdown (repair and continue)
        lr_d = self._lr(weight=5000.0)
        self.vehicle3.tms_driver_id = self.driver3
        trip_d = self._trip(lr_d, planned_vehicle_id=self.vehicle3.id, planned_driver_id=self.driver3.id)
        trip_d.action_depart()
        self.env["tms.trip.breakdown"].create({"trip_id": trip_d.id, "location": "Zaheerabad (NH-65)",
                                               "cause": "tyre", "repair_cost": 9400.0})
        # ---- a breakdown solved by a market replacement, the trip delivered after it
        lr_f = self._order_lr(order1, weight=7000.0)
        trip_f = self._trip(lr_f, start_odometer=10600.0)
        wiz = self.env["tms.breakdown.report.wizard"].with_context(
            trip_f.action_report_breakdown_wizard()["context"]).create({
                "location": "Igatpuri", "cause": "mechanical", "description": "Clutch failure",
                "resolution": "replace_market", "market_vehicle_id": self.market_vehicle.id,
                "broker_id": self.vendor.id, "hire_amount": 38500.0, "market_driver_name": "Ramesh Yadav",
                "market_driver_phone": "9800000000", "market_driver_licence": "MH15 2011 0045",
                "market_driver_licence_expiry": today + timedelta(days=700), "repair_cost": 6800.0})
        wiz.action_confirm()
        self._deliver_and_verify(trip_f)
        self._invoice(lr_f)
        self._book(trip_f, el1, {self.p_fuel: 8200.0})
        self._book(trip_f, el1, {self.p_hire: 38500.0}, replacement=self.vehicle)  # not in the plan
        # ---- truck expense: a repair booked to the truck, not to a trip
        self._bill([{"price_unit": 18500.0, "tms_cost_class": "truck_running", "vehicle_id": self.vehicle.id,
                     "tms_truck_category_id": self.cat_repair.id, "name": "Clutch overhaul"}]).action_post()
        # ---- the trip on the road reports its delay
        self.env["tms.trip.dsr"].create({"trip_id": trip_d.id, "location": "Zaheerabad", "status": "delayed_breakdown",
                                         "eta": fields.Datetime.now() + timedelta(hours=8),
                                         "remark": "Two tyres being replaced at the roadside"})
        # ---- charges: one customer charge, one vendor recovery, both approved and unbilled
        detention = self.env.ref("tms_core.tms_category_detention")
        self.env["tms.trip.charge"].create({"trip_id": trip_b.id, "consignment_id": lr_b.id,
                                            "category_id": detention.id, "amount": 2500.0,
                                            "recover_from": "customer"}).action_confirm()
        fine = self.env.ref("tms_core.tms_category_rto_fine", raise_if_not_found=False) or detention
        self.env["tms.trip.charge"].create({"trip_id": trip_c.id, "category_id": fine.id, "amount": 2000.0,
                                            "recover_from": "vendor", "partner_id": self.vendor.id}).action_confirm()
        # ---- a damage claim under review
        claim = self.env["tms.damage.claim"].create({
            "consignment_id": lr_a.id, "trip_id": trip_a.id, "damage_type": "shortage", "affected_qty": 3,
            "estimated_amount": 8100.0, "notice_date": today})
        claim.action_submit()
        # ---- compliance: an expired PUC on a truck nobody has renewed, a licence expiring soon
        puc = self.env["tms.compliance.document"].search([("vehicle_id", "=", self.vehicle2.id)], limit=1)
        puc.valid_until = today - timedelta(days=3)
        dl = self.env["tms.compliance.document"].search([("driver_id", "=", self.driver.id)], limit=1)
        dl.valid_until = today + timedelta(days=20)
        self.env.flush_all()
        return {"order1": order1, "order2": order2, "trip_a": trip_a, "trip_b": trip_b, "trip_c": trip_c,
                "trip_d": trip_d, "trip_f": trip_f, "held": held, "claim": claim}


@tagged("post_install", "-at_install")
class TestUiCockpit(CockpitScenario):

    def test_exception_dashboard(self):
        s = self._scenario()
        cockpit = self.env["tms.cockpit"].with_user(self._manager())
        data = cockpit.get_exception_dashboard(company_id=self.company.id)
        tiles = {t["key"]: t for t in data["tiles"]}
        self.assertEqual(tiles["breakdowns"]["value"], 1)
        self.assertIn(s["trip_d"].name, tiles["breakdowns"]["sub"])
        self.assertEqual(tiles["replacements"]["value"], 1)
        self.assertIn("Igatpuri", tiles["replacements"]["sub"])
        self.assertEqual(tiles["claims"]["value"], 1)
        self.assertAlmostEqual(tiles["claims"]["amount_sub"], 8100.0)
        self.assertAlmostEqual(tiles["unbilled"]["customer"], 2500.0)
        self.assertAlmostEqual(tiles["unbilled"]["vendor"], 2000.0)
        self.assertEqual(tiles["pod"]["value"], 1)
        self.assertEqual(tiles["delayed"]["value"], 1)
        kinds = [x["kind"] for x in data["exceptions"]]
        for kind in ("Breakdown", "POD unverified", "Bill on hold", "Claim to approve", "Compliance", "Expiring"):
            self.assertIn(kind, kinds)
        # most urgent first
        self.assertEqual(kinds[0], "Breakdown")
        held = next(x for x in data["exceptions"] if x["kind"] == "Bill on hold")
        self.assertEqual(held["open"]["model"], "account.move")
        self.assertIn("{{amount:30500.0}}", held["detail"])
        # contribution: closed trips count in totals, open ones do not
        contrib = data["contribution"]
        self.assertTrue(contrib["available"])
        names = [b["trip"] for b in contrib["bars"]]
        self.assertIn(s["trip_a"].name, names)
        bar_a = next(b for b in contrib["bars"] if b["trip"] == s["trip_a"].name)
        self.assertFalse(bar_a["open"])
        row_a = self.env["tms.trip.pnl"].search([("trip_id", "=", s["trip_a"].id)])
        self.assertAlmostEqual(bar_a["value"], row_a.revenue + row_a.recoveries - row_a.direct_cost - row_a.std_charge)
        self.assertAlmostEqual(contrib["total"], sum(b["value"] for b in contrib["bars"] if not b["open"]))
        self.assertTrue(any(b["open"] for b in contrib["bars"]))
        # recoveries table and breakdown cards
        self.assertEqual(data["recoveries"]["count"], 2)
        self.assertAlmostEqual(data["recoveries"]["total"], 4500.0)
        labels = {c["trip"]: c["label"] for c in data["breakdowns"]}
        self.assertEqual(labels[s["trip_d"].name], "Open")
        self.assertEqual(labels[s["trip_f"].name], "Replaced")

    def test_bill_service_log_does_not_block_truck(self):
        """A posted bill line naming a truck creates a fleet service log; it is a cost record, not a workshop job."""
        trip, _lr = self._delivered_trip()
        bill = self._bill([{"price_unit": 500.0, "tms_trip_id": trip.id}])
        bill.action_post()
        logs = self.env["fleet.vehicle.log.services"].search([("vehicle_id", "=", self.vehicle.id),
                                                             ("account_move_line_id", "!=", False)])
        self.assertTrue(logs)
        self.assertEqual(set(logs.mapped("state")), {"new"})
        self.assertFalse(self.vehicle._tms_has_open_service())
        self.env["fleet.vehicle.log.services"].create({"vehicle_id": self.vehicle.id, "description": "Clutch job"})
        self.assertTrue(self.vehicle._tms_has_open_service())

    def test_dashboard_is_the_first_screen(self):
        menu = self.env.ref("tms_core.menu_tms_exception_dashboard")
        self.assertEqual(menu.action, self.env.ref("tms_core.action_tms_exception_dashboard"))
        self.assertEqual(menu.action.tag, "tms_exception_dashboard")
        self.assertEqual(self.env.ref("tms_core.menu_tms_kpi_board").action,
                         self.env.ref("tms_core.action_tms_kpi_dashboard"))

    def test_so_pnl_ties_to_views(self):
        s = self._scenario()
        cockpit = self.env["tms.cockpit"].with_user(self._manager())
        data = cockpit.get_so_pnl(company_id=self.company.id)
        rows = {r["so"]: r for r in data["rows"]}
        self.assertIn(s["order1"].name, rows)
        self.assertIn(s["order2"].name, rows)
        self.assertIn("No sales order", rows)
        items = self.env["tms.so.pnl"].search([("order_id", "=", s["order1"].id)])
        r1 = rows[s["order1"].name]
        self.assertAlmostEqual(r1["rev_a"], sum(items.mapped("revenue")))
        self.assertAlmostEqual(r1["cost_p"], sum(items.mapped("estimated_cost")))
        self.assertAlmostEqual(r1["cost_a"], sum(items.mapped("actual")) + sum(items.mapped("standard_charge")))
        self.assertAlmostEqual(r1["con_a"], r1["rev_a"] - r1["cost_a"])
        self.assertEqual(r1["trips"], 3)
        self.assertEqual(r1["type"], "Regular")
        # totals and tiles are the sum of the rows; planned figures only from orders
        self.assertAlmostEqual(data["tiles"]["rev_a"], sum(r["rev_a"] for r in data["rows"]))
        self.assertEqual(data["tiles"]["orders"], 2)
        # the first order is selected: elements by cost item, its trips with the plan spread by weight
        det = cockpit.get_so_pnl(company_id=self.company.id, order_key=str(s["order1"].id))["detail"]
        names = [e["name"] for e in det["elements"]]
        self.assertIn("Fuel", names)
        fuel = next(e for e in det["elements"] if e["name"] == "Fuel")
        self.assertAlmostEqual(fuel["actual"], 15300.0 + 9800.0 + 8200.0)
        self.assertEqual(len(det["trips"]), 3)
        plan = sum(t["cost_plan"] or 0.0 for t in det["trips"])
        legs_on_lines = self.env["tms.trip.leg"].search([("order_id", "=", s["order1"].id)])
        if legs_on_lines:
            self.assertLessEqual(plan, r1["cost_p"] + 0.01)
        self.assertIn("plan", det["drivers"])
        loose = cockpit.get_so_pnl(company_id=self.company.id, order_key="none")["detail"]
        self.assertEqual(loose["key"], "none")
        self.assertEqual(loose["trips"][0]["trip"], s["trip_d"].name)

    def test_truck_pnl_ties_to_views(self):
        s = self._scenario()
        cockpit = self.env["tms.cockpit"].with_user(self._manager())
        data = cockpit.get_truck_pnl(company_id=self.company.id, vehicle_id=self.vehicle.id)
        row = next(r for r in data["rows"] if r["id"] == self.vehicle.id)
        view = self.env["tms.truck.pnl"].search([("vehicle_id", "=", self.vehicle.id),
                                                 ("period", "=", fields.Date.today().replace(day=1))])
        self.assertAlmostEqual(row["revenue"], view.revenue)
        self.assertAlmostEqual(row["truck"], view.running_actual + view.fixed_actual)
        self.assertAlmostEqual(row["truck"], 18500.0)
        self.assertAlmostEqual(row["result"], view.operating_result)
        self.assertAlmostEqual(row["addon"], 38500.0)
        det = data["detail"]
        self.assertEqual(det["id"], self.vehicle.id)
        trips = {t["trip"]: t for t in det["trips"]}
        self.assertIn(s["trip_f"].name, trips)
        self.assertAlmostEqual(trips[s["trip_f"].name]["addon"], 38500.0)
        # the statement ends in the operating result and its parts add up to it
        tot = det["statement"][-1]
        self.assertEqual(tot["kind"], "tot")
        self.assertAlmostEqual(tot["value"], row["result"])
        parts = sum(x["value"] for x in det["statement"] if x.get("kind") not in ("sec", "sum", "tot"))
        self.assertAlmostEqual(parts, row["result"], places=2)
        classes = {b["cls_key"] for b in det["bills"]}
        self.assertEqual(classes, {"trip", "add", "truck"})
        self.assertTrue(det["note"])


@tagged("post_install", "-at_install")
class TestUiCockpitPages(CockpitScenario, HttpCase):
    """The three pages open in a browser without a client error. With TMS_SHOT_DIR set, screenshots are saved."""

    def test_pages_render(self):
        if not (os.environ.get("TMS_SHOT_DIR") and os.environ.get("TMS_SHOT_SCRIPT")):
            self.skipTest("Set TMS_SHOT_DIR and TMS_SHOT_SCRIPT to render the pages")
        self._scenario()
        self._manager()
        # the second factor by email guards interactive logins; the test session signs in directly
        with patch.object(type(self.env["res.users"]), "_rpc_api_keys_only", lambda self: False):
            self.authenticate("boss", "boss-pass-2026")
        script = os.environ["TMS_SHOT_SCRIPT"]
        pages = [
            ("dashboard", "/odoo/action-tms_core.action_tms_exception_dashboard"),
            ("so_pnl", "/odoo/action-tms_profitability.action_tms_so_pnl_page"),
            ("truck_pnl", "/odoo/action-tms_profitability.action_tms_truck_pnl_page"),
        ]
        args = [os.environ.get("TMS_SHOT_PYTHON", "python3"), script, self.base_url(), self.session.sid,
                os.environ["TMS_SHOT_DIR"]] + ["%s=%s" % p for p in pages]
        def lift_memory_limit():
            # the server caps its address space; a browser needs more than that cap
            soft, hard = resource.getrlimit(resource.RLIMIT_AS)
            resource.setrlimit(resource.RLIMIT_AS, (hard, hard))
        proc = subprocess.run(args, capture_output=True, text=True, timeout=600, preexec_fn=lift_memory_limit)
        sys.stdout.write(proc.stdout)
        sys.stdout.write(proc.stderr)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
