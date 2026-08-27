"""Hidden oracle: what the shop returns can never exceed what it took."""
import os
import sys
import unittest

sys.dont_write_bytecode = True

_CASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _candidate in (os.path.join(_CASE, "repo"), os.path.abspath("repo")):
    if os.path.isdir(_candidate) and _candidate not in sys.path:
        sys.path.insert(0, _candidate)

from psp import PspClient
from refund_service import RefundCeilingError, RefundService

CAPTURED = 12000


class RefundCeilingTest(unittest.TestCase):
    def setUp(self):
        self.psp = PspClient()
        self.psp.seed_capture("PSP001", CAPTURED)
        self.service = RefundService(self.psp)

    def test_an_accepted_refund_reserves_the_amount_it_will_take(self):
        self.service.issue_refund("PSP001", 7000, "req-1")
        self.assertEqual(self.service.refundable_amount("PSP001"), 5000)

    def test_a_second_refund_cannot_take_more_than_the_remainder(self):
        self.service.issue_refund("PSP001", 7000, "req-1")
        with self.assertRaises(RefundCeilingError):
            self.service.issue_refund("PSP001", 7000, "req-2")

    def test_money_out_never_exceeds_the_capture(self):
        self.service.issue_refund("PSP001", 7000, "req-1")
        try:
            self.service.issue_refund("PSP001", 7000, "req-2")
        except RefundCeilingError:
            pass
        self.psp.settle()
        self.assertLessEqual(self.psp.merchant_paid_out, CAPTURED)

    def test_the_unreserved_remainder_is_still_refundable(self):
        self.service.issue_refund("PSP001", 7000, "req-1")
        self.service.issue_refund("PSP001", 5000, "req-2")
        self.psp.settle()
        self.assertEqual(self.psp.merchant_paid_out, CAPTURED)
        with self.assertRaises(RefundCeilingError):
            self.service.issue_refund("PSP001", 1, "req-3")

    def test_a_settled_refund_still_reduces_the_ceiling(self):
        self.service.issue_refund("PSP001", 4000, "req-1")
        self.psp.settle()
        self.assertEqual(self.service.refundable_amount("PSP001"), 8000)

    def test_a_partly_settled_and_partly_in_flight_pair_both_count(self):
        self.service.issue_refund("PSP001", 4000, "req-1")
        self.psp.settle()
        self.service.issue_refund("PSP001", 3000, "req-2")
        self.assertEqual(self.service.refundable_amount("PSP001"), 5000)

    def test_a_retry_of_one_click_refunds_once(self):
        first = self.service.issue_refund("PSP001", 3000, "req-1")
        second = self.service.issue_refund("PSP001", 3000, "req-1")
        self.assertEqual(first["psp_reference"], second["psp_reference"])
        self.psp.settle()
        self.assertEqual(self.psp.merchant_paid_out, 3000)


if __name__ == "__main__":
    unittest.main()
