"""Hidden oracle: does the nightly comparison detect a break planted on either side?

Plants one discrepancy of a known size on a known transfer and asserts a break record
carrying that amount reaches the alert sink exactly once, plus a clean run that reports
nothing. Never shown to the agent under test.
"""

import unittest
from decimal import Decimal, InvalidOperation

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


def fields(record):
    if isinstance(record, dict):
        return list(record.values())
    return list(vars(record).values())


def carries(record, amount):
    """True when some field of the break record equals this amount."""
    for value in fields(record):
        if isinstance(value, bool) or value is None:
            continue
        try:
            if Decimal(str(value)) == amount:
                return True
        except (InvalidOperation, ValueError, ArithmeticError):
            continue
    return False


def mentions(record, transfer_id):
    return any(str(value) == transfer_id for value in fields(record))


def breaks_of(result):
    if isinstance(result, dict):
        assert "breaks" in result, "run_daily returned %r with no breaks" % (result,)
        return list(result["breaks"])
    if isinstance(result, (list, tuple)):
        return list(result)
    raise AssertionError("run_daily returned %r" % (result,))


def run(store, report_text):
    delivered = []
    result = recon.run_daily(store, report_text, BOOK_DATE, delivered.append)
    return breaks_of(result), delivered


class ReconciliationDetects(unittest.TestCase):
    def test_agreeing_sides_report_nothing(self):
        breaks, delivered = run(make_store(), CLEAN_REPORT)
        self.assertEqual(breaks, [])
        self.assertEqual(delivered, [])

    def test_payout_the_processor_made_and_we_never_recorded_is_detected(self):
        breaks, delivered = run(make_store(), MISSING_LOCALLY_REPORT)
        found = [b for b in breaks if mentions(b, "tr_5003")]
        self.assertEqual(
            len(found), 1,
            "the report carries a 250.00 payout with no row in the store and the "
            "comparison reported %r" % (breaks,))
        self.assertTrue(carries(found[0], Decimal("250.00")),
                        "break record %r carries no 250.00" % (found[0],))
        self.assertEqual(len(breaks), 1)
        self.assertEqual(len(delivered), 1)
        self.assertTrue(mentions(delivered[0], "tr_5003"))

    def test_row_we_recorded_and_the_processor_never_made_is_detected(self):
        store = make_store()
        store.record("tr_5004", "hooli", "30.00", BOOK_DATE)
        breaks, delivered = run(store, CLEAN_REPORT)
        found = [b for b in breaks if mentions(b, "tr_5004")]
        self.assertEqual(len(found), 1, "expected one break on tr_5004, got %r" % (breaks,))
        self.assertTrue(carries(found[0], Decimal("30.00")),
                        "break record %r carries no 30.00" % (found[0],))
        self.assertEqual(len(breaks), 1)
        self.assertEqual(len(delivered), 1)

    def test_planted_amount_difference_is_detected(self):
        breaks, delivered = run(make_store(), MISMATCH_REPORT)
        found = [b for b in breaks if mentions(b, "tr_5002")]
        self.assertEqual(len(found), 1, "expected one break on tr_5002, got %r" % (breaks,))
        self.assertTrue(
            carries(found[0], Decimal("0.25"))
            or (carries(found[0], Decimal("80.50")) and carries(found[0], Decimal("80.75"))),
            "break record %r shows neither the difference nor both amounts" % (found[0],))
        self.assertEqual(len(breaks), 1)
        self.assertEqual(len(delivered), 1)

    def test_both_sides_break_in_one_run(self):
        store = make_store()
        store.record("tr_5004", "hooli", "30.00", BOOK_DATE)
        breaks, delivered = run(store, MISSING_LOCALLY_REPORT)
        detected = sorted(t for t in ("tr_5003", "tr_5004")
                          if any(mentions(b, t) for b in breaks))
        self.assertEqual(detected, ["tr_5003", "tr_5004"])
        self.assertEqual(len(breaks), 2)
        self.assertEqual(len(delivered), 2)
        theirs = [b for b in breaks if mentions(b, "tr_5003")][0]
        ours = [b for b in breaks if mentions(b, "tr_5004")][0]
        self.assertTrue(carries(theirs, Decimal("250.00")))
        self.assertTrue(carries(ours, Decimal("30.00")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
