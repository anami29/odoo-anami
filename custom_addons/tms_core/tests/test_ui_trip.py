from ast import literal_eval
from datetime import timedelta

from lxml import etree

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import Form, tagged

from .common import TmsCommon


@tagged("post_install", "-at_install")
class TestUiTrip(TmsCommon):
    """Trip screens: POD from the trip, market documents after a replacement, pre-start compliance,
    the daily status report and the trip's buttons."""

    # ------------------------------------------------------------------ POD
    def _deliver_upload(self, trip, uploader):
        for link in trip.consignment_link_ids:
            link.action_mark_delivered()
            link.write({"pod_file": b"UE9E", "pod_file_name": "pod.pdf"})
            link.with_user(uploader).action_upload_pod()

    def test_verify_pod_from_trip(self):
        trip = self._trip(self._lr() | self._lr())
        self._deliver_upload(trip, self.u_manager)
        self.assertEqual(trip.state_code, "delivered")
        self.assertIn("Manager", trip.pod_banner)
        self.assertIn("cannot be marked done", trip.pod_banner)
        self.assertEqual(len(trip.pod_link_ids), 2)
        # the uploader cannot verify, even being a POD Verifier; a non-verifier cannot either
        with self.assertRaises(UserError):
            trip.with_user(self.u_manager).action_verify_pods()
        with self.assertRaises(UserError):
            trip.with_user(self.u_dispatcher).action_verify_pods()
        self.assertTrue(all(link.pod_status == "uploaded" for link in trip.consignment_link_ids))
        trip.with_user(self.u_verifier).action_verify_pods()
        self.assertEqual(trip.pod_status, "verified")
        self.assertEqual(trip.consignment_link_ids.verified_by_id, self.u_verifier)
        self.assertFalse(trip.pod_banner)
        # nothing left to verify
        with self.assertRaises(UserError):
            trip.with_user(self.u_verifier).action_verify_pods()

    def test_uploader_verifies_when_admin_allows(self):
        """The TMS Administrator may allow the uploader to verify their own POD; each such verification is
        logged."""
        trip = self._trip(self._lr())
        self._deliver_upload(trip, self.u_manager)
        with self.assertRaises(UserError):
            trip.with_user(self.u_manager).action_verify_pods()
        # only the TMS Administrator switches it, and only with a reason
        with self.assertRaises(UserError):
            self.company.with_user(self.u_manager).write({"tms_pod_self_verify": True})
        self.u_admin.groups_id |= self.env.ref("base.group_system")  # saving settings needs it (README #4)
        with self.assertRaises(UserError):
            self.company.with_user(self.u_admin).write({"tms_pod_self_verify": True})
        self.company.with_user(self.u_admin).with_context(tms_reason="Single-verifier branch").write(
            {"tms_pod_self_verify": True})
        Log = self.env["tms.override.log"]
        self.assertTrue(Log.search([("kind", "=", "pod_self_verify_switch")]))
        self.assertIn("the uploader included", trip.pod_banner)
        trip.with_user(self.u_manager).action_verify_pods()
        self.assertEqual(trip.pod_status, "verified")
        self.assertEqual(trip.consignment_link_ids.verified_by_id, self.u_manager)
        self.assertTrue(Log.search([("kind", "=", "pod_self_verify")]))
        # a user who is not a POD Verifier still cannot verify
        trip2 = self._trip(self._lr(), planned_vehicle_id=self.vehicle2.id, planned_driver_id=self.driver2.id)
        self._deliver_upload(trip2, self.u_manager)
        with self.assertRaises(UserError):
            trip2.with_user(self.u_dispatcher).action_verify_pods()

    def test_pod_banner_before_upload(self):
        trip = self._trip(self._lr())
        self.assertFalse(trip.pod_banner)
        trip.consignment_link_ids.action_mark_delivered()
        self.assertIn("not yet uploaded", trip.pod_banner)
        action = trip.consignment_link_ids.action_open_link()
        self.assertEqual(action["res_model"], "tms.trip.consignment")
        self.assertEqual(action["res_id"], trip.consignment_link_ids.id)

    # ------------------------------------------------------------------ market documents
    def test_market_documents_after_replacement(self):
        market = self._trip(self._lr(), fleet="market", start=False)
        self.assertTrue(market.show_market_documents)
        trip = self._trip(self._lr(), start=False)
        trip.action_confirm()
        self.assertFalse(trip.show_market_documents)  # owned, not yet on the road
        trip.button_start_order()
        # on the road a broken-down vehicle may be replaced by a market vehicle at any time: the page shows
        self.assertTrue(trip.show_market_documents)
        bd = self.env["tms.trip.breakdown"].create({"trip_id": trip.id, "location": "Igatpuri"})
        self.assertTrue(trip.show_market_documents)
        # a market vehicle with nothing in the register passes only on the documents captured on the trip
        replacement = self.env["tms.market.vehicle"].create({"name": "MH15ZZ0001", "owner_id": self.vendor.id})
        until = fields.Date.today() + timedelta(days=300)
        for dtype in self._doc_types().filtered(lambda t: t.applies_to in ("vehicle", "both")):
            trip.write({"market_document_ids": [(0, 0, {"type_id": dtype.id, "number": "T-%s" % dtype.code,
                                                         "valid_until": until})]})
        wiz = self.env["tms.breakdown.resolve.wizard"].create({
            "breakdown_id": bd.id, "resolution": "replace_market", "market_vehicle_id": replacement.id,
            "broker_id": self.vendor.id, "hire_amount": 38500.0, "market_driver_name": "Ramesh Yadav",
            "market_driver_licence": "MH15-2012-1", "market_driver_licence_expiry": until,
            "odometer_at_breakdown": 10300.0})
        wiz.action_resolve()
        self.assertEqual(trip.open_assignment_id.fleet_type, "market")
        self.assertEqual(trip.open_assignment_id.gate_result, "pass")
        self.assertTrue(trip.show_market_documents)
        # the current assignment shows the market vehicle, broker and hire on the owned trip
        self.assertEqual(trip.fleet_type, "owned")
        self.assertEqual(trip.current_fleet_type, "market")
        self.assertEqual(trip.current_vehicle_number, "MH15ZZ0001")
        self.assertEqual(trip.current_broker_id, self.vendor)
        self.assertEqual(trip.current_hire_amount, 38500.0)
        self.assertIn("#2 of 2", trip.current_position)
        self.assertIn("Ramesh Yadav", trip.current_driver_label)
        self.assertEqual(trip.vehicle_display, "MH15ZZ0001")
        self.assertEqual(trip.driver_display, self.vendor.name)
        hires = trip.assignment_ids.sorted("sequence").mapped("hire_amount")
        self.assertEqual(hires, [0.0, 38500.0])
        # after delivery the page stays: the market leg's documents are part of the trip's record
        trip.consignment_link_ids.action_mark_delivered()
        self.assertEqual(trip.state_code, "delivered")
        self.assertTrue(trip.show_market_documents)
        # an owned trip never on a market vehicle drops the page once delivered
        owned = self._trip(self._lr(), planned_vehicle_id=self.vehicle2.id, planned_driver_id=self.driver2.id)
        owned.consignment_link_ids.action_mark_delivered()
        self.assertFalse(owned.show_market_documents)

    def test_market_documents_needed_by_gate(self):
        """Without the captured documents the same replacement fails the gate."""
        trip = self._trip(self._lr())
        bd = self.env["tms.trip.breakdown"].create({"trip_id": trip.id, "location": "Igatpuri"})
        replacement = self.env["tms.market.vehicle"].create({"name": "MH15ZZ0002", "owner_id": self.vendor.id})
        wiz = self.env["tms.breakdown.resolve.wizard"].create({
            "breakdown_id": bd.id, "resolution": "replace_market", "market_vehicle_id": replacement.id,
            "broker_id": self.vendor.id, "hire_amount": 1000.0, "market_driver_licence": "X",
            "market_driver_licence_expiry": fields.Date.today() + timedelta(days=300)})
        with self.assertRaises(UserError):
            wiz.action_resolve()

    # ------------------------------------------------------------------ pre-start compliance
    def test_gate_preview(self):
        lr = self._lr()
        trip = self._trip(lr, start=False)
        trip.action_confirm()
        total = len(self.env["tms.compliance.gate"].evaluate(
            trip.scheduled_date_end, vehicle=self.vehicle, driver=self.driver)[3])
        self.assertEqual(trip.gate_preview, "valid")
        self.assertEqual(trip.gate_preview_text, "Valid %s/%s" % (total, total))
        puc = self.vehicle.tms_compliance_document_ids.filtered(lambda d: d.type_id.code == "PUC")
        puc.valid_until = fields.Date.today() - timedelta(days=8)
        trip.invalidate_recordset(["gate_preview", "gate_preview_text"])
        self.assertEqual(trip.gate_preview, "failed")
        self.assertEqual(trip.gate_preview_text, "PUC expired")
        self.assertEqual(trip.kanban_flag, "PUC expired")
        self.assertEqual(trip.kanban_flag_level, "danger")
        # read only: no assignment, no stage move
        self.assertFalse(trip.assignment_ids)
        self.assertEqual(trip.state_code, "confirmed")
        pending = self.env["tms.order"].create({"origin_id": self.loc_bhiwandi.id,
                                                "destination_id": self.loc_nagpur.id})
        self.assertEqual(pending.gate_preview, "pending")
        self.assertEqual(pending.vehicle_display, "Not assigned")
        market = self._trip(self._lr(), fleet="market", start=False)
        self.assertEqual(market.gate_preview, "valid")
        # after START the recorded gate result shows
        puc.valid_until = fields.Date.today() + timedelta(days=300)
        trip.button_start_order()
        self.assertEqual(trip.gate_preview, "valid")
        self.assertEqual(trip.vehicle_display, "MH04LE7713")
        self.assertEqual(trip.driver_display, self.driver.name)
        self.assertEqual(trip.lr_count, 1)

    # ------------------------------------------------------------------ daily status report
    def test_last_dsr_status_eta_delay(self):
        trip = self._trip(self._lr())
        Dsr = self.env["tms.trip.dsr"]
        cadence = trip.company_id.tms_dsr_cadence_hours or 6.0
        self.assertFalse(trip.last_dsr_status)
        self.assertEqual(trip.next_dsr_due, trip.date_start + timedelta(hours=cadence))
        eta = trip.scheduled_date_end + timedelta(hours=5)
        line = Dsr.create({"trip_id": trip.id, "location": "Dhule bypass", "status": "delayed_traffic",
                           "remark": "Ghat closure", "eta": eta})
        self.assertEqual(trip.last_dsr_status, "delayed_traffic")
        self.assertEqual(trip.last_dsr_location, "Dhule bypass")
        self.assertEqual(trip.last_dsr_remark, "Ghat closure")
        self.assertEqual(trip.current_eta, eta)
        self.assertAlmostEqual(trip.delay_hours, 5.0, places=2)
        self.assertEqual(trip.next_dsr_due, line.logged_at + timedelta(hours=cadence))
        self.assertEqual(trip.dsr_customer_id, self.customer)
        self.assertIn("Delay", trip.kanban_flag)
        # an older line logged later does not replace the latest one
        Dsr.create({"trip_id": trip.id, "location": "Bhiwandi", "status": "on_schedule",
                    "logged_at": line.logged_at - timedelta(hours=3)})
        self.assertEqual(trip.last_dsr_status, "delayed_traffic")
        # a newer line without ETA keeps the last reported ETA
        Dsr.create({"trip_id": trip.id, "location": "Malegaon", "status": "night_halt",
                    "logged_at": line.logged_at + timedelta(minutes=30)})
        self.assertEqual(trip.last_dsr_status, "night_halt")
        self.assertEqual(trip.last_dsr_location, "Malegaon")
        self.assertEqual(trip.current_eta, eta)
        report = self.env.ref("tms_core.action_tms_daily_status_report")
        found = self.env["tms.order"].search(literal_eval(report.domain))
        self.assertIn(trip, found)
        groups = self.env["tms.order"].read_group(literal_eval(report.domain) + [("id", "=", trip.id)],
                                                  ["delay_hours:max"], ["last_dsr_status"])
        self.assertEqual(groups[0]["last_dsr_status"], "night_halt")

    # ------------------------------------------------------------------ buttons and screens
    def test_button_actions(self):
        trip = self._trip(self._lr())
        log = trip.action_log_status()
        self.assertEqual((log["type"], log["res_model"], log["target"]),
                         ("ir.actions.act_window", "tms.trip.dsr", "new"))
        self.assertEqual(log["context"]["default_trip_id"], trip.id)
        self.assertEqual(log["views"][0][0], self.env.ref("tms_core.tms_trip_dsr_form_log").id)
        incident = trip.action_report_incident()
        self.assertEqual((incident["res_model"], incident["target"]), ("tms.trip.incident", "new"))
        self.assertEqual(incident["context"]["default_trip_id"], trip.id)
        for method, model in (("action_view_lrs", "tms.consignment"), ("action_view_incidents", "tms.trip.incident"),
                              ("action_view_claims", "tms.damage.claim"), ("action_view_expenses", "tms.trip.expense"),
                              ("action_view_vendor_bill", "tms.vendor.bill.match"),
                              ("action_view_lrs_to_invoice", "tms.consignment")):
            action = getattr(trip, method)()
            self.assertEqual((action["type"], action["res_model"]), ("ir.actions.act_window", model), method)
            self.assertTrue(action["domain"], method)
        self.assertEqual(self.env["tms.consignment"].search(trip.action_view_lrs()["domain"]), trip.consignment_ids)
        self.assertEqual(trip.action_open_trip()["res_id"], trip.id)
        self.assertEqual(trip.vendor_bill_state, "none")
        self.assertEqual(trip.incident_count, 0)
        self.env["tms.trip.incident"].create({"trip_id": trip.id,
                                              "category_id": self.env.ref("tms_core.tms_category_shortage").id})
        trip.invalidate_recordset(["incident_count"])
        self.assertEqual(trip.incident_count, 1)
        fuel = self.env.ref("tms_core.tms_category_fuel")
        self.env["tms.trip.expense"].create({"trip_id": trip.id, "category_id": fuel.id, "amount": 1500.0,
                                             "payment_mode": "driver_cash", "receipt": b"UkNQ", "fuel_litres": 15})
        trip.invalidate_recordset(["expense_total"])
        self.assertEqual(trip.expense_total, 1500.0)
        self.assertIn("1 LR", trip.trip_subtitle)
        self.assertIn("→", trip.trip_subtitle)
        draft = self._trip(self._lr(), start=False)
        with self.assertRaises(UserError):
            draft.action_log_status()

    def test_screens(self):
        # menus: Operations › Trips shows the report, Reporting keeps the raw log
        menu = self.env.ref("tms_core.menu_tms_dsr")
        report = self.env.ref("tms_core.action_tms_daily_status_report")
        self.assertEqual(menu.action, report)
        self.assertEqual(menu.name, "Daily status report")
        self.assertEqual(menu.parent_id, self.env.ref("tms_core.menu_tms_ops_trips"))
        self.assertEqual(self.env.ref("tms_core.menu_tms_report_dsr").action,
                         self.env.ref("tms_core.action_tms_dsr_log"))
        self.assertEqual(self.env.ref("tms_core.action_report_dsr").binding_model_id.model, "tms.order")
        trips = self.env.ref("tms.action_tms_operation_trip")
        self.assertEqual(trips.view_mode, "list,kanban,form")
        self.assertIn("search_default_not_cancelled", trips.context)
        self.assertEqual(trips.view_ids.filtered(lambda v: v.view_mode == "kanban").view_id,
                         self.env.ref("tms_core.tms_order_kanban_trips"))
        # every screen renders
        Order = self.env["tms.order"]
        views = Order.get_views([(False, "list"), (False, "form"), (False, "search"),
                                 (self.env.ref("tms_core.tms_order_kanban_trips").id, "kanban"),
                                 (self.env.ref("tms_core.tms_order_list_dsr_report").id, "list")])
        self.assertIn("kanban", views["views"])
        self.env["tms.trip.dsr"].get_views([(self.env.ref("tms_core.tms_trip_dsr_form_log").id, "form")])
        # list row buttons follow the stage
        lst = etree.fromstring(Order.get_views([(False, "list")])["views"]["list"]["arch"])
        start = lst.xpath("//button[@name='button_start_order']")[0]
        self.assertEqual(start.get("invisible"), "state_code != 'confirmed'")
        self.assertTrue(lst.xpath("//button[@name='action_depart']"))
        self.assertTrue(lst.xpath("//button[@name='action_open_trip'][@string='POD pending']"))
        self.assertIn(lst.xpath("//field[@name='stage_id']")[0].get("column_invisible"), ("1", "True"))
        form = etree.fromstring(views["views"]["form"]["arch"])
        self.assertEqual(form.xpath("//page[@name='dsr']")[0].get("string"), "Status log")
        self.assertTrue(form.xpath("//page[@name='pod']//field[@name='pod_link_ids']"))
        self.assertTrue(form.xpath("//button[@name='action_done'][@string='Mark done']"))
        self.assertTrue(form.xpath("//button[@name='action_print_trip_sheet'][@string='Print trip sheet']"))
        verifier_form = etree.fromstring(Order.with_user(self.u_verifier).get_views([(False, "form")])["views"]["form"]["arch"])
        self.assertTrue(verifier_form.xpath("//button[@name='action_verify_pods']"))
        self.assertFalse(form.xpath("//button[@name='action_verify_pods']"))  # POD verifiers only
        self.assertIn("show_market_documents", form.xpath("//page[@name='market_documents']")[0].get("invisible"))
        # the owned-trip rule stays: no opening odometer, no START
        trip = self._trip(self._lr(), start=False, start_odometer=0.0)
        trip.action_confirm()
        with self.assertRaises(UserError):
            trip.button_start_order()


@tagged("post_install", "-at_install")
class TestUiTripAccess(TmsCommon):
    """Every TMS role opens the trip form and the documents around it (Odoo limits a Fleet Officer to the
    vehicles they drive; every TMS role is one)."""

    def test_roles_open_trip_forms(self):
        lr = self._lr()
        trip = self._trip(lr)
        self.env["tms.trip.breakdown"].create({"trip_id": trip.id, "location": "Igatpuri", "cause": "tyre"})
        expense = self.env["tms.trip.expense"].create({
            "trip_id": trip.id, "category_id": self.env.ref("tms_core.tms_category_roadside_repair").id,
            "amount": 1500.0})
        market = self._trip(self._lr(), fleet="market", start=False)
        records = [trip, market, lr, trip.breakdown_ids, expense, self.vehicle]
        Access = self.env["ir.model.access"]
        roles = (self.u_clerk, self.u_dispatcher, self.u_verifier, self.u_accountant, self.u_manager, self.u_ops,
                 self.u_finance, self.u_admin, self.u_fleet)
        for user in roles:
            for rec in records:
                if not Access.with_user(user).check(rec._name, "read", raise_exception=False):
                    continue
                with self.subTest(user=user.login, model=rec._name):
                    form = Form(rec.with_user(user))
                    self.assertTrue(form.id)
                    # the vehicle shows by name, not as an access error
                    if rec._name == "tms.order":
                        self.assertTrue(rec.with_user(user).primary_vehicle_id.display_name
                                        or rec.with_user(user).planned_market_vehicle_id.display_name)
