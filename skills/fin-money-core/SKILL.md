---
name: fin-money-core
description: >-
  Financial correctness for any code that moves or records value: exact representation, rounding
  and conservation, operation identity, ambiguous external outcomes, durable dedupe, concurrency
  on authoritative state, hard limits, reconciliation and rollout. Use when a change touches a
  value someone is owed, or a call that moves one. Defer to the exchange, payments, ledger or
  onchain skill when the change names their domain.
license: MIT
---

# Money core

Every money path shares the same mechanisms: what a number is, who owns an operation's identity, what an
ambiguous answer means, who wins a race, and who can tell you that you are wrong. One question governs all of
them: can this system produce an incorrect economic outcome while every component behaves exactly as
specified? This skill states the ten invariants the other skills cite by name, and loses every contested
match: where a change names a venue, a processor, a chain or a ledger, that skill's specialisation binds.

## When to use

The change carries a quantity someone can be owed, or calls something that creates, discharges or reverses
that obligation. Any one of five axes firing is enough:

- **amount** a value that is or becomes an amount owed, held, ordered, posted, priced or settled;
- **effect** a call that moves value or instructs someone else to, its retry, or its failure handling;
- **authority** what decides a balance, price, limit or eligibility, including reuse of a live flag, enum or
  config value;
- **replay** identity, keys, ordering or dedupe;
- **rollout** deploy or config for a money path that is sharded, regionalised, or must be fleet-uniform.

Also: how such a quantity is represented or converted, how one operation is identified across retries, how
concurrent writers order themselves on authoritative state, how a durable cursor advances over a range.

Literals are routing hints, not the definition: `amount`, `balance`, `price`, `qty`, `fee`, `notional`,
`rate`, `Decimal(`, `round(`, `quantize(`, `* 100`, `/ 1e18`, `int(`; `idempotency_key`, `client_order_id`,
`dedupe`, `_seen`, `cursor`, `watermark`, `last_synced`; `FOR UPDATE`, `SKIP LOCKED`, `isolation_level`,
`SERIALIZABLE`, `advisory_lock`, `asyncio.Lock`; any `except`, `catch`, `retry` or `backoff` around those; a
flag, enum, shard, region or migration on a path carrying them.

## When not to

Skip when the numbers are analytics that never become an obligation, and no balance, order, payment or
transfer is written: backtest statistics, greeks, implied vol, Monte Carlo, ML features, chart coordinates.
Nothing there can be demanded by a counterparty.

Defer, do not restate. A venue or exchange client goes to `fin-exchange-integration`, a processor or rail to
`fin-payments`, a journal, posting or chart of accounts to `fin-ledger`, a chain, token or custody path to
`fin-onchain`, and tests, reconciliation design or a ship decision to `fin-verification`. Load this skill
when none matched, or beside them for the arithmetic, identity, retry and rollout half. Those skills cite the
invariants below by name and add only what their domain changes.

## Workflow

1. **Gate.** Answer the five axes below from the diff alone. None fire: review as ordinary code and stop
   here. The gate's job is to exempt, not to claim territory.
2. **Name the effects.** What moves, from whom to whom, in what unit, reversible for how long. Then fix
   representation before stating anything else: type, scale, where the scale comes from, rounding direction,
   who keeps the residue.
3. **Name the authority** for each quantity and the window in which two systems may legitimately disagree.
   Report authority and exposure.
4. **Establish operation identity** per effect, durable before the effect, and classify every counterparty
   failure signal DEFINITE-NO, DEFINITE-YES or UNKNOWN so the class reaches the decision point.
5. **Find the races and the crash points**: every read-modify-write on a value someone is owed, and every
   window between the external effect and the local commit. Name the one chokepoint every value-mutating
   path ends in.
6. **Decide the limits and the rollout surface**: which ceiling rejects, which one is the aggregate, who
   still reads the flag you are reusing, which shards run the change.
7. **A comment is a claim.** Every property a docstring or design note asserts is proven by a named
   test, or the sentence is deleted. The asserted invariant is repeatedly exactly where the bug lives, and
   the assertion is what let it survive review.
8. **Implement, then report.** Load only the references this change needs, write the control and its test,
   and emit one entry per real finding.

## Invariants

Ten, stated here once and in full. Every other skill cites them by these names.

**exact representation.** A value a counterparty can demand is exact: integer minor units, a scaled integer
with a declared exponent, or a decimal built only from strings and integers. A value nobody can demand
(greeks, implied vol, VaR, backtest statistics) is correctly a float, and exactly one named, tested function
crosses from estimate to obligation. Exactness dies at storage and at the wire, not in the arithmetic: a
column type, a driver coercion or a JSON number destroys a value that was exact in memory. An amount without
its currency in the same row or struct, and without its scale resolved from runtime metadata rather than
hardcoded, is not an amount.

**rounding and conservation.** Direction comes from the operation's category, never from a global default: a
statute or scheme that names the mode is copied as per-jurisdiction configuration; an exchange between two
representations floors what the system pays out and ceils what it collects, per leg; a split of one exact
total uses largest-remainder with a deterministic tie-break. Every call site names the scale as well as the
direction, because a ceiling at 18 decimals rounds nothing. The residue is posted to a named account, split
parts sum to the original total, and no round trip returns more than it put in, including when different
principals open and close it. One shared helper on both legs of a convertible relationship rounds the same
way in both directions and leaks value.

**operation identity.** One identity per economic decision to act: stable across every retry of that
decision, distinct for two intents with byte-identical payloads, never a hash of the request body, and
required at the type level rather than optional. It is owned atomically by exactly one writer (a unique
constraint on `(tenant, key)` is one mechanism, a `SELECT` then `INSERT` is not), derives from a value that
survives `ROLLBACK`, and commits durably before the first externally visible effect, with no transaction
block lexically enclosing the call. A key minted from an uncommitted sequence yields a new id on the retry
and buys a second real effect. Whoever owns the resource compares the stored economically significant fields
(from, to, amount, currency) and rejects a mismatch without either executing or replaying.

**ambiguous outcomes.** A response that does not prove the outcome is UNKNOWN, never "did not happen". Per
effect, classify each counterparty signal: DEFINITE-NO only where the counterparty documents, for that exact
code, that the instruction was not enqueued; DEFINITE-YES; otherwise UNKNOWN. A retry is permitted only where
the production code path itself establishes that the instruction could not have become externally visible,
which means a pre-send failure: DNS resolution failure, connection refused, TLS handshake failure, a local
validation or serialization rejection. Knowledge available only to a test harness does not qualify. A
timeout, a socket error once transmission may have begun, any 5xx and any 429 resolve by asking the authority
about the identity you sent, never by resubmitting, and the intent row stays committed while you resolve. One
`except Exception:` around a value-moving call destroys the classification and is itself the defect.

**durable dedupe.** Dedupe state is exactly as durable as what it protects and is written in the same
transaction as the effect, keyed on the counterparty's own identifier. An in-memory set, an LRU cache or a
process-local dict evaporates on restart, which is precisely when a REST backfill, a webhook redelivery or a
queue redrive replays every already-applied event; a comment reading "dedups by tradeId" above a
module-level `set()` is not evidence, the storage is. The same rule governs a cursor or high-water mark: it
advances only inside the conditional and the transaction that covered the range, and an error, a provider
range rejection, a truncated page or a result count at the documented cap is a hole, not an empty result.

**concurrency on authoritative state.** No unprotected read-modify-write on a value someone is owed. The
guard is the write: a conditional `UPDATE ... WHERE` whose rowcount decides whether the effect proceeds, or a
lock taken before the check and held past the act. Three properties, all required, because any two without
the third still race: duration (the whole check-to-act section, not a block that dedents in between), key
determinism (a stable digest or a small registry, never a per-process salted `hash()`), and subject (the key
the act mutates, which is often a nonce or a batch rather than the row you read). A money transaction states
its isolation level explicitly.

**authority.** Name whose copy of each quantity is the record, and the window in which two systems may
legitimately disagree. A pushed payload is a notification whose arrival order you do not control, not a fact:
guard legality, guard version, then re-read amount, status and attribution from the authority before any
value-moving decision. A lookup that cannot answer raises or returns an explicit absent type, never `0`,
`-1`, `null`, `0x00` or the last known value, because no caller can tell a sentinel from a real number and
neither can the control downstream of it.

**reconciliation.** Every economic quantity you report has a named external authority, a named join key, and
a scheduled comparison that actually runs in production, reading through a path independent of the writer.
Breaks are aged, counted, and delivered to a fail-closed destination, so a missing configuration stops the
system rather than silencing the alert. An invariant that exists as SQL in a comment, as a docstring, or as a
"worth running as a cron" note is absent. Where no external authority exists, say so, and substitute an
internal invariant: conservation, solvency, sum of parts. This is the only control that catches the failures
of every other rule.

**hard limits.** A limit rejects the proposed operation rather than observing it, in the same transaction as
the write and before any external effect; a warning, a log line or a metric nobody alerts on is not a
control, and declining costs nothing but the operation. Per-item limits are satisfiable by an unbounded
number of items, so at least one limit is an aggregate over the batch, wave or basket. A kill switch is
exercisable faster than the loss accrues and is not resettable by the component that tripped it. A per-entity
override on a solvency, credit-limit or liquidation check is field-level audit-logged and is reported
whenever the path is reviewed.

**rollout.** A flag, enum, field or helper that a deployed consumer still reads belongs to that consumer:
reusing it changes what that consumer decides, without changing that consumer's code and without appearing in
its diff. Enumerate every deployed reader before reuse, re-execute every existing caller before moving a
function, exercise every shard, stripe, partition and region, and assert coverage per shard rather than by a
representative one. A rollback is a deploy and needs its own test. Delete a dead money path rather than
leaving it callable.

## References

Each row is a standing instruction: when the mechanism appears in the change, read the file before you
conclude, and apply it in order.

| file | read it when the change contains |
|---|---|
| [representation](references/representation.md) | `Decimal(`, `float(`, `f64`, `double`, `Float`, `Numeric`, `NUMERIC(`, `REAL`, a protobuf `double`, a JSON `number` amount, `decimals()`, `scale`, `exponent`, an ORM column declaration on an amount, or a money-path timestamp: `date.today()`, `datetime.now()`, `utcnow()`, a `TIMESTAMP` column with no zone, a settlement or accrual date |
| [rounding-and-allocation](references/rounding-and-allocation.md) | `round(`, `floor`, `ceil`, `trunc`, `int(`, `quantize(`, `//`, `* rate`, `/ total`, `split`, `allocate`, `pro_rata`, `convert`, or `%` applied to a money value |
| [indeterminacy-and-idempotency](references/indeterminacy-and-idempotency.md) | `idempotency_key`, `Idempotency-Key`, `client_order_id`, `request_id`, `retry`, `backoff`, `tenacity`, `timeout=`, `except Exception`, `catch (err)`, `5xx`, `429`, or any `except` or `catch` around a value-moving call |
| [concurrency-and-failure-boundaries](references/concurrency-and-failure-boundaries.md) | `FOR UPDATE`, `SKIP LOCKED`, `isolation_level`, `SERIALIZABLE`, `REPEATABLE READ`, `40001`, `advisory_lock`, `asyncio.Lock`, `session.begin()`, `engine.begin()`, `@transaction.atomic`, `outbox`, or a balance read and written back |
| [ordering-and-coverage](references/ordering-and-coverage.md) | a webhook or event handler, a state machine over an economic entity, `cursor`, `watermark`, `last_synced`, `from_block`, a page cap or `limit=`, a backfill, redrive or reconnect loop |
| [authority-limits-and-rollout](references/authority-limits-and-rollout.md) | a limit, cap, threshold, kill switch or override; a price or rate lookup that can fail; reuse of an existing flag, enum, config value or helper; a shard, region or migration on a money path; deciding authority and exposure |

## Output

When the change is economic, open with the two fields on one line, and omit them entirely when it is not:

```
authority: EXTERNAL (Stripe) · exposure: customer
```

Then one entry per real finding, and only for findings that exist:

```
FINDING   <the wrong economic outcome, concretely>
WHY       <the mechanism that produces it>
EVIDENCE  <file:line>
FIX       <the change that closes it>
TEST      <the property to assert>
```

Add `VERDICT   SHIP | NO-SHIP: <the unresolved control>` as a final line only when the task is a review or a
ship decision. No findings is one or two sentences saying so and why the change is safe. Never emit an empty
slot, and never emit a section for a concept the change does not touch.

Implemented, not described: a claimed control points at executable code, and where the risk requires it, at a
named test. A comment, a TODO, an unused helper or a design paragraph is not evidence. A control that is
absent is reported as `UNRESOLVED: <control> (<why>)`, never as a completed checklist row, and a named risk
you will not implement is made uncallable on a line execution actually reaches.

This skill's usual pair is `authority: EXTERNAL`, `exposure: own`. It becomes `SELF` and `record` the moment
the code is the system of record for a quantity, and `customer` the moment a principal id sits on the row.
Emit the fuller MONEY CONTRACT block from
[authority-limits-and-rollout](references/authority-limits-and-rollout.md) only when authority is `SELF`,
exposure is `record`, or the change opens a value-moving path that did not exist before.
