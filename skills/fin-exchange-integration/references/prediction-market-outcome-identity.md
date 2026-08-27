# Prediction markets: outcome identity and the payout vector

> **Provenance**
> provider: cross-venue (Polymarket, Kalshi, Limitless, Hyperliquid) · surface: public vendor documentation, market-detail and portfolio reference pages · version: Polymarket CLOB V2, Kalshi API v2, Limitless public API, Hyperliquid info endpoint
> verified_at: 2026-08-25
> sources: https://docs.polymarket.com/market-data/market-details · https://docs.polymarket.com/concepts/positions-tokens · https://docs.polymarket.com/api-reference/markets/get-clob-market-info · https://docs.kalshi.com/api-reference/market/get-market · https://docs.kalshi.com/api-reference/portfolio/get-positions · https://docs.limitless.exchange/api-reference/trading/create-order · https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint/spot
> pinned: not applicable. No source code, contract address or client-library behaviour is cited in this file. Every claim below is from vendor documentation at the URLs above, fetched on the verified_at date.
> verified: the Polymarket Gamma market-payload field list (`clobTokenIds`, `conditionId`, `outcomes`, `outcomePrices`, `negRisk`, `acceptingOrders`); the sentence "Each market has exactly two outcome tokens" and the `$1` collateral backing sentence on the positions-and-tokens page; that a per-market negative-risk flag selects the verifying contract under one EIP-712 domain version; Kalshi `ticker` and `event_ticker` as the keys, `position_fp` as one signed number over one ticker, `notional_value_dollars`, and the `market_result` values `yes`, `no` and `scalar` with a `value` field in cents; the Limitless `marketSlug` plus `tokenId` surface and the `winningOutcomeIndex` / `payoutNumerators` pair; the Hyperliquid `outcomeMeta` integer `outcome` identifier and its `sideSpecs` array.
> unverified: whether a Polymarket event with more than two outcomes is one market or several linked markets; every Hyperliquid outcome-market identifier beyond the field names listed above.
> revalidate_when: Polymarket changes the market-payload field names or the exactly-two-outcome-tokens statement, Kalshi adds a `market_result` value or changes `notional_value_dollars`, Limitless changes the `winningOutcomeIndex` and `payoutNumerators` pair, or Hyperliquid renames `outcomeMeta` or `sideSpecs`.

The instrument's payout is a number the venue publishes rather than a constant, and the mapping from a human label to the
identifier that carries money is data with a version rather than a constant either. Both failures are silent: every
arithmetic step downstream of an identifier nobody holds is individually correct, and a binary abstraction over an outcome
set that is not binary produces a plausible number rather than an exception.

**Scope.** Outcome identifiers and the shape of the outcome set only. Price grids and fees, short exposure and the two-book
invariant, order identity and the settlement-authority split each have their own reference. Venue-specific field names,
endpoints and error codes belong in that venue's own reference. Nothing here describes how a venue determines an outcome.

## Contents

- Outcome identity is market data, never a local computation
- Two outcomes is a special case, not the shape of the domain
- Required assertions, as code
- What is verified here, and what is not

## Outcome identity is market data, never a local computation

Key every order, fill, position and reserve on the identifier the venue's own market feed hands you, and never on one you
derived. Polymarket's Gamma market payload carries `clobTokenIds`, `conditionId`, `outcomes` and `outcomePrices` alongside
`negRisk` and `acceptingOrders` (field list read 2026-08-25). Kalshi keys on `ticker` and `event_ticker`. Limitless keys on
`marketSlug` plus a `tokenId` inside the order arguments. Hyperliquid's `outcomeMeta` request returns an integer `outcome`
identifier plus a `sideSpecs` array that defines the sides.

Two consequences follow, and both are cheap to enforce.

First, **the mapping from a human label to an identifier is data with a version, not a constant.** Limitless documents
`winningOutcomeIndex` where `0` is YES and `1` is NO, so the index order is meaningful and published. Nothing licenses
carrying that convention to another venue. Read the label to identifier mapping from the market payload each time, and store
the identifier, not the label, on every row that carries money.

Second, **a wrong identifier does not raise.** Every arithmetic step downstream of a token id that nobody holds is correct;
the balance read returns zero, the position quietly disappears, and there is no error at the site where the mistake was made.
Assert that the identifier on a fill matches an identifier the market payload published, in the same transaction that books
the fill.

One identifier in this family gets recomputed instead of read, and it fails in exactly that silent way.

**Never reimplement an identifier derivation you can read.** The conditional-token ids behind an outcome token are derived
on chain by a scheme this file does not restate, because restating a derivation is how a client ends up with a
reimplementation that disagrees with the chain and raises nothing when it does. Read the identifier from the venue's feed.
If you believe you need to derive one, that belief is the thing to test against a live payload before any of it reaches an
order.

**A per-market flag can select the contract you sign against.** Polymarket runs two verifying contracts for one product,
selected by whether the market is a negative-risk market, and both sign under the same EIP-712 domain version; the
addresses belong in that venue's own reference. **The flag selects the exchange contract, not a different collateral
asset.** The V1-era claim that negative risk also switches the collateral token is wrong for V2. Where a venue runs more
than one deployment for one product, resolve the signing target from the per-instrument flag rather than from a constant,
and assert the resolved target against the flag in the same place you assert the identifier.

## Two outcomes is a special case, not the shape of the domain

Polymarket's positions-and-tokens page states, quoted, "Each market has exactly two outcome tokens", and states the backing
just as plainly: "Every Yes/No pair in existence is backed by exactly `$1` of pUSD collateral locked in the CTF contract."
Kalshi's `position_fp` is a single signed number over one ticker. Limitless publishes
`winningOutcomeIndex` for a winner-take-all result **and** `payoutNumerators` for a split payout, which is a vector.
Hyperliquid publishes `sideSpecs` as an array. Three different shapes, and only one of them is the YES/NO pair a binary
abstraction assumes.

Write the model as a payout vector over an outcome set of size N and let N equal two, rather than writing a `bool` and
generalising later. The following change with N and each one is a place a binary abstraction silently produces a number:

- **Complement.** With exactly two outcomes the complement of YES is a single tradeable instrument, NO, and its price is
  `1 - p`. With N mutually exclusive outcomes the complement of outcome `i` is the basket of the other `N - 1`. That basket
  has price `1 - p_i` only if the outcome prices sum to one, and it is tradeable as one instrument only where the venue says
  so. Where the venue does not offer the basket, the complement is not a position you can take, and any code that computes
  `1 - p` and calls it a hedge is computing a number with no instrument behind it.
- **Payout.** A binary market pays a fixed unit to one side. Kalshi documents `notional_value_dollars` as, quoted, "The total
  value of a single contract at settlement in dollars", and its settlement record documents `market_result` values of `yes`,
  `no` and `scalar` with a `value` field described as "Payout of a single yes contract in cents". A payout of one dollar per
  contract is a common case, not a rule.
- **Collateral.** The collateral that backs a position is the **maximum aggregate liability across the resolution
  states the market can reach**, not the largest single entry in the payout vector:

      required = max over states s of  sum over outcomes i of  position[i] * payout[i](s)

  `max(payout_vector)` is the same number only when exactly one outcome pays in every state. It understates a
  split resolution, and a split is a state real venues reach. One complete set of a two-outcome market: under
  winner-takes-all the states are `[1,0]` and `[0,1]`, each summing to 1, so `max(...)` and the aggregate agree
  at 1. Under a `[50,50]` split both entries pay, the aggregate is 100 and `max(...)` reads 50, half the
  liability. Under `[70,30]` the aggregate is again 100 while `max(...)` reads 70. Take the maximum over states
  of the total, and enumerate the states the venue documents rather than assuming there are only N of them. Any
  constant `1` in a collateral expression is an assumption about N, about the payout scale, and about the market
  never splitting.

**Do not state a binary property as universal.** Where this file states a relationship that holds only for two outcomes, it
says so in the same sentence. Do the same in the code, with the outcome count asserted next to the arithmetic that assumes it.

## Required assertions

```python
# tests/test_prediction_market_outcome_identity.py
from decimal import Decimal
from fractions import Fraction
import pytest

def test_the_identifier_on_a_fill_was_published_by_the_market(book_fill, market_payload):
    # a wrong identifier does not raise: assert it in the same transaction that books the fill
    assert book_fill.outcome_id in market_payload.published_outcome_ids
    assert book_fill.outcome_id != derive_outcome_id(book_fill)   # read, never recomputed

def test_the_signing_target_is_resolved_from_the_per_market_flag(market_payload, signer):
    # one product, two deployments, selected by the market's own flag rather than a constant
    assert signer.verifying_contract_for(market_payload) == market_payload.exchange_for_flag
    assert signer.collateral_asset(market_payload) == signer.collateral_asset(other_flag_market)

def test_complement_price_only_where_there_are_two_outcomes(binary_market, multi_market):
    assert binary_market.outcome_count == 2
    assert binary_market.price("YES") + binary_market.complement_price("YES") == Decimal(1)
    assert multi_market.outcome_count > 2
    with pytest.raises(NoSingleComplement):          # the complement is a basket, not an instrument
        multi_market.complement_price(multi_market.outcomes[0])

def aggregate_liability(position, payout_states):
    # the maximum over resolution STATES of the total owed in that state, not the largest
    # single entry in any one payout vector
    return max(sum(p * q for p, q in zip(position, state)) for state in payout_states)

def test_collateral_is_the_maximum_aggregate_liability_across_states(multi_market):
    one_complete_set = [1, 1]                       # one of each outcome token

    # winner-takes-all: exactly one outcome pays, so the total is 1 in either state
    assert aggregate_liability(one_complete_set, [[1, 0], [0, 1]]) == 1

    # a 50/50 split pays BOTH entries. max(vector) reads 0.5 and is half the liability.
    assert aggregate_liability(one_complete_set, [[Fraction(1, 2), Fraction(1, 2)]]) == 1
    assert max([Fraction(1, 2), Fraction(1, 2)]) == Fraction(1, 2)   # what the old rule read

    # a 70/30 split is the same total. max(vector) reads 0.7.
    assert aggregate_liability(one_complete_set, [[Fraction(7, 10), Fraction(3, 10)]]) == 1
    assert max([Fraction(7, 10), Fraction(3, 10)]) == Fraction(7, 10)

    # the collateral the venue requires is the aggregate, over every state it documents
    assert multi_market.complete_set_collateral == aggregate_liability(
        one_complete_set, multi_market.documented_payout_states
    )
    assert multi_market.outcome_count_asserted_beside_the_arithmetic is True
```

## What is verified here, and what is not

The provenance block is the authoritative list. These are the items most likely to be mistaken for established facts.

- **Not established:** whether a Polymarket event with more than two outcomes is one market or a set of linked markets. The
  `negRisk` flag exists on the market payload and the positions-and-tokens page says a market has exactly two outcome
  tokens. Those two facts are compatible with more than one structure and this pass did not resolve which.
- **Deleted rather than carried:** the collection-id derivation note. It was a V1-era reading with no URL and no commit
  behind it. The rule it was attached to, that you read an identifier rather than reimplement its derivation, survives on
  its own evidence and is stated above without it.
- **Rates, addresses, tick sets and status enums change without notice.** Re-verify anything you hard-code, and prefer
  reading the value from the market payload over hard-coding it at all.
