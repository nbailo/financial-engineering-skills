#!/usr/bin/env python3
"""The version that is correct about the things this example is about.

Read it next to unsafe_bot.py. The two consume the same frozen event log and disagree about
five things: when collateral is reserved, which side of the book a short obligates, what a
fee is, whether a redelivered message is a new fact, and whether a payout is a vector or an
index.

Scope of the claims below
-------------------------
Every property asserted in a comment here has a test in tests/ that fails if it stops
holding. Nothing here is a claim about a real venue. There is no live mode and no credential
path: the only counterparty is fake_venue.FakeVenue, in this process.

The in-memory stand-ins, named
-----------------------------
`self.orders` stands in for a committed intent table, `self.seen_events` for a durable dedupe
table, and `self.watermark` for a conditional `UPDATE ... WHERE v < :v` in the same
transaction as the effect. A dict is not a database and this example does not ship one. What
the example does show is the ordering that makes the durable version correct: the intent and
its key exist before the call, and the guard is checked before the effect rather than after.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction

from fake_venue import (BUY, MICRO, SELL, AmbiguousResponse, FakeVenue, OrderRequest,
                        Rejected, expected_profit_fee_micro, rate_ppm_for, sign_request)

DEFAULT_BALANCES = {"FUSD": 1000 * MICRO, "FPOINT": 100 * MICRO}

# Which stream a message arrived on. The fake venue numbers monotonically within a source
# and says nothing about order across sources, so the watermark is namespaced by source and a
# settlement message does not suppress an order message carrying a lower sequence. The
# cross-source case is the one test_the_credit_comes_from_the_authority_not_from_local_state
# covers: a fill this process has not seen yet is already in the authority's number.
SOURCE = {"ORDER_ACCEPTED": "orders", "FILL": "orders", "ORDER_CANCELLED": "orders",
          "MARKET_RESOLVED": "settlement"}

# The legal (state, event) pairs, enumerated. Everything absent is refused with an explicit
# error rather than ignored. A terminal state accepts exactly the correction the venue makes
# to a fact already booked, which is a late fill on a cancelled order, and nothing else. No
# status message re-opens anything.
# The pair is what is legal; the resulting state belongs to the handler, because a fill lands
# in WORKING or in FILLED depending on what is left, and a fill on a cancelled order leaves
# the terminal state alone.
LEGAL = frozenset({
    ("PENDING", "ORDER_ACCEPTED"),
    ("WORKING", "FILL"),
    ("WORKING", "ORDER_CANCELLED"),
    ("CANCELLED", "FILL"),
})


class IllegalTransition(RuntimeError):
    pass


class UnknownFeeAsset(RuntimeError):
    pass


class InsufficientAvailable(RuntimeError):
    pass


@dataclass
class OrderRecord:
    client_key: str
    outcome: str
    side: str
    qty: int
    price_micro: int
    state: str = "PENDING"
    order_id: str | None = None
    filled: int = 0
    reserved_micro: int = 0
    locked_shares: int = 0


@dataclass
class Break:
    """One disagreement between this process and the authority."""
    what: str
    ours: int
    theirs: int


@dataclass
class SafeBot:
    market: dict
    authority: object
    available: dict = field(default_factory=lambda: dict(DEFAULT_BALANCES))
    reserved: dict = field(default_factory=lambda: {a: 0 for a in DEFAULT_BALANCES})
    fees_paid: dict = field(default_factory=lambda: {a: 0 for a in DEFAULT_BALANCES})
    positions: dict = field(default_factory=dict)
    locked: dict = field(default_factory=dict)
    orders: dict = field(default_factory=dict)
    by_order_id: dict = field(default_factory=dict)
    seen_events: set = field(default_factory=set)
    watermark: dict = field(default_factory=dict)
    settled: set = field(default_factory=set)
    settlement_credit: dict = field(default_factory=dict)
    settlement_dust: Fraction = Fraction(0)
    alerts: list = field(default_factory=list)

    def __post_init__(self) -> None:
        for outcome in self.market["outcomes"]:
            self.positions.setdefault(outcome, 0)
            self.locked.setdefault(outcome, 0)

    # ------------------------------------------------------------------ balances

    @property
    def collateral(self) -> str:
        return self.market["collateral_asset"]

    @property
    def payout_micro(self) -> int:
        return self.market["payout_per_share_micro"]

    def _reserve(self, asset: str, amount: int) -> None:
        if amount > self.available[asset]:
            raise InsufficientAvailable(
                f"{asset}: need {amount}, available {self.available[asset]}")
        self.available[asset] -= amount
        self.reserved[asset] += amount

    def _release(self, asset: str, amount: int) -> None:
        self.reserved[asset] -= amount
        self.available[asset] += amount

    def _lock_shares(self, outcome: str, qty: int) -> None:
        free = self.positions[outcome] - self.locked[outcome]
        if qty > free:
            raise InsufficientAvailable(f"{outcome}: need {qty} shares, free {free}")
        self.locked[outcome] += qty

    # ------------------------------------------------------- obligation and sizing

    def collateral_obligation(self, side: str, qty: int, price_micro: int) -> int:
        """What a resting order obligates, in micro-units of collateral.

        A buy obligates the price it bid. A sell obligates nothing beyond the shares it
        locks, because this venue refuses a sell it cannot cover from inventory. There is no
        third case here, because there is no short: see `plan_short_exposure`.
        """
        return qty * price_micro if side == BUY else 0

    def complement_of(self, outcome: str) -> str:
        outcomes = self.market["outcomes"]
        if len(outcomes) != 2:
            raise ValueError("the complement of one outcome is a basket unless there are two")
        return outcomes[1] if outcome == outcomes[0] else outcomes[0]

    def plan_short_exposure(self, outcome: str, qty: int, price_micro: int) -> dict:
        """Short exposure to an outcome is a purchase of its complement, priced at the
        complement's price, on the complement's book.

        The obligation is therefore `qty * (payout - price)` and not `qty * price`. The two
        differ by `qty * (payout - 2 * price)`, which changes sign at the midpoint, so a
        keeper that carries a signed quantity is wrong in both directions and cannot be
        corrected with a constant factor. tests/test_unsafe_bot_is_wrong.py pins the number.
        """
        complement = self.complement_of(outcome)
        complement_price = self.payout_micro - price_micro
        return {"outcome": complement, "side": BUY, "qty": qty,
                "price_micro": complement_price,
                "reserve_micro": qty * complement_price}

    # ------------------------------------------------------------------- sending

    def submit(self, venue: FakeVenue, client_key: str, outcome: str, side: str,
               qty: int, price_micro: int) -> str:
        """Three phases, and the first one commits.

        The key is minted from this intent instance, the intent row and its reserve exist
        before the call, and the outcome is recorded after. A response that does not prove
        the outcome leaves the intent standing as UNKNOWN and is never resubmitted under a
        new key: `reconcile_unknown` asks the venue about the identity that was sent.
        """
        if client_key in self.orders:
            raise ValueError(f"client key already used for a different intent: {client_key}")
        if side == SELL and qty > self.positions[outcome] - self.locked[outcome]:
            raise ValueError(
                "this venue cannot represent a short; use plan_short_exposure and buy the "
                "complement")

        rec = OrderRecord(client_key, outcome, side, qty, price_micro)
        self.orders[client_key] = rec
        self._hold(rec)
        return self._send(venue, rec)

    def _hold(self, rec: OrderRecord) -> None:
        obligation = self.collateral_obligation(rec.side, rec.qty, rec.price_micro)
        if obligation:
            self._reserve(self.collateral, obligation)
            rec.reserved_micro = obligation
        if rec.side == SELL:
            self._lock_shares(rec.outcome, rec.qty)
            rec.locked_shares = rec.qty

    def _unwind(self, rec: OrderRecord) -> None:
        if rec.reserved_micro:
            self._release(self.collateral, rec.reserved_micro)
            rec.reserved_micro = 0
        if rec.locked_shares:
            self.locked[rec.outcome] -= rec.locked_shares
            rec.locked_shares = 0

    def _send(self, venue: FakeVenue, rec: OrderRecord) -> str:
        req = OrderRequest(rec.client_key, self.market["market_id"], rec.outcome, rec.side,
                           rec.qty, rec.price_micro)
        try:
            view = venue.place_order(req, sign_request(req.payload()))
        except AmbiguousResponse as exc:
            # UNKNOWN, never "did not happen". The reserve stays held, because the order may
            # be resting, and the key stays the same, because it is how the venue will
            # recognise the intent when we ask.
            rec.state = "UNKNOWN"
            self.alerts.append(f"{rec.client_key}: ambiguous submission ({exc})")
            return "UNKNOWN"
        except Rejected as exc:
            rec.state = "REJECTED"
            self._unwind(rec)
            self.alerts.append(f"{rec.client_key}: rejected ({exc.reason})")
            return "REJECTED"
        self._bind(rec, view)
        return "SENT"

    def _bind(self, rec: OrderRecord, view: dict) -> None:
        rec.order_id = view["order_id"]
        self.by_order_id[view["order_id"]] = rec.client_key

    def reconcile_unknown(self, venue: FakeVenue, client_key: str) -> str:
        """Resolve an UNKNOWN by asking the venue for the identity we sent."""
        rec = self.orders[client_key]
        if rec.state != "UNKNOWN":
            raise ValueError(f"{client_key} is {rec.state}, not UNKNOWN")
        view = venue.get_order_by_client_key(client_key)
        if view is not None:
            self._bind(rec, view)
            rec.state = "PENDING"
            return "FOUND"
        # The venue does not have it. Resending under the same key is the same intent, so
        # even if this second call is also ambiguous the venue can still only hold one.
        rec.state = "PENDING"
        return self._send(venue, rec)

    # ---------------------------------------------------------------- event intake

    def apply_event(self, ev: dict) -> str:
        event_type = ev.get("type")
        if event_type not in SOURCE:
            raise IllegalTransition(f"unknown event type: {event_type!r}")
        event_id = ev["event_id"]
        if event_id in self.seen_events:
            return "DUPLICATE"
        entity = ev.get("order_id") or ev["market_id"]
        key = (SOURCE[event_type], entity)
        if ev["seq"] <= self.watermark.get(key, 0):
            self.seen_events.add(event_id)
            return "STALE"

        if event_type == "MARKET_RESOLVED":
            self._on_resolved(ev)
        else:
            self._on_order_event(ev)

        self.watermark[key] = ev["seq"]
        self.seen_events.add(event_id)
        return "APPLIED"

    def apply_all(self, events) -> None:
        for ev in events:
            self.apply_event(ev)

    def _on_order_event(self, ev: dict) -> None:
        client_key = ev.get("client_key")
        rec = self.orders.get(client_key)
        if rec is None:
            raise IllegalTransition(
                f"order event for an intent this process never committed: {client_key!r}")
        if rec.order_id is None:
            self._bind(rec, {"order_id": ev["order_id"]})
        elif rec.order_id != ev["order_id"]:
            raise IllegalTransition(
                f"{client_key}: event carries {ev['order_id']}, intent is bound to "
                f"{rec.order_id}")
        if (rec.state, ev["type"]) not in LEGAL:
            raise IllegalTransition(f"({rec.state}, {ev['type']}) is not a legal pair")

        if ev["type"] == "ORDER_ACCEPTED":
            self._on_accepted(rec, ev)
        elif ev["type"] == "FILL":
            self._on_fill(rec, ev)
        else:
            rec.state = "CANCELLED"
            self._unwind(rec)

    def _on_accepted(self, rec: OrderRecord, ev: dict) -> None:
        venue_terms = (ev["outcome"], ev["side"], ev["qty"], ev["price_micro"])
        if venue_terms != (rec.outcome, rec.side, rec.qty, rec.price_micro):
            # The venue is the authority on what is resting. Re-hold against its terms rather
            # than against the intent, and say so.
            self.alerts.append(
                f"{rec.client_key}: venue terms {venue_terms} differ from the intent "
                f"{(rec.outcome, rec.side, rec.qty, rec.price_micro)}; re-holding")
            self._unwind(rec)
            rec.outcome, rec.side, rec.qty, rec.price_micro = venue_terms
            self._hold(rec)
        rec.state = "WORKING"

    def _on_fill(self, rec: OrderRecord, ev: dict) -> None:
        qty, price = ev["qty"], ev["price_micro"]
        if qty > rec.qty - rec.filled:
            raise IllegalTransition(
                f"{rec.client_key}: fill of {qty} exceeds remaining {rec.qty - rec.filled}")
        if rec.side == BUY:
            if price > rec.price_micro:
                self.alerts.append(f"{rec.client_key}: bought above the bid")
            # A late fill on an order whose hold was already released has nothing left to
            # release, so take the minimum rather than driving the reserve negative. The
            # obligation is real either way and comes out of available.
            slice_held = min(qty * rec.price_micro, rec.reserved_micro)
            if slice_held:
                self._release(self.collateral, slice_held)
                rec.reserved_micro -= slice_held
            self.available[self.collateral] -= qty * price
            self.positions[rec.outcome] += qty
        else:
            if price < rec.price_micro:
                self.alerts.append(f"{rec.client_key}: sold below the offer")
            unlock = min(qty, rec.locked_shares)
            self.locked[rec.outcome] -= unlock
            rec.locked_shares -= unlock
            self.positions[rec.outcome] -= qty
            self.available[self.collateral] += qty * price
        if self.available[self.collateral] < 0:
            self.alerts.append(
                f"{rec.client_key}: collateral went negative booking a fill the venue "
                f"already made")
        if self.positions[rec.outcome] < 0:
            self.alerts.append(
                f"{rec.client_key}: position on {rec.outcome} went negative, which this "
                f"venue cannot represent")
        self._book_fee(ev["fee"], qty, price, ev["liquidity"])
        rec.filled += qty
        if rec.filled == rec.qty:
            # A terminal state is not rewritten by a later event. An order the venue
            # cancelled stays CANCELLED even when a late fill completes it, because the
            # cancellation is a fact the venue stated and this fill is a correction to what
            # was already booked, not a new lifecycle.
            if rec.state == "WORKING":
                rec.state = "FILLED"
            self._unwind(rec)

    def _book_fee(self, fee: dict, qty: int, price_micro: int, liquidity: str) -> None:
        """Amount, rate and asset are three different things.

        The amount is what the venue charged. The rate is the parameter it says it used. The
        asset is which balance it came out of. Recomputing the amount from the rate is a
        check on the venue, not a substitute for reading it, and neither one tells you which
        balance to debit.
        """
        asset, amount, rate_ppm = fee["asset"], fee["amount_micro"], fee["rate_ppm"]
        if asset not in self.available:
            raise UnknownFeeAsset(
                f"fee charged in {asset!r}, which this process holds no balance for")
        expected_rate = rate_ppm_for(self.market, liquidity)
        if rate_ppm != expected_rate:
            self.alerts.append(
                f"fee rate {rate_ppm} ppm for a {liquidity} fill, metadata says {expected_rate}")
        expected_amount = expected_profit_fee_micro(self.market, qty, price_micro, rate_ppm)
        if amount != expected_amount:
            self.alerts.append(
                f"fee amount {amount} does not match {expected_amount} at {rate_ppm} ppm")
        self.available[asset] -= amount
        self.fees_paid[asset] += amount

    # ------------------------------------------------------------------ settlement

    def _on_resolved(self, ev: dict) -> None:
        market_id = ev["market_id"]
        if market_id in self.settled:
            # Credited exactly once. The watermark and the dedupe table above catch a
            # redelivery of the same message; this catches a resolution that arrives again
            # with a fresh identity, which is what a stream that renumbers on reconnect does.
            return
        numerators = ev["payout_numerators"]
        denominator = ev["payout_denominator"]
        outcomes = self.market["outcomes"]
        if len(numerators) != len(outcomes) or sum(numerators) != denominator:
            raise ValueError(f"payout vector {numerators}/{denominator} is not a distribution")

        still_open = [ck for ck, r in self.orders.items()
                      if r.state in ("PENDING", "WORKING", "UNKNOWN")]
        if still_open:
            self.alerts.append(f"market resolved with orders still open: {sorted(still_open)}")

        # Never credit from the pushed payload's view of what we hold, and never from local
        # state alone: re-read the position from its authority first. Ordering across the two
        # streams is not guaranteed, so a fill that has not reached this process yet is
        # already in the authority's number.
        held = self.authority.positions(market_id)
        for outcome in outcomes:
            if held.get(outcome, 0) != self.positions.get(outcome, 0):
                self.alerts.append(
                    f"position break at settlement on {outcome}: authority "
                    f"{held.get(outcome, 0)}, local {self.positions.get(outcome, 0)}")

        credited = 0
        for index, outcome in enumerate(outcomes):
            qty = held.get(outcome, 0)
            exact = qty * self.payout_micro * numerators[index]
            share = exact // denominator
            self.settlement_dust += Fraction(exact - share * denominator, denominator)
            credited += share
        self.available[self.collateral] += credited
        self.settlement_credit[market_id] = credited
        self.settled.add(market_id)

    # -------------------------------------------------------------- reconciliation

    def reconcile(self, market_id: str) -> list:
        """Compare this process against the authority, position by position.

        This is the comparison a deployment has to schedule. The example runs it from a test
        with a planted break; it does not ship a scheduler or an alert destination, and a
        reconciliation that nothing runs is not a control.
        """
        breaks = []
        held = self.authority.positions(market_id)
        for outcome in self.market["outcomes"]:
            ours = self.positions.get(outcome, 0)
            theirs = held.get(outcome, 0)
            if ours != theirs:
                breaks.append(Break(f"position:{outcome}", ours, theirs))
        return breaks

    def snapshot(self) -> dict:
        """Everything that decides an economic outcome, in a comparable shape."""
        return {
            "available": dict(sorted(self.available.items())),
            "reserved": dict(sorted(self.reserved.items())),
            "fees_paid": dict(sorted(self.fees_paid.items())),
            "positions": dict(sorted(self.positions.items())),
            "locked": dict(sorted(self.locked.items())),
            "settlement_credit": dict(sorted(self.settlement_credit.items())),
            "settlement_dust": str(self.settlement_dust),
            "orders": {ck: (r.state, r.filled, r.reserved_micro, r.locked_shares)
                       for ck, r in sorted(self.orders.items())},
        }


def rebuild(market: dict, authority, events, intents=None) -> SafeBot:
    """Rebuild state from the two logs, deterministically.

    There are two: the venue's event log, and this process's own intent log, which the venue
    never sends because it is state we own. They are merged on sequence, because an intent is
    committed before the call that produces the acceptance. Nothing else is read, no clock is
    consulted, and no iteration order over a set decides anything, so two rebuilds of the
    same pair of logs produce byte-identical snapshots.
    """
    bot = SafeBot(market=market, authority=authority)
    pending = sorted(INTENTS if intents is None else intents,
                     key=lambda i: (i["before_seq"], i["client_key"]))
    index = 0

    def commit(intent: dict) -> None:
        rec = OrderRecord(intent["client_key"], intent["outcome"], intent["side"],
                          intent["qty"], intent["price_micro"])
        bot.orders[intent["client_key"]] = rec
        bot._hold(rec)

    for ev in events:
        while index < len(pending) and pending[index]["before_seq"] <= ev["seq"]:
            commit(pending[index])
            index += 1
        bot.apply_event(ev)
    while index < len(pending):
        commit(pending[index])
        index += 1
    return bot


# The intent log for fixtures/script.json. `before_seq` is the sequence the intent was
# committed ahead of, which is the ordering `submit` produces and
# test_the_hold_exists_before_the_call_returns pins.
INTENTS = [
    {"before_seq": 1, "client_key": "ck-buy-yes-1", "outcome": "YES", "side": BUY,
     "qty": 100, "price_micro": 400000},
    {"before_seq": 4, "client_key": "ck-buy-no-1", "outcome": "NO", "side": BUY,
     "qty": 50, "price_micro": 550000},
    {"before_seq": 5, "client_key": "ck-sell-yes-1", "outcome": "YES", "side": SELL,
     "qty": 30, "price_micro": 700000},
]
