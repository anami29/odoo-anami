from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import Form, tagged

from .common import TmsCommon


@tagged("post_install", "-at_install")
class TestUiBreakdown(TmsCommon):
    """The one-step Report breakdown dialog on the trip."""

    def _started_trip_with_bill(self, **kw):
        lr = self._lr()
        bill = self.env["tms.ewaybill"].create({"name": "341022579981", "consignment_id": lr.id,
                                                "distance_km": 800})
        lr.line_ids.ewaybill_id = bill
        trip = self._trip(lr, start=False, **kw)
        trip.action_confirm()
        trip.ewaybill_update_ids.write({"evidence_reference": "PORTAL-1"})
        trip.ewaybill_update_ids.action_done()
        trip.button_start_order()
        return trip, lr, bill

    def _wizard(self, trip, **vals):
        action = trip.action_report_breakdown_wizard()
        self.assertEqual(action["res_model"], "tms.breakdown.report.wizard")
        base = {"location": "Igatpuri, Mumbai–Nashik Hwy", "cause": "mechanical",
                "description": "Clutch plate failure"}
        base.update(vals)
        return self.env["tms.breakdown.report.wizard"].with_context(action["context"]).create(base)

    def test_market_replacement_end_to_end(self):
        trip, lr, bill = self._started_trip_with_bill()
        wiz = self._wizard(trip, resolution="replace_market", market_vehicle_id=self.market_vehicle.id,
                           broker_id=self.vendor.id, hire_amount=38500.0, market_driver_name="Ramesh Yadav",
                           market_driver_phone="9800000000", market_driver_licence="MH15 2011 0045",
                           market_driver_licence_expiry=fields.Date.today() + timedelta(days=700),
                           repair_cost=6800.0)
        self.assertEqual(wiz.current_vehicle_driver, "MH04LE7713 (owned) · Ramesh Patil")
        self.assertFalse(wiz.gate_fail)
        self.assertIn("Valid", wiz.gate_preview)
        self.assertIn(bill.name, wiz.ewaybill_preview)
        self.assertIn(lr.name, wiz.ewaybill_preview)
        self.assertIn("MH15HH4410", wiz.partb_vehicle)
        self.assertIn("hire memo ₹38,500.00", wiz.creates_summary)
        self.assertIn("assignment #2", wiz.creates_summary)
        res = wiz.action_confirm()
        self.assertEqual(res["tag"], "display_notification")
        self.assertEqual(res["params"]["title"], "Vehicle replaced · assignment #2 opened")
        self.assertEqual(res["params"]["next"]["type"], "ir.actions.act_window_close")
        bd = trip.breakdown_ids
        self.assertEqual(len(bd), 1)
        self.assertTrue(bd.name.startswith("BD/%s/" % fields.Date.today().year), bd.name)
        self.assertEqual(bd.state, "resolved")
        self.assertEqual(bd.resolution, "replace_market")
        self.assertEqual(bd.repair_cost, 6800.0)
        self.assertEqual(len(trip.assignment_ids), 2)
        asg = trip.open_assignment_id
        self.assertEqual(asg.sequence, 2)
        self.assertEqual(asg.fleet_type, "market")
        self.assertEqual(asg.reason, "breakdown")
        self.assertEqual(bd.replacement_assignment_id, asg)
        self.assertTrue(asg.hire_memo_id)
        self.assertEqual(asg.hire_memo_id.amount_untaxed, 38500.0)
        tasks = trip.ewaybill_update_ids.filtered(lambda t: t.assignment_id == asg)
        self.assertEqual(tasks.mapped("vehicle_number"), ["MH15HH4410"])
        self.assertEqual(tasks.state, "pending")
        with self.assertRaises(UserError):
            tasks.action_done()  # the evidence rule stays

    def test_own_replacement_form_and_reposition_expense(self):
        self.vehicle2.tms_driver_id = self.driver2
        trip, _lr, _bill = self._started_trip_with_bill()
        action = trip.action_report_breakdown_wizard()
        form = Form(self.env["tms.breakdown.report.wizard"].with_context(action["context"]),
                    view="tms_core.tms_breakdown_report_wizard_form")
        form.location = "Igatpuri"
        form.cause = "tyre"
        form.resolution = "replace_own"
        form.vehicle_id = self.vehicle2
        self.assertEqual(form.driver_id, self.driver2)  # defaults from the vehicle
        form.reposition_cost = 4200.0
        form.odometer_at_breakdown = 10300.0
        form.replacement_odometer = 500.0
        self.assertIn("reposition expense ₹4,200.00", form.creates_summary)
        wiz = form.save()
        res = wiz.action_confirm()
        self.assertEqual(res["params"]["title"], "Vehicle replaced · assignment #2 opened")
        self.assertEqual(trip.vehicle_id, self.vehicle2)
        expense = trip.expense_ids.filtered(lambda e: e.category_id.code == "REPOSITION")
        self.assertEqual(len(expense), 1)
        self.assertEqual(expense.amount, 4200.0)
        self.assertEqual(expense.vehicle_id, self.vehicle2)
        self.assertEqual(expense.state, "draft")

    def test_own_replacement_refused_in_broker_mode(self):
        self.company.tms_operation_mode = "broker"
        keys = [k for k, _label in
                self.env["tms.breakdown.report.wizard"].fields_get(["resolution"])["resolution"]["selection"]]
        self.assertEqual(keys, ["replace_market", "repair"])
        trip, _lr, _bill = self._started_trip_with_bill(fleet="market")
        wiz = self._wizard(trip, resolution="replace_own", vehicle_id=self.vehicle2.id,
                           driver_id=self.driver2.id)
        self.assertTrue(wiz.is_broker_mode)
        with self.assertRaises(UserError):
            wiz.action_confirm()
        self.assertFalse(trip.breakdown_ids)

    def test_repair_logs_revised_eta(self):
        trip = self._trip(self._lr())
        eta = fields.Datetime.now().replace(microsecond=0) + timedelta(hours=30)
        wiz = self._wizard(trip, resolution="repair", revised_eta=eta, repair_cost=1500.0)
        self.assertFalse(wiz.gate_preview)
        self.assertIn("DSR line with revised ETA", wiz.creates_summary)
        res = wiz.action_confirm()
        self.assertEqual(res["params"]["title"], "Repair recorded")
        bd = trip.breakdown_ids
        self.assertEqual(bd.state, "resolved")
        self.assertEqual(bd.resolution, "repair")
        self.assertEqual(len(trip.assignment_ids), 1)
        line = trip.dsr_ids.filtered(lambda d: d.eta == eta)
        self.assertEqual(len(line), 1)
        self.assertEqual(line.status, "delayed_breakdown")
        self.assertEqual(bd.service_log_id.amount, 1500.0)

    def test_gate_preview_fails_on_expired_document(self):
        trip = self._trip(self._lr())
        puc = self.vehicle2.tms_compliance_document_ids[:1]
        puc.valid_until = fields.Date.today() - timedelta(days=3)
        wiz = self._wizard(trip, resolution="replace_own", vehicle_id=self.vehicle2.id,
                           driver_id=self.driver2.id)
        self.assertTrue(wiz.gate_fail)
        self.assertIn("Expired", wiz.gate_preview)
        self.assertIn("MH04LE7714 cannot be assigned", wiz.gate_message)
        with self.assertRaises(UserError):
            wiz.action_confirm()
        self.assertFalse(trip.breakdown_ids)
        self.assertEqual(len(trip.assignment_ids), 1)

    def test_old_two_step_path_still_numbers_and_resolves(self):
        arch = self.env["tms.order"].get_view(view_type="form")["arch"]
        self.assertIn('name="action_report_breakdown_wizard"', arch)
        trip = self._trip(self._lr())
        bd = self.env["tms.trip.breakdown"].create({"trip_id": trip.id, "location": "Malegaon"})
        self.assertTrue(bd.name.startswith("BD/"))
        name = bd.name
        bd.reported_at = fields.Datetime.now() - timedelta(hours=1)
        self.assertEqual(bd.name, name)
        action = bd.action_resolve()
        wiz = self.env[action["res_model"]].with_context(action["context"]).create({"resolution": "repair"})
        wiz.action_resolve()
        self.assertEqual(bd.state, "resolved")
        self.assertEqual(dict(bd._fields["cause"].selection)["tyre"], "Tyre burst")

    def test_repair_cost_books_trip_expense(self):
        """TMS-UIG-001 #7 item 4: the repair cost of record is a draft trip expense linked to the service log."""
        trip, lr, bill = self._started_trip_with_bill()
        wiz = self._wizard(trip, resolution="repair", repair_cost=4500.0,
                           revised_eta=fields.Datetime.now() + timedelta(hours=6))
        wiz.action_confirm()
        bd = trip.breakdown_ids
        exp = bd.expense_id
        self.assertTrue(exp)
        self.assertEqual(exp.state, "draft")
        self.assertEqual(exp.trip_id, trip)
        self.assertEqual(exp.amount, 4500.0)
        self.assertEqual(exp.category_id, self.env.ref("tms_core.tms_category_roadside_repair"))
        self.assertEqual(exp.breakdown_id, bd)
        self.assertEqual(exp.incident_id, bd.incident_id)
        self.assertEqual(exp.vehicle_id, self.vehicle)
        self.assertEqual(bd.service_log_id.tms_expense_id, exp)
        self.assertEqual(bd.service_log_id.amount, 4500.0)
        # a corrected cost follows the draft expense; no second expense is created
        bd.repair_cost = 5200.0
        self.assertEqual(exp.amount, 5200.0)
        self.assertEqual(len(trip.expense_ids.filtered("breakdown_id")), 1)

    def test_no_repair_cost_no_expense(self):
        trip, lr, bill = self._started_trip_with_bill()
        self._wizard(trip, resolution="repair", revised_eta=fields.Datetime.now() + timedelta(hours=2)).action_confirm()
        self.assertFalse(trip.breakdown_ids.expense_id)
