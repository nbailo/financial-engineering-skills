# Proof mechanisms: which mechanism proves which property

The domain skill names the property that has to hold. This file names the mechanism that proves it, and says
what makes each proof real rather than decorative. Nothing here is owed because customer money exists.

Three fields shape the proof, and none of them is an ordinal:

- **The mechanism in scope decides which properties need proving.** A change to fee arithmetic and a change
  to a reconnect path owe different evidence. Take the property list from the domain skill, then come here
  for the mechanism that proves each one.
- **Authority decides the primary oracle.** `EXTERNAL` means an outside system holds the truth and can tell
  you that you are wrong, so comparison against it is the primary proof and it is available. `SELF` means
  nothing outside can, so replay, determinism and conservation carry the whole burden.
- **Exposure adjusts depth.** Whose money is lost when the code is wrong: `own` capital, a `customer`'s
  funds, or the integrity of a `record` other systems consume. Depth means cadence, how many crash
  boundaries you execute, whether the read path is independent of the writer, and who writes the adversarial
  pass. Exposure never adds a technique by itself.

Do not compute a "blast radius per unit time". It is not observable from a diff, and a fabricated number
then sets every downstream evidence claim.
## Contents

- [Reading authority and exposure off a diff](#reading-authority-and-exposure-off-a-diff)
- [Authority is a property of a quantity](#authority-is-a-property-of-a-quantity)
- [The floor every money path owes](#the-floor-every-money-path-owes)
- [Property to mechanism](#property-to-mechanism)
- [Primary oracle when authority is EXTERNAL](#primary-oracle-when-authority-is-external)
- [Primary oracle when authority is SELF](#primary-oracle-when-authority-is-self)
- [Conditional mechanisms and the trigger each one needs](#conditional-mechanisms-and-the-trigger-each-one-needs)
- [Cost and blind spot, per technique](#cost-and-blind-spot-per-technique)
- [What exposure changes and what it does not](#what-exposure-changes-and-what-it-does-not)
- [No reachable effect: paper, read-only, research](#no-reachable-effect-paper-read-only-research)
- [Emitting the evidence rows](#emitting-the-evidence-rows)

## Reading authority and exposure off a diff

| Field and value | Observable signals |
|---|---|
| **no reachable effect** | No value-moving call reachable from an entrypoint, **or** every such call sits behind a `dry_run`/`simulate`/`paper` guard, **and** every base URL is a testnet/sandbox/paper host. |
| **exposure: own** | A value-moving call (`create_order`, `charges.create`, `transfer`, `eth_sendRawTransaction`, `withdraw`, a journal write) is reachable from an entrypoint **and** a live credential path exists: `os.environ['*_API_KEY']`, a secrets-manager read, or a client constructed against a non-sandbox host. |
| **exposure: customer** | Any of: an `account_id`/`user_id`/`customer_id`/`tenant_id` column on a balance/position/holdings row or as a parameter of the money-moving function; a payout or withdrawal path; a webhook or callback that credits; **two or more** venue or processor adapters; a transfer whose two sides belong to different principals. |
| **exposure: record** | Any of: a loop matching or allocating across resting orders; a `journal_entry`/`ledger_entry` writer that is **not** a mirror of an external processor; a custody signer holding user keys; assignment of trade/transfer IDs other systems consume; a sequencer, consensus or settlement-batch path; a mint/burn authority. |
| **authority: EXTERNAL** | A venue, processor, rail or chain publishes the quantity you also hold, on an identifier it minted. Name it: `EXTERNAL (Binance)`, `EXTERNAL (Stripe)`, `EXTERNAL (Ethereum)`. |
| **authority: SELF** | Nothing outside publishes the quantity. You mint it, and a wrong value is not detectable after the fact by comparison. |

**Raise exposure one step, and always say why:** a `SELECT` then `UPDATE` on a balance column in separate
statements; a money transaction whose isolation level is never explicitly set; a per-account override on a
solvency, credit-limit or liquidation check; an immutable deploy target with multi-day fix latency (an
on-chain contract behind a 7-day governance timelock is `record` pre-deploy regardless of TVL); one codebase
deployed to N chains or regions.

An explicit `authority:` or `exposure:` statement in `AGENTS.md` or `CLAUDE.md` overrides inference. It may
raise exposure freely; lowering it takes an explicit user statement, because under-stating exposure is the
dangerous direction.

## Authority is a property of a quantity

Authority belongs to a quantity, not to a codebase. One process routinely holds external authority for
settlement state and self authority for the liabilities it books against that settlement. A broker OMS is
the record for its clients' orders *and* a client of the exchange. Knight Capital sat exactly on that
boundary.

Where one authority covers every quantity in scope, emit the single line. Where it does not, emit
`authority: MIXED` and qualify the quantities that differ, one line each, two or three qualifiers at most:

```
authority: MIXED · exposure: customer
  venue position       EXTERNAL (Binance)
  client order record  SELF
```

Each qualified quantity takes its own primary oracle from the two sections below. The venue-position half
reconciles on the venue's `tradeId`; the client-order-record half has nothing to reconcile against and needs
a deterministic core. Do not build a matrix out of this, and do not qualify a quantity the change does not
touch.

## The floor every money path owes

Two mechanisms apply to every economic change whatever the exposure, because both cost minutes:

- **Money-math unit tests** over rounding, fees and allocation.
- **Exact-representation tests that include the ORM column type and the JSON round trip.** This is the one
  such projects actually fail. Agents do not write `float` in arithmetic; mature projects still declare
  `Float` **columns**, and freqtrade stores every money field as SQL `Float`. Assert the *declaration*: the
  ORM column is `Numeric(38, 18)`/`DECIMAL`, never `Float`/`DOUBLE PRECISION`/protobuf `double`, and a value
  survives `dump` then `load` with its scale intact.

Everything below is earned by a property the change actually puts at risk. A verdict that lists techniques
the change does not touch trains the reader to skip the block.

## Property to mechanism

| Property the domain skill requires | Mechanism that proves it | The proof is real when |
|---|---|---|
| An amount is exact from arithmetic to storage to wire | Minor-unit or decimal tests over the column declaration and the serialiser | The assertion is on the declared type, and a value survives `dump` then `load` with its scale intact |
| A normalised instruction is legal against every venue constraint at once | Round-trip property test over generated (price, size) in the boundary region of the real constraint set | The constraint values are driven from a **production** fixture, never a hand-written or testnet one |
| Value is conserved | Conservation and sign constraints asserted **after every step** | The invariant is a Hypothesis `@invariant()` or a jqwik `check()` evaluated after every rule, not once at the end, so a transient violation a concurrent reader could see is visible |
| A retry cannot double an effect | Idempotence property on every retryable operation | The key is the **counterparty's** event id, not yours |
| A set the design claims commutes really commutes | Permutation-invariance, **only** where the design claims it | It is not asserted on something order-dependent, which passes and hides the ordering bug |
| An ambiguous outcome is resolved by query, never by resubmission | Delivered-then-failed test, plus the mirror case where the failure is provably pre-send | The stub delivers the request before failing, and the mirror case proves the code distinguishes UNKNOWN from "did not happen" |
| A crash between a local commit and a foreign mutation loses nothing | Kill at the boundary, restart, run recovery | Recovery **reads back** every field written ahead of the effect |
| A pushed stream produces one state whatever the arrival order | Recorded-fixture replay, then shuffled, duplicated and restart-interrupted | `record_mode="none"`, so an unrecorded request raises. Left at `once`, an absent cassette means CI silently records against the live API and reports a pass |
| A limit holds under every reachable sequence | Property test over generated fills, rejects and reconnects | The limit is checked on the emit path before the send, and the halt state still allows cancels |
| The comparison detects | Seeded discrepancy of a known size on a known entity | The store is **freshly migrated**, so an un-backfilled opening balance fails the test instead of muting production |
| A reported quantity matches its authority | Scheduled comparison joined on the authority's own key | The read path is independent of the writer, and the join key is the counterparty's, not yours |
| A locally computed fee, notional or average price matches the counterparty's own | Differential check against the counterparty's reported value on every event | The comparison runs per event and alarms beyond one minor unit |

Two details from the crash row and the comparison row are worth stating outright, because both are the half
that gets skipped. Persisting a write-ahead identifier is the half that gets done; consuming it is the half
that gets skipped. Measured on one corpus: 3/3 implementations persisted the write-ahead client id and 1/3
consumed it, so `resume()` calls `get_order(None)` and raises `ValueError` on exactly the crash the journal
exists for. On the differential row, a representative implementation received the venue's own realized-PnL
field on every event and never cross-checked it (G-pnl P9, 0/3).

## Primary oracle when authority is EXTERNAL

The authority can tell you that you are wrong. Use it, and prove it can.

- **The scheduled comparison.** One entrypoint per reported quantity, joined on the identifier the authority
  minted, read through a path independent of the writer. A job that re-reads the store the writer populated
  finds arithmetic bugs and can never find a missing write. Uber's version-gap detector over an
  `EntityChangeLog` works because the log is written by a different path than the store it verifies.
- **The detect-test.** Seed a discrepancy against a freshly migrated store and assert one break row and
  exactly one alert to the routed destination. This is the cheapest test in the suite and it protects every
  reconciliation in every other skill at once.
- **The differential check.** The venue publishes the fee it charged and the executed price and quantity per
  fill; the processor publishes `balance_transaction`. Comparing each locally computed number against it on
  every event catches fee-tier changes and rounding-mode drift that no fixture encodes.

## Primary oracle when authority is SELF

Reconciliation does not disappear. It turns inward: recompute each quantity by replaying its own
append-only entries, cross-check replicas against each other, and check the internal invariants
(`sum(entries) == 0`, `debits <= credits` where configured) continuously. The proof burden moves before
deployment.

- **Make the workload traceable, or the checker cannot reconstruct anything.** Elle's result: dependencies
  are inferable only when versions are *recoverable* (each observed version maps to a specific write) and
  objects are *traceable* (each version has exactly one trace). Blind writes to a register *"destroy
  history"*, and a counter is non-recoverable, because you cannot tell which increment produced a version.
  **A workload that sets balances is nearly uncheckable; one that appends uniquely-identified transfers is
  fully checkable.**
- **Prove determinism with a meta-test that runs one seed twice and byte-compares the trace.** That is the
  test that finds the leak. S2 combined madsim with turmoil and still leaked determinism through HTTP
  timestamp headers inserted by dependencies and Rust's randomized `HashMap` iteration order.
- **Measure generator coverage, because it binds before simulator power does.** `TEST(cond)` counters whose
  cross-run hit counts say whether a scenario is generated at all. TigerBeetle ran the VOPR on 1024 cores
  24/7 and still shipped a query bug, because both merge-capable fuzzers generated queries sharing a common
  prefix, so matching objects were always index-consecutive and the zig-zag merge join's **probe** path was
  never taken.
- **Get an adversarial pass someone else wrote.** sled's guide claims simulation yields systems "Jepsen will
  not find bugs in"; Jepsen then found two safety bugs plus seven crashes in the most simulation-invested
  database in existence. The VOPR corrupted **whole sectors**, always caught by checksums and always
  repaired; Jepsen flipped **single bits in padding**, which passed checksums and hit an assertion. A
  simulator injects the faults its author imagined.

## Conditional mechanisms and the trigger each one needs

Each row loads when the diff changes the mechanism named in the middle column. None of them loads because
exposure is `customer` or `record`.

| Mechanism | Load it when the change touches | Not a trigger |
|---|---|---|
| Model-based testing against a naive reference model | A lifecycle or state machine you are re-implementing, where the reachable states outnumber what a hand-written sequence covers | Customer money, code size, or how important the system feels |
| Network-layer fault injection | A retry, reconnect, timeout, partial-failure or partition path | A path with no counterparty |
| Barrier-based double-spend reproduction | A read-then-write on a balance, or a change of isolation level or locking | Concurrency that touches no shared economic row |
| Mutation testing, money-math modules only | A rounding, fee, allocation or PnL module whose tests are claimed to cover it | Coverage percentage anywhere else |
| Race detector in CI | New shared mutable state, or a hand-rolled concurrent structure | A lost update across two transactions, which is not a data race |
| Shadow or dark-launch diffing | A rewrite of a money path that has an incumbent and production traffic | A greenfield path with no incumbent to diff against |
| Deterministic simulation testing | A self-authoritative state machine, matching, consensus, or distributed ordering | Exposure, complexity, or team size |
| Protocol-aware DST | Consensus or a storage engine you wrote yourself | Using someone else's consensus or storage engine |
| Jepsen-style external adversarial audit | A claimed consistency model | A system whose correctness claim is "matches the venue" |
| Linearizability or Elle cycle checking | A distributed store you wrote | A single-writer store |
| loom, jcstress | A hand-rolled lock-free structure on the money path | A DB transaction or a mutex, where loom sees nothing that is not a loom replacement type |

What each of the heavier rows actually costs and buys:

- **Fault injection, in cost order.** delay/reorder/duplicate, then partition with **asymmetric heal**, then
  process kill at a chosen boundary, then clock skew, then storage corruption. Do not start with disk
  corruption: Antithesis found three HashiCorp Raft bugs in one hour with network partitions alone.
  Toxiproxy toxics are `latency`, `down`, `bandwidth`, `slow_close`, `timeout`, `reset_peer`, `slicer`,
  `limit_data`, applied upstream or downstream independently. `timeout` reproduces the money case: it
  "stops all data from getting through, and closes the connection after timeout."
- **Model-based testing.** Two constraints decide whether it is worth anything: **derive the model from the spec or the venue's docs,
  never from the implementation**, and keep it in a separate module. A model written by the implementer from
  the same misunderstanding encodes the same bug, which is testing code against itself. Jepsen's TigerBeetle
  checker was about 1,600 lines of Clojure written from the docs, and it modelled **error codes** rather than
  amounts alone; a model checking balances alone misses the `Batch.EMPTY` duplicate-timestamp bug.
- **The barrier double-spend is a barrier test, not a thread loop.** Two connections, both `SELECT`, both
  block on a barrier, both write, at the isolation level the money transaction really runs at: that level
  decides whether the single-statement `UPDATE … WHERE balance >= :amt` form is safe, and whether a `40001`
  serialization-failure retry path is itself a required test.
- **Mutation testing.** Line coverage "does not check that your tests are actually able to detect faults"
  (PIT). Demand a high mutation score on the money-math modules rather than chasing coverage elsewhere.
  Stated honestly: no published finance-specific case study of mutation testing was found in the research,
  so this row is reasoned from mechanism.
- **The race detector is never evidence of a money control.** `-race` "can't find races in code paths that
  are not executed", at 5–10× memory and 2–20× time.
- **What DST costs.** Closing the system to nondeterminism. FoundationDB deploys one node per core, avoids
  multithreaded concurrency, and *"largely avoided taking dependencies on external systems"*; madsim shims
  `getrandom`, `getentropy`, `CCRandomGenerateBytes`, `clock_gettime`. Simulation finds no performance bugs
  and tests no third-party dependency. Name the fault levels so a run reports what it exercised: TigerBeetle
  uses *City Breeze* (no faults), *Red Desert* (crashes, flaky network, latency, no corruption) and
  *Radioactive* (8% read-path, 9% write-path corruption per replica), reporting 3.3s of VOPR as 39 minutes
  of real time. Two things people skip are the two that matter: `buggify`-style injection points *inside
  production code paths* (return an unusual-but-legal error, add a delay) and randomised tuning parameters,
  so "specific performance tuning values do not accidentally become necessary for correctness" (FDB).
  Buying DST as a service collapses the cost of closing the system, which is the part that makes a home-made
  harness the wrong call below `record`.
- **Protocol-aware DST** is the marginal step past system-level invariants: assert *per-replica internal*
  invariants (cross-replica commit checksum equality, LSM metadata checksum equality across levels,
  byte-for-byte superblock/grid/client-reply equality) plus deep liveness: *"replicas should never wind up
  in a state where they need to coordinate"* when their logs are uncorrupted.

## Cost and blind spot, per technique

| Technique | Adoption cost | Catches | Structurally cannot find |
|---|---|---|---|
| `@given` property test on a pure function | hours; no infrastructure | rounding into invalidity, `qty == 0`, filter interaction, exponent assumptions | anything requiring a sequence or shared state |
| Round-trip through the real driver/codec | hours; needs a real DB in the test | `Float` columns, JSON precision loss above `2**53`, protobuf `double` | a value that never crosses the boundary in test |
| `RuleBasedStateMachine` + naive model | 1–3 days per component; the model is the cost | interleaved lifecycle bugs, in-memory dedupe (with a `restart` rule), unpostable reservations, error-code divergence | bugs the model shares with the implementation; anything outside the generated command alphabet |
| Generator-coverage assertions | hours once the machine exists | the blind spot that makes every other generated test a false negative | nothing, but it only reports what you thought to name |
| Mutation testing, money-math modules only | ~1 day to scope; minutes-to-hours per CI run | tests that execute money math without asserting its boundary | anything outside the mutated modules; equivalent mutants waste the reviewer's time |
| Differential vs a second implementation | hours if the incumbent still runs | rounding-mode drift, fee-tier changes, allocation remainder handling | a defect both implementations share |
| Differential vs the venue's reported fee/price | hours; runs in production, free | fee-tier and rebate changes no fixture encodes | anything the venue does not report |
| Two-connection barrier test | hours; one per contended row-shape | lost update, missing `40001` retry, a `FOR UPDATE` that was never taken | interleavings you did not name; anything not on that row |
| loom / jcstress | days, and only if the target exists | memory-model bugs in hand-written lock-free structures | database transactions, mutexes, anything not using loom's replacement types |
| Race detector in CI (`-race`, TSan) | ~1 hour; 5–10× memory, 2–20× time | in-process data races on executed paths | **double-spend**: a cross-transaction lost update is not a data race |
| Line coverage | free | untested files | whether a single executed line asserts anything |

## What exposure changes and what it does not

Exposure moves the depth dials on mechanisms already selected. It does not select mechanisms.

| Dial | own | customer | record |
|---|---|---|---|
| Crash boundaries executed | At least the one the change crosses | All three: after the intent commit, after the call, after the outcome write | All three |
| Comparison cadence | Daily is enough | Stated against the authority's documented lag, not a round number | Continuous, and internal |
| Read path for the comparison | May share infrastructure with the writer | Independent of the writer | Independent, and cross-replica |
| Who writes the adversarial pass | You | You, or a second person | Someone outside the team |
| Rewrite of an existing money path | Tests | Shadow diff against the incumbent until the diff is empty for a stated volume | Shadow diff plus replay of recorded history |

That is the whole of it. A `customer_id` on a balance row raises the dials in this table. It does not
conjure a race detector, a reference model or a simulator into the requirement list.

## No reachable effect: paper, read-only, research

The floor and nothing else. A backtest that asserts rounding and unit correctness and nothing else is
correctly tested.

- **Never report or gate on dry-run PnL.** freqtrade's dry-run fills market orders from order-book volume
  with 5% maximum slippage, fills limit orders when price touches the level, and assumes
  `stoploss_on_exchange` filled at the stop price. Every assumption biases PnL upward: dry-run is a plumbing
  test, not an economics test. Label it an upper bound and state the fill assumptions.
- **This state is fragile.** It holds only while *every* value-moving call is guarded *and* every base URL
  is a sandbox host. One `if not dry_run:` removed, one env var flipped, and exposure is `own`. If the diff
  touches that guard, treat exposure as `own` now.

## Emitting the evidence rows

One row per property the change puts at risk, naming the mechanism that proves it. `PRESENT` needs a real
test name and a real `file:line`. A mechanism named without one is `ABSENT`, and one ABSENT row is NO-SHIP.
Do not emit rows for a property the change does not touch.

```
Evidence:        ambiguous outcome resolved by query | test_timeout_after_delivery      | tests/test_pay.py:140  | PRESENT
                 crash boundary loses nothing        | test_kill_between_call_and_write | tests/test_crash.py:31 | PRESENT
                 the comparison detects              | test_seeded_break_alerts_once    | tests/test_recon.py:19 | PRESENT
```

The fuller ship-verdict block belongs to a review or a release decision. Emit only the slots the change
touches. A slot emitted with no `file:line` is ABSENT, and one ABSENT slot makes the verdict NO-SHIP.

```
SHIP VERDICT
Reconciliation:  <entrypoint file:line> · authority <name> · join key <counterparty's own field>
                 · cadence <interval, vs the authority's stated lag> · detect-test <test name>
Breach class:    <the predicate that matched> -> halt level <1..6>, implemented at <file:line>
Evidence:        <the reconciliation detect-test actually run> · <the crash-boundary test actually run>
Verdict:         SHIP, or NO-SHIP: <the first ABSENT slot, named>
```

A separate risk table is never needed: each control already carries its `file:line` and its test name.
