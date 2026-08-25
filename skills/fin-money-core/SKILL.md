---
name: fin-money-core
description: >-
  Financial correctness for any code that moves or records value: exact amounts, rounding,
  operation identity, ambiguous external outcomes, durable dedupe, concurrency, hard limits,
  reconciliation and rollout. Use when a change touches a value someone is owed or a call that
  moves one; defer to a domain skill when it names a venue, processor, ledger or chain.
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
`fin-onchain`. That skill normally wins outright, having already specialised the invariants below onto its
own mechanisms; load this one alongside it only for a cross-domain mechanism it does not cover, and
`fin-verification` only when tests, proof or reconciliation are actually changing, when the ask is review,
readiness or a ship decision, or where a skill demands stronger proof for the mechanism in scope. Never as an
automatic consequence of customer exposure.

## Workflow

1. **Gate.** Answer the five axes below from the diff alone. None fire: review as ordinary code and stop
   here. The gate's job is to exempt, not to claim territory.
2. **Name the effects.** What moves, from whom to whom, in what unit, reversible for how long. Then fix
   representation before stating anything else: type, scale, where the scale comes from, rounding direction,
   who keeps the residue.
3. **Name the authority** for each quantity and the window in which two systems may legitimately disagree.
   Report authority per quantity, and exposure.
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

**exact representation.** A value a counterparty can demand carries no representation error where it is stored, sent
or compared: integer minor units, a scaled integer with a declared exponent, and a decimal that never passes through
binary floating point all hold that. A value nobody can demand (greeks, implied vol, VaR, backtest statistics) is
correctly a float, and each crossing from estimate to obligation is a named, tested function rather than an implicit
assignment. Exactness dies at storage and at the wire, not in the arithmetic: a column type, a driver coercion or a
JSON number destroys a value that was exact in memory. An amount is inseparable from its currency and its scale at
every boundary it crosses, and the scale traces to the authority that defines it, resolved at runtime wherever that
authority can change it.

**rounding and conservation.** Direction comes from the operation's category, never from a global default: a statute
or scheme that names the mode is copied as per-jurisdiction configuration; an exchange between two representations
rounds per leg in a declared direction, which absent a scheme rule floors what the system pays out and ceils what it
collects; a split of one exact total is deterministic and its parts sum to that total, which largest-remainder with a
declared tie-break achieves. Every call site names the scale as well as the direction, because a ceiling at 18
decimals rounds nothing. The residue is posted to a named account, and no round trip returns more than it put in,
including when different principals open and close it. A helper shared by both legs of a convertible relationship
rounds the same way in both directions and leaks value unless the direction is a parameter of the leg.

**operation identity.** One identity per economic decision to act: stable across every retry of that decision,
distinct for two intents with byte-identical payloads, never a hash of the request body, and impossible for a caller
to omit, by signature or by a first statement that rejects its absence. It is owned atomically by exactly one writer
(a unique constraint on `(tenant, key)` is one mechanism, a `SELECT` then `INSERT` is not), derives from a value that
survives `ROLLBACK`, and commits durably before the first externally visible effect, with no transaction block
lexically enclosing the call. A key minted from an uncommitted sequence yields a new id on the retry and buys a second
real effect. Whoever owns the resource compares the stored economically significant fields (from, to, amount,
currency) and rejects a mismatch without either executing or replaying.

**ambiguous outcomes.** A response that does not prove the outcome is UNKNOWN, never "did not happen". Per effect,
classify each counterparty signal: DEFINITE-NO only where the counterparty documents, for that exact code, that the
instruction was not enqueued; DEFINITE-YES; otherwise UNKNOWN. A retry is permitted only where the production code
path itself establishes that the instruction could not have become externally visible, which in practice means a
pre-send failure: DNS resolution failure, connection refused, TLS handshake failure, a local validation or
serialization rejection. Knowledge available only to a test harness does not qualify. A timeout, a socket error once
transmission may have begun, any 5xx and any 429 resolve by asking the authority about the identity you sent, never by
resubmitting, and the intent row stays committed while you resolve. One `except Exception:` around a value-moving call
destroys the classification and is itself the defect.

**durable dedupe.** Dedupe state is exactly as durable as what it protects and is written in the same transaction as
the effect, keyed on the counterparty's identity for the transition being applied rather than on the delivery that
carried it. An in-memory set, an LRU cache or a process-local dict evaporates on restart, which is precisely when a
REST backfill, a webhook redelivery or a queue redrive replays every already-applied event; a comment reading "dedups
by tradeId" above a module-level `set()` is not evidence, the storage is. The same rule governs a cursor or high-water
mark: it advances only inside the conditional and the transaction that covered the range, and an error, a provider
range rejection, a truncated page or a result count at the documented cap is a hole, not an empty result.

**concurrency on authoritative state.** No unprotected read-modify-write on a value someone is owed. The guard is the
write: check and act are one step no interleaving can enter, whether by a conditional `UPDATE ... WHERE` whose
rowcount decides the effect or by a lock taken before the check and held past the act. Three properties, all required,
because any two without the third still race: duration (the whole check-to-act section, not a block that dedents in
between), key determinism (a stable digest or a small registry, never a per-process salted `hash()`), and subject (the
key the act mutates, which is often a nonce or a batch rather than the row you read). A money transaction states its
isolation level explicitly.

**authority.** Authority is a property of a quantity, not of a codebase: one process can hold external authority for
settlement state and self authority for the liabilities it originates. Name whose copy of each quantity is the record,
and the window in which two systems may legitimately disagree. A pushed payload is a notification whose arrival order
you do not control, not a fact: guard legality, guard version, then re-read amount, status and attribution from that
quantity's authority before any value-moving decision. A lookup that cannot answer raises or returns an explicit
absent type, never `0`, `-1`, `null`, `0x00` or the last known value, because no caller can tell a sentinel from a
real number and neither can the control downstream of it.

**reconciliation.** Every economic quantity you report has a named external authority, a named join key, and a
scheduled comparison that actually runs in production, reading through a path independent of the writer. Breaks are
aged, counted, and delivered to a fail-closed destination, so a missing configuration stops the system rather than
silencing the alert. An invariant that exists as SQL in a comment, as a docstring, or as a "worth running as a cron"
note is absent. Where no external authority exists, say so, and substitute an internal invariant: conservation,
solvency, sum of parts. This is the only control that catches the failures of every other rule.

**hard limits.** A limit rejects the proposed operation rather than observing it, in the same transaction as the write
and before any external effect; a warning, a log line or a metric nobody alerts on is not a control, and declining
costs nothing but the operation. Per-item limits are satisfiable by an unbounded number of items, so at least one
limit is an aggregate over the batch, wave or basket. A kill switch is exercisable faster than the loss accrues and is
not resettable by the component that tripped it. A per-entity override on a solvency, credit-limit or liquidation
check is field-level audit-logged and is reported whenever the path is reviewed.

**rollout.** A flag, enum, field or helper that a deployed consumer still reads belongs to that consumer: reusing it
changes what that consumer decides, without changing that consumer's code and without appearing in its diff. Enumerate
every deployed reader before reuse, re-execute every existing caller before moving a function, exercise every shard,
stripe, partition and region, and assert coverage per shard rather than by a representative one. A rollback is a
deploy and needs its own test. Delete a dead money path rather than leaving it callable.

## References

Each row is a standing instruction: when the mechanism appears in the change, read that file before you conclude.

| file | read it when the change contains |
|---|---|
| [representation](references/representation.md) | `Decimal(`, `float(`, `f64`, `double`, `Float`, `Numeric`, `NUMERIC(`, `REAL`, an ORM column declaration on an amount, `localcontext`, `MathContext`, `prec`, `traps[Inexact]`; or a money-path timestamp: `date.today()`, `datetime.now()`, `utcnow()`, a `TIMESTAMP` column with no zone, a settlement or accrual date |
| [currency-and-scale](references/currency-and-scale.md) | `* 100`, `/ 100`, `to_minor`, `decimals()`, `scale`, `exponent`, a currency or asset column, `SUM(amount)` with no currency grouping, `rate`, `fx`, `convert`, or a JPY/KWD/ISK/HUF/CLF special case |
| [serialization-and-width](references/serialization-and-width.md) | a JSON `number` amount, `JSON.parse`, `toFixed`, `BigInt`, a protobuf `double`, an Avro or Parquet `decimal` scale, a CSV or spreadsheet hop; `int64`, `uint64`, `uint256`, `checked_add`, `mulDiv`, `unchecked {`, `SafeMath`, a sum compared against a bound |
| [rounding-modes](references/rounding-modes.md) | `round(`, `quantize(`, `setScale`, `ROUND_`, `MidpointRounding`, `Math.Round`; or a mode set by statute or contract: `vat`, `tax`, `apr`, `apy`, `interest`, `accrual`, `day_count`, `ACT/360`, `30/360` |
| [exchange-rounding-direction](references/exchange-rounding-direction.md) | `deposit`, `mint`, `withdraw`, `redeem`, `convertTo`, `preview`, `totalSupply`, `totalAssets`, `shares`, `mulDown`, `_upscale`, a share price or LP ratio, a points-to-cash or base-to-quote leg |
| [allocation-and-residue](references/allocation-and-residue.md) | `split`, `allocate`, `pro_rata`, `distribute`, `weights`, `remainder`, a per-line rate reconciled against an invoice total, or a rounding-difference or residue account |
| [integer-arithmetic-traps](references/integer-arithmetic-traps.md) | `int(`, `//`, `%`, `trunc`, `floor`, `ceil`, `Math.floor`, `div_euclid`, `floorDiv`, `<<` or `>>` on an amount, or a threshold, mask, cap or tick boundary compared with `>` or `>=` |
| [ambiguous-outcomes](references/ambiguous-outcomes.md) | `except Exception`, `catch (err)`, `retry`, `backoff`, `tenacity`, `timeout=`, `5xx`, `429`, `409`, `-1006`, `-1007`, `-2013`, `RequestTimeout`, `transient`, a status-class handler on a payment response, a sweep of `INFLIGHT` rows |
| [idempotency-keys](references/idempotency-keys.md) | `idempotency_key`, `Idempotency-Key`, `client_order_id`, `request_id`, `ClientToken`, a key minted from `RETURNING id` or after `flush()`, a fingerprint or payload hash, a `RETENTION` or `TTL` constant, a server that stores keys |
| [isolation-and-locking](references/isolation-and-locking.md) | `FOR UPDATE`, `SKIP LOCKED`, `isolation_level`, `SERIALIZABLE`, `REPEATABLE READ`, `40001`, `40P01`, `1213`, `lock_version`, `rowcount`, or a balance read and written back |
| [distributed-locks-and-fencing](references/distributed-locks-and-fencing.md) | `advisory_lock`, `pg_advisory`, `asyncio.Lock`, `Redlock`, `hash(` inside a lock key, a lease, TTL, epoch, fencing token, leader election, or a single-writer claim |
| [crash-boundaries-and-outbox](references/crash-boundaries-and-outbox.md) | `session.begin()`, `engine.begin()` or `@transaction.atomic` around an external call, a database write and a `publish(` in one function, `outbox`, a saga, or a compensating or reversing action |
| [ordering-and-coverage](references/ordering-and-coverage.md) | a webhook or event handler, a state machine over an economic entity, `cursor`, `watermark`, `last_synced`, `from_block`, a page cap or `limit=`, a backfill, redrive or reconnect loop |
| [authority-limits-and-rollout](references/authority-limits-and-rollout.md) | a limit, cap, threshold, kill switch or override; a price or rate lookup that can fail; reuse of an existing flag, enum, config value or helper; a shard, region or migration on a money path; deciding authority and exposure |

## Output

When the change is economic, open with authority and exposure, and with nothing at all when it is not.
Authority is per quantity: one line where a single authority covers every quantity in scope, `MIXED` plus two
or three qualifying lines where it does not. Never a matrix.

```
authority: EXTERNAL (Stripe) · exposure: customer

authority: MIXED · exposure: customer
  settlement state      EXTERNAL (Stripe)
  internal liabilities  SELF
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

The usual pair here is `authority: EXTERNAL`, `exposure: own`; a quantity this code is the record for is
`SELF` with exposure `record`, and exposure is `customer` once a principal id sits on the row. Emit the
fuller MONEY CONTRACT block from [authority-limits-and-rollout](references/authority-limits-and-rollout.md)
only when a quantity in scope is `SELF`, exposure is `record`, or the change opens a value-moving path that
did not exist before.
