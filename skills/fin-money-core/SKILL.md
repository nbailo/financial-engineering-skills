---
name: fin-money-core
description: >-
  Financial correctness for any money path: amount representation and rounding, operation
  identity and idempotency, ambiguous external outcomes, read-modify-write races on
  authoritative state, cursor completeness, limits that reject, and rollout. Use when a change
  touches a value someone is owed, or a call that moves one. Defer to the exchange, payments,
  ledger, onchain or matching skill when the change names their domain.
license: MIT
---

# Money Core: the mechanisms every money path shares

One question governs everything below: can this system produce an incorrect economic outcome while every component behaves
exactly as specified? This skill owns the general mechanisms (representation, rounding, operation identity, indeterminacy,
concurrency, ceilings, rollout) and states in full the seven cross-cutting rules the other skills cite by name. It loses every
contested match: where the change names a venue, a processor, a chain or a ledger schema, that skill's instantiation binds.

## Workflow

1. Confirm the change can alter an economic outcome, and name which axis fired (amount, effect, authority, replay, rollout).
   Bias to exempt.
2. Name every effect: what moves, from whom to whom, in what unit, reversible for how long.
3. Fix representation before stating any invariant: type, scale, source of the scale, unit, rounding direction, and who
   benefits from the residue.
4. Name the authority for each quantity, and the window in which two systems can legitimately disagree.
5. State the invariants as executable predicates, and name the single chokepoint every value-mutating path ends in.
6. Establish operation identity for each effect and make it durable strictly before the effect, then classify every
   counterparty failure signal as DEFINITE-NO, DEFINITE-YES or UNKNOWN and carry that class to the decision point.
7. Find the read-modify-write races and the crash points between the external effect and the local commit, then decide the
   ceilings that reject and the rollout surface.
8. Name the reconciliation, load only the references this change needs, and implement the controls and their tests before
   declaring the path complete.

## When this applies

A change carries a quantity someone can be owed, or calls something that creates, discharges or reverses that obligation. Also: a change to how such a
quantity is represented or converted, to how one operation is identified across retries, to how concurrent writers order themselves on authoritative
state, to how a durable cursor advances over a range, or to how a money path is deployed and configured.

Literals are routing hints, not the definition. Typical ones: `amount`, `balance`, `price`, `qty`, `fee`, `notional`, `rate`, `Decimal(`, `round(`,
`quantize(`, `* 100`, `/ 1e18`, `int(`; `idempotency_key`, `client_order_id`, `dedupe`, `_seen`, `cursor`, `watermark`, `last_synced`; `FOR UPDATE`,
`SKIP LOCKED`, `isolation_level`, `SERIALIZABLE`, `advisory_lock`, `asyncio.Lock`; any `except`, `catch`, `retry` or `backoff` around those; a flag,
enum, shard, region or migration on a path carrying them.

Defer, do not restate. An exchange client (`ccxt`, a venue SDK) goes to `fin-exchange-integration`, a processor or rail to
`fin-payments`, a journal or chart of accounts to `fin-ledger`, a chain or token to `fin-onchain`, your own matching, allocation or settlement batch to
`fin-matching-and-settlement`. Load this skill when none matched, or beside them for the arithmetic, retry and rollout half.

## Core rules

These seven are the shared vocabulary. Other skills cite them by name and specialise them; here each is stated in full.

### The economic-diff gate

Decide whether a change can alter an economic outcome before you review it as ordinary code. Answer five questions from the
change alone. The gate's job is to exempt, not to claim territory, but a change that slips past it is reviewed for style and
types and never asked the question that catches correct-looking components producing a wrong economic outcome.

- **Amount.** Touches a value that is or becomes an amount owed, held, ordered, posted, priced or settled.
- **Effect.** Calls, retries, or handles the failure of something that moves value, or instructs someone else to.
- **Authority.** Changes who or what decides a balance, price, limit or eligibility, including reuse of an existing flag, enum
  or config value.
- **Replay.** Changes identity, keys, ordering or dedupe.
- **Rollout.** Changes deploy or config for a money path that is sharded, regionalised, or must be fleet-uniform.

**Shape**

```
changed path -> any of the five axes fire?
   no  -> declare none, review as ordinary code
   yes -> name the axes, declare the tier, apply the rules below
```

**How it appears** A path under the repo's money directories, an import of a payment, exchange, chain or ledger client, or a
symbol matching `balance|amount|price|qty|order|refund|payout|transfer|ledger|posting|settle|withdraw`. Exempt even when the
numbers look financial: backtest statistics, greeks, implied vol, Monte Carlo, so long as no balance, order, payment or
transfer is written. Nothing there can be demanded by a counterparty.

### Implemented, not described

A named risk is implemented, made unreachable, or reported as unresolved. A comment describing a missing control **is** the
missing control. This is the highest-frequency failure in the domain: the reviewer identifies the correct control accurately,
writes a sentence about it, then reads that sentence back during self-review as though it were the control.

**Shape**

```
risk named -> control implemented, at file:line, with a named test
           -> or a refusal on a path execution actually reaches
           -> or reported as UNRESOLVED, with the reason
```

**How it appears** A `TODO`, a design note, a "worth adding", a function defined and never called, a `...` or `pass` stub, a
docstring paragraph, an invariant written as commented-out SQL. `raise NotImplementedError` on a dead branch is decoration.

### A comment is a claim

Read every design note and docstring as a list of asserted properties. Each property is either proven by a named test, or the
sentence is deleted. The asserted invariant is repeatedly exactly where the bug lives, and the assertion is what let the bug
survive review: the reader stops checking at the sentence claiming the check already happened.

**Shape**

```
comment/docstring -> enumerate the properties it asserts
   each property  -> a named test that fails when the property is false
                  -> otherwise remove the sentence
```

**How it appears** "The flush guarantees the row exists." "The monotonic guard makes gaps impossible." "Dedups by tradeId",
above a process-local set. "Terminal states are absorbing", above a machine with a silent default arm.

### Durable intent before the external effect

Record a stable identity for the intent, durably, before the first externally visible economic side effect. The effect has
three phases and the first one commits. An ambiguous response is UNKNOWN, never "did not happen".

**Shape**

```
mint identity from the intent instance (not from the request bytes)
COMMIT intent(identity, exact serialized request, target)   # flush is not persistence
external effect
record outcome, in the same transaction as the state change it authorises
```

- The identity derives from a value that survives a rollback. A key built from an uncommitted sequence id yields a different
  id on the retry and buys a second real effect.
- No transaction block lexically encloses the call: such a block unwinds the intent row on the exact exception the row exists
  for, with no explicit rollback token anywhere in the change.
- On ambiguity the intent row stays committed. No rollback, no delete, no compensating write until the outcome is known: query
  the counterparty for the identity you sent, and never resubmit.
- Every field written before the effect is read by the recovery path. A startup pass loads each in-flight row, queries the
  counterparty with the persisted identity, and converges to exactly one effect. A persisted identity no code path reads back
  is the same defect as not persisting it.
- The outcome record and any outbound event commit together with the state change (outbox row or change capture).

**How it appears**

| Literal | What it means here |
|---|---|
| `flush()` inside an open transaction | not persistence; the row does not survive the timeout |
| `with session.begin()`, `engine.begin()`, `@transaction.atomic` | encloses the call, rolls the intent back |
| `BIGSERIAL`, `nextval` in a key | does not survive `ROLLBACK`; the retry mints `N+1` and pays twice |
| `uuid4()` or `ulid()` inside a retry-reentrant function | a new identity per attempt |
| `(tenant, key)` unique index | required; no `SELECT`-then-`INSERT`, and the unique violation resolves to the winner's stored result rather than surfacing raw |

Worked negative: freqtrade writes nothing before submitting an order (`freqtradebot.py:963`) and cannot recover a lost submit.
Worked positive: hummingbot's `start_tracking_order` commits before the request goes out.

### Arrival order is not occurrence order

A pushed event is a notification whose arrival order you do not control. Guard legality, guard version, then re-read the object
from its authority before any value-moving decision. Never act on the pushed payload's state.

**Shape**

```
event arrives
  -> is (state, event) legal? no -> explicit error, never a silent return
  -> UPDATE watermark SET v=:v WHERE id=:id AND v<:v    # the guard IS the write
     rowcount 1? no -> stop
  -> re-read amount, status, attribution from the authority
  -> effect, in the same transaction as the watermark write
```

A read-then-compare guard is a time-of-check race two concurrent redeliveries both pass. A guard written as "if a prior record exists and this event
is older, return" is skipped entirely once a terminal event removed the record, and a replayed pre-snapshot event re-inserts a phantom row. A terminal
state accepts exactly the events by which the counterparty corrects a fact you already booked, and is never re-opened by a status message. The
watermark is keyed on the entity id, stored independently of the live object, and must be a total order before `>=` is correct: where the source
publishes no version, derive one from its own sequence; where the only version is a coarse clock, the watermark is the pair `(clock,
applied_event_ids)` and an event at the same clock is admitted unless its id is already in the persisted set. Wall-clock arrival is not a version;
last-write-wins is not a policy.

**How it appears** Deny by default with `_ => Err(InvalidStateTransition)`, never a bare `return`. nautilus_trader ships `(Canceled, Filled) =>
Filled` annotated `// Real world possibility`, plus a fifteenth status `Voided` for `(Filled, FillVoided)`, while `(Canceled, Accepted)`, `(Filled,
Accepted)`, `(Filled, Canceled)` and `(Rejected, *)` stay absorbing by hitting the deny-by-default arm. Stripe's `created` is second-granularity, and
`refund.created` and `refund.updated` on one `re_...` routinely share a second; a bare `>=` there discards the `succeeded` event and the refund is
pending forever.

### Proven coverage before the cursor advances

A durable cursor, watermark or high-water mark advances only over a range whose completeness was established, inside the same
conditional and the same transaction that covered that range.

**Shape**

```
claim range -> fetch -> is coverage proven?
   yes -> apply effects and advance the cursor, one transaction
   no  -> leave the cursor; the range stays claimable
branch that skips the work -> skips the advance
```

An error, a provider range rejection, a result count sitting at the documented page cap, or a truncated page is a hole, not an
empty result. The failure mode is permanent silent under-crediting: value vanishes with no error and no log line, because the
only record the range was ever owed to anyone was the cursor.

**How it appears** A "nothing to do" branch that commits progress anyway: the query is guarded by `if (addresses.size > 0)`
while `saveCursor()` is not, so the cursor sprints to the head on a fresh deploy and every address registered afterwards can
never see a deposit in a passed block. Likewise a filter set (address list, subscription list) loaded once per outer iteration
while the inner loop advances the cursor: re-read it at the same cadence as the loop.

### Reconciliation runs in production

Every economic quantity you report has a named external authority, a named join key, and a scheduled comparison that actually runs, reading through a
path independent of the writer. This is the only control that catches the failures of every other rule, which is why it is the one most often left as
intent: an invariant existing as SQL in a comment, as a docstring, or as a "worth running as a cron" note is absent.

**Shape**

```
quantity -> external authority + join key
         -> scheduled entrypoint, independent read path
         -> break: aged, counted, alerted
alert destination = config key with no default, raising at import if unset
```

**How it appears** Where no external authority exists for a quantity, saying so explicitly is the T3 declaration, and an internal invariant becomes
the substitute (conservation, solvency, sum of parts). The test matrix and the fault injection for these belong to `fin-verification`.

## Representation, rounding and residue

### An obligation and an estimate are different kinds of number

A value a counterparty can demand is an obligation. A value nobody can demand is an estimate. The type declares which, and
there is exactly one named, tested crossing from estimate to obligation. Storage and the wire boundary are where this fails,
not the arithmetic: a value can be exact in memory and lossy the moment it is written or serialized.

**Shape**

```
obligation -> integer minor units | scaled integer + declared exponent | decimal built from strings/ints
estimate   -> binary floating point, correctly
estimate --quantize(value, scale, mode)--> obligation     # one named, tested function
```

**How it appears** Obligations: balance, posting, fee, tax, invoice line, settlement, payout, token amount, an order price or quantity checked against
venue filters. Estimates: greeks, implied vol, VaR, Monte Carlo, backtest statistics, ML features, chart coordinates, all correctly binary floating
point. "Never use a float" is the wrong rule, and a competent reviewer discards a whole suite over it. Boundary literals: `NUMERIC(`, `REAL`, `Float`,
a protobuf `double`, a JSON `number` carrying an amount, an ORM column declaration on an amount.

### An amount without its currency and scale is not an amount

Every stored amount carries its currency in the same row, struct or type, and every comparison, sum and equality check reads it; scale is resolved
from runtime metadata, never hardcoded. A schema with a minor-unit column and no currency column cannot validate the currency on an inbound webhook or
fill, and is silently wrong wherever the exponent is not 2. This is the default shape of a hand-rolled amounts table.

**Shape**

```
amount = (value, currency, scale)   travelling together, compared together
scale(purpose) resolved at runtime: charge | payout | display | calculation
```

**How it appears** Exponent 0: JPY, KRW, VND, CLP, ISK, UGX. Exponent 3: BHD, KWD, JOD, OMR, TND. Exponent 4: CLF. One currency carries several scales
at one vendor: Stripe charges HUF and TWD at 2 decimals but pays out only in multiples of 100, ISK and UGX payouts must end in `00`, and Kraken
calculates BTC at 10 decimals against 5 displayed. Charge, payout, display and calculation scale are four possibly-different numbers.

### Rounding direction comes from the operation's category, and the residue has an owner

Choose direction per operation, from what the operation is, never as a global default. Post the residue to a named account.

**Shape**

```
statute/scheme names the mode -> copy it, as per-jurisdiction config
exchange between two representations -> floor what the system pays out, ceil what it collects, per leg
split of one exact total -> largest-remainder, deterministic tie-break, assert sum(parts) == total
```

Direction without a scale is a no-op: a ceiling at 18 decimals rounds nothing, so name the scale at every call site. Where the counterparty chooses
when and how often to convert (shares against assets, LP tokens against reserves, base against quote, points against cash), a single shared helper on
both legs rounds the same way in both directions and leaks value on the round trip. Check the denominator too: if it can reach 0 or 1, or be inflated
by a transfer bypassing the accounting entrypoint, add virtual shares and assets, or seed and burn.

**How it appears** EU Member States fix VAT rounding and round-up may be mandatory; euro conversion is legally half-up. Store
these as per-jurisdiction and per-instrument configuration, never as a constant. The Balancer V2 ComposableStablePool bug
(3 Nov 2025, over $120M) is one helper reused on both legs. Property test over multiple actors: for an adversarially ordered
sequence of operations by *different* principals, sum of outputs is at most sum of inputs per asset, and no user-initiated
round trip returns more than it put in. The per-actor form survives an A-deposits, B-withdraws extraction.

## Identity, ambiguity and races

### Operation identity is a property of the decision, not of the bytes

The identity is unique per decision-to-act and byte-identical across every retry of that decision. It is never a hash of the
request body, and it is required at the type level rather than optional with enforcement deferred to prose about the API layer.
Whoever owns the resource owns the equivalence check.

**Shape**

```
key_for(intent) -> stable across n calls for one decision
                -> distinct for distinct intents with byte-identical payloads
own the resource      -> compare stored economically significant fields (from, to, amount, currency)
                         mismatch -> error that neither executes nor replays the stored response
client of a processor -> changed body = new intent = new key
                      -> your own pre-send guard: outgoing bytes vs bytes stored with that key
```

**How it appears** The usual half-implementation is `idempotency_key text UNIQUE` on the table, the parameter left `Optional[str] = None`, and the
row's fields never compared; the replay then lets a reversal mark a transfer reversed while writing zero compensating entries. No `uuid4()` or
`ulid()` sits inside any function a retry loop can re-enter. The fingerprint is a salted HMAC over the canonical payload, not a bare digest, and for a
capped operation ("transfer up to X", a balancing transfer) it compares the request as the client meant it, so a retry carrying the original cap
matches even though the committed amount differed. Never rely on the counterparty's own check: it is optional in every standard, and undocumented at
Adyen and PayPal.

### Every failure signal carries a class, and the class reaches the decision point

For each external effect, enumerate the counterparty's failure signals and classify each DEFINITE-NO, DEFINITE-YES or UNKNOWN.
UNKNOWN is the only branch that can duplicate money, which is why a misclassified rejection pays twice, and a single
`except Exception:` around a value-moving call destroys the classification and is itself the defect.

**Shape**

```
signal -> DEFINITE-NO   (counterparty documents, for that exact code, that it was not enqueued)
       -> DEFINITE-YES  (record the outcome)
       -> UNKNOWN       (query by the minted identity before any retry)
undocumented -> UNKNOWN
```

**How it appears** The status for "key seen, body differs" is inconsistent across vendors: 422 IETF, 409 Stripe, 400 OASIS,
`IdempotentParameterMismatch` at AWS. Do not branch on the code; branch on "not a clean 2xx for my key, therefore UNKNOWN,
therefore reconcile". The sharper axis, from TigerBeetle's `transient()` (`tigerbeetle.zig:318`): can retrying with identical
request data produce a different outcome? Insufficient funds is transient; a payload conflict is not.

### Dedupe state is exactly as durable as what it protects

Deduplication state is persisted in the same transaction as the state it protects, keyed on the counterparty's own identifier. An
in-memory set, an LRU cache or a process-local dict evaporates on restart, precisely when the standard recovery paths (a REST
backfill, a webhook redelivery, a queue redrive) replay every already-applied event.

**Shape**

```
BEGIN; INSERT dedupe(counterparty_id); apply the balance or position mutation; COMMIT
```

**How it appears** `_seen_ids`, `@lru_cache`, a module-level `set()`, and a comment reading "dedups by tradeId" above one of them.
The comment is not evidence; the storage is. This is the most common way dedupe silently stops working.

### A lock covers the whole check-to-act section, on the key the act mutates

Three independent properties, all required: duration, key determinism, subject. Any two without the third still race.

**Shape**

```
BEGIN; lock(stable_key_of(the subject the act mutates)); check; act; COMMIT
```

**How it appears**

- **Duration.** `with engine.begin() as conn: SELECT ... FOR UPDATE` releases at the dedent, before the sign and broadcast it
  was meant to protect. `FOR UPDATE SKIP LOCKED LIMIT 50` then `fetchall()` and a closed transaction lets N workers process the
  same 50 rows. `async with session_factory()` with no `session.begin()`, and a declared-but-never-acquired `asyncio.Lock()`,
  both hold nothing.
- **Key determinism.** `pg_advisory_xact_lock(hash(chain) & 0x7FFFFFFF)` uses Python's `hash()`, salted per interpreter by
  `PYTHONHASHSEED` for `str`, `bytes` and `datetime`. `hash('ethereum')` differs in every process while `hash(1) == 1` in all
  of them. Require a stable digest (`crc32` or `blake2b` over the UTF-8 bytes) or a small integer registry, regardless of the
  key's current type.
- **Subject.** `FOR UPDATE` on the withdrawal row while broadcasting a transaction keyed on the *nonce* satisfies both other
  properties and still races. Commonly an admin `reject()` drops the row lock between the status check and the reversal, the
  withdrawal is broadcast in that window, and the user keeps both the coins and the balance.

### No legal value doubles as "unset"

A money-path function whose input is absent raises, or returns an explicit absent type. It never returns a value the caller can
mistake for a real one. State sign rules in the positive direction: prices may be negative, quantities may not. "A price is not
non-negative" is a double negative that produces the wrong assertion.

**Shape**

```
lookup(missing) -> raise | Absent
never           -> 0 | "" | -1 | null | 0x00 | the last known value
```

**How it appears** A profit or notional function with no mark price ever set must not return zero, which a risk consumer reads as "flat, no exposure"
on a live open position, and `except ValueError: unrealized = Decimal(0)` inside a snapshot re-introduces the same lie one layer up. FCA Final Notice,
Citigroup Global Markets Ltd, 17 May 2024, ¶4.27: an unavailable index price defaulted to `-1`, the pre-trade estimate computed quantity times -1 and
rendered `-58,000,000`, and the trader read the number they expected and clicked Execute. The same missing feed blanked the wave-notional soft block
("Due to lack of market data, Wave notional cannot be found", ¶4.30) and it proceeded anyway: a sentinel default in a price lookup defeated the
confirmation control. Nomad, Aug 2022: a trusted root of `0x00` also meant "not proven".

## Ceilings, overrides and rollout

### A ceiling that warns is not a control

The ceiling rejects the proposed operation, in the same transaction as the write, before any external effect. Declining costs nothing but the
operation. Per-item limits are satisfiable by an unbounded number of items, so at least one ceiling is an aggregate.

**Shape**

```
propose -> ceilings evaluated: per item AND at least one aggregate over batch/wave/basket
        -> breach -> reject, same transaction, before the effect
kill switch: exercisable faster than the loss accrues, not resettable by the component that tripped it
```

**How it appears**

- FCA Final Notice, CGML, ¶4.18(a) and ¶4.33: per-item hard blocks (US$2bn notional, 200m shares) let US$196bn through, and the notice states
  a basket-level wave notional hard block would have prevented the incident.
- Compound could not stop an approximately $50M mis-distribution (around 168,000 COMP claimed) inside a 7-day governance
  process. Goldman ¶8, ¶9 and ¶31: blocks lifted repeatedly by the author of the policy being violated.
- The anomaly signal goes to a channel named by a config key with no default that raises at import if unset, not a log line, a
  metric nobody alerts on, or a distribution list. Knight ¶23 and ¶24: the 33 Account's $2m limit was "linked to no automated
  controls".
- A per-entity override on a solvency, credit-limit or liquidation check raises the tier by one, and every change to it is
  field-level audit-logged (who, when, old value, new value), with no code path able to set it without one.

### Reusing a live flag or a shared helper is a change of authority

A flag, enum, field or helper a deployed consumer still reads belongs to that consumer. Reusing it changes what that consumer
decides, without changing that consumer's code.

**Shape**

```
before reuse      -> enumerate every deployed reader of the value
before relocation -> re-execute every existing caller under test
rollout           -> every shard, stripe, partition and region exercised, coverage asserted per shard
rollback          -> a change with its own test
```

**How it appears** Knight ¶14 and ¶41: "moving the cumulative quantity function inadvertently disabled the cumulative quantity functionality in the
Power Peg code". Never retested for nine years. Goldman ¶23: A-H and L-Z tested, I-K not, so coverage is asserted per shard and not by a
representative one. Knight ¶27: the rollback spread the fault to all eight servers. Delete dead money paths rather than leaving them callable.

## Tier

The tier gates the required *evidence*, never which rules apply. The axis is who absorbs the error, and whether an outside
party can tell you that you are wrong. A `FINANCIAL_TIER:` line in the repo overrides inference; it may raise freely, and
lowering it needs an explicit statement, because under-tiering is the dangerous direction. `fin-verification` maps tier to
required technique in [tier-matrix](../fin-verification/references/tier-matrix.md).

| T | Placed here by |
|---|---|
| **T0** | No value-moving call reachable, or all behind a dry-run or paper guard, and every host a sandbox or testnet |
| **T1** | A value-moving call is reachable **and** a live credential path exists (an environment API key, a secrets-manager read, a non-sandbox host). Own capital, bounded loss |
| **T2** | A `user_id`, `customer_id` or `tenant_id` on a balance or position row; a payout or withdrawal path; a crediting webhook; **two or more** venue or processor adapters; a transfer whose sides belong to different principals. Someone else eats the error |
| **T3** | You are the record: matching or allocating across resting orders; a ledger writer that is not a mirror of an external processor; a custody signer; an ID assigner other systems consume; a sequencer or settlement batch; a mint or burn authority. **No external oracle exists**, so the evidence has to be internal |

**Escalate one tier, always reported:** a `SELECT` then `UPDATE` on a balance in separate statements; a money transaction whose
isolation level is never set; a per-entity override on a solvency, credit-limit or liquidation check; an immutable deploy
target with multi-day fix latency; one codebase deployed to N chains or regions.

## Output

Every economic change ends with the gate result and the check. Seven labels, this order, one line each except `controls`, which carries a
real `file:line` per control or an explicit `UNRESOLVED:` line. A described control with no location is a defect.

```
ECONOMIC-DIFF: <amount|effect|authority|replay|rollout, which, or none>
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

**At T2 and above**, add the contract block, filling only the slots this change touches. A slot the change touches and cannot
fill is the finding: write it on the `controls:` line as `UNRESOLVED`.

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

At T2 and above the `controls:` line carries the test as well: `<control> -> <file:line> · <test name>`. There is no separate risk table at any tier;
`controls:` is the single evidence surface. A control with no `file:line` fails the run, and if you will not implement one, make the path uncallable
with a `raise NotImplementedError` on a path execution actually reaches. **At T3**, add the per-technique evidence table; `fin-verification` owns
its shape.

## References

Each row is a standing instruction: when the literal appears, read the file **immediately**, apply it in order, do not summarise it.

| file | read it when the change contains |
|---|---|
| [representation](references/representation.md) | `Decimal(`, `float(`, `f64`, `double`, `Float`, `Numeric`, `NUMERIC(`, `REAL`, a protobuf `double`, a JSON `number` amount, `decimals()`, `scale`, `exponent`, an ORM column declaration on an amount, or a money-path timestamp: `date.today()`, `datetime.now()`, `utcnow()`, a `TIMESTAMP` column with no zone, a settlement or accrual date |
| [rounding-and-allocation](references/rounding-and-allocation.md) | `round(`, `floor`, `ceil`, `trunc`, `int(`, `quantize(`, `//`, `* rate`, `/ total`, `split`, `allocate`, `pro_rata`, `convert`, or `%` applied to a money value |
| [indeterminacy-and-idempotency](references/indeterminacy-and-idempotency.md) | `idempotency_key`, `Idempotency-Key`, `request_id`, `retry`, `backoff`, `tenacity`, `timeout=`, `except Exception`, `catch (err)`, `5xx`, `429`, or any `except` or `catch` around a value-moving call |
| [concurrency-and-failure-boundaries](references/concurrency-and-failure-boundaries.md) | `FOR UPDATE`, `SKIP LOCKED`, `isolation_level`, `SERIALIZABLE`, `REPEATABLE READ`, `40001`, `advisory_lock`, `asyncio.Lock`, `session.begin()`, `engine.begin()`, `@transaction.atomic`, `outbox`, or a balance read and written back |
