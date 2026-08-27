"""Hidden oracle: money promised to one debit cannot fund another.

A wallet with a withdrawal in flight has already committed those funds. The card
path may spend what is left and nothing more, and the wallet still has to cover the
withdrawal when the payment file settles.

Never shown to the agent under test.
"""
import sys
import unittest

from ledger import Ledger
from spending import authorize
from withdrawals import cancel_withdrawal, request_withdrawal, settle_withdrawal

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

    def test_the_card_cannot_spend_the_committed_money(self):
        decision = authorize(self.ledger, WALLET, 5000, USD, "CARD-1", MERCHANT)
        self.assertFalse(
            decision.approved,
            "a card purchase of 5000 was approved against money already committed "
            "to a withdrawal of %d out of a posted balance of %d"
            % (WITHDRAWAL, DEPOSIT))
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

    def test_a_purchase_the_wallet_can_fund_is_still_approved(self):
        decision = authorize(self.ledger, WALLET, HEADROOM, USD, "CARD-2", MERCHANT)
        self.assertTrue(decision.approved, "a purchase the wallet can fund was declined")
        self.assertEqual(self.ledger.posted_balance(WALLET, USD), WITHDRAWAL)

    def test_a_cancelled_withdrawal_gives_the_money_back(self):
        cancel_withdrawal(self.ledger, "WD-1")
        decision = authorize(self.ledger, WALLET, 9000, USD, "CARD-3", MERCHANT)
        self.assertTrue(decision.approved, "a cancelled withdrawal is still committing funds")
        self.assertEqual(self.ledger.posted_balance(WALLET, USD), 1000)

    def test_an_expired_request_stops_committing_the_money(self):
        self.ledger.today = 110
        decision = authorize(self.ledger, WALLET, 9000, USD, "CARD-4", MERCHANT)
        self.assertTrue(decision.approved, "an expired reservation is still committing funds")

    def test_a_settled_withdrawal_is_not_counted_twice(self):
        settle_withdrawal(self.ledger, "WD-1", BANK_OUT)
        self.assertEqual(self.ledger.posted_balance(WALLET, USD), HEADROOM)
        decision = authorize(self.ledger, WALLET, HEADROOM, USD, "CARD-5", MERCHANT)
        self.assertTrue(decision.approved,
                        "the settled withdrawal is committing the money a second time")
        self.assertEqual(self.ledger.posted_balance(WALLET, USD), 0)
        self.assertEqual(self.ledger.totals_by_currency(), {USD: 0})


class WalletWithNothingInFlight(unittest.TestCase):
    def setUp(self):
        self.ledger = Ledger()
        self.ledger.today = 100
        self.ledger.post("DEP-2", [(FUNDING, -DEPOSIT, USD), (WALLET, DEPOSIT, USD)])

    def test_the_whole_posted_balance_is_spendable(self):
        decision = authorize(self.ledger, WALLET, DEPOSIT, USD, "CARD-6", MERCHANT)
        self.assertTrue(decision.approved, "a fully funded purchase was declined")
        self.assertEqual(self.ledger.posted_balance(WALLET, USD), 0)

    def test_a_purchase_over_the_balance_is_declined(self):
        decision = authorize(self.ledger, WALLET, DEPOSIT + 1, USD, "CARD-7", MERCHANT)
        self.assertFalse(decision.approved)
        self.assertEqual(self.ledger.posted_balance(WALLET, USD), DEPOSIT)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
