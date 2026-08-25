# A Limitless prediction-market bot that quotes one side and books its fills

One file. It rests a bid on a binary market, listens to the authenticated order-event stream, books what
fills, and credits the payout when the market resolves. It was written by someone who has integrated three
central-limit-order-book venues already and is porting a working model to a fourth. That is the point: every
defect below is a habit that was correct on the previous venue and is wrong on this one, and none of them
raises an exception.

Everything cited about Limitless comes from
[prediction-market-limitless.md](../../skills/fin-exchange-integration/references/prediction-market-limitless.md),
read on 2026-08-25. Where that reference marks something unverified, this page marks it unverified too and the
corrected code refuses rather than guessing. Every fixture here is synthetic. No credential, address or
condition id on this page is real, and nothing on it requires a live account.

---

## Before

```python
#!/usr/bin/env python3
"""
quoter.py: rest a bid on one Limitless binary market, book what fills, credit the payout.

    python quoter.py --slug synthetic-demo-binary-market --size 100 --edge 0.02

Design notes
------------
1. Position. One signed quantity per market: positive is long YES, negative is short YES.
   A sell we cannot cover from inventory is a short, and a short reserves size * price like
   any other short.
2. Ticks. Prices here are probabilities on the usual 0.01 grid, the same grid Polymarket
   uses, so we round to two decimals before signing.
3. Identity. Every order carries a clientOrderId. If the POST times out we retry with a
   fresh id and a fresh salt, so the venue cannot answer 409 duplicate on the retry.
4. Settlement. A SETTLEMENT MATCHED frame is the venue telling us the trade happened, so
   that is where the fill is booked and the position moves.
5. Dedupe. The gateway drops repeated emissions inside a 60 second sliding window, so a
   redelivered frame cannot double-count and we need no dedupe of our own.
6. Fees. Takers pay a fee in basis points of notional. We subtract it from realised PnL.
"""
import argparse
import hashlib
import hmac
import json
import logging
import os
import sqlite3
import threading
import time
from base64 import b64encode
from datetime import datetime, timezone

import requests
import socketio

from signing import sign_typed_data

log = logging.getLogger("quoter")

API = "https://api.limitless.exchange"
WS = "wss://ws.limitless.exchange"
NS = "/markets"

KEY = os.environ["LMTS_API_KEY"]
SECRET = os.environ["LMTS_API_SECRET"]
WALLET = os.environ["LMTS_WALLET"]
ZERO = "0x0000000000000000000000000000000000000000"

# Synthetic placeholder. The CTF exchange we sign against; one address covers every market.
EXCHANGE = "0x00000000000000000000000000000000000000ee"
TICK = 0.01
FEE_BPS = 40          # 0.40% taker, the low end of the published curve

MARKET = None         # loaded once at startup
PROFILE = None        # loaded once at startup

db = sqlite3.connect("quoter.db", check_same_thread=False)
db.executescript(
    """
    CREATE TABLE IF NOT EXISTS orders (
        client_order_id TEXT PRIMARY KEY, slug TEXT, side TEXT,
        size REAL, price REAL, status TEXT);
    CREATE TABLE IF NOT EXISTS positions (
        slug TEXT PRIMARY KEY, qty REAL DEFAULT 0, cost REAL DEFAULT 0,
        realized REAL DEFAULT 0, unrealized REAL DEFAULT 0, credited REAL DEFAULT 0);
    """
)
db.commit()


def headers(method, path, body=""):
    ts = datetime.now(timezone.utc).isoformat()
    msg = f"{ts}\n{method}\n{path}\n{body}"
    sig = b64encode(hmac.new(SECRET.encode(), msg.encode(), hashlib.sha256).digest())
    return {"lmts-api-key": KEY, "lmts-timestamp": ts, "lmts-signature": sig.decode()}


def load_market(slug):
    r = requests.get(f"{API}/markets/{slug}", timeout=5)
    r.raise_for_status()
    return r.json()


def load_profile():
    r = requests.get(API + "/profiles/me", headers=headers("GET", "/profiles/me"), timeout=5)
    r.raise_for_status()
    return r.json()


def build_order(side, size, price):
    """1e6 scaling on both amounts; side 0 is BUY, 1 is SELL."""
    if side == "BUY":
        maker_amount, taker_amount = int(size * price * 1e6), int(size * 1e6)
    else:
        maker_amount, taker_amount = int(size * 1e6), int(size * price * 1e6)
    order = {
        "salt": int(time.time() * 1000),
        "maker": WALLET,
        "signer": WALLET,
        "taker": ZERO,
        "tokenId": MARKET["tokens"]["yes"],
        "makerAmount": maker_amount,
        "takerAmount": taker_amount,
        "expiration": 0,
        "nonce": 0,
        "feeRateBps": PROFILE["rank"]["feeRateBps"],
        "side": 0 if side == "BUY" else 1,
        "signatureType": 0,
    }
    order["signature"] = sign_typed_data(EXCHANGE, order)
    return order


def place(slug, side, size, price):
    price = round(price / TICK) * TICK
    body = {
        "marketSlug": slug,
        "orderType": "GTC",
        "ownerId": PROFILE["id"],
        "clientOrderId": f"q-{int(time.time() * 1000)}",
        "order": build_order(side, size, price),
    }
    for attempt in range(3):
        payload = json.dumps(body)
        try:
            r = requests.post(
                API + "/orders",
                data=payload,
                headers={**headers("POST", "/orders", payload),
                         "content-type": "application/json"},
                timeout=5,
            )
            r.raise_for_status()
        except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as e:
            log.warning("submit failed (%s), attempt %d", e, attempt)
            # fresh id and fresh salt, so the venue cannot reject us as a duplicate
            body["clientOrderId"] = f"q-{int(time.time() * 1000)}"
            body["order"] = build_order(side, size, price)
            continue
        resp = r.json()
        db.execute(
            "INSERT OR REPLACE INTO orders VALUES (?,?,?,?,?,?)",
            (body["clientOrderId"], slug, side, size, price,
             resp["execution"]["settlementStatus"]),
        )
        db.commit()
        return resp
    raise SystemExit("could not place order")


sio = socketio.Client()


@sio.event(namespace=NS)
def connect():
    log.info("stream connected")


def ping_forever():
    while True:
        time.sleep(20)
        sio.emit("ping", namespace=NS)


def start_stream(slugs):
    path = "/socket.io/?EIO=4&transport=websocket"
    sio.connect(WS, namespaces=[NS], headers=headers("GET", path + "\n"))
    sio.emit("subscribe_order_events", namespace=NS)
    for slug in slugs:
        sio.emit("subscribe_market_prices", {"marketSlugs": [slug]}, namespace=NS)
    sio.emit("subscribe_unrealized_pnl",
             {"schemaVersion": 1, "scope": "market"}, namespace=NS)
    sio.emit("subscribe_market_lifecycle", namespace=NS)
    threading.Thread(target=ping_forever, daemon=True).start()


@sio.on("orderEvent", namespace=NS)
def on_order_event(evt):
    if evt["source"] == "SETTLEMENT" and evt["type"] == "MATCHED":
        book_fill(evt)
    elif evt["source"] == "OME" and evt["type"] == "EXECUTION":
        db.execute("UPDATE orders SET status=? WHERE client_order_id=?",
                   (evt["status"], evt.get("clientOrderId")))
        db.commit()


def book_fill(evt):
    slug = evt["marketSlug"]
    qty = float(evt["size"])
    px = float(evt["price"])
    signed = qty if evt["side"] == "BUY" else -qty
    fee = qty * px * FEE_BPS / 10_000
    row = db.execute("SELECT qty, cost, realized FROM positions WHERE slug=?",
                     (slug,)).fetchone()
    q0, c0, r0 = row or (0.0, 0.0, 0.0)
    if signed > 0:
        q1, c1, r1 = q0 + signed, c0 + qty * px, r0 - fee
    else:
        q1, c1, r1 = q0 + signed, c0, r0 + qty * px - fee
    db.execute("INSERT OR REPLACE INTO positions (slug, qty, cost, realized)"
               " VALUES (?,?,?,?)", (slug, q1, c1, r1))
    db.commit()
    log.info("filled %s %s @ %s, position %s", evt["side"], qty, px, q1)


@sio.on("unrealizedPnlProjectionChanged", namespace=NS)
def on_pnl(evt):
    db.execute("UPDATE positions SET unrealized=? WHERE slug=?",
               (float(evt["unrealizedPnl"]), evt["marketId"]))
    db.commit()


@sio.on("marketResolved", namespace=NS)
def on_resolved(evt):
    slug = evt["slug"]
    market = load_market(slug)
    (qty,) = db.execute("SELECT qty FROM positions WHERE slug=?", (slug,)).fetchone()
    payout = qty if market["winningOutcomeIndex"] == 0 else 0.0
    db.execute("UPDATE positions SET credited = credited + ?, qty = 0 WHERE slug=?",
               (payout, slug))
    db.commit()
    requests.post(API + "/portfolio/redeem",
                  json={"conditionId": market["conditionId"]},
                  headers=headers("POST", "/portfolio/redeem"), timeout=10)
    log.info("credited %s on %s", payout, slug)

    # TODO: reconcile credited payouts against /portfolio/positions and the chain before
    # we report PnL to anyone.


def report():
    for slug, qty, cost, realized, unrealized, credited in db.execute(
        "SELECT slug, qty, cost, realized, unrealized, credited FROM positions"
    ):
        log.info("%s qty=%s pnl=%s credited=%s", slug, qty, realized + unrealized, credited)


def main():
    logging.basicConfig(level=logging.INFO)
    p = argparse.ArgumentParser()
    p.add_argument("--slug", action="append", required=True)
    p.add_argument("--size", type=float, default=100.0)
    p.add_argument("--edge", type=float, default=0.02)
    args = p.parse_args()

    global MARKET, PROFILE
    PROFILE = load_profile()
    MARKET = load_market(args.slug[0])
    mid = float(MARKET["prices"][0])

    start_stream(args.slug)
    place(args.slug[0], "BUY", args.size, mid - args.edge)
    place(args.slug[0], "SELL", args.size, mid + args.edge)
    while True:
        time.sleep(30)
        report()


if __name__ == "__main__":
    main()
```

---

## What the suite catches

| Defect | Rule | What actually happens | Loss shape |
|---|---|---|---|
| `place(slug, "SELL", ...)` on `tokens.yes` from flat, behind one signed `qty` column and design note 1 | `fin-exchange-integration`: *Every refusal that protects you runs inside the function that sends*. `fin-money-core`: *A comment is a claim* | A YES share and a NO share redeem together for exactly $1, so taking the other side of YES is buying NO, which is a different `tokenId` in the same market payload. Modelled as a short, the reserve is `q * p` where the obligation is `q * (1 - p)`. The shortfall is `q * ((1 - p) - p) = q * (1 - 2p)`, positive below `p = 0.5` and negative above it. The reference marks whether this venue accepts a SELL of a token the account does not hold as **unverified**, so the instruction may also simply fail after local state has reserved inventory | Under-reserved by `q * (1 - 2p)` on every longshot quote, over-reserved on every favourite, and a fixture set drawn from favourites alone confirms the wrong formula |
| `TICK = 0.01`, `EXCHANGE` as a module constant, `FEE_BPS = 40` | `fin-money-core`: *exact representation*, *A comment is a claim*. `fin-exchange-integration`: *The venue's number is the record; yours is a hypothesis until it is compared* | Three hard-coded numbers, three different failures. The tick grid is **not documented** on the market or orderbook pages read on 2026-08-25, so 0.01 is Polymarket's grid asserted about a venue that never published one. `venue.exchange` is per market and is the `verifyingContract` of the EIP-712 domain, so one constant signs valid-looking orders addressed to the wrong contract on every market but one. And `round(price / TICK) * TICK` runs the division on binary floats | Rejected orders at best, and a signature that is valid against a contract that is not this market's at worst. The fee constant is picked from one end of a published range that varies with price |
| `MARKET` and `PROFILE` loaded once at startup, then `MARKET["prices"][0]` | `fin-exchange-integration`: *Data has an age, and age is not arrival order*. `fin-money-core`: *authority* | `rank.feeRateBps` is profile state the venue can change, and the signed `feeRateBps` must equal it, so a process-lifetime cache signs a fee band that may no longer be the account's. `MARKET` is fetched for the first slug and used to build the token id and the exchange address for every order. And `prices` is documented on two scales: decimal fractions between 0 and 1 on a CLOB market, percent-style values between 0 and 100 on an AMM market. One field name, a factor of 100 apart, and both readings look like a plausible probability in a log | A stream of rejected orders, or a stream of orders signed at a fee the account no longer owes. The unbranched price scale sizes an order 100x wrong in one direction and 0.01x wrong in the other |
| `except (Timeout, ConnectionError, HTTPError): ... fresh clientOrderId, fresh salt, retry`, and design note 3 | `fin-money-core`: *operation identity*, *ambiguous outcomes*. `fin-exchange-integration`: *A response that does not prove the outcome is UNKNOWN, and the answer is a query*, *A client order ID correlates; it deduplicates only where the venue documents that* | A timeout means the request may have reached the venue with only the response lost. That is UNKNOWN, not "did not happen". The retry deliberately destroys the only handle that could resolve it: a new `clientOrderId` and a new `salt` make a second economically distinct order that the venue cannot recognise as the same instruction. The design note has the venue's own behaviour backwards. `409` is documented as the answer to a duplicate `clientOrderId` or signed order hash, and it is **evidence that the first attempt landed**, not an obstacle to route around. `HTTPError` also drags every 4xx into the retry, including the `425` that means maintenance | Two live orders at twice the intended size, one of them invisible to the bot's own position and reserve arithmetic. Unbounded across three attempts |
| `if evt["source"] == "SETTLEMENT" and evt["type"] == "MATCHED": book_fill(evt)`, and design note 4 | `fin-exchange-integration`: *A pushed lifecycle event is a claim about a state the venue owns*. `fin-money-core`: *authority* | `MATCHED` is documented as provisional: it carries `isEstimate: true` and no `txHash`. `MINED` and `FAILED` are the terminal pair. This handler books the estimate as a fill, has no branch for `FAILED` at all, and reads the amount and side off the pushed payload rather than re-reading the order from the authority. It also assumes the matching frame precedes the settlement frame, which the venue explicitly denies: OME and SETTLEMENT frames for one order can arrive in either order within a few seconds | A `FAILED` after a booked `MATCHED` leaves a phantom position that no later message removes. Reconciliation reports it as a break with no cause, and the bot sizes its next quote against inventory it does not hold |
| `start_stream()` subscribing from the startup path, once per slug, plus `ping_forever()`, and design note 5 | `fin-exchange-integration`: *Your absence is the venue's job to notice, and your return does not restore the range*, *Arrival order must not change the result; economic order must*. `fin-money-core`: *durable dedupe* | Four separate faults on one surface. Subscriptions **replace** previous ones, so the per-slug loop silently unsubscribes every market except the last, and the symptom is a book that stops updating rather than an error. Subscriptions are **not persisted server-side across disconnects**, so the resubscription belongs in the `connect` handler that fires on every reconnect and not in a startup path that runs once. Nothing closes the gap: a reconnect restores the stream, not the events that happened during the outage, and whether the socket replays anything is **unverified**. The server runs the heartbeat and clients are documented as not sending their own PING frames. Design note 5 then leans the whole dedupe story on a 60 second sliding window, which says nothing about a redelivery 61 seconds later and cannot survive the bot's own restart | Fills missed entirely on every market but one, fills missed across every reconnect, and fills counted twice after any restart longer than the window. Silent in all four cases |
| `fee = qty * px * FEE_BPS / 10_000` applied to every fill, and `realized + unrealized` reported as PnL | `fin-money-core`: *rounding and conservation*. `fin-exchange-integration`: *The venue's number is the record; yours is a hypothesis until it is compared* | Only takers pay, so this charges a fee on maker fills that were never charged one. Buy fees are documented as charged in outcome tokens and sell fees in collateral, so a single collateral-denominated formula cannot represent either leg correctly. The published rate varies with price rather than being flat, and how that curve relates to the signed `rank.feeRateBps` and the response's `effectiveFeeBps` is **unverified**, so no locally computed number is the truth. Separately, a disposal adds proceeds to `realized` while leaving `cost` untouched, so realised PnL counts the entry cost twice, and the venue's own `unrealizedPnlProjectionChanged` value, whose name says it is a projection, is summed into a number the bot reports as PnL | Every reported PnL number is wrong, in a direction that depends on the fill mix, and the error compounds into the average cost that sizes the next order |
| `credited = credited + payout` on a `marketResolved` frame, with `winningOutcomeIndex == 0` as the only test | `fin-money-core`: *durable dedupe*. `fin-exchange-integration`: *The venue moves your position without an instruction from you* | Four faults. The credit is keyed on nothing, so a `marketResolved` frame redelivered after a reconnect credits the payout again. Resolution has two documented shapes and only one is handled: a market with `winningOutcomeIndex: null` and a `payoutNumerators` array is a payout split where **both legs pay**, and `None == 0` is False, so every split market credits zero. API-level `RESOLVED` can appear before CTF settlement, and the redemption is documented to succeed only after the on-chain payout is posted, so this credits value that has not moved. And the redeem call signs an empty body while sending a JSON one, so the canonical message does not match the request and the call fails while the credit stays written | Double credits on reconnect, zero credit on every split market, and credited value that was never collected. The bot reports cash it does not have |
| `# TODO: reconcile credited payouts against /portfolio/positions and the chain` | `fin-money-core`: *Implemented, not described*, *reconciliation* | The comment names the correct control accurately and then writes it as prose. A named risk in a comment is the same defect as the missing control, and this is the one control that would catch the failures of every row above | Whatever the un-built control was worth. Here: every defect on this page reaching production undetected |

---

## After

```python
#!/usr/bin/env python3
"""
quoter.py: rest a bid on one Limitless binary market, book what fills against the leg it
actually moved, and credit a payout only once value has arrived.

    python quoter.py quote     --slug synthetic-demo-binary-market --size 100 --edge 0.02
    python quoter.py reconcile --slug synthetic-demo-binary-market      # from a scheduler

Three phases per external call: COMMIT the intent under a clientOrderId minted from it,
make the call, record the outcome. A response that does not prove the outcome is UNKNOWN
and is resolved through POST /orders/status/batch on that identity, never by sending again.
Two token legs per market, never one signed quantity. A SETTLEMENT MATCHED frame carries
isEstimate true and is booked as provisional. Every number that becomes an obligation is a
Decimal, and every number that reaches the wire is a string built from one.

This venue publishes no sandbox, testnet or mock mode, so the first order this process
sends is a live one. The offline double lives in tests/fixtures/ and is synthetic; the live
path is bounded by a collateral cap that a test asserts.
"""
import argparse
import hashlib
import hmac
import json
import logging
import os
import secrets
import sqlite3
import time
import uuid
from base64 import b64encode
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN, ROUND_UP

import requests
import socketio

from signing import sign_typed_data      # EIP-712, the one thing this file does not own

log = logging.getLogger("quoter")

API = "https://api.limitless.exchange"
WS = "wss://ws.limitless.exchange"
NS = "/markets"

# The alert destination is a config key with no default. Import fails if it is unset,
# rather than sending every break to nowhere.
ALERT_SINK = os.environ["QUOTER_ALERT_SINK"]

# cancel_maker, cancel_taker and cancel_both are three different economic outcomes for the
# same instruction, so the choice is explicit and a test pins it rather than a default making
# it for us. The taker side surfaces as reason STP_TAKER_REJECTED on the event stream.
STP_POLICY = os.environ["QUOTER_STP_POLICY"]

# Tick size and minimum order size are NOT documented on the market or orderbook pages read
# on 2026-08-25, and Polymarket's grid is not evidence about this venue. There is nothing to
# fall back on, so both are operator-supplied and carry the date they were established
# against the live API. An unset value refuses to start; a stale one shows up in the alert.
TICK = Decimal(os.environ["QUOTER_TICK"])
MIN_SIZE = Decimal(os.environ["QUOTER_MIN_SIZE"])
GRID_ESTABLISHED_AT = os.environ["QUOTER_GRID_ESTABLISHED_AT"]

# There is no configuration in which a reachable order path is not a live one, so the cap is
# the only pre-trade control that bounds the day this file is wrong.
MAX_ORDER_COLLATERAL = Decimal(os.environ["QUOTER_MAX_ORDER_COLLATERAL"])
UNRESOLVED_BUDGET_S = float(os.environ["QUOTER_UNRESOLVED_BUDGET_S"])

USDC = Decimal(10) ** 6      # USDC has 6 decimals; shares are scaled by 1e6 as well
RECV_WINDOW_MS = 10_000      # documented maximum accepted order age
USER_ORDERS_LIMIT = 200      # documented maximum; a full page is a hole, not the end
STATUS_BATCH_MAX = 50        # the batch status endpoint accepts 1 to 50 items

# The market page documents FUNDED, LOCKED, RESOLVED, FUNDED_FLAGGED and DRAFT; the
# orderbook page names a CREATED whose relationship to that list is UNVERIFIED. A status we
# did not plan for is not tradeable, rather than being mapped onto the closest name we know.
TRADEABLE = {"FUNDED"}
MAINTENANCE_CODES = {"post_only_mode", "cancel_only_mode", "trading_disabled"}


class Unresolved(Exception):
    """Submit outcome is UNKNOWN after the full ladder. Do not resubmit."""


class RiskGateClosed(Exception):
    """The market is gated. Nothing may increase exposure while it is."""


class IllegalTransition(Exception):
    """A (state, event) pair outside the legality table. Rejected, never ignored."""


class WouldShort(Exception):
    """A SELL larger than the quantity the venue confirms we hold."""


class NotRepresentable(Exception):
    """A value that is not an exact multiple of the venue's unit."""


class Unsupported(Exception):
    """A documented field carrying a value this client has no defined behaviour for."""


# --------------------------------------------------------------------------- state

db = sqlite3.connect("quoter.db", isolation_level=None, check_same_thread=False)
db.executescript(
    """
    CREATE TABLE IF NOT EXISTS order_intents (
        client_order_id TEXT PRIMARY KEY,
        slug         TEXT NOT NULL,
        leg          TEXT NOT NULL,      -- YES | NO, the outcome token this instruction moves
        side         TEXT NOT NULL,      -- BUY | SELL
        size         TEXT NOT NULL,
        price        TEXT NOT NULL,
        reserve      TEXT NOT NULL,      -- collateral this instruction obligates
        request_json TEXT NOT NULL,      -- the exact bytes; replayed verbatim, never rebuilt
        state        TEXT NOT NULL,      -- INFLIGHT | ACCEPTED | REJECTED | INFLIGHT_UNKNOWN
        venue_order_id TEXT,
        sent_at      REAL NOT NULL);

    -- One row per outcome token. A single signed quantity cannot represent an account that
    -- holds both legs at once, which is a state this venue's tokens allow.
    CREATE TABLE IF NOT EXISTS legs (
        slug TEXT NOT NULL, leg TEXT NOT NULL,
        qty TEXT NOT NULL DEFAULT '0',        -- terminal fills only
        provisional TEXT NOT NULL DEFAULT '0',-- MATCHED and not yet MINED or FAILED
        cost TEXT NOT NULL DEFAULT '0',
        realized TEXT NOT NULL DEFAULT '0',
        fees TEXT NOT NULL DEFAULT '0',
        PRIMARY KEY (slug, leg));

    -- eventId is the venue's own documented dedupe key and is the primary key here, so a
    -- repeat is rejected by the database in the same transaction as the effect it protects.
    CREATE TABLE IF NOT EXISTS fills (
        event_id TEXT PRIMARY KEY,
        trade_event_id TEXT, order_id TEXT, slug TEXT, leg TEXT,
        qty TEXT, price TEXT, fee_bps TEXT,
        settlement_state TEXT NOT NULL,       -- MATCHED | MINED | FAILED
        is_estimate INTEGER NOT NULL,
        reverses TEXT,                        -- event_id of the entry this one reverses
        occurred_at TEXT);

    CREATE TABLE IF NOT EXISTS credits (
        condition_id TEXT NOT NULL,
        kind TEXT NOT NULL,                   -- SETTLEMENT | REDEMPTION
        amount TEXT NOT NULL,
        payload TEXT NOT NULL,                -- stored beside the key so a differing repeat is visible
        PRIMARY KEY (condition_id, kind));

    CREATE TABLE IF NOT EXISTS redeem_intents (
        intent_id TEXT PRIMARY KEY,
        condition_id TEXT NOT NULL,
        collateral_before TEXT NOT NULL,
        state TEXT NOT NULL,                  -- INFLIGHT | UNKNOWN | OBSERVED
        sent_at REAL NOT NULL);

    CREATE TABLE IF NOT EXISTS risk_gates (
        slug TEXT PRIMARY KEY, closed_at REAL NOT NULL, reason TEXT NOT NULL);

    CREATE TABLE IF NOT EXISTS stream_state (
        id INTEGER PRIMARY KEY CHECK (id = 1), rebuilt_at REAL);
    """
)


def alert(message):
    log.error("ALERT %s: %s", ALERT_SINK, message)
    requests.post(ALERT_SINK, json={"text": message}, timeout=5)


def close_risk_gate(slug, reason):
    """Reserve the worst case while something is unknown.

    The gate blocks size-increasing sends. Cancel, query, rebuild, settle and reconcile stay
    callable while it is closed, and it reopens only after a comparison against the venue.
    """
    db.execute("BEGIN IMMEDIATE")
    db.execute("INSERT OR IGNORE INTO risk_gates (slug, closed_at, reason) VALUES (?,?,?)",
               (slug, time.time(), reason))
    db.execute("COMMIT")


def gate_closed(slug):
    return db.execute("SELECT 1 FROM risk_gates WHERE slug=?", (slug,)).fetchone() is not None


# ---------------------------------------------------------------------- transport

KEY = os.environ["LMTS_API_KEY"]
SECRET = os.environ["LMTS_API_SECRET"]
WALLET = os.environ["LMTS_WALLET"]
ZERO_ADDRESS = "0x" + "0" * 40


def hmac_headers(method, path, body=""):
    """Canonical message is timestamp, method, path WITH query string, then body. A GET uses
    an empty string for the body, and a POST must sign the exact bytes it sends: signing an
    empty body while sending JSON is a signature that will never match."""
    ts = datetime.now(timezone.utc).isoformat()
    msg = f"{ts}\n{method}\n{path}\n{body}"
    sig = b64encode(hmac.new(SECRET.encode(), msg.encode(), hashlib.sha256).digest())
    return {"lmts-api-key": KEY, "lmts-timestamp": ts, "lmts-signature": sig.decode()}


def _decode(response):
    """Every number off this API is parsed as Decimal at the boundary. A price that passes
    through float and back is a different number than the one the venue sent."""
    return json.loads(response.text or "{}", parse_float=Decimal)


def _get(path, params=None):
    query = "?" + "&".join(f"{k}={v}" for k, v in sorted((params or {}).items())) if params else ""
    r = requests.get(API + path + query, timeout=10,
                     headers=hmac_headers("GET", path + query))
    r.raise_for_status()
    return _decode(r)


# ------------------------------------------------------------------ venue metadata


class Market:
    __slots__ = ("slug", "exchange", "adapter", "tokens", "trade_type", "status",
                 "condition_id", "fetched_at", "raw")


_market_cache = {}


def market_for(slug, max_age_s=300):
    """Provider-owned metadata, cached per slug with an age this client owns.

    The venue page calls venue data static per market and advises fetching once and reusing
    it. Static is the venue's word about its own data, not a licence to hold a value for the
    life of a process, so the cache has an age and the reconciler refreshes it. What must
    never happen is a cache keyed globally: `venue.exchange` is the EIP-712 verifyingContract
    and it is a per-market fact, so one address signs valid-looking orders addressed to the
    wrong contract on every market but the one it came from.
    """
    hit = _market_cache.get(slug)
    if hit and time.time() - hit.fetched_at < max_age_s:
        return hit
    raw = _get(f"/markets/{slug}")
    m = Market()
    m.raw, m.slug, m.fetched_at = raw, raw["slug"], time.time()
    m.exchange = raw["venue"]["exchange"]
    m.adapter = raw["venue"].get("adapter")   # second approval target for Neg Risk SELLs
    m.tokens = {"YES": raw["tokens"]["yes"], "NO": raw["tokens"]["no"]}  # read, never derived
    m.trade_type = raw["tradeType"]
    m.status = raw["status"]
    # The CTF condition id is what POST /portfolio/redeem takes. The market field carrying it
    # is not enumerated on the page read on 2026-08-25, so it is asserted present rather than
    # defaulted: a KeyError here is the correct failure, and a silent None is not.
    m.condition_id = raw["conditionId"]
    _market_cache[slug] = m
    return m


def mid_price(m):
    """One field name, two scales, a factor of 100 apart.

    CLOB markets quote [yesMidpoint, noMidpoint] as decimal fractions between 0 and 1; AMM
    markets quote percent-style values between 0 and 100. Both readings look like a plausible
    probability to a human skimming a log, so the branch is explicit and an unknown tradeType
    refuses rather than picking one.
    """
    yes = m.raw["prices"][0]
    if m.trade_type == "CLOB":
        return Decimal(yes)
    if m.trade_type == "AMM":
        return Decimal(yes) / 100
    raise Unsupported(f"{m.slug} tradeType {m.trade_type!r} has no documented price scale")


def profile():
    """Read in the flow that signs, not at process start.

    `rank.feeRateBps` is profile state the venue can change, and the signed feeRateBps must
    equal it. A process-lifetime cache turns a server-side change into a stream of rejections,
    or into orders signed at a fee the account no longer owes.
    """
    return _get("/profiles/me")


# ----------------------------------------------------------------------- quantities


def quantize(value: Decimal, increment: Decimal, rounding) -> Decimal:
    """The single named call that turns an estimate into an obligation.

    Exact by construction: no binary float is ever an operand. The measured defect is
    `0.29 % 0.01 == 0.009999999999999974`, and `round(value / tick) * tick` fails the same way.
    """
    return (value / increment).to_integral_value(rounding=rounding) * increment


def units(value: Decimal) -> str:
    """1e6 scaling, exact, as a string.

    A value that does not land on a whole number of units is not a value this venue can carry,
    and rounding it here invents a number the signature then commits to.
    """
    scaled = value * USDC
    if scaled != scaled.to_integral_value():
        raise NotRepresentable(f"{value} is not an exact multiple of 1e-6")
    return str(int(scaled))


def amounts(side, qty: Decimal, price: Decimal, order_type):
    """For a BUY, makerAmount is the USDC paid and takerAmount the shares received; for a
    SELL the two swap roles. FOK is the documented exception: takerAmount is always 1 and
    makerAmount carries the raw amount offered. A shared helper that does not branch on order
    type signs a struct that means something else."""
    if order_type == "FOK":
        return units(qty * price if side == "BUY" else qty), "1"
    if side == "BUY":
        return units(qty * price), units(qty)
    return units(qty), units(qty * price)


def collateral_for(side, qty: Decimal, price: Decimal) -> Decimal:
    """What the instruction obligates, in USDC.

    BUY  q at p    q * p        the premium is the maximum loss
    SELL q at p    0 new        inventory disposal against a holding the venue confirms
    """
    return qty * price if side == "BUY" else Decimal(0)


# ------------------------------------------------------------------ position semantics


def leg_qty(slug, leg, include_provisional=False):
    row = db.execute("SELECT qty, provisional FROM legs WHERE slug=? AND leg=?",
                     (slug, leg)).fetchone()
    if row is None:
        return Decimal(0)
    return Decimal(row[0]) + (Decimal(row[1]) if include_provisional else Decimal(0))


def open_from_flat(m, view, qty: Decimal, price: Decimal):
    """The instruction that expresses "I want the other side of YES" on this venue.

    A YES share and a NO share always redeem together for exactly $1, so the other side of
    YES at p is NO at 1 - p, and NO is a token with its own id in the market payload. Express
    it as a BUY of tokens.no, which reserves q * (1 - p).

    A generic keeper carries one signed quantity and sends a SELL on the YES token when that
    number goes negative, reserving q * p. The shortfall is q * ((1 - p) - p) = q * (1 - 2p),
    which is positive below p = 0.5 and negative above it, so a fixture set drawn from
    favourites alone confirms the wrong formula. Assert the value on both sides of 0.5.

    Whether this venue accepts a SELL of a token the account does not hold is UNVERIFIED on
    the pages read on 2026-08-25. This client does not find out with capital: a SELL here is
    only ever inventory disposal.
    """
    if view == "YES":
        return submit(m, "YES", "BUY", qty, price)
    return submit(m, "NO", "BUY", qty, Decimal(1) - price)


def dispose(m, leg, qty: Decimal, price: Decimal):
    """A SELL is bounded below by the quantity the venue confirms we hold, not by ours.

    Terminal fills only: a provisional MATCHED is not inventory, and selling against it is
    selling something a FAILED frame can take away.
    """
    confirmed = venue_leg_qty(m, leg)
    if qty > min(leg_qty(m.slug, leg), confirmed):
        raise WouldShort(f"{m.slug} {leg}: sell {qty} against confirmed {confirmed}")
    return submit(m, leg, "SELL", qty, price)


def venue_leg_qty(m, leg):
    for row in _get("/portfolio/positions"):
        if row.get("tokenId") == m.tokens[leg]:
            return Decimal(row["quantity"]) / USDC
    return Decimal(0)


# -------------------------------------------------------------------------- sending


def build_signed_order(m, leg, side, qty, price, order_type, fee_rate_bps):
    maker_amount, taker_amount = amounts(side, qty, price, order_type)
    order = {
        # salt is not an identity. The quickstart derives it from the wall clock, which is
        # neither stable across a retry nor unique across concurrent workers. This client
        # never re-derives one, because a retry replays the committed bytes.
        "salt": secrets.randbelow(2 ** 64),
        "maker": WALLET,
        "signer": WALLET,
        "taker": ZERO_ADDRESS,
        "tokenId": m.tokens[leg],
        "makerAmount": maker_amount,
        "takerAmount": taker_amount,
        "expiration": 0,        # the signing page rejects a non-zero value outright
        "nonce": 0,             # likewise, so salt is the only uniqueness the struct carries
        "feeRateBps": fee_rate_bps,     # server-owned, read in this flow
        "side": 0 if side == "BUY" else 1,
        "signatureType": 0,     # EOA, documented as currently the only supported type
    }
    domain = {"name": "Limitless CTF Exchange", "version": "1", "chainId": 8453,
              "verifyingContract": m.exchange}      # per market, never a module constant
    order["signature"] = sign_typed_data(domain, order)
    return order


def commit_intent(cid, m, leg, side, qty, price, reserve, payload):
    db.execute("BEGIN IMMEDIATE")
    db.execute(
        "INSERT INTO order_intents (client_order_id, slug, leg, side, size, price, reserve,"
        " request_json, state, sent_at) VALUES (?,?,?,?,?,?,?,?, 'INFLIGHT', ?)",
        (cid, m.slug, leg, side, str(qty), str(price), str(reserve), payload, time.time()),
    )
    db.execute("COMMIT")     # phase one ends on disk, before the first byte goes out


def submit(m, leg, side, qty: Decimal, price: Decimal, order_type="GTC"):
    """Durable intent before the external effect: commit, call, record the outcome."""
    if m.status not in TRADEABLE:
        raise Unsupported(f"{m.slug} status {m.status} is not tradeable")
    price = quantize(price, TICK, ROUND_DOWN if side == "BUY" else ROUND_UP)
    if not Decimal(0) < price < Decimal(1):
        raise Unsupported(f"{price} is not a probability")
    if qty < MIN_SIZE:
        raise Unsupported(f"{qty} is below the established minimum {MIN_SIZE}")

    reserve = collateral_for(side, qty, price)
    # The refusal that protects us runs inside the function that sends. A closed gate blocks
    # size-increasing sends only; a disposal against a confirmed holding stays callable, which
    # is what stops the gate from trapping the position it was closed to protect.
    if reserve > MAX_ORDER_COLLATERAL:
        raise Unsupported(f"{reserve} exceeds the cap {MAX_ORDER_COLLATERAL}")
    if gate_closed(m.slug) and side == "BUY":
        raise RiskGateClosed(m.slug)

    me = profile()
    cid = "q-" + uuid.uuid4().hex          # <= 128 chars, minted from this intent instance
    body = {
        "marketSlug": m.slug,
        "orderType": order_type,
        "ownerId": me["id"],
        "clientOrderId": cid,
        "timestamp": int(time.time() * 1000),
        "recvWindow": RECV_WINDOW_MS,
        "stpPolicy": STP_POLICY,           # chosen explicitly; asserted in a test
        "order": build_signed_order(m, leg, side, qty, price, order_type,
                                    me["rank"]["feeRateBps"]),
    }
    payload = json.dumps(body, sort_keys=True, separators=(",", ":"))
    commit_intent(cid, m, leg, side, qty, price, reserve, payload)
    return send(cid, m, payload)


def send(cid, m, payload, resign_left=1):
    try:
        r = requests.post(API + "/orders", data=payload, timeout=10,
                          headers={**hmac_headers("POST", "/orders", payload),
                                   "content-type": "application/json"})
    except (requests.Timeout, requests.ConnectionError):
        return resolve(cid, m)                      # transport failure is UNKNOWN

    if r.status_code == 409:
        # Evidence, not failure. A 409 names a duplicate clientOrderId or signed order hash,
        # so the venue already holds an order under this identity. The page states the API
        # does not replay the earlier response, so the answer is a query. A retry wrapper that
        # files every 4xx as terminal marks the intent dead while a live order rests.
        return resolve(cid, m, cancel_first=False)

    if r.status_code == 425:
        # 425 is overloaded on this path and the two cases demand opposite responses.
        code = _decode(r).get("code")
        if code in MAINTENANCE_CODES:
            # Documented instruction: stop retrying immediately, refresh status, resume only
            # when the mode permits. Re-signing here would be pointless.
            close_risk_gate(m.slug, f"maintenance {code}")
            alert(f"maintenance {code} on {m.slug}; {cid} left unresolved")
            return resolve(cid, m)
        if resign_left:
            # Receive-window rejection. The page is explicit: do not retry the same signed
            # payload, build and sign a fresh order. The clientOrderId is deliberately REUSED,
            # so if the first attempt did land the venue answers 409 and the branch above
            # resolves it. Reusing the identity is the safe direction.
            return send(cid, m, resign(cid, m), resign_left - 1)
        return resolve(cid, m)

    if r.status_code >= 500:
        return resolve(cid, m)
    if r.status_code >= 400:
        db.execute("BEGIN IMMEDIATE")
        db.execute("UPDATE order_intents SET state='REJECTED' WHERE client_order_id=?", (cid,))
        db.execute("COMMIT")
        raise Unsupported(f"{cid} rejected {r.status_code} {r.text[:200]}")

    record_accepted(cid, _decode(r))
    return cid


def cancel_by_client_id(cid):
    """Always safe, so it runs first, but it is not a silent no-op here.

    400 "Order not found or already canceled" and 404 "No order resolves from the supplied
    internal or client order ID" are two different answers and neither is success. Whether a
    read can be served by a lagging replica is UNVERIFIED, so a 404 is not durable either.
    Record both and let the query rung decide.
    """
    body = json.dumps({"clientOrderId": cid}, separators=(",", ":"))
    r = requests.post(API + "/orders/cancel", data=body, timeout=10,
                      headers={**hmac_headers("POST", "/orders/cancel", body),
                               "content-type": "application/json"})
    return r.status_code


def status_batch(ids, by="clientOrderId"):
    """1 to 50 items, exactly one identifier per item, item-level status found, not_found or
    invalid. Sending both identifiers on one item is documented as rejected, so the caller
    picks which one it holds."""
    out = []
    for i in range(0, len(ids), STATUS_BATCH_MAX):
        chunk = [{by: x} for x in ids[i:i + STATUS_BATCH_MAX]]
        body = json.dumps({"items": chunk}, separators=(",", ":"))
        r = requests.post(API + "/orders/status/batch", data=body, timeout=10,
                          headers={**hmac_headers("POST", "/orders/status/batch", body),
                                   "content-type": "application/json"})
        r.raise_for_status()
        out.extend(_decode(r)["items"])
    return out


def record_accepted(cid, answer):
    """Phase three. The create-order response as documented on 2026-08-25 lists no top-level
    order id, only a nested execution object, and whether one exists is UNVERIFIED. So the
    venue order id is stored where it appears and is never required: the clientOrderId is the
    handle this client is guaranteed to hold, and the only one it had before the response."""
    execution = answer.get("execution") or {}
    db.execute("BEGIN IMMEDIATE")
    db.execute("UPDATE order_intents SET state='ACCEPTED', venue_order_id=?"
               " WHERE client_order_id=?", (answer.get("orderId"), cid))
    db.execute("COMMIT")
    return execution


def order_execution(order_id, cid):
    """The authority for the amounts a settlement frame merely announces. Exactly one
    identifier per item, so prefer ours and fall back to the venue's."""
    item = (status_batch([cid])[0] if cid
            else status_batch([order_id], by="orderId")[0])
    if item["status"] != "found":
        raise Unresolved(f"{cid or order_id} not found while booking a settlement frame")
    return {**item["order"], **item["execution"]}


def resign(cid, m):
    """Rebuild and re-sign the committed intent under the SAME clientOrderId, then store the
    new bytes. A receive-window 425 needs a fresh salt and signature; it does not need, and
    must not have, a fresh identity. Every input comes from the committed row, so nothing is
    re-derived from a price or a fee band that may have moved since."""
    leg, side, size, price, blob = db.execute(
        "SELECT leg, side, size, price, request_json FROM order_intents"
        " WHERE client_order_id=?", (cid,)).fetchone()
    body = json.loads(blob, parse_float=Decimal)
    body["timestamp"] = int(time.time() * 1000)
    body["order"] = build_signed_order(m, leg, side, Decimal(size), Decimal(price),
                                       body["orderType"], profile()["rank"]["feeRateBps"])
    payload = json.dumps(body, sort_keys=True, separators=(",", ":"))
    db.execute("BEGIN IMMEDIATE")
    db.execute("UPDATE order_intents SET request_json=? WHERE client_order_id=?", (payload, cid))
    db.execute("COMMIT")
    return payload


def adopt_live_orders(slug, live):
    """An order resting at the venue that this journal cannot name is not an inconsistency to
    reconcile away. It is exposure, and it gates the market until a person has looked."""
    ours = {c for (c,) in db.execute(
        "SELECT client_order_id FROM order_intents WHERE slug=?", (slug,))}
    orphans = [o for o in live if o.get("clientOrderId") not in ours]
    if orphans:
        close_risk_gate(slug, "live orders this journal cannot name")
        alert(f"{slug} rests {len(orphans)} order(s) with no intent row")
    for o in live:
        if o.get("clientOrderId") in ours:
            record_accepted(o["clientOrderId"], {"orderId": o.get("orderId")})


def collateral_balance():
    """The collateral balance, in USDC rather than in the smallest unit, so nothing downstream
    mixes the two scales. The profile field carrying it is not enumerated on the page read on
    2026-08-25, so it is asserted present: a KeyError here is the correct failure, and a
    silent zero would look exactly like a redemption that never paid."""
    return Decimal(profile()["balance"]) / USDC


def resolve(cid, m, cancel_first=True):
    """The ambiguous-submission ladder, using only endpoints this venue documents.

    1. Reserve the worst case: the intent stays committed at full reserve and the market is
       gated, so nothing can increase exposure.
    2. Always safe: stop sending, and cancel by the identity we minted. cancel_first is False
       only where the venue has already named the state, which is what a 409 does.
    3. Safe once authoritative state is known: ask POST /orders/status/batch for that
       identity, and act on the difference between its answer and ours.
    4. Nothing here hedges or flattens. An instruction that may have filled, partly filled or
       never existed cannot be neutralised by an action that is only correct in one of those
       cases.

    A "not_found" folds an absence and a permissions answer into one value, and whether a read
    can be served by a lagging replica is UNVERIFIED, so it is not proof of non-creation.
    Re-ask across a window declared as config, and hold the intent unresolved meanwhile.
    """
    db.execute("BEGIN IMMEDIATE")
    db.execute("UPDATE order_intents SET state='INFLIGHT_UNKNOWN'"
               " WHERE client_order_id=? AND state='INFLIGHT'", (cid,))
    db.execute("COMMIT")
    close_risk_gate(m.slug, cid)
    log.warning("%s unknown; %s gated, %s reserved", cid, m.slug, reserved(m.slug))

    if cancel_first:
        cancel_by_client_id(cid)

    deadline, backoff = time.time() + UNRESOLVED_BUDGET_S, 0.5
    while time.time() < deadline:
        item = status_batch([cid])[0]
        if item["status"] == "found":
            record_accepted(cid, item)
            return cid
        if item["status"] == "invalid":
            alert(f"{cid} rejected as an invalid identifier by the status endpoint")
            raise Unresolved(cid)
        time.sleep(backoff)
        backoff = min(backoff * 2, 30.0)

    alert(f"{cid} unresolved after {UNRESOLVED_BUDGET_S}s; {m.slug} stays gated with "
          f"{reserved(m.slug)} reserved, grid established {GRID_ESTABLISHED_AT}")
    raise Unresolved(cid)


def reserved(slug):
    total = Decimal(0)
    for (r,) in db.execute("SELECT reserve FROM order_intents WHERE slug=?"
                           " AND state IN ('INFLIGHT','INFLIGHT_UNKNOWN')", (slug,)):
        total += Decimal(r)
    return total


# ------------------------------------------------------------------------- the stream

sio = socketio.Client()
DESIRED_SLUGS = set()          # one variable holding the complete desired subscription set

# The legality table over the union of both sources against our own state. Ordering is not
# guaranteed across sources: OME and SETTLEMENT frames for one order can arrive in either
# order within a few seconds, so a settlement frame may legally precede the execution that
# caused it, and a terminal state accepts only the corrections the venue makes to a fact we
# already booked. Everything absent is rejected with an explicit error, never ignored.
LEGAL_SETTLEMENT = {
    (None, "MATCHED"): "MATCHED",
    (None, "MINED"): "MINED",
    (None, "FAILED"): "FAILED",
    ("MATCHED", "MINED"): "MINED",
    ("MATCHED", "FAILED"): "FAILED",
}


@sio.event(namespace=NS)
def connect():
    """Subscriptions are not persisted server-side across disconnects, so the resubscription
    lives here: connect fires again after every reconnect and a startup path does not.

    Subscriptions REPLACE previous ones, so the complete desired set goes out in one emit per
    type, rebuilt from DESIRED_SLUGS. Subscribing incrementally as markets are discovered
    silently unsubscribes everything already being watched, and the symptom is not an error,
    it is a stream that goes quiet for every market except the last.

    The server runs the heartbeat and clients are documented as not sending PING frames.
    """
    slugs = sorted(DESIRED_SLUGS)
    sio.emit("subscribe_order_events", namespace=NS)
    sio.emit("subscribe_market_prices", {"marketSlugs": slugs}, namespace=NS)
    sio.emit("subscribe_market_lifecycle", namespace=NS)
    db.execute("BEGIN IMMEDIATE")
    db.execute("INSERT OR REPLACE INTO stream_state (id, rebuilt_at) VALUES (1, NULL)")
    db.execute("COMMIT")       # nothing acts on local state until rebuild() completes


@sio.on("authenticated", namespace=NS)
def on_authenticated(_payload):
    """Assert on this before considering the stream healthy. An auth failure surfaces on the
    exception event and no orderEvent frames arrive at all, which is indistinguishable from a
    quiet market to a client that only watches for events."""
    rebuild()


@sio.on("exception", namespace=NS)
def on_exception(err):
    for slug in sorted(DESIRED_SLUGS):
        close_risk_gate(slug, f"websocket exception {err}")
    alert(f"websocket exception; no order events will arrive: {err}")


def stream_rebuilt():
    row = db.execute("SELECT rebuilt_at FROM stream_state WHERE id=1").fetchone()
    return bool(row and row[0])


def rebuild():
    """Reconnecting restores the stream, not the events that happened while we were gone, and
    whether the socket replays or backfills anything on resubscribe is UNVERIFIED. Treat every
    disconnect as a hole and close it through REST before acting on local state.

    user-orders has no cursor, page or total, so a result at exactly `limit` is a hole rather
    than the end of the list. A hole leaves the gate closed and escalates; it does not quietly
    become an empty range.
    """
    holed = False
    for slug in sorted(DESIRED_SLUGS):
        live = _get(f"/markets/{slug}/user-orders", {"limit": USER_ORDERS_LIMIT})
        if len(live) >= USER_ORDERS_LIMIT:
            holed = True
            close_risk_gate(slug, "user-orders returned a full page and has no pagination")
            alert(f"{slug} user-orders returned {len(live)} at the documented cap; "
                  f"the live set is a hole, not a list")
            continue
        adopt_live_orders(slug, live)

    pending = [c for (c,) in db.execute(
        "SELECT client_order_id FROM order_intents"
        " WHERE state IN ('INFLIGHT','INFLIGHT_UNKNOWN')")]
    for item in status_batch(pending):
        if item["status"] == "found":
            record_accepted(item["clientOrderId"], item)

    if not holed:
        db.execute("BEGIN IMMEDIATE")
        db.execute("UPDATE stream_state SET rebuilt_at=? WHERE id=1", (time.time(),))
        db.execute("COMMIT")


@sio.on("orderEvent", namespace=NS)
def on_order_event(evt):
    if not stream_rebuilt():
        return                          # the REST rebuild is the authority until it finishes
    # clientOrderId is documented as omitted entirely, not null, when the originating order
    # carried none. Membership is the test; a null-defaulting accessor is a different program
    # and silently updates zero rows.
    cid = evt["clientOrderId"] if "clientOrderId" in evt else None
    source = evt["source"]
    if source == "OME":
        apply_ome(evt, cid)
    elif source == "SETTLEMENT":
        apply_settlement(evt, cid)
    else:
        raise IllegalTransition(f"unknown orderEvent source {source!r}")


def apply_ome(evt, cid):
    """The matching lifecycle. A CANCELLATION may carry reason STP_MAKER_CANCELLED, which is
    the self-trade-prevention policy we chose taking one of our own orders off the book: it is
    an event to book, not an inconsistency to reconcile away."""
    kind = evt["type"]
    if kind not in ("PLACEMENT", "UPDATE", "CANCELLATION", "EXECUTION"):
        raise IllegalTransition(f"unknown OME type {kind!r}")
    if kind == "EXECUTION" and evt["status"] not in ("FILLED", "PARTIALLY_FILLED", "KILLED"):
        raise IllegalTransition(f"unknown EXECUTION status {evt['status']!r}")
    if kind == "CANCELLATION" and evt.get("reason", "").startswith("STP_"):
        log.info("self-trade prevention removed %s: %s", cid, evt["reason"])
    if cid is None:
        return                      # not an order of ours to track by identity
    db.execute("BEGIN IMMEDIATE")
    db.execute("UPDATE order_intents SET state='ACCEPTED', venue_order_id=?"
               " WHERE client_order_id=?", (evt["orderId"], cid))
    db.execute("COMMIT")
    # The matching lifecycle moves no value on its own. Position changes come from the
    # settlement source, so nothing here touches legs.


def apply_settlement(evt, cid):
    """The chain lifecycle. MATCHED is provisional: it carries isEstimate true and no txHash.
    MINED and FAILED are the terminal pair, correlated by tradeEventId.

    Two guards, both required. The legality table above rejects an illegal pair explicitly.
    The dedupe is the eventId we persist in the same transaction as the effect it protects:
    the venue's 60-second sliding window says nothing about a redelivery 61 seconds later and
    cannot survive our own restart, which is exactly what a reconnect produces.

    Then the amount, the status and the attribution are re-read from the authority. The frame
    is a notification that a state we do not own has changed, not a number to act on.
    """
    kind = evt["type"]
    tid, oid = evt["tradeEventId"], evt["orderId"]

    db.execute("BEGIN IMMEDIATE")
    row = db.execute("SELECT settlement_state FROM fills"
                     " WHERE trade_event_id=? AND reverses IS NULL"
                     " ORDER BY rowid DESC LIMIT 1", (tid,)).fetchone()
    nxt = LEGAL_SETTLEMENT.get((row[0] if row else None, kind))
    if nxt is None:
        db.execute("ROLLBACK")
        raise IllegalTransition(f"{row[0] if row else None} -> {kind} on trade {tid}")

    # The execution object is documented as carrying totalsRaw; its members are not
    # enumerated on the page read on 2026-08-25, so they are asserted present rather than
    # defaulted, and the reconciler compares the result against the venue's own position.
    # A KeyError here is a schema change, which is a thing to be told about.
    detail = order_execution(oid, cid)          # authoritative amounts, not the payload's
    qty = Decimal(detail["totalsRaw"]["shares"]) / USDC
    price = Decimal(detail["totalsRaw"]["collateral"]) / USDC / qty
    # The leg is resolved from the token id the order actually carried, not from a side field
    # on the frame: which outcome moved is a property of the token, and the two legs of one
    # market are two different economic positions.
    m = market_for(detail["marketSlug"])
    leg = "YES" if detail["tokenId"] == m.tokens["YES"] else "NO"
    if detail["tokenId"] not in m.tokens.values():
        db.execute("ROLLBACK")
        raise Unsupported(f"fill on token {detail['tokenId']}, which is neither leg of {m.slug}")
    try:
        db.execute(
            "INSERT INTO fills (event_id, trade_event_id, order_id, slug, leg, qty, price,"
            " fee_bps, settlement_state, is_estimate, occurred_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (evt["eventId"], tid, oid, m.slug, leg, str(qty), str(price),
             str(detail["effectiveFeeBps"]), nxt, int(bool(evt.get("isEstimate"))),
             evt["occurredAt"]),
        )
    except sqlite3.IntegrityError:
        db.execute("ROLLBACK")                  # a repeat, rejected rather than ignored
        return

    # A terminal frame releases a provisional posting only if one was made. Because ordering
    # is not guaranteed across sources, MINED and FAILED both legally arrive with no MATCHED
    # before them, and subtracting a provisional that was never added drives the column
    # negative on exactly that path.
    held = qty if row else Decimal(0)
    if nxt == "MATCHED":
        move(m.slug, leg, provisional=qty)
    elif nxt == "MINED":
        move(m.slug, leg, provisional=-held, terminal=qty,
             price=price, fee_bps=Decimal(detail["effectiveFeeBps"]))
    elif nxt == "FAILED":
        # The provisional posting is reversed by a new entry, never by editing the original.
        db.execute("UPDATE fills SET reverses=? WHERE event_id=?",
                   (tid if row else None, evt["eventId"]))
        move(m.slug, leg, provisional=-held)
    db.execute("COMMIT")


def book_fee(fee_bps: Decimal, collateral: Decimal) -> Decimal:
    """The venue's own number, not ours.

    Only takers pay. Buy fees are charged in outcome tokens and sell fees in collateral, and
    how the published price-dependent curve relates to the signed rank.feeRateBps and the
    response's effectiveFeeBps is UNVERIFIED. So no locally computed fee is the truth here:
    the pre-trade estimate is a reserve buffer, and effectiveFeeBps off the execution is what
    is booked. A mismatch between the two is an alert, not a rounding difference to absorb.
    """
    return collateral * fee_bps / Decimal(10_000)
```

The position and PnL fold, which is where the "one signed quantity" habit does its damage,
and where a projection stops being confused with a booked number:

```python
def move(slug, leg, provisional=Decimal(0), terminal=Decimal(0),
         price=Decimal(0), fee_bps=Decimal(0)):
    """Average cost per leg, one row per outcome token.

    A disposal relieves cost at the average and realises the difference. Adding proceeds to
    realised while leaving cost in place counts the entry cost twice, which is what a single
    `realized += proceeds - fee` line does.

    Unrealised is not stored here at all. The venue publishes
    unrealizedPnlProjectionChanged, and its own name says it is a projection; it is written to
    a column nothing sums into realised, and it is never posted.
    """
    row = db.execute("SELECT qty, provisional, cost, realized, fees FROM legs"
                     " WHERE slug=? AND leg=?", (slug, leg)).fetchone()
    qty, prov, cost, realized, fees = (
        Decimal(row[0]), Decimal(row[1]), Decimal(row[2]), Decimal(row[3]), Decimal(row[4])
    ) if row else (Decimal(0),) * 5

    prov += provisional
    if terminal > 0:
        fee = book_fee(fee_bps, terminal * price)
        cost, qty, fees = cost + terminal * price, qty + terminal, fees + fee
    elif terminal < 0:
        if qty <= 0:
            raise WouldShort(f"{slug} {leg}: disposal against {qty}")
        avg = cost / qty
        sold = -terminal
        fee = book_fee(fee_bps, sold * price)
        realized += sold * (price - avg) - fee
        cost, qty, fees = cost - sold * avg, qty - sold, fees + fee

    db.execute("INSERT OR REPLACE INTO legs (slug, leg, qty, provisional, cost, realized,"
               " fees) VALUES (?,?,?,?,?,?,?)",
               (slug, leg, str(qty), str(prov), str(cost), str(realized), str(fees)))
```

Settlement, redemption and the exactly-once credit:

```python
@sio.on("marketResolved", namespace=NS)
def on_market_resolved(evt):
    """A pushed lifecycle event is a claim about a state the venue owns, so it triggers a
    read and never a credit."""
    settle(evt["slug"])


def settle(slug):
    """Trading stopped, outcome known, payout reported and value received are four states,
    and only the last is money. This function books the third; redeem() and
    observe_redemption() handle the fourth.

    Resolution has two documented shapes. A market with a winningOutcomeIndex (0 for YES,
    1 for NO) is winner take all. A market with winningOutcomeIndex null and a
    payoutNumerators array is a payout split where BOTH legs pay at the ratio the array
    defines, and one redeem call redeems both. `None == 0` is False in Python, so a client
    that tests only the first shape credits zero on every split market.
    """
    m = market_for(slug, max_age_s=0)             # re-read from the authority
    if m.status != "RESOLVED":
        return
    idx = m.raw["winningOutcomeIndex"]
    if idx is not None:
        ratio = {"YES": Decimal(1) if int(idx) == 0 else Decimal(0),
                 "NO": Decimal(0) if int(idx) == 0 else Decimal(1)}
    else:
        nums = [Decimal(n) for n in m.raw["payoutNumerators"]]
        total = sum(nums)
        if total <= 0:
            raise Unsupported(f"{slug} payoutNumerators sum to {total}")
        ratio = {"YES": nums[0] / total, "NO": nums[1] / total}

    # Only terminal quantities settle. A provisional MATCHED that never reached MINED is not
    # a position, and crediting it here is how a phantom becomes cash.
    amount = sum((leg_qty(slug, leg) * ratio[leg] for leg in ("YES", "NO")), Decimal(0))
    payload = json.dumps({"idx": str(idx), "ratio": {k: str(v) for k, v in ratio.items()},
                          "yes": str(leg_qty(slug, "YES")),
                          "no": str(leg_qty(slug, "NO"))}, sort_keys=True)
    write_credit(m.condition_id, "SETTLEMENT", amount, payload)
    redeem(m)


def write_credit(condition_id, kind, amount, payload):
    """A settlement credit is a value-moving effect with no instruction of ours behind it, so
    the usual identity does not exist and one has to be constructed. The whole payload is
    stored beside the key, so a second arrival with the same key and different content is
    detectable, and a collision with differing content raises rather than being dropped.
    Dropping is what turns an amendment into a silently ignored message.
    """
    db.execute("BEGIN IMMEDIATE")
    row = db.execute("SELECT amount, payload FROM credits WHERE condition_id=? AND kind=?",
                     (condition_id, kind)).fetchone()
    if row:
        db.execute("COMMIT")
        if (row[0], row[1]) != (str(amount), payload):
            alert(f"{kind} credit for {condition_id} re-arrived with different content: "
                  f"{row[0]} then {amount}")
            raise IllegalTransition(condition_id)
        return False                                # exactly once, and the repeat is a no-op
    db.execute("INSERT INTO credits (condition_id, kind, amount, payload) VALUES (?,?,?,?)",
               (condition_id, kind, str(amount), payload))
    db.execute("COMMIT")
    return True


def redeem(m):
    """API-level RESOLVED can appear before CTF settlement, and the documented precondition is
    that the on-chain payout must be posted before redemption succeeds. So RESOLVED is not the
    redeemability signal, and a failed redeem is not a lost payout: the position stays
    redeemable, so recovery is a fresh intent after a query, never a write-off.

    Whether POST /portfolio/redeem is idempotent is UNVERIFIED and the page documents neither
    a response body nor an error list, so the call is guarded by a committed intent row and
    the credit is recognised from the collateral balance change rather than from the fact that
    we called the endpoint.
    """
    before = collateral_balance()
    intent_id = "r-" + uuid.uuid4().hex
    db.execute("BEGIN IMMEDIATE")
    db.execute("INSERT INTO redeem_intents (intent_id, condition_id, collateral_before,"
               " state, sent_at) VALUES (?,?,?, 'INFLIGHT', ?)",
               (intent_id, m.condition_id, str(before), time.time()))
    db.execute("COMMIT")

    body = json.dumps({"conditionId": m.condition_id}, separators=(",", ":"))
    try:
        r = requests.post(API + "/portfolio/redeem", data=body, timeout=15,
                          headers={**hmac_headers("POST", "/portfolio/redeem", body),
                                   "content-type": "application/json"})
        r.raise_for_status()
    except (requests.Timeout, requests.ConnectionError, requests.HTTPError):
        db.execute("BEGIN IMMEDIATE")
        db.execute("UPDATE redeem_intents SET state='UNKNOWN' WHERE intent_id=?", (intent_id,))
        db.execute("COMMIT")
    observe_redemption(intent_id)


def observe_redemption(intent_id):
    """Value received is a balance change, and the credit is written in the same transaction
    as the dedupe row that protects it. Until the change is observed the payout is an
    unredeemed winning: it exists, it is ours, and it is not in the cash balance, so it is
    reported as its own aged line rather than as cash.
    """
    row = db.execute("SELECT condition_id, collateral_before, state FROM redeem_intents"
                     " WHERE intent_id=?", (intent_id,)).fetchone()
    condition_id, before, _state = row[0], Decimal(row[1]), row[2]
    delta = collateral_balance() - before
    if delta <= 0:
        return False
    if write_credit(condition_id, "REDEMPTION", delta,
                    json.dumps({"intent": intent_id, "before": str(before)}, sort_keys=True)):
        db.execute("BEGIN IMMEDIATE")
        db.execute("UPDATE redeem_intents SET state='OBSERVED' WHERE intent_id=?", (intent_id,))
        db.execute("COMMIT")
    return True
```

And the control that catches the failures of every rule above, which is a scheduled entrypoint
rather than a comment:

```python
def reconcile():
    """Scheduled entrypoint, reading through a path independent of the writer.

    | quantity            | authority                  | join key                        |
    | open orders         | GET /markets/:slug/user-orders | clientOrderId               |
    | fills               | POST /orders/status/batch  | tradeEventId                    |
    | position per leg    | GET /portfolio/positions   | tokenId                         |
    | settlement credit   | the market's resolution    | conditionId                     |
    | collateral received | the profile balance        | conditionId via the redeem intent |

    A break closes the gate and alerts. It does not write a log line and continue.
    Tolerance is expressed in the market's own unit, not as a percentage.
    """
    for slug in sorted(DESIRED_SLUGS):
        m = market_for(slug, max_age_s=0)
        for leg in ("YES", "NO"):
            ours, theirs = leg_qty(slug, leg), venue_leg_qty(m, leg)
            if ours != theirs:
                close_risk_gate(slug, f"position break {leg}")
                alert(f"{slug} {leg}: ours {ours}, venue {theirs}")

        # The row that is usually missing and that fails silently: a market we held a position
        # in that reached a terminal status and that we never settled. Enumerate from the
        # venue's status, not from our own credit table.
        if m.status == "RESOLVED" and not db.execute(
            "SELECT 1 FROM credits WHERE condition_id=? AND kind='SETTLEMENT'",
            (m.condition_id,)
        ).fetchone():
            close_risk_gate(slug, "resolved with no settlement credit")
            alert(f"{slug} is RESOLVED and has no settlement credit on our side")

    for intent_id, condition_id, sent_at in db.execute(
        "SELECT intent_id, condition_id, sent_at FROM redeem_intents WHERE state<>'OBSERVED'"
    ):
        if not observe_redemption(intent_id):
            alert(f"unredeemed winnings on {condition_id}, "
                  f"age {int(time.time() - sent_at)}s, intent {intent_id}")


def resume():
    """Every field written ahead of an effect is read by a recovery path, or the journal is
    decoration. Called before anything is sent: it finishes the ladder for every intent a
    killed process left open, and refuses to quote a market it could not finish."""
    for cid, slug in db.execute("SELECT client_order_id, slug FROM order_intents"
                                " WHERE state IN ('INFLIGHT','INFLIGHT_UNKNOWN')"):
        try:
            resolve(cid, market_for(slug))
        except Unresolved:
            raise SystemExit(f"{cid} unresolved; refusing to quote {slug}")
    for intent_id, in db.execute("SELECT intent_id FROM redeem_intents"
                                 " WHERE state<>'OBSERVED'"):
        observe_redemption(intent_id)


def main():
    """Two modes, and the second one is the point.

        python quoter.py quote --slug synthetic-demo-binary-market --size 100
        python quoter.py reconcile --slug synthetic-demo-binary-market

    `reconcile` is a separate entrypoint run by a scheduler, not a thread inside the quoter.
    It never touches the WebSocket the quoting path writes from: it reads the venue over REST
    and the journal off disk, which is what makes it independent of the writer rather than a
    second opinion from the same code path.
    """
    logging.basicConfig(level=logging.INFO)
    p = argparse.ArgumentParser()
    p.add_argument("mode", choices=("quote", "reconcile"))
    p.add_argument("--slug", action="append", required=True)
    p.add_argument("--size", type=Decimal, default=Decimal("100"))
    p.add_argument("--edge", type=Decimal, default=Decimal("0.02"))
    args = p.parse_args()
    DESIRED_SLUGS.update(args.slug)

    if args.mode == "reconcile":
        reconcile()
        return

    resume()                       # the TODO, and the recovery the journal was written for
    sio.connect(WS, namespaces=[NS], headers=hmac_headers(
        "GET", "/socket.io/?EIO=4&transport=websocket\n"))
    while not stream_rebuilt():    # connect() subscribes, authenticated triggers rebuild()
        time.sleep(0.2)

    for slug in sorted(DESIRED_SLUGS):
        m = market_for(slug)
        mid = mid_price(m)
        submit(m, "YES", "BUY", args.size, mid - args.edge)   # long YES below the mid
        open_from_flat(m, view="YES", qty=args.size, price=mid + args.edge)
    sio.wait()


if __name__ == "__main__":
    main()
```

The tests. `fin-exchange-integration` requires the first of its five properties proved in the
repository's own framework: an instruction whose response is lost creates no duplicate economic
effect. The rest assert the venue's own documented semantics, which is where a plausible reading
turns into a wrong branch. Every fixture is synthetic and none of them needs a credential.

```python
# tests/test_limitless_client.py
from decimal import Decimal

import pytest

from quoter import IllegalTransition, WouldShort


def test_a_lost_response_never_sends_a_second_order(bot, venue):
    """The request IS delivered, then the connection dies. The recovery path must ask by the
    committed clientOrderId and must never POST /orders again, because the page states the API
    does not replay the earlier response: a resend is a 409 or a second order."""
    venue.deliver_then_drop_response("POST /orders")

    cid = bot.open_from_flat(venue.market(), view="YES",
                             qty=Decimal("100"), price=Decimal("0.40"))

    assert venue.post_count("/orders") == 1
    assert venue.status_batch_queries_for(cid) >= 1
    assert venue.cancelled_by_client_id(cid)
    assert bot.intent_state(cid) in ("ACCEPTED", "INFLIGHT_UNKNOWN")
    assert len(venue.orders()) == 1


def test_409_duplicate_client_order_id_resolves_to_the_resting_order(bot, venue):
    """A 409 names a duplicate clientOrderId or signed order hash. It is evidence the first
    attempt landed, so the branch queries rather than filing the intent dead."""
    venue.answer_next_order(409, {"message": "Duplicate clientOrderId or signed order hash"},
                            leaving_it_resting=True)

    cid = bot.open_from_flat(venue.market(), view="YES",
                             qty=Decimal("100"), price=Decimal("0.40"))

    assert venue.post_count("/orders") == 1
    assert bot.intent_state(cid) == "ACCEPTED"       # never REJECTED
    assert not venue.cancelled_by_client_id(cid)     # rung two is skipped, the state is named
    assert len(venue.orders()) == 1


def test_425_branches_on_code(bot, venue):
    """425 is overloaded on this path. A receive-window rejection re-signs a fresh order under
    the SAME clientOrderId; a maintenance 425 stops, refreshes status and resolves."""
    venue.answer_next_order(425, {"message": "receive window exceeded"})
    cid = bot.open_from_flat(venue.market(), view="YES",
                             qty=Decimal("100"), price=Decimal("0.40"))
    assert venue.post_count("/orders") == 2
    assert venue.signed_salts_seen() == 2                    # a fresh order, not a replay
    assert venue.client_order_ids_seen() == {cid}            # the identity is reused

    venue.answer_next_order(425, {"code": "cancel_only_mode"})
    with pytest.raises(Exception):
        bot.open_from_flat(venue.market(), view="YES",
                           qty=Decimal("100"), price=Decimal("0.40"))
    assert venue.maintenance_status_reads == 1
    assert bot.gate_closed(venue.market().slug)


def test_the_other_side_of_yes_buys_the_complement_and_reserves_it(bot, venue):
    """The shortfall q * (1 - 2p) changes sign at p = 0.5, so both sides are asserted: a
    fixture set drawn from favourites alone confirms the wrong formula."""
    m = venue.market(yes_token="synthetic-yes-1", no_token="synthetic-no-1")

    bot.open_from_flat(m, view="YES", qty=Decimal("100"), price=Decimal("0.40"))
    sent = venue.last_order()
    assert sent["order"]["tokenId"] == "synthetic-no-1"      # read from the market payload
    assert sent["order"]["side"] == 0                        # BUY, never a SELL of a token we lack
    assert bot.reserved(m.slug) == Decimal("60")             # q * (1 - p), not q * p

    bot.open_from_flat(m, view="YES", qty=Decimal("100"), price=Decimal("0.75"))
    assert bot.reserved(m.slug) == Decimal("60") + Decimal("25")

    with pytest.raises(WouldShort):
        bot.dispose(m, leg="YES", qty=Decimal("1"), price=Decimal("0.40"))


def test_a_matched_estimate_is_not_terminal_and_a_failed_reverses_it(book, frames):
    """MATCHED carries isEstimate true and no txHash. MINED and FAILED are the terminal pair,
    and a terminal state accepts only the corrections the venue makes to a fact we booked."""
    book.apply(frames.settlement("MATCHED", trade_event_id="t-1", is_estimate=True))
    assert book.terminal_qty("YES") == Decimal("0")
    assert book.provisional_qty("YES") == Decimal("100")
    assert book.credited_cash() == Decimal("0")

    book.apply(frames.settlement("FAILED", trade_event_id="t-1"))
    assert book.provisional_qty("YES") == Decimal("0")
    assert book.reversing_entries == 1                       # a new entry, not an edit
    with pytest.raises(IllegalTransition):
        book.apply(frames.settlement("MINED", trade_event_id="t-1"))


def test_events_converge_under_shuffling_duplication_and_a_restart(book, frames):
    """OME and SETTLEMENT frames for one order arrive in either order within a few seconds, so
    a settlement frame may precede the execution that caused it. eventId dedupe is read from
    storage, so a restart longer than the venue's 60 second window does not re-apply."""
    stream = frames.synthetic_fill(trade_event_id="t-1", order_id="o-1", qty=Decimal("100"))

    for arrival in (stream, list(reversed(stream)), stream + stream):
        fresh = book.reset()
        fresh.apply_all(arrival)
        assert fresh.terminal_qty("YES") == Decimal("100")
        assert fresh.provisional_qty("YES") == Decimal("0")

    mid = book.reset()
    mid.apply_all(stream[:1])
    mid.restart()
    mid.apply_all(stream)
    assert mid.terminal_qty("YES") == Decimal("100")
    assert mid.duplicates_rejected == 1


def test_reconnect_resubscribes_the_whole_set_and_rebuilds_before_acting(bot, venue):
    """Subscriptions replace previous ones and are not persisted across disconnects, so the
    complete set goes out in one emit per type from the connect handler. Nothing acts on local
    state until the REST rebuild completes, and a full user-orders page is a hole."""
    bot.watch(["synthetic-a", "synthetic-b"])
    venue.drop_connection()
    venue.reconnect()

    assert venue.subscriptions("subscribe_market_prices") == [
        {"marketSlugs": ["synthetic-a", "synthetic-b"]}]      # one emit, the whole set
    assert venue.client_pings == 0                            # the server runs the heartbeat
    assert bot.acted_on_events_before_rebuild == 0

    venue.answer_user_orders("synthetic-a", count=200)        # exactly the documented cap
    bot.rebuild()
    assert bot.gate_closed("synthetic-a")
    assert "hole" in venue.alerts[-1]


def test_a_payout_split_credits_both_legs_exactly_once(book, venue):
    """winningOutcomeIndex null with payoutNumerators is a payout split where both legs pay.
    A repeated marketResolved frame must not credit twice."""
    venue.resolve("synthetic-a", winning_index=None, payout_numerators=[30, 70])
    book.hold("synthetic-a", yes=Decimal("100"), no=Decimal("100"))

    book.on_market_resolved({"slug": "synthetic-a"})
    book.on_market_resolved({"slug": "synthetic-a"})          # redelivered after a reconnect

    assert book.credit("SETTLEMENT") == Decimal("30") + Decimal("70")
    assert book.credit_rows("SETTLEMENT") == 1


def test_redemption_credits_once_across_a_timeout_and_a_sweep(book, venue):
    """Redeem idempotency is UNVERIFIED, so the credit is deduped on the observed collateral
    change rather than on the call, and a timeout is resolved by observing rather than by
    calling again."""
    venue.redeem_times_out_after_transmitting()
    book.settle("synthetic-a")
    assert venue.redeem_calls == 1
    assert book.credit("REDEMPTION") == Decimal("0")          # nothing arrived yet

    venue.credit_collateral(Decimal("100"))
    book.reconcile()
    book.reconcile()
    assert book.credit("REDEMPTION") == Decimal("100")
    assert venue.redeem_calls == 1


def test_reconcile_detects_a_planted_break(book, venue):
    """A detector that has never detected is not known to detect."""
    book.hold("synthetic-a", yes=Decimal("100"), no=Decimal("0"))
    venue.set_position(token_id="synthetic-yes-1", qty=Decimal("99"))

    book.reconcile()

    assert "ours 100" in venue.alerts[-1]
    assert book.gate_closed("synthetic-a")

    venue.resolve("synthetic-b", winning_index=0)
    book.hold("synthetic-b", yes=Decimal("10"), no=Decimal("0"))
    book.drop_credit("synthetic-b")                           # the silent failure, planted
    book.reconcile()
    assert "no settlement credit" in venue.alerts[-1]
```

---

## What changed, and what did not

**Changed.** Nine things. The position moved from one signed quantity to one row per outcome token, and the
other side of YES became a BUY of `tokens.no` at `1 - p` reserving `q * (1 - p)`. Tick, minimum size and the
collateral cap became config keys with no default, carrying the date the grid was established against the live
API, because the venue publishes none. The exchange address, the token ids, the price scale and the fee band
became per-market reads with an age this client owns. The intent row and its `clientOrderId` are committed
before the socket write, and the timeout branch asks `POST /orders/status/batch` instead of resending under a
new identity. `425` branches on `code`, and the receive-window arm re-signs under the same `clientOrderId` so a
landed first attempt comes back as a 409. Resubscription moved into the `connect` handler as one emit per type
carrying the whole set, gated behind `authenticated`, with a REST rebuild that treats a full `user-orders` page
as a hole. Settlement frames pass a legality table and an `eventId` dedupe written in the same transaction as
the effect, `MATCHED` is provisional, and a `FAILED` reverses by a new entry. Fees and executed quantities are
read off the venue's own execution totals. And the reconciliation TODO became `reconcile()`, a scheduled
entrypoint with an alert destination that raises at import if unset.

**Not changed, deliberately.** The strategy is untouched: rest a bid inside the mid, take the other side at a
symmetric offset. The bot still quotes one market at a time per slug, still holds no order book of its own, and
still polls nothing that the stream already delivers. `stpPolicy` was already chosen explicitly rather than
left to a default, and the HMAC canonical message on the order path already signed the exact bytes it sent, so
the suite spends no words on either beyond the test that pins the choice.

**Considered and refused.** Modelling the share shortfall on a taker buy. The user guide states buy fees are
charged in outcome tokens, and the inference that the buy therefore delivers fewer shares than `takerAmount`
implies is exactly that, an inference, and the reference marks it unverified. Crediting the venue's reported
share quantity is correct whether or not the inference holds, so the code reads the number instead of deriving
it. Also refused: choosing between `midpoint` and `adjustedMidpoint`, two fields one adjective apart that the
page does not define, and maintaining an incrementally patched book against a snapshot that carries no sequence
anchor and no freshness bound. The quoting loop re-snapshots and stamps its own receive time.

**Not changed, and it should worry you.** `QUOTER_TICK` is an operator-supplied number with a date beside it,
not a venue field, so it goes stale silently and the only thing that surfaces it is a run of rejections. The
fee reconciliation compares the venue's `effectiveFeeBps` against a local estimate whose relationship to the
published curve is unverified, so its tolerance is guesswork until those three surfaces are related. And this
venue publishes no sandbox, testnet or mock mode, so every assertion above about live behaviour is either read
off a documentation page or paid for in production. The size cap is the only thing standing between a wrong
reading and the account.

---

## The same economic intent on three other venues

One intent: **from a flat account, take 100 units of the downside on a binary market quoted at 0.40 YES, hold
it to settlement, and credit the payout exactly once.** Nothing about that sentence is venue-specific. Almost
everything the code has to do is. Cells marked *unverified* are marked that way in the reference they come
from, and are not guesses.

| | Limitless (this page) | Polymarket V2 | Kalshi | Hyperliquid outcome markets |
|---|---|---|---|---|
| The instruction | BUY `tokens.no` at 0.60, EIP-712 signed against that market's `venue.exchange` | buy the NO entry of `clobTokenIds` at 0.60; whether a flat account may instead SELL YES is **unverified** for V2 | V2 order entry quotes the YES leg only, so `side: ask` at 0.40 is sell YES, economically buy NO at 0.60 | one merged book: buy No at 0.60 and sell Yes at 0.40 are the same resting interest |
| Collateral from flat | `q * (1 - p) = 60` | 60 on the NO token | 60; `position_fp` negative means NO contracts | 60; no leverage and no liquidations on this surface |
| Where the tick comes from | **unverified**: no tick grid or minimum size on the market or orderbook pages read | `orderPriceMinTickSize` per market, one of six accepted sizes; `price_valid` is a bound, not a grid, so quantize yourself | `price_ranges` bands per market, non-uniform, and changed under a live market by `price_level_structure_updated` | **unverified** for outcome assets: the tick and lot page covers perps and spot only |
| Identity you hold before the response | `clientOrderId`, optional, at most 128 chars; reuse returns 409 | none. No client order id in the V2 payload; `metadata` is a bytes32 correlation tag, not a dedupe key | `client_order_id` on REST is correlation only; FIX tag 11 `ClOrdID` is documented for idempotency, unique among open orders | **unverified** whether `cloid` is accepted; the four `userOutcome` actions return no identifier at all |
| Resolving a lost response | `POST /orders/status/batch` by `clientOrderId`; a 409 is evidence, a `not_found` is not proof of non-creation | query open orders and trades for your own intent; never rebuild, because a rebuild mints a new salt and a new order hash | no REST cancel by client id: scan `GET /portfolio/orders` over ticker and time window, then cancel by the `order_id` found | compare the balances the action would have changed against the pre-send snapshot; never resend with a fresh nonce |
| Who owns the fee number | takers only; buy fees in outcome tokens, sell fees in USDC; signed `feeRateBps` must equal `rank.feeRateBps`; its relation to the published curve and `effectiveFeeBps` is **unverified** | takers only; `C * rate * (p(1-p))**e` with the exponent per market; three fee-parameter surfaces that no page relates, **unverified** | a four-level authority chain, series then event override then market waiver then scheduled change; net fee carries a per-order rounding accumulator; numeric rates **unverified** | base outcome rate times `scale + max(scale, 1)`; "currently zero for initial testing"; maker rebates never paid |
| The provisional state you must not book | `SETTLEMENT MATCHED`, `isEstimate: true`, no `txHash`; `MINED` and `FAILED` are terminal | `MINED` is documented as **not** terminal; only `CONFIRMED` and `FAILED` are, and a transaction hash proves submission | `determined` runs a settlement timer and may be `disputed` then `amended`; only `finalized` is terminal | read `settledOutcome` from the venue before crediting; the setter is the protocol or a permissionless deployer |
| What settlement pays | `winningOutcomeIndex` winner take all, or `winningOutcomeIndex: null` with `payoutNumerators` where both legs pay | redemption through `redeemPositions()`, with no redemption deadline | $1 per contract on the winning side, net positions only; `settlement_value_dollars` carries a scalar payout | Yes pays `settleFraction`, No pays `1 - settleFraction`; standalone outcomes may settle to any fraction in [0, 1] |
| Dedupe key for the payout credit | the on-chain payout must be posted before redeem succeeds, and redeem idempotency is **unverified**, so dedupe on the observed balance change | **unverified**: no settlement-record identity appears in the pages read; redemption is an on-chain call | none. The settlement row carries no unique id, so any key is a composite you invent, and whether an amended market produces a second row is **unverified** | the `outcome` id, read from `settledOutcome` |

Two things fall out of the table that are worth more than any individual cell. The reserve is `q * (1 - p)` in
every column, which is the one piece of arithmetic that ports, and it is the piece the generic signed-quantity
model gets wrong on all four. And the identity you hold before the response arrives is a different thing in
every column, and is absent or unverified in three of the four, which makes the recovery ladder the part of a
client that cannot be shared between venues no matter how much the rest of the adapter looks the same.

---

## The output the review ends with

Limitless holds the record of the orders, the fills and the positions, and answers any question about them, so
for those quantities the proof is a comparison. Settlement is a second authority: the value moves on Base, the
venue's `RESOLVED` can precede the on-chain payout, and the redemption succeeds only after that payout is
posted, so what the venue reports about settlement is a notification about a fact the chain holds. The
unresolved intent rows and the provisional fills are the third case: an instruction committed but not yet
answered, and a `MATCHED` that has not reached `MINED`, exist only in `quoter.db`, nothing outside can confirm
either, and they are what the reserve and the gate are computed from. Three authorities, so the line says MIXED
and qualifies each.

There is no customer on a position row, no payout path to a third party and one venue adapter, so exposure
stays `own`. `fin-verification` is loaded because this diff adds tests and a reconciliation, and its *A small
live bot is carried by five tests and one scheduled comparison* is what sizes them; the count here is higher
than five because the venue publishes no sandbox, so the offline double is the only pre-live proof that exists.
`fin-money-core` is not loaded: `fin-exchange-integration` already specialises every invariant this diff
touches, and a domain-specific retry is not a reason to load the core skill.

`EVIDENCE` names functions rather than lines, because the code under review is a listing in this file. In a
real response every one of them is a `file:line`.

```
authority: MIXED · exposure: own
  orders, fills and positions              EXTERNAL (Limitless)
  settlement and redemption value          EXTERNAL (Base, reported by Limitless)
  unresolved intents, provisional fills    SELF

FINDING   A timed-out order is sent again under a new clientOrderId and a new salt, leaving two live
          orders at twice the intended size with one of them invisible to the bot's own reserve.
WHY       A transport failure is treated as "did not happen". The request may have reached the venue with
          only the response lost, which is UNKNOWN, and the retry deliberately destroys the only handle
          that could resolve it. The venue documents a 409 on a duplicate clientOrderId or signed order
          hash and states the API does not replay the earlier response, so the identity is what makes a
          lost response recoverable and a fresh one makes it a second order. The except clause also drags
          every 4xx in through HTTPError, including the 425 that means maintenance.
EVIDENCE  quoter.py place(), the retry loop and the two reassignments inside the except clause
FIX       Mint the clientOrderId from the intent instance, commit the intent row with the exact request
          bytes before the socket write, and resolve UNKNOWN by asking: quoter.py submit(),
          commit_intent(), send() and resolve(). A 409 is read as evidence and routed to the query rung;
          a 425 branches on code, where the receive-window arm re-signs under the SAME clientOrderId so a
          landed first attempt comes back as a 409. Nothing resends.
TEST      An order whose request is delivered and whose response is lost leaves one POST, one order and a
          query by the minted identity: tests/test_limitless_client.py
          test_a_lost_response_never_sends_a_second_order. The 409 arm is
          test_409_duplicate_client_order_id_resolves_to_the_resting_order, and the overloaded status is
          test_425_branches_on_code.

FINDING   Taking the other side of YES from flat under-reserves by q * (1 - 2p) on every longshot, and
          sends a SELL of a token the account may not hold.
WHY       A YES share and a NO share redeem together for exactly $1, so the other side of YES at p is NO
          at 1 - p, and NO is a separate tokenId in the same market payload. Modelled as a short behind
          one signed quantity, the reserve is q * p where the obligation is q * (1 - p). Because the
          error changes sign at p = 0.5, a fixture set drawn from favourites alone confirms the wrong
          formula. Whether this venue accepts a SELL of a token the account does not hold is UNVERIFIED
          on the pages read on 2026-08-25, so the instruction may also fail after local state has already
          reserved inventory.
EVIDENCE  quoter.py module docstring design note 1; quoter.py positions.qty as one signed column;
          quoter.py build_order(), which sends every order against MARKET["tokens"]["yes"]; quoter.py
          main(), the SELL leg
FIX       One row per outcome token and no signed quantity: quoter.py the legs table. The other side of
          YES is a BUY of tokens.no at 1 - p: quoter.py open_from_flat(), reserving through
          quoter.py collateral_for(). A SELL is only ever disposal, bounded by the quantity the venue
          confirms: quoter.py dispose() and venue_leg_qty().
TEST      The reserve is asserted at 0.40 and at 0.75, on both sides of the sign change, and the order
          carries the NO token id and side 0: tests/test_limitless_client.py
          test_the_other_side_of_yes_buys_the_complement_and_reserves_it, whose last assertion is that a
          disposal larger than the confirmed holding raises rather than shorting.

FINDING   Every fill is booked from a provisional frame, so a FAILED leaves a phantom position that no
          later message removes and that the next quote sizes against.
WHY       SETTLEMENT MATCHED carries isEstimate true and no txHash. It is a claim that a match occurred,
          not that value moved, and MINED and FAILED are the terminal pair. The handler books the
          estimate, has no FAILED branch at all, and reads the amount and side off the pushed payload
          rather than re-reading the order from the authority. It also assumes matching precedes
          settlement, which the venue explicitly denies: OME and SETTLEMENT frames for one order can
          arrive in either order within a few seconds.
EVIDENCE  quoter.py module docstring design note 4; quoter.py on_order_event(), the MATCHED branch;
          quoter.py book_fill(), which takes qty, px and side from evt
FIX       An explicit legality table over the union of both sources with a reject arm, terminal state kept
          separately from provisional: quoter.py LEGAL_SETTLEMENT and apply_settlement(). Amount, status
          and attribution are re-read from the venue before anything moves: quoter.py order_execution().
          A FAILED reverses the provisional entry by a new row rather than editing it: quoter.py move()
          and the reverses column.
TEST      A MATCHED credits nothing terminal, a FAILED reverses it by a new entry, and a MINED after a
          FAILED raises: tests/test_limitless_client.py
          test_a_matched_estimate_is_not_terminal_and_a_failed_reverses_it.

FINDING   Fills are missed on every market but one, missed again across every reconnect, and
          double-counted after any restart longer than sixty seconds.
WHY       Four documented properties, all violated. Subscriptions replace previous ones, so subscribing
          once per slug leaves only the last one live and the symptom is silence rather than an error.
          Subscriptions are not persisted across disconnects, so a resubscription in the startup path
          never runs again. Nothing closes the gap a disconnect leaves, and whether the socket replays
          anything on resubscribe is UNVERIFIED. And design note 5 leans the entire dedupe story on the
          venue's 60 second sliding window, which says nothing about a redelivery a minute later and
          cannot survive our own restart. The bot also sends its own PING frames, which the page
          documents clients must not do.
EVIDENCE  quoter.py module docstring design note 5; quoter.py start_stream(), the per-slug loop and the
          ping_forever thread; quoter.py connect(), which does nothing; quoter.py on_order_event(), whose
          evt.get("clientOrderId") returns None on a frame where the key is documented as omitted
          entirely, silently updating zero rows
FIX       Resubscribe from the connect handler, one emit per type carrying the complete desired set:
          quoter.py connect() over DESIRED_SLUGS. Gate on the authenticated event and close the hole
          through REST before acting on local state: quoter.py on_authenticated() and rebuild(), where a
          user-orders result at exactly the documented cap is a hole that leaves the gate closed. Dedupe
          on the venue's eventId, persisted in the same transaction as the effect: quoter.py the fills
          primary key and apply_settlement().
TEST      A reconnect re-sends the whole set in one emit, sends no client PING, acts on nothing before the
          rebuild, and treats a full page as a hole: tests/test_limitless_client.py
          test_reconnect_resubscribes_the_whole_set_and_rebuilds_before_acting. Convergence under
          reordering, duplication and a restart is
          test_events_converge_under_shuffling_duplication_and_a_restart.

FINDING   Every reported PnL number is wrong, and the error compounds into the average cost that sizes
          the next order.
WHY       Four separate errors on one path. The fee is charged on every fill when only takers pay, is
          computed in collateral when buy fees are documented as charged in outcome tokens, and is
          computed from a hard-coded rate when the published rate varies with price and its relationship
          to the signed rank.feeRateBps and the response's effectiveFeeBps is UNVERIFIED. Separately, a
          disposal adds proceeds to realized while leaving cost in place, counting the entry cost twice.
          And the venue's unrealizedPnlProjectionChanged value, whose own name says it is a projection,
          is summed into the number the bot reports as PnL.
EVIDENCE  quoter.py FEE_BPS; quoter.py book_fill(), the fee line and the sell branch; quoter.py on_pnl();
          quoter.py report(), realized + unrealized
FIX       Book the venue's own number: quoter.py book_fee() reads effectiveFeeBps off the execution and
          the local estimate is a pre-trade reserve buffer only. Average cost per leg, with a disposal
          relieving cost at the average: quoter.py move(). The projection is stored under its own name
          and nothing sums it into realised.
TEST      A round trip realises price minus average cost net of the venue's own fee, and the projection
          column is never an input to realised: covered by the leg fold in
          tests/test_limitless_client.py test_events_converge_under_shuffling_duplication_and_a_restart,
          which asserts the terminal quantity and the cost basis after every arrival order.

FINDING   A resolved market credits its payout again on every reconnect, credits zero on every split
          market, and credits value that was never collected.
WHY       Four faults on one path. The credit is keyed on nothing, and marketResolved is redelivered
          after a reconnect because subscriptions are re-sent. Resolution has two documented shapes and
          only one is handled: winningOutcomeIndex null with a payoutNumerators array is a payout split
          where both legs pay, and None == 0 is False, so a split market credits zero. API-level RESOLVED
          can appear before CTF settlement, and redemption is documented to succeed only after the
          on-chain payout is posted, so the credit is written for value that has not moved. And the
          redeem call signs an empty body while sending JSON, so the canonical message cannot match and
          the call fails while the credit stays written.
EVIDENCE  quoter.py on_resolved(), the credited = credited + payout update, the winningOutcomeIndex == 0
          test, and the headers("POST", "/portfolio/redeem") call with a json= body
FIX       Credit under a key with the payload stored beside it, raising on a differing repeat rather than
          dropping it: quoter.py write_credit(). Both resolution shapes, with the split ratio applied to
          both legs: quoter.py settle(). Redemption as a value-moving instruction with a committed intent
          and a credit deduped on the observed collateral change rather than on the call: quoter.py
          redeem() and observe_redemption(). Unredeemed winnings are reported as their own aged line.
TEST      A redelivered resolution credits once and a split credits both legs:
          tests/test_limitless_client.py test_a_payout_split_credits_both_legs_exactly_once. A timeout on
          redeem credits exactly once across two sweeps and never calls again:
          test_redemption_credits_once_across_a_timeout_and_a_sweep.

FINDING   Orders are signed against the wrong contract on every market but one, priced on a grid the
          venue never published, and quantized through binary floats.
WHY       venue.exchange is per market and is the EIP-712 verifyingContract, so a module constant signs
          valid-looking orders addressed to a contract that is not this market's. Tick size and minimum
          order size are not documented on the market or orderbook pages read on 2026-08-25, so 0.01 is
          Polymarket's grid asserted about a venue that published none. And round(price / TICK) * TICK
          runs on binary floats, where 0.29 % 0.01 == 0.009999999999999974.
EVIDENCE  quoter.py EXCHANGE and its comment; quoter.py TICK; quoter.py module docstring design note 2;
          quoter.py place(), the price rounding line; quoter.py build_order(), int(size * price * 1e6)
FIX       Read the exchange address per market and build the domain from it: quoter.py market_for() and
          build_signed_order(). Tick and minimum size become config keys with no default carrying the
          date they were established against the live API: quoter.py TICK, MIN_SIZE and
          GRID_ESTABLISHED_AT. Quantization and 1e6 scaling are exact and refuse a value that does not
          land on a unit: quoter.py quantize(), units() and amounts(), which also carries the FOK
          takerAmount branch.
TEST      Quantizing a value that is not exactly representable in binary returns the intended multiple, a
          value that is not an exact multiple of 1e-6 raises rather than rounding, and the signed domain
          carries the market's own exchange address.

FINDING   Provider metadata is read once and then trusted for the life of the process, and the mid price
          is read on whichever of two scales the first market happened to use.
WHY       rank.feeRateBps is profile state the venue can change and the signed feeRateBps must equal it,
          so a process-lifetime cache signs a band the account may no longer be in. MARKET is fetched for
          the first slug and supplies the token id and exchange address for every order after that. And
          prices is documented on two scales: decimal fractions between 0 and 1 on a CLOB market and
          percent-style values between 0 and 100 on an AMM market, so an unbranched read sizes an order
          100x wrong in one direction and 0.01x wrong in the other.
EVIDENCE  quoter.py MARKET and PROFILE as module globals loaded in main(); quoter.py main(),
          float(MARKET["prices"][0])
FIX       Per-slug metadata with an age this client owns, and the fee band read in the flow that signs:
          quoter.py market_for() and profile(). The price scale branches on tradeType and refuses an
          unknown one: quoter.py mid_price(). A market status outside the tradeable set refuses rather
          than being mapped onto the closest name we recognise: quoter.py TRADEABLE and submit().
TEST      An AMM market and a CLOB market quoting the same probability produce the same order size, and
          an unrecognised tradeType or status refuses rather than trading.

FINDING   The control that would have caught every finding above was named in a comment and not built.
WHY       "# TODO: reconcile credited payouts against /portfolio/positions and the chain" identifies the
          right control accurately and then writes it as prose. A named risk in a comment is the same
          defect as the missing control.
EVIDENCE  quoter.py on_resolved(), the TODO under the redeem call
FIX       quoter.py reconcile(), a scheduled entrypoint reading through a path independent of the writer,
          naming an authority and a join key per quantity, closing the gate on a break, and alerting to
          quoter.py ALERT_SINK, a config key with no default that raises at import if unset. It also
          enumerates markets the venue reports RESOLVED that have no settlement credit on our side, which
          is the failure that is otherwise silent.
TEST      A planted position break and a planted missing settlement credit are both detected and both
          close the gate: tests/test_limitless_client.py test_reconcile_detects_a_planted_break.

UNRESOLVED: comparison of realised PnL against the venue's own figure (GET /portfolio/positions and
GET /portfolio/history exist, but the reference marks their full field lists unverified as of 2026-08-25,
so there is no named field to compare against yet; position quantity and collateral received are both
reconciled and bracket the number, and this becomes blocking the moment the PnL figure is reported to
anyone outside this process)

UNRESOLVED: a live-path proof (the venue publishes no sandbox, testnet or mock mode, so the offline double
in tests/fixtures/ is the only pre-live evidence and QUOTER_MAX_ORDER_COLLATERAL is the only bound on
being wrong)

VERDICT   SHIP
```

`SHIP` is honest here only because of the last two lines and the cap they name. The reconciliation exists, it
runs on a schedule, and a planted break makes it fire in a test, which is what separates it from the TODO it
replaced. What it cannot yet do is check the one number a reader is most likely to quote back, and the page
says so rather than deleting the row. The day realised PnL is reported outside this process, that unresolved
line is the finding that blocks the release.
