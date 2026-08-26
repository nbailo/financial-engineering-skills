# Limitless Exchange: the orderEvent stream, its two sources and the reconnect hole

> **Provenance**
> provider: Limitless Exchange · surface: Socket.IO WebSocket event stream and the REST reads that close a gap in it · chain: Base, chainId 8453
> version: the docs publish no API version number. The changelog records all four official SDKs at v1.1.0 on 2026-08-12, restated from this block's own sources.
> verified_at: 2026-08-25
> sources: every path below is under `https://docs.limitless.exchange/`, enumerated by the docs index at
> `https://docs.limitless.exchange/llms.txt`. `/developers/websocket-events`. Also
> `/api-reference/trading/`: `create-order`, `order-status-batch`, `user-orders`. Also `/changelog`.
> pinned: none. No versioned client artefact is cited here. Every statement below is read off the documentation pages named, never off SDK source.
> verified: the `orderEvent` `source` and `type` values with the page's own gloss of `OME` and `SETTLEMENT`; the `STP_MAKER_CANCELLED` and `STP_TAKER_REJECTED` reasons and the three `stpPolicy` values; the `EXECUTION` `status` values and that an `EXECUTION` is promised only for an immediate-or-cancel order; `MATCHED` as provisional with `isEstimate: true` and no `txHash`, `MINED` and `FAILED` as the terminal pair sharing `tradeEventId`; the cross-source ordering statement, quoted; the 60-second sliding dedupe window, quoted; the four timestamp fields and the deprecation of `timestamp`; both subscription statements, that subscriptions replace previous ones and are not persisted across disconnects; the server-run heartbeat and the instruction not to send client PINGs; the `user-orders` `statuses` values, the 1-to-200 `limit` and the documented absence of pagination metadata; the `exception` event carrying a `WsException` and the `authenticated` event.
> unverified: what the 60-second dedupe window is keyed on, and whether it suppresses anything after a reconnect or a restart; whether the WebSocket replays or backfills anything on resubscribe.
> re-read: the settlement surface (`developers/websocket-events`, `api-reference/trading/create-order`, `api-reference/trading/order-status-batch`) was read again on 2026-08-26, and the settlement statements below rest on that read. The rest of this block rests on the 2026-08-25 read, which is why `verified_at` is unchanged.
> revalidate_when: any changelog entry touching WebSocket events or the subscription model; a new `orderEvent` `type` or `source`; the page documenting a replay or backfill on resubscribe; an SDK minor release. The changelog ships multiple entries per week, so treat anything hard-coded from this file as stale after a month.

**Scope.** Consuming the authenticated `orderEvent` stream, folding its two sources into one state, and rebuilding after a
disconnect. Market discovery and authentication, order signing, submission and recovery, cancellation, and the book, fees
and portfolio reads each have their own reference.

## Contents

- OME versus SETTLEMENT: two sources, no ordering, one provisional frame
- Reconnect and resubscription, and the gap nobody fills for you
- Required assertions

## OME versus SETTLEMENT: two sources, no ordering, one provisional frame

The authenticated `orderEvent` stream carries a `source` discriminator with exactly two values.

- `source: "OME"`, glossed by the page as "off-chain matching engine updates: resting-order state changes
  (`PLACEMENT`, `UPDATE`, `CANCELLATION`) and the terminal result of an immediate-or-cancel order (`EXECUTION`)". A
  `CANCELLATION` may carry `reason: "STP_MAKER_CANCELLED"`, the self-trade-prevention outcome; the `stpPolicy` you sent
  on `POST /orders` (`cancel_maker`, `cancel_taker`, `cancel_both`) decides which of your own orders dies, and the taker
  side surfaces as `reason: "STP_TAKER_REJECTED"`. An `EXECUTION` carries `status` in `FILLED`, `PARTIALLY_FILLED` or
  `KILLED`, and all three are documented against FAK and FOK outcomes. Do not wait for an `EXECUTION` to learn that a
  resting order filled: the page promises one only for an immediate-or-cancel order.
- `source: "SETTLEMENT"`, glossed as the "settlement lifecycle for CLOB trades: a provisional `MATCHED` the moment the
  engine fills your order (before the on-chain transaction), then a terminal `MINED` or `FAILED`". On a `MATCHED`,
  "`isEstimate: true`, there is no `txHash` yet, and the fill can still be rolled back by a later `FAILED`".

Three rules follow directly from the documented text.

**Do not order the two sources.** "OME and SETTLEMENT events for the same order can arrive in either order within a few
seconds." Your legality table is therefore over the union of both event types against your own state, with an explicit
reject arm rather than a silent ignore, and it must accept a settlement frame arriving before the matching frame that
"caused" it. Model the two as separate columns of one row keyed on `orderId`, not as one linear status field, because a
single status enum forces you to pick a winner between two frames that are both true.

**Do not credit an estimate as cash.** A `MATCHED` frame with `isEstimate: true` and no `txHash` is a claim that a match
occurred, not that value moved. `MINED` and `FAILED` are the terminal pair on this source: the page says a `MATCHED`
"and its terminal `MINED` / `FAILED` share the same `tradeEventId`", which is the join key. If a fill is going to become
a ledger posting, the posting waits for the terminal frame or is written in a provisional state that a later `FAILED`
reverses by a new entry rather than by editing the original. Treating `MATCHED` as final is how a `FAILED` becomes a
phantom position that reconciliation later reports as a break with no cause.

**Do not rely on the dedupe window.** The page says: "Repeated emissions within a 60-second sliding window are dropped, so
retries and replays will not double-deliver." That is a 60-second window and nothing more. It is not a durable
guarantee, it says nothing about a redelivery 61 seconds later, and it cannot survive your own restart. **What the
window is keyed on is UNVERIFIED**: the page does not say whether a redelivery after a reconnect is suppressed at all,
so do not let a reconnect count as protected. Your dedupe is the `eventId` you persisted, in the same transaction as the
state it protects. An in-memory set re-applies every counted fill after a restart, and the
60-second window will not save you because your restart took longer than that.

Timestamps are four distinct quantities: `occurredAt` (lifecycle fact time), `matchedAt` (persisted match time, on
settlement frames), `publishedAt` (gateway queue time), and `timestamp`, which the page marks deprecated legacy. Age and
arrival order are different guards. Compute staleness against `occurredAt`, and never against `publishedAt`, which
measures the gateway rather than the fact.

## Reconnect and resubscription, and the gap nobody fills for you

Two documented statements generate most of the client bugs on this surface.

"Subscriptions **replace** previous ones." A second `subscribe_market_prices` does not add to the first, it supersedes
it. The page's own worked warning: if you want both AMM prices and the CLOB orderbook, send `marketAddresses` and
`marketSlugs` **together in one call**. Any code that subscribes incrementally as markets are discovered silently
unsubscribes everything it was already watching, and the symptom is not an error, it is a book that stops updating for
every market except the last one added. Keep the full desired subscription set in one variable and re-send the whole set
on every change.

"Subscriptions are not persisted server-side across disconnects." The resubscription therefore belongs in the `connect`
handler, not in your startup path, because `connect` fires again after every reconnect and startup does not.

The venue runs the heartbeat: "**No client PING required.** The server runs the Socket.IO heartbeat (server-initiated
`ping` / client `pong`) automatically." Sending your own PING frames is documented as something clients must not do.

What no documented mechanism gives you is the gap. Reconnecting restores the stream, not the events that happened while
you were gone, and **whether any replay or backfill occurs on resubscribe is UNVERIFIED**. Treat a disconnect as a hole
and close it through REST before acting on local state: `GET /markets/:slug/user-orders` for the live set (the page
states that "If `statuses` is omitted, the API returns live orders", that `statuses` accepts `LIVE`, `MATCHED`,
`CANCELED` and `UNMATCHED`, that `limit` runs 1 to 200, and that "The endpoint has no cursor, page, total, or other
pagination metadata"), then `POST /orders/status/batch` for anything you hold an identity for but did not see terminate.
The absence of pagination metadata is itself a hazard: a result at exactly `limit` is a hole, not the end of the list.
Nothing may act on local state until that rebuild completes.

Authentication failure on the socket surfaces on the `exception` event as a `WsException`, and no `orderEvent` frames
arrive at all. A client that only watches for events sees silence, which is indistinguishable from a quiet market. Assert
on the `authenticated` event before you consider the stream healthy.

## Required assertions

Each of these is a test in the repository's own framework, before live keys, and each fails today if the corresponding
control is absent.

1. **Events converge under shuffling.** Replay a stream of `orderEvent` frames in arrival order, then reversed,
   duplicated, and interrupted by a simulated restart, and assert every run converges on the same state once each fold
   sorts into the venue's canonical economic order, and that `eventId` dedupe is read from storage rather than memory.
   Include a `SETTLEMENT MATCHED` arriving before its `OME EXECUTION`, and a `FAILED` after a `MATCHED` that was already
   booked.
2. **A `MATCHED` is not cash.** Assert that a frame carrying `isEstimate: true` and no `txHash` produces no booked
   posting, or a provisional one that a later `FAILED` reverses by a new entry rather than by editing the original, and
   that the reversal joins on `tradeEventId`.
3. **Staleness is measured against `occurredAt`.** Assert that the age guard reads `occurredAt` and never `publishedAt`,
   and that arrival order is a separate guard from age.
4. **Reconnect closes the hole.** Assert that the `connect` handler re-sends the complete subscription set in one call
   per subscription type, that no local state is acted on until the REST rebuild completes, and that a `user-orders`
   result of exactly `limit` items is treated as a hole rather than the end.
5. **Silence is not health.** Assert that the client requires the `authenticated` event before it considers the stream
   healthy, and that a `WsException` on the `exception` event fails the readiness gate rather than being logged.
