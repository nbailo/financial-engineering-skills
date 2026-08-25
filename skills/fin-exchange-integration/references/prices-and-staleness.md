# Prices and staleness

Taking a number off a local book and handing it to the order path. Everything here is about the *consumer* of
a book rather than its construction: what a depth-limited snapshot does not tell you, what an empty or a
crossed book actually means, which of the several available numbers is a price you may quote or size from, and
the wall-clock freshness gate that decides whether any of them is still true. The sibling file
`orderbook-sync.md` covers the other half: how the book is built, how a sequence gap is detected, and the
re-snapshot that clears one. **The venue-specific facts here are dated (read 2026-08-24) and volatile:
re-verify against the venue's current documentation before relying on any of them.**

## Contents

- Snapshot depth limits: a truncated book used for deep-size pricing
- A book you failed to receive is not an empty book; crossed vs locked
- Prices you may quote from: why last trade price is not a quote; mid vs microprice
- Staleness on the book: the wall-clock age gate, distinct from the ordering guard
- Test recipe: absent versus empty, and the crossed book that is not an arbitrage

## Snapshot depth limits

A REST snapshot is depth-limited: 5000 levels per side on Binance Spot. Binance warns that you *"won't learn
the quantities for the levels outside of the initial snapshot unless they change."*

**Levels outside the snapshot horizon are unknown, not empty.** `sum(qty for level in local_book)` is not
available liquidity; it is available liquidity *within the horizon*, plus whatever has ticked since. A
depth-weighted VWAP over the whole local book over-estimates fillable size for any order large enough to reach
past it, and the error is one-directional: you always think there is more depth than there is. Carry the
horizon explicitly: record the worst price present in the snapshot per side at join time, and if a sizing
calculation walks past it, return **an explicit "insufficient known depth" signal**, not a number.

## Absent is not empty; crossed is not locked

**A book you failed to receive is not an empty book.** A subscription that silently died leaves an empty book
object whose `best_bid()` returns `None`, and sizing code that reads `None` as "no liquidity, fall back to last
price" or as zero. Same defect as treating a failed position query as flat: NautilusTrader's reconciler
*"skips cached positions for that venue during the cycle instead of treating missing reports as flat"*. Model
three states: `SYNCED`, `UNSYNCED` (gap seen, resnapshot in flight), `NEVER_RECEIVED`. Let only `SYNCED`
reach the order path.

**Crossed and locked are different, and only one of them is an error.**

| Condition | Meaning | Action |
|---|---|---|
| `best_bid == best_ask` (**locked**) | Valid market state | Trade it; rejecting locked books drops good data |
| `best_bid > best_ask` (**crossed**) | Your reconstruction is wrong, or you merged two venues' books | Integrity error: mark `UNSYNCED`, re-snapshot, suppress quoting |

NautilusTrader's `book_check_integrity` returns `BookIntegrityError::OrdersCrossed` **only** when
`best_bid > best_ask`; `bid == ask` passes. A crossed book on a single venue is almost always your own
mid-update state or a phantom level from a swallowed gap, and code that reads it as an arbitrage fires into
both sides of a market that does not exist: free money in a backtest, a position in production. **A crossed
book is a signal that your reconstruction is wrong, not a trading opportunity.**

## Prices you may quote from

**Last trade price is not a quote.** It is a print: one counterparty pair, at a size you do not know, possibly
from the other side of the book, possibly a wick a single order through thin liquidity produced. Sizing or
bounding an order from it turns one bad print into a position. Two failure shapes from the evidence base:
unrealised PnL and liquidation distance computed from last price (mark price does not move on a single thin
print; last price does), and the silent ladder *mark → last quote → last trade → yesterday's bar close*, where
each rung is a bigger lie and none is surfaced to the caller.

The rule: **use the side-appropriate quote (ask for buys, bid for sells)** and where a worst case is needed,
take the **adverse side, not the mid**. NautilusTrader's risk engine bounds a quote-quantity limit order with
`min(limit, ask)` for buys and `max(limit, bid)` for sells; with no price it logs
`Cannot check order risk: no price available` and `continue`s; **a missing price is a hard stop for sizing,
not a reason to fall back.** Where a fallback ladder is required for *valuation*, it must be ordered, explicit
and reported: the instrument lands in `stale_instruments` on fallback, in `unpriced_instruments` (excluded
from the sum) if it never had a price, and `is_stale` propagates to the caller. FX conversion never silently
falls back to a rate of 1.0.

**Mid vs microprice.** Both are derived numbers; neither is a price you can trade.

```
mid        = (best_bid + best_ask) / 2
microprice = (best_bid * ask_qty + best_ask * bid_qty) / (bid_qty + ask_qty)
```

The size weighting is crossed on purpose: a large bid quantity against a small ask pulls the microprice *up*,
toward the ask, because the thin side is the side that will be consumed. Mid ignores the imbalance entirely and
is therefore the wrong reference when the two sides differ by an order of magnitude, which at the top of a
crypto book is most of the time. Both collapse to the same number when `bid_qty == ask_qty`.

Two things neither one is. First, **neither is an executable price**: quoting size against a mid that sits
half a tick inside the spread understates your cost by exactly that half-tick per side; use the side you will
actually cross. Second, **the top-of-book quantities are the ones you can see**; where a venue publishes
aggregated or throttled depth, `bid_qty`/`ask_qty` are a sample and the microprice inherits that sampling
error. This file's research base establishes the arithmetic above and the side-appropriate-quote rule; it does
**not** establish any claim about microprice as a predictor of future price. Do not encode one on its
authority.

## Staleness on the book

Store every book with the **venue's own event timestamp** and evaluate `now − ts_event > max_age` **on the
order-submission path**, on every tick. Not `ts_init`, not socket-receive time: local receive time hides
venue-side delay, which is exactly the delay you need to see. NautilusTrader carries `ts_event` (venue) and
`ts_init` (local) as separate fields on every data object for this reason.

**This is not the ordering guard, and greping for `stale` will find the wrong one.** `ts < last_seen`
(ordering) and `now − ts > max_age` (freshness) fail in opposite situations: a perfectly ordered feed that
stopped ten seconds ago passes the ordering guard and fails the freshness gate. nautilus's `is_stale`
(`crates/data/src/aggregation.rs:451`) is `ts_init < self.builder.ts_last`, an ordering guard. Write the
`now − ts_event` form explicitly, as its own predicate, on the send path.

The related trap is the snapshot captured before an `await`: an order priced from a book read before a lock, a
rate-limit sleep or a retry backoff is priced from a book hundreds of milliseconds old. Bound the snapshot's
age **relative to the send timestamp**, using venue event time, and re-read or abort past it.

Quoting stops on any of: age > `max_age`, a sequence gap, an unsynced book, or a subscription silent for longer
than its expected inter-message interval. **Track time-since-last-message per subscription**: a socket can be
open, healthy at the TCP level, and delivering nothing.

## Test recipes

**(1) Absent vs empty, and crossed.** Assert that a handler which never received a message makes the sizing
path raise or return an explicit no-price signal rather than reading `best_bid() is None` as zero liquidity;
and that a frame producing `best_bid > best_ask` marks the book `UNSYNCED` and generates no order, including
from the arbitrage path, if one exists.
