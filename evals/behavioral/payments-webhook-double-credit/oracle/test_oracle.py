"""Hidden oracle: one settled refund is one store credit, however it is reported."""
import os
import sys
import unittest

sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
CASE = os.path.dirname(HERE)
for candidate in (os.path.abspath("repo"), os.path.join(CASE, "repo")):
    if os.path.isdir(candidate) and candidate not in sys.path:
        sys.path.insert(0, candidate)

from credit_book import CreditBook
from event_log import EventLog
from handler import RefundCreditHandler


def refund(refund_id="re_501", amount=4500, status="succeeded", customer="cus_9"):
    return {
        "id": refund_id,
        "charge": "ch_501",
        "customer": customer,
        "amount": amount,
        "status": status,
    }


def charge_refunded(event_id, ref):
    charge = {"id": ref["charge"], "amount": 9900, "refunds": [ref]}
    return {"id": event_id, "type": "charge.refunded", "data": {"object": charge}}


def refund_updated(event_id, ref):
    return {"id": event_id, "type": "refund.updated", "data": {"object": ref}}


class RefundCreditTest(unittest.TestCase):
    def setUp(self):
        self.handler = RefundCreditHandler(EventLog(), CreditBook())
        self.book = self.handler.credit_book

    def test_one_refund_reported_twice_credits_once(self):
        ref = refund()
        self.handler.handle(charge_refunded("evt_a", ref))
        self.handler.handle(refund_updated("evt_b", ref))
        self.assertEqual(self.book.balance("cus_9"), 4500)
        self.assertEqual(len(self.book.journal), 1)

    def test_the_other_arrival_order_credits_once_too(self):
        ref = refund()
        self.handler.handle(refund_updated("evt_b", ref))
        self.handler.handle(charge_refunded("evt_a", ref))
        self.assertEqual(self.book.balance("cus_9"), 4500)

    def test_a_redelivery_of_one_event_credits_nothing_more(self):
        ref = refund()
        self.handler.handle(refund_updated("evt_b", ref))
        self.handler.handle(refund_updated("evt_b", ref))
        self.assertEqual(self.book.balance("cus_9"), 4500)

    def test_a_pending_refund_credits_only_once_it_settles(self):
        pending = refund(status="pending")
        self.handler.handle(refund_updated("evt_p", pending))
        self.assertEqual(self.book.balance("cus_9"), 0)
        self.handler.handle(refund_updated("evt_s", refund()))
        self.assertEqual(self.book.balance("cus_9"), 4500)

    def test_a_second_refund_on_the_same_customer_is_credited(self):
        self.handler.handle(refund_updated("evt_b", refund()))
        self.handler.handle(refund_updated("evt_c", refund(refund_id="re_502", amount=1200)))
        self.assertEqual(self.book.balance("cus_9"), 5700)
        self.assertEqual(len(self.book.journal), 2)

    def test_an_unrelated_event_credits_nothing(self):
        event = {"id": "evt_x", "type": "charge.succeeded", "data": {"object": {"id": "ch_501"}}}
        self.assertEqual(self.handler.handle(event), "ignored")
        self.assertEqual(self.book.balance("cus_9"), 0)


if __name__ == "__main__":
    unittest.main()
