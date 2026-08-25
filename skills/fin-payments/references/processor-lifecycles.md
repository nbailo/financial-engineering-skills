# Processor lifecycles

The authorize → capture → settle path for each processor, stated as state machines with the transitions that
generated code gets wrong: which statuses are cancellable, which capture modes destroy the remaining
authorization, where the deadline actually comes from, and which "supported" flags are not capability
assertions. Every fact here is per-processor and several are per-account-configuration, so the file is a
lookup keyed on the API symbol in the diff, not a narrative.

## Contents

- Stripe `PaymentIntent` state graph, the cancellable statuses, and `status` vs money
- Adyen result codes: non-final (`Received`, `Pending`, `PresentToShopper`) vs final, and `PartiallyAuthorised`
- Authorization windows by brand, channel and CIT/MIT; reading `capture_before` instead of computing it
- Partial capture: destructive by default (Stripe, Adyen single) vs preserved (multicapture, Adyen
  multiple-partial-captures enabled by Support)
- `final_capture` defaults, and the `request_multicapture=if_available` silent no-op trap
- Incremental authorization: absolute totals, decline semantics, discarded piggy-backed field updates, caps
- Overcapture as a clearing-time trick; authenticate-high-capture-low under SCA
- Adyen modification APIs: `[capture-received]`, the modification `pspReference`, `originalReference`
- `cancelOrRefund`: what it is for and where it must not be used
- Per-method amount ceilings, and minor-unit overrides that contradict ISO 4217 (ISK, UGX, HUF, TWD)
- PayPal and Square: capture key uniqueness per API call type, 200 vs 201 on replay

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

## Authorization windows, and why you must read `capture_before`

Windows are brand-, channel- and CIT/MIT-specific. Stripe and the network classify a transaction as MIT or CIT
from signals of cardholder participation, *"not solely on API parameters like `off_session`"*, so the window
is **not computable from your own request flags**, even if you knew the brand.

| brand | channel | CIT/MIT | window |
|---|---|---|---|
| Visa | CNP | MIT | 5 days, exactly **4 d 18 h** |
| Visa | CNP | CIT | 7 days |
| Mastercard / Amex / Discover | CNP | either | 7 days |
| Mastercard / Amex / Discover | card-present | either | **2 days** |
| any, JPY-issued (JP) | n/a | n/a | up to 30 days |

Source: `docs.stripe.com/payments/place-a-hold-on-a-payment-method`. That page does not state a Visa
card-present window; treat it as **unverified** rather than assuming it equals the CNP figure.

Non-card methods on Stripe carry their own windows: Klarna **28 calendar days, to midnight**; Affirm 30 days;
Afterpay 13 days; Cash App 7 days; PayPal 10 days, auto-extending to 20.

```python
charge = stripe.Charge.retrieve(pi.latest_charge)
deadline = getattr(charge, "capture_before", None)   # unix seconds, server-supplied
if deadline is None:
    raise CaptureDeadlineUnknown(charge.id)          # do NOT substitute a constant
schedule_capture_sweep(charge.id, at=deadline - CAPTURE_SAFETY_MARGIN)
```

On expiry the funds are released and the PaymentIntent moves to `canceled`. Adyen surfaces the same class of
rejection as a capture failure reason string of the form `"Operation maximum period allowed: X days"`. Branch
on it and stop; retrying a capture past the scheme window never succeeds.

## Partial capture: destructive by default

The *same API call* has opposite consequences for the remaining authorization depending on account
configuration that is invisible in the diff.

| processor / mode | after `capture(amount < authorized)` | remainder capturable later? | how it is turned on |
|---|---|---|---|
| Stripe, default | remainder **released** | no | n/a |
| Stripe, multicapture | remainder held | yes: ≤50 non-final + 1 final | `capture_method='manual'` + `request_multicapture` at confirm |
| Adyen, single partial capture (default) | *"Any unclaimed amount that is left over after partially capturing a payment is automatically cancelled."* | no | n/a |
| Adyen, multiple partial captures | *"The unclaimed amount after an initial partial capture is not automatically cancelled"* | yes | **Adyen Support must enable it on the merchant account** |

Sources: `docs.stripe.com/payments/place-a-hold-on-a-payment-method` (*"A partial capture automatically
releases the remaining amount"*), `docs.stripe.com/payments/multicapture`, `docs.adyen.com/online-payments/capture`.

Because the mode is account state, the code asserts the mode it requires instead of assuming it:

```python
MULTI_PARCEL = config.require("payments.multicapture_enabled")   # no default; deploy-time config

def capture_parcel(pi_id, minor, is_final):
    pi = stripe.PaymentIntent.retrieve(pi_id)
    if MULTI_PARCEL and pi.capture_method != "manual":
        raise ConfigError("multicapture requires capture_method='manual'")
    if not MULTI_PARCEL and minor < pi.amount_capturable:
        raise WouldReleaseRemainder(pi_id, pi.amount_capturable - minor)
    stripe.PaymentIntent.capture(pi_id, amount_to_capture=minor,
                                 final_capture=is_final,
                                 idempotency_key=key_for(parcel_row))
    after = stripe.PaymentIntent.retrieve(pi_id)
    if not is_final and after.amount_capturable == 0:
        raise RemainderReleased(pi_id)   # the if_available trap fired; alert, do not ship parcel 2
```

`amount_capturable == 0` after a non-final capture is the only in-band evidence that multicapture was not
actually active. Check it on the *first* parcel, while a parcel is still unshipped, not on the second.

The balance-transaction side-effect of a single partial capture is a `charge` balance transaction for the
**full authorized** amount plus a **`refund`** balance transaction for the uncaptured portion
(`docs.stripe.com/reports/balance-transaction-types`). Reconcilers keyed on `type == 'refund'` report refunds
that never happened; use `reporting_category` and correlate to an actual Refund object.

### `final_capture` and the `if_available` silent no-op

`final_capture` **defaults to `true`**. The first omission on a multi-parcel order releases everything not yet
captured: a shipping bug that looks like a successful capture.

```python
stripe.PaymentIntent.capture(pi, amount_to_capture=parcel_1, final_capture=False, ...)  # required
stripe.PaymentIntent.capture(pi, amount_to_capture=parcel_2, final_capture=False, ...)
stripe.PaymentIntent.capture(pi, amount_to_capture=parcel_3, final_capture=True,  ...)  # last one only
```

Constraints: ≤50 non-final captures plus one final capture; the sum must not exceed the authorized amount;
`capture_method='manual'` is required; multicapture is **not supported with `source_transaction`**.

The documented trap: `request_multicapture='if_available'` is **accepted** together with
`capture_method='automatic'`, and `multicapture.status` may then read `available`, *and multicapture still
does not work*. A "supported" flag is a statement about eligibility, not a capability assertion. Gate on the
asserted config plus `capture_method == 'manual'` plus the `amount_capturable` post-condition above.

## Incremental authorization

`amount` on `increment_authorization` is the **new total**, not a delta, and it must exceed the current
authorization (`docs.stripe.com/payments/incremental-authorization`).

```python
new_total = current_authorized_minor + extra_minor      # ABSOLUTE
assert new_total > current_authorized_minor
try:
    pi = stripe.PaymentIntent.increment_authorization(
        pi_id, amount=new_total, idempotency_key=key_for(increment_row))
except stripe.error.CardError as e:                     # e.code == "card_declined"
    # 1. The authorization survives, unchanged, at current_authorized_minor.
    # 2. Anything bundled into this call was DISCARDED. Re-apply it separately.
    stripe.PaymentIntent.modify(pi_id, metadata=meta,
                                idempotency_key=key_for(meta_row))
    raise IncrementDeclined(pi_id, current_authorized_minor)
```

Fields that are silently dropped when the increment declines: `application_fee_amount`, `transfer_data`,
`metadata`, `description`, `statement_descriptor`. A caller that updates `transfer_data` in the same call and
swallows the error now believes the platform split is one thing while Stripe holds another; the money divides
differently at capture than the platform recorded.

| constraint | value |
|---|---|
| max increment attempts | 10 |
| per-increment cap | greater of **500 USD** or **500%** of the previously authorized amount |
| effect on `capture_before` | **none**: increments do not extend the window |
| after a partial capture via multicapture | not usable |
| SCA | increments are MITs → **no liability shift** |

Passing a delta is the standard failure: if `extra < current_authorized` the API errors; if
`extra > current_authorized` the authorization is silently set to *the extra amount*, under-authorizing the
stay, and the capture then fails or is force-posted above the authorized amount, chargeback-eligible as "No
Authorization".

## Overcapture is a clearing-time trick

Overcapture creates **no new network authorization** (`docs.stripe.com/payments/overcapture`). Caps are
brand-, country- and MCC-specific, in the range **+15% to +30%**, exposed as `overcapture.status` and
`maximum_amount_capturable`. Visa excludes the EEA.

Under SCA you generally must have authenticated for at least the amount you will capture, and exceeding the
authenticated amount requires **cancelling and creating a new payment**, a fresh customer interaction.
Therefore: **authenticate high, capture low.**

Worked case (hotel, 200.00 EUR room plus expected incidentals):

| approach | authenticate / authorize | checkout total | outcome |
|---|---|---|---|
| authorize low, overcapture | 200.00 | 213.47 | inside a +15% cap this clears, but under SCA the authentication covered 200.00; over the cap or outside it, cancel + re-create, customer re-authenticates at checkout |
| **authenticate high, capture low** | 260.00 | 213.47 | one capture of `21347`; the 46.53 remainder is released by the same partial-capture rule above, which is correct here and only here |

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
release a hold, or book a refund whose original processing fee is not returned.

## Amount ceilings and minor-unit overrides

ISO 4217 gives an exponent; processors override it. A `Money` type keyed only on currency produces 100×
errors.

| currency | ISO 4217 exponent | Stripe charge | Stripe payout |
|---|---|---|---|
| JPY, KRW, VND, CLP, XOF/XAF/XPF, BIF, DJF, GNF, KMF, PYG, RWF, VUV | 0 | integer major unit | n/a |
| KWD, BHD, JOD, OMR, TND | **3** | thousandths | n/a |
| **ISK** | 0 | must be sent **two-decimal, ending `00`** | n/a |
| **UGX** | 0 | same | n/a |
| **HUF** | 0 in ISO; Stripe treats charges as two-decimal | two-decimal | **zero-decimal: amount must be divisible by 100** |
| **TWD** | two-decimal for charges | two-decimal | **divisible by 100** |

Sources: `docs.stripe.com/currencies`; ISO 4217 exponent lists.

Arithmetic that fails: ISK 1,500 kr from a generic exponent-0 table serialises as `amount=1500` and charges
**15.00 kr**, a 100× undercharge; Stripe wants `150000`. KWD 1.500 is `1500`, not `150`. And a HUF balance of
`123456` minor units cannot be paid out in full; the payout must be a multiple of 100, so `56` is a permanent
residue a payout job must book, not drop.

```python
def assert_processor_units(currency: str, minor: int, op: str) -> None:
    if op == "charge" and currency in ("ISK", "UGX"):
        assert minor % 100 == 0, f"{currency} charge must end in 00: {minor}"
    if op == "payout" and currency in ("HUF", "TWD"):
        assert minor % 100 == 0, f"{currency} payout must be divisible by 100: {minor}"
```

Ceilings are method-specific, not global (`docs.stripe.com/currencies`):

| method | max digits |
|---|---|
| most cards | 12 |
| **American Express** | **9** |
| most non-card methods | 8 (i.e. 999,999.99) |

Validate the cart total against the ceiling for the *selected* method, after the method is chosen; a total
that passes for Visa can be unpayable on Amex.

## PayPal and Square

**PayPal, `PayPal-Request-Id`** (`developer.paypal.com/api/rest/reference/idempotency/`):

- *"When you omit the PayPal-Request-Id header from a request, PayPal duplicates the request."* Omission is not
  "no dedupe"; it is documented duplication.
- A replay returns *"the latest status of the previous request that used that same header"*, the **latest**
  status, not the original response body. Do not treat a replay as a snapshot of what you got the first time.
- *"The PayPal-Request-Id header value must be unique for both each request **and an API call type**. For
  example, authorize payment and capture authorized payment."* Reusing one order-scoped id across authorize and
  capture is documented misuse.
- Orders v2 capture retains keys for **6 hours** (`developer.paypal.com/api/orders/v2/orders-capture`), and the
  replay signal is the status code: **`200 OK` = replayed, `201 Created` = freshly captured.**

```python
PAYPAL_CAPTURE_KEY_RETENTION = timedelta(hours=6)     # documented, per call type
assert CAPTURE_RETRY_HORIZON < PAYPAL_CAPTURE_KEY_RETENTION

def paypal_key(intent_row, call: str) -> str:
    # call ∈ {"authorize", "capture", "refund"}; uniqueness is per request AND per call type
    return f"{intent_row.id}:{call}"                  # persisted with the row before the call

resp = http.post(f"/v2/checkout/orders/{order_id}/capture",
                 headers={"PayPal-Request-Id": paypal_key(row, "capture")})
if resp.status_code == 200:
    replayed = True    # deduped correctly; now do NOT re-fire fulfilment, email, ledger write
elif resp.status_code == 201:
    replayed = False
else:
    mark_unknown(row); return          # never mint a fresh key to "try again"
```

Past the 6-hour bound the key is gone and a re-send is a new capture. Resolve by reading the order, not by
re-sending.

**Square** (`developer.squareup.com/docs/build-basics/common-api-patterns/idempotency`): same key + same body
returns *"the response as the first successful `CreatePayment` response"*; same key + different body yields
*"an error indicating that you used the idempotency key previously."* **Key length and retention are not
stated on that page**; do not assert a number, and bound Square retry horizons in hours rather than days.
Braintree's `Idempotency-Key` (`apiRequestKey`) covers `transaction.sale`, `credit`, `submitForSettlement`,
`submitForPartialSettlement`, `void` and `refund`; its retention is likewise undocumented.

Stripe's SDKs mint a random 128-bit key per POST *above* the retry loop
(`stripe-python/stripe/_api_requestor.py:86-88`, `:567-573`; `stripe-node/src/RequestSender.ts:391-392`) and
default to `max_network_retries = 2`. `RequestSender.ts:400-403`: *"Closed-connection errors are retried
regardless of that setting... and those codes can surface after the API processed the request, so the retry
needs a key to dedupe against."* That covers retries **inside one SDK call only**; a caller that wraps
`create()` in its own `try/except` gets a new random key and a second charge.
