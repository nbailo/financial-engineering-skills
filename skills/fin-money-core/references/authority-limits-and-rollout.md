# Authority, limits and rollout

Who decides a number, what stops a wrong number from leaving, and what changes the answer for code you did
not touch. These three are one reference because the same incidents produce all three findings: a price
authority that returned a sentinel, a limit that observed instead of rejecting, and a flag whose meaning
changed under a consumer nobody redeployed. Read this when the change decides eligibility or a price, when it
adds or moves a cap, or when it reuses an existing flag, enum, helper or config value.

## Contents

- [Reporting authority and exposure](#reporting-authority-and-exposure)
- [The retired T0 to T3 scale, and what it encoded](#the-retired-t0-to-t3-scale-and-what-it-encoded)
- [No legal value doubles as unset](#no-legal-value-doubles-as-unset)
- [Sign rules stated in the positive direction](#sign-rules-stated-in-the-positive-direction)
- [A ceiling that warns is not a control](#a-ceiling-that-warns-is-not-a-control)
- [Where the anomaly signal goes](#where-the-anomaly-signal-goes)
- [Per-entity overrides](#per-entity-overrides)
- [Reusing a live flag or a shared helper](#reusing-a-live-flag-or-a-shared-helper)
- [Fleet coverage, rollback, and dead paths](#fleet-coverage-rollback-and-dead-paths)
- [Implemented, not described](#implemented-not-described)
- [A comment is a claim](#a-comment-is-a-claim)
- [The fuller evidence block](#the-fuller-evidence-block)

---

## Reporting authority and exposure

Two fields, reported on one line whenever the change is economic:

```
authority: EXTERNAL (Binance) · exposure: own
```

**Authority** is whether anything outside your process can tell you that a quantity is wrong.

- `EXTERNAL`: a venue, a processor, a chain, a bank holds the record for that quantity. Reconciliation
  against it is available, and reconciliation is therefore the primary proof.
- `SELF`: nothing outside holds the truth for that quantity. A system-of-record ledger, a matching engine, an
  ID assigner, a custody signer's own view of its funds and nonces. Replay, determinism and conservation
  assertions are the proof, because there is nothing to reconcile against.

**Authority is a property of a quantity, not of a codebase, and not of a process.** One process routinely
holds external authority for settlement state, self authority for the liabilities it books against that
state, self authority for its wallet nonce and signing state, and external authority for chain inclusion. A
single finding may also carry its own authority, where that is what makes it a finding.

Where one authority covers every quantity in scope, emit the single line above. Where it does not, emit
`MIXED` and qualify the quantities that differ, one line each, two or three of them:

```
authority: MIXED · exposure: customer
  settlement state      EXTERNAL (Stripe)
  internal liabilities  SELF
```

Two or three qualifiers, never a taxonomy and never a matrix. It stays cheap to emit and cheap to read.

**Exposure** is whose money is lost when the code is wrong: `own` capital, a `customer`'s funds, or the
integrity of a `record` other systems consume.

Exposure decides how much evidence. Authority decides which kind, per quantity: reconcile what something else
holds, replay and conserve what only you hold. fin-verification maps a property to the mechanism that proves
it.

## The retired T0 to T3 scale, and what it encoded

The suite used to report a single ordinal. It is retired because it encoded two unrelated things at once,
which is why a paper-trading bot and a custody signer kept arguing about the same number. The signals it
collected are still the fastest way to place a change on the two axes above, so they are kept here as a
lookup, not as a label to report.

| Old tier | Placed there by | Reads today as |
|---|---|---|
| T0 | No value-moving call reachable, or all of them behind a dry-run or paper guard, and every host a sandbox or testnet | not economic |
| T1 | A value-moving call is reachable **and** a live credential path exists (an environment API key, a secrets-manager read, a non-sandbox host). Own capital, bounded loss | exposure `own` |
| T2 | A `user_id`, `customer_id` or `tenant_id` on a balance or position row; a payout or withdrawal path; a crediting webhook; two or more venue or processor adapters; a transfer whose sides belong to different principals. Someone else eats the error | exposure `customer` |
| T3 | You are the record: matching or allocating across resting orders; a ledger writer that is not a mirror of an external processor; a custody signer; an ID assigner other systems consume; a sequencer or settlement batch; a mint or burn authority. No external oracle exists, so the evidence has to be internal | authority `SELF`, exposure `record` |

The escalation list survives intact, now as "treat the exposure as one step worse and say why":

- a `SELECT` then `UPDATE` on a balance in separate statements;
- a money transaction whose isolation level is never set;
- a per-entity override on a solvency, credit-limit or liquidation check;
- an immutable deploy target with multi-day fix latency;
- one codebase deployed to N chains or regions.

## No legal value doubles as unset

A money-path function whose input is absent raises, or returns an explicit absent type. It never returns a
value the caller can mistake for a real one: not `0`, not `""`, not `-1`, not `null`, not `0x00`, and not the
last known value.

```
lookup(missing) -> raise | Absent
never           -> 0 | "" | -1 | null | 0x00 | the last known value
```

The FCA's Final Notice against **Citigroup Global Markets Ltd** (17 May 2024) is the complete worked example,
because the same missing feed defeated both the number and the control that was supposed to catch it.

- **¶4.27**: an unavailable index price defaulted to `-1`. The pre-trade estimate computed quantity times
  `-1` and rendered `-58,000,000`. The trader read a number of roughly the magnitude they expected, with a
  sign they did not interrogate, and clicked Execute.
- **¶4.30**: the same missing feed blanked the wave-notional soft block, which displayed *"Due to lack of
  market data, Wave notional cannot be found"*, and the order proceeded anyway. A sentinel default in a price
  lookup had disabled a confirmation control two systems away from where it was written.

Two more shapes of the same defect:

- A profit or notional function with no mark price ever set that returns zero. A risk consumer reads zero as
  "flat, no exposure" on a live open position. Writing `except ValueError: unrealized = Decimal(0)` inside a
  snapshot builder re-introduces the same lie one layer up, after the underlying function was fixed.
- **Nomad** (August 2022): a trusted root of `0x00` also meant "not proven", so every message proved itself.

## Sign rules stated in the positive direction

State what is allowed, not what is disallowed. Prices may be negative (settled negative oil, negative
funding, a credit line). Quantities may not. Written as a negation, the rule becomes *"a price is not
non-negative"*, a double negative that reliably produces the wrong assertion in the test that was supposed to
enforce it.

## A ceiling that warns is not a control

The ceiling rejects the proposed operation, in the same transaction as the write, before any external effect.
Declining costs nothing but the operation. Per-item limits are satisfiable by an unbounded number of items,
so at least one ceiling is an aggregate over the batch, wave or basket.

```
propose -> ceilings evaluated: per item AND at least one aggregate over batch/wave/basket
        -> breach -> reject, same transaction, before the effect
kill switch: exercisable faster than the loss accrues, not resettable by the component that tripped it
```

Three incidents, each isolating a different half of that rule.

- **CGML ¶4.18(a) and ¶4.33.** Per-item hard blocks existed and were well chosen in isolation: US$2bn
  notional and 200m shares per order. A basket of small orders passed every one of them, and US$196bn
  reached the market. The Final Notice states that a basket-level wave notional hard block would have
  prevented the incident. Per-item limits did not fail; they were never the binding constraint.
- **Compound.** Approximately $50M was mis-distributed (around 168,000 COMP claimed) and could not be
  stopped, because the only mechanism able to stop it was a 7-day governance process. A kill switch slower
  than the loss accrues is not a kill switch.
- **Goldman ¶8, ¶9 and ¶31.** Blocks were lifted repeatedly, by the author of the policy being violated. A
  limit resettable by the component or the person it constrains is a suggestion.

## Where the anomaly signal goes

The signal goes to a destination that is configured, fail-closed, and read by a human on a schedule. The
property is that an unconfigured destination stops the money path rather than degrading silently to nowhere;
a config key with no default, read where a miss raises before any value moves, is one mechanism that gets
there. Not a log line, not a metric nobody alerts on, not a distribution list.

**Knight ¶23 and ¶24** is the counter-example that names the failure precisely: the 33 Account's $2m limit
was *"linked to no automated controls"*. The number existed, was correct, was breached, and nothing was
listening.

## Per-entity overrides

A per-entity override on a solvency, credit-limit or liquidation check is the most dangerous configuration
row in a money system, because it is invisible in code review: the code is correct and the data exempts one
principal from it. Every change to one is field-level audit-logged (who, when, old value, new value), with no
code path able to set it without producing that record, and the presence of any override is itself reported
when the path is reviewed.

## Reusing a live flag or a shared helper

A flag, enum, field or helper that a deployed consumer still reads belongs to that consumer. Reusing it
changes what that consumer decides, without changing that consumer's code, and without appearing in the diff
of the consumer at all.

```
before reuse      -> enumerate every deployed reader of the value
before relocation -> re-execute every existing caller under test
rollback          -> a change with its own test
```

**Knight ¶14 and ¶41** is the canonical instance, and the wording of the notice is worth keeping verbatim:
*"moving the cumulative quantity function inadvertently disabled the cumulative quantity functionality in the
Power Peg code"*. The Power Peg code had not been used in nine years and had never been retested. The reuse
was of a flag; the damage was in code nobody had opened.

The rule that falls out: delete a dead money path rather than leaving it callable. A dead path that cannot be
deleted is one whose entry point raises on a line execution actually reaches, not one guarded by a comment.

## Fleet coverage, rollback, and dead paths

Exercise every shard, stripe, partition and region, and assert coverage per shard rather than by a
representative one.

- **Goldman ¶23**: the change was tested for tickers A-H and L-Z. I-K was not covered. A representative
  sample of a partitioned fleet is not coverage of the fleet; it is coverage of the sample.
- **Knight ¶27**: the rollback spread the fault to all eight servers. A rollback is a deploy, it runs the
  same partial-fleet risk as the deploy that preceded it, and it needs its own test.

## Implemented, not described

A named risk is implemented, made unreachable, or reported as unresolved. A comment describing a missing
control **is** the missing control. This is the highest-frequency failure in the domain, and its shape is
always the same: the reviewer identifies the correct control accurately, writes a sentence about it, then
reads that sentence back during self-review as though it were the control.

```
risk named -> control implemented, at file:line, with a named test
           -> or a refusal on a path execution actually reaches
           -> or reported as UNRESOLVED, with the reason
```

What it looks like in a diff: a `TODO`, a design note, a "worth adding", a function defined and never called,
a `...` or `pass` stub, a docstring paragraph, an invariant written as commented-out SQL. And the near miss
that reads as a refusal but is not: `raise NotImplementedError` on a dead branch is decoration. The refusal
has to sit on a line execution actually reaches, which usually means the entry point rather than the deepest
unfinished helper.

## A comment is a claim

Read every design note and docstring as a list of asserted properties. Each property is either proven by a
named test, or the sentence is deleted. The asserted invariant is repeatedly exactly where the bug lives, and
the assertion is what let the bug survive review: the reader stops checking at the sentence claiming the
check already happened.

```
comment/docstring -> enumerate the properties it asserts
   each property  -> a named test that fails when the property is false
                  -> otherwise remove the sentence
```

Four real ones, each sitting directly above the code that contradicts it:

- *"The flush guarantees the row exists."* It does not; the row does not survive the transaction that is
  about to roll back.
- *"The monotonic guard makes gaps impossible."* Only if the guard is the write and the sequence is a total
  order.
- *"Dedups by tradeId"*, above a process-local set.
- *"Terminal states are absorbing"*, above a machine with a silent default arm.

## The fuller evidence block

The default output is one entry per real finding. Emit this fuller block only where the stakes justify it:
authority `SELF`, exposure `record`, or a change that opens a value-moving path that did not exist before.
Fill only the slots the change actually touches. A slot the change touches and cannot fill is itself the
finding, reported as `UNRESOLVED`.

```
MONEY CONTRACT
effects:        what moves, from and to, in what unit, reversible for how long
quantities:     type, scale, source of the scale, rounding mode, who keeps the residue
authority:      the system of record per quantity, and the window in which two systems may legitimately disagree
invariants:     the predicate, the chokepoint that enforces it, the paths proven not to bypass it
identity:       per effect: minted where, committed when, what a duplicate returns, and which counterparty
                signals are DEFINITE-NO, DEFINITE-YES or UNKNOWN
concurrency:    per read-then-write site: isolation, lock, retry semantics, the breaking interleaving
reconciliation: quantity, external authority, join key, cadence, break aging
```

A control named in this block points at executable code at a `file:line`, and where the risk requires it, at
a named test. A comment, a TODO, an unused helper or a design paragraph is not evidence, and a control with
no location is reported as `UNRESOLVED: <control> (<why>)` rather than as a completed row.
