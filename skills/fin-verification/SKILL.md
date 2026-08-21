---
name: fin-verification
description: Use when a money-path diff touches tests, fixtures or assertions (pytest, hypothesis/@given, fast-check, proptest, vcr record_mode, Toxiproxy, a testnet/sandbox base URL), adds any symbol matching reconcile|break|drift|halt|kill_switch, or the inferred tier is T2+, or the ask is "is this ready", "prove it", "write tests". Load it ALONGSIDE the domain skill, never instead. Skip only if nothing asserted is an amount.
license: MIT
---

# Proving a Money Path

Every control in this suite can be written down and still not exist. This skill owns the proof: that the
control is wired to something a human reads, that it detects a discrepancy you plant in it, and that it holds
under generated sequences. The domain skills own *designing* the control. `fin-ledger` owns what a posting
is, `fin-exchange-integration` owns what a reconnect must re-fetch; you own whether any of it runs. Ask of
every control in the diff: **if this were silently removed tomorrow, which test goes red?** If the answer is
"none", the control is already absent.

Several failures that look like design failures are verification failures: a write-ahead field the recovery
path never reads back; a dedupe set that evaporates on restart, exactly when the double-count happens; a
journal that is written and does not balance. Knight emitted 97 "Power Peg disabled" emails in the 89 minutes
before the open and nobody read them: the signal existed, and nothing proved it reached a reader.

## When this applies

Load this skill **in addition to** the domain skill, never instead of it, when any of these is observable:

- the diff adds or edits `tests/`, `test_*.py`, `*_test.go`, `*.spec.ts`, `conftest.py`, a fixture or a cassette;
- it contains `hypothesis`, `@given`, `@rule`, `RuleBasedStateMachine`, `fast-check`, `fc.commands`, `proptest`,
  `jqwik`, `vcr`, `record_mode`, `toxiproxy`, `madsim`, `loom`, `jcstress`, `-race`, `mutmut`, `cargo-mutants`;
- any symbol matches `reconcil|recon_|break|drift|suspense|clearing|kill_switch|halt|dead_man`, or a base URL
  contains `testnet`, `sandbox`, `paper`, `-uat`, `-sim`;
- the task text is "is this ready", "prove it", "how do I verify", "reconciliation", "review before ship",
  "roll back", "write tests";
- **or** the tier is T2 or above, though nobody said the word "test". A live multi-venue team asked to "add a
  third venue" never says it, and is exactly the team with no reconciliation.

**Skip** when the asserted values are analytics that never become an obligation (backtest statistics, greeks,
implied vol, Monte Carlo) and no balance, order, payment or transfer is written.

## The seven non-negotiables

### 1. The reconciliation's contents are a contract (*specialises G7*)

G7 gets you a scheduled entrypoint with an alert destination read from a config key with no default. Here is
what that job must contain, or it ships green and finds nothing:

- **Authority and join key, named per quantity.** Join on the **counterparty's own identifier**:
  `pspReference`, the venue's `tradeId`/`orderId`, the chain's `(blockHash, txHash, logIndex)`. **Never on
  yours.** `merchantReference` and `clientOrderId` are assigned by you and are not unique: one retried attempt
  produces two `pspReference`s under one `merchantReference`, and the join silently drops a row.
- **A read path independent of the writer.** A job that re-reads the cache the writer populated finds
  arithmetic bugs and can never find a missing write. A reconciliation that compares the cache to itself is
  the usual shape of this mistake, and it passes forever.
- **A cadence stated against the authority's documented lag** (report availability, statement cutover,
  replication lag), not a round number.
- **A tolerance in the instrument's own unit.** nautilus pairs `DEFAULT_TOLERANCE = 0.0001` with a
  *single-unit* tolerance keyed on the instrument's size precision (`reconciliation/positions.rs:40`, `:423`).
  A fixed epsilon is wrong for both a 0-decimal and an 18-decimal asset.
- **A `break` row** (`detected_at, source_a, source_b, amount, currency, status`) in an **aged bucket** with
  a hard escalation threshold and a fixed sweep. The Fed's Difference account is swept monthly and expensed.
- **A first run that survives un-backfilled history.** A per-account reconciliation is routinely broken on day
  one by opening balances nobody backfilled, which guarantees the alert gets muted.

This is the step most integrations skip, in every domain: nothing queries the venue's position endpoint,
nothing asserts `sum(signed fills) == size`, and the venue's own realized-PnL field arrives on every event and
is never cross-checked. The best open-source trading platform computes the continuous check and discards it
with `let _ =` (`execution/src/engine/mod.rs:1737`), under a docstring calling it "the core invariant
maintained here".

### 2. Prove the reconciliation detects

**Feed the reconciliation a known discrepancy and assert it produces the break record and fires the alert to
the routed channel.** Rule 1 requires the job to run; nothing else requires it to *detect*.

The test seeds a mismatch of a known amount on a known account against a **freshly-migrated** database, runs
the job, and asserts four things: a `break` row exists with the right `amount`, `currency`, `source_a`,
`source_b`; the suspense posting leaves the trial balance balanced; the alert sink received **exactly one**
message; and a clean run produces **zero** breaks. Running it against a fresh migration is what makes an
un-backfilled opening balance fail the test instead of muting production. This is the cheapest test in the
suite and it protects every reconciliation in every other skill at once.

### 3. Kill the process at every phase boundary

An **atomic phase** is the set of local mutations between two foreign mutations, so there are exactly three
boundaries: **after the intent COMMIT and before the call**; **after the call and before the outcome write**;
**after the outcome write and before the publish**. At each one: `kill -9`, restart, run the recovery path,
assert **exactly one external effect and exactly one local record**.

**Every field written ahead of the effect MUST be read back by that test's recovery path.** Persisting the
write-ahead client id is the easy half and the half that gets done; consuming it on recovery is the half that
gets skipped, leaving a `resume()` that calls `get_order(None)` and raises `ValueError` on exactly the crash
the journal exists for.

### 4. The ambiguous-response test uses a delivered-then-failed request

**A test that stubs the call to raise before delivery tests nothing.** The failure mode is the request that
*arrived*. Use a Toxiproxy `timeout` toxic ("stops all data from getting through, and closes the connection
after timeout") or a stub that writes the request upstream and then closes.

Assert three things: **no resubmission occurs**; the code **queries by the identity it minted**; **exactly one
effect exists** afterwards. Then run the mirror case where the effect genuinely did not happen and assert the
retry **does** fire. CCXT re-POSTs a create-order on `RequestTimeout` because `RequestTimeout extends
NetworkError extends OperationFailed` and the single funnel's retry predicate is `e instanceof
OperationFailed` (`ts/src/base/Exchange.ts:6435`) with no HTTP-method discrimination, and the re-sent request
carries the identical `newClientOrderId`.

### 5. Replay and permute every event consumer

Take a recorded event stream, apply it in the recorded order, then apply **shuffled permutations of it with
duplicates and with a restart in the middle**, and assert final state is **byte-identical every time**.

Assert conservation and idempotence **after every step, not only at the end**. That is what catches an
in-memory dedupe set (a `_seen_trade_ids` set that lives only in the process, so a restart re-applies every
counted fill), a watermark stored on the live object, and an illegal transition, in one pass. Then assert
**generator coverage explicitly**: does the generator ever emit a duplicate at a terminal state, or a
restart between the effect and the outcome write? jqwik's `injectDuplicates()` and `Statistics.coverage` are
the mechanism; nautilus ships the order-invariance form as `test_avg_px_invariant_to_fill_arrival_order`.

### 6. Name the provenance before you assert

**Before writing an `assert`, `panic`, `abort`, `process::exit` or unhandled throw on a money path, name the
provenance of every value in the asserted relation. If any of them crossed a network, a file, a config or a
clock, it is an operating error and needs a fail-closed guard, not an assertion.**

A ledger at rest loses nothing; a position at rest loses money and an unattended resting order keeps filling.
TigerBeetle asserts in production at ~1 per 10.6 lines (487 in 5,166): *"assertions downgrade catastrophic
correctness bugs into liveness bugs"*, asserting "the positive space that you do expect AND the negative space
that you do not expect", on six replicas with state in a replicated WAL. nautilus compiles its three
`debug_assert!`s out of release. Both are correct; the difference is deployment topology, not rigour. The
Polymarket overfill assertion looked like a programmer error and was an operating error: the violating
`last_qty=5.012345` against `quantity=5.000000` came from the venue.

### 7. The design-notes section is a list of claims (*specialises G3*)

G3 says check each asserted property against the code. The mechanism: **enumerate the design-notes and
docstring block as a numbered claim list before you read the implementation**, then bind each number to a test
name or delete the sentence. Two shapes recur. The **ordering claim**: "the flush guarantees the row exists
before the call", documented at line 288 and performed at line 248. The **exactly-once claim**: "move money
into `refunded_cents` exactly once", over a branch whose body is `pass`. The asserted invariant is
repeatedly exactly where the bug lives, because the assertion is what let it survive review.

## The minimum viable test set for a small live bot (T1)

Five tests and one scheduled job. Demanding deterministic simulation from someone writing a 300-line bot buys
nothing. Its dominant risk is a duplicate order after a timeout, not consensus divergence. Write these, stop.

1. **`test_timeout_that_already_filled`**: rule 4 applied to `POST /order`. Toxiproxy `timeout` toxic, or a
   stub that delivers then hangs. Assert: no resubmit; the bot calls its query-by-`clientOrderId` path; exactly
   one order exists. Run the mirror case where the order genuinely does not exist and assert the retry fires.
2. **`test_generated_order_satisfies_every_filter`**: a property test over `(price, qty)`, including values
   near `minQty`, near `minNotional`, and with more decimals than `tickSize`/`stepSize` allow. Assert every
   filter simultaneously (`price % tickSize == 0`, `qty % stepSize == 0`, `price*qty >= minNotional`,
   `qty <= maxQty`), that normalisation never returns `qty == 0` without an explicit skip signal, and that
   rounding is always *toward* validity and never increases size beyond available balance. Parameterise LIMIT
   vs MARKET so `MARKET_LOT_SIZE` is exercised. Drive filter values from a **production** `exchangeInfo` fixture.
3. **`test_fill_stream_replayed_shuffled_and_duplicated`**: rule 5 on one recorded session: place, partial,
   partial, amend/cancel, reject, disconnect, reconnect-with-replay. Assert `position == sum(signed filled
   qty)` and `cash == -sum(price*qty) - sum(fees)` computed **independently of the bot's own accumulators**,
   and that its fee equals the venue's reported fee per fill. Then feed the same stream with duplicates and one
   swapped pair and assert identical terminal state.
4. **`test_kill_after_send_places_no_second_order`**: rule 3's first boundary only. `SIGKILL` between "request
   sent" and "order persisted locally"; restart; assert startup reconciliation converges to exactly one order
   and the correct position. If the bot has no startup reconciliation, this test is what forces you to write one.
5. **`test_limits_hold_and_ambiguity_halts`**: a property test over generated sequences of fills, rejects and
   reconnects. Assert that at no point does position exceed `max_position`, notional exceed `max_notional`, or
   orders-per-minute exceed the cap; and that an ambiguous reconciliation result puts the bot in a state that
   places no new orders **while cancels still work**.

**Plus the daily comparison**, which is rule 1 at T1 cadence and is not optional: one scheduled entrypoint
asserting `sum(signed fills) == venue position` and `local free balance == venue balance` per asset, joined on
the venue's `tradeId`, alerting to a config key with no default.

**Deliberately excluded at this size:** model-based testing, deterministic simulation, race detectors, mutation
testing, Jepsen, loom. A 300-line bot with these five tests and the daily comparison is in far better shape
than one with 95% line coverage of its indicator math.

## Properties that actually matter for money

Five, and the rest are decoration:

- **Conservation**: `sum(all account deltas) == 0` after **every** step, and globally
  `Σ balances + fees == Σ deposits − Σ withdrawals`, not only per entity.
- **Idempotence**: `f(f(x)) == f(x)` for every operation a retry can repeat.
- **Permutation-invariance**: only for operation sets your design *claims* are order-independent. If it is not
  claimed, do not assert it: find out which order the design depends on and test that order.
- **Reservation-implies-postable**: no reachable state holds a committed reservation that cannot be posted.
- **Allocation totality**: `sum(allocate(total, weights)) == total` over generated totals and degenerate
  weight vectors.

**Model-based testing** drives the implementation and an independently written, deliberately **naive reference
model** from one generated command sequence. Derive the model from the specification or the venue's docs,
**never from the implementation**, and keep it in a separate module, or you are testing code against
itself. `fc.commands`/`fc.asyncModelRun`, `RuleBasedStateMachine` with `@invariant()`,
`proptest-state-machine`, jqwik `ActionSequenceArbitrary`.

**Concurrency.** Write the double-spend reproduction as a two-connection **barrier** test (both transactions
read, both block on a barrier, both write), never as a loop of threads hoping to hit the window. loom and
jcstress exhaustively permute interleavings of hand-written lock-free structures under the C11 memory model,
and loom cannot see a type that is not a loom replacement type: on a database transaction or a mutex they
report nothing.

## When an invariant fires at runtime: classify, then act

**"Halt" names six different actions with wildly different blast radii. Name which one, in the code, at the
call site.**

| # | Halt level | What it does | Obligations |
|---|---|---|---|
| 1 | Reject the operation | this call fails, typed; process fine | untouched |
| 2 | Freeze one aggregate | writes to that account/symbol refused; reads still serve | untouched |
| 3 | Fail-closed (risk-off) | no new or increasing exposure; cancel, close, flatten, settle stay hot | actively managed |
| 4 | Cancel-all + disconnect the emitter | withdraw resting orders, sever order entry; risk and drop-copy stay up | actively managed |
| 5 | Quiesce | stop accepting *and* producing; drain; deliver or explicitly void everything already produced | drained, then frozen |
| 6 | Process abort | `panic` / `exit` | **abandoned** |

Evaluate the predicates in order; first match wins. The last column names who designs the response. You prove
it exists, is at the smallest scope that provably contains the breach, and is reachable by a test.

| Observable predicate | Response | Designed in |
|---|---|---|
| No external effect yet, and the check runs in the same transaction as the write | **Level 1**, typed, terminal for that idempotency key. Never `log.warn` and proceed; never clamp into range | `fin-money-core`, `fin-ledger` |
| Wrong value in your own store; no counterparty acted on it; no open position | **Level 2**, write path only. **No automatic corrective write**; repair is a separate reviewed job | `fin-ledger` |
| An external record disagrees with yours, and money left / a fill happened / a customer saw the balance | Neither halt nor silent reversal: book to a named, aged, reversible **suspense** account, keep operating, escalate on a clock | `fin-ledger` |
| A position, working order or obligation exists whose value moves without you acting | **Level 3.** Record the true value, alert, close the risk gate for that scope. `cancel_all` and `flatten` MUST work while it is closed, with a test proving it. Reopen only on a successful reconcile, never on a timer, never by the code path that closed it | `fin-exchange-integration` |
| Own output exceeded its bound relative to its input (`orders_out` vs `orders_in`, `shares_issued` vs authorised, `payouts` vs instructions) | **Level 4**, automatic. The bound is checked **on the emit path before the send**, not by a monitor, and the flag must not be resettable by the component that tripped it | `fin-matching-and-settlement` |
| Recomputable from an append-only log that passes its own checksums | Mark the view stale; return a typed `Stale{as_of}`, never a stale number, never zero; rebuild | `fin-ledger` |
| Every value in the relation was produced by this process, with no network, file, config or clock (rule 6) | **Level 6.** Crash | — |

Six prohibitions, each traceable to an incident, and each a discipline failure, not an ignorance one:

- **Never abort a process holding unmanaged obligations.** Ariane 501: *"It was the decision to cease the
  processor operation which finally proved fatal."*
- **Never let the failure path create state while the system is live and aberrant**: no retry, no resubmit,
  no hot rollback. Knight ¶27: *"This action worsened the problem."*
- **Never disable the failing check as the mitigation.** NASDAQ 2012 removed the validation code from the
  failover path to get the cross out, and that is what created the error position.
- **Never silently drop the violating event, and never clamp a reported quantity into range.** The units were
  really received. nautilus's `allow_overfills` defaults to `false`, which discards the fill report entirely.
- **Never gate the risk-reducing path on the same flag that gates the risk-increasing path.**
- **Never implement a halt by severing the transport.** `halt ⇒ engine quiesced ∧ everything already produced
  delivered or explicitly voided.`

Where the invariant can be transiently false by design, give it a self-heal window before escalating. LULD
waits 15 seconds in Limit State before pausing. A check that halts on a momentarily-inconsistent intermediate
state is itself an availability bug.

## Tier gates the evidence, never which rules apply

**Tier from Axis B and the observable signal table.** Axis B is: *does an external oracle exist that you can
reconcile against?* A bot has one: the exchange. A payments integrator has one: the processor. **A matching engine,
custodian or system-of-record ledger cannot: it IS the oracle.** When Axis B is "no", reconciliation is
unavailable as a safety net and the proof burden moves before deployment, into simulation. **That is the only
principled reason to demand deterministic simulation. Code complexity, team size and how important the system
feels are not reasons.** Do not compute "max loss per erroneous action × actions per second": it is not
observable from a diff, and the fabricated number then sets every downstream evidence requirement.

- **T0 → T1** is crossed by *an order can actually be sent*. Property tests become required because filters
  and rounding are adversarial input spaces the author cannot enumerate.
- **T1 → T2** is crossed by *someone else eats the error*. Loss is no longer bounded by capital deliberately
  exposed, which is what justifies model-based testing, an independent reconciliation path, network-level
  fault injection, and shadow diffing for any rewrite.
- **T2 → T3** is crossed by *no external oracle exists*. A bug is not detectable by reconciliation after the
  fact, so the only place to find it is a simulator. "Correct" becomes a *claimed consistency model*, which is
  exactly what an external adversarial audit tests and an internal simulator systematically under-tests.

At T3, deterministic simulation means closing the system to nondeterminism (FoundationDB deploys one node per
core and avoids multithreaded concurrency; madsim shims `getrandom`, `getentropy`, `clock_gettime`), injecting
faults *inside* production code paths (`buggify`), randomising tuning parameters so none becomes load-bearing,
and proving determinism with a meta-test that runs one seed twice and byte-compares the trace. Then assert
generator coverage with FDB-style `TEST(cond)` counters: TigerBeetle ran the VOPR on 1024 cores 24/7 and Jepsen
still found two safety bugs plus seven crashes, because the fuzzer generated matching objects always
consecutive in the index, so the zig-zag merge join's probe path was never exercised. **DST does not subsume
an external adversarial pass.** Antithesis makes DST purchasable, hence *recommended*, not wasteful, at T2.

## Fixtures, shadow diffs, and technique blind spots

**Record HTTP and WebSocket fixtures from production endpoints and replay them with `record_mode="none"`, so
any unrecorded request fails the test.** Testnet proves protocol conformance and nothing else: its order books
are independent and unsynchronised, are wiped periodically, expose only `/api` and not `/sapi`, carry different
filter thresholds, and can receive a breaking API change **before** production. Binance SPOT testnet shipped
the `MIN_NOTIONAL` → `NOTIONAL` rename first. A testnet fixture gives you the wrong `tickSize`, and the filter
property test then proves nothing. A dry-run is optimistic by construction: freqtrade's fills a market order
from order-book volume with 5% maximum slippage and assumes `stoploss_on_exchange` fills at the stop price.
Never report or gate on dry-run PnL as if it were realistic. The cassette set must include **reject,
partial-fill-then-cancel, over-fill/residual, 429-then-418, and mid-stream disconnect**. Happy-path fills test
nothing about the branches that lose money.

**Shadow diffing** is the cheapest oracle available for a rewrite at T2 and above: run the new implementation
against production traffic, compare its economic outputs to the incumbent's, alert on divergence beyond one
minor unit, ship nothing until the diff is empty for a stated volume.

A race detector will never find a double-spend. A lost update across two transactions is not a data race, and
it *"can't find races in code paths that are not executed."* Line coverage does not show whether money math
asserts anything: run mutation testing on the rounding, fee, allocation and PnL modules **only**, and demand a
high score there rather than chasing coverage.

## REQUIRED OUTPUT: the ship verdict

Every response that reviews, tests or approves a money path ends with this block. Fill every slot. A slot with
no `file:line` is an ABSENT row, and one ABSENT row makes the verdict NO-SHIP.

```
SHIP VERDICT
Tier:            T<n>  (Axis B: external oracle = yes|no — <the signal that decided it>)
Reconciliation:  <entrypoint file:line> · authority <name> · join key <counterparty's own field>
                 · cadence <interval, vs the authority's stated lag> · detect-test <test name>
Breach class:    <the predicate that matched> → halt level <1–6>, implemented at <file:line>
Evidence:        one row per technique this tier requires (references/tier-matrix.md):
                 <technique> | <test name> | <file:line> | PRESENT|ABSENT
NAMED RISKS:     risk | implemented at file:line | test name
Verdict:         SHIP  —  or  NO-SHIP: <the first ABSENT row, named>
```

## References

| File | Read it when |
|---|---|
| [tier-matrix.md](references/tier-matrix.md) | **Before you call any technique required, recommended or wasteful**, and whenever you emit a `Tier:` line. Read it in order; do not summarise it. |
| [reconciliation-design.md](references/reconciliation-design.md) | The diff or task contains `reconcile`, `recon`, `break`, `suspense`, `clearing`, `drift`, or a scheduled job comparing two sources → read it immediately and apply it in order. Do not summarise it. |
| [property-and-model-testing.md](references/property-and-model-testing.md) | The diff contains `hypothesis`, `@given`, `@rule`, `RuleBasedStateMachine`, `fast-check`, `fc.commands`, `proptest`, `jqwik`, `mutmut`, `cargo-mutants`, `Stryker`, or `PIT` → read it immediately and apply it in order. Do not summarise it. |
| [fault-injection-and-replay.md](references/fault-injection-and-replay.md) | The diff contains `toxiproxy`, `vcr`, `cassette`, `record_mode`, `SIGKILL`, `kill -9`, `madsim`, `turmoil`, `antithesis`, `loom`, `jcstress`, `-race`, or a partition/nemesis harness → read it immediately and apply it in order. Do not summarise it. |
