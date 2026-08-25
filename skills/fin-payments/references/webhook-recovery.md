# Webhook recovery

Delivery is at-least-once, and it stops after a few days. The events that never arrived are invisible until
a job goes looking, so this file carries the two sweeper loops, the cursor rule that keeps a truncated page
from becoming a silent hole, and the clocks that decide how long you have: the delivery horizon, the manual
resend window, and the idempotency-key retention that is shorter than both.

## Contents

- The sweeper: listing processor objects changed since a cursor so a dropped event self-heals
- Retry horizons and what expires: Stripe 3 days, Adyen queued-and-failing behaviour

---

## The sweeper

A webhook that is never delivered produces no error anywhere. The self-healing job is two loops, and it is a
**scheduled entrypoint in production**, not a script (*reconciliation runs in production*).

**Loop A: gap fill from the event stream.** Page `stripe.Event.list(created={"gte": cursor - overlap},
limit=100)` with `starting_after`, inserting each event into the same inbox with `source='sweeper'`; the
unique index makes overlap free. Use an overlap of at least the tolerance window, because `created` is a
second-granularity clock and a cursor set to the exact last `created` skips events sharing that second, the
same off-by-one a watermark makes when it advances to a timestamp that unread rows also carry.

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

## Retry horizons and what expires

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
and rendered against an API version you have since migrated off. This is the whole argument for treating
`raw_body` as evidence, not input.
