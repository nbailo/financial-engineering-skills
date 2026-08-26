---
name: fin-exchange-integration
description: >-
  Trading through a venue you do not operate: ambiguous or duplicate orders, venue constraints,
  fills, reconnect recovery, stale market data, fees, funding, position, PnL; also
  prediction-market settlement and payout credit. Use for a client of Binance, OKX, Bybit, Kraken,
  Hyperliquid, ccxt, FIX, Polymarket, Kalshi or Limitless.
license: MIT
---

# Trading against a venue you do not operate

This is the primary use case of this suite: trading bots, market makers, arbitrage and execution systems that instruct a spot,
derivatives or prediction-market venue to act, over its own API, ccxt or FIX. The venue, not you, holds the authoritative
copy of your orders, fills, position and PnL, and it answers if you ask. Every rule here follows from one question: if this
response never arrives, what does the venue now believe, and how do you find out without sending a second instruction?

## When to use

Your process instructs a system you do not control to take an economic action on your behalf, and that system, not you, decides
what happened. That covers sending, amending and cancelling orders, and equally covers deriving fills, average price,
commission, position, margin, funding, PnL or a book from that venue's payloads, where a lost message becomes a wrong number. A
throwaway script counts; so does a paper-trading branch, since the same code gets live keys.

Routing literals, evidence rather than definition: imports of `ccxt`, `python-binance`, `hyperliquid`, `deribit`, `alpaca` or
`ib_insync`; FIX, OUCH or SBE spoken as a client; `create_order`, `cancelOrder`, `cancel_replace`; `newClientOrderId`,
`clOrdId`, `cloid`, `orderLinkId`; `exchangeInfo`, `tickSize`, `stepSize`, `listenKey`, a user-data websocket. An in-house
`Broker` class with none of those spellings is still a venue client.

## When not to

- You own the book and match incoming against resting orders, compute allocation or priority, or publish a sequenced feed.
  That is the venue side, where your authority is SELF rather than EXTERNAL, and it is opt-in material outside this installed
  set. Clearing, liquidation waterfalls and deciding what an instrument is worth at expiry have no skill in this suite at all:
  say that plainly rather than routing to one. Trading an instrument somebody else settles belongs here.
- Fills become double-entry postings: `fin-ledger`.
- The change is only amount arithmetic, rounding direction, or the retry classification of an outbound call: `fin-money-core`,
  which otherwise loads alongside this skill only for a cross-domain mechanism this one does not already specialise.
  `fin-verification` loads when tests, proof or reconciliation actually change, when the ask is review or readiness, or where a
  rule below demands stronger proof, never because exposure is `customer`.
- The code reads historical payloads and never sends an instruction: a backtest, a greeks or implied-vol run, a Monte Carlo.
  Analytics, not a money path.

## Workflow

1. **Name the instruction and what accepting it obligates you to.** An order commits you to a position the moment the venue
   accepts it; a cancel commits you to nothing. Neither is a fact until the venue says so.
2. **Mint an identity from the intent instance and commit it durably before the send.** Committed means readable by another
   process after a crash, not `flush()` inside an open transaction.
3. **Enumerate the venue's answers and classify each.** Anything but a clean acceptance carrying your identity is UNKNOWN.
4. **Resolve UNKNOWN by asking the venue about the identity you sent,** never by sending again.
5. **Put every refusal inside the function that sends,** before the write, from live position, not a cached metric.
6. **Decide how local state is rebuilt after a disconnect, a restart or a stream gap,** what cancels your resting orders while
   you are gone, and what stays blocked until the rebuild finishes.
7. **Reconcile position, fills, fees and funding on a schedule,** and define what a mismatch does besides a log line.
8. **Load a reference below before assessing the mechanism its trigger names,** then prove the five properties.

## Invariants

### A client order ID correlates; it deduplicates only where the venue documents that

The identity you attach to an instruction is a **correlation key by default**: it ties a response to an intent and nothing more.
It deduplicates only where the venue documents that resending it returns the original instruction rather than creating a second,
with that citation beside the code. Several venues scope uniqueness to *open* orders, so the reuse window can be zero after a
fill or a cancel, exactly when a lost response is most likely. Specialises *operation identity*, the money-core rule each
invariant here names in italics: one identity per logical instruction, reused by every retry, never one per attempt, never
derived from a bar timestamp, `strategy+symbol+side` or a wall-clock second.

### A response that does not prove the outcome is UNKNOWN, and the answer is a query

Specialises *ambiguous outcomes*. Use the venue's documented semantics for that endpoint, and infer nothing beyond what the
response proves. A timeout, a socket error once transmission may have begun, any 5xx, a 429, and any rejection the venue does
not document as "not enqueued" for that exact code are UNKNOWN. Resolve by querying the identity you sent, escalating
propagation retry, open orders, order history, then fills, stopping at the first **definite** rung. Two rungs are routinely
misread. A rejection saying your identity is already in use is evidence the first instruction is open, so branch on the venue's
message rather than its numeric code. A "no such order" moments after placement is not proof of non-creation, because the read
may have hit a replica the write has not reached; re-query across the documented propagation window first. **A retry is
permitted only where the production code path itself establishes that the instruction could not have become externally
visible,** meaning a failure before transmission began: DNS resolution failure, connection refused, TLS handshake failure, a
local validation or serialization rejection. Everything else is resolved by asking, never by resending.

### An unresolved instruction is a position, and it carries a clock

While the outcome is unknown, reserve the worst case: hold the **venue-and-product-defined worst-case exposure** for that
instrument as though the order filled, close the risk gate for it, and give the state a wall-clock budget declared as config.
For a spot buy that equals the notional; for a long option it is the premium paid, for a short option it is unbounded or set by
the venue's margin model, and under leverage or a nonlinear payoff it is neither. What that budget expires into is ordered by
whether the action reduces risk in **every** state still possible: filled, partly filled, or never created.

- **Always safe:** stop sending, and cancel by the identity you sent where the venue cancels by client identity. Cancelling an
  order that never existed is a no-op; cancelling one that rests removes exposure you did not intend.
- **Safe once the venue answers:** query that identity, reconcile against its position number, act on the difference.
- **Conditional:** a hedge or flatten, only where the venue has confirmed a position, or exposure is bounded so the hedge cannot
  invert the sign.
- **Never:** an action whose correctness assumes the instruction filled, or assumes it did not. Flattening an instrument whose
  instruction never filled opens the opposite position.

Hold and escalate with no clock leaves the desk dead until somebody wakes up, which is how the rule gets disabled. A clock that
expires into an action able to invert the position is worse than the wait.

### Every refusal that protects you runs inside the function that sends

Specialises *hard limits*. Two independent bodies can refuse an instruction: the venue, through the constraints its validator
publishes, and you, through your risk limits. Both are evaluated inside the function that sends, before the write, against live
position and a reference price for **this** instrument; a check in a sibling module, or a monitor reading a metric, is a
detector, because some send path will not go through it. Re-check the whole constraint set together after rounding, because
satisfying one can break another, and a quantity rounded to zero is an explicit skip.

### Your absence is the venue's job to notice, and your return does not restore the range

A process that dies cannot cancel anything, so the only switch that fires *because you went away* is the one the venue holds.
Coming back is the harder half: re-subscribing restores the connection, not the range. Nothing may act on local state until the
rebuild completes, the missed range is materialised rather than skipped, the cursor outlives the process, and a page at the
documented cap is a hole rather than the end.

### Data has an age, and age is not arrival order

Freshness and ordering fail in opposite situations: a perfectly ordered feed that stopped ten seconds ago passes the ordering
guard and fails the freshness guard. Store the **venue's own event timestamp** on every book, mark, index, quote and funding
rate, evaluate `now − ts > max_age` on the send path, and keep `ts < last_seen` as a separate guard.

### Arrival order must not change the result; economic order must

Specialises *durable dedupe*. Persist every fill before it changes anything, dedupe on the venue's own event identity **before**
the state transition, reject the duplicate rather than ignoring it, and write the dedupe record in the same transaction as the
state it protects, because an in-memory dedupe set re-applies every counted fill after a restart. Establish the canonical
economic order from the venue's own sequencing, trade identity, execution sequence number, transaction time, in the precedence
it documents, then fold the persisted fills in that order; where the venue's own data cannot establish that order, reject
explicitly rather than guessing a sequence. The property is **convergence, not commutativity**: a stream replayed shuffled,
duplicated and interrupted by a restart reaches the same state as that stream in arrival order, because both are sorted into the
canonical order before folding. Realized PnL under FIFO, LIFO or average cost is a function of the economic sequence, so the same
fills in a different economic order are a different number. Read the cumulative totals the venue publishes (`executedQty`,
`cumQty`) rather than summing observed deltas, and reconcile the two where the venue publishes both.

### A pushed lifecycle event is a claim about a state the venue owns

Specialises *authority*. Guard legality against an explicit `(state, event)` table with a raising default arm, never a silent
ignore. Guard version with a watermark keyed on the **instruction identity**, stored apart from the live object, where the
guarded write is itself the guard and runs in the transaction with the effect. Then re-read amount, status and attribution from
the venue before any value-moving decision. A terminal state accepts exactly the events by which the venue corrects a fact you
already booked, a late fill or a fill void, never a *status* message re-opening it.

### The venue moves your position without an instruction from you

Funding, ADL, liquidation, settlement, delivery and corporate actions are required PnL components, each deduped by the venue's
own settlement or income id exactly as fills are deduped by trade id. Ingest the full account-activity feed: filtering "activity
that is mine" by "activity whose identity I generated" excludes exactly the events that change PnL without your consent. Key
position on every dimension the venue itself separates, never on instrument alone.

### A quantity larger than you asked for is exposure, not an exception

Record the venue's number **unclamped**, carry the excess in a field that position and PnL fold over, alert, and close the risk
gate for that instrument. The gate blocks new sends and size-increasing amends only, keeps cancel, flatten, position, PnL and
margin callable with a test that proves it, and reopens only on a successful comparison against the venue, never on a timer and
never from the code path that closed it. Never abort, never clamp, never drop the event.

### The venue's number is the record; yours is a hypothesis until it is compared

Specialises *reconciliation*. Because an external party can confirm your state, comparison against its answer is the safety net,
not defensive coding. Name the venue and the join key for every economic quantity you report, ship the comparison as a scheduled
entrypoint that runs in production through a path independent of the writer, compare per position key, choose a cadence
exceeding the venue's documented replication lag, and express tolerance in the instrument's own tick or lot. A break closes the
gate and has a fail-closed delivery path; computing the number and discarding it is the common failure.

**Prove these five properties in the repository's own language and framework, before live keys.** A TODO, a description or a
pointer to a test plan is the missing control, not a plan for it.

1. A lost response creates no duplicate economic effect: the client queries the identity it sent, and one order exists.
2. A provable pre-send failure, and only that, follows the documented retry path.
3. A normalised order satisfies every relevant venue constraint at once, per order type, and is never larger than asked.
4. Reconnect and backfill neither lose nor double-count a fill.
5. Local position and PnL converge to the venue's authoritative state.

## References

Coverage is uneven by venue. A venue named below has a dedicated reference carrying its field names, error
codes and assertions; the prediction-market files add a provenance block with a verified_at date and an
explicit unverified list, and the others do not yet. A venue not named is covered by the invariants above plus
ccxt or FIX, and nothing venue-specific about it may be asserted. The repository README carries the
provider-support matrix stating the level for each.

- [venues/binance-orders.md](references/venues/binance-orders.md): the diff sends, cancels or recovers a Binance order: `newClientOrderId`, `-1006`/`-1007`/`-2010`/`-2013`, `recvWindow`, `countdownCancelAll`, order-count limits
- [venues/binance-filters.md](references/venues/binance-filters.md): the diff rounds a Binance price or size: `exchangeInfo`, `tickSize`, `stepSize`, `LOT_SIZE`, `MARKET_LOT_SIZE`, `NOTIONAL`, `pricePrecision`, COIN-M contracts
- [venues/binance-streams.md](references/venues/binance-streams.md): the diff reads a Binance private stream or a fee: `listenKey`, `executionReport`, `ORDER_TRADE_UPDATE`, `z`/`l`/`t`, `commissionAsset`, the BNB discount
- [venues/binance-orderbook.md](references/venues/binance-orderbook.md): the diff joins a Binance depth stream to a snapshot: `depthUpdate`, `lastUpdateId`, `U`/`u`/`pu`
- [venues/okx-bybit-kraken-orders.md](references/venues/okx-bybit-kraken-orders.md): the diff names `okx`, `bybit`, `kraken`, `clOrdId`, `orderLinkId`, `cl_ord_id`, `userref`, `tgtCcy`, or one of those venues' recovery endpoints or rate limits
- [venues/okx-bybit-kraken-orderbook.md](references/venues/okx-bybit-kraken-orderbook.md): the diff names `seqId`/`prevSeqId`, a Bybit `u`/`seq` depth frame, or a Kraken CRC32 book `checksum`
- [venues/coinbase-orders.md](references/venues/coinbase-orders.md): the code names `coinbase`, `advanced_trade`, a replayed create returning the original order, or `DUPLICATE_CLIENT_ORDER_ID`
- [venues/deribit-orders.md](references/venues/deribit-orders.md): the code names `deribit`, a `label` order tag, `set_heartbeat`, or `enable_cancel_on_disconnect`
- [venues/hyperliquid-orders.md](references/venues/hyperliquid-orders.md): the code names `hyperliquid`, `cloid`, a `(signer, nonce)` replay window, `orderStatus`, or `iocCancelRejected`
- [venues/divergence-matrix.md](references/venues/divergence-matrix.md): the repo builds more than one venue adapter, or defines a venue-agnostic `Order`, `OrderBook`, `RateLimiter` or client-order-ID abstraction
- [pre-trade-controls.md](references/pre-trade-controls.md): the diff normalises price or quantity against venue filters, or adds a notional, position, rate or price-band limit
- [order-state-machine.md](references/order-state-machine.md): the diff defines an order status enum, handles `ExecutionReport`/`OrdStatus`, or reads `leaves_qty`/`LeavesQty`/`CumQty`
- [reconnect-and-backfill.md](references/reconnect-and-backfill.md): the diff reconnects a stream, defines a resync or ready gate, persists a fills cursor, or arms cancel-on-disconnect
- [orderbook-sync.md](references/orderbook-sync.md): the diff applies a depth delta, names `lastUpdateId`, `pu`/`U`/`u`, `seqId`/`prevSeqId`, `change_id` or a book `checksum`, or discards and re-snapshots a book on a sequence gap
- [prices-and-staleness.md](references/prices-and-staleness.md): the diff reads `best_bid`/`best_ask`, a mid, a microprice or a last trade price off a book, walks depth to size an order, or adds a `max_age` staleness gate
- [position-and-pnl.md](references/position-and-pnl.md): the diff names `positionAmt`, `avgPrice`/`avg_px`, `unrealizedProfit`, `markPrice`, `contractSize`/`ctVal`, an inverse contract, margin, or a stock split
- [fees-and-funding.md](references/fees-and-funding.md): the diff names `commission`/`commissionAsset`, a fee rate, `fundingRate`, an income or settlement id, ADL, liquidation, settlement or delivery
- [execution-algorithms.md](references/execution-algorithms.md): the diff defines a parent/child order, a TWAP/VWAP/POV/IS schedule, `participation_rate`, `slice`, or a benchmark price
- [ccxt.md](references/ccxt.md): the diff imports `ccxt`/`ccxt.pro`, or touches `precisionMode`/`amountToPrecision`/`createMarketBuyOrderRequiresPrice`
- [fix.md](references/fix.md): the diff speaks FIX: `35=D`, `PossDupFlag`, `PossResend`, `OrigClOrdID`, `ResendRequest`, `ExecRefID`, iLink 3 / FIXP
- [prediction-market-core.md](references/prediction-market-core.md): the diff quantizes a prediction-market price or charges a fee: `orderPriceMinTickSize`, `price_ranges`, a `[tick, 1 - tick]` bound, `p × (1 - p)`, or `bps × notional`
- [prediction-market-outcome-identity.md](references/prediction-market-outcome-identity.md): the diff maps an outcome label to an identifier or assumes a binary payout: `clobTokenIds`, `winningOutcomeIndex`, `payoutNumerators`, `notional_value_dollars`, `sideSpecs`
- [prediction-market-complement-and-books.md](references/prediction-market-complement-and-books.md): the diff sells from flat, reserves collateral or checks two legs: `position_fp`, a `1 - p` complement, `splitPosition`, `bestBid(YES) + bestBid(NO)`
- [prediction-market-order-identity.md](references/prediction-market-order-identity.md): the diff recovers a prediction-market submission or reconciles its quantities: a signed `timestamp` that replaced `nonce`, `client_order_id` with no documented semantics, `open_interest_fp`, `realized_pnl_dollars`
- [prediction-market-settlement-integration.md](references/prediction-market-settlement-integration.md): the diff handles a prediction market closing, resolving or paying out: `determined`/`amended`/`finalized`, a `MATCHED`/`MINED` settlement frame, `payoutNumerators`, a settlement or redeem credit, or `redeemPositions`/`/portfolio/redeem`
- [prediction-market-polymarket-v2.md](references/prediction-market-polymarket-v2.md): the diff signs, posts or books a Polymarket CLOB V2 order: `py-clob-client-v2`, `@polymarket/clob-client-v2`, `builderCode`/`builder_code`, pUSD, `getClobMarketInfo`, `feeSchedule`, `orderPriceMinTickSize`, a `0.005`/`0.0025` tick, or a signed order still carrying `feeRateBps`, `nonce` or `taker`
- [prediction-market-kalshi.md](references/prediction-market-kalshi.md): the diff trades, books or settles a Kalshi event contract: `outcome_side`/`book_side`, `orderbook_fp`, `use_yes_price`, `price_ranges`, a `*_dollars` or `*_fp` fixed-point field, `client_order_id`, an amend `count`, `open_interest_fp`, `exchange_index`, or `determined`/`amended`/`finalized`
- [prediction-market-limitless.md](references/prediction-market-limitless.md): the diff names `limitless`, `api.limitless.exchange`, an `lmts-api-key` HMAC header, `venue.exchange`, `tradeType`, `clientOrderId`, `/orders/status/batch`, or a `409 Conflict` on a reused id
- [prediction-market-limitless-signing.md](references/prediction-market-limitless-signing.md): the diff builds the Limitless signed struct: a `"Limitless CTF Exchange"` domain, `salt`, `signatureType`, `rank.feeRateBps`, or an FOK `takerAmount = 1`
- [prediction-market-limitless-cancel.md](references/prediction-market-limitless-cancel.md): the diff withdraws a Limitless quote: `/orders/cancel-batch`, `/orders/cancel-replace`, a `207 Multi-Status`, or `GET /maintenance/status`
- [prediction-market-limitless-events.md](references/prediction-market-limitless-events.md): the diff consumes the Limitless stream: `subscribe_order_events`, an `orderEvent` frame, `OME` versus `SETTLEMENT`, `isEstimate`, `occurredAt`
- [prediction-market-limitless-book-and-fees.md](references/prediction-market-limitless-book-and-fees.md): the diff reads the Limitless book or credits its payout: `adjustedMidpoint`, an already-merged YES-side book, `effectiveFeeBps`, or `POST /portfolio/redeem`
- [prediction-market-hyperliquid-hip4.md](references/prediction-market-hyperliquid-hip4.md): the diff touches a Hyperliquid outcome market's book or balance operations: `splitOutcome`/`mergeOutcome`/`mergeQuestion`/`negateOutcome`, a `userOutcome` action, a merged `#<encoding>` book, or a deployer fee scale
- [prediction-market-hyperliquid-hip4-outcomes.md](references/prediction-market-hyperliquid-hip4-outcomes.md): the diff reads a Hyperliquid outcome definition or its settlement: `outcomeMeta`, `settledOutcome`, `outcomeMetaUpdates`, `sideSpecs`, `settleFraction`, a pipe-delimited `description`, or an asset id above `100_000_000`
- [test-properties.md](references/test-properties.md): you are writing or reviewing the tests for any of the five properties above
- [seams.md](references/seams.md): the same process posts fills into a ledger, or is both a venue for its own clients and a client of another venue

## Output

On any economic change, one line: `authority: EXTERNAL (<venue>) · exposure: own`, the usual pair here because the venue can
always tell you that you are wrong. Exposure rises to `customer` when the bot trades money that is not yours, and to `record`
where this process is also the system of record for somebody else's orders. Authority is per quantity: where one covers
everything in scope emit that single line, and where it does not, emit `authority: MIXED · exposure: <e>` and one indented
line per quantity that differs, two or three at most, such as `fills, position, PnL   EXTERNAL (Binance)` above
`unresolved intent rows   SELF`.

Then one entry per real finding, which may carry its own authority where that is what makes it a finding:

```
FINDING   <the wrong economic outcome, concretely>
WHY       <the mechanism that produces it>
EVIDENCE  <file:line>
FIX       <the change that closes it>
TEST      <the property to assert>
```

Add `VERDICT   SHIP | NO-SHIP: <the unresolved control>` only when the task is a review or a ship decision. No findings means
one or two sentences saying so and why the change is safe. An absent control is `UNRESOLVED: <control> (<why>)`, never a
completed row. Emit the fuller venue contract from [seams.md](references/seams.md) only when exposure is `customer` or
`record`, or the change adds a second venue adapter.
