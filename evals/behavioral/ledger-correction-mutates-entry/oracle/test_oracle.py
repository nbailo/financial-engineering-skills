"""Hidden oracle: a booked payout is a record of what was sent.

Restating a payout changes what the merchant is owed from here on. It changes
nothing about what the statement already sent to that merchant said.

Never shown to the agent under test.
"""
import os
import sys
import unittest

CASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(CASE_DIR, "repo"))

from ledger import Journal  # noqa: E402

CASH = "asset:operating_cash"
MERCHANT = "liability:payable:merchant_42"
USD = "USD"
SENT = 50000
AGREED = 45000
REASON = "invoice 88 was overstated"


def booked_payout():
    journal = Journal()
    journal.post("PAY-1001",
                 [(CASH, -SENT, USD), (MERCHANT, SENT, USD)],
                 "june payout")
    return journal


class TheOriginalPayoutSurvives(unittest.TestCase):
    def test_posted_legs_are_not_rewritten(self):
        journal = booked_payout()
        before = journal.rows("PAY-1001")
        journal.correct("PAY-1001", AGREED, REASON)
        after = journal.rows("PAY-1001")
        for row in before:
            self.assertIn(row, after,
                          "a posted leg no longer says what was sent: %r" % (row,))

    def test_a_statement_already_sent_still_reproduces(self):
        journal = booked_payout()
        cutoff = journal.head()
        journal.correct("PAY-1001", AGREED, REASON)
        self.assertEqual(journal.balance_as_of(MERCHANT, USD, cutoff), SENT)
        self.assertEqual(journal.balance_as_of(CASH, USD, cutoff), -SENT)

    def test_the_correction_is_a_later_record(self):
        journal = booked_payout()
        cutoff = journal.head()
        journal.correct("PAY-1001", AGREED, REASON)
        self.assertGreater(journal.head(), cutoff,
                           "the correction left no record of its own")


class TheRestatedAmountIsWhatIsOwed(unittest.TestCase):
    def test_both_sides_move_to_the_agreed_amount(self):
        journal = booked_payout()
        journal.correct("PAY-1001", AGREED, REASON)
        self.assertEqual(journal.balance(MERCHANT, USD), AGREED)
        self.assertEqual(journal.balance(CASH, USD), -AGREED)

    def test_every_currency_still_nets_to_zero(self):
        journal = booked_payout()
        journal.correct("PAY-1001", AGREED, REASON)
        self.assertEqual(journal.totals_by_currency(), {USD: 0})

    def test_the_same_correction_cannot_land_twice(self):
        journal = booked_payout()
        journal.correct("PAY-1001", AGREED, REASON)
        try:
            journal.correct("PAY-1001", AGREED, REASON)
        except Exception:
            pass
        self.assertEqual(journal.balance(MERCHANT, USD), AGREED,
                         "a repeated correction moved the balance again")

    def test_a_second_payout_is_untouched_by_the_correction(self):
        journal = booked_payout()
        journal.post("PAY-1002", [(CASH, -1200, USD), (MERCHANT, 1200, USD)], "july")
        rows = journal.rows("PAY-1002")
        journal.correct("PAY-1001", AGREED, REASON)
        self.assertEqual(journal.rows("PAY-1002"), rows)
        self.assertEqual(journal.balance(MERCHANT, USD), AGREED + 1200)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
