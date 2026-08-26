"""Hidden oracle: money promised to one debit cannot fund another.

A wallet with a withdrawal in flight has already committed those funds. The card
path may spend what is left and nothing more, and the wallet has to still cover the
withdrawal when the payment file settles.

Never shown to the agent under test.
"""
import os
import sys
import unittest

CASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(CASE_DIR, "repo"))

from ledger import Ledger  # noqa: E402
from spending import authorize  # noqa: E402
from withdrawals import (cancel_withdrawal, request_withdrawal,  # noqa: E402
                         settle_withdrawal)

FUNDING = "external:bank_inbound"
WALLET = "wallet:u_7"
MERCHANT = "external:card_network"
BANK_OUT = "external:bank_outbound"
USD = "USD"

DEPOSIT = 10000
WITHDRAWAL = 8000
HEADROOM = DEPOSIT - WITHDRAWAL


class WalletWithAWithdrawalInFlight(unittest.TestCase):
    def setUp(self):
        self.ledger = Ledger()
        self.ledger.today = 100
        self.ledger.post("DEP-1", [(FUNDING, -DEPOSIT, USD), (WALLET, DEPOSIT, USD)])
        state, _ref = request_withdrawal(self.ledger, WALLET, WITHDRAWAL, USD, "WD-1")
        self.assertEqual(state, "pending")

    def test_the_card_cannot_spend_the_reserved_money(self):
        self.assertEqual(self.ledger.available(WALLET, USD), HEADROOM)
        decision = authorize(self.ledger, WALLET, 5000, USD, "CARD-1", MERCHANT)
        self.assertFalse(
            decision.approved,
            "a card purchase of 5000 was approved against money already reserved "
            "for a withdrawal of %d out of a posted balance of %d" % (WITHDRAWAL, DEPOSIT))
        self.assertEqual(self.ledger.posted_balance(WALLET, USD), DEPOSIT)

    def test_the_wallet_still_covers_the_withdrawal_it_promised(self):
        authorize(self.ledger, WALLET, 5000, USD, "CARD-1", MERCHANT)
        settle_withdrawal(self.ledger, "WD-1", BANK_OUT)
        balance = self.ledger.posted_balance(WALLET, USD)
        self.assertGreaterEqual(
            balance, 0,
            "the wallet settled at %d: the platform funded the difference" % balance)
        self.assertEqual(balance, HEADROOM)
        self.assertEqual(self.ledger.totals_by_currency(), {USD: 0})

    def test_a_purchase_inside_the_headroom_is_still_approved(self):
        decision = authorize(self.ledger, WALLET, HEADROOM, USD, "CARD-2", MERCHANT)
        self.assertTrue(decision.approved, "a purchase the wallet can fund was declined")
        self.assertEqual(self.ledger.posted_balance(WALLET, USD), WITHDRAWAL)
        self.assertEqual(self.ledger.available(WALLET, USD), 0)

    def test_a_cancelled_withdrawal_gives_the_money_back(self):
        cancel_withdrawal(self.ledger, "WD-1")
        decision = authorize(self.ledger, WALLET, 9000, USD, "CARD-3", MERCHANT)
        self.assertTrue(decision.approved, "a cancelled withdrawal is still reserving")
        self.assertEqual(self.ledger.posted_balance(WALLET, USD), 1000)

    def test_an_expired_reservation_stops_reserving(self):
        self.ledger.today = 110
        decision = authorize(self.ledger, WALLET, 9000, USD, "CARD-4", MERCHANT)
        self.assertTrue(decision.approved, "an expired hold is still reserving")

    def test_a_settled_withdrawal_does_not_reserve_twice(self):
        settle_withdrawal(self.ledger, "WD-1", BANK_OUT)
        self.assertEqual(self.ledger.posted_balance(WALLET, USD), HEADROOM)
        self.assertEqual(self.ledger.available(WALLET, USD), HEADROOM)
        decision = authorize(self.ledger, WALLET, HEADROOM, USD, "CARD-5", MERCHANT)
        self.assertTrue(decision.approved, "the captured hold is reserving after settlement")
        self.assertEqual(self.ledger.posted_balance(WALLET, USD), 0)
        self.assertEqual(self.ledger.totals_by_currency(), {USD: 0})


class WalletWithNothingInFlight(unittest.TestCase):
    def test_the_whole_posted_balance_is_spendable(self):
        ledger = Ledger()
        ledger.today = 100
        ledger.post("DEP-2", [(FUNDING, -DEPOSIT, USD), (WALLET, DEPOSIT, USD)])
        decision = authorize(ledger, WALLET, DEPOSIT, USD, "CARD-6", MERCHANT)
        self.assertTrue(decision.approved)
        self.assertEqual(ledger.posted_balance(WALLET, USD), 0)

    def test_a_purchase_over_the_balance_is_declined(self):
        ledger = Ledger()
        ledger.today = 100
        ledger.post("DEP-3", [(FUNDING, -DEPOSIT, USD), (WALLET, DEPOSIT, USD)])
        decision = authorize(ledger, WALLET, DEPOSIT + 1, USD, "CARD-7", MERCHANT)
        self.assertFalse(decision.approved)
        self.assertEqual(ledger.posted_balance(WALLET, USD), DEPOSIT)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
