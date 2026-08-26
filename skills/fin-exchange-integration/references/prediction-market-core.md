# Prediction markets: tick grids, price bounds and the fee model

> **Provenance**
> provider: cross-venue (Polymarket, Kalshi) · surface: public vendor documentation, market-detail, fee and fee-rounding reference pages · version: Polymarket CLOB V2, Kalshi API v2
> verified_at: 2026-08-25
> sources: https://docs.polymarket.com/market-data/market-details · https://docs.polymarket.com/trading/fees · https://docs.polymarket.com/api-reference/markets/get-clob-market-info · https://docs.kalshi.com/api-reference/market/get-market · https://docs.kalshi.com/getting_started/fee_rounding
> pinned: not applicable. No source code, contract address or client-library behaviour is cited in this file. Every claim below is from vendor documentation at the URLs above, fetched on the verified_at date.
> verified: the Polymarket tick-size field `orderPriceMinTickSize`, its six accepted values and the multiples rule, both halves quoted; the Polymarket fee formula, its taker-only rule and its documented symmetry around 0.50, quoted, together with the worked $50 trade at a $1.75 taker fee and the 0.07 Crypto rate; the `fd.r`, `fd.e`, `fd.to`, `mbf` and `tbf` members on the CLOB market-info response and the `feeSchedule.*` members on Gamma, by name and documented meaning; Kalshi `price_ranges` and `price_level_structure`; the Kalshi `FeeType` enum naming `quadratic`; the Kalshi net-fee decomposition, its centicent trade-fee rounding, the two balance precisions, the per-order rebate accumulator and the three-fill worked example, all quoted.
> unverified: whether Polymarket applies the same fee formula to negative-risk markets; whether the Polymarket `feeSchedule.*` fields on Gamma and the `fd.{r,e,to}` shape on the CLOB market-info response carry the same values for one market at one instant, and how the basis-point `mbf` and `tbf` members relate to either; Kalshi's current numeric fee rate and the linearity of the pre-rounding fee in contract count, the schedule PDF having returned HTTP 429 when fetched on 2026-08-25.
> revalidate_when: the accepted tick-size set or the `feeSchedule` field names change, a Polymarket V3 migration notice appears, Kalshi publishes a rate change through `get-series-fee-changes`, the Kalshi fee schedule becomes readable again, or the Kalshi trade-fee rounding granularity moves off `$0.0001`.

The fee here is a function of expected profit rather than notional, which makes a `bps × notional` model wrong by up to two
orders of magnitude, asymmetrically, across an identity the venue itself guarantees. The price grid is a per-market field
rather than a decimal count, and it is not even uniform within one market. Both produce a wrong number on a code path where
every arithmetic step is individually correct.

**Scope.** The price grid, the bounds around it, the fee model and the fields whose sentinel value is a plausible price.
Outcome identifiers and the payout vector, short exposure and the two-book invariant, and order identity and the settlement
authority split each have their own reference. Venue-specific field names, endpoints and error codes belong in that venue's
own reference.

## Contents

- Tick grids, price bounds, and the difference between them
- Fees are a function of expected profit, not notional
- In-band sentinels whose value is the modal real value
- Required assertions, as code
- What is verified here, and what is not

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
what the buy-complement identity needs to survive fees. Kalshi's `FeeType` enum names `quadratic` as its principal
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

## Required assertions

```python
# tests/test_prediction_market_core.py
from decimal import Decimal
import pytest

ACCEPTED_TICKS = {Decimal(s) for s in ("0.1", "0.01", "0.005", "0.0025", "0.001", "0.0001")}

def test_tick_is_read_from_the_market(client, market):
    assert market.tick in ACCEPTED_TICKS or market.price_ranges     # one venue or the other
    quoted = client.quantize_to_grid(Decimal("0.123456"), market)   # snap to the grid just read
    assert client.is_on_grid(quoted, market)
    assert market.min_price <= quoted <= market.max_price           # [tick, 1 - tick], a separate check

def test_a_decimal_count_satisfies_neither_check(market_with_half_cent_tick):
    # 0.1234 rounded to three decimals is 0.123, which has three decimals and is off the grid
    assert not is_on_grid(Decimal("0.1234").quantize(Decimal("0.001")), market_with_half_cent_tick)

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

def test_fee_is_reconciled_per_order_not_per_fill(order):
    # a per-order rebate accumulator makes a fill's fee a function of the fills before it
    assert order.fee_reconciled_on == "order"
    assert [f.fee for f in order.fills] != [order.fills[0].fee] * len(order.fills)

def test_notional_fee_break_even_is_one_minus_p_over_p():
    # 1% of notional at p = 0.99 is NOT EV-negative at certainty; the threshold is 0.01 / 0.99
    p, f = Decimal("0.99"), Decimal("0.01")
    assert f * p < Decimal(1) - p                       # notional base: net +$0.0001 per contract
    assert f * Decimal(1) == Decimal(1) - p             # payout base: the same rate breaks even exactly

def test_absence_is_represented_at_the_read(price_endpoint):
    # a substituted default is a valid price in the same type as real data and raises nowhere
    price_endpoint.no_trade_has_occurred()
    assert client.read_price(price_endpoint) is ABSENT  # never a plausible number travelling on
```

## What is verified here, and what is not

The provenance block is the authoritative list. These are the items most likely to be mistaken for established facts.

- **Not established, and it is three surfaces rather than two:** Polymarket names fee parameters in three places with
  nothing relating them. The CLOB market-info response carries `fd.r`, `fd.e` and `fd.to` ("Fee rate", "Fee curve exponent",
  "Whether fees apply to takers only") and, at the same level, `mbf` and `tbf` ("Maker base fee in basis points", "Taker
  base fee in basis points"). Gamma carries `feeSchedule.rate`, `.exponent`, `.takerOnly` and `.rebateRate`. The names and
  the documented meanings are verified; that any two of them carry the same value for one market at one instant is not, and
  no page fetched relates a basis-point member to a rate that is not in basis points. **Treat the live venue metadata for
  the market you are about to trade as the runtime authority,** name in a comment which surface you read, and reconcile
  against the fee the venue actually charged on a fill. Do not read one surface and validate against another.
- **Not established:** whether Polymarket applies the same fee formula to negative-risk markets.
- **Resolved this pass, previously recorded as a disagreement:** the granularity of the Kalshi trade-fee ceiling. The
  current fee-rounding page states the centicent, `$0.0001`. The competing "next cent" reading came from an archived copy
  of the schedule PDF with no fetchable source, so it has been deleted rather than carried as a rival reading.
- **Deleted rather than carried:** the two named in-band sentinel rows. Each was a V1-era reading with no URL and no commit
  behind it, which is why the section above attributes the shape to no venue and tells you to establish the answer against
  a live response instead.
- **Rates, addresses, tick sets and status enums change without notice.** Re-verify anything you hard-code, and prefer
  reading the value from the market payload over hard-coding it at all.
