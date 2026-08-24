---
name: fin-matching-and-settlement
description: Use when the code IS the venue, not its client: matching an order against resting orders, order_book/price_level/total_qty you own, pro-rata allocation, an auction or cross, self-trade prevention, price bands, LULD, halt/resume, a feed you publish (RptSeq, MoldUDP64, ITCH, snapshot vs incremental), netting, DVP, settlement, liquidation, ADL, reportPayouts. Skip when calling someone else's venue: fin-exchange-integration.
license: MIT
---

# You are the venue

This skill is for code that **is** the record: a matching engine, a book you own, an allocation or auction
algorithm, a feed you publish, a clearing or settlement batch, a liquidation engine, an internal crossing
network, a prediction-market resolver. The defining property is that **no external party can confirm your
state**. You are what everyone else reconciles *against*, so reconciliation is structurally unavailable as a
safety net and the proof burden moves *before* deployment, into replay and simulation. That is why this is a
separate skill from `fin-exchange-integration`. The question every change here must answer: **if this process
dies now, does replaying the persisted inputs reproduce the identical emitted event sequence, byte for byte?**

## When this applies

Any of these in the diff, the repo, or the task text:

- a loop that walks resting orders and decrements a resting quantity; `order_book`, `OrderBook`, `price_level`,
  `PriceLevel`, `book.bids`/`book.asks`, `total_qty`, `leaves_qty` as structures **this repo owns**
- `match`, `uncross`, `cross`, `allocate`, `pro_rata`, `time_priority`, `iceberg`, `auction`, `opening_price`
- publishing a feed: `seq_num`, `RptSeq`, `MDIncrementalRefresh`, MoldUDP64, ITCH, OUCH, SBE, gap-fill, A/B feeds
- `halt`, `resume`, `LULD`, `price_band`, `kill_switch`, `circuit_breaker`, a gate that **rejects** others' orders
- `netting`, `net_position`, `DVP`, `settlement_batch`, `finality`, a `mark_price` **you compute**, `liquidate`,
  `insurance_fund`, `ADL`, `reportPayouts`, `payoutNumerators`, complete sets
- an identifier other systems consume because you assigned it: `ExecID`, match number, trade id, sequence number

**Not this skill** when the code calls `create_order`, ccxt, a venue SDK or a FIX *client* session, or
reconciles against somebody else's fills. That is `fin-exchange-integration`. If it is both, split it below.

## Split the diff before you tier it (SEAM S4)

*The reciprocal of this rule is in `fin-exchange-integration`, stated from the venue-client side.*

**A client reconciles against the venue; a venue has nothing to reconcile against.** Where one process is both
(a broker OMS that is the system of record for its clients' orders and simultaneously a client of the
exchange), **split the diff**: the client half is T2 and requires reconciliation by client order ID; the venue
half is T3 and requires order-by-order rejection on the last hop, a deterministic core, and deterministic
simulation. Do not let one tier declaration cover both halves. Knight Capital sat exactly on this boundary.

## The non-negotiables

Cancel/fill races, cancel-remaining-quantity, idempotent cancel, rejecting a cancel on a terminal order,
ownership checks and time priority are **absent from this file on purpose**. They are the well-known part of
the problem and they get built correctly without a rule telling you to. What follows is the part that does not.

### Journal the input, commit with the mutation, check the publish

**The book mutation and the durable record of the resulting execution commit together, and the outbound
publish reads from that durable record.** **Journal the INPUT command before matching**, so replay reproduces
the same executions; never treat the in-memory emit as the record. A crash between the state change and the
emit must replay to the identical event, byte for byte.

The write path IS these steps, in this order:

1. Append the inbound command to the journal and flush it, **before** the book is touched.
2. Match. Mutate the book and insert the resulting executions in the **same** transaction / WAL entry.
3. Commit.
4. A publisher reads *committed* executions and sends them. **Check the send result.**
   `let _ = tx.send(ev);` discards a closed-channel or full-queue error and silently drops a published
   execution, which no downstream consumer can detect. Bind the result; on error, halt that transformation.

LMAX: *"the current state of the Business Logic Processor is entirely derivable by processing the input
events"*. The journaler stores all input events durably, and a production bug is diagnosed by replaying the
event sequence on a dev machine. Ordering is the half engines get right; durability is the half they skip: no
WAL, no journal, no outbox. The two rationalisations are *"it's an in-process channel, it cannot fail"* and
*"the WAL is a later milestone"*; both leave a venue unable to state what it executed.

### Sequence the matching side and persist the command stream

**Sequence the matching side, not only the cancel side, and persist the inbound command stream.** Treat
"deterministic and replayable" as a property you must be able to demonstrate **by replaying**, not a claim in
a comment. Every emitted event carries a gap-free sequence number, consumed on rejects as well as accepts, and
the sequence generator lives **inside the deterministic core** alongside ExecID / match-number assignment, so
replay reproduces both identically. Keep the decision core free of wall-clock reads, RNG, I/O and
map-iteration-order dependence; inject time and randomness as explicit parameters.

A cancel that races an execution must not renumber or un-emit a published execution; corrections travel as a
Trade Cancel referencing the original match number. **An acknowledged cancel must be honoured, or the
acknowledgement must be retracted to the counterparty before the order is executed**. NASDAQ ¶24 fn 4: cancels
*"acknowledged"* *"immediately upon submission"* were nonetheless filled, and notifying members *"was not
discussed"*.

This is where a comment claim most often outruns the code in venue implementations. Cancel-side sequencing is
the part that gets built; the matching side and the persisted command stream are the parts that do not, while
the design note still asserts *"Sequence numbers are consumed on rejects too, so the event stream has no gaps"*
in a design where `let _ = tx.send(ev)` can drop an event. Either a replay test exists that names its seed and
byte-compares the emitted sequence, or the sentence claiming replayability is deleted.

### Checked arithmetic on anything you publish

**Aggregates you publish use checked arithmetic on the emit path, not `debug_assert`.**
`level.total_qty -= qty` on a `u64` guarded only by a debug assertion wraps to ~1.8e19 in a **release** build
and is published as depth to consumers who trade against it. Use `checked_sub().ok_or(...)` (or the language's
equivalent) and treat an underflow as a **fan-out conservation breach**: halt that transformation at the
smallest scope, do not publish, and do not clamp to zero. **If you saturate rather than check, you must emit
the saturation**. A saturated aggregate is a lie with no exception attached.

The rationalisation is *"`qty ≤ level.total_qty` by construction"*. It was by construction; the drift is the
bug you are hunting, and the debug assertion that would have caught it is not in the binary you shipped. In
hand-rolled books the drift is commonly recognised and then answered with a debug-only check.

### Two shipped answers on assertions in production

Both answers ship today in the same problem domain.

- **TigerBeetle keeps assertions live in release** (Zig `ReleaseSafe`), ~1 assertion per 10.6 lines of state
  machine, overflow-checking every accumulator before mutation (`sum_overflows`). Its rationale: *"Assertions
  detect programmer errors… The only correct way to handle corrupt code is to crash. Assertions downgrade
  catastrophic correctness bugs into liveness bugs."* And: *"assert the positive space that you do expect AND
  the negative space that you do not expect."*
- **nautilus_trader uses `saturating_add`/`saturating_sub`** on quantities (`orders/mod.rs:1270`, `:1366`),
  correct where a panic would leave exposure unmanaged, and its invariants are `debug_assert!` with
  `debug-assertions = false` in **both** `[profile.dev]` and `[profile.release]`, so they are compiled out
  everywhere.

Choose per path and write the choice down. **Where a panic would abandon an obligation, saturate and emit the
saturation event. Where nothing is in flight, assert live and crash.** A `debug_assert` on a published
aggregate is neither of the two.

### A bounded transformation carries a counter and a hard bound, on the emit path

**Every bounded transformation carries a counter keyed to its inbound unit** (parent order id, instruction id,
batch id) **and a hard bound** (`max_children_per_parent`, `max_notional_per_parent`, `max_shares_vs_ADV`,
`max_messages_out_per_message_in`), **checked ON THE EMIT PATH BEFORE THE SEND**, not by a monitor reading a
metric. On breach: set a flag the emit path reads before every subsequent send, cancel resting orders, and
disconnect the order-entry session, while risk, position and drop-copy stay alive. **The flag must not be
resettable by the component that tripped it. Never disable the failing check as the mitigation.** A
suspense/error account is single-purpose and linked to an automated firm-aggregate limit that **rejects new
orders** on breach.

Knight ¶21: no *"control to compare orders leaving SMARS with those that entered it"* and *"no procedures in
place to halt SMARS's operations in response to its own aberrant activity"*. ¶17, $460M in 45 minutes; ¶27,
*"continued to send millions of child orders while its personnel attempted to identify the source"*, and the
remediation re-armed the defect on seven more servers: *"This action worsened the problem."* Goldman's
rate-based circuit breakers worked and personnel *"repeatedly lifted the circuit breakers blocks between 8:44
a.m. and 9:32 a.m."* without their own policy's authorisation, still investigating. A kill switch resets on a
named authorising role plus a recorded root-cause determination, never on a bare "resume".

### Validate the inbound order economically

**Reject any order whose price is more than a configured band away from that instrument's own reference
price**, and **reject or cancel-newest on a self-match**. The band is derived **per instrument from that
instrument's own reference price**, never from a cross-universe aggregate, and the same derivation applies on
every session-state code path (pre-market, auction, halt, continuous). Self-match prevention is applied at the
**account-family** level, not per strategy, and self-matched prints are filtered out of published volume. A
book without both prints the fat-finger trade and then owes everyone a bust process.

Goldman ¶25/¶30: the pre-market band's upper bound was 1.5× the highest closing price of *any* listed option
($3,090), so a $1 order in any name passed. CFTC v. Coinbase, March 2021, $6.5M: two internally operated
programs *"matched orders with one another … resulting in trades between accounts owned by Coinbase"*, and that
volume propagated into CME's Bitcoin Real Time Index, CoinMarketCap and the NYSE Bitcoin Index. **A prevented
match is not a trade**. Report it as a counterfactual (`Quantity prevented from trading`), never as a fill to
either side and never as volume. 15c3-5(c)(1)(ii) binds these as pre-trade rejections on the last hop,
order-by-order, measured from **orders entered** rather than executions received.

## Halt means quiesce, at the smallest scope

`halt ⇒ engine quiesced ∧ everything already produced delivered or explicitly voided`, at the smallest scope
that contains the breach. **Severing the transport is not a halt**. It abandons in-flight executions the
participants cannot see. Name which level you mean:

| # | Level | Existing obligations | Use for |
|---|---|---|---|
| 1 | Reject this operation, typed | untouched | a bad inbound order |
| 2 | Freeze one aggregate (symbol, account) | untouched | one book's invariant |
| 3 | Fail-closed: no new or increasing exposure; cancel / close / flatten / settle stay hot | actively managed | open exposure at detection |
| 4 | Cancel-all + disconnect order entry; risk, position, drop-copy stay up | actively managed | a fan-out bound breached |
| 5 | Quiesce: stop accepting **and** stop producing, drain in-flight, deliver or explicitly void everything already produced | drained, then frozen | venue-level halt |
| 6 | Process abort | **abandoned** | only where nothing is in flight |

Risk-reducing paths (`cancel`, `replace-down`, `close`, `flatten`, `settle`, `reconcile`) stay callable while
halted and are gated by a **different flag** than the risk-increasing path, with a test that exercises them in
the halted state. Where an invariant can be **momentarily** false during a known intermediate state, name that
state and give the check a bounded self-heal window before escalating; a check that fires on a legitimately
intermediate state is itself an availability bug. Confine exceptions to tasks; never abort a process holding
unmanaged obligations.

TSE/JPX, 1 October 2020, escalated to a whole-day halt because participants held undelivered fills and no rule
existed for post-halt resumption. LULD's Trading Pause still executes the closing transaction, and LULD state
is derived, not delivered: Limit State is the NBO *equalling but not crossing* the band, so equals vs crosses
and wall-clock vs market-data seconds are where the off-by-ones live.

## Auctions, and any revalidate-and-recompute loop

**Never compute an auction or cross price over a book that concurrent cancels can mutate between compute and
print.** A revalidate-and-recompute loop must consume the **ENTIRE** pending event queue per pass, or the input
set must be frozen before computation begins. Consuming one event per pass is a livelock whenever the arrival
rate exceeds one event per pass. A component livelocked on its input queue keeps accepting inputs it cannot
process, so its last computed output is arbitrarily stale. **Assert the freshness of a computation's input set
at the point of commit.**

**Never disable a correctness check to force completion during an incident.** If a check is disabled, every
output produced afterwards is quarantined and reconciled before it is treated as authoritative. A
reconciliation failure raises an owned, escalating alert; withholding output is not a response.

SEC 34-69655, NASDAQ / Facebook IPO, 18 May 2012: ¶19, *"only the first of those two cancellations was
incorporated into a third price/volume calculation"*, ¶20, *"because the system was designed to perform a
separate recalculation for each of those cancellations"*; ¶23/¶26, validation lines removed from the failover
let an 11:11 input set price an 11:30 cross, and the >3 million share short position came from the **cancel
imbalance inside that 19-minute window**, not from the removal itself; ¶30, the Execution App then correctly
refused to emit and nobody learned why for over two hours. ¶65's remediation: close the order ports before the
calculation, or take bursts of changes *"in one recalculation … rather than in multiple recalculations"*.
**A retry ceiling is not the fix**. It would have aborted the cross, not completed it. The defect is a loop
making strictly less progress than the arrival rate.

## Allocation, and the residue

**Pro-rata allocation rounds down, therefore cannot allocate everything, and therefore must never be the last
step**: define the leftover pass (FIFO by time priority, or the venue's documented rule) and assert
`Σ allocations == aggressing quantity` before any execution is emitted. **Execution prints at the RESTING
order's price, not the aggressor's.** Where aggressing quantity exceeds total resting quantity, the FIFO
exception applies. Three quantity conventions share one word (OUCH Cancel's intended total, OUCH Replace's
chain-cumulative total, ITCH Modify's decrement), and the conversion table is in the allocation reference.

## Publishing market data

- **Sequence per message, not per packet.** A MoldUDP64 packet header carries the sequence of the *first*
  message and the rest are implicit, so publish the message count and treat `count == 0` (heartbeat) and
  `0xFFFF` (end of session) as sequence-carrying control packets.
- **Per-instrument sequences are not monotonic across a reset.** CME MDP 3.0 Channel Reset (`35=X`,
  `269-MDEntryType=J` Empty Book) resets `83-RptSeq` to **1 per instrument**, empties book, volume, high/low
  and the indicative opening price, deletes the channel's recovery snapshots, and does **not** resend
  settlement prices. Publish the reset explicitly; never make a consumer infer it from a sequence going back.
- **Snapshot and incremental need a stated join key and direction.** State which incremental sequence each
  snapshot is valid as-of, and that all updates in the latest incremental must be processed before the book is
  valid; on a gap, **all** books may be wrong, so say so in the spec you publish.
- **A gap is declared after A/B arbitration, never before.** The same packet arrives first on either feed
  independently of you; a sequence gap means the packet was lost on **both**. One retransmission request does
  not necessarily close a gap. A response truncates at the messages that completely fit.
- **Two filters, one feed.** Non-printable executions are excluded from **volume** to prevent double-counting
  with the later bulk print; Trade messages are excluded from the **book**. Publish the flag; document which
  downstream use takes which filter.
- **Fairness is architectural, not intentional.** Reg NMS Rule 603(a) prohibits releasing quote and trade data
  on a proprietary feed before sending it for consolidation. NYSE breached it because its proprietary path was
  faster (disparities *"from single-digit milliseconds to, on occasion, multiple seconds"*), and could not
  prove compliance because it had not retained the transmission-timing files. Retain them.

## Netting, DVP and finality

Assert these per cycle `C`, per currency `X`, before emitting any settlement instruction:

- `Σ_p net(p, C, X) == 0`. Nonzero means the cycle created or destroyed money in `X`.
- Every gross obligation belongs to **exactly one** cycle; double inclusion double-settles, omission drops it.
- `Σ_p |net(p)| ≤ Σ_g amount(g)`. Netting never *increases* the amount to be moved; a violation is a sign error.
- Net **per currency**: netting EUR against USD requires an explicit FX obligation with a recorded rate.
- Recomputing from the same input set yields identical nets. If it depends on iteration order, float or the
  wall clock, it is not a netting algorithm.
- `Σ gross == Σ net + Σ offsets`, with `offsets` materialised rather than implied; and once the cycle settles,
  `posted(p, C, X) == net(p, C, X)` exactly.

After netting, **only a net claim can be demanded or a net obligation be owed** (SFD Art. 2(k)). A collections,
dunning or liquidity-forecast job still reading the gross ledger double-counts the participant. After novation
the original A↔B obligation **no longer exists**: exposure, margin, netting sets and default management key on
the CCP. Open offer novates at execution, classic novation at clearing acceptance, and the gap between them is
a real bilateral window.

**DVP is not "both legs at the same instant"; it is "neither leg is final unless both are"**. PFMI Principle
12: *"the final settlement of one obligation occurs if and only if the final settlement of the linked
obligation also occurs, regardless of whether the FMI settles on a gross or net basis and when finality
occurs."* A two-phase commit over *provisional* postings satisfies the timing intuition and not the iff, and
the iff is what eliminates principal risk. **Final settlement is a legally defined moment** fixed by the
system's rules and the applicable insolvency law: your code records which side of it each instruction is on, it
does not invent one. A system that books late and back-dates the value date has not achieved value-date
finality; it has produced a record that looks like it did.

## Liquidation, marks and the backstop

- **Mark price, index price and last price are three numbers with three jobs.** A thin book, or a wick on one
  venue, moves last price; it must not move the number the liquidation engine uses. State in the code which
  price each calculation reads.
- **The mark you publish is an input an adversary can pay to move.** Size risk limits so that the cost of
  moving the mark exceeds the payoff of the position it liquidates. Hyperliquid's JELLY market, 26 March 2025,
  is the worked example: self-trading against a thin oracle forced the HLP backstop vault into a large short.
- **A backstop that can be forced to inherit a position is the attack target, not a safety net.** Insurance-fund
  draw, socialised loss and ADL are distinct waterfall steps: name the step, publish it with the inputs that
  selected it, and remember the deleveraged counterparty has no independent way to check your work.
- A liquidation is an execution: same journal, same sequence numbers, same checked aggregates and same publish
  check as any other match, not a side effect of a risk loop.

## Resolution and complete sets

- **Model resolution as a payout vector over outcome slots with a denominator**, persisting
  `(numerators[], denominator)`, never as a winning-outcome enum or a `bool won`. The type must be able to
  represent `[1,1]/2` (a 50/50) and `[3,1]/4`.
- **Credit only on the venue's explicit finality signal**: `payoutDenominator[conditionId] > 0` on the Gnosis
  CTF, `status == finalized` on Kalshi. `closed`, `determined`, `disputed`, `amended` and a proposed oracle
  price are all not-final, and Kalshi's `amended` **restarts the settlement timer**.
- **CTF resolution is one-shot.** `reportPayouts` requires `payoutDenominator[conditionId] == 0`; there is no
  re-report, no correction and no admin override. Build the reversal path *above* it (a new condition, or an
  off-ledger make-whole) rather than planning to re-report.
- **Complete-set conservation is a floor-versus-round trap.** The chain computes `floor(stake × num / den)`
  **per position and then sums**; a mirror ledger computing the same quotient with round-half-up credits more
  than the chain pays, invisibly until the first non-trivial payout vector. On a `[1,1]` resolution, redeeming
  `x` YES and `x` NO returns `2·floor(x/2)` while merging first returns `x` exactly, and the residue stays in
  the contract permanently; there is no sweep. Merge complete sets before redeeming whenever the payout vector
  is not `[1,0]`-shaped, and assert `collateral_held ≥ Σ_i supply_i × num_i / den` continuously.

## Required output: the VENUE CONTRACT block

Every response that changes engine, feed, allocation, settlement or resolution code ends with this block,
filled in, immediately before the NAMED RISKS table (G2). A slot you cannot fill is the finding.

```
VENUE CONTRACT
- Venue half:  T3 — <what makes you the record: assigns ExecID / crosses resting orders / mints the payout>
- Client half: T2 — <the venue you are also a client of, and the reconciliation key> | none
- Journal:     <file:line where the inbound command is committed before the book is touched>
- Replay test: <test name that replays the command stream and byte-compares the emitted event sequence>
- Publish:     <file:line where the send result is bound and checked>
- Emit bound:  <name>=<value> at <file:line>; trip flag reset owner: <component, not the emitter>
- Halt level:  <1-6>; risk-reducing paths gated by <flag> at <file:line>; test: <name>
- Published aggregates: <field> checked at <file:line>; saturation emitted at <file:line> | none saturates
```

## References

A literal from the middle column appears in the code, the repo or the task text → **read that file
immediately and apply it in order. Do not summarise it.**

| File | Read it immediately when the code or task contains | Covers |
|---|---|---|
| [matching-and-allocation.md](references/matching-and-allocation.md) | `pro_rata`, `allocate`, `time_priority`, `iceberg`, `auction`, `uncross`, `cross`, `opening_price`, `imbalance` | CME allocation pipeline, rounding and the leftover pass, priority preservation, iceberg refresh, cross computation and the freeze/recompute contract |
| [market-data-publication.md](references/market-data-publication.md) | `RptSeq`, `seq_num`, MoldUDP64, ITCH, SBE, `MDIncrementalRefresh`, `snapshot`, `retransmit`, `gap` | Per-message sequencing, channel reset, snapshot/incremental joins, A/B arbitration, printable flags, 603(a) timing evidence |
| [journaling-and-recovery.md](references/journaling-and-recovery.md) | `wal`, `journal`, `outbox`, `replay`, `snapshot`, `recover`, `failover`, `sequencer`, `epoch`, `fencing` | Input journaling and flush ordering, replay harness, banned constructs in the core, snapshot/recovery, single-writer failover, deterministic simulation |
| [risk-halts-and-settlement.md](references/risk-halts-and-settlement.md) | `halt`, `LULD`, `price_band`, `kill_switch`, `15c3-5`, `netting`, `DVP`, `finality`, `liquidate`, `ADL`, `reportPayouts` | 15c3-5 clause by clause with Knight and Goldman, band derivation, halt/resume state, netting invariants, DVP models, waterfalls, resolution |
