# Limitless Exchange: public client integration

> **Provenance**
> provider: Limitless Exchange · surface: REST + Socket.IO WebSocket + EIP-712 CTF order signing
> · chain: Base, chainId 8453
> version: the docs publish no API version number. The EIP-712 signing domain is version "1"; the changelog
> records all four official SDKs at v1.1.0 on 2026-08-12. Both are restated from this block's own sources.
> verified_at: 2026-08-25
> sources: every path below is under `https://docs.limitless.exchange/`, enumerated by the docs index at
> `https://docs.limitless.exchange/llms.txt`. `/developers/`: `introduction`, `authentication`, `venue-system`,
> `eip712-signing`, `maintenance-mode`, `websocket-events`, `migrate-from-polymarket`, `quickstart/python`.
> `/api-reference/trading/`: `create-order`, `order-status-batch`, `cancel`, `batch-cancel`, `cancel-replace`,
> `user-orders`, `orderbook`. Also `/api-reference/markets/get-market`, `/api-reference/portfolio/` `get-profile`,
> `positions`, `redeem`, `withdraw`, `/user-guide/fees`, `/user-guide/negrisk-overview`, `/changelog`.
> pinned: none. No versioned client artefact is cited here. Every statement below is read off the documentation pages
> named, never off SDK source. The changelog records all four official SDKs at v1.1.0 on 2026-08-12.
> verified: base URLs and chain; the three authentication modes and the five token scopes; the HMAC canonical message
> and its three headers; the EIP-712 domain and the twelve-field Order struct; the zero constraint on `expiration` and
> `nonce`; `side` and `signatureType` encodings; 1e6 amount scaling and the FOK `takerAmount` convention; `venue.exchange`
> as `verifyingContract` and `venue.adapter` for Neg Risk SELL approvals; `clientOrderId` as an optional 128-character
> dedupe key and the 409 on reuse; the batch order-status endpoint and its one-identifier-per-item rule; the
> `settlementStatus` enum as a list of values; cancel, batch cancel and cancel-replace semantics including
> non-atomicity and 207; the maintenance modes and the 425; the `orderEvent` `source` and `type` values with the page's
> own gloss of `OME` and `SETTLEMENT`, the `eventId` model, `MATCHED` as provisional, `MINED` and `FAILED` as the
> terminal pair sharing `tradeEventId`, and `DELAYED` with its `eligibleAt`; the 60-second dedupe window; the
> cross-source ordering statement; both subscription statements; the merged YES-side orderbook; the market `status` enum
> and the CLOB versus AMM price scale; redemption preconditions; the withdrawal allowlist rule; the absence of a sandbox.
> unverified: tick size, minimum order size and rate limits; whether the create-order response carries a top-level venue
> order id; how the user-guide fee curve relates to the signed `rank.feeRateBps` and the response `effectiveFeeBps`;
> whether a status read is guaranteed to reflect an order the API has just accepted, so whether a 404 from cancel is
> durable; the meaning of `RETRYING` and of `CONFIRMED`, and where `CONFIRMED` sits relative to `MINED`; whether
> `eligibleAt` equals the submit time plus `settings.takerDelayMs`; what the 60-second dedupe window is keyed on; the
> full field list of the positions and history endpoints; whether redeem is idempotent;
> whether the WebSocket replays anything on resubscribe; orderbook `size` units, any sequence anchor joining a snapshot
> to `orderbookUpdate`, and the difference between `midpoint` and `adjustedMidpoint`; the `CREATED` market status. Each
> is labelled again inline where it matters.
> re-read: the settlement surface (`developers/websocket-events`, `api-reference/trading/create-order`,
> `api-reference/trading/order-status-batch`, `api-reference/markets/get-market`) was read again on 2026-08-26, and the
> settlement statements below rest on that read. The rest of this block still rests on the 2026-08-25 read, which is why
> `verified_at` is unchanged.
> revalidate_when: any changelog entry touching orders, `clientOrderId`, cancel-replace, the venue system, EIP-712 or
> WebSocket events; a signing-page domain `version` other than `"1"` or `chainId` other than `8453`; a new value in
> `settlementStatus`; an SDK minor release. The changelog ships multiple entries per week, so treat anything hard-coded
> from this file as stale after a month.

**Scope.** Public client integration only: discovering markets, signing and sending orders, recovering an order's fate,
consuming the event stream, and reading positions, history, redemption and withdrawal. Nothing here describes how the
venue runs its book internally, and nothing here is drawn from a non-public source.

## Contents

- The two things that make this venue different from a generic CLOB client
- Market discovery, and why `venue` is a per-market fact you must read
- REST and WebSocket boundaries, and the absence of any sandbox
- Authentication modes, and the one operation HMAC tokens cannot perform
- EIP-712 order signing, and the signed fields that are not free parameters
- Four identities: `clientOrderId`, `orderId`, `eventId`, `tradeEventId`
- An ambiguous submission, and the recovery ladder the documented endpoints allow
- Cancel, batch cancel and cancel-replace: three different failure shapes
- Maintenance mode, and why 425 means two different things
- OME versus SETTLEMENT: two sources, no ordering, one provisional frame
- Reconnect and resubscription, and the gap nobody fills for you
- Positions, history, redemption and withdrawal
- CLOB and Neg Risk, and the approval that is easy to miss
- One book, YES-side, already merged, with no freshness bound
- Fees are charged in two different assets
- Required assertions
- What is verified here and what is not

## The two things that make this venue different from a generic CLOB client

First, the `verifyingContract` you sign against is **per market**, not per venue. The signing page gives the EIP-712
domain as `{"name": "Limitless CTF Exchange", "version": "1", "chainId": 8453, "verifyingContract": "<venue.exchange
address>"}`, and the venue page says that address is obtained by fetching market venue data via `GET /markets/:slug`. A
client that hard-codes one exchange address, the shape every Polymarket-derived client starts from, signs valid-looking
orders addressed to the wrong contract.

Second, an order's fate reaches you on **one event name from two sources**, with no ordering guarantee between them. The
WebSocket page states: "**Ordering is not guaranteed across sources.** OME and SETTLEMENT events for the same order can
arrive in either order within a few seconds." A state machine that assumes matching precedes settlement, or that a
settlement frame is the last word, will book a fill it later has to unbook.

## Market discovery, and why `venue` is a per-market fact you must read

`GET /markets/:slug` is the single call the introduction page puts between authentication and order construction. The
fields that change what your code does:

- `venue.exchange`, the contract you sign against. `venue.adapter`, a second address that matters for Neg Risk selling.
  The venue page calls venue data "**static** per market" and advises "fetch once and reuse", so cache it keyed by slug
  and never keyed globally.
- `tokens.yes` and `tokens.no`. The migration page describes this as a fixed convention on Limitless, against
  Polymarket's `clobTokenIds` array paired positionally with `outcomes`. Read the id; do not derive it.
- `tradeType`, which separates CLOB markets from AMM markets. This is not cosmetic: the market page states that on CLOB
  markets `prices` is "[yesMidpoint, noMidpoint] as decimal fractions between 0 and 1", while "AMM markets quote
  percent-style values between 0 and 100". One field name, two scales, a factor of 100 apart. A client that reads
  `prices[0]` without branching on `tradeType` sizes an order 100x wrong in one direction and 0.01x wrong in the other,
  and both errors look like a plausible probability to a human skimming a log.
- `status`, documented as `FUNDED` ("the market is live and accepting trades"), `LOCKED` ("trading is paused on the
  market"), `RESOLVED` ("the winning outcome is known"), `FUNDED_FLAGGED` ("the market is live but flagged for review")
  and `DRAFT` ("the market exists but has not been funded yet"). The orderbook page adds that a book is returned only for
  markets with status `CREATED` or `FUNDED`, and that "AMM markets do not have an order book."
- `settings.takerDelayMs`. The market page states that when it is greater than `0`, "marketable (taker) orders on this
  market are briefly held before the matching engine fills them", and the create-order page returns
  `settlementStatus: "DELAYED"` with an `eligibleAt` for such an order. The association is documented. **The arithmetic
  is not: no page states that `eligibleAt` equals your submit time plus `takerDelayMs`, so that identity is UNVERIFIED.**
  Read `eligibleAt` off the response, and never compute a release time locally.
- `winningOutcomeIndex` and `payoutNumerators`, the resolution fields.

**Tick size and minimum order size are UNVERIFIED.** Neither the market-details page nor the orderbook page as read on
2026-08-25 documents a tick grid or a minimum size. Do not assume Polymarket's grid applies. Discover the constraint
empirically against the live API before you round anything, and put the answer behind a named constant with the date you
established it.

## REST and WebSocket boundaries, and the absence of any sandbox

REST base URL `https://api.limitless.exchange`. WebSocket `wss://ws.limitless.exchange`, Socket.IO namespace `/markets`.
Network is "Base mainnet (chain ID `8453`)" with "real USDC" as collateral. Unlike Polymarket, which the migration page
notes splits across `gamma-api`, `clob` and `data-api` hosts, everything REST lives on one host.

The introduction page states there is "no sandbox, testnet, mock mode, or Base Sepolia deployment". Every integration
test that talks to this venue spends production capital. That removes the usual paper-trading answer to "how do we prove
the client works", and it means the exposure question is decided the moment a credential exists: there is no
configuration in which a reachable order path is not a live one. Build the offline test double yourself, from the payload
shapes in this file, and keep the live path behind a size cap that a test asserts.

## Authentication modes, and the one operation HMAC tokens cannot perform

Three modes, with different reach:

- **Privy identity token.** Obtained by authenticating with Privy and capturing the `token` field. Passed as
  `identity: Bearer <token>` when deriving an API token.
- **HMAC scoped tokens**, the day-to-day programmatic path. Three headers: `lmts-api-key` (the token id),
  `lmts-timestamp` (ISO-8601, documented as needing to be within 30 seconds of server time), and `lmts-signature`, a
  base64 HMAC-SHA256 over the canonical message `{ISO-8601 timestamp}\n{HTTP METHOD}\n{request path with query
  string}\n{request body}`. The path component includes the query string, and a GET uses an empty string for the body.
- **EIP-712 signatures** for orders. The authentication page notes your private key "is still required for EIP-712 order
  signing (unless using delegated signing)".

Scopes: `trading` (place and cancel orders, and the required base scope for delegated signing), `account_creation`,
`delegated_signing` ("Server signs orders on behalf of sub-accounts"), `withdrawal`, and `admin`.

**The asymmetry worth designing around:** the withdraw page states that "Withdrawal-address allowlist management requires
a Privy identity token. HMAC/scoped API tokens cannot add or delete withdrawal addresses." A compromised HMAC token can
therefore withdraw to an already-allowlisted address but cannot add a new one. Treat allowlist mutation as a separate,
higher-authority path with its own approval, because the venue already does.

Delegated operation is expressed two ways depending on the endpoint, and mixing them up is a 403: the portfolio reads use
the header `x-on-behalf-of: <profileId>` and require `delegated_signing`, `POST /orders` takes `onBehalfOf` in the body,
and the cancel endpoints take `?onBehalfOf=<subProfileId>` as a query parameter.

## EIP-712 order signing, and the signed fields that are not free parameters

Domain, quoted from the signing page:

```json
{ "name": "Limitless CTF Exchange", "version": "1", "chainId": 8453, "verifyingContract": "<venue.exchange address>" }
```

The `Order` type has twelve fields: `salt` (uint256), `maker` (address), `signer` (address), `taker` (address), `tokenId`
(uint256), `makerAmount` (uint256), `takerAmount` (uint256), `expiration` (uint256), `nonce` (uint256), `feeRateBps`
(uint256), `side` (uint8), `signatureType` (uint8). The migration page contrasts this with Polymarket V2, which removed
`taker`, `expiration`, `nonce` and `feeRateBps` and added `timestamp`, `metadata` and `builder`: Limitless "retains
classic CTF fields".

Four of those twelve are constrained rather than chosen:

1. **`expiration` and `nonce` must both be `0`.** The signing page says so plainly and adds that orders with non-zero
   values are rejected outright. One consequence is factual about your own payload: `nonce` is not available to you as an
   anti-replay device, so the only uniqueness the signed struct carries is `salt`. A second is an inference and is
   **UNVERIFIED**: a zero `expiration` most likely means no venue-side time-to-live, which would make every resting order
   yours to cancel. Design for that, since it is the unsafe direction, but confirm it before relying on the opposite.
2. **`feeRateBps` is a server-owned value you copy, not a value you pick.** The migration page: "On Limitless, for
   markets that charge a fee the signed `feeRateBps` must equal your profile's `rank.feeRateBps`", and hard-coding `0`
   works only on zero-fee markets. `rank.feeRateBps` comes from `GET /profiles/me`, which also returns the `id` you pass
   as the mandatory `ownerId` on every `POST /orders`. Because a fee band is profile state that the venue can change,
   caching it at process start and signing it forever converts a silent server-side change into a stream of rejected
   orders, or worse a stream of orders signed at a fee you no longer owe. Re-read it on a rejection rather than retrying.
3. **`signatureType` is `0`**, EOA, documented as "currently the only supported type". There is no second value to
   select, so a client that exposes it as configuration is exposing a way to be wrong.

The remaining eight are yours, with `side` encoded as `0` for BUY and `1` for SELL.

Amount scaling: "USDC has **6 decimals** (1 USDC = 1,000,000 units). Shares are also scaled by **1e6**." For a BUY,
`makerAmount` is the USDC paid and `takerAmount` the shares received; for a SELL the two swap roles. The quickstart's
worked example is a BUY of 10 shares at $0.65 giving `makerAmount = 6,500,000` and `takerAmount = 10,000,000`. **FOK
orders are the exception:** the signing page states FOK orders "Always set `takerAmount = 1`", with `makerAmount`
carrying the raw amount offered. A shared amount-computation helper that does not branch on order type produces an FOK
order whose signed struct means something else.

**`salt` is not an identity.** The quickstart derives it as `salt = int(time.time() * 1000)`, described on the signing
page as "Unique order identifier (typically timestamp-based)". A wall-clock millisecond is not stable across a retry and
is not unique across concurrent workers. Do not use `salt` as your correlation key, and do not let a retry re-derive it:
either the retry sends the byte-identical signed payload, or it is a different order. The create-order page confirms the
venue deduplicates on the signed hash as well as on `clientOrderId`: the 409 as read on 2026-08-26 says the
"`clientOrderId` or the signed order hash already exists or is being processed."

## Four identities: `clientOrderId`, `orderId`, `eventId`, `tradeEventId`

| identity | minted by | shape | where it appears |
|---|---|---|---|
| `clientOrderId` | you | string, "at most 128 characters", optional | `POST /orders` body, cancel by id, batch status, echoed on `orderEvent` |
| `orderId` | the venue | UUIDv4 | user orders, cancel results, `orderEvent`, portfolio history rows |
| `eventId` | the venue | per-lifecycle string, shapes below | `orderEvent` frames only |
| `tradeEventId` | the venue | string | create-order `execution`, MATCHED and terminal settlement frames, history rows |

`clientOrderId` is documented as an "Optional uniqueness and deduplication key". Optional is the trap: an order sent
without one can be recovered afterwards only by `orderId`, and the create-order response as documented on 2026-08-25 does
not list a top-level `orderId` field, only a nested `execution` object. **Whether the create-order response carries a
top-level venue order id is UNVERIFIED**; the page documents `execution` fields (`matched`, `settlementStatus`, `reason`,
`eligibleAt`, `tradeEventId`, `txHash`, `stpMakerCancels`, `feeRateBps`, `effectiveFeeBps`, `totalsRaw`) and no top-level
identifier. Send a `clientOrderId` on every order. It is the only handle you are guaranteed to hold before the response
arrives, which is the only moment that matters when the response does not.

Mint it from the intent instance and commit it before the send. The batch status endpoint accepts a `clientOrderId` for
orders in terminal states, so the mapping is documented to outlive the live order; **whether the uniqueness constraint is
permanent or scoped to open orders is UNVERIFIED**, so do not build a scheme that reuses a value on the assumption it
expires.

The WebSocket page gives `eventId` a documented construction that makes it a durable dedupe key rather than a transport
detail: `PLACEMENT`, `UPDATE` and `CANCELLATION` use numeric ids; `EXECUTION` uses `"terminal:<orderId>"`; `MATCHED` uses
`"matched:<tradeEventId>:<orderId>"`; terminal settlement uses `"settlement:<tradeEventId>:<orderId>"`. Persist
`eventId` in the same transaction as the effect it causes, and reject a repeat rather than ignoring it. `clientOrderId`
is "omitted entirely (not `null`) when absent from the originating order", so a parser that reads it with a
null-defaulting accessor and one that requires the key present behave differently on the same frame.

## An ambiguous submission, and the recovery ladder the documented endpoints allow

The create-order page documents no guidance for a timed-out or lost response. It does document the two facts you need to
build one. Reuse of a `clientOrderId` "returns `409 Conflict`; the API does not replay the earlier response." And
`POST /orders/status/batch` accepts 1 to 50 items, each carrying "exactly one per item, not both" of `orderId` or
`clientOrderId`, and returns item-level `status` of `"found"`, `"not_found"` or `"invalid"`.

So the ladder is:

1. **Never resend.** Because the API "does not replay the earlier response", a resend is not idempotent recovery. It is
   either a 409 or a second order.
2. **Ask by `clientOrderId`** through `POST /orders/status/batch`. A `"found"` item carries `order`, optional
   `makerMatches` and `execution`, and settles the question.
3. **Read a 409 as evidence, not as failure.** A 409 naming a duplicate `clientOrderId` says the identity is taken:
   the page's wording is "already exists or is being processed", which is a positive answer to "did my first attempt
   arrive" and not yet an answer to what became of it. Query for the detail rather than concluding either way. The
   common bug is a retry wrapper that classifies 4xx as terminal-failure and marks the intent dead while a live order
   rests on the book.
4. **A `"not_found"` is not proof of non-creation.** The page states that "An unknown identifier, including an order owned
   by another profile, returns item-level `status: "not_found"`", which folds a permissions answer and an absence answer
   into one value. **Whether a status read is guaranteed to reflect an order the API has just accepted is UNVERIFIED**:
   no page states a read-after-write guarantee. Re-query across a short window you declare as config before concluding
   the order never existed, and hold the intent as unresolved meanwhile.
5. **Reserve the worst case while unresolved.** Hold the order at its worst-case exposure, which for a collateralised
   binary buy is the collateral it commits, close new sends for that token, and give the state a wall-clock budget.
   Cancel by `clientOrderId` is the safe expiry action, with the caveat in the next section that cancel is not a
   no-op here.

The `settlementStatus` values the create-order page documents are `UNMATCHED`, `MATCHED`, `MINED`, `CONFIRMED`,
`RETRYING`, `FAILED`, `DELAYED`, `CANCELED`. The batch status endpoint reports the same list without `CANCELED`: it
"does not include ... a historical `CANCELED` state". A cancelled order therefore does not report
`settlementStatus: "CANCELED"` there, so do not read a cancellation out of that field. `DELAYED` is non-terminal, its
`eligibleAt` being the "ISO-8601 time the order is released to the matching engine". **`RETRYING` and `CONFIRMED` are
UNVERIFIED**: no page read on 2026-08-26 defines either, and neither appears among the `SETTLEMENT` event types, so
where `CONFIRMED` sits relative to `MINED` is not established. Treat both as unresolved rather than terminal, and
resolve them by re-querying. None of these is a reason to send anything.

The `425` on the create path is a receive-window rejection, and the page is explicit: "Do not retry the same signed
payload after a receive-window `425`; build and sign a fresh order." A fresh order means a fresh `salt` and signature.
Whether the fresh order carries the same `clientOrderId` is your decision and it is load-bearing: reusing it makes the
venue reject a duplicate if the first attempt did land, which is the safe direction. `timestamp` (Unix milliseconds) and
`recvWindow` ("Maximum accepted order age (up to 10000 ms)") are the fields that produce this rejection.

## Cancel, batch cancel and cancel-replace: three different failure shapes

**Single cancel** is `POST /orders/cancel` with exactly one of `orderId` or `clientOrderId`; both or neither returns 400.
Cancelling here is **not** a silent no-op. An already-filled or non-cancellable order returns `400 Bad Request` with
"Order not found or already canceled", and an unknown id returns `404` with "No order resolves from the supplied internal
or client order ID". A generic client that treats a cancel error as a hard failure and stops will strand an unresolved
intent; one that treats every cancel error as success will report a resting order as cancelled. Branch on the two codes
and re-query.

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

## Positions, history, redemption and withdrawal

`GET /portfolio/positions` and `GET /portfolio/history` are the authenticated reads, both accepting `x-on-behalf-of` with
the `delegated_signing` scope. The changelog records CLOB history rows carrying optional `tradeEventId`, `orderId` and
`makerMatchId` "for reconciliation purposes" as of 2026-08-10, and `GET /portfolio/history` accepting `market=<slug>` as
of 2026-08-11. **The full field list of both endpoints is UNVERIFIED** from the pages read on 2026-08-25; establish the
cost-basis and PnL field names against the live API before you reconcile on them.

Resolution has two shapes, and a client that models only the first under-credits. A market with `status: RESOLVED` and a
`winningOutcomeIndex` (0 for YES, 1 for NO) is winner-take-all. A market with `winningOutcomeIndex: null` and a non-null
`payoutNumerators` array is a payout split, and the positions page states that such a position is "a payout split" where
both legs pay: "one call redeems both YES and NO holdings at the ratio defined by `payoutNumerators`". A ledger that
zeroes the losing leg on resolution is wrong on every split market.

`POST /portfolio/redeem` takes `conditionId`, "CTF condition id (`bytes32` hex string)", and optional `onBehalfOf`. Its
documented precondition is the one that matters: "API-level resolved status can appear before CTF settlement; on-chain
payout must be posted before redemption succeeds." The API's own `RESOLVED` is therefore not the redeemability signal.
Trading stopped, outcome known, payout reported and value received are four distinct states, and only the last is money.
**Whether redeem is idempotent is UNVERIFIED**; the page documents neither a response body nor an error list. Until you
establish it, guard the call with your own committed intent row and dedupe the resulting credit on the on-chain
transaction, not on the fact that you called the endpoint.

`POST /portfolio/withdraw` takes `amount` as a string in the token's smallest unit (`"1000000"` is 1 USDC), optional
`token` (defaults to USDC), optional `onBehalfOf` and optional `destination`. A `destination` is accepted only if it is
the caller's account address, the caller's smart wallet address, or an active allowlisted withdrawal address on the
authenticated profile. For an `onBehalfOf` withdrawal the allowlist belongs to the authenticated partner, not the
sub-account, which is a deliberate authority boundary worth preserving in your own code rather than flattening.

## CLOB and Neg Risk, and the approval that is easy to miss

The user guide defines a Neg Risk market as a bundle where "only one outcome can win", and states the structural fact
that drives the integration difference: "Each outcome has a Yes and No order book, but here's the twist: **all the "No"
contracts across the different outcomes are linked**." Conversion between outcomes exists as a product feature, described
as switching outcomes by converting No to Yes and unlocking USDC by converting extra No shares back into cash.

For a client, the operational difference is approvals. The venue page: "For NegRisk SELL orders, you must approve to
**both** the exchange and the adapter addresses." A simple CLOB market "Uses only `venue.exchange` for SELL order
approvals". An approval helper written against a simple market and reused on a Neg Risk market produces a SELL that fails
at the contract, after your local state has already reserved the inventory. The migration page frames the same difference
against Polymarket, which uses a separate Neg Risk exchange contract, whereas Limitless handles it through the venue
system with an "extra approval to `venue.adapter` for SELL".

Whether prices across the outcomes of a Neg Risk bundle are constrained to sum to 1 is **UNVERIFIED**; the overview page
as read does not state it. Do not build an arbitrage or collateral check on an assumed sum.

## One book, YES-side, already merged, with no freshness bound

The orderbook endpoint returns a **single YES-side book**, with `bids` and `asks` arrays whose levels carry `price`
(number), `size` (number) and `side` (`"BUY"` or `"SELL"`), alongside `tokenId` (the market's YES position id),
`lastTradePrice` (nullable), `midpoint` and `adjustedMidpoint`. Prices are decimals in 0 to 1, under the identity the
page states directly: a "YES share and a NO share always redeem together for exactly $1", so `price(YES) + price(NO) = 1`.

Two consequences a generic two-book client gets wrong.

**There is no separate NO book to fetch.** Derive the NO side by inverting price to `1 - p` and flipping bid to ask.

**Do not add NO liquidity on top.** The page states: "The book you get back already merges **all** liquidity for the
market: native NO orders are converted into their YES-side equivalent before aggregation." A client that folds in NO-side
resting interest from any other source counts the same order twice, which inflates the depth it walks and undersizes the
slippage it expects on exactly the orders it is about to send.

**There is no sequence anchor and no freshness contract.** The page notes that "No bound on response freshness is
guaranteed", and documents no sequence number or timestamp field on the response. **Whether the WebSocket
`orderbookUpdate` frame carries a sequence number that joins to this snapshot is UNVERIFIED.** Until you establish one,
do not maintain an incrementally patched book against this snapshot: re-snapshot rather than patch, stamp your own
receive time, and gate the send path on an age you declare rather than on an ordering guarantee nobody gave you. **Size
units are UNVERIFIED** on the page as read; establish whether `size` is shares or raw 1e6 units before you compute a
notional from it.

**The difference between `midpoint` and `adjustedMidpoint` is UNVERIFIED.** Two fields one adjective apart, both
plausible inputs to a quoting loop, and the page as read does not define either. Do not pick one by name.

One inconsistency worth recording rather than resolving: the orderbook page says a book is returned for markets with
status `CREATED` or `FUNDED`, while the market-details page enumerates `FUNDED`, `LOCKED`, `RESOLVED`, `FUNDED_FLAGGED`
and `DRAFT` with no `CREATED`. **The relationship between the two lists is UNVERIFIED.** Treat any status you did not
plan for as not tradeable rather than mapping it onto the closest name you recognise.

## Fees are charged in two different assets

Two documented facts that a `bps * notional` fee model cannot represent.

First, the asset differs by side: the user guide states buy fees are charged in "Outcome tokens (contracts)" and sell
fees in "Collateral (USDC)". What follows from that asset choice is an inference rather than a documented sentence, and
it is the one that costs money: if a taker buy pays its fee in outcome tokens, the buy delivers **fewer shares than
`takerAmount` implies**. A position keeper that credits `takerAmount / 1e6` shares on a taker buy then over-counts the
position by the fee, on every taker buy, and the error compounds into average cost and every PnL number derived from it.
Credit the shares the venue reports rather than the shares you asked for, which is correct either way and does not depend
on the inference holding.

Second, only takers pay. The user guide states "**Fees only apply to takers**", meaning orders that settle immediately
against the resting book, and that makers providing liquidity pay nothing. The rate is documented as varying with price
rather than being flat, the user guide describing a curve of 0.40% to 3.00% for buys and 0.42% to 1.50% for sells, with
buy fees falling as probability rises and sell fees peaking near $0.50.

**The relationship between that curve and the signed `feeRateBps` is UNVERIFIED.** The signed field must equal your
profile's `rank.feeRateBps`, a profile-level band, while the user guide describes a price-dependent curve, and the
create-order response returns both `feeRateBps` and `effectiveFeeBps`. Those three numbers are not obviously the same
quantity, and the documentation read on 2026-08-25 does not reconcile them. Do not compute a fee locally and treat it as
the truth. Read `effectiveFeeBps` and the execution totals from the response, reconcile them against the balance change,
and alert on a mismatch rather than assuming your formula.

## Required assertions

Each of these is a test in the repository's own framework, before live keys, and each fails today if the corresponding
control is absent.

1. **A lost response creates no second order.** Simulate a timeout on `POST /orders`, run the recovery path, and assert it
   issues `POST /orders/status/batch` keyed on the committed `clientOrderId` and never a second `POST /orders`. Assert
   separately that a 409 naming a duplicate `clientOrderId` resolves the intent to "the venue has this identity, query
   it" rather than to failure.
2. **A `425` branches on `code`.** Assert that a receive-window `425` re-signs a fresh order, that a maintenance `425`
   stops and refreshes `GET /maintenance/status`, and that `cancel_only` and `disabled` gate different operations.
3. **Amounts and the fee asset are right.** Assert BUY and SELL amount computation at 1e6 scaling, assert the FOK
   `takerAmount = 1` branch, and assert that a taker buy credits the venue's reported share quantity rather than
   `takerAmount / 1e6`.
4. **The signed struct matches server state.** Assert `expiration == 0`, `nonce == 0`, `signatureType == 0`,
   `verifyingContract == venue.exchange` for the market being traded, and `feeRateBps == rank.feeRateBps` read in the same
   flow rather than from a process-lifetime cache.
5. **Events converge under shuffling.** Replay a stream of `orderEvent` frames in arrival order, then reversed,
   duplicated, and interrupted by a simulated restart, and assert every run converges on the same state once each fold
   sorts into the venue's canonical economic order, and that `eventId` dedupe is read from storage rather than memory. Include a `SETTLEMENT MATCHED` arriving before its `OME EXECUTION`, and a
   `FAILED` after a `MATCHED` that was already booked. Assert separately that a `DELAYED` response releases on the
   `eligibleAt` the venue returned and never on a locally computed submit time plus `settings.takerDelayMs`, and that
   `RETRYING` and `CONFIRMED` leave the order unresolved rather than terminal.
6. **Reconnect closes the hole.** Assert that the `connect` handler re-sends the complete subscription set in one call
   per subscription type, that no local state is acted on until the REST rebuild completes, and that a `user-orders`
   result of exactly `limit` items is treated as a hole rather than the end.
7. **A 207 is not a success.** Assert that a batch cancel returning `207 Multi-Status` leaves the failed ids marked live
   and triggers a re-query, and that a cancel-replace returning `cancel.status: "SUCCESS"` with
   `replacement.status: "FAILURE"` marks the quote withdrawn.
8. **Reconciliation runs in production.** Name Limitless as the external authority and the join key for each quantity:
   `orderId` and `clientOrderId` for orders, `tradeEventId` for fills, `conditionId` for redemption. Ship the comparison
   as a scheduled entrypoint reading through a path independent of the writer, with an alert destination that is a config
   key with no default.

## What is verified here and what is not

Everything quoted above was read from the documentation pages named in the provenance block on 2026-08-25, and the
settlement pages again on 2026-08-26. The following are stated as unverified and must not be presented otherwise:

- Tick size, minimum order size and rate limits. Not documented on the market, orderbook or introduction pages as read.
- Whether the `POST /orders` response carries a top-level venue order id.
- How the user-guide fee curve, the signed `rank.feeRateBps` and the response `effectiveFeeBps` relate.
- Whether a status read is guaranteed to reflect a write the API has just accepted, and therefore whether a `404` from
  cancel or a `not_found` from the batch status endpoint is durable.
- What the 60-second dedupe window is keyed on, and whether it suppresses anything after a reconnect or a restart.
- The meanings of `RETRYING` and `CONFIRMED`, and where `CONFIRMED` sits relative to `MINED`.
- Whether `eligibleAt` equals your submit time plus `settings.takerDelayMs`. The association is documented. The
  arithmetic is not.
- Whether the WebSocket replays or backfills anything on resubscribe.
- The complete response field lists of `GET /portfolio/positions` and `GET /portfolio/history`.
- Whether `POST /portfolio/redeem` is idempotent, and what it returns.
- Whether prices across the outcomes of a Neg Risk bundle are constrained to sum to 1.
- Whether the uniqueness constraint on `clientOrderId` is permanent or scoped to open orders.
- Orderbook `size` units, whether any sequence number joins a book snapshot to the `orderbookUpdate` stream, and the
  difference between `midpoint` and `adjustedMidpoint`.
- The `CREATED` market status, which the orderbook page names and the market-details status enum does not.

The venue ships multiple documented changes per week. Re-read the changelog before trusting any hard-coded value here.
