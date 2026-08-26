# Deribit: no client order ID, and the recovery that follows from that

> **Provenance**
> provider: Deribit · surface: order identity and recovery, units on inverse products, post-only repricing, cancel-on-disconnect and the heartbeat, and book sequencing
> version: as stated in this file's own body, the 2026-08-24 research pass. No API version was recorded.
> verified_at: not established
> sources: https://docs.deribit.com/api-reference/trading/private-buy
> verified: none in this pass. No sentence below was re-read against a source for the 2026-08-25 review pass.
> unverified: all of it. This file's material predates the provenance requirement and was not re-checked in the 2026-08-25 review pass, so its claims carry the confidence of their original sourcing and no more, with no date you can check. The body already carries its own list of what the original research did not establish, and that list is still the right one to read before asserting anything from here; this block adds only that nothing on the rest of the file has been re-read since. The URL above is where a recheck starts; it resolved on 2026-08-25, and nothing in it was read against a claim in this file.
> revalidate_when: Deribit adds a uniqueness check to `label`, changes the `post_only` default on any order endpoint, or changes the triggers that fire cancel-on-disconnect.

Deribit has no client order ID at all. `label` is a free tag with no collision check, so reuse silently creates a second
order and the safe response to an ambiguous submission is a query by label, never a resend. Two further behaviours change
what a generic client does here: `post_only` defaults to `true` on `private/buy` and silently reprices rather than
rejecting, and the venue cancels your orders on triggers your shutdown path may not include. Facts are as of the
2026-08-24 research pass.

## Contents

- The identity model, and where it sits against a venue with idempotent replay
- There is no client order ID: `label` ≤64 chars with no uniqueness check
- The recovery algorithm by label plus instrument plus time
- Units: `amount` in USD on inverse and perpetual products
- `post_only` defaulting to `true` and silently repricing
- `enable_cancel_on_disconnect` scope, and the three triggers `private/logout` is not one of
- The heartbeat and the credit bucket as order-cancellation mechanisms
- JSON `number` as a transport encoding only
- Book: `change_id`
- Recovery endpoints and time bounds
- What the research does not establish and must not be asserted

---

## The identity model

| | Deribit |
|---|---|
| Field | `label` (optional tag) |
| Length / charset | **≤64 chars** |
| Venue-enforced uniqueness | **none** (OpenAPI schema carries `"uniqueKey": ""`) |
| Behaviour on reuse | creates a **second order**, silently |
| Class | C: no collision check |
| Query by it? | `get_open_orders_by_label` / `get_order_state_by_label` |
| The real replay guard | none: you must make the label unique by construction |
| Safe ambiguous-submit action | query by label; never resend under the same label |

One venue in this suite sits in class A, idempotent replay, where a duplicate client order id returns the original order
and the re-POST is the supported query. That branch is wrong here and produces a second position.

Source: <https://docs.deribit.com/api-reference/trading/private-buy>.

---

## Deribit

### There is no client order ID

> "`label`: **user defined label for the order (maximum 64 characters)**"

The schema field carries `"uniqueKey": ""`, no uniqueness constraint. The API concedes collisions
itself: `private/edit_by_label` **"works only when there is exactly one open order with the specified label"**,
and `private/cancel_block_rfq_quote` notes **"Mass cancellation by label is not supported."**

The consequence is the whole rule: **the venue will not stop you reusing a label, so reuse silently creates a
second order.** Uniqueness is your responsibility, enforced at construction:

```python
label = f"{strategy_id}-{uuid4().hex}"   # ≤64 chars, asserted at construction. One label per LOGICAL order,
assert len(label) <= 64                  # never per attempt, never derived from (strategy, side, bar_ts).
```

### The recovery algorithm

ID *equality* is unavailable, but a label that is unique by construction gives you an equality-shaped **query**.
The steps, in order, stopping at the first definite answer:

1. `private/get_open_orders_by_label`, scoped by currency + label.
2. `private/get_order_state_by_label`, for an order that may already have left the open set.
3. Order history for the instrument over `[sent_at − skew, now]`.
4. **User trade history** for the instrument over the same window. This is the only rung that speaks to
   economic effect: an order can be invisible in every order endpoint and still have moved your position.

```python
def resolve_deribit(rpc, intent):
    # Exact parameter sets must be read from docs.deribit.com; the load-bearing part is the sequence and
    # the collision handling, not the argument spelling.
    args = {"currency": intent.currency, "label": intent.label}
    open_orders = rpc("private/get_open_orders_by_label", args)["result"]
    if len(open_orders) > 1:
        raise LabelCollisionBreak(intent.label, open_orders)   # you already double-sent; gate the instrument
    if open_orders:
        return Resolution.CONFIRMED(open_orders[0])
    state = rpc("private/get_order_state_by_label", args)["result"]
    return Resolution.CONFIRMED(state) if state else scan_trade_history(
        rpc, intent.instrument_name, intent.not_before, intent.not_after)
```

`len(open_orders) > 1` is not a warning. It is proof two orders exist under one label, unrecoverable by label;
fall back to `order_id` and reconcile both.

### Units: `amount` is USD on the inverse products

`amount` is **USD** on perpetual and inverse futures, and base currency on options and linear products;
`contracts` is the alternative field and **the two must agree if both are sent**. The measured bug is a
`/price` that should not be there: on BTC-PERPETUAL, `amount = usd_notional / price` turns a $50,000 order at
104,000 into `0.4808` (forty-eight cents of notional) while `amount = usd_notional` is already correct.
Attach a unit to every size crossing a module boundary (base / quote / contracts / USD-notional) and convert
only inside the venue adapter, from the venue's own `contract_size` in `public/get_instruments`.

### `post_only` defaults to `true` and silently reprices

> "If the new price would cause the order to be filled immediately (as taker), the price will be changed to be
> just below the spread"

`reject_post_only` (default `false`) makes the venue reject instead. So a bot sending a crossing limit price
on Deribit with default parameters gets **no error and no fill at its price**; it gets a resting order at a
price it never chose, and every downstream calculation keyed on `intent.price` is now wrong. Send
`{"post_only": True, "reject_post_only": True}` whenever the strategy depends on its price. Re-verify the
default per endpoint (`private/sell`, `private/edit`) and per SDK: several third-party clients declare
`post_only=False` in their own signatures, changing behaviour by whether the field is sent explicitly.

Handle the three would-cross outcomes as three different states across venues: Deribit **repriced**,
Hyperliquid `Alo` **rejected**, Binance futures `GTX` **cancelled**.

### Cancel-on-disconnect, and the shutdown that does not trigger it

`private/enable_cancel_on_disconnect`: auth scope `account:read_write`, `scope` is `connection` (default) or
`account`; `connection` scope is WebSocket-only, and COD is not supported over HTTP. It fires on (1) proper TCP
termination, (2) a connection closed after **10 minutes of inactivity**, (3) heartbeat-detected disconnection.

It does **not** fire on `private/logout`; Deribit publishes the matrix explicitly: graceful logout leaves
orders live even with COD enabled, so code calling `logout` on `SIGTERM` believing COD will clean up leaves
resting orders working after the process exits. FIX sessions use tag 9001 `CancelOnDisconnect` / tag 9003
`DontCancelOnDisconnect`. Unlike Binance (`countdownTime` in **milliseconds**), Kraken and OKX (**seconds**)
and Bybit (`timeWindow` 3–300 s), **Deribit is connection-scoped with no timer at all**: a cross-venue
`arm_dead_mans_switch(seconds=30)` has no Deribit implementation and silently no-ops.

### The heartbeat is an order-cancellation mechanism

`public/set_heartbeat` (interval ≥10 s) makes the server send `heartbeat` and `test_request`; the client must
answer `test_request` with `/api/v2/public/test`. "If your software fails to do so, the API server will
**immediately close the connection**." With COD enabled, that closure cancels every open order, so on Deribit
a blocking event loop (a synchronous indicator computation, a GC pause, a slow consumer) is an
order-cancellation mechanism. Answer from a task that cannot be blocked by the trading loop, but gate it on
that loop's *liveness*: a heartbeat sent by an independent thread while the strategy is wedged keeps the switch
disarmed exactly when it is needed.

The rate limiter reaches the same outcome by another route. Credits are a leaky bucket: non-matching-engine
default cost 500, cap 50 000, refill 10 000/s ⇒ ~20 rps sustained, ~100 burst; matching-engine requests are
tiered by trailing-7-day volume (>$25M: 30/s burst 100; >$5M: 20/s burst 50; >$1M: 10/s burst 30; ≤$1M: 5/s
burst 20), and `public/get_instruments` alone costs **10 000 credits**. Exceeding returns `too_many_requests`
(**10028**) **and terminates the session**, which, with COD on, cancels your orders. The web UI shares the
pool, so a human refreshing a dashboard can cancel a bot's book.

### JSON `number` is a transport encoding, not the record

Deribit's `private/buy` schema declares **`amount`: JSON type `number`** and **`price`: JSON type `number`**.
RFC 8259 §6 limits interoperable JSON numbers to binary64, so by the time your parser hands you the field the
value was already a float. That does not license floats in your model. Compute and store price and size
**exact** (integer minor units, or `Decimal` built only from strings); convert **once**, at the serialize step,
asserting the encoding round-trips (`Decimal(repr(f)) == exact`); never persist the float as the record,
re-deriving it from Deribit's own execution report; and parse inbound with a decimal-preserving decoder;
`Decimal(str(json_number))` is **not** a fix, because `str()` of an already-damaged double is faithful to the
damage.

### Book

`change_id` / `prev_change_id`. The first `snapshot` has **no** `prev_change_id`; on every subsequent message
`prev_change_id` must equal the last `change_id` applied. A mismatch means messages were missed: discard the
book and re-snapshot, never patch.

## Recovery endpoints and time bounds

| Venue | Endpoint | Accepts the client identifier? | Bound |
|---|---|---|---|
| Deribit | `private/get_open_orders_by_label` | by label | open orders only; >1 result is a break |
| Deribit | `private/get_order_state_by_label` | by label | a collision returns ambiguity |
| Deribit | order / user-trade history by instrument + time | no | window built from your persisted `sent_at` |

---

## Not established by the research: do not assert

- **Deribit `post_only` default per endpoint.** `true` is documented for `private/buy` only, and the default on the other
  order endpoints was not captured.
- **The exact parameter sets** of the methods in the recovery snippet above. The sequence there is load-bearing; the
  argument spelling must come from the current reference.
- **That the `change_id` model transfers.** It is Deribit's, and no other venue in this suite was shown to use it. Do not
  assume a venue with no documented sequencing behaves the same way.
