# ccxt and FIX — two client layers with their own failure modes

Two abstraction layers that add failures the venue does not have. ccxt's own manual says a `RequestTimeout`
leaves the outcome unknown and, fifty lines earlier, that its *parent class* "can be blindly re-tried"; the
single funnel every signed REST call passes through predicates on the parent class with no HTTP-method
discrimination. FIX splits the same problem into two orthogonal flags — `PossDupFlag(43)` is a session-layer
assertion, `PossResend(97)` an application-layer one — and handling either with the other's mechanism drops
orders in one direction and re-books fills in the other. All ccxt file/line citations are against commit
`6059cf4724ea9134b6c75194a341a1bb32e503a6` (2026-08-24) and are a **verification recipe**: re-run them
against the version in your lockfile before relying on any of them.

## Contents

### ccxt
- The retry funnel — `fetch2`, `e instanceof OperationFailed`, `maxRetriesOnFailure`, and what to set
- The two contradictory manual passages, quoted, and which one to believe
- The auto-generated client order ID is fresh per call, not per intent
- Why the documented `fetchBalance()` recovery is race-prone, and the replacement
- `precisionMode`, and the `ROUND`/`TRUNCATE` asymmetry between price and amount
- `createMarketBuyOrderRequiresPrice` — the base-vs-quote market-buy trap, worked
- Unified vs raw: `info`, `clientOrderId` mapping, and `calculateFee`
- ccxt.pro: the sliding cache, `newUpdates`, and one rate limiter per instance
- Mapping the exception hierarchy onto UNKNOWN / rejected

### FIX
- `PossDupFlag(43)` vs `PossResend(97)` — which layer owns each
- ResendRequest, GapFill, SequenceReset-Reset, and venues that disable resend entirely
- `ClOrdID(11)` / `OrigClOrdID(41)` chains, and `CxlRejReason` = Unknown Order
- `OrderQty = CumQty + LeavesQty`, chain-cumulative across replaces
- `ExecRefID(19)` — Trade Cancel and Trade Correct
- Cancel-on-disconnect, logon sequencing, and iLink 3 / FIXP UUID identity

## ccxt

### The retry funnel

`fetch2` (`ts/src/base/Exchange.ts:6404`) is the single funnel: `request()` (`:6455`) calls it, and every
generated implicit endpoint — `privatePostOrder` included — is bound to `request` by
`this.defineRestApi(this.api, 'request')` (`:629`). Inside the loop — three checkable facts follow it:

```ts
// ts/src/base/Exchange.ts:6435
} catch (e) {
    if (e instanceof OperationFailed) {          // <-- the entire retry predicate
        if (i < retries) { ... await this.sleep (retryDelay); }
        else { throw e; }
    } else { throw e; }
}
```

1. **The predicate is the error class alone.** `ts/src/base/errorHierarchy.ts` nests
   `OperationFailed → NetworkError → RequestTimeout` (declarations at `ts/src/base/errors.ts:171`, `:177`,
   `:219`). A submit timeout is inside the retry set.
2. **No method or path discrimination.** A `POST /api/v3/order` is retried on exactly the same terms as a
   `fetchTicker`; nothing in `fetch2` reads `request['method']`.
3. **It arms from a global option.** `handleOptionAndParams` (`Exchange.ts:6796`) falls through the
   method-scoped lookup to `value = this.safeValue2(this.options, optionName, defaultOptionName)`
   (`:6814`). Setting `exchange.options['maxRetriesOnFailure'] = 3` for a flaky `fetchOHLCV` silently arms
   blind order re-submission for every POST in the process.

**The mitigation, stated honestly.** The default is `retries = 0`, so this is off unless someone turns it on.
And the request body was already built by `createOrderRequest` before `fetch2` was entered, so the retry
re-sends the *identical* `newClientOrderId` — which on Binance is rejected as a duplicate **while the order
is open**, and accepted as a fresh order once it is not. ccxt is accidentally safe on venues that enforce
open-order uniqueness and unsafe on venues with no collision check at all.

### The two contradictory manual passages

| Where | Text | About |
|---|---|---|
| `wiki/Manual.md:8816` | "When a `RequestTimeout` is raised, **the user doesn't know the outcome of a request** (whether it was accepted by the exchange server or not)." | the member |
| `wiki/Manual.md:8867` | "So, once again: **OperationFailed can be blindly re-tried and should success**, while `OperationRejected` is a failure that depends on specific exact factors…" | the parent class |

`RequestTimeout` *is* an `OperationFailed`. The manual is method-discriminating where the code is not — the
same section (`:8820`) says "for fetching requests it is safe to retry the call" and gives `cancelOrder()` its
own procedure. **Believe the per-exception paragraph, not the taxonomy paragraph, and never state the retry
rule as a property of an error class.** It is a property of the operation: is re-executing this request
capable of creating a second economic effect?

```python
ex = ccxt.binance({'enableRateLimit': True, 'newUpdates': True})
ex.options['maxRetriesOnFailure'] = 0     # never blind-retry a POST through fetch2
assert ex.precisionMode == ccxt.TICK_SIZE # pin what THIS build reports; never hardcode the mode
```

Setting it to 0 does **not** make you safe — it makes ccxt stop deciding for you. Your own retry wrapper
still has to distinguish a GET from a create-order.

### The auto-generated client order ID is per call, not per intent

`ts/src/binance.ts:5791-5801`:

```ts
if (clientOrderId === undefined) {
    const broker = this.safeDict (this.options, 'broker');
    if (broker !== undefined) {
        const brokerId = this.safeString (broker, 'spot');
        if (brokerId !== undefined) {
            request['newClientOrderId'] = brokerId + this.uuid22 ();   // minted here
        }
    }
} else {
    request['newClientOrderId'] = clientOrderId;
}
```

`uuid22()` runs inside `createOrderRequest`, i.e. **once per `createOrder()` invocation**. The natural
caller-level retry —

```python
try:    ex.create_order(sym, 'limit', 'buy', qty, px)
except ccxt.RequestTimeout:
        ex.create_order(sym, 'limit', 'buy', qty, px)   # DIFFERENT id, SECOND order
```

— mints a different ID and creates a second order reconciliation cannot merge with the first. Contrast
stripe-python, which computes the idempotency header **above** the retry loop and re-uses the same dict on
every attempt (`stripe/_api_requestor.py:567-573` → `_http_client.py:267`). ccxt's only idempotency
affordance is the ID *you* supply: pass `params={'newClientOrderId': cid}` with `cid` persisted before the
first attempt.

### The documented recovery, and the replacement

`wiki/Manual.md:8824-8826` prescribes, verbatim:

> "- if a request to `createOrder()` fails with a `RequestTimeout` the user should: - call `fetchOrders()`,
>   `fetchOpenOrders()`, `fetchClosedOrders()` to check if the request to place the order has succeeded and
>   the order is now open - **if the order is not `'open'` the user should `fetchBalance()` to check if the
>   balance has changed** since the order was created on the first run and then was filled and closed.""

The last rung is the problem: **balance moves for reasons that have nothing to do with your order** — another
strategy on the same key, a fee debit, a funding payment, a settlement, an ADL, a liquidation. It is a lower
bound, not a procedure. Replace it with a query keyed on the ID you minted:

```python
def resolve_ambiguous_create(ex, symbol, cid, sent_at):
    # 1. private stream buffer over a window bracketing sent_at (cheapest, matching-engine sourced)
    # 2. single-order query BY CLIENT ID, backed off across the venue's propagation window
    for delay in (0.2, 0.5, 1.0, 2.0, 5.0):
        try:
            # param key is venue-specific; confirm in ts/src/<venue>.ts where your id lands
            return ex.fetch_order(None, symbol, {'origClientOrderId': cid})
        except ccxt.OrderNotFound:
            time.sleep(delay)          # NOT proof of non-creation — Binance query reads are async
        except ccxt.NetworkError:
            time.sleep(delay)          # a failed observation is not an observation
    # 3. open orders  4. order history over [sent_at - skew, now]  5. fetch_my_trades, same window
    #    (5) is the ONLY rung that speaks to economic effect: an order can be invisible in every
    #    order endpoint and still have moved your position
    return None                        # -> INFLIGHT_UNKNOWN at full notional, gate closed, clock running
```

### `precisionMode` and the ROUND/TRUNCATE asymmetry

`exchange.precisionMode` is an **instance property** (`Exchange.ts:413`) whose per-exchange value has changed
across ccxt versions. The manual now calls `DECIMAL_PLACES` "**DEPRECATED, CCXT no longer uses this mode
anywhere**" (`wiki/Manual.md:1219`) and at this commit `binance.ts:1340`, `kraken.ts:536` and
`hyperliquid.ts:221` all set `TICK_SIZE` — but ccxt#13554 records Binance being explicitly *left* on
`DECIMAL_PLACES` at migration time. A helper written against the old semantics reads
`market['precision']['price'] = 0.01` as "0 decimal places"; ccxt#23516 is the concrete failure, where
`decimal_to_precision` returned **`0`** for a Hyperliquid price under a mode mismatch.

| Mode | Constant | `market['precision']['price']` means |
|---|---|---|
| `TICK_SIZE` | `4` | the tick itself, a float — `0.01` |
| `SIGNIFICANT_DIGITS` | `3` | Nth place of the last non-zero digit |
| `DECIMAL_PLACES` | `2` | count of decimal digits — **deprecated** |

**Read `exchange.precisionMode` at runtime and branch on it. Never hardcode a mode, never infer it from the
magnitude of the number.**

The rounding directions are not symmetric, and this is load-bearing:

```ts
// ts/src/base/Exchange.ts:7328   priceToPrecision
const result = this.decimalToPrecision (price, ROUND,    market['precision']['price'],  ...);
// ts/src/base/Exchange.ts:7340   amountToPrecision
const result = this.decimalToPrecision (amount, TRUNCATE, market['precision']['amount'], ...);
```

Price is **rounded to nearest**; amount is **truncated toward zero**. Truncation is the safe direction for
amount. ROUND is not safe for a fee-grossed exit price: it surrenders up to half a tick of the markup roughly
half the time. Compute the exit in `Decimal`, quantize up for a sell and down for a buy, and hand ccxt a
value already on-tick. Both helpers throw `InvalidOrder` when the result is the string `'0'` (`:7329`,
`:7341`) — that guard exists because the silent-zero bug shipped. Both return a **string**: send it
unmodified, since `float(ex.amount_to_precision(...))` re-introduces the representation the helper removed
and `str(1e-05) == '1e-05'` reaches the wire as illegal characters.

### `createMarketBuyOrderRequiresPrice`

On 21 exchange implementations at this commit — Bybit, OKX, Coinbase, Gate, HTX, Bitget, KuCoin-family and
others — a **spot market buy** takes the **quote cost**, not the base quantity. `ts/src/bybit.ts:4404-4425`:

```ts
} else if (market['spot'] && isMarketOrder && (side === 'buy')) {
    let createMarketBuyOrderRequiresPrice = true;                       // :4407 default at the call site
    [ createMarketBuyOrderRequiresPrice, params ] = this.handleOptionAndParams (params, 'createOrder', 'createMarketBuyOrderRequiresPrice');
    if (createMarketBuyOrderRequiresPrice) {
        const quoteAmount = Precise.stringMul (this.numberToString (amount), priceString);
        request['qty'] = this.getCost (symbol, cost !== undefined ? cost : quoteAmount);   // amount * price
    } else {                                          // no cost and no price:
        request['qty'] = amountString;                // your `amount` is sent AS the quote cost
    }
}
```

Worked, BTC/USDT at 60,000, intent "buy 0.5 BTC":

| Config | Call | `qty` on the wire | You get |
|---|---|---|---|
| flag `true` (default) | `create_order(sym,'market','buy', 0.5, 60000)` | `30000` (quote) | ≈0.5 BTC — correct |
| flag `true` | `create_order(sym,'market','buy', 0.5)` | — | `InvalidOrder`: price required |
| flag `false` | `create_order(sym,'market','buy', 0.5)` | `0.5` (quote) | **0.5 USDT of BTC** — 1/60000× |
| flag `false` | `create_order(sym,'market','buy', 30000)` | `30000` | ≈0.5 BTC — correct |

Note `bybit.ts:1142` sets `'createMarketBuyOrderRequiresPrice': false` in `options.createOrder` while the
call site defaults the local to `true` — the effective value is whichever the options lookup finds. **Assert
the resolved value at startup and never write a spot market buy without it.** OKX's
`createMarketBuyOrderWithCost` forces the flag `false` and adds `tgtCcy: 'quote_ccy'` (`ts/src/okx.ts:3120`).

### Unified vs raw

- **`info` is the raw venue payload and the unified struct is lossy.** Anything ccxt has no unified name for
  — `positionSide`, `workingType`, `selfTradePreventionMode`, Binance's `z`/`Z` cumulative fields, the
  venue's own realized-PnL figure — reaches you only via `order['info']`. Read economic fields from `info`;
  use unified names for control flow.
- **A unified field name is not a guarantee about the venue field.** ccxt#23370: unified `clientOrderId` was
  mapped to Kraken's integer `userref`, not the native `cl_ord_id` — so "I set `clientOrderId`" did not mean
  the venue held your client order ID. Grep `ts/src/<venue>.ts` for where your ID lands in `request`.
- **`calculateFee` is disclaimed by ccxt itself** (`wiki/Manual.md:7470`): "**WARNING! This method is
  experimental, unstable and may produce incorrect results in certain cases.** … Do not rely on precalculated
  values" (repeated at `:1171` and `:7510`). Never let a computed fee reach a ledger; book
  `commission`/`commissionAsset` as reported, and record an explicit absence.

### ccxt.pro

`watch*` methods resolve from a **sliding cache**, not a queue. `ts/src/pro/binance.ts:161` declares
`'ordersLimit': 1000` and `:5550-5551` constructs `this.orders = new ArrayCacheBySymbolById(limit)`.
The manual (`wiki/ccxt.pro.manual.md:353`): "**The cache limits have to be set prior to calling any
watch-methods and cannot change during a program run.**"

**A version divergence you must check rather than read from the docs.** At this commit
`ts/src/base/Exchange.ts:440` declares `newUpdates: boolean = true` and `:631` overrides it from
`options['newUpdates']` — the default is already `true`. The manual still carries the older
*Deprecation Warning* at `wiki/ccxt.pro.manual.md:427`: "in the future `newUpdates: true` will be the default
mode and you will have to set newUpdates to false to get the sliding cache." **Pass `newUpdates` explicitly
and assert `exchange.newUpdates` at startup**, because the naive loop

```python
while True:
    for o in await ex.watchOrders():   # newUpdates=False -> the WHOLE cache, every wake-up:
        handle(o)                      # up to 1000 historical orders reprocessed per iteration
```

replays history on every resolution unless `handle` is idempotent. Make it idempotent regardless — that is
the same watermark-on-client-order-ID guard the redelivery rule already requires.

Two more: **one rate limiter per exchange instance** — "Do not use multiple instances of the same exchange
with the same API keypair from the same IP address" (`wiki/Manual.md:902`), so every worker holding its own
`ccxt.binance()` defeats the throttle. And ccxt#8245: `watchOrders()` only resolves on an update, so a
program that awaits the watcher before placing never places, and one that places before the socket is up
loses the first `NEW`/`TRADE` events. Establish the private stream, confirm it, then send.

### Mapping the exception hierarchy onto UNKNOWN / rejected

From `ts/src/base/errorHierarchy.ts`. The column that matters is **"could this attempt have created an
order?"** — not "is this transient?".

| Class | Parent | On a create-order POST |
|---|---|---|
| `RequestTimeout` | `NetworkError` | **UNKNOWN.** Manual `:8816` says so outright. Resolve by client ID. |
| `ExchangeNotAvailable` / `OnMaintenance` | `NetworkError` | **UNKNOWN.** The request may have landed before the gateway gave up. |
| `DDoSProtection` / `RateLimitExceeded` | `NetworkError` | **UNKNOWN.** No venue documents that a 429 on an order endpoint guarantees non-creation. |
| `InvalidNonce` / `ChecksumError` | `NetworkError` | Treat as UNKNOWN unless your venue documents that signature/nonce validation strictly precedes matching. Note the class sits under `OperationFailed`, so the funnel retries it. |
| `BadResponse` / `NullResponse` | `OperationFailed` | **UNKNOWN.** The venue may have accepted and ccxt failed to parse the answer. |
| `CancelPending` | `OperationFailed` | Not a create-order outcome; re-read terminal state rather than re-cancelling. |
| `InvalidOrder`, `BadRequest`, `BadSymbol` | `ExchangeError` | **Rejected, no order.** Deterministic — fix the payload; retrying is pointless. |
| `DuplicateOrderId` | `InvalidOrder` | **Rejected — and it proves an order with that ID exists.** It does not prove *this* attempt created it. Query, do not assume. |
| `OrderNotFound` | `InvalidOrder` | Answer to a query, not proof of non-creation immediately after a submit. Re-query with backoff, then fills. |
| `InsufficientFunds` | `ExchangeError` | **Rejected, no order.** Retry only from a new sizing decision, never blindly. |
| `OperationRejected` (+ `MarketClosed`, `NoChange`, …) | `ExchangeError` | Rejected. Manual `:8867`: "depends on specific exact factors that need to be considered, before request can be retried." |
| `AuthenticationError`, `PermissionDenied` | `ExchangeError` | Rejected. Stop; do not loop on credentials. |

The whole `ExchangeError` subtree means **the venue answered**. The whole `OperationFailed` subtree means
**you did not hear the answer** — and for a state-changing call, silence is UNKNOWN.

## FIX

### `PossDupFlag(43)` vs `PossResend(97)`

The two flags are orthogonal, and the split is exactly "retry" vs "duplicate". Dictionary text
(FIX 4.4 Standard Message Header, b2bits fixopaedia):

| Tag | Comment | Receiver processing rule |
|---|---|---|
| `PossDupFlag(43)` | "Always required for retransmitted messages, whether prompted by the sending system or as the result of a resend request." | "**if a message with this sequence number has been previously received, ignore message, if not, process normally**" |
| `PossResend(97)` | "Required when message may be duplicate of another message sent under a **different** sequence number." | "**forward message to application and determine if previously received (i.e. verify order id and parameters)**" |
| `OrigSendingTime(122)` | "Required for message resent as a result of a Resend Request (2). If data is not available set to same value as SendingTime (52)" | — |

- **`PossDup=Y` is a session-layer assertion at the *same* `MsgSeqNum`.** Your engine dedupes it by sequence
  number and it never reaches business logic. Passing it to the application re-books the fill.
- **`PossResend=Y` is an application-layer assertion at a *new* `MsgSeqNum`.** The spec pushes the decision
  up and names the discriminator: *verify order id and parameters*. This is FIX telling you, in normative
  text, that `ClOrdID` is the retry-vs-duplicate discriminator **and that a human has to write the
  comparison**. Nothing compels your counterparty to have written theirs.

Handling either with the other's mechanism is the classic pair of bugs: discarding a `PossResend=Y` order
because "we've seen this ClOrdID" drops a legitimately new order; passing a `PossDup=Y` execution report to
the fill handler double-books the position.

FIX has vocabulary for collision — `OrdRejReason(103) = 6` "Duplicate Order (e.g. dupe ClOrdID<11>)",
`CxlRejReason(102) = 6` "Duplicate ClOrdID<11> received" — but note what those are: **rejections**. FIX has no
"return the original order" semantic anywhere.

### ResendRequest, GapFill, and the venues that have neither

The session-layer replay path is `ResendRequest(35=2)`, answered with the original messages carrying
`PossDup=Y`, or with `SequenceReset(35=4)` in **Gap Fill** mode (`GapFillFlag=Y`) for administrative messages
that should not be replayed. `SequenceReset` in **Reset** mode is the disaster hatch, and the FIX 4.4
dictionary says why not to reach for it: it "should ONLY be used to recover from a disaster situation which
cannot be recovered via Gap Fill", "may result in the possibility of lost messages", "should NOT be used as a
normal response to a Resend Request", can only *increase* the sequence number, and its receipt at an
out-of-sequence `MsgSeqNum` must **not** trigger a further ResendRequest. An engine that answers every
ResendRequest with a Reset has silently converted a recoverable gap into permanent data loss, with no error
anywhere.

**And the mechanism is not universal.** Binance's SPOT FIX API states:

> "### Resend Request `<2>` — **Resend requests are currently not supported.**"
> — <https://github.com/binance/binance-spot-api-docs/blob/master/fix-api.md>

The same document requires strict monotonic sequencing — the client's `MsgSeqNum(34)` "must increase
monotonically, with each subsequent message having a sequence number that is exactly 1 greater than the
previous message" — and offers `MessageHandling(25035)` = `UNORDERED(1)` / `SEQUENTIAL(2)`.

**Consequence, and this is the sentence to act on:** a FIX venue that disables ResendRequest removes the
session-layer replay mechanism, and a venue that has not implemented the `PossResend` application check
removes the application-layer one. With neither, FIX gives you **no** replay path and you are back to the
same posture as a REST venue: query-first by `ClOrdID`, never resend.

| Venue / protocol | `ClOrdID` uniqueness window | Safe recovery move |
|---|---|---|
| Kraken FIX | "across open orders **and FIX session**" (docs.kraken.com/api/docs/guides/spot-clordid/) | Resend the same `ClOrdID` — safe for the life of that session, and only that session |
| Binance SPOT FIX | open orders only; ResendRequest unsupported | Query by `ClOrdID`. Never resend. |
| Generic FIX counterparty | sender's obligation only — `ClOrdID(11)`: "Uniqueness must be guaranteed within a single trading day" | New `MsgSeqNum`, same `ClOrdID`, `PossResend=Y` — **only** with written confirmation the venue implements the application check; otherwise query-first |

`ClOrdID(11)`'s dictionary text is a constraint on the **sender**, not a promise from the venue: it says
firms "should ensure uniqueness across days, for example by embedding a date within the ClOrdID field". It
says nothing about what the venue retains or does on collision.

### `ClOrdID` chains, and the reject that says "NONE"

`OrigClOrdID(41)` is "ClOrdID of the **previous** order (**NOT the initial order of the day**)". So the
identity of a live FIX order is a **chain** of ClOrdIDs across every cancel and cancel-replace, and a client
that persists only the latest one cannot resolve a fill that was in flight across a replace. Persist the
chain, and reconcile against the whole chain.

`OrderCancelReject(35=9)` echoes the request's `ClOrdID(11)` and the `OrigClOrdID(41)` of the order being
cancelled or replaced — **except** that when `CxlRejReason` = Unknown Order, `OrigClOrdID` is set to the
literal string `"NONE"`. Code that keys a lookup on `OrigClOrdID` without that check will search for an order
called `NONE`. The same message notes "Filled orders cannot be changed."

### `OrderQty = CumQty + LeavesQty`

The identity holds on every `ExecutionReport(35=8)`, and **`CumQty` and `AvgPx` are cumulative across the
whole replace chain** — they reflect all versions of the order, not the current one. A client that resets
`CumQty` to zero on a replace mis-states its own position by exactly the pre-replace fills. Assert the
identity on every report and treat a violation as a break, not a rounding issue.

`OrdStatus(39)` has a precedence ordering: `PendingCancel` is highest, then `PendingReplace`, `DoneForDay`,
`Calculated`, `Filled`, `Stopped`, `Suspended`, `Canceled`/`Expired`, `PartiallyFilled`,
`New`/`Rejected`/`PendingNew`, `AcceptedForBidding`. An order that is simultaneously partially filled and
pending cancel reports `PendingCancel` — so **`OrdStatus` alone does not tell you your filled quantity**;
`CumQty` does. FIX additionally specifies that execution information "should not be communicated in the same
report as one which communicates other state changes", so a fill and a state change arrive as two reports and
your handler must not assume one message carries both.

*(FIX 4.4 Appendix D's per-scenario order-state-change matrices were **not** read as raw text for this file;
treat scenario-level claims about them as unverified until you read
`FIX-Latest-as-of-EP284-Order-State-Changes.pdf` yourself.)*

### `ExecRefID(19)` — trade cancel and trade correct

`ExecRefID(19)` is required on `ExecType` = Trade Cancel and Trade Correct, and it points at **the last
corrected `ExecID`** — not the original. A correction chain therefore has to be walked, and applying a
correction against the original `ExecID` re-applies an amount that was already superseded. The rule this
implies: **position and PnL must be revisable after the fact** — the venue can cancel or correct a trade you
already booked — **while the book must not be**, because a break has no effect on current book state.

Same shape as the terminal-state rule: a terminal order state accepts exactly the events by which the venue
corrects a fact you already booked, and nothing else.

### Cancel-on-disconnect and session lifecycle

Arm the venue-native switch at logon, not in your process. Deribit's FIX interface uses tag `9001`
`CancelOnDisconnect` and tag `9003` `DontCancelOnDisconnect`; Deribit also documents that a **graceful**
logout does *not* cancel orders even with COD enabled, while an unexpected disconnect, inactivity timeout or
heartbeat failure does. That asymmetry means your clean-shutdown path must cancel explicitly; the switch
will not do it for you. Reconnect preserving sequence numbers so the gap-fill path can run — every
sequence-reset variant trades recoverability for liveness, and after any reset you have no replay window and
must reconcile by querying order state.

### iLink 3 / FIXP — identity is `(sequence number, UUID)`

CME's iLink 3 binary order entry moves the identity of a recoverable message to the **pair** (sequence
number, UUID). Sequence numbers reset to 1 per UUID and per week; the UUID must be monotonically increasing
(microseconds since epoch is the recommended construction) "to prevent usage of duplicate UUID's intraweek,
which can affect subsequent retransmission of those messages." The spec then warns against the reflex move:

> "**Do not Terminate the FIXP session and Re-Negotiate with a new UUID as a normal response to a Not Applied
> message.** Re-Negotiate with a new UUID should be used only to recover from a disaster situation…
> Re-Negotiating with a new UUID will mean recovering messages sent by the exchange in the previous FIXP
> session with the previous UUID" (CME iLink Binary Order Entry — Session Layer)

— i.e. "just reconnect fresh" permanently strands exactly the messages you were trying to recover. Handle
`NotApplied` in place. Relatedly (CME iLink 3 business-layer docs, read as summary only): when a Business
Message Reject omits the sequence number of the rejected message, the exchange did **not** increment its
inbound sequence for it and the client must not either.
