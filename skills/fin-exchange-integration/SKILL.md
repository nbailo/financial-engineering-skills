---
name: fin-exchange-integration
description: >-
  Financial correctness for trading bots, market makers and execution systems trading through a
  venue you do not operate: ambiguous order submission, duplicate-order risk, venue constraints,
  fills, reconnect recovery, stale market data, fees, funding, position and PnL reconciliation.
  Use when building or reviewing a client of Binance, Bybit, OKX, Kraken, Hyperliquid, Alpaca,
  ccxt or FIX. A 50-line bot counts.
license: MIT
---

# Trading against a venue you do not operate

This is the primary use case of this suite: trading bots, market makers, arbitrage and execution systems that instruct
Binance, Bybit, OKX, Kraken, Hyperliquid, Alpaca, ccxt or FIX to act. The venue, not you, holds the authoritative copy
of your orders, fills, position and PnL, and it answers if you ask. Every rule here follows from one question: if this
response never arrives, what does the venue now believe, and how do you find out without sending a second instruction?

## When to use

Your process instructs a system you do not control to take an economic action on your behalf, and that system,
not you, decides what happened. That covers sending, amending and cancelling orders, and equally covers
deriving fills, average price, commission, position, margin, funding, PnL or a book from that venue's payloads,
because those derivations are where a lost message becomes a wrong number. A throwaway script counts, and so
does a paper-trading branch: the same code gets live keys later.

Routing literals, evidence rather than definition: imports of `ccxt`, `python-binance`, `hyperliquid`, `deribit`,
`alpaca` or `ib_insync`; FIX, OUCH or SBE spoken as a client; `create_order`, `cancelOrder`, `cancel_replace`;
`newClientOrderId`, `clOrdId`, `cloid`, `orderLinkId`; `exchangeInfo`, `tickSize`, `stepSize`, `listenKey`, a
user-data websocket. An in-house `Broker` class with none of those spellings is still a venue client.

## When not to

- You own the book and match incoming against resting orders, compute allocation or priority, publish a
  sequenced feed, or run a clearing or liquidation batch. That is the venue side, where your authority is
  SELF rather than EXTERNAL. Its skill, `fin-matching-and-settlement`, is not in this installed set; it
  lives in the repository's advanced area. A resolution **you assign** belongs to it, while trading a
  prediction market somebody else resolves belongs here.
- Fills become double-entry postings: `fin-ledger`.
- The change is only amount arithmetic, rounding direction, or the retry classification of an outbound call:
  `fin-money-core`.
- The code reads historical payloads and never sends an instruction. A backtest, a greeks or implied-vol
  calculation, a Monte Carlo run: analytics, not a money path.

## Workflow

1. **Name the instruction and what accepting it obligates you to.** An order commits you to a position the
   moment the venue accepts it; a cancel commits you to nothing. Neither is a fact until the venue says so.
2. **Mint an identity from the intent instance and commit it durably before the send.** Committed means
   readable by another process after a crash, not `flush()` inside an open transaction.
3. **Enumerate the venue's possible answers and classify each one.** Anything that is not a clean acceptance
   carrying your identity is UNKNOWN, including a rejection that names no business rule.
4. **Resolve UNKNOWN by asking the venue about the identity you sent,** never by sending again, and declare in
   advance what happens when the question stays unanswered.
5. **Put every refusal inside the function that sends:** the venue's published constraints and your own risk
   limits, evaluated there, before the write, from live position rather than a cached metric.
6. **Decide how local state is rebuilt after a disconnect, a restart or a stream gap,** what cancels your
   resting orders while you are gone, and what stays blocked until the rebuild finishes.
7. **Reconcile position, fills, fees and funding against the venue on a schedule,** and define what a mismatch
   does besides writing a log line.
8. **Load only the venue reference this code touches, then prove the five properties below** in the
   repository's own test framework, before the code runs against live keys.

## Invariants

Each one specialises a money-core invariant, named in italics, and adds only what is different here.

### A client order ID correlates; it deduplicates only where the venue documents that

Treat the identity you attach to an instruction as a **correlation key by default**: it ties a response to an
intent and nothing more. It is a deduplication key only where the venue documents that resending it returns
the original instruction instead of creating a second one, and that citation belongs beside the code. Binance
spot and futures, OKX and Kraken enforce uniqueness only among *open* orders, so the reuse window is zero
seconds after a fill or a cancel, exactly the marketable-limit case where a lost response is most likely.
Specialises *operation identity*: one identity per logical instruction, reused by every retry of it, never one
per attempt, and never derived from a bar timestamp, `strategy+symbol+side` or a wall-clock second.

### A response that does not prove the outcome is UNKNOWN, and the answer is a query

Specialises *ambiguous outcomes*. Use the venue's documented semantics for that endpoint, and infer nothing
beyond what the response proves. A timeout, a socket error once transmission may have begun, any 5xx, a 429,
and any rejection the venue does not document as "not enqueued" for that exact code are UNKNOWN. Resolve by
querying the identity you sent, escalating propagation retry, open orders, order history, then fills, stopping
at the first definite rung. **A retry is permitted only where the production code path itself establishes that
the instruction could not have become externally visible,** meaning a failure before transmission began: DNS
resolution failure, connection refused, TLS handshake failure, a local validation or serialization rejection.
Everything else is resolved by asking, never by resending.

### An unresolved instruction is a position, and it carries a clock

Hold it at **full notional** in the risk calculation, close the risk gate for that instrument, and give the
state a wall-clock budget declared as a config value. Past the budget the system takes the risk-*reducing*
action automatically, cancel by identity then flatten the instrument, rather than waiting for a human. Hold
and escalate with no clock leaves the desk dead until somebody wakes up, which is how the rule gets disabled.

### Every refusal that protects you runs inside the function that sends

Specialises *hard limits*. Two independent bodies can refuse an instruction: the venue, through the constraints its
validator publishes, and you, through your risk limits. Both are evaluated inside the function that sends, before the
write, against live position and a reference price for **this** instrument. A check in a sibling module, or a monitor
reading a metric, is a detector rather than a control, because some send path will not go through it. Re-check the
whole constraint set together after rounding, because satisfying one can break another; rounding toward validity never
increases size, and a quantity rounded to zero is an explicit skip rather than a silent no-op.

### Your absence is the venue's job to notice, and your return does not restore the range

A process that dies cannot cancel anything, so the only switch that fires *because you went away* is the one the venue
holds. Coming back is the harder half: re-subscribing restores the connection, not the range. Nothing may act on local
state until the rebuild completes, the missed range is computed and materialised rather than skipped, the cursor
outlives the process, and a page at the documented cap is a hole rather than the end.

### Data has an age, and age is not arrival order

Freshness and ordering fail in opposite situations: a perfectly ordered feed that stopped ten seconds ago passes the
ordering guard and fails the freshness guard, and a reviewer who searches for one finds it and concludes the other
exists. Store the **venue's own event timestamp** on every book, mark, index, quote and funding rate, evaluate `now −
ts > max_age` on the order-submission path, and keep the `ts < last_seen` ordering drop as a separate guard.

### Fill-derived state is a fold, never an accumulator

Specialises *durable dedupe*. Read cumulative totals from the venue (`executedQty`, `cumQty`) rather than
summing deltas you observed. Dedupe on the venue's own event identity **before** the state transition, reject
the duplicate rather than ignoring it, and write the dedupe record in the same transaction as the state it
protects, because an in-memory dedupe set re-applies every counted fill after a restart. Position, average
price and realized PnL are recomputed as an order-independent fold over the persisted fill set, so ascending
and descending arrival produce identical output.

### A pushed lifecycle event is a claim about a state the venue owns

Specialises *authority*. Guard legality against an explicit `(state, event)` table with a raising default arm,
never a silent ignore. Guard version with a watermark keyed on the **instruction identity**, stored apart from
the live object, where the guarded write is itself the guard and runs in the transaction with the effect. Then
re-read amount, status and attribution from the venue before any value-moving decision. A terminal state
accepts exactly the events by which the venue corrects a fact you already booked, a late fill or a fill void,
and is never re-opened by a *status* message.

### The venue moves your position without an instruction from you

Funding, ADL, liquidation, settlement, delivery and corporate actions are required PnL components, each
deduped by the venue's own settlement or income id exactly as fills are deduped by trade id. Ingest the full
account-activity feed, not only the responses to your own instructions: filtering "activity that is mine" by
"activity whose identity I generated" excludes exactly the events that change PnL without your consent. Key
position on every dimension the venue itself separates, never on instrument alone.

### A quantity larger than you asked for is exposure, not an exception

Record the venue's number **unclamped**, carry the excess in a field that position and PnL fold over, alert,
and close the risk gate for that instrument. The gate blocks new sends and size-increasing amends only:
cancel, flatten, position, PnL and margin stay callable while it is closed, and a test proves that they do. It
reopens only on a successful comparison against the venue, never on a timer and never from the code path that
closed it. Never abort, never clamp, never drop the event.

### The venue's number is the record; yours is a hypothesis until it is compared

Specialises *reconciliation*. Because an external party can confirm your state, comparison against its answer
is the safety net, not defensive coding. Name the venue and the join key for every economic quantity you
report, ship the comparison as a scheduled entrypoint that runs in production through a path independent of
the writer, compare per position key, choose a cadence exceeding the venue's documented replication lag, and
express the tolerance in the instrument's own tick or lot. A break has a fail-closed delivery path and closes
the gate; computing the number and discarding it is the common failure.

**Prove these five properties in the repository's own language and framework.** A description, a TODO or a
pointer to a test plan is the missing control, not a plan for it.

1. An instruction whose response is lost creates no duplicate economic effect: the client queries by the
   identity it sent, does not resend, and exactly one instruction exists at the end.
2. A provable pre-send failure, and only that, follows the documented retry path.
3. A normalised order satisfies every relevant venue constraint simultaneously, for each order type, and is
   never larger than what was asked for.
4. Reconnect and backfill neither lose nor double-count a fill.
5. Local position and PnL converge to the venue's authoritative state.

## References

| File | Read it when (one hop; apply it in order, do not summarise) |
|---|---|
| [venues/binance.md](references/venues/binance.md) | the code imports `python-binance`, names `binance`, or hits `api.binance.com`/`fapi` |
| [venues/okx-bybit-kraken.md](references/venues/okx-bybit-kraken.md) | the code names `okx`, `bybit`, `kraken`, `clOrdId`, `orderLinkId`, `cl_ord_id`, `userref` |
| [venues/coinbase-deribit-hyperliquid.md](references/venues/coinbase-deribit-hyperliquid.md) | the code names `coinbase`, `advanced_trade`, `deribit`, `hyperliquid`, `cloid`, `set_heartbeat` |
| [venues/divergence-matrix.md](references/venues/divergence-matrix.md) | the repo builds more than one venue adapter, or defines a venue-agnostic `Order`, `OrderBook`, `RateLimiter` or client-order-ID abstraction |
| [pre-trade-controls.md](references/pre-trade-controls.md) | the diff normalises price or quantity against venue filters, or adds a notional, position, rate or price-band limit |
| [order-state-machine.md](references/order-state-machine.md) | the diff defines an order status enum, handles `ExecutionReport`/`OrdStatus`, or reads `leaves_qty`/`LeavesQty`/`CumQty` |
| [reconnect-and-backfill.md](references/reconnect-and-backfill.md) | the diff reconnects a stream, defines a resync or ready gate, persists a fills cursor, or arms cancel-on-disconnect |
| [orderbook-sync.md](references/orderbook-sync.md) | the diff names `lastUpdateId`, `depthUpdate`, `pu`/`U`/`u`, `seqId`/`prevSeqId`, a book `checksum`, or applies a diff to a snapshot |
| [position-and-pnl.md](references/position-and-pnl.md) | the diff names `positionAmt`, `avgPrice`/`avg_px`, `unrealizedProfit`, `markPrice`, `fundingRate`, `contractSize`/`ctVal`, an inverse contract, or a stock split |
| [execution-algorithms.md](references/execution-algorithms.md) | the diff defines a parent/child order, a TWAP/VWAP/POV/IS schedule, `participation_rate`, `slice`, or a benchmark price |
| [ccxt-and-fix.md](references/ccxt-and-fix.md) | the diff imports `ccxt`/`ccxt.pro`, touches `precisionMode`/`amountToPrecision`, or speaks FIX (`35=D`, `PossDupFlag`, `OrigClOrdID`, `ResendRequest`) |
| [prediction-markets.md](references/prediction-markets.md) | the diff names `polymarket`, `kalshi`, `conditionId`, `negRisk`, `payoutNumerators`, or a complete set |
| [test-properties.md](references/test-properties.md) | you are writing or reviewing the tests for any of the five properties above |
| [seams.md](references/seams.md) | the same process posts fills into a ledger, or is both a venue for its own clients and a client of another venue |

## Output

On any economic change, one line: `authority: EXTERNAL (<venue>) · exposure: own`. That pair is the usual one here,
because the venue can always tell you that you are wrong. Exposure rises to `customer` when the bot trades money that
is not yours, and to `record` where this process is also the system of record for somebody else's orders.

Then one entry per real finding:

```
FINDING   <the wrong economic outcome, concretely>
WHY       <the mechanism that produces it>
EVIDENCE  <file:line>
FIX       <the change that closes it>
TEST      <the property to assert>
```

Add `VERDICT   SHIP | NO-SHIP: <the unresolved control>` only when the task is a review or a ship decision. No
findings means one or two sentences saying so and why the change is safe. Never emit a slot for a concept the
change does not touch. A claimed control points at executable code, and where the risk requires it a test;
comments, TODOs, unused helpers and design prose are not evidence, and an absent control is reported as
`UNRESOLVED: <control> (<why>)` rather than a completed row. Emit the fuller venue contract, whose slots and
dual-role rules live in [seams.md](references/seams.md), only when exposure is `customer` or `record`, or the
change adds a second venue adapter.
