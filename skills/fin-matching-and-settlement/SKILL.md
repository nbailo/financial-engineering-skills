---
name: fin-matching-and-settlement
description: >-
  Financial correctness for systems that are themselves the venue: matching against resting
  orders, pro-rata allocation, auctions, self-trade prevention, price bands and halts,
  market-data publication, netting, settlement and liquidation. Use when your code assigns the
  authoritative trade, book or payout others rely on, with no external oracle. When calling
  someone else's venue, use fin-exchange-integration.
license: MIT
---

# You are the venue

This code **is** the record: it assigns the trade, the book, the sequence, the net or the payout that
everyone else treats as fact. No outside party can confirm your state, so reconciliation against an external
authority is structurally unavailable and the proof burden moves before deployment, into replay and
simulation. When the change instead calls a venue you do not operate, that half is
`fin-exchange-integration`. One question governs every change here: if this process dies now, does replaying
the persisted inputs reproduce the identical emitted event sequence, byte for byte?

## Workflow

1. Split the change: which half makes you the record, and which half makes you a client of someone else. Tier
   and review the halves separately.
2. Name the authoritative state you assign (execution, book, sequence, net, mark, payout) and the durable
   inputs it must be reproducible from.
3. Make the input durable and ordered before any authoritative state changes.
4. Establish determinism: the same inputs produce the same outputs and the same published sequence, with
   identity and sequence assignment inside the deterministic core.
5. Enumerate the legal state transitions and reject everything else with an explicit, typed refusal.
6. Bound every transformation and every emission with a counter keyed to the inbound unit and a hard limit
   checked on the emit path, before the send.
7. Decide halt scope, allocation residue, and what the published feed is allowed to say.
8. Prove replay reconstructs the state, load only the references this change needs, and implement the
   controls and their tests before declaring the path complete.

## When this applies

Your process assigns state that no counterparty can independently verify: it crosses resting orders and mints
the execution, computes an auction or cross price, publishes the feed that is the market's view of the book,
decides a mark, a net, a liquidation or a payout, or issues an identifier other systems consume because you
issued it. The structural test: if you are wrong, no external statement exists that would show it. That is why
the evidence has to be internal.

Literals that *suggest* this skill, and never define it: a loop that walks resting orders and decrements a
resting quantity; `order_book`, `OrderBook`, `price_level`, `PriceLevel`, `book.bids`/`book.asks`,
`total_qty`, `leaves_qty` as structures **this repo owns**; `match`, `uncross`, `cross`, `allocate`,
`pro_rata`, `time_priority`, `iceberg`, `auction`, `opening_price`; feed publication (`seq_num`, `RptSeq`,
`MDIncrementalRefresh`, MoldUDP64, ITCH, OUCH, SBE, gap-fill, A/B feeds); `halt`, `resume`, `LULD`,
`price_band`, `kill_switch`, `circuit_breaker`, a gate that **rejects** others' orders; `netting`,
`net_position`, `DVP`, `settlement_batch`, `finality`, a `mark_price` **you compute**, `liquidate`,
`insurance_fund`, `ADL`, and `reportPayouts`, `payoutNumerators` or complete sets for a resolution **you assign**;
`ExecID`, match number, trade id, sequence number. Rename all of them and every rule below still fires.

**Not this skill** when the code calls `create_order`, `ccxt`, a venue SDK or a FIX client session, or reconciles
against somebody else's fills: that is `fin-exchange-integration`, and so is trading a prediction market somebody
else resolves. If the same change also writes authoritative balances or postings, load `fin-ledger` alongside this
file. If it is both venue and client, split it.

## Seam S4: venue and client, split the change before you tier it

*The reciprocal of this rule lives in `fin-exchange-integration`, stated from the venue-client side.*

A client reconciles against the venue; a venue has nothing to reconcile against. Where one process is both (a
broker OMS that is the system of record for its clients' orders and simultaneously a client of the exchange),
split the change: the client half is T2 and requires reconciliation by client order identity; the venue half
is T3 and requires order-by-order rejection on the last hop, a deterministic core, and deterministic
simulation. Do not let one tier declaration cover both halves. Knight Capital sat exactly on this boundary.

## Core rules

Cancel/fill races, cancel-remaining-quantity, idempotent cancel, rejecting a cancel on a terminal order,
ownership checks and time priority are **absent from this file on purpose**. They are the well-known part of
the problem and they get built correctly without a rule telling you to. What follows is the part that does not.

### Authoritative state is reproducible from durable, ordered inputs

The state you assign is only as recoverable as the inputs that produced it. Record the inbound command
durably and in order before the state it changes is touched, commit the state change and the obligations it
creates in one atomic step, and publish from the committed record rather than from memory. This specialises
*durable intent before the external effect*: at a venue the intent is the inbound command, and the external
effect is the execution everyone else books.

**Shape**

```
append inbound command to a durable ordered log, flush
  -> transform: mutate state and insert the resulting obligations in ONE commit
  -> publisher reads COMMITTED obligations
  -> send, bind the result, check it
```

A crash between the state change and the emission must replay to the identical event. If the emission is the
only record, a process that dies mid-send cannot state what it executed: either downstream holds an execution
the venue has no row for, or the venue holds a row nobody was told about. Discarding the send result is the
same hole with no crash required, and no consumer can detect it.

**How it appears:** LMAX, *"the current state of the Business Logic Processor is entirely derivable by
processing the input events"*; the journaler stores all input events durably, and a production bug is
diagnosed by replaying the event sequence on a dev machine. Ordering is the half engines get right;
durability is the half they skip: no WAL, no journal, no outbox. In Rust, `let _ = tx.send(ev);` discards a
closed-channel or full-queue error and silently drops a published execution. The two rationalisations are
*"it is an in-process channel, it cannot fail"* and *"the WAL is a later milestone"*; both leave a venue
unable to state what it executed.

### Determinism is demonstrated by replay, never asserted in a comment

Same inputs, same outputs, same published sequence, including the identities you mint. Every emitted event
carries a gap-free sequence number, consumed on rejects as well as accepts, and the sequence generator lives
**inside** the deterministic core alongside identity assignment, so replay reproduces both. Keep the core free
of wall-clock reads, randomness, I/O and map-iteration-order dependence; inject time and randomness as
explicit parameters. A design note claiming replayability with no test behind it is *a comment is a claim*:
name the test and its seed, or delete the sentence.

**Shape**

```
inputs -> deterministic core (assign identity, assign sequence) -> emitted sequence
replay(same inputs) == recorded emitted sequence, byte for byte
correction = a NEW event referencing the original identity, never a renumber or an un-emit
```

Sequencing the cancel side is the part that gets built. The matching side and the persisted command stream
are the parts that do not, so a race between a cancel and an execution ends up renumbering or un-emitting
something a participant already saw.

**How it appears:** identities other systems consume because you assigned them (`ExecID`, match number, trade
id, sequence number) are minted in the core, not by a service call. A published execution is corrected by a
Trade Cancel referencing the original match number. The design note to distrust reads *"Sequence numbers are
consumed on rejects too, so the event stream has no gaps"* in a codebase where `let _ = tx.send(ev)` can drop
one. Either a replay test exists that names its seed and byte-compares the emitted sequence, or the claim goes.

### Only enumerated transitions are legal, and every refusal is explicit

Enumerate the legal `(state, event)` pairs and reject everything else with a typed error; never silently
ignore. You are the authority, so never take an inbound message's assertion about state as the state:
re-read the entity from the committed store inside the transaction that acts on it. This is *arrival order is
not occurrence order* seen from the authority's side. You control the order, which is exactly why the order
you assign is the one you have to enforce.

**Shape**

```
event arrives -> re-read entity from the committed store
              -> (state, event) in the legal set ? apply : reject with a typed error
terminal state accepts only the corrections the rules define, never a status message
```

An acknowledgement is itself a transition. Once you have told a participant an order is cancelled, executing
it afterwards is not a race you lost; it is a published state being contradicted.

**How it appears:** an acknowledged cancel must be honoured, or the acknowledgement must be retracted to the
counterparty before the order executes. NASDAQ ¶24 fn 4: cancels *"acknowledged"* *"immediately upon
submission"* were nonetheless filled, and notifying members *"was not discussed"*. The same discipline governs
session state (pre-market, auction, halt, continuous are states, not flags to read opportunistically) and
resolution finality, below.

### An aggregate you publish is checked in the build you ship

Any quantity that leaves the process as depth, volume, a net or a mark is checked where it is computed, in the
binary you deploy, not by an assertion the release build strips. Treat an underflow or overflow as a
conservation breach: halt that transformation at the smallest scope, do not publish, and do not clamp
silently. If you saturate rather than check, **emit the saturation**. A saturated aggregate with no exception
attached is a lie.

**Shape**

```
aggregate := aggregate (op) delta        # checked AT the operation, in the shipped build
breach -> refuse to publish -> halt the smallest scope that contains it
saturate instead -> emit the saturation event
```

The rationalisation is *"the delta cannot exceed the aggregate by construction"*. It was by construction; the
drift is the bug you are hunting, and the check that would have caught it is not in the binary you shipped.

**How it appears:** `level.total_qty -= qty` on a `u64` guarded only by a debug assertion wraps to ~1.8e19 in
a **release** build and is published as depth to consumers who trade against it. Use `checked_sub().ok_or(...)`
or the language's equivalent. Two answers ship today in this same problem domain:

- **TigerBeetle keeps assertions live in release** (Zig `ReleaseSafe`), ~1 assertion per 10.6 lines of state
  machine, overflow-checking every accumulator before mutation (`sum_overflows`). Its rationale: *"Assertions
  detect programmer errors… The only correct way to handle corrupt code is to crash. Assertions downgrade
  catastrophic correctness bugs into liveness bugs."* And: *"assert the positive space that you do expect AND
  the negative space that you do not expect."*
- **nautilus_trader uses `saturating_add`/`saturating_sub`** on quantities (`orders/mod.rs:1270`, `:1366`),
  correct where a panic would leave exposure unmanaged, and its invariants are `debug_assert!` with
  `debug-assertions = false` in **both** `[profile.dev]` and `[profile.release]`, so they are compiled out
  everywhere.

Choose per path and write the choice down. Where a panic would abandon an obligation, saturate and emit the
saturation event. Where nothing is in flight, assert live and crash. A `debug_assert` on a published aggregate
is neither of the two.

### A fan-out bound lives on the emit path, keyed to the inbound unit

Every transformation that turns one input into many outputs carries a counter keyed to that inbound unit and a
hard bound checked **before the send**, not by a monitor reading a metric afterwards. On breach: set a flag
the emit path reads before every subsequent send, cancel resting orders, and disconnect the order-entry
session, while risk, position and drop-copy stay alive. The flag must not be resettable by the component that
tripped it, and disabling the failing check is never the mitigation.

**Shape**

```
inbound unit -> counter(unit) += 1 -> counter > bound ? trip flag, refuse to emit : emit
every later emission reads the flag first
reset needs a named authorising role plus a recorded root cause, never a bare "resume"
```

A monitor polling a metric is one interval behind an unbounded loop, and an unbounded loop empties the account
inside that interval.

**How it appears:** bounds worth naming are `max_children_per_parent`, `max_notional_per_parent`,
`max_shares_vs_ADV`, `max_messages_out_per_message_in`. A suspense or error account is single-purpose and
linked to an automated firm-aggregate limit that **rejects new orders** on breach. Knight ¶21: no *"control to
compare orders leaving SMARS with those that entered it"* and *"no procedures in place to halt SMARS's
operations in response to its own aberrant activity"*. ¶17, $460M in 45 minutes; ¶27, *"continued to send
millions of child orders while its personnel attempted to identify the source"*, and the remediation re-armed
the defect on seven more servers: *"This action worsened the problem."* Goldman's rate-based circuit breakers
worked and personnel *"repeatedly lifted the circuit breakers blocks between 8:44 a.m. and 9:32 a.m."* without
their own policy's authorisation, still investigating.

### An inbound order is validated against its own instrument's economics

Reject any order priced more than a configured band away from **that instrument's own** reference price, and
reject or cancel-newest when both sides are the same economic party. The band derives per instrument from that
instrument's own reference price, never from a cross-universe aggregate, and the same derivation applies on
every session-state code path. A prevented match is not a trade: record it as a counterfactual, never as a
fill to either side and never as volume. A book without both prints the fat-finger trade and then owes
everyone a bust process.

**Shape**

```
inbound order -> band(this instrument's own reference price) -> reject
              -> same account family on both sides -> reject or cancel-newest
prevented match -> counterfactual record, excluded from fills and from published volume
```

Published volume leaves your process and becomes an index input for parties you have no contract with.
Whatever you print becomes somebody else's price.

**How it appears:** Goldman ¶25/¶30, the pre-market band's upper bound was 1.5× the highest closing price of
*any* listed option ($3,090), so a $1 order in any name passed. CFTC v. Coinbase, March 2021, $6.5M: two
internally operated programs *"matched orders with one another … resulting in trades between accounts owned by
Coinbase"*, and that volume propagated into CME's Bitcoin Real Time Index, CoinMarketCap and the NYSE Bitcoin
Index. Report the counterfactual as `Quantity prevented from trading`. Self-match prevention binds at the
**account-family** level, not per strategy. 15c3-5(c)(1)(ii) binds these as pre-trade rejections on the last
hop, order-by-order, measured from **orders entered** rather than executions received.

### A revalidate-and-recompute loop that consumes less than it receives does not converge

Never compute a price over a state that concurrent changes can mutate between compute and print. A loop that
revalidates and recomputes must consume the **entire** pending input queue per pass, or the input set must be
frozen before computation begins. Consuming one event per pass is a livelock whenever the arrival rate exceeds
one event per pass, and a component livelocked on its input queue keeps accepting inputs it cannot process, so
its last computed output is arbitrarily stale. Bounding the retries converts the hang into an abort; it does
not make the loop converge. Assert the freshness of a computation's input set at the point of commit.

**Shape**

```
compute over input set -> inputs arrived during the pass ? recompute : print
converges only if one pass drains every pending input, or the set is frozen first
retry ceiling -> abort, not convergence
```

Never disable a correctness check to force completion during an incident. If a check is disabled, every
output produced afterwards is quarantined and reconciled before it is treated as authoritative. A
reconciliation failure raises an owned, escalating alert; withholding output is not a response.

**How it appears:** SEC 34-69655, NASDAQ / Facebook IPO, 18 May 2012. ¶19, *"only the first of those two
cancellations was incorporated into a third price/volume calculation"*; ¶20, *"because the system was designed
to perform a separate recalculation for each of those cancellations"*; ¶23/¶26, validation lines removed from
the failover let an 11:11 input set price an 11:30 cross, and the >3 million share short position came from
the **cancel imbalance inside that 19-minute window**, not from the removal itself; ¶30, the Execution App
then correctly refused to emit and nobody learned why for over two hours. ¶65's remediation: close the order
ports before the calculation, or take bursts of changes *"in one recalculation … rather than in multiple
recalculations"*. A retry ceiling would have aborted the cross, not completed it. The defect is a loop making
strictly less progress than the arrival rate.

### Halt means quiesce, at the smallest scope that contains the breach

A halt is `engine quiesced ∧ everything already produced delivered or explicitly voided`, at the smallest
scope that contains the breach. Severing the transport is not a halt: it abandons in-flight executions the
participants cannot see. Name which level you mean.

**Shape**

```
detect breach -> pick the smallest level that contains it -> stop producing -> drain
              -> deliver or explicitly void everything already produced
risk-reducing paths stay callable, gated by a DIFFERENT flag than the risk-increasing path
```

| # | Level | Existing obligations | Use for |
|---|---|---|---|
| 1 | Reject this operation, typed | untouched | a bad inbound order |
| 2 | Freeze one aggregate (symbol, account) | untouched | one book's invariant |
| 3 | Fail-closed: no new or increasing exposure; cancel / close / flatten / settle stay hot | actively managed | open exposure at detection |
| 4 | Cancel-all + disconnect order entry; risk, position, drop-copy stay up | actively managed | a fan-out bound breached |
| 5 | Quiesce: stop accepting **and** stop producing, drain in-flight, deliver or explicitly void everything already produced | drained, then frozen | venue-level halt |
| 6 | Process abort | **abandoned** | only where nothing is in flight |

Risk-reducing paths (`cancel`, `replace-down`, `close`, `flatten`, `settle`, `reconcile`) stay callable while
halted, gated by a different flag, with a test that exercises them in the halted state. Where an invariant can
be **momentarily** false during a known intermediate state, name that state and give the check a bounded
self-heal window before escalating; a check that fires on a legitimately intermediate state is itself an
availability bug. Confine exceptions to tasks; never abort a process holding unmanaged obligations.

**How it appears:** TSE/JPX, 1 October 2020, escalated to a whole-day halt because participants held
undelivered fills and no rule existed for post-halt resumption. LULD's Trading Pause still executes the
closing transaction, and LULD state is derived, not delivered: Limit State is the NBO *equalling but not
crossing* the band, so equals versus crosses, and wall-clock versus market-data seconds, are where the
off-by-ones live.

## Allocation, and the residue

**Rounding down cannot distribute everything, so the residue pass is part of the algorithm, not a follow-up.**
Pro-rata allocation must never be the last step: define the leftover pass (FIFO by time priority, or the
venue's documented rule) and assert `Σ allocations == aggressing quantity` before any execution is emitted.
**Execution prints at the RESTING order's price, not the aggressor's.** Where aggressing quantity exceeds
total resting quantity, the FIFO exception applies.

**How it appears:** three quantity conventions share one word (OUCH Cancel's intended total, OUCH Replace's
chain-cumulative total, ITCH Modify's decrement), and the conversion table is in the allocation reference.

## Publishing market data

**A feed is a contract about identity, order and completeness, and a consumer can only detect the gaps you
taught it to detect.** Everything a consumer must infer, you have failed to publish.

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
  not necessarily close a gap: a response truncates at the messages that completely fit, and treating a
  truncated response as a covered range is *proven coverage before the cursor advances* failing inside your
  own publisher.
- **Two filters, one feed.** Non-printable executions are excluded from **volume** to prevent double-counting
  with the later bulk print; Trade messages are excluded from the **book**. Publish the flag; document which
  downstream use takes which filter.
- **Fairness is architectural, not intentional.** Reg NMS Rule 603(a) prohibits releasing quote and trade data
  on a proprietary feed before sending it for consolidation. NYSE breached it because its proprietary path was
  faster (disparities *"from single-digit milliseconds to, on occasion, multiple seconds"*), and could not
  prove compliance because it had not retained the transmission-timing files. Retain them.

## Netting, DVP and finality

**Netting is a conservation law with a legal consequence, so assert the conservation before the instruction
leaves.** Per cycle `C`, per currency `X`, before emitting any settlement instruction:

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
system's rules and the applicable insolvency law: your code records which side of it each instruction is on,
it does not invent one. A system that books late and back-dates the value date has not achieved value-date
finality; it has produced a record that looks like it did.

## Liquidation, marks and the backstop

**A number you publish and also act on is an input an adversary can pay to move.** Separate the price that
reports from the price that decides.

- **Mark price, index price and last price are three numbers with three jobs.** A thin book, or a wick on one
  venue, moves last price; it must not move the number the liquidation engine uses. State in the code which
  price each calculation reads.
- **Size risk limits so that the cost of moving the mark exceeds the payoff of the position it liquidates.**
  Hyperliquid's JELLY market, 26 March 2025, is the worked example: self-trading against a thin oracle forced
  the HLP backstop vault into a large short.
- **A backstop that can be forced to inherit a position is the attack target, not a safety net.**
  Insurance-fund draw, socialised loss and ADL are distinct waterfall steps: name the step, publish it with
  the inputs that selected it, and remember the deleveraged counterparty has no independent way to check your
  work.
- A liquidation is an execution: same journal, same sequence numbers, same checked aggregates and same publish
  check as any other match, not a side effect of a risk loop.

## Resolution and complete sets

**A payout is a vector over outcome slots with a denominator, and crediting it is a one-way door.**

- **Persist `(numerators[], denominator)`**, never a winning-outcome enum or a `bool won`. The type must be
  able to represent `[1,1]/2` (a 50/50) and `[3,1]/4`.
- **Credit only on the venue's explicit finality signal**: `payoutDenominator[conditionId] > 0` on the Gnosis
  CTF, `status == finalized` on Kalshi. `closed`, `determined`, `disputed`, `amended` and a proposed oracle
  price are all not-final, and Kalshi's `amended` **restarts the settlement timer**.
- **Where the authority's resolution write has no correction path, the reversal path is built above it, never
  inside it: a new instrument, or an off-ledger make-whole.** Resolution is one-shot on the Gnosis CTF, where
  `reportPayouts` requires `payoutDenominator[conditionId] == 0` and there is no re-report, no correction and no
  admin override. Decide the make-whole before the resolver ships, not after it has paid.
- **Complete-set conservation is a floor-versus-round trap.** The chain computes `floor(stake × num / den)`
  **per position and then sums**; a mirror ledger computing the same quotient with round-half-up credits more
  than the chain pays, invisibly until the first non-trivial payout vector. On a `[1,1]` resolution, redeeming
  `x` YES and `x` NO returns `2·floor(x/2)` while merging first returns `x` exactly, and the residue stays in
  the contract permanently; there is no sweep. Merge complete sets before redeeming whenever the payout vector
  is not `[1,0]`-shaped, and assert `collateral_held ≥ Σ_i supply_i × num_i / den` continuously.

## Output

Every economic change ends with this block. A change the economic-diff gate exempts needs none of it.

```
FINANCIAL CHECK
tier:       T<n>, and the signal that placed it there
effect:     what moves value, from whom to whom, in what unit
identity:   the stable identity of the intent, durably recorded at file:line
ambiguity:  which counterparty responses are UNKNOWN, and how they resolve
authority:  whose copy of each quantity is the record
recovery:   what a crash or restart between the effect and the local commit does
controls:   <control> -> <file:line>, one per line; at T2 and above also `· <test name>`
            UNRESOLVED: <control> (<why>), for anything not implemented
```

`controls:` is where *implemented, not described* is enforced: every control named is a real `file:line` or an
explicit `UNRESOLVED:` line. A described control with no location is the finding.

**At T2 and above, add the ENGINE CONTRACT block.** A change that touches state you assign is T3: no
counterparty can confirm it, so there is no external authority to reconcile against and the evidence has to be
internal. The journal, the replay test, the bound and the halt gate *are* the reconciliation.

Two cases in this domain stay on the default block, and they are the ones to check the gate against. A venue
deployed only to a sandbox, holding no participant funds and minting no identifier an outside system consumes,
is T0: the engine is real, but nothing it assigns is yet anyone's record. A read-only path over
already-published records (a report, a volume query, a backtest over the recorded command stream) that
republishes nothing and creates no obligation is T0 as well. Both become T3 the day participant funds or an
outside consumer arrive, or the day the number they produce becomes an index input, a mark or a settlement
figure.

Fill the block immediately before the `controls:` lines. **Fill only the slots this change touches.** A slot
the change touches and cannot fill is the finding; a slot it does not touch is omitted, not left blank. The
client-of-a-venue block is `VENUE CONTRACT` in `fin-exchange-integration`, and a process that is both a venue and
a client of another venue emits both.

```
ENGINE CONTRACT
- Venue half:  T3, <what makes you the record: assigns ExecID / crosses resting orders / mints the payout>
- Client half: T2, <the venue you are also a client of, and the reconciliation key> | none
- Journal:     <file:line where the inbound command is committed before the book is touched>
- Replay test: <test name that replays the command stream and byte-compares the emitted event sequence>
- Publish:     <file:line where the send result is bound and checked>
- Emit bound:  <name>=<value> at <file:line>; trip flag reset owner: <component, not the emitter>
- Halt level:  <1-6>; risk-reducing paths gated by <flag> at <file:line>; test: <name>
- Published aggregates: <field> checked at <file:line>; saturation emitted at <file:line> | none saturates
```

At T3 add the per-technique evidence table whose shape `fin-verification` owns. The internal invariants
(conservation, `Σ net == 0`, `collateral_held ≥ Σ obligations`, replay versus live sequence) still obey
*reconciliation runs in production*: ship them as a scheduled entrypoint reading through a path independent of
the writer, with an alert destination that has no default.

## References

A literal from the middle column appears in the code, the repo or the task text → **read that file
immediately and apply it in order. Do not summarise it.**

| File | Read it immediately when the code or task contains | Covers |
|---|---|---|
| [matching-and-allocation.md](references/matching-and-allocation.md) | `pro_rata`, `allocate`, `time_priority`, `iceberg`, `auction`, `uncross`, `cross`, `opening_price`, `imbalance` | CME allocation pipeline, rounding and the leftover pass, priority preservation, iceberg refresh, cross computation and the freeze/recompute contract |
| [market-data-publication.md](references/market-data-publication.md) | `RptSeq`, `seq_num`, MoldUDP64, ITCH, SBE, `MDIncrementalRefresh`, `snapshot`, `retransmit`, `gap` | Per-message sequencing, channel reset, snapshot/incremental joins, A/B arbitration, printable flags, 603(a) timing evidence |
| [journaling-and-recovery.md](references/journaling-and-recovery.md) | `wal`, `journal`, `outbox`, `replay`, `snapshot`, `recover`, `failover`, `sequencer`, `epoch`, `fencing` | Input journaling and flush ordering, replay harness, banned constructs in the core, snapshot/recovery, single-writer failover, deterministic simulation |
| [risk-halts-and-settlement.md](references/risk-halts-and-settlement.md) | `halt`, `LULD`, `price_band`, `kill_switch`, `15c3-5`, `netting`, `DVP`, `finality`, `liquidate`, `ADL`, `reportPayouts` | 15c3-5 clause by clause with Knight and Goldman, band derivation, halt/resume state, netting invariants, DVP models, waterfalls, resolution |
