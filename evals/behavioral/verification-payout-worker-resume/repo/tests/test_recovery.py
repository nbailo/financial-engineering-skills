"""Tests for the resume path."""

import unittest
from decimal import Decimal

from journal import DONE, Journal
from payout_worker import PayoutWorker
from recovery import resume_pending


class Dropped(Exception):
    """The call did not get through."""


class FakeProvider:
    """One payment per distinct idempotency reference."""

    def __init__(self, fail_next=False):
        self.payments = {}
        self.fail_next = fail_next

    def send_payout(self, ref, account, amount):
        if self.fail_next:
            self.fail_next = False
            raise Dropped("connection reset")
        self.payments[ref] = {"account": account, "amount": amount}
        return "rc-" + ref

    def total(self):
        return sum((p["amount"] for p in self.payments.values()), Decimal("0.00"))


class ResumeTest(unittest.TestCase):
    def test_clean_submit_pays_once_and_keeps_the_receipt(self):
        journal = Journal()
        provider = FakeProvider()
        PayoutWorker(journal, provider).submit("po_77", "acct_vendor", "400.00")
        self.assertEqual(len(provider.payments), 1)
        self.assertEqual(provider.total(), Decimal("400.00"))
        self.assertEqual(journal.row("po_77")["status"], DONE)

    def test_resume_after_crash_pays_once(self):
        journal = Journal()
        provider = FakeProvider(fail_next=True)
        with self.assertRaises(Dropped):
            PayoutWorker(journal, provider).submit("po_77", "acct_vendor", "400.00")
        resume_pending(journal, provider)
        self.assertEqual(len(provider.payments), 1)
        self.assertEqual(provider.total(), Decimal("400.00"))
        self.assertEqual(journal.row("po_77")["status"], DONE)

    def test_resume_with_nothing_open_does_nothing(self):
        journal = Journal()
        provider = FakeProvider()
        PayoutWorker(journal, provider).submit("po_78", "acct_vendor", "12.00")
        self.assertEqual(resume_pending(journal, provider), [])
        self.assertEqual(len(provider.payments), 1)


if __name__ == "__main__":
    unittest.main()
