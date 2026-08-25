# Idempotency keys

The identity you send a processor has a retention clock and a scope boundary, and both are that provider's
own numbers rather than a property of idempotency keys. This file carries those numbers, the replay signals
that say a call was deduplicated, the requests the key layer never sees, and the one case where a fresh key
is legal.

## Contents

- PayPal and Square: capture key uniqueness per API call type, 200 vs 201 on replay
- Idempotency keys: retention bounds, scope boundaries, what the key does not cover
- Replay signals (`Idempotent-Replayed`, 200 vs 201), 409 and Adyen 704, and when a fresh key is legal

---

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

## Idempotency keys: retention, scope, and what the key does not cover

*Operation identity* requires an identity minted from the intent instance and committed before the call. What a
processor adds is that its memory of that identity is **finite and partitioned**. Once retention expires or the
request crosses the scope boundary, the counterparty no longer recognises the key, and a resend is a fresh
instruction rather than a retry.

| provider / layer | retention of a key | source |
|---|---|---|
| Stripe | **≥ 24 hours**, then a reused key produces a *new request* | Stripe idempotent requests |
| Adyen | **≥ 7 days**, scoped to the company account | Adyen API idempotency |
| PayPal Orders v2 capture | **6 hours**, per call type | `developer.paypal.com/api/orders/v2/orders-capture` |
| Open Banking | **24 hours** | scheme guidance |
| AWS Lambda Powertools idempotency | **1 hour** default expiry | Powertools documentation |
| Square, Braintree | **not documented**; do not assert a number, bound retry horizons in hours | see above |

Adyen scopes the key by region as well as by account: keys *"will not be checked for duplication in other
regions"*, so a cross-region failover turns a retry into a second payment.

Bound the same-key retry loop by wall clock to well inside the documented retention, and **store that bound as
an asserted constant next to the provider's number** so the two cannot drift apart silently:

```python
STRIPE_KEY_RETENTION = timedelta(hours=24)      # documented lower bound
ADYEN_KEY_RETENTION  = timedelta(days=7)
assert RETRY_HORIZON < STRIPE_KEY_RETENTION
```

Past the bound, stop retrying, mark the attempt **UNKNOWN**, and resolve by querying the processor for your own
reference or by reading the settlement report. Never mint a fresh key to try again.

Pin the key to the `(provider, endpoint/region, credential)` recorded at mint time, and encode it to the
narrowest length limit across every provider you target, validated at construction:

| provider | maximum key length |
|---|---|
| Stripe | 255 |
| Adyen | 64 |
| AWS | 64 |
| Open Banking | 40 |

**What the key does not cover.** The idempotency layer sits behind the rate limiter, the auth layer and most
request validation, so a **429**, a **401** and most **400**s are answered before the key is ever consulted;
they are not deduplicated responses and they say nothing about whether an earlier attempt executed. A key is
also meaningless at a different processor. **500s ARE cached**, and a cached 500 means *indeterminate*: Stripe
states there is no client-side algorithm that resolves it, and Stripe's back office may roll the charge forward
to the network afterwards, so the object *"surfaces only via webhook"*. That is the case the sweeper's
non-terminal loop exists to find (see `webhook-recovery.md`).

## Replay signals, 409, and when a fresh key is legal

A deduplicated call still returns a success body. Unless you branch on the replay signal, every downstream side
effect (fulfilment, email, ledger write) fires a second time for one correctly deduplicated payment.

| provider | replay signal | meaning |
|---|---|---|
| Stripe | header `Idempotent-Replayed: true` | the stored response was served; do not re-fire side effects |
| PayPal (Orders v2 capture) | **`200 OK` = replayed**, `201 Created` = freshly captured | same |
| Adyen | HTTP **409**, or 422/409 with error code **704** | the operation is *already in flight* |

HTTP **409**, and Adyen's error code **704**, mean "already in flight": back off and **read**, never retry as a
write. A retry-as-write on 409 races the in-flight original and is how a single intent becomes two.

Send an identity on the **reversal** call too, not only on the original. A refund, a capture reversal and a
transfer reversal each move money and each need their own key, minted from their own intent row.

**When a fresh key is legal.** Generate a new key only after a **non-409 4xx**: a validation error the processor
rejected *before* its idempotency layer, where the instruction provably never entered the money path. After a
timeout, a socket close or any 5xx, the same key is re-sent unchanged, because the outcome is UNKNOWN and a new
key would ask for a second execution.
