# A Stripe refund endpoint and its webhook

A Flask endpoint that refunds part or all of an order through Stripe, and the webhook handler that moves
the money in the local database when the refund settles. Every payments integration on earth has this pair
of functions, and they are usually written by whoever picked up the "add refunds" ticket. The version below
is realistic code written under time pressure, and its failure is specific: the webhook *plumbing* is
perfect and the webhook *semantics* are absent.

---

## Before

```python
# app/refunds.py
import os

import stripe
from flask import Blueprint, jsonify, request
from sqlalchemy import func

from .db import db
from .models import Order, OrderRefund, ProcessedStripeEvent

bp = Blueprint("refunds", __name__)
stripe.api_key = os.environ["STRIPE_SECRET_KEY"]
WEBHOOK_SECRET = os.environ["STRIPE_WEBHOOK_SECRET"]


@bp.post("/orders/<int:order_id>/refunds")
def create_refund(order_id):
    """Refund `amount_cents` against an order.

    We reserve a local OrderRefund row first, so we always have a record of the attempt
    even if the Stripe call times out, and we derive the idempotency key from that row's
    id so a client retry cannot double-refund.
    """
    body = request.get_json(force=True)
    amount_cents = int(body["amount_cents"])
    if amount_cents <= 0:
        return jsonify(error="amount must be positive"), 400

    order = (
        db.session.query(Order).filter_by(id=order_id).with_for_update().one_or_none()
    )
    if order is None:
        return jsonify(error="unknown order"), 404

    already = (
        db.session.query(func.coalesce(func.sum(OrderRefund.amount_cents), 0))
        .filter(
            OrderRefund.order_id == order_id,
            OrderRefund.status == "succeeded",
        )
        .scalar()
    )
    if already + amount_cents > order.amount_cents:
        return jsonify(error="refund exceeds order total"), 400

    refund = OrderRefund(order_id=order_id, amount_cents=amount_cents, status="pending")
    db.session.add(refund)
    db.session.flush()  # so refund.id exists before we build the key
    key = f"order-{order_id}-refund-{refund.id}"

    try:
        sr = stripe.Refund.create(
            payment_intent=order.payment_intent_id,
            amount=amount_cents,
            idempotency_key=key,
            metadata={"order_id": str(order_id)},
        )
    except stripe.error.StripeError as e:
        db.session.rollback()
        return jsonify(error=str(e)), 502

    refund.stripe_refund_id = sr.id
    refund.status = sr.status
    db.session.commit()
    return jsonify(refund_id=sr.id, status=sr.status), 201


@bp.post("/webhooks/stripe")
def stripe_webhook():
    """Stripe calls this on every refund state change.

    The handler is idempotent: we insert the event id into processed_stripe_events and
    bail out if we have seen it, and the monotonic `created` guard drops anything that
    arrives out of order, so duplicate and reordered deliveries are both harmless.
    """
    try:
        event = stripe.Webhook.construct_event(
            request.data, request.headers.get("Stripe-Signature"), WEBHOOK_SECRET
        )
    except (ValueError, stripe.error.SignatureVerificationError):
        return "", 400

    if db.session.get(ProcessedStripeEvent, event["id"]) is not None:
        return "", 200

    obj = event["data"]["object"]

    if event["type"] in ("refund.created", "refund.updated", "charge.refund.updated"):
        refund = (
            db.session.query(OrderRefund)
            .filter_by(stripe_refund_id=obj["id"])
            .one_or_none()
        )
        if refund is None:
            db.session.add(ProcessedStripeEvent(id=event["id"]))
            db.session.commit()
            return "", 200

        if refund.last_event_created is not None and refund.last_event_created >= obj["created"]:
            db.session.add(ProcessedStripeEvent(id=event["id"]))
            db.session.commit()
            return "", 200

        refund.status = obj["status"]
        refund.last_event_created = obj["created"]

        if obj["status"] == "succeeded":
            order = db.session.get(Order, int(obj["metadata"]["order_id"]))
            order.refunded_cents += obj["amount"]

    db.session.add(ProcessedStripeEvent(id=event["id"]))
    db.session.commit()
    return "", 200
```

```python
# app/models.py  (the relevant columns)
class OrderRefund(db.Model):
    __tablename__ = "order_refunds"
    id = db.Column(db.BigInteger, primary_key=True)          # BIGSERIAL
    order_id = db.Column(db.BigInteger, db.ForeignKey("orders.id"), nullable=False)
    stripe_refund_id = db.Column(db.Text, unique=True)
    amount_cents = db.Column(db.BigInteger, nullable=False)
    status = db.Column(db.Text, nullable=False)
    last_event_created = db.Column(db.BigInteger)
```

---

## What the suite catches

| Defect | Rule | What actually happens | Loss shape |
|---|---|---|---|
| `db.session.flush()` then `db.session.rollback()` on `StripeError` | `fin-money-core`: *operation identity*, *ambiguous outcomes* | The docstring says the row exists so the attempt survives a timeout. It does not: `flush()` inside an open transaction is not persistence, and the `rollback()` fires on exactly the ambiguous timeout the row was written for. Postgres **does not roll back BIGSERIAL**. The client retries, gets `refund.id = N+1`, derives `order-7-refund-N+1`, and Stripe sees a brand-new request. | **A second real refund of the full amount.** Duplicate principal, silent, and the local table shows one refund because the first row was rolled away. |
| `except stripe.error.StripeError` as one branch | `fin-money-core`: *ambiguous outcomes* | A `CardError`, an `InvalidRequestError` and a `requests.Timeout` are flattened into one 502. The classification that decides whether the money moved is destroyed at the point it is created. | Prerequisite for the above: without the classification there is no correct branch to take. |
| `refund.status = obj["status"]`; `order.refunded_cents += obj["amount"]` | `fin-payments`: *A pushed event names an object; only the authority holds that object's state*. `fin-money-core`: *authority* | Every ledger move is made from a snapshot of the object as it looked when the event was *queued*. `stripe.Refund.retrieve(...)` appears **nowhere** in the handler. Order attribution comes from payload `metadata`, which is equally stale. | Money booked against the wrong amount, wrong status, or wrong order. |
| No dispute or chargeback check before `Refund.create` | `fin-payments`: *The refund ceiling is the processor's number, net of everything in flight* | The strings "dispute" and "chargeback" appear **nowhere** in the endpoint. Stripe: *"You can't issue a refund outside the dispute process while the dispute is open."* On bank-debit rails the failure is worse: Stripe warns of *"a risk of double refund … the customer might receive two credits for the same transaction."* | **The customer is paid twice, plus the dispute fee.** The highest single-event loss in this example. |
| The ceiling is `order.amount_cents`, counting only `succeeded` | `fin-payments`: *The refund ceiling is the processor's number, net of everything in flight* | The order total is not the captured amount: a partial capture, an incremental authorization or an application fee all break the equality. Counting only `succeeded` refunds means in-flight money reserves nothing, so two concurrent partial refunds both pass the check. | Over-refund up to the whole order, repeatable. |
| `refund.last_event_created >= obj["created"]` | `fin-payments`: *Ordering is per object, on the authority's own sequence, and the guard is the write*. `fin-money-core`: *concurrency on authoritative state* | Stripe's `created` is **second-granularity**, and `refund.created` and `refund.updated` on the same `re_…` routinely share a second. A bare `>=` discards the `succeeded` event and the refund stays `pending` forever. The guard is also a TOCTOU: two concurrent redeliveries both read, both pass, both write. Event-id dedupe cannot substitute: a late `refund.created` carries a fresh `event.id` the table has never seen and re-arms the money branch. | Refund never books, or books twice. Both silent until the settlement report. |
| Unresolvable event still inserted into `processed_stripe_events` | `fin-payments`: *The event identity dedupes the delivery; the object and the transition dedupe the effect* | The signature verification and the unique index are both correct, and the unresolvable event is committed to the dedupe table anyway. Stripe then stops redelivering and the miss is permanent. The dedupe mechanism works **against** recovery. | Total, permanent loss of that event. No error, no retry, no log line anyone reads. |

---

## After

```python
# app/refunds.py
import json
import os
import uuid

import stripe
from flask import Blueprint, jsonify, request
from sqlalchemy import text

from .db import db
from .models import DeadLetter, Order, ProcessedStripeEvent

bp = Blueprint("refunds", __name__)
stripe.api_key = os.environ["STRIPE_SECRET_KEY"]
WEBHOOK_SECRET = os.environ["STRIPE_WEBHOOK_SECRET"]

# Reconciliation: the alert destination is a config key with no default, so import
# fails rather than alerting into a void.
ALERT_SINK = os.environ["PAYMENTS_ALERT_SINK"]

# A dispute is closed only in these three states. Anything else, including a status
# this code has never seen, is open, and open means no refund. Fail closed.
CLOSED_DISPUTE = {"won", "lost", "warning_closed"}

# Ordering is per object, on the authority's own sequence, and the guard is the write.
# These are the enumerated legal (state, event) pairs. `succeeded → failed` is legal,
# because the bank can return the money up to 30 days after the post date.
# `succeeded → pending` is not: a terminal state is never re-opened by a status message.
LEGAL_REFUND_TRANSITIONS = {
    ("pending", "pending"), ("pending", "requires_action"), ("pending", "succeeded"),
    ("pending", "failed"), ("pending", "canceled"),
    ("requires_action", "requires_action"), ("requires_action", "pending"),
    ("requires_action", "succeeded"), ("requires_action", "failed"),
    ("requires_action", "canceled"),
    ("succeeded", "succeeded"), ("succeeded", "failed"),
    ("failed", "failed"), ("canceled", "canceled"),
}


class IllegalTransition(Exception):
    pass


# ------------------------------------------------------------------- the refund call


@bp.post("/orders/<int:order_id>/refunds")
def create_refund(order_id):
    client_key = request.headers.get("Idempotency-Key")
    if not client_key:
        return jsonify(error="Idempotency-Key header is required"), 400

    body = request.get_json(force=True)
    amount_cents = int(body["amount_cents"])
    currency = body["currency"].lower()  # never an amount without its currency
    if amount_cents <= 0:
        return jsonify(error="amount must be positive"), 400

    order = db.session.get(Order, order_id)
    if order is None:
        return jsonify(error="unknown order"), 404

    # ---- outside any transaction: the API is current state, not the payload -------
    intent = stripe.PaymentIntent.retrieve(
        order.payment_intent_id, expand=["latest_charge.dispute"]
    )
    charge = intent.latest_charge
    if charge is None or charge.captured is False:
        # Uncaptured funds are cancelled, never refunded.
        return jsonify(error="not captured; cancel the PaymentIntent instead"), 409
    if charge.currency != currency:
        return jsonify(error=f"currency mismatch: charge is {charge.currency}"), 400

    dispute = charge.dispute
    if dispute is not None and dispute.status not in CLOSED_DISPUTE:
        return jsonify(error=f"dispute {dispute.id} is open ({dispute.status})"), 409

    # The ceiling is the captured amount minus everything in flight, computed here from
    # the authority's own numbers.
    pending = db.session.execute(
        text("SELECT COALESCE(SUM(amount_cents),0) FROM refund_attempts"
             " WHERE charge_id = :c AND status IN ('pending','requires_action')"),
        {"c": charge.id},
    ).scalar_one()
    refundable = charge.amount_captured - charge.amount_refunded - pending
    if amount_cents > refundable:
        return jsonify(error=f"exceeds refundable {refundable}"), 400

    # ---- phase 1: COMMIT the intent, with the key and the exact bytes -------------
    attempt = db.session.execute(
        text("""
        INSERT INTO refund_attempts
            (client_key, charge_id, order_id, amount_cents, currency,
             idempotency_key, request_body, status)
        VALUES (:ck, :charge, :order, :amt, :cur, :key, :body, 'inflight')
        ON CONFLICT (client_key) DO NOTHING
        RETURNING id, idempotency_key, request_body, status
        """),
        {
            "ck": client_key, "charge": charge.id, "order": order_id,
            "amt": amount_cents, "cur": currency,
            # Operation identity: minted from the intent instance, not from a row id,
            # not from a body hash, and from a value that survives ROLLBACK.
            "key": f"rf_{uuid.uuid4().hex}",
            "body": json.dumps(
                {"charge": charge.id, "amount": amount_cents, "currency": currency},
                sort_keys=True,
            ),
        },
    ).one_or_none()
    db.session.commit()  # <- the intent is on disk before the first byte goes out

    if attempt is None:  # the retry resolves to the winner's row, never a raw 23505
        attempt = db.session.execute(
            text("SELECT id, idempotency_key, request_body, status, stripe_refund_id"
                 " FROM refund_attempts WHERE client_key = :ck"),
            {"ck": client_key},
        ).one()
        stored = json.loads(attempt.request_body)
        if stored != {"charge": charge.id, "amount": amount_cents, "currency": currency}:
            return jsonify(error="idempotency key reused with a different body"), 422
        if attempt.status != "inflight":
            return jsonify(refund_id=attempt.stripe_refund_id, status=attempt.status), 200

    # ---- phase 2: the call. No transaction lexically encloses it. -----------------
    try:
        sr = stripe.Refund.create(
            charge=charge.id,
            amount=amount_cents,
            idempotency_key=attempt.idempotency_key,
            metadata={"order_id": str(order_id), "attempt": str(attempt.id)},
        )
    except stripe.error.InvalidRequestError as e:
        _finish(attempt.id, "rejected", None)          # DEFINITE-NO: documented reject
        return jsonify(error=str(e)), 400
    except (stripe.error.APIConnectionError, stripe.error.APIError,
            stripe.error.RateLimitError, stripe.error.IdempotencyError):
        # Not a clean 2xx for my key ⇒ UNKNOWN ⇒ resolve, never resubmit.
        alert(f"refund attempt {attempt.id} UNKNOWN; resolving by key")
        return jsonify(status="unknown", attempt_id=attempt.id), 202

    # ---- phase 3: record the outcome ---------------------------------------------
    _finish(attempt.id, sr.status, sr.id)
    return jsonify(refund_id=sr.id, status=sr.status), 201


def _finish(attempt_id, status, refund_id):
    db.session.execute(
        text("UPDATE refund_attempts SET status = :s, stripe_refund_id = :r"
             " WHERE id = :i"),
        {"s": status, "r": refund_id, "i": attempt_id},
    )
    db.session.commit()


def resolve_inflight_attempts():
    """The persisted idempotency key is READ by a recovery path.

    Runs on boot and on a schedule. Stripe's key retention is at least 24 hours; past
    that this must resolve from the settlement report instead, because the identity you
    send the counterparty expires and is scoped.
    """
    rows = db.session.execute(
        text("SELECT id, charge_id, idempotency_key FROM refund_attempts"
             " WHERE status = 'inflight' AND created_at > now() - interval '20 hours'")
    ).all()
    for row in rows:
        found = stripe.Refund.list(charge=row.charge_id, limit=100)
        for sr in found.auto_paging_iter():
            if sr.metadata.get("attempt") == str(row.id):
                _finish(row.id, sr.status, sr.id)
                break
        else:
            alert(f"refund attempt {row.id} still unresolved; escalating")


# ----------------------------------------------------------------------- the webhook


@bp.post("/webhooks/stripe")
def stripe_webhook():
    try:
        event = stripe.Webhook.construct_event(
            request.data, request.headers.get("Stripe-Signature"), WEBHOOK_SECRET
        )
    except (ValueError, stripe.error.SignatureVerificationError):
        return "", 400

    if db.session.get(ProcessedStripeEvent, event["id"]) is not None:
        return "", 200
    if not event["type"].startswith(("refund.", "charge.refund.")):
        return "", 200

    refund_id = event["data"]["object"]["id"]

    # The webhook is a trigger; the API is current state. Outside any transaction.
    fresh = stripe.Refund.retrieve(refund_id, expand=["charge"])

    attempt = db.session.execute(
        text("SELECT id, status FROM refund_attempts WHERE stripe_refund_id = :r"),
        {"r": refund_id},
    ).one_or_none()
    if attempt is None:
        # Unresolvable is NOT processed. Stripe must keep redelivering.
        db.session.add(DeadLetter(event_id=event["id"], payload=request.data.decode()))
        db.session.commit()
        alert(f"webhook {event['id']} names unknown refund {refund_id}")
        return "", 200

    # The guard IS the write, and it handles the same-second case. `created` is
    # second-granularity, so the watermark is (created, applied_event_ids).
    admitted = db.session.execute(
        text("""
        INSERT INTO refund_watermarks (refund_id, created, applied_event_ids)
        VALUES (:rid, :created, ARRAY[:eid]::text[])
        ON CONFLICT (refund_id) DO UPDATE
           SET created = EXCLUDED.created,
               applied_event_ids = CASE
                 WHEN refund_watermarks.created < EXCLUDED.created
                   THEN EXCLUDED.applied_event_ids
                 ELSE refund_watermarks.applied_event_ids || EXCLUDED.applied_event_ids
               END
         WHERE refund_watermarks.created < EXCLUDED.created
            OR (refund_watermarks.created = EXCLUDED.created
                AND NOT refund_watermarks.applied_event_ids @> ARRAY[:eid]::text[])
        RETURNING refund_id
        """),
        {"rid": refund_id, "created": event["created"], "eid": event["id"]},
    ).rowcount

    if admitted != 1:
        db.session.add(ProcessedStripeEvent(id=event["id"]))
        db.session.commit()  # genuinely superseded; nothing to apply
        return "", 200

    # Admission serialised us against every concurrent redelivery, so the status we
    # compare against is read here, inside the same transaction, not before it.
    current = db.session.execute(
        text("SELECT status FROM refund_attempts WHERE id = :i FOR UPDATE"),
        {"i": attempt.id},
    ).scalar_one()
    if (current, fresh.status) not in LEGAL_REFUND_TRANSITIONS:
        db.session.rollback()
        db.session.add(DeadLetter(event_id=event["id"], payload=request.data.decode()))
        db.session.commit()
        alert(f"illegal transition {current} -> {fresh.status} on {refund_id}")
        raise IllegalTransition(f"{current} -> {fresh.status}")

    # Every value below comes from `fresh`, never from the payload.
    db.session.execute(
        text("UPDATE refund_attempts SET status = :s, amount_cents = :a, currency = :c"
             " WHERE id = :i"),
        {"s": fresh.status, "a": fresh.amount, "c": fresh.currency, "i": attempt.id},
    )
    if fresh.status == "succeeded":
        post_refund_to_ledger(attempt.id, fresh)   # balanced group; see examples/ledger
    elif fresh.status == "failed":
        post_refund_failure_to_ledger(attempt.id, fresh)

    # Processed is written in the same transaction as the effect, and only here.
    db.session.add(ProcessedStripeEvent(id=event["id"]))
    db.session.commit()
    return "", 200
```

```sql
-- migrations/00N_refund_attempts.sql
CREATE TABLE refund_attempts (
    id               bigserial PRIMARY KEY,
    client_key       text        NOT NULL UNIQUE,   -- the caller's Idempotency-Key
    idempotency_key  text        NOT NULL UNIQUE,   -- ours, sent to Stripe, uuid4
    charge_id        text        NOT NULL,
    order_id         bigint      NOT NULL REFERENCES orders(id),
    amount_cents     bigint      NOT NULL CHECK (amount_cents > 0),
    currency         text        NOT NULL,
    request_body     jsonb       NOT NULL,          -- replayed verbatim on retry
    status           text        NOT NULL,
    stripe_refund_id text        UNIQUE,
    created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON refund_attempts (charge_id) WHERE status IN ('pending','requires_action');

CREATE TABLE refund_watermarks (
    refund_id         text PRIMARY KEY,
    created           bigint NOT NULL,   -- Stripe `created`, SECOND granularity
    applied_event_ids text[] NOT NULL
);
```

---

## What changed, and what did not

**Changed.** The idempotency key is a `uuid4` minted at intent formation and committed before the call,
so it is identical on every retry and survives a rollback that a BIGSERIAL does not. The exception handler
splits into DEFINITE-NO and UNKNOWN, and UNKNOWN returns `202` and resolves by querying instead of
retrying. The refund ceiling is computed from `amount_captured` with in-flight refunds counted, and an
open dispute refuses the refund outright. The webhook re-fetches the refund and reads every field from
that response. The version guard became a conditional `INSERT … ON CONFLICT … WHERE` whose rowcount is
the decision, with the same-second case handled by an applied-event-id set. Unresolvable events go to a
dead-letter table and are *not* marked processed.

**Not changed, deliberately.** The signature verification is byte-for-byte the original. It was already
correct and was left alone. The event-id dedupe table stayed; it is still the right first gate, it just
cannot substitute for a per-object watermark. Amounts remained integer minor units, because they already
were. The endpoint is still one Flask route with a synchronous Stripe call; nothing was moved to a queue.
The `with_for_update()` on the order row was **removed**, not tightened. The ceiling is now computed from
Stripe's own captured figure and enforced by a unique constraint on `client_key`, so the row lock was
protecting nothing that still needed protecting.

**Not changed, and named as out of scope.** `post_refund_to_ledger` is a call, not an implementation. The
balanced group it must write is *Reversal fee treatment is a term of the contract, confirmed against
settlement* in `fin-payments`, and it lives in `examples/ledger`. Whether the original processing cost
comes back, whether the reversal carries a fee of its own, and how a split reverses are properties of the
provider, the rail and the merchant agreement, so the group is not written as the mirror image of the
charge group and it is not written from an assumption in either direction. It is taken from the contract
and confirmed against the settlement lines for that reversal, which is the only place the gap shows.
Marketplace transfer reversal, the other half of that same rule, is absent because this charge is
single-party; if a `transfer_data` ever appears on the intent, the reversal has to land in the same unit of
work as the refund.

---

## The output the review ends with

A customer is on the other side of the money: the refund credits someone else's card, the crediting path is
driven by a webhook this service does not control, and the loss lands on the merchant and the cardholder
rather than on the account that wrote the code. Stripe holds the record of the refund itself, so for that
quantity authority is EXTERNAL and comparison against its settlement report is the primary proof. The
in-flight reserve is different: an attempt that has been committed but not yet answered exists only here,
and it is what the refund ceiling subtracts. No external party can confirm it, so that quantity is SELF and
its evidence is replay and concurrency rather than comparison.

`fin-payments` asks for its fuller control table when exposure is `record`, when a payout, withdrawal or
crediting path changes, when two or more processors or rails are in scope, or when the change touches three
of its invariants at once. This change touches six, so the test names below are the ones that table would
have carried.

`EVIDENCE` names functions rather than lines, because the code under review is a listing in this file. In a
real response every one of them is a `file:line`.

```
authority: MIXED · exposure: customer
  refund, dispute and settlement state  EXTERNAL (Stripe)
  in-flight refund reserve              SELF

FINDING   A refund whose Stripe call times out is refunded a second time in full, and the local table
          shows one refund because the first row was rolled away. Duplicate principal, silent.
WHY       `db.session.flush()` inside an open transaction is not persistence, and `db.session.rollback()`
          fires on exactly the ambiguous timeout the row was written for. Postgres does not roll back
          BIGSERIAL, so the client retry gets `refund.id = N+1`, derives `order-7-refund-N+1`, and Stripe
          sees a brand-new request.
EVIDENCE  app/refunds.py create_refund(), the flush that mints the key and the rollback in the except
          clause; app/models.py OrderRefund.id, BIGSERIAL
FIX       Mint `rf_<uuid4>` from the intent instance, from a value that survives ROLLBACK, and commit it
          with the exact request body before the call: app/refunds.py create_refund(), with no transaction
          lexically enclosing the Stripe call and the replay resolved against the stored request_body.
TEST      A retry after a timeout sends the same idempotency key and produces one refund at Stripe; a
          reused key carrying a different body is rejected rather than executed.

FINDING   The branch that decides whether the money moved cannot be written, because the information it
          needs is destroyed at the point it arrives.
WHY       `except stripe.error.StripeError` flattens a `CardError`, an `InvalidRequestError` and a
          `requests.Timeout` into one 502. DEFINITE-NO and UNKNOWN become the same value.
EVIDENCE  app/refunds.py create_refund(), the single except clause
FIX       Split the handler: `InvalidRequestError` is the documented rejection and finishes the attempt as
          `rejected`; `APIConnectionError`, `APIError`, `RateLimitError` and `IdempotencyError` are
          UNKNOWN, return 202, and are resolved by app/refunds.py resolve_inflight_attempts() reading the
          charge's refunds by `metadata.attempt`.
TEST      Each modelled Stripe failure lands in exactly one class, and no UNKNOWN path resubmits.

FINDING   Money is booked against the wrong amount, the wrong status or the wrong order, from a snapshot
          of the object as it looked when the event was queued.
WHY       `refund.status = obj["status"]` and `order.refunded_cents += obj["amount"]` read the payload.
          `stripe.Refund.retrieve(...)` appears nowhere in the handler, and order attribution comes from
          payload `metadata`, which is equally stale. The queue can be days deep.
EVIDENCE  app/refunds.py stripe_webhook(), the assignments from `obj`
FIX       Re-read the named object from Stripe outside any transaction, then decide, then write; every
          value written comes from `fresh`: app/refunds.py stripe_webhook().
TEST      test_webhook_reads_from_api_not_payload.

FINDING   The cardholder is paid twice and the merchant pays the dispute fee on top. The highest
          single-event loss in this example.
WHY       The strings "dispute" and "chargeback" appear nowhere in the endpoint. Stripe: *"You can't issue
          a refund outside the dispute process while the dispute is open."* On bank-debit rails Stripe
          warns of *"a risk of double refund … the customer might receive two credits for the same
          transaction."*
EVIDENCE  app/refunds.py create_refund(), the path from the ceiling check straight to Refund.create
FIX       Expand `latest_charge.dispute` on the retrieve and refuse while the dispute status is not one of
          the three closed states: app/refunds.py CLOSED_DISPUTE, applied in create_refund(). An unknown
          status counts as open, so the check fails closed.
TEST      An open dispute on the charge refuses the refund; a status the code has never seen refuses it
          too.

FINDING   An order can be over-refunded up to its whole total, repeatably.
WHY       The ceiling is `order.amount_cents`, which is not the captured amount once a partial capture, an
          incremental authorization or an application fee exists, and only `succeeded` refunds are
          subtracted. In-flight money reserves nothing, so two concurrent partial refunds both pass a
          check neither would pass alone.
EVIDENCE  app/refunds.py create_refund(), the `already + amount_cents > order.amount_cents` comparison
FIX       Compute the ceiling from Stripe's own numbers, `amount_captured - amount_refunded`, minus the
          local pending and requires_action attempts on that charge: app/refunds.py create_refund(), with
          the partial index on refund_attempts (charge_id) supporting it.
TEST      test_ceiling_counts_inflight_refunds.

FINDING   A refund either never books and sits `pending` forever, or books twice. Both silent until the
          settlement report.
WHY       Stripe's `created` is second-granularity, and `refund.created` and `refund.updated` on the same
          `re_…` routinely share a second, so a bare `>=` discards the `succeeded` event. The guard is
          also a TOCTOU: two concurrent redeliveries both read, both pass, both write. Event-id dedupe
          cannot substitute, because a late `refund.created` carries a fresh `event.id` the table has
          never seen and re-arms the money branch.
EVIDENCE  app/refunds.py stripe_webhook(), the `refund.last_event_created >= obj["created"]` comparison
FIX       Make the guard the write: a conditional INSERT ... ON CONFLICT ... WHERE on refund_watermarks
          whose rowcount is the decision, with the watermark carrying `(created, applied_event_ids)` so an
          unseen identity at the same second is still admitted: app/refunds.py stripe_webhook() and
          migrations refund_watermarks.
TEST      test_same_second_created_both_apply.

FINDING   An event that cannot be resolved is lost permanently, with no error, no retry and no log line
          anyone reads.
WHY       The unresolvable event is committed to `processed_stripe_events` anyway, so Stripe stops
          redelivering. The dedupe mechanism works against recovery: the miss is a hole in local state,
          not a processed event.
EVIDENCE  app/refunds.py stripe_webhook(), the `refund is None` branch that inserts ProcessedStripeEvent
FIX       Dead-letter it, alert, and leave it undeduped so redelivery continues: app/refunds.py
          stripe_webhook(), the DeadLetter insert. `processed_stripe_events` is written only in the same
          transaction as the effect.
TEST      test_unknown_refund_is_not_marked_processed.

UNRESOLVED: scheduled settlement-report reconciliation (no balance-transaction importer exists yet; the
alert destination is present at app/refunds.py ALERT_SINK and nothing reads the report)
UNRESOLVED: the balanced refund group is called and not implemented here (app/refunds.py
post_refund_to_ledger; it lives in examples/ledger)

VERDICT   NO-SHIP: scheduled settlement-report reconciliation
```

That is the honest reading: everything upstream of the money moving is now correct, and nothing yet
compares what Stripe says it paid out against what this service believes it refunded.

Two rows the retired block carried as `n/a` are simply gone: the transfer reversal named out of scope
above, and destination verification, which belongs to wire, RTP and FedNow under *Finality is a property of
the rail, not of your API surface* rather than to a card refund. A concept the change does not touch gets
no slot at all.
