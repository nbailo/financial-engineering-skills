"""Does the book hold what the ledger actually moved into the pool account?

The authority is the validated ledger's own delivery metadata. Every assertion compares a
customer balance against that number and nothing else.
"""
import sys

sys.dont_write_bytecode = True

import unittest  # noqa: E402

import deposits  # noqa: E402
from ledger_stream import ACCOUNT_BY_TAG, DEPOSIT_ADDRESS, VALIDATED_PAYMENTS  # noqa: E402


def delivered_by_account():
    """What the ledger recorded as delivered, per customer, straight from the metadata."""
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


class CreditMatchesDelivery(unittest.TestCase):
    def setUp(self):
        self.book = deposits.apply_validated(deposits.DepositBook(), VALIDATED_PAYMENTS)

    def test_partial_payment_credits_only_what_arrived(self):
        # tx-02 authorised 750 XRP and moved two drops.
        self.assertEqual(self.book.balance_of("cust-2"), 12500002)

    def test_partial_payment_with_other_flags_set_credits_only_what_arrived(self):
        # tx-07 carries the partial flag alongside another flag and moved five drops.
        self.assertEqual(self.book.balance_of("cust-1"), 30000005)

    def test_book_matches_delivered_amount_everywhere(self):
        self.assertEqual(self.book.balances, delivered_by_account())

    def test_total_credited_equals_total_delivered(self):
        self.assertEqual(sum(self.book.balances.values()), 42500007)

    def test_failed_payment_is_not_credited(self):
        self.assertNotIn("tx-03", self.book.applied)

    def test_payment_to_another_pool_is_not_credited(self):
        self.assertNotIn("tx-04", self.book.applied)

    def test_untagged_payment_is_held_not_credited(self):
        self.assertIn("tx-05", self.book.unattributed)
        self.assertNotIn("tx-05", self.book.applied)

    def test_replayed_stream_credits_nothing_twice(self):
        before = dict(self.book.balances)
        deposits.apply_validated(self.book, VALIDATED_PAYMENTS)
        self.assertEqual(self.book.balances, before)


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.TestLoader().loadTestsFromTestCase(CreditMatchesDelivery))
    sys.exit(0 if result.wasSuccessful() else 1)
