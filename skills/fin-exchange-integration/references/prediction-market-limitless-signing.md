# Limitless Exchange: EIP-712 order signing and Neg Risk approvals

> **Provenance**
> provider: Limitless Exchange · surface: EIP-712 CTF order signing and the approval targets a Neg Risk SELL needs · chain: Base, chainId 8453
> version: the docs publish no API version number. The EIP-712 signing domain is version "1"; the changelog records all four official SDKs at v1.1.0 on 2026-08-12. Both are restated from this block's own sources.
> verified_at: 2026-08-25
> sources: every path below is under `https://docs.limitless.exchange/`, enumerated by the docs index at
> `https://docs.limitless.exchange/llms.txt`. `/developers/`: `eip712-signing`, `venue-system`,
> `migrate-from-polymarket`, `quickstart/python`. Also `/api-reference/trading/create-order`,
> `/api-reference/portfolio/get-profile`, `/user-guide/negrisk-overview`, and `/changelog`.
> pinned: none. No versioned client artefact is cited here. Every statement below is read off the documentation pages named, never off SDK source.
> verified: the EIP-712 domain and the twelve-field `Order` struct; the zero constraint on `expiration` and `nonce` and the outright rejection of non-zero values; `feeRateBps` as the profile's `rank.feeRateBps` and `ownerId` from `GET /profiles/me`; `signatureType` as the only supported type; `side` encoding; 1e6 amount scaling for USDC and shares, the worked BUY example, and the FOK `takerAmount = 1` convention; `salt` as a typically timestamp-based unique order identifier; the create-order 409 naming the signed order hash as well as `clientOrderId`, as read on 2026-08-26; the Neg Risk definition and the linked-NO structural statement; `venue.exchange` for a simple CLOB SELL approval and **both** `venue.exchange` and `venue.adapter` for a Neg Risk SELL.
> unverified: whether a zero `expiration` means no venue-side time-to-live; whether prices across the outcomes of a Neg Risk bundle are constrained to sum to 1.
> revalidate_when: a signing-page domain `version` other than `"1"` or `chainId` other than `8453`; a second `signatureType`; any changelog entry touching EIP-712, the venue system or Neg Risk approvals; an SDK minor release. The changelog ships multiple entries per week, so treat anything hard-coded from this file as stale after a month.

**Scope.** Constructing and signing the order struct, and the approvals a SELL needs. Market discovery and authentication,
submission and recovery, cancellation, the event stream, and the book, fees and portfolio reads each have their own
reference. Nothing here describes how the venue runs its book internally.

## Contents

- EIP-712 order signing, and the signed fields that are not free parameters
- CLOB and Neg Risk, and the approval that is easy to miss
- Required assertions

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

## Required assertions

Each of these is a test in the repository's own framework, before live keys, and each fails today if the corresponding
control is absent.

1. **The signed struct matches server state.** Assert `expiration == 0`, `nonce == 0`, `signatureType == 0`,
   `verifyingContract == venue.exchange` for the market being traded, and `feeRateBps == rank.feeRateBps` read in the same
   flow rather than from a process-lifetime cache.
2. **Amounts branch on order type.** Assert BUY and SELL amount computation at 1e6 scaling against the documented worked
   example, and assert the FOK `takerAmount = 1` branch separately, because a shared helper that does not branch produces
   an FOK order whose signed struct means something else.
3. **A retry does not re-derive `salt`.** Assert that a retry sends the byte-identical signed payload, and that no code
   path recomputes `salt` from a wall clock for an intent that has already been signed.
4. **A Neg Risk SELL approves both targets.** Assert that the approval helper resolves its targets from the market's own
   `venue` data, that a Neg Risk market approves `venue.exchange` **and** `venue.adapter`, and that a simple CLOB market
   approves only `venue.exchange`.
