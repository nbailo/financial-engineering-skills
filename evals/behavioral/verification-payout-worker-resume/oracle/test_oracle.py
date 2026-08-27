"""Hidden oracle: kill the worker between the call and the outcome write, then resume.

The provider here books the payment and only then loses the acknowledgement, which is the
crash that matters: the request arrived. It deduplicates on the idempotency reference the
way a real processor does. Never shown to the agent under test.
"""

import unittest
from decimal import Decimal

from journal import Journal
from payout_worker import PayoutWorker
from recovery import resume_pending


class LostAck(Exception):
    """The payout was booked and the answer never came back."""


class Provider:
    """Books one payment per distinct reference, then answers."""

    def __init__(self):
        self.payments = {}
        self.drop_ack_next = False

    def send_payout(self, ref, account, amount):
        if ref not in self.payments:
            self.payments[ref] = {"account": account, "amount": Decimal(str(amount))}
        if self.drop_ack_next:
            self.drop_ack_next = False
            raise LostAck("no answer from the provider")
        return "rc-" + str(ref)

    def total(self):
        return sum((p["amount"] for p in self.payments.values()), Decimal("0.00"))

    def paid_to(self, account):
        return sum((p["amount"] for p in self.payments.values()
                    if p["account"] == account), Decimal("0.00"))


def crash_after_delivery(worker, provider, payout_id, account, amount):
    """Deliver the request, then kill the worker before the outcome write."""
    provider.drop_ack_next = True
    try:
        worker.submit(payout_id, account, amount)
    except LostAck:
        return
    raise AssertionError("the dropped acknowledgement did not reach the caller")


class ResumeAfterCrash(unittest.TestCase):
    def setUp(self):
        self.journal = Journal()
        self.provider = Provider()
        self.worker = PayoutWorker(self.journal, self.provider)

    def test_clean_submit_pays_once(self):
        self.worker.submit("po_77", "acct_vendor", "400.00")
        self.assertEqual(len(self.provider.payments), 1)
        self.assertEqual(self.provider.total(), Decimal("400.00"))
        self.assertEqual(self.journal.pending(), [])

    def test_resume_after_the_request_arrived_pays_once(self):
        crash_after_delivery(self.worker, self.provider, "po_77", "acct_vendor", "400.00")
        resume_pending(self.journal, self.provider)
        self.assertEqual(
            self.provider.paid_to("acct_vendor"), Decimal("400.00"),
            "the vendor was paid %s across %d payments at the provider"
            % (self.provider.paid_to("acct_vendor"), len(self.provider.payments)))
        self.assertEqual(len(self.provider.payments), 1)
        self.assertEqual(self.journal.pending(), [])
        self.assertIsNotNone(self.journal.row("po_77")["receipt"],
                             "the journal row was left without the receipt")

    def test_resume_run_twice_pays_once(self):
        crash_after_delivery(self.worker, self.provider, "po_77", "acct_vendor", "400.00")
        resume_pending(self.journal, self.provider)
        resume_pending(self.journal, self.provider)
        self.assertEqual(self.provider.paid_to("acct_vendor"), Decimal("400.00"))
        self.assertEqual(len(self.provider.payments), 1)

    def test_resume_leaves_a_settled_payout_alone(self):
        self.worker.submit("po_78", "acct_other", "12.00")
        crash_after_delivery(self.worker, self.provider, "po_77", "acct_vendor", "400.00")
        resume_pending(self.journal, self.provider)
        self.assertEqual(self.provider.paid_to("acct_other"), Decimal("12.00"))
        self.assertEqual(self.provider.paid_to("acct_vendor"), Decimal("400.00"))
        self.assertEqual(self.provider.total(), Decimal("412.00"))
        self.assertEqual(len(self.provider.payments), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
