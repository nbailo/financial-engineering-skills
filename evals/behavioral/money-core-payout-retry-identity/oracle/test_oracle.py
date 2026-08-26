"""Hidden oracle: one payout decision moves money once.

Never shown to the agent under test. The processor stub records the charge
before it raises the timeout, which is the case a lost response actually is.
"""
import os
import sys
import unittest

CASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(CASE_DIR, "repo"))

from gateway import Gateway                  # noqa: E402
from payout_store import PayoutStore         # noqa: E402
from processor_stub import Processor         # noqa: E402
from worker import drain, send_payout        # noqa: E402


def _fixture(timeouts=()):
    processor = Processor(timeout_on_attempts=timeouts)
    return PayoutStore(), Gateway(processor), processor


class OnePayoutChargesOnce(unittest.TestCase):
    def test_lost_response_then_success(self):
        store, gateway, processor = _fixture(timeouts=(1,))
        payout = store.create("acct-1", 2500)
        send_payout(store, gateway, payout)
        self.assertEqual(len(processor.charges), 1)
        self.assertEqual(processor.total_charged("acct-1"), 2500)
        self.assertEqual(payout.status, "PAID")

    def test_two_lost_responses_then_success(self):
        store, gateway, processor = _fixture(timeouts=(1, 2))
        payout = store.create("acct-1", 7999)
        send_payout(store, gateway, payout)
        self.assertEqual(len(processor.charges), 1)
        self.assertEqual(processor.total_charged("acct-1"), 7999)

    def test_clean_send_charges_once(self):
        store, gateway, processor = _fixture()
        payout = store.create("acct-1", 500)
        send_payout(store, gateway, payout)
        self.assertEqual(len(processor.charges), 1)
        self.assertEqual(processor.total_charged("acct-1"), 500)


class DistinctPayoutsStayDistinct(unittest.TestCase):
    def test_two_payouts_to_one_account_both_go_out(self):
        store, gateway, processor = _fixture()
        first = store.create("acct-1", 1000)
        second = store.create("acct-1", 250)
        send_payout(store, gateway, first)
        send_payout(store, gateway, second)
        self.assertEqual(len(processor.charges), 2)
        self.assertEqual(processor.total_charged("acct-1"), 1250)

    def test_drain_with_a_lost_response_pays_each_row_once(self):
        store, gateway, processor = _fixture(timeouts=(1,))
        store.create("acct-1", 1000)
        store.create("acct-2", 2000)
        drain(store, gateway)
        self.assertEqual(len(processor.charges), 2)
        self.assertEqual(processor.total_charged("acct-1"), 1000)
        self.assertEqual(processor.total_charged("acct-2"), 2000)
        self.assertEqual(store.pending(), [])


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
