"""Does the book hold every confirmed deposit the chain shows, and only those?

The chain is the authority. Expected balances are recomputed here from the log set rather
than from anything the indexer records. The second scenario runs the same indexer against
a denser chain, where the watermark must not move over blocks whose deposits are missing.
"""
import sys

sys.dont_write_bytecode = True

import unittest  # noqa: E402

import indexer  # noqa: E402
import node as node_module  # noqa: E402
from chain import (ACCOUNT_BY_DEPOSIT_ADDRESS, HEAD_BLOCK, INTERNAL_ADDRESSES,  # noqa: E402
                   LOGS, TRACKED_TOKEN)

START_CURSOR = 200
SAFE_HEAD = HEAD_BLOCK - indexer.CONFIRMATIONS


def key_of(log):
    return (log["block_hash"], log["tx_hash"], log["log_index"])


def owed(log):
    """Does this log oblige us to move a customer balance?"""
    if log["token"] != TRACKED_TOKEN:
        return False
    if log["from"] in INTERNAL_ADDRESSES:
        return False
    return log["to"] in ACCOUNT_BY_DEPOSIT_ADDRESS


def confirmed_deposits():
    """Every log that owes a customer money once it is deep enough to credit."""
    return [log for log in LOGS if log["block_number"] <= SAFE_HEAD and owed(log)]


def expected_balances():
    balances = {}
    for log in confirmed_deposits():
        account = ACCOUNT_BY_DEPOSIT_ADDRESS[log["to"]]
        balances[account] = balances.get(account, 0) + log["amount"]
    return balances


def dense_logs():
    """A busier stretch: one block holds more logs than a response can carry."""
    out = [{"block_number": 203, "block_hash": "0xblk203", "tx_hash": "0xd00",
            "log_index": 0, "token": TRACKED_TOKEN, "from": "0xpayer1",
            "to": "0xdep1", "amount": 1000000}]
    for index in range(5):
        out.append({"block_number": 205, "block_hash": "0xblk205",
                    "tx_hash": "0xd1%d" % index, "log_index": index,
                    "token": TRACKED_TOKEN, "from": "0xpayer2",
                    "to": "0xdep2", "amount": 2000000})
    out.append({"block_number": 207, "block_hash": "0xblk207", "tx_hash": "0xd20",
                "log_index": 0, "token": TRACKED_TOKEN, "from": "0xpayer3",
                "to": "0xdep3", "amount": 3000000})
    return out


class DenseNode:
    """The same provider, answering over the denser chain."""

    def __init__(self):
        self.logs = dense_logs()
        self.calls = 0

    def block_number(self):
        return HEAD_BLOCK

    def get_logs(self, from_block, to_block):
        self.calls += 1
        if self.calls > 400:
            raise RuntimeError("the indexer queried without bound")
        hits = [log for log in self.logs
                if from_block <= log["block_number"] <= to_block]
        hits.sort(key=lambda log: (log["block_number"], log["log_index"]))
        return hits[:node_module.MAX_LOGS_PER_RESPONSE]


class ConfirmedDepositsAreCredited(unittest.TestCase):
    def setUp(self):
        self.store = indexer.sync(node_module.Node(), indexer.DepositStore(START_CURSOR))

    def test_every_confirmed_deposit_is_credited(self):
        missing = [log["tx_hash"] for log in confirmed_deposits()
                   if key_of(log) not in self.store.applied]
        self.assertEqual(missing, [])

    def test_balances_match_the_chain(self):
        self.assertEqual(self.store.balances, expected_balances())

    def test_total_credited_matches_the_chain(self):
        self.assertEqual(sum(self.store.balances.values()), 110750000)

    def test_watermark_covers_only_credited_blocks(self):
        skipped = [log["tx_hash"] for log in LOGS
                   if owed(log) and log["block_number"] <= self.store.cursor
                   and key_of(log) not in self.store.applied]
        self.assertEqual(skipped, [])

    def test_deposits_above_the_confirmed_point_wait(self):
        self.assertLessEqual(self.store.cursor, SAFE_HEAD)
        for log in LOGS:
            if log["block_number"] > SAFE_HEAD:
                self.assertNotIn(key_of(log), self.store.applied)

    def test_internal_transfer_is_not_income(self):
        self.assertNotIn(("0xblk212", "0xtx04", 0), self.store.applied)

    def test_untracked_token_is_not_credited(self):
        self.assertNotIn(("0xblk226", "0xtx07", 0), self.store.applied)

    def test_reprocessing_a_range_credits_nothing_twice(self):
        before = dict(self.store.balances)
        self.store.cursor = START_CURSOR
        indexer.sync(node_module.Node(), self.store)
        self.assertEqual(self.store.balances, before)


class WatermarkNeverOutrunsCredit(unittest.TestCase):
    """On a chain the provider cannot serve in full, no deposit is left behind it."""

    def test_watermark_covers_only_credited_blocks(self):
        node = DenseNode()
        store = indexer.DepositStore(START_CURSOR)
        try:
            indexer.sync(node, store)
        except Exception:
            pass
        skipped = [log["tx_hash"] for log in node.logs
                   if log["block_number"] <= store.cursor
                   and key_of(log) not in store.applied]
        self.assertEqual(skipped, [])


if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = unittest.TestSuite([
        loader.loadTestsFromTestCase(ConfirmedDepositsAreCredited),
        loader.loadTestsFromTestCase(WatermarkNeverOutrunsCredit),
    ])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
