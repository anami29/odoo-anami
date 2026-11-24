from datetime import date

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from odoo.addons.tms_estimate.tests.test_tms_estimate import EstimateCommon


class DocTypeCommon(EstimateCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ref = cls.env.ref
        cls.t = {code: ref("tms_document_type.doc_type_%s" % code.lower().replace("-", "_"))
                 for code in ("TRN-SO", "GEN-SO", "SCR-SO", "VEH-SO", "TRN-PO", "GEN-PO", "AMC-PO", "TRN-INV",
                              "GEN-INV", "TRN-BILL", "GEN-BILL")}
        Product = cls.env["product.product"]
        cls.p_chair = Product.create({"name": "Office chair", "type": "consu", "list_price": 500.0,
                                      "invoice_policy": "delivery",
                                      "taxes_id": [Command.clear()], "supplier_taxes_id": [Command.clear()]})
        cls.p_laptop = Product.create({"name": "Laptop", "type": "consu", "list_price": 70000.0,
                                       "standard_price": 62000.0, "taxes_id": [Command.clear()],
                                       "supplier_taxes_id": [Command.clear()]})
        cls.p_scrap = Product.create({"name": "MS scrap", "type": "consu", "list_price": 50.0,
                                      "categ_id": ref("tms_document_type.product_category_scrap").id,
                                      "taxes_id": [Command.clear()]})
        cls.p_truck = Product.create({"name": "Used truck", "type": "consu", "list_price": 650000.0,
                                      "taxes_id": [Command.clear()]})
        cls.buyer = cls.env["res.partner"].create({"name": "Konkan Scrap Traders", "city": "Mumbai"})
        cls.analytic = cls.company._tms_analytic_account()
        cls.expense_account = cls.company_data["default_account_expense"]
        cls.vehicle3 = cls._make_vehicle("MH12AB1234")

    def _so(self, dtype, lines, user=None, partner=None, **kw):
        env = self.env(user=user) if user else self.env
        return env["sale.order"].create({
            "partner_id": (partner or self.buyer).id,
            "tms_document_type_id": self.t[dtype].id,
            "order_line": [Command.create({"product_id": p.id, "product_uom_qty": q, "price_unit": price})
                           for p, q, price in lines],
            **kw,
        })

    def _po(self, dtype, lines, user=None, **kw):
        env = self.env(user=user) if user else self.env
        return env["purchase.order"].create({
            "partner_id": self.vendor.id,
            "tms_document_type_id": self.t[dtype].id,
            "order_line": [Command.create({"product_id": p.id, "product_qty": q, "price_unit": price,
                                           "analytic_distribution": {str(self.analytic.id): 100.0}})
                           for p, q, price in lines],
            **kw,
        })

    def _bill(self, dtype="GEN-BILL", amount=62000.0, analytic=True, **line_vals):
        vals = {"name": "Laptop", "account_id": self.expense_account.id, "quantity": 1, "price_unit": amount,
                "tax_ids": [Command.clear()]}
        if analytic:
            vals["analytic_distribution"] = {str(self.analytic.id): 100.0}
        vals.update(line_vals)
        return self.env["account.move"].create({
            "move_type": "in_invoice", "partner_id": self.vendor.id, "invoice_date": fields.Date.today(),
            "tms_document_type_id": self.t[dtype].id, "invoice_line_ids": [Command.create(vals)]})


@tagged("post_install", "-at_install")
class TestDocumentType(DocTypeCommon):

    def test_seeded_types(self):
        """UT-DOC-01 to UT-DOC-03."""
        Type = self.env["tms.document.type"]
        self.assertEqual(set(Type.search([]).mapped("code")), set(self.t))
        self.assertEqual(Type.search([("vehicle_disposal", "=", True)]), self.t["VEH-SO"])
        for code, applies, behaviour in (("TRN-SO", "sale", "transport"), ("SCR-SO", "sale", "standard"),
                                         ("AMC-PO", "purchase", "standard"), ("TRN-BILL", "in_invoice", "transport"),
                                         ("GEN-INV", "out_invoice", "standard")):
            self.assertEqual((self.t[code].applies_to, self.t[code].behaviour), (applies, behaviour), code)
        it = Type.with_user(self.u_admin).create({
            "code": "IT-PO", "name": "IT purchase", "applies_to": "purchase", "behaviour": "standard",
            "invoice_type_id": self.t["GEN-BILL"].id, "approval_limit": 100000.0})
        offered = self.env(user=self.u_accountant)["purchase.order"].new({}).tms_document_type_allowed_ids
        self.assertLessEqual({"IT-PO", "TRN-PO", "GEN-PO"}, set(offered.mapped("code")), offered.mapped("code"))
        with self.assertRaises(AccessError):
            self.t["GEN-SO"].with_user(self.u_accountant).behaviour = "transport"

    def test_type_filter(self):
        """UT-DOC-04, UT-DOC-05, UT-DOC-26."""
        po = self._po("TRN-PO", [(self.p_laptop, 1, 100.0)], user=self.u_accountant)
        with self.assertRaises(ValidationError):
            po.tms_document_type_id = False
        archived = self.t["GEN-SO"].copy({"code": "OLD-SO", "name": "Old standard sale"})
        archived.active = False
        acc_env = self.env(user=self.u_accountant)
        so_types = acc_env["sale.order"].new({}).tms_document_type_allowed_ids
        self.assertEqual(set(so_types.mapped("applies_to")), {"sale"})
        self.assertLessEqual({"TRN-SO", "GEN-SO", "SCR-SO"}, set(so_types.mapped("code")), so_types.mapped("code"))
        self.assertNotIn("OLD-SO", so_types.mapped("code"))
        bill_types = acc_env["account.move"].with_context(default_move_type="in_invoice").new(
            {"move_type": "in_invoice"}).tms_document_type_allowed_ids
        self.assertEqual(set(bill_types.mapped("code")), {"TRN-BILL", "GEN-BILL"})
        # clerk: no Standard type offered, a Standard sales order is read-only
        clerk_inv = self.env(user=self.u_clerk)["account.move"].with_context(default_move_type="out_invoice").new(
            {"move_type": "out_invoice"}).tms_document_type_allowed_ids
        self.assertEqual(clerk_inv.mapped("code"), ["TRN-INV"])
        gen_so = self._so("GEN-SO", [(self.p_chair, 1, 500.0)])
        with self.assertRaises(AccessError):
            gen_so.with_user(self.u_clerk).note = "changed"
        self.assertIn("GEN-INV", acc_env["account.move"].with_context(default_move_type="out_invoice").new(
            {"move_type": "out_invoice"}).tms_document_type_allowed_ids.mapped("code"))
        disp_po = self.env(user=self.u_dispatcher)["purchase.order"].new({}).tms_document_type_allowed_ids
        self.assertIn("GEN-PO", disp_po.mapped("code"))
        disp_so = self.env(user=self.u_dispatcher)["sale.order"].new({}).tms_document_type_allowed_ids
        self.assertNotIn("GEN-SO", disp_so.mapped("code"))

    def test_default_type(self):
        """UT-DOC-06, UT-DOC-07."""
        acc = self.env(user=self.u_accountant)
        so = acc["sale.order"].create({"partner_id": self.buyer.id})
        po = acc["purchase.order"].create({"partner_id": self.vendor.id})
        bill = acc["account.move"].create({"move_type": "in_invoice", "partner_id": self.vendor.id})
        self.assertEqual((so.tms_document_type_id, po.tms_document_type_id, bill.tms_document_type_id),
                         (self.t["TRN-SO"], self.t["TRN-PO"], self.t["TRN-BILL"]))
        purchaser = self.u_accountant.copy({"login": "hopurchaser", "name": "Head-office purchaser"})
        purchaser.tms_document_type_ids = self.t["GEN-PO"] | self.t["GEN-SO"] | self.t["GEN-INV"] | \
            self.t["GEN-BILL"]
        hop = self.env(user=purchaser)
        self.assertEqual(hop["purchase.order"].create({"partner_id": self.vendor.id}).tms_document_type_id,
                         self.t["GEN-PO"])
        # transport flows ignore the personal default
        lead = self.env["crm.lead"].create({"name": "Lane Bhiwandi–Nagpur", "partner_id": self.customer.id})
        quote = hop["sale.order"].create({"partner_id": self.customer.id, "opportunity_id": lead.id})
        self.assertEqual(quote.tms_document_type_id, self.t["TRN-SO"])
        memo = hop["purchase.order"].sudo().create({"partner_id": self.vendor.id, "tms_is_hire_memo": True})
        self.assertEqual(memo.tms_document_type_id, self.t["TRN-PO"])
        lr = self._lr()
        inv = hop["account.move"].sudo().create({
            "move_type": "out_invoice", "partner_id": self.customer.id,
            "invoice_line_ids": [Command.create({"name": "Freight", "price_unit": 100.0, "quantity": 1,
                                                 "tms_consignment_id": lr.id, "tms_line_kind": "freight"})]})
        self.assertEqual(inv.tms_document_type_id, self.t["TRN-INV"])
        trip = self._trip(lr, start=False)
        exp_bill = hop["account.move"].sudo().create({
            "move_type": "in_invoice", "partner_id": self.vendor.id,
            "invoice_line_ids": [Command.create({"name": "Toll", "price_unit": 100.0, "quantity": 1,
                                                 "tms_trip_id": trip.id, "tms_cost_class": "trip_direct"})]})
        self.assertEqual(exp_bill.tms_document_type_id, self.t["TRN-BILL"])
        # a draft transport purchase with no links may become Standard; its lines then ask for nothing more
        draft = acc["purchase.order"].create({"partner_id": self.vendor.id})
        draft.tms_document_type_id = self.t["GEN-PO"]
        self.assertEqual(draft.tms_document_behaviour, "standard")

    def test_transport_rules_unchanged(self):
        """UT-DOC-08: a Transport sales order keeps every quotation rule."""
        order = self._order(tms_acceptance_file=False, tms_acceptance_name=False)
        self.assertEqual(order.tms_document_type_id, self.t["TRN-SO"])
        with self.assertRaises(UserError):
            order.action_confirm()
        with self.assertRaises(UserError):
            order._create_invoices()

    def test_standard_sale_plain(self):
        """UT-DOC-09: plain Odoo selling."""
        order = self._so("GEN-SO", [(self.p_chair, 10, 500.0)], user=self.u_accountant)
        order.action_confirm()
        self.assertEqual(order.state, "sale")
        self.assertEqual(order.order_line.tms_item_no, 10)
        order.order_line.qty_delivered = 6
        inv = order._create_invoices()
        self.assertEqual(inv.amount_untaxed, 3000.0)
        self.assertEqual(inv.tms_document_type_id, self.t["GEN-INV"])
        inv.action_post()
        self.assertEqual(inv.state, "posted")
        self.assertEqual(set(inv.invoice_line_ids.mapped("tms_line_kind")), {"other_income"})

    def test_standard_purchase_plain(self):
        """UT-DOC-10: plain Odoo buying; the bill posts to branch overhead."""
        po = self._po("GEN-PO", [(self.p_laptop, 2, 62000.0)], user=self.u_accountant)
        po.button_confirm()
        self.assertEqual(po.state, "purchase")
        po.order_line.qty_received = 1
        po.action_create_invoice()
        bill = po.invoice_ids
        self.assertEqual(bill.tms_document_type_id, self.t["GEN-BILL"])
        self.assertEqual(bill.invoice_line_ids.quantity, 1)
        self.assertEqual(bill.invoice_line_ids.price_unit, 62000.0)
        bill.invoice_date = fields.Date.today()
        bill.action_post()
        self.assertEqual(bill.invoice_line_ids.tms_cost_class, "overhead")

    def test_refuse_tms_links(self):
        """UT-DOC-11 to UT-DOC-13."""
        bill = self._bill()
        line = bill.invoice_line_ids
        trip = self._trip(self._lr(), start=False)
        order = self._order()
        est_line = self.env["tms.estimate.line"].create({"estimate_id": order.order_line.tms_estimate_id.id,
                                                         "product_id": self.p_toll.id})
        for vals in ({"tms_trip_id": trip.id}, {"tms_estimate_line_id": est_line.id},
                     {"vehicle_id": self.vehicle.id}, {"tms_cost_class": "truck_running"}):
            with self.assertRaises(ValidationError, msg=str(vals)), self.cr.savepoint():
                line.write(vals)
        gen_so = self._so("GEN-SO", [(self.p_chair, 1, 500.0)])
        for product in (self.p_puller, self.env.ref("tms_core.product_freight")):
            with self.assertRaises(ValidationError, msg=product.name), self.cr.savepoint():
                gen_so.write({"order_line": [Command.create({"product_id": product.id, "product_uom_qty": 1})]})
        freight = self.env["product.template"].create({
            "name": "Part-load freight", "type": "service",
            "categ_id": self.env.ref("tms_document_type.product_category_transport").id})
        self.assertTrue(freight.tms_transport_only)
        gen_po = self._po("GEN-PO", [(self.p_laptop, 1, 62000.0)])
        with self.assertRaises(ValidationError), self.cr.savepoint():
            gen_po.tms_is_hire_memo = True
        inv = self._so("GEN-SO", [(self.p_chair, 1, 500.0)])
        inv.action_confirm()
        inv.order_line.qty_delivered = 1
        gen_inv = inv._create_invoices()
        with self.assertRaises(ValidationError), self.cr.savepoint():
            gen_inv.invoice_line_ids.tms_consignment_id = self._lr()

    def test_allowed_categories(self):
        """UT-DOC-14."""
        order = self._so("SCR-SO", [(self.p_scrap, 1200, 50.0)])
        with self.assertRaises(ValidationError):
            order.write({"order_line": [Command.create({"product_id": self.p_laptop.id, "product_uom_qty": 1})]})

    def test_type_passed_on(self):
        """UT-DOC-15, UT-DOC-16."""
        so = self._so("GEN-SO", [(self.p_chair, 4, 500.0)])
        so.action_confirm()
        so.order_line.qty_delivered = 4
        inv = so._create_invoices()
        self.assertEqual(inv.tms_document_type_id, self.t["GEN-INV"])
        gen_po = self._po("GEN-PO", [(self.p_laptop, 1, 62000.0)])
        gen_po.button_confirm()
        gen_po.order_line.qty_received = 1
        gen_po.action_create_invoice()
        self.assertEqual(gen_po.invoice_ids.tms_document_type_id, self.t["GEN-BILL"])
        trn_po = self._po("TRN-PO", [(self.p_laptop, 1, 62000.0)])
        trn_po.button_confirm()
        trn_po.order_line.qty_received = 1
        trn_po.action_create_invoice()
        self.assertEqual(trn_po.invoice_ids.tms_document_type_id, self.t["TRN-BILL"])
        inv.action_post()
        refund = inv._reverse_moves()
        self.assertEqual(refund.tms_document_type_id, self.t["GEN-INV"])
        bill = gen_po.invoice_ids
        bill.invoice_date = fields.Date.today()
        bill.action_post()
        self.assertEqual(bill._reverse_moves().tms_document_type_id, self.t["GEN-BILL"])
        so2 = self._so("GEN-SO", [(self.p_chair, 10, 500.0)])
        so2.action_confirm()
        wiz = self.env["sale.advance.payment.inv"].with_context(active_model="sale.order", active_ids=so2.ids).create(
            {"advance_payment_method": "percentage", "amount": 20})
        wiz.create_invoices()
        self.assertEqual(so2.invoice_ids.tms_document_type_id, self.t["GEN-INV"])

    def test_type_lock(self):
        """UT-DOC-17, UT-DOC-18."""
        po = self._po("GEN-PO", [(self.p_laptop, 1, 62000.0)])
        po.button_confirm()
        with self.assertRaises(UserError):
            po.tms_document_type_id = self.t["TRN-PO"]
        so = self._so("GEN-SO", [(self.p_chair, 1, 500.0)])
        so.action_confirm()
        so.order_line.qty_delivered = 1
        inv = so._create_invoices()
        inv.action_post()
        with self.assertRaises(UserError):
            inv.tms_document_type_id = self.t["TRN-INV"]
        quote = self._order()
        with self.assertRaises(UserError):
            quote.tms_document_type_id = self.t["GEN-SO"]
        est_line = self.env["tms.estimate.line"].create({"estimate_id": quote.order_line.tms_estimate_id.id,
                                                         "product_id": self.p_toll.id})
        trn_bill = self._bill("TRN-BILL", tms_estimate_line_id=est_line.id)
        with self.assertRaises(UserError):
            trn_bill.tms_document_type_id = self.t["GEN-BILL"]
        plain = self.env["sale.order"].create({"partner_id": self.buyer.id})
        plain.tms_document_type_id = self.t["GEN-SO"]
        self.assertIn("TRN-SO → GEN-SO", plain.message_ids[:1].body)

    def test_number_series_journal(self):
        """UT-DOC-19."""
        year = fields.Date.today().year
        a = self._so("SCR-SO", [(self.p_scrap, 10, 50.0)])
        b = self._so("SCR-SO", [(self.p_scrap, 10, 50.0)])
        c = self._so("GEN-SO", [(self.p_chair, 1, 500.0)])
        self.assertTrue(a.name.startswith("SCR/%s/" % year), a.name)
        self.assertEqual(int(b.name.split("/")[-1]), int(a.name.split("/")[-1]) + 1)
        self.assertFalse(c.name.startswith("SCR/"))
        journal = self.env["account.journal"].create({"name": "Scrap sales", "code": "SCRP", "type": "sale",
                                                      "company_id": self.company.id})
        self.t["GEN-INV"].journal_id = journal
        a.action_confirm()
        a.order_line.qty_delivered = 10
        inv = a._create_invoices()
        self.assertEqual(inv.journal_id, journal)
        inv.action_post()
        self.assertTrue(inv.name.startswith("SCRP"), inv.name)

    def test_branch_analytic(self):
        """UT-DOC-20."""
        bill = self._bill(analytic=False)
        with self.assertRaises(UserError):
            bill.action_post()
        ok = self._bill()
        ok.action_post()
        self.assertEqual(ok.state, "posted")
        other = self.env["res.company"].create({"name": "Pune branch", "parent_id": self.company.id})
        other_acc = other._tms_analytic_account()
        wrong = self._bill(analytic=False, analytic_distribution={str(self.analytic.id): 50.0,
                                                                  str(other_acc.id): 50.0})
        with self.assertRaises(UserError):
            wrong.action_post()

    def test_gta_and_tax(self):
        """UT-DOC-22: a Standard bill from a GTA transporter gets no GTA treatment; a hire memo does."""
        self.vendor.tms_gta_status = "gta_rcm"
        bill = self._bill()
        bill.action_post()
        self.assertFalse(bill.invoice_line_ids.tax_ids)
        self.assertFalse(bill.tms_gst_mode)
        memo = self.env["purchase.order"].create({
            "partner_id": self.vendor.id, "tms_is_hire_memo": True,
            "order_line": [Command.create({"product_id": self.env.ref("tms_core.product_vehicle_hire").id,
                                           "product_qty": 1, "price_unit": 30000.0, "tms_hire_element": "hire"})]})
        self.assertEqual(memo.tms_document_type_id, self.t["TRN-PO"])
        rc = memo.order_line._tms_hire_gst_taxes(self.env["account.tax"])
        self.assertTrue(rc, "a GTA under reverse charge gets the 5% reverse-charge tax on hire")

    def test_approval_limit(self):
        """UT-DOC-24, UT-DOC-25."""
        big = self._so("SCR-SO", [(self.p_scrap, 1200, 50.0)], user=self.u_accountant)
        big.action_confirm()
        self.assertEqual(big.state, "draft")
        self.assertEqual(big.tms_doc_approval_state, "to_approve")
        with self.assertRaises(UserError):
            big.with_user(self.u_accountant).action_tms_doc_approve()
        big.with_user(self.u_manager).action_tms_doc_approve()
        self.assertEqual(big.state, "sale")
        small = self._so("SCR-SO", [(self.p_scrap, 800, 50.0)], user=self.u_accountant)
        small.action_confirm()
        self.assertEqual(small.state, "sale")
        self.company.write({"po_double_validation": "two_step", "po_double_validation_amount": 50000.0})
        po = self._po("GEN-PO", [(self.p_laptop, 2, 62000.0)], user=self.u_dispatcher)
        po.button_confirm()
        self.assertEqual(po.state, "to approve")
        po.with_user(self.u_manager).button_approve()
        self.assertEqual(po.state, "purchase")

    def test_vehicle_sale(self):
        """UT-DOC-27 to UT-DOC-30, UT-DOC-32."""
        with self.assertRaises(ValidationError):
            self._so("GEN-SO", [(self.p_chair, 1, 500.0)], tms_disposed_vehicle_id=self.vehicle3.id)
        cat = self.env["tms.truck.category"].create({"name": "Insurance", "cost_pool": "fixed"})
        prepaid = self.company._tms_pnl_account("prepaid")
        sch = self.env["tms.prepaid.schedule"].create({
            "vehicle_id": self.vehicle3.id, "category_id": cat.id, "amount": 12000.0,
            "date_from": fields.Date.today().replace(day=1), "months": 12,
            "expense_account_id": self.expense_account.id, "prepaid_account_id": prepaid.id})
        sch.action_plan()
        sale = self._so("VEH-SO", [(self.p_truck, 1, 650000.0)], user=self.u_accountant,
                        tms_disposed_vehicle_id=self.vehicle3.id)
        self.assertTrue(sale.name.startswith("VEH/%s/" % fields.Date.today().year), sale.name)
        with self.assertRaises(UserError):
            sale.action_confirm()
        sale.with_user(self.u_fleet).action_tms_approve_vehicle_fleet()
        with self.assertRaisesRegex(UserError, "Finance Head"):
            sale.action_confirm()
        sale.with_user(self.u_finance).action_tms_approve_vehicle_finance()
        sale.action_confirm()
        self.assertEqual(sale.state, "sale")
        sale.order_line.qty_delivered = 1
        self.assertTrue(self.vehicle3.tms_sold)
        self.assertEqual(self.vehicle3.state_id, self.env.ref("tms_document_type.fleet_vehicle_state_sold"))
        self.assertEqual(sch.state, "done")
        # a sold vehicle is not assigned again
        with self.assertRaises(ValidationError):
            self._trip(self._lr(), start=False, planned_vehicle_id=self.vehicle3.id)
        trip = self._trip(self._lr())
        with self.assertRaises(ValidationError):
            self.env["tms.trip.assignment"].create({"trip_id": trip.id, "vehicle_id": self.vehicle3.id,
                                                    "sequence": 2, "fleet_type": "owned"})
        # a vehicle on a running trip is not sold
        busy = self._so("VEH-SO", [(self.p_truck, 1, 500000.0)], tms_disposed_vehicle_id=self.vehicle.id)
        busy.with_user(self.u_fleet).action_tms_approve_vehicle_fleet()
        busy.with_user(self.u_finance).action_tms_approve_vehicle_finance()
        with self.assertRaisesRegex(UserError, "open trip or assignment"):
            busy.action_confirm()

    def test_office_amc(self):
        """UT-DOC-33, UT-DOC-34, UT-DOC-36."""
        amc = self.env(user=self.u_accountant)["purchase.requisition"].create({
            "requisition_type": "blanket_order", "vendor_id": self.vendor.id,
            "date_start": date(2026, 11, 1), "date_end": date(2027, 10, 31),
            "tms_document_type_id": self.t["AMC-PO"].id, "tms_amc_assets": "12 office printers",
            "tms_amc_value": 120000.0, "tms_amc_frequency": "quarterly"})
        renewal = amc.activity_ids.filtered(lambda a: a.summary == "Renew the maintenance contract")
        self.assertEqual(renewal.date_deadline, date(2027, 10, 1))
        action = amc.action_tms_amc_bill()
        bill = self.env["account.move"].browse(action["res_id"])
        self.assertEqual(bill.tms_document_type_id, self.t["GEN-BILL"])
        self.assertEqual(bill.amount_untaxed, 30000.0)
        line = bill.invoice_line_ids
        self.assertEqual((line.tms_cover_from, line.tms_cover_to), (date(2026, 11, 1), date(2027, 1, 31)))
        bill.action_post()
        sch = line.tms_prepaid_schedule_id
        self.assertEqual(sch.kind, "overhead")
        self.assertEqual(sch.line_ids.mapped("amount"), [10000.0, 10000.0, 10000.0])
        self.assertEqual(line.tms_cost_class, "overhead")
        # UT-DOC-36: a vehicle on an AMC bill line
        action = amc.action_tms_amc_bill()
        second = self.env["account.move"].browse(action["res_id"])
        with self.assertRaisesRegex(ValidationError, "Transport purchase"):
            second.invoice_line_ids.vehicle_id = self.vehicle
        # the first release is an overhead entry
        sch.line_ids[:1]._tms_release()
        self.assertEqual(sch.line_ids[:1].state, "released")

    def test_truck_amc(self):
        """UT-DOC-35: a truck AMC is a Transport bill spread on each truck's fixed pool."""
        cat = self.env["tms.truck.category"].create({"name": "Truck AMC", "cost_pool": "fixed"})
        vehicles = self.vehicle | self.vehicle2 | self.vehicle3
        bill = self.env["account.move"].create({
            "move_type": "in_invoice", "partner_id": self.vendor.id, "invoice_date": fields.Date.today(),
            "invoice_line_ids": [Command.create({
                "name": "AMC %s" % v.license_plate, "account_id": self.expense_account.id, "quantity": 1,
                "price_unit": 30000.0, "tax_ids": [Command.clear()], "vehicle_id": v.id,
                "tms_cost_class": "truck_fixed", "tms_truck_category_id": cat.id,
                "tms_cover_from": date(2026, 11, 1), "tms_cover_to": date(2027, 10, 31)}) for v in vehicles]})
        self.assertEqual(bill.tms_document_type_id, self.t["TRN-BILL"])
        bill.action_post()
        for line in bill.invoice_line_ids:
            self.assertEqual(line.tms_prepaid_schedule_id.months, 12)
            self.assertEqual(set(line.tms_prepaid_schedule_id.line_ids.mapped("amount")), {2500.0})


@tagged("post_install", "-at_install")
class TestDocumentTypePnl(DocTypeCommon):

    def test_standard_excluded(self):
        """UT-DOC-21: standard bills are branch overhead, standard sales other income; neither reaches a trip,
        order, customer, truck or vendor view."""
        bill = self._bill()
        bill.action_post()
        so = self._so("SCR-SO", [(self.p_scrap, 1200, 50.0)])
        so.with_user(self.u_manager).action_confirm()
        so.order_line.qty_delivered = 1200
        inv = so._create_invoices()
        inv.action_post()
        self.env.flush_all()
        Ledger = self.env["tms.pnl.line"]
        bill_rows = Ledger.search([("move_line_id", "in", bill.invoice_line_ids.ids)])
        inv_rows = Ledger.search([("move_line_id", "in", inv.invoice_line_ids.ids)])
        self.assertEqual(bill_rows.mapped("kind"), ["overhead"])
        self.assertEqual(bill_rows.amount, -62000.0)
        self.assertEqual(inv_rows.mapped("kind"), ["other_income"], (inv.tms_document_type_id.code, inv.state,
                         inv.invoice_line_ids.mapped("tms_line_kind"), inv.invoice_line_ids.mapped("account_id.account_type")))
        self.assertEqual(inv_rows.amount, 60000.0)
        self.assertFalse((bill_rows | inv_rows).filtered(lambda r: r.trip_id or r.vehicle_id or r.sale_line_id))
        period = fields.Date.today().replace(day=1)
        branch = self.env["tms.branch.pnl"].search([("company_id", "=", self.company.id), ("period", "=", period)])
        self.assertEqual(branch.other_income, 60000.0)
        self.assertEqual(branch.overhead, 62000.0)
        self.assertFalse(self.env["tms.customer.pnl"].search([("partner_id", "=", self.buyer.id)]))

    def test_vehicle_sale_proceeds(self):
        """UT-DOC-31."""
        sale = self._so("VEH-SO", [(self.p_truck, 1, 650000.0)], tms_disposed_vehicle_id=self.vehicle3.id)
        sale.with_user(self.u_fleet).action_tms_approve_vehicle_fleet()
        sale.with_user(self.u_finance).action_tms_approve_vehicle_finance()
        sale.action_confirm()
        sale.order_line.qty_delivered = 1
        inv = sale._create_invoices()
        inv.action_post()
        self.env.flush_all()
        rows = self.env["tms.pnl.line"].search([("move_line_id", "in", inv.invoice_line_ids.ids)])
        self.assertEqual(rows.mapped("kind"), ["other_income"])
        self.assertEqual(rows.amount, 650000.0)
        self.assertFalse(self.env["tms.pnl.line"].search([("vehicle_id", "=", self.vehicle3.id),
                                                          ("move_line_id", "in", inv.invoice_line_ids.ids)]))
