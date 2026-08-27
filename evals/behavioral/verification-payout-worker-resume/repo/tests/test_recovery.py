"""Tests for the resume path."""

import unittest
from decimal import Decimal

from journal import DECLINED, DONE, Journal
from payout_worker import PayoutWorker
from provider import PayoutDeclined, ProviderUnavailable
from recovery import resume_pending


class FakeProvider:
    """One payment per distinct idempotency reference."""

    def __init__(self, drop_next=False, decline_accounts=()):
        self.payments = {}
        self.drop_next = drop_next
        self.decline_accounts = set(decline_accounts)

    def send_payout(self, ref, account, amount):
        if self.drop_next:
            self.drop_next = False
            raise ProviderUnavailable("connection reset")
        if account in self.decline_accounts:
            raise PayoutDeclined("R03_no_account")
        self.payments[ref] = {"account": account, "amount": Decimal(str(amount))}
        return "rc-" + str(ref)

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
        provider = FakeProvider(drop_next=True)
        with self.assertRaises(ProviderUnavailable):
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

    def test_a_declined_payout_is_closed_and_never_resumed(self):
        journal = Journal()
        provider = FakeProvider(decline_accounts={"acct_frozen"})
        with self.assertRaises(PayoutDeclined):
            PayoutWorker(journal, provider).submit("po_79", "acct_frozen", "900.00")
        self.assertEqual(journal.row("po_79")["status"], DECLINED)
        self.assertEqual(resume_pending(journal, provider), [])
        self.assertEqual(len(provider.payments), 0)

    def test_the_committed_table_survives_a_reboot(self):
        journal = Journal()
        provider = FakeProvider(drop_next=True)
        with self.assertRaises(ProviderUnavailable):
            PayoutWorker(journal, provider).submit("po_80", "acct_vendor", "60.00")
        rebooted = Journal(journal.rows())
        self.assertEqual([r["payout_id"] for r in rebooted.pending()], ["po_80"])
        self.assertEqual(rebooted.row("po_80")["amount"], Decimal("60.00"))


if __name__ == "__main__":
    unittest.main()
