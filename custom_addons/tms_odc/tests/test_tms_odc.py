import base64
from datetime import datetime, time, timedelta

from odoo import Command, fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from odoo.addons.tms_estimate.tests.test_tms_estimate import EstimateCommon


class OdcCommon(EstimateCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company.write({"tms_odc_enabled": True, "tms_odc_max_width": 2.6, "tms_odc_max_height": 3.8,
                           "tms_odc_max_length": 12.0, "tms_odc_max_weight": 25.0, "tms_odc_km_per_day": 80.0})
        cls.customer.tms_credit_limit = 100000000.0
        cls.p_puller.tms_equipment_kind = "puller"
        cls.p_axle.write({"tms_equipment_kind": "axle", "tms_rated_capacity": 8.0})
        cls.p_escort.tms_equipment_kind = "escort"
        india = cls.env.ref("base.in")
        State = cls.env["res.country.state"]

        def state(code, name):
            return State.search([("country_id", "=", india.id), ("code", "=", code)], limit=1) or State.create(
                {"country_id": india.id, "code": code, "name": name})
        cls.st_mh = state("MH", "Maharashtra")
        cls.st_ts = state("TS", "Telangana")
        cls.st_ka = state("KA", "Karnataka")
        cls.env.user.tz = "Asia/Kolkata"

    def _odc_order(self, km=720.0, cargo="odc"):
        return self.env["sale.order"].create({
            "partner_id": self.customer.id,
            "require_signature": False,
            "tms_acceptance_file": base64.b64encode(b"Accepted by email"),  # BR-QTN-06
            "tms_acceptance_name": "acceptance.txt",
            "order_line": [Command.create({
                "product_id": self.env.ref("tms_estimate.product_movement").id,
                "name": "ODC move, 92 t transformer",
                "tms_is_movement": True,
                "tms_cargo_type": cargo,
                "tms_origin_id": self.loc_bhiwandi.id,
                "tms_destination_id": self.loc_nagpur.id,
                "tms_distance_km": km,
                "product_uom_qty": 1,
            })],
        })

    def _piece(self, est, weight=92.0):
        return self.env["tms.estimate.piece"].create({
            "estimate_id": est.id, "description": "Transformer", "count": 1,
            "length": 9.8, "width": 4.2, "height": 4.6, "weight": weight})

    def _routes(self, est, kms=(300.0, 200.0, 220.0)):
        R = self.env["tms.estimate.route"]
        for st, km in zip((self.st_mh, self.st_ts, self.st_ka), kms):
            R.create({"estimate_id": est.id, "state_id": st.id, "km": km, "permit_authority": "PWD %s" % st.code})

    def _ready_order(self):
        order = self._odc_order()
        est = order.order_line.tms_estimate_id
        self._worked_example(est)
        self._piece(est)
        self._routes(est)
        order.action_confirm()
        return order

    def _odc_lr(self, order, confirm=True):
        lr = self._lr(confirm=False, rate_type=False, typed_rate=0.0, typed_rate_reason=False,
                      sale_line_id=order.order_line.id, weight=92000.0, packages=1)
        lr.line_ids.write({"commodity": "Transformer", "packaging_type": "loose",
                           "piece_length": 9.8, "piece_width": 4.2, "piece_height": 4.6})
        if confirm:
            lr.action_confirm()
        return lr

    def _permit(self, trip, state, **kw):
        today = fields.Date.context_today(trip)
        vals = {"trip_id": trip.id, "state_id": state.id, "name": "P-%s" % state.code,
                "valid_from": today - timedelta(days=1), "valid_to": today + timedelta(days=10),
                "puller_plate": "MH 04 LE 7713"}
        vals.update(kw)
        return self.env["tms.odc.permit"].create(vals)

    def _escort_po(self):
        po = self.env["purchase.order"].create({
            "partner_id": self.vendor.id,
            "order_line": [Command.create({"product_id": self.p_escort.id, "product_qty": 9, "price_unit": 4500})]})
        po.button_confirm()
        return po

    def _confirmed_trip(self):
        order = self._ready_order()
        lr = self._odc_lr(order)
        trip = self._trip(lr, start=False)
        self.assertEqual(trip.tms_cargo_type, "odc")
        trip.action_load_odc_from_estimate()
        axle = trip.equipment_ids.filtered(lambda e: e.kind == "axle")
        axle.write({"asset_ref": "MAT-14 hydraulic axle set"})
        escort = trip.equipment_ids.filtered(lambda e: e.kind == "escort")
        escort.write({"source": "purchase", "purchase_line_id": self._escort_po().order_line.id,
                      "registration": "MH 12 AB 1111"})
        trip.action_confirm()
        return trip, lr


@tagged("post_install", "-at_install")
class TestOdc(OdcCommon):
    def test_switch_and_settings(self):
        """BR-CFG-13: ODC is off by default; Regular is the default Cargo Type (BR-ODC-01)."""
        self.assertFalse(self.env["res.company"].create({"name": "New branch"}).tms_odc_enabled)
        order = self._odc_order(cargo="regular")
        self.assertEqual(order.order_line.tms_cargo_type, "regular")
        self.company.tms_odc_enabled = False
        with self.assertRaises(ValidationError):
            order.order_line.tms_cargo_type = "odc"
        self.company.tms_odc_enabled = True
        order.order_line.tms_cargo_type = "odc"
        est = order.order_line.tms_estimate_id
        self.assertEqual(est.km_per_day, 80.0)
        self.assertEqual(est.days, 9)
        settings = self.env["res.config.settings"].create({})
        self.assertEqual(settings.tms_odc_max_weight, 25.0)

    def test_piece_suggests_odc(self):
        """BR-ODC-02, BR-AUD-06: a piece above a threshold suggests ODC; keeping Regular is logged."""
        order = self._odc_order(cargo="regular")
        line = order.order_line
        est = line.tms_estimate_id
        piece = self._piece(est)
        self.assertTrue(piece.is_odc)
        self.assertIn("width", piece.odc_reason)
        self.assertTrue(line.tms_odc_suggested)
        with self.assertRaises(UserError):
            order.action_quotation_sent()
        line.tms_regular_reason = "Customer ships under its own permits"
        log = self.env["tms.override.log"].search([("kind", "=", "cargo_regular"), ("res_id", "=", order.id)])
        self.assertTrue(log)
        order.action_quotation_sent()
        small = self._odc_order(cargo="regular")
        self.env["tms.estimate.piece"].create({"estimate_id": small.order_line.tms_estimate_id.id,
                                               "description": "Panel", "length": 6.0, "width": 2.0,
                                               "height": 3.0, "weight": 10.0})
        self.assertFalse(small.order_line.tms_odc_suggested)

    def test_odc_estimate_checks(self):
        """BR-ODC-03: puller, axles carrying the heaviest piece, the route adding up to the distance."""
        order = self._odc_order()
        est = order.order_line.tms_estimate_id
        self._piece(est)
        with self.assertRaises(UserError):
            order.action_quotation_sent()  # no puller, no axles, no route
        self._worked_example(est)
        self.assertAlmostEqual(est.rated_capacity, 112.0)
        self._routes(est, kms=(300.0, 200.0, 100.0))
        self.assertEqual(est.state_count, 3)
        permit_line = est.line_ids.filtered(lambda x: x.product_id == self.p_permit)
        self.assertEqual(permit_line.quantity, 3)
        with self.assertRaises(UserError) as err:
            order.action_quotation_sent()
        self.assertIn("600", str(err.exception))
        est.route_ids.filtered(lambda r: r.state_id == self.st_ka).km = 220.0
        self.p_axle.tms_rated_capacity = 5.0  # 14 × 5 = 70 t < 92 t
        est.invalidate_recordset(["odc_problems", "rated_capacity"])
        with self.assertRaises(UserError) as err:
            order.action_quotation_sent()
        self.assertIn("92", str(err.exception))
        self.p_axle.tms_rated_capacity = 8.0
        order.action_quotation_sent()
        order.action_confirm()
        self.assertEqual(est.state, "locked")
        with self.assertRaises(UserError):
            est.route_ids[0].km = 10.0
        with self.assertRaises(UserError):
            self._piece(est)

    def test_lr_dimensions_and_ewaybill(self):
        """BR-ODC-01, BR-ODC-04: Cargo Type flows to the LR; dimensions; ODC e-way bills at 20 km a day."""
        order = self._ready_order()
        lr = self._lr(confirm=False, rate_type=False, typed_rate=0.0, typed_rate_reason=False,
                      sale_line_id=order.order_line.id, weight=92000.0, packages=1)
        self.assertEqual(lr.tms_cargo_type, "odc")
        self.assertTrue(lr.odc_suggested)
        with self.assertRaises(UserError):
            lr.action_confirm()
        lr.line_ids.write({"piece_length": 9.8, "piece_width": 4.2, "piece_height": 4.6})
        generated = datetime.combine(fields.Date.today(), time(6, 0))
        bill = self.env["tms.ewaybill"].create({"name": "123456789013", "consignment_id": lr.id,
                                                "generated_on": generated, "distance_km": 720})
        self.assertTrue(bill.is_odc)
        self.assertEqual((bill.valid_until.date() - generated.date()).days, 36)
        self.assertFalse(lr.ewaybill_validity_mismatch)
        bill.valid_until = generated + timedelta(days=4)
        lr.invalidate_recordset(["ewaybill_validity_mismatch"])
        self.assertTrue(lr.ewaybill_validity_mismatch)
        lr.action_confirm()
        self.assertEqual(lr.state, "confirmed")
        # a Regular LR with an oversize piece needs a reason
        reg = self._lr(confirm=False, weight=30000.0, packages=1)
        with self.assertRaises(UserError):
            reg.action_confirm()
        reg.tms_regular_reason = "Customer's own permit"
        reg.action_confirm()
        self.assertTrue(self.env["tms.override.log"].search([("kind", "=", "cargo_regular"), ("res_id", "=", reg.id),
                                                             ("res_model", "=", "tms.consignment")]))

    def test_trip_equipment_and_confirm(self):
        """BR-ODC-05: equipment from the estimate, filled before Confirm; substitutions need the OH."""
        order = self._ready_order()
        lr = self._odc_lr(order)
        trip = self._trip(lr, start=False)
        self.assertEqual(trip.tms_cargo_type, "odc")
        with self.assertRaises(UserError):
            trip.action_confirm()  # loads equipment, axles and escort unfilled
        trip.action_load_odc_from_estimate()
        self.assertEqual(sorted(trip.equipment_ids.mapped("kind")), ["axle", "escort", "puller"])
        puller = trip.equipment_ids.filtered(lambda e: e.kind == "puller")
        self.assertEqual(puller.vehicle_id, self.vehicle)
        self.assertEqual(trip.odc_state_ids, self.st_mh | self.st_ts | self.st_ka)
        self.assertEqual(trip.payload_kg, 112000.0)
        axle = trip.equipment_ids.filtered(lambda e: e.kind == "axle")
        self.assertEqual(axle.required_count, 14)
        axle.write({"asset_ref": "MAT-12", "actual_count": 12, "substitution_reason": "Two lines in service"})
        escort = trip.equipment_ids.filtered(lambda e: e.kind == "escort")
        escort.write({"source": "purchase", "purchase_line_id": self._escort_po().order_line.id})
        with self.assertRaises(UserError) as err:
            trip.action_confirm()
        self.assertIn("Operations Head", str(err.exception))
        with self.assertRaises(UserError):
            axle.with_user(self.u_dispatcher).action_approve_substitution()
        axle.with_user(self.u_ops).action_approve_substitution()
        self.assertTrue(self.env["tms.override.log"].search([("kind", "=", "equipment"), ("res_id", "=", trip.id)]))
        # the puller must be the trip's vehicle, so that every cost line names it
        puller.vehicle_id = self.vehicle2
        with self.assertRaises(UserError):
            trip.action_confirm()
        puller.vehicle_id = self.vehicle
        trip.action_confirm()
        self.assertEqual(trip.state_code, "confirmed")
        # a Regular trip may not carry an ODC LR
        lr2 = self._odc_lr(order)
        trip2 = self._trip(lr2, start=False, planned_vehicle_id=self.vehicle2.id, planned_driver_id=self.driver2.id)
        trip2.tms_cargo_type = "regular"
        with self.assertRaises(UserError):
            trip2.action_confirm()

    def test_start_gate(self):
        """BR-ODC-06, UC-29: permits for every state covering dates and puller, survey, escort; no override."""
        trip, _lr = self._confirmed_trip()
        self.company.with_context(tms_reason="ODC gate test").tms_enforce_compliance = False
        with self.assertRaises(UserError):
            trip.button_start_order()
        self._permit(trip, self.st_mh)
        self._permit(trip, self.st_ts)
        with self.assertRaises(UserError) as err:
            trip.button_start_order()
        self.assertIn("Karnataka", str(err.exception))
        ka = self._permit(trip, self.st_ka, puller_plate="MH 04 XX 0001")
        with self.assertRaises(UserError) as err:
            trip.button_start_order()
        self.assertIn("another puller", str(err.exception))
        ka.puller_plate = "MH04LE7713"
        ka.valid_to = fields.Date.today() - timedelta(days=1)
        ka.valid_from = fields.Date.today() - timedelta(days=5)
        with self.assertRaises(UserError) as err:
            trip.button_start_order()
        self.assertIn("trip dates", str(err.exception))
        ka.valid_to = fields.Date.today() + timedelta(days=10)
        with self.assertRaises(UserError) as err:
            trip.button_start_order()
        self.assertIn("survey", str(err.exception))
        with self.assertRaises(UserError):
            trip.action_approve_route_survey()  # no file yet
        trip.write({"survey_file": b"UERG", "survey_file_name": "survey.pdf", "survey_date": fields.Date.today()})
        with self.assertRaises(UserError):
            trip.with_user(self.u_dispatcher).action_approve_route_survey()
        trip.with_user(self.u_fleet).action_approve_route_survey()
        self.assertTrue(trip.survey_approved)
        ka.write({"escort_required": True, "escort_count": 2})
        with self.assertRaises(UserError) as err:
            trip.button_start_order()
        self.assertIn("escort", str(err.exception))
        ka.escort_count = 1
        self.assertFalse(trip.odc_start_gaps)
        trip.button_start_order()
        self.assertEqual(trip.state_code, "started")
        # a new survey file lapses the approval
        trip.survey_file = b"UERH"
        self.assertFalse(trip.survey_approved)

    def test_dsr_window_eta_and_trip_sheet(self):
        """BR-ODC-07, BR-NTF-04, BR-PRN-03."""
        trip, lr = self._confirmed_trip()
        for st in (self.st_mh, self.st_ts, self.st_ka):
            self._permit(trip, st, window_from=22.0, window_to=6.0, escort_required=st == self.st_ka)
        trip.write({"survey_file": b"UERG", "survey_file_name": "survey.pdf"})
        trip.with_user(self.u_ops).action_approve_route_survey()
        trip.button_start_order()
        Dsr = self.env["tms.trip.dsr"]
        noon_ist = datetime.combine(fields.Date.today(), time(6, 30))  # 12:00 in Kolkata
        Dsr.create({"trip_id": trip.id, "location": "Bhiwandi", "status": "departed", "logged_at": noon_ist})
        self.assertTrue(trip.activity_ids.filtered(lambda a: "movement window" in (a.summary or "")))
        Dsr.create({"trip_id": trip.id, "location": "Nashik", "status": "halted_window", "logged_at": noon_ist})
        now = fields.Datetime.now()
        self.env["tms.ewaybill"].create({"name": "123456789099", "consignment_id": lr.id, "generated_on": now,
                                         "distance_km": 720, "valid_until": now + timedelta(days=1, hours=12)})
        Dsr.create({"trip_id": trip.id, "location": "Dhule", "status": "on_schedule",
                    "eta": now + timedelta(days=3)})
        self.assertTrue(trip.activity_ids.filtered(lambda a: "123456789099" in (a.summary or "")))
        self.assertEqual(self.env["tms.order"]._cron_odc_eta_alert(), trip)
        html = self.env["ir.actions.report"]._render_qweb_html("tms_core.report_trip_sheet", trip.ids)[0]
        html = html.decode() if isinstance(html, bytes) else html
        self.assertIn("ODC permits", html)
        self.assertIn("P-KA", html)
        self.assertIn("22:00", html)
