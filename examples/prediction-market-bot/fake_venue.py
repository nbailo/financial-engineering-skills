#!/usr/bin/env python3
"""An in-process fake prediction-market venue. No network, no credentials, no live mode.

What this is
------------
A stub venue that runs inside the test process. It exists so the example can exercise the
mechanics that go wrong on real venues without talking to one: an idempotent order key, a
response that does not prove the outcome, maker and taker fills, a fee charged in an asset
that is not the collateral asset, a result that exists before it is final, and a resolution
message that gets redelivered.

What this is not
----------------
It is not a model of any named venue and nothing in it is evidence about one. Every
identifier, rate and number below is invented. The cross-venue properties it does implement
(a market is a payout vector over an outcome set, short exposure is a purchase of the
complement, the fee is a function of expected profit rather than notional) are described in
skills/fin-exchange-integration/references/prediction-market-core.md.

Units
-----
Amounts are integers in micro-units of a named asset. There is no float anywhere in this
example. A price is micro-units of collateral per share, in (0, payout_per_share_micro).
Rates are integers in parts per million.

Fee asset
---------
This venue charges its fee in FPOINT and settles trades in FUSD. For fee purposes it defines
one micro-FPOINT as one micro-FUSD of expected profit. The two balances are still separate
and are never netted against each other. Amount, rate and asset are three different things
and every fee record here carries all three.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"

MICRO = 1_000_000
PPM = 1_000_000

# A fake key for a fake venue. It authenticates nothing: the counterparty is a Python object
# in this process. It is here so the signing seam is visible in the example rather than
# invented later against a real venue, whose scheme this does not claim to resemble.
FAKE_SIGNING_KEY = b"FAKE-KEY-in-process-stub-only-not-a-credential"

BUY, SELL = "BUY", "SELL"
MAKER, TAKER = "MAKER", "TAKER"


class VenueError(RuntimeError):
    pass


class Rejected(VenueError):
    """The venue answered, and the answer proves the order does not exist."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class AmbiguousResponse(VenueError):
    """The request was sent and no answer came back.

    UNKNOWN, never "did not happen". The order may be resting. The only correct next step is
    to ask the venue for the identity that was sent, never to send a new one.
    """


def canonical(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def sign_request(payload: dict, key: bytes = FAKE_SIGNING_KEY) -> str:
    return hmac.new(key, canonical(payload), hashlib.sha256).hexdigest()


def load_market(path: Path | None = None) -> dict:
    return json.loads((path or FIXTURES / "market.json").read_text(encoding="utf-8"))


@dataclass(frozen=True)
class OrderRequest:
    client_key: str
    market_id: str
    outcome: str
    side: str
    qty: int
    price_micro: int

    def payload(self) -> dict:
        return {
            "client_key": self.client_key,
            "market_id": self.market_id,
            "outcome": self.outcome,
            "side": self.side,
            "qty": self.qty,
            "price_micro": self.price_micro,
        }


def rate_ppm_for(market: dict, liquidity: str) -> int:
    fee = market["fee"]
    return fee["maker_rate_ppm"] if liquidity == MAKER else fee["taker_rate_ppm"]


def expected_profit_fee_micro(market: dict, qty: int, price_micro: int, rate_ppm: int) -> int:
    """fee = qty * rate * p * (1 - p) * payout, in micro-units of the fee asset.

    Integer arithmetic throughout, ceiled to the next micro-unit in the venue's favour. The
    fee is zero at both price bounds and is symmetric about the midpoint, which is the
    property that lets a buy of the complement cost the same as the sell it replaces.
    """
    payout = market["payout_per_share_micro"]
    numerator = qty * rate_ppm * price_micro * (payout - price_micro)
    denominator = PPM * payout
    return -(-numerator // denominator)


class FakeVenue:
    """The stub. Drive it from a test or from fixtures/script.json.

    `place_order` and `cancel` are the client-facing calls. `fill` and `resolve` are the test
    driver: they are how the scenario says "the book traded" without needing a matching engine.
    """

    def __init__(self, market: dict, faults: dict[str, str] | None = None) -> None:
        self.market = market
        # client_key -> one of "ambiguous_after_accept", "ambiguous_before_accept". Each
        # fires once, so the recovery path can then run against a venue that answers.
        self.faults = dict(faults or {})
        self.orders: dict[str, dict] = {}
        self.by_client_key: dict[str, str] = {}
        self.events: list[dict] = []
        self.positions_held: dict[str, int] = {o: 0 for o in market["outcomes"]}
        self.determination: dict | None = None
        self.resolution: dict | None = None
        self._next_order = 1

    @property
    def payout_micro(self) -> int:
        return self.market["payout_per_share_micro"]

    def _emit(self, event_type: str, **fields) -> dict:
        seq = len(self.events) + 1
        event = {"seq": seq, "event_id": f"ev-{seq:03d}", "type": event_type,
                 "market_id": self.market["market_id"], **fields}
        self.events.append(event)
        return event

    def _validate(self, req: OrderRequest) -> None:
        m = self.market
        if req.market_id != m["market_id"]:
            raise Rejected("UNKNOWN_MARKET")
        if self.resolution is not None:
            raise Rejected("MARKET_RESOLVED")
        if self.determination is not None:
            # A result exists. The book is shut even though the payout is not final yet.
            raise Rejected("MARKET_DETERMINED")
        if req.outcome not in m["outcomes"]:
            raise Rejected("UNKNOWN_OUTCOME")
        if req.side not in (BUY, SELL):
            raise Rejected("UNKNOWN_SIDE")
        if req.qty < m["min_size_shares"]:
            raise Rejected("BELOW_MIN_SIZE")
        if req.price_micro % m["tick_micro"] != 0:
            raise Rejected("OFF_TICK")
        if not 0 < req.price_micro < self.payout_micro:
            raise Rejected("PRICE_OUT_OF_RANGE")
        if req.side == SELL:
            # This venue cannot represent a short. A sell it cannot cover from inventory is
            # refused, not turned into a negative position. Acquiring that exposure means
            # buying the complement.
            resting = sum(o["qty"] - o["filled"] for o in self.orders.values()
                          if o["side"] == SELL and o["outcome"] == req.outcome
                          and o["state"] == "WORKING")
            if req.qty + resting > self.positions_held[req.outcome]:
                raise Rejected("INSUFFICIENT_POSITION")

    def place_order(self, req: OrderRequest, signature: str) -> dict:
        if not hmac.compare_digest(signature, sign_request(req.payload())):
            raise Rejected("BAD_SIGNATURE")

        # Idempotent on the client key. A resend of the same intent returns the same order and
        # emits no second acceptance. A different intent under a used key is refused outright,
        # because the venue cannot tell which one the caller meant.
        existing_id = self.by_client_key.get(req.client_key)
        if existing_id is not None:
            existing = self.orders[existing_id]
            if existing["request"] != req.payload():
                raise Rejected("CLIENT_KEY_REUSED_WITH_DIFFERENT_PAYLOAD")
            return self.view(existing_id)

        self._validate(req)

        fault = self.faults.pop(req.client_key, None)
        if fault == "ambiguous_before_accept":
            raise AmbiguousResponse("connection reset before the venue saw the order")

        order_id = f"ord-{self._next_order:03d}"
        self._next_order += 1
        self.orders[order_id] = {
            "order_id": order_id, "client_key": req.client_key, "outcome": req.outcome,
            "side": req.side, "qty": req.qty, "price_micro": req.price_micro,
            "filled": 0, "state": "WORKING", "request": req.payload(),
        }
        self.by_client_key[req.client_key] = order_id
        self._emit("ORDER_ACCEPTED", order_id=order_id, client_key=req.client_key,
                   outcome=req.outcome, side=req.side, qty=req.qty,
                   price_micro=req.price_micro)

        if fault == "ambiguous_after_accept":
            raise AmbiguousResponse("socket closed after the order was accepted")
        return self.view(order_id)

    def view(self, order_id: str) -> dict:
        o = self.orders[order_id]
        return {k: o[k] for k in
                ("order_id", "client_key", "outcome", "side", "qty", "price_micro",
                 "filled", "state")}

    def get_order_by_client_key(self, client_key: str) -> dict | None:
        """The query that resolves an ambiguous submission. Ask for the identity you sent."""
        order_id = self.by_client_key.get(client_key)
        return None if order_id is None else self.view(order_id)

    def cancel(self, client_key: str) -> dict:
        order_id = self.by_client_key.get(client_key)
        if order_id is None:
            raise Rejected("UNKNOWN_CLIENT_KEY")
        o = self.orders[order_id]
        if o["state"] != "WORKING":
            raise Rejected("NOT_WORKING")
        o["state"] = "CANCELLED"
        self._emit("ORDER_CANCELLED", order_id=order_id, client_key=client_key,
                   remaining=o["qty"] - o["filled"])
        return self.view(order_id)

    def fill(self, client_key: str, qty: int, price_micro: int, liquidity: str) -> dict:
        """Test driver: the book traded against a resting or crossing order."""
        order_id = self.by_client_key[client_key]
        o = self.orders[order_id]
        if qty > o["qty"] - o["filled"]:
            raise Rejected("FILL_EXCEEDS_REMAINING")
        o["filled"] += qty
        if o["filled"] == o["qty"] and o["state"] == "WORKING":
            o["state"] = "FILLED"
        signed = qty if o["side"] == BUY else -qty
        self.positions_held[o["outcome"]] += signed
        rate_ppm = rate_ppm_for(self.market, liquidity)
        fee_micro = expected_profit_fee_micro(self.market, qty, price_micro, rate_ppm)
        return self._emit(
            "FILL", order_id=order_id, client_key=client_key, outcome=o["outcome"],
            side=o["side"], qty=qty, price_micro=price_micro, liquidity=liquidity,
            fee={"amount_micro": fee_micro, "rate_ppm": rate_ppm,
                 "asset": self.market["fee"]["asset"]})

    def _check_vector(self, payout_numerators: list[int], payout_denominator: int) -> None:
        if len(payout_numerators) != len(self.market["outcomes"]):
            raise Rejected("PAYOUT_VECTOR_WRONG_LENGTH")
        if sum(payout_numerators) != payout_denominator:
            raise Rejected("PAYOUT_VECTOR_DOES_NOT_SUM_TO_ONE")

    def determine(self, payout_numerators: list[int], payout_denominator: int) -> dict:
        """A result exists and can still change. This is not the payout.

        Trading stops, a result exists, and the result stops being revisable. Those are three
        instants on the venues this stands in for, and only the last one pays. The event says
        `provisional` so that a client cannot read it as terminal, and this venue has no path
        that pays here and takes it back later.
        """
        if self.resolution is not None:
            raise Rejected("MARKET_RESOLVED")
        self._check_vector(payout_numerators, payout_denominator)
        self.determination = {"payout_numerators": list(payout_numerators),
                              "payout_denominator": payout_denominator}
        return self._emit("MARKET_DETERMINED", provisional=True, **self.determination)

    def resolve(self, payout_numerators: list[int], payout_denominator: int) -> dict:
        """The terminal payout state. Winner-take-all is [1, 0] over 1. A split is [1, 1] over 2.

        The payout is a vector, so a split market has no winning index and the field is null.
        Code that reads only the index credits nothing on exactly the case where reading the
        vector would have credited half. Nothing in this venue undoes what this state pays.
        """
        self._check_vector(payout_numerators, payout_denominator)
        winners = [i for i, n in enumerate(payout_numerators) if n == payout_denominator]
        self.resolution = {"payout_numerators": list(payout_numerators),
                           "payout_denominator": payout_denominator,
                           "winning_outcome_index": winners[0] if winners else None}
        return self._emit("MARKET_RESOLVED", **self.resolution)

    def positions(self, market_id: str) -> dict[str, int]:
        """The authority read. What the venue says you hold, not what your log implies."""
        if market_id != self.market["market_id"]:
            raise Rejected("UNKNOWN_MARKET")
        return dict(self.positions_held)

    def events_since(self, seq: int = 0) -> list[dict]:
        """A reconnect. The venue redelivers from a cursor and does not renumber."""
        return [json.loads(json.dumps(e)) for e in self.events if e["seq"] > seq]

    def settlement_events(self) -> list[dict]:
        return [json.loads(json.dumps(e)) for e in self.events
                if e["type"] == "MARKET_RESOLVED"]


def run_script(venue: FakeVenue, script: list[dict]) -> list[dict]:
    """Replay a frozen operator script through the venue. Used to build and to check fixtures."""
    for step in script:
        action = step["action"]
        if action == "place":
            req = OrderRequest(step["client_key"], venue.market["market_id"], step["outcome"],
                               step["side"], step["qty"], step["price_micro"])
            venue.place_order(req, sign_request(req.payload()))
        elif action == "fill":
            venue.fill(step["client_key"], step["qty"], step["price_micro"], step["liquidity"])
        elif action == "cancel":
            venue.cancel(step["client_key"])
        elif action == "resolve":
            venue.resolve(step["payout_numerators"], step["payout_denominator"])
        else:
            raise ValueError(f"unknown script action: {action}")
    return venue.events_since(0)


def load_script(name: str = "script.json") -> list[dict]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def load_session(name: str = "session.jsonl") -> list[dict]:
    text = (FIXTURES / name).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def build_session() -> tuple[FakeVenue, list[dict]]:
    """The scenario every test and the demo share: one venue, one frozen event log."""
    venue = FakeVenue(load_market())
    events = run_script(venue, load_script())
    return venue, events


def _write_fixture() -> None:
    _, events = build_session()
    lines = [json.dumps(e, sort_keys=True, separators=(",", ":")) for e in events]
    (FIXTURES / "session.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {len(lines)} events to fixtures/session.jsonl")


if __name__ == "__main__":
    _write_fixture()
