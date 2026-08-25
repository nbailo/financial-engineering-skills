# Prediction markets: the cross-venue properties

> **Provenance**
> provider: cross-venue (Polymarket, Kalshi, Limitless, Hyperliquid) · surface: public vendor documentation, REST and WebSocket reference pages · version: Polymarket CLOB V2, Kalshi API v2, Limitless public API, Hyperliquid info endpoint
> verified_at: 2026-08-25
> sources: https://docs.polymarket.com/v2-migration · https://docs.polymarket.com/trading/fees · https://docs.polymarket.com/market-data/market-details · https://docs.polymarket.com/concepts/positions-tokens · https://docs.polymarket.com/trading/positions/manage · https://docs.kalshi.com/api-reference/market/get-market · https://docs.kalshi.com/api-reference/portfolio/get-positions · https://docs.kalshi.com/api-reference/orders/create-order-v2 · https://docs.kalshi.com/getting_started/fee_rounding · https://docs.limitless.exchange/developers/programmatic-api · https://hyperliquid.gitbook.io/hyperliquid-docs · https://kalshi.com/docs/kalshi-fee-schedule.pdf (an archived copy read in a pass dated 2026-08-24; the live URL returned HTTP 429 on 2026-08-25 and was not re-read)
> pinned: not applicable. The V1 Polymarket exchange behaviour cited below as history carries no commit and no address, which is why it is marked unverified rather than pinned. Every other claim is from vendor documentation at the URLs above, read on the date stated beside it.
> verified: the fee formula and taker-only rule on Polymarket; the Polymarket tick-size field and its accepted values; the Polymarket V2 signed-struct field changes; the sentence "Each market has exactly two outcome tokens" and the quoted sell mechanic; Kalshi `price_ranges` and `price_level_structure`; Kalshi `position_fp` sign semantics; the Kalshi net-fee decomposition and balance precisions; the absence of any documented `client_order_id` semantics on the Kalshi create-order page; Kalshi `notional_value_dollars`; the Limitless order and portfolio surface; the Hyperliquid `outcomeMeta` field names.
> unverified: whether the current Polymarket exchange still mints a complementary pair when two buys cross, and the prose and V1 contract rule behind that claim, both carried from a pass dated 2026-08-24 and not re-read on 2026-08-25; the granularity a Kalshi trade fee is ceiled to, where the archived schedule read on 2026-08-24 says the next cent and the fee-rounding page read on 2026-08-25 says $0.0001; which of a venue's two contradictory order-book sort-order documents is in force; the two in-band sentinel rows and the collection-id derivation note, all carried from the 2026-08-24 pass with no recorded URL or commit; whether a Polymarket CLOB V2 sell from a flat account is rejected, and with what error; whether a Polymarket event with more than two outcomes is one market or several linked markets; whether the Polymarket `feeSchedule.*` fields on Gamma and the `{ r, e, to }` shape on the migration page describe the same parameters; whether Polymarket applies the same fee formula to negative-risk markets; Kalshi's current fee rate and the linearity of the pre-rounding fee in contract count; whether Kalshi still exposes `open_interest_fp` on the positions response; the Limitless client-order-id convention and its EIP-712 domain parameters; every Hyperliquid outcome-market name and identifier beyond the four field names listed above.
> revalidate_when: a Polymarket V3 migration notice appears, the accepted tick-size set or the `feeSchedule` field names change, Kalshi publishes a rate change through `get-series-fee-changes`, Kalshi adds or removes a market status, the Kalshi fee schedule becomes readable again, or Limitless documents an order-id convention.

The general CLOB priors are not merely incomplete here, they are inverted. There is no borrow: short exposure is the
purchase of the complementary outcome, and whether a flat account can take it at all differs by venue. Two *bids* can cross
each other, and the venue mints the instruments that settle them rather than finding a counterparty, so the no-arbitrage
invariant spans two books instead of one. The fee is a function of expected profit rather than notional, which makes a
`bps × notional` model wrong by up to two orders of magnitude, asymmetrically, across an identity the venue itself
guarantees. The instrument's payout is a number the venue publishes rather than a constant. Applying general priors produces
a wrong collateral number on a code path where every arithmetic step is individually correct.

**Scope.** Cross-venue properties only: the ones that hold on more than one venue, and the ones whose shape differs by venue
in a way a shared abstraction will get wrong. Venue-specific field names, endpoints and error codes belong in that venue's
own reference. Settlement lifecycle handling belongs in the settlement-integration reference. Nothing here describes how a
venue determines an outcome.

## Contents

- Outcome identity is market data, never a local computation
- Two outcomes is a special case, not the shape of the domain
- Short exposure is a purchase of the complement, and the flat-sell reserve a naive keeper gets wrong
- Two bids can cross, and the invariant spans both books
- Tick grids, price bounds, and the difference between them
- Fees are a function of expected profit, not notional
- In-band sentinels whose value is the modal real value
- Order identity and ambiguous submission on a venue whose uniqueness token you sign
- Fills, open interest, position and PnL are four quantities, not one
- Trading state and settlement state have different authorities
- Reconciliation: one authority and one join key per quantity
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

Two identifiers in this family get recomputed instead of read, and both fail in exactly that silent way.

**An identifier that looks like a hash is not necessarily computed as one.** A pass dated 2026-08-24 read the conditional
token helper behind Polymarket's outcome tokens and recorded that a collection id is hashed to a point on the alt_bn128
curve and then **added**, which is why the helper carries an inline-assembly square root rather than a keccak call, and why
a keccak reimplementation produces ids that never match the chain. **UNVERIFIED:** no URL or commit was recorded for that
reading and it was not repeated on 2026-08-25, so carry it as the shape of the trap rather than as a current fact. The
operational rule does not depend on it. Read the identifier from the venue's feed and never reimplement a derivation you
can read.

**A per-market flag can select the contract you sign against.** Polymarket runs two verifying contracts for one product,
selected by whether the market is a negative-risk market, and both sign under the same EIP-712 domain version; the
addresses belong in that venue's own reference. **The flag selects the exchange contract, not a different collateral
asset.** The V1-era claim that negative risk also switches the collateral token is wrong for V2. Where a venue runs more
than one deployment for one product, resolve the signing target from the per-instrument flag rather than from a constant,
and assert the resolved target against the flag in the same place you assert the identifier.

## Two outcomes is a special case, not the shape of the domain

Polymarket's positions page states, quoted, "Each market has exactly two outcome tokens", and describes the pair as backed by
a fixed unit of collateral. Kalshi's `position_fp` is a single signed number over one ticker. Limitless publishes
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
- **Collateral.** The collateral that backs a complete set is a function of the maximum payout across the outcome set, not of
  the number two. Any constant `1` in a collateral expression is an assumption about both N and the payout scale.

**Do not state a binary property as universal.** Where this file states a relationship that holds only for two outcomes, it
says so in the same sentence. Do the same in the code, with the outcome count asserted next to the arithmetic that assumes it.

## Short exposure is a purchase of the complement, and there is no borrow

"Short YES" is not a position a binary venue represents. The exposure is a purchase of NO, at the complementary price, on
the complementary book, and on some venues a flat account cannot take it at all. One economic trade therefore has two
names, and the venues that publish the identity publish it as an axiom rather than as a convenience: a YES bid at `X` is
the same resting object as a NO ask at `1 - X`, and direction does not move the price. A position keeper that carries a
signed quantity against one price scale is modelling a margin venue that is not there.

Prices on these venues are probabilities in the open interval between zero and one, and the interesting arithmetic is what a
position obligates you to rather than what it costs. For a binary market with a unit payout, quantity `q` and price `p`:

```
buy  q of an outcome at p           cost      = q * p          maximum loss = q * p
hold q of an outcome                payout    = q * v          v is the venue's settlement value per contract
sell q of an outcome you hold at p  proceeds  = q * p          inventory disposal, no new obligation
sell q of an outcome from flat      reserve   = q * (1 - p)    only where the venue can represent the resulting position
```

The last line is the one that gets written wrong. A flat-account sell of YES at `p` is economically a purchase of the
complement at `1 - p`, so the collateral it obligates is `q * (1 - p)`. A position keeper that carries a signed quantity and
reserves `q * p` for short exposure, which is the correct reserve on a margin venue, reserves the wrong side of the book.

**The error term, exactly.** Required reserve minus naive reserve is

```
q * ((1 - p) - p) = q * (1 - 2p)
```

Worked, with `q = 100`:

| p | required `q * (1 - p)` | naive `q * p` | shortfall `q * (1 - 2p)` | effect |
|---|---|---|---|---|
| 0.40 | 60 | 40 | +20 | under-reserved by 20 |
| 0.50 | 50 | 50 | 0 | correct by coincidence |
| 0.75 | 25 | 75 | -50 | over-reserved by 50 |

The term is `1 - 2p`, and `2p - 1` is its negation. The negation is not a sign convention, it is a different claim: it says
the keeper under-reserves above `p = 0.5` and over-reserves below it, which is backwards. Because the true error changes sign
at `p = 0.5`, a fixture set drawn from favourites alone confirms the wrong formula and a fixture set drawn from longshots
alone confirms the right one. Test both sides of `0.5` explicitly, and assert the shortfall value rather than its sign.

**Partition the flat sell by venue. Neither semantics is universal.**

- **Kalshi can represent it.** `position_fp` is documented as, quoted, "String representation of the number of contracts
  bought in this market. Negative means NO contracts and positive means YES contracts". One signed quantity spans both sides,
  so a flat-account YES sell resolves into complementary NO exposure and the reserve is `q * (1 - p)` on the NO leg. Model it
  as the purchase of the complement, with the complementary price, and reconcile against the venue's signed number.
- **Polymarket requires the seller to own the tokens being sold.** The documented mechanic is, quoted, "Sell Yes at $0.60 →
  Give up 1 Yes token, receive $0.60", against a position the page describes as yours: "You can sell your position at any
  time before resolution." Nothing in the pages read in this pass describes a borrow, a short facility or a margined sell.
  Acquiring the exposure a flat sell would give you means acquiring the complementary token, through a purchase or through
  the documented `splitPosition()` path that turns collateral into a full set. **UNVERIFIED:** what the CLOB V2 API returns
  when a flat account submits a sell, and whether the rejection is local to the client library or comes from the venue. Do
  not build a retry classification on an assumed error.

Carry every one of these numbers as an exact decimal. Both venues express prices in fractions of a unit with per-market
increments, and the products above land on repeating binary fractions.

## Two bids can cross, and the invariant spans both books

A CLOB adapter written against a margin venue assumes a bid crosses an ask. On a venue that mints outcome tokens out of
collateral, two *bids* cross each other and the venue manufactures the counterparty rather than finding one. A pass dated
2026-08-24 recorded Polymarket documentation stating the mechanic in prose, quoted: "Since `$0.60` + `$0.40` = `$1.00`,
the orders match... `$1.00` is converted into 1 Yes token and 1 No token", and recorded the V1 exchange contract
implementing it as two buys crossing at a price sum of one or more and two sells crossing at a price sum of one or less,
mapping to a mint and a merge respectively. **UNVERIFIED:** neither was re-read on 2026-08-25, and the current exchange is
a different deployed address whose source was not read. The V1 rule is cited as history, not as a live contract. Nothing
below depends on it: the arithmetic holds for any venue that mints a complementary pair out of collateral, and a venue
that publishes one book per outcome needs the same check for its own reasons.

Two consequences, and both are cheap.

First, **marketability is a two-book question.** A YES buy at 0.55 is marketable against an entirely empty YES ask book if
a NO bid rests at 0.45. Depth computed from one book understates liquidity, mis-predicts fills, and makes a router decline
a trade that would have filled.

Second, **the uncrossed-book invariant spans both books:**

```
bestBid(YES) + bestBid(NO)  <  1  <  bestAsk(YES) + bestAsk(NO)
```

Anything else is a matchable state. The check is cheap, it holds on every venue that mints and merges a complementary
pair, and it catches stale books, swapped outcome identifiers and an unconverted opposite-leg price scale on the first
snapshot that carries both legs. Assert it there, and stop quoting rather than quoting through it. Where a venue publishes
bids only, for both outcomes, derive each ask from the opposite leg and apply the same invariant to the derived numbers;
the one-book no-arbitrage check a generic CLOB adapter runs is not merely weaker there, it is vacuous, because a crossed
state on a single leg is not representable.

**Do not index the ends of a level array to get top-of-book.** Take `max` over bids and `min` over asks. The earlier pass
recorded one venue documenting a single endpoint's sort order two contradictory ways, ascending in the guide and its own
JSON example and descending in the OpenAPI schema for that same endpoint, with the two documents also disagreeing on the
minimum order size. **UNVERIFIED:** which document was in force, and whether either still says what was recorded. The
contradiction was the observation, not its resolution. An end index is correct under one document and silently inverted
under the other; `max` and `min` are correct under both, and cost nothing.

## Tick grids, price bounds, and the difference between them

**The increment is a field, and it is not uniform.** Polymarket's market payload carries `orderPriceMinTickSize`, with
accepted values `0.1`, `0.01`, `0.005`, `0.0025`, `0.001` and `0.0001`, and the documentation states, quoted, "Always read the
active value from the market rather than assuming a fixed increment." Kalshi goes further: `price_ranges` is an array of
`{start, end, step}` objects where `step` is documented as "Price step/tick size for this range in dollars", alongside a
`price_level_structure` field described as "Price level structure for this market, defining price ranges and tick sizes". The
grid is therefore non-uniform **within one market**, and a single tick constant cannot express it. Derive the validity check
from the live grid, quantize toward validity in exact decimal, and re-check after any round trip through the venue.

**The bound and the grid are two separate checks.** Valid prices run from one tick to one minus one tick, not from zero to
one. On a `0.01`-tick market the extreme quotable probabilities are 1% and 99%, and expressing 99.5% needs a market with a
finer grid. Passing the bound says nothing about whether the price is a multiple of the tick, and sitting on the grid says
nothing about whether it is inside the bound. Rounding to a decimal count satisfies neither: 0.1234 rounded to three
decimals is 0.123, which has three decimals and is not a multiple of 0.005. Run both checks in exact decimal, and run them
again after every round trip, because responses routinely carry more precision than requests accept and a price you read
back and resubmit unchanged is rejected off-grid or silently truncated.

## Fees are a function of expected profit, not notional

**A published fee model here charges on expected profit.** Polymarket documents
`fee = C x feeRate x p x (1 - p)`, with `C` the shares traded and `p` the share price, and states, quoted, "Makers are never
charged fees. Only takers pay fees." Its worked example is 100 shares in the Crypto category at 0.50, giving
`100 x 0.07 x 0.50 x 0.50 = $1.75`, and the fee peaks at `p = 0.50` because `p(1-p)` is maximised there. Two structural
consequences hold wherever a venue uses this shape: the fee vanishes at the price bounds, and `fee(p) = fee(1 - p)`, which is
exactly the symmetry required for the buy-complement identity to survive fees. Kalshi's `FeeType` enum names `quadratic` as
its principal structure, which has the same shape; its numeric rates live in a schedule outside the API and are not quoted
here. A `bps × notional` model has neither property.

**A notional model is not miscalibrated here, it is incoherent.** Buying 100 shares of an outcome at 0.99 costs $99 and can
earn at most $1.00. A taker fee of 1% of notional is $0.99, which is 99% of the largest profit the trade can produce, so the
trade is EV-negative at every true probability, certainty included. The expected-profit model charges $0.0693 on the same
trade, which is the rate applied to `q x p x (1 - p)`, the maximum earnings multiplied by the implied probability of earning
them.

**Watch which denominator you validate the model against.** Fee over *expected* profit is the rate itself and does not move
with `p`, which is the property the model is built on. Fee over *maximum* profit is `rate x p`, which does move: 6.93% at
p = 0.99 and 3.5% at p = 0.50 on the same 0.07 rate. The two ratios are routinely swapped in commentary, and a fee test that
asserts a constant share of maximum profit fails against a correct implementation.

**The asymmetry, exactly.** The venue guarantees that buying 100 YES at 0.99 is the same economic trade as selling 100 NO
at 0.01. One trade, two names, and the two fee models disagree about whether the two names cost the same:

```
bps on notional, 100 bps:
    YES leg: 100 * 0.99 * 0.01 = $0.9900
    NO  leg: 100 * 0.01 * 0.01 = $0.0100      ratio 99x, close to two orders of magnitude

expected-profit model, rate 0.07:
    YES leg: 100 * 0.07 * 0.99 * 0.01 = $0.0693
    NO  leg: 100 * 0.07 * 0.01 * 0.99 = $0.0693      ratio 1.00x, identical
```

A router carrying a notional model routes to whichever leg has the smaller notional, systematically and on every trade,
and under-reserves the fee on the other leg by up to `p / (1 - p)`. **Check every component of the charge, not the headline
curve.** A component proportional to collateral rather than to `p(1-p)`, a builder or referral fee for instance, breaks the
symmetry back open even where the platform curve keeps it, and the same drift returns through the component nobody modelled.

**The realised fee is not always recomputable from the fill.** Kalshi documents the net fee as, quoted, "**Net fee** = trade
fee + rounding fee - rebate (always >= $0.00)", where the trade fee is "rounded up to the nearest $0.0001 (centicent)", the
rounding fee comes from "Floor `balance_change` toward negative infinity to the user's target balance precision.
`rounding_fee = balance_change - floor(balance_change)`", target precision is `$0.0001` for direct members and `$0.01`
otherwise, and a rebate accumulator "tracks cumulative rounding overpayment across all fills of an order. Once the accumulated
rounding exceeds $0.01, a whole-cent rebate is issued and the accumulator is reduced by $0.01." A per-order accumulator makes
the fee on a fill a function of the fills before it, so the net fee is not a pure function of price and quantity and cannot be
recomputed from one fill in isolation. Reconcile total fee per order rather than per fill.

**Ceiling each fill makes the fee super-additive, and the granularity of the ceiling is the whole effect.** Where the
pre-rounding fee is linear in contract count, one hundred 1-lot fills pay one hundred ceilings and one 100-lot fill pays one.
What that costs depends entirely on what the fee is ceiled to. With a quadratic rate of 0.07 at p = 0.99, the pre-rounding
fee on a 1-lot fill is $0.000693 and on a 100-lot fill is $0.0693:

| ceiling granularity | one 100-lot fill | one hundred 1-lot fills | ratio |
|---|---|---|---|
| whole cent | $0.07 | `100 x $0.01` = $1.00 | 14.3x |
| centicent, `$0.0001` | $0.0693 | `100 x $0.0007` = $0.07 | 1.01x |

**The two readings of Kalshi disagree on which granularity applies,** and the disagreement is recorded rather than resolved.
An archived fee schedule read in a pass dated 2026-08-24 says the fee rounds up to the next cent; the fee-rounding page read
on 2026-08-25 and quoted above says the trade fee is "rounded up to the nearest $0.0001 (centicent)". That is the difference
between a fourteen-fold fee surprise on a sliced order and none at all, so read the granularity from the venue's current page
before you size a slicing schedule. **UNVERIFIED** either way: the pre-rounding formula and the current rate were not read in
this pass, and the rate above is carried from the earlier one.

The property that survives whatever the rate and the granularity turn out to be is
`sum(fee over the fills) >= fee(one fill of the same total size)`. Assert it, and alert when the ratio crosses a configured
bound, because execution slicing and an aggressive order walking a thin book both multiply the fee directly and neither
raises. Fee timing moves where PnL is recognised on top of that: a taker charged at match and a maker reimbursed monthly
against a threshold are the same headline rate and a different cash-flow schedule, and a reimbursement with a floor is not
receivable until the floor is met.

The operational rule is the same on every venue: **book the venue's own fee field**, and keep your model as a pre-trade
estimate and a reconciliation tolerance. A fee you recomputed is a hypothesis about the venue's rounding.

## In-band sentinels whose value is the modal real value

A sentinel outside the type's normal range raises somewhere. A sentinel that is a valid value in the same type as real data
does not, and on a probability venue the usual sentinels are the most plausible readings a field can carry.

| Field | Sentinel | Also a real value, meaning |
|---|---|---|
| a last-trade-price endpoint that substitutes a default when no trade exists | `0.5` | a genuine coin-flip market |
| a remaining-quantity field where zero encodes "never touched" | `0` | fully filled |

Both rows were read from Polymarket V1 surfaces in the 2026-08-24 pass and **were not re-verified.** They are kept as the
shape of the trap rather than as current venue facts, because the shape recurs: ask what the endpoint you actually call
returns when the underlying quantity does not exist, and represent absence in your own types at the read rather than letting
a plausible number travel downstream. Where a venue's *displayed* price changes definition with liquidity, a midpoint
normally and a last trade once the spread widens past a threshold, it is a display field and never a mark.

## Order identity and ambiguous submission

The general rule holds here: mint one identity from the intent instance, commit it durably before the send, and resolve an
ambiguous outcome by asking the venue about the identity you sent rather than sending again. What differs is where the
uniqueness token lives, and on one venue it lives inside the bytes you sign.

**Polymarket V2 moved uniqueness into the signed struct.** The migration page records that `taker`, `expiration`, `nonce` and
`feeRateBps` were removed from the signed order, and that `timestamp` in milliseconds was added and "replaces nonce for
uniqueness", alongside `metadata` and `builder` as bytes32 fields. Fees are, quoted, "determined by the protocol at match
time, not embedded in your signed order." The consequence for retry handling is direct, and it is an inference from those
quoted facts rather than a documented sentence: **re-signing a timed-out submission produces a new timestamp, therefore a
different signed order, therefore a second order.** The retry path and the duplicate-order path are the same path. Sign once,
persist the signed payload with its timestamp, and resend those exact bytes or query, never re-sign.

**Kalshi's client order id has no documented semantics.** The create-order page lists `client_order_id` as optional and
carries no description of it, no statement about idempotency, and no statement about what happens when the same value is sent
twice (page read 2026-08-25). Required fields are `ticker`, `side`, `count`, `price`, `time_in_force` and
`self_trade_prevention_type`, and the response carries `order_id`. Treat `client_order_id` as a correlation key only. It
deduplicates nothing until the venue documents that it does.

**Limitless does not document the convention.** The programmatic API page shows `POST /orders` with delegated fields
`marketSlug`, `orderType` in `GTC`, `FAK`, `FOK`, `onBehalfOf` and `args`, and responses carrying `orderId`, but the
client-supplied versus venue-supplied identifier convention is not stated on that page. **UNVERIFIED.** Its batch status
endpoint `POST /orders/status/batch` is the query rung for resolving an unknown outcome.

**An order can rest in a state that is neither working nor rejected.** Polymarket documents order statuses `live`, `matched`,
`delayed` and `unmatched`, a separate enum from its trade statuses. A `delayed` order exists and can still fill, so absence
from a list filtered on `live` is not evidence that no order was created. Enumerate the venue's full status set before
writing the query that resolves an ambiguous submit.

## Fills, open interest, position and PnL are four quantities

They are routinely all called "position" in conversation and they are not the same number.

- **Fills** are the venue's execution records. They are the input to everything below and the only one with a natural
  identity to dedupe on.
- **Position** is your net exposure. Kalshi nets it into one signed `position_fp`. A venue whose outcomes are separate
  tokens can leave you holding both legs at once, in which case a single signed number cannot represent what you hold.
- **Open interest** counts outstanding contracts without netting, so it is a different number from position by construction.
  The repo's earlier pass, dated 2026-08-24, recorded `open_interest_fp` on Kalshi's positions response; the same page read
  on 2026-08-25 lists `position_fp`, `market_exposure_dollars`, `realized_pnl_dollars` and `fees_paid_dollars` and does not
  list an open-interest field. **The two readings disagree and the disagreement is recorded rather than resolved.** Read the
  field list from the response you actually receive.
- **PnL** is a function of the economic order of fills, not their arrival order, so the same fills folded in a different
  order are a different realised number. Read the venue's own totals where it publishes them. Kalshi documents
  `realized_pnl_dollars` as "Locked in profit and loss, in dollars" and `fees_paid_dollars` as "Fees paid on fill orders, in
  dollars". Limitless exposes `GET /portfolio/positions` and `GET /portfolio/history`, and a separate
  `unrealizedPnlProjectionChanged` WebSocket event whose own name says it is a projection. A projection is not a booked
  number and must not be posted as one.

Settlement and redemption are PnL components you never instructed, exactly as funding and liquidation are on a perpetuals
venue. They arrive without an order of yours behind them, so a feed filtered to "activity whose identity I generated"
excludes them.

## Trading state and settlement state have different authorities

This is the split that a single venue client tends to collapse, and it is the reason the settlement lifecycle is a separate
concern from order handling.

**Trading state** is orders, fills, the book and your working exposure. The authority is the venue's matching system, it
answers when asked, and it answers quickly.

**Settlement state** is what an outcome pays and whether you have received it. The authority may be somewhere else entirely.
On Polymarket and Limitless the value transfer is on a chain, and the venue's message about it is a notification about a fact
the chain holds. On Kalshi the venue's own ledger is the authority and the market status carries the answer.

Two authorities means two reconciliations, on different cadences, against different join keys, with different definitions of
"final". A client that reads its settled balance from the same message that told it a trade matched has one authority where
there are two.

## Reconciliation: one authority and one join key per quantity

Name both for every economic quantity you report, and ship the comparison as a scheduled entrypoint that reads through a path
independent of the writer.

| Quantity | Authority | Join key | Caveat |
|---|---|---|---|
| fills | the venue's fills endpoint | the venue's own trade or fill identity | cumulative totals where published beat summed deltas |
| position | the venue's positions endpoint | outcome identifier, per every dimension the venue separates | a netting venue and a token venue disagree on what one row means |
| fees | the venue's fee field on the fill | fill identity | a per-order accumulator makes the model an estimate only |
| realised PnL | the venue's own total where published | position key | your fold must use the venue's economic order |
| settled value | the venue's settlement record, or the chain | see the settlement-integration reference | not the same authority as the trading side |

Express tolerance in the market's own tick or contract unit rather than a percentage, choose a cadence longer than the
venue's documented replication lag, and make a break close the risk gate rather than write a log line.

## Required assertions

```python
# tests/test_prediction_market_core.py
from decimal import Decimal
import pytest

ACCEPTED_TICKS = {Decimal(s) for s in ("0.1", "0.01", "0.005", "0.0025", "0.001", "0.0001")}

def test_complement_price_only_where_there_are_two_outcomes(binary_market, multi_market):
    assert binary_market.outcome_count == 2
    assert binary_market.price("YES") + binary_market.complement_price("YES") == Decimal(1)
    assert multi_market.outcome_count > 2
    with pytest.raises(NoSingleComplement):          # the complement is a basket, not an instrument
        multi_market.complement_price(multi_market.outcomes[0])

def test_books_do_not_cross_across_the_two_legs(yes_book, no_book):
    # two bids cross at a sum of one or more, and the venue mints the pair that settles them
    assert max(l.price for l in yes_book.bids) + max(l.price for l in no_book.bids) < Decimal(1)
    assert min(l.price for l in yes_book.asks) + min(l.price for l in no_book.asks) > Decimal(1)
    assert top_of_book(yes_book) == top_of_book(reversed_levels(yes_book))   # max/min, never an end index

def test_flat_sell_reserve_is_the_complement_kalshi(keeper_kalshi):
    # Kalshi represents the resulting position as NO exposure on one signed quantity
    keeper_kalshi.place(ticker="X", side="SELL", outcome="YES", qty=100, price=Decimal("0.40"))
    assert keeper_kalshi.reserved == Decimal("60")        # 100 * (1 - 0.40), never 100 * 0.40
    assert keeper_kalshi.position_fp == Decimal("-100")   # negative means NO contracts

@pytest.mark.parametrize("p,shortfall", [(Decimal("0.40"), Decimal("20")),
                                         (Decimal("0.50"), Decimal("0")),
                                         (Decimal("0.75"), Decimal("-50"))])
def test_naive_reserve_error_is_one_minus_two_p(p, shortfall):
    # the error term is q * (1 - 2p) and it changes sign at 0.5; q * (2p - 1) is the negation
    q = Decimal(100)
    assert q * (Decimal(1) - p) - q * p == q * (Decimal(1) - 2 * p) == shortfall

def test_polymarket_sell_requires_inventory(client_polymarket):
    # the documented mechanic gives up a token you hold; there is no borrow
    with pytest.raises(InsufficientOutcomeTokens):
        client_polymarket.sell(token_id=YES_TOKEN, qty=100, price=Decimal("0.40"))   # flat account

def test_tick_is_read_from_the_market(client, market):
    assert market.tick in ACCEPTED_TICKS or market.price_ranges     # one venue or the other
    quoted = client.quantize_to_grid(Decimal("0.123456"), market)   # responses out-precision requests
    assert client.is_on_grid(quoted, market)
    assert market.min_price <= quoted <= market.max_price           # [tick, 1 - tick], a separate check

def test_fee_booked_is_the_venue_field(fill):
    assert fill.booked_fee == fill.venue_fee_field                  # never the model
    assert abs(fill.booked_fee - model_fee(fill)) <= RECONCILIATION_TOLERANCE

def test_fee_is_symmetric_under_the_venue_identity(fee_model):
    # buy 100 YES at 0.99 is sell 100 NO at 0.01; a bps-on-notional model differs by 99x here
    assert fee_model(qty=100, price=Decimal("0.99")) == fee_model(qty=100, price=Decimal("0.01"))
    whole = fee_model(qty=100, price=Decimal("0.99"))
    sliced = sum(fee_model(qty=1, price=Decimal("0.99")) for _ in range(100))
    assert sliced >= whole                                          # ceilings are super-additive
    assert sliced <= whole * MAX_SLICING_FEE_RATIO                  # a configured bound, not a log line

def test_identity_survives_an_ambiguous_submit(client, venue):
    # resolve by asking about the identity you sent; never re-sign, never resend
    with venue.timeout_after_transmission():
        client.submit(intent_id="i-1")
    assert client.resolve(intent_id="i-1").order_count == 1
    assert client.signed_payload("i-1") == client.signed_payload("i-1")   # bytes are stable
```

## What is verified here, and what is not

Everything quoted above was read from the vendor pages listed in the provenance block on 2026-08-25. The provenance block's
`verified` and `unverified` lists are the authoritative statement; the items below are the ones most likely to be mistaken for
established facts.

- **Not established:** the behaviour of a Polymarket CLOB V2 sell from a flat account, including the error it produces. The
  quoted mechanic describes giving up a token you hold, which is not the same as a documented rejection.
- **Not established:** whether a Polymarket event with more than two outcomes is one market or a set of linked markets. The
  `negRisk` flag exists on the market payload and the positions page says a market has exactly two outcome tokens. Those two
  facts are compatible with more than one structure and this pass did not resolve which.
- **Not established:** whether Polymarket's Gamma `feeSchedule.rate`, `feeSchedule.exponent`, `feeSchedule.takerOnly` and
  `feeSchedule.rebateRate` describe the same parameters as the `{ r, e, to }` shape named on the migration page. Read both
  and reconcile them at runtime rather than assuming one maps onto the other.
- **Not established:** Kalshi's current fee rate, the pre-rounding fee formula, and therefore the exact super-additivity
  ratio across fills. The net-fee decomposition and the rounding rules are quoted and verified; the rate is not.
- **In disagreement:** whether Kalshi's positions response still carries `open_interest_fp`. Recorded above, unresolved.
- **In disagreement:** the granularity a Kalshi trade fee is ceiled to, whole cent or centicent. Recorded above, unresolved,
  and it changes the cost of a sliced order by roughly fourteen times at the price bounds.
- **Not established:** whether the current Polymarket exchange keeps the V1 rule that two buys crossing at a price sum of one
  mint a complementary pair. The documentation prose and the V1 contract rule both come from a pass dated 2026-08-24 and were
  not re-read; the current exchange is a different deployed address whose source was not read. The two-book invariant is
  arithmetic about any mint-and-merge venue and does not rest on that rule.
- **Not established:** which of a venue's two contradictory order-book sort-order documents is in force, or whether either
  still says what the earlier pass recorded. The contradiction was the observation. Taking `max` and `min` is correct under
  both readings, which is why the rule does not depend on resolving it.
- **Not established:** the two in-band sentinel rows and the collection-id derivation note. All three are V1-era readings
  carried from the earlier pass with no recorded URL or commit, kept as the shape of a trap rather than as current facts.
- **Rates, addresses, tick sets and status enums change without notice.** Re-verify anything you hard-code, and prefer
  reading the value from the market payload over hard-coding it at all.
