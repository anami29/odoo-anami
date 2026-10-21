from datetime import timedelta

from odoo import Command, fields

from odoo.addons.account.tests.common import AccountTestInvoicingCommon


class TmsCommon(AccountTestInvoicingCommon):
    """Indian company with GST chart, the TMS roles as users, masters and a compliant fleet."""

    @classmethod
    @AccountTestInvoicingCommon.setup_country("in")
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.company_data["company"]
        # the test runner's user acts as an all-rounder
        cls.env.user.groups_id = [Command.link(cls.env.ref(g).id) for g in (
            "tms_core.group_tms_admin", "tms_core.group_dispatcher", "tms_core.group_branch_accountant",
            "tms_core.group_booking_clerk", "purchase.group_purchase_manager")]
        cls.company.write({
            "name": "Premier Transport Bhiwandi",
            "state_id": cls.env.ref("base.state_in_mh").id,
            "vat": "27AAGCP1234L1ZV",
            "tms_branch_code": "BHW",
            "tms_carrier_liability_limit": 100000.0,
        })
        cls.env["tms.gta.option"].create({
            "company_id": cls.company.id,
            "date_from": fields.Date.today().replace(month=1, day=1) - timedelta(days=366),
            "date_to": fields.Date.today().replace(month=12, day=31) + timedelta(days=366),
            "option": "fcm5",
        })
        Account = cls.env["account.account"]
        cls.driver_adv_account = Account.create({"code": "123450", "name": "Driver advances",
                                                 "account_type": "asset_receivable", "reconcile": True})
        cls.fuel_card_account = Account.create({"code": "212340", "name": "Fuel card provider",
                                                "account_type": "liability_current"})
        cls.tds_rec_account = Account.create({"code": "123460", "name": "TDS receivable",
                                              "account_type": "asset_current"})
        cls.company.write({
            "tms_driver_advance_account_id": cls.driver_adv_account.id,
            "tms_fuel_card_account_id": cls.fuel_card_account.id,
            "tms_fastag_account_id": cls.fuel_card_account.id,
            "tms_tds_receivable_account_id": cls.tds_rec_account.id,
        })
        # ---- users per role (BR-BRN-07)
        def user(login, *groups):
            return cls.env["res.users"].with_context(no_reset_password=True).create({
                "name": login.title(),
                "login": login,
                "email": "%s@example.com" % login,
                "company_id": cls.company.id,
                "company_ids": [Command.set(cls.company.ids)],
                "groups_id": [Command.set([cls.env.ref("base.group_user").id]
                                          + [cls.env.ref(g).id for g in groups])],
            })

        cls.u_clerk = user("clerk", "tms_core.group_booking_clerk")
        cls.u_dispatcher = user("dispatcher", "tms_core.group_dispatcher")
        cls.u_verifier = user("verifier", "tms_core.group_pod_verifier")
        cls.u_accountant = user("accountant", "tms_core.group_branch_accountant",
                                "account.group_account_manager")
        cls.u_manager = user("manager", "tms_core.group_branch_manager", "tms_core.group_pod_verifier")
        cls.u_ops = user("opshead", "tms_core.group_operations_head", "tms_core.group_branch_manager")
        cls.u_finance = user("finance", "tms_core.group_finance_head", "tms_core.group_branch_accountant")
        cls.u_admin = user("tmsadmin", "tms_core.group_tms_admin", "tms_core.group_branch_accountant")
        cls.u_fleet = user("fleet", "tms_core.group_fleet_manager")
        # ---- masters
        Partner = cls.env["res.partner"]
        cls.loc_bhiwandi = Partner.create({"name": "Bhiwandi yard", "tms_location": True, "city": "Bhiwandi",
                                           "state_id": cls.env.ref("base.state_in_mh").id})
        cls.loc_nagpur = Partner.create({"name": "Nagpur depot", "tms_location": True, "city": "Nagpur",
                                         "state_id": cls.env.ref("base.state_in_mh").id})
        cls.customer = Partner.create({
            "name": "Konkan Agro Foods", "vat": "27AAACK1234A1Z5", "l10n_in_pan": "AAACK1234A",
            "state_id": cls.env.ref("base.state_in_mh").id, "country_id": cls.env.ref("base.in").id,
            "l10n_in_gst_treatment": "regular", "tms_freight_basis": "tbb", "tms_billing_cycle": "monthly",
            "tms_credit_limit": 1000000.0, "email": "konkan@example.com",
            "tms_free_hours_unloading": 4.0, "tms_detention_rate": 2400.0,
        })
        cls.consignee = Partner.create({"name": "Vidarbha Traders", "city": "Nagpur",
                                        "state_id": cls.env.ref("base.state_in_mh").id})
        cls.vendor = Partner.create({
            "name": "Sai Roadlines", "l10n_in_pan": "ABCPS1234D", "tms_is_transporter": True,
            "tms_gta_status": "non_gta", "tms_advance_percent": 70.0,
            "bank_ids": [Command.create({"acc_number": "001122334455"})],
        })
        cls.vehicle_class = cls.env["tms.vehicle.class"].create({"name": "32 ft MXL", "planned_annual_km": 72000})
        cls.vehicle = cls._make_vehicle("MH04LE7713")
        cls.vehicle2 = cls._make_vehicle("MH04LE7714")
        cls.driver = cls._make_driver("Ramesh Patil", "MH0420110012345")
        cls.driver2 = cls._make_driver("Suresh Jadhav", "MH0420110012346")
        cls.market_vehicle = cls.env["tms.market.vehicle"].create({
            "name": "MH15HH4410", "owner_id": cls.vendor.id, "payload_kg": 16000.0})
        cls._make_docs(market_vehicle=cls.market_vehicle)

    # ------------------------------------------------------------------
    @classmethod
    def _doc_types(cls):
        return cls.env["tms.compliance.document.type"].search([("mandatory_for_start", "=", True)])

    @classmethod
    def _make_docs(cls, vehicle=None, driver=None, market_vehicle=None, days=365):
        until = fields.Date.today() + timedelta(days=days)
        for dtype in cls._doc_types():
            if dtype.applies_to in ("vehicle", "both") and (vehicle or market_vehicle):
                cls.env["tms.compliance.document"].create({
                    "type_id": dtype.id, "number": "%s-%s" % (dtype.code, (vehicle or market_vehicle).id),
                    "valid_until": until,
                    "vehicle_id": vehicle.id if vehicle else False,
                    "market_vehicle_id": market_vehicle.id if market_vehicle else False,
                })
            if dtype.applies_to in ("driver", "both") and driver:
                cls.env["tms.compliance.document"].create({
                    "type_id": dtype.id, "number": "DL-%s" % driver.id, "valid_until": until,
                    "driver_id": driver.id,
                })

    @classmethod
    def _make_vehicle(cls, plate):
        brand = cls.env["fleet.vehicle.model.brand"].create({"name": "Tata %s" % plate})
        model = cls.env["fleet.vehicle.model"].create({"name": "Signa 4825", "brand_id": brand.id})
        vehicle = cls.env["fleet.vehicle"].create({
            "model_id": model.id, "license_plate": plate, "tms_ownership": "owned",
            "tms_payload_kg": 25000.0, "tms_vehicle_class_id": cls.vehicle_class.id, "tms_fuel_norm": 3.5,
            "company_id": cls.company.id,
        })
        cls._make_docs(vehicle=vehicle)
        return vehicle

    @classmethod
    def _make_driver(cls, name, licence):
        driver = cls.env["tms.driver"].create({"name": name, "driver_license_number": licence,
                                               "driver_license_expiration_date": fields.Date.today() + timedelta(days=900)})
        cls._make_docs(driver=driver)
        return driver

    def _lr(self, basis="tbb", weight=9234.0, packages=100, rate=4.5, value=40000.0, confirm=True, **kw):
        vals = {
            "consignor_id": self.customer.id,
            "consignee_id": self.consignee.id,
            "freight_basis": basis,
            "origin_id": self.loc_bhiwandi.id,
            "destination_id": self.loc_nagpur.id,
            "rate_type": "per_kg",
            "typed_rate": rate,
            "typed_rate_reason": "Counter rate",
            "line_ids": [Command.create({"commodity": "Rice bags", "packaging_type": "bags",
                                         "packages": packages, "actual_weight": weight,
                                         "declared_value": value})],
        }
        vals.update(kw)
        lr = self.env["tms.consignment"].create(vals)
        if confirm:
            lr.action_confirm()
        return lr

    def _trip(self, lrs, fleet="owned", start=True, **kw):
        vals = {
            "fleet_type": fleet,
            "origin_id": self.loc_bhiwandi.id,
            "destination_id": self.loc_nagpur.id,
            "scheduled_date_start": fields.Datetime.now(),
            "scheduled_duration": 24.0,
            "consignment_link_ids": [Command.create({"consignment_id": lr.id, "packages": lr.total_packages})
                                     for lr in lrs],
        }
        if fleet == "owned":
            vals.update({"planned_vehicle_id": self.vehicle.id, "planned_driver_id": self.driver.id,
                         "start_odometer": 10000.0})
        else:
            vals.update({"planned_market_vehicle_id": self.market_vehicle.id, "broker_id": self.vendor.id,
                         "planned_hire_amount": 30000.0, "market_driver_name": "Babu",
                         "market_driver_licence": "MH15X1",
                         "market_driver_licence_expiry": fields.Date.today() + timedelta(days=400)})
        vals.update(kw)
        trip = self.env["tms.order"].create(vals)
        if fleet == "market":
            action = trip.action_create_hire_memo()
            memo = self.env["purchase.order"].browse(action["res_id"])
            memo.button_confirm()
        if start:
            trip.action_confirm()
            trip.button_start_order()
        return trip

    def _deliver_and_verify(self, trip, **link_vals):
        for link in trip.consignment_link_ids:
            if link_vals:
                link.write(link_vals)
            link.action_mark_delivered()
            link.write({"pod_file": b"UE9E", "pod_file_name": "pod.pdf"})
            link.with_user(self.u_dispatcher).action_upload_pod()
            link.with_user(self.u_verifier).action_verify_pod()
