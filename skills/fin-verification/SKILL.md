---
name: fin-verification
description: >-
  Proof that a money path is correct: reconciliation against an independent authority,
  planted-break tests that prove it detects, crash-boundary recovery, replay and reordering
  tests, and stronger evidence when the system is its own authority. Use alongside a domain
  skill when a money-path change touches tests or assertions, when reconciliation or a kill
  switch is involved, or when the ask is whether this is ready to ship.
license: MIT
---

# Proving a Money Path

Loaded alongside a domain skill, never instead of one. The domain skill designs the control and names the
property that has to hold; this skill names the mechanism that proves it, and decides whether the control
runs in production, detects a discrepancy planted in it, and survives a restart. Of every control in the
diff: if it were deleted tonight, which test goes red? If none, it is already absent.

Load this skill when tests, proof or reconciliation are actually being changed, when the ask is review,
readiness or verification, or when a domain skill requires stronger proof for the mechanism in scope. Not
because customer money exists.

## When to use

Whenever someone is being asked to believe a money path is correct:

- a change adds or edits the tests, fixtures or assertions standing behind an economic quantity;
- it adds, moves or removes a comparison against an external record;
- it adds a way to stop trading, paying or posting because something looks wrong;
- the domain skill in scope demands stronger proof for the mechanism being changed: a second venue adapter
  with no position reconciliation, a rewrite of a path already carrying production traffic, a state machine
  you author that nothing outside can check. The trigger is the mechanism, never the exposure.

Routing hints, not the definition: `tests/`, `test_*.py`, `*_test.go`, `*.spec.ts`, `conftest.py`, a fixture or
a cassette; a symbol matching `reconcil|recon_|break|drift|suspense|clearing|kill_switch|halt|dead_man`; a base
URL containing `testnet`, `sandbox`, `paper`, `-uat`, `-sim`; task text such as "is this ready", "prove it",
"how do I verify", "review before ship", "roll back", "write tests". The References table carries the
per-technique literals.

## When not to

Skip when the asserted values are analytics that never become an obligation (backtest statistics, greeks,
implied vol, Monte Carlo) and no balance, order, payment or transfer is written. Skip when the change moves
no proof: a `customer_id` on a balance row is not a reason to load this skill, and exposure on its own never
adds a technique.

Never load this alone. `fin-ledger` owns what a balance is, `fin-exchange-integration` a venue client,
`fin-payments` a processor lifecycle, `fin-onchain` a chain boundary, `fin-money-core` amounts, identity
and retries. The domain skill normally wins by itself, because it already specialises the money-core
invariants that apply; load `fin-money-core` alongside it only for a genuine cross-domain mechanism the
domain skill does not cover. They design the control; this skill proves it.

## Workflow

1. Name the economic claim this change makes, and name who eats the loss if the claim is false.
2. Report `authority` (EXTERNAL or SELF, per quantity) and `exposure` (own, customer or record). Authority
   decides the primary oracle, exposure decides the depth of the proof, and neither one adds a technique the
   changed mechanism does not need. Read
   [proof-mechanisms.md](references/proof-mechanisms.md) for the mechanism that proves the property in scope.
3. Name the authority and the join key for every economic quantity the system reports. When authority is
   SELF there is no join key to have, and step 4 turns inward.
4. Ship the comparison against that authority as a scheduled entrypoint, reading through a path
   independent of the writer, with a fail-closed delivery path for breaks. When authority is SELF,
   replace it with replay from the append-only log plus continuous conservation checks.
5. Plant a break of a known size on a known entity against a freshly migrated store, and assert it is
   detected: break record, balanced corrective posting, exactly one alert, zero breaks on a clean run.
6. Kill the process at each boundary between a local commit and a foreign mutation, restart, run the
   recovery path, and assert exactly one external effect and exactly one local record.
7. Replay every event consumer in the recorded order, then shuffled, duplicated and interrupted by a
   restart; assert identical terminal state, with conservation and idempotence asserted after every step.
8. Read the design notes as a numbered claim list. Bind each claim to a named test or delete the
   sentence. Then emit a finding for everything described and not implemented.

## Invariants

### Comparing two numbers proves nothing until you name which one is the authority

Specialises *reconciliation*. A scheduled comparison is a control only if every quantity it reports has a named
external authority, a join key that authority minted, a read path independent of the writer, a cadence stated
against the authority's documented lag, a tolerance in the instrument's own precision, and a durable break record
with an escalation clock. Your own reference is not unique across retries: one retried attempt produces two
counterparty identifiers under one of yours, and the join drops a row silently instead of raising one. A job that
re-reads the store the writer populated finds arithmetic bugs and can never find a missing write.

### A detector that has never detected is not known to detect

Feed the comparison a discrepancy of a known size on a known entity, against a freshly migrated store, and assert
it produces the break record and delivers exactly one alert to the routed destination. Running the job proves it
runs. The fresh store is what makes an un-backfilled opening balance fail the test instead of muting production.
This is the cheapest test in the suite, and it protects every reconciliation in every other skill at once.

### A crash between two foreign mutations is only survivable if you have executed it

An atomic phase is the set of local mutations between two foreign mutations, so a value-moving path has exactly
three boundaries: after the intent commit and before the call, after the call and before the outcome write, after
the outcome write and before the publish. At each one, kill the process, restart, run recovery, and assert exactly
one external effect and exactly one local record. Every field written ahead of the effect must be read back by
that recovery path. Persisting it is the half that gets done; consuming it is the half that gets skipped, leaving
a resume path that raises on exactly the crash the journal exists for.

### The ambiguity worth testing is the request that arrived

Specialises *ambiguous outcomes*. A stub that raises before delivery tests nothing, because the failure mode is
the request the counterparty received and then failed to acknowledge. Deliver it, then break the connection:
assert no resubmission, assert the code queries the counterparty by the identity it minted, assert exactly one
effect exists afterwards. Run the mirror case where the effect genuinely did not happen, or you have proved only
that the code gave up, not that it distinguishes UNKNOWN from "did not happen".

### An event consumer is correct only if every arrival order produces the same state

Specialises *authority*, whose pushed-payload clause this proves. Apply a recorded stream in order, then apply
shuffled permutations of it with duplicates and with a restart in the middle, and assert the terminal state is
identical every time. Assert conservation and idempotence after every step, not only at the end: that is what
catches an in-memory dedupe set, a version watermark stored on the live object, and an illegal transition, in one
pass. Assert generator coverage explicitly, or the generator quietly never produces the case the bug lives in.

### Five properties matter for money, and the rest are decoration

Conservation: `sum(all account deltas) == 0` after every step, and globally `sum(balances) + fees == sum(deposits)
- sum(withdrawals)`. Idempotence: `f(f(x)) == f(x)` for every operation a retry can repeat.
Permutation-invariance, only for operation sets the design *claims* are order-independent; if it is not claimed,
find out which order the design depends on and test that order. Reservation-implies-postable: no reachable state
holds a committed reservation that cannot be posted. Allocation totality: `sum(allocate(total, weights)) ==
total`.

### An assertion is a claim about values you produced, not values you received

Before writing an assert, panic, abort, process exit or unhandled throw on a money path, name the
provenance of every value in the asserted relation. If any of them crossed a network, a file, a config or
a clock, it is an operating error and needs a typed, fail-closed guard, not an assertion. Crash only when
abandoning obligations costs less than continuing: a ledger at rest loses nothing, a position at rest
loses money, and an unattended resting order keeps filling.

### The design notes are a numbered list of claims, each bound to a test or deleted

Specialises *a comment is a claim*. Enumerate the comments and docstrings as claim 1 to claim n before you read
the implementation, then bind each number to a test name or delete the sentence. Never accept a claim whose
evidence is the implementation you are about to read. Two shapes recur: the ordering claim, "the flush guarantees
the row exists before the call", documented at line 288 and performed at line 248; and the exactly-once claim,
"move money into `refunded_cents` exactly once", over a branch whose body is `pass`.

### A small live bot is carried by five tests and one scheduled comparison

`test_timeout_that_already_filled` with its mirror `test_provable_presend_failure_retries`,
`test_normalize_satisfies_every_filter_simultaneously`, `test_fill_stream_replayed_shuffled_and_duplicated`,
`test_kill_after_send_places_no_second_order` and `test_limits_hold_and_ambiguity_halts`, plus a daily comparison
joined on the authority's own key and alerting to a config key with no default. Demanding deterministic simulation
from a 300-line bot buys nothing and spends the budget those six need. The reference says what each one asserts.

### When authority is SELF there is nothing to reconcile against

Reconciliation does not disappear, it turns inward: recompute each quantity by replaying its own
append-only entries, cross-check replicas against each other, and check the internal invariants
continuously. The proof burden moves before deployment, into deterministic replay proven by a meta-test
that runs one seed twice and byte-compares the trace, generator-coverage counters, and an adversarial pass
someone else wrote. That is the only principled reason to demand simulation. Code complexity, team size
and how important the system feels are not reasons.

## References

Load a reference when its trigger is in the diff, not on the way past.

| File | Read it when |
|---|---|
| [proof-mechanisms.md](references/proof-mechanisms.md) | You are about to call a technique needed or not needed, or you are reporting `authority` and `exposure`. Find the property in scope, then read its mechanism row. |
| [small-bot-proof-set.md](references/small-bot-proof-set.md) | The change is a live bot of a few hundred lines against one venue on own capital, and the question is which tests it owes. |
| [reconciliation-design.md](references/reconciliation-design.md) | A scheduled job compares two sources, or the diff touches its join key, its cadence, its tolerance, or the query it reads through. |
| [reconciliation-worked-shapes.md](references/reconciliation-worked-shapes.md) | You are writing the comparison itself for a venue position, a processor payout, on-chain crediting or an internal ledger, or a dual-write cutover or backfill. |
| [reconciliation-breaks.md](references/reconciliation-breaks.md) | The diff contains `break`, `drift`, `suspense`, `clearing`, a break row, an aged bucket or sweep, or the alert destination for a comparison. |
| [halt-and-fail-closed.md](references/halt-and-fail-closed.md) | The diff contains `kill_switch`, `halt`, `dead_man`, `panic`, `abort`, `sys.exit`, `assert` or `debug_assert!` on a money path, or a gate that stops trading, paying or posting. |
| [money-properties-and-generators.md](references/money-properties-and-generators.md) | The diff contains `@given`, `hypothesis`, `fc.property`, `proptest`, a generator or strategy, a money column or wire type, `mutmut`, `cargo-mutants`, `Stryker`, `PIT`, or a second implementation of one amount. |
| [model-based-testing.md](references/model-based-testing.md) | The diff contains `RuleBasedStateMachine`, `@rule`, `Bundle`, `consumes`, `fc.commands`, `fc.modelRun`, `prop_state_machine!`, `ActionSequenceArbitrary`, or a reference model. |
| [concurrency-and-isolation-tests.md](references/concurrency-and-isolation-tests.md) | The diff contains a `SELECT` then `UPDATE` on a balance, `FOR UPDATE`, an isolation level, `40001`, `loom`, `jcstress` or `-race`. |
| [network-fault-injection.md](references/network-fault-injection.md) | The diff contains `toxiproxy`, `reset_peer`, `limit_data`, a timeout, retry or reconnect path, `-1006`, `-1007`, `recvWindow`, or a signature tolerance. |
| [crash-boundary-tests.md](references/crash-boundary-tests.md) | The diff contains `SIGKILL`, `kill -9`, a crash point, a recovery or resume entrypoint, a write-ahead intent row, or dedupe state that must survive a restart. |
| [replay-and-shadow-diffing.md](references/replay-and-shadow-diffing.md) | The diff replays an event stream, shuffles or duplicates events, rebuilds state from a journal, or shadows a rewrite against the incumbent it replaces. |
| [recorded-fixtures-and-sandboxes.md](references/recorded-fixtures-and-sandboxes.md) | The diff contains `vcr`, `cassette`, `record_mode`, `match_on`, a fixture of venue constraints, or a `testnet`, `sandbox`, `paper` or `dry_run` host or guard. |
| [deterministic-simulation.md](references/deterministic-simulation.md) | The diff contains `madsim`, `turmoil`, `antithesis`, a seeded simulator or VOPR, `buggify`, or a state machine that is its own authority. |

## Output

When the change is economic, open with one line: `authority: EXTERNAL (<name>) | SELF` and
`exposure: own | customer | record`. Authority is a property of a quantity, not of the codebase. Where one
authority covers everything in scope, emit the single line. Where it does not, emit `authority: MIXED` and
qualify the differing quantities on one line each, two or three qualifiers at most:

```
authority: MIXED · exposure: customer
  venue position       EXTERNAL (Binance)
  client order record  SELF
```

A `customer_id` on a balance or position row, a payout path, a crediting webhook or a second venue adapter moves
exposure to `customer`; becoming the record for a quantity (matching, allocating, a ledger that mirrors nothing, a
custody signer, an identifier other systems consume) moves that quantity to SELF and exposure to `record`.

Then one entry per real finding, and nothing for a concept the change does not touch:

```
FINDING   <the wrong economic outcome, concretely>
WHY       <the mechanism that produces it>
EVIDENCE  <file:line>
FIX       <the change that closes it>
TEST      <the property to assert>
```

A claimed control points at executable code and, where the risk needs it, a named test. A control that is
absent is reported as `UNRESOLVED: <control> (<why>)`, never as a completed checklist row. No findings is
one or two sentences saying so and why the change is safe.

When the task is a review or a ship decision, end with one line:

```
VERDICT   SHIP  |  NO-SHIP: <the unresolved control>
```

Emit one evidence row per property this change puts at risk, and never a row for a mechanism it does not
touch. The fuller ship-verdict block in the reference belongs to a review or a release decision.
