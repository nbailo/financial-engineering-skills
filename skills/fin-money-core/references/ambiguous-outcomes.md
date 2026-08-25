# Ambiguous outcomes

You are about to write the `except`/`catch` arm of a value-moving call. Two mechanisms are routinely
collapsed into one; this is the first, the **classification** of what the counterparty told you: did the
effect happen? Per-signal tables, the shape that carries the answer to the decision point, and the loop that
resolves what is still unknown. The **identity** under which you may safely ask again is the other
mechanism, the idempotency key.

## Contents

- Two axes, not one
- Signal classification table
- Carrying the classification
- Concurrent duplicates
- Not-found is not proof of absence
- `resolve_unresolved_intents()`
- Grep list

## Two axes, not one

Every response to a value-moving call answers, or fails to answer, two independent questions.

| Axis | Question | Values | Who decides |
|---|---|---|---|
| **Occurrence** | Did the effect happen at the counterparty? | `DEFINITE-NO` · `DEFINITE-YES` · `UNKNOWN` | the counterparty's documentation, for that exact code |
| **Retry-safety** | Can retrying with *identical* request data produce a different outcome? | transient · non-transient | the state, not the network |

The second axis is TigerBeetle's, verbatim (`src/tigerbeetle.zig:318`):

```zig
/// Returns `true` if the error code depends on transient system status and retrying
/// the same transfer with identical request data can produce different outcomes.
pub fn transient(result: CreateTransferStatus) bool {
```

`exceeds_credits` / `exceeds_debits` (insufficient funds) are **transient**; a later credit makes the same
transfer succeed. Every `exists_with_different_*` is **non-transient**: a payload conflict is deterministic and
retrying is pointless. Most codebases get this backwards in both directions.

**The load-bearing constraint on the first axis:** classify a path `DEFINITE-NO` **only where the counterparty
documents, for that exact code, that the request was not enqueued.** Absent that document it is `UNKNOWN`;
this is the only branch that can duplicate money, and a misclassified `400` pays twice.

CCXT is the worked negative. Its manual states the ambiguity correctly (`ccxt/wiki/Manual.md:8816`: *"When a
`RequestTimeout` is raised, the user doesn't know the outcome of a request"*) and forty lines earlier says of
the parent class *"**OperationFailed** can be blindly re-tried and should success"*. `RequestTimeout extends
NetworkError extends OperationFailed` (`ts/src/base/errors.ts:219`, `:177`, `:171`), and the retry predicate
in the funnel every signed REST call passes through is `e instanceof OperationFailed`
(`ts/src/base/Exchange.ts:6435`) with **no HTTP-method or path discrimination**: a `POST` create-order retries
on the same terms as a `GET` ticker (`:6455`, `:629`). Default `retries = 0`, but
`exchange.options['maxRetriesOnFailure'] = 3` arms blind order re-submission exchange-wide (`:6796`, `:6814`).

## Signal classification table

Fill this in per counterparty from *their* docs. These rows are the ones vendor documentation establishes verbatim.

| Signal | Occurrence | Retry-safe with same key? | Establishing text |
|---|---|---|---|
| Clean `2xx` for my key | DEFINITE-YES | n/a: record the outcome | none |
| Stripe `200` + `Idempotent-Replayed: true` | DEFINITE-YES, already applied | do not re-apply downstream effects | Stripe idempotent-requests docs |
| Stripe `500` | **UNKNOWN**, and the 500 **is cached under the key** | retrying returns the same 500 forever | *"Treat requests that return `500` errors as indeterminate"*; Stripe's back-office may roll the charge *forward* to the network afterwards, surfacing the object only by webhook |
| Stripe `409 Conflict` | UNKNOWN: concurrent attempt in flight | back off and **read** | *"The request conflicts with another request (perhaps due to using the same idempotent key)"* |
| Stripe `429` | UNKNOWN | new key only after resolving by read | *rate limiters run before the idempotency layer*, so the 429 is not a replay. Stripe does **not** document that the endpoint was never reached; under the DEFINITE-NO rule it stays UNKNOWN |
| Stripe request-validation `400` | DEFINITE-NO | fix the payload, new intent, new key | *"We save results only after the execution of an endpoint begins. If incoming parameters fail validation … we don't save the idempotent result because no API endpoint initiates the execution."* |
| Stripe `idempotency_error` (409) | DEFINITE-NO for *this* body | never retry this body under this key | *"an `Idempotency-Key` is re-used on a request that does not match the first request's API endpoint and parameters"* |
| Stripe `424 External Dependency Failed` | UNKNOWN | reconcile | Stripe error-handling docs |
| Adyen `422` or `409` + errorCode **704** *"request already processed or in progress"* | UNKNOWN: concurrent | header `transient-error: true` means retry later **with the same key**; absent or `false` means **do not retry** | Adyen API-idempotency docs |
| Binance `-1007 TIMEOUT` | UNKNOWN | never resubmit; query | *"Timeout waiting for response from backend server. Send status unknown; execution status unknown."* |
| Binance `-1006 UNEXPECTED_RESP` | UNKNOWN | query | carries *"Execution status unknown."* |
| Binance HTTP `5XX` (futures `503`) | UNKNOWN | query | *"It is important to **NOT** treat this as a failure operation; the execution status is **UNKNOWN** and could have been a success."* |
| Binance `429` / `418` on an order endpoint | UNKNOWN | query | no venue documents a 429 as proof of non-creation |
| Binance `-2013 NO_SUCH_ORDER` right after placement | UNKNOWN | re-query with backoff | three data sources (Matching Engine / Memory / Database) with asynchronous propagation |
| Binance `-1013`, `-2010` with a filter message, `-4164` | DEFINITE-NO | fix and re-intent | documented matching-engine business rejections |
| Binance `-2011 CANCEL_REJECTED` | the *order* is no longer open; re-read its terminal state | do not retry the cancel | expected in normal operation |
| Binance `409` + `-2021` (cancelReplace) | **split**: one leg succeeded | determine which leg before anything else | `-2022` = both failed ⇒ DEFINITE-NO |
| Socket timeout / connection reset with no response | UNKNOWN | same key only | stripe-node `src/RequestSender.ts:400-403`: *"those codes can surface after the API processed the request, so the retry needs a key to dedupe against"* |
| TigerBeetle `exists` | DEFINITE-YES | *"many applications should handle `exists` exactly as `created`"* | `create_transfers` reference |
| TigerBeetle `exists_with_different_amount` (and 10 siblings) | DEFINITE-NO for this body | non-transient | `src/tigerbeetle.zig:236-247` |
| TigerBeetle `id_already_failed` | DEFINITE-NO, **permanently** | *"the application … must generate a new idempotency id"* | `src/state_machine.zig:3736` |
| TigerBeetle `exceeds_credits` / `exceeds_debits` | DEFINITE-NO now | **transient**: same id may succeed later | `tigerbeetle.zig:318` |

**Do not branch on the status code for "key seen, body differs."** It is `422` (IETF §2.7) · `409` (Stripe,
Brandur) · `400` + `Repeatability-Result: rejected` (OASIS) · `IdempotentParameterMismatch` (AWS) · per-field
`exists_with_different_*` (TigerBeetle) · "an error" (Square) · **undocumented** (Adyen, PayPal). Branch on
*"not a clean 2xx for my key" ⇒ UNKNOWN ⇒ reconcile.*

## Carrying the classification

A single `except Exception:` around a value-moving call destroys the classification, and that is the defect:
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
which writes `new_state=OrderState.FAILED`, the same terminal state used for a client-side `min_order_size`
rejection (where FAILED is true) and for a socket timeout (where it is a guess); the strategy receives
`MarketOrderFailureEvent` and typically re-places. The *cancel* path in the same file is correct
(`:539-546`): `except asyncio.TimeoutError:` logs and changes no state.

## Concurrent duplicates

`409` (and Adyen's `422`/`409` with errorCode `704`) means *another attempt of this same operation is in
flight*. It is `UNKNOWN`, not `FAILED`: the operation may be executing at this instant. The defect is a generic
status-class handler.

```javascript
if (res.status >= 400 && res.status < 500) { markPaymentDeclined(order); }   // WRONG
```

The first request succeeds, the order is marked declined, the customer is charged and not fulfilled. The
correct arm is **back off and READ** (re-query under the same key, or query the provider by your own reference)
and only then decide. Same for Stripe's cached `500`: retrying returns `500` forever while the money may
already have moved, and Stripe's back-office may roll the charge *forward* to the network afterwards,
surfacing the object **only via webhook**, so subscribe to the webhook types for objects you never see in an
API response.

Both major processors ship an in-band directive for this: Adyen's `transient-error: true` header means the
request *can* be retried later under the same key (absent or `false` ⇒ **do not retry**), and Stripe's
`Stripe-Should-Retry: true|false` is honoured by both SDKs (`stripe/_http_client.py:143-149`,
`src/RequestSender.ts:334-341`) as an override of the client's own policy: *"The API may ask us not to retry
(eg; if doing so would be a no-op) or advise us to retry (eg; in cases of lock timeouts); we defer to that."*

Answering a concurrent duplicate with a conflict versus blocking until the first completes is a genuine design
choice: no source establishes one as correct; 409 pushes retry responsibility to the client, blocking costs
liveness. **Synthesising a success is not on the list.**

## Not-found is not proof of absence

A query that returns nothing immediately after an ambiguous submit is a fourth answer, not a `DEFINITE-NO`.
Binance documents three data sources with different staleness and asynchronous propagation; `-2013` can mean
"not yet visible in the replica you queried" or "archived after 90 days" (`-2026`).

hummingbot is a rare implementation that treats this correctly
(`hummingbot/connector/client_order_tracker.py`), in three parts: **a single not-found is not evidence**:
`_lost_order_count_limit` defaults to 3 (`:45`), so it takes four consecutive not-founds to declare an order
lost (`:221-250`); **a "lost" order remains fillable**: `all_fillable_orders` is
`{**self.active_orders, **self.lost_orders}` (`:107-109`), so a fill arriving after the declaration is still
applied to it; and **lost status survives restart**: `restore_tracking_states` (`:152-164`) re-files a
persisted failure into `_lost_orders` rather than dropping it, and the order leaves the set only when the
venue speaks authoritatively about it (`:295-301`).

## `resolve_unresolved_intents()`

Phase 2 of the idempotency-key lifecycle exists to feed this loop; if nothing reads the row
back, the row is decoration.

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

- **It runs before the system takes new action.** nautilus_trader gates the trader on it:
  `crates/live/src/node/mod.rs:440-447` calls `perform_startup_reconciliation()`, and on failure
  `abort_startup(...)` then `Err`; `start_trader()` (`:454`) is never reached.
- **It queries by the identity persisted in phase 2**, not by a balance diff. CCXT's documented
  timeout-recovery procedure falls back to `fetchBalance()` and infers from a balance change: race-prone
  against fees, funding, and any other strategy on the same account.
- **It terminates in a break, not a guess.** Bound the attempts; the terminal state is "unknown: needs
  reconciliation", with that instrument's or tenant's path closed. Jepsen flagged retry-until-reply as an
  unresolved hazard in TigerBeetle because it converts definite errors into indefinite ones.
- **Any identifier it must invent is deterministic over counterparty-supplied fields including a counterparty
  timestamp**, so the same inference after a restart dedupes against itself; nautilus,
  `crates/execution/src/reconciliation/ids.rs:103-107`: *"the same logical event replayed after restart still
  hashes the same (venue re-reports identical ts)."*

Worked negative: freqtrade writes nothing before `create_order` (`freqtradebot.py:963`), so there is no
`INFLIGHT` row to enumerate and a lost submit is unrecoverable by construction. Worked positive: hummingbot's
`start_tracking_order` runs before the POST with `exchange_order_id=None`, serialisable via `to_json`
(`in_flight_order.py:273`) before anything hits the wire.

## Grep list

| Pattern | What it finds |
|---|---|
| `except Exception` / `catch (e)` within 5 lines of a value-moving call | the classification flattened at the point it was created |
| `status >= 400 and status < 500` on a payment response | 409/422/704 mapped to "declined" |
| a persisted `client_order_id` / `idem_key` column with no reader | phase 5 missing |
