# A Binance spot bot that buys and rests a take-profit

Two hundred lines that buy a fixed notional of a symbol at the touch and rest a limit sell one percent above
the entry. This is the shape of code that gets written on a Thursday afternoon by someone who has read the
Binance docs once and is shipping under time pressure. That is the point: the code is mostly right, and
the four things wrong with it are the four that cost money.

---

## Before

```python
#!/usr/bin/env python3
"""
scalper.py: buy a fixed notional at the touch, then rest a take-profit sell.

    python scalper.py --symbol BTCUSDT --notional 200 --take-profit 0.01

Design notes
------------
1. Order IDs. Every order gets a clientOrderId and we store it, so if we crash between the
   POST and the response we can find the order again by ID instead of guessing from balances.
2. Retries. newClientOrderId is unique on Binance (a duplicate submit comes back -2010
   "Duplicate order sent." rather than creating a second order), so retrying a timed-out
   create is safe.
3. Filters. Price is snapped to tickSize and quantity to stepSize before every send.
4. Fees. Binance spot is 0.1%/side by default. The target does *not* include a fee markup,
   so a nominal 1% take-profit realises a bit less than 1%; widen --take-profit if it matters.
"""
import argparse
import logging
import os
import sqlite3
import time

import requests
from binance.spot import Spot
from binance.error import ClientError, ServerError

log = logging.getLogger("scalper")

db = sqlite3.connect("scalper.db")
db.execute(
    "CREATE TABLE IF NOT EXISTS orders ("
    " client_order_id TEXT PRIMARY KEY, symbol TEXT, side TEXT,"
    " qty REAL, price REAL, status TEXT)"
)
db.commit()


def load_filters(client, symbol):
    info = client.exchange_info(symbol=symbol)
    f = {x["filterType"]: x for x in info["symbols"][0]["filters"]}
    return {
        "tick": float(f["PRICE_FILTER"]["tickSize"]),
        "step": float(f["LOT_SIZE"]["stepSize"]),
        "min_notional": float(f["NOTIONAL"]["minNotional"]),
    }


def snap(value, increment):
    """Snap `value` down to the nearest multiple of `increment`."""
    return round(value - (value % increment), 8)


def best_bid(client, symbol):
    book = client.book_ticker(symbol=symbol)
    return float(book["bidPrice"])


def place_buy(client, symbol, notional, filters):
    price = snap(best_bid(client, symbol), filters["tick"])
    qty = snap(notional / price, filters["step"])
    if qty * price < filters["min_notional"]:
        raise SystemExit(f"{qty} @ {price} is below minNotional {filters['min_notional']}")

    req = dict(
        symbol=symbol,
        side="BUY",
        type="LIMIT",
        timeInForce="GTC",
        quantity=qty,
        price=price,
    )
    try:
        resp = client.new_order(**req)
    except (requests.Timeout, requests.ConnectionError, ServerError) as e:
        log.warning("buy submit failed (%s), retrying once", e)
        resp = client.new_order(**req)

    cid = resp["clientOrderId"]
    db.execute(
        "INSERT OR REPLACE INTO orders VALUES (?,?,?,?,?,?)",
        (cid, symbol, "BUY", qty, price, resp["status"]),
    )
    db.commit()
    log.info("buy %s placed: %s %s @ %s", cid, qty, symbol, price)
    return resp


def wait_for_fill(client, symbol, cid, timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        o = client.get_order(symbol=symbol, origClientOrderId=cid)
        if o["status"] == "FILLED":
            return o
        if o["status"] in ("CANCELED", "REJECTED", "EXPIRED"):
            raise SystemExit(f"buy {cid} ended {o['status']}")
        time.sleep(1)
    raise SystemExit(f"buy {cid} did not fill in {timeout}s")


def place_take_profit(client, symbol, buy, target_pct, filters):
    entry = float(buy["price"])
    tp = snap(entry * (1 + target_pct), filters["tick"])
    qty = snap(float(buy["executedQty"]), filters["step"])

    req = dict(
        symbol=symbol,
        side="SELL",
        type="LIMIT",
        timeInForce="GTC",
        quantity=qty,
        price=tp,
    )
    try:
        resp = client.new_order(**req)
    except (requests.Timeout, requests.ConnectionError, ServerError) as e:
        log.warning("sell submit failed (%s), retrying once", e)
        resp = client.new_order(**req)

    db.execute(
        "INSERT OR REPLACE INTO orders VALUES (?,?,?,?,?,?)",
        (resp["clientOrderId"], symbol, "SELL", qty, tp, resp["status"]),
    )
    db.commit()
    log.info("take-profit %s placed: %s @ %s", resp["clientOrderId"], qty, tp)
    return resp


def main():
    logging.basicConfig(level=logging.INFO)
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--notional", type=float, default=200.0)
    p.add_argument("--take-profit", type=float, default=0.01)
    args = p.parse_args()

    client = Spot(api_key=os.environ["BINANCE_KEY"], api_secret=os.environ["BINANCE_SECRET"])
    filters = load_filters(client, args.symbol)

    # TODO: on startup, reconcile our open orders against the exchange before placing
    # anything new, otherwise a crash mid-cycle leaves an orphan resting order.

    buy = place_buy(client, args.symbol, args.notional, filters)
    buy = wait_for_fill(client, args.symbol, buy["clientOrderId"])
    place_take_profit(client, args.symbol, buy, args.take_profit, filters)


if __name__ == "__main__":
    main()
```

---

## What the suite catches

| Defect | Rule | What actually happens | Loss shape |
|---|---|---|---|
| `except Timeout: ... retry` around `new_order` | `fin-money-core`: *operation identity*, *ambiguous outcomes*. `fin-exchange-integration`: *A response that does not prove the outcome is UNKNOWN, and the answer is a query* | A timeout means the request may have reached the matching engine and the response was lost. It is `UNKNOWN`, not "did not happen". The retry sends a second, unconditioned buy. | Double position at double notional, one leg invisible to the bot's own sizing. Unbounded if the loop is longer than one retry. |
| `cid = resp["clientOrderId"]`, read *after* the send | `fin-money-core`: *operation identity*. `fin-exchange-integration`: *A client order ID correlates; it deduplicates only where the venue documents that* | The correlation key is minted by the venue and only exists once the response arrives. On the exact failure it is needed for, there is nothing to query by. Nothing durable is written before the first byte goes out. | Total loss of the recovery path: the bot cannot tell "never placed" from "placed and filled". |
| Design note 2: "newClientOrderId is unique … retrying is safe" | `fin-exchange-integration`: *A client order ID correlates; it deduplicates only where the venue documents that*. `fin-money-core`: *A comment is a claim*. `fin-verification`: *The design notes are a numbered list of claims, each bound to a test or deleted* | On Binance spot and futures the client order ID is unique **only among open orders**. Once the first order fills or is cancelled, the ID is free again and the resend creates a genuine second order. `-2010` never fires. The comment is what let this survive review. | The duplicate above, plus false confidence: the assertion is load-bearing for the retry and is false. |
| `snap()` runs `value % increment` on floats | `fin-money-core`: *exact representation* | `0.29 % 0.01 == 0.009999999999999974`, so `snap(0.29, 0.01)` returns **0.28**, a whole step below the intended value. `int(0.29/0.01) == 28` fails identically. Rounding "down to the nearest tick" silently drops a full tick whenever the value is not exactly representable in binary. | One tick or one step per order, always in the same direction. On quantity it also risks tripping `minNotional` and being rejected outright. |
| `entry = float(buy["price"])` for the take-profit | `fin-exchange-integration`: *The round-trip gross-up, and the quantization direction* | The target is computed from the price the bot *asked for*, not from `cummulativeQuoteQty / executedQty`, and it is never grossed up for the round-trip commission. A `+1%` target at 0.1% taker fees per side realises **+0.798%**. The sell quantity is separately shaved by the base-asset commission with no compensating price increase. | Persistent bleed: ~20% of the intended edge, on 100% of round trips, always understating cost. |
| `# TODO: on startup, reconcile our open orders …` | `fin-money-core`: *Implemented, not described* | A named risk written as a comment is the same defect as the missing control: the right control is identified and described accurately, then written as prose instead of built. | Whatever the un-built control was worth. Here: an orphan resting order that keeps filling. |

---

## After

```python
#!/usr/bin/env python3
"""
scalper.py: buy a fixed notional at the touch, then rest a take-profit sell whose
price covers the round-trip commission.

    python scalper.py --symbol BTCUSDT --notional 200 --take-profit 0.01

Three phases per external call: COMMIT the intent, make the call, record the outcome.
A timeout is UNKNOWN and is resolved by querying the client order ID we minted, never
by resending. Every price and quantity is an obligation and is held in Decimal.
"""
import argparse
import json
import logging
import os
import sqlite3
import time
import uuid
from decimal import Decimal, ROUND_DOWN, ROUND_UP, localcontext

import requests
from binance.spot import Spot
from binance.error import ClientError, ServerError

log = logging.getLogger("scalper")

# Binance error codes that document the request was NOT accepted. Everything else,
# including every transport failure, is UNKNOWN. Every failure signal carries a class:
# a path is DEFINITE-NO only where the venue documents, for that exact code, that the
# request was not enqueued.
DEFINITE_NO = {-1013, -1021, -1100, -1102, -1111, -2010}

# A ceiling that warns is not a control: the risk gate is a config key with no
# default. Import fails if unset.
ALERT_SINK = os.environ["SCALPER_ALERT_SINK"]


class Unresolved(Exception):
    """Submit outcome is UNKNOWN after the full query ladder. Do not resubmit."""


class NothingFilled(Exception):
    """No legal value doubles as "unset": an absent quantity is an error, not Decimal(0)."""


# --------------------------------------------------------------------------- state

db = sqlite3.connect("scalper.db", isolation_level=None)  # explicit BEGIN/COMMIT
db.executescript(
    """
    CREATE TABLE IF NOT EXISTS order_intents (
        client_order_id TEXT PRIMARY KEY,
        symbol          TEXT NOT NULL,
        side            TEXT NOT NULL,
        request_json    TEXT NOT NULL,   -- the exact bytes; replayed verbatim, never rebuilt
        state           TEXT NOT NULL,   -- INFLIGHT | ACCEPTED | REJECTED | INFLIGHT_UNKNOWN
        venue_status    TEXT,
        executed_qty    TEXT,
        cum_quote_qty   TEXT,
        sent_at         REAL NOT NULL
    );
    """
)


def commit_intent(cid, symbol, side, body):
    db.execute("BEGIN IMMEDIATE")
    db.execute(
        "INSERT INTO order_intents (client_order_id, symbol, side, request_json,"
        " state, sent_at) VALUES (?,?,?,?, 'INFLIGHT', ?)",
        (cid, symbol, side, json.dumps(body, sort_keys=True), time.time()),
    )
    db.execute("COMMIT")  # phase 1 ends on disk, before the first byte goes out


def record_outcome(cid, order):
    db.execute("BEGIN IMMEDIATE")
    db.execute(
        "UPDATE order_intents SET state='ACCEPTED', venue_status=?, executed_qty=?,"
        " cum_quote_qty=? WHERE client_order_id=?",
        (order["status"], order["executedQty"], order["cummulativeQuoteQty"], cid),
    )
    db.execute("COMMIT")


# ----------------------------------------------------------------------- quantities


def quantize(value: Decimal, increment: Decimal, rounding) -> Decimal:
    """The single named call that turns an estimate into an obligation.

    Exact by construction: no binary float is ever an operand. `%` on floats is the
    measured defect: 0.29 % 0.01 == 0.009999999999999974.
    """
    return (value / increment).to_integral_value(rounding=rounding) * increment


def load_filters(client, symbol):
    """Filters come from the live venue and are held as Decimal from the string."""
    info = client.exchange_info(symbol=symbol)
    f = {x["filterType"]: x for x in info["symbols"][0]["filters"]}
    return {
        "tick": Decimal(f["PRICE_FILTER"]["tickSize"]),
        "step": Decimal(f["LOT_SIZE"]["stepSize"]),
        "min_notional": Decimal(f["NOTIONAL"]["minNotional"]),
        "base": info["symbols"][0]["baseAsset"],
    }


def wire(d: Decimal) -> str:
    """str(1e-05) == '1e-05' reaches the wire as illegal characters. Decimal does not."""
    return format(d.normalize(), "f")


# ---------------------------------------------------------------------- the send path


def submit(client, symbol, side, qty: Decimal, price: Decimal):
    """Durable intent before the external effect: commit, call, record the outcome."""
    cid = "fes-" + uuid.uuid4().hex[:28]  # <= 36 chars, matches Binance's charset
    body = dict(
        symbol=symbol,
        side=side,
        type="LIMIT",
        timeInForce="GTC",
        quantity=wire(qty),
        price=wire(price),
        newClientOrderId=cid,
    )
    commit_intent(cid, symbol, side, body)

    try:
        resp = client.new_order(**body)
    except ClientError as e:
        if e.error_code in DEFINITE_NO:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "UPDATE order_intents SET state='REJECTED', venue_status=?"
                " WHERE client_order_id=?",
                (str(e.error_code), cid),
            )
            db.execute("COMMIT")
            raise
        return resolve(client, symbol, cid)  # not a documented no ⇒ UNKNOWN
    except (requests.Timeout, requests.ConnectionError, ServerError):
        return resolve(client, symbol, cid)  # transport failure ⇒ UNKNOWN

    record_outcome(cid, resp)
    return resp


def resolve(client, symbol, cid, rungs=6):
    """The ambiguous-response ladder. Query by the identity we minted. Never resend.

    -2013 NO_SUCH_ORDER immediately after a submit is not proof of non-creation: Binance
    documents three data sources (Matching Engine / Memory / Database) with different
    staleness and asynchronous propagation between them.
    """
    for i in range(rungs):
        try:
            order = client.get_order(symbol=symbol, origClientOrderId=cid)
            record_outcome(cid, order)
            log.info("resolved %s by query: %s", cid, order["status"])
            return order
        except ClientError as e:
            if e.error_code != -2013:
                raise
        time.sleep(0.5 * 2**i)

    # Next rungs: open orders, then history. Binance's allOrders window is bounded,
    # check references/venues/binance.md for the current bound before relying on it.
    for order in client.get_open_orders(symbol=symbol) + client.get_orders(
        symbol=symbol, limit=1000
    ):
        if order["clientOrderId"] == cid:
            record_outcome(cid, order)
            return order

    db.execute("BEGIN IMMEDIATE")
    db.execute(
        "UPDATE order_intents SET state='INFLIGHT_UNKNOWN' WHERE client_order_id=?", (cid,)
    )
    db.execute("COMMIT")
    alert(f"{cid} unresolved after full ladder; risk gate closed for {symbol}")
    raise Unresolved(cid)


def resume(client):
    """Every field written ahead of the effect is read by the recovery path.

    Called before anything is placed. Without this, the persisted client order ID is
    write-only and the journal is decoration.
    """
    rows = db.execute(
        "SELECT client_order_id, symbol FROM order_intents"
        " WHERE state IN ('INFLIGHT','INFLIGHT_UNKNOWN')"
    ).fetchall()
    for cid, symbol in rows:
        log.info("resuming unresolved intent %s", cid)
        try:
            resolve(client, symbol, cid)
        except Unresolved:
            # The risk-reducing action for a bot that holds no other position is
            # to refuse to start. It is not to place another order.
            raise SystemExit(f"{cid} unresolved; refusing to trade {symbol}")


def alert(message):
    log.error("ALERT %s: %s", ALERT_SINK, message)
    requests.post(ALERT_SINK, json={"text": message}, timeout=5)


# ------------------------------------------------------------------------ the strategy


def place_buy(client, symbol, notional: Decimal, filters):
    book = client.book_ticker(symbol=symbol)
    price = quantize(Decimal(book["bidPrice"]), filters["tick"], ROUND_DOWN)
    qty = quantize(notional / price, filters["step"], ROUND_DOWN)
    if qty == 0:
        raise SystemExit(f"{notional} at {price} quantizes to zero on step {filters['step']}")
    if qty * price < filters["min_notional"]:
        raise SystemExit(f"{qty} @ {price} is below minNotional {filters['min_notional']}")
    return submit(client, symbol, "BUY", qty, price)


def take_profit_order(buy, target: Decimal, fee_in: Decimal, fee_out: Decimal, filters):
    """A cost paid in a third unit: gross up before you quantize, and quantize toward
    the target-preserving side."""
    filled = Decimal(buy["executedQty"])
    quote = Decimal(buy["cummulativeQuoteQty"])
    if filled == 0:
        raise NothingFilled(buy["clientOrderId"])

    with localcontext() as ctx:
        ctx.prec = 34  # declared, not inherited from whatever ran before us
        vwap = quote / filled  # the executed basis, not the price we asked for
        target_px = vwap * (1 + target) * (1 + fee_in) * (1 + fee_out)

    price = quantize(target_px, filters["tick"], ROUND_UP)  # sell target: up, never nearest

    # A base-asset commission shrinks what we can actually sell.
    base_fee = sum(
        (Decimal(f["commission"]) for f in buy.get("fills", ())
         if f["commissionAsset"] == filters["base"]),
        Decimal(0),
    )
    qty = quantize(filled - base_fee, filters["step"], ROUND_DOWN)
    return qty, price


def main():
    logging.basicConfig(level=logging.INFO)
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--notional", type=Decimal, default=Decimal("200"))
    p.add_argument("--take-profit", type=Decimal, default=Decimal("0.01"))
    p.add_argument("--fee", type=Decimal, default=Decimal("0.001"))
    args = p.parse_args()

    client = Spot(api_key=os.environ["BINANCE_KEY"], api_secret=os.environ["BINANCE_SECRET"])
    filters = load_filters(client, args.symbol)

    resume(client)  # the TODO, implemented

    buy = place_buy(client, args.symbol, args.notional, filters)
    while buy["status"] != "FILLED":
        time.sleep(1)
        buy = client.get_order(symbol=args.symbol, origClientOrderId=buy["clientOrderId"])
        if buy["status"] in ("CANCELED", "REJECTED", "EXPIRED"):
            raise SystemExit(f"buy ended {buy['status']}")
    record_outcome(buy["clientOrderId"], buy)

    # GET /api/v3/order carries no `fills`. The commission lives on the trade records,
    # so read them from the venue rather than inferring a fee we did not observe.
    buy["fills"] = client.my_trades(symbol=args.symbol, orderId=buy["orderId"])

    qty, price = take_profit_order(buy, args.take_profit, args.fee, args.fee, filters)
    submit(client, args.symbol, "SELL", qty, price)


if __name__ == "__main__":
    main()
```

And the test that makes the first defect a regression rather than an opinion. `fin-exchange-integration`
requires the first of its five properties proved in the repository's own framework: an instruction whose
response is lost creates no duplicate economic effect. A description or a pointer to a test plan is the
missing control, not a plan for it.

```python
# tests/test_ambiguous_submit.py
def test_timeout_that_already_filled(bot, venue):
    """The request IS delivered upstream, then the connection dies. Toxiproxy `timeout`
    toxic semantics: 'stops all data from getting through, and closes the connection'."""
    venue.deliver_upstream_then_fail("POST /api/v3/order", after_ms=50)

    order = bot.place_buy("BTCUSDT", Decimal("200"), bot.filters)

    assert venue.post_count("/api/v3/order") == 1          # MUST NOT resubmit
    assert venue.queried_by_client_id(order["clientOrderId"])  # MUST query the minted id
    assert len(venue.orders("BTCUSDT")) == 1               # exactly one order exists


def test_timeout_that_did_not_arrive(bot, venue):
    """The mirror case: the request never reached the venue. Only the harness knows
    that, so the production path still ends UNKNOWN, closes the gate and resends
    nothing. Harness knowledge is not a licence to retry."""
    venue.drop_before_delivery("POST /api/v3/order")

    with pytest.raises(Unresolved):
        bot.place_buy("BTCUSDT", Decimal("200"), bot.filters)
    assert venue.post_count("/api/v3/order") == 1          # still no resubmit
    assert bot.intent_state_is("INFLIGHT_UNKNOWN")         # and the gate is closed
```

---

## What changed, and what did not

**Changed.** Six things: the intent row and its client order ID are committed before the socket write; the
timeout branch queries instead of resending; the recovery path reads the field the send path wrote;
`Decimal` replaces float in the two functions that produce numbers the venue sees; the take-profit basis
moves from the requested price to the executed VWAP and is grossed up for both commission legs before
quantization; and the reconciliation TODO becomes `resume()`.

**Not changed, deliberately.** The strategy is untouched: buy the bid, rest a target above it. The
polling fill loop was not replaced with a user-data websocket; the bot still holds one position at a time
with no position store; there is no dead-man switch, because a single non-restarting script that exits
after placing one order is not the case *Your absence is the venue's job to notice, and your return does
not restore the range* is about, and adding cancel-on-disconnect here would be
ceremony. `minNotional` handling, the `PARTIALLY_FILLED` branch, `recvWindow` skew and `429`/`418`
backoff were already correct in the original and were left alone, which is why the suite spends no words
on them.

**Not changed, and it should worry you.** There is still no scheduled reconciliation of position and
realized PnL against the venue, and no funding, ADL or settlement ingestion, because spot has none. If
this bot grows a position store, *The venue's number is the record; yours is a hypothesis until it is
compared* becomes the most important rule on this page and the `resume()` call is not a substitute for it.

---

## The output the review ends with

Binance holds the record and answers any question about it, so authority is EXTERNAL and reconciliation
against the venue is the primary proof. There is no customer on a position row, no payout path and one
venue adapter, so exposure stays `own`. At `own`, `fin-verification`'s *At exposure `own`, five tests and
one scheduled comparison carry the risk* sets the whole evidence bar, and no fuller venue contract is
emitted: `fin-exchange-integration` asks for that only when exposure is `customer` or `record`, or when a
second venue adapter appears.

`EVIDENCE` names functions rather than lines, because the code under review is a listing in this file. In a
real response every one of them is a `file:line`.

```
authority: EXTERNAL (Binance) · exposure: own

FINDING   A timed-out buy is sent a second time, leaving twice the intended position at twice the
          notional, with one leg invisible to the bot's own sizing. Unbounded if the retry loop is
          longer than one attempt.
WHY       The except clause treats a transport failure as "did not happen". The request may have reached
          the matching engine with only the response lost, which is UNKNOWN, and the resend is
          unconditioned.
EVIDENCE  scalper.py place_buy(), the bare retry inside the except clause; scalper.py
          place_take_profit(), the same shape on the sell
FIX       Classify the signal, and resolve UNKNOWN by querying the identity already sent rather than by
          sending again: scalper.py submit() and scalper.py resolve(), with DEFINITE_NO restricted to the
          codes Binance documents as not enqueued.
TEST      A submit whose request is delivered and whose response is then lost leaves exactly one order,
          one POST and a query by the minted client order ID: tests/test_ambiguous_submit.py
          test_timeout_that_already_filled.

FINDING   On the exact failure the recovery path exists for, the bot cannot tell "never placed" from
          "placed and filled", so the recovery path is not merely broken but absent.
WHY       `cid = resp["clientOrderId"]` is read after the send. The correlation key is minted by the venue
          and exists only once the response arrives, and nothing durable is written before the first byte
          goes out.
EVIDENCE  scalper.py place_buy(), the `cid = resp["clientOrderId"]` assignment and the INSERT after it
FIX       Mint `fes-<uuid4>` from the intent instance and commit the intent row, with the exact request
          bytes, before the socket write: scalper.py submit() and scalper.py commit_intent(); the retry
          replays `request_json` verbatim rather than rebuilding it.
TEST      Every field written ahead of the effect is read back by the recovery path: a process killed
          between the intent commit and the response restarts into scalper.py resume() and resolves that
          intent by ID.

FINDING   The retry above was approved because a design note asserts Binance deduplicates on
          `newClientOrderId`, and the assertion is false.
WHY       On Binance spot and futures the client order ID is unique only among *open* orders. Once the
          first order fills or is cancelled the ID is free again and the resend creates a genuine second
          order, so `-2010` never fires. The reuse window is zero seconds after a fill, which is exactly
          the marketable-limit case where a lost response is most likely.
EVIDENCE  scalper.py module docstring, design note 2
FIX       Treat the identity as a correlation key, delete the sentence, and put the venue's documented
          semantics beside the code that depends on them: scalper.py submit() and scalper.py resolve().
TEST      Each numbered design note is bound to a test name or deleted; note 2 has no test and does not
          survive the pass.

FINDING   Every order leaves one tick low on price and one step low on quantity, always in the same
          direction, and a quantity shaved this way can drop under `minNotional` and be rejected outright.
WHY       `snap()` runs `value % increment` on binary floats. `0.29 % 0.01 == 0.009999999999999974`, so
          `snap(0.29, 0.01)` returns 0.28, a whole step below the intended value. `int(0.29/0.01) == 28`
          fails identically.
EVIDENCE  scalper.py snap(), and its call sites in place_buy() and place_take_profit()
FIX       One named function crosses from estimate to obligation with no binary float as an operand:
          scalper.py quantize(), over Decimals built from the venue's own filter strings in
          scalper.py load_filters(), with scalper.py wire() so `str(1e-05)` never reaches the wire.
TEST      Quantizing a value that is not exactly representable in binary returns the intended multiple,
          and a normalised order satisfies every venue filter simultaneously.

FINDING   A nominal +1% take-profit realises +0.798% at 0.1% taker fees per side, and the sell quantity is
          separately short by the base-asset commission. About 20% of the intended edge, on 100% of round
          trips, always understating cost.
WHY       The target is computed from the price the bot asked for rather than from
          `cummulativeQuoteQty / executedQty`, and is never grossed up for the round-trip commission. A
          BUY on Binance spot charges the fee in the base asset just bought, so what can be sold is less
          than `executedQty`.
EVIDENCE  scalper.py place_take_profit(), the `entry` and `tp` computation and the `qty` taken from
          executedQty
FIX       Take the basis from the executed VWAP, gross up by both commission legs before quantizing,
          quantize a sell target up rather than to nearest, and subtract the base-asset commission read
          from the venue's own trade records: scalper.py take_profit_order(), fed by client.my_trades().
TEST      A round trip filled at the target price nets at least the requested return after both fees, and
          the sell quantity never exceeds the filled quantity net of base-asset commission.

FINDING   A crash mid-cycle leaves an orphan resting order that keeps filling, and the control that would
          find it was described rather than built.
WHY       `# TODO: on startup, reconcile our open orders against the exchange before placing anything new`
          names the right control accurately and then writes it as prose. A named risk in a comment is the
          same defect as the missing control.
EVIDENCE  scalper.py main(), the TODO above the first place_buy() call
FIX       scalper.py resume(), called before the first send: it resolves every INFLIGHT and
          INFLIGHT_UNKNOWN intent and exits rather than trading a symbol it could not resolve.
TEST      An intent left INFLIGHT by a killed process is resolved on the next start, and one that stays
          unresolved refuses to trade that symbol.

UNRESOLVED: scheduled comparison of position and realized PnL against the venue (this bot holds one
position at a time and no position store, so there is no local number to compare; adding a position store
makes the comparison mandatory, and resume() is not a substitute for it)

VERDICT   SHIP
```

`SHIP` is honest only because this script places one order and exits. Two things carry that verdict and
neither appears as a finding, because the diff fixed them rather than leaving them open: `scalper.py`
`ALERT_SINK` is a config key with no default, so import fails rather than alerting into a void, and an
unresolved intent closes the risk gate instead of placing anything else. The unresolved comparison stays on
the page rather than being deleted from it, which is what makes it blocking the day a position store
appears.
