# ccxt: a client layer with its own failure modes

An abstraction layer that adds failures the venue does not have. ccxt's own manual says a `RequestTimeout`
leaves the outcome unknown and, fifty lines earlier, that its *parent class* "can be blindly re-tried"; the
single funnel every signed REST call passes through predicates on the parent class with no HTTP-method
discrimination. All ccxt file/line citations are against commit
`6059cf4724ea9134b6c75194a341a1bb32e503a6` (2026-08-24) and are a **verification recipe**: re-run them
against the version in your lockfile before relying on any of them.

## Contents

- The retry funnel: `fetch2`, `e instanceof OperationFailed`, `maxRetriesOnFailure`, and what to set
- The two contradictory manual passages, quoted, and which one to believe
- The auto-generated client order ID is fresh per call, not per intent
- Why the documented `fetchBalance()` recovery is race-prone, and the replacement
- `precisionMode`, and the `ROUND`/`TRUNCATE` asymmetry between price and amount
- `createMarketBuyOrderRequiresPrice`: the base-vs-quote market-buy trap, worked
- Unified vs raw: `info`, `clientOrderId` mapping, and `calculateFee`
- ccxt.pro: the sliding cache, `newUpdates`, and one rate limiter per instance
- Mapping the exception hierarchy onto UNKNOWN / rejected

## The retry funnel

`fetch2` (`ts/src/base/Exchange.ts:6404`) is the single funnel: `request()` (`:6455`) calls it, and every
generated implicit endpoint (`privatePostOrder` included) is bound to `request` by
`this.defineRestApi(this.api, 'request')` (`:629`). Inside the loop, three checkable facts follow it:

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
re-sends the *identical* `newClientOrderId`, which on Binance is rejected as a duplicate **while the order
is open**, and accepted as a fresh order once it is not. ccxt is accidentally safe on venues that enforce
open-order uniqueness and unsafe on venues with no collision check at all.

## The two contradictory manual passages

| Where | Text | About |
|---|---|---|
| `wiki/Manual.md:8816` | "When a `RequestTimeout` is raised, **the user doesn't know the outcome of a request** (whether it was accepted by the exchange server or not)." | the member |
| `wiki/Manual.md:8867` | "So, once again: **OperationFailed can be blindly re-tried and should success**, while `OperationRejected` is a failure that depends on specific exact factors…" | the parent class |

`RequestTimeout` *is* an `OperationFailed`. The manual is method-discriminating where the code is not; the
same section (`:8820`) says "for fetching requests it is safe to retry the call" and gives `cancelOrder()` its
own procedure. **Believe the per-exception paragraph, not the taxonomy paragraph, and never state the retry
rule as a property of an error class.** It is a property of the operation: is re-executing this request
capable of creating a second economic effect?

```python
ex = ccxt.binance({'enableRateLimit': True, 'newUpdates': True})
ex.options['maxRetriesOnFailure'] = 0     # never blind-retry a POST through fetch2
assert ex.precisionMode == ccxt.TICK_SIZE # pin what THIS build reports; never hardcode the mode
```

Setting it to 0 does **not** make you safe; it makes ccxt stop deciding for you. Your own retry wrapper
still has to distinguish a GET from a create-order.

## The auto-generated client order ID is per call, not per intent

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
caller-level retry:

```python
try:    ex.create_order(sym, 'limit', 'buy', qty, px)
except ccxt.RequestTimeout:
        ex.create_order(sym, 'limit', 'buy', qty, px)   # DIFFERENT id, SECOND order
```

This mints a different ID and creates a second order reconciliation cannot merge with the first. Contrast
stripe-python, which computes the idempotency header **above** the retry loop and re-uses the same dict on
every attempt (`stripe/_api_requestor.py:567-573` → `_http_client.py:267`). ccxt's only idempotency
affordance is the ID *you* supply: pass `params={'newClientOrderId': cid}` with `cid` persisted before the
first attempt.

## The documented recovery, and the replacement

`wiki/Manual.md:8824-8826` prescribes, verbatim:

> "- if a request to `createOrder()` fails with a `RequestTimeout` the user should: - call `fetchOrders()`,
>   `fetchOpenOrders()`, `fetchClosedOrders()` to check if the request to place the order has succeeded and
>   the order is now open - **if the order is not `'open'` the user should `fetchBalance()` to check if the
>   balance has changed** since the order was created on the first run and then was filled and closed.""

The last rung is the problem: **balance moves for reasons that have nothing to do with your order** (another
strategy on the same key, a fee debit, a funding payment, a settlement, an ADL, a liquidation). It is a lower
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
            time.sleep(delay)          # NOT proof of non-creation; Binance query reads are async
        except ccxt.NetworkError:
            time.sleep(delay)          # a failed observation is not an observation
    # 3. open orders  4. order history over [sent_at - skew, now]  5. fetch_my_trades, same window
    #    (5) is the ONLY rung that speaks to economic effect: an order can be invisible in every
    #    order endpoint and still have moved your position
    return None                        # -> INFLIGHT_UNKNOWN at full notional, gate closed, clock running
```

## `precisionMode` and the ROUND/TRUNCATE asymmetry

`exchange.precisionMode` is an **instance property** (`Exchange.ts:413`) whose per-exchange value has changed
across ccxt versions. The manual now calls `DECIMAL_PLACES` "**DEPRECATED, CCXT no longer uses this mode
anywhere**" (`wiki/Manual.md:1219`) and at this commit `binance.ts:1340`, `kraken.ts:536` and
`hyperliquid.ts:221` all set `TICK_SIZE`, but ccxt#13554 records Binance being explicitly *left* on
`DECIMAL_PLACES` at migration time. A helper written against the old semantics reads
`market['precision']['price'] = 0.01` as "0 decimal places"; ccxt#23516 is the concrete failure, where
`decimal_to_precision` returned **`0`** for a Hyperliquid price under a mode mismatch.

| Mode | Constant | `market['precision']['price']` means |
|---|---|---|
| `TICK_SIZE` | `4` | the tick itself, a float: `0.01` |
| `SIGNIFICANT_DIGITS` | `3` | Nth place of the last non-zero digit |
| `DECIMAL_PLACES` | `2` | count of decimal digits: **deprecated** |

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
`:7341`); that guard exists because the silent-zero bug shipped. Both return a **string**: send it
unmodified, since `float(ex.amount_to_precision(...))` re-introduces the representation the helper removed
and `str(1e-05) == '1e-05'` reaches the wire as illegal characters.

## `createMarketBuyOrderRequiresPrice`

On 21 exchange implementations at this commit (Bybit, OKX, Coinbase, Gate, HTX, Bitget, KuCoin-family and
others) a **spot market buy** takes the **quote cost**, not the base quantity. `ts/src/bybit.ts:4404-4425`:

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
| flag `true` (default) | `create_order(sym,'market','buy', 0.5, 60000)` | `30000` (quote) | ≈0.5 BTC (correct) |
| flag `true` | `create_order(sym,'market','buy', 0.5)` | n/a | `InvalidOrder`: price required |
| flag `false` | `create_order(sym,'market','buy', 0.5)` | `0.5` (quote) | **0.5 USDT of BTC** (1/60000×) |
| flag `false` | `create_order(sym,'market','buy', 30000)` | `30000` | ≈0.5 BTC (correct) |

Note `bybit.ts:1142` sets `'createMarketBuyOrderRequiresPrice': false` in `options.createOrder` while the
call site defaults the local to `true`; the effective value is whichever the options lookup finds. **Assert
the resolved value at startup and never write a spot market buy without it.** OKX's
`createMarketBuyOrderWithCost` forces the flag `false` and adds `tgtCcy: 'quote_ccy'` (`ts/src/okx.ts:3120`).

## Unified vs raw

- **`info` is the raw venue payload and the unified struct is lossy.** Anything ccxt has no unified name for
  (`positionSide`, `workingType`, `selfTradePreventionMode`, Binance's `z`/`Z` cumulative fields, the
  venue's own realized-PnL figure) reaches you only via `order['info']`. Read economic fields from `info`;
  use unified names for control flow.
- **A unified field name is not a guarantee about the venue field.** ccxt#23370: unified `clientOrderId` was
  mapped to Kraken's integer `userref`, not the native `cl_ord_id`. So "I set `clientOrderId`" did not mean
  the venue held your client order ID. Grep `ts/src/<venue>.ts` for where your ID lands in `request`.
- **`calculateFee` is disclaimed by ccxt itself** (`wiki/Manual.md:7470`): "**WARNING! This method is
  experimental, unstable and may produce incorrect results in certain cases.** … Do not rely on precalculated
  values" (repeated at `:1171` and `:7510`). Never let a computed fee reach a ledger; book
  `commission`/`commissionAsset` as reported, and record an explicit absence.

## ccxt.pro

`watch*` methods resolve from a **sliding cache**, not a queue. `ts/src/pro/binance.ts:161` declares
`'ordersLimit': 1000` and `:5550-5551` constructs `this.orders = new ArrayCacheBySymbolById(limit)`.
The manual (`wiki/ccxt.pro.manual.md:353`): "**The cache limits have to be set prior to calling any
watch-methods and cannot change during a program run.**"

**A version divergence you must check rather than read from the docs.** At this commit
`ts/src/base/Exchange.ts:440` declares `newUpdates: boolean = true` and `:631` overrides it from
`options['newUpdates']`; the default is already `true`. The manual still carries the older
*Deprecation Warning* at `wiki/ccxt.pro.manual.md:427`: "in the future `newUpdates: true` will be the default
mode and you will have to set newUpdates to false to get the sliding cache." **Pass `newUpdates` explicitly
and assert `exchange.newUpdates` at startup**, because the naive loop

```python
while True:
    for o in await ex.watchOrders():   # newUpdates=False -> the WHOLE cache, every wake-up:
        handle(o)                      # up to 1000 historical orders reprocessed per iteration
```

replays history on every resolution unless `handle` is idempotent. Make it idempotent regardless; that is
the same watermark-on-client-order-ID guard the redelivery rule already requires.

Two more: **one rate limiter per exchange instance**, "Do not use multiple instances of the same exchange
with the same API keypair from the same IP address" (`wiki/Manual.md:902`), so every worker holding its own
`ccxt.binance()` defeats the throttle. And ccxt#8245: `watchOrders()` only resolves on an update, so a
program that awaits the watcher before placing never places, and one that places before the socket is up
loses the first `NEW`/`TRADE` events. Establish the private stream, confirm it, then send.

## Mapping the exception hierarchy onto UNKNOWN / rejected

From `ts/src/base/errorHierarchy.ts`. The column that matters is **"could this attempt have created an
order?"**, not "is this transient?".

| Class | Parent | On a create-order POST |
|---|---|---|
| `RequestTimeout` | `NetworkError` | **UNKNOWN.** Manual `:8816` says so outright. Resolve by client ID. |
| `ExchangeNotAvailable` / `OnMaintenance` | `NetworkError` | **UNKNOWN.** The request may have landed before the gateway gave up. |
| `DDoSProtection` / `RateLimitExceeded` | `NetworkError` | **UNKNOWN.** No venue documents that a 429 on an order endpoint guarantees non-creation. |
| `InvalidNonce` / `ChecksumError` | `NetworkError` | Treat as UNKNOWN unless your venue documents that signature/nonce validation strictly precedes matching. Note the class sits under `OperationFailed`, so the funnel retries it. |
| `BadResponse` / `NullResponse` | `OperationFailed` | **UNKNOWN.** The venue may have accepted and ccxt failed to parse the answer. |
| `CancelPending` | `OperationFailed` | Not a create-order outcome; re-read terminal state rather than re-cancelling. |
| `InvalidOrder`, `BadRequest`, `BadSymbol` | `ExchangeError` | **Rejected, no order.** Deterministic: fix the payload; retrying is pointless. |
| `DuplicateOrderId` | `InvalidOrder` | **Rejected, and it proves an order with that ID exists.** It does not prove *this* attempt created it. Query, do not assume. |
| `OrderNotFound` | `InvalidOrder` | Answer to a query, not proof of non-creation immediately after a submit. Re-query with backoff, then fills. |
| `InsufficientFunds` | `ExchangeError` | **Rejected, no order.** Retry only from a new sizing decision, never blindly. |
| `OperationRejected` (+ `MarketClosed`, `NoChange`, …) | `ExchangeError` | Rejected. Manual `:8867`: "depends on specific exact factors that need to be considered, before request can be retried." |
| `AuthenticationError`, `PermissionDenied` | `ExchangeError` | Rejected. Stop; do not loop on credentials. |

The whole `ExchangeError` subtree means **the venue answered**. The whole `OperationFailed` subtree means
**you did not hear the answer**, and for a state-changing call, silence is UNKNOWN.
