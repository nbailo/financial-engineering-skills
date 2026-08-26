# Binance orders: identity, recovery, error classes and limits

> **Provenance**
> provider: Binance · surface: spot and USDⓈ-M order entry: client order id rules, the query endpoints that resolve a lost response and their retention, error classification, `recvWindow`, rate limits, self-trade prevention, hedge mode and cancel-on-disconnect
> version: as stated in this file's own header, the spot documentation repository at "Last Updated: 2026-07-27" and the derivatives documentation read 2026-08-24. Neither dating was re-checked here.
> verified_at: not established
> sources: https://github.com/binance/binance-spot-api-docs · https://developers.binance.com/docs/binance-spot-api-docs · https://developers.binance.com/docs/derivatives/usds-margined-futures/general-info
> verified: none in this pass. No sentence below was re-read against a source for the 2026-08-25 review pass.
> unverified: all of it, including the dating in the header above. This file predates the provenance requirement and was not re-checked in the 2026-08-25 review pass, so its claims carry the confidence of their original sourcing and no more, with no date you can check. That applies with most force to the error-code table and the retention bounds, which are the two things here that decide whether a retry is safe. The URLs above are where a recheck starts; each of them resolved on 2026-08-25, and nothing in any of them was read against a claim in this file.
> revalidate_when: an error code in the ambiguous set changes meaning, or a new one appears; a query endpoint's retention window moves; the client order id charset or uniqueness scope changes; the spot FIX API starts supporting Resend Request; a rate-limit header or ban escalation rule changes.

Everything Binance-specific about instructing the venue that a correct client cannot infer: the
client-order-ID uniqueness scope and the query endpoints that recover an order whose response was lost, the
error codes that mean UNKNOWN versus the ones that mean rejected, the timing and rate-limit rules that decide
whether an instruction reaches the book at all, and the order-type preconditions that reject it when it does.

## Contents

- [Two venues, not one](#two-venues-not-one): what changes between spot and USDⓈ-M
- [`newClientOrderId`](#newclientorderid): charset, uniqueness scope, `-2010 "Duplicate order sent."`
- [Query-before-retry: endpoints and their retention bounds](#query-before-retry-endpoints-and-their-retention-bounds)
- [Data sources and what `-2013` actually means](#data-sources-and-what--2013-actually-means)
- [Error codes, classified](#error-codes-classified): UNKNOWN vs rejected vs rate-limited, and the HTTP layer (403 WAF, 409, 429, 418, 5XX)
- [Timing: `recvWindow` and the two-phase check](#timing-recvwindow-and-the-two-phase-check)
- [Rate limits](#rate-limits): weight vs order count, headers, ban escalation
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

A shared `BinanceAdapter` branching on `futures: bool` and reusing one book-sync routine is the defect the
Binance file set exists to prevent.

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
