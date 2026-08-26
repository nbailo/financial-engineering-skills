"""Each of the five design notes in unsafe_bot.py, refuted with a number.

None of these is an exception. Every one of them is a balance that is quietly wrong while the
process reports success, which is why the counter-example is worth keeping in the repository
next to the version that gets them right.
"""
import unittest

from fake_venue import BUY, MICRO, SELL, TAKER, FakeVenue, load_market
from safe_bot import InsufficientAvailable, SafeBot, rebuild
from tests import scenario
from unsafe_bot import UnsafeBot

MARKET_ID = "FAKE-BINARY-1"
START_FUSD = 1000 * MICRO


class UnsafeIsWrong(unittest.TestCase):
    def setUp(self):
        self.venue, self.events = scenario.winner_takes_all()
        self.market = load_market()

    def both(self):
        safe = rebuild(self.market, self.venue, self.events)
        unsafe = UnsafeBot(market=self.market)
        unsafe.apply_all(self.events)
        return safe, unsafe

    def test_resting_orders_are_not_reserved(self):
        """Design note 1. Nothing is held while an order rests, so the balance overstates
        what is spendable by the whole size of the book."""
        venue = FakeVenue(load_market())
        safe = SafeBot(market=load_market(), authority=venue)
        unsafe = UnsafeBot(market=load_market())
        safe.submit(venue, "ck-1", "YES", BUY, 100, 400000)
        unsafe.submit(venue, "ck-2", "NO", BUY, 100, 550000)
        accepted = venue.events_since(0)
        safe.apply_all([e for e in accepted if e["client_key"] == "ck-1"])
        unsafe.apply_all([e for e in accepted if e["client_key"] == "ck-2"])
        self.assertEqual(safe.available["FUSD"], START_FUSD - 40 * MICRO)
        self.assertEqual(safe.reserved["FUSD"], 40 * MICRO)
        self.assertEqual(unsafe.available["FUSD"], START_FUSD,
                         "the unsafe bot shows the full balance as spendable")
        self.assertFalse(hasattr(unsafe, "reserved"))

    def test_nothing_is_held_for_the_fee_the_fill_will_owe(self):
        """Design note 1, on the asset the fee is charged in. The fee is not FUSD, so
        holding the collateral says nothing about being able to pay it. The safe bot cannot
        rest an order whose worst-case fee it could not cover. The unsafe bot rests it, the
        fill arrives, and the fee owed is larger than the whole FPOINT balance."""
        balances = {"FUSD": START_FUSD, "FPOINT": 1 * MICRO}
        venue = FakeVenue(load_market())
        safe = SafeBot(market=load_market(), authority=venue, available=dict(balances))
        with self.assertRaises(InsufficientAvailable) as caught:
            safe.submit(venue, "ck-safe", "YES", BUY, 100, 400000)
        self.assertIn("FPOINT", str(caught.exception))
        self.assertEqual(venue.orders, {}, "the order never reached the venue")

        unsafe = UnsafeBot(market=load_market(), available=dict(balances))
        self.assertEqual(unsafe.submit(venue, "ck-unsafe", "YES", BUY, 100, 400000), "SENT")
        fill = venue.fill("ck-unsafe", 100, 400000, TAKER)
        unsafe.apply_all(venue.events_since(0))
        self.assertEqual(fill["fee"]["amount_micro"], 1680000)
        self.assertGreater(fill["fee"]["amount_micro"], balances["FPOINT"],
                           "the fill owes more fee than the bot holds")
        self.assertEqual(unsafe.available["FPOINT"], 1 * MICRO,
                         "and the fee asset balance never moves, so nothing shows it")

    def test_short_reserve_is_the_wrong_side(self):
        """Design note 2. Short exposure is a purchase of the complement, so the obligation
        is the complement's price. The error changes sign at the midpoint, so no constant
        factor corrects it."""
        safe = SafeBot(market=self.market, authority=self.venue)
        unsafe = UnsafeBot(market=self.market)
        payout = self.market["payout_per_share_micro"]
        for price, expected_error in ((400000, 6 * MICRO), (500000, 0), (750000, -15 * MICRO)):
            with self.subTest(price=price):
                correct = safe.plan_short_exposure("YES", 30, price)["reserve_micro"]
                naive = unsafe.collateral_obligation(SELL, 30, price)
                self.assertEqual(correct, 30 * (payout - price))
                self.assertEqual(naive, 30 * price)
                self.assertEqual(correct - naive, expected_error)
                self.assertEqual(correct - naive, 30 * (payout - 2 * price))

    def test_settlement_is_credited_twice(self):
        """Design note 3. A reconnect redelivers the resolution and the payout lands again."""
        safe, unsafe = self.both()
        self.assertEqual(safe.available["FUSD"], 1051 * MICRO)
        self.assertEqual(unsafe.settlement_credited, 70 * MICRO)
        before = unsafe.available["FUSD"]

        redelivered = self.venue.settlement_events()
        safe.apply_all(redelivered)
        unsafe.apply_all(redelivered)

        self.assertEqual(safe.available["FUSD"], 1051 * MICRO, "the safe bot credits once")
        self.assertEqual(unsafe.settlement_credited, 140 * MICRO)
        self.assertEqual(unsafe.available["FUSD"], before + 70 * MICRO)
        self.assertEqual(unsafe.available["FUSD"] - safe.available["FUSD"], 67879000)

    def test_split_market_credits_nothing(self):
        """Design note 5. A split resolution has no winning index, so an index reader credits
        zero on exactly the case where the vector says half."""
        venue, events = scenario.split()
        safe = rebuild(self.market, venue, events)
        unsafe = UnsafeBot(market=self.market)
        unsafe.apply_all(events)
        self.assertEqual(safe.settlement_credit[MARKET_ID], 35 * MICRO)
        self.assertEqual(unsafe.settlement_credited, 0)

    def test_fee_is_taken_from_the_wrong_asset(self):
        """Design note 4. Amount, rate and asset collapsed into one number: the maker fill is
        charged a taker rate, and every charge comes out of the collateral balance."""
        safe, unsafe = self.both()
        self.assertEqual(safe.fees_paid["FPOINT"], 1113000)
        self.assertEqual(safe.fees_paid["FUSD"], 0)
        self.assertEqual(safe.available["FPOINT"], 100 * MICRO - 1113000)
        # 1_008_000 of that is the maker fill, which the venue charged nothing for.
        self.assertEqual(unsafe.fee_paid, 2121000)
        self.assertEqual(unsafe.available["FPOINT"], 100 * MICRO,
                         "the fee asset balance never moves")
        self.assertEqual(unsafe.fee_paid - safe.fees_paid["FPOINT"], 1008000)

    def test_an_ambiguous_submission_mints_a_second_order(self):
        """The retry under a fresh key the venue cannot recognise. Two live orders, one
        intended, and the second one is not visible as a duplicate to anybody."""
        venue = FakeVenue(load_market(), faults={"ck-1": "ambiguous_after_accept"})
        unsafe = UnsafeBot(market=load_market())
        unsafe.submit(venue, "ck-1", "YES", BUY, 100, 400000)
        self.assertEqual(unsafe.retries, ["ck-1-retry1"])
        self.assertEqual(len(venue.orders), 2)
        self.assertEqual(sorted(venue.by_client_key), ["ck-1", "ck-1-retry1"])

        # The safe bot, same fault, same intent: one order.
        venue_2 = FakeVenue(load_market(), faults={"ck-1": "ambiguous_after_accept"})
        safe = SafeBot(market=load_market(), authority=venue_2)
        safe.submit(venue_2, "ck-1", "YES", BUY, 100, 400000)
        safe.reconcile_unknown(venue_2, "ck-1")
        self.assertEqual(len(venue_2.orders), 1)

    def test_the_unsafe_bot_ignores_what_it_does_not_recognise(self):
        """The quiet one. An event type nobody planned for is skipped rather than refused."""
        unsafe = UnsafeBot(market=self.market)
        self.assertEqual(unsafe.apply_event({"type": "ORDER_AMENDED", "seq": 1}), "IGNORED")


if __name__ == "__main__":
    unittest.main()
