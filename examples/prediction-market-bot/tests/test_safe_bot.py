"""What the safe bot is claimed to get right, one test per claim.

Every property asserted in a comment in safe_bot.py has a case here. A claim with no case is
a claim nobody checks, which is how the asserted invariant ends up being exactly where the
bug lives.
"""
import json
import unittest
from fractions import Fraction

from fake_venue import (BUY, MAKER, MICRO, SELL, TAKER, FakeVenue, Rejected,
                        expected_profit_fee_micro, load_market, load_script, load_session,
                        run_script)
from safe_bot import (INTENTS, IllegalTransition, InsufficientAvailable, SafeBot,
                      UnknownFeeAsset, rebuild)
from tests import scenario

MARKET_ID = "FAKE-BINARY-1"
START_FUSD = 1000 * MICRO
START_FPOINT = 100 * MICRO


def fill_event(seq, **over):
    event = {"seq": seq, "event_id": f"ev-{seq:03d}", "type": "FILL", "market_id": MARKET_ID,
             "order_id": "ord-001", "client_key": "ck-1", "outcome": "YES", "side": BUY,
             "qty": 10, "price_micro": 400000, "liquidity": TAKER,
             "fee": {"amount_micro": 0, "rate_ppm": 70000, "asset": "FPOINT"}}
    event.update(over)
    return event


def working_buy():
    """A bot with one resting buy: 100 YES at 0.40, accepted by the venue."""
    venue = FakeVenue(load_market())
    bot = SafeBot(market=load_market(), authority=venue)
    bot.submit(venue, "ck-1", "YES", BUY, 100, 400000)
    bot.apply_all(venue.events_since(0))
    return venue, bot


class ReserveWhileResting(unittest.TestCase):
    def test_the_hold_exists_before_the_call_returns(self):
        venue = FakeVenue(load_market(), faults={"ck-1": "ambiguous_after_accept"})
        bot = SafeBot(market=load_market(), authority=venue)
        self.assertEqual(bot.submit(venue, "ck-1", "YES", BUY, 100, 400000), "UNKNOWN")
        # The response proved nothing, so the collateral stays held.
        self.assertEqual(bot.reserved["FUSD"], 40 * MICRO)
        self.assertEqual(bot.available["FUSD"], START_FUSD - 40 * MICRO)

    def test_a_resting_buy_holds_its_collateral(self):
        _, bot = working_buy()
        self.assertEqual(bot.orders["ck-1"].state, "WORKING")
        self.assertEqual(bot.reserved["FUSD"], 40 * MICRO)
        self.assertEqual(bot.available["FUSD"], START_FUSD - 40 * MICRO)

    def test_a_cancel_releases_the_hold(self):
        venue, bot = working_buy()
        venue.cancel("ck-1")
        bot.apply_all(venue.events_since(1))
        self.assertEqual(bot.orders["ck-1"].state, "CANCELLED")
        self.assertEqual(bot.reserved["FUSD"], 0)
        self.assertEqual(bot.available["FUSD"], START_FUSD)

    def test_a_partial_fill_releases_only_its_slice(self):
        venue, bot = working_buy()
        venue.fill("ck-1", 25, 400000, MAKER)
        bot.apply_all(venue.events_since(1))
        self.assertEqual(bot.reserved["FUSD"], 30 * MICRO)
        self.assertEqual(bot.available["FUSD"], START_FUSD - 40 * MICRO)
        self.assertEqual(bot.positions["YES"], 25)

    def test_two_resting_orders_cannot_hold_more_than_the_balance(self):
        venue = FakeVenue(load_market())
        bot = SafeBot(market=load_market(), authority=venue)
        bot.submit(venue, "ck-1", "YES", BUY, 2000, 400000)
        with self.assertRaises(InsufficientAvailable):
            bot.submit(venue, "ck-2", "NO", BUY, 2000, 550000)

    def test_a_sell_locks_shares_and_no_collateral(self):
        venue, events = scenario.winner_takes_all()
        bot = rebuild(load_market(), venue, events[:5])
        self.assertEqual(bot.locked["YES"], 30)
        self.assertEqual(bot.positions["YES"], 100)
        self.assertEqual(bot.available["FUSD"], START_FUSD - 40 * MICRO - 275 * MICRO // 10)
        self.assertEqual(bot.reserved["FUSD"], 275 * MICRO // 10)


class FeeAssetReservation(unittest.TestCase):
    """The fee is charged in FPOINT and the position is in FUSD.

    Holding the collateral alone lets an order rest that a fill can leave unable to pay its
    fee, so the worst-case fee is held in the asset the fee is charged in, before the order
    can rest.
    """

    def test_a_resting_order_holds_the_worst_case_fee_in_the_fee_asset(self):
        _, bot = working_buy()
        self.assertEqual(bot.reserved["FPOINT"], 1680000)
        self.assertEqual(bot.available["FPOINT"], START_FPOINT - 1680000)
        self.assertEqual(bot.reserved["FUSD"], 40 * MICRO)

    def test_the_hold_is_taken_at_the_price_that_maximises_the_fee(self):
        bot = SafeBot(market=load_market(), authority=None)
        # A bid above the midpoint fills at or below itself, and the fee peaks at the
        # midpoint, so the fee at the bid is not the worst case.
        self.assertEqual(bot.worst_case_fee_micro(BUY, 100, 900000), 1750000)
        self.assertEqual(expected_profit_fee_micro(load_market(), 100, 900000, 70000), 630000)
        # An offer above the midpoint fills at or above itself, where the fee only falls.
        self.assertEqual(bot.worst_case_fee_micro(SELL, 30, 700000), 441000)

    def test_an_order_whose_fee_cannot_be_held_never_reaches_the_venue(self):
        venue = FakeVenue(load_market())
        bot = SafeBot(market=load_market(), authority=venue,
                      available={"FUSD": START_FUSD, "FPOINT": 1 * MICRO})
        with self.assertRaises(InsufficientAvailable) as caught:
            bot.submit(venue, "ck-1", "YES", BUY, 100, 400000)
        self.assertIn("FPOINT", str(caught.exception))
        self.assertEqual(venue.orders, {})
        # The collateral it could afford is not left held for an order that never rested.
        self.assertEqual(bot.available["FUSD"], START_FUSD)
        self.assertEqual(bot.reserved["FUSD"], 0)
        self.assertEqual(bot.orders["ck-1"].state, "REFUSED")

    def test_an_order_whose_fee_asset_this_process_does_not_hold_cannot_rest(self):
        venue = FakeVenue(load_market())
        bot = SafeBot(market=load_market(), authority=venue, available={"FUSD": START_FUSD})
        with self.assertRaises(UnknownFeeAsset):
            bot.submit(venue, "ck-1", "YES", BUY, 100, 400000)
        self.assertEqual(venue.orders, {})
        self.assertEqual(bot.orders["ck-1"].state, "REFUSED")
        self.assertEqual(bot.available["FUSD"], START_FUSD)

    def test_a_fill_pays_its_fee_out_of_what_the_order_held(self):
        venue = FakeVenue(load_market())
        bot = SafeBot(market=load_market(), authority=venue,
                      available={"FUSD": START_FUSD, "FPOINT": 1680000})
        bot.submit(venue, "ck-1", "YES", BUY, 100, 400000)
        self.assertEqual(bot.available["FPOINT"], 0, "the whole fee balance is committed")
        venue.fill("ck-1", 100, 400000, TAKER)
        bot.apply_all(venue.events_since(0))
        self.assertEqual(bot.fees_paid["FPOINT"], 1680000)
        self.assertEqual(bot.available["FPOINT"], 0)
        self.assertEqual(bot.reserved["FPOINT"], 0)
        self.assertEqual(bot.alerts, [])

    def test_a_cancel_releases_the_fee_hold(self):
        venue, bot = working_buy()
        venue.cancel("ck-1")
        bot.apply_all(venue.events_since(1))
        self.assertEqual(bot.reserved["FPOINT"], 0)
        self.assertEqual(bot.available["FPOINT"], START_FPOINT)


class ConcurrentRestingOrders(unittest.TestCase):
    """Two orders resting at once. The holds add, so one balance cannot back both."""

    def test_two_resting_orders_reserve_the_sum_of_their_worst_cases(self):
        venue = FakeVenue(load_market())
        bot = SafeBot(market=load_market(), authority=venue)
        bot.submit(venue, "ck-1", "YES", BUY, 100, 400000)
        bot.submit(venue, "ck-2", "NO", BUY, 50, 550000)
        bot.apply_all(venue.events_since(0))
        self.assertEqual(bot.orders["ck-1"].state, "WORKING")
        self.assertEqual(bot.orders["ck-2"].state, "WORKING")
        self.assertEqual(bot.reserved["FUSD"], 40 * MICRO + 275 * MICRO // 10)
        self.assertEqual(bot.reserved["FPOINT"], 1680000 + 875000)
        self.assertGreater(bot.reserved["FPOINT"], max(1680000, 875000),
                           "the sum, not the larger of the two")
        self.assertEqual(bot.available["FUSD"], START_FUSD - bot.reserved["FUSD"])
        self.assertEqual(bot.available["FPOINT"], START_FPOINT - bot.reserved["FPOINT"])

    def test_a_second_order_affordable_alone_is_refused_beside_the_first(self):
        # 60 FUSD. The first order commits 40 of it. The second costs 55, which the balance
        # covers on its own and does not cover beside the first.
        balances = {"FUSD": 60 * MICRO, "FPOINT": START_FPOINT}
        alone_venue = FakeVenue(load_market())
        alone = SafeBot(market=load_market(), authority=alone_venue, available=dict(balances))
        self.assertEqual(alone.submit(alone_venue, "ck-2", "NO", BUY, 100, 550000), "SENT")

        venue = FakeVenue(load_market())
        bot = SafeBot(market=load_market(), authority=venue, available=dict(balances))
        bot.submit(venue, "ck-1", "YES", BUY, 100, 400000)
        with self.assertRaises(InsufficientAvailable) as caught:
            bot.submit(venue, "ck-2", "NO", BUY, 100, 550000)
        self.assertIn("FUSD", str(caught.exception))
        self.assertEqual(bot.reserved["FUSD"], 40 * MICRO)
        self.assertEqual(bot.available["FUSD"], 20 * MICRO)
        self.assertEqual(bot.orders["ck-2"].state, "REFUSED")
        self.assertEqual(list(venue.by_client_key), ["ck-1"])

    def test_the_fee_holds_add_up_the_same_way(self):
        # 2 FPOINT. Either order's worst-case fee fits alone. Together they do not.
        balances = {"FUSD": START_FUSD, "FPOINT": 2 * MICRO}
        alone_venue = FakeVenue(load_market())
        alone = SafeBot(market=load_market(), authority=alone_venue, available=dict(balances))
        self.assertEqual(alone.submit(alone_venue, "ck-2", "NO", BUY, 50, 550000), "SENT")
        self.assertEqual(alone.reserved["FPOINT"], 875000)

        venue = FakeVenue(load_market())
        bot = SafeBot(market=load_market(), authority=venue, available=dict(balances))
        bot.submit(venue, "ck-1", "YES", BUY, 100, 400000)
        with self.assertRaises(InsufficientAvailable) as caught:
            bot.submit(venue, "ck-2", "NO", BUY, 50, 550000)
        self.assertIn("FPOINT", str(caught.exception))
        # The collateral was never the constraint: 67.5 FUSD of 1000 was affordable.
        self.assertEqual(bot.reserved["FUSD"], 40 * MICRO)
        self.assertEqual(bot.reserved["FPOINT"], 1680000)
        self.assertEqual(list(venue.by_client_key), ["ck-1"])


class ShortExposure(unittest.TestCase):
    def test_a_short_is_planned_as_a_purchase_of_the_complement(self):
        venue, _ = scenario.winner_takes_all()
        bot = SafeBot(market=load_market(), authority=venue)
        plan = bot.plan_short_exposure("YES", 30, 400000)
        self.assertEqual(plan["outcome"], "NO")
        self.assertEqual(plan["side"], BUY)
        self.assertEqual(plan["price_micro"], 600000)
        self.assertEqual(plan["reserve_micro"], 30 * 600000)

    def test_the_bot_refuses_to_sell_what_it_does_not_hold(self):
        venue = FakeVenue(load_market())
        bot = SafeBot(market=load_market(), authority=venue)
        with self.assertRaises(ValueError) as caught:
            bot.submit(venue, "ck-1", "YES", SELL, 30, 700000)
        self.assertIn("complement", str(caught.exception))


class Fees(unittest.TestCase):
    def test_the_fee_asset_and_the_collateral_asset_are_separate_balances(self):
        venue, events = scenario.winner_takes_all()
        bot = rebuild(load_market(), venue, events)
        self.assertEqual(bot.fees_paid["FPOINT"], 672000 + 441000)
        self.assertEqual(bot.fees_paid["FUSD"], 0)
        self.assertEqual(bot.available["FPOINT"], START_FPOINT - bot.fees_paid["FPOINT"])

    def test_a_maker_fill_is_charged_its_own_rate(self):
        venue, events = scenario.winner_takes_all()
        bot = rebuild(load_market(), venue, events[:2])
        self.assertEqual(bot.positions["YES"], 60, "the maker fill was booked")
        self.assertEqual(bot.fees_paid["FPOINT"], 0, "and charged at the maker rate")
        self.assertEqual(bot.alerts, [])

    def test_a_fee_in_an_asset_this_process_does_not_hold_is_refused(self):
        _, bot = working_buy()
        with self.assertRaises(UnknownFeeAsset):
            bot.apply_event(fill_event(90, fee={"amount_micro": 1, "rate_ppm": 70000,
                                                "asset": "SOMETHING-ELSE"}))

    def test_an_amount_that_does_not_match_the_rate_raises_an_alert(self):
        _, bot = working_buy()
        bot.apply_event(fill_event(90, qty=10, price_micro=400000,
                                   fee={"amount_micro": 999, "rate_ppm": 70000,
                                        "asset": "FPOINT"}))
        self.assertTrue(any("does not match" in a for a in bot.alerts), bot.alerts)

    def test_a_rate_that_does_not_match_the_metadata_raises_an_alert(self):
        _, bot = working_buy()
        bot.apply_event(fill_event(90, liquidity=MAKER,
                                   fee={"amount_micro": 0, "rate_ppm": 70000,
                                        "asset": "FPOINT"}))
        self.assertTrue(any("metadata says" in a for a in bot.alerts), bot.alerts)


class DeterministicRebuild(unittest.TestCase):
    def test_two_rebuilds_of_one_log_are_byte_identical(self):
        venue_a, events_a = scenario.winner_takes_all()
        venue_b, events_b = scenario.winner_takes_all()
        first = rebuild(load_market(), venue_a, events_a).snapshot()
        second = rebuild(load_market(), venue_b, events_b).snapshot()
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))

    def test_the_frozen_log_rebuilds_to_the_known_state(self):
        venue, _ = scenario.winner_takes_all()
        bot = rebuild(load_market(), venue, load_session())
        self.assertEqual(bot.available["FUSD"], 1051 * MICRO)
        self.assertEqual(bot.available["FPOINT"], START_FPOINT - 1113000)
        self.assertEqual(bot.positions, {"YES": 70, "NO": 0})
        self.assertEqual(bot.reserved["FUSD"], 0)
        self.assertEqual(bot.alerts, [])

    def test_a_full_redelivery_changes_nothing(self):
        venue, events = scenario.winner_takes_all()
        bot = rebuild(load_market(), venue, events)
        before = json.dumps(bot.snapshot(), sort_keys=True)
        bot.apply_all(venue.events_since(0))
        self.assertEqual(json.dumps(bot.snapshot(), sort_keys=True), before)

    def test_a_redelivery_under_fresh_identities_changes_nothing(self):
        # The dedupe table would catch a repeat of the same event_id. This proves the
        # watermark is doing work of its own, by renumbering the identities and leaving the
        # sequence alone.
        venue, events = scenario.winner_takes_all()
        bot = rebuild(load_market(), venue, events)
        before = json.dumps(bot.snapshot(), sort_keys=True)
        renamed = [dict(e, event_id=f"renumbered-{e['seq']}") for e in events]
        self.assertTrue(all(bot.apply_event(e) == "STALE" for e in renamed))
        self.assertEqual(json.dumps(bot.snapshot(), sort_keys=True), before)


class Legality(unittest.TestCase):
    def test_an_event_for_an_intent_we_never_committed_is_refused(self):
        _, bot = working_buy()
        with self.assertRaises(IllegalTransition):
            bot.apply_event(fill_event(90, client_key="ck-never-sent"))

    def test_an_unknown_event_type_is_refused_not_ignored(self):
        _, bot = working_buy()
        with self.assertRaises(IllegalTransition):
            bot.apply_event(fill_event(90, type="ORDER_AMENDED"))

    def test_a_second_acceptance_does_not_re_open_a_working_order(self):
        venue, bot = working_buy()
        accepted = dict(venue.events[0], event_id="ev-901", seq=901)
        with self.assertRaises(IllegalTransition):
            bot.apply_event(accepted)

    def test_a_fill_beyond_the_remaining_quantity_is_refused(self):
        _, bot = working_buy()
        with self.assertRaises(IllegalTransition) as caught:
            bot.apply_event(fill_event(90, qty=101))
        self.assertIn("exceeds remaining", str(caught.exception))

    def test_a_filled_order_accepts_no_further_fill(self):
        venue, bot = working_buy()
        venue.fill("ck-1", 100, 400000, TAKER)
        bot.apply_all(venue.events_since(1))
        self.assertEqual(bot.orders["ck-1"].state, "FILLED")
        with self.assertRaises(IllegalTransition):
            bot.apply_event(fill_event(90, qty=1))

    def test_a_late_fill_on_a_cancelled_order_is_a_correction_and_is_booked(self):
        venue, events = scenario.winner_takes_all()
        bot = rebuild(load_market(), venue, events[:7])
        self.assertEqual(bot.orders["ck-buy-no-1"].state, "CANCELLED")
        late = fill_event(90, order_id="ord-002", client_key="ck-buy-no-1", outcome="NO",
                          qty=50, price_micro=550000,
                          fee={"amount_micro": 866250, "rate_ppm": 70000, "asset": "FPOINT"})
        self.assertEqual(bot.apply_event(late), "APPLIED")
        self.assertEqual(bot.positions["NO"], 50)
        self.assertEqual(bot.orders["ck-buy-no-1"].state, "CANCELLED")
        self.assertGreaterEqual(bot.reserved["FUSD"], 0)

    def test_the_venue_terms_win_over_the_intent(self):
        venue = FakeVenue(load_market())
        bot = SafeBot(market=load_market(), authority=venue)
        bot.submit(venue, "ck-1", "YES", BUY, 100, 400000)
        bot.orders["ck-1"].state = "PENDING"
        bot.seen_events.clear()
        bot.watermark.clear()
        amended = dict(venue.events[0], qty=60, event_id="ev-902", seq=902)
        bot.apply_event(amended)
        self.assertEqual(bot.orders["ck-1"].qty, 60)
        self.assertEqual(bot.reserved["FUSD"], 24 * MICRO)
        self.assertTrue(any("differ from the intent" in a for a in bot.alerts), bot.alerts)


class Settlement(unittest.TestCase):
    def test_the_payout_is_credited_exactly_once(self):
        venue, events = scenario.winner_takes_all()
        bot = rebuild(load_market(), venue, events)
        self.assertEqual(bot.settlement_credit[MARKET_ID], 70 * MICRO)
        self.assertEqual(bot.available["FUSD"], 1051 * MICRO)
        # A reconnect that renumbers, so neither the dedupe table nor the watermark stops it.
        # The credited-once guard is the only thing left, and it holds.
        for event in venue.settlement_events():
            bot.apply_event(dict(event, event_id="reconnect-1", seq=900))
        self.assertEqual(bot.settlement_credit[MARKET_ID], 70 * MICRO)
        self.assertEqual(bot.available["FUSD"], 1051 * MICRO)

    def test_a_split_market_pays_the_vector_not_the_index(self):
        venue, events = scenario.split()
        bot = rebuild(load_market(), venue, events)
        self.assertIsNone(events[-1]["winning_outcome_index"])
        self.assertEqual(bot.settlement_credit[MARKET_ID], 35 * MICRO)

    def test_the_credit_comes_from_the_authority_not_from_local_state(self):
        venue, events = scenario.winner_takes_all()
        bot = rebuild(load_market(), venue, events[:7])
        self.assertEqual(bot.positions["YES"], 70)
        # A fill that has not reached this process yet is already in the venue's number.
        bot.authority = scenario.StubAuthority(MARKET_ID, {"YES": 71, "NO": 0})
        bot.apply_event(events[7])
        self.assertEqual(bot.settlement_credit[MARKET_ID], 71 * MICRO)
        self.assertTrue(any("position break" in a for a in bot.alerts), bot.alerts)

    def test_an_order_still_open_at_resolution_is_alerted(self):
        venue, events = scenario.winner_takes_all()
        bot = rebuild(load_market(), venue, [e for e in events if e["seq"] != 7])
        self.assertTrue(any("orders still open" in a for a in bot.alerts), bot.alerts)

    def test_rounding_leaves_dust_that_is_accounted_for(self):
        authority = scenario.StubAuthority(MARKET_ID, {"YES": 1, "NO": 0})
        bot = SafeBot(market=load_market(), authority=authority)
        bot.apply_event({"seq": 1, "event_id": "ev-001", "type": "MARKET_RESOLVED",
                         "market_id": MARKET_ID, "payout_numerators": [2, 1],
                         "payout_denominator": 3, "winning_outcome_index": None})
        credited = bot.settlement_credit[MARKET_ID]
        self.assertEqual(credited, 666666)
        self.assertEqual(bot.settlement_dust, Fraction(2, 3))
        self.assertEqual(credited + bot.settlement_dust, Fraction(2 * MICRO, 3))

    def test_a_payout_vector_that_is_not_a_distribution_is_refused(self):
        authority = scenario.StubAuthority(MARKET_ID, {"YES": 1, "NO": 0})
        bot = SafeBot(market=load_market(), authority=authority)
        with self.assertRaises(ValueError):
            bot.apply_event({"seq": 1, "event_id": "ev-001", "type": "MARKET_RESOLVED",
                             "market_id": MARKET_ID, "payout_numerators": [1, 1],
                             "payout_denominator": 1, "winning_outcome_index": None})


class ProvisionalDetermination(unittest.TestCase):
    """A result exists is not the same fact as the payout has been made.

    The venue publishes the outcome while saying it can still be revised, and publishes a
    terminal payout later. Only the second one moves a balance, and nothing moves it back.
    """

    def trading_done(self):
        """The frozen scenario up to the point the market stops trading."""
        venue = FakeVenue(load_market())
        events = run_script(venue, [s for s in load_script() if s["action"] != "resolve"])
        return venue, rebuild(load_market(), venue, events)

    def test_a_provisional_determination_moves_no_balance(self):
        venue, bot = self.trading_done()
        before = json.dumps(bot.snapshot(), sort_keys=True)
        determined = venue.determine([1, 0], 1)
        self.assertTrue(determined["provisional"])
        self.assertEqual(bot.apply_event(determined), "APPLIED")
        self.assertEqual(bot.provisional_credit[MARKET_ID], 70 * MICRO)
        # The 70 FUSD the position is worth is not spendable and is not credited.
        self.assertEqual(bot.available["FUSD"], 981 * MICRO)
        self.assertNotIn(MARKET_ID, bot.settlement_credit)
        self.assertNotIn(MARKET_ID, bot.settled)
        self.assertEqual(json.dumps(dict(bot.snapshot(), provisional_credit={}),
                                    sort_keys=True), before)

    def test_only_the_terminal_payout_state_releases_the_value(self):
        venue, bot = self.trading_done()
        bot.apply_event(venue.determine([1, 0], 1))
        # A redelivery of the determination is still not a payout.
        self.assertEqual(bot.apply_event(dict(venue.events[-1], event_id="reconnect-1")),
                         "STALE")
        self.assertEqual(bot.available["FUSD"], 981 * MICRO)
        self.assertEqual(bot.settlement_credit, {})

        self.assertEqual(bot.apply_event(venue.resolve([1, 0], 1)), "APPLIED")
        self.assertEqual(bot.settlement_credit[MARKET_ID], 70 * MICRO)
        self.assertEqual(bot.available["FUSD"], 1051 * MICRO)
        self.assertEqual(bot.provisional_credit, {}, "the provisional record is spent")
        for event in venue.settlement_events():
            bot.apply_event(dict(event, event_id="reconnect-2", seq=900))
        self.assertEqual(bot.available["FUSD"], 1051 * MICRO)

    def test_a_determination_after_the_terminal_payout_pays_nothing(self):
        venue, bot = self.trading_done()
        bot.apply_event(venue.resolve([1, 0], 1))
        self.assertEqual(bot.available["FUSD"], 1051 * MICRO)
        # The venue has no path back from the terminal state.
        with self.assertRaises(Rejected):
            venue.determine([0, 1], 1)
        # And a message claiming one is refused a credit, whatever it carries.
        late = {"seq": 900, "event_id": "ev-900", "type": "MARKET_DETERMINED",
                "market_id": MARKET_ID, "payout_numerators": [0, 1],
                "payout_denominator": 1, "provisional": True}
        self.assertEqual(bot.apply_event(late), "APPLIED")
        self.assertEqual(bot.available["FUSD"], 1051 * MICRO)
        self.assertEqual(bot.settlement_credit[MARKET_ID], 70 * MICRO)
        self.assertEqual(bot.provisional_credit, {})
        self.assertTrue(any("after the terminal payout" in a for a in bot.alerts), bot.alerts)


class AmbiguousSubmission(unittest.TestCase):
    def test_a_lost_response_after_acceptance_leaves_exactly_one_order(self):
        venue = FakeVenue(load_market(), faults={"ck-1": "ambiguous_after_accept"})
        bot = SafeBot(market=load_market(), authority=venue)
        self.assertEqual(bot.submit(venue, "ck-1", "YES", BUY, 100, 400000), "UNKNOWN")
        self.assertEqual(bot.orders["ck-1"].state, "UNKNOWN")
        self.assertEqual(bot.reconcile_unknown(venue, "ck-1"), "FOUND")
        bot.apply_all(venue.events_since(0))
        self.assertEqual(len(venue.orders), 1)
        self.assertEqual(bot.orders["ck-1"].state, "WORKING")
        self.assertEqual(bot.reserved["FUSD"], 40 * MICRO)

    def test_a_lost_response_before_acceptance_resends_the_same_key(self):
        venue = FakeVenue(load_market(), faults={"ck-1": "ambiguous_before_accept"})
        bot = SafeBot(market=load_market(), authority=venue)
        self.assertEqual(bot.submit(venue, "ck-1", "YES", BUY, 100, 400000), "UNKNOWN")
        self.assertEqual(venue.orders, {})
        self.assertEqual(bot.reconcile_unknown(venue, "ck-1"), "SENT")
        self.assertEqual(len(venue.orders), 1)
        self.assertEqual(list(venue.by_client_key), ["ck-1"])
        bot.apply_all(venue.events_since(0))
        self.assertEqual(bot.orders["ck-1"].state, "WORKING")

    def test_a_client_key_is_never_reused_for_a_second_intent(self):
        venue = FakeVenue(load_market())
        bot = SafeBot(market=load_market(), authority=venue)
        bot.submit(venue, "ck-1", "YES", BUY, 100, 400000)
        with self.assertRaises(ValueError):
            bot.submit(venue, "ck-1", "YES", BUY, 50, 400000)


class Reconciliation(unittest.TestCase):
    def test_a_clean_run_reconciles(self):
        venue, events = scenario.winner_takes_all()
        bot = rebuild(load_market(), venue, events)
        self.assertEqual(bot.reconcile(MARKET_ID), [])

    def test_a_planted_break_is_detected(self):
        venue, events = scenario.winner_takes_all()
        bot = rebuild(load_market(), venue, events)
        bot.positions["YES"] -= 1
        breaks = bot.reconcile(MARKET_ID)
        self.assertEqual(len(breaks), 1)
        self.assertEqual((breaks[0].what, breaks[0].ours, breaks[0].theirs),
                         ("position:YES", 69, 70))


class IntentLog(unittest.TestCase):
    def test_every_frozen_intent_is_acknowledged_in_the_frozen_log(self):
        keys = {i["client_key"] for i in INTENTS}
        seen = {e["client_key"] for e in load_session() if e["type"] == "ORDER_ACCEPTED"}
        self.assertEqual(keys, seen)


if __name__ == "__main__":
    unittest.main()
