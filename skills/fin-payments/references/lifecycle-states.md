# Lifecycle states

What a processor's status value and its synchronous response actually say about where the money is,
stated as state machines with the transitions that generated code gets wrong: which statuses are
cancellable, which result codes are an outcome, and whose reference the modification API hands back. Every
fact here is per-processor and several are per-account-configuration, so this is a lookup keyed on the API
symbol in the diff, not a narrative.

## Contents

- Stripe `PaymentIntent` state graph, the cancellable statuses, and `status` vs money
- Adyen result codes: non-final (`Received`, `Pending`, `PresentToShopper`) vs final, and `PartiallyAuthorised`
- Adyen modification APIs: `[capture-received]`, the modification `pspReference`, `originalReference`
- `cancelOrRefund`: what it is for and where it must not be used

---

## Stripe `PaymentIntent`: the state graph

Seven statuses. Only two are terminal, and **neither of them is a failure state**.

| status | money position | cancellable | notes |
|---|---|---|---|
| `requires_payment_method` | nothing held | yes | **also where a *failed attempt* lands**, not terminal |
| `requires_confirmation` | nothing held | yes | |
| `requires_action` | nothing held | yes | 3DS / redirect pending |
| `processing` | indeterminate | **only for `us_bank_account`** | async methods sit here for days |
| `requires_capture` | authorization held | yes | cancel here; **do not refund** |
| `succeeded` | captured | no | terminal, and *stays* terminal through refunds and disputes |
| `canceled` | released | no | terminal; "can't be undone" |

Source: `docs.stripe.com/payments/paymentintents/lifecycle`.

Two consequences that generated code misses:

**`payment_intent.payment_failed` does not mean the payment is dead.** A failed attempt returns the intent to
`requires_payment_method` so the customer can try another card. A handler that cancels the order or releases
inventory on that event kills a live payment, and the intent can still reach `succeeded` afterwards, out of
order relative to your cancel.

**`succeeded` is a lifecycle fact, not an economic one.** Stripe:
*"Subsequent refunds, disputes, and outcomes are reflected on the Charge... even though the PaymentIntent
remains `succeeded`"* (`docs.stripe.com/payments/payment-intents/verifying-status`). Keep the two in separate
columns and never compute money from the lifecycle column:

```sql
ALTER TABLE payments
  ADD COLUMN pi_status        text        NOT NULL,           -- lifecycle, 7 values above
  ADD COLUMN charge_id        text,                           -- ch_… once a charge exists
  ADD COLUMN currency         char(3)     NOT NULL,
  ADD COLUMN authorized_minor bigint      NOT NULL DEFAULT 0,
  ADD COLUMN captured_minor   bigint      NOT NULL DEFAULT 0, -- from charge.amount_captured
  ADD COLUMN refunded_minor   bigint      NOT NULL DEFAULT 0,
  ADD COLUMN disputed_minor   bigint      NOT NULL DEFAULT 0; -- dispute currency may differ; see below
-- Forbidden: SELECT sum(amount) FROM payments WHERE pi_status = 'succeeded'
```

The cancel/refund branch, written from the status set rather than from a guess:

```python
CANCELLABLE = {"requires_payment_method", "requires_confirmation",
               "requires_action", "requires_capture"}

def terminate(pi):
    cancellable = pi.status in CANCELLABLE or (
        pi.status == "processing" and "us_bank_account" in pi.payment_method_types)
    if cancellable:
        return stripe.PaymentIntent.cancel(pi.id, idempotency_key=key_for(intent_row))
    if pi.status == "succeeded":
        return refund_within_ceiling(pi)          # ceiling from charge.amount_captured
    raise NotTerminable(pi.id, pi.status)         # never fall through to Refund.create
```

Stripe on the `requires_capture` case, verbatim: *"the charge attached to the PaymentIntent remains uncaptured
and can't be refunded directly. You must cancel the PaymentIntent."* An intent may also auto-cancel after too
many confirmation attempts, so `canceled` can appear without your code asking for it; treat `canceled`
arriving unsolicited as a real event, not as an impossible one.

## Adyen result codes: which ones are an outcome

`docs.adyen.com/online-payments/build-your-integration/payment-result-codes/`.

| `resultCode` | final? | what your code may do |
|---|---|---|
| `Received` | **no** | persist `pspReference`, set local state pending, wait for the webhook |
| `Pending` | **no** | same |
| `PresentToShopper` | **no** | render the voucher/QR; the payment has not happened |
| `Authorised` | yes | funds held: still not captured, still not settled |
| `Refused` | yes | declined; the `refusalReason` is the branch |
| `Cancelled` | yes | |
| `Error` | yes | **an error at Adyen, not a decline**; outcome may still be unknown |
| `PartiallyAuthorised` | yes | the authorised amount is **not** the requested amount |

The governing sentence, and the reason none of the "final" codes is a licence to fulfil: *"The status of a
payment can sometimes change after you get the result code, so we recommend that you do not use the result
code to update your order management system."*

On `PartiallyAuthorised`, take the capture ceiling from the authorised amount in the response, never from your
order total. The continuation flow for the unpaid remainder (split tender) is **not established by the sources
behind this file**; do not assume Adyen re-requests it.

## Adyen modification APIs: the reference you get back is not the payment's

`/captures`, `/cancels`, `/refunds` and `/cancelOrRefund` are asynchronous. The synchronous body is a receipt:

```json
{ "pspReference": "8815329842436895", "response": "[capture-received]" }
```

That `pspReference` is the **modification's** reference. The webhook that follows carries it as
`pspReference`, and the *payment's* reference as `originalReference`:

| webhook field | on `AUTHORISATION` | on `CAPTURE` / `REFUND` / `CANCELLATION` |
|---|---|---|
| `pspReference` | the payment | the **modification** |
| `originalReference` | absent | the payment's `pspReference` |
| `merchantReference` | yours | yours: **not unique**, grouping only |
| `success` | authorised? | *request was valid and submitted to the bank*, **not settled** |
| `eventDate` | ISO 8601 | the ordering field |

Adyen documents duplicate deliveries sharing exactly `eventCode` and `pspReference`, which makes the pair the
dedupe key. Deduping on `pspReference` alone collapses `AUTHORISATION` with `CAPTURE`, or two refunds against
one payment, into a single row and silently drops a real money movement.

```sql
CREATE TABLE adyen_events (
  event_code         text        NOT NULL,
  psp_reference      text        NOT NULL,   -- modification's own reference
  original_reference text,                   -- payment's pspReference; NULL on AUTHORISATION
  merchant_reference text        NOT NULL,   -- NOT unique: grouping attribute only
  success            boolean     NOT NULL,
  amount_minor       bigint      NOT NULL,
  currency           char(3)     NOT NULL,
  event_date         timestamptz NOT NULL,
  reason             text,
  PRIMARY KEY (event_code, psp_reference)    -- unique index, not an if-exists check
);
```

`CAPTURE` with `success=true` is a bank submission, not settled funds. The settled position appears only in
the Settlement details report.

## `cancelOrRefund`: what it is for, and where it must not be used

It exists because the merchant often does not know whether the payment has been captured yet; capture delay
is an account setting with three modes (**immediate/automatic, which is the default**, delayed automatic, and
manual), so an order-cancellation path racing an automatic capture genuinely cannot tell.

| you know the capture state | call |
|---|---|
| not captured | `/cancels` |
| captured | `/refunds` |
| genuinely unknown | `/cancelOrRefund` |

Prohibitions, from `docs.adyen.com/online-payments/classic-integrations/modify-payments/cancel-or-refund`:

- **Must not be used for payments with multiple partial captures.**
- It does not accept split instructions; a platform must determine the capture state and call `/cancels` or
  `/refunds` explicitly so the split can be expressed.
- The synchronous response is only `[cancelOrRefund-received]` plus a modification `pspReference`. The real
  outcome arrives as the `CANCEL_OR_REFUND` webhook, and *which* of the two happened is only knowable there.

A UI that says "cancelled" on the 200 is therefore a guess, and the ledger effect differs between the two arms:
release a hold, or book a refund under whatever reversal fee treatment your processor agreement states.
