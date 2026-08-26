# Prediction markets: short exposure, the flat-sell reserve and the two-book invariant

> **Provenance**
> provider: cross-venue (Polymarket, Kalshi) · surface: public vendor documentation, concept and order-book reference pages · version: Polymarket CLOB V2, Kalshi API v2
> verified_at: 2026-08-25
> sources: https://docs.polymarket.com/concepts/positions-tokens · https://docs.polymarket.com/concepts/prices-orderbook · https://docs.kalshi.com/api-reference/portfolio/get-positions · https://docs.kalshi.com/getting_started/orderbook_responses
> pinned: not applicable. No source code, contract address or client-library behaviour is cited in this file. Every claim below is from vendor documentation at the URLs above, fetched on the verified_at date.
> verified: the Polymarket sell mechanic quoted from the positions-and-tokens page and the sentence that a position may be sold at any time before resolution; the Polymarket two-buys-cross mechanic and the pair it mints, quoted from the prices-and-order-book page; Kalshi `position_fp` sign semantics, quoted; the Kalshi bids-only order book, its ascending sort and the statement that the best bid is the last element.
> unverified: whether a Polymarket CLOB V2 sell from a flat account is rejected, and with what error.
> revalidate_when: Polymarket documents a short facility or a margined sell, Polymarket changes the two-buys-cross mechanic or the collateral it mints, Kalshi changes the sign convention on `position_fp`, or Kalshi's order-book response stops being bids-only or changes its sort order.

The general CLOB priors are not merely incomplete here, they are inverted. There is no borrow: short exposure is the
purchase of the complementary outcome, and whether a flat account can take it at all differs by venue. Two *bids* can cross
each other, and the venue mints the instruments that settle them rather than finding a counterparty, so the no-arbitrage
invariant spans two books instead of one. Applying general priors produces a wrong collateral number on a code path where
every arithmetic step is individually correct.

**Scope.** Short exposure, the collateral a flat sell obligates, and the no-arbitrage check across the two legs. Outcome
identifiers and the payout vector, the price grid and the fee model, and order identity each have their own reference.
Venue-specific field names, endpoints and error codes belong in that venue's own reference.

## Contents

- Short exposure is a purchase of the complement, and the flat-sell reserve a naive keeper gets wrong
- Two bids can cross, and the invariant spans both books
- Required assertions, as code
- What is verified here, and what is not

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

## Required assertions

```python
# tests/test_prediction_market_complement_and_books.py
from decimal import Decimal
import pytest

def test_books_do_not_cross_across_the_two_legs(yes_book, no_book):
    # two bids cross at a sum of one or more, and the venue mints the pair that settles them
    assert max(l.price for l in yes_book.bids) + max(l.price for l in no_book.bids) < Decimal(1)
    assert min(l.price for l in yes_book.asks) + min(l.price for l in no_book.asks) > Decimal(1)
    assert top_of_book(yes_book) == top_of_book(reversed_levels(yes_book))   # max/min, never an end index

def test_marketability_is_a_two_book_question(router, yes_book, no_book):
    # a YES buy at 0.55 is marketable against an empty YES ask book if a NO bid rests at 0.45
    yes_book.asks.clear()
    no_book.bids.add(price=Decimal("0.45"), size=100)
    assert router.is_marketable(side="BUY", outcome="YES", price=Decimal("0.55")) is True

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
```

## What is verified here, and what is not

The provenance block is the authoritative list. These are the items most likely to be mistaken for established facts.

- **Not established:** the behaviour of a Polymarket CLOB V2 sell from a flat account, including the error it produces. The
  quoted mechanic describes giving up a token you hold, which is not the same as a documented rejection. The assertion in
  this file therefore tests the local inventory gate and never a venue error string.
- **Resolved this pass:** the two-buys-cross mechanic and the pair it mints are documented prose on Polymarket's
  prices-and-order-book page, quoted above. The unsourced V1 contract rule that an earlier pass carried alongside it has
  been deleted; nothing here rests on any deployed exchange's source.
- **Deleted rather than carried:** the claim that a named venue documents one endpoint's sort order two contradictory ways.
  It was a V1-era reading with no URL and no commit behind it. The rule it was attached to, that you take `max` over bids
  and `min` over asks rather than indexing an end, survives on its own evidence and is stated above without it.
- **Rates, addresses, tick sets and status enums change without notice.** Re-verify anything you hard-code, and prefer
  reading the value from the market payload over hard-coding it at all.
