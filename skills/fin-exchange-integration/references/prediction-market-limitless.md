# Limitless Exchange: markets, authentication, and the identity of an order you may have lost

> **Provenance**
> provider: Limitless Exchange · surface: REST market discovery, the three authentication modes, order submission and the batch order-status endpoint · chain: Base, chainId 8453
> version: the docs publish no API version number; the changelog records all four official SDKs at v1.1.0 on 2026-08-12, restated from this block's own sources.
> verified_at: 2026-08-25
> sources: every path below is under `https://docs.limitless.exchange/`, enumerated by the docs index at
> `https://docs.limitless.exchange/llms.txt`. `/developers/`: `introduction`, `authentication`, `venue-system`,
> `migrate-from-polymarket`, `websocket-events`. `/api-reference/trading/`: `create-order`, `order-status-batch`,
> `cancel`, `user-orders`, `orderbook`. Also `/api-reference/markets/get-market`,
> `/api-reference/portfolio/` `get-profile`, `withdraw`, and `/changelog`.
> pinned: none. No versioned client artefact is cited; every statement is read off the documentation pages named, never off SDK source.
> verified: base URLs and chain; the absence of a sandbox; the three authentication modes and the five token scopes; the HMAC canonical message and its three headers; the withdrawal-allowlist authority asymmetry; the three spellings of delegated operation; `venue.exchange` as `verifyingContract` and `venue.adapter` for Neg Risk SELL approvals; venue data as static per market; `tokens.yes` and `tokens.no` as a fixed convention; `tradeType` and the two price scales, quoted; the market `status` enum with each documented gloss; the orderbook page's `CREATED` or `FUNDED` precondition and its statement that AMM markets have no order book; `settings.takerDelayMs` and its documented association with a `DELAYED` order and an `eligibleAt`; `clientOrderId` as an optional 128-character uniqueness and deduplication key, its trimming and non-blank rule, and the `409 Conflict` on reuse with the statement that the API does not replay the earlier response; the create-order response `execution` field list and the absence of a documented top-level identifier; the batch order-status endpoint, its 1-to-50 range, its exactly-one-identifier-per-item rule and its item-level `found`, `not_found` and `invalid` statuses, including that an order owned by another profile returns `not_found`; the `settlementStatus` enum as a list of values and the batch endpoint's omission of `CANCELED`; `DELAYED` as non-terminal with `eligibleAt`; the `eventId` construction for `PLACEMENT`, `UPDATE`, `CANCELLATION`, `EXECUTION`, `MATCHED` and terminal settlement; that `clientOrderId` is omitted entirely rather than null when absent; the receive-window `425` instruction not to retry the same signed payload, and the `timestamp` and `recvWindow` fields that produce it.
> unverified: tick size, minimum order size and rate limits; whether `eligibleAt` equals the submit time plus `settings.takerDelayMs`; the `CREATED` market status, which the orderbook page names and the market-details status enum does not; whether the create-order response carries a top-level venue order id; whether a status read is guaranteed to reflect an order the API has just accepted, so whether a `not_found` is durable; the meaning of `RETRYING` and of `CONFIRMED`, and where `CONFIRMED` sits relative to `MINED`; whether the uniqueness constraint on `clientOrderId` is permanent or scoped to open orders.
> re-read: the create-order and batch order-status pages were read again on 2026-08-26, and the settlement-status statements below rest on that read. The rest of this block rests on the 2026-08-25 read, which is why `verified_at` is unchanged.
> revalidate_when: any changelog entry touching the venue system, authentication, orders or `clientOrderId`; a new value in the market `status` enum or in `settlementStatus`; the docs publishing a tick grid, a minimum order size or a rate limit; the create-order page documenting a top-level order id or a read-after-write guarantee; an SDK minor release. The changelog ships multiple entries per week, so treat anything hard-coded from this file as stale after a month.

**Scope.** Discovering a market, getting authenticated, and holding an order's identity well enough to answer "did it
land?" when the response does not arrive. Order signing, cancellation and maintenance, the event stream, and the book, fees
and portfolio reads each have their own reference. Nothing here describes how the venue runs its book internally, and
nothing here is drawn from a non-public source.

## Contents

- The two things that make this venue different from a generic CLOB client
- Market discovery, and why `venue` is a per-market fact you must read
- REST and WebSocket boundaries, and the absence of any sandbox
- Authentication modes, and the one operation HMAC tokens cannot perform
- Four identities: `clientOrderId`, `orderId`, `eventId`, `tradeEventId`
- An ambiguous submission, and the recovery ladder the documented endpoints allow
- Required assertions

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
   Cancel by `clientOrderId` is the safe expiry action, with the caveat that cancel on this venue is not a no-op
   and carries failure shapes of its own.

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

## Required assertions

Each of these is a test in the repository's own framework, before live keys, and each fails today if the corresponding
control is absent.

1. **A lost response creates no second order.** Simulate a timeout on `POST /orders`, run the recovery path, and assert it
   issues `POST /orders/status/batch` keyed on the committed `clientOrderId` and never a second `POST /orders`. Assert
   separately that a 409 naming a duplicate `clientOrderId` resolves the intent to "the venue has this identity, query
   it" rather than to failure.
2. **A `not_found` does not close the intent.** Assert that a single `not_found` re-queries across the declared window
   before concluding non-creation, that the intent stays unresolved and reserved at its worst-case exposure meanwhile,
   and that `RETRYING` and `CONFIRMED` leave the order unresolved rather than terminal.
3. **`eventId` dedupe is durable.** Assert that `eventId` is persisted in the same transaction as the effect it causes and
   that a repeat is rejected rather than ignored, and that a parser distinguishes an omitted `clientOrderId` key from a
   null one.
4. **Venue data is per market, and `prices` is branched on `tradeType`.** Assert that the cache holding `venue.exchange`,
   `venue.adapter`, `tokens.yes` and `tokens.no` is keyed by market slug and that a second market cannot read the first
   market's entry; assert that a CLOB market reads `prices[0]` as a fraction between 0 and 1 and an AMM market as a
   percent-style value between 0 and 100; and assert that a `status` the code has never seen is refused rather than
   mapped onto the nearest name it recognises.
5. **`eligibleAt` is read, never computed.** Assert that a `DELAYED` order releases on the `eligibleAt` the venue returned
   and never on a locally computed submit time plus `settings.takerDelayMs`.
6. **The live path is capped.** There is no sandbox, so assert that the size cap on the only reachable order path is
   enforced by a test rather than by a convention, and that allowlist mutation is refused to an HMAC credential exactly
   as the venue refuses it.
