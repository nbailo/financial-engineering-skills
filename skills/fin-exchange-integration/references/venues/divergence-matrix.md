# Venue divergence matrix

One matrix, in eight panels, because thirteen behaviours × nine venues does not render as a single markdown
table. Rows are behaviours that differ across venues; columns are the venues. It exists for the moment you
write a venue-agnostic abstraction — an `Order`, an `OrderBook`, a `RateLimiter`, a client-order-ID minter, a
"size" field — because that is the moment one venue's behaviour becomes a silent default applied to venues
that do not share it. Read the row before you decide the abstraction has one answer.

## Contents

- How to read it: column keys, the as-of date, and what a blank cell means
- Panel A — Client order ID: field name, collision behaviour, window after terminal state, charset
- Panel B — Recovery: which endpoint accepts the client ID, and the retention bound that ends it
- Panel C — Book synchronisation: snapshot source, gap-detection field, checksum availability
- Panel D — Ack semantics and the UNKNOWN set: what a success response proves, what means "unresolved"
- Panel E — Rate limits: limiter shape, key, headers, and what a breach costs
- Panel F — Cancel-on-disconnect: exact API name, unit, range, and the arming precondition
- Panel G — Self-trade prevention: mode names, which side governs, and the non-fill it produces
- Panel H — Size units and fee asset: base vs contracts vs USD, and the currency the fee arrives in
- What breaks when you write a venue-agnostic abstraction: `Order`, the `clOrdID` helper, `RateLimiter`,
  `OrderBook` — the specific wrong behaviour each produces
- The eight fields that must not be flattened

## How to read it

Column keys: **BinS** Binance Spot · **BinF** Binance USDⓈ-M Futures · **OKX** OKX v5 · **Byb** Bybit v5 ·
**Krk** Kraken Spot REST/WS v2 · **CB-AT** Coinbase Advanced Trade · **Drbt** Deribit v2 · **HL** Hyperliquid ·
**FIX** a generic FIX 4.4/Latest venue.

Every cell is as of **2026-08-24** and carries the venue's own wording where one exists. **"undocumented"
means the venue publishes nothing** — it is not a synonym for "same as the others", and you may not code
against it. **"not established here"** means this corpus did not verify it; go read the venue doc before you
key behaviour on that cell. Venue docs move; re-verify anything load-bearing.

## Panel A — Client order ID

| Behaviour | BinS | BinF | OKX | Byb | Krk | CB-AT | Drbt | HL | FIX |
|---|---|---|---|---|---|---|---|---|---|
| Field | `newClientOrderId` | `newClientOrderId` | `clOrdId` | `orderLinkId` | `cl_ord_id` | `client_order_id` | `label` — a tag, not an ID | `cloid` | `ClOrdID(11)` |
| On collision | reject `-2010` "Duplicate order sent." | reject | reject | reject `110072` (derivs) / `170141` (spot) | reject | **returns the original order** | **no check — second order** | undocumented — assume second order | `OrdRejReason(103)=6` *if the venue implements it* |
| Class | B reject | B reject | B reject | B reject | B reject | **A idempotent replay** | C none | C none | venue-dependent |
| Window after terminal state | **zero** (see note) | **zero** | **zero** — "the same clOrdId may be reused for a new order" | **undocumented** | **zero** (REST/WS); Kraken FIX extends it to the FIX session | not stated; behaves unbounded | zero | zero — the nonce is the guard | unstated by the spec |
| Charset / length | not published in this corpus | `^[\.A-Z\:/a-z0-9_-]{1,36}$` | ≤32 case-sensitive alnum; hyphens rejected in practice | ≤36 alnum + `-` `_` | dashed UUID, 32-hex, or free ASCII ≤18; stored as a 128-bit int; mutually exclusive with `userref` | not established here | ≤64 chars, non-unique | 128-bit hex, `0x` + 32 hex | sender must guarantee uniqueness **within a trading day** |

Verbatim, and it is the sentence the whole file turns on — OKX, Place order: *"clOrdId must be unique among
all currently pending (live or partially_filled) orders in the account. Once an order reaches a terminal state
(filled, canceled, mmp_canceled), the same clOrdId may be reused for a new order. Uniqueness is not enforced
historically — GET /api/v5/trade/order returns only the latest match when multiple orders share a clOrdId."*

**Binance's own two sentences disagree.** The header says "A unique id among open orders"; the next sentence
says orders with the same ID "can be accepted only when the previous one is **filled**, otherwise the order
will be rejected". Under the first reading a *cancel* frees the ID; under the second it does not. Take the
shorter window and do not rely on either.

**FIX's "uniqueness must be guaranteed within a single trading day" is an obligation on the sender, not a
promise from the venue.** It says nothing about retention and nothing about collision behaviour. The
protocol's replay machinery is elsewhere and it is two orthogonal flags: `PossDupFlag(43)` is a *session*
assertion at the **same** `MsgSeqNum` ("if a message with this sequence number has been previously received,
ignore message"); `PossResend(97)` is an *application* assertion under a **different** `MsgSeqNum`, and the
spec pushes the check to you — "forward message to application and determine if previously received (i.e.
**verify order id and parameters**)". Nothing compels a venue to implement it, and Binance's FIX API states
"**Resend requests are currently not supported**", which removes the other one.

## Panel B — Recovery by client ID, and the bound that ends it

| Behaviour | BinS | BinF | OKX | Byb | Krk | CB-AT | Drbt | HL | FIX |
|---|---|---|---|---|---|---|---|---|---|
| Query by client ID | `GET /api/v3/order?origClientOrderId=` | `GET /fapi/v1/order?origClientOrderId=` | `GET /api/v5/trade/order?instId=&clOrdId=` | `GET /v5/order/realtime?orderLinkId=` | `POST /0/private/OpenOrders` then `ClosedOrders`, both accept `cl_ord_id` | **none supported** | by label only: `private/get_open_orders_by_label` | `info` → `orderStatus` accepts `oid` or `cloid` | none in the spec — the ExecutionReport chain |
| Retention bound | `-2026 ORDER_ARCHIVED` at **90 days** for cancelled/expired with no executed qty | unfindable if `CANCELED`/`EXPIRED` **and** no fills **and** created + **3 days** < now; everything at 90 days | `orders-history` 7 d, but **cancelled-incomplete kept 2 hours**; archive 3 months | `realtime` covers the **recent 500** closed; `history` 7 d, but Cancelled/Rejected/Deactivated only **24 h** | not established here | n/a — see below | label collisions; `edit_by_label` "works only when there is exactly one open order with the specified label" | nonce set: valid until **100 further nonces** by that signer, and within T−2d…T+1d | venue-defined |
| Fills endpoint | `GET /api/v3/myTrades?startTime=` | trade history | `fills` 3 d / `fills-history` 3 months | execution list | trade history | `GET /api/v3/brokerage/orders/historical/fills` | trade history by instrument + time | user fills | ExecutionReports |
| Multi-order collision on the query | — | — | **returns only the latest match** | — | `QueryOrders` requires `txid` and does **not** accept `cl_ord_id` | — | `edit_by_label` refuses | — | `OrigClOrdID(41)` chains across replaces |

**Coinbase Advanced Trade inverts the usual advice.** `GET /orders/historical/{order_id}` takes `order_id` as
a required path parameter and marks the `client_order_id` query parameter **Deprecated**; List Orders has no
client-order-id filter at all. There is no supported lookup by `client_order_id`, so the *supported* recovery
from an ambiguous submit is to **re-POST the identical create-order** — which is safe precisely because
Coinbase documents replay-returns-original. On this one venue the retry *is* the query. Note the unresolved
edge: `NewOrderFailureReason` separately enumerates `DUPLICATE_CLIENT_ORDER_ID`, which cannot be reconciled
with replay-returns-original in every case. Integration-test it before you depend on either.

**Two bounds are shorter than a typical reconciliation schedule.** OKX keeps cancelled-incomplete orders for
**2 hours**; Bybit keeps fully-cancelled orders for **24 hours**. A nightly reconciliation job cannot see
either population, and the code that concludes "no such order therefore it was never created" will be wrong
for every order in it. Encode the bound in the query, and return "outside retention" as a distinct answer
from "not found".

## Panel C — Book synchronisation

| Behaviour | BinS | BinF | OKX | Byb | Krk | CB-AT | Drbt | HL | FIX |
|---|---|---|---|---|---|---|---|---|---|
| Snapshot source | **REST** `/api/v3/depth?limit=5000` — no in-band snapshot | **REST** — no in-band snapshot | in-band on subscribe | in-band `snapshot` frame | in-band snapshot + updates | not established here | in-band `snapshot` | not established here | venue-defined (CME: separate Market Recovery snapshot feed) |
| Gap-detection field | `U` / `u` with the "restart if `U > localId + 1`" branch | **`pu` must equal the previous event's `u`** | `seqId` / `prevSeqId` | `u`, plus `seq` as a cross-depth comparator | sequence + checksum; "Updates should always be processed in sequence" | `sequence_num`, per product, **increments by exactly 1** | `change_id` / `prev_change_id`; the first snapshot omits `prev_change_id` | not established here | `MsgSeqNum`; CME joins on `369-LastMsgSeqNumProcessed` |
| First-event acceptance rule | snapshot `lastUpdateId` ∈ `[U, u]` | `U <= lastUpdateId AND u >= lastUpdateId` | `prevSeqId: -1` on a fresh snapshot | `u == 1` means **service restart**, overwrite the book | — | — | snapshot has no `prev_change_id` | — | drop cached incrementals with packet seq < 369 |
| Checksum | none | none | field present but **always 0 from 2026-06-23 (prod)** — "must no longer be used" | none documented | **CRC32 over exactly the top 10 levels** regardless of subscribed depth, on the string forms | none documented | none documented | none documented | n/a |
| Quantities | absolute, `qty 0` = delete | absolute, `qty 0` = delete | absolute | absolute | absolute | not established here | absolute | not established here | venue-defined (ITCH Modify is a *decrement*) |

Binance Spot and Binance USDⓈ-M Futures are the same company, the same field prefix, and **two different
algorithms**. Futures has `pu`; spot does not. Server-side coalescing can produce a futures frame whose
`[U, u]` looks adjacent while an intermediate frame was dropped, and `pu` is the only field that catches it.
The book then carries a phantom level forever — the delete for it was in the lost frame — and the strategy
quotes into a spread that does not exist.

Bybit's `u` is not a monotonic counter: `u == 1` is a restart, and level-1 re-emits a snapshot with the
**same `u`** after 3 seconds of no change. Gap detection written as "u must strictly increase" produces a
false resync every three seconds on a quiet instrument and misses the one event that actually invalidates the
book.

## Panel D — Ack semantics and the UNKNOWN set

| Behaviour | BinS | BinF | OKX | Byb | Krk | CB-AT | Drbt | HL | FIX |
|---|---|---|---|---|---|---|---|---|---|
| What a success response proves | accepted, not executed | accepted, not executed | *"Successful response only means the request has been accepted by the exchange"* | *"The ack of create/amend/cancel order request indicates that the request is successfully accepted"*; `retMsg: "OK"` ≠ filled | accepted | order object returned | accepted | `resting` (carries `oid`) / `filled` (`totalSz`, `avgPx`) / `error` | `35=8` `ExecType=New` is the ack |
| UNKNOWN set | `-1006` "Execution status unknown", `-1007` "Send status unknown; execution status unknown", **any 5XX**, socket timeout, 429/418 | same, plus HTTP 503 "Unknown error…" | any transport failure; no unknown-status code documented | any transport failure; no unknown-status code documented | any transport failure; no unknown-status code documented | any transport failure | `10028 too_many_requests` **terminates the session** | any transport failure; a used-nonce rejection proves the action landed | no report received; a session drop mid-order |
| Not-found ≠ not-created | `-2013 NO_SUCH_ORDER`, sources Matching Engine ⇒ Memory ⇒ Database, *"some delay in the response is normal and expected"* | same | `orders-pending` returns only `live`/`partially_filled` | after a release/restart, closed orders of a Unified account are only in order history | — | legacy Exchange: *"If the order is canceled, and if the order had no matches, the response might return the status code 404"* | label lookup returns nothing for a filled order | — | `CxlRejReason=Unknown Order` sets `OrigClOrdID` to `"NONE"` |
| Cancel-path reject | `-2011 CANCEL_REJECTED` — expected in normal operation | `-2011` | — | — | — | — | — | `iocCancelRejected` on an IOC that did not match | `35=9 OrderCancelReject`; "Filled orders cannot be changed" |

Binance states it in words no abstraction should soften: *"It is important to **NOT** treat this as a failure
operation; the execution status is **UNKNOWN** and could have been a success."* And nowhere in this corpus
does any venue document that a **429 on an order endpoint guarantees non-creation**. The common wisdom
("429 means it didn't happen, just retry") is unsupported by every doc read here.

Binance's cancel/replace is not atomic: HTTP 409 + `-2021` means **one leg succeeded** and you must determine
which before doing anything else; `-2022` means both failed.

## Panel E — Rate limits

| Behaviour | BinS / BinF | OKX | Byb | Krk | CB-AT | Drbt | HL | FIX |
|---|---|---|---|---|---|---|---|---|
| Shape | **two independent limiters**: request weight per **IP**, unfilled-order count per **account** | per-endpoint buckets, **shared across REST and WebSocket** | per-UID per-endpoint rolling 1 s, **plus** per-IP 600 req / 5 s | decaying counters; trading limiter is **separate** from the REST limiter | not established here | leaky-bucket **credits** | not established here | per-counterparty session throttle; no spec-level shape |
| The trap | *filled* orders do not count against the order limiter — a market maker getting no fills is throttled harder than one that does | sub-account cap 1000 order requests / 2 s → `50061`; splitting flow between REST and WS buys no headroom | — | **cancel cost rises as the order gets younger: +8 down to +1.** A quote flipper burns ~9 units per cycle | — | `public/get_instruments` alone costs **10 000 credits** against a 50 000 cap | — | — |
| Headers | `X-MBX-USED-WEIGHT-*`, `X-MBX-ORDER-COUNT-*`, `Retry-After` | — | `X-Bapi-Limit`, `X-Bapi-Limit-Status`, `X-Bapi-Limit-Reset-Timestamp` | — | — | — | — | — |
| Breach | 429 → 418 → IP ban escalating **2 minutes to 3 days**; `-1015 TOO_MANY_ORDERS` arrives with HTTP 429 | `50011`, `50061` | `retCode 10006`; HTTP **403 ⇒ terminate all sessions and wait ≥10 min** — retrying extends the ban | `EOrder:Rate limit exceeded`, `EOrder:Orders limit exceeded`; max open orders per pair 60/80/225 | not established here | `10028` **drops the WebSocket session** — and with cancel-on-disconnect armed, that cancels every open order | not established here | — |

Rejected Binance orders are **not guaranteed to carry `X-MBX-ORDER-COUNT-*`**, so a limiter that learns only
from response headers under-counts precisely during a rejection burst — the moment it most needs to be right.
Reserve locally before sending, then reconcile against the header.

## Panel F — Cancel-on-disconnect

| Venue | API | Unit | Range / default | Arming precondition and gotcha |
|---|---|---|---|---|
| Binance USDⓈ-M | `POST /fapi/v1/countdownCancelAll` | **milliseconds** | per `symbol`; `0` disables | weight 10; countdowns checked ~every 10 ms; the doc warns against setting it "too precise or too small" |
| Bybit | `POST /v5/order/disconnected-cancel-all` | **seconds** | `timeWindow` 3–300; `product` defaults to **`OPTIONS`** | **the private WS must subscribe the `dcp` topic or DCP never triggers**; institutional accounts; ~10 s for a config change to take effect |
| OKX | `POST /api/v5/trade/cancel-all-after` | **seconds** | `0` or `[10, 120]` | heartbeat ~1 s; optional per-`tag` scope (max 20); cancellation is sequential and "may take up to a few seconds" |
| Kraken Spot | `POST /private/CancelAllOrdersAfter` (WS v2: `cancel_after`) | **seconds** | `< 86400`; `0` disables | recommended cadence: call every 15–30 s with `timeout=60`; **disable it before scheduled venue maintenance** |
| Deribit | `private/enable_cancel_on_disconnect` | n/a — connection-based | `scope` = `connection` (default) or `account`; `connection` is WS-only | fires on TCP close, 10-minute inactivity, or heartbeat failure — **not** on `private/logout`. With `set_heartbeat` (interval ≥10 s) armed, failing to answer `test_request` with `public/test` closes the connection *and cancels every order*: a blocked event loop is an order-cancellation mechanism |
| Binance Spot, CB-AT, Hyperliquid | not established in this corpus | — | — | verify against current docs before assuming one exists |
| FIX | venue-configured session option, **not a spec tag** | venue-defined | venue-defined | confirm per counterparty in writing |

The unit differs — Binance milliseconds, everyone else seconds — so copying an integration between venues
sets a 60-second guard to 60 milliseconds or a 60-millisecond guard to 60 seconds. And OKX says the quiet
part out loud: orders cancel "one by one and this operation may take up to a few seconds… **clients should not
use this feature as part of their trading strategies**". A dead-man switch bounds worst-case exposure; it
does not define the moment you are flat.

## Panel G — Self-trade prevention

| Venue | Modes | Which side governs | The non-fill it produces |
|---|---|---|---|
| Binance Spot | `NONE`, `EXPIRE_MAKER`, `EXPIRE_TAKER`, `EXPIRE_BOTH`, `DECREMENT`, `TRANSFER` | **taker governs**, with one exception: `TRANSFER` requires *both* sides to specify it, else it degrades to `DECREMENT` | execution type `TRADE_PREVENTION`, order status `EXPIRED_IN_MATCH`; a prevented match *"is not to be confused with a trade, as no orders will match"*; queried from a separate `GET /api/v3/preventedMatches`. Identity: `origQty − executedQty − preventedQty` = quantity still available |
| Binance Futures | same mode names | taker | **effective only with `IOC`, `GTC` or `GTD`** — setting STP on a `FOK` or `GTX` order is a silent no-op |
| OKX | `cancel_maker` (**default**), `cancel_taker`, `cancel_both` | set at the **master-account level and applies across sub-accounts** | terminal state `mmp_canceled` |
| Coinbase Exchange (legacy) | `dc` decrement-and-cancel (**default**; equal sizes ⇒ **both** cancelled), `co` cancel-oldest, `cn` cancel-newest, `cb` cancel-both | **taker's instruction wins** | full or partial cancellation, not a trade |
| Coinbase AT, Bybit, Kraken, Deribit, Hyperliquid | not established in this corpus | — | read the venue doc before relying on a default |
| FIX / Nasdaq | no standard; Nasdaq AIQ reports the counterfactual: `Decrement Shares` ("incremental, not cumulative"), quantity prevented from trading, the price it would have traded at, the liquidity flag it would have earned | venue-defined | the two counterfactual fields diverge exactly in the asymmetric modes |

The default is not neutral and it is not portable. Under decrement your resting liquidity shrinks silently;
under cancel-oldest your resting order dies entirely; under cancel-newest your aggressor dies. Two
sub-accounts market-making the same OKX instrument have their maker orders cancelled by their own taker flow
unless someone configured otherwise. And a state machine that reads "the order left the book with quantity
consumed" as a fill books a trade that never happened.

## Panel H — Size units and fee asset

| Behaviour | BinS | BinF | Binance COIN-M | OKX | Byb | Krk | CB-AT | Drbt | HL | FIX |
|---|---|---|---|---|---|---|---|---|---|---|
| Size unit | base asset; market BUY may use `quoteOrderQty` | **base asset** | **contracts** — BTCUSD multiplier 100 USD, most alts 10 USD | `sz` = **contracts** for derivatives, base for spot/margin; effective multiplier `ctMult × ctVal`; `tgtCcy` picks base/quote for spot market orders | not established here | not established here | not established here | `amount` is **USD** for perpetual/inverse, base for options/linear; `contracts` is the alternative field and the two must agree | base units, rounded to `szDecimals` | `OrderQty(38)` in the venue's own unit; `OrderQty = CumQty + LeavesQty`, **chain-cumulative across replaces** |
| Price unit rule | `tickSize`; `price % tick == 0` | `tickSize` — **`pricePrecision` is not `tickSize`**, the doc says "please do not use it as tickSize" | `tickSize` | `tickSize` | not established here | decimal/string parsing required for the checksum | not established here | `tickSize` | **≤5 significant figures**, ≤(`MAX_DECIMALS − szDecimals`) decimals (6 perps / 8 spot), integer prices exempt | venue-defined |
| Fee asset | `commissionAsset` — **BUY charges the fee in the base asset you just bought**, SELL charges it on the quote notional; a BNB discount changes the *currency* and does not apply to tax or special commissions | reported on the event; may be **absent**, and adapters then *estimate* it | adapters may default inverse commission to **zero** when absent | venue-reported field | venue-reported field | venue-reported field | venue-reported field | venue-reported field | venue-reported field | venue-defined |

`amount` on a Deribit inverse perpetual is already USD. Dividing it by price to "convert notional to size"
converts a correct value into a wrong one. On Binance COIN-M the same expression is wrong by exactly the
100-USD multiplier. On OKX, applying `ctMult × ctVal` in both your code and the adapter is a straight
multiplier on position size. Same field name, four unit systems.

The fee-asset row has one universal: **never assume the fee arrives in the quote currency.** Buy 36.38 GTC on
Binance spot with a 0.1% fee charged in GTC and you hold 36.34. Selling `trade.amount` returns
`-2010 insufficient balance`; selling `free_balance` returns `-1013 Filter failure: LOT_SIZE`, because 36.34
is not a multiple of `stepSize`. Both endings are in the freqtrade tracker (#5481, #1371).

## What breaks when you write a venue-agnostic abstraction

### A unified `Order` type

```python
class Order:                     # every field here is a lossy projection
    symbol: str
    size: Decimal                # base? contracts? USD? — Panel H says four answers
    price: Decimal               # tick-quantized? 5 sig figs? — Panel H says two rules
    post_only: bool              # rejected? cancelled? repriced? — three states, one bit
    client_order_id: str         # Deribit has none
    status: Literal["NEW", "PARTIALLY_FILLED", "FILLED", "CANCELED", "REJECTED"]
```

- **`size: Decimal`** discards the unit. `size_in_contracts = usd_notional / price` is off by 100× on Binance
  COIN-M, is a double conversion on OKX if the adapter already applied `ctMult × ctVal`, and is a
  *de*-conversion on a Deribit inverse perp whose `amount` was already USD. Carry `Qty(value, unit)` and
  convert exactly once, at the venue adapter.
- **`price: Decimal`** presumes a tick. Hyperliquid's constraint is significant figures, which no tick-size
  rounder expresses; ccxt#23516 is the recorded outcome — `decimal_to_precision` returned **`0`** for a
  Hyperliquid price because `precisionMode` was `TICK_SIZE` while the precision value was a decimal count.
  A zero price on a live order is a rejection at best.
- **`post_only: bool`** flattens three different post-trade states. Binance Spot encodes it as an order
  *type* (`LIMIT_MAKER`) and **rejects** on cross; Binance Futures uses `timeInForce=GTX` and rejects; Bybit
  and Kraken **cancel**; Hyperliquid `tif: "Alo"` rejects; **Deribit's `post_only` defaults to `true` and
  silently reprices** the order "to be just below the spread" unless you set `reject_post_only: true`. The
  Deribit path is the dangerous one: no error, no fill at your price, and a resting order at a price your
  code never computed — so `order.price != requested_price` from the first message, and every inventory skew
  and stale-quote decision downstream is computed against a book that does not exist.
- **`status`** has no slot for `EXPIRED_IN_MATCH` (Binance STP), for OKX's `mmp_canceled`, or for the
  fifteenth state a venue needs when it *voids* a fill you already booked. Mapping `EXPIRED_IN_MATCH` onto
  `CANCELED` is survivable; mapping the prevented quantity onto `executedQty` books a phantom trade.
- **`client_order_id: str`** is a lie on Deribit, where `label` is a non-unique tag. A type that makes the
  field non-optional forces every Deribit call site to fabricate an identifier the venue will not enforce.

### A shared `clOrdID` helper

Minting is the safe part: intersect the charsets — OKX ≤32 alnum (hyphens rejected) ∩ Kraken free-ASCII ≤18 ∩
Binance futures `^[\.A-Z\:/a-z0-9_-]{1,36}$` ∩ Bybit ≤36 — and you get **≤18 alphanumeric characters, no
punctuation**, validated at construction.

The unsafe part is the retry policy people attach to it. `if timeout: resend(same_client_id)` is correct on
Coinbase Advanced Trade, correct on Nasdaq OUCH under `UserRefNum`, correct on a FIX venue that implements the
`PossResend` application check, correct on Hyperliquid **only if you resend the byte-identical signed action
under the identical nonce** — and creates a second order on Binance, OKX, Kraken REST/WS and Deribit the
instant the first attempt terminated. The predicate is per-venue and observable; the helper is not the place
to decide it.

The second failure is the return type of the recovery call:

```python
def recover(client_id: str) -> Order | None:   # None means three different things
```

`None` conflates *never created*, *created but outside this venue's retention bound* (OKX cancelled-incomplete
after 2 hours, Bybit fully-cancelled after 24 hours, Binance futures cancelled-with-no-fills after 3 days),
and *this venue has no lookup by client ID at all* (Coinbase Advanced Trade, Deribit). Only the first
justifies resubmitting. Return a tri-state — `Found(order)` / `ProvenAbsent` / `Unresolved(reason)` — and let
`Unresolved` hold the order `INFLIGHT_UNKNOWN` at full notional.

Third: verify the ID reaches the field you think it does. ccxt#23370 records the unified `clientOrderId`
being mapped to Kraken's integer `userref`, not the native `cl_ord_id` — so "I set clientOrderId" did not mean
"the venue stored my client order ID", and recovery-by-client-ID did not work at all on that pairing.

### One `RateLimiter`

A token bucket at N requests/second is wrong on every column of Panel E. Binance needs **two** buckets with
**different keys** — weight per IP, unfilled-order count per account — so sharing an IP across accounts shares
one and not the other. Kraken prices a cancel by the *age of the order being cancelled*, +8 down to +1, which
makes quote-flipping cadence a rate-limit design problem rather than a latency one. Deribit charges credits,
and one `public/get_instruments` metadata refresh costs 10 000 of a 50 000 cap — an innocuous startup call
inside a generic "1 request = 1 token" limiter takes out a fifth of the budget, and exceeding the budget drops
the WebSocket session, which under cancel-on-disconnect cancels your book. Bybit's per-IP breach is an HTTP
403 whose documented remedy is to **stop for ten minutes**, so a generic exponential-backoff retry makes it
strictly worse.

The cross-cutting bug is worse than the throughput one: a generic limiter that retries on 429 turns a
rate-limit response into a **duplicate order**, in bursts, during volatility, across many symbols at once —
because no venue in this corpus documents that a 429 on an order endpoint means the order was not created.
Rate limiting and order submission cannot share a retry policy.

### One `OrderBook`

A shared `apply_delta(seq, side, price, qty)` presumes one gap predicate. There are at least six (Panel C),
and they disagree about what a *gap* even is: Binance Spot's `[U, u]` window, Binance Futures' `pu ==
previous u`, OKX's `prevSeqId`, Bybit's `u == 1`-means-restart with same-`u` re-emission, Coinbase's
strict +1, Deribit's `prev_change_id` absent on the first snapshot. Implement the union as one predicate and
you get a book that is internally consistent and economically wrong.

Integrity checks do not unify either. Kraken's CRC32 covers **exactly the top 10 levels regardless of the
depth you subscribed to** — checksum your whole 100-level book and you mismatch on every message. OKX's
checksum has returned a constant `0` in production since 2026-06-23: code written as
`if msg.get('checksum') and computed != msg['checksum']: resync()` **silently stopped validating** that day,
with no error and no log line, while code written as `if computed != msg['checksum']: resync()` degraded into
a resync loop. Write integrity checks so that a disappearing field fails loudly — `if checksum is None or
checksum == 0: alert()` — never so that a falsy value skips the branch.

And the snapshot itself has no shared shape: Binance Spot and Futures have **no in-band snapshot** and require
a REST re-fetch, while Bybit, Kraken and Deribit deliver one on the wire. A single `on_orderbook_message`
handler shared across venues cannot be correct for all of them, and a REST snapshot is depth-limited — levels
outside it are *unknown*, not empty, so a depth-weighted VWAP over the whole local book overstates liquidity.

## The eight fields that must not be flattened

| Field | Why it cannot be one type | Carry instead |
|---|---|---|
| `size` | base / contracts / USD-notional, four systems across nine venues | `Qty(value, unit)`; convert once, in the adapter |
| `price` | tick-size vs 5-significant-figures vs integer-exempt | the venue's own quantizer, returning a **string** |
| `post_only` | rejected / cancelled / **silently repriced** | a three-valued would-cross outcome, plus `reject_post_only` on Deribit |
| `client_order_id` | absent on Deribit; idempotent only on Coinbase AT; window zero on four venues | the ID **and** its collision class (A / B / C) and window |
| recovery result | not-created vs past-retention vs no-lookup-exists | `Found(order)` / `ProvenAbsent` / `Unresolved(reason)` |
| rate-limit budget | weight-per-IP + count-per-account + age-priced cancels + credits | a per-venue limiter keyed the way that venue keys it |
| book sequence | six mutually incompatible gap predicates and two snapshot models | the venue's own join algorithm, and nothing else |
| fee | asset varies with **side**, discount programme, and settlement type | `Fee(amount, asset)`, never a scalar rate |

Where a capability genuinely does not exist on a venue, model it as absent — an optional field, a capability
flag, an adapter that refuses — rather than as a default. A lowest-common-denominator interface picks one
venue's behaviour and applies it to eight that never agreed to it.
