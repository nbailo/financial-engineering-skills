# Webhook endpoint

The receive path, which does three things: verify the bytes, insert one row, return. Covers the signature
check over the raw unparsed body, the ack deadline that forces the work out of the handler, the inbox row a
2xx promises, and the delivery-killing traps that live in framework and CDN configuration.

## Contents

- Signature verification over the raw unparsed body; the `v1` scheme; replay tolerance; Adyen HMAC
- Ack-then-work: Stripe's "2xx prior to any complex logic", Adyen's 10-second timeout and `[accepted]`
- The inbox table: raw signed event stored, acked, processed asynchronously; why a 2xx asserts nothing
- 3xx responses kill delivery: http→https, trailing-slash, and CDN-introduced redirects
- Never fulfil from a client redirect: 3DS `return_url`, checkout success pages

---

## Signature verification over the raw unparsed body

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
The field that orders events is the event's own timestamp, never the signature timestamp.

Adyen signs each notification item, not the request: the HMAC arrives in that item's
`additionalData.hmacSignature`, so a request carrying several `notificationItems` is validated **item by
item**, and one unsigned item does not invalidate the others. Use the SDK's `HmacValidator`
(`Adyen::Utils::HmacValidator`, `com.adyen.util.HmacValidator`, `@adyen/api-library`'s `hmacValidator`) rather
than reimplementing the concatenation: *the exact field order of Adyen's signed payload is not established by
this repository's research; do not hand-roll it from memory.* Adyen additionally supports HTTP basic auth on
the webhook endpoint; that is a second, independent check, not a substitute.

## Ack-then-work

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
@app.post("/webhooks/stripe")                       # exact path, no trailing slash; see below
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

## The inbox table

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

## 3xx responses kill delivery

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

## Never fulfil from a client redirect

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
