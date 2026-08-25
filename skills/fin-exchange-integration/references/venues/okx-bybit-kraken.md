# OKX, Bybit and Kraken

The three venues whose client-order-ID and recovery semantics are most often assumed to match Binance's and do
not. OKX states its own reuse rule outright and keeps cancelled-incomplete orders in orders-history for only
**2 hours**. Bybit documents uniqueness with no retention window at all. Kraken publishes two different windows
for the same field on two different protocols. Each venue's identifier field, uniqueness scope, retention
bound, recovery endpoints, unit model, book algorithm and limiter shape is below; all venue text was read on
**2026-08-24**; re-verify before keying production behaviour on any of it.

## Contents

- The window table: uniqueness scope and post-terminal window, all four protocol rows
- OKX `clOrdId`: "only applied towards all pending orders", reuse after terminal state, charset
- OKX recovery and the 2-hour cliff: endpoint-by-endpoint retention
- OKX units: `sz` in contracts vs base, `ctMult × ctVal`, `tgtCcy`
- OKX book: `seqId`/`prevSeqId`, and the checksum that is always 0 from 2026-06-23
- OKX STP: `cancel_maker` default, set at master-account level, applied across sub-accounts
- Bybit `orderLinkId`: `110072` / `170141`, `orderId` precedence, the empty-ID case
- Bybit ack ≠ execution
- Bybit recovery: the 500-order realtime cap and the 24-hour cancelled window
- Bybit book: `u == 1` is a restart, `seq` is the cross-depth comparator
- Bybit order quirks: reduce-only, `closeOnTrigger`, PostOnly-cancels
- Kraken `cl_ord_id`: three formats, one 128-bit integer, two published windows
- Kraken recovery: `QueryOrders` does not accept `cl_ord_id`
- Kraken book: the CRC32 checksum written out, with reproducible arithmetic
- Rate-limit shapes: OKX shared REST+WS, Bybit IP+UID, Kraken decaying counters with order-age penalties
- Minting one ID for all three

## The window table

The question that decides your retry branch is not "does the venue reject duplicates" (all three do) but
**for how long after the order terminates**. While the window is open the rejection itself proves the order
exists; once it has closed, resending creates a *second* order.

| Venue / protocol | Field | Uniqueness scope, in the venue's words | Window after terminal state | Collision response |
|---|---|---|---|---|
| OKX v5 | `clOrdId` | "unique among all currently pending (live or partially_filled) orders in the account" | **zero**: "the same clOrdId may be reused for a new order" | rejection (`51xxx` family; exact code **unverified**) |
| Bybit v5 | `orderLinkId` | "always unique": scope not defined | **undocumented, unverified** | `110072` (UTA/derivatives), `170141` (spot) |
| Kraken REST/WS | `cl_ord_id` | "across open orders … per client" | **zero** | rejection |
| Kraken FIX | `ClOrdID(11)` | "across open orders **and FIX session**" | **rest of the FIX session** | rejection |

Sources: OKX Place-order `clOrdId` note and <https://www.okx.com/docs-v5/trick_en/>; Bybit
create-order and error pages (`docs/v5/order/create-order`, `docs/v5/error`); Kraken
<https://docs.kraken.com/api/docs/guides/spot-clordid/>.

Kraken FIX is the only row here that licenses a resend, and only **for the life of that session**. A reconnect
starts a new session and ends the guarantee, so it must be keyed on the session id, not on the process.

## OKX: `clOrdId`

OKX writes the rule the other venues leave you to infer:

> "`clOrdId` is a user-defined unique order identifier at the User ID level. … **clOrdId must be unique among
> all currently pending (live or partially_filled) orders in the account. Once an order reaches a terminal
> state (filled, canceled, mmp_canceled), the same clOrdId may be reused for a new order. Uniqueness is not
> enforced historically: GET /api/v5/trade/order returns only the latest match when multiple orders share a
> clOrdId.**"
> Source: OKX v5 Place order, `clOrdId` field note

and in the tricks guide: *"clOrdId uniqueness check is only applied towards all **pending** orders."*

1. **Never resubmit on an ambiguous response.** The guarantee is alive exactly while the order is open: the
   case where `orders-pending` already shows it to you and you did not need it.
2. **Reuse poisons the query path.** `GET /api/v5/trade/order` returns only the latest match, so reusing an ID
   after termination makes every future lookup of it resolve to the wrong order, silently and permanently.
   `strategy-symbol-side` and `strategy-bar_timestamp` schemes destroy your handle on history OKX still holds.

**Charset:** ≤32 case-sensitive alphanumerics. NautilusTrader's OKX adapter reports hyphens are rejected in
practice (<https://nautilustrader.io/docs/latest/integrations/okx/>), so a dashed UUID that Binance and Bybit
both accept fails here.

**Rejection code:** the research behind this file establishes only that a duplicate is rejected in the `51xxx`
family; the specific code is **not established**. Read it off OKX's error page before writing a branch that
treats it as a business rejection rather than UNKNOWN. Over-applying UNKNOWN is safe; reading a transport
failure as a duplicate rejection is not.

**A 200 is not an execution, and a 200 on cancel is not a cancellation.** OKX: *"Successful response only means
the request has been accepted by the exchange."* The cancel is confirmed only when the `orders` channel shows
`"state":"canceled"`.

## OKX: recovery and the 2-hour cliff

| Endpoint | Takes `clOrdId` | Population | Retention |
|---|---|---|---|
| `GET /api/v5/trade/order` | yes, with `instId` | one order | "only the latest one will be returned" when the ID is shared |
| `GET /api/v5/trade/orders-pending` | filter | `live` + `partially_filled` only | while open |
| `GET /api/v5/trade/orders-history` | filter | terminal orders | **7 days, but "the incomplete orders that have been canceled are only reserved for 2 hours"** |
| `GET /api/v5/trade/orders-history-archive` | filter | terminal orders | 3 months |
| `GET /api/v5/trade/fills` | n/a | executions | 3 days |
| `GET /api/v5/trade/fills-history` | n/a | executions | 3 months |

**The 2-hour reservation is the tightest window in the venue corpus.** An order cancelled having never filled
leaves `orders-history` two hours later, and the archive and fills endpoints cover only the *filled*
population. A nightly reconciliation finds neither the order nor a fill, and reading "no order, no fill" as
"never created" is wrong for every cancelled-incomplete order older than two hours.

- **The ambiguity ladder runs inside the 2-hour bound**, not on the reconciliation schedule: an
  `INFLIGHT_UNKNOWN` order on OKX has a deadline in minutes, not the open-ended "hold and escalate" a 3-day
  window elsewhere tolerates. Your persisted intent row is the audit path for this population.
- `GET /api/v5/trade/order` requires `instId` alongside `clOrdId`, so that row must carry the instrument in the
  exact form OKX accepts, not a normalised internal symbol.

## OKX units: `sz`, `ctVal`, `tgtCcy`

`sz` means two different things on the same venue:

| Instrument type | `sz` unit | Economic size |
|---|---|---|
| SWAP / FUTURES / OPTION | **number of contracts** | `contracts × ctMult × ctVal` (`ctValCcy` names the unit) |
| SPOT / MARGIN | **base currency** | identity |

NautilusTrader models the OKX multiplier as `ctMult × ctVal`. The recurring defect is applying it twice (in an
adapter and again in strategy code), a clean multiplier on position size and on every risk check downstream.
Attach the unit at the type level (`Contracts`, `BaseQty`, `UsdNotional`); convert only at the adapter boundary.

`tgtCcy` selects whether a **spot market order**'s `sz` is denominated in base or quote (`base_ccy` /
`quote_ccy`). The per-side defaults are **not established by the research behind this file**; read them off
the current Place-order page, then **send `tgtCcy` explicitly on every spot market order** rather than relying
on a default. A buy sized in quote when you meant base is wrong by the price.

## OKX book: `seqId`/`prevSeqId`, and the checksum that is now always 0

> "the checksum field will remain present in push messages but **will always return 0 and must no longer be
> used for data integrity validation**"
> Source: <https://www.okx.com/en-sg/help/okx-order-book-channels-checksum-field-deprecation>

Affected channels: `books`, `books-l2-tbt`, `books50-l2-tbt`. Demo **2026-06-02**, production **2026-06-23**.
`books5` and `bbo-tbt` are unaffected; `books-rpi` never carried one. Integrity moves to `seqId`/`prevSeqId`.

Both obvious guard shapes fail on the changeover day, in opposite directions:

```python
if computed_crc != msg["checksum"]: resync()                        # WRONG: resyncs on every frame
if msg.get("checksum") and computed_crc != msg["checksum"]: resync()  # WRONG: 0 is falsy, stops validating

cs = msg.get("checksum")                                            # RIGHT: the field's loss is an event
if cs is None or cs == 0:
    raise IntegrityFieldRetired("OKX checksum is 0; seqId/prevSeqId is the only integrity signal")
```

The sequence rule, as implemented by an operator: `prevSeqId` must equal the `seqId` you last applied. On
mismatch, **drop the frame and suppress all further updates until a fresh snapshot arrives with
`prevSeqId: -1`**; do not patch, do not interpolate, do not fetch the missing levels.

```python
def on_books_update(self, msg):
    prev, cur = msg["prevSeqId"], msg["seqId"]
    if prev == -1:                                  # snapshot
        self.book.replace(msg); self.applied = cur; self.suppressed = False; return
    if self.suppressed: return                      # stay dark until the next prevSeqId == -1
    if prev != self.applied:
        self.suppressed = True
        self.book.invalidate()                      # gates order submission, not just a log line
        self.request_snapshot(); return
    self.book.apply(msg); self.applied = cur
```

**Unresolved:** whether `seqId` can legitimately decrease; secondary sources describe resets, and OKX's
order-book channel page is a JS-rendered SPA that was not readable in the verification pass. The rule above is
safe under either answer: it never trusts a frame whose linkage it cannot verify.

## OKX: STP is a master-account setting

Modes are `cancel_maker` (default), `cancel_taker`, `cancel_both`, configured at the **master-account level and
applied across sub-accounts** (<https://www.okx.com/docs-v5/trick_en/>). So a setup quoting from sub-account A
and taking from sub-account B is one STP scope to OKX: under the default `cancel_maker`, B's taker flow
**cancels A's resting quotes**, not a rejection the taker sees, but a cancellation of orders the quoter
believes are working. Treat STP-originated cancellations as **non-fills** in reconciliation and as an inventory
event in the quoter.

## Bybit: `orderLinkId`

> "`orderLinkId` … A max of 36 characters. Combinations of numbers, letters (upper and lower cases), dashes,
> and underscores are supported. Futures, Perps & Spot: orderLinkId rules: optional param **always unique**.
> Options orderLinkId rules: required param **always unique**"
> Source: <https://bybit-exchange.github.io/docs/v5/order/create-order>

"Always unique" is an **instruction to the client**, not a published server retention policy, the same
category as FIX's "uniqueness must be guaranteed within a single trading day". How long Bybit remembers a
terminated `orderLinkId` is **undocumented and unverified**. Do not build a retry on it.

| Code | Meaning | Category |
|---|---|---|
| `110072` | "OrderLinkedID is duplicate" (UTA / derivatives) | business rejection: proves an order exists, not which attempt made it |
| `170141` | "Duplicate clientOrderId" (spot) | same |
| `10006` | per-UID rate limit | UNKNOWN on an order endpoint |
| `20006` | duplicate `reqId` on the WebSocket Trade channel | request-level, not order-level |
| `10403` | connection-level request-rate breach | UNKNOWN |
| HTTP `403` "access too frequent" | IP limiter | **terminate all sessions, wait ≥10 min**; retrying extends the ban |

**`orderId` takes precedence over `orderLinkId`** when both are sent, the mirror image of Binance, which
returns `-2039 CLIENT_ORDER_ID_INVALID` when the two disagree. Sending both *detects* a mapping bug on Binance
and *hides* one on Bybit: Bybit acts on the `orderId` and your `orderLinkId` mapping stays wrong. Send one.

**An empty `orderLinkId` marks a venue-generated fill**: liquidation, ADL, settlement
(<https://nautilustrader.io/docs/latest/integrations/bybit/>). Filtering "orders that are mine" by "orders whose
client ID I generated" excludes exactly the events that move your position without your consent. Verify the
marker against Bybit's current docs before keying on it.

## Bybit: ack ≠ execution

> "The ack of create/amend/cancel order request indicates that the request is successfully accepted"
> Source: <https://bybit-exchange.github.io/docs/v5/websocket/trade/guideline>

`retCode: 0` / `retMsg: "OK"` is acceptance, not a fill and not a cancellation; the economic outcome arrives on
the private `order` and `execution` streams. Same page: `reqId` ≤36 characters and duplicates return `20006`;
`X-BAPI-RECV-WINDOW` defaults to **5000 ms**, so clock skew past that rejects every order at the worst moment.

## Bybit recovery: the 500-order realtime cap and the 24-hour cancelled window

| Endpoint | Population | Bound |
|---|---|---|
| `GET /v5/order/realtime?orderLinkId=` | unfilled / partially filled, **plus the most recent 500 closed** (Cancelled, Filled) | 500 closed orders, account-wide |
| `GET /v5/order/history?orderLinkId=` | closed orders | **last 7 days** for all closed states *except* Cancelled / Rejected / Deactivated; **last 24 hours** for those three; **beyond 7 days, only orders that have fills**. Per query, `endTime - startTime <= 7 days` |

- **The 500 is a rolling account-wide cap, not per-symbol.** A quoting strategy evicts a closed order from
  `realtime` within minutes; it is a liveness endpoint, not a recovery endpoint.
- **A fully-cancelled order is queryable for 24 hours, then gone from both endpoints**: OKX's 2-hour cliff,
  four times wider. Any cadence slower than daily has lost that population.
- The restart caveat: *"After a server release or restart, filled, cancelled, and rejected orders of Unified
  account should only be queried through order history."*

So the ladder is: private stream → `history` (never `realtime`) for anything terminal → executions. Encode the
7-day per-query bound as a loop and page until the venue returns fewer rows than the page size; a row count at
the documented cap is a hole, not an empty result.

## Bybit book: `u == 1` is a restart, `seq` is the comparator

From <https://bybit-exchange.github.io/docs/v5/websocket/public/orderbook>:

- `type` is `snapshot` or `delta`.
- **`u == 1` means the service restarted** and the message is a fresh snapshot that must **overwrite** the
  local book. It is not update number 1 in a sequence you can continue.
- For `orderbook.1`, a snapshot is re-emitted with the **same `u`** if nothing changed for 3 seconds. A
  repeated `u` there is normal, not a gap.
- **`seq` is a separate cross-depth comparator**: "for the smaller `seq`, the data is generated earlier". It is
  how you order an `orderbook.1` message against an `orderbook.50` message for the same instrument. `u` cannot.

Gap detection built solely on "`u` strictly increases" does both wrong things at once: false resyncs on the
level-1 same-`u` re-send, and a missed restart signal.

```python
def on_depth(self, msg):
    u, seq = msg["data"]["u"], msg["data"]["seq"]
    if msg["type"] == "snapshot" or u == 1:
        self.book.replace(msg["data"]); self.u = u; self.seq = seq; return
    if self.u is None or u != self.u + 1:
        self.book.invalidate(); self.resubscribe(); return   # discard, never patch
    self.book.apply(msg["data"]); self.u, self.seq = u, seq
```

## Bybit: reduce-only, `closeOnTrigger`, PostOnly

- **Reduce-only is unsupported on Spot** (<https://nautilustrader.io/docs/latest/integrations/bybit/>).
- **An oversized reduce-only order is split into multiple orders**, not rejected. One `orderLinkId` now maps to
  more than one venue order, and a reconciler assuming a 1:1 client-ID-to-order mapping breaks here.
- **`closeOnTrigger` can cancel or reduce your *other* orders** to make room; orders you never touched reach a
  terminal state, and that is not "the user cancelled it".
- **PostOnly that would cross is *cancelled*, not rejected.** Binance spot `LIMIT_MAKER` rejects. Two different
  state machines: a cancelled order emits a terminal `Cancelled` you must distinguish from a user cancel; a
  rejected order may never appear in open orders at all.
- `positionIdx` is `0`/`1`/`2` and position mode is account-wide. Key positions on `(symbol, positionIdx)`.

## Kraken `cl_ord_id`: three formats, one 128-bit integer, two windows

From <https://docs.kraken.com/api/docs/guides/spot-clordid/>:

- Three accepted formats: **dashed UUID**, **32-hex**, **free ASCII ≤18 characters**.
- **Stored internally as a 128-bit integer.**
- **Mutually exclusive with `userref`**: one or the other, never both.
- *"Kraken verifies cl_ord_id uniqueness across open orders for each client. **FIX protocol extends this
  uniqueness check to across open orders and FIX session.**"*
- The guide's comparison table: `cl_ord_id` → "Open orders (+ FIX session) per client"; Kraken Id → "Open and
  closed orders for all clients"; `userref` → "None". And "Good For, Kraken Id: **Record keeping (unique
  across all orders over time)**".

Read the last row as the venue naming its durable identifier: `cl_ord_id` is the correlation key for the life
of an order; the Kraken `txid` is what belongs in your ledger.

Because the field is normalised into a 128-bit integer, **do not assume byte-for-byte round-tripping of a free
ASCII ID**: compare what the venue echoes against what you sent, at first response, and treat a mismatch as a
mapping bug. (Inference from the documented storage type, not documented behaviour; worth one assertion.)
The FIX row is the one usable resend guarantee in this file, and it is session-scoped:

```python
if venue == "kraken" and protocol == "fix" and order.fix_session_id == self.session.id:
    resend(order)                # a duplicate is rejected, and the rejection proves it landed
else: resolve_by_query(order)    # every other row in the window table
```

CCXT caveat: the unified `clientOrderId` was mapped to Kraken's integer `userref`, **not** the native
`cl_ord_id` (ccxt#23370). "I set `clientOrderId`" does not mean the venue holds your client order ID.

## Kraken recovery: `QueryOrders` does not take `cl_ord_id`

| Endpoint | Takes `cl_ord_id` | Notes |
|---|---|---|
| `POST /0/private/OpenOrders` | **yes**: "Restrict results to given client order id" | open orders only |
| `POST /0/private/ClosedOrders` | **yes**, plus `start` / `end` / `ofs` / `closetime` | paginate on `ofs` |
| `POST /0/private/QueryOrders` | **no: requires `txid`**; accepts `userref` but not `cl_ord_id` | usable only once you hold the Kraken order id |

The client-ID recovery path is therefore `OpenOrders(cl_ord_id)` → `ClosedOrders(cl_ord_id, start=sent_at−skew)`,
with `QueryOrders` as a second hop once a `txid` is resolved. Code reaching for `QueryOrders` as its "query by
ID" step has no ID to query with.

**Unresolved, worth an integration test on your own account:** whether a *closed* order stays queryable by
`cl_ord_id`, and whether a closed order's ID is reusable; the guide states the scope and is silent on both.
If closed orders are not queryable by `cl_ord_id`, a timeout that coincided with an immediate fill cannot be
resolved by ID at all, and trade history is your only rung.

## Kraken: the CRC32 book checksum, written out

Two facts that both have to hold (<https://docs.kraken.com/api/docs/websocket-v2/book>,
<https://docs.kraken.com/api/docs/guides/spot-ws-book-v2/>):

1. **The checksum covers exactly the top 10 levels per side, regardless of the depth you subscribed to**: 10,
   25, 100, 500 or 1000. Checksum a 100-deep book and you mismatch on every message.
2. **It is computed over the *string* forms.** The guide instructs: *"Parse `price` and `qty` fields using a
   decimal or string decoder to preserve full precision."* Kraken v2 sends these as JSON **numbers**; a default
   parser turns `0.10000000` into float `0.1`, the trailing zeros vanish, and the checksum never matches again.

```
for each of the top 10 ASKS, ASCENDING by price:   emit price_str, then qty_str
for each of the top 10 BIDS, DESCENDING by price:  emit price_str, then qty_str
  where each token = the string with "." removed, then leading "0"s removed
concatenate in that order, encode UTF-8, CRC32, cast to UNSIGNED 32-bit, compare to `checksum`
```

Reproducible in a REPL (three levels a side for brevity; slice at 10 in production):

```python
import json, zlib
from decimal import Decimal

raw = ('{"channel":"book","type":"snapshot","data":[{"symbol":"BTC/USD",'
       '"bids":[{"price":45283.5,"qty":0.10000000},{"price":45283.4,"qty":1.20000000},'
                '{"price":45283.0,"qty":0.00500000}],'
       '"asks":[{"price":45284.1,"qty":0.05000000},{"price":45284.5,"qty":2.00000000},'
                '{"price":45290.0,"qty":0.30000000}]}]}')

d = json.loads(raw, parse_float=Decimal)["data"][0]   # Decimal keeps "0.10000000" intact
tok = lambda v: str(v).replace(".", "").lstrip("0")

parts = []
for lvl in d["asks"][:10]:                            # asks ascending
    parts += [tok(lvl["price"]), tok(lvl["qty"])]
for lvl in d["bids"][:10]:                            # bids descending
    parts += [tok(lvl["price"]), tok(lvl["qty"])]

payload = "".join(parts)
# '45284150000004528452000000004529003000000045283510000000452834120000000452830500000'
assert zlib.crc32(payload.encode("utf-8")) & 0xFFFFFFFF == 2158453468
```

Run the identical code with a plain `json.loads(raw)` and the payload collapses to
`'45284154528452045290034528351452834124528305'`, CRC32 `1275150685`: **same book, unrelated integer, on the
first message.** Not a subtle drift you debug later.

- **Sort numerically, not lexically**: `"45283.5"` sorts before `"9000.0"` as a string. **A level whose `qty`
  is 0 is a delete:** remove it before checksumming, since `"0".lstrip("0")` is empty and shortens the payload.
- **You checksum your reconstructed book**, using the last `qty` string received per resting level; retain the
  string form of every level, not only the top 10.
- On mismatch: **discard the book and re-subscribe.** The checksum says the book is wrong, not which level.

## Rate-limit shapes

| | OKX v5 | Bybit v5 | Kraken Spot |
|---|---|---|---|
| Model | per-endpoint buckets; **trading limits shared across REST and WebSocket** | per-UID rolling 1 s window **per endpoint**, stacked on a per-IP cap | **decaying counters**: two separate ones |
| Key | User ID / sub-account | UID **and** IP | API key (REST); API key + pair (trading) |
| Headline caps | sub-accounts capped at 1000 order requests / 2 s (`50061`) | IP 600 req / 5 s; connection level 3000 req/s | REST counter max 15, decay −0.33/s (Starter); 20, −0.5 (Intermediate); 20, −1 (Pro) |
| Breach signal | `50011` generic, `50061` sub-account order cap | `10006` (UID), HTTP `403` (IP), `10403` (connection) | `EOrder:Rate limit exceeded`, `EOrder:Orders limit exceeded` |
| Recovery | back off; never resubmit the order | **HTTP 403 ⇒ terminate all HTTP sessions, wait ≥10 minutes**; a retry loop extends the ban | wait for the counter to decay |
| Headers | n/a | `X-Bapi-Limit`, `X-Bapi-Limit-Status`, `X-Bapi-Limit-Reset-Timestamp` | n/a |

**OKX:** splitting order flow between REST and WebSocket buys no headroom. **Kraken runs two limiters, and
`AddOrder`/`CancelOrder` are deliberately excluded from the general one.** The general REST counter costs 1 per
call and **2 for ledger and trade-history calls**; the trading counter is per-pair and charges **by order
lifetime**, which inverts the usual intuition:

| Action | Cost |
|---|---|
| `AddOrder` | +1 |
| `CancelOrder` | **+8 down to +1, larger the *younger* the order** |
| `Amend` | +1, plus +3 down to +1 by age |
| `Edit` | +1, plus +6 down to +0 by age |
| Decay | −1 / −2.34 / −3.75 per second, by tier |
| Threshold | 60 / 125 / 180, by tier |
| Open orders per pair | 60 / 80 / 225, by tier (`EOrder:Orders limit exceeded`) |

The **exact age brackets mapping to each cost are not established by the research behind this file**; read
them from <https://docs.kraken.com/api/docs/guides/spot-ratelimits> before sizing a quoting loop. The shape is
enough for the decision: cancel-replacing every 200 ms burns roughly **9 units per cycle** against a threshold
of 60–180 and throttles within seconds. Prefer `Amend` over cancel-replace and fewer, longer-lived quotes; on
Kraken, quote churn is priced.

CCXT trap: **`rateLimit` for Kraken holds a delay in milliseconds between requests, not requests per second**
(Freqtrade exchange notes); reading it as a rate is an order-of-magnitude error.

## Minting one ID for all three

The charset intersection across these three plus Binance is **≤18 alphanumeric characters, no punctuation**:
OKX ≤32 case-sensitive alphanumerics with hyphens rejected in practice ∩ Kraken free-ASCII ≤18 (or a dashed
UUID / 32-hex, which OKX will not take) ∩ Bybit ≤36 alnum/`-`/`_` ∩ Binance futures
`^[\.A-Z\:/a-z0-9_-]{1,36}$`. Validate at **construction**, not at send; a 36-character dashed UUID passes
Binance and Bybit and fails OKX exactly when you are trying to place an order.

Bybit's documented limit is **36**; a field report (passivbot#436) shows `retCode 10001, "order link id is
longer than 45"`, implying a 45-character server check on at least one category. ≤36 is the safe Bybit rule.

```python
def test_id_charset_is_the_intersection():
    assert re.fullmatch(r"[A-Za-z0-9]{1,18}", mint_client_order_id("mm", next_counter()))

def test_id_counter_survives_restart():
    # a re-issued ID collides with your own open order on all three venues, and on OKX it poisons
    # GET /api/v5/trade/order for that ID permanently; restore the counter from durable storage
    a = mint_client_order_id("mm", next_counter()); restart_process()
    assert mint_client_order_id("mm", next_counter()) != a
```
