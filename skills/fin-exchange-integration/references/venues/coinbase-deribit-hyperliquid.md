# Coinbase Advanced Trade, Deribit and Hyperliquid

> **Provenance**
> provider: Coinbase Advanced Trade, Deribit and Hyperliquid · surface: order identity and recovery on the three venues whose semantics break the default, plus units, post-only behaviour, cancel-on-disconnect and price validity
> version: as stated in this file's own header, the 2026-08-24 research pass. No API version was recorded per venue.
> verified_at: not established
> sources: https://docs.cdp.coinbase.com/api-reference/advanced-trade-api/rest-api/orders/create-order · https://docs.cdp.coinbase.com/coinbase-app/advanced-trade-apis/websocket/websocket-overview · https://docs.deribit.com/api-reference/trading/private-buy · https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/
> verified: none in this pass. No sentence below was re-read against a source for v0.5.0.
> unverified: all of it. This file predates the provenance requirement and was not re-checked in the v0.5.0 pass, so its claims carry the confidence of their original sourcing and no more, with no date you can check. The body already carries its own list of what the original research did not establish, and that list is still the right one to read before asserting anything from here; this block adds only that nothing on the rest of the file has been re-read since. The URLs above are where a recheck starts; each of them resolved on 2026-08-25, and nothing in any of them was read against a claim in this file.
> revalidate_when: Coinbase removes or restores the lookup by client order id, or changes what a replayed create returns; Deribit adds a uniqueness check to `label`; Hyperliquid documents a dedup guarantee for `cloid`, or changes the nonce window or the significant-figure price rule; any of the three changes its cancel-on-disconnect triggers.

The three venues whose identity semantics break the default. Coinbase Advanced Trade is the **only** venue in
scope with true create-order idempotency (a duplicate `client_order_id` returns the original order) and
because the lookup-by-client-id path is deprecated, the re-POST *is* the query. Deribit has no client order ID
at all: `label` is a free tag with no collision check, so reuse silently creates a second order. Hyperliquid's
`cloid` is a correlation key with no documented dedup guarantee; the real replay guard is `(signer, nonce)`,
and re-signing a retry converts it into a duplicate order. Facts are as of the 2026-08-24 research pass.

## Contents

- The three identity models, side by side: read this before writing any retry branch
- **Coinbase**: replay-returns-original; why re-POST is the only supported query; the byte-identical-body
  requirement; the unreconciled `DUPLICATE_CLIENT_ORDER_ID`; the paginated time-window scan that replaces the
  missing client-order-id filter; legacy `client_oid`'s 404; per-product `sequence_num` and in-band snapshots
- **Deribit**: `label` ≤64 chars with no uniqueness check; the recovery algorithm by label + instrument + time;
  `amount` in USD on inverse/perp; `post_only` defaulting to `true` and silently repricing;
  `enable_cancel_on_disconnect` scope and the three triggers `private/logout` is not one of; the heartbeat and
  the credit bucket as order-cancellation mechanisms; JSON `number` as a transport encoding only; `change_id`
- **Hyperliquid**: `cloid` as correlation only; the `(signer, nonce)` window: 100 highest nonces per signer,
  bounded by T−2d…T+1d; the byte-identical resend and the one line that breaks it; the ≤5-significant-figure /
  ≤`MAX_DECIMALS − szDecimals` price rule with quantizer and worked arithmetic; `iocCancelRejected`
- Recovery endpoints and time bounds, per venue
- What the research does not establish and must not be asserted

---

## The three identity models

| | Coinbase Advanced Trade | Deribit | Hyperliquid |
|---|---|---|---|
| Field | `client_order_id` (required) | `label` (optional tag) | `cloid` (optional 128-bit hex `0x…32 hex`) |
| Length / charset | string; charset not published | **≤64 chars** | exactly 32 hex digits after `0x` |
| Venue-enforced uniqueness | account-scoped | **none** (OpenAPI schema carries `"uniqueKey": ""`) | **none documented** |
| Behaviour on reuse | **returns the original order; creates nothing** | creates a **second order**, silently | undocumented: assume a second order |
| Class | A: idempotent replay | C: no collision check | C: no collision check |
| Query by it? | **deprecated** on Get Order; absent from List Orders | `get_open_orders_by_label` / `get_order_state_by_label` | `info` → `orderStatus` accepts `oid` **or** `cloid` |
| The real replay guard | the `client_order_id` itself | none: you must make the label unique by construction | **`(signer, nonce)`**, not `cloid` |
| Safe ambiguous-submit action | **re-POST the identical create-order** | query by label; never resend under the same label | resend the **byte-identical signed action under the identical nonce** |

Sources: <https://docs.cdp.coinbase.com/api-reference/advanced-trade-api/rest-api/orders/create-order>,
<https://docs.deribit.com/api-reference/trading/private-buy>, and Hyperliquid's exchange-endpoint and
nonces-and-api-wallets pages under <https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/>.

---

## Coinbase Advanced Trade

### Replay returns the original order

> "`client_order_id` string **required**: A unique ID provided for the order. **If the ID provided is not
> unique, the order will not be created and the order corresponding with that ID will be returned instead.**"
> Source: Create Order reference

The only class-A guarantee in the venue set: a blind retry is safe **and** it answers "did my first attempt
create an order?".

### The query path is being removed, so the retry *is* the query

- `GET /api/v3/brokerage/orders/historical/{order_id}` takes `order_id` as a **required path parameter**, and
  its `client_order_id` query parameter is marked **"(Deprecated)"**.
- `GET /api/v3/brokerage/orders/historical/batch` (List Orders) has **no client-order-id filter at all**. Its
  filters are `order_ids`, `product_ids`, `product_type`, `order_status`, `order_side`, `start_date`,
  `end_date`, `asset_filters`, `limit`, `cursor`.

So "query before you retry" is **not implementable here**. Invert it: re-POST.

```python
# The Coinbase recovery path. The retry is the query; the body must never change between attempts.
def submit_coinbase(http, intent, budget_s: float) -> Resolution:
    body = {
        "client_order_id": intent.client_order_id,   # minted ONCE per logical order, fsync'd before the
                                                     # first send, reused verbatim on every attempt
        "product_id":      intent.product_id,
        "side":            intent.side,
        "order_configuration": {"limit_limit_gtc": {
            "base_size": str(intent.size),           # decimal strings, never repr(float)
            "limit_price": str(intent.price), "post_only": True}},
    }
    payload = json.dumps(body, sort_keys=True)       # frozen: a changed body is a DIFFERENT order
    deadline = time.monotonic() + budget_s
    while time.monotonic() < deadline:
        try:
            r = http.post("/api/v3/brokerage/orders", data=payload).json()
        except (Timeout, ConnectionError, HTTP5xx):
            continue                                 # SAFE: the replay is idempotent by contract
        if r.get("success"):
            got = r["success_response"]              # envelope field names: verify against the current ref
            # The returned order may be the ORIGINAL, not this attempt. Prove it is the order you meant.
            assert_matches_intent(got, intent)       # product, side, size, price, config; all seven fields
            return Resolution.CONFIRMED(got)
        reason = r["error_response"].get("new_order_failure_reason")
        if reason == "DUPLICATE_CLIENT_ORDER_ID":    # contradicts replay-returns-original (see below), but
            return resolve_by_scan(http, intent)     # still PROOF the order exists; never re-mint the ID
        return Resolution.REJECTED(reason)           # INSUFFICIENT_FUND / PRODUCT_TRADING_HALTED /
                                                     # UNSUPPORTED_ORDER_CONFIGURATION = definitely not created
    return Resolution.INFLIGHT_UNKNOWN               # full notional in risk; gate the product; escalate
```

Two things that code encodes and prose loses. **The body is frozen with the ID**: the replay returns whatever
order that ID names, so a re-POST with a different price is not a retry and carries no guarantee about which
body won; persist the exact payload alongside the ID. And **verify the returned order against your intent**:
replay-returns-original means the response can describe an order you sent minutes ago with different terms if
your ID minting ever repeats. `assert_matches_intent` is what turns the guarantee into a proof.

### The unreconciled `DUPLICATE_CLIENT_ORDER_ID`

`NewOrderFailureReason` enumerates `DUPLICATE_CLIENT_ORDER_ID` alongside `INSUFFICIENT_FUND`,
`PRODUCT_TRADING_HALTED` and `UNSUPPORTED_ORDER_CONFIGURATION`. That cannot be reconciled with
replay-returns-original in all cases. **Whether the failure reason applies only to batch/preview paths, or is
legacy from Coinbase Exchange/Pro, is unresolved and was not settled by an integration test in the research
pass.** Handle both outcomes, as the code above does. Also unverified: the **retention window** of the
idempotency guarantee; the doc states the behaviour with no expiry, and "behaves as unbounded" is an
observation, not a published policy. Do not build a day-boundary ID scheme that depends on it.

### When the replay itself cannot resolve it

List Orders cannot filter on `client_order_id`, so the fallback is a paginated time-window scan matched
locally, which is why a `not_before`/`not_after` bracket is persisted before the send.

```python
def resolve_by_scan(http, intent):
    cursor, hits = None, []
    while True:                                      # paginate to exhaustion; one call truncates the gap
        page = http.get("/api/v3/brokerage/orders/historical/batch", params={
            "product_ids": intent.product_id,
            "start_date": iso(intent.not_before),    # bracket the send; do not scan "today"
            "end_date": iso(intent.not_after), "limit": 100,
            **({"cursor": cursor} if cursor else {})}).json()
        hits += [o for o in page["orders"] if o["client_order_id"] == intent.client_order_id]
        cursor = page.get("cursor")
        if not page.get("has_next"):
            break
    if len(hits) > 1:
        raise DuplicateOrderBreak(intent.client_order_id, hits)   # gate the product; do not pick one
    if hits:
        return Resolution.CONFIRMED(hits[0])
    # Order endpoints can be empty while the position moved. Fills are the only ground truth.
    return check_fills(http, "/api/v3/brokerage/orders/historical/fills", intent)
```

### Coinbase Exchange (legacy) `client_oid` is a different guarantee

The older Exchange/Pro API *does* support lookup (`client_oid` "must be preceded by the `client:`
namespace") and publishes its failure mode: **"If the order is canceled, and if the order had no matches, the
response might return the status code 404"**
(<https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/orders/get-single-order>). That is a venue
stating in its own documentation that **404 does not mean "never existed"**. Do not port Advanced Trade's
replay assumption onto this API, and do not port this API's 404 handling onto Advanced Trade.

### Feed

`sequence_num` is **per-product** and increments by exactly 1: a gap means messages were dropped; a value lower
than the previous means out-of-order delivery and may be ignored. Coinbase says why the transport is not
enough: "even though a WebSocket connection is over TCP, the WebSocket servers receive market data in a manner
that can result in dropped messages"
(<https://docs.cdp.coinbase.com/coinbase-app/advanced-trade-apis/websocket/websocket-overview>). Coinbase
delivers **in-band** snapshots, unlike Binance spot/futures where a gap forces a REST re-fetch, so a shared
`on_orderbook_message` handler written against Binance mishandles them. On a gap, discard the book and take
the next snapshot; never patch. The measured client-side failure here is hummingbot#7211, "orders are failing on hummingbot but successfully
posted on exchange", a `KeyError: 'order_id'` on the submit path. The venue accepted the orders; an unguarded
field access marked them FAILED locally. An exception raised *after the request left the process* moves the
order to UNKNOWN, never to FAILED.

---

## Deribit

### There is no client order ID

> "`label`: **user defined label for the order (maximum 64 characters)**"

The schema field carries `"uniqueKey": ""`, no uniqueness constraint. The API concedes collisions
itself: `private/edit_by_label` **"works only when there is exactly one open order with the specified label"**,
and `private/cancel_block_rfq_quote` notes **"Mass cancellation by label is not supported."**

The consequence is the whole rule: **the venue will not stop you reusing a label, so reuse silently creates a
second order.** Uniqueness is your responsibility, enforced at construction:

```python
label = f"{strategy_id}-{uuid4().hex}"   # ≤64 chars, asserted at construction. One label per LOGICAL order,
assert len(label) <= 64                  # never per attempt, never derived from (strategy, side, bar_ts).
```

### The recovery algorithm

ID *equality* is unavailable, but a label that is unique by construction gives you an equality-shaped **query**.
The steps, in order, stopping at the first definite answer:

1. `private/get_open_orders_by_label`, scoped by currency + label.
2. `private/get_order_state_by_label`, for an order that may already have left the open set.
3. Order history for the instrument over `[sent_at − skew, now]`.
4. **User trade history** for the instrument over the same window. This is the only rung that speaks to
   economic effect: an order can be invisible in every order endpoint and still have moved your position.

```python
def resolve_deribit(rpc, intent):
    # Exact parameter sets must be read from docs.deribit.com; the load-bearing part is the sequence and
    # the collision handling, not the argument spelling.
    args = {"currency": intent.currency, "label": intent.label}
    open_orders = rpc("private/get_open_orders_by_label", args)["result"]
    if len(open_orders) > 1:
        raise LabelCollisionBreak(intent.label, open_orders)   # you already double-sent; gate the instrument
    if open_orders:
        return Resolution.CONFIRMED(open_orders[0])
    state = rpc("private/get_order_state_by_label", args)["result"]
    return Resolution.CONFIRMED(state) if state else scan_trade_history(
        rpc, intent.instrument_name, intent.not_before, intent.not_after)
```

`len(open_orders) > 1` is not a warning. It is proof two orders exist under one label, unrecoverable by label;
fall back to `order_id` and reconcile both.

### Units: `amount` is USD on the inverse products

`amount` is **USD** on perpetual and inverse futures, and base currency on options and linear products;
`contracts` is the alternative field and **the two must agree if both are sent**. The measured bug is a
`/price` that should not be there: on BTC-PERPETUAL, `amount = usd_notional / price` turns a $50,000 order at
104,000 into `0.4808` (forty-eight cents of notional) while `amount = usd_notional` is already correct.
Attach a unit to every size crossing a module boundary (base / quote / contracts / USD-notional) and convert
only inside the venue adapter, from the venue's own `contract_size` in `public/get_instruments`.

### `post_only` defaults to `true` and silently reprices

> "If the new price would cause the order to be filled immediately (as taker), the price will be changed to be
> just below the spread"

`reject_post_only` (default `false`) makes the venue reject instead. So a bot sending a crossing limit price
on Deribit with default parameters gets **no error and no fill at its price**; it gets a resting order at a
price it never chose, and every downstream calculation keyed on `intent.price` is now wrong. Send
`{"post_only": True, "reject_post_only": True}` whenever the strategy depends on its price. Re-verify the
default per endpoint (`private/sell`, `private/edit`) and per SDK: several third-party clients declare
`post_only=False` in their own signatures, changing behaviour by whether the field is sent explicitly.

Handle the three would-cross outcomes as three different states across venues: Deribit **repriced**,
Hyperliquid `Alo` **rejected**, Binance futures `GTX` **cancelled**.

### Cancel-on-disconnect, and the shutdown that does not trigger it

`private/enable_cancel_on_disconnect`: auth scope `account:read_write`, `scope` is `connection` (default) or
`account`; `connection` scope is WebSocket-only, and COD is not supported over HTTP. It fires on (1) proper TCP
termination, (2) a connection closed after **10 minutes of inactivity**, (3) heartbeat-detected disconnection.

It does **not** fire on `private/logout`; Deribit publishes the matrix explicitly: graceful logout leaves
orders live even with COD enabled, so code calling `logout` on `SIGTERM` believing COD will clean up leaves
resting orders working after the process exits. FIX sessions use tag 9001 `CancelOnDisconnect` / tag 9003
`DontCancelOnDisconnect`. Unlike Binance (`countdownTime` in **milliseconds**), Kraken and OKX (**seconds**)
and Bybit (`timeWindow` 3–300 s), **Deribit is connection-scoped with no timer at all**: a cross-venue
`arm_dead_mans_switch(seconds=30)` has no Deribit implementation and silently no-ops.

### The heartbeat is an order-cancellation mechanism

`public/set_heartbeat` (interval ≥10 s) makes the server send `heartbeat` and `test_request`; the client must
answer `test_request` with `/api/v2/public/test`. "If your software fails to do so, the API server will
**immediately close the connection**." With COD enabled, that closure cancels every open order, so on Deribit
a blocking event loop (a synchronous indicator computation, a GC pause, a slow consumer) is an
order-cancellation mechanism. Answer from a task that cannot be blocked by the trading loop, but gate it on
that loop's *liveness*: a heartbeat sent by an independent thread while the strategy is wedged keeps the switch
disarmed exactly when it is needed.

The rate limiter reaches the same outcome by another route. Credits are a leaky bucket: non-matching-engine
default cost 500, cap 50 000, refill 10 000/s ⇒ ~20 rps sustained, ~100 burst; matching-engine requests are
tiered by trailing-7-day volume (>$25M: 30/s burst 100; >$5M: 20/s burst 50; >$1M: 10/s burst 30; ≤$1M: 5/s
burst 20), and `public/get_instruments` alone costs **10 000 credits**. Exceeding returns `too_many_requests`
(**10028**) **and terminates the session**, which, with COD on, cancels your orders. The web UI shares the
pool, so a human refreshing a dashboard can cancel a bot's book.

### JSON `number` is a transport encoding, not the record

Deribit's `private/buy` schema declares **`amount`: JSON type `number`** and **`price`: JSON type `number`**.
RFC 8259 §6 limits interoperable JSON numbers to binary64, so by the time your parser hands you the field the
value was already a float. That does not license floats in your model. Compute and store price and size
**exact** (integer minor units, or `Decimal` built only from strings); convert **once**, at the serialize step,
asserting the encoding round-trips (`Decimal(repr(f)) == exact`); never persist the float as the record,
re-deriving it from Deribit's own execution report; and parse inbound with a decimal-preserving decoder;
`Decimal(str(json_number))` is **not** a fix, because `str()` of an already-damaged double is faithful to the
damage.

### Book

`change_id` / `prev_change_id`. The first `snapshot` has **no** `prev_change_id`; on every subsequent message
`prev_change_id` must equal the last `change_id` applied. A mismatch means messages were missed: discard the
book and re-snapshot, never patch.

---

## Hyperliquid

### `cloid` is a correlation key; the nonce is the replay guard

The entire published specification of `cloid` is one sentence: "**Client Order ID (cloid) is an optional
128 bit hex string**, e.g. 0x1234567890abcdef1234567890abcdef". No uniqueness statement, no collision
behaviour, no window. It is a cancel/modify selector (`cancelByCloid`;
`modify` accepts `Number | Cloid`) and an `orderStatus` lookup key, and nothing more. NautilusTrader uses
`cloid` to dedupe *inbound status reports*, a strictly weaker claim than create-order dedup. **Whether a
reused `cloid` is rejected or silently creates a second order is undocumented; assume the latter.**

The actual idempotency mechanism lives at the signature layer:

> "On Hyperliquid, **the 100 highest nonces are stored per address. Every new transaction must have nonce
> larger than the smallest nonce in this set and also never have been used before.** Nonces are tracked per
> signer, which is the user address if signed with private key of the address, or the agent address if signed
> with an API wallet. **Nonces must be within (T - 2 days, T + 1 day)**, where T is the unix millisecond
> timestamp on the block of the transaction."

> "Once an agent is deregistered, its used nonce state may be pruned … **previously signed actions can be
> replayed once the nonce set is pruned.**"

Read the window carefully: it is **not a duration**. It is *"until 100 further nonces have been consumed by
that signer"*, additionally bounded by T−2d…T+1d. For a maker batching every 100 ms that is under ten seconds,
and under one second if each batch consumes several; the replay guarantee can expire between your timeout and
your retry.

### The safe retry, and the one line that breaks it

```python
# Persisted BEFORE the socket write: the nonce, the exact signed action bytes, and the signature.
intent = {
    "cloid":     "0x" + uuid4().hex,          # correlation only; never relied on for dedup
    "nonce":     nonce,                       # ms timestamp, strictly increasing per signer
    "action":    action,                      # the exact dict that was signed
    "signature": sig,                         # signing may be non-deterministic: you cannot rebuild this
}
db.commit(intent)                             # fsync before send

def retry_hyperliquid(http, intent):
    # SAFE: byte-identical resend. Rejected as a used nonce if the first attempt landed; accepted if not.
    return http.post("/exchange", json={"action":    intent["action"],
                                        "nonce":     intent["nonce"],      # IDENTICAL
                                        "signature": intent["signature"]}) # IDENTICAL

def retry_hyperliquid_WRONG(http, intent, wallet):
    nonce = int(time.time() * 1000)           # ← this line converts a safe retry into a second order
    return http.post("/exchange", json=sign(intent["action"], nonce, wallet))
```

The wrong version is what every "just refresh the timestamp and re-sign" helper does, and it is
indistinguishable from the right version in a review that greps for `cloid`. Two operational consequences. **Do not reuse API-wallet (agent) addresses**: once an agent is deregistered
its nonce state may be pruned and previously signed actions become replayable; generate a new agent wallet per
deployment. **The nonce is per signer**: two processes sharing one agent wallet share one 100-nonce set and
evict each other's retry windows, so run one signer per order-emitting process.

Recovery query: `info` → `orderStatus` accepts `oid` **or** `cloid`, but the authoritative retry primitive is
the nonce, not the query. The query says what happened; the nonce says what a resend will do.

### Price validity is significant figures, not tick size

Two constraints apply **simultaneously**: **≤ 5 significant figures** and **≤ `MAX_DECIMALS − szDecimals`
decimal places**, with `MAX_DECIMALS` = **6** (perps) / **8** (spot), and one exemption overrides the first:
"**Integer prices are always allowed, regardless of the number of significant figures**". Sizes are rounded to
`szDecimals`.

```python
from decimal import Decimal, ROUND_DOWN, ROUND_UP

def hl_price(px: Decimal, sz_decimals: int, is_spot: bool, side: str) -> str:
    """Quantize toward validity. Buy rounds down, sell rounds up; never to nearest, which can make
    the order more aggressive than the strategy intended."""
    mode = ROUND_DOWN if side == "BUY" else ROUND_UP
    if px == px.to_integral_value():                 # integer prices skip the sig-fig rule entirely
        return str(int(px))
    max_dec = (8 if is_spot else 6) - sz_decimals
    five_sig = px.quantize(Decimal(1).scaleb(px.adjusted() - 4), rounding=mode)   # 5 significant figures
    out = five_sig.quantize(Decimal(1).scaleb(-max_dec), rounding=mode)           # then the decimal cap
    if out <= 0:                                     # a formatter must be total: valid price, or raise
        raise ValueError(f"quantized to {out} from {px}")
    return format(out.normalize(), "f")
```

Worked, on a perp with `szDecimals = 2` ⇒ `max_dec = 6 − 2 = 4`:

| Input price | 5 sig figs | ≤4 decimals | Sent | Why |
|---|---|---|---|---|
| `0.0312345` | `0.031234` | `0.0312` | `0.0312` | the decimal cap binds, not the sig-fig cap |
| `104237.5` | `104240` | `104240` | `104240` | sig figs bind; result is an integer, which is always legal |
| `1.234567` | `1.2346` | `1.2346` | `1.2346` | both caps satisfied by the sig-fig step |
| `12.3` | `12.3` | `12.3` | `12.3` | already legal: quantization must be idempotent |

**Do not reuse a tick-size rounder here.** ccxt#23516 is the measured failure: `decimal_to_precision` called
with `counting_mode=exchange.precisionMode` (`TICK_SIZE`) against a precision value that was an integer decimal
count (`5`) **silently returned `0`** for a price of `0.18119111`. A zero price on a live order is an instant
rejection or a fill at an absurd level. Assert the output is `> 0` and re-parses legal before it reaches the
signer.

### Order statuses

`resting` (with `oid`), `filled` (carries `totalSz`, `avgPx`, `oid`), `error`. TIF is `Alo` (post-only),
`Ioc`, `Gtc`; `r` is reduce-only; `grouping` is `na` / `normalTpsl` / `positionTpsl`. **An IOC that finds no
match is a *rejection*, not an expiry**: it reports `iocCancelRejected`, which NautilusTrader surfaces as
`OrderRejected`, so a state machine mapping "IOC with no fill" to `EXPIRED` never sees it. Post-only that would
cross is likewise **rejected**, not repriced: the opposite of Deribit.

## Recovery endpoints and time bounds

| Venue | Endpoint | Accepts the client identifier? | Bound |
|---|---|---|---|
| Coinbase AT | `POST /api/v3/brokerage/orders` (identical body) | **the replay is the query** | none published; behaves as unbounded, **unverified** |
| Coinbase AT | `GET /orders/historical/{order_id}` | `client_order_id` **deprecated** | requires the venue's `order_id` |
| Coinbase AT | `GET /orders/historical/batch` + `cursor` | **no filter** | `start_date`/`end_date`; match locally, paginate to exhaustion |
| Coinbase AT | `GET /orders/historical/fills` | no | the only rung proving economic effect |
| Coinbase Exchange | Get Single Order | yes, `client:{client_oid}` | **404 for cancelled-with-no-fills**: not proof of non-creation |
| Deribit | `private/get_open_orders_by_label` | by label | open orders only; >1 result is a break |
| Deribit | `private/get_order_state_by_label` | by label | a collision returns ambiguity |
| Deribit | order / user-trade history by instrument + time | no | window built from your persisted `sent_at` |
| Hyperliquid | `info` → `orderStatus` | by `cloid` or `oid` | the resend under the identical nonce is the stronger primitive |

---

## Not established by the research: do not assert

- **Coinbase Advanced Trade rate-limit shape** (per-endpoint quotas, headers, ban ladder) and **Hyperliquid
  rate limits** (address-weighted or otherwise). No primary source captured for either; build the limiter from
  the live documentation, not from this file.
- **Hyperliquid `l2Book` / feed sequencing.** Not sourced. Do not assume Coinbase's `sequence_num` or Deribit's
  `change_id` model transfers. **Hyperliquid `cloid` collision behaviour** is likewise undocumented.
- **Coinbase idempotency retention** and the `DUPLICATE_CLIENT_ORDER_ID` contradiction. Both unresolved.
- **Deribit `post_only` default per endpoint** (`true` is documented for `private/buy` only) and the **exact
  parameter sets** of the methods in the recovery snippet. The sequence there is load-bearing; the argument
  spelling must come from the current reference.
