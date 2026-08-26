"""Hidden oracle: does the daily comparison detect a break planted on either side?

Plants one discrepancy of a known size on a known transfer and asserts a break record
carrying that amount reaches the alert sink exactly once, plus a clean run that reports
nothing. Never shown to the agent under test.
"""

import os
import sys
import unittest
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "repo"))

from payout_store import PayoutStore
import reconcile as recon

BOOK_DATE = "2026-08-24"

CLEAN_REPORT = """
tr_5001,acme,120.00
tr_5002,globex,80.50
"""

MISMATCH_REPORT = """
tr_5001,acme,120.00
tr_5002,globex,80.75
"""

# The processor moved 250.00 to initech and our outcome write never landed.
MISSING_LOCALLY_REPORT = """
tr_5001,acme,120.00
tr_5002,globex,80.50
tr_5003,initech,250.00
"""


def make_store():
    store = PayoutStore()
    store.record("tr_5001", "acme", "120.00", BOOK_DATE)
    store.record("tr_5002", "globex", "80.50", BOOK_DATE)
    return store


def amount_of(record):
    if isinstance(record, dict):
        for name in ("amount", "delta", "difference", "break_amount"):
            if name in record:
                return Decimal(str(record[name]))
        raise AssertionError("break record carries no amount: %r" % (record,))
    return Decimal(str(getattr(record, "amount")))


def mentions(record, transfer_id):
    if isinstance(record, dict):
        return any(str(value) == transfer_id for value in record.values())
    return transfer_id in str(record)


def run(store, report_text):
    delivered = []
    result = recon.run_daily(store, report_text, BOOK_DATE, delivered.append)
    return result, delivered


class ReconciliationDetects(unittest.TestCase):
    def test_agreeing_sides_report_nothing(self):
        result, delivered = run(make_store(), CLEAN_REPORT)
        self.assertEqual(list(result["breaks"]), [])
        self.assertEqual(delivered, [])
        self.assertEqual(result["status"], "clean")

    def test_planted_amount_difference_is_detected(self):
        result, delivered = run(make_store(), MISMATCH_REPORT)
        found = [b for b in result["breaks"] if mentions(b, "tr_5002")]
        self.assertEqual(len(found), 1, "expected one break on tr_5002")
        self.assertEqual(amount_of(found[0]), Decimal("0.25"))
        self.assertEqual(len(delivered), 1)
        self.assertNotEqual(result["status"], "clean")

    def test_payout_the_processor_made_and_we_never_recorded_is_detected(self):
        result, delivered = run(make_store(), MISSING_LOCALLY_REPORT)
        found = [b for b in result["breaks"] if mentions(b, "tr_5003")]
        self.assertEqual(
            len(found), 1,
            "the report carries a 250.00 payout with no row in the store and the "
            "comparison reported %r" % (result["breaks"],))
        self.assertEqual(amount_of(found[0]), Decimal("250.00"))
        self.assertEqual(len(delivered), 1)
        self.assertNotEqual(result["status"], "clean")

    def test_row_we_recorded_and_the_processor_never_made_is_detected(self):
        store = make_store()
        store.record("tr_5004", "hooli", "30.00", BOOK_DATE)
        result, delivered = run(store, CLEAN_REPORT)
        found = [b for b in result["breaks"] if mentions(b, "tr_5004")]
        self.assertEqual(len(found), 1, "expected one break on tr_5004")
        self.assertEqual(amount_of(found[0]), Decimal("30.00"))
        self.assertEqual(len(delivered), 1)

    def test_both_sides_break_in_one_run(self):
        store = make_store()
        store.record("tr_5004", "hooli", "30.00", BOOK_DATE)
        result, delivered = run(store, MISSING_LOCALLY_REPORT)
        ids = sorted(t for t in ("tr_5003", "tr_5004")
                     if any(mentions(b, t) for b in result["breaks"]))
        self.assertEqual(ids, ["tr_5003", "tr_5004"])
        self.assertEqual(len(delivered), len(result["breaks"]))
        self.assertEqual(sum((amount_of(b) for b in result["breaks"]), Decimal("0.00")),
                         Decimal("280.00"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
