# Matching, allocation and auction computation

The allocation pipeline of a venue that owns its book: how an aggressing quantity is split across resting orders, why every
rounding step leaves a residue that must be assigned by a named second pass, what preserves and what destroys time priority, and
how a call auction or cross computes an uncrossing price over an input set that must be frozen or fully drained before the
print. Every rule here assumes you are the matching side, not a participant reading the result.

## Contents

1. **The CME step pipeline** — the ordered allocation steps, what is configurable per product, and how to express the pipeline as data rather than branching code.
2. **Pro-rata arithmetic and the residue** — integer floor division, the conservation assertion that precedes any emitted execution, the minimum-allocation threshold, why pro-rata is never terminal.
3. **The leftover pass** — FIFO by time priority, the one-extra-lot bound, the determinism requirement on the tie-break key.
4. **Execution price is the resting order's** — price improvement accrues to the aggressor; the FIFO exception when aggressing quantity exceeds resting quantity.
5. **Priority preservation matrix** — replace vs modify vs reducing cancel; CME's three priority-destroying edits including the account-number change.
6. **Quantity conventions that share one word** — OUCH Cancel intended-total, OUCH Replace chain-cumulative, ITCH Modify decrement; conversion table and the assertion that catches a mis-read.
7. **Iceberg and reserve orders** — display vs total, refresh arithmetic, requeue at the back, what the feed reveals.
8. **Self-trade prevention as the implementer** — four incompatible semantics, no neutral default, counterfactual reporting, account-family scope.
9. **Auction and cross computation** — the uncrossing algorithm, the tie-break ladder, indicative price and imbalance, the LULD auction collar.
10. **The freeze-or-drain contract** — closing the ports vs draining the queue, the freshness assertion at commit, the Facebook IPO interleaving step by step.
11. **Worked interleavings** — aggressor vs cancel, aggressor vs replace, cross recomputation vs a cancel burst, each with the emitted event sequence.
12. **Property tests for an allocator** — conservation, cap, priority monotonicity, determinism under shuffle, the residue invariant.

---

## 1. The CME step pipeline

CME Globex matching is **a pipeline of allocation steps configured per product**, not one algorithm: "Algorithm steps are
sequenced such that all quantity is allocated by the end of the algorithm process" [CME Globex Matching Algorithm Steps]. The
published step vocabulary is TOP → LMM → Split → FIFO/Pro-Rata → Leveling → FIFO; product algorithm identifiers are drawn from
`{A, C, F, K, O, Q, S, T}`. **The letter→step composition is a venue configuration table, not a fact to hard-code from memory**
— read it from CME's own matching-algorithm page for the product you are cloning. This file asserts the step set and the
identifier set, not the per-letter expansion.

| Step | What it does | Configurable parameter |
|---|---|---|
| TOP | Allocates first to the order that established the current best price | Max lots the TOP order may take |
| LMM | Allocates a fixed percentage to designated lead market makers | LMM percentage, participant list |
| Split | Divides aggressing quantity between a FIFO portion and a Pro-Rata portion | Split percentage |
| Pro-Rata | Proportional to resting quantity, **rounded down** | Pro-Rata Minimum |
| Leveling | Evens out allocations produced by the preceding step | Venue-defined |
| FIFO | Strict time priority; exact, no rounding | none |

Express this as **data**, and assert the terminating property at config load, not at match time:

```python
for instrument, pipeline in PIPELINES.items():   # pipeline loaded from the instrument definition
    assert pipeline and pipeline[-1].exact, (    # "Pro Rata is never the last step of an algorithm"
        f"{instrument}: ends in {pipeline[-1].kind}, which rounds down")
```

`if algo == "PRO_RATA": ... elif algo == "FIFO": ...` cannot express Split or TOP-then-pro-rata and has nowhere to hang that
assertion. It also makes config into code — and the EDGA/EDGX finding is that the engine's behaviour *is* the filed rule
("Technical specifications are not a substitute for exchange rules", SEC Rel. 34-74032), so the product → pipeline map is a
published artefact.

## 2. Pro-rata arithmetic and the residue

"Fractional lots received are rounded **down** prior to allocation" and "**Pro Rata is never the last step of an algorithm due
to the required rounding**; the Pro Rata step will always be followed by either a FIFO step or Leveling and FIFO steps" [CME
Globex Matching Algorithm Steps]. Do the arithmetic in integers, **multiply before dividing**:

```python
alloc_i = (leaves_i * match_qty) // total_leaves       # correct
alloc_i = int(match_qty * (leaves_i / total_leaves))   # WRONG: float division then truncation
```

`leaves_i / total_leaves` is a binary float; the product can land a hair *above* the true value and `int()` returns one lot more
than the exact floor. That lot makes `Σ alloc > match_qty` and over-fills the aggressor. Worked case — aggressing buy 100 lots
against four resting sells at one price, total resting 997:

| Order | Priority | `leaves` | `leaves × 100 // 997` | Residue pass (§3) | Final |
|---|---|---|---|---|---|
| A | t0 | 500 | 50 | +1 | 51 |
| B | t1 | 300 | 30 | — | 30 |
| C | t2 | 120 | 12 | — | 12 |
| D | t3 | 77 | 7 | — | 7 |
| | | **997** | **99** | **1** | **100** |

The residue is bounded by `n − 1` for `n` participants and is zero only when every quotient divides exactly. Assert conservation
in the function that builds the executions, before any is emitted:

```rust
let allocated: u64 = allocations.values().try_fold(0u64, |a, q| a.checked_add(*q))
    .ok_or(EngineError::AllocationOverflow)?;
let target = aggressing_qty.min(total_leaves);   // NOT aggressing_qty alone — see §4
if allocated != target {   // conservation breach: freeze the book, do not publish, do not clamp
    return Err(EngineError::AllocationResidue { allocated, target });
}
```

**Pro-Rata Minimum.** CME's threshold zeroes small allocations, with the published test `{displayed working qty}/{total working
lots} × {match qty} ≥ PR Min`, evaluated against the **original denominator**. Whether the denominator is then recomputed over
surviving participants is a venue decision — **write it into your own rule text**; the sources here establish the threshold
test, not a re-normalisation rule. Either way the zeroed quantity flows into the leftover pass and conservation still has to
hold. The threshold also makes allocation **non-monotone in `match_qty`**: a participant can get zero on a 100-lot aggressor and
a positive allocation on a 101-lot one (§12).

## 3. The leftover pass

Default and most common: **FIFO by time priority**, with the published bound that "no order receiv[es] more than one additional
lot" [Databento, "CME matching algorithms explained", corroborating CME]. Alternatives that ship elsewhere — largest-remainder,
round-robin from a rotating cursor, allocate-to-TOP — are defensible; **none is a default you may pick silently**, because the
choice is observable in the print and is part of your rulebook.

```python
def assign_residue(allocations, resting_in_priority_order, residue):
    for o in resting_in_priority_order:            # a stable, total order — see below
        if residue == 0: break
        if allocations[o.id] < o.leaves:           # never allocate past the order's own size
            allocations[o.id] += 1; residue -= 1
    assert residue == 0, "residue exceeded eligible one-lot slots"
```

The tie-break key must be **total, deterministic and reproducible from the journal**:

| Key component | Source | Why not the obvious alternative |
|---|---|---|
| `price` | the resting order | — |
| `priority_seq` | the sequence number assigned when priority was (re)established | wall-clock nanoseconds tie under batching, and a clock read inside the core makes replay non-reproducible |
| `order_id` | last resort, only if ids are assigned in arrival order | a UUID or hashed id makes allocation depend on the id generator |

Never iterate a `HashMap`/`dict` whose order is unspecified or randomised: Rust's `HashMap` and Python's `set` vary across
processes or seeds, enough for two replicas to allocate the residue to different participants from one input stream. Use a
`BTreeMap`, a sorted `Vec`, or an intrusive queue per level.

## 4. Execution price is the resting order's

"Orders are matched against existing order book orders at the price of the order **on the book**, not at the price of the taker
order" [Coinbase Exchange matching-engine documentation]. Price improvement accrues to the aggressor.

| Case | Book | Incoming | Prints |
|---|---|---|---|
| Buy aggressor improves | sell 200 @ 100.50 | buy 200 @ **101.00** | 200 @ **100.50** — buyer pays 0.50 under its limit |
| Sell aggressor improves | buy 200 @ 100.00 | sell 200 @ **99.00** | 200 @ **100.00** — seller receives 1.00 over its limit |
| Walks two levels | sell 100 @ 100.50, sell 100 @ 100.75 | buy 200 @ 101.00 | **two** prints: 100 @ 100.50 and 100 @ 100.75 |

The third row is the one that gets collapsed: an aggressor walking `k` levels produces `k` executions at `k` prices, and the
average is **derived**, for a client blotter — never a print, never a feed message, never the price on an execution.

**The FIFO exception.** "If there is more quantity aggressing than available (resting), CME Globex uses **FIFO as an exception**
to the algorithm in place" [CME Globex Matching Algorithm Steps]. When `aggressing_qty ≥ Σ leaves`, every resting order fills in
full and the configured algorithm cannot change the outcome — but it is a different code path, and a suite exercising only the
configured algorithm never runs it. Generate `aggressing_qty ∈ {Σ leaves − 1, Σ leaves, Σ leaves + 1}`: the equality case is
where the off-by-one lives, and where a `Σ alloc == aggressing_qty` assertion (rather than `min(...)`) starts firing falsely.

## 5. Priority preservation matrix

| Operation | Priority | Source |
|---|---|---|
| OUCH **Replace** (any) | **Lost — always.** "Replacing an order always gives it a new timestamp for its time priority on the book." | Nasdaq OUCH 5.0 |
| OUCH **Cancel** reducing quantity | **Preserved.** "If you wish you simply partially cancel an order and retain its time priority, send a Cancel Order Message instead." | Nasdaq OUCH 5.0 |
| OUCH **Modify** (type M) | **Preserved** — exists "for modifications that will not affect order priority on the book"; quantity **decrease** only plus a narrow set of sell/short side changes. "Increasing share amount is not allowed and requests to do so will be ignored." | Nasdaq OUCH 5.0 |
| CME modify — quantity **increase** | **Lost** | CME Globex Matching Algorithm Steps |
| CME modify — **price change** | **Lost** | idem |
| CME modify — **account number change** | **Lost** | idem |
| ITCH **Order Replace** (what you publish) | New order reference number; remaining shares of the original no longer accessible; side, symbol and attribution are **not in the message** — the consumer retains them from the original Add | Nasdaq TotalView-ITCH 5.0 |
| System-initiated repricing | Priority may change with no client action; `Order Priority Update` (type T): "as a result of the updated priority, a new order reference number will be assigned" | Nasdaq OUCH 5.0 |

CME verbatim: "A modified order loses its timestamp priority when any of these values are modified: **Increase of working
quantity** of the order. **Change of price**. **Change of account number**."

The account-number change is the trap — economically invisible (same instrument, side, price, size) and it silently costs queue
position. So the priority-destroying set cannot be a chain of `if`s in the amend handler:

```rust
const PRIORITY_DESTROYING: &[EditField] = &[EditField::QtyIncrease, EditField::Price, EditField::Account];
// inside apply_amend, after order.apply_fields(edit)?:
if edit.touched().iter().any(|f| PRIORITY_DESTROYING.contains(f)) {
    reset_priority(order, seq);        // the ONLY writer of order.priority_seq
}
```

Then write the test that a pure account-tag change moves the order to the back of its level. That test is the rule; the table
without it is a comment.

## 6. Quantity conventions that share one word

Three messages from two protocols published by the same vendor use "quantity" for three different things.

| Message | Field | Semantics | Convert to `leaves` |
|---|---|---|---|
| OUCH **Cancel Order** | intended order size | "the maximum number of shares that can be executed **in total** after the cancel is applied… Entering a zero here will cancel any remaining open shares" | `leaves = max(0, intended_total − cum_exec)`; `0` ⇒ `leaves = 0` |
| OUCH **Replace** | Quantity | "Total number of shares liable, **inclusive of previous executions and Self Match Prevention decremented shares** on this order chain" | `leaves = chain_total − cum_exec − stp_decremented` |
| ITCH **Order Cancel** / **Modify** | Cancelled Shares | a **decrement**; multiple Modify messages for one order reference are **cumulative** | `leaves = leaves − decrement` |
| FIX **ExecutionReport** | `OrderQty(38)` | `OrderQty = CumQty + LeavesQty`; `CumQty`/`AvgPx` are **chain-cumulative across replaces** | `LeavesQty(151)` is given; never recompute from submitted qty |

Nasdaq's worked example for Replace: 500 entered, 100 executed; a Replace carrying 500 leaves **400** exposed, a Replace
carrying 600 exposes **500**. The stated rationale: "This may seem a bit confusing at first, but **it inhibits the risk of
double-liability throughout the order/replace chain**." Newtype these — `IntendedTotalQty`, `ChainCumulativeQty`, `DecrementQty`
— so each conversion is a named function and the compiler refuses the mix-up. Then assert on every quantity-bearing inbound,
before the book is touched:

```python
assert 0 <= new_leaves <= chain_total - cum_exec - stp_decremented
assert not (msg.is_cancel and new_leaves > order.leaves), "a Cancel may only reduce"
assert not (new_leaves > order.leaves and order.priority_seq == old_priority_seq), \
       "leaves increased without a priority reset"     # §5
```

The last two catch the convention mis-read — a decrement read as a total, or the reverse — as `leaves` moving the wrong way,
long before it surfaces as an exposure error.

## 7. Iceberg and reserve orders

A display-quantity order rests with `display ≤ total`. Two rules from CME Globex Matching Algorithm Steps: **refresh quantity**
is the lesser of the configured display quantity, the remainder when the remainder is ≤ display quantity, or the remaining
display quantity on a partial fill; and **requeue at the back** — "the Display Quantity order's priority is refreshed to be the
lowest of the remaining orders at the price level (order is placed at the end of the queue)."

1. The refresh happens in the **same deterministic step** as the match that consumed the slice — never a timer, never a
   background task; either produces a book replay cannot reproduce. The refreshed slice takes a **new** `priority_seq` from the
   sequencer that numbered the aggressing command, so replay reproduces the requeue position exactly.
2. **Whether the refreshed slice can match the *same* aggressor that consumed the previous slice is a venue rule, and is not
   established by the sources behind this file.** If it is eligible within the same event, an iceberg fills twice while displayed
   orders behind it get nothing. Decide it, publish it, pin it with a replay test whose name states the choice
   (`iceberg_refresh_not_eligible_within_same_aggression`).

On an ITCH-shaped feed the consumer sees only display shares — "when the number of display shares for an order reaches zero, the
order is dead and should be removed from the book" [Nasdaq TotalView-ITCH 5.0] — so a publisher emitting Delete on exhaustion
and Add on refresh leaks the reserve through a repeating Add/Delete pattern at one price. State in your feed spec that hidden
quantity exists and how a refresh appears.

## 8. Self-trade prevention as the implementer

Four incompatible semantics ship today, with **no neutral default**. Resting R = 500 and incoming I = 300, both account family
X, same price:

| Mode | Resting after | Incoming after | Aggressor continues? |
|---|---|---|---|
| **decrement-both** (Nasdaq AIQ "Decrement both"; Coinbase `dc`, the default) | 200 | 0 | no — I exhausted |
| **cancel-oldest** (AIQ "Cancel oldest"; Coinbase `co`) | cancelled in full (500 removed) | 300 | **yes** |
| **cancel-newest** (Coinbase `cn`) | 500, untouched | cancelled in full | no |
| **cancel-both** (Coinbase `cb`) | cancelled in full | cancelled in full | no |

Coinbase's `dc` has a degenerate case: **equal sizes cancel both orders**. Where the sides carry different instructions,
Coinbase documents that **the taker's instruction takes precedence**. Kalshi requires the field on entry —
`self_trade_prevention_type ∈ {taker_at_cross, maker}` on create-order-v2 — a fifth vocabulary for the same decision. Binance
reports a cumulative `preventedQuantity` separately from executed quantity and ships a `TRANSFER` mode moving prevented quantity
and notional *between accounts sharing a `tradeGroupId`*.

**Report the counterfactual, as a counterfactual.** Nasdaq's AIQ Canceled message carries `Decrement Shares` ("incremental, not
cumulative"), `Quantity prevented from trading` ("Shares that would have executed if the trade would have occurred"), the price
it would have traded at, and the liquidity flag it would have earned — and states when the first two diverge: "For 'Decrement
both' they are always the same. For 'Cancel oldest' they will be different if the incoming order is smaller than the resting
order." Above: decrement-both gives `Decrement Shares = 300`, `prevented = 300`; cancel-oldest gives `Decrement Shares = 500`
and `prevented = 300`.

- **A prevented match is not a trade.** No fill to either side, no `ExecID`, no match number, **excluded from published
  volume**. CFTC v. Coinbase (March 2021, $6.5M): two internally operated programs "matched orders with one another … resulting
  in trades between accounts owned by Coinbase", and that volume propagated into CME's Bitcoin Real Time Index, CoinMarketCap
  and the NYSE Bitcoin Index.
- **Scope is the account family** — not the strategy, the session or the API key. The decision is made **before** any execution
  is emitted for that pair, inside the deterministic step, and prevented quantity enters the remaining-quantity identity (§6:
  OUCH Replace's total is "inclusive of previous executions **and Self Match Prevention decremented shares**"). Decrementing for
  STP without recording `stp_decremented` computes the wrong exposure on the chain's next replace.

## 9. Auction and cross computation

Given a frozen input set (§10):

1. **Candidate prices** = every distinct limit price in the auction book, plus the reference price; market orders count at the
   extremes, not at a price of their own.
2. Per candidate `p`: `cum_buy(p)` = buy interest willing to pay ≥ `p`; `cum_sell(p)` = sell interest willing to accept ≤ `p`;
   `exec(p) = min(cum_buy, cum_sell)`; `imbalance(p) = cum_buy − cum_sell` (signed).
3. **Maximise `exec(p)`** — the only universal criterion.
4. Break ties. **The ladder below is the shape published auction rules take, not a quotation of any venue's rule; the ordering
   is not sourced to a primary document in this repo's research and yours must come from your own filed rule text**: minimum
   `|imbalance(p)|`; then the side of the residual imbalance; then proximity to the reference price; then a stated final rule
   making the selection **total** (e.g. the lower surviving price).
5. Round to tick in a **stated direction** and re-verify `exec` at the rounded price — rounding can move the price off the
   maximising point.
6. Allocate at that single price through the §1–§3 pipeline; §2's conservation assertion applies unchanged, plus **exactly one
   price appears on every execution in the cross**.

Assert before the print: `exec(p*) ≥ exec(p)` for every candidate, and, on the residual book, `best_bid < best_ask`. A crossed
book after an uncrossing is arithmetically impossible and one line to check — NASDAQ's proprietary feed published a crossed
top-of-book for over two hours on 18 May 2012 (SEC 34-69655 ¶31).

**Indicative price and imbalance.** What you publish pre-cross is a promise about the computation: publish indicative price,
indicative volume, imbalance quantity and side, and **assert the print against the last indicative**. NASDAQ's indicative volume
was 82 million against a 75.7 million share print — a 6.3 million share gap "NASDAQ did not address … during the minutes and
hours following the cross" (¶27). Indicative state is resettable state: a CME MDP 3.0 Channel Reset (`35=X`,
`269-MDEntryType=J`) empties book, trade volume, high/low **and the indicative opening price**, and resets `83-RptSeq` to 1 per
instrument. If you publish an IOP, publish its clearing.

**LULD auction collar.** A reopening auction after a LULD pause prices inside a published collar, and Nasdaq TotalView-ITCH 5.0
carries a dedicated **LULD Auction Collar** message so the collar is *delivered*, not derived downstream: publish the reference
price and both bounds. Collar-extension mechanics are venue-specific and **not established** by the research behind this file.
Surrounding LULD state is derived too — Limit State is the NBO **equalling but not crossing** the band, and a Trading Pause
still executes the closing transaction; equals-vs-crosses and wall-clock-vs-market-data seconds are where the off-by-ones live.

## 10. The freeze-or-drain contract

Two designs are correct; consuming one event per pass is neither.

**A — Freeze.** Close the order ports for that security when the calculation is triggered — the remediation NASDAQ agreed to
(SEC Rel. 34-69655 ¶65): "For IPO and Halt Crosses, NASDAQ will **close its order ports to new Cross orders and cancels** of
orders in the security involved in the Cross **after the calculation of the Cross is triggered**." Late arrivals are **rejected
with a typed error**, not silently queued.

**B — Drain.** Take the whole burst in one recomputation. Same paragraph: "For Opening and Closing Crosses, NASDAQ will change
its system to **take into account bursts of changes to orders that would affect the result of the Cross in one recalculation of
the Cross rather than in multiple recalculations**."

```python
# WRONG — the cursor advances by one event per pass (SEC 34-69655 ¶9, ¶20)
while True:
    price, volume = compute(book.snapshot())
    ev = pending.pop_one()                        # ONE cancellation
    if ev is None: break
    book.apply(ev)                                # ...and recompute

# RIGHT — drain to the tail, compute, commit only if nothing arrived meanwhile
while True:
    while (ev := pending.pop()) is not None:      # ENTIRE queue
        book.apply(ev)
    watermark = book.last_applied_seq
    price, volume = compute(book)
    if book.last_applied_seq == watermark:        # freshness at the point of commit
        commit(Cross(price, volume, input_watermark=watermark)); break
```

**A retry ceiling is not the fix** — it would have aborted the Facebook cross, not completed it. The defect is a loop making
strictly less progress than the arrival rate. **Carry the watermark onto the cross record**: the downstream confirmation
component compares *that number*, rather than recomputing a share count from its own view — the mismatch that produced the
two-hour confirmation blackout below.

**The interleaving, step by step** (SEC Rel. 34-69655, paragraphs inline):

| Time | Event | Consequence |
|---|---|---|
| — | Load tested to 40,000 orders (¶12) | members entered **over 496,000** (¶12) |
| 11:05:00 | Cross triggered; load-tested to 40,000 orders, members entered **over 496,000** (¶12) | one calculate-plus-validate pass takes **20 ms** vs a usual **1–2 ms** (¶17) |
| pass 1 | Compute over snapshot `S0`; cancels `c1`, `c2` arrive during the 20 ms | validation fails: an order used in `S0` was cancelled |
| pass 2 | "incorporated only the **first** cancellation received during the first calculation" (¶9) | `c2` still pending ⇒ fails again; "the system was designed to perform a separate recalculation for each of those cancellations… **A loop resulted**" (¶20) |
| 11:05:10→11:30 | Loop continues; the IPO Cross Application falls **19 minutes** behind the live stream (¶26) | it keeps accepting inputs it cannot process, so its last output is arbitrarily stale |
| 11:30:09 | Failover to a duplicate engine with "several lines of code that configured the validation check function" removed (¶23); acknowledged cancels filled anyway, and telling members "**was not discussed by any of the participants**" (¶24 fn 4) | cross prints over the book **as of 11:11**; **38,000+** marketable orders excluded; **30,000+** "stuck" (¶26) |
| — | "more sell shares than buy shares were cancelled during this period" (¶28) | NASDAQ takes a **>3 million share short**, ≈$129M |
| after | Execution App, unaffected by the loop, holds the 11:30:09 view and cannot reconcile against an 11:11 cross | "marked the cross as being in error and **did not disseminate confirmations**" (¶30); crossed quote published (¶31) |

The last row is the one to design against: **a reconciliation whose only action is to withhold output is an availability failure
wearing a correctness check's clothes.** It was right, and it escalated to nobody.

## 11. Worked interleavings

The sequencer establishes one total order over commands; each outcome below follows from *where* the concurrent command landed
in it, not from a race inside the matcher.

**(a) Aggressor versus concurrent cancel.** Resting buy `O1` 200 @ 100 (member M); incoming sell 300 @ 100 (cmd `1001`); M's
cancel of `O1` (cmd `1002`).

| Sequenced order | Emitted event sequence |
|---|---|
| `1002` before `1001` | `5001 CancelAck(O1, leaves=0)` → `5002 OrderAccepted(sell 300)`; the sell rests, no execution. The ack is truthful |
| `1001` before `1002` | `5001 Execution(match=77, O1, 200 @ 100)` → `5002 Execution(match=77, aggressor, 200 @ 100)` → `5003 CancelReject(O1, ORDER_TERMINAL)`. **CancelReject, never CancelAck** — a cancel on a terminal order is rejected |

The forbidden third outcome is `CancelAck` followed by an execution on that order. A design that acks optimistically must be
able to **retract** the ack before the execution is emitted — NASDAQ ¶24 fn 4, where notifying members "was not discussed".

**(b) Aggressor versus concurrent replace**, from the venue side (Nasdaq's own interleaving):

```
in : NewOrder(UserRefNum=7, qty=500)          out: Accepted(UserRefNum=7, qty=500)
in : Replace(orig=7, new=8, qty=500)          -- queued behind the aggressor
                                              out: Executed(UserRefNum=7, 100 @ px, match=91)
                                              out: Replaced(orig=7, new=8, leaves=400)
```

`leaves = chain_total(500) − cum_exec(100) − stp_decremented(0) = 400` (§6). The execution carries the **original**
`UserRefNum`; the replacement id appears only on `Replaced`. An engine attributing the in-flight fill to the replacement version
reports the wrong `leaves` on the next message. Note also that a *malformed* replace against a live order **cancels the
original** in OUCH's model — the failure path of an amend is not "nothing happened".

**(c) Cross recomputation racing a cancel burst**, at 3 cancels per 20 ms pass, consuming one per pass: queue depth at the end
of pass *n* is `2n` — 3, 5, 7, … — strictly increasing, so the loop **never** converges and its computed price ages by one pass
every pass. Draining (§10) makes the end depth 0 by construction, so the loop terminates as soon as one compute window passes
with no arrival — and the freshness assertion is what proves that happened.

## 12. Property tests for an allocator

| Property | Assertion | Bug it catches |
|---|---|---|
| Conservation | `Σ alloc == min(aggressing_qty, Σ leaves)` | floor-and-stop pro-rata (§2); a leftover pass out of slots |
| Cap and sign | `0 ≤ alloc_i ≤ leaves_i` | one-extra-lot loop allocating past an order's size |
| Priority monotonicity | equal `leaves` at one price ⇒ `priority_seq_i < priority_seq_j` implies `alloc_i ≥ alloc_j` | residue assigned to the wrong end of the queue; reversed comparator |
| Determinism under shuffle | shuffling the input list, priority keys preserved, yields an identical allocation map | `HashMap` iteration; dependence on arrival container order |
| Residue bound | `residue < participant_count`, and each participant gains **at most one** extra lot | a residue pass that runs twice; a pro-rata denominator error |
| Price | every execution's price is one of the resting prices consumed | charging the aggressor its own limit (§4) |
| Pipeline termination | `pipeline[-1].exact` for every configured product | a product configured to end in Pro-Rata |
| Replay identity | same command stream + same seed ⇒ byte-identical emitted event sequence | a clock read, RNG or unordered iteration inside the core |

```python
resting = st.lists(st.tuples(st.integers(1, 10_000), st.integers(0, 1_000_000)), min_size=1, max_size=12) \
           .map(lambda xs: [Order(leaves=q, priority_seq=s) for q, s in xs])

@example(orders=[Order(500, 0), Order(300, 1), Order(120, 2), Order(77, 3)], aggr=100)  # §2, pinned
@given(orders=resting, aggr=st.integers(1, 30_000))
@settings(max_examples=2000)
def test_allocator(orders, aggr):
    alloc = allocate(orders, aggr, PIPELINE_PRO_RATA_THEN_FIFO)
    assert sum(alloc.values()) == min(aggr, sum(o.leaves for o in orders))
    assert all(0 <= alloc[o.id] <= o.leaves for o in orders)
    shuffled = orders[:]; random.Random(0).shuffle(shuffled)
    assert allocate(shuffled, aggr, PIPELINE_PRO_RATA_THEN_FIFO) == alloc
    by_prio = sorted(orders, key=lambda o: o.priority_seq)
    for a, b in zip(by_prio, by_prio[1:]):
        if a.leaves == b.leaves:
            assert alloc[a.id] >= alloc[b.id]
```

Two Hypothesis behaviours to plan around: `derandomize` **auto-enables in CI**, so CI runs the same examples forever and
exploration must happen in a separate long-running randomized job; and the example database is a local `.hypothesis/examples`
directory CI discards, so a counterexample survives only if committed as an `@example(...)` — which is why the §2 case is pinned
above.

The generator is the binding constraint, not the assertions. Assert coverage *on the generator*: some fraction of cases hitting
`aggr > Σ leaves` (§4), some producing a nonzero residue, some placing a participant exactly on the Pro-Rata Minimum boundary,
some with two orders of equal `leaves` and adjacent `priority_seq`. TigerBeetle shipped a query bug because its fuzzer always
generated objects consecutive in the index; an allocator fuzzer whose quantities always sum exactly to the aggressor never runs
the residue pass at all.
