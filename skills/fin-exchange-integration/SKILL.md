---
name: fin-exchange-integration
description: >-
  Use when code trades on a venue you do not operate — ccxt, binance, bybit, okx, kraken, deribit,
  hyperliquid, alpaca, ib_insync, FIX, polymarket, kalshi — or names create_order/cancelOrder,
  newClientOrderId/orderLinkId/clOrdId/cloid, tickSize/stepSize/minNotional, a depth or user-data stream,
  or derives fills, fees, position or PnL from one. A 50-line bot counts. Skip if the code IS the venue:
  fin-matching-and-settlement.
license: MIT
---

# Trading against a venue you do not operate

Your code is a client: the venue holds the authoritative copy of your orders, fills, position and PnL, and it
answers if you ask. Every rule here follows from one question: **if this response never arrives, what does the
venue now believe, and how do I find out without sending a second order?** An external party can confirm your
state, so reconciliation against that answer, not defensive coding, is the safety net. If your code *is* the
venue (matching, allocation, priority, feed publication, clearing) load `fin-matching-and-settlement` instead;
if a fill becomes a journal entry, that half is `fin-ledger`.

> **`G1`–`G7`** are the always-on financial guardrails: **G1** economic-diff gate · **G2** a named risk is implemented or the process refuses to start · **G3** every comment claim checked against the code · **G4** an ambiguous external call has three phases and the first one COMMITs · **G5** enumerate legal `(state, event)` pairs, guard the version on the entity id, re-read from the authority · **G6** a watermark advances only past a verifiably covered range · **G7** the reconciliation runs in production or it does not exist. Install them with `scripts/install-guardrails.sh`; every rule below stands on its own without them.

## When this applies

Any diff that imports `ccxt`, `python-binance`, `hyperliquid`, `deribit`, `alpaca` or `ib_insync`, speaks
FIX/OUCH/SBE as a client, calls `create_order`/`cancelOrder`/`cancel_replace`, names `newClientOrderId`/
`clOrdId`/`cloid`, `exchangeInfo`, `tickSize`/`stepSize`, `listenKey` or a user-data websocket, or derives
fills, average price, commission, position, margin, funding, PnL or a book from a venue's payloads. A
throwaway script counts. A paper-trading branch counts: the same code gets live keys later.

**Not this skill.** You own the book and match incoming against resting orders, compute allocation or priority,
publish a sequenced feed, or run a clearing or liquidation batch → `fin-matching-and-settlement`. You post
fills into double-entry accounts → `fin-ledger`.

**Read your venue's file before writing any recovery path.** The code names or hits `binance` →
[binance.md](references/venues/binance.md). `okx`, `bybit`, `kraken` →
[okx-bybit-kraken.md](references/venues/okx-bybit-kraken.md). `coinbase`, `deribit`, `hyperliquid` →
[coinbase-deribit-hyperliquid.md](references/venues/coinbase-deribit-hyperliquid.md). `polymarket`, `kalshi`,
`conditionId`, `negRisk` → [prediction-markets.md](references/prediction-markets.md). Read it immediately and
apply it in order. Do not summarise it.

## The send path: seven steps, every order, every time

**Step 1: pin the instrument metadata.** Fetch filters from the live venue (`exchangeInfo` or equivalent) at
startup, cache with a refresh, and commit a production fixture for tests. Validate against the filter set for
**the order type you are sending**: `MARKET_LOT_SIZE` is a separate filter from `LOT_SIZE` and applies only to
MARKET orders, and `NOTIONAL` and `MIN_NOTIONAL` are different filter types of which a symbol exposes one.

**Step 2: quantize toward validity.** Round price to `tickSize` and quantity to `stepSize` in the direction
that keeps the order legal, then re-check **every** filter simultaneously: `price % tick == 0`,
`qty % step == 0`, `price * qty >= minNotional`, `minQty <= qty <= maxQty`. Do this in `Decimal`:
`int(0.29/0.01) == 28`, so a float floor-division silently loses a whole step. Serialize as a decimal string.
`str(1e-05) == '1e-05'` reaches the wire as illegal characters. If quantization would produce `qty == 0`,
return an explicit skip signal; never a silent no-op.

**Step 3: mint the client order ID and commit it before the socket write.** Get the per-venue guarantee right
before you write the retry; see the rule below.

**Step 4: classify the response, then resolve.** A 200 means accepted, never executed. Anything that is not a
business rejection is UNKNOWN. Do not resubmit. Run the ladder below.

**Step 5: fold the fills.** Read cumulative filled quantity from the venue (`executedQty`, `cumQty`), never a
delta you accumulated. Dedupe on `trade_id`; the same fill arrives on the stream *and* the poll. Recompute
average price from the persisted fill set every time. Record an overfill; do not clamp it. Subtract a
base-asset commission from the credited quantity before computing what you can sell.

**Step 6: keep the book and the connection honest.** Follow **your venue's** exact snapshot/incremental join
algorithm and nothing else: Binance Spot and Binance Futures use different algorithms and the wrong one is the
single most-copied incorrect snippet in the ecosystem. On any sequence gap, **discard the book and re-snapshot,
never patch.** Renew the user-data keepalive; a lapsed `listenKey` (60-minute lifetime) is a blind position.
Judge staleness from the data, not from socket state.

## Step 7, REQUIRED OUTPUT: emit these two tests as code, in this response

Before this bot runs against live keys, the response that creates or edits the order path **contains both
tests, as code**. Not a description of them, not a TODO, not a pointer to a test plan. Fill this in:

```python
# (a) tests/test_ambiguous_submit.py — the timeout that already filled
def test_timeout_that_already_filled(bot, venue):
    # toxiproxy `timeout` toxic, or a stub: request IS delivered upstream, then 503
    venue.deliver_upstream_then_fail("POST /order", status=503)
    bot.place(SYMBOL, "BUY", qty, px)
    assert venue.post_count("/order") == 1                       # MUST NOT resubmit
    assert venue.was_queried_by(client_id=bot.last_client_order_id)   # MUST query by client ID
    assert len(bot.all_orders(SYMBOL)) == 1                      # exactly one order, not two

def test_timeout_that_never_arrived(bot, venue):
    venue.drop_before_upstream_then_fail("POST /order", status=503)   # mirror case
    bot.place(SYMBOL, "BUY", qty, px)
    assert venue.was_queried_by(client_id=bot.last_client_order_id)
    assert venue.post_count("/order") == 2                       # here the retry DOES happen

# (b) tests/test_filters.py — the filter property test
FILTERS = load_fixture("exchangeInfo.BTCUSDT.json")   # captured from PRODUCTION, not hand-written
@pytest.mark.parametrize("order_type", ["LIMIT", "MARKET"])   # exercises MARKET_LOT_SIZE
@given(price=decimals_near(minPrice, minNotional / minQty, tick_decimals + 3),
       qty=decimals_near(minQty, minNotional / price, step_decimals + 3))
def test_normalize_satisfies_every_filter_simultaneously(price, qty, order_type):
    out = normalize(price, qty, FILTERS, order_type)
    if out is SKIP:                     # an explicit skip signal is a legal outcome
        return
    assert out.qty != 0                 # zero is never a silent result
    assert out.price % tick == 0 and out.qty % step == 0        # Decimal, not float
    assert out.price * out.qty >= min_notional
    assert min_qty <= out.qty <= max_qty
    assert out.qty <= qty               # rounding toward validity never increases size
```

## The client order ID is a correlation key, not an idempotency key

**Treat it as a correlation key unless the venue documents that resending it returns the original order.**
Binance spot and futures, OKX and Kraken REST/WS enforce uniqueness only among *open* orders. The window is
**zero seconds** after fill or cancel, which is exactly the marketable-limit/IOC case where an ambiguous
response is most likely. OKX says it outright: *"Once an order reaches a terminal state (filled, canceled,
mmp_canceled), the same clOrdId may be reused for a new order."* Resending after termination creates a
**second order**, and the malign outcome is a strategy that hedges and exits one lot while carrying a
naked residual it cannot see.

**Specialising G4, an order's committed intent row carries seven fields before the socket write:** the client
order ID; the full economic intent (instrument, side, qty, price, TIF, reduce-only, `positionSide`);
venue + account + API-key identity, because uniqueness is per-account; the venue's own sequence or nonce where
one exists (OUCH `UserRefNum`, Hyperliquid nonce, FIX `MsgSeqNum`); the exact signed payload bytes on
signature-authenticated venues; `sent_at` with a `not_before`/`not_after` bracket, because every history
endpoint is time-windowed; and `state = INTENT_RECORDED`, flipped to `SENT_UNCONFIRMED` after the write.

One ID per **logical order**, reused across every retry of that order, never one per attempt, and never derived
from a value that repeats (bar timestamp, `strategy+symbol+side`, a wall-clock second). Cross-venue charset
intersection is **≤18 alphanumeric characters, no punctuation**; validate at construction, not at send.
**Query-first is the default and the venue table is the exception list.** The retention bounds live with it,
so read your venue's file before writing the retry branch.

## The UNKNOWN resolution ladder

HTTP 5XX, a socket timeout, HTTP 429, Binance `-1006 UNEXPECTED_RESP` and `-1007 TIMEOUT` ("Send status
unknown; execution status unknown") are all **UNKNOWN**. Do not resubmit; resolve by querying the client order
ID. No venue in the corpus documents that a 429 on an order endpoint guarantees non-creation.

- `-2013 NO_SUCH_ORDER` immediately after placement is **not** proof of non-creation. Binance documents three
  data sources (Matching Engine, Memory, Database) with different staleness, and states *"The API system is
  asynchronous, so some delay in the response is normal and expected."* Re-query with backoff across the
  propagation window, then open orders, then order history, then **fills** (the only ground truth about
  economic effect), stopping at the first rung that returns a definite answer.
- `-2011 CANCEL_REJECTED` is expected in normal operation because the order filled between your decision and
  your cancel. Re-read its terminal state; do not retry the cancel as an error.
- Cancel/replace is not atomic. HTTP 409 + `-2021` means **one leg succeeded**; determine which before doing
  anything else. `-2022` means both failed.
- **Never use ccxt's documented `fetchBalance()` timeout-recovery procedure**, which is race-prone against
  fees, funding and other strategies, and set `options['maxRetriesOnFailure']` to 0: ccxt's retry funnel
  re-POSTs a create-order under the *identical* client order ID. Read
  [ccxt-and-fix.md](references/ccxt-and-fix.md) before touching a ccxt error path.

If the ladder cannot resolve it, hold the order `INFLIGHT_UNKNOWN` **at full notional in the risk
calculation**, close the risk gate for that instrument, and escalate. **That state carries a wall-clock budget
declared as a config value.** Past it the system automatically takes the risk-*reducing* action
(cancel-by-client-ID, then flatten the instrument) rather than waiting for a human. "Hold and escalate" with no
clock leaves the desk dead until someone wakes up, and that is how the rule gets disabled.

## The pre-trade check runs inside the function that sends

`max_order_notional`, `max_position`, `max_orders_per_second` and a **price-deviation band derived from that
instrument's own reference price** are evaluated **inside the function that calls `submit_order`**, before the
send, reading live position. Not by a monitor reading a metric, not in a sibling module. Measure exposure from
**orders entered, not executions received**. 17 CFR 240.15c3-5(c)(1) requires the control be "applied on an
automated, pre-trade basis, before orders are routed" and assessed "on the basis of exposure from orders
entered … rather than relying on a post-execution, after-the-fact determination". Derive the band the same way
on every session state (pre-market, auction, halt, continuous). Goldman's pre-market band was bounded by 1.5×
the highest close of *any* listed option, so a $1 order in any name passed it.

## Cancel-on-disconnect is armed at the venue, not in your process

**Arm the venue-native switch at session start** (Binance/Bybit/OKX cancel-on-disconnect, Deribit
`set_heartbeat`, FIX `CancelOnDisconnect`) with a timeout shorter than your reconnect backoff. A process that
dies cannot cancel anything, and the venue's switch is the only one that fires *because you went away*.
**Then** wire the local fallback: an unconditional `cancel_all()` called from inside the state-invalidation
function (the same one that sets `stale`, `disconnected` or `unsynced`) with its return value checked, for
the case where you are still connected and your own state went bad. Under G2 a defined-but-uncalled `on_stale`,
a `cancel_all` behind a config flag defaulting off, or a line deferring the decision to "whoever owns risk" is
the defect, not a plan.

## Market data is stale when `now − ts > max_age`

Every book, mark, index, quote and funding rate is stored with the **venue's own event timestamp** and a
declared `max_age`, and the order-submission path evaluates `now − ts > max_age` on every tick. Quoting stops
on: age > `max_age`, a sequence gap, an unsynced book, or an unrenewed `listenKey`; all judged from the data,
because a socket can be open and delivering nothing.

**This is not the ordering guard.** `ts < last_seen` (ordering) and `now − ts > max_age` (freshness) fail in
opposite situations: a perfectly ordered feed that stopped ten seconds ago passes the first and fails the
second. A system needs both, and a reviewer who greps for `stale` will find the ordering guard and conclude
this rule is satisfied. nautilus's `is_stale` is exactly that (`ts_init < self.builder.ts_last`), and no
project in the corpus has a wall-clock age gate on market data feeding an order decision. Write the `now − ts`
form explicitly.

## Fills fold; they do not accumulate

Order fills by the venue's own sequence or event time before they touch position state. Dedupe on `trade_id`
**before** the state transition, reject the duplicate rather than ignoring it, and write the dedupe set in the
same transaction as the position row. An in-memory `_seen_trade_ids` re-applies every counted fill after a
restart. **Recompute average entry price as an order-independent fold over the persisted fill set on every
update, never accumulate it incrementally.** That is what stops a REST backfill interleaved with the live
socket from permanently corrupting entry price instead of transiently reordering it.

Ship the test that proves the fold: ascending and descending fill order produce a byte-identical average.
nautilus asserts exactly this in `test_avg_px_invariant_to_fill_arrival_order`, and freqtrade reached the same
design independently in `recalc_trade_from_orders`. The one escape hatch is a reorder buffer, and only where
the venue's sequence is authoritative and you assert it.

## After a reconnect, the snapshot is not the recovery

Re-snapshotting is the part everyone already writes. These four are the part that fails:

1. **Gate.** `mark_ready()` is called after `on_resync` completes, never before, and order submission is
   blocked until it is. The ordering bug is the whole failure.
2. **Gap.** Compute what was missed by diffing the snapshot against persisted state and emit a synthetic
   missed-fill event. Recovering net position leaves realized PnL permanently short. Synthesised ids must be
   **deterministic over venue-supplied fields including a venue timestamp**, so the same inference after a
   restart dedupes against itself.
3. **Durability.** `last_trade_id` / `last_update_id` is persisted to disk or Redis, not held in memory, or
   every cold start `continue`s past the reconciliation entirely.
4. **Pagination.** The backfill loops until the venue returns fewer rows than the page size. Under G6 a result
   count at the documented cap is a hole, not an empty result; one unpaginated call silently truncates the gap.

## The user-data redelivery guard

Specialising G5 for user-data streams: the watermark keys on the **client order ID**, is persisted
independently of the order object, and **balance events get the same guard, not just orders**.

Write `if last_seen[client_id] >= event.update_time: return`, keyed on the ID, only where `update_time` is a
total order. The measured bug is `if existing is not None and event.update_time < existing.update_time: return`.
A terminal event has already popped `existing`, so the guard is skipped, a replayed pre-snapshot
`PARTIALLY_FILLED` re-inserts a phantom open order, and `reconcile` then sees it in `live` and declines to
re-place. The bot believes it is quoting; the book holds nothing; the hedge leg is naked and invisible.

**A late fill on a cancelled order is not a redelivery.** `(Canceled, Filled)` is a legal transition
(nautilus ships it annotated `// Real world possibility`) and the fill is real money. The cancel was a
*request*, not a fact. `(Canceled, Accepted)`, `(Filled, Accepted)`, `(Filled, Canceled)` and `(Rejected, *)`
are absent from the table and must hit the `_ => Err(InvalidStateTransition)` arm. A terminal state accepts
exactly the events by which the venue corrects a fact you already booked (a late fill, a fill void) and is
never re-opened by a *status* message. Deny by default; never silently ignore.

## Reconcile position and realized PnL against the venue

Specialising G7 with what to compare: a scheduled job queries the venue for position and balance and asserts
`Σ(signed local fills) == venue position size` **and** `local realized PnL == the venue's own realized figure`,
**per `(symbol, positionSide)`**. Where the venue publishes its realized-PnL figure on the execution event,
cross-check on **every event**, not only on the schedule. Receiving that field and never comparing it is the
measured 0-of-3 failure.

**Choose the cadence to exceed the venue's documented replication lag.** Binance labels each endpoint's data
source (Matching Engine / Memory / Database); the private stream is ME-sourced and many REST reads are not, so
a reconciliation running faster than the lag oscillates and gets muted. **Express the tolerance in the
instrument's own tick or lot, never a fixed epsilon.** On mismatch, record the venue's value, close the risk
gate for that instrument, and reopen only on a successful reconcile. The best platform in the corpus has the
startup reconciliation gate and **discards** the continuous one (`let _ =`). Computing the number and throwing
it away is the failure mode to avoid.

## Commissions and price targets

**Commission in a third asset.** Book the fee in the currency the venue reports (`commissionAsset`) and handle
a null or absent fee asset. A commission taken in an asset that is neither the quote nor the settlement asset
is converted at a **recorded rate** and included in `net_realized_pnl`, **or** the returned struct carries an
explicit `fees_unconverted: [assets]` field that every consumer must handle. On a BNB-discount Binance account,
excluding BNB commissions overstates headline profit by the entire fee bill. A base-asset commission reduces
the quantity you actually received: buy 36.38 GTC with a 0.1% GTC fee and you hold 36.34. Selling
`trade.amount` returns `-2010 insufficient balance`, and selling `free_balance` returns `-1013 Filter failure:
LOT_SIZE` because it is not a multiple of `stepSize`. Subtract the fee, then re-snap down in `Decimal`.

**Gross up before you quantize, and quantize in the safe direction.** Compute the exit from the actual executed
VWAP (`cummulativeQuoteQty / executedQty`), then
`exit = entry_vwap * (1 + target) * (1 + fee_in) * (1 + fee_out)` at the account's effective maker/taker rates,
**then** quantize, rounding **up for a sell target, down for a buy target**, never to nearest, which
surrenders up to half a tick of the fee markup half the time. A nominal +1% take-profit realizes roughly +0.8%
at 0.1% round-trip taker fees. The direction of this error is always the same: costs understated, profit
overstated.

## Positions the venue moves without you

**Key positions on `(symbol, positionSide)`, not on symbol alone**, and read the account's position mode at
startup. In hedge mode a LONG +5 and a SHORT −3 collapse into a fabricated flat −3 when `positionSide` is
dropped, and every downstream sizing, flatten and liquidation-distance decision is then computed from a
position that does not exist. Check reduce-only preconditions against the mode: reduce-only is unavailable in
Binance hedge mode and on Bybit spot, `closePosition` is incompatible with `quantity`, and oversized
reduce-only orders are split by Bybit or rejected by Binance with `-2022`.

**Funding, ADL, liquidation, settlement and delivery are required PnL components**, each deduped by the venue's
settlement/income id exactly as fills are deduped by `trade_id`. A bare `balance += funding` with no settlement
id double-counts on every redelivery and backfill. Ingest the venue's income/transaction-history endpoint, not
only the order stream. **Recognise venue-generated orders as yours**: filtering "orders that are mine" by
"orders whose client ID I generated" excludes exactly the events that change PnL without your consent.
NautilusTrader's adapters identify these by Binance's `autoclose-`, `adl_autoclose`, `settlement_autoclose-`
and `delivery_autoclose-` client-ID prefixes and by an empty `orderLinkId` on Bybit. Verify those markers
against your venue's current docs before keying on a prefix.

## An overfill is exposure, not an exception

On an overfill or any venue-vs-local quantity disagreement: **record the venue's reported quantity unclamped**,
add the excess to an `overfill_qty` **field** (a field, not a log line), alert, and close the risk gate for
that instrument. Position and PnL are recomputed as a fold over the fill set **including** `overfill_qty`. The
extra units are real exposure someone must hedge.

The gate blocks `submit_order` and any size-increasing amend **only**. `cancel_all(scope)`, `flatten(scope)`,
position, PnL and margin all keep working while it is closed, **and a test proves it**. Ariane 501: *"It was
the decision to cease the processor operation which finally proved fatal"*; Knight ¶42: disconnect the emitter,
keep managing the position. The gate reopens only on a successful reconciliation against the venue, never on a
timer and never from the code path that closed it. Never `abort`, never clamp, never drop the event: nautilus's
`allow_overfills` defaults to **false**, and with false the reconciliation path *discards the fill*
(`return None`), leaving your model short by exactly that amount with a `WARN` as the only trace.

## References

One hop each. Read on the predicate, apply in order, do not summarise.

| File | Read it when |
|---|---|
| [venues/binance.md](references/venues/binance.md) | the code imports `python-binance`, names `binance`, or hits `api.binance.com`/`fapi` |
| [venues/okx-bybit-kraken.md](references/venues/okx-bybit-kraken.md) | the code names `okx`, `bybit`, `kraken`, `clOrdId`, `orderLinkId`, `cl_ord_id`, `userref` |
| [venues/coinbase-deribit-hyperliquid.md](references/venues/coinbase-deribit-hyperliquid.md) | the code names `coinbase`, `advanced_trade`, `deribit`, `hyperliquid`, `cloid`, `set_heartbeat` |
| [venues/divergence-matrix.md](references/venues/divergence-matrix.md) | the repo constructs more than one venue adapter, or defines any venue-agnostic `Order`, `OrderBook`, `RateLimiter` or client-order-ID abstraction |
| [order-state-machine.md](references/order-state-machine.md) | the diff defines an order status enum, handles `ExecutionReport`/`OrdStatus`, or reads `leaves_qty`/`LeavesQty`/`CumQty` |
| [orderbook-sync.md](references/orderbook-sync.md) | the diff names `lastUpdateId`, `depthUpdate`, `pu`/`U`/`u`, `seqId`/`prevSeqId`, a book `checksum`, or applies a diff to a snapshot |
| [position-and-pnl.md](references/position-and-pnl.md) | the diff names `positionAmt`, `avgPrice`/`avg_px`, `unrealizedProfit`, `markPrice`, `fundingRate`, `contractSize`/`ctVal`, an inverse contract, or a stock split |
| [execution-algorithms.md](references/execution-algorithms.md) | the diff defines a parent/child order, a TWAP/VWAP/POV/IS schedule, `participation_rate`, `slice`, or a benchmark price |
| [ccxt-and-fix.md](references/ccxt-and-fix.md) | the diff imports `ccxt`/`ccxt.pro`, touches `precisionMode`/`amountToPrecision`, or speaks FIX (`35=D`, `PossDupFlag`, `OrigClOrdID`, `ResendRequest`) |
| [prediction-markets.md](references/prediction-markets.md) | the diff names `polymarket`, `kalshi`, `conditionId`, `negRisk`, `payoutNumerators`, or a complete set |
