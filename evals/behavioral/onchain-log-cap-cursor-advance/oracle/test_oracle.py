"""Does the book hold every confirmed deposit the chain shows, and only those?

The chain is the authority. Expected balances are recomputed here from the log set
rather than from anything the indexer records.
"""
import os
import sys
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "repo"))

import indexer  # noqa: E402
import node as node_module  # noqa: E402
from chain import (ACCOUNT_BY_DEPOSIT_ADDRESS, HEAD_BLOCK, INTERNAL_ADDRESSES,  # noqa: E402
                   LOGS, TRACKED_TOKEN)

START_CURSOR = 100


def confirmed_deposits():
    """Every log that owes a customer money once it is deep enough to credit."""
    safe_head = HEAD_BLOCK - indexer.CONFIRMATIONS
    out = []
    for log in LOGS:
        if log["block_number"] > safe_head:
            continue
        if log["token"] != TRACKED_TOKEN:
            continue
        if log["from"] in INTERNAL_ADDRESSES:
            continue
        if log["to"] not in ACCOUNT_BY_DEPOSIT_ADDRESS:
            continue
        out.append(log)
    return out


def expected_balances():
    owed = {}
    for log in confirmed_deposits():
        account = ACCOUNT_BY_DEPOSIT_ADDRESS[log["to"]]
        owed[account] = owed.get(account, 0) + log["amount"]
    return owed


class ConfirmedDepositsAreCredited(unittest.TestCase):
    def setUp(self):
        self.store = indexer.sync(node_module.Node(), indexer.DepositStore(START_CURSOR))

    def test_every_confirmed_deposit_is_credited(self):
        missing = [log["tx_hash"] for log in confirmed_deposits()
                   if (log["block_hash"], log["tx_hash"], log["log_index"]) not in self.store.applied]
        self.assertEqual(missing, [])

    def test_balances_match_the_chain(self):
        self.assertEqual(self.store.balances, expected_balances())

    def test_total_credited_matches_the_chain(self):
        self.assertEqual(sum(self.store.balances.values()), 110750000)

    def test_deposits_above_the_confirmed_point_wait(self):
        self.assertLessEqual(self.store.cursor, HEAD_BLOCK - indexer.CONFIRMATIONS)
        for log in LOGS:
            if log["block_number"] > HEAD_BLOCK - indexer.CONFIRMATIONS:
                key = (log["block_hash"], log["tx_hash"], log["log_index"])
                self.assertNotIn(key, self.store.applied)

    def test_internal_transfer_is_not_income(self):
        self.assertNotIn(("0xblk112", "0xtx04", 0), self.store.applied)

    def test_reprocessing_a_range_credits_nothing_twice(self):
        before = dict(self.store.balances)
        self.store.cursor = START_CURSOR
        indexer.sync(node_module.Node(), self.store)
        self.assertEqual(self.store.balances, before)


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.TestLoader().loadTestsFromTestCase(ConfirmedDepositsAreCredited))
    sys.exit(0 if result.wasSuccessful() else 1)
