# Evidence by risk: exposure and authority

Which verification techniques are **required**, **recommended**, or **actively wasteful**, and why each
boundary sits where it does. Two orthogonal fields decide it, never one ordinal:

- **exposure decides how much evidence.** Whose money is lost when the code is wrong: `own` capital, a
  `customer`'s funds, or the integrity of a `record` other systems consume.
- **authority decides which kind.** `EXTERNAL` means an outside system holds the truth and can tell you
  that you are wrong, so reconciliation against it is the primary proof and it is available. `SELF` means
  nothing outside can, so replay, determinism and conservation carry the whole burden.

Neither field is "blast radius per unit time". Do not compute one: it is not observable from a diff, and
a fabricated number then sets every downstream evidence requirement. Read this file before telling anyone
a technique is required or unnecessary. The wasteful column is as load-bearing as the required one,
because demanding deterministic simulation from a 300-line bot is how a verification programme loses its
audience.

**This file branches.** Everything down to the end of *Emitting the evidence rows* is shared. After that,
read the block for your exposure, then the authority block if authority is SELF, and stop. The `record`
block is not a longer `own` block; it is a different set of techniques for a system with no counterparty
to check itself against.

## Contents

- [Legend](#legend)
- [Reading the two fields off a diff](#reading-the-two-fields-off-a-diff)
- [The evidence table](#the-evidence-table)
- [Boundary justifications](#boundary-justifications)
- [Emitting the evidence rows](#emitting-the-evidence-rows)
- [No reachable effect: paper, read-only, research](#no-reachable-effect-paper-read-only-research)
- [Exposure own: live, own capital, bounded loss](#exposure-own-live-own-capital-bounded-loss)
- [Exposure customer: someone else eats the error](#exposure-customer-someone-else-eats-the-error)
- [Authority SELF, exposure record: no external oracle](#authority-self-exposure-record-no-external-oracle)
- [Assertion placement](#assertion-placement)

## Legend

| Mark | Meaning |
|---|---|
| **R** | **Required.** Its absence is an ABSENT row in the ship verdict, and one ABSENT row is NO-SHIP. |
| **r** | **Recommended.** Worth the cost here; absence is a finding, not a blocker. |
| **–** | Not required here. Do it if it is free; do not ask for it. |
| **✗** | **Overkill: actively wasteful.** It consumes the budget the **R** rows in the same column need. |

## Reading the two fields off a diff

| Field and value | Observable signals |
|---|---|
| **no reachable effect** | No value-moving call reachable from an entrypoint, **or** every such call sits behind a `dry_run`/`simulate`/`paper` guard, **and** every base URL is a testnet/sandbox/paper host. |
| **exposure: own** | A value-moving call (`create_order`, `charges.create`, `transfer`, `eth_sendRawTransaction`, `withdraw`, a journal write) is reachable from an entrypoint **and** a live credential path exists: `os.environ['*_API_KEY']`, a secrets-manager read, or a client constructed against a non-sandbox host. |
| **exposure: customer** | Any of: an `account_id`/`user_id`/`customer_id`/`tenant_id` column on a balance/position/holdings row or as a parameter of the money-moving function; a payout or withdrawal path; a webhook or callback that credits; **two or more** venue or processor adapters; a transfer whose two sides belong to different principals. |
| **exposure: record**, and **authority: SELF** | Any of: a loop matching or allocating across resting orders; a `journal_entry`/`ledger_entry` writer that is **not** a mirror of an external processor; a custody signer holding user keys; assignment of trade/transfer IDs other systems consume; a sequencer, consensus or settlement-batch path; a mint/burn authority. |
| **authority: EXTERNAL** | A venue, processor, rail or chain publishes the quantity you also hold, on an identifier it minted. Name it: `EXTERNAL (Binance)`, `EXTERNAL (Stripe)`, `EXTERNAL (Ethereum)`. |

**Raise exposure one step, and always report why:** a `SELECT` then `UPDATE` on a balance column in
separate statements; a money transaction whose isolation level is never explicitly set; a per-account
override on a solvency, credit-limit or liquidation check; an immutable deploy target with multi-day fix
latency (an on-chain contract behind a 7-day governance timelock is `record` pre-deploy regardless of
TVL); one codebase deployed to N chains or regions.

**Split the diff when one process is both sides.** A broker OMS is the record for its clients' orders
*and* a client of the exchange: the client half is `authority: EXTERNAL, exposure: customer` and
reconciles by client order ID, the venue half is `authority: SELF, exposure: record` and needs a
deterministic core. One declaration must not cover both; Knight Capital sat exactly on this boundary.

An explicit `authority:` or `exposure:` statement in `AGENTS.md` or `CLAUDE.md` overrides inference. It
may raise exposure freely; lowering it takes an explicit user statement, because under-stating exposure is
the dangerous direction.

## The evidence table

| # | Technique | no effect | own | customer | record |
|---:|---|---|---|---|---|
| 1 | Unit tests on money math (rounding, fees, allocation) | R | R | R | R |
| 2 | Exact-arithmetic / minor-unit tests **including the ORM column type and the JSON round-trip** | R | R | R | R |
| 3 | Round-trip property tests (serialize; normalize-to-filters) | r | **R** | R | R |
| 4 | Conservation + sign constraints asserted **after every step** | r | **R** | R | R |
| 5 | Idempotence on every retryable operation; permutation-invariance **only** where the design claims it | – | **R** | R | R |
| 6 | Recorded-fixture replay, production-captured, `record_mode="none"` | r | **R** | R | R (upstream deps) |
| 7 | Ambiguous-response test (delivered-then-failed, then query by minted identity) | – | **R** | R | R |
| 8 | Crash between the effect and the commit | – | **R** (at least 1 boundary) | R (all three) | R (all three) |
| 9 | Pre-trade / pre-post limit invariants (max position, notional, fan-out, kill switch) | – | **R** | R | R |
| 10 | Reconciliation-**detects** test (seeded discrepancy, break row, exactly one alert) | – | **R** | R | R |
| 11 | Continuous production reconciliation + aged break bucket | – | **R** (daily) | **R** (independent path, 1h or better) | R (continuous, internal) |
| 12 | Differential check against the counterparty's own reported value | – | r (fees, balances) | **R** | R (upstream legs only) |
| 13 | Model-based testing against a naive reference model | – | r | **R** | R |
| 14 | Network-layer fault injection (Toxiproxy toxic, nemesis) | – | r | **R** | R |
| 15 | Deterministic replay from a journal + a determinism meta-test | – | r | **R** | R |
| 16 | Shadow / dark-launch diffing against the incumbent | – | – | **R** for any rewrite | R |
| 17 | Barrier-based double-spend reproduction | – | r (if shared state) | **R** | R |
| 18 | Race detector in CI (`-race`, TSan) | – | r | **R** | R |
| 19 | Mutation testing, money-math modules **only** | – | r | **R** | R |
| 20 | Generator-coverage assertions (`Statistics.coverage`, `TEST(cond)`) | – | – | r | **R** |
| 21 | Deterministic simulation testing (own harness or bought) | ✗ | ✗ | r (or buy it) | **R** |
| 22 | Protocol-aware DST (per-replica internal invariants) | ✗ | ✗ | – | **R** if you wrote consensus or storage |
| 23 | Jepsen-style external adversarial audit | ✗ | ✗ | – | **R** if you claim a consistency model |
| 24 | Linearizability / Elle cycle checking | ✗ | ✗ | – | **R** if distributed |
| 25 | loom / jcstress | ✗ | ✗ | r (hand-rolled lock-free only) | r (same) |

Required-row counts, which are the number of `Evidence:` rows a verdict carries: **no effect, 2**;
**own, 11**; **customer, 19** (row 16 only for a rewrite); **record, 21** plus up to three conditional
(rows 22 to 24).

## Boundary justifications

**No effect to `own` is crossed by "an order can actually be sent."** One live credential, or one base URL
that is no longer a sandbox host, moves the whole column. Property tests (rows 3 to 5) become required not
because the code got complex (it did not) but because *filters and rounding are adversarial input spaces
the author cannot enumerate by hand*; and every partial-failure row switches on at once (7, 8, 10, 11),
because a partial failure requires a counterparty who can have received something.

**`own` to `customer` is crossed by "someone else eats the error."** Loss stops being bounded by capital
deliberately exposed, which pays for the step change in rows 13 to 19: a reference model, a reconciliation
path independent of the writer, network-level fault injection. It is also where shadow diffing (16)
becomes required for a rewrite: you now have production traffic and an incumbent, a cheaper and stronger
oracle than any test you can write.

**EXTERNAL to SELF is crossed by "no external oracle exists."** This is the deterministic-simulation
boundary and the only principled one. If you are the system of record, a bug is not detectable by
reconciliation after the fact, so the only place left to find it is before deployment, in a simulator. It
is also where "correct" stops being "matches the venue" and becomes a *claimed consistency model*: what an
external adversarial audit tests and an internal simulator systematically under-tests.

## Emitting the evidence rows

One row per **R** cell in your column. `PRESENT` needs a real test name and a real `file:line`; a technique
named without one is `ABSENT`, and one ABSENT row is NO-SHIP. Do not emit rows for another column's
techniques; a verdict listing DST as ABSENT for a bot trains the reader to ignore the block.

```
Evidence:        ambiguous-response | test_timeout_after_delivery      | tests/test_pay.py:140  | PRESENT
                 crash-at-boundary  | test_kill_between_call_and_write | tests/test_crash.py:31 | PRESENT
                 recon-detects      | test_seeded_break_alerts_once    | tests/test_recon.py:19 | PRESENT
                 model-based        | (none)                           | (none)                 | ABSENT
```

The fuller ship-verdict block belongs to a review or release decision when authority is SELF, exposure is
`record`, or the change rewrites a money path already in production. Emit only the slots the change
touches. A slot emitted with no `file:line` is ABSENT, and one ABSENT slot makes the verdict NO-SHIP.

```
SHIP VERDICT
Reconciliation:  <entrypoint file:line> · authority <name> · join key <counterparty's own field>
                 · cadence <interval, vs the authority's stated lag> · detect-test <test name>
Breach class:    <the predicate that matched> -> halt level <1..6>, implemented at <file:line>
Evidence:        <the reconciliation detect-test actually run> · <the crash-boundary test actually run>
Verdict:         SHIP, or NO-SHIP: <the first ABSENT slot, named>
```

At exposure `record`, `Evidence:` stops being two named tests and becomes the per-technique table above,
one row per **R** cell including the deterministic-simulation rows and the external adversarial pass. A
separate risk table is never needed: each control already carries its `file:line` and its test name.

## No reachable effect: paper, read-only, research

**Required: rows 1 and 2 only**; rows 3, 4 and 6 are `r`, everything else `–` or `✗`. A backtest that
asserts rounding and unit correctness and nothing else is correctly tested.

- **Row 2 is the one such projects actually fail.** Agents do not write `float` in arithmetic; mature
  projects still declare `Float` **columns**: freqtrade stores every money field as SQL `Float`. The test
  asserts the *declaration*: the ORM column is `Numeric(38, 18)`/`DECIMAL`, never
  `Float`/`DOUBLE PRECISION`/protobuf `double`, and a value survives `dump` then `load` with its scale
  intact.
- **Never report or gate on dry-run PnL.** freqtrade's dry-run fills market orders from order-book volume
  with 5% maximum slippage, fills limit orders when price touches the level, and assumes
  `stoploss_on_exchange` filled at the stop price. Every assumption biases PnL upward: dry-run is a
  plumbing test, not an economics test; label it an upper bound and state the fill assumptions.
- **This state is fragile.** It holds only while *every* value-moving call is guarded *and* every base URL
  is a sandbox host. One `if not dry_run:` removed, one env var flipped, and exposure is `own`; if the diff
  touches that guard, treat exposure as `own` now.

**Promoted out by:** a reachable value-moving call plus a live credential path.

## Exposure own: live, own capital, bounded loss

**Required: rows 1 to 11, eleven evidence rows.** Five tests and one scheduled comparison satisfy them.
That is the proportionality proof: at this size, those six carry the risk, and a 300-line bot with them is
in far better shape than one with 95% line coverage of its indicator math. Two of the six ship as
executable code in `fin-exchange-integration` (tests 1 and 2). The rows the five do not name are the
money-math and exact-arithmetic unit tests (rows 1 and 2, including the ORM column type and the JSON
round-trip) that every column owes, and the daily comparison's own detect-test.

1. **`test_timeout_that_already_filled`**: *the ambiguity worth testing is the request that arrived*,
   applied to order submission. A Toxiproxy `timeout` toxic on the downstream, or a stub that delivers
   then hangs. Assert: no resubmit; the bot calls its query-by-`clientOrderId` path; exactly one order
   exists. Run the mirror case, `test_provable_presend_failure_retries`, where the failure is provably
   pre-send (`down` toxic: connection refused) and assert the documented retry path fires.
2. **`test_normalize_satisfies_every_filter_simultaneously`**: a normalised instruction is legal against
   every venue constraint at once, or is an explicit skip, and is never larger than what was asked for.
   Proven as a property test over generated (price, size) in the boundary region of the real constraint
   set, one run per instruction type. Assert all constraints in one pass, that normalisation never returns
   zero without an explicit skip signal, and that rounding moves toward validity and never increases size
   beyond available balance. Drive the constraint values from a **production** fixture, never a
   hand-written or testnet one. On one venue that reads: `price % tickSize == 0`, `qty % stepSize == 0`,
   `price*qty >= minNotional`, `minQty <= qty <= maxQty`, a LIMIT/MARKET parameterisation to exercise
   `MARKET_LOT_SIZE`, and filter values loaded from `exchangeInfo`.
3. **`test_fill_stream_replayed_shuffled_and_duplicated`**: *every arrival order produces the same state*,
   on one recorded session: place, partial, partial, amend/cancel, reject, disconnect,
   reconnect-with-replay. Assert `position == sum(signed filled qty)` and
   `cash == -sum(price*qty) - sum(fees)` computed **independently of the bot's own accumulators**, and
   that its fee equals the venue's reported fee per fill. Then feed the same stream with duplicates and
   one swapped pair and assert identical terminal state.
4. **`test_kill_after_send_places_no_second_order`**: the first boundary of *a crash you have executed*,
   only. `SIGKILL` between "request sent" and "order persisted locally"; restart; assert startup
   reconciliation converges to exactly one order and the correct position. If the bot has no startup
   reconciliation, this test is what forces you to write one.
5. **`test_limits_hold_and_ambiguity_halts`**: a property test over generated sequences of fills, rejects
   and reconnects. Assert that at no point does position exceed `max_position`, notional exceed
   `max_notional`, or orders-per-minute exceed the cap; and that an ambiguous reconciliation result puts
   the bot in a state that places no new orders **while cancels still work**.

**Plus the daily comparison**, which is row 11 at this cadence and is not optional: one scheduled
entrypoint asserting `sum(signed fills) == venue position` and `local free balance == venue balance` per
asset, joined on the venue's `tradeId`, alerting to a config key with no default.

What each remaining cell means when you check it:

| Row | Satisfied by | Fails when |
|---|---|---|
| 3 | Filter values driven from a **production** `exchangeInfo`/instrument fixture | The fixture came from testnet, which carries different filter thresholds and received the `MIN_NOTIONAL` to `NOTIONAL` rename **before** production (ccxt #17545) |
| 4 | The invariant is a Hypothesis `@invariant()` / jqwik `check()` evaluated after every rule | It is asserted once at the end, so a transient violation a concurrent reader could see is invisible |
| 5 | Idempotence keyed on the **counterparty's** event id; permutation-invariance only on sets the design claims commute | Permutation-invariance is asserted on something order-dependent, which passes and hides the ordering bug |
| 6 | `record_mode="none"`, so any unrecorded request raises | `once` is left in place: absent cassette means CI silently records against the live API and reports a pass |
| 8 | `SIGKILL` after the request is sent and before the local write; recovery **reads back** the field written ahead | The write-ahead client id is persisted and never read: measured 3/3 persisted, 1/3 consumed; `resume()` calls `get_order(None)` and raises `ValueError` on exactly the crash the journal exists for |
| 10 | A seeded discrepancy against a **freshly-migrated** database produces a `break` row and exactly one alert | The recon is run against a warm database, so an un-backfilled opening balance breaks it on day one in production instead |
| 11 | One scheduled entrypoint, joined on the **venue's** `tradeId`, alerting to a config key with no default | It joins on your `clientOrderId`, which is not unique across retries, and drops rows silently |

**The trap that is not in the table.** Hypothesis sets `derandomize=True` automatically in CI and its
example database is a local `.hypothesis/examples` directory CI discards; a team expecting fresh random
examples per run gets the same ones forever, losing every counterexample. Commit each as `@example(...)`.

**Do not spend budget here, and say why when asked.** Deliberately excluded at this size: model-based
testing, deterministic simulation, race detectors, mutation testing, Jepsen, loom.

| Row | Mark | Why not at exposure `own` |
|---|---|---|
| 21 DST | ✗ | Requires closing the system to nondeterminism: libc shims for time and entropy, no dependency you cannot simulate. A bot's dominant risk is a duplicate order after a timeout, not consensus divergence |
| 25 loom / jcstress | ✗ | They check memory-model correctness of lock-free structures; loom cannot see any type that is not a loom replacement type, so on a DB transaction or a mutex they report nothing |
| 13 model-based | r | The five tests above already drive the sequences that matter, and a reference model is a second implementation to maintain |
| 18 race detector | r | Never evidence of a money control: a lost update across two transactions is not a data race, and `-race` "can't find races in code paths that are not executed", at 5–10× memory and 2–20× time |

**Promoted out by:** a second venue or processor adapter, a `customer_id` on a balance row, a payout path,
or a crediting webhook.

## Exposure customer: someone else eats the error

**Required: rows 1 to 19, nineteen evidence rows.** Rows 1 to 11 keep the meaning above; below is what
changes.

- **Row 11 tightens twice.** The read path must be **independent of the writer** (a job that re-reads the
  cache the writer populated finds arithmetic bugs and can never find a missing write), and the cadence is
  stated against the authority's documented lag, not a round number. Uber's version-gap detector over an
  `EntityChangeLog` works because the log is written by a different path than the store it verifies.
- **Row 12, differential against the counterparty, is free and now required.** The venue publishes the fee
  it charged and the executed price and quantity per fill; the processor publishes `balance_transaction`.
  Compare every locally computed fee, notional and average price against it on every event; alarm beyond
  one minor unit; this catches fee-tier changes and rounding-mode drift no fixture encodes. Measured
  failure: a representative implementation received the venue's own realized-PnL field on every event and
  never cross-checked it (G-pnl P9, 0/3).
- **Row 13, model-based testing.** `fc.commands` plus `fc.asyncModelRun` (fast-check),
  `RuleBasedStateMachine` with `Bundle`/`consumes()` and `@invariant()` (Hypothesis),
  `ReferenceStateMachine` plus `StateMachineTest` driven by `prop_state_machine!`
  (proptest-state-machine), `ActionSequenceArbitrary` (jqwik). Two constraints decide whether it is worth
  anything: **derive the model from the spec or the venue's docs, never from the implementation**, and
  keep it in a separate module; a model written by the implementer from the same misunderstanding encodes
  the same bug, which is testing code against itself. Jepsen's TigerBeetle checker was ~1,600 lines of
  Clojure written from the docs and modelled **error codes**, not just amounts; a model checking balances
  alone misses the `Batch.EMPTY` duplicate-timestamp bug.
- **Row 14, fault injection, in cost order.** delay/reorder/duplicate, then partition with **asymmetric
  heal**, then process kill at a chosen boundary, then clock skew, then storage corruption. Do not start
  with disk corruption: Antithesis found three HashiCorp Raft bugs in one hour with network partitions
  alone. Toxiproxy toxics are `latency`, `down`, `bandwidth`, `slow_close`, `timeout`, `reset_peer`,
  `slicer`, `limit_data`, applied upstream or downstream independently; `timeout` reproduces the money
  case: it "stops all data from getting through, and closes the connection after timeout."
- **Row 16, shadow diffing, is required for any rewrite of a money path.** Run the new implementation
  against production traffic, compare economic outputs to the incumbent's, alert beyond one minor unit,
  and ship nothing until the diff is empty for a stated volume.
- **Row 17, the double-spend reproduction, is a barrier test, not a thread loop.** Two connections, both
  `SELECT`, both block on a barrier, both write. Under READ COMMITTED PostgreSQL re-fetches the row and
  re-evaluates the `WHERE` for `UPDATE … SET balance = balance - :amt WHERE id = :id AND balance >= :amt`,
  so that form is safe and the SELECT-compute-UPDATE form is not. Under REPEATABLE READ or SERIALIZABLE
  the `40001` serialization-failure retry path is itself a required test; an untested one is where a
  "safe" isolation level silently drops a payment.
- **Row 19, mutation testing, is scoped to rounding, fee, allocation and PnL modules only.** Line coverage
  "does not check that your tests are actually able to detect faults" (PIT). Demand a high mutation score
  *there* rather than chasing coverage. Stated honestly: no published finance-specific case study of
  mutation testing was found in the research, so this row is reasoned from mechanism.
- **Row 21 is `r (or buy it)`, not ✗**, because buying DST as a service collapses the cost of closing the
  system to nondeterminism, the part that makes it wasteful below `record`.

**Promoted out by:** becoming the record (matching, allocating, a ledger that mirrors nothing, a custody
signer, or ID assignment other systems consume), which also flips authority to SELF.

## Authority SELF, exposure record: no external oracle

**Required: rows 1 to 21, plus 22 to 24 where their condition holds.** The defining constraint is that
rows 11 and 12 have lost their counterparty. Reconciliation does not disappear; it turns inward: recompute
each balance by replaying its own append-only entries, cross-check replicas against each other, and check
the internal invariants (`sum(entries) == 0`, `debits <= credits` where configured) continuously.

- **Make the workload traceable or the checker cannot reconstruct anything.** Elle's result: dependencies
  are inferable only when versions are *recoverable* (each observed version maps to a specific write) and
  objects are *traceable* (each version has exactly one trace). "Blind writes to a register destroy
  history", and a counter is non-recoverable: you cannot tell which increment produced a version. **A
  workload that sets balances is nearly uncheckable; one that appends uniquely-identified transfers is
  fully checkable.**
- **What DST actually costs (row 21).** Closing the system to nondeterminism. FoundationDB deploys one
  node per core, avoids multithreaded concurrency, and "largely avoided taking dependencies on external
  systems"; madsim shims `getrandom`, `getentropy`, `CCRandomGenerateBytes`, `clock_gettime`. S2 combined
  madsim with turmoil and still leaked determinism through HTTP timestamp headers inserted by dependencies
  and Rust's randomized `HashMap` iteration order. **Prove determinism with a meta-test that runs one seed
  twice and byte-compares the trace**: that is the test that finds the leak. Simulation finds no
  performance bugs and tests no third-party dependency. Name the fault levels so a run reports what it
  exercised: TigerBeetle uses *City Breeze* (no faults), *Red Desert* (crashes, flaky network, latency, no
  corruption) and *Radioactive* (8% read-path, 9% write-path corruption per replica), reporting 3.3s of
  VOPR as 39 minutes of real time.
- **Two things people skip, and they are the two that matter (rows 20 and 21).** `buggify`-style injection
  points *inside production code paths* (return an unusual-but-legal error, add a delay) and randomised
  tuning parameters, so "specific performance tuning values do not accidentally become necessary for
  correctness" (FDB). Then `TEST(cond)` counters whose cross-run hit counts say if a scenario is generated
  at all. **The binding constraint on DST is generator coverage, not simulator power:** TigerBeetle ran
  the VOPR on 1024 cores 24/7 and still shipped a query bug because both merge-capable fuzzers generated
  queries sharing a common prefix, so matching objects were always index-consecutive and the zig-zag merge
  join's **probe** path was never taken.
- **Row 22, protocol-aware DST**, is the marginal step past system-level invariants: assert *per-replica
  internal* invariants (cross-replica commit checksum equality, LSM metadata checksum equality across
  levels, byte-for-byte superblock/grid/client-reply equality) plus deep liveness ("replicas should never
  wind up in a state where they need to coordinate" when their logs are uncorrupted).
- **Row 23 is required because DST does not subsume it.** sled's guide claims simulation yields systems
  "Jepsen will not find bugs in"; Jepsen then found two safety bugs plus seven crashes in the most
  DST-invested database in existence. The VOPR corrupted **whole sectors**, always caught by checksums and
  always repaired; Jepsen flipped **single bits in padding**, which passed checksums and hit an assertion.
  A simulator injects the faults its author imagined.

## Assertion placement

Both answers in the corpus are correct, and the difference is deployment topology, not rigour.
TigerBeetle asserts in production at about 1 per 10.6 lines (487 in 5,166, `TIGER_STYLE.md:104-113`),
asserting *"the positive space that you do expect AND the negative space that you do not expect"*, on six
replicas with state in a replicated WAL: *"assertions downgrade catastrophic correctness bugs into
liveness bugs"*, and a ledger at rest loses nothing. nautilus compiles its three `debug_assert!`s out of
release, because a panic in a process holding open orders leaves exposure unmanaged, and an unattended
resting order keeps filling.

Jepsen's counterweight: an assertion on **recoverable** state turns a repairable fault into an outage,
which is what the padding bit-flip did.

The discriminator is provenance, not tier. The Polymarket overfill assertion looked like a programmer
error and was an operating error: the violating `last_qty=5.012345` against `quantity=5.000000` came from
the venue, so it needed a typed fail-closed guard and a break record, not a panic. Name the provenance of
every term in the asserted relation first; if any of them crossed a network, a file, a config or a clock,
it is not an assertion.
