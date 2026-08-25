# Oracle reads and what a price is

A push feed is stale between updates by design, and a price that is correct is still not a valuation. The five
checks run in order; the last section is about what the number means rather than how fresh it is.

## Contents

- **Oracle reads**: the five checks, in order, with the deprecation.
- **What a price is**: spot as a quantity, not a valuation.

## Oracle reads

`AggregatorV3Interface.latestRoundData()` returns
`(uint80 roundId, int256 answer, uint256 startedAt, uint256 updatedAt, uint80 answeredInRound)`.
Chainlink push feeds update *"when the value deviates beyond a specified threshold **or** when the heartbeat
idle time has passed"*, and the docs state flatly that they *"do not provide streaming data."* **Between
updates the feed is stale by design**, so a read with no freshness check is a read of an unknown-age price.

Five checks, in the order they must run:

| # | Check | Failure it catches |
|---|---|---|
| 1 | **Never `latestAnswer()`** | *"No timestamp is included to check data freshness"*, the whole class |
| 2 | `answer > 0` and `updatedAt != 0` | uninitialised / absent round |
| 3 | `block.timestamp - updatedAt <= heartbeat + slack`, where `heartbeat` is **that feed's published heartbeat read from configuration** | a low-volatility feed that has not moved for hours, liquidating healthy positions at a stale mark |
| 4 | answer is **strictly inside** the aggregator's `minAnswer` / `maxAnswer` bounds | Venus (BSC) and Blizz (Avalanche), 2022-05-13: the LUNA/USD aggregator's `minAnswer` floor of **$0.10** kept reporting $0.10 as LUNA went to ~$0, so LUNA stayed borrowable at ~100× its market value. **−$13.5M and −$8.3M**; Blizz could not pause in time because of its timelock |
| 5 | **On an L2**, the sequencer-uptime feed, with a grace period | see below |

Two things the check list does not contain, deliberately:

- **`answeredInRound` is documented as "Deprecated — Previously used when answers could take multiple rounds
  to be computed"** in Chainlink's current API reference. A large body of audit boilerplate still demands
  `require(answeredInRound >= roundId)`. Treat it as optional and note the deprecation in the PR; do not
  build the freshness guarantee on it.
- **A heartbeat constant you chose.** The heartbeat is a per-feed, per-chain published parameter. A single
  `MAX_STALENESS = 3600` applied to every feed is either too tight (spurious reverts on a 24h-heartbeat feed)
  or useless (a 1h-heartbeat feed 20 hours stale passes).

The `minAnswer`/`maxAnswer` bounds live on the **underlying aggregator**, not on `AggregatorV3Interface`;
you reach them through the proxy's current aggregator, and their values are per-feed configuration you must
record alongside the heartbeat. *(The exact accessor names on the deployed aggregator are not established by
the sources behind this file; read them off the contract.)*

**L2 sequencer uptime.** Chainlink's prescribed pattern: read the uptime feed, where `answer == 0` means the
sequencer is **up** and `1` means **down**; after recovery, require
`block.timestamp - startedAt > GRACE_PERIOD_TIME` (the docs' worked example uses 3600 s) before trusting any
L2 price. The failure this prevents: sequencer down two hours, feed frozen, sequencer returns, liquidation
bots fire in the first block against pre-outage prices before any user could top up collateral.
**Arbitrum-specific:** *"The `startedAt` variable returns `0` only on Arbitrum when the Sequencer Uptime
contract is not yet initialized"* (on other L2s `startedAt` is never 0), so `block.timestamp - 0` passes a
naive grace check trivially. Reject `startedAt == 0` explicitly.

**Feed decimals are not token decimals.** ETH/USD is 8; some feeds are 18; the token is whatever the token
says. Scale by the **feed's** `decimals()` and the **token's** `decimals()` as two separate reads, both
cached by address. Mixing them is a silent 10ⁿ error in a collateral computation, the same shape as
scaling a 6-decimal USDC amount by `1e18`, and just as invisible.

---

## What a price is

An AMM spot price, or any single-venue price, is **a quantity you can buy at the margin**, not a valuation.
Using one as the input to a solvency, collateral or liquidation decision means the position holder can set
their own collateral value if the venue is thin enough.

The worked case is Compound's DAI feed, **2020-11-26**. The Open Price Feed took DAI from **Coinbase Pro
alone**. DAI briefly printed ~$1.30 there while Kraken and Huobi stayed near $1.00, and **~$89M of positions
were liquidated across 124 of 225,793 users** (dYdX took ~$8M more). Nobody has to have attacked it: *the
"adverse market conditions vs manipulation" debate is unresolved and irrelevant; the design is wrong either
way.* A single-venue feed is a design defect on the day it ships.

Mango Markets, **2022-10-12**, is the same failure with the opposite label: the reported price was
**correct**. MNGO spot moved $0.03 → $0.91 on roughly $5M of buying because the market was thin, unrealised
PnL on that mark was accepted as collateral, and $115M of bad debt followed. No oracle check in the previous
section would have fired. What is missing there is a **liquidity-aware haircut** and a cap on how much of a
position's collateral value may derive from a mark the holder can move.

TWAPs bound *cost*, not *truth*. A TWAP over window `W` forces an attacker to hold the manipulated price for
a meaningful fraction of `W`, which prices the attack; it does not make the resulting number a valuation,
and it makes the feed lag by construction, which is its own liquidation hazard on a real move. State the
window, state what holding the price for that window costs on the specific venue, and compare that to the
maximum extractable value. If you cannot write those two numbers down, the feed is not sized.

**Two parameters bound every transaction whose ordering you do not control**, and both are correctness
parameters rather than UX ones: an explicit, non-zero, caller-supplied `amountOutMin` (or maximum-input), and
a bounded `deadline`. `amountOutMin = 0` is "give the sandwicher whatever they want"; the loss shows up as
market impact in the P&L and is never diagnosed. `deadline = type(uint256).max` is what lets a swap evicted
from the mempool at a low fee mine hours later against a market that has moved; the deadline is the thing
that makes it fail instead.
