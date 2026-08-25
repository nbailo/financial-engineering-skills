---
name: fin-exchange-integration
description: >-
  Financial correctness for trading on a venue you do not operate: order identity, ambiguous
  submissions, venue constraints, partial fills, stale market data, reconnect recovery, and
  position, fee and PnL reconciliation. Use when building or reviewing a client of an exchange,
  including ccxt, Binance, Bybit, OKX, Kraken, Deribit, Alpaca and FIX. A 50-line bot counts. If
  the code is the venue, use fin-matching-and-settlement.
license: MIT
---

# Trading against a venue you do not operate

Your code is a client: the venue holds the authoritative copy of your orders, fills, position and PnL, and it answers if you ask.
Every rule here follows from one question: if this response never arrives, what does the venue now believe, and how do you find
out without sending a second instruction? If your code *is* the venue (matching, allocation, priority, feed publication, clearing)
load `fin-matching-and-settlement` instead; if a fill becomes a journal entry, that half is `fin-ledger`.

## Workflow

1. **Name the instruction and what accepting it obligates you to.** An order commits you to a position the moment the venue
   accepts it; a cancel commits you to nothing. Neither is a fact until the venue says so.
2. **Give the instruction an identity that survives a lost response, and commit it durably before sending.** Committed means
   readable by another process after a crash, not written inside an open transaction.
3. **Enumerate the venue's possible answers and classify them.** Anything that is not a clean acceptance carrying your identity is
   UNKNOWN, including a rejection that names no business rule.
4. **Resolve UNKNOWN by asking the venue about the identity you sent,** never by sending again, and decide in advance what happens
   when the question cannot be answered.
5. **Put every refusal inside the function that sends:** the venue's own published constraints and your risk limits, evaluated
   there, before the write, from live position rather than a cached metric.
6. **Decide how local state is rebuilt after a disconnect, a restart or a stream gap,** what cancels your resting orders while you
   are gone, and what stays blocked until the rebuild finishes.
7. **Reconcile position, fills and fees against the venue's answer on a schedule,** and define what the mismatch does besides producing a log line.
8. **Load only the venue reference this implementation needs, then write the two tests as code:** an ambiguous submission that
   does not duplicate the effect, and an instruction that satisfies every constraint at once.

## When this applies

Your process instructs a system you do not control to take an economic action on your behalf, and that system, not you, decides
what happened. That covers sending, amending and cancelling orders, and equally covers deriving fills, average price, commission,
position, margin, funding, PnL or a book from that venue's payloads, because those derivations are where a lost message becomes a
wrong number. A throwaway script counts, and so does a paper-trading branch: the same code gets live keys later.

As routing hints only, a diff is usually in scope when it imports `ccxt`, `python-binance`, `hyperliquid`, `deribit`, `alpaca` or `ib_insync`, speaks
FIX/OUCH/SBE as a client, calls `create_order`/`cancelOrder`/`cancel_replace`, names `newClientOrderId`/`clOrdId`/`cloid`, `exchangeInfo`,
`tickSize`/`stepSize`, `listenKey` or a user-data websocket. Those literals are evidence, not the definition: an in-house `Broker` class with none of
those spellings is still a venue client. The reverse holds too, and *the economic-diff gate* is biased to exempt: a backtest that reads historical
payloads and never sends an instruction is analytics, not a money path.

**Not this skill.** You own the book and match incoming against resting orders, compute allocation or priority, publish a sequenced feed, or run a
clearing or liquidation batch, load `fin-matching-and-settlement`; that skill also owns a resolution **you assign**, while trading a prediction market
somebody else resolves is this one. You post fills into double-entry accounts, load `fin-ledger`. The change is only amount arithmetic, rounding
direction, or the retry classification of an outbound call, load `fin-money-core`.

**Read your venue's file from the References table below before writing any recovery path,** on the predicate stated there, applying it in order
rather than summarising it.

## Core rules

### An identity the counterparty scopes to open instructions correlates; it does not deduplicate

The identity you attach to an instruction is an idempotency key only where the counterparty documents that resending it returns
the original instruction instead of creating a second one. Where it is unique only among *live* instructions it correlates a
response to an intent and nothing more: once the instruction reaches a terminal state the value is free again, and resending it
opens a second position. Treat it as a correlation key by default. This specialises *durable intent before the external effect*.

**Shape**

```
identity = f(intent instance)          # survives ROLLBACK, never derived from a repeating value
COMMIT intent row(identity, economic terms, account, sequence, payload, time bracket, state)
send instruction                       # no transaction block lexically encloses this line
record outcome against identity
```

One identity per *logical* instruction, reused across every retry of it, never one per attempt. `flush()` inside an open
transaction is not persistence: a rollback on the exact timeout the row exists for mints a fresh identity on retry and buys twice.
An identity built from a bar timestamp, `strategy+symbol+side` or a wall-clock second repeats on its own.

**How it appears**

| Literal | What it tells you |
|---|---|
| Binance spot and futures, OKX, Kraken REST/WS | uniqueness enforced only among *open* orders. The reuse window is **zero seconds** after fill or cancel, exactly the marketable-limit/IOC case where an ambiguous response is most likely |
| OKX documentation | *"Once an order reaches a terminal state (filled, canceled, mmp_canceled), the same clOrdId may be reused for a new order."* |
| The committed intent row, seven fields | client order ID; full economic intent (instrument, side, qty, price, TIF, reduce-only, `positionSide`); venue + account + API-key identity, because uniqueness is per-account; the venue's own sequence or nonce where one exists (OUCH `UserRefNum`, Hyperliquid nonce, FIX `MsgSeqNum`); the exact signed payload bytes on signature-authenticated venues; `sent_at` with a `not_before`/`not_after` bracket, because every history endpoint is time-windowed; `state = INTENT_RECORDED`, flipped to `SENT_UNCONFIRMED` after the write |
| Cross-venue charset intersection | ≤18 alphanumeric characters, no punctuation. Validate at construction, not at send |
| `session.begin()`, `engine.begin()`, `@transaction.atomic` | none may lexically enclose the send |
| Query-first is the default | the venue table is the exception list and the retention bounds live there. The malign outcome is a strategy that hedges and exits one lot while carrying a naked residual it cannot see |

### An answer that is not a clean acceptance says nothing about whether the effect happened

A timeout, a socket close, a 5XX, a rate-limit rejection or a vendor "status unknown" code is UNKNOWN, never "did not happen". A
success status means accepted, never executed. Resolve UNKNOWN by asking the venue about the identity you sent, escalating through
sources until one is definite, and stop at that rung. Never resubmit.

**Shape**

```
acceptance carrying your identity   -> booked
names a business rule you violated  -> rejected, terminal
anything else                       -> UNKNOWN
UNKNOWN -> query by identity: propagation retry -> open list -> history -> fills
        -> still undecided: hold at full notional, close the gate, run the clock
never   -> send the instruction again
```

The ladder ends at fills because they are the only ground truth about economic effect. A "not found" on the first rung is not
proof of non-creation: read replicas lag the matching engine, so the absence you observe may be staleness rather than absence.

**How it appears**

- HTTP 5XX, a socket timeout, HTTP 429, Binance `-1006 UNEXPECTED_RESP` and `-1007 TIMEOUT` (*"Send status unknown; execution
  status unknown"*) are all UNKNOWN. No venue in the corpus documents that a 429 on an order endpoint guarantees non-creation.
- `-2013 NO_SUCH_ORDER` immediately after placement is **not** proof of non-creation. Binance documents three data sources (Matching Engine, Memory,
  Database) with different staleness and states *"The API system is asynchronous, so some delay in the response is normal and expected."* Re-query
  with backoff across the propagation window, then open orders, then order history, then fills.
- `-2011 CANCEL_REJECTED` is expected in normal operation: the order filled between your decision and your cancel. Re-read its
  terminal state; do not retry the cancel as an error.
- Cancel/replace is not atomic. HTTP 409 with `-2021` means **one leg succeeded**; determine which before anything else. `-2022` means both failed.
- **Never use ccxt's documented `fetchBalance()` timeout-recovery procedure**, which races against fees, funding and other
  strategies. Set `options['maxRetriesOnFailure']` to 0: ccxt's retry funnel re-POSTs a create-order under the *identical* client
  order ID. Read [ccxt-and-fix.md](references/ccxt-and-fix.md) first.

Unresolved, hold the instruction `INFLIGHT_UNKNOWN` **at full notional in the risk calculation**, close the risk gate for that
instrument, and escalate. That state carries a wall-clock budget declared as a config value. Past it the system automatically
takes the risk-*reducing* action (cancel by identity, then flatten the instrument) rather than waiting for a human. "Hold and
escalate" with no clock leaves the desk dead until someone wakes up, and that is how the rule gets disabled.

### Every refusal that protects you runs inside the function that sends

Two independent bodies can refuse an instruction: the counterparty, through the constraints its validator publishes, and you, through your risk limits.
Both are evaluated inside the function that sends, before the write, against live state. A check in a sibling module or a monitor reading a metric is a
detector, not a control, because some send path will not go through it. Measure exposure from instructions *entered*, not executions received.

**Shape**

```
send(intent):
    constraints = cached(instrument)      # fetched live at startup, refreshed, fixtured for tests
    round each field toward legality in exact decimals; qty -> 0 is an explicit SKIP, never a no-op
    re-check ALL constraints together, for THIS instruction type
    position, reference_price = read live
    evaluate notional, position, rate and a deviation band from this instrument's own reference price
    refuse -> explicit error   |   pass -> write
```

Satisfying one constraint can break another, so the whole set is re-checked together after rounding, and rounding toward validity
never increases size. Derive the deviation band the same way on every session state (pre-market, auction, halt, continuous): a
band derived from a value the instrument does not participate in admits everything.

**How it appears**

- Fetch via `exchangeInfo` or the venue's equivalent. `MARKET_LOT_SIZE` is a separate filter from `LOT_SIZE` and applies only to MARKET orders.
  `NOTIONAL` and `MIN_NOTIONAL` are different filter types, of which a symbol exposes one. Commit a fixture captured from production, not hand-written.
- The simultaneous check: `price % tick == 0`, `qty % step == 0`, `price * qty >= minNotional`, `minQty <= qty <= maxQty`.
- Do it in `Decimal`: `int(0.29/0.01) == 28`, so a float floor-division silently loses a whole step. Serialize as a decimal
  string; `str(1e-05) == '1e-05'` reaches the wire as illegal characters.
- 17 CFR 240.15c3-5(c)(1) requires the control be "applied on an automated, pre-trade basis, before orders are routed" and
  assessed "on the basis of exposure from orders entered … rather than relying on a post-execution, after-the-fact determination".
- Goldman's pre-market band was bounded by 1.5× the highest close of *any* listed option, so a $1 order in any name passed it.
- Names to look for, and to check are actually called on the send path: `max_order_notional`, `max_position`, `max_orders_per_second`.

### Your absence is the counterparty's job to notice, and your return does not restore what you missed

A process that dies cannot cancel anything, so the only switch that fires *because you went away* is the one the counterparty
holds. Coming back is the harder half: re-subscribing restores the connection, not the range. Nothing may act on local state until
the rebuild completes, the missed range is computed and materialised rather than skipped, the cursor outlives the process, and a
page at the documented cap is a hole rather than the end. This specialises *proven coverage before the cursor advances*.

**Shape**

```
session start -> arm the counterparty-side dead-man switch (timeout < reconnect backoff)
invalidate_state():  result = cancel_all(); check(result)   # unchecked return is not a cancel
reconnect -> snapshot -> diff(snapshot, persisted state) -> emit synthetic missed events
          -> persist cursor ONLY over the range covered -> mark ready -> allow sends
sequence gap                                     -> discard and re-snapshot, never patch
page at the documented cap, or a range rejection -> hole, not an empty result
```

The local `cancel_all()` covers the different failure where you are still connected and your own state went bad, so it belongs inside the function
that invalidates state, not beside it. Under *implemented, not described*, a defined-but-uncalled `on_stale`, a `cancel_all` behind a config flag
defaulting off, or a line deferring the decision to "whoever owns risk" is the defect, not a plan.

**How it appears**

- Venue-native switches: Binance, Bybit and OKX cancel-on-disconnect; Deribit `set_heartbeat`; FIX `CancelOnDisconnect`. Arm one
  at session start with a timeout shorter than your reconnect backoff.
- **Gate.** `mark_ready()` is called after `on_resync` completes, never before, and order submission is blocked until it is. The
  ordering bug is the whole failure.
- **Gap.** Diff the snapshot against persisted state and emit a synthetic missed-fill event; recovering net position alone leaves
  realized PnL permanently short. Synthesised ids must be **deterministic over venue-supplied fields including a venue
  timestamp**, so the same inference after a restart dedupes against itself.
- **Durability.** `last_trade_id` / `last_update_id` is persisted to disk or Redis, not held in memory, or every cold start
  `continue`s past the reconciliation entirely.
- **Pagination.** The backfill loops until the venue returns fewer rows than the page size. One unpaginated call silently truncates the gap.
- Follow **your venue's** exact snapshot/incremental join algorithm and nothing else: Binance Spot and Binance Futures use
  different algorithms, and the wrong one is the most-copied incorrect snippet in the ecosystem.

### Data has an age, and age is not arrival order

Every quantity you decide on carries a timestamp assigned by the authority that produced it, and a declared maximum age. Freshness
and ordering fail in opposite situations: a perfectly ordered feed that stopped ten seconds ago passes the ordering guard and
fails the freshness guard. A system needs both, and a reviewer who searches for one will find it and conclude the other exists.

**Shape**

```
store(value, authority_event_time)
before any decision:   now - event_time > max_age   -> stop quoting
independently:         event_time < last_seen       -> out of order, drop
```

Judge staleness from the data, because a socket can be open and delivering nothing.

**How it appears**

- Store the **venue's own event timestamp** on every book, mark, index, quote and funding rate, and evaluate `now − ts > max_age`
  on the order-submission path on every tick.
- Quoting stops on: age > `max_age`, a sequence gap, an unsynced book, or an unrenewed `listenKey` (60-minute lifetime; a lapsed
  one is a blind position).
- nautilus's `is_stale` is the *ordering* guard (`ts_init < self.builder.ts_last`), and no project in the corpus has a wall-clock
  age gate on market data feeding an order decision. Write the `now − ts` form explicitly.

### Quantities fold from the recorded set; they never accumulate from deltas

Read cumulative totals from the authority rather than summing deltas you observed, and derive every economic aggregate as an order-independent fold
over the persisted event set. Deduplicate on the authority's own event identity, before the state transition, rejecting the duplicate rather than
ignoring it, and write the dedupe record in the same transaction as the state it protects.

**Shape**

```
event -> dedupe on authority event id (same transaction as the state row) -> append to persisted set
position, average price, realized PnL = fold(persisted set)      # recomputed, never incremented
```

An in-memory dedupe set re-applies every counted event after a restart. An incrementally accumulated average turns a backfill
interleaved with a live stream from a transient reordering into permanent corruption.

**How it appears**

- Read cumulative filled quantity from the venue (`executedQty`, `cumQty`), never a delta you accumulated. Dedupe on `trade_id`: the same fill arrives
  on the stream *and* the poll. `_seen_trade_ids` held only in memory is the measured form of the restart bug.
- Ship the test that proves the fold: ascending and descending fill order produce a byte-identical average. nautilus asserts exactly this in
  `test_avg_px_invariant_to_fill_arrival_order`, and freqtrade reached the same design independently in `recalc_trade_from_orders`.
- The one escape hatch is a reorder buffer, and only where the venue's sequence is authoritative and you assert it.

### A pushed lifecycle event is a claim about a state the counterparty owns

Arrival order is not occurrence order, so a push is a notification, not an authority. Guard legality, guard version, then re-read
the quantity from the authority before any value-moving decision. This specialises *arrival order is not occurrence order*.

**Shape**

```
event -> (state, event) legal?    no -> explicit error, never a silent ignore
      -> watermark keyed on the INSTRUCTION identity, stored apart from the live object,
         the guarded UPDATE being itself the guard, in the transaction with the effect
      -> re-read amount, status and attribution from the authority -> then decide
```

A terminal state accepts exactly the events by which the venue corrects a fact you already booked (a late fill, a fill void) and is never re-opened by
a *status* message. Deny by default. The watermark keys on the identity rather than on the object, because the object can be gone.

**How it appears**

- Write `if last_seen[client_id] >= event.update_time: return`, keyed on the ID, only where `update_time` is a total order.
  Balance events get the same guard, not just orders.
- The measured bug is `if existing is not None and event.update_time < existing.update_time: return`. A terminal event has already popped `existing`,
  so the guard is skipped, a replayed pre-snapshot `PARTIALLY_FILLED` re-inserts a phantom open order, and `reconcile` then sees it in `live` and
  declines to re-place. The bot believes it is quoting; the book holds nothing; the hedge leg is naked and invisible.
- **A late fill on a cancelled order is not a redelivery.** `(Canceled, Filled)` is a legal transition (nautilus ships it
  annotated `// Real world possibility`) and the fill is real money. The cancel was a *request*, not a fact.
- `(Canceled, Accepted)`, `(Filled, Accepted)`, `(Filled, Canceled)` and `(Rejected, *)` are absent from the table and must hit
  the `_ => Err(InvalidStateTransition)` arm.

## Position, fees and PnL

### The counterparty's number is the record; yours is a hypothesis until it is compared

Because an external party can confirm your state, reconciliation against that answer, not defensive coding, is the safety net. Name the authority and
the join key for every economic quantity you report, then ship the comparison as a scheduled entrypoint that runs. This specialises *reconciliation
runs in production*; under *a comment is a claim*, an invariant living as SQL in a docstring or a "worth running as a cron" note counts as absent.

**Shape**

```
scheduled: authority_position, authority_pnl = query(venue)
  assert Σ(signed local fills) == authority position, per (instrument, position side)
  assert local realized PnL     == the authority's own realized figure
  mismatch -> record the authority's value, close the gate for that instrument
           -> reopen only on a later successful comparison
```

**How it appears**

- Compare **per `(symbol, positionSide)`**. Where the venue publishes its realized-PnL figure on the execution event, cross-check
  on **every event**, not only on the schedule. Receiving that field and never comparing it is the measured 0-of-3 failure.
- **Choose the cadence to exceed the venue's documented replication lag.** Binance labels each endpoint's data source (Matching Engine / Memory /
  Database); the private stream is ME-sourced and many REST reads are not, so a reconciliation running faster than the lag oscillates and gets muted.
- **Express the tolerance in the instrument's own tick or lot, never a fixed epsilon.**
- The best platform in the corpus has the startup reconciliation gate and **discards** the continuous one (`let _ =`). Computing
  the number and throwing it away is the failure mode to avoid.

### A cost paid in a third unit is still part of the outcome

A fee charged in a unit that is neither the quote nor the settlement asset does not disappear because it is inconvenient to convert: either convert it
at a recorded rate into the reported figure, or expose it unconverted in a field every consumer is forced to handle. A fee taken out of what you
received reduces what you can subsequently send, and a price target computed before fees is not the target you meant.

**Shape**

```
received_qty = filled_qty - fee, where the fee is in the unit received  -> then re-round down
executed_vwap = quote executed / base executed
target = executed_vwap * (1 + goal) * (1 + fee_in) * (1 + fee_out)  -> quantize away from your favour
fee in a third unit -> convert at a recorded rate, or surface it unconverted
```

**How it appears**

- Book the fee in the currency the venue reports (`commissionAsset`) and handle a null or absent fee asset. On a BNB-discount Binance account,
  excluding BNB commissions overstates headline profit by the entire fee bill. Otherwise the struct carries a `fees_unconverted: [assets]` field.
- A base-asset commission reduces the quantity you actually received: buy 36.38 GTC with a 0.1% GTC fee and you hold 36.34.
  Selling `trade.amount` returns `-2010 insufficient balance`, and selling `free_balance` returns `-1013 Filter failure: LOT_SIZE`
  because it is not a multiple of `stepSize`. Subtract the fee, then re-snap down in `Decimal`.
- Compute the exit from the actual executed VWAP (`cummulativeQuoteQty / executedQty`), then `exit = entry_vwap * (1 + target) * (1 + fee_in) * (1 +
  fee_out)` at the account's effective maker/taker rates, **then** quantize, rounding **up for a sell target, down for a buy target**, never to
  nearest, which surrenders up to half a tick of the fee markup half the time. A nominal +1% take-profit realizes roughly +0.8% at 0.1% round-trip
  taker fees. The direction of this error is always the same: costs understated, profit overstated.

### The counterparty changes your position without an instruction from you

Value moves on your account from events you did not initiate, and a model that ingests only the responses to your own instructions
is structurally blind to them. Ingest the authority's full account-activity feed, key position on every dimension the venue keys
it on, and treat venue-originated activity as yours.

**Shape**

```
position key = (instrument, every dimension the venue itself separates)   # never instrument alone
ingest account activity, not only the responses to your own instructions
each activity type deduped by ITS own authority id, exactly as fills are
```

Filtering "activity that is mine" by "activity whose identity I generated" excludes exactly the events that change PnL without your consent.

**How it appears**

- **Key positions on `(symbol, positionSide)`, not on symbol alone**, and read the account's position mode at startup. In hedge
  mode a LONG +5 and a SHORT −3 collapse into a fabricated flat −3 when `positionSide` is dropped, and every downstream sizing,
  flatten and liquidation-distance decision is then computed from a position that does not exist.
- Check reduce-only preconditions against the mode: reduce-only is unavailable in Binance hedge mode and on Bybit spot, `closePosition` is
  incompatible with `quantity`, and oversized reduce-only orders are split by Bybit or rejected by Binance with `-2022`.
- **Funding, ADL, liquidation, settlement and delivery are required PnL components**, each deduped by the venue's
  settlement/income id exactly as fills are deduped by `trade_id`. A bare `balance += funding` with no settlement id double-counts
  on every redelivery and backfill. Ingest the venue's income/transaction-history endpoint, not only the order stream.
- NautilusTrader's adapters identify venue-generated orders by Binance's `autoclose-`, `adl_autoclose`, `settlement_autoclose-` and
  `delivery_autoclose-` client-ID prefixes and by an empty `orderLinkId` on Bybit. Verify those markers against your venue's docs before keying on one.

### An excess quantity is exposure, not an exception

When the authority reports more than you asked for, the extra units exist and someone must hedge them. Record the authority's number unclamped, carry
the excess in a field that position and PnL fold over, and close the gate. The gate blocks new and size-increasing instructions only: everything that
reduces or measures risk keeps working while it is closed. Never abort, never clamp, never drop the event.

**Shape**

```
authority qty != local qty -> record authority qty unclamped, excess -> a FIELD (not a log line)
                           -> alert, close the gate for that instrument
gate closed: new sends and size-increasing amends refused; cancel, flatten, position, PnL and
             margin all still callable, and a test proves it
gate reopens: only on a successful comparison against the authority, never on a timer,
              never from the code path that closed it
```

**How it appears**

- Position and PnL are recomputed as a fold over the fill set **including** `overfill_qty` (a field, not a log line). The gate blocks `submit_order`
  and any size-increasing amend only; `cancel_all(scope)`, `flatten(scope)`, position, PnL and margin keep working.
- Ariane 501: *"It was the decision to cease the processor operation which finally proved fatal"*. Knight ¶42: disconnect the
  emitter, keep managing the position.
- nautilus's `allow_overfills` defaults to **false**, and with false the reconciliation path *discards the fill* (`return None`),
  leaving your model short by exactly that amount with a `WARN` as the only trace.

## Seam S3: exchange and ledger

A fill becomes a posting. The venue is the authority for the fill, the ledger is the authority for the balance, and neither answers the other's
question. The join key is the venue's own trade identifier: the ledger transaction id derives from it, so the same fill arriving on the stream and on
the poll posts once. Realized PnL, fees and funding post as journal entries; positions do not, because a position is revisable and a posted entry is
not. A fill busted inside the clearly-erroneous window is a new balancing entry, never an edit to the old one. `fin-ledger` states this seam from the
balance side, and a contradiction between the two statements is a suite defect to report rather than a judgement call.

## Seam S4: venue and client

A client reconciles against the venue; a venue has nothing to reconcile against. Where one process is both, operating a venue and trading on another (a
broker OMS that is the system of record for its clients' orders and is simultaneously a client of the exchange), split the change and tier the halves
separately: the client half is T2 and reconciles by client order identity, the venue half is T3 and requires order-by-order rejection on the last hop,
a deterministic core and deterministic simulation. One tier declaration must not cover both halves. Knight Capital sat exactly on this boundary.
`fin-matching-and-settlement` states this seam from the venue side.

## Required tests

Two properties are proven as code, in the response that creates or edits the order path, before this runs against live keys. Not a description, not a TODO, not a pointer to a test plan.

**(a) An ambiguous submission does not duplicate the economic effect.** The instruction is delivered but the answer is lost: the client must not send
again, must ask the venue about the identity it sent, and must end with exactly one instruction in existence. The mirror case, where the request never
reached the venue, must end with the retry actually happening, otherwise the test passes by doing nothing.

**(b) A normalised instruction satisfies every venue constraint simultaneously.** Generated over the boundary region of the real constraint set, for
each instruction type, the output is either an explicit skip or legal against all constraints at once, and never larger than what was asked for.

The Python below is one instantiation. In Rust or Go the same two properties are asserted with a fault-injecting transport double and a table-driven or `proptest`/`quickcheck` generator over the same production fixture.

```python
# (a) tests/test_ambiguous_submit.py: the timeout that already filled
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

# (b) tests/test_filters.py: the filter property test
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

## Output

Default for every economic change at T0 and T1. Seven labels, this order, about ten lines:

```
FINANCIAL CHECK
tier:       T<n>, and the signal that placed it there
effect:     what moves value, from whom to whom, in what unit
identity:   the stable identity of the intent, durably recorded at file:line
ambiguity:  which venue responses are UNKNOWN, and how they resolve
authority:  whose copy of each quantity is the record
recovery:   what a crash or restart between the send and the local commit does
controls:   <control> -> <file:line>, one per line; at T2 and above also `· <test name>`
            UNRESOLVED: <control> (<why>), for anything not implemented
```

Every control named is either a real `file:line` or an explicit `UNRESOLVED:` line; a described control with no location is a defect. Below T2 the
`controls:` line is the whole evidence requirement, and no named-risks table is emitted on a routine change. **At T2 and above** (a customer or tenant
on a position row, a payout path, or two or more venue adapters), add the domain contract block:

```
VENUE CONTRACT
venue + account: which venue, which account, whether identity is scoped per-account
id semantics:    correlation only, or documented dedupe, with the retention window and its source
unknown set:     the responses treated as UNKNOWN, and the query that resolves each
recovery:        how position, fills and the book are rebuilt after a gap, and what is blocked meanwhile
reconciliation:  the scheduled comparison, its join key, its cadence and its tolerance unit
risk gate:       what closes it, what stays callable while closed, what reopens it
```

Emit only the slots this change touches: a slot it touches and cannot fill is the finding, a slot it does not touch is omitted. At T3 add the
per-technique evidence table, whose shape `fin-verification` owns. Do not emit this block below T2. The venue-of-record block is `ENGINE CONTRACT` in
`fin-matching-and-settlement`, and a process that is both a venue and a client of another venue emits both.

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
