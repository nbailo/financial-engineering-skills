#!/usr/bin/env python3
"""The counter-example. Do not copy any of it.

Every design note in this file is false, and tests/test_unsafe_bot_is_wrong.py names the
test that proves each one false. It is written the way this code actually gets written: by
somebody porting a working model from a margin venue, where each of these habits was correct.
None of them raises an exception here. They produce wrong numbers quietly.

The five, and where they are refuted
------------------------------------
1. "Collateral is committed when a trade happens, so there is nothing to hold while an order
   rests."                              refuted by test_resting_orders_are_not_reserved
2. "A sell is a short, and a short reserves what it sells."
                                        refuted by test_short_reserve_is_the_wrong_side
3. "A repeated response is one we already handled, so booking it again is harmless."
                                        refuted by test_settlement_is_credited_twice
4. "The fee is one number at one rate."  refuted by test_fee_is_taken_from_the_wrong_asset
5. "A resolved market has a winner."     refuted by test_split_market_credits_nothing
"""
from __future__ import annotations

from dataclasses import dataclass, field

from fake_venue import (BUY, MICRO, AmbiguousResponse, FakeVenue, OrderRequest,
                        expected_profit_fee_micro, sign_request)

DEFAULT_BALANCES = {"FUSD": 1000 * MICRO, "FPOINT": 100 * MICRO}

# The rate is a constant in the source instead of a field on the fill, so a maker fill is
# charged a taker rate and a rate change ships as a code change.
TAKER_RATE_PPM = 70000


@dataclass
class UnsafeBot:
    market: dict
    available: dict = field(default_factory=lambda: dict(DEFAULT_BALANCES))
    positions: dict = field(default_factory=dict)
    orders: dict = field(default_factory=dict)
    fee_paid: int = 0
    settlement_credited: int = 0
    retries: list = field(default_factory=list)

    def __post_init__(self) -> None:
        for outcome in self.market["outcomes"]:
            self.positions.setdefault(outcome, 0)

    @property
    def collateral(self) -> str:
        return self.market["collateral_asset"]

    @property
    def payout_micro(self) -> int:
        return self.market["payout_per_share_micro"]

    def collateral_obligation(self, side: str, qty: int, price_micro: int) -> int:
        # One signed quantity per market, so a sell is a short and a short reserves what it
        # sold. On a book where the complement is a separate instrument this is the other
        # side of the market.
        return qty * price_micro

    def submit(self, venue: FakeVenue, client_key: str, outcome: str, side: str,
               qty: int, price_micro: int) -> str:
        # The call comes first and nothing durable exists before it, so a lost response has
        # nothing to reconcile against and a fresh key is the only way forward.
        req = OrderRequest(client_key, self.market["market_id"], outcome, side, qty,
                           price_micro)
        try:
            view = venue.place_order(req, sign_request(req.payload()))
        except AmbiguousResponse:
            retry_key = f"{client_key}-retry1"
            self.retries.append(retry_key)
            retry = OrderRequest(retry_key, self.market["market_id"], outcome, side, qty,
                                 price_micro)
            view = venue.place_order(retry, sign_request(retry.payload()))
        self.orders[view["client_key"]] = {"state": "SENT", "outcome": outcome, "side": side,
                                           "qty": qty, "price_micro": price_micro,
                                           "filled": 0}
        return "SENT"

    def apply_event(self, ev: dict) -> str:
        # The gateway drops repeats, so there is no dedupe here and no watermark. Anything
        # unrecognised is a message for a feature we do not use, so it is skipped.
        handler = {"ORDER_ACCEPTED": self._on_accepted, "FILL": self._on_fill,
                   "ORDER_CANCELLED": self._on_cancelled,
                   "MARKET_RESOLVED": self._on_resolved}.get(ev.get("type"))
        if handler is None:
            return "IGNORED"
        handler(ev)
        return "APPLIED"

    def apply_all(self, events) -> None:
        for ev in events:
            self.apply_event(ev)

    def _record(self, ev: dict) -> dict:
        return self.orders.setdefault(ev.get("client_key"), {
            "state": "SENT", "outcome": ev.get("outcome"), "side": ev.get("side"),
            "qty": ev.get("qty", 0), "price_micro": ev.get("price_micro", 0), "filled": 0})

    def _on_accepted(self, ev: dict) -> None:
        self._record(ev)["state"] = "WORKING"

    def _on_cancelled(self, ev: dict) -> None:
        self._record(ev)["state"] = "CANCELLED"

    def _on_fill(self, ev: dict) -> None:
        record = self._record(ev)
        qty, price = ev["qty"], ev["price_micro"]
        if ev["side"] == BUY:
            self.available[self.collateral] -= qty * price
            self.positions[ev["outcome"]] += qty
        else:
            self.available[self.collateral] += qty * price
            self.positions[ev["outcome"]] -= qty
        record["filled"] += qty
        # One fee, one rate, one balance.
        fee = expected_profit_fee_micro(self.market, qty, price, TAKER_RATE_PPM)
        self.available[self.collateral] -= fee
        self.fee_paid += fee

    def _on_resolved(self, ev: dict) -> None:
        # A resolved market has a winner, and the payout is the winner's position.
        index = ev.get("winning_outcome_index")
        if index is None:
            return
        outcome = self.market["outcomes"][index]
        credit = self.positions[outcome] * self.payout_micro
        self.available[self.collateral] += credit
        self.settlement_credited += credit

    def snapshot(self) -> dict:
        return {
            "available": dict(sorted(self.available.items())),
            "positions": dict(sorted(self.positions.items())),
            "fee_paid": self.fee_paid,
            "settlement_credited": self.settlement_credited,
            "orders": {k: (v["state"], v["filled"]) for k, v in sorted(self.orders.items())},
        }
