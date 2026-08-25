# Webhooks

The inbox architecture a processor's delivery semantics force on you: verify, acknowledge, persist, then
process asynchronously from stored state. Covers the dedupe keys that are correct per provider, the ordering
field that is not the signature timestamp, the watermark schema that survives a second-granularity clock, and
the delivery-killing traps that live in framework and CDN configuration rather than in handler code.

## Contents

- Signature verification over the raw unparsed body; the `v1` scheme; replay tolerance; Adyen HMAC
- Ack-then-work: Stripe's "2xx prior to any complex logic", Adyen's 10-second timeout and `[accepted]`
- The inbox table: raw signed event stored, acked, processed asynchronously; why a 2xx asserts nothing
- Dedupe keys per provider: Stripe `(event.type, data.object.id)` alongside `event.id`; Adyen
  `(eventCode, pspReference)` and why `pspReference` alone collapses distinct modifications
- Ordering: `event.created` / `eventDate`; the signature timestamp is regenerated per delivery attempt
- The `(created, applied_event_ids)` watermark: schema, the conditional `UPDATE`, and the same-second case
- Dead-letter table, alerting, and replay from the inbox after a dependency arrives
- 3xx responses kill delivery: http→https, trailing-slash, and CDN-introduced redirects
- Never fulfil from a client redirect: 3DS `return_url`, checkout success pages
- The sweeper: listing processor objects changed since a cursor so a dropped event self-heals
- Retry horizons and what expires: Stripe 3 days, Adyen queued-and-failing behaviour
- Reading the object from its authority: which call, where on the path, and why not the payload

---

## 1. Signature verification over the raw unparsed body

The signature is an HMAC over the **bytes on the wire**. Any framework that parses the body before your code
sees it, and hands you a dict you then re-serialise, changes key order, whitespace and unicode escaping; the
HMAC fails on a payload that is perfectly authentic, and the usual "fix" is to disable verification.

| framework | the byte-preserving accessor | the trap |
|---|---|---|
| Flask / Quart | `request.get_data()` | `request.get_json()`: parsed, bytes gone |
| Django | `request.body` | reading `request.POST` first consumes the stream; `@csrf_exempt` also required |
| Express | `express.raw({type: 'application/json'})` on that route | a global `express.json()` mounted earlier |
| FastAPI | `await request.body()` | a Pydantic body model in the signature |
| Rails | `request.body.read` | `params` |

Stripe's scheme, per <https://docs.stripe.com/webhooks>: header `Stripe-Signature: t=<unix>,v1=<hex>,…`,
HMAC-SHA256 over the concatenation `t + "." + <raw payload>` with the endpoint's signing secret; compare in
constant time; **ignore any scheme other than `v1`**. Future schemes will appear in the same header and a
verifier that accepts "any matching element" is trivially downgraded. Default tolerance is **5 minutes**, and
Stripe warns explicitly: *"Don't use a tolerance value of `0`. Using a tolerance value of `0` disables the
recency check entirely."* A tolerance smaller than your host clock skew rejects live traffic; a tolerance of
zero rejects nothing.

**The signature timestamp is regenerated on every delivery attempt.** It is a replay guard and nothing else.
See §5.

Adyen signs each notification item, not the request: the HMAC arrives in that item's
`additionalData.hmacSignature`, so a request carrying several `notificationItems` is validated **item by
item**, and one unsigned item does not invalidate the others. Use the SDK's `HmacValidator`
(`Adyen::Utils::HmacValidator`, `com.adyen.util.HmacValidator`, `@adyen/api-library`'s `hmacValidator`) rather
than reimplementing the concatenation: *the exact field order of Adyen's signed payload is not established by
this repository's research; do not hand-roll it from memory.* Adyen additionally supports HTTP basic auth on
the webhook endpoint; that is a second, independent check, not a substitute.

## 2. Ack-then-work

Both vendors instruct you to respond before doing work, and their reasons differ in a way that matters.

| vendor | instruction | consequence for your code |
|---|---|---|
| Stripe | return 2xx *"prior to any complex logic that might cause a timeout"* | slow handler ⇒ retry ⇒ handler runs concurrently with itself |
| Adyen | respond with a success status (e.g. `202`) within **10 seconds**; *"Do not validate or process the data at this step"* | past 10s the webhook is marked **Failing** and queued for retry |

Adyen's instruction is the sharper one: it tells you to ack **before validating**, which means your 2xx cannot
be conditional on the HMAC being valid. The consequence is structural: the inbox row must carry a
`sig_valid` column, and the async processor must refuse to act on a row where it is false. Adyen integrations
that ack a request body also expect the literal body `[accepted]`; *which Adyen integration versions require
that body is not established by this repository's research; read the webhook page for the integration you
are on. The 2xx-within-10-seconds part is documented and load-bearing.*

The receive endpoint therefore does exactly three things: verify, insert one row, return. No `retrieve` call,
no ledger write, no email, no fulfilment.

```python
# routes.py
@app.post("/webhooks/stripe")                       # exact path, no trailing slash; see §8
def receive_stripe():
    raw = request.get_data()                        # bytes, before any JSON parse
    sig = request.headers.get("Stripe-Signature", "")
    try:
        event = stripe.Webhook.construct_event(raw, sig, WEBHOOK_SECRET)  # tolerance defaults to 300s
    except stripe.SignatureVerificationError:       # stripe.error.SignatureVerificationError on stripe-python <8
        return "", 400                              # a bad signature never becomes good; retries are harmless
    obj = event["data"]["object"]
    with db.begin() as tx:                          # one INSERT; if it raises, the 500 is correct
        tx.execute(sa.text("""
            INSERT INTO webhook_inbox (provider, event_id, event_type, object_id, object_created,
                                       api_version, raw_body, sig_header, sig_valid, source)
            VALUES ('stripe', :eid, :etype, :oid, :created, :ver, :raw, :sig, TRUE, 'push')
            ON CONFLICT (provider, event_id) DO NOTHING
        """), dict(eid=event["id"], etype=event["type"], oid=obj["id"],
                   created=event["created"], ver=event["api_version"],
                   raw=raw, sig=sig))
    return "", 200                                  # means "durably stored". Nothing more.
```

**Let the INSERT failure become a 5xx.** A `try/except: return 200` around the store is the one bug in this
file that is unrecoverable: the provider marks the event delivered, the retry never comes, and your own logs
show a clean 200.

## 3. The inbox table

```sql
CREATE TABLE webhook_inbox (
  id              bigserial PRIMARY KEY,
  provider        text        NOT NULL,
  event_id        text        NOT NULL,          -- evt_… / Adyen pspReference of the notification
  event_type      text        NOT NULL,          -- 'refund.updated' / 'AUTHORISATION'
  object_id       text        NOT NULL,          -- re_… pi_… ch_… / the object's own pspReference
  object_created  bigint      NOT NULL,          -- Stripe event.created (unix s) / eventDate epoch
  api_version     text,                          -- the version the payload was rendered at
  raw_body        bytea       NOT NULL,          -- bytea, NOT json/jsonb: a jsonb round-trip
                                                 -- reorders keys and the HMAC can never be re-checked
  sig_header      text        NOT NULL,
  sig_valid       boolean     NOT NULL,
  source          text        NOT NULL,          -- 'push' | 'sweeper' | 'manual_resend'
  received_at     timestamptz NOT NULL DEFAULT now(),
  processed_at    timestamptz,                   -- set ONLY in the txn that applied the effect
  attempts        int         NOT NULL DEFAULT 0,
  dead_lettered_at timestamptz,
  last_error      text
);
CREATE UNIQUE INDEX webhook_inbox_uniq ON webhook_inbox (provider, event_id);
CREATE INDEX webhook_inbox_todo ON webhook_inbox (received_at)
  WHERE processed_at IS NULL AND dead_lettered_at IS NULL;
```

The unique index is the correctness mechanism; a preceding `SELECT … WHERE event_id = ?` is only an
optimisation, because two deliveries of the same event race through the lookup and both pass.

**Why a 2xx asserts nothing.** The provider's delivery log shows 200 for every event you stored and then
dropped on the floor. The only measurement of *processed* is yours:

```sql
SELECT count(*) FROM webhook_inbox
 WHERE processed_at IS NULL AND dead_lettered_at IS NULL
   AND received_at < now() - interval '5 minutes';   -- alert > 0
```

Under *reconciliation runs in production* the alert destination is a config key with **no default** that
raises at import when unset.

## 4. Dedupe keys per provider

| provider | transport identity | business identity of the effect | what the wrong key does |
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

## 5. Ordering

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

## 6. The `(created, applied_event_ids)` watermark

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
| 3 | `evt_CCC` | `refund.created` (redelivery of #1, new `t=`) | 1719000000 | `succeeded` |

Delivered 1 → 2 → 3.

| step | bare `created <= applied_at` guard | `(applied_at, applied_ids)` guard |
|---|---|---|
| 1 | admit; wm ← 1719000000; re-read ⇒ `pending`; no ledger move | admit; wm ← (1719000000, {`evt_AAA`}); re-read ⇒ `pending` |
| 2 | `1719000000 <= 1719000000` ⇒ **drop**. The refund is `pending` in your books forever, and nothing ever fires again | `applied_at = created` and `evt_BBB` ∉ applied_ids ⇒ **admit**; re-read ⇒ `succeeded`; post the principal leg, leave the fee expensed |
| 3 | drop (already broken) | `applied_at = created` and `evt_BBB` ≠ `evt_CCC`, `evt_AAA` ∈ applied_ids ⇒ drop at the watermark; the inbox unique index had already rejected the duplicate `event_id` anyway |

Event-id dedupe cannot substitute for the watermark, and the watermark cannot substitute for event-id dedupe.
A *late* `refund.created` carries an `event.id` your `processed_events` table has never seen and re-arms the
money branch; the watermark is what refuses it. Conversely a redelivery of an admitted event carries the same
`event.id`, and the unique index is what refuses that.

Legality is the second half of *arrival order is not occurrence order*: enumerate the legal `(state, event)` pairs with an explicit
`_ => reject` arm. `succeeded`, `failed` and `canceled` never regress to `pending` on a *status* message, but
a terminal state does accept the events by which the processor corrects a fact you already booked. Stripe
refunds are the concrete case: a `succeeded` refund can later report `failed` (return of funds, up to 30 days
from the post date), and `charge.dispute.funds_reinstated` arrives after you wrote the loss off. Those are
economic corrections and they must be admitted.

## 7. Dead-letter, alert, replay

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

## 8. 3xx responses kill delivery

Stripe counts a `301`/`302` on the webhook URL as a **delivery failure**. The retry budget burns down over
3 days and the events are then gone. The symptom is "payments succeed, orders never fulfil", with a clean
`200` in your own logs, at the redirect *target*, which the provider never followed.

Sources, in the order they actually bite:

| layer | setting | effect |
|---|---|---|
| Django | `APPEND_SLASH = True` (default) | `POST /webhooks/stripe` → `301` to `/webhooks/stripe/` |
| Rails | `config.force_ssl = true` | `http://` registration → `301` to `https://` |
| nginx | `return 301 https://$host$request_uri;` in the `:80` server | same |
| CloudFront | viewer protocol policy *Redirect HTTP to HTTPS* | same, and invisible in the origin's logs |
| Cloudflare | *Always Use HTTPS* | same |

Two other silent killers on the same path: a CSRF middleware answering `403` on the webhook POST (Django needs
`@csrf_exempt` on the view), and nginx's `client_max_body_size` default of **1m** returning `413` on a large
event payload.

Assert it, don't inspect it:

```python
def test_webhook_endpoint_does_not_redirect():
    r = requests.post(WEBHOOK_URL, data=b"{}", allow_redirects=False, timeout=5)
    assert r.status_code == 400, r.status_code   # signature rejected; proves the route was REACHED
    assert not r.is_redirect
```

A `3xx` here fails the test; a `200` here is worse than a failure, because it means the endpoint acks
unsigned bodies.

## 9. Never fulfil from a client redirect

3DS `return_url`, Checkout `success_url` and the `redirect_status` query parameter are **client-controlled and
optional**. The customer closes the tab, the mobile browser drops the deep link, or an attacker requests the
success URL directly, and Stripe is explicit: *"Don't attempt to handle order fulfillment on the client
side."*

The redirect landing page is allowed to be a *trigger*, on exactly the same terms as a webhook: re-read the
object from the API and run the same admit-and-apply path, sharing the code with the inbox processor. It is
never a second code path, and it never writes a fulfilment or a ledger entry from the query string.

For an asynchronous method (SEPA Direct Debit, ACH Direct Debit, Bacs, most bank redirects), the customer
reaches the success page while the PaymentIntent is still `processing`. A success page that fulfils has
shipped goods for a payment that has not happened yet and may never.

## 10. The sweeper

A webhook that is never delivered produces no error anywhere. The self-healing job is two loops, and it is a
**scheduled entrypoint in production**, not a script (*reconciliation runs in production*).

**Loop A: gap fill from the event stream.** Page `stripe.Event.list(created={"gte": cursor - overlap},
limit=100)` with `starting_after`, inserting each event into the same inbox with `source='sweeper'`; the
unique index makes overlap free. Use an overlap of at least the tolerance window, because `created` is a
second-granularity clock and a cursor set to the exact last `created` skips events sharing that second, the
same failure as §6, one layer up.

**Advance the cursor only over a range you verifiably covered, which is *proven coverage before the
cursor advances*:** a page that comes back with
`has_more = true`, an API error mid-page, or a count at the documented cap is a hole, not an end. Advance
inside the same transaction that inserted the page, never in a `finally`.

**Loop B: re-read your own non-terminal set.** Loop A cannot help once events age out of the list window.
Select your own rows in a non-terminal state (`pending`, `processing`, `requires_capture`, refunds not yet
`succeeded`/`failed`, disputes not yet closed) older than a threshold, and re-read each one from the API
through the same admit-and-apply path. This loop has no dependency on the event stream at all, and it is the
one that survives a multi-day outage. It is the same shape as freqtrade's `manage_open_orders`, which fetches
the order from the venue before deciding anything (`freqtrade/freqtradebot.py:1613`).

Loop B is also the only thing that finds the charge Stripe's back office rolled forward after a cached `500`,
which *"surfaces only via webhook"*, and therefore not at all if that webhook was the one you dropped.

## 11. Retry horizons and what expires

| clock | value | source |
|---|---|---|
| Stripe webhook retries, live mode | up to **3 days**, exponential backoff | Stripe webhooks doc |
| Stripe webhook retries, sandbox | 3 attempts over a few hours | Stripe webhooks doc |
| Stripe manual resend | **15 days** (Dashboard) / **30 days** (CLI) | Stripe webhooks doc |
| Stripe signature tolerance | **5 minutes** default; never `0` | Stripe webhooks doc |
| Adyen ack deadline | **10 seconds**, then marked *Failing* and queued for retry | Adyen handle-webhook-events |
| Adyen total retry horizon | **not established**: no primary page in this repository's research states it. Do not code against a number | n/a |
| Stripe idempotency-key retention | ≥ **24 hours**, then a reused key produces a *new request* | Stripe idempotent requests |
| Adyen idempotency-key retention | ≥ **7 days**, scoped to the company account, not checked across regions | Adyen API idempotency |

The two clocks that must not be confused: the **webhook** horizon is days-to-weeks, the **idempotency-key**
horizon is hours-to-days. Any design where a redelivered webhook re-issues a processor call under the original
idempotency key is relying on a key that has already been pruned, and the call executes for real.

Event payloads are frozen at the account's API version *at event time* and are never updated. Combine that
with a 3-day retry horizon and a 30-day manual resend, and the payload you process can be arbitrarily stale
and rendered against an API version you have since migrated off. This is the whole argument for §7's rule that
`raw_body` is evidence, not input.

## 12. Reading the object from its authority

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
