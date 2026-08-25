# Binary prediction markets: client-side quoting, order entry and position keeping

On a binary venue the general CLOB priors are not merely incomplete, they are inverted. There is no short: you buy the
complementary outcome. Two *bids* can cross each other, and the venue mints the instruments to settle them. The no-arbitrage
invariant spans two books. Fee models are not `bps × notional` and are wrong by up to two orders of magnitude, asymmetrically,
under an identity the venue itself guarantees. Applying general priors here produces a wrong collateral number on a code path
that looks entirely correct.

**Scope.** This file is the client side only: reading a book, quoting, sizing, entering orders, paying fees and keeping a
position as a customer of Polymarket, Kalshi or a similar binary CLOB. How such a venue determines an outcome and pays it out
is the venue's own concern; trade as though the answer arrives from outside and you cannot influence it.

## Contents

- Identifiers you read rather than derive, and the exchange contract that follows the flag
- Buying NO is selling YES: the identity, and the collateral number the "short" branch gets wrong
- Two bids crossing: `MatchType.MINT`/`MERGE`, and the no-arbitrage invariant that spans both books
- Top-of-book when the venue documents its own sort order two contradictory ways
- Price bounds `[tick, 1−tick]`, tapered per-market grids, and the request/response precision mismatch
- Fees as a function of expected profit: `k·C·p·(1−p)`, and the exact 99× asymmetry a bps model produces
- Per-fill ceilings, super-additivity, and the net fee you cannot recompute from a fill
- In-band sentinels whose value is the modal real value
- Required assertions, as code; and what in this file is verified and what is not

**Dating.** Every venue fact below was read from primary sources (contract source, vendor docs, the archived Kalshi
fee-schedule PDF) in a pass dated **2026-08-24**. Fee rates, addresses and tick grids are volatile; re-verify anything you
hard-code. The last section states what is verified and what is not.

## Identifiers you read rather than derive

**Token ids are market data, not a local computation.** Read `clobTokenIds` from the venue's market feed and key every order,
fill and position on the value it gives you. Collection ids in particular are **not** hashes: `CTHelpers` carries ~200 lines of
inline-assembly `sqrt` over the alt_bn128 field because ids are hashed to a curve point and **added**, so a keccak
reimplementation of `getCollectionId` produces ids that never match the chain.

**One flag selects two things at once.** Polymarket runs a standard exchange and a negative-risk exchange, and they are
different EIP-712 verifying contracts signing against different collateral tokens. Read the `neg_risk` flag per token and
select **both** the contract you sign against and the collateral address from it. Get it wrong and you have not signed an
invalid order, you have addressed a token that was never minted: the balance read returns 0 and the position disappears from
your ledger with no error at the derivation site, because every arithmetic step downstream is correct on an id nobody holds.

## Buying NO is selling YES

On Polymarket, `Trading._deriveAssetIds` returns `(makerAssetId, takerAssetId) = (0, tokenId)` for BUY and `(tokenId, 0)` for
SELL (collateral is asset id **0**) and `_fillOrder` does `_transfer(order.maker, to, makerAssetId, making)`. A SELL moves
ERC-1155 tokens **out of the maker's balance**. There is no borrow. "Short YES" is not a position the venue can represent.

Kalshi states the identity as an axiom: **"`bid ≡ yes`, `ask ≡ no`, always."** `(buy, yes)` and `(sell, no)` are both
`outcome_side=yes`; `(buy, no)` and `(sell, yes)` are both `outcome_side=no`. *"Direction does not change the price."*
Kalshi's REST book returns **bids only, for both sides**: a YES bid at X is the same object as a NO ask at `$1.00 − X`.

**The bug this produces.** A generic position keeper stores a signed `qty` and reserves `p` per unit of short exposure. A flat
account selling 100 YES at $0.40 is buying 100 NO at $0.60: the reserve is `100 × (1 − 0.40) = $60`, not `100 × 0.40 = $40`.
The keeper under-reserves by `(2p − 1)` per unit, an error that changes sign at p = 0.5, so it looks correct in half your
fixtures. Model the flat-account sell as a buy of the complementary token, with the complementary token id and the
complementary book. Kalshi nets positions (`position_fp` is signed: *"Negative means NO contracts and positive means YES
contracts"*); Polymarket does not, and you can hold both legs at once. Kalshi's `open_interest_fp` counts contracts
*"disconsidering netting"*: two different quantities, both called "position" in casual usage.

**Two price scales, one field name.** Kalshi's WebSocket book reports no-side deltas in **no-leg pricing** by default;
`use_yes_price: true` unifies them, and the docs say the default *"will be flipped to `true` in a future release"*, after
which *"the flag itself will then be removed"*. Set it explicitly: a client relying on the default silently receives the
other scale on the day it flips, and the same code inverts every no-side level.

## Two bids crossing, and the invariant across two books

`CalculatorHelper._isCrossing`: two BUYs cross iff `priceA + priceB >= ONE`; two SELLs cross iff `priceA + priceB <= ONE`.
`Trading._deriveMatchType` maps `BUY + BUY → MatchType.MINT` and `SELL + SELL → MatchType.MERGE`: the venue manufactures the
counterparty rather than finding one. Polymarket's docs, in prose: *"Since `$0.60` + `$0.40` = `$1.00`, the orders match…
`$1.00` is converted into 1 Yes token and 1 No token."*

Two consequences. First, **marketability is a two-book question**: a YES buy at 0.55 is marketable against an empty YES ask
book if a NO bid rests at 0.45, so liquidity computed from one book understates depth and mis-predicts fills. Second, **the
uncrossed-book invariant spans both books:**

```
bestBid(YES) + bestBid(NO)  <  1  <  bestAsk(YES) + bestAsk(NO)
```

Anything else is a matchable state. Cheap, always true, and it catches stale books, swapped token ids and an unconverted
no-leg price scale on the first snapshot. Assert it on every snapshot carrying both legs; alert and stop quoting rather than
quoting through it.

**Do not index the ends of the arrays to get top-of-book.** Polymarket documents the same endpoint's sort order two
contradictory ways: the guide says *"Bids are ordered by ascending price and asks by descending price, so the best bid and ask
are the last entries"* (its JSON example agrees: `bids: 0.01, 0.02, 0.03…`), while the OpenAPI schema for that endpoint says
bids are *"sorted by price descending"*. The two also disagree on `min_order_size` (`"5"` vs `'1'`). Take `max`/`min` over the
levels. **Which document is currently correct is unverified; the contradiction itself is the verified fact.**

## Price bounds and tick grids

**Valid prices are `[tick, 1 − tick]`, not `[0, 1]`.** Polymarket's SDK: `price_valid(price, tick_size) → price >=
float(tick_size) and price <= 1 - float(tick_size)`. On a `0.01`-tick market the extreme quotable probabilities are 1% and
99%; expressing 99.5% needs a `0.001`-tick market. The legal tick set is exactly `{0.1, 0.01, 0.001, 0.0001}` (the
`ROUNDING_CONFIG` keys), and the SDK's own `__resolve_tick_size` caches the market's tick with a **300-second TTL**, so a tick
change produces up to five minutes of rejected orders.

**Kalshi's grid is non-uniform, per-market, and mutable.** `price_ranges` (an array of `{start, end, step}` bands) is *"the
source of truth for valid prices: any price on the grid is valid, and any off-grid price is rejected"*. Thirteen named
structures exist and the docs say explicitly **"Do not key pricing logic off this name"** (`price_level_structure` is a label,
not a contract). Two of them:

| Structure | Low band | Middle | High band |
|---|---|---|---|
| `tapered_deci_cent` | $0.001 below $0.10 | $0.01 | $0.001 above $0.90 |
| `center_deci_edge_centi_cent` | $0.0001 below $0.01 | n/a | $0.0001 above $0.99 |

Rationale, verbatim: near the bounds *"small absolute price differences represent large relative changes in implied
probability"*. On `tapered_deci_cent`, `$0.095` is on-grid (0.001 band) and `$0.505` is not (0.01 band); derive the check
from the market's live `price_ranges`, never a hard-coded step, and subscribe to `price_level_structure_updated` on the
lifecycle channel, which delivers a **new** `price_ranges` mid-life.

**Request and response precision differ.** Kalshi `FixedPointDollars`: *"Most request fields accept 2-4 decimal places…;
responses emit up to 6."* Reading `last_price_dollars` and resubmitting it as an order price produces both off-grid rejections
and silent truncation. Quantize toward validity against the live grid, in `Decimal`, after any venue round-trip.

**The venue SDK does this in `float`.** `py_clob_client/order_builder/builder.py` computes `raw_maker_amt = raw_taker_amt *
raw_price`, then rounds **up** at `round_config.amount + 4` digits and **down** at the target, a workaround necessary only
because `size * price` in binary floating point lands on `…9999` and `…0001` values. The amounts you sign are float-derived:
carry your own as `Decimal` and compare before you sign.

## Fees are a function of expected profit, not notional

Both major venues charge on **expected profit**, and both fee functions vanish at the price bounds:

| Venue | Formula | Rates read 2026-08-24 |
|---|---|---|
| Kalshi (fee schedule PDF, "effective Oct 1, 2025") | `fees = round up(0.07 x C x P x (1-P))`, `round up = rounds to the next cent` | general `0.07`; maker `0.0175`; INX / NASDAQ-100 `0.035`. **"There is no settlement fee."** |
| Polymarket (docs) | `fee = C × feeRate × p × (1 − p)` | crypto table peaks at **$1.75 per 100 shares at p = 0.50**, and is **$0.07 at both p = 0.01 and p = 0.99**. Fees rounded to 5 dp; smallest fee charged 0.00001 USDC. **"Makers are never charged fees. Only takers pay fees."** |
| Polymarket on-chain | BUY: `fee = (feeRateBps * min(price, ONE - price) * outcomeTokens) / (price * BPS_DIVISOR)`; SELL: `fee = feeRateBps * min(price, ONE - price) * outcomeTokens / (BPS_DIVISOR * ONE)` | guarded by `if (price > 0 && price <= ONE)`; `MAX_FEE_RATE_BIPS = 1000` |

Kalshi's rationale, verbatim: *"Trading fees are charged as a variable percentage fee of the expected earnings on an
individual contract, which is calculated by multiplying the maximum potential earnings from the contract by the implied
probability of making those earnings."*

**A notional model is not miscalibrated, it is incoherent.** Buying 100 shares of YES at $0.99 costs $99 and can earn at most
$1.00. A 1%-of-notional taker fee is **$0.99 (99% of maximum possible profit)**, so the trade is EV-negative at *any* true
probability. The `p(1−p)` model charges $0.07: 7% of maximum profit, the same 7% it charges at p = 0.50.

**The asymmetry, exactly.** The venue guarantees `buy 100 YES @ 0.99 ≡ sell 100 NO @ 0.01`. One economic trade:

```
bps-on-notional, 100 bps:
    YES leg: 100 × 0.99 × 0.01 = $0.9900
    NO  leg: 100 × 0.01 × 0.01 = $0.0100      →  ratio 99×, two orders of magnitude

expected-profit model (Polymarket crypto, feeRate 0.07):
    YES leg: 100 × 0.07 × 0.99 × 0.01 = $0.0693
    NO  leg: 100 × 0.07 × 0.01 × 0.99 = $0.0693      →  ratio 1.00×, identical
```

`fee(p) = fee(1 − p)` is **required** for the buy-NO/sell-YES identity to survive fees, and the deployed Polymarket exchange
enforces the shape structurally with `min(price, ONE − price)`. A router carrying a bps model routes to whichever leg has the
smaller notional, systematically, and under-reserves fees by up to `p/(1−p)` on the other leg.

**Polymarket's fee rate is signed into the order.** `feeRateBps` is a field in `ORDER_TYPEHASH` and `_validateOrder` rejects
`order.feeRateBps > getMaxFeeRate()`; it cannot be renegotiated after signing, and "makers pay nothing" is an operator policy
expressed by signing `feeRateBps = 0`, not a contract invariant. Resolve it from the venue before signing.

## Per-fill ceilings and super-additivity

**Kalshi's "round up to the next cent" makes the fee super-additive across fills.** From the fee-schedule tables at `P =
$0.99`:

| Order shape | General schedule (0.07) | INX / NASDAQ-100 (0.035) |
|---|---|---|
| one 100-lot fill | `ceil(0.07 × 100 × 0.99 × 0.01)` = **$0.07** | `ceil(0.035 × 100 × 0.99 × 0.01)` = **$0.04** |
| one hundred 1-lot fills | `100 × ceil(0.000693)` = `100 × $0.01` = **$1.00** | `100 × $0.01` = **$1.00** |
| ratio | **14.3×** | **25×** |

Execution slicing, or an aggressive order walking a thin book, multiplies the fee directly. `Σ fee(1) ≥ fee(N)` always; assert
it and alert when the ratio exceeds a configured bound.

**And the net fee is not a pure function of `(price, quantity)`.** Kalshi's current model has three components per fill: a
trade fee ceiled to $0.0001, a rounding fee that floors the balance change **toward −∞** to the member's precision ($0.0001
for direct members, $0.01 otherwise), and a rebate, with an accumulator that *"is maintained per order across all fills
regardless of whether the fills are taker or maker"*, issuing a whole-cent rebate once accumulated rounding exceeds $0.01. The
older maker-fee reimbursement path is monthly with a floor: *"reimbursed in the first week of the following month if their
reimbursement exceeds $10."* Net fee = `trade fee + rounding fee − rebate`, always ≥ $0.00, **not recomputable from a single
fill.** Book the venue's own fee field; use your model as a pre-trade estimate and a reconciliation tolerance only.

Fee timing moves where P&L is recognised. **Polymarket** charges the taker at match, in kind from proceeds (`_fillOrder`
transfers `taking - fee`: outcome tokens on a BUY, collateral on a SELL). **Kalshi** charges the taker at match, and maker
fees at match on `quadratic_with_maker_fees` series.

## In-band sentinels whose value is the modal real value

Every one is a valid-looking value in the same type as real data, so a client that treats absence as a number reads a plausible
one.

| Field | Sentinel | Also means |
|---|---|---|
| Polymarket `get-last-trade-price` | `"0.5"`: *"Returns default values of `\"0.5\"` for price … if no trades found"* | a genuine coin-flip market |
| `orderStatus[hash].remaining` | `0` = never touched (`remaining == 0 ? order.makerAmount : remaining`) | fully filled |

Polymarket's *displayed* price is the midpoint **unless** *"the spread is wider than `$0.10`"*, in which case it is the last
trade, a mark whose definition changes with liquidity. Do not use it as a mark.

## Required assertions

```python
# tests/test_binary_venue_invariants.py
from decimal import Decimal

def test_books_do_not_cross_across_legs(yes_book, no_book):
    # bids cross at pA + pB >= 1 (venue MINTs); asks cross at pA + pB <= 1 (venue MERGEs)
    assert max(l.price for l in yes_book.bids) + max(l.price for l in no_book.bids) < Decimal(1)
    assert min(l.price for l in yes_book.asks) + min(l.price for l in no_book.asks) > Decimal(1)
    assert top_of_book(yes_book) == top_of_book(reversed_levels(yes_book))   # never bids[0] / bids[-1]

def test_fee_is_symmetric_under_the_venue_identity(fee_model):
    # buy 100 YES @ 0.99  ==  sell 100 NO @ 0.01; the venue guarantees this
    assert fee_model(qty=100, price=Decimal("0.99")) == fee_model(qty=100, price=Decimal("0.01"))
    assert sum(fee_model(qty=1, price=Decimal("0.99")) for _ in range(100)) >= fee_model(100, Decimal("0.99"))

def test_flat_sell_reserves_the_complement(keeper):
    keeper.place(token="YES", side="SELL", qty=100, price=Decimal("0.40"))   # flat account
    assert keeper.reserved_collateral == Decimal("60")     # 100 × (1 − 0.40), not 40

def test_a_round_tripped_price_is_requantized(client, market):
    # responses emit more precision than requests accept, and the grid is per-market
    px = client.last_price(market)                         # e.g. Decimal("0.123456")
    quoted = client.quantize_to_grid(px, market.price_ranges)
    assert on_grid(quoted, market.price_ranges)
    assert Decimal(market.tick) <= quoted <= Decimal(1) - Decimal(market.tick)
```

## What is verified here, and what is not

**Verified against primary sources** (contract source in `Polymarket/ctf-exchange`, `Polymarket/py-clob-client`;
`docs.polymarket.com`, `docs.kalshi.com`; the Kalshi fee schedule PDF dated "effective Oct 1, 2025" via the Wayback Machine):
every contract function name, `require` string, constant and formula quoted above, the fee formulas and rates, the tick-grid
structures, the `[tick, 1−tick]` bound, and the sentinel table.

**Explicitly unverified, do not build on these:**

- Which of Polymarket's two contradictory order-book sort-order documents is in force (the contradiction is verified; the
  resolution is not). Same for `min_order_size` `"5"` vs `'1'`.
- Contract addresses, fee rates and TTLs, dated 2026-08-24 and changing without notice.
