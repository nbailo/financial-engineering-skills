# Prediction markets: the cross-venue properties

> **Provenance**
> provider: cross-venue (Polymarket, Kalshi, Limitless, Hyperliquid) · surface: public vendor documentation, REST and WebSocket reference pages · version: Polymarket CLOB V2, Kalshi API v2, Limitless public API, Hyperliquid info endpoint
> verified_at: 2026-08-25
> sources: https://docs.polymarket.com/v2-migration · https://docs.polymarket.com/trading/fees · https://docs.polymarket.com/market-data/market-details · https://docs.polymarket.com/concepts/positions-tokens · https://docs.polymarket.com/concepts/prices-orderbook · https://docs.polymarket.com/concepts/order-lifecycle · https://docs.polymarket.com/api-reference/markets/get-clob-market-info · https://docs.kalshi.com/api-reference/market/get-market · https://docs.kalshi.com/api-reference/portfolio/get-positions · https://docs.kalshi.com/api-reference/orders/create-order-v2 · https://docs.kalshi.com/getting_started/fee_rounding · https://docs.kalshi.com/getting_started/orderbook_responses · https://docs.limitless.exchange/api-reference/trading/create-order · https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint/spot
> pinned: not applicable. No source code, contract address or client-library behaviour is cited in this file. Every claim below is from vendor documentation at the URLs above, fetched on the verified_at date.
> verified: the Polymarket fee formula, its taker-only rule and its documented symmetry around 0.50; the Polymarket tick-size field, its six accepted values and the multiples rule; the Polymarket two-buys-cross mechanic and the pair it mints; the sentence "Each market has exactly two outcome tokens", the `$1` collateral backing sentence and the quoted sell mechanic; the Polymarket order-status enum; Kalshi `price_ranges` and `price_level_structure`; the Kalshi bids-only order book, its ascending sort and the complement rule; Kalshi `position_fp` sign semantics and the absence of an open-interest field on that response; the Kalshi net-fee decomposition, its centicent trade-fee rounding, the balance precisions and the three-fill worked example; the absence of any documented `client_order_id` semantics on the Kalshi create-order page; Kalshi `notional_value_dollars`; the Limitless `clientOrderId` and `settlementStatus` surface; the Hyperliquid `outcomeMeta` and `settledOutcome` field names.
> unverified: whether a Polymarket CLOB V2 sell from a flat account is rejected, and with what error; whether a Polymarket event with more than two outcomes is one market or several linked markets; whether the Polymarket `feeSchedule.*` fields on Gamma and the `fd.{r,e,to}` shape on the CLOB market-info response carry the same values for one market at one instant, and how the basis-point `mbf` and `tbf` members on that same response relate to either; whether Polymarket applies the same fee formula to negative-risk markets; Kalshi's current numeric fee rate and the linearity of the pre-rounding fee in contract count, the schedule PDF having returned HTTP 429 when fetched on 2026-08-25; the Limitless client-order-id uniqueness scope; every Hyperliquid outcome-market identifier beyond the field names listed above.
> revalidate_when: a Polymarket V3 migration notice appears, the accepted tick-size set or the `feeSchedule` field names change, Kalshi publishes a rate change through `get-series-fee-changes`, Kalshi adds or removes a market status, the Kalshi fee schedule becomes readable again, or the Kalshi trade-fee rounding granularity moves off `$0.0001`.

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
- **Polymarket documents selling only as disposal of inventory.** The mechanic on the positions-and-tokens page is, quoted,
  "Sell Yes at `$0.60` → Give up 1 Yes token, receive `$0.60`", against a position the same page describes as yours: "You
  can sell your position at any time before resolution." No page fetched in this pass describes a borrow, a short facility
  or a margined sell. Acquiring the exposure a flat sell would give you means acquiring the complementary token, through a
  purchase or through the documented `splitPosition()` path that turns collateral into a full set. **UNVERIFIED, and the
  distinction matters:** the absence of a documented short facility is not a documented rejection. What the CLOB V2 API
  returns when a flat account submits a sell was not established. Do not assert a specific error, and do not build a retry
  classification on one. Gate the send on inventory you can prove you hold, which is a control you own and does not depend
  on what the venue would have done.

Carry every one of these numbers as an exact decimal. Both venues express prices in fractions of a unit with per-market
increments, and the products above land on repeating binary fractions.

## Two bids can cross, and the invariant spans both books

A CLOB adapter written against a margin venue assumes a bid crosses an ask. On a venue that mints outcome tokens out of
collateral, two *bids* cross each other and the venue manufactures the counterparty rather than finding one. Polymarket's
prices-and-order-book page states the mechanic directly for a Yes buyer at `$0.60` and a No buyer at `$0.40`, quoted:
"Since `$0.60` + `$0.40` = `$1.00`, the orders match" and "`$1.00` is converted into 1 Yes token and 1 No token, each
going to their respective buyers."

That is a documented venue mechanic, not an inference from contract source, and nothing below rests on any particular
deployed exchange. The arithmetic holds for any venue that mints a complementary pair out of collateral, and a venue that
publishes one book per outcome needs the same check for its own reasons.

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

**Do not index the ends of a level array to get top-of-book.** Take `max` over bids and `min` over asks. Sort order is a
per-endpoint documented fact, and it is not the same fact everywhere: Kalshi's order-book page states that its arrays are
"sorted by price in ascending order" and that "the highest bid (best bid) is the last element in each array", so the index
that is correct there is wrong on any venue that sorts the other way. An end index encodes a sort order you would have to
re-verify on every endpoint of every venue; `max` and `min` are correct under either order and cost nothing.

## Tick grids, price bounds, and the difference between them

**The increment is a field, and it is not uniform.** Polymarket's market payload carries `orderPriceMinTickSize`, with
accepted values `0.1`, `0.01`, `0.005`, `0.0025`, `0.001` and `0.0001`. The documentation states both halves of the rule:
"Prices must be multiples of this value" and "Always read the active value from the market rather than assuming a fixed
increment." Multiples, not decimal places. Kalshi goes further: `price_ranges` is an array of
`{start, end, step}` objects where `step` is documented as "Price step/tick size for this range in dollars", alongside a
`price_level_structure` field described as "Price level structure for this market, defining price ranges and tick sizes". The
grid is therefore non-uniform **within one market**, and a single tick constant cannot express it. Derive the validity check
from the live grid, quantize toward validity in exact decimal, and re-check after any round trip through the venue.

**The bound and the grid are two separate checks.** Valid prices run from one tick to one minus one tick, not from zero to
one. On a `0.01`-tick market the extreme quotable probabilities are 1% and 99%, and expressing 99.5% needs a market with a
finer grid. Passing the bound says nothing about whether the price is a multiple of the tick, and sitting on the grid says
nothing about whether it is inside the bound. Rounding to a decimal count satisfies neither: 0.1234 rounded to three
decimals is 0.123, which has three decimals and is not a multiple of 0.005. Run both checks in exact decimal, and run them
again after every round trip. A price you read back and resubmit unchanged is not automatically acceptable: the grid it
must satisfy is the one the market carries now, a venue may accept a different precision on a request than it emits on a
response, and both are per-venue facts to establish rather than assume.

## Fees are a function of expected profit, not notional

**A published fee model here charges on expected profit.** Polymarket documents
`fee = C × feeRate × p × (1 - p)`, with `C` the shares traded and `p` the share price, and states, quoted, "Makers are never
charged fees. Only takers pay fees." Its Crypto rate is 0.07, and its own table prices a $50 trade value at a $1.75 taker
fee, which is that formula at 100 shares and `p = 0.50`. The fee peaks at `p = 0.50` because `p(1-p)` is maximised there.

Two structural consequences hold wherever a venue uses this shape: the fee vanishes at the price bounds, and
`fee(p) = fee(1 - p)`. The second is not an inference here. The same page states it, quoted: "The fee amount in USDC is
symmetric around 50% probability — a trade at 30¢ incurs the same dollar fee as a trade at 70¢." That symmetry is exactly
what the buy-complement identity needs in order to survive fees. Kalshi's `FeeType` enum names `quadratic` as its principal
structure, which has the same shape; its numeric rates live in a schedule outside the API, and that schedule returned
HTTP 429 when fetched on 2026-08-25, so no rate for it is quoted here. A `bps × notional` model has neither property.

**A notional model does not merely mis-set the rate here, it charges nearly all the available profit near the bounds.**
Buying 100 shares of an outcome at 0.99 costs $99 of notional and can earn at most $1.00. A taker fee of 1% *of notional* is
$0.99, which is 99% of the largest profit the trade can produce. That is not the same as reversing the sign. Per contract the
gross gain at certainty is `1 - p`, and a fee quoted as a fraction `f` of notional costs `f * p` on entry, so the trade is
EV-negative at certainty only when

```
f * p > 1 - p        equivalently        f > (1 - p) / p
```

At `p = 0.99` that threshold is `0.01 / 0.99`, about 1.0101%, so a 1% notional fee leaves `$100 - $99.00 - $0.99 = $0.01` on
100 shares: marginally positive, not negative. The threshold moves fast with the price, 11.11% at `p = 0.90` and 100% at
`p = 0.50`, which is the real objection to the model rather than a sign flip at any one price.

**Name the base before you compare a rate to that threshold.** A fee quoted on *payout* costs `f` per contract rather than
`f * p`, so 1% of payout is $1.00 on the same trade, cancels the $1.00 gain exactly, and leaves zero. On that base the
break-even condition is `f > 1 - p`, which is 1% at `p = 0.99`. The two thresholds coincide only at `p = 0.5` and diverge
toward either bound, so a rate carried from one base to the other is wrong by a factor of `p`. The expected-profit model
charges $0.0693 on the same trade, which is the rate applied to `q x p x (1 - p)`, the maximum earnings multiplied by the
implied probability of earning them.

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

**A per-fill ceiling makes the fee super-additive across identical children, and the granularity sets how much that costs.**
Where the pre-rounding fee is linear in contract count, one hundred 1-lot fills at one price pay one hundred ceilings and
one 100-lot fill pays one. The documented Kalshi granularity is the centicent: the fee-rounding page, fetched 2026-08-25,
says the trade fee is "Fee
from the fee model, rounded up to the nearest $0.0001 (centicent)". At that granularity the slicing penalty on a single
component is small, and it is the *rounding fee* and the per-order rebate accumulator, not the trade-fee ceiling, that make
the per-fill number unpredictable. The page's own worked example is the one to reason from: three 1-lot fills of the same
contract at `$0.055` carry net fees of `$0.0150`, `$0.0050` and `$0.0150`, because the second fill is the one where the
accumulator crosses `$0.01` and issues the whole-cent rebate. Three identical fills, three different fees.

**Do not size a slicing schedule against a granularity you have not read.** A coarser ceiling makes the same effect large
rather than small, so the granularity is a per-venue fact with a fetch date beside it, never a constant carried between
venues. **UNVERIFIED for Kalshi:** the pre-rounding fee formula and the current numeric rate, which live in a schedule PDF
that returned HTTP 429 on 2026-08-25. Any worked ratio you compute for a specific venue is a hypothesis until you have both.

The property that survives whatever the rate and the granularity turn out to be is narrower than "slicing costs more". It
holds only where every child shares the same price, the same role (maker or taker), the same fee schedule, the same asset,
the same fee function and the same rounding, and the pre-rounding fee is linear in size with each child rounded up:

```
sum(fee over the children) >= fee(one fill of the same total size)
```

**Outside those conditions splitting can be cheaper, so do not assert it there.** An order walking a price ladder pays a
different `p` per child, and on a `p(1-p)` curve a child near a bound is nearly free. A child that rests rather than takes
pays the maker side, which on a taker-only schedule is nothing. A rebate breaks it directly: the Kalshi accumulator quoted
above returns a whole cent mid-order, so the second of three identical fills there costs less than the first, and where the
rebates outweigh the extra ceilings the sliced total is the cheaper one. Where
the conditions do not hold, reconcile against the fee the venue actually charged and alert when the realised total crosses a
configured bound in either direction, because execution slicing and an aggressive order walking a thin book both multiply the
fee directly and neither raises.

Fee timing moves where PnL is recognised on top of that: a taker charged at match and a maker reimbursed monthly against a
threshold are the same headline rate and a different cash-flow schedule, and a reimbursement with a floor is not receivable
until the floor is met.

The operational rule is the same on every venue: **book the venue's own fee field**, and keep your model as a pre-trade
estimate and a reconciliation tolerance. A fee you recomputed is a hypothesis about the venue's rounding.

## In-band sentinels whose value is the modal real value

A sentinel outside the type's normal range raises somewhere. A sentinel that is a valid value in the same type as real data
does not, and on a probability venue the usual sentinels are the most plausible readings a field can carry.

The shape, with no venue attributed to it, because this pass established no current instance: a price endpoint that
substitutes a default when no trade exists returns a number indistinguishable from a real quote, and a remaining-quantity
field where zero encodes "never touched" is indistinguishable from fully filled. **Do not carry either as a fact about a
named venue on the strength of this file.** Carry the question instead: ask what the endpoint you actually call returns
when the underlying quantity does not exist, establish the answer against a live response, and represent absence in your
own types at the read rather than letting a plausible number travel downstream. Where a venue's *displayed* price changes
definition with liquidity, a midpoint normally and a last trade once the spread widens past a threshold, it is a display
field and never a mark.

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

**Limitless does document one, and it is the strongest of the three.** Its create-order page describes `clientOrderId` as
an "Optional uniqueness and deduplication key. The submitted value may contain at most 128 characters; it is then trimmed
and must not be blank", and states the reuse behaviour: "Reuse returns `409 Conflict`; the API does not replay the earlier
response." Both halves are load-bearing. The venue really does deduplicate on the value you mint, so send one on every
order; and because it will not replay the first response, a 409 is evidence that your first attempt landed rather than a
failure to retry past. Its batch status endpoint `POST /orders/status/batch` is the query rung that turns that evidence
into the order's actual state. **UNVERIFIED:** whether the uniqueness constraint is permanent or scoped to open orders, so
do not design a scheme that reuses a value on the assumption it expires.

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
- **Open interest** counts outstanding contracts without netting, so it is a different number from position by construction,
  and on Kalshi the two live on different objects. `open_interest_fp` is documented on **Market**, as "String representation
  of the number of contracts bought on this market disconsidering netting". The **positions** response does not carry it: as
  fetched on 2026-08-25 it lists `ticker`, `exchange_index`, `total_traded_dollars`, `position_fp`,
  `market_exposure_dollars`, `realized_pnl_dollars`, `fees_paid_dollars` and `last_updated_ts`. Reading a market-wide
  quantity off a positions row, or expecting a positions row to carry one, gets you a number about everybody's contracts
  where you wanted a number about yours.
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

def test_a_flat_sell_is_stopped_by_our_own_inventory_gate(client_polymarket, venue):
    # asserts OUR control, not the venue's error: no venue rejection for a flat sell is
    # established, so nothing here may assert one. The gate is ours and is testable today.
    with pytest.raises(InsufficientOutcomeTokens):
        client_polymarket.sell(token_id=YES_TOKEN, qty=100, price=Decimal("0.40"))   # flat account
    assert venue.orders_received == 0                    # the send never left the process

def test_tick_is_read_from_the_market(client, market):
    assert market.tick in ACCEPTED_TICKS or market.price_ranges     # one venue or the other
    quoted = client.quantize_to_grid(Decimal("0.123456"), market)   # snap to the grid just read
    assert client.is_on_grid(quoted, market)
    assert market.min_price <= quoted <= market.max_price           # [tick, 1 - tick], a separate check

def test_fee_booked_is_the_venue_field(fill):
    assert fill.booked_fee == fill.venue_fee_field                  # never the model
    assert abs(fill.booked_fee - model_fee(fill)) <= RECONCILIATION_TOLERANCE

def test_fee_is_symmetric_under_the_venue_identity(fee_model):
    # buy 100 YES at 0.99 is sell 100 NO at 0.01; a bps-on-notional model differs by 99x here
    assert fee_model(qty=100, price=Decimal("0.99")) == fee_model(qty=100, price=Decimal("0.01"))
    whole = fee_model(qty=100, price=Decimal("0.99"))
    # one price, one role, one schedule, one rounding: the only shape where slicing must cost more
    sliced = sum(fee_model(qty=1, price=Decimal("0.99")) for _ in range(100))
    assert sliced >= whole                                          # never asserted across a walked price
    assert sliced <= whole * MAX_SLICING_FEE_RATIO                  # a configured bound, not a log line

def test_notional_fee_break_even_is_one_minus_p_over_p():
    # 1% of notional at p = 0.99 is NOT EV-negative at certainty; the threshold is 0.01 / 0.99
    p, f = Decimal("0.99"), Decimal("0.01")
    assert f * p < Decimal(1) - p                       # notional base: net +$0.0001 per contract
    assert f * Decimal(1) == Decimal(1) - p             # payout base: the same rate breaks even exactly

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
  quoted mechanic describes giving up a token you hold, which is not the same as a documented rejection. The assertion in
  this file therefore tests the local inventory gate and never a venue error string.
- **Not established:** whether a Polymarket event with more than two outcomes is one market or a set of linked markets. The
  `negRisk` flag exists on the market payload and the positions-and-tokens page says a market has exactly two outcome
  tokens. Those two facts are compatible with more than one structure and this pass did not resolve which.
- **Not established, and it is three surfaces rather than two:** Polymarket names fee parameters in three places with
  nothing relating them. The CLOB market-info response carries `fd.r`, `fd.e` and `fd.to` ("Fee rate", "Fee curve exponent",
  "Whether fees apply to takers only") and, at the same level, `mbf` and `tbf` ("Maker base fee in basis points", "Taker
  base fee in basis points"). Gamma carries `feeSchedule.rate`, `.exponent`, `.takerOnly` and `.rebateRate`. The names and
  the documented meanings are verified; that any two of them carry the same value for one market at one instant is not, and
  no page fetched relates a basis-point member to a rate that is not in basis points. **Treat the live venue metadata for
  the market you are about to trade as the runtime authority,** name in a comment which surface you read, and reconcile
  against the fee the venue actually charged on a fill. Do not read one surface and validate against another.
- **Not established:** Kalshi's current numeric fee rate and the pre-rounding fee formula, and therefore any specific
  super-additivity ratio across fills. The schedule PDF returned HTTP 429 on 2026-08-25. The net-fee decomposition, the
  centicent trade-fee rounding, the balance precisions and the three-fill worked example are quoted and verified; the rate
  is not, and no rate is quoted here.
- **Resolved this pass, previously recorded as a disagreement:** Kalshi's positions response does not carry
  `open_interest_fp`; that field is on `Market`. The field lists above are from the pages fetched on 2026-08-25.
- **Resolved this pass, previously recorded as a disagreement:** the granularity of the Kalshi trade-fee ceiling. The
  current fee-rounding page states the centicent, `$0.0001`. The competing "next cent" reading came from an archived copy
  of the schedule PDF with no fetchable source, so it has been deleted rather than carried as a rival reading.
- **Resolved this pass:** the two-buys-cross mechanic and the pair it mints are documented prose on Polymarket's
  prices-and-order-book page, quoted above. The unsourced V1 contract rule that an earlier pass carried alongside it has
  been deleted; nothing here rests on any deployed exchange's source.
- **Deleted rather than carried:** the collection-id derivation note, the two named in-band sentinel rows, and the claim
  that a named venue documents one endpoint's sort order two contradictory ways. Each was a V1-era reading with no URL and
  no commit behind it. The rules they were attached to survive on their own evidence and are stated above without them.
- **Rates, addresses, tick sets and status enums change without notice.** Re-verify anything you hard-code, and prefer
  reading the value from the market payload over hard-coding it at all.
