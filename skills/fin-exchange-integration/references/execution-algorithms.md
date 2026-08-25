# Execution algorithms

For the developer building a parent order that slices into children: TWAP, VWAP, POV, implementation shortfall,
anything with a `participation_rate`. The failure modes are feedback loops (a participation rate driven by a
volume series your own fills inflate) and accounting errors between parent and children, where the parent's
view of what is working diverges from the live children and the algorithm keeps sending. Every algorithm here
carries a mandatory price bound and a mandatory time bound.

## Contents

- The accounting identity: one number, not two
- Building the schedule: the remainder is the whole problem
- Re-checking parent liveness on every tick
- Re-slicing after a partial fill without double-counting
- Restart: the schedule input is reconciled `leaves_qty`, never config
- The mandatory pair: a price bound and a time bound
- Never drive a rate from a metric your own fills inflate (and volume is not liquidity)
- Scheduling drift and end-of-schedule dumping
- Collaring a marketable child, derived per session state
- Child lifecycle: cancel races, orphans, and the fill after completion
- Non-fills that decrement nothing: post-only reprice and STP
- Benchmarks and what they actually measure
- Backtest fills a live path cannot deliver
- What the venue does with your children when you disconnect
- REQUIRED OUTPUT: the conservation tests

## The accounting identity: one number, not two

A parent quantity and a separate "filled so far" counter are two numbers that drift. Make the **parent's
remaining quantity the running remainder** and there is one:

`parent.leaves_qty + Σ(accepted child leaves_qty) + Σ(child cum_qty) == parent.original_qty`

NautilusTrader enforces this by construction: each spawn *reduces the primary's quantity*, is capped at the
primary's `leaves_qty`, and restores the deduction if denied or rejected **before acceptance**; the final slice
submits the primary itself; children carry `{primary_client_order_id}-E{n}` (Execution concept guide,
`crates/trading/src/algorithm/twap.rs`).

Knight Capital is what happens when the termination state lives where the emitter cannot read it. SEC 34-70694
¶16, verbatim:

> "Because the cumulative quantity function had been moved, this server continuously sent child orders, in
> rapid sequence, for each incoming parent order **without regard to the number of share executions Knight had
> already received from trading centers**. Although one part of Knight's order handling system recognized that
> the parent orders had been filled, **this information was not communicated to SMARS**."

The fill state existed and was correct; it was not readable by the component whose loop bound depended on it.
212 parent orders became "millions of child orders" and over 4 million executions in 45 minutes (¶17); ¶21 names
the missing control: *"a control to compare orders leaving SMARS with those that entered it."* So the counter
and the emit check live in the same function, and the check is `≤`:

```python
def spawn_child(parent_id, qty, price) -> ChildOrder | None:
    parent = store.load_parent_for_update(parent_id)     # SELECT ... FOR UPDATE, not a cached copy
    if parent.is_closed():                               # reloaded, not captured; see below
        return None
    if qty > parent.leaves_qty:                          # hard bound, ON THE EMIT PATH
        raise ParentQuantityExceeded(parent_id, qty, parent.leaves_qty)
    if parent.children_sent + 1 > parent.max_children:   # max_children_per_parent, configured
        raise ChildCountExceeded(parent_id)
    parent.leaves_qty -= qty                             # deduct BEFORE the send
    parent.children_sent += 1
    store.commit(parent)
    try:
        return venue.submit(client_order_id=f"{parent_id}-E{parent.children_sent}", qty=qty, price=price)
    except Denied:                                       # pre-acceptance only
        store.restore(parent_id, qty); raise
```

## Building the schedule: the remainder is the whole problem

`slice = round(parent_qty / n, lot_size)` loses or invents quantity whenever the division is inexact.

| Parent | Intervals | Step | Naive per-slice | Σ slices | Error |
|---|---|---|---|---|---|
| 1000 | 7 | 1 | `floor` → 142 | 994 | 6 units never trade; parent reported complete |
| 1000 | 7 | 1 | `round-half-up` → 143 | 1001 | 1 unit over-executes |
| 1.0 BTC | 6 | 0.001 | `floor` → 0.166 | 0.996 | 0.004 BTC unexecuted |

The production shape (`twap.rs`): compute `qty_per_interval = floor(total / num_intervals)` **at the
instrument's `size_precision`**, append the remainder as an additional slice, then **deny the parent order** if
`sum(scheduled_sizes) != total_qty`. It runs before the first child is sent, not after the last.

```python
def build_slices(total: Decimal, n: int, step: Decimal) -> list[Decimal]:
    per = (total / n).quantize(step, rounding=ROUND_DOWN)
    if per < min_qty or per * ref_price < min_notional:
        raise ScheduleInfeasible(total, n, step)      # fewer, larger slices; never a silent 0
    slices = [per] * n
    remainder = total - per * n                       # exact in Decimal
    if remainder > 0:
        slices.append(remainder)                      # a real extra slice, not a rounding fudge
    assert sum(slices) == total, (slices, total)      # deny the parent if this fails
    return slices
```

`ScheduleInfeasible` catches `per < minQty` or `per * price < minNotional`, where the venue rejects **every**
child and the algorithm loops until the horizon. And `remainder` is frequently below `minQty`: decide
explicitly whether it merges into the last slice or is abandoned, not by discovering a rejected order later.

**A uniform TWAP schedule is a choice, not a neutral default.** Almgren & Chriss (*Optimal Execution of
Portfolio Transactions*, 2000): λ = 0, risk neutrality, yields the "naïve" strategy of *"trading in equally
sized packets, using all available trading time equally"*. Their temporary impact is `h(n/τ) = ε·sgn(n) + η·n/τ`
with ε ≈ half the bid-ask spread **plus fees**; a schedule with no ε is a different strategy, not a safer one.

## Re-checking parent liveness on every tick

A schedule held only in a timer keeps firing after the parent is cancelled, filled elsewhere, or expired.
`twap.rs`'s `on_time_event` **reloads the primary from the cache** and, if `primary.is_closed()`, cancels the
timer and completes the sequence. Reload; never close over a snapshot:

```python
parent = store.load_parent(parent_id)        # RELOAD. A captured object is stale by definition.
if parent.is_closed() or parent.leaves_qty <= 0:      # Filled|Canceled|Rejected|Expired|Denied
    self.timer.cancel(); self.finish(parent_id, reason=parent.status); return
```

`is_closed()` must be the venue's own terminal-status set, not a local boolean the tick handler maintains: a
parent can be closed by a path the algorithm never sees: an operator flatten, a venue-side liquidation, a
reduce-only fill from another strategy on the same `(symbol, positionSide)`.

## Re-slicing after a partial fill without double-counting

The measured bug: on a partial fill the algorithm recomputes the remaining schedule from
`parent_qty - filled_qty`, but the children already scheduled-and-unsent are **still in the timer queue**, and
the union over-executes by exactly that quantity. There is no correct way to reconcile two schedules.

1. On a fill, **do not rebuild the schedule.** `leaves_qty` already decreased; the next tick reads it.
2. If you must rebuild (the horizon moved, the cap changed), **cancel the timer, drain every pending scheduled
   entry, and only then build a new schedule from the freshly-loaded `leaves_qty`.** Draining is a synchronous
   step whose completion you assert, not a flag you set.
3. A scheduled-but-unsent entry holds no quantity; only an *accepted* child does. Never deduct twice.

An amend that *increases* a child's size is a new deduction through `spawn_child`'s bound check; one that
decreases it restores the difference only when the venue acknowledges, never on request.

## Restart: the schedule input is reconciled `leaves_qty`, never config

The process restarts mid-schedule, the algorithm reads its config (`sell 10,000 over 1 hour`) and starts again
from zero; the 4,000 already executed are invisible. On start, the gate matters more than the query:

```
1. Load persisted parents in a non-terminal state, by parent id.        (disk/Redis, not memory)
2. Query the venue: open orders, order history, and FILLS, per parent's child-id prefix.
3. Fold fills → recompute cum_qty per child; parent.leaves_qty = original_qty − Σ(child cum_qty).
4. Adopt or cancel every child the venue reports that local state does not know about.
5. mark_ready()  ← only now.  Order submission is blocked until this returns.
6. Rebuild the schedule from parent.leaves_qty and the REMAINING horizon (not the original horizon).
```

Step 5 before step 3 is the whole failure. Step 6's second clause is its own bug: resuming with 45 minutes
elapsed against the original 60-minute horizon derives a slice size that cannot finish, and the shortfall lands
in the end-of-schedule dump. A venue query that *fails* is not a report of zero; skip the cycle and stay gated
rather than treating missing reports as flat.

## The mandatory pair: a price bound and a time bound

Every parent carries both. Not one, and not "a participation cap, which is effectively a time bound".

CFTC-SEC, *Findings Regarding the Market Events of May 6, 2010* (30 Sep 2010), p.2: **the report never names
the firm**; it says only "a large fundamental trader (a mutual fund complex)" / "the large Fundamental Seller".
The strings "Waddell" and "trillion" each appear zero times in it:

> "This large fundamental trader chose to execute this sell program via an automated execution algorithm ('Sell
> Algorithm') that was programmed to feed orders into the June 2010 E-Mini market to **target an execution rate
> set to 9% of the trading volume calculated over the previous minute, but without regard to price or time.**"

Footnote 24 names the two missing throttles in the report's own words:

> "…some traders feed orders into the market based on volume-weighted average price ('VWAP') algorithms that are
> designed to obtain an average price over a specified period of time and therefore have a **built-in time
> throttle that prevents an unexpectedly fast execution that can cause significant market impact. Other such
> throttles include a limit price that would prevent executions at unfavorable prices.**"

The comparison run, quoted precisely because it is more qualified than the folklore (p.2): the same trader had
previously used *"a combination of manual trading entered over the course of a day and several automated
execution algorithms"* taking price, time and volume into account, taking *"more than 5 hours"* for the first
75,000 contracts. On 6 May the volume-only algorithm finished in *"just 20 minutes"*.

| Bound | Parameter | Evaluated | When it binds |
|---|---|---|---|
| Price | `limit_price` on every child + `max_deviation_from_arrival` on the parent | before every send, against the instrument's own reference | stop sending; raise `PRICE_BOUND_BREACHED`; **do not** widen and retry |
| Time | `end_time` (absolute) + `max_rate` in qty per unit time | every tick | stop at `end_time` with `leaves_qty > 0`; raise `UNFINISHED_SCHEDULE`; never compensate with a terminal clip |
| Fan-out | `max_children_per_parent`, `max_notional_per_parent` | inside `spawn_child`, before the send | halt the parent; the flag is not resettable by the component that tripped it |

The price bound is evaluated **independently of the participation logic**: a separate function reading a
separate input, so a bug in the rate calculation cannot disable it. Both fail *closed*, and an unfinished parent
must be a first-class visible state: a system that treats it as an error is one whose operator disables it.

## Never drive a rate from a metric your own fills inflate

A participation-rate control loop whose input includes its own output is a positive feedback loop. The report,
p.14:

> "**The Sell Algorithm used by the large Fundamental Seller responded to the increased volume by increasing the
> rate at which it was feeding the orders into the market**, even though orders that it already sent to the
> market were arguably not yet fully absorbed by fundamental buyers or cross-market arbitrageurs."

**(a) Your own prints are in the tape.** Exclude them by `trade_id`: `sum(t.qty for t in tape if t.trade_id not
in own_ids)` over the window, on venue event timestamps. Do **not** subtract your `cum_qty` from the tape total:
your fills and the tape are independently timestamped, independently late streams, so the subtraction goes
negative under ordinary reordering. Where the public tape exposes no id you can match (many crypto venues
publish an aggregate-trade id, not the maker/taker ids you receive) you *cannot* exclude them; treat the
participation figure as an upper bound and set a correspondingly lower cap.

**(b) Exclusion is not enough, because the churn you induce is not your print.** On 6 May the volume that
accelerated the algorithm was other participants': HFTs passing inventory between themselves, of which an
own-print filter removes not one contract. This is why the price bound is mandatory and separate: it is the only
control that binds on a feedback loop you did not author. Bound the rate on both sides,
`qty_this_interval = min(schedule_qty, pov_rate * participation_volume, max_qty_per_interval, leaves_qty)`,
where the third term is an absolute clamp in instrument units that no volume estimate can raise.

**And volume is not liquidity.** p.14: *"especially in times of significant volatility high trading volume is
not a reliable indicator of market liquidity."* Measured, p.15: *"between 2:45:13 and 2:45:27, HFTs traded over
27,000 contracts, which accounted for about 49 percent of the total trading volume, while buying only about 200
additional contracts net"*, while buy-side E-Mini depth was *"about $58 million, less than 1% of its depth from
that morning's level."* So add a book-depth term that can only *reduce* size, gated for freshness like any other
market data (`now − ts > max_age`, on the venue's event timestamp): a stale depth reading licenses the size it
was captured before. And a marketable algorithm cannot tell that its counterparty is not real; §II.3.a: *"the
system did not necessarily recognize that it was hitting stub quotes (just that it was hitting the NBBO)."*

## Scheduling drift and end-of-schedule dumping

The schedule falls behind (venue rejects, a thin book, rate limits, a gated restart) and the algorithm sends
the shortfall as one clip at the horizon, converting a scheduling problem into a market-impact event with no
operator in the loop. Cap the catch-up so `carry` never bypasses `cap`:

`want = scheduled + carry`; `send = min(want, cap, leaves)`; `carry = want - send`. The carry accumulates but
never escapes `cap`, and when `carry > max_carry` (configured as a fraction of parent quantity) the algorithm
raises `ScheduleBehind` **at that moment**, not at the horizon. At `end_time`, `leaves_qty > 0` is an
`UNFINISHED_SCHEDULE` outcome that is reported and stops; the parent does not become a market order.

Slicing is a blast-radius control even when everything upstream is wrong: of Citi's US$444bn erroneous basket on
2 May 2022, US$189bn reached the CitiSmart algorithm, which sliced rather than sending whole notionals, and only
US$1.4bn executed before the trader cancelled at 09:10:30 (FCA Final Notice, Citigroup Global Markets Limited,
**17 May 2024**, ¶4.38–4.39). It is not a substitute for the missing upstream hard block.

## Collaring a marketable child, derived per session state

**Every marketable child is collared against that instrument's own reference price, and the collar is derived
the same way on every session-state code path.** Two failures, both in the primary record.

**The cross-universe aggregate.** SEC 34-75331 (*In the Matter of Goldman, Sachs & Co.*, 30 June 2015), ¶25: for
options with a bid/ask below $1 the band was ±100% of the NBBO, at or above $1 ±50%, *"However, as of August 20,
2013, **during pre-market hours, Sigma Options employed a 'default' price check, which allowed the transmittal
of options orders with any price greater than $0.01 and less than 1.5 times the highest closing price from the
prior day for any listed option.**"* ¶30: the orders *"were not stopped … because they were **priced at $1,
which fell between $0.01 and $3,090**"*. Computed from a maximum across the whole universe, the bound
constrained one instrument and was vacuous for every other. ~1.5 million contracts (≈150 million underlying
shares) executed; ~$38m loss after clearly-erroneous busts; $7m penalty.

**The session-state branch that was never wired.** SEC 34-70694 ¶21: Knight capped the limit price on a parent
and its children at 9.5% below the National Best Bid (sells) / above the National Best Offer (buys) as of the
time SMARS received the parent: *"Further, it did not apply to orders, such as the 212 orders described above,
that Knight received before the market open and intended to send to participate in the opening auction."*

So the collar is a function with two mandatory inputs and no fallback:

```python
def collar(order, session_state) -> tuple[Decimal, Decimal]:
    ref = reference_price(order.instrument_id, session_state)      # may raise; never defaults
    assert ref.instrument_id == order.instrument_id                # the Goldman assertion
    assert now_ns() - ref.ts_event <= MAX_REF_AGE_NS[session_state]
    band = BAND[session_state][order.instrument_id.class_]
    return ref.price * (1 - band), ref.price * (1 + band)
```

| Session state | Reference for **this** instrument | Notes |
|---|---|---|
| Continuous | own best bid/offer, or the venue's mark for that contract | freshest reference available; tightest band |
| Pre-/post-market | that instrument's own prior-session close | **never** an aggregate over other instruments; band no wider than continuous |
| Auction / pre-open | that instrument's indicative auction price if published, else its own prior close | the branch Knight's collar did not cover |
| Halted | none exists | **reject.** Do not widen; do not carry a pre-halt price forward as if fresh |

Three rules the two orders make non-negotiable: no session-state branch may be *wider* than continuous; a
reference that cannot be sourced is a rejection, never a default (NautilusTrader's risk engine logs `Cannot
check order risk: no price available` and declines to size rather than inventing one,
`crates/risk/src/engine/mod.rs`); and a placeholder in a price field reaches the market: Goldman's axes each
carried *"a placeholder price of $1"* (¶18), which a `$0.01` lower bound passed; sentinels belong to a type
that cannot reach the wire.

RTS 6 Article 15(1) is the regulatory floor for the same control: price collars that *"automatically block or
cancel orders that do not meet set price parameters, **differentiating between different financial
instruments**"*, plus maximum order values, maximum order volumes, and message limits covering submission,
modification **and** cancellation; 15(3) adds execution throttles, 15(6) a controlled override *procedure*:
authorised and logged, not a config flag. MiFID II RTS 6 Article 12 separately requires that you identify the
algorithm, trader, desk and client responsible for every order sent, which is what makes a partial kill work.

## Child lifecycle: cancel races, orphans, and the fill after completion

| Event | Wrong handling | Correct handling |
|---|---|---|
| Parent cancelled | cancel children; the timer keeps firing | **stop the schedule first** (cancel the timer, drain pending entries, assert drained), *then* cancel each live child by client order ID, *then* confirm each terminal state. Reverse this and you race your own scheduler |
| Cancel rejected | treat as an error and retry the cancel | the child filled between your decision and your cancel. Re-read its terminal state and fold the fill. On Binance this is `-2011 CANCEL_REJECTED`, and it is normal operation |
| Child fills after the parent reached `leaves_qty == 0` | drop the fill, or clamp it to remaining | the fill is real money. Record the venue's quantity **unclamped**, add the excess to an `overfill_qty` field, alert, close the risk gate for that instrument. The gate blocks `submit_order` and size-increasing amends only; `cancel_all(scope)`, `flatten(scope)`, position, PnL and margin keep working |
| Child cancel/replace | assume atomicity | it is not atomic. On Binance, HTTP 409 with `-2021` means **one leg succeeded**: determine which first; `-2022` means both failed. A replace that changes size changes the parent deduction, so resolve the leg before touching `leaves_qty` |
| Late fill on a cancelled child | reject as an illegal transition | `(Canceled, Filled)` is legal; the cancel was a *request*, not a fact. `(Canceled, Accepted)` is not, and must raise |

## Non-fills that decrement nothing: post-only reprice and STP

A child that never traded must not decrement the parent's remaining quantity, and a *prevented* quantity must be
restored to it. Both mistakes silently under-execute the parent while the algorithm reports itself on schedule.

**Post-only.** A post-only child that would cross is either rejected outright or, on venues that reprice, placed
at a price you did not compute. Treat rejection as a pre-acceptance denial (restore the deducted quantity and
reschedule) and a reprice as a **new economic fact**: if it moved the resting price outside the collar, cancel.
Never retry a post-only reject by flipping to a taker order; that is a fee-model change plus a collar bypass.

**Self-trade prevention.** A "prevented match" is not a trade. Binance Spot's STP FAQ gives the arithmetic as
`origQty − executedQty − preventedQty = quantity available for further execution`, with `preventedQuantity`
cumulative over the order's lifetime and reported separately. An algorithm computing a child's remaining as
`origQty − executedQty` believes that child is still working when it is not.

| Venue | Modes | Whose instruction governs | Gotcha |
|---|---|---|---|
| Binance Spot | `NONE`, `EXPIRE_TAKER`, `EXPIRE_MAKER`, `EXPIRE_BOTH`, `DECREMENT`, `TRANSFER` | account/symbol configuration | `DECREMENT` reduces **both** orders; `TRANSFER` moves prevented quantity and notional *between accounts* sharing a `tradeGroupId` |
| Coinbase | `dc` (default), `co`, `cn`, `cb` | **the taker's** instruction takes precedence | under `dc` with equal sizes **both** orders are cancelled; `co` cancels the resting order in full and lets the taker continue |
| Nasdaq (AIQ) | "Decrement both", "Cancel oldest" | AIQ configuration | the AIQ Canceled message reports `Decrement Shares` (*"incremental, not cumulative"*) **and** "Quantity prevented from trading"; they differ under "Cancel oldest" when the incoming order is smaller than the resting one |

Two parents in one account family working opposite sides of an instrument will self-match, so aggregate
self-trade risk **at the account-family level before submission**, not per algorithm instance. It is an
enforcement matter: the CFTC fined Coinbase $6.5m in March 2021 after two internally operated programs
*"matched orders with one another … resulting in trades between accounts owned by Coinbase"*, volume that
propagated into CME's Bitcoin Real Time Index, CoinMarketCap and the NYSE Bitcoin Index.

## Benchmarks and what they actually measure

| Benchmark | Reference price | What it hides |
|---|---|---|
| Arrival price / implementation shortfall | the mid (or decision price) at the instant the parent was accepted | the cost of delay, unless decision time is recorded separately from acceptance time |
| Interval VWAP | volume-weighted price over the parent's execution window | **your own prints are in it.** A large parent partly defines its own benchmark; more impact scores better |
| TWAP | unweighted mean over the window | where the volume actually was; perfect tracking can sit arbitrarily far from interval VWAP |

Decompose implementation shortfall into terms recorded when each becomes knowable: `(decision_px → arrival_px)`
delay, `(arrival_px → Σ fill_px·qty / Σ qty)` execution, `(unfilled_qty × (final_px − decision_px))` opportunity
cost, fees and base-asset commission fourth. Compute the executed leg from the venue's cumulative fields
(`cummulativeQuoteQty / executedQty` on Binance), never a running average you accumulated: a REST backfill
interleaved with the live socket permanently corrupts the latter; a fold over the fill set reorders transiently.

## Backtest fills a live path cannot deliver

Each row is a named default in a real system, and each inflates results with no error message.

| Assumption | Where | The live truth |
|---|---|---|
| A touched limit is filled | NautilusTrader `DefaultFillModel`: `prob_fill_on_limit=1.0`, `prob_slippage=0.0` | a touch is necessary, not sufficient. Queue position decides, and you cannot observe yours from a public feed |
| Orders fill completely | QuantConnect LEAN: *"In backtests, the pre-built fill models assume orders completely fill"* | partial fills are the normal case for any child sized above top-of-book |
| Replayed liquidity is inexhaustible | NautilusTrader `liquidity_consumption=False`: *"the same displayed size can support more than one simulated order in an iteration"* | your first child eats the size your second child assumed |
| No slippage inside a bar | Freqtrade: *"All orders are filled at the requested price (no slippage) as long as the price is within the candle's high/low range"* | for a slicer this is the entire cost model |
| Stops fill at the stop price | Freqtrade: *"Stoploss exits happen exactly at stoploss price, even if low was lower"* | a stop is a market order at the trigger |
| Fills against stale data | LEAN's documented "stale fills" hazard, filling against price data timestamped an hour or more in the past | the same bug class as sizing a child from a book snapshot older than the child: invisible in backtest, a guaranteed adverse fill live |
| No fees, no spread | n/a | Almgren–Chriss's ε is *half the bid-ask spread plus fees*. For a high-turnover slicer a fee-free backtest is a different strategy, not an optimistic one |

Intrabar ordering is unknowable: NautilusTrader splits a bar into four synthetic updates, defaults to O→H→L→C,
and says its adaptive path *"is a deterministic heuristic, not a reconstruction of the actual trade sequence"*,
mattering *"when both a protective stop and a profit target lie inside the same bar because the first visited
level determines which order can fill first"*; when a parent's price bound and its end-of-schedule action fall
in one bar, that decides which binds. And a bar's availability timestamp must be the interval **close**
(`ts_init = ts_event + interval_ns` for open-stamped sources), or a VWAP curve from the current bar is prophecy.

## What the venue does with your children when you disconnect

The venue's dead-man switch cancels **resting children**, which live at the venue. It does not touch the parent,
which lives in your process: on reconnect `leaves_qty` is unchanged while zero children rest, which is why the
restart sequence above re-derives the schedule from reconciled state rather than resuming the timer. Arm the
venue-native switch at session start with a timeout shorter than your reconnect backoff; the parameters differ
enough that copying one integration to another silently disarms it.

| Venue | Endpoint | Unit | Range / default | Note |
|---|---|---|---|---|
| Binance USDⓈ-M | `POST /fapi/v1/countdownCancelAll` | **milliseconds** | per-`symbol`; `0` disables | weight 10; countdowns checked ~every 10 ms; the doc warns against setting it "too precise or too small" |
| Kraken Spot | `POST /private/CancelAllOrdersAfter` | **seconds** | `< 86400`; `0` disables | recommended cadence every 15–30 s with `timeout=60`; disable before scheduled maintenance |
| Bybit | `POST /v5/order/disconnected-cancel-all` | **seconds** | `timeWindow` 3–300 | **the private WS must subscribe the `dcp` topic or DCP will not trigger**; `product` defaults to `OPTIONS`; institutional only; ~10 s for a config change to take effect |
| Deribit | `private/enable_cancel_on_disconnect` | connection-based | `scope` = `connection` (default) or `account` | triggers on TCP close, 10-minute inactivity, heartbeat failure (**not** on `private/logout`); WS only |
| OKX | `POST /api/v5/trade/cancel-all-after` | **seconds** | `0` or `[10, 120]` | heartbeat ~1 s; optional per-`tag` scope, max 20 concurrent tag-level CAAs |

OKX states the limit outright: *"the trading engine will cancel orders on behalf of the client one by one and
this operation may take up to a few seconds… clients should not use this feature as part of your trading
strategies."* A DMS bounds worst-case exposure; it does not define the moment your children stopped existing.
Its per-`tag` scope is the only mass-cancel primitive here scopable to one algorithm; everything else is symbol-
or account-wide, so a mass cancel from one parent kills every other parent on that symbol. Cancel by
`{parent}-E{n}` client order ID when the scope must be one parent.

**Not established here:** venue-native *algorithmic order* endpoints (exchange-hosted TWAP/VWAP products where
the venue holds the parent) are outside what this corpus verified. Do not assume one exists, and do not assume a
venue-hosted parent is cancelled by a dead-man switch; read that endpoint's own documentation.

## REQUIRED OUTPUT: the conservation tests

Ship these alongside the parent/child code. The first is the control Knight ¶21 names as missing.

```python
def test_children_never_exceed_parent(algo, venue, clock):
    algo.start(parent_qty=Decimal("1000"), intervals=7, step=Decimal("1"),
               end_time=clock.now() + timedelta(minutes=70))
    for _ in range(20):                                   # run PAST the horizon
        clock.advance(minutes=10); algo.on_tick()
        sent   = sum(o.qty for o in venue.orders(prefix=algo.parent_id))
        filled = sum(f.qty for f in venue.fills(prefix=algo.parent_id))
        assert sent <= Decimal("1000")                    # the emit-path bound
        assert algo.parent.leaves_qty + filled + venue.working_qty(algo.parent_id) == Decimal("1000")
    assert venue.order_count(prefix=algo.parent_id) <= algo.max_children

def test_cancel_stops_the_schedule(algo, venue, clock):
    algo.start(parent_qty=Decimal("1000"), intervals=7, step=Decimal("1"))
    clock.advance(minutes=10); algo.on_tick()
    n = venue.order_count(prefix=algo.parent_id)
    algo.cancel_parent()
    for _ in range(3):                                    # three tick intervals after the cancel
        clock.advance(minutes=10); algo.on_tick()
    assert venue.order_count(prefix=algo.parent_id) == n  # no orphan children
    assert all(o.is_terminal for o in venue.orders(prefix=algo.parent_id))

def test_restart_resumes_from_filled_quantity(algo_cls, store, venue, clock):
    algo = algo_cls(store); algo.start(parent_qty=Decimal("1000"), intervals=7, step=Decimal("1"))
    clock.advance(minutes=20); algo.on_tick(); venue.fill_all(prefix=algo.parent_id)
    revived = algo_cls(store); revived.recover()          # reconcile, THEN mark_ready
    assert revived.parent.leaves_qty == Decimal("1000") - venue.filled_qty(prefix=algo.parent_id)
    assert not revived.can_submit_before_ready()          # the gate, not the query, is the fix
```
