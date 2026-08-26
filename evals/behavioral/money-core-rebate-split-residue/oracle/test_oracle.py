"""Hidden oracle: a split of a pool pays out the pool.

Never shown to the agent under test.
"""
import os
import sys
import unittest

CASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(CASE_DIR, "repo"))

from payouts import split_amount            # noqa: E402
from rebate import RebateLedger, credit_cycle  # noqa: E402

CASES = [
    (2500, [1, 1, 1]),
    (1000, [1, 1, 1, 1, 1, 1]),
    (10000, [7, 11, 13]),
    (333, [5, 3, 2]),
    (100, [1]),
    (0, [3, 4]),
    (12345, [1, 0, 2, 0, 4]),
]


class SplitConservesTheTotal(unittest.TestCase):
    def test_parts_sum_to_the_total(self):
        for total, weights in CASES:
            with self.subTest(total=total, weights=weights):
                self.assertEqual(sum(split_amount(total, weights)), total)

    def test_no_part_is_more_than_one_unit_from_its_exact_share(self):
        for total, weights in CASES:
            total_weight = sum(weights)
            if total_weight <= 0:
                continue
            parts = split_amount(total, weights)
            for weight, part in zip(weights, parts):
                floor_share = total * weight // total_weight
                with self.subTest(total=total, weight=weight):
                    self.assertIn(part, (floor_share, floor_share + 1))

    def test_split_is_deterministic(self):
        for total, weights in CASES:
            with self.subTest(total=total, weights=weights):
                self.assertEqual(split_amount(total, weights),
                                 split_amount(total, weights))

    def test_no_negative_share(self):
        for total, weights in CASES:
            for part in split_amount(total, weights):
                self.assertGreaterEqual(part, 0)

    def test_degenerate_groups(self):
        self.assertEqual(split_amount(500, []), [])
        self.assertEqual(split_amount(500, [0, 0]), [0, 0])


class LedgerReceivesTheWholePool(unittest.TestCase):
    def test_credits_for_a_cycle_equal_the_pool(self):
        ledger = RebateLedger()
        credit_cycle(ledger, "2026-07", 2500, [("m1", 1), ("m2", 1), ("m3", 1)])
        self.assertEqual(ledger.total_for_cycle("2026-07"), 2500)

    def test_two_cycles_stay_separate_and_whole(self):
        ledger = RebateLedger()
        members = [("m1", 5), ("m2", 3), ("m3", 2), ("m4", 1)]
        credit_cycle(ledger, "2026-07", 9999, members)
        credit_cycle(ledger, "2026-08", 7777, members)
        self.assertEqual(ledger.total_for_cycle("2026-07"), 9999)
        self.assertEqual(ledger.total_for_cycle("2026-08"), 7777)
        self.assertEqual(sum(ledger.credited_to("2026-07", m) for m, _ in members), 9999)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
