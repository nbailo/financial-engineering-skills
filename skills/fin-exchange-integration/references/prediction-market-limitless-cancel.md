# Limitless Exchange: cancel, cancel-replace and maintenance mode

> **Provenance**
> provider: Limitless Exchange · surface: REST cancel, batch cancel, cancel-replace and the maintenance-status endpoint · chain: Base, chainId 8453
> version: the docs publish no API version number. The changelog records all four official SDKs at v1.1.0 on 2026-08-12, restated from this block's own sources.
> verified_at: 2026-08-25
> sources: every path below is under `https://docs.limitless.exchange/`, enumerated by the docs index at
> `https://docs.limitless.exchange/llms.txt`. `/api-reference/trading/`: `cancel`, `batch-cancel`, `cancel-replace`,
> `create-order`. Also `/developers/maintenance-mode` and `/changelog`.
> pinned: none. No versioned client artefact is cited here. Every statement below is read off the documentation pages named, never off SDK source.
> verified: single cancel taking exactly one of `orderId` or `clientOrderId` and returning 400 for both or neither, its `400` on an already-filled or non-cancellable order and its `404` on an unknown id, with both messages quoted; batch cancel's exactly-one-non-empty-array rule, its 1-to-50 bounds, its `200`, `207 Multi-Status` and `400` outcomes and the shape of `canceled` and `failed`; cancel-replace's documented non-atomicity, quoted, its `cancel.status` and `replacement.status` value sets, the `409` on a successful cancel with a failed replacement, the `mode` values, and the new-`clientOrderId` and `replacement.ownerId` requirements; the maintenance endpoint being unauthenticated, its `active` and `scheduled` arrays and their fields, the `425 Too Early` with a body `code`, the four modes and what each permits, and the documented instruction to stop retrying and refresh.
> unverified: whether a status read is guaranteed to reflect an order the API has just accepted, so whether a `404` from cancel is durable.
> revalidate_when: any changelog entry touching cancel, batch cancel or cancel-replace; a new maintenance mode or a new `code` value; the cancel page documenting a read-after-write guarantee; an SDK minor release. The changelog ships multiple entries per week, so treat anything hard-coded from this file as stale after a month.

**Scope.** Withdrawing an order, replacing one, and the venue states in which you cannot do either. Market discovery and
authentication, order signing, submission and recovery, the event stream, and the book, fees and portfolio reads each have
their own reference.

## Contents

- Cancel, batch cancel and cancel-replace: three different failure shapes
- Maintenance mode, and why 425 means two different things
- Required assertions

## Cancel, batch cancel and cancel-replace: three different failure shapes

**Single cancel** is `POST /orders/cancel` with exactly one of `orderId` or `clientOrderId`; both or neither returns 400.
Cancelling here is **not** a silent no-op. An already-filled or non-cancellable order returns `400 Bad Request` with
"Order not found or already canceled", and an unknown id returns `404` with "No order resolves from the supplied internal
or client order ID". A generic client that treats a cancel error as a hard failure and stops will strand an unresolved
intent; one that treats every cancel error as success will report a resting order as cancelled. Branch on the two codes
and re-query. **Whether a status read is guaranteed to reflect a write the API has just accepted is UNVERIFIED**, so a
`404` is not durable evidence: re-query across a short window you declare as config before concluding an order never
existed.

**Batch cancel** is `POST /orders/cancel-batch` with exactly one non-empty array of `orderIds` (1-50 UUIDv4) or
`clientOrderIds` (1-50, each at most 128 characters). It reports partial success: `200 OK` for all cancelled,
**`207 Multi-Status` for some cancelled and some failed**, `400` for none cancelled. The response carries `canceled` and
`failed`, where for client-id requests `failed` is keyed by `clientOrderId` and "includes `orderId` only if resolution
succeeded before cancellation failed". A client that checks only `response.ok` treats a 207 as a full cancel and leaves
live orders it believes are gone.

**Cancel-replace** is `POST /orders/cancel-replace`, and its most important documented property is that it does not do
what its name suggests: "The two actions are **not atomic**: cancellation and replacement have independent outcomes, and a
successful cancellation does not guarantee a successful replacement." `cancel.status` is `SUCCESS`, `FAILURE` or
`UNKNOWN`; `replacement.status` adds `NOT_ATTEMPTED`. A successful cancel with a failed replacement returns `409` and
leaves you flat when you intended to be quoted, which for a market maker is a silent inventory and obligation change. The
`mode` field (`STOP_ON_FAILURE` or `ALLOW_FAILURE`) chooses which risk you take, so choose it explicitly and assert the
choice in a test. The replacement must carry a new `clientOrderId`, and `replacement.ownerId` must own the cancelled
order.

## Maintenance mode, and why 425 means two different things

`GET /maintenance/status` requires no authentication and returns `active` and `scheduled` arrays, each entry carrying
`startsAt`, `endsAt`, `publicMessage` and `effects`. Trading endpoints return `425 Too Early` when maintenance blocks an
action, with a `code` in the body such as `"post_only_mode"`, `"cancel_only_mode"` or `"trading_disabled"`.

The documented modes and what each permits: `normal` allows create, post-only create and cancel; `post_only` blocks
create but allows post-only create and cancel; `cancel_only` blocks both create paths and allows cancel; `disabled`
blocks all three, including cancel.

Two consequences. First, `425` is overloaded: on `POST /orders` it is either a receive-window rejection or maintenance,
and the two demand opposite responses. Re-signing a fresh order is correct for the receive window and pointless during
maintenance. Read the `code` field before deciding. The documented instruction on maintenance is to "stop retrying
immediately, refresh status, resume only when mode permits". Second, `disabled` blocks cancel, which means the venue can
enter a state in which your resting orders cannot be withdrawn by you. That is a real exposure, not a transient error,
and the only pre-trade control that bounds it is the size you were willing to leave resting. The page also notes that for
delayed taker orders you should "continue monitoring events if `eligibleAt` passes during active maintenance".

## Required assertions

Each of these is a test in the repository's own framework, before live keys, and each fails today if the corresponding
control is absent.

1. **A `425` branches on `code`.** Assert that a receive-window `425` re-signs a fresh order, that a maintenance `425`
   stops and refreshes `GET /maintenance/status`, and that `cancel_only` and `disabled` gate different operations.
2. **A 207 is not a success.** Assert that a batch cancel returning `207 Multi-Status` leaves the failed ids marked live
   and triggers a re-query, and that a cancel-replace returning `cancel.status: "SUCCESS"` with
   `replacement.status: "FAILURE"` marks the quote withdrawn.
3. **A cancel error is neither success nor a stop.** Assert that a `400` "Order not found or already canceled" and a
   `404` "No order resolves from the supplied internal or client order ID" take different branches, and that both
   re-query rather than concluding the order is gone.
4. **`mode` is chosen explicitly.** Assert which of `STOP_ON_FAILURE` and `ALLOW_FAILURE` the cancel-replace path sends,
   so the risk that choice takes is a decision in a test rather than a library default.
5. **`disabled` is bounded by resting size.** Assert that the pre-trade size cap on resting orders is enforced on the send
   path, because `disabled` blocks cancel and the venue can therefore hold your orders open against your instruction.
