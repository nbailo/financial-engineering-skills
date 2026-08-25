---
name: fin-verification
description: >-
  Proof that a money path is correct: reconciliation design, executable invariants, fault
  injection at crash boundaries, event replay and permutation, property and model-based testing,
  and the evidence each risk tier requires before shipping. Use alongside a domain skill when a
  money-path change touches tests or assertions, when a reconciliation or kill switch is
  involved, or when the ask is whether this is ready to ship.
license: MIT
---

# Proving a Money Path

This skill is loaded **alongside a domain skill, never instead of one**. The domain skill owns what a
posting is, what a reconnect must re-fetch, what a settlement batch owes. This skill owns whether any of
it runs in production, detects a discrepancy planted in it, and survives a restart. Ask of every control
in the diff: if it were silently deleted tomorrow, which test goes red? If the answer is none, the
control is already absent.

## Workflow

1. Name the economic claim this change makes, and name who eats the loss if the claim is false.
2. Establish the tier, and therefore the evidence this change owes. Load only the references that tier
   and this implementation need, and read each in order rather than summarising it.
3. Name the external authority and the join key for every economic quantity the system reports.
4. Ship the comparison against that authority as a scheduled entrypoint. At T1 that is the daily
   comparison below; at T2 and above it reads through a path independent of the writer.
5. Prove the comparison detects a real break, by injecting one. At T1 this is the daily comparison's
   detect-test; at T2 and above, one detect-test per reconciliation.
6. Kill the process at a phase boundary, restart, and assert the recovery converges to exactly one
   effect. At T1 that is the one boundary you have actually executed (request sent, not yet persisted
   locally); at T2 and above, every boundary of the path.
7. At T2 and above, replay and permute every event consumer, including redelivery and reordering. At T3
   the permutation is exhaustive and generator coverage is asserted.
8. Read the design notes as a list of claims: each is proven by a named test or deleted. Implement every
   control you name, with its test, before you call the path complete, then issue the verdict.

## When this applies

Load this skill whenever someone is being asked to believe a money path is correct. That happens when a
change adds or edits the tests, fixtures or assertions standing behind an economic quantity; when it
adds, moves or removes a comparison against an external record; when it adds a way to stop trading,
paying or posting because something looks wrong; or when the tier is T2 or above, whatever the diff says
about tests. Above T1 the trigger is structural, not lexical: a live multi-venue team asked to "add a
third venue" never says the word test, and is exactly the team with no reconciliation.

Literals that usually mean this skill applies, as routing hints and not as the definition: `tests/`,
`test_*.py`, `*_test.go`, `*.spec.ts`, `conftest.py`, a fixture or a cassette; `hypothesis`, `@given`,
`@rule`, `RuleBasedStateMachine`, `fast-check`, `fc.commands`, `proptest`, `jqwik`, `vcr`,
`record_mode`, `toxiproxy`, `madsim`, `loom`, `jcstress`, `-race`, `mutmut`, `cargo-mutants`; a symbol
matching `reconcil|recon_|break|drift|suspense|clearing|kill_switch|halt|dead_man`; a base URL
containing `testnet`, `sandbox`, `paper`, `-uat`, `-sim`; task text such as "is this ready", "prove it",
"how do I verify", "review before ship", "roll back", "write tests".

Skip when the asserted values are analytics that never become an obligation (backtest statistics,
greeks, implied vol, Monte Carlo) and no balance, order, payment or transfer is written. This skill also
never stands alone: load `fin-ledger` for what a balance is, `fin-exchange-integration` for a venue
client, `fin-matching-and-settlement` when you are the venue, `fin-payments` for a processor,
`fin-onchain` for a chain boundary, `fin-money-core` for amounts, identity and retries. They design the
control; you prove it.

## Core rules

Several failures that look like design failures are verification failures: a write-ahead field the
recovery path never reads back, a dedupe set that evaporates on restart exactly when the double-count
happens, a journal that is written and does not balance.

### Comparing two numbers proves nothing until you name which one is the authority

A scheduled comparison is a control only if every quantity it reports has a named external authority, a
join key that authority minted, a read path independent of the writer, a cadence stated against the
authority's documented lag, a tolerance in the instrument's own unit, and a durable break record with an
escalation clock. This specialises *reconciliation runs in production*.

**Shape**

```
authority   := the external system that owns the quantity
join key    := an identifier the authority minted, never one you minted
read path   := independent of the writer, not the writer's own cache
cadence     := the authority's stated publication lag, not a round number
tolerance   := one unit of the instrument's own precision, not a fixed epsilon
compare -> break record (aged bucket, hard threshold, fixed sweep) -> alert sink
```

Your own reference is not unique across retries: one retried attempt produces two counterparty
identifiers under one of yours, and the join silently drops a row instead of raising one. A job that
re-reads the store the writer populated finds arithmetic bugs and can never find a missing write, which
is why a reconciliation comparing a cache to itself passes forever. A per-entity comparison is routinely
broken on day one by opening balances nobody backfilled, which guarantees the alert gets muted.

**How it appears**

| Layer | Instantiation |
|---|---|
| Join on the counterparty's key | `pspReference`, the venue's `tradeId`/`orderId`, the chain's `(blockHash, txHash, logIndex)`. Never `merchantReference` or `clientOrderId`: you assigned those. |
| Tolerance in the instrument's unit | nautilus pairs `DEFAULT_TOLERANCE = 0.0001` with a single-unit tolerance keyed on the instrument's size precision (`reconciliation/positions.rs:40`, `:423`). A fixed epsilon is wrong for both a 0-decimal and an 18-decimal asset. |
| Break record and sweep | `detected_at, source_a, source_b, amount, currency, status`. The Fed's Difference account is swept monthly and expensed. |
| The check that is computed and discarded | The best open-source trading platform computes the continuous check and throws it away with `let _ =` (`execution/src/engine/mod.rs:1737`), under a docstring calling it "the core invariant maintained here". |
| The check nobody wrote | Nothing queries the venue's position endpoint, nothing asserts `sum(signed fills) == size`, and the venue's own realized-PnL field arrives on every event and is never cross-checked. |

### A detector that has never detected is not known to detect

Feed the comparison a discrepancy of a known size on a known entity and assert it produces the break
record and delivers the alert to the routed destination. Running the job proves it runs; only a planted
break proves it detects.

**Shape**

```
fresh migration (no backfill) -> seed a known discrepancy -> run the job
assert break row exists with the right amount, unit and both source names
assert the corrective posting leaves the books balanced
assert the alert sink received exactly one message
assert a clean run produces zero breaks
```

Running against a freshly migrated store is what makes an un-backfilled opening balance fail the test
instead of muting production. The alert also has to arrive somewhere a human reads: Knight emitted 97
"Power Peg disabled" emails in the 89 minutes before the open and nobody read them, so the signal
existed and nothing proved it reached a reader. This is the cheapest test in the suite and it protects
every reconciliation in every other skill at once.

**How it appears** Assert on a `break` row (`amount`, `currency`, `source_a`, `source_b`), on a
suspense posting that keeps the trial balance balanced, on a fake alert sink's message count, and on a
zero-break clean run.

### A crash between two foreign mutations is only survivable if you have executed it

An atomic phase is the set of local mutations between two foreign mutations, so a value-moving path has
exactly three boundaries. At each one, kill the process, restart, run the recovery path, and assert
exactly one external effect and exactly one local record. Every field written ahead of the effect must
be read back by that test's recovery path.

**Shape**

```
local mutations -> COMMIT intent
    | boundary 1: after the intent commit, before the call
external effect
    | boundary 2: after the call, before the outcome write
persist outcome
    | boundary 3: after the outcome write, before the publish
publish
at each boundary: kill -> restart -> recover -> assert exactly one effect, one record
```

Persisting the write-ahead identifier is the easy half and the half that gets done. Consuming it on
recovery is the half that gets skipped, leaving a resume path that looks up the intent with a null
identity and raises on exactly the crash the journal exists for.

**How it appears** `kill -9` or `SIGKILL` between phases; a `resume()` that calls `get_order(None)`
and raises `ValueError`; `madsim` or `turmoil` for the deterministic form.

### The ambiguity worth testing is the request that arrived

A test that stubs the call to raise before delivery tests nothing, because the failure mode is the
request the counterparty received and then failed to acknowledge. Deliver the request, then break the
connection. This specialises *durable intent before the external effect*.

**Shape**

```
stub writes the request upstream, then closes   (NOT: raises before delivery)
assert no resubmission occurs
assert the code queries the counterparty by the identity it minted
assert exactly one effect exists afterwards
mirror case: the effect genuinely did not happen -> assert the retry does fire
```

Without the mirror case you have proved only that the code gave up, not that it distinguishes UNKNOWN
from "did not happen".

**How it appears** A Toxiproxy `timeout` toxic ("stops all data from getting through, and closes the
connection after timeout"), or a stub that delivers then hangs. The defect this catches in the wild:
CCXT re-POSTs a create-order on `RequestTimeout`, because `RequestTimeout extends NetworkError extends
OperationFailed` and the single funnel's retry predicate is `e instanceof OperationFailed`
(`ts/src/base/Exchange.ts:6435`) with no HTTP-method discrimination, and the re-sent request carries the
identical `newClientOrderId`.

### An event consumer is correct only if every arrival order produces the same state

Take a recorded event stream, apply it in the recorded order, then apply shuffled permutations of it
with duplicates and with a restart in the middle, and assert the final state is byte-identical every
time. This specialises *arrival order is not occurrence order*.

**Shape**

```
recorded stream -> apply in order -> snapshot A
same stream -> shuffle + duplicate + restart mid-stream -> apply -> snapshot B
assert A == B
assert conservation and idempotence after every step, not only at the end
assert generator coverage: does it ever emit a duplicate at a terminal state,
       or a restart between the effect and the outcome write?
```

Asserting after every step is what catches, in one pass, an in-memory dedupe set that lives only in the
process (so a restart re-applies every counted fill), a version watermark stored on the live object
instead of independently, and an illegal transition. Assert coverage explicitly, or the generator
quietly never produces the case the bug lives in.

**How it appears** A `_seen_trade_ids` set held in process memory; jqwik's `injectDuplicates()` and
`Statistics.coverage` for the coverage assertion; nautilus ships the order-invariance form as
`test_avg_px_invariant_to_fill_arrival_order`.

### An assertion is a claim about values you produced, not values you received

Before writing an assert, panic, abort, process exit or unhandled throw on a money path, name the
provenance of every value in the asserted relation. If any of them crossed a network, a file, a config
or a clock, it is an operating error and needs a typed, fail-closed guard, not an assertion.

**Shape**

```
provenance of every term is this process only     -> assertion is legitimate
provenance includes network | file | config | clock -> typed rejection, fail-closed
crash only when abandoning obligations costs less than continuing
```

A ledger at rest loses nothing. A position at rest loses money, and an unattended resting order keeps
filling, so the correct level depends on deployment topology and on what the process is holding.

**How it appears** TigerBeetle asserts in production at about 1 per 10.6 lines (487 in 5,166):
*"assertions downgrade catastrophic correctness bugs into liveness bugs"*, asserting "the positive space
that you do expect AND the negative space that you do not expect", on six replicas with state in a
replicated WAL. nautilus compiles its three `debug_assert!`s out of release. Both are correct; the
difference is topology, not rigour. The Polymarket overfill assertion looked like a programmer error and
was an operating error: the violating `last_qty=5.012345` against `quantity=5.000000` came from the
venue.

### The design notes are a numbered list of claims, each bound to a test or deleted

Enumerate the design-notes and docstring block as a numbered claim list before you read the
implementation, then bind each number to a test name or delete the sentence. This specialises *a comment
is a claim*.

**Shape**

```
read comments and docstrings first -> claim 1, claim 2, ... claim n
for each claim: name the test that proves it, or delete the sentence
never: a claim whose evidence is the implementation you are about to read
```

The asserted invariant is repeatedly exactly where the bug lives, because the assertion is what let it
survive review.

**How it appears** Two shapes recur. The ordering claim: "the flush guarantees the row exists before
the call", documented at line 288 and performed at line 248. The exactly-once claim: "move money into
`refunded_cents` exactly once", over a branch whose body is `pass`.

## The minimum viable test set for a small live bot (T1)

**This is the proportionality proof: at this size, five tests and one scheduled job carry the risk.**
Demanding deterministic simulation from someone writing a 300-line bot buys nothing. Its dominant risk is a
duplicate order after a timeout, not consensus divergence. These five plus the daily comparison are the
core of the T1 set, and the tier matrix carries the full eleven required rows: the only rows they do not
name are the money-math and exact-arithmetic unit tests (including the ORM column type and the JSON
round-trip) that every tier from T0 up already owes, and the daily comparison's own detect-test. Nothing
above T1 is added to either list. Two of the five ship as executable code in `fin-exchange-integration`:
test 1 (`test_timeout_that_already_filled` with its mirror `test_timeout_that_never_arrived`) and test 2
(`test_normalize_satisfies_every_filter_simultaneously`). The remaining three and the daily comparison
are specified here, in this form, and are the same set, not an additional demand.

1. **`test_timeout_that_already_filled`**: *the ambiguity worth testing is the request that arrived*,
   applied to order submission. Toxiproxy `timeout` toxic, or a stub that delivers then hangs. Assert:
   no resubmit; the bot calls its query-by-`clientOrderId` path; exactly one order exists. Run the
   mirror case where the order genuinely does not exist and assert the retry fires.
2. **`test_normalize_satisfies_every_filter_simultaneously`**: a normalised instruction is legal
   against every venue constraint at once, or is an explicit skip, and is never larger than what was
   asked for. This is *every refusal that protects you runs inside the function that sends*, proven as a
   property test over generated (price, size) in the boundary region of the real constraint set, one run
   per instruction type. Assert all constraints in one pass, that normalisation never returns zero
   without an explicit skip signal, and that rounding moves toward validity and never increases size
   beyond available balance. Drive the constraint values from a **production** fixture, never a
   hand-written or testnet one. On one venue that reads: `price % tickSize == 0`, `qty % stepSize == 0`,
   `price*qty >= minNotional`, `minQty <= qty <= maxQty`, a LIMIT/MARKET parameterisation to exercise
   `MARKET_LOT_SIZE`, and filter values loaded from `exchangeInfo`.
3. **`test_fill_stream_replayed_shuffled_and_duplicated`**: *every arrival order produces the same
   state*, on one recorded session: place, partial, partial, amend/cancel, reject, disconnect,
   reconnect-with-replay. Assert `position == sum(signed filled qty)` and
   `cash == -sum(price*qty) - sum(fees)` computed **independently of the bot's own accumulators**, and
   that its fee equals the venue's reported fee per fill. Then feed the same stream with duplicates and
   one swapped pair and assert identical terminal state.
4. **`test_kill_after_send_places_no_second_order`**: the first boundary of *a crash you have executed*,
   only. `SIGKILL` between "request sent" and "order persisted locally"; restart; assert startup
   reconciliation converges to exactly one order and the correct position. If the bot has no startup
   reconciliation, this test is what forces you to write one.
5. **`test_limits_hold_and_ambiguity_halts`**: a property test over generated sequences of fills,
   rejects and reconnects. Assert that at no point does position exceed `max_position`, notional exceed
   `max_notional`, or orders-per-minute exceed the cap; and that an ambiguous reconciliation result puts
   the bot in a state that places no new orders **while cancels still work**.

**Plus the daily comparison**, which is the authority-and-join-key rule at T1 cadence and is not
optional: one scheduled entrypoint asserting `sum(signed fills) == venue position` and
`local free balance == venue balance` per asset, joined on the venue's `tradeId`, alerting to a config
key with no default.

**Deliberately excluded at this size:** model-based testing, deterministic simulation, race detectors,
mutation testing, Jepsen, loom. A 300-line bot with these five tests and the daily comparison is in far
better shape than one with 95% line coverage of its indicator math.

## Properties that actually matter for money

Five, and the rest are decoration:

- **Conservation**: `sum(all account deltas) == 0` after **every** step, and globally
  `sum(balances) + fees == sum(deposits) - sum(withdrawals)`, not only per entity.
- **Idempotence**: `f(f(x)) == f(x)` for every operation a retry can repeat.
- **Permutation-invariance**: only for operation sets your design *claims* are order-independent. If it
  is not claimed, do not assert it: find out which order the design depends on and test that order.
- **Reservation-implies-postable**: no reachable state holds a committed reservation that cannot be
  posted.
- **Allocation totality**: `sum(allocate(total, weights)) == total` over generated totals and degenerate
  weight vectors.

**Model-based testing** drives the implementation and an independently written, deliberately naive
reference model from one generated command sequence. Derive the model from the specification or the
venue's docs, **never from the implementation**, and keep it in a separate module, or you are testing
code against itself. `fc.commands`/`fc.asyncModelRun`, `RuleBasedStateMachine` with `@invariant()`,
`proptest-state-machine`, jqwik `ActionSequenceArbitrary`.

**Concurrency.** Write the double-spend reproduction as a two-connection **barrier** test (both
transactions read, both block on a barrier, both write), never as a loop of threads hoping to hit the
window. loom and jcstress exhaustively permute interleavings of hand-written lock-free structures under
the C11 memory model, and loom cannot see a type that is not a loom replacement type: on a database
transaction or a mutex they report nothing.

## When an invariant fires at runtime: classify, then act

**"Halt" names six different actions with wildly different blast radii. Name which one, in the code, at
the call site.**

| # | Halt level | What it does | Obligations |
|---|---|---|---|
| 1 | Reject the operation | this call fails, typed; process fine | untouched |
| 2 | Freeze one aggregate | writes to that account/symbol refused; reads still serve | untouched |
| 3 | Fail-closed (risk-off) | no new or increasing exposure; cancel, close, flatten, settle stay hot | actively managed |
| 4 | Cancel-all + disconnect the emitter | withdraw resting orders, sever order entry; risk and drop-copy stay up | actively managed |
| 5 | Quiesce | stop accepting *and* producing; drain; deliver or explicitly void everything already produced | drained, then frozen |
| 6 | Process abort | `panic` / `exit` | **abandoned** |

Evaluate the predicates in order; first match wins. The last column names who designs the response. You
prove it exists, is at the smallest scope that provably contains the breach, and is reachable by a test.

| Observable predicate | Response | Designed in |
|---|---|---|
| No external effect yet, and the check runs in the same transaction as the write | **Level 1**, typed, terminal for that idempotency key. Never `log.warn` and proceed; never clamp into range | `fin-money-core`, `fin-ledger` |
| Wrong value in your own store; no counterparty acted on it; no open position | **Level 2**, write path only. **No automatic corrective write**; repair is a separate reviewed job | `fin-ledger` |
| An external record disagrees with yours, and money left / a fill happened / a customer saw the balance | Neither halt nor silent reversal: book to a named, aged, reversible **suspense** account, keep operating, escalate on a clock | `fin-ledger` |
| A position, working order or obligation exists whose value moves without you acting | **Level 3.** Record the true value, alert, close the risk gate for that scope. `cancel_all` and `flatten` MUST work while it is closed, with a test proving it. Reopen only on a successful reconcile, never on a timer, never by the code path that closed it | `fin-exchange-integration` |
| Own output exceeded its bound relative to its input (`orders_out` vs `orders_in`, `shares_issued` vs authorised, `payouts` vs instructions) | **Level 4**, automatic. The bound is checked **on the emit path before the send**, not by a monitor, and the flag must not be resettable by the component that tripped it | `fin-matching-and-settlement` |
| Recomputable from an append-only log that passes its own checksums | Mark the view stale; return a typed `Stale{as_of}`, never a stale number, never zero; rebuild | `fin-ledger` |
| Every value in the relation was produced by this process, with no network, file, config or clock (see *an assertion is a claim about values you produced*) | **Level 6.** Crash | (this skill) |

Six prohibitions, each traceable to an incident, and each a discipline failure, not an ignorance one:

- **Never abort a process holding unmanaged obligations.** Ariane 501: *"It was the decision to cease the
  processor operation which finally proved fatal."*
- **Never let the failure path create state while the system is live and aberrant**: no retry, no
  resubmit, no hot rollback. Knight ¶27: *"This action worsened the problem."*
- **Never disable the failing check as the mitigation.** NASDAQ 2012 removed the validation code from the
  failover path to get the cross out, and that is what created the error position.
- **Never silently drop the violating event, and never clamp a reported quantity into range.** The units
  were really received. nautilus's `allow_overfills` defaults to `false`, which discards the fill report
  entirely.
- **Never gate the risk-reducing path on the same flag that gates the risk-increasing path.**
- **Never implement a halt by severing the transport.** A halt means the engine is quiesced AND
  everything already produced is delivered or explicitly voided.

Where the invariant can be transiently false by design, give it a self-heal window before escalating.
LULD waits 15 seconds in Limit State before pausing. A check that halts on a momentarily-inconsistent
intermediate state is itself an availability bug.

## Tier gates the evidence, never which rules apply

**Tier from Axis B and the observable signal table.** Axis B is: *does an external oracle exist that you
can reconcile against?* A bot has one: the exchange. A payments integrator has one: the processor. **A
matching engine, custodian or system-of-record ledger cannot: it IS the oracle.** When Axis B is "no",
reconciliation is unavailable as a safety net and the proof burden moves before deployment, into
simulation. **That is the only principled reason to demand deterministic simulation. Code complexity,
team size and how important the system feels are not reasons.** Do not compute "max loss per erroneous
action times actions per second": it is not observable from a diff, and the fabricated number then sets
every downstream evidence requirement.

- **T0 to T1** is crossed by *an order can actually be sent*. Property tests become required because
  filters and rounding are adversarial input spaces the author cannot enumerate.
- **T1 to T2** is crossed by *someone else eats the error*. Loss is no longer bounded by capital
  deliberately exposed, which is what justifies model-based testing, an independent reconciliation path,
  network-level fault injection, and shadow diffing for any rewrite.
- **T2 to T3** is crossed by *no external oracle exists*. A bug is not detectable by reconciliation after
  the fact, so the only place to find it is a simulator. "Correct" becomes a *claimed consistency model*,
  which is exactly what an external adversarial audit tests and an internal simulator systematically
  under-tests.

At T3, deterministic simulation means closing the system to nondeterminism (FoundationDB deploys one node
per core and avoids multithreaded concurrency; madsim shims `getrandom`, `getentropy`, `clock_gettime`),
injecting faults *inside* production code paths (`buggify`), randomising tuning parameters so none
becomes load-bearing, and proving determinism with a meta-test that runs one seed twice and byte-compares
the trace. Then assert generator coverage with FDB-style `TEST(cond)` counters: TigerBeetle ran the VOPR
on 1024 cores 24/7 and Jepsen still found two safety bugs plus seven crashes, because the fuzzer
generated matching objects always consecutive in the index, so the zig-zag merge join's probe path was
never exercised. **DST does not subsume an external adversarial pass.** Antithesis makes DST purchasable,
hence *recommended*, not wasteful, at T2.

## Fixtures, shadow diffs, and technique blind spots

**Record HTTP and WebSocket fixtures from production endpoints, and replay them in a mode where a request
the recording does not contain fails the test instead of reaching the network.** A replay library that
records what it misses turns an absent fixture into a silent live call that passes CI, so the replay mode
is part of the control: in vcrpy that is `record_mode="none"` rather than the default `once`. Testnet
proves protocol conformance and nothing else: its order books are independent and unsynchronised, are
wiped periodically, expose only `/api` and not
`/sapi`, carry different filter thresholds, and can receive a breaking API change **before** production.
Binance SPOT testnet shipped the `MIN_NOTIONAL` to `NOTIONAL` rename first. A testnet fixture gives you
the wrong `tickSize`, and the filter property test then proves nothing. A dry-run is optimistic by
construction: freqtrade's fills a market order from order-book volume with 5% maximum slippage and
assumes `stoploss_on_exchange` fills at the stop price. Never report or gate on dry-run PnL as if it were
realistic. The cassette set must include **reject, partial-fill-then-cancel, over-fill/residual,
429-then-418, and mid-stream disconnect**. Happy-path fills test nothing about the branches that lose
money.

**Shadow diffing** is the cheapest oracle available for a rewrite at T2 and above: run the new
implementation against production traffic, compare its economic outputs to the incumbent's, alert on
divergence beyond one minor unit, ship nothing until the diff is empty for a stated volume.

A race detector will never find a double-spend. A lost update across two transactions is not a data race,
and it *"can't find races in code paths that are not executed."* Line coverage does not show whether
money math asserts anything: run mutation testing on the rounding, fee, allocation and PnL modules
**only**, and demand a high score there rather than chasing coverage.

## Output

### Default, every economic change, T0 and T1

```
FINANCIAL CHECK
tier:       T<n>, Axis B (does an external oracle exist: yes or no), and the signal that decided it
effect:     what moves value, from whom to whom, in what unit
identity:   the stable identity of the intent, durably recorded at file:line
ambiguity:  which counterparty responses are UNKNOWN, and how they resolve
authority:  whose copy of each quantity is the record
recovery:   what a crash or restart between the effect and the local commit does
controls:   <control> -> <file:line>, one per line; at T2 and above also `· <test name>`
            UNRESOLVED: <control> (<why>), for anything not implemented
```

At T0 and T1 that block plus one line, `SHIP` or `NO-SHIP: <the first unresolved control>`, is the whole
verdict. No evidence table, no separate risk table: the `controls:` line carries the implementation
evidence, and a control described with no `file:line` and no `UNRESOLVED:` is a defect (*implemented, not
described*). The tier matrix, not the vocabulary in the diff, decides which techniques this tier actually
requires; at T1 that is the eleven rows in its T1 column, whose core is the five tests above plus the daily
comparison.

### T2 and above: the ship verdict

At T2 and above, and for any response that approves a money path for release, the FINANCIAL CHECK is
followed by this block. Emit only the slots the change actually touches. A slot you emit with no
`file:line` is ABSENT, and one ABSENT slot makes the verdict NO-SHIP.

```
SHIP VERDICT
Reconciliation:  <entrypoint file:line> · authority <name> · join key <counterparty's own field>
                 · cadence <interval, vs the authority's stated lag> · detect-test <test name>
Breach class:    <the predicate that matched> -> halt level <1..6>, implemented at <file:line>
Evidence:        <the reconciliation detect-test actually run> · <the crash-boundary test actually run>
Verdict:         SHIP, or NO-SHIP: <the first ABSENT slot, named>
```

There is still no separate risk table at T2: the FINANCIAL CHECK's `controls:` line carries a test name
alongside the `file:line` for each control, which is the same evidence a risk table would restate.

### T3

`Evidence:` stops being two named tests and becomes the per-technique table, which exists at T3 and
nowhere else. Emit one row for every technique the tier matrix marks required at T3, including the
deterministic-simulation rows and the external adversarial pass:

```
<technique> | <test name> | <file:line> | PRESENT|ABSENT
```

## References

| File | Read it when |
|---|---|
| [tier-matrix.md](references/tier-matrix.md) | **Before you call any technique required, recommended or wasteful**, and whenever you emit a `Tier:` line. Read it in order; do not summarise it. |
| [reconciliation-design.md](references/reconciliation-design.md) | The diff or task contains `reconcile`, `recon`, `break`, `suspense`, `clearing`, `drift`, or a scheduled job comparing two sources → read it immediately and apply it in order. Do not summarise it. |
| [property-and-model-testing.md](references/property-and-model-testing.md) | The diff contains `hypothesis`, `@given`, `@rule`, `RuleBasedStateMachine`, `fast-check`, `fc.commands`, `proptest`, `jqwik`, `mutmut`, `cargo-mutants`, `Stryker`, or `PIT` → read it immediately and apply it in order. Do not summarise it. |
| [fault-injection-and-replay.md](references/fault-injection-and-replay.md) | The diff contains `toxiproxy`, `vcr`, `cassette`, `record_mode`, `SIGKILL`, `kill -9`, `madsim`, `turmoil`, `antithesis`, `loom`, `jcstress`, `-race`, or a partition/nemesis harness → read it immediately and apply it in order. Do not summarise it. |
