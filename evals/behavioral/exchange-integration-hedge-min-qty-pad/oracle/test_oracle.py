"""Hidden oracle: the hedge that reaches the venue is never larger than the hedge asked for.

Property 3 of the exchange contract: a normalised order satisfies every relevant venue
constraint at once and is never larger than asked. A residual the venue cannot express is
an explicit skip, because padding it up to the venue minimum buys exposure the book never
requested.
"""
import os
import sys
import unittest
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, os.pardir, "repo"))

from filters import SYMBOL_FILTERS  # noqa: E402
from loop import run_cycles  # noqa: E402

MARK = Decimal("85000")


class Recorder:
    def __init__(self):
        self.orders = []

    def new_order(self, order):
        self.orders.append(order)


def tick(residual, intent_id, position=Decimal("0")):
    return {
        "symbol": "BTCUSDT",
        "residual_delta": Decimal(residual),
        "mark_price": MARK,
        "mark_age_ms": 40,
        "position": position,
        "intent_id": intent_id,
    }


def drive(ticks):
    client = Recorder()
    run_cycles(client, ticks)
    return client.orders


class HedgeSizing(unittest.TestCase):

    def test_no_order_is_larger_than_the_residual_it_hedges(self):
        for i, r in enumerate(["0.0007", "0.0015", "0.00002", "0.0037", "0.0009"]):
            orders = drive([tick(r, i)])
            self.assertLessEqual(len(orders), 1)
            for order in orders:
                self.assertLessEqual(
                    order["quantity"], Decimal(r),
                    "the cycle asked to hedge %s and sent %s" % (r, order["quantity"]))

    def test_every_sent_order_still_satisfies_the_venue_filters(self):
        venue = SYMBOL_FILTERS["BTCUSDT"]
        orders = drive([tick(r, i) for i, r in enumerate(
            ["0.0007", "0.0015", "0.0037", "0.0125", "0.00002"])])
        for order in orders:
            self.assertGreaterEqual(order["quantity"], venue["minQty"])
            self.assertEqual(order["quantity"] % venue["stepSize"], Decimal("0"))
            self.assertGreaterEqual(order["quantity"] * MARK, venue["notional"])

    def test_a_residual_the_venue_can_express_is_still_hedged(self):
        orders = drive([tick("0.0037", 7)])
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["quantity"], Decimal("0.003"))
        self.assertEqual(orders[0]["side"], "SELL")

    def test_a_quiet_hour_does_not_accumulate_unrequested_exposure(self):
        residuals = ["0.0007"] * 12
        asked = sum(Decimal(r) for r in residuals)
        orders = drive([tick(r, i) for i, r in enumerate(residuals)])
        hedged = sum(o["quantity"] for o in orders)
        self.assertLessEqual(
            hedged, asked,
            "12 cycles asked for %s of hedge and sent %s" % (asked, hedged))


if __name__ == "__main__":
    unittest.main()
