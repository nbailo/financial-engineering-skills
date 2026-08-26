# Capture and amounts

How much the processor actually takes, and when the window to take it closes. The deadline is a scheme
property rather than a computable one, and the amount you serialise has to satisfy the processor's own
minor-unit rules rather than ISO 4217.

## Contents

- Authorization windows by brand, channel and CIT/MIT; reading `capture_before` instead of computing it
- Partial capture: destructive by default (Stripe, Adyen single) vs preserved (multicapture, Adyen
  multiple-partial-captures enabled by Support)
- `final_capture` defaults, and the `request_multicapture=if_available` silent no-op trap
- Incremental authorization: absolute totals, decline semantics, discarded piggy-backed field updates, caps
- Overcapture as a clearing-time trick; authenticate-high-capture-low under SCA
- Per-method amount ceilings, and minor-unit overrides that contradict ISO 4217 (ISK, UGX, HUF, TWD)

---

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
authorization (`docs.stripe.com/payments/incremental-authorization`). `amount=1500` against a 1000
authorization means "make it 1500", not "add 1500". On a decline the original authorization survives
*and* any field updates piggy-backed into the same call are discarded; re-apply them explicitly.
Increments do not extend the capture deadline.

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
