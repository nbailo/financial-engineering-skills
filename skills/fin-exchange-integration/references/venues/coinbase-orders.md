# Coinbase Advanced Trade: order identity and recovery

> **Provenance**
> provider: Coinbase Advanced Trade, with one contrast to Coinbase Exchange (legacy) · surface: order identity and recovery, the deprecated lookup path, and per-product feed sequencing
> version: as stated in this file's own body, the 2026-08-24 research pass. No API version was recorded.
> verified_at: not established
> sources: https://docs.cdp.coinbase.com/api-reference/advanced-trade-api/rest-api/orders/create-order · https://docs.cdp.coinbase.com/coinbase-app/advanced-trade-apis/websocket/websocket-overview
> verified: none in this pass. No sentence below was re-read against a source for the 2026-08-25 review pass.
> unverified: all of it. This file's material predates the provenance requirement and was not re-checked in the 2026-08-25 review pass, so its claims carry the confidence of their original sourcing and no more, with no date you can check. The body already carries its own list of what the original research did not establish, and that list is still the right one to read before asserting anything from here; this block adds only that nothing on the rest of the file has been re-read since. The URLs above are where a recheck starts; each of them resolved on 2026-08-25, and nothing in any of them was read against a claim in this file.
> revalidate_when: Coinbase removes or restores the lookup by client order id, changes what a replayed create returns, publishes a rate-limit shape, or changes the cancel-on-disconnect behaviour of the Advanced Trade websocket.

Coinbase Advanced Trade is the **only** venue in scope with true create-order idempotency: a duplicate `client_order_id`
returns the original order and creates nothing. Because the lookup-by-client-id path is deprecated, the re-POST *is* the
query, which inverts the usual rule that a retry is the dangerous branch. Facts are as of the 2026-08-24 research pass.

## Contents

- The identity model, and where it sits against the venues that have no collision check
- Replay returns the original order; why re-POST is the only supported query; the byte-identical-body requirement
- The unreconciled `DUPLICATE_CLIENT_ORDER_ID`
- The paginated time-window scan that replaces the missing client-order-id filter
- Legacy `client_oid`'s 404, which is a different guarantee
- Per-product `sequence_num` and in-band snapshots
- Recovery endpoints and time bounds
- What the research does not establish and must not be asserted

---

## The identity model

| | Coinbase Advanced Trade |
|---|---|
| Field | `client_order_id` (required) |
| Length / charset | string; charset not published |
| Venue-enforced uniqueness | account-scoped |
| Behaviour on reuse | **returns the original order; creates nothing** |
| Class | A: idempotent replay |
| Query by it? | **deprecated** on Get Order; absent from List Orders |
| The real replay guard | the `client_order_id` itself |
| Safe ambiguous-submit action | **re-POST the identical create-order** |

Two venues in this suite sit in the opposite class, C, with no collision check at all: reuse there creates a second order
rather than returning the first, and the safe action is a query rather than a resend. Do not carry a Coinbase retry branch
to either of them.

Source: <https://docs.cdp.coinbase.com/api-reference/advanced-trade-api/rest-api/orders/create-order>.

---

## Coinbase Advanced Trade

### Replay returns the original order

> "`client_order_id` string **required**: A unique ID provided for the order. **If the ID provided is not
> unique, the order will not be created and the order corresponding with that ID will be returned instead.**"
> Source: Create Order reference

The only class-A guarantee in the venue set: a blind retry is safe **and** it answers "did my first attempt
create an order?".

### The query path is being removed, so the retry *is* the query

- `GET /api/v3/brokerage/orders/historical/{order_id}` takes `order_id` as a **required path parameter**, and
  its `client_order_id` query parameter is marked **"(Deprecated)"**.
- `GET /api/v3/brokerage/orders/historical/batch` (List Orders) has **no client-order-id filter at all**. Its
  filters are `order_ids`, `product_ids`, `product_type`, `order_status`, `order_side`, `start_date`,
  `end_date`, `asset_filters`, `limit`, `cursor`.

So "query before you retry" is **not implementable here**. Invert it: re-POST.

```python
# The Coinbase recovery path. The retry is the query; the body must never change between attempts.
def submit_coinbase(http, intent, budget_s: float) -> Resolution:
    body = {
        "client_order_id": intent.client_order_id,   # minted ONCE per logical order, fsync'd before the
                                                     # first send, reused verbatim on every attempt
        "product_id":      intent.product_id,
        "side":            intent.side,
        "order_configuration": {"limit_limit_gtc": {
            "base_size": str(intent.size),           # decimal strings, never repr(float)
            "limit_price": str(intent.price), "post_only": True}},
    }
    payload = json.dumps(body, sort_keys=True)       # frozen: a changed body is a DIFFERENT order
    deadline = time.monotonic() + budget_s
    while time.monotonic() < deadline:
        try:
            r = http.post("/api/v3/brokerage/orders", data=payload).json()
        except (Timeout, ConnectionError, HTTP5xx):
            continue                                 # SAFE: the replay is idempotent by contract
        if r.get("success"):
            got = r["success_response"]              # envelope field names: verify against the current ref
            # The returned order may be the ORIGINAL, not this attempt. Prove it is the order you meant.
            assert_matches_intent(got, intent)       # product, side, size, price, config; all seven fields
            return Resolution.CONFIRMED(got)
        reason = r["error_response"].get("new_order_failure_reason")
        if reason == "DUPLICATE_CLIENT_ORDER_ID":    # contradicts replay-returns-original (see below), but
            return resolve_by_scan(http, intent)     # still PROOF the order exists; never re-mint the ID
        return Resolution.REJECTED(reason)           # INSUFFICIENT_FUND / PRODUCT_TRADING_HALTED /
                                                     # UNSUPPORTED_ORDER_CONFIGURATION = definitely not created
    return Resolution.INFLIGHT_UNKNOWN               # worst-case exposure in risk; gate; escalate
```

Two things that code encodes and prose loses. **The body is frozen with the ID**: the replay returns whatever
order that ID names, so a re-POST with a different price is not a retry and carries no guarantee about which
body won; persist the exact payload alongside the ID. And **verify the returned order against your intent**:
replay-returns-original means the response can describe an order you sent minutes ago with different terms if
your ID minting ever repeats. `assert_matches_intent` is what turns the guarantee into a proof.

### The unreconciled `DUPLICATE_CLIENT_ORDER_ID`

`NewOrderFailureReason` enumerates `DUPLICATE_CLIENT_ORDER_ID` alongside `INSUFFICIENT_FUND`,
`PRODUCT_TRADING_HALTED` and `UNSUPPORTED_ORDER_CONFIGURATION`. That cannot be reconciled with
replay-returns-original in all cases. **Whether the failure reason applies only to batch/preview paths, or is
legacy from Coinbase Exchange/Pro, is unresolved and was not settled by an integration test in the research
pass.** Handle both outcomes, as the code above does. Also unverified: the **retention window** of the
idempotency guarantee; the doc states the behaviour with no expiry, and "behaves as unbounded" is an
observation, not a published policy. Do not build a day-boundary ID scheme that depends on it.

### When the replay itself cannot resolve it

List Orders cannot filter on `client_order_id`, so the fallback is a paginated time-window scan matched
locally, which is why a `not_before`/`not_after` bracket is persisted before the send.

```python
def resolve_by_scan(http, intent):
    cursor, hits = None, []
    while True:                                      # paginate to exhaustion; one call truncates the gap
        page = http.get("/api/v3/brokerage/orders/historical/batch", params={
            "product_ids": intent.product_id,
            "start_date": iso(intent.not_before),    # bracket the send; do not scan "today"
            "end_date": iso(intent.not_after), "limit": 100,
            **({"cursor": cursor} if cursor else {})}).json()
        hits += [o for o in page["orders"] if o["client_order_id"] == intent.client_order_id]
        cursor = page.get("cursor")
        if not page.get("has_next"):
            break
    if len(hits) > 1:
        raise DuplicateOrderBreak(intent.client_order_id, hits)   # gate the product; do not pick one
    if hits:
        return Resolution.CONFIRMED(hits[0])
    # Order endpoints can be empty while the position moved. Fills are the only ground truth.
    return check_fills(http, "/api/v3/brokerage/orders/historical/fills", intent)
```

### Coinbase Exchange (legacy) `client_oid` is a different guarantee

The older Exchange/Pro API *does* support lookup (`client_oid` "must be preceded by the `client:`
namespace") and publishes its failure mode: **"If the order is canceled, and if the order had no matches, the
response might return the status code 404"**
(<https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/orders/get-single-order>). That is a venue
stating in its own documentation that **404 does not mean "never existed"**. Do not port Advanced Trade's
replay assumption onto this API, and do not port this API's 404 handling onto Advanced Trade.

### Feed

`sequence_num` is **per-product** and increments by exactly 1: a gap means messages were dropped; a value lower
than the previous means out-of-order delivery and may be ignored. Coinbase says why the transport is not
enough: "even though a WebSocket connection is over TCP, the WebSocket servers receive market data in a manner
that can result in dropped messages"
(<https://docs.cdp.coinbase.com/coinbase-app/advanced-trade-apis/websocket/websocket-overview>). Coinbase
delivers **in-band** snapshots, unlike Binance spot/futures where a gap forces a REST re-fetch, so a shared
`on_orderbook_message` handler written against Binance mishandles them. On a gap, discard the book and take
the next snapshot; never patch. The measured client-side failure here is hummingbot#7211, "orders are failing on hummingbot but successfully
posted on exchange", a `KeyError: 'order_id'` on the submit path. The venue accepted the orders; an unguarded
field access marked them FAILED locally. An exception raised *after the request left the process* moves the
order to UNKNOWN, never to FAILED.

## Recovery endpoints and time bounds

| Venue | Endpoint | Accepts the client identifier? | Bound |
|---|---|---|---|
| Coinbase AT | `POST /api/v3/brokerage/orders` (identical body) | **the replay is the query** | none published; behaves as unbounded, **unverified** |
| Coinbase AT | `GET /orders/historical/{order_id}` | `client_order_id` **deprecated** | requires the venue's `order_id` |
| Coinbase AT | `GET /orders/historical/batch` + `cursor` | **no filter** | `start_date`/`end_date`; match locally, paginate to exhaustion |
| Coinbase AT | `GET /orders/historical/fills` | no | the only rung proving economic effect |
| Coinbase Exchange | Get Single Order | yes, `client:{client_oid}` | **404 for cancelled-with-no-fills**: not proof of non-creation |

---

## Not established by the research: do not assert

- **Coinbase Advanced Trade rate-limit shape** (per-endpoint quotas, headers, ban ladder). No primary source captured;
  build the limiter from the live documentation, not from this file.
- **Coinbase idempotency retention** and the `DUPLICATE_CLIENT_ORDER_ID` contradiction. Both unresolved.
- **That the `sequence_num` model transfers.** It is per product here, and no other venue in this suite was shown to use
  it. Do not assume a venue with no documented sequencing behaves the same way.
