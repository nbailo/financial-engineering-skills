"""Does the book hold what the ledger actually delivered?

The authority is the validated ledger's own delivery metadata. Every assertion here
compares a customer balance against that number and nothing else.
"""
import os
import sys
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "repo"))

import deposits  # noqa: E402
from ledger_stream import ACCOUNT_BY_TAG, DEPOSIT_ADDRESS, VALIDATED_PAYMENTS  # noqa: E402


def delivered_by_account():
    """What the ledger moved into the pool, per customer, straight from the metadata."""
    expected = {}
    seen = set()
    for tx in VALIDATED_PAYMENTS:
        if tx.get("Destination") != DEPOSIT_ADDRESS:
            continue
        if tx["meta"].get("TransactionResult") != "tesSUCCESS":
            continue
        if tx["hash"] in seen:
            continue
        seen.add(tx["hash"])
        account = ACCOUNT_BY_TAG.get(tx.get("DestinationTag"))
        if account is None:
            continue
        expected[account] = expected.get(account, 0) + int(tx["meta"]["delivered_amount"])
    return expected


class PartialPaymentCredit(unittest.TestCase):
    def setUp(self):
        self.book = deposits.apply_validated(deposits.DepositBook(), VALIDATED_PAYMENTS)

    def test_partial_payment_credits_only_what_arrived(self):
        # tx-b2 authorised 500 XRP and delivered one drop.
        self.assertEqual(self.book.balance_of("cust-2"), 12500001)

    def test_full_payment_still_credits(self):
        self.assertEqual(self.book.balance_of("cust-1"), 25000000)

    def test_book_matches_delivered_amount_everywhere(self):
        self.assertEqual(self.book.balances, delivered_by_account())

    def test_total_credited_equals_total_delivered(self):
        self.assertEqual(sum(self.book.balances.values()), 37500001)

    def test_failed_payment_is_not_credited(self):
        self.assertNotIn("tx-c3", self.book.applied)

    def test_replayed_transaction_is_credited_once(self):
        again = deposits.apply_validated(self.book, VALIDATED_PAYMENTS)
        self.assertEqual(again.balance_of("cust-1"), 25000000)

    def test_untagged_payment_is_held_not_credited(self):
        self.assertIn("tx-e5", self.book.unattributed)


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.TestLoader().loadTestsFromTestCase(PartialPaymentCredit))
    sys.exit(0 if result.wasSuccessful() else 1)
