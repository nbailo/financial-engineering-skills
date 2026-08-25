# Binance: spot, USDⓈ-M futures, COIN-M

Everything Binance-specific that a correct client cannot infer: the filter set and its exact predicates, the
client-order-ID uniqueness scope and the query endpoints that recover an order whose response was lost, the
error codes that mean UNKNOWN versus the ones that mean rejected, the two different order-book join algorithms
(Spot and Futures are not the same), and the user-data stream lifecycle. Facts are dated to the docs revision
read (spot repo HEAD "Last Updated: 2026-07-27", derivatives 2026-08-24), so re-verify before keying
production behaviour on one.

## Contents

- [Two venues, not one](#two-venues-not-one): what changes between spot and USDⓈ-M
- [Instrument metadata](#instrument-metadata): `exchangeInfo`, `symbolStatus`, refresh, fixture capture
- [Filter reference](#filter-reference): every filter, its predicate, and which order type it validates
- [Futures-only metadata](#futures-only-metadata): `pricePrecision` is not `tickSize`; COIN-M multipliers
- [`newClientOrderId`](#newclientorderid): charset, uniqueness scope, `-2010 "Duplicate order sent."`
- [Query-before-retry: endpoints and their retention bounds](#query-before-retry-endpoints-and-their-retention-bounds)
- [Data sources and what `-2013` actually means](#data-sources-and-what--2013-actually-means)
- [Error codes, classified](#error-codes-classified): UNKNOWN vs rejected vs rate-limited, and the HTTP layer (403 WAF, 409, 429, 418, 5XX)
- [Timing: `recvWindow` and the two-phase check](#timing-recvwindow-and-the-two-phase-check)
- [Rate limits](#rate-limits): weight vs order count, headers, ban escalation
- [Order book: the Spot algorithm](#order-book-the-spot-algorithm)
- [Order book: the Futures algorithm](#order-book-the-futures-algorithm): the `pu` continuity check
- [User data stream](#user-data-stream): `listenKey`, `listenKeyExpired`, the Ed25519 migration
- [`executionReport`: cumulative vs last-fill](#executionreport-cumulative-vs-last-fill)
- [Commission](#commission): the side flip and the BNB discount's scope
- [STP and `EXPIRED_IN_MATCH`](#stp-and-expired_in_match)
- [Hedge mode, reduce-only, `closePosition`](#hedge-mode-reduce-only-closeposition)
- [Cancel-on-disconnect, and the FIX API's `ResendRequest`](#cancel-on-disconnect-and-the-fix-apis-disabled-resendrequest)

## Two venues, not one

| | Spot (`api.binance.com`, `/api/v3`) | USDⓈ-M Futures (`fapi.binance.com`, `/fapi/v1`) |
|---|---|---|
| Book join | `lastUpdateId` in `[U, u]`, then `U == prev_u + 1` | `U <= lastUpdateId AND u >= lastUpdateId`, then `pu == prev_u` |
| Post-only | order **type** `LIMIT_MAKER` → rejected on cross | `timeInForce=GTX` → rejected/expired on cross |
| Client-ID charset | not published as a regex on the spot New Order page | `^[\.A-Z\:/a-z0-9_-]{1,36}$` |
| User-data transport | `listenKey` deprecated 2025-04-07, docs removed 2025-10-24 → WebSocket API subscription, **Ed25519 key required** | `listenKey`, `PUT /fapi/v1/listenKey`, 60 min |
| Exec event | `executionReport` (flat) | `ORDER_TRADE_UPDATE` (order fields nested under `o`) |
| `-2021`/`-2022` | cancelReplace partial / total failure | different conditions: see the error table |
| Quantity unit | base asset | base asset (linear); **contracts** on COIN-M |

A shared `BinanceAdapter` branching on `futures: bool` and reusing one book-sync routine is the defect this
file exists to prevent.

## Instrument metadata

`GET /api/v3/exchangeInfo` / `GET /fapi/v1/exchangeInfo`. Fetch at startup, refresh on a schedule, and
**fail closed** (refuse to size) when a symbol's metadata is absent or older than a configured max age.
`symbolStatus` is a live field and gained a new value `CANCEL_ONLY` in 2026-07 (spot `CHANGELOG.md`): a symbol
can move to a state where `submit_order` is rejected while `cancel_all` still works, so branch on it. Tick,
step and notional minimums are revised and symbols are delisted, so anything cached at process start is a
stale snapshot, not a constant. Commit a **production-captured** fixture (`exchangeInfo.BTCUSDT.json`) for the
filter property test; hand-written fixtures agree with the hand-written rounder and prove nothing.

## Filter reference

From `filters.md` (spot docs repo). Filters live at two levels, `symbols[].filters[]` and top-level
`exchangeFilters[]`; any of `minPrice` / `maxPrice` / `tickSize` set to `0` disables **that clause only**.

| Filter | Fields | Predicate | Applies to |
|---|---|---|---|
| `PRICE_FILTER` | `minPrice`, `maxPrice`, `tickSize` | `price >= minPrice`, `price <= maxPrice`, `price % tickSize == 0` | any order carrying a price |
| `PERCENT_PRICE_BY_SIDE` | `bidMultiplierUp/Down`, `askMultiplierUp/Down`, `avgPriceMins` | BUY uses the **bid** multipliers, SELL the **ask** multipliers, against the `avgPriceMins` average | priced orders |
| `LOT_SIZE` | `minQty`, `maxQty`, `stepSize` | `minQty <= qty <= maxQty`, `qty % stepSize == 0` | **non-MARKET** orders |
| `MARKET_LOT_SIZE` | `minQty`, `maxQty`, `stepSize` (**its own values**) | same predicate, different numbers | **MARKET orders only** |
| `MIN_NOTIONAL` | `minNotional`, `applyToMarket`, `avgPriceMins` | `price * qty >= minNotional` | symbols exposing this variant |
| `NOTIONAL` | `minNotional`, `maxNotional`, `applyMinToMarket`, `applyMaxToMarket`, `avgPriceMins` | both bounds | symbols exposing this variant |
| `ICEBERG_PARTS` | `limit` | `ceil(qty / icebergQty) <= limit` | iceberg orders |
| `MAX_POSITION` | `maxPosition` | free base **+ locked base + qty of all open BUY orders** ≤ `maxPosition` | account-level, per symbol |
| `MAX_NUM_ORDERS` | `maxNumOrders` | counts **algo orders too** | per symbol |
| `MAX_NUM_ALGO_ORDERS` / `MAX_NUM_ICEBERG_ORDERS` / `MAX_NUM_ORDER_LISTS` / `MAX_NUM_ORDER_AMENDS` / `TRAILING_DELTA` | matching `maxNum*`; trailing min/max deltas | independent counters and bounds | per symbol / per order |
| `EXCHANGE_MAX_NUM_ORDERS` / `..._ALGO_ORDERS` / `..._ICEBERG_ORDERS` / `..._ORDER_LISTS` | n/a | account-wide across **all** symbols | `exchangeFilters[]` |

Four things this table exists to stop:

1. **`MARKET_LOT_SIZE` is a separate filter from `LOT_SIZE`, applying only to MARKET orders**, and its
   `maxQty` is frequently far below `LOT_SIZE.maxQty`. One shared `round_to_step(qty, market.lot_size.step)`
   helper produces `-1013 Filter failure: MARKET_LOT_SIZE` at the moment you are trying to exit fast.
2. **`MIN_NOTIONAL` and `NOTIONAL` are different filter types**; a symbol exposes one or the other, so code
   that only looks up `MIN_NOTIONAL` under-validates and never sees `maxNotional`.
3. **A MARKET order's notional is not computed from your price**: the engine substitutes the `avgPriceMins`
   VWAP, or last price when `avgPriceMins == 0`, so a client check against last price can pass while the
   engine rejects, and vice versa.
4. **`PERCENT_PRICE_BY_SIDE` is asymmetric.** The doc's own worked example is a bid band of 0.2–1.2 against
   an ask band of 0.8–5, so a symmetric `abs(price/ref - 1) < band` check is wrong on every symbol.

Do it all in `Decimal`: `0.29 % 0.01 == 0.009999999999999974` and `int(0.29/0.01) == 28` (CPython 3, verified);
float floor-division loses a whole step, and `str(1e-05) == '1e-05'` reaches the wire as a value the
decimal parser rejects (`-1100` or `-1111`; which fires was not established by live test).

## Futures-only metadata

**`pricePrecision` is not `tickSize`.** The USDⓈ-M `exchangeInfo` doc says verbatim of `pricePrecision`:
*"please do not use it as tickSize"*. Decimal count and tick size are independent constraints: a symbol can
carry `pricePrecision: 2` with `tickSize: 0.05`, and rounding to two decimals produces `100.03`, which is not
a multiple of the tick. Read `tickSize` from the symbol's `PRICE_FILTER` and `stepSize` from its `LOT_SIZE`,
on futures exactly as on spot. The notional floor is `-4164 "Order's notional must be no smaller than 5.0
(unless you choose reduce only)"`; the number is not universally 5, so read it from the symbol's filters, and
a residual close below it must set `reduceOnly` or refuse to send.

**COIN-M quantity is in contracts, not base asset.** BTCUSD contracts are 100 USD each; most alt COIN-M
contracts are 10 USD (Binance contract-specification support page). `size = usd_notional / price` on COIN-M is
off by 100×, while USDⓈ-M linear quantity is in base asset. Same exchange, two unit systems; carry the unit
with the number across every module boundary and convert only in the adapter.

## `newClientOrderId`

**Charset (USDⓈ-M New Order page):** *"Can only be string following the rule: `^[\.A-Z\:/a-z0-9_-]{1,36}$`"*:
alphanumerics plus `.` `:` `/` `_` `-`, max 36. A dashed UUID fits; base64 does not (`+` and `=` are excluded).
Validate at construction, not at send.

**Uniqueness scope: the load-bearing fact.** Spot `POST /api/v3/order` and futures `POST /fapi/v1/order`
document the field as:

> `newClientOrderId` | STRING | NO | "**A unique id among open orders.** Automatically generated if not sent.
> **Orders with the same `newClientOrderID` can be accepted only when the previous one is filled, otherwise the
> order will be rejected.**"

Binance's own two sentences disagree: the header implies the ID is freed on *any* exit from the open set
(filled, cancelled, expired); the next implies only a *fill* frees it. Both cannot be literally true. **Assume
the shorter window (the ID is free the moment the order leaves the open set) and never rely on either.**
This is a collision guard against currently-open orders, **not an idempotency key**: resending after
termination creates a *second order*, in exactly the marketable-limit/IOC case where your response was most
likely to be lost.

**Collision error** (`errors.md`, under `-2010 NEW_ORDER_REJECTED`): `"Duplicate order sent."` means *the
`clOrdId` is already in use*, while `"Unknown order sent."` means the order (by `orderId`, `clOrdId` or
`origClOrdId`) could not be found. So `-2010 "Duplicate order sent."` is *evidence*, not merely a rejection:
it proves the original order exists and is open. Resolve by querying it; do not mutate the ID and retry.
Separately, `-2039 CLIENT_ORDER_ID_INVALID` (*"Client order ID is not correct for this order ID"*) fires
when you send both `orderId` and `origClientOrderId` and they disagree; send both deliberately in
reconciliation code, since it turns a silent mapping bug into an error.

## Query-before-retry: endpoints and their retention bounds

Every endpoint below is bounded; reconciliation that does not encode the bound stops finding orders and then
concludes they were never created. **Spot**, in ladder order:

1. `GET /api/v3/order?symbol=&origClientOrderId=`, Data Source: **Memory ⇒ Database**.
2. `GET /api/v3/openOrders?symbol=`
3. `GET /api/v3/allOrders?symbol=&startTime=&endTime=`, Data Source: **Database**. *"The time between
   `startTime` and `endTime` can't be longer than 24 hours"*; `limit` 1000, and a response of exactly 1000
   rows is a hole, not an empty result; page until the count is below the limit.
4. `GET /api/v3/myTrades?symbol=&startTime=`, **fills: the only ground truth about economic effect**, and
   the user-data stream `executionReport` alongside it.

Hard bound: `-2026 ORDER_ARCHIVED`, *"Order was canceled or expired with no executed qty over 90 days ago and
has been archived."* Client-ID lookup is not a durable audit path; your own ledger is.
**USDⓈ-M futures**: `GET /fapi/v1/order?symbol=&origClientOrderId=`, with a published exclusion:

> "These orders will not be found: order status is `CANCELED` or `EXPIRED` **AND** order has NO filled trade
> **AND** created time + **3 days** < current time; [or] order create time + **90 days** < current time"

**A futures order cancelled with zero fills is unfindable by client ID after 3 days**; a weekly
reconciliation built on this endpoint has already lost that population.

## Data sources and what `-2013` actually means

> "**The API system is asynchronous, so some delay in the response is normal and expected.** … These are the
> three sources, ordered by least to most potential for delays in data updates: Matching Engine, Memory,
> Database. Some endpoints can have more than 1 data source. (e.g. Memory ⇒ Database) This means that the
> endpoint will check the first Data Source, and if it cannot find the value it's looking for it will check
> the next one." (`rest-api.md#data-sources`)

So `-2013 NO_SUCH_ORDER` immediately after placement is **not** proof of non-creation; it can mean "not yet
visible in the replica you queried". Re-query with backoff across the propagation window before concluding
anything (`-2026`, archived, is a *different* code, so `-2013` on an old order is not the archive path). The
same fact governs reconciliation cadence: the private stream is Matching-Engine-sourced and many REST reads
are not, so a reconciliation running faster than the replication lag oscillates, gets labelled flaky, and gets
muted.

## Error codes, classified

| Code | Name / message | Class | What to do |
|---|---|---|---|
| `-1006` | `UNEXPECTED_RESP`: "An unexpected response was received from the message bus. **Execution status unknown.**" | **UNKNOWN** | Never resubmit. Run the query ladder. |
| `-1007` | `TIMEOUT`: "Timeout waiting for response from backend server. **Send status unknown; execution status unknown.**" | **UNKNOWN** | Same. The API-layer timeout is 10 s. Docs add: *"This does not always mean that the request failed in the Matching Engine. If the status of the request has not appeared in User Data Stream, please perform an API query for its status."* |
| `-1021` | `INVALID_TIMESTAMP`: "Timestamp for this request is outside of the recvWindow" / "…was 1000ms ahead of the server's time" | Rejected (pre-ME) *or* latency signal | See the timing section. Do not "fix" it by raising `recvWindow`. |
| `-1100` | `ILLEGAL_CHARS`: "Illegal characters found in parameter '%s'" | Rejected | Serialization bug (scientific notation). Fix the formatter. |
| `-1111` | `BAD_PRECISION`: "Parameter '%s' has too much precision" | Rejected | Quantization bug. |
| `-1013` | Filter failure: `"Filter failure: <FILTER_NAME>"` | Rejected | Business rejection. The filter name tells you which predicate. Do **not** retry unchanged. |
| `-1015` | `TOO_MANY_ORDERS` | Rate-limited | Arrives with **HTTP 429**. Order-count limiter, per account. |
| `-2010` | `NEW_ORDER_REJECTED`: message table incl. "Duplicate order sent.", "Account has insufficient balance for requested action.", "Order would immediately match and take." | Rejected | Branch on the **message**, not the code. "Duplicate order sent." proves the first order is open. "Order would immediately match and take." is the `LIMIT_MAKER` would-cross rejection. |
| `-2011` | `CANCEL_REJECTED`: "Unknown order sent." | Expected in normal operation | The order filled between your decision and your cancel. Re-read its terminal state; do not retry as an error. |
| `-2013` | `NO_SUCH_ORDER` | **Not proof of non-creation** | Re-query with backoff. See data sources. |
| `-2021` (spot) | Order cancel-replace **partially** failed | **One leg succeeded** | Arrives with HTTP 409. Determine which leg before doing anything else. |
| `-2022` (spot) | Order cancel-replace failed | Both legs failed | Safe to re-decide. |
| `-2026` | `ORDER_ARCHIVED`: "canceled or expired with no executed qty over 90 days ago" | Terminal, unqueryable | Your ledger is the only record. |
| `-2039` | `CLIENT_ORDER_ID_INVALID` | Rejected | `orderId` and `origClientOrderId` disagree: a mapping bug. |
| `-4131` (futures) | Counterparty best price breaches the `PERCENT_PRICE` band | Rejected | A market order into a thin book. |
| `-4164` (futures) | "Order's notional must be no smaller than 5.0 (unless you choose reduce only)" | Rejected | Set `reduceOnly` on residual closes, or refuse to send. |
| `-2018` / `-2019` / `-2020` (futures) | insufficient balance / insufficient margin / unable to fill | Rejected | Business rejections. |
| `-2022` (futures) | `ReduceOnly Order is rejected` | Rejected | A conflicting open order, or hedge-mode misuse. |

**The futures error table reuses `-2021` and `-2022` for conditions that are not the spot cancelReplace codes.**
`-2022 ReduceOnly Order is rejected` is documented in the USDⓈ-M table; the futures meaning of `-2021` is *not*
established by the research behind this file; read it off the current derivatives error-code page before
keying a branch on it. Never carry a spot error map into a futures client.

**The HTTP layer** classifies independently of the body:

| Status | Meaning | Class |
|---|---|---|
| `403` | WAF limit violated | Rejected at the edge: the request may not have reached the ME, but this is not documented as a guarantee |
| `409` | `cancelReplace` **partially** succeeded (paired with `-2021`) | **One leg landed** |
| `429` | Rate limit breached | **UNKNOWN on an order endpoint**: no Binance doc states a 429 guarantees non-creation |
| `418` | IP auto-banned after repeatedly ignoring 429 | Stop sending entirely |
| `4XX` / `5XX` | Malformed request / internal error | Rejected / **UNKNOWN** |

Binance states the 5XX case itself: *"It is important to **NOT** treat this as a failure operation; the
execution status is **UNKNOWN** and could have been a success"*; the USDⓈ-M general-info page repeats it for
HTTP 503. Add the client-side case the venue cannot tell you about: a socket timeout that returned no code.

## Timing: `recvWindow` and the two-phase check

The documented pseudocode (`rest-api.md`, Timing security):

```
if (timestamp < (serverTime + 1 second) && (serverTime - timestamp) <= recvWindow) {
  // begin processing
  serverTime = getCurrentTime()
  if (serverTime - timestamp) <= recvWindow { forward to Matching Engine } else { reject }
} else { reject }
```

A clock more than ~1 second **fast** is rejected regardless of `recvWindow`; that is the distinct
"was 1000ms ahead of the server's time" message. And a request can pass the entry check and be rejected at the
ME boundary *after queueing*, so repeated `-1021` under a synchronised clock is a **latency** alarm, not a
clock alarm (this reading follows from the pseudocode; Binance does not state it in prose). `recvWindow`
defaults to 5000 ms, max 60000: *"It is recommended to use a small recvWindow of 5000 or less!"* Raising it to
60000 does not fix a clock problem; it widens the window in which an order delayed a full minute by a network
stall still reaches the book.

## Rate limits

Two independent limiters with **different keys**:

| Limiter | Keyed on | Header | Breach |
|---|---|---|---|
| Request weight | **IP**: "The limits on the API are based on the IPs, not the API keys" | `X-MBX-USED-WEIGHT-(intervalNum)(intervalLetter)` | HTTP 429 |
| Unfilled order count | **account** | `X-MBX-ORDER-COUNT-(intervalNum)(intervalLetter)` | HTTP 429 + `-1015 TOO_MANY_ORDERS` |

- The order limiter counts only **unfilled** orders: *"orders which are partially or fully filled do not count
  against the rate limit"*, a quote-churn limiter, so a maker getting no fills is throttled harder than one
  that is trading.
- **Rejected orders are not guaranteed to carry `X-MBX-ORDER-COUNT-*`**, so a limiter that learns only from
  response headers under-counts precisely during a rejection burst. Reserve locally *before* sending.
- `Retry-After` (seconds) accompanies both 429 and 418: on 429 it is how long to wait to *avoid* a ban, on 418
  it is when the ban ends. Escalation is 429 → 418 → an IP ban scaling **from 2 minutes to 3 days**, so a retry
  loop makes it worse. Sharing one IP across accounts shares the weight limiter and not the order-count
  limiter; sharing one API key across processes defeats any local limiter you built.
- Spot market-data websockets add their own: connections die on a **24-hour** timer, the server pings every
  20 s and expects a pong within a minute, inbound is capped at **5 messages/second** per connection (PING,
  PONG and JSON control messages all count), 1024 streams per connection, and **300 connections per 5 minutes
  per IP**, which is what a naive per-symbol reconnect storm trips first.

## Order book: the Spot algorithm

`web-socket-streams.md`, "How to manage a local order book correctly". Do not paraphrase; these are the steps.

1. Open a stream to `wss://stream.binance.com:9443/ws/<symbol>@depth`.
2. Buffer the events you receive. Note the `U` of the **first** event received.
3. `GET /api/v3/depth?symbol=<SYMBOL>&limit=5000`.
4. If the snapshot's `lastUpdateId` is **strictly less than** the `U` from step 2, the snapshot is too old:
   **go back to step 3**.
5. Discard every buffered event with `u <= lastUpdateId`. The first remaining event must satisfy
   `U <= lastUpdateId+1` and `u >= lastUpdateId+1`, i.e. `lastUpdateId` falls inside `[U-1, u]`.
6. Set your local book to the snapshot. Set `localId = lastUpdateId`.
7. Apply the update procedure to the buffered events, then to live events:
   - if `u < localId` → **ignore** the event (it predates the snapshot);
   - if `U > localId + 1` → **you missed events. Discard the book and restart from step 1.**
   - otherwise `U` of each event equals `u + 1` of the previous;
   - per level: absent locally ⇒ insert; `qty == 0` ⇒ delete; else **set** (the payload is the absolute
     quantity at that price, never a delta);
   - `localId = u`.

Spot depth events carry **no `pu` field**. A snapshot is depth-limited (5000 per side), so levels outside it
are *unknown*, not empty: *"you won't learn the quantities for the levels outside of the initial snapshot
unless they change."* Sizing a large order against `sum(local_book)` over-states available liquidity.

## Order book: the Futures algorithm

USDⓈ-M "How to manage a local order book correctly". **The acceptance rule and the continuity rule are both
different from spot.** Using the spot procedure here is the single most-copied incorrect snippet in this
ecosystem.

1. Open a stream to `wss://fstream.binance.com/stream?streams=<symbol>@depth`.
2. Buffer the events you receive.
3. `GET /fapi/v1/depth?symbol=<SYMBOL>&limit=<venue max>`.
4. Drop any buffered event with `u < lastUpdateId`.
5. The **first event you process** must satisfy `U <= lastUpdateId` **AND** `u >= lastUpdateId`. (Spot's rule
   is `lastUpdateId` inside `[U-1, u]` after discarding `u <= lastUpdateId`, not the same predicate.) If no
   buffered event satisfies it, go back to step 3.
6. Set the book to the snapshot.
7. For **every subsequent event**: `pu` must equal the **previous event's `u`**. On mismatch, the stream has a
   hole: **discard the book and re-initialise from step 3.**
8. Level semantics are identical to spot: `qty == 0` ⇒ delete, otherwise set the absolute quantity. *"Receiving
   an event that removes a price level that is not in your local order book can happen and is normal"*; do not
   warn-spam on it.

Why `pu` is not optional: server-side coalescing can emit a frame whose `[U, u]` range still looks adjacent to
your `localId` while an intermediate frame was dropped, and `pu` is the only field that catches it. The book
then carries a phantom level forever (the delete was in the lost frame) and the strategy quotes into a
spread that does not exist, posting inside the real book and adversely selected on every print. On any gap on
either venue: **discard and re-snapshot. Never patch, never interpolate, never "fill in the missing levels."**
Suppress quoting on that instrument between the gap and the completed resync.

## User data stream

**USDⓈ-M futures.** `POST /fapi/v1/listenKey` starts a stream; the key *"will close after 60 minutes unless a
keepalive is sent"*; `PUT /fapi/v1/listenKey` extends it by 60 minutes. Critically: *"if the account has an
active `listenKey`, that `listenKey` will be returned and its validity will be extended for 60 minutes"*, so
`POST`ing again does **not** rotate the key, and code that "gets a fresh key" on reconnect is a keepalive
under a different name.

**Spot.** Receiving user data on `wss://stream.binance.com:9443` via `listenKey` was **deprecated 2025-04-07**
and all `listenKey` documentation for that endpoint was **removed 2025-10-24** (`CHANGELOG.md`). The
replacement is subscribing to the User Data Stream on the **WebSocket API**, which **requires an Ed25519 API
key**: an HMAC key cannot subscribe, so a spot bot authenticating with an HMAC secret and calling
`POST /api/v3/userDataStream` is on a path Binance has said will be removed.

Both venues: run the keepalive on a scheduler strategy work **cannot starve**, renewing at ≤30 min against the
60-min TTL: a keepalive `await`ed in the same event loop as a blocking indicator misses one tick, the stream
closes, and the bot keeps trading against its last known position. Treat `listenKeyExpired` as **"you are now
blind"** (halt new orders, reconcile, resync), not an informational log line. And **subscribe and confirm the
private stream before sending the first order**: placing first loses the initial `NEW`/`TRADE` events and the
order starts life untracked.

## `executionReport`: cumulative vs last-fill

Spot `executionReport` carries both, for different jobs.

| Field | Meaning | Use it for |
|---|---|---|
| `z` | **cumulative** filled quantity | position: `filled = z` (assignment, not `+=`) |
| `Z` | **cumulative** quote asset transacted | notional |
| `l` / `L` / `Y` | **last** executed quantity / price / quote quantity (`L * l`) | the fill record |
| `n` / `N` | commission amount / **commission asset** | fee booking; `N` can be **null** on non-trade events |
| `t` | trade id | the fill dedupe key |
| `i` / `c` / `C` | orderId / clientOrderId / original clientOrderId | correlation |
| `X` / `x` / `T` | order status / execution type / transaction time | state machine, ordering, staleness |

The doc states the derivation outright: **"Average price can be found by doing `Z` divided by `z`."**
`position += l` is **not** idempotent under reconnection, replay, or dual stream+poll ingestion; `filled = z`
is. Build the fill record from `l`/`L`/`t` and the order status from `z`/`Z`, exactly the split
NautilusTrader's Binance futures adapter makes
(`crates/adapters/binance/src/futures/websocket/streams/parse_exec.rs`), and dedupe fills on `t` before the
position transition, persisting the dedupe set in the same transaction as the position row. USDⓈ-M emits
`ORDER_TRADE_UPDATE` with the order fields **nested under `o`** (`o.z`, `o.l`, `o.L`, `o.t`, …); verify the
full futures field map against the current derivatives doc before keying on a field this table does not name,
because several widely-circulated futures field names are not in any primary source.

## Commission

**The fee side flips with the order side** (`faqs/commission_faq.md`): on a **SELL** the commission is charged
on the notional, i.e. in the **quote** asset; on a **BUY**, *"the received amount would be `quantity`"*; the
commission comes out of the **base asset you just bought**. So buying 36.38 GTC with a 0.1% GTC-denominated
fee leaves you holding **36.34** GTC. Selling `trade.amount`
returns `-2010 "Account has insufficient balance for requested action."` (freqtrade#1371); selling the raw
free balance returns `-1013 Filter failure: LOT_SIZE` because 36.34 is not a multiple of `stepSize`
(freqtrade#5481). The correct model, in `Decimal`: `credited = filled_qty − (fee if commissionAsset == base)`,
then re-snap **down** to `stepSize`.

**The BNB discount's scope is narrower than a single `fee_rate` scalar can express.** When
`discount.enabledForAccount && discount.enabledForSymbol`, the **standard** commission is converted to BNB and
multiplied by `discount`, and the doc states the discount *"does not apply to tax commissions or special
commissions"*, so one scalar rate is wrong three ways at once: wrong currency, wrong rate, wrong composition.
Book the fee in the asset the venue reports (`commissionAsset` / `N`), handle null, and where it is neither
the quote nor the settlement asset, convert at a **recorded** rate or surface it as unconverted.
Do not treat an adapter's fee as truth: NautilusTrader's Binance adapter *estimates*
`default_taker_fee × qty × price` for USD-M linear when Binance omits the commission and defaults COIN-M
inverse commission to zero; ccxt's `calculateFee` is *"experimental, unstable and may produce incorrect
results"*.

## STP and `EXPIRED_IN_MATCH`

Modes (`enums.md`): `NONE`, `EXPIRE_MAKER`, `EXPIRE_TAKER`, `EXPIRE_BOTH`, `DECREMENT`, `TRANSFER`.

- **The taker's mode governs**, with one exception: `TRANSFER` requires **both** sides to specify `TRANSFER`,
  otherwise the pair degrades to `DECREMENT` (`faqs/stp_faq.md`). `DECREMENT` expires the smaller order (or
  both if equal) and increments **both** orders' prevented quantity. `tradeGroupId == -1` means the account is
  in no trade group, so STP can only fire against itself.
- **A prevented match is not a trade**; the FAQ says so: no orders match. The order emits execution type
  `TRADE_PREVENTION` and reaches status `EXPIRED_IN_MATCH`. A fill parser keyed on "the order left the book
  with quantity consumed" books a phantom trade and diverges from the venue by the prevented quantity.
  Prevented matches are queried from a **separate endpoint**, `GET /api/v3/preventedMatches`.
- STP changes your remaining-quantity arithmetic: `origQty − executedQty − preventedQty = quantity available
  for further execution`, and `preventedQuantity` is **cumulative over the order's lifetime**, so
  `leaves = qty − cum` is wrong whenever STP has fired. **Futures STP is only effective with `IOC`, `GTC` or
  `GTD`**; setting it on a `FOK` or `GTX` order is a silent no-op (USDⓈ-M New Order page).

## Hedge mode, reduce-only, `closePosition`

The exact wording, so you can check the precondition rather than discover it at flatten time: `reduceOnly`
**"Cannot be used in Hedge Mode"** and `closePosition` is **incompatible with `quantity`** (USDⓈ-M New Order
page); `-2022 ReduceOnly Order is rejected` fires when an existing open order conflicts. `GTC` on USDⓈ-M is
**not forever** (*"validity is 1 year from placement"*) and `goodTillDate` must be `> now + 600 s` and
`< 253402300799000` ms, with NautilusTrader's adapter rejecting an expiry off a whole-second boundary rather
than silently rounding it. Futures `PERCENT_PRICE` is evaluated against **mark price** and `workingType`
selects `MARK_PRICE` vs `CONTRACT_PRICE` for stop triggers: a stop distance computed from last price is not
the distance the engine uses, and liquidation is always a mark-price event.

## Cancel-on-disconnect, and the FIX API's disabled `ResendRequest`

`POST /fapi/v1/countdownCancelAll` (USDⓈ-M) is the dead-man's switch. `countdownTime` is in
**milliseconds**; Kraken's and OKX's equivalents are in seconds, so copying an integration across venues
silently disarms it by a factor of 1000. Per-`symbol`; `0` disables; weight 10; countdowns are checked roughly
every 10 ms, and the doc warns against setting the countdown "too precise or too small". Re-arm on every
reconnect, and drive the heartbeat from the liveness of the trading loop rather than an independent timer that
keeps pinging while the strategy is wedged. **A spot equivalent was not found** by the research behind this
file (futures and Options market-maker endpoints were verified; a spot endpoint was not); absence is not
established, so check before asserting either way. Binance SPOT FIX, meanwhile, has **neither** of FIX's two
replay mechanisms in its usual form:
*"### Resend Request `<2>`: Resend requests are currently not supported"* (`fix-api.md`), and it requires
strict monotonic sequencing (*"the client's `MsgSeqNum (34)` must increase monotonically, with each subsequent
message having a sequence number that is exactly 1 greater than the previous message"*) with
`MessageHandling(25035)` selectable as `UNORDERED(1)` or `SEQUENTIAL(2)`. The standard institutional retry
("same `ClOrdID`, new `MsgSeqNum`, `PossResend=Y`") therefore has no session-layer support here. Fall back to
query-first recovery by `origClientOrderId`, within the retention bounds above.
