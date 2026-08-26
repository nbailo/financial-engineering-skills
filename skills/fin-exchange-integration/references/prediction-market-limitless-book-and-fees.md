# Limitless Exchange: the merged YES book, the two fee assets, and the portfolio reads

> **Provenance**
> provider: Limitless Exchange · surface: REST orderbook, the public fee guide, and the authenticated portfolio reads including redemption and withdrawal · chain: Base, chainId 8453
> version: the docs publish no API version number. The changelog records all four official SDKs at v1.1.0 on 2026-08-12, restated from this block's own sources.
> verified_at: 2026-08-25
> sources: every path below is under `https://docs.limitless.exchange/`, enumerated by the docs index at
> `https://docs.limitless.exchange/llms.txt`. `/api-reference/trading/orderbook`, `/api-reference/markets/get-market`,
> `/api-reference/portfolio/` `get-profile`, `positions`, `redeem`, `withdraw`. Also `/user-guide/fees`,
> `/api-reference/trading/create-order` and `/changelog`.
> pinned: none. No versioned client artefact is cited here. Every statement below is read off the documentation pages named, never off SDK source.
> verified: the single YES-side book, its level fields, `tokenId`, `lastTradePrice`, `midpoint` and `adjustedMidpoint`, the `price(YES) + price(NO) = 1` identity quoted from the redemption sentence, the already-merged statement quoted, and the absence of any guaranteed response freshness; the fee asset differing by side, quoted; the takers-only rule, quoted; the documented buy and sell fee ranges and the shape of the curve; the create-order response carrying both `feeRateBps` and `effectiveFeeBps`; the two resolution shapes, `winningOutcomeIndex` for winner-take-all and a non-null `payoutNumerators` for a payout split, with the one-call-redeems-both sentence quoted; the `redeem` `conditionId` parameter and its documented precondition that on-chain payout must be posted before redemption succeeds; the `withdraw` parameters, the smallest-unit amount encoding and the three accepted destination classes; the changelog entries adding `tradeEventId`, `orderId` and `makerMatchId` to CLOB history rows and `market=<slug>` to history.
> unverified: orderbook `size` units, any sequence anchor joining a snapshot to `orderbookUpdate`, and the difference between `midpoint` and `adjustedMidpoint`; how the user-guide fee curve relates to the signed `rank.feeRateBps` and the response `effectiveFeeBps`; the full field lists of the positions and history endpoints; whether redeem is idempotent.
> re-read: `api-reference/markets/get-market` was read again on 2026-08-26 for the resolution fields. The rest of this block rests on the 2026-08-25 read, which is why `verified_at` is unchanged.
> revalidate_when: any changelog entry touching the orderbook, the fee curve, the portfolio reads or redemption; the docs defining `adjustedMidpoint` or the `size` units; a documented idempotency statement on redeem; an SDK minor release. The changelog ships multiple entries per week, so treat anything hard-coded from this file as stale after a month.

**Scope.** Reading the book, what a fill actually costs and in which asset, and reading, redeeming and withdrawing what you
hold. Market discovery and authentication, order signing, submission and recovery, cancellation, and the event stream each
have their own reference.

## Contents

- One book, YES-side, already merged, with no freshness bound
- Fees are charged in two different assets
- Positions, history, redemption and withdrawal
- Required assertions

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

## Required assertions

Each of these is a test in the repository's own framework, before live keys, and each fails today if the corresponding
control is absent.

1. **A taker buy credits the venue's share count.** Assert that a taker buy credits the share quantity the venue reports
   rather than `takerAmount / 1e6`, because the buy fee is charged in outcome tokens and the difference compounds into
   average cost and every PnL number derived from it.
2. **The book is not double-counted and not patched.** Assert that no NO-side liquidity from any other source is added to
   the returned book, that the NO side is derived by inverting price and flipping side, and that a stale snapshot is
   re-fetched rather than incrementally patched.
3. **A split market credits both legs.** Assert that a market with `winningOutcomeIndex: null` and a non-null
   `payoutNumerators` pays both YES and NO at the published ratio, and that no code path zeroes the losing leg on
   resolution.
4. **Redemption waits for the chain.** Assert that `RESOLVED` alone does not trigger a redeem, that the call is guarded by
   a committed intent row, and that the resulting credit is deduped on the on-chain transaction rather than on the fact
   that the endpoint was called.
5. **Reconciliation runs in production.** Name Limitless as the external authority and the join key for each quantity:
   `orderId` and `clientOrderId` for orders, `tradeEventId` for fills, `conditionId` for redemption. Ship the comparison
   as a scheduled entrypoint reading through a path independent of the writer, with an alert destination that is a config
   key with no default.
