# Webhook processing

Applying a stored event exactly once, in the order the authority generated it, from state the authority
holds. Covers the two identities that do two different jobs, the ordering field that is not the signature
timestamp, the watermark schema that survives a second-granularity clock, the dead-letter path that keeps a
redelivery coming, and the re-read that is the whole handler.

## Contents

- Two identities: the event identity that dedupes delivery, and the object-plus-transition identity that the
  effect must be idempotent on: Stripe `(event.type, data.object.id)` alongside `event.id`; Adyen
  `(eventCode, pspReference)` and why `pspReference` alone collapses distinct modifications
- Ordering: `event.created` / `eventDate`; the signature timestamp is regenerated per delivery attempt
- The `(created, applied_event_ids)` watermark: schema, the conditional `UPDATE`, and the same-second case
- Dead-letter table, alerting, and replay from the inbox after a dependency arrives
- Reading the object from its authority: which call, where on the path, and why not the payload

---

## Dedupe keys per provider

Two identities, doing two different jobs. A provider redelivering a notification sends the **same** event
identity, so a unique index on that identity is correct and cheap, and it solves transport redelivery and
nothing else. Separately generated event objects carry **distinct** identities and can describe one underlying
object transition, and one object can be reported under more than one event type. So dedupe the transport on the
event identity, and make the effect idempotent on the object identity and the transition, which is the second
column below.

| provider | transport identity | identity of the effect (object and transition) | what the wrong key does |
|---|---|---|---|
| Stripe | `event.id`: unique index, necessary | `(event.type, data.object.id)` | Stripe: *"In some cases, two separate Event objects are generated and sent. To identify these duplicates, use the ID of the object in `data.object` along with the `event.type`."* Event-id dedupe alone lets the same fact apply twice |
| Adyen | the notification's own `pspReference` | `(eventCode, pspReference)` | Adyen: duplicates *"have the same values in the `eventCode` and `pspReference` fields"*. `pspReference` alone collapses `AUTHORISATION` with `CAPTURE`, and two refunds on one payment into one |
| PayPal | `id` on the webhook event | *not established by this repository's research*; do not invent one |

Two Adyen-specific facts that break naive joins: a **modification's** `pspReference` differs from the
payment's, which is carried separately as `originalReference`; and `merchantReference` is yours and Adyen does
not enforce uniqueness on it: a retried payment attempt for one order produces two `pspReference`s under one
`merchantReference`.

**`(event.type, object_id)` is a *collapsing* key, not a discard key.** Two `refund.updated` events on one
`re_…` are genuinely distinct facts (pending → succeeded). Collapsing them is safe only because the handler's
whole job is *re-read the object and reconcile to its current state* (PAY2); under that handler a duplicate
is a no-op by construction. If your handler applies a delta taken from the payload, neither key saves you:
`balance += event.data.object.amount` is wrong under both.

Never reuse a processor **idempotency key** as an inbox dedupe key. They run on different clocks: Stripe
prunes idempotency keys after ≥24 h, while a webhook retries for 3 days and can be manually resent for 15–30.

## Ordering

| provider | ordering field | type | never use |
|---|---|---|---|
| Stripe | `event.created` | unix **seconds** | the `t=` element of `Stripe-Signature` |
| Adyen | `eventDate` | ISO 8601 | the receipt time at your edge |

Stripe: *"Stripe doesn't guarantee the delivery of events in the order that they're generated."* Adyen: *"To
ensure you are processing events in the correct chronological order, always check the timestamp."* Both are
at-least-once, and neither is ordered.

The `Stripe-Signature` timestamp is **regenerated per delivery attempt**. A first-attempt-failed event
redelivered an hour later carries a *newer* `t=` than a fresh event generated in between; sorting by it
inverts causality on exactly the events that were retried, i.e. under load.

`received_at` is worse than either: it is your edge's clock ordering the provider's facts, and it is the LWW
data-loss pattern with an extra hop.

## The `(created, applied_event_ids)` watermark

`event.created` is second-granularity, and `refund.created` and `refund.updated` on the same `re_…` routinely
share a second. The plain monotonic guard `if event.created <= wm: return` is therefore not conservative;
it is a money bug.

```sql
CREATE TABLE object_watermarks (
  provider    text   NOT NULL,
  object_id   text   NOT NULL,          -- re_… pi_… ch_… / pspReference
  applied_at  bigint NOT NULL,          -- highest event.created applied to this object
  applied_ids text[] NOT NULL,          -- event ids applied AT applied_at (reset on advance)
  PRIMARY KEY (provider, object_id)
);
```

`applied_ids` is bounded by the number of events sharing one second on one object; it is reset, not
appended, whenever `applied_at` advances.

Admission, in the same transaction as the effect, with the guard **being** the write
(*arrival order is not occurrence order*):

```python
def admit(tx, provider, object_id, created, event_id) -> bool:
    n = tx.execute(sa.text("""
        INSERT INTO object_watermarks (provider, object_id, applied_at, applied_ids)
        VALUES (:p, :o, :c, ARRAY[:e]) ON CONFLICT DO NOTHING
    """), ...).rowcount
    if n == 1:
        return True                                     # first event ever for this object
    n = tx.execute(sa.text("""
        UPDATE object_watermarks SET applied_at = :c, applied_ids = ARRAY[:e]
         WHERE provider = :p AND object_id = :o AND applied_at < :c
    """), ...).rowcount
    if n == 1:
        return True                                     # strictly newer second
    n = tx.execute(sa.text("""
        UPDATE object_watermarks SET applied_ids = applied_ids || :e
         WHERE provider = :p AND object_id = :o AND applied_at = :c
           AND NOT (:e = ANY(applied_ids))
    """), ...).rowcount
    return n == 1                                       # same second, id not yet applied
```

`rowcount 0` from all three means *already applied, or strictly older*; drop it. Do not read that as "done"
without the three-branch structure: a bare `UPDATE … WHERE status='pending'` returning 0 rows conflates
"someone else did it" with "the predicate missed".

### Worked case: the refund that stays pending forever

One refund `re_1Px…` on charge `ch_9Qz…`, two Stripe events emitted in the same second:

| # | event id | type | `created` | object state on re-read |
|---|---|---|---|---|
| 1 | `evt_AAA` | `refund.created` | 1719000000 | `pending` |
| 2 | `evt_BBB` | `refund.updated` | 1719000000 | `succeeded` |
| 3 | `evt_AAA` | `refund.created`, redelivery of #1: same event id, new `t=` | 1719000000 | `succeeded` |

Delivered 1 → 2 → 3.

| step | bare `created <= applied_at` guard | `(applied_at, applied_ids)` guard |
|---|---|---|
| 1 | admit; wm ← 1719000000; re-read ⇒ `pending`; no ledger move | admit; wm ← (1719000000, {`evt_AAA`}); re-read ⇒ `pending` |
| 2 | `1719000000 <= 1719000000` ⇒ **drop**. The refund is `pending` in your books forever, and nothing ever fires again | `applied_at = created` and `evt_BBB` ∉ applied_ids ⇒ **admit**; re-read ⇒ `succeeded`; post the principal leg, leave the fee expensed |
| 3 | drop (already broken) | the inbox unique index rejects the duplicate `event_id` before the watermark is consulted; had it reached the watermark, `evt_AAA` ∈ applied_ids ⇒ drop |

Event-id dedupe cannot substitute for the watermark, and the watermark cannot substitute for event-id dedupe.
A *separately generated* `refund.created` for the same refund, whose `created` is older than the one already
applied, carries an `event.id` your `processed_events` table has never seen, passes the unique index and
re-arms the money branch; the watermark is what refuses it. Conversely a redelivery of an admitted event carries
the **same** `event.id`, and the unique index is what refuses that.

Legality is the second half of *arrival order is not occurrence order*: enumerate the legal `(state, event)` pairs with an explicit
`_ => reject` arm. `succeeded`, `failed` and `canceled` never regress to `pending` on a *status* message, but
a terminal state does accept the events by which the processor corrects a fact you already booked. Stripe
refunds are the concrete case: a `succeeded` refund can later report `failed` (return of funds, up to 30 days
from the post date), and `charge.dispute.funds_reinstated` arrives after you wrote the loss off. Those are
economic corrections and they must be admitted.

## Dead-letter, alert, replay

Insert into `processed_events`, or set `processed_at`, **only in the transaction that applied the effect**
(PAY4). An event you cannot resolve (unknown object, order row not yet created, a dependency in flight) is
dead-lettered and alerted and is **NOT** marked processed, so the provider's redelivery still reaches you.

```python
try:
    with db.begin() as tx:
        if not admit(tx, "stripe", object_id, created, event_id):
            mark_processed(tx, row_id); return
        obj = stripe.Refund.retrieve(object_id)      # the authority. Between signature check and first write.
        apply_effect(tx, obj)                        # ledger move derived from obj, never from raw_body
        mark_processed(tx, row_id)                   # same transaction as the effect
except UnresolvedDependency as e:
    with db.begin() as tx:                           # separate txn: the effect txn rolled back
        tx.execute(sa.text("""UPDATE webhook_inbox
                                 SET attempts = attempts + 1, last_error = :err,
                                     dead_lettered_at = CASE WHEN attempts + 1 >= 5
                                                             THEN now() ELSE NULL END
                               WHERE id = :id"""), dict(err=str(e), id=row_id))
    alerts.page(ALERT_DEST, "webhook unresolved", event_id=event_id)   # ALERT_DEST has no default
```

Replay is a re-run of the same processor over the stored row with `processed_at IS NULL`. It is safe for the
same reason duplicate delivery is safe: the handler re-reads the object and the watermark decides. Never
replay by re-parsing `raw_body` into a state change; `raw_body` exists so the signature stays checkable and
so an incident has the exact bytes, not so you can apply it.

Stripe's manual resend (Dashboard 15 days, CLI 30 days) is a second replay source; it lands on the same
endpoint and needs no special path.

## Reading the object from its authority

The re-read is not a style preference, it is the whole handler. Between the signature check and the first
write, the processor of the stored row calls the authority and uses **that** response for every value-moving
decision. The check on a diff is simply that such a read occurs on that path, whatever the call is spelled:

| processor | the authority read |
|---|---|
| Stripe | `stripe.Refund.retrieve(id)`, `stripe.PaymentIntent.retrieve(id)`, `stripe.Charge.retrieve(id)` |
| Adyen | the payment-details endpoint for the `pspReference` |
| any | the same read behind an internal port that wraps one of the above |

Make every ledger move, every fulfilment and every order attribution from that response, never from
`event.data.object`.

**Why the payload cannot stand in for it.** Stripe Event objects are **immutable** and rendered at the account's
API version *at event time*; they are never updated. With a 3-day retry horizon and a 15-to-30-day manual
resend, the payload you are holding can be arbitrarily stale and rendered against an API version you have since
migrated off. The payload is a snapshot taken when the event was queued, and the queue can be days deep.

**Attribution is the sharpest case.** `metadata` read off the payload attributes money to whatever the object
looked like when the event was queued: the order id, the connected account, the customer. If any of those were
corrected on the object afterwards, the payload pays the wrong party and every double-entry check still passes.
Read attribution from the re-read object, in the same unit of work as the effect.

Two events for the same object can be in flight at once, so the payload is not even a consistent view of one
instant. Adyen states the same about its synchronous response: *"The status of a payment can sometimes change
after you get the result code, so we recommend that you do not use the result code to update your order
management system."*
