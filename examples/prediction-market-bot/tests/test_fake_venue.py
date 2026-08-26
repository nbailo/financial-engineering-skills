"""The stub venue itself, and the frozen fixture that pins it.

If the fixture stops matching what the venue produces, every other test in this directory is
asserting against a log nothing generates. Regenerate with:

    python3 examples/prediction-market-bot/fake_venue.py
"""
import json
import unittest

from fake_venue import (BUY, MAKER, SELL, TAKER, FakeVenue, OrderRequest, Rejected,
                        expected_profit_fee_micro, load_market, load_session, sign_request)
from tests import scenario


def request(venue, client_key, outcome="YES", side=BUY, qty=100, price_micro=400000):
    return OrderRequest(client_key, venue.market["market_id"], outcome, side, qty, price_micro)


class FrozenFixture(unittest.TestCase):
    def test_the_frozen_log_is_what_the_venue_produces(self):
        _, events = scenario.winner_takes_all()
        self.assertEqual(events, load_session())

    def test_the_frozen_log_is_stored_in_sequence_order(self):
        events = load_session()
        self.assertEqual([e["seq"] for e in events], list(range(1, len(events) + 1)))


class OrderIdentity(unittest.TestCase):
    def setUp(self):
        self.venue = FakeVenue(load_market())

    def test_resending_the_same_intent_returns_the_same_order(self):
        req = request(self.venue, "ck-1")
        first = self.venue.place_order(req, sign_request(req.payload()))
        second = self.venue.place_order(req, sign_request(req.payload()))
        self.assertEqual(first["order_id"], second["order_id"])
        self.assertEqual(len(self.venue.orders), 1)
        self.assertEqual(len(self.venue.events), 1, "the resend emitted a second acceptance")

    def test_a_used_key_with_different_terms_is_refused(self):
        req = request(self.venue, "ck-1")
        self.venue.place_order(req, sign_request(req.payload()))
        other = request(self.venue, "ck-1", qty=50)
        with self.assertRaises(Rejected) as caught:
            self.venue.place_order(other, sign_request(other.payload()))
        self.assertEqual(caught.exception.reason, "CLIENT_KEY_REUSED_WITH_DIFFERENT_PAYLOAD")

    def test_a_bad_signature_is_refused(self):
        req = request(self.venue, "ck-1")
        with self.assertRaises(Rejected) as caught:
            self.venue.place_order(req, "00" * 32)
        self.assertEqual(caught.exception.reason, "BAD_SIGNATURE")
        self.assertEqual(self.venue.orders, {})

    def test_a_lookup_by_client_key_answers_what_was_sent(self):
        req = request(self.venue, "ck-1")
        self.venue.place_order(req, sign_request(req.payload()))
        self.assertEqual(self.venue.get_order_by_client_key("ck-1")["client_key"], "ck-1")
        self.assertIsNone(self.venue.get_order_by_client_key("ck-never-sent"))


class VenueConstraints(unittest.TestCase):
    def setUp(self):
        self.venue = FakeVenue(load_market())

    def refused(self, **kwargs):
        req = request(self.venue, kwargs.pop("client_key", "ck-x"), **kwargs)
        with self.assertRaises(Rejected) as caught:
            self.venue.place_order(req, sign_request(req.payload()))
        return caught.exception.reason

    def test_below_min_size_is_refused(self):
        self.assertEqual(self.refused(qty=1), "BELOW_MIN_SIZE")

    def test_off_tick_is_refused(self):
        self.assertEqual(self.refused(price_micro=400001), "OFF_TICK")

    def test_a_price_at_or_beyond_the_payout_is_refused(self):
        self.assertEqual(self.refused(price_micro=1000000), "PRICE_OUT_OF_RANGE")

    def test_an_unknown_outcome_is_refused(self):
        self.assertEqual(self.refused(outcome="MAYBE"), "UNKNOWN_OUTCOME")

    def test_a_sell_beyond_inventory_is_refused(self):
        self.assertEqual(self.refused(side=SELL, qty=10, price_micro=700000),
                         "INSUFFICIENT_POSITION")

    def test_two_sells_that_together_exceed_inventory_are_refused(self):
        buy = request(self.venue, "ck-buy", qty=10, price_micro=400000)
        self.venue.place_order(buy, sign_request(buy.payload()))
        self.venue.fill("ck-buy", 10, 400000, TAKER)
        first = request(self.venue, "ck-s1", side=SELL, qty=6, price_micro=700000)
        self.venue.place_order(first, sign_request(first.payload()))
        self.assertEqual(
            self.refused(client_key="ck-s2", side=SELL, qty=6, price_micro=700000),
            "INSUFFICIENT_POSITION")


class FeeModel(unittest.TestCase):
    """Three separate things: the amount, the rate that produced it, and the asset it is in."""

    def setUp(self):
        self.market = load_market()

    def test_the_fee_is_symmetric_about_the_midpoint(self):
        payout = self.market["payout_per_share_micro"]
        for price in range(10000, payout, 10000):
            self.assertEqual(
                expected_profit_fee_micro(self.market, 40, price, 70000),
                expected_profit_fee_micro(self.market, 40, payout - price, 70000),
                f"asymmetric at {price}")

    def test_the_fee_is_largest_at_the_midpoint(self):
        payout = self.market["payout_per_share_micro"]
        midpoint = expected_profit_fee_micro(self.market, 40, payout // 2, 70000)
        for price in range(10000, payout, 10000):
            self.assertLessEqual(expected_profit_fee_micro(self.market, 40, price, 70000),
                                 midpoint)

    def test_a_notional_rate_would_be_a_different_number(self):
        # 40 shares at 0.40 is 16 FUSD of notional. The same 7 percent charged on notional is
        # 1.12 FUSD; charged on expected profit it is 0.672. Same rate, same trade, and the
        # two models are not within rounding of each other.
        self.assertEqual(expected_profit_fee_micro(self.market, 40, 400000, 70000), 672000)
        notional_model = 40 * 400000 * 70000 // 1000000
        self.assertEqual(notional_model, 1120000)

    def test_a_maker_fill_carries_its_own_rate_and_a_zero_amount(self):
        venue = FakeVenue(self.market)
        req = request(venue, "ck-1")
        venue.place_order(req, sign_request(req.payload()))
        maker = venue.fill("ck-1", 10, 400000, MAKER)
        taker = venue.fill("ck-1", 10, 400000, TAKER)
        self.assertEqual(maker["fee"], {"amount_micro": 0, "rate_ppm": 0, "asset": "FPOINT"})
        self.assertEqual(taker["fee"]["rate_ppm"], 70000)
        self.assertGreater(taker["fee"]["amount_micro"], 0)
        self.assertEqual(taker["fee"]["asset"], self.market["fee"]["asset"])
        self.assertNotEqual(self.market["fee"]["asset"], self.market["collateral_asset"])


class Resolution(unittest.TestCase):
    def test_a_payout_vector_that_is_not_a_distribution_is_refused(self):
        venue = FakeVenue(load_market())
        with self.assertRaises(Rejected):
            venue.resolve([1, 1], 1)
        with self.assertRaises(Rejected):
            venue.resolve([1], 1)

    def test_a_split_has_no_winning_index(self):
        venue, events = scenario.split()
        resolved = events[-1]
        self.assertEqual(resolved["payout_numerators"], [1, 1])
        self.assertIsNone(resolved["winning_outcome_index"])

    def test_a_winner_takes_all_resolution_names_the_index(self):
        _, events = scenario.winner_takes_all()
        self.assertEqual(events[-1]["winning_outcome_index"], 0)

    def test_a_determination_is_provisional_and_the_resolution_is_the_payout(self):
        venue = FakeVenue(load_market())
        determined = venue.determine([1, 0], 1)
        self.assertEqual(determined["type"], "MARKET_DETERMINED")
        self.assertTrue(determined["provisional"])
        self.assertIsNone(venue.resolution)
        # A result exists, so the book is shut.
        req = request(venue, "ck-late")
        with self.assertRaises(Rejected) as caught:
            venue.place_order(req, sign_request(req.payload()))
        self.assertEqual(caught.exception.reason, "MARKET_DETERMINED")
        resolved = venue.resolve([1, 0], 1)
        self.assertEqual(resolved["type"], "MARKET_RESOLVED")
        self.assertNotIn("provisional", resolved)
        # And the terminal state has no way back to a revisable one.
        with self.assertRaises(Rejected):
            venue.determine([0, 1], 1)

    def test_a_reconnect_redelivers_without_renumbering(self):
        venue, events = scenario.winner_takes_all()
        again = venue.events_since(0)
        self.assertEqual([e["event_id"] for e in again], [e["event_id"] for e in events])
        self.assertEqual([e["seq"] for e in again], [e["seq"] for e in events])
        self.assertEqual(json.dumps(again, sort_keys=True),
                         json.dumps(events, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
