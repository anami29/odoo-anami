from unittest.mock import patch

from odoo import fields
from odoo.tests import tagged
from odoo.tools.safe_eval import safe_eval

from .common import TmsCommon


@tagged("post_install", "-at_install")
class TestUiBilling(TmsCommon):
    """The hire bill shows its three-way match; freight invoices are listed under Trip money."""

    def _hire_bill(self, amount):
        lr = self._lr()
        trip = self._trip(lr, fleet="market")
        memo = trip.hire_memo_ids
        self._deliver_and_verify(trip)
        memo.action_create_invoice()
        bill = memo.invoice_ids
        bill.invoice_date = fields.Date.today()
        bill.invoice_line_ids.price_unit = amount
        return trip, memo, bill

    def test_match_state_on_held_and_passed_bill(self):
        trip, memo, bill = self._hire_bill(30200.0)
        self.assertEqual(bill.tms_match_ids.state, "blocked")
        self.assertEqual(bill.tms_match_state, "hold")
        self.assertEqual(bill.tms_match_line_ids, bill.tms_match_ids.line_ids)
        self.assertAlmostEqual(bill.tms_match_approved, 30000.0)
        self.assertAlmostEqual(bill.tms_match_recoveries, 0.0)
        self.assertAlmostEqual(bill.tms_match_net_payable, 30000.0)
        self.assertAlmostEqual(bill.tms_match_bill_amount, 30200.0)
        self.assertAlmostEqual(bill.tms_match_variance, 200.0)
        self.assertIn("0.5%", bill.tms_match_tolerance_note)
        self.assertIn("₹250.00", bill.tms_match_tolerance_note)
        self.assertIn(memo.name, bill.tms_hire_memo_refs)
        self.assertEqual(bill.tms_ui_trip_count, 1)
        self.assertEqual(bill.tms_ui_hire_memo_count, 1)
        self.assertEqual(bill.action_tms_view_trips()["res_id"], trip.id)
        self.assertEqual(bill.action_tms_view_hire_memos()["res_id"], memo.id)
        # billing the agreed hire passes the match
        bill.invoice_line_ids.price_unit = 30000.0
        bill.invalidate_recordset()
        self.assertEqual(bill.tms_match_ids.state, "passed")
        self.assertEqual(bill.tms_match_state, "passed")
        self.assertAlmostEqual(bill.tms_match_variance, 0.0)
        # a bill without a hire memo has no match status
        plain = self.init_invoice("in_invoice", partner=self.vendor, amounts=[500.0])
        self.assertFalse(plain.tms_match_state)

    def test_rerun_match_and_apply_recoveries(self):
        _trip, _memo, bill = self._hire_bill(30000.0)
        Match = type(self.env["tms.vendor.bill.match"])
        with patch.object(Match, "action_refresh", autospec=True) as refresh:
            bill.action_tms_rerun_match()
        refresh.assert_called_once()
        self.assertEqual(refresh.call_args.args[0], bill.tms_match_ids)
        with patch.object(Match, "action_apply_recoveries", autospec=True) as apply:
            bill.action_tms_apply_recoveries()
        apply.assert_called_once()
        self.assertEqual(apply.call_args.args[0], bill.tms_match_ids)
        # the real re-run recomputes a match whose state went stale
        bill.tms_match_ids.state = "blocked"
        bill.action_tms_rerun_match()
        self.assertEqual(bill.tms_match_ids.state, "passed")

    def test_bill_form_shows_match(self):
        arch = self.env["account.move"].get_views([(False, "form")])["views"]["form"]["arch"]
        for name in ("tms_match_state", "tms_match_line_ids", "action_tms_rerun_match",
                     "action_tms_apply_recoveries", "action_tms_view_trips", "tms_match_net_payable"):
            self.assertIn(name, arch)

    def test_freight_invoice_action(self):
        lr = self._lr(basis="paid")
        lr_moves = self.env["tms.invoice.lr.wizard"]._tms_create_invoices(lr)
        self.assertTrue(lr_moves)
        plain = self.init_invoice("out_invoice", partner=self.customer, amounts=[1000.0])
        action = self.env.ref("tms_core.action_tms_freight_invoices")
        found = self.env["account.move"].search(safe_eval(action.domain))
        self.assertTrue(lr_moves <= found)
        self.assertNotIn(plain, found)
        self.assertEqual(action.view_ids.filtered(lambda v: v.view_mode == "list").view_id,
                         self.env.ref("account.view_out_invoice_tree"))
        heading = self.env.ref("tms_core.menu_tms_money_invoices")
        self.assertEqual(heading.parent_id, self.env.ref("tms_core.menu_tms_finance"))
        self.assertFalse(heading.action)
        self.assertEqual(self.env.ref("tms_core.menu_tms_freight_invoices").parent_id, heading)
