---
name: fin-money-core
description: >-
  Use when a diff touches an amount, price, qty, balance, fee or rate; a value-moving call and its
  retry/except path; an idempotency key, cursor or dedupe key; a rounding or scale conversion; a
  SELECT-then-UPDATE, FOR UPDATE, isolation level or lock on a money row; or a flag/shard rollout of one.
  Defer to fin-exchange-integration, fin-payments, fin-ledger, fin-onchain or fin-matching-and-settlement
  if the diff names their APIs.
license: MIT
---

# Money Core

One question governs everything below. **Can this system produce an incorrect economic outcome while every
component behaves exactly as specified?** This skill owns the general mechanisms (representation, rounding,
idempotency, indeterminacy, concurrency, ceilings, rollout) and it **loses every contested match**: where the
diff names a venue, a processor, a chain or a ledger schema, that skill's instantiation of the rule binds.

> **`G1`–`G7`** are the always-on financial guardrails: **G1** economic-diff gate · **G2** a named risk is implemented or the process refuses to start · **G3** every comment claim checked against the code · **G4** an ambiguous external call has three phases and the first one COMMITs · **G5** enumerate legal `(state, event)` pairs, guard the version on the entity id, re-read from the authority · **G6** a watermark advances only past a verifiably covered range · **G7** the reconciliation runs in production or it does not exist. Install them with `scripts/install-guardrails.sh`; every rule below stands on its own without them.

## When this applies

Any changed line carrying an amount or a call that moves one: `amount`, `balance`, `price`, `qty`, `fee`, `notional`, `rate`, `Decimal(`, `round(`, `quantize(`, `* 100`, `/ 1e18`, `int(`; `idempotency_key`, `client_order_id`, `dedupe`, `_seen`, `cursor`, `watermark`, `last_synced`; `FOR UPDATE`, `SKIP LOCKED`, `isolation_level`, `SERIALIZABLE`, `advisory_lock`, `asyncio.Lock`; any `except`/`catch`/`retry`/`backoff` wrapped around those; a flag, enum, shard, region or migration on a path carrying them.

**Defer, do not restate.** An exchange or `ccxt` → `fin-exchange-integration`. A processor or rail → `fin-payments`. A journal, posting or chart of accounts → `fin-ledger`. A chain, RPC or token → `fin-onchain`. Your own matching, allocation or settlement batch → `fin-matching-and-settlement`. Load this skill when none of those matched, or beside them for the arithmetic, retry and rollout half.

## Step 0: the economic-diff gate, first, every time

A changed path matches the repo's money paths, imports a payment / exchange / chain / ledger client, or touches a symbol matching `balance|amount|price|qty|order|refund|payout|transfer|ledger|posting|settle|withdraw` ⇒ economic. Otherwise answer these five from the diff alone:

- **AMOUNT:** touches a value that is or becomes an amount owed, held, ordered, posted, priced or settled.
- **EFFECT:** calls, retries, or handles the failure of something that moves value or instructs someone else to.
- **AUTHORITY:** changes who or what decides a balance, price, limit or eligibility, *including reuse of an existing flag, enum or config value*.
- **REPLAY:** changes identity, keys, ordering or dedupe.
- **ROLLOUT:** changes deploy or config for a money path that is sharded, regionalised, or must be fleet-uniform.

All five NO and no match ⇒ emit `ECONOMIC-DIFF: none` and review normally; otherwise emit `ECONOMIC-DIFF: <which>` and run Steps 1–12. **Skip** when the numbers are analytics that never become an obligation (backtest statistics, greeks, implied vol, Monte Carlo) and no balance, order, payment or transfer is written. *The gate's job is to exempt.*

## Step 1: declare the tier

A `FINANCIAL_TIER:` line in the repo overrides inference; it may raise freely, and lowering it needs an explicit user statement, because under-tiering is the dangerous direction. The tier gates required *evidence*, never which rules apply.

| T | Placed here by |
|---|---|
| **T0** | No value-moving call reachable, or all behind `dry_run`/`paper`, and every host sandbox/testnet |
| **T1** | A value-moving call (`create_order`, `charges.create`, `transfer`, `eth_sendRawTransaction`, `withdraw`, a journal write) is reachable **and** a live credential path exists: `os.environ['*_API_KEY']`, a secrets-manager read, a non-sandbox host |
| **T2** | A `user_id`/`customer_id`/`tenant_id` on a balance or position row; a payout or withdrawal path; a crediting webhook; **two or more** venue/processor adapters; a transfer whose sides belong to different principals |
| **T3** | You are the record: matching or allocating across resting orders; a ledger writer that is not a mirror of an external processor; a custody signer; an ID assigner other systems consume; a sequencer or settlement batch; a mint/burn authority. **No external oracle exists.** |

**+1 tier, always reported:** a `SELECT` then `UPDATE` on a balance in separate statements · a money transaction whose isolation level is never set · a per-entity override on a solvency, credit-limit or liquidation check · an immutable deploy target with multi-day fix latency · one codebase deployed to N chains or regions.

## Steps 2–12: the pass, and the artefact each step owes

Run in order. **A step whose artefact is missing did not run.**

| # | Do | Artefact |
|---|---|---|
| 2 | List every operation that moves value, creates an obligation, or changes an amount someone is entitled to: what moves, from whom to whom, in what units, reversible for how long | the effects table. **Empty ⇒ Step 0 was wrong; re-run it** |
| 3 | Fix representation first; you cannot state an invariant about a quantity whose type is wrong | per quantity `(type, scale, source-of-scale, unit, rounding mode, beneficiary)` |
| 4 | Name the record per quantity and the window in which two systems disagree: the API is current state, the webhook is a trigger, the settlement report is the money | per quantity: authority + divergence window |
| 5 | State invariants as executable predicates (`Σ deltas == 0`, `Σ balances <= custodied`, `Σ lines == total`) and name the chokepoint function every value-mutating path ends in | the predicates + the chokepoint name + the path enumeration proving none bypasses it |
| 6 | Mark the provisional→final boundary: at which event does an inbound value become spendable? It is a **state**, never a boolean on one row | the state machine, spendable transition marked |
| 7 | Determine operation identity per effect | `(identity, minted-by, written-when, scope, retention, duplicate behaviour)` |
| 8 | Enumerate the counterparty's failure signals and classify each | the error-classification table |
| 9 | For every read-then-write on an authoritative quantity, name the interleaving that breaks Step 5 | per site `(isolation, lock, retry semantics, breaking interleaving)` |
| 10 | Enumerate every point the process can die between an external effect and the local commit | the crash-point list + the recovery action for each |
| 11 | Controls and rollout surface | the control inventory + the rollout answer |
| 12 | Name the external authority and join key for every Step 4 quantity, plus cadence and break aging. Where none exists, say so; that **is** the T3 declaration | the reconciliation spec. Test matrix → **fin-verification** |

## The non-negotiables

### Every stored amount carries its currency in the same row, struct or type

And every comparison, sum and equality check reads it. A schema with `amount_cents` and no `currency` column
is silently wrong for JPY, KRW, VND, CLP, ISK and UGX (exponent 0), for BHD, KWD, JOD, OMR and TND (exponent
3) and for CLF (exponent 4), and cannot validate the currency on an inbound webhook or fill. **A missing
`currency` column is the default shape of a hand-rolled amounts table.** One currency carries several scales
at one vendor: Stripe charges HUF/TWD at 2 decimals but pays out only in multiples of 100, ISK/UGX payouts
must end in `00`, Kraken calculates BTC at 10 decimals against 5 displayed. Charge, payout, display and
calculation scale are four possibly-different numbers, resolved from runtime metadata, never hardcoded.

### Mint the idempotency key from the identity of the intent instance

Unique per decision-to-act, byte-identical across every retry of that decision, **never a hash of the request
body**. Mint it in the process where the decision is made; commit it together with the exact serialized
request bytes and the target `(provider, endpoint/region, credential)` in the same durable transaction that
records the intent, **strictly before the first byte is sent**; on retry replay the stored bytes verbatim
under the same key. *Specialises G4 with the mechanics G4 does not carry:*

- `key_for(intent)` is stable across n calls and differs for distinct intents with byte-identical payloads.
- No `uuid4()` / `ulid()` sits inside any function a retry loop can re-enter.
- The key derives from a value that **survives `ROLLBACK`**. A Postgres `BIGSERIAL` does not: a key built
  from an uncommitted row id yields `N+1` on the retry and buys a second real refund.
- `(tenant, key)` has a unique index and there is no `SELECT`-then-`INSERT`; the unique-violation path is
  caught and resolved to the winner's stored result, never surfaced as a raw `UniqueViolation`.

WRONG: `db.add(refund); db.flush(); key = f"order-{order.id}-refund-{refund.id}"` … `except StripeError: db.rollback()`.
RIGHT: `key = uuid4()` at intent formation → `db.add(Attempt(key=key, body=body)); db.commit()` → call.

### Every external money call is three phases and the first one COMMITs

Write the intent row and `COMMIT` it (`flush()` inside an open transaction is not persistence), then call,
then record the outcome. *Specialises G4; these four clauses are the added mechanism:*

- On an ambiguous failure the intent row stays committed: no `rollback()`, no `DELETE`, **no compensating
  write until the outcome is known**.
- No `with session.begin()`, `engine.begin()`, `@transaction.atomic` or equivalent may lexically enclose the
  call; such a block rolls back on exception with no `rollback` token anywhere in the diff.
- The outcome record and any outbound event commit in the **same** transaction as the state change (outbox
  row or CDC), never as two independent operations.
- **Every field written pre-effect is read by the recovery path.** A startup `resolve_unresolved_intents()`
  pass loads each `INFLIGHT` row, uses the persisted identity to query the counterparty, and converges to
  exactly one effect. A persisted `client_id` no code path reads back is the same defect as not persisting it.

Worked negative: freqtrade writes nothing before `create_order` (`freqtradebot.py:963`) and cannot recover a
lost submit. Worked positive: hummingbot's `start_tracking_order` commits before the POST.

### A cursor advances only past a range you verifiably covered

*Specialises G6.* Verify completeness **before** committing progress: an error, a provider range rejection, a
result count at the provider's documented cap, or a truncated page is a **hole**, not an empty result. Two
forms G6 does not name:

- A "nothing to do" branch commits nothing. If the query is guarded by `if (addresses.size > 0)` then
  `saveCursor()` is guarded by the same condition; otherwise the cursor sprints to the head on a fresh
  deploy and every address registered afterwards can never see a deposit in a passed block.
- Re-read any set the loop filters on (address lists, subscription lists) at the **same cadence as the loop**,
  not once per outer iteration.

### Arrival order is not occurrence order

*Specialises G5.* Two guards, and the failure modes G5 does not enumerate.

**Legality.** The default arm is `_ => Err(InvalidStateTransition)`, never a silent `return`. A terminal state
accepts exactly the events by which the counterparty corrects a fact you already booked (a late fill that
crossed a cancel ack, a fill void) and nothing else. Do **not** write "terminal states are absorbing":
nautilus_trader ships `(Canceled, Filled) => Filled` annotated `// Real world possibility`, plus a fifteenth
status `Voided` for `(Filled, FillVoided)`. `(Canceled, Accepted)`, `(Filled, Accepted)`, `(Filled, Canceled)`
and `(Rejected, *)` stay absorbing by hitting the deny-by-default arm.

**Version.** `UPDATE watermarks SET v = :v WHERE id = :id AND v < :v`, proceed only on rowcount 1, in the same
transaction as the effect. `if seen_version(id) >= v: return` followed by a write is a TOCTOU that two
concurrent redeliveries both pass. Never write `if existing is not None and event.ts < existing.updated_at:
return`. Once a terminal event pops the entity, `existing` is `None`, the guard is skipped, and a replayed
pre-snapshot event re-inserts a phantom row.

**The version must be a total order; `>=` is correct only when it is.** Where the source publishes no version,
derive one from its own sequence. Where the only version available is a **coarse clock** (Stripe's `created`
is second-granularity, and `refund.created` and `refund.updated` on one `re_…` routinely share a second), the
watermark is the pair `(created, applied_event_ids)`, and an event at the same `created` is admitted unless
its id is already in the persisted set. A bare `>=` on a second-granularity timestamp discards the `succeeded`
event and the refund is pending forever. Wall-clock arrival is not a version; last-write-wins is not a policy.

### The lock, the key, the subject, the duration

A lock is held for the entire check→act critical section; its key is byte-identical in every process that
takes it; and the key it locks is the key the act mutates. Three mechanical checks:

**(1) Duration.** The transaction boundary encloses the **act**, not just the check.
`with engine.begin() as conn: SELECT ... FOR UPDATE` releases at the dedent, before the sign and broadcast it
was meant to protect. `FOR UPDATE SKIP LOCKED LIMIT 50` then `fetchall()` and a closed transaction lets N
workers process the same 50 rows. `async with session_factory()` with no `session.begin()`, and a
declared-but-never-acquired `asyncio.Lock()`, both hold nothing.

**(2) Key determinism.** `pg_advisory_xact_lock(hash(chain) & 0x7FFFFFFF)` uses Python's `hash()`, salted per
interpreter by `PYTHONHASHSEED` **for `str`, `bytes` and `datetime`**. `hash('ethereum')` differs in every
process while `hash(1) == 1` in all of them. Require a stable digest (`crc32`/`blake2b` over the UTF-8 bytes)
or a small integer registry regardless of the key's current type.

**(3) Subject.** The locked key is the key the act mutates. `FOR UPDATE` on the withdrawal row while
broadcasting a transaction keyed on the *nonce* satisfies both other clauses and still races. Commonly, an
admin `reject()` drops the row lock between the status check and `reverse()`, the withdrawal is broadcast
in that window, and the user keeps both the coins and the balance.

### No legal value doubles as "unset"

A money-path function whose input is absent raises or returns an explicit absent type. It never returns `0`,
`""`, `-1`, `null`, `0x00` or the last-known value. **Prices may be negative; quantities may not.** State it
in that direction, because "a price is not non-negative" is a double negative that produces
`assert price >= 0`. Concretely: `unrealized_pnl()` and `notional()` with no mark price ever set must not
return `Decimal(0)`, which a risk consumer reads as "flat, no exposure" on a live open position; and
`except ValueError: unrealized = Decimal(0)` inside a `snapshot()` re-introduces the same lie one layer up.

**FCA Final Notice, Citigroup Global Markets Ltd, 17 May 2024, ¶4.27:** an unavailable index price defaulted
to `-1`, the pre-trade estimate computed `quantity × -1` and rendered `-58,000,000`, and the trader read the
number they expected and clicked Execute. The same missing feed blanked the wave-notional soft block ("Due to
lack of market data, Wave notional cannot be found", ¶4.30) and it proceeded anyway. **A sentinel default in a
price lookup defeated the confirmation control.** Nomad, Aug 2022: trusted root `0x00` also meant "not proven".

### Classify the failure signal, and carry the classification to the decision point

*Specialises G4's UNKNOWN clause.* For every external effect, write down the counterparty's failure signals
and classify each **DEFINITE-NO**, **DEFINITE-YES** or **UNKNOWN**, and carry that classification to the
decision point instead of flattening it into a generic exception. **A single `except Exception:` around a
value-moving call destroys the classification and is the defect.**

- A path may be classified **DEFINITE-NO only where the counterparty documents, for that exact code, that the
  request was not enqueued.** Absent that document it is UNKNOWN. UNKNOWN is the only branch that can duplicate
  money, where a misclassified `400` pays twice.
- UNKNOWN paths query by the minted identity before any retry. DEFINITE-YES paths record the outcome.
- The status for "key seen, body differs" is inconsistent: 422 IETF · 409 Stripe · 400 OASIS ·
  `IdempotentParameterMismatch` AWS. **Do not branch on the code:** branch on *"not a clean 2xx for my key"
  ⇒ UNKNOWN ⇒ reconcile.*
- The sharper axis, from TigerBeetle's `transient()` (`tigerbeetle.zig:318`): *can retrying with identical
  request data produce a different outcome?* Insufficient funds is transient; a payload conflict is not.

## Representation: obligation or estimate

**A value a counterparty can demand is an obligation; a value nobody can demand is an estimate.** Obligations
(balance, posting, fee, tax, invoice line, settlement, payout, token amount, an order price/qty checked
against venue filters) live their whole life as integer minor units, a scaled integer with a declared
exponent, or an arbitrary-precision decimal built only from strings or integers. Estimates (greeks, implied
vol, VaR, Monte Carlo, backtest statistics, ML features, chart coordinates) are correctly binary floating
point: **"never use a float" is the wrong rule and a competent reviewer discards a suite over it.** A float is
read only as the argument of the single named, tested `quantize(value, scale, mode)` call that turns an
estimate into an obligation, and the storage and wire boundary is where this fails, not the arithmetic.

## Rounding direction, scale, and residue

Choose rounding direction **per operation, from the operation's category**, never as a global default, and
post the residue to a **named** account.

1. **If a statute, regulation, scheme rule or contract names the mode, level or day-count, copy it** and store
   it as per-jurisdiction/per-instrument configuration, never as a constant. EU Member States fix VAT rounding
   and round-up may be mandatory; euro conversion is legally half-up.
2. **Otherwise, for any exchange between two representations of one value where the counterparty chooses when
   and how often** (shares↔assets, LP tokens↔reserves, base↔quote, points↔cash): **floor what the system pays
   out, ceil what it collects, per leg.** One helper on both legs is the Balancer V2 ComposableStablePool bug
   (3 Nov 2025, >$120M). **Direction without a scale is a no-op:** name the scale at every call site, since
   `ceil` at 18 decimals rounds nothing. Check the denominator too: if it can reach 0 or 1, or be inflated by
   a transfer bypassing the accounting entrypoint, add virtual shares/assets or seed-and-burn.
3. **When splitting one exact total into parts, direction is irrelevant and conservation is mandatory:**
   largest-remainder in integer minor units, deterministic tie-break, `assert Σ parts == total`.

**Property test, stated over multiple actors:** for an adversarially ordered sequence of operations by
*different* principals, `Σ outputs ≤ Σ inputs` per asset and no user-initiated round trip returns more than it
put in. The per-actor form is what survives an A-deposits / B-withdraws extraction.

## Idempotency key enforcement

The key parameter is **required at the type level**, not `Optional[str] = None` with enforcement deferred to
prose about the API layer. Two branches, keyed on an observable predicate:

- **You own the server or the resource.** The server compares the stored row's economically significant fields
  (from, to, amount, currency) to the incoming request; a key match with a field mismatch returns an error
  that **neither executes the request nor replays the stored response**, and the fingerprint is a salted HMAC
  over the canonical payload, not a bare digest. For a *capped* operation ("transfer up to X", a balancing
  transfer) the fingerprint compares the request as the client meant it, so a retry carrying the original cap
  matches even though the committed amount differed.
- **You are a client of someone else's processor.** Never rely on that check; it is optional in every
  standard and undocumented at Adyen and PayPal. A changed body is a new intent and therefore a new key, and
  you add your own pre-send guard comparing the outgoing bytes to the bytes stored with that key.

The usual half-implementation is `idempotency_key text UNIQUE` on the table, a parameter left optional, and
the row's fields never compared. The replay then lets `reverse_transfer` mark a transfer reversed **while
writing zero compensating entries**.

## Dedupe state is as durable as what it protects

Deduplication state is persisted **in the same transaction as the state it protects**. An in-memory
`_seen_ids` set, an LRU cache, or a process-local dict evaporates on restart, precisely when the standard
recovery path (a REST backfill, a webhook redelivery, a queue redrive) replays every already-applied event.
Write the dedupe row and the balance/position mutation in one transaction, keyed on the counterparty's own
identifier. A comment reading "dedups by tradeId" is not evidence: the set behind it is usually a
process-local one, and this is the most common way dedupe silently stops working.

## A ceiling that warns is not a control

- The ceiling **rejects** the proposed operation, in the same transaction as the write, before any external
  effect. Declining costs nothing but the operation.
- **At least one ceiling is an aggregate** over the batch, wave or basket, not only per item; per-item limits
  are satisfiable by an unbounded number of items. FCA/CGML ¶4.18(a), ¶4.33: per-item hard blocks (US$2bn
  notional, 200m shares) let **US$196bn** through, and the notice states a basket-level wave notional hard
  block would have prevented the incident.
- The kill switch is exercisable **faster than the loss accrues**, and **the component that tripped it cannot
  reset it.** Compound could not stop an ~$50M mis-distribution (~168,000 COMP claimed) inside a 7-day
  governance process; Goldman ¶8/¶9/¶31, blocks lifted repeatedly by the author of the policy being violated.
- The anomaly signal goes to a channel named by a **config key with no default that raises at import if
  unset**, not a log line, a metric nobody alerts on, or a distribution list. Knight ¶23/¶24: the 33
  Account's $2m limit was "linked to no automated controls".
- **A per-entity override on a solvency, credit-limit or liquidation check** raises the tier by one **and**
  every change to it is field-level audit-logged (who, when, old value, new value), with no code path able to
  set it without one.

## Change and rollout surface

- **Never repurpose a flag, enum, or field a deployed consumer still reads: grep every deployed artefact for
  readers before reusing the value**, and delete dead money paths rather than leaving them callable.
- **When a shared helper is relocated or reused by a second caller, re-execute every existing caller under
  test before the change lands.** Knight ¶14/¶41: *"moving the cumulative quantity function inadvertently
  disabled the cumulative quantity functionality in the Power Peg code"*. Never retested for nine years.
- **Every shard, stripe, partition or region of a money path is exercised by the pre-deployment test, and
  coverage is asserted per shard**, not by a representative one (Goldman ¶23: A-H and L-Z tested, I-K not).
  Rollback is a change with its own test. Knight ¶27: the rollback spread the fault to all eight servers.

## Time and business date (SHOULD)

Every money-path timestamp is timezone-aware UTC with an explicit type, and the **business date** derives from
a named cutoff in a named timezone, never `date.today()` or a naive `datetime.now()`. Funding intervals,
settlement dates, accrual periods, statement cutoffs, failure windows and retention bounds all key on it.
Never order events across nodes by wall clock.

---

## REQUIRED OUTPUT: every economic diff ends with this, in this order

```
ECONOMIC-DIFF: <AMOUNT|EFFECT|AUTHORITY|REPLAY|ROLLOUT — which, or none>
Financial tier: T<n> (inferred from: <signal>)
```

Then the Step 2–12 artefacts, as tables:

| slot | columns | step |
|---|---|---|
| Effects | what moves · from → to · units · reversible for how long | 2 |
| Quantities | quantity · type · scale · source-of-scale · unit · rounding mode · beneficiary | 3 |
| Authority | quantity · system of record · divergence window | 4 |
| Invariants | predicate · chokepoint fn · paths proven not to bypass it | 5 |
| Identity | effect · identity · minted by · written when · scope · duplicate returns | 7 |
| Failure signals | signal · DEFINITE-NO / DEFINITE-YES / UNKNOWN · where the counterparty documents it | 8 |
| Concurrency | site · isolation · lock · retry semantics · the breaking interleaving | 9 |
| Reconciliation | quantity · external authority · join key · cadence · break aging | 12 |

Then, last, and never omitted:

```
### NAMED RISKS
| risk | implemented at file:line | test name |
```

**A row with no `file:line` fails the run.** If you will not implement it, make the path uncallable with a
`raise NotImplementedError` **on a path that is actually reached**.

## References

Each row is a standing instruction: when the literal appears, read the file **immediately** and apply it in order. **Do not summarise it.**

| file | read it when the diff contains |
|---|---|
| [representation](references/representation.md) | `Decimal(`, `float(`, `f64`, `double`, `Float`, `Numeric`, `NUMERIC(`, `REAL`, a protobuf `double`, a JSON `number` amount, `decimals()`, `scale`, `exponent`, or an ORM column declaration on an amount |
| [rounding-and-allocation](references/rounding-and-allocation.md) | `round(`, `floor`, `ceil`, `trunc`, `int(`, `quantize(`, `//`, `* rate`, `/ total`, `split`, `allocate`, `pro_rata`, `convert`, or `%` applied to a money value |
| [indeterminacy-and-idempotency](references/indeterminacy-and-idempotency.md) | `idempotency_key`, `Idempotency-Key`, `request_id`, `retry`, `backoff`, `tenacity`, `timeout=`, `except Exception`, `catch (err)`, `5xx`, `429`, or any `except`/`catch` around a value-moving call |
| [concurrency-and-failure-boundaries](references/concurrency-and-failure-boundaries.md) | `FOR UPDATE`, `SKIP LOCKED`, `isolation_level`, `SERIALIZABLE`, `REPEATABLE READ`, `40001`, `advisory_lock`, `asyncio.Lock`, `session.begin()`, `engine.begin()`, `@transaction.atomic`, `outbox`, or a balance read and written back |
