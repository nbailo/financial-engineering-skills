# Indeterminacy and idempotency

You are about to write a value-moving call, its `except`/`catch` arm, or the key that makes retrying it safe.
Two mechanisms have to be right and are routinely collapsed into one: the **classification** of what the
counterparty told you (did the effect happen?) and the **identity** under which you may safely ask again. Below:
per-signal classification tables, the five-phase key lifecycle, the IETF key/fingerprint split, the server
cases, the rollback trap, the retention arithmetic, and the loop that consumes the pre-effect write.

## Contents

- Two axes, not one: did it happen, and can a retry change the answer
- Signal classification table — HTTP, processors, venues, TigerBeetle
- Carrying the classification to the decision point
- The idempotency key lifecycle: five phases
- Key and fingerprint are two mechanisms (IETF draft-07)
- Server behaviour, case by case
- The rollback trap: `ROLLBACK` undoes the row, not the sequence
- Retention, scope, and length: the retry loop must live inside the window
- Concurrent duplicates: 409 means back off and READ
- `resolve_unresolved_intents()` — the startup loop
- Not-found is not proof of absence
- Grep list

## Two axes, not one

Every response to a value-moving call answers, or fails to answer, two independent questions.

| Axis | Question | Values | Who decides |
|---|---|---|---|
| **Occurrence** | Did the effect happen at the counterparty? | `DEFINITE-NO` · `DEFINITE-YES` · `UNKNOWN` | the counterparty's documentation, for that exact code |
| **Retry-safety** | Can retrying with *identical* request data produce a different outcome? | transient · non-transient | the state, not the network |

The second axis is TigerBeetle's, verbatim — `src/tigerbeetle.zig:318`:

```zig
/// Returns `true` if the error code depends on transient system status and retrying
/// the same transfer with identical request data can produce different outcomes.
pub fn transient(result: CreateTransferStatus) bool {
```

`exceeds_credits` / `exceeds_debits` (insufficient funds) are **transient** — a later credit makes the same
transfer succeed. Every `exists_with_different_*` is **non-transient**: a payload conflict is deterministic and
retrying is pointless. Most codebases get this backwards in both directions.

**The load-bearing constraint on the first axis:** classify a path `DEFINITE-NO` **only where the counterparty
documents, for that exact code, that the request was not enqueued.** Absent that document it is `UNKNOWN` —
this is the only branch that can duplicate money, and a misclassified `400` pays twice.

CCXT is the worked negative. Its manual states the ambiguity correctly (`ccxt/wiki/Manual.md:8816`: *"When a
`RequestTimeout` is raised, the user doesn't know the outcome of a request"*) and forty lines earlier says of
the parent class *"**OperationFailed** can be blindly re-tried and should success"*. `RequestTimeout extends
NetworkError extends OperationFailed` (`ts/src/base/errors.ts:219`, `:177`, `:171`), and the retry predicate
in the funnel every signed REST call passes through is `e instanceof OperationFailed`
(`ts/src/base/Exchange.ts:6435`) with **no HTTP-method or path discrimination** — a `POST` create-order retries
on the same terms as a `GET` ticker (`:6455`, `:629`). Default `retries = 0`, but
`exchange.options['maxRetriesOnFailure'] = 3` arms blind order re-submission exchange-wide (`:6796`, `:6814`).

## Signal classification table

Fill this in per counterparty from *their* docs. These rows are the ones vendor documentation establishes verbatim.

| Signal | Occurrence | Retry-safe with same key? | Establishing text |
|---|---|---|---|
| Clean `2xx` for my key | DEFINITE-YES | n/a — record the outcome | — |
| Stripe `200` + `Idempotent-Replayed: true` | DEFINITE-YES, already applied | do not re-apply downstream effects | Stripe idempotent-requests docs |
| Stripe `500` | **UNKNOWN**, and the 500 **is cached under the key** | retrying returns the same 500 forever | *"Treat requests that return `500` errors as indeterminate"*; Stripe's back-office may roll the charge *forward* to the network afterwards, surfacing the object only by webhook |
| Stripe `409 Conflict` | UNKNOWN — concurrent attempt in flight | back off and **read** | *"The request conflicts with another request (perhaps due to using the same idempotent key)"* |
| Stripe `429` | UNKNOWN | new key only after resolving by read | *rate limiters run before the idempotency layer* — so the 429 is not a replay. Stripe does **not** document that the endpoint was never reached; under the DEFINITE-NO rule it stays UNKNOWN |
| Stripe request-validation `400` | DEFINITE-NO | fix the payload, new intent, new key | *"We save results only after the execution of an endpoint begins. If incoming parameters fail validation … we don't save the idempotent result because no API endpoint initiates the execution."* |
| Stripe `idempotency_error` (409) | DEFINITE-NO for *this* body | never retry this body under this key | *"an `Idempotency-Key` is re-used on a request that does not match the first request's API endpoint and parameters"* |
| Stripe `424 External Dependency Failed` | UNKNOWN | reconcile | Stripe error-handling docs |
| Adyen `422` or `409` + errorCode **704** *"request already processed or in progress"* | UNKNOWN — concurrent | header `transient-error: true` means retry later **with the same key**; absent or `false` means **do not retry** | Adyen API-idempotency docs |
| Binance `-1007 TIMEOUT` | UNKNOWN | never resubmit; query | *"Timeout waiting for response from backend server. Send status unknown; execution status unknown."* |
| Binance `-1006 UNEXPECTED_RESP` | UNKNOWN | query | carries *"Execution status unknown."* |
| Binance HTTP `5XX` (futures `503`) | UNKNOWN | query | *"It is important to **NOT** treat this as a failure operation; the execution status is **UNKNOWN** and could have been a success."* |
| Binance `429` / `418` on an order endpoint | UNKNOWN | query | no venue documents a 429 as proof of non-creation |
| Binance `-2013 NO_SUCH_ORDER` right after placement | UNKNOWN | re-query with backoff | three data sources (Matching Engine / Memory / Database) with asynchronous propagation |
| Binance `-1013`, `-2010` with a filter message, `-4164` | DEFINITE-NO | fix and re-intent | documented matching-engine business rejections |
| Binance `-2011 CANCEL_REJECTED` | the *order* is no longer open — re-read its terminal state | do not retry the cancel | expected in normal operation |
| Binance `409` + `-2021` (cancelReplace) | **split**: one leg succeeded | determine which leg before anything else | `-2022` = both failed ⇒ DEFINITE-NO |
| Socket timeout / connection reset with no response | UNKNOWN | same key only | stripe-node `src/RequestSender.ts:400-403`: *"those codes can surface after the API processed the request, so the retry needs a key to dedupe against"* |
| TigerBeetle `exists` | DEFINITE-YES | *"many applications should handle `exists` exactly as `created`"* | `create_transfers` reference |
| TigerBeetle `exists_with_different_amount` (and 10 siblings) | DEFINITE-NO for this body | non-transient | `src/tigerbeetle.zig:236-247` |
| TigerBeetle `id_already_failed` | DEFINITE-NO, **permanently** | *"the application … must generate a new idempotency id"* | `src/state_machine.zig:3736` |
| TigerBeetle `exceeds_credits` / `exceeds_debits` | DEFINITE-NO now | **transient** — same id may succeed later | `tigerbeetle.zig:318` |

**Do not branch on the status code for "key seen, body differs."** It is `422` (IETF §2.7) · `409` (Stripe,
Brandur) · `400` + `Repeatability-Result: rejected` (OASIS) · `IdempotentParameterMismatch` (AWS) · per-field
`exists_with_different_*` (TigerBeetle) · "an error" (Square) · **undocumented** (Adyen, PayPal). Branch on
*"not a clean 2xx for my key" ⇒ UNKNOWN ⇒ reconcile.*

## Carrying the classification

A single `except Exception:` around a value-moving call destroys the classification, and that is the defect —
not the missing log line, not the missing retry. The classification is data that must reach the decision point.

```python
Occurrence = Literal["DEFINITE_NO", "DEFINITE_YES", "UNKNOWN"]

@dataclass(frozen=True)
class CallResult:
    occurrence: Occurrence
    transient: bool            # can an identical retry change the answer?
    provider_ref: str | None   # populated only on DEFINITE_YES
    signal: str                # "-1007", "http_500", "exists_with_different_amount"

def classify(exc_or_resp) -> CallResult: ...   # one per counterparty, table-driven

res = classify(exc)
if res.occurrence == "DEFINITE_YES":
    record_outcome(intent, res.provider_ref)         # never re-send
elif res.occurrence == "UNKNOWN":
    query_by_minted_identity(intent)                 # BEFORE any retry
elif res.transient:
    schedule_retry(intent)                           # same key, same bytes
else:
    fail_intent(intent, res.signal)                  # a new intent needs a new key
```

hummingbot ships the defect and its own counter-example in one file. `exchange_py_base.py:466` catches
`except Exception as ex` around the create POST and routes it to `_update_order_after_failure` (`:519-531`),
which writes `new_state=OrderState.FAILED` — the same terminal state used for a client-side `min_order_size`
rejection (where FAILED is true) and for a socket timeout (where it is a guess); the strategy receives
`MarketOrderFailureEvent` and typically re-places. The *cancel* path in the same file is correct
(`:539-546`): `except asyncio.TimeoutError:` logs and changes no state.

## The idempotency key lifecycle

| Phase | What happens | The failure if you skip it |
|---|---|---|
| **1 — mint** | At intent formation, in the process where the *decision* is made. Random UUIDv4 / ULID / UUIDv7, or the identifier of a durable object that **is** the intent instance. Never a hash of the request body. | `uuid4()` inside the retried function mints a fresh key per attempt ⇒ zero protection (FM-8) |
| **2 — commit** | `INSERT` the intent row carrying `(key, exact serialized request bytes, provider, endpoint/region, credential, state='INFLIGHT')` and **`COMMIT`** it. `flush()` inside an open transaction is not persistence. | crash between effect and record leaves no evidence the effect happened (FM-7) |
| **3 — send** | Send the stored bytes. No `with session.begin()` / `@transaction.atomic` may lexically enclose the call — such a block rolls back on exception with no `rollback` token in the diff. | the ambiguous failure rolls back the row that exists for exactly that failure |
| **4 — replay** | On retry, re-send the **stored bytes verbatim** under the **same key**, against the same `(provider, endpoint/region, credential)`. Never re-serialize. | a re-quote, a nonce, a `Date` header, map iteration order or float formatting changes the payload; OASIS rejects on any header change but `Date` |
| **5 — resolve** | Record the outcome, or converge an `UNKNOWN` by querying under the minted identity. Every field written in phase 2 is **read** by this path. | a persisted `client_id` no code path reads back is the same defect as not persisting it |

Stripe's SDKs implement phases 1 and 4 structurally, visible from the call graph alone. `stripe-python` builds
the header dict at `stripe/_api_requestor.py:567-573` and hands it to `HTTPClient.request_with_retries`
(`_http_client.py:258`), whose `while True:` loop (`:267`) re-issues with the *same dict* — the key is computed
**above** the loop. The key itself, `_api_requestor.py:86-88`:

```python
def _generate_idempotency_key() -> str:
    b = os.urandom(16)
    return f"{b[0:4].hex()}-{b[4:6].hex()}-{b[6:8].hex()}-{b[8:10].hex()}-{b[10:].hex()}"
```

128 bits of `urandom`; `stripe-node` does the same at `src/RequestSender.ts:391-392`
(`stripe-node-retry-${uuid4()}`). **Neither SDK hashes anything** — the payload comparison is the server's job.

**The limit of that protection:** the SDK-minted key covers retries *inside one SDK call*
(`max_network_retries: int = 2`, `stripe/__init__.py:39`). A caller who wraps `stripe.Charge.create()` in their
own `try/except` and calls again gets a **new** random key and a **second charge**. Same for CCXT's
auto-generated client order ID — `this.uuid22()` is minted inside `createOrderRequest`
(`ts/src/binance.ts:5791-5801`), once per `createOrder()` invocation, so
`try { createOrder() } catch (RequestTimeout) { createOrder() }` produces a different `newClientOrderId` and a
second order. **Phase 2 is the gap no SDK can close for you.**

Each hop mints its own key from its own durable record rather than forwarding the caller's: Brandur passes
`idempotency_key: 'rocket-rides-atomic-#{key.id}'` downstream, `key.id` being the primary key of his local
`idempotency_keys` row, so a restart re-derives it. A forwarded caller key collides across tenants and lets a
caller choose your key (IETF §5); keep theirs only as your own dedup index, scoped `(caller_id, caller_key)`.

## Key and fingerprint

`draft-ietf-httpapi-idempotency-key-header-07` (15 Oct 2025) specifies both mechanisms in adjacent sections and
assigns them to different parties.

- §2: *"a unique value **generated by the client** which the resource uses to recognize subsequent retries of
  the same request"*; §2.2: *"MUST NOT be reused with another request with a different request payload … It is
  RECOMMENDED that a UUID or **a similar random identifier** be used."*
- §2.4: *"An idempotency fingerprint MAY be used **in conjunction with** an idempotency key … generated from
  request payload data **by the resource**."* §2.5.2 orders the resource's duties: identify the key from the
  header → **generate the fingerprint if required** → enforce idempotency.

| | **Key** | **Fingerprint** |
|---|---|---|
| Question | "Which operation is this?" | "Is this the same operation I already have under this key?" |
| Minted by | client, at the layer where the intent is formed | server, from the request payload |
| Value | random / intent-instance identity | HMAC or field-match over the economically significant fields |
| Role | **lookup** — it is the dedup index | **guard** — it never selects a row, it validates one |
| Absent ⇒ | double-charge / double-order | the second, *different* request silently returns the **first** request's success |
| Normative | MUST (IETF §2.2) | MAY (IETF §2.4) |

**A payload hash as the key is a money bug, four ways.** (a) Two legitimate identical intents collapse — £10 to
the landlord on Tuesday and £10 on Wednesday hash the same; the second returns the first transfer's id, the
payee never receives it, and *the payer's own books show one transfer where one is expected*, so reconciliation
on the payer side finds nothing. (b) The key becomes guessable; IETF §5 records low-entropy keys as a
cross-tenant read primitive (*"attackers MAY determine other keys and use them to fetch existing idempotent
cache entries, belonging to other clients"*), and Adyen's stated mitigation is the *"version 4 (random) UUID
type to prevent two API credentials under the same account from accessing each others responses."* (c) Any
field that legitimately moves between attempts changes the hash — the scheme *causes* the regenerated-key
failure it claims to prevent. (d) A cryptographic hash is uniformly distributed by construction, the worst
case for B-tree/LSM insert locality; Shopify measured ~50% INSERT improvement moving to ULIDs.

**Storage form of the fingerprint:** `HMAC(server_secret, per-row salt, canonical_payload)`, not `sha256(body)`
— a bare digest is an offline oracle on the amount and counterparty after a database-only breach (IETF WG issue
#40). Scope that requirement to the you-own-the-server branch: on the client side, an attacker who can craft a
preimage against your own idempotency table already has write access to it.

**The capped-operation exception**, which no idempotency specification states and TigerBeetle implements
(`src/state_machine.zig:4016-4030`):

```zig
// This is a special case in the idempotency check:
// When _resubmitting_ the same balancing transfer, the amount will likely be
// different from what was previously committed, but as long as it is within the
// range of possible values it should fail with `exists` rather than
// `exists_with_different_amount`.
if (t.flags.balancing_debit or t.flags.balancing_credit) {
    if (t.amount < e.amount) return .exists_with_different_amount;
} else {
    if (t.amount != e.amount) return .exists_with_different_amount;
}
```

The fingerprint compares **the request as the client meant it**, not the committed result. For "transfer up to
X", a retry carrying `X` must match even though the committed amount was less.

## Server behaviour, case by case

| Case | Correct behaviour | Signals in the wild |
|---|---|---|
| **Key unseen** | Execute — but write key + fingerprint + `IN_PROGRESS` durably **before** any external effect, enforced by a unique index on `(tenant, key)` or a conditional write. Never `SELECT`-then-`INSERT`: the concurrent pair is the whole point. | Brandur `CREATE UNIQUE INDEX idempotency_keys_user_id_idempotency_key ON idempotency_keys (user_id, idempotency_key)`; Powertools conditional `PutItem` — *"If locking fails, it means we already have an idempotency record"* |
| **Same key + same body + IN FLIGHT** | Do **not** execute. Return an explicit conflict, or block until the first completes. **Never fabricate success** — the response body would be a lie about state that does not exist yet. Hold the lock as a **lease**, not forever. | IETF §2.6/§2.7 → `409` + problem+json *"A request is outstanding for this Idempotency-Key"*; Stripe `409`; Adyen `422`/`409` + `704`; Powertools `IdempotencyAlreadyInProgressError`; Brandur `409 error_request_in_progress`, *"Only acquire a lock if the key is unlocked or its lock has expired"* |
| **Same key + same body + COMPLETED** | Replay the stored status code and body, **and signal that it is a replay**. | `Idempotent-Replayed: true` (Stripe) · `200` vs `201` on capture (PayPal) · `Repeatability-Result` (OASIS) · `exists` vs `created` (TigerBeetle) · **none at all** (Open Banking UK always returns `201`; Adyen only echoes the key header) |
| **Same key + DIFFERENT body** | **Reject.** Neither execute nor replay. | Every source agrees on the behaviour and none agrees on the code — see the "do not branch on the code" note above. Open Banking UK frames it as fraud: *"If the TPP changes the request body, the ASPSP must not modify the end resource. The ASPSP may treat this as a fraudulent action."* |
| **Key expired / outside retention** | Two designs exist and the common one is dangerous. Prefer explicit rejection for money. | Silent degradation: Stripe *"We generate a new request if a key is reused after the original is pruned."* Explicit: OASIS carries `Repeatability-First-Sent` so the server can answer deterministically — *"the server cannot guarantee the request was not already executed and so MUST return an error"* → `412 Precondition Failed` + `Repeatability-Result: rejected` |

**Evaluate expiry in application code against a stored timestamp, never via the store's TTL.** Powertools:
*"We don't rely on DynamoDB or any persistence storage layer to determine whether a record is expired to avoid
eventual inconsistency states."* DynamoDB TTL deletion can lag; a TTL-driven correctness decision is a race.

**Unresolved across sources, and it matters: is a *failed* outcome replayed or re-executed?** Stripe caches
failures including `500`s and most `400`s (*"A request that returns a `400` sends back the same `400` if
followed by a new request with the same idempotency key"*); OASIS mandates the opposite, re-executing when the
original response was `4xx`/`5xx`; TigerBeetle poisons the id permanently (`id_already_failed`) and requires a
new one. Three standards-or-better sources, three incompatible contracts. **Never assume a replayed error is
authoritative, and state in your own docstring which contract your server implements.**

Give in-progress records a **short, separately configured expiry** derived from the operation's own deadline
(Powertools `in_progress_expiry_timestamp`), so a crashed attempt does not lock the key for the whole retention
window. And **never `DELETE` the record on exception** when the wrapped code may already have performed an
external effect: mark it indeterminate — deletion erases the only trace of the effect.

## The rollback trap

`ROLLBACK` undoes the row. It does not undo the sequence. PostgreSQL, *Sequence Manipulation Functions*: *"to
avoid blocking concurrent transactions that obtain numbers from the same sequence, a `nextval` operation is
never rolled back."* MySQL `AUTO_INCREMENT` behaves the same way.

```sql
BEGIN;
INSERT INTO order_refunds (order_id, amount_cents) VALUES (42, 5000) RETURNING id;  -- id = 7
--   key := 'order-42-refund-7'  →  POST /v1/refunds  →  socket timeout
ROLLBACK;                     -- the row is gone; the sequence is not
BEGIN;
INSERT INTO order_refunds (order_id, amount_cents) VALUES (42, 5000) RETURNING id;  -- id = 8
--   key := 'order-42-refund-8'  →  Stripe sees a brand-new request  →  A SECOND REAL REFUND
```

Deriving the key from an uncommitted autoincrement id is a common refund-path defect. Wrong shape, then right:

```python
db.add(refund); db.flush()                       # not committed
key = f"order-{order.id}-refund-{refund.id}"     # derived from an uncommitted BIGSERIAL
try:    stripe.Refund.create(..., idempotency_key=key)
except StripeError: db.rollback()                # destroys the reservation, keeps the sequence
# ---- right ----
key = uuid4()                                    # phase 1, at intent formation
db.add(Attempt(key=key, body=body, provider="stripe", state="INFLIGHT"))
db.commit()                                      # phase 2 — COMMIT, not flush
stripe.Refund.create(..., idempotency_key=str(key))   # phase 3, outside any open transaction
```

**Legal key sources — they survive `ROLLBACK`:** a client-minted UUIDv4 / ULID / UUIDv7 formed at intent
formation; the primary key of a row committed in an *earlier* transaction; an already-existing foreign business
identifier (Open Banking UK's `x-idempotency-key: FRESCO.21302.GFX.20` equals the payment's
`EndToEndIdentification`; TigerBeetle calls this "Reuse Foreign Identifier"). **Illegal:** any
`BIGSERIAL`/`SERIAL`/`AUTO_INCREMENT` read inside the transaction that will roll back, anything derived from
it, any `RETURNING id` from an uncommitted `INSERT`.

Sibling defect on the same path: check-then-insert with no `ON CONFLICT` handling, where the honest retry
surfaces a raw `UniqueViolation`. `(tenant, key)` gets a unique index, the insert is conditional, and the
unique-violation path is **caught and resolved to the winner's stored result** — never re-raised.

## Retention, scope, and length

A retry that outlives the provider's retention window does not error: the provider forgets the key, executes a
**second real operation**, and returns `2xx`. Nothing in the response distinguishes it from the first.

| Provider | Documented retention | Key scope | Max length |
|---|---|---|---|
| Stripe | **≥24 h**, then pruned; *"We generate a new request if a key is reused after the original is pruned."* | account | ≤255 |
| Adyen | **≥7 days** after first submission | company account — **and not checked across regional endpoints** | ≤64 |
| PayPal (Orders v2 capture) | **6 h** | unique per request **and per API call type** (authorize ≠ capture) | not stated in the fetched docs |
| Open Banking UK | the preceding **24 h** | per TPP | ≤40 |
| AWS `ClientToken` | not stated | per **Region**; per **Availability Zone** for zonal requests | ≤64 ASCII |
| AWS Powertools | default `expires_after_seconds` **3600** | per configured key attributes | n/a |
| Square, Braintree | unspecified | unspecified | unspecified |

Three consequences, each a diff-level check:

1. **Bound the same-key retry loop by wall clock to well inside the provider's window, and store that bound as
   an asserted constant next to the provider's number** (`STRIPE_RETENTION = timedelta(hours=24)`,
   `RETRY_HORIZON = timedelta(hours=6); assert RETRY_HORIZON < STRIPE_RETENTION`). Past the bound, stop
   retrying, mark the attempt `UNKNOWN`, and resolve by querying your own reference or the settlement report.
   **Never re-send an expired key.**
2. **The DLQ, the manual-replay button, and the webhook resend horizon all count as retries.** Stripe permits
   manual webhook resend far beyond the 24 h key prune; a Powertools 1 h default against a queue redrive the
   next morning is the same defect, smaller.
3. **Pin the key to the `(provider, endpoint/region, credential)` recorded at mint time and retry only against
   that tuple** — a mid-retry failover from Adyen's EU endpoint to the US endpoint duplicates the payment with
   a perfectly correct key. Encode to the narrowest length across every provider you target, at construction.

## Concurrent duplicates

`409` — and Adyen's `422`/`409` with errorCode `704` — means *another attempt of this same operation is in
flight*. It is `UNKNOWN`, not `FAILED`: the operation may be executing at this instant. The defect is a generic
status-class handler.

```javascript
if (res.status >= 400 && res.status < 500) { markPaymentDeclined(order); }   // WRONG
```

The first request succeeds, the order is marked declined, the customer is charged and not fulfilled. The
correct arm is **back off and READ** — re-query under the same key, or query the provider by your own reference
— and only then decide. Same for Stripe's cached `500`: retrying returns `500` forever while the money may
already have moved, and Stripe's back-office may roll the charge *forward* to the network afterwards,
surfacing the object **only via webhook** — so subscribe to the webhook types for objects you never see in an
API response.

Both major processors ship an in-band directive for this: Adyen's `transient-error: true` header means the
request *can* be retried later under the same key (absent or `false` ⇒ **do not retry**), and Stripe's
`Stripe-Should-Retry: true|false` is honoured by both SDKs (`stripe/_http_client.py:143-149`,
`src/RequestSender.ts:334-341`) as an override of the client's own policy: *"The API may ask us not to retry
(eg; if doing so would be a no-op) or advise us to retry (eg; in cases of lock timeouts); we defer to that."*

Answering a concurrent duplicate with a conflict versus blocking until the first completes is a genuine design
choice — no source establishes one as correct; 409 pushes retry responsibility to the client, blocking costs
liveness. **Synthesising a success is not on the list.**

## `resolve_unresolved_intents()`

Phase 2 exists to feed this loop; if nothing reads the row back, the row is decoration.

```python
def resolve_unresolved_intents(session, clock):
    rows = session.execute(text("""
        SELECT id, tenant_id, idem_key, provider, endpoint, credential_id, request_bytes,
               our_reference, created_at
          FROM payment_intents WHERE state = 'INFLIGHT'
         ORDER BY created_at FOR UPDATE SKIP LOCKED
    """)).all()
    for r in rows:
        age = clock.now() - r.created_at
        if age < RETENTION[r.provider]:
            # inside the window: the key is still a valid dedup token
            res = provider(r.provider, r.endpoint, r.credential_id).get_by_idempotency_key(r.idem_key)
        else:
            res = None                       # expired: NEVER re-send under this key
        if res is None:
            res = query_ladder(r)            # our_reference → object list over [created_at, now] → settlement report
        if res is None:
            mark_break(session, r, reason="unresolved_after_ladder")   # halt this path; do not guess
            continue
        record_outcome(session, r, res)      # exactly one effect
    session.commit()
```

Four properties, each independently checkable:

- **It runs before the system takes new action.** nautilus_trader gates the trader on it —
  `crates/live/src/node/mod.rs:440-447` calls `perform_startup_reconciliation()`, and on failure
  `abort_startup(...)` then `Err`; `start_trader()` (`:454`) is never reached.
- **It queries by the identity persisted in phase 2**, not by a balance diff. CCXT's documented
  timeout-recovery procedure falls back to `fetchBalance()` and infers from a balance change — race-prone
  against fees, funding, and any other strategy on the same account.
- **It terminates in a break, not a guess.** Bound the attempts; the terminal state is "unknown — needs
  reconciliation", with that instrument's or tenant's path closed. Jepsen flagged retry-until-reply as an
  unresolved hazard in TigerBeetle because it converts definite errors into indefinite ones.
- **Any identifier it must invent is deterministic over counterparty-supplied fields including a counterparty
  timestamp**, so the same inference after a restart dedupes against itself — nautilus,
  `crates/execution/src/reconciliation/ids.rs:103-107`: *"the same logical event replayed after restart still
  hashes the same (venue re-reports identical ts)."*

Worked negative: freqtrade writes nothing before `create_order` (`freqtradebot.py:963`), so there is no
`INFLIGHT` row to enumerate and a lost submit is unrecoverable by construction. Worked positive: hummingbot's
`start_tracking_order` runs before the POST with `exchange_order_id=None`, serialisable via `to_json`
(`in_flight_order.py:273`) before anything hits the wire.

## Not-found is not proof of absence

A query that returns nothing immediately after an ambiguous submit is a fourth answer, not a `DEFINITE-NO`.
Binance documents three data sources with different staleness and asynchronous propagation; `-2013` can mean
"not yet visible in the replica you queried" or "archived after 90 days" (`-2026`).

hummingbot is a rare implementation that treats this correctly
(`hummingbot/connector/client_order_tracker.py`), in three parts: **a single not-found is not evidence** —
`_lost_order_count_limit` defaults to 3 (`:45`), so it takes four consecutive not-founds to declare an order
lost (`:221-250`); **a "lost" order remains fillable** — `all_fillable_orders` is
`{**self.active_orders, **self.lost_orders}` (`:107-109`), so a fill arriving after the declaration is still
applied to it; and **lost status survives restart** — `restore_tracking_states` (`:152-164`) re-files a
persisted failure into `_lost_orders` rather than dropping it, and the order leaves the set only when the
venue speaks authoritatively about it (`:295-301`).

## Grep list

| Pattern | What it finds |
|---|---|
| `except Exception` / `catch (e)` within 5 lines of a value-moving call | the classification flattened at the point it was created |
| `uuid4()` / `ulid()` / `uuid22()` inside a function a retry loop re-enters | phase 1 in the wrong place |
| `flush()` followed by a key derivation | the rollback trap |
| `rollback()` / `DELETE FROM .*intent` in an `except` around an external call | phase 2 undone by the failure it exists for |
| `with session.begin()` / `@transaction.atomic` lexically enclosing an external call | implicit rollback, no `rollback` token in the diff |
| `status >= 400 and status < 500` on a payment response | 409/422/704 mapped to "declined" |
| `Optional[str] = None` on an `idempotency_key` parameter | enforcement deferred to prose |
| `SELECT ... WHERE idem_key` followed by `INSERT` | the check-then-insert race |
| a `RETENTION`/`TTL` constant with no asserted relation to the retry horizon | the second operation at the window edge |
| a persisted `client_order_id` / `idem_key` column with no reader | phase 5 missing |
