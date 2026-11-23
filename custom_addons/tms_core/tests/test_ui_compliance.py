from datetime import timedelta

from odoo import Command, fields
from odoo.tests import tagged
from odoo.tools.safe_eval import safe_eval

from .common import TmsCommon


@tagged("post_install", "-at_install")
class TestUiCompliance(TmsCommon):

    def _doc(self, holder, code):
        return holder.tms_compliance_document_ids.filtered(lambda d: d.type_code == code)

    def test_vehicle_row_valid(self):
        v = self.vehicle
        n = len(self._doc_types().filtered(lambda t: t.applies_to in ("vehicle", "both")))
        self.assertEqual(v.tms_cmp_gate_state, "valid")
        self.assertEqual(v.tms_cmp_gate, "Valid %s/%s" % (n, n))
        for prefix in ("rc", "fit", "permit", "ins", "puc"):
            self.assertEqual(v["tms_cmp_%s_state" % prefix], "valid")
        self.assertEqual(v.tms_cmp_puc_until, self._doc(v, "PUC").valid_until)
        self.assertFalse(v.tms_cmp_renew_code)

    def test_vehicle_expired_puc_blocks(self):
        v = self.vehicle
        self._doc(v, "PUC").valid_until = fields.Date.today() - timedelta(days=3)
        self.assertEqual(v.tms_cmp_puc_state, "expired")
        self.assertIn("expired", v.tms_cmp_puc_text)
        self.assertEqual(v.tms_cmp_gate_state, "blocked")
        self.assertEqual(v.tms_cmp_gate, "Blocked")
        self.assertEqual(v.tms_cmp_renew_code, "PUC")
        blocked = self.env["fleet.vehicle"].search([("tms_cmp_gate_state", "=", "blocked")])
        self.assertIn(v, blocked)
        self.assertNotIn(self.vehicle2, blocked)

    def test_vehicle_expiring_and_missing(self):
        v = self.vehicle
        self._doc(v, "FIT").valid_until = fields.Date.today() + timedelta(days=10)
        self.assertEqual(v.tms_cmp_fit_state, "expiring")
        self.assertIn("10 d", v.tms_cmp_fit_text)
        self.assertEqual(v.tms_cmp_gate_state, "expiring")
        self.assertIn(v, self.env["fleet.vehicle"].search([("tms_cmp_gate_state", "=", "expiring")]))
        self._doc(v, "PERMIT").unlink()
        self.assertEqual(v.tms_cmp_permit_state, "missing")
        self.assertEqual(v.tms_cmp_gate_state, "blocked")
        self.assertEqual(v.tms_cmp_renew_code, "PERMIT")

    def test_insurance_policy_drives_cell(self):
        v = self.vehicle
        insurer = self.env["res.partner"].create({"name": "New India Assurance"})
        end = fields.Date.today() + timedelta(days=20)
        self.env["tms.insurance"].create({
            "policy_number": "NIA-7713", "insurer_id": insurer.id, "state": "active",
            "start_date": fields.Date.today() - timedelta(days=345), "end_date": end,
            "vehicle_ids": [Command.link(v.id)],
        })
        self.assertEqual(v.tms_cmp_ins_until, end)
        self.assertNotEqual(v.tms_cmp_ins_until, self._doc(v, "INS").valid_until)
        self.assertEqual(v.tms_cmp_ins_state, "expiring")
        self.assertEqual(v.tms_cmp_gate_state, "expiring")
        action = v.action_renew_document("INS")
        self.assertEqual(action["res_model"], "tms.insurance")
        self.assertEqual(action["context"]["default_vehicle_ids"], [v.id])

    def test_driver_row_from_licence_fields(self):
        driver = self.env["tms.driver"].create({
            "name": "Gurpreet Singh", "driver_license_number": "PB1020090045678", "driver_license_type": "C",
            "driver_license_expiration_date": fields.Date.today() + timedelta(days=12),
        })
        self.assertEqual(driver.tms_cmp_dl_until, driver.driver_license_expiration_date)
        self.assertEqual(driver.tms_cmp_dl_state, "expiring")
        self.assertEqual(driver.tms_cmp_gate_state, "expiring")
        driver.driver_license_expiration_date = fields.Date.today() - timedelta(days=1)
        self.assertEqual(driver.tms_cmp_dl_state, "expired")
        self.assertEqual(driver.tms_cmp_gate, "Blocked")
        # a licence document takes precedence, as in the gate
        self.assertEqual(self.driver.tms_cmp_gate_state, "valid")
        self.assertEqual(self.driver.tms_cmp_dl_until, self._doc(self.driver, "DL").valid_until)
        # a user without access to the licence number still gets the register
        clerk_view = driver.with_user(self.u_clerk)
        self.assertEqual(clerk_view.tms_cmp_dl_state, "expired")

    def test_renew_action_prefilled_and_gate_picks_it_up(self):
        v = self.vehicle
        self._doc(v, "PUC").valid_until = fields.Date.today() - timedelta(days=2)
        action = v.action_renew_document()
        self.assertEqual(action["res_model"], "tms.compliance.document")
        self.assertEqual(action["target"], "new")
        ctx = action["context"]
        puc_type = self.env.ref("tms_core.doc_type_puc")
        self.assertEqual(ctx["default_type_id"], puc_type.id)
        self.assertEqual(ctx["default_vehicle_id"], v.id)
        self.assertEqual(action["views"][0][0], self.env.ref("tms_core.tms_compliance_document_renew_form").id)
        doc = self.env["tms.compliance.document"].with_context(ctx).create({
            "number": "MH04-PUC-883120", "valid_until": fields.Date.today() + timedelta(days=180),
            "attachment": b"UERG", "attachment_name": "puc.pdf",
        })
        self.assertEqual(doc.type_id, puc_type)
        self.assertEqual(doc.vehicle_id, v)
        self.assertEqual(v.tms_cmp_puc_state, "valid")
        self.assertEqual(v.tms_cmp_gate_state, "valid")
        result, failures, _w, _s = self.env["tms.compliance.gate"].evaluate(fields.Date.today(), vehicle=v)
        self.assertEqual(result, "pass", failures)
        # driver and per-document renewals
        self.assertEqual(self.driver.action_renew_document("DL")["context"]["default_driver_id"], self.driver.id)
        mv_doc = self.market_vehicle.compliance_document_ids[:1]
        ctx = mv_doc.action_tms_renew()["context"]
        self.assertEqual(ctx["default_market_vehicle_id"], self.market_vehicle.id)
        self.assertNotIn("default_vehicle_id", ctx)

    def test_register_excludes_market_vehicles(self):
        market_fleet = self.market_vehicle._tms_fleet_vehicle()
        action = self.env.ref("tms_core.action_tms_compliance_register")
        rows = self.env["fleet.vehicle"].search(safe_eval(action.domain))
        self.assertIn(self.vehicle, rows)
        self.assertNotIn(market_fleet, rows)
        self.assertNotIn(market_fleet, self.env["fleet.vehicle"].search([("tms_cmp_gate_state", "in", ("blocked", "expiring", "valid"))]))
        menu = self.env.ref("tms_core.menu_tms_compliance_document")
        self.assertEqual(menu.name, "Compliance register")
        self.assertEqual(menu.action, action)
        self.assertEqual(self.env.ref("tms_core.menu_tms_report_compliance").action,
                         self.env.ref("tms_core.action_tms_compliance_document"))
        # the flat list in the register context starts on own vehicles and drivers
        docs_action = self.env.ref("tms_core.action_tms_compliance_document_register")
        self.assertIn("search_default_own_fleet", docs_action.context)
        own = self.env["tms.compliance.document"].search([("market_vehicle_id", "=", False)])
        self.assertFalse(own.filtered("market_vehicle_id"))

    def test_document_days_left(self):
        doc = self._doc(self.vehicle, "RC")
        doc.valid_until = fields.Date.today() + timedelta(days=5)
        self.assertEqual(doc.tms_days_left, 5)
        self.assertEqual(doc.tms_days_left_text, "5 d")
        doc.valid_until = fields.Date.today() - timedelta(days=4)
        self.assertEqual(doc.tms_days_left_text, "Expired 4 d ago")

    def test_views_load(self):
        for model, xmlid in (("fleet.vehicle", "tms_core.fleet_vehicle_compliance_register_list"),
                             ("tms.driver", "tms_core.tms_driver_compliance_register_list"),
                             ("tms.compliance.document", "tms_core.tms_compliance_document_renew_form")):
            view = self.env.ref(xmlid)
            self.env[model].get_views([(view.id, view.type)])
        switch = self.vehicle.action_tms_compliance_register_switch()
        self.assertEqual(switch["res_model"], "tms.driver")
