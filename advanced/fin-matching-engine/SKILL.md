---
name: fin-matching-engine
description: >-
  Financial correctness for code that owns an order book and mints the executions others book: durable
  ordered input, deterministic replay, order-state transitions, allocation conservation and residue,
  priority and iceberg refresh, auctions, self-match prevention, checked aggregates, fan-out bounds, halt
  and resume, single-writer recovery. Use when you are the venue; to call one, use fin-exchange-integration.
license: MIT
---

# You own the book

This code **is** the record. It crosses resting orders, mints the execution, assigns the priority and the
sequence, and produces the book everyone downstream treats as fact. Nothing outside can tell you that you
are wrong, so the proof burden moves before deployment, into replay, determinism and conservation. Its usual
pair is `authority: SELF · exposure: record`, and one question governs every change: if this process dies
now, does replaying the persisted inputs reproduce the identical emitted sequence, byte for byte? Where this
file names an invariant in italics, `fin-money-core` states it in full.

## When to use

Your process assigns order-book state no counterparty can independently verify: it matches an aggressing
order against resting orders, allocates a fill across them, decides who holds queue position, computes an
auction or cross price, or issues an identifier other systems consume because you issued it. If you are
wrong, no external statement exists that would show it, so the evidence has to be internal.

Literals that *suggest* this skill and never define it: a loop walking resting orders and decrementing a
resting quantity; `order_book`, `price_level`, `book.bids`, `total_qty`, `leaves_qty` as structures **this
repo owns**; `match`, `uncross`, `allocate`, `pro_rata`, `time_priority`, `iceberg`, `auction`, `imbalance`;
a gate that **rejects** somebody else's order; `ExecID`, match number, a sequence number **you mint**.

## When not to

- The code calls `create_order`, `ccxt`, a venue SDK or a FIX client session, or reconciles against
  somebody else's fills. That is the client side, and `fin-exchange-integration` owns it.
- The change publishes the feed rather than computing the book: packet and message sequencing, snapshot
  and incremental joins, gap detection, A/B arbitration, conflation. `fin-market-data-publication` owns it.
- The same change also writes authoritative balances or postings: `fin-ledger` owns that half. If it is only
  amount arithmetic, rounding direction, operation identity or retry classification: `fin-money-core`.
- The code reads recorded payloads and assigns nothing: a backtest, a volume query that republishes nothing.

A process that is both a venue and a client of another venue is two changes. Split it and report `authority`
and `exposure` per half: a merged declaration inherits the weaker obligation, because the client half has a
reconciliation and that reads as proof for the whole.

## Workflow

1. Split the change into the venue half and the client half; report `authority` and `exposure` for each.
2. Name the authoritative state you assign and the durable inputs it must be reproducible from.
3. Make the input durable and ordered before any authoritative state changes, under exactly one writer.
4. Establish determinism, with identity and sequence assignment inside the deterministic core.
5. Enumerate the legal transitions, refuse the rest, and name the rulebook behind each venue-specific answer.
6. Bound every emission with a counter keyed to the inbound unit and a hard limit checked before the send.
7. Decide halt scope, residue and the fate of in-flight executions, prove replay, then ship the controls.

## Invariants

Cancel/fill races, idempotent cancel, rejecting a cancel on a terminal order, ownership checks and time
priority are **absent on purpose**: they get built correctly without a rule telling anyone to.

### Authoritative state is reproducible from durable, ordered inputs

Specialises *operation identity*: the intent is the inbound command, the first externally visible effect is
the execution everyone else books, and four parts bind rather than any one architecture.

- The inbound command is durable and ordered **before** the state it changes is touched.
- The state change and the obligations it creates commit as one atomic step.
- What you publish is read from the committed record, never from the in-memory vector that produced it.
- Exactly one writer may extend that record, and the resource holding it **rejects** a stale-epoch write
  rather than trusting a displaced writer to have stopped.

One implementation, offered as an example and not the only correct one: append the command to a journal and
flush, mutate the book and insert the executions and outbound rows in one commit, then relay from the
committed rows and bind the send result. A replicated log with quorum acknowledgement, or a consensus group
whose commit point is that log, satisfies the same four parts by another mechanism.

If the emission is the only record, a process that dies mid-send cannot state what it executed. LMAX states
the property as *"the current state of the Business Logic Processor is entirely derivable by processing the
input events"*. Ordering is the half engines get right, durability the half they skip: `let _ = tx.send(ev);`
drops a published execution on the excuse *"it is in-process, it cannot fail"*, and two engines matching one
book for 200 ms produce two irreconcilable histories.

### Determinism is demonstrated by replay, never asserted in a comment

Same inputs, same outputs, same emitted sequence, including the identities you mint. Keep the core free of
wall-clock reads, randomness, I/O and map-iteration-order dependence; assign sequence numbers, `ExecID` and
match numbers **inside** the core so replay reproduces them. A correction is a **new** event referencing the
original identity, never a renumber and never an un-emit. Either a replay test names its seed and
byte-compares the emitted sequence, or the claim of replayability goes.

**Whether a rejected command consumes a sequence number is a protocol decision, not an invariant.** What is
invariant is that the numbering rule is published, total and reproducible: a consumer distinguishes a number
you never assigned from a message it lost only if you told it the convention. Take it from the protocol you
speak, publish it, pin it with a test named for the choice. *"Sequence numbers are consumed on rejects too,
so the event stream has no gaps"* is true of the generator and false of the transport wherever
`let _ = tx.send(ev)` can drop one.

### Only enumerated transitions are legal, and every refusal is explicit

Specialises *authority* seen from the authority's side, and *concurrency on authoritative state*, because
the re-read and the act have to sit in one transaction. Enumerate the legal `(state, event)` pairs and reject
everything else with a typed error; never silently ignore. Never take an inbound message's assertion about
state as the state: re-read the entity from the committed store inside the transaction that acts on it. An
acknowledgement is itself a transition, so once you have told a participant an order is cancelled, executing
it afterwards is a published state being contradicted. Session state is the same discipline, pre-market,
auction, halt and continuous being states rather than flags read opportunistically. NASDAQ, SEC Rel.
34-69655 ¶24 fn 4: cancels *"acknowledged"* *"immediately upon submission"* were nonetheless filled, and
notifying members *"was not discussed"*.

### A venue-specific answer comes from a published rulebook, and the code names the one it implements

Several answers this engine depends on are **not** derivable from first principles. Each is a rule of the
model you chose, published by the venue whose behaviour you reproduce, and each has more than one defensible
answer shipping today. The invariant is not which answer you pick, but that you picked it deliberately, that
it is in your published rules, and that a test named for the choice pins it.

| Question | What is universal | Source of the specific answer |
|---|---|---|
| What price does an execution print at? | one price per execution, identical on both sides, derived from the book state the match consumed | the model. Continuous order-book venues that document it print at the **resting** order's price, so improvement accrues to the aggressor; a call auction prints every execution in the cross at the single uncrossing price; midpoint and periodic models print at neither side's limit |
| Which amendments destroy time priority? | the priority-destroying set is one named constant, applied in one place, and the only writer of the priority key | the rulebook. A quantity increase and a price change destroy priority nearly everywhere; the economically invisible edits, an account-number change among them, differ by venue |
| When does an iceberg slice refresh, and may it match the aggressor that consumed the previous slice? | the refresh happens inside the same deterministic step as the match that consumed the slice, never on a timer | the rulebook. Eligibility within the same aggression is a venue rule and both answers ship |
| How is an auction tie broken once executable volume is maximised? | maximising executable volume is the only universal criterion, and a stated final rule makes the selection **total** | your own filed rule text. The imbalance, side and reference-price ladder is the shape published auction rules take, not a quotation of any venue's rule |
| Both sides of a potential match are the same economic party | the decision is made before any execution is emitted, and a prevented match is a counterfactual, not a fill | the rulebook. Four incompatible semantics ship, with no neutral default |

A convention hard-coded from memory produces a book consistent with itself and at odds with your published
rules. Add a row for any other convention your protocol fixes; the allocation reference sources those above.

### An inbound order is validated against its own instrument's economics

Specialises *hard limits* on the last hop before the book. Reject any order priced more than a configured
band away from **that instrument's own** reference price, and reject or cancel-newest when both sides are
the same economic party. The band derives per instrument, never from a cross-universe aggregate, and the same
derivation runs on every session-state code path. A missing reference price rejects; it never substitutes a
sentinel, and a sentinel is never multiplied. A prevented match is not a trade: record it as a counterfactual,
never as a fill to either side and never as volume. Goldman Sachs, SEC order of 20 August 2013 ¶25 and ¶30:
the pre-market band's upper bound came from the highest closing price of *any* listed option, so a $1 order
in any name passed. CFTC v. Coinbase, March 2021: two internally operated programs *"matched orders with one
another"*, and that volume propagated into third-party indices. Whatever you print becomes someone's price.

### Allocation is not finished until the residue has an owner

Specialises *rounding and conservation*: rounding down cannot distribute everything, so the residue pass is
part of the algorithm and not a follow-up. Pro-rata allocation must never be the last step. Define the
leftover pass, most commonly FIFO by time priority but always the pass your own rules name, and assert
`Σ allocations == min(aggressing quantity, Σ resting quantity)` before any execution is emitted. Do the
arithmetic in integers, multiplying before dividing, and make the tie-break key total, deterministic and
reproducible from the journal, which rules out iterating any map whose order is unspecified. Three quantity
conventions also share one word, an intended total after a cancel, a chain-cumulative total on a replace and
a decrement on a modify; reading one as another moves `leaves` the wrong way long before it surfaces as an
exposure error, and the conversion table is in the allocation reference.

### An aggregate you publish is checked in the build you ship

Specialises *rounding and conservation*. Any quantity that leaves the process as depth, volume or an
aggregate is checked where it is computed, in the binary you deploy, not by an assertion the release build
strips. Treat an underflow or overflow as a conservation breach: halt that transformation at the smallest
scope, do not publish, do not clamp silently. If you saturate rather than check, **emit the saturation**,
because a saturated aggregate with no exception attached is a lie. The rationalisation is *"the delta cannot
exceed the aggregate by construction"*: it was by construction, the drift is the bug you are hunting, and the
check that would have caught it is not in the binary you shipped. `level.total_qty -= qty` on a `u64` guarded
only by a debug assertion wraps to roughly 1.8e19 in a **release** build and is published as depth. Two
answers ship, live-in-release assertions versus saturate-and-emit, and the journaling reference states both
with their build settings. Where a panic would abandon an obligation, saturate and emit; where nothing is in
flight, assert live and crash. A `debug_assert` here is neither of the two.

### A fan-out bound lives on the emit path, keyed to the inbound unit

Specialises *hard limits*, keyed to the inbound unit rather than to a batch. Every transformation turning one
input into many outputs carries a counter keyed to that inbound unit and a hard bound checked **before the
send**, not by a monitor, which is always one interval behind an unbounded loop. Per-item bounds need an
aggregate companion, since per-item limits are satisfiable by an unbounded number of items. On breach: set a
flag the emit path reads before every send, cancel resting orders, disconnect order entry, keep risk,
position and drop-copy alive. Reset authority is independent of the component that tripped, and the reset
records what the cause was found to be; disabling the failing check is never the mitigation. Knight Capital,
SEC Rel. 34-70694 ¶21: no *"control to compare orders leaving SMARS with those that entered it"*; ¶27, it
*"continued to send millions of child orders while its personnel attempted to identify the source"*, and the
remediation re-armed the defect on seven more servers: *"This action worsened the problem."*

### A revalidate-and-recompute loop that consumes less than it receives does not converge

Never compute a price over state that concurrent changes can mutate between compute and print. A loop that
revalidates and recomputes must consume the **entire** pending input queue per pass, or the input set must be
frozen first. Consuming one event per pass is a livelock whenever the arrival rate exceeds one per pass, and
a component livelocked on its queue keeps accepting inputs it cannot process, so its last output is
arbitrarily stale. A retry ceiling converts the hang into an abort; it does not make the loop converge.
Assert input-set freshness at commit, carry that watermark onto the record you print, and never disable a
correctness check to force completion: output produced while one is off is quarantined before it is
authoritative. SEC Rel. 34-69655, NASDAQ and the Facebook IPO, 18 May 2012. ¶20, *"because the system was
designed to perform a separate recalculation for each of those cancellations"*, so *"a loop resulted"*; ¶23
and ¶26, validation lines removed from the failover let an 11:11 input set price an 11:30 cross, and the
multi-million share short came from the cancel imbalance inside that window, not from the removal. ¶65's
remediation is the rule: close the order ports before the calculation, or take bursts of changes *"in one
recalculation … rather than in multiple recalculations"*.

### Halt means quiesce, at the smallest scope that contains the breach

A halt is `engine quiesced ∧ everything already produced delivered or explicitly voided`, at the smallest
scope containing the breach. Severing the transport is not a halt: it abandons in-flight executions the
participants cannot see.

Name the level: (1) reject this operation, typed; (2) freeze one aggregate; (3) fail-closed, no new or
increasing exposure while cancel, close, flatten and settle stay hot; (4) cancel-all and disconnect order
entry while risk, position and drop-copy stay up; (5) quiesce, stopping acceptance **and** production,
draining in-flight and delivering or explicitly voiding everything produced; (6) process abort, legal only
where nothing is in flight. The risk reference maps each level to the fate it leaves obligations in.
Risk-reducing paths (`cancel`, `replace-down`, `close`, `flatten`, `settle`, `reconcile`) stay callable while
halted, gated by a **different** flag than the risk-increasing path, with a test exercising them in the
halted state. Where an invariant can be **momentarily** false during a known intermediate state, name that
state and give the check a bounded self-heal window before escalating. TSE/JPX, 1 October 2020, escalated to
a whole-day halt because participants held undelivered fills and no rule existed for resumption.

## References

A literal from the middle column appears in the code, the repo or the task text → **read that file
immediately and apply it. Do not summarise it.** All three live beside this file, in `references/`.

| File | Read it immediately when the code or task contains | Covers |
|---|---|---|
| [matching-and-allocation.md](references/matching-and-allocation.md) | `pro_rata`, `allocate`, `time_priority`, `iceberg`, `auction`, `uncross`, `cross`, `opening_price`, `imbalance`, `stp` | The allocation step pipeline, rounding and the leftover pass, execution-price conventions, priority preservation and loss, iceberg refresh, self-match prevention, cross computation, the freeze-or-drain contract, allocator property tests |
| [journaling-and-recovery.md](references/journaling-and-recovery.md) | `wal`, `journal`, `outbox`, `replay`, `snapshot`, `recover`, `failover`, `sequencer`, `epoch`, `fencing` | What counts as an input, ordering and flush, the publish check, the deterministic core's banned constructs, identity assignment, the replay harness, snapshots, crash points, single-writer fencing, deterministic simulation, assertion policy |
| [risk-controls-and-halts.md](references/risk-controls-and-halts.md) | `halt`, `resume`, `kill_switch`, `circuit_breaker`, `price_band`, `LULD`, `pre_trade`, `risk_limit`, `bust`, `trade_break` | Pre-trade rejection and its measurement basis, band derivation and sentinel prices, per-item versus aggregate limits, kill-switch latency, halt levels and the fate of obligations, resumption and bust semantics |

## Output

Open with one line, `authority: SELF · exposure: record` being the usual pair, then one entry per finding:

```
FINDING   <the wrong economic outcome, concretely>
WHY       <the mechanism that produces it>
EVIDENCE  <file:line>
FIX       <the change that closes it>
TEST      <the property to assert>
```

Add `VERDICT   SHIP | NO-SHIP: <the unresolved control>` as a final line only for a review or a ship
decision. No findings is one or two sentences saying so and why the change is safe. A claimed control points
at executable code and, where the risk needs it, a named test; an absent one is `UNRESOLVED: <control>
(<why>)`. **This skill also emits a fuller block by default**, because authority is SELF: the journal, the
replay test, the emit bound, the halt gate and the conservation assertions *are* the reconciliation, and
findings alone do not show whether they exist.

```
ENGINE CONTRACT
- Venue half:   authority SELF · exposure record. <crosses resting orders / assigns priority / mints ExecID>
- Client half:  authority EXTERNAL (<venue>) · exposure <own|customer>, reconciled by <key> | none
- Durable in:   <file:line where the inbound command is durable and ordered before the book is touched>
- One writer:   <the fence, and the resource that rejects a stale epoch> | single process, no failover
- Replay test:  <test name that replays the command stream and byte-compares the emitted sequence>
- Publish:      <file:line where the send result is bound and checked>
- Rulebook:     <venue-specific answer this change depends on> = <the answer>, pinned by <test name>
- Emit bound:   <name>=<value> at <file:line>; trip flag reset owner: <component, not the emitter>
- Halt level:   <1-6>; risk-reducing paths gated by <flag> at <file:line>; test: <name>
- Aggregates:   <field> checked at <file:line>; saturation emitted at <file:line> | none saturates
- Conservation: <assertion, such as Σ allocations == min(aggressing qty, Σ leaves)> at <file:line>
```

Fill only the slots this change touches. A slot it touches and cannot fill **is the finding**, reported as
`UNRESOLVED:`; a slot it does not touch is omitted, not left blank. Two cases drop back to findings alone: an
engine deployed only to a sandbox that mints no identifier an outside system consumes, and a read-only path
that creates no obligation. Both become `exposure: record` the day participant funds or an outside consumer
arrive. The internal invariants still obey *reconciliation*: ship them as a scheduled entrypoint running in
production, reading through a path independent of the writer, with an alert destination that has no default.
A process that is also a client of another venue emits both blocks, and §10 of
[journaling-and-recovery.md](references/journaling-and-recovery.md) covers deterministic simulation where
`fin-verification` is not installed.
