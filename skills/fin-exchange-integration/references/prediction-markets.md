# Prediction markets — Polymarket, Kalshi and binary CLOBs

On a binary venue the general CLOB priors are not merely incomplete, they are inverted. There is no short: you buy the
complementary outcome. Two *bids* can cross each other, and the venue mints the instruments to settle them. The no-arbitrage
invariant spans two books. Fee models are not `bps × notional` and are wrong by up to two orders of magnitude, asymmetrically,
under an identity the venue itself guarantees. Applying general priors here produces a wrong collateral number on a code path
that looks entirely correct.

## Contents

- Complete sets: `splitPosition`/`mergePositions`/`redeemPositions`, N-outcome partitions, the floor that makes merge ≠ redeem
- Identifiers: what `conditionId` and a position id are functions of, and the collateral-token trap
- Buying NO is selling YES: the identity, and the collateral number the "short" branch gets wrong
- Two bids crossing: `MatchType.MINT`/`MERGE`, and the no-arbitrage invariant that spans both books
- Price bounds `[tick, 1−tick]`, tapered per-market grids, and the request/response precision mismatch
- Fees as a function of expected profit: `k·C·p·(1−p)`, and the exact 99× asymmetry a bps model produces
- Per-fill ceilings, super-additivity, and the net fee you cannot recompute from a fill
- Negative-risk conversion: the arithmetic, the wrapped collateral, the burn address that is not `address(0)`
- Resolution: dispute discards the proposal, TOO EARLY reopens the market, `[1,1]` is a legal payout vector, `amended`
  restarts a settlement timer, the CTF commit is irreversible
- Open orders at resolution, and the states in which you cannot cancel
- Open positions at resolution, and a balance you credited before the result changed
- In-band sentinels whose value is the modal real value
- Required assertions, as code; and what in this file is verified and what is not

**Dating.** Every venue fact below was read from primary sources — contract source, vendor docs, the archived Kalshi
fee-schedule PDF — in a pass dated **2026-08-24**. Fee rates, addresses, tick grids and lifecycle enums are volatile;
re-verify anything you hard-code. The last section states what is verified and what is not.

## Complete sets and the conservation invariant

**The primitive is `1 collateral unit ⇄ 1 unit of each of N outcomes`, enforced at the contract, not by convention.**
`ConditionalTokens.splitPosition(collateralToken, parentCollectionId, conditionId, partition, amount)` pulls `amount`
collateral and mints `amount` of **every** position in the partition; `mergePositions` is the exact inverse. Polymarket states
the consequence: *"Every Yes/No pair in existence is backed by exactly `$1` of pUSD collateral locked in the CTF contract."*

Four properties that break naive ledgers:

| Property | Source | Consequence |
|---|---|---|
| The invariant is per `(conditionId, collateralToken)`, not global | `getPositionId(collateralToken, collectionId)` mixes the collateral address into the ERC-1155 id | Two collateral tokens on one condition = two disjoint token sets and two independent pools |
| The partition is a bitmask; splitting is not restricted to the full set | `splitPosition` accepts any disjoint partition with `0 < indexSet < fullIndexSet`; when `freeIndexSet != 0` the source is the **combined** position, not collateral | Splitting `$:(A\|C)` into `$:(A)` and `$:(C)` on a 3-outcome condition burns one id and mints two with **no collateral movement** — a ledger reconciling "collateral in = tokens out" sees a mint with no funding event |
| Positions nest; redeeming a nested position pays in **tokens**, not money | with a non-zero `parentCollectionId`, `redeemPositions` ends in `_mint(msg.sender, getPositionId(collateralToken, parentCollectionId), totalPayout, "")` | "Redeem ⇒ cash arrives" is wrong for every leg of a Polymarket Combo (`Y(A ^ N(B ^ C))` is a depth-3 CTF position) above the last |
| Redemption truncates **per position id**, then sums | `totalPayout = totalPayout.add(payoutStake.mul(payoutNumerator).div(den))` inside the loop | Merge-then-withdraw and redeem-both are not the same number |

**Worked case — merge before you redeem.** Condition resolves 50/50 (`payoutNumerators = [1,1]`, `payoutDenominator = 2`); you
hold 3 base units of YES and 3 of NO.

```
redeemPositions(collateral, bytes32(0), conditionId, indexSets=[1,2])
  → floor(3 × 1 / 2) + floor(3 × 1 / 2) = 1 + 1 = 2 base units
mergePositions(collateral, bytes32(0), conditionId, [1,2], 3)   // no resolution check
  → 3 collateral base units, exactly
```

The one-base-unit deficit stays in the CTF permanently; there is no sweep function. Generalised: an N-outcome condition
resolving `[1,1,…,1]` (den = N) pays `N·floor(x/N) ≤ x` on a complete set of size `x`. **If the payout vector is not
`[1,0]`-shaped, merge first** — and mirror the chain's arithmetic exactly: `floor`, per position id, not round-half-up on the
total. The bug is invisible until the first non-trivial payout vector, because `den` is 1 on every ordinary resolution.

## Identifiers: what a position id is a function of

`getConditionId = keccak256(abi.encodePacked(oracle, questionId, outcomeSlotCount))` — a different oracle address or slot
count is a **different condition** with independent state. The two initialisation sentinels mean different things:
`payoutNumerators[id].length == 0` is *not prepared*; `payoutDenominator[id] == 0` is *not resolved*. Collection ids are
**not** hashes — `CTHelpers` carries ~200 lines of inline-assembly `sqrt` over the alt_bn128 field because they are hashed to
a curve point and **added**, so a keccak reimplementation of `getCollectionId` produces ids that never match the chain. Read
`clobTokenIds` from market data.

**The trap that silently zeroes a position:** Polymarket neg-risk markets use `WrappedCollateral` (`wcol`), not USDC, as the
CTF collateral token — `NegRiskAdapter.getPositionId` calls `CTHelpers.getPositionId(address(wcol), collectionId)`. Deriving
neg-risk ids with the USDC address yields tokens that were never minted: balance reads return 0, redemption reverts, and the
position disappears from your ledger with no error at the derivation site. Read the `neg_risk` flag per token and select both
the collateral address **and** the exchange contract from it; the neg-risk and standard exchanges are different EIP-712
verifying contracts. And because `NegRiskOperator.prepareCondition(...)` is an explicit **no-op**, the UMA adapter's
`questionID` (`keccak256(ancillaryData ‖ ",initializer:" ‖ creator)`) and the CTF `conditionId` are unrelated identifiers on
those markets — resolve the condition from `NegRiskAdapter`, never from the UMA request hash.

## Buying NO is selling YES

On Polymarket, `Trading._deriveAssetIds` returns `(makerAssetId, takerAssetId) = (0, tokenId)` for BUY and `(tokenId, 0)` for
SELL — collateral is asset id **0** — and `_fillOrder` does `_transfer(order.maker, to, makerAssetId, making)`. A SELL moves
ERC-1155 tokens **out of the maker's balance**. There is no borrow. "Short YES" is not a position the venue can represent.

Kalshi states the identity as an axiom: **"`bid ≡ yes`, `ask ≡ no`, always."** `(buy, yes)` and `(sell, no)` are both
`outcome_side=yes`; `(buy, no)` and `(sell, yes)` are both `outcome_side=no`. *"Direction does not change the price."*
Kalshi's REST book returns **bids only, for both sides** — a YES bid at X is the same object as a NO ask at `$1.00 − X`.

**The bug this produces.** A generic position keeper stores a signed `qty` and reserves `p` per unit of short exposure. A flat
account selling 100 YES at $0.40 is buying 100 NO at $0.60: the reserve is `100 × (1 − 0.40) = $60`, not `100 × 0.40 = $40`.
The keeper under-reserves by `(2p − 1)` per unit — an error that changes sign at p = 0.5, so it looks correct in half your
fixtures. Model the flat-account sell as a buy of the complementary token, with the complementary token id and the
complementary book. Kalshi nets positions (`position_fp` is signed: *"Negative means NO contracts and positive means YES
contracts"*); the CTF does not — you can hold both legs simultaneously on-chain. Kalshi's `open_interest_fp` counts contracts
*"disconsidering netting"*: two different quantities, both called "position" in casual usage.

**Two price scales, one field name.** Kalshi's WebSocket book reports no-side deltas in **no-leg pricing** by default;
`use_yes_price: true` unifies them, and the docs say the default *"will be flipped to `true` in a future release"*, after
which *"the flag itself will then be removed"*. Set it explicitly — a client relying on the default silently receives the
other scale on the day it flips, and the same code inverts every no-side level.

## Two bids crossing, and the invariant across two books

`CalculatorHelper._isCrossing`: two BUYs cross iff `priceA + priceB >= ONE`; two SELLs cross iff `priceA + priceB <= ONE`.
`Trading._deriveMatchType` maps `BUY + BUY → MatchType.MINT` and `SELL + SELL → MatchType.MERGE`, and `_executeMatchCall`
calls `splitPosition` / `mergePositions` on the CTF to manufacture the counterparty. Polymarket's docs, in prose: *"Since
`$0.60` + `$0.40` = `$1.00`, the orders match… `$1.00` is converted into 1 Yes token and 1 No token."*

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
levels. **Which document is currently correct is unverified — the contradiction itself is the verified fact.**

## Price bounds and tick grids

**Valid prices are `[tick, 1 − tick]`, not `[0, 1]`.** Polymarket's SDK: `price_valid(price, tick_size) → price >=
float(tick_size) and price <= 1 - float(tick_size)`. On a `0.01`-tick market the extreme quotable probabilities are 1% and
99%; expressing 99.5% needs a `0.001`-tick market. The legal tick set is exactly `{0.1, 0.01, 0.001, 0.0001}`, and the client
caches the market's tick with a **300-second TTL**, so a tick change produces up to five minutes of rejected orders.

**Kalshi's grid is non-uniform, per-market, and mutable.** `price_ranges` — an array of `{start, end, step}` bands — is *"the
source of truth for valid prices: any price on the grid is valid, and any off-grid price is rejected"*. Thirteen named
structures exist and the docs say explicitly **"Do not key pricing logic off this name"** (`price_level_structure` is a label,
not a contract). Two of them:

| Structure | Low band | Middle | High band |
|---|---|---|---|
| `tapered_deci_cent` | $0.001 below $0.10 | $0.01 | $0.001 above $0.90 |
| `center_deci_edge_centi_cent` | $0.0001 below $0.01 | — | $0.0001 above $0.99 |

Rationale, verbatim: near the bounds *"small absolute price differences represent large relative changes in implied
probability"*. On `tapered_deci_cent`, `$0.095` is on-grid (0.001 band) and `$0.505` is not (0.01 band) — derive the check
from the market's live `price_ranges`, never a hard-coded step, and subscribe to `price_level_structure_updated` on the
lifecycle channel, which delivers a **new** `price_ranges` mid-life.

**Request and response precision differ.** Kalshi `FixedPointDollars`: *"Most request fields accept 2-4 decimal places…;
responses emit up to 6."* Reading `last_price_dollars` and resubmitting it as an order price produces both off-grid rejections
and silent truncation. Quantize toward validity against the live grid, in `Decimal`, after any venue round-trip.

**The venue SDK does this in `float`.** `py_clob_client/order_builder/builder.py` computes `raw_maker_amt = raw_taker_amt *
raw_price`, then rounds **up** at `round_config.amount + 4` digits and **down** at the target — a workaround necessary only
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
$1.00. A 1%-of-notional taker fee is **$0.99 — 99% of maximum possible profit** — so the trade is EV-negative at *any* true
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

**And the net fee is not a pure function of `(price, quantity)`.** Kalshi's current model has three components per fill — a
trade fee ceiled to $0.0001, a rounding fee that floors the balance change **toward −∞** to the member's precision ($0.0001
for direct members, $0.01 otherwise), and a rebate — with an accumulator that *"is maintained per order across all fills
regardless of whether the fills are taker or maker"*, issuing a whole-cent rebate once accumulated rounding exceeds $0.01. The
older maker-fee reimbursement path is monthly with a floor: *"reimbursed in the first week of the following month if their
reimbursement exceeds $10."* Net fee = `trade fee + rounding fee − rebate`, always ≥ $0.00, **not recomputable from a single
fill.** Book the venue's own fee field; use your model as a pre-trade estimate and a reconciliation tolerance only.

Fee timing moves where P&L is recognised. **Polymarket:** taker fee at match, in kind from proceeds (`_fillOrder` transfers
`taking - fee` — outcome tokens on a BUY, collateral on a SELL); no redemption fee; a **separate** `feeBips` on neg-risk
conversion. **Kalshi:** taker fee at match, maker fees at match on `quadratic_with_maker_fees` series, no settlement fee (fees
*"may apply for sub-cent scalar settlement"*).

## Negative-risk conversion

`NegRiskAdapter.convertPositions(marketId, indexSet, amount)` converts `k` NO shares across a market of `N` mutually exclusive
outcomes into `(k − 1)` collateral **and one YES share in each of the other `(N − k)` outcomes**. In sequence, from the
contract: count the NO legs in `indexSet` (`noPositionCount = k`); `wcol.mint((N − k) · amount)` — **wrapped collateral minted
out of thin air**; `splitPosition` that into `(N − k)` complete sets; transfer every resulting NO token to
`NO_TOKEN_BURN_ADDRESS` along with the caller's `k` NO tokens; `release((k − 1) · amount)` of underlying collateral to the
caller; hand over the `(N − k)` YES tokens. Fees are taken on **both** legs — `feeAmount = (_amount * md.feeBips()) /
FEE_DENOMINATOR`, with `(k − 1) · feeAmount` in collateral and `feeAmount` of each YES token going to the vault.

Four facts that break indexers and solvency alarms:

- **`WrappedCollateral` is deliberately not 1:1 backed.** `mint(uint256)` mints with no deposit; `release(address,uint256)`
  sends underlying out with no burn. Both are `onlyOwner` (the adapter). `WCOL.totalSupply()` is **not** a measure of
  collateral held.
- **`NO_TOKEN_BURN_ADDRESS` is an ordinary address**, `address(bytes20(bytes32(keccak256("NO_TOKEN_BURN_ADDRESS"))))`, not
  `address(0)`, and tokens are *transferred* there, not `_burn`ed. Exclude it from every outstanding-supply, open-interest and
  required-collateral sum, or your solvency alarm fires permanently after the first conversion.
- **Reading the result takes two calls.** `MarketDataLib.result()` is *"if the market has not been determined, returns zero"*
  — and returns zero for "question index 0 won". `getDetermined()` is separate; `getResult()` alone credits holders of the
  first outcome on every undetermined market.
- **The whole thing rests on one flag.** `MarketStateManager._reportOutcome`: `if (_outcome == true) { if (data.determined())
  revert MarketAlreadyDetermined; ... }`. If two questions in one market could both resolve YES, the `k` burned NO tokens
  would have been worth `k − 2` and the pool is short. Reporting `false` is **not** guarded by `determined()` — false reports
  are unlimited and unordered.

**Reference data is mutable here.** Augmented neg-risk markets carry *placeholder* outcomes clarified mid-life via the
bulletin-board contract — *"Only trade on named outcomes"*; *"The 'Other' outcome's definition changes as placeholders are
clarified"* — so a pricing model keyed to a snapshot of the outcome set is silently wrong after a clarification. A market is
capped at 256 questions (`questionId = marketId + uint8(index)`) and `incrementQuestionCount` *"does _not_ check to see if the
questionCount is already at the maximum value"*; **[UNVERIFIED]** what happens at question 257.

## Resolution

The lifecycle has no analogue in a CLOB integration, and the two venues have **opposite finality semantics**.

| Event | Polymarket (UMA → CTF) | Kalshi |
|---|---|---|
| Challenge window | Polymarket docs say **2 hours**; UMA describes `ManagedOptimisticOracleV2` with a proposer whitelist and extensions that are *"not a fixed length and [are] ended by Risk Labs market review team"*, e.g. ~15 min for unflagged sports. **The two primary sources disagree — do not hard-code a window** | `settlement_timer_seconds` exposed on the market |
| Dispute | first proposal is **discarded**; `priceDisputed` → `_reset` writes a **new** `requestTimestamp` | status `determined → disputed` |
| Re-determination | resolves per the **2nd** request; *"at most 2 OO Requests at a time for a question"* | status `amended`: *"Re-determined after a dispute. **Settlement timer restarts.**"* |
| "Too early" | `_resolve` returns `_reset(...)` when the OO price equals `_ignorePrice() = type(int256).min` (UMIP-107 p4); UMA: *"a subsequent oracle request is created and the market stays open"* | not modelled |
| Admin override | `flag()` sets `manualResolutionTimestamp = block.timestamp + SAFETY_PERIOD` (**1 hour**) and pauses; `resolveManually` then accepts only `[0,1]`, `[1,0]`, `[1,1]` | — |
| Finality | `reportPayouts` is a **one-shot on-chain write** | only `finalized` is terminal |
| Reversal | **none exists at the CTF layer** | `amended` is the reversal path |

**A dispute makes your cached proposal the wrong answer, not a stale one.** UMA, verbatim: *"if the first request for a given
market is disputed, a 2nd request is made with the same rules. The settlement of the prediction market then ignores the 1st
request and only resolves as per the 2nd request."* `_reset` overwrites `questionData.requestTimestamp` with
`block.timestamp`, so the OO request key `(requester, identifier, timestamp, ancillaryData)` **mutates**: a watcher holding
the old timestamp queries a dead request and reports "no price available" forever, or credits the discarded answer.

**"Resolve did not resolve" is a normal state, not an error.** Handle the P4 magic number as a transition to "re-requested":
do not retry-until-success, do not alert as a failure, do not close the position.

**The payout is a vector, not a winner.** `reportPayouts` requires only `den > 0` — `[1,1]` (each token redeems for $0.50) and
`[3,1]` are both legal at the CTF layer. Polymarket narrows it: `UmaCtfAdapter._constructPayouts` accepts exactly `{0, 0.5e18,
1e18}` and reverts `InvalidOOPrice` otherwise. **Persist `(numerators[], denominator)`. A `bool won` cannot store a tie.** And
Kalshi contracts do not always pay $1: `notional_value_dollars` is *"the total value of a single contract at settlement"*,
`result` can be `scalar` with a `settlement_value_dollars` *"only filled after determination"*, and `strike_type` can be
`functional` (*"Mapping from expiration values to settlement values"*). The $0/$1 binary payoff is a special case.

**A tie is unrepresentable on neg-risk markets.** `NegRiskOperator.reportPayouts` requires `payout0 + payout1 != 1 → revert
InvalidPayouts`, while `UmaCtfAdapter` maps an OO price of `0.5e18` to `[1,1]`. The adapter's comment acknowledges it: *"Note
that a tie is not a valid outcome when used with the `NegRiskOperator`."* `resolveManually` routes through the same operator
and cannot report `[1,1]` either. **[UNVERIFIED]** how such a market is unwedged; the on-chain paths give no route.

**The outcome index does not mean "yes".** UMIP-107 defines `p1`/`p2`/`p3`/`p4` as values **read from the request's own
ancillary data**, defaulting to 0 / 1 / 0.5 / `type(int256).min`; UMA's verification guide works an example in which *"p1
corresponds to Ruud, p2 to Djokovic"*. Read the label↔index mapping from the market's own resolution data — on Gamma,
`outcomes`, `outcomePrices` and `clobTokenIds` are JSON-encoded strings inside JSON, correlated by index. A labelling bug
swaps both legs of every position and is invisible until resolution.

**Marking during a dispute measures the vote, not the event.** In the July 2025 Zelenskyy-suit market (reported at $160–200M
notional) *"the price of 'yes' has now plummeted to $0.04 from $0.19"* while the dispute was live; a daily P&L consuming that
price unannotated reports a loss caused by governance.

## Open orders at resolution, and cancellation you cannot perform

**Cancellation is not a universally available operation.** A shutdown procedure of "cancel all, then exit" has undefined
behaviour in each of these states:

| State | Place | Amend | Cancel | Resting orders |
|---|---|---|---|---|
| Kalshi, past `close_time` | rejected | rejected | **rejected `MARKET_INACTIVE`** | remain until the exchange acts |
| Kalshi, **exchange** pause | rejected | rejected | **rejected** | **remain on the book**, unless `cancel_order_on_pause` was set **at order entry** |
| Kalshi, trading pause | rejected | — | allowed | — |
| Kalshi, `inactive → active` | — | — | — | **all resting orders cancelled on reactivation** |
| Polymarket, marketable order in a delay window (sports) | — | — | **refused** while status is `delayed` | — |
| Polymarket, partially filled order | — | — | only the unfilled portion | filled portion is not cancellable |

Set `cancel_order_on_pause = true` on Kalshi orders unless you have a written reason not to — it can only be set at entry, and
the pause is exactly the state in which you cannot set it retroactively. Polymarket's dead-man switch is short: heartbeats
every **5 s**, all resting orders cancelled after **10 s** of silence. A GC pause, a slow reconcile or a redeploy crosses it —
treat a 10-second stall as a full-book-loss event and rebuild from the venue's open-orders endpoint.

## Open positions at resolution, and a balance you already credited

**Credit only on the venue's explicit finality signal**: `payoutDenominator[conditionId] > 0` on the CTF, or `status ==
finalized` on Kalshi. Not on `closed`, `determined`, `disputed`, `amended`, a proposed OO price, or a UI "resolved" label.
`closed` is overloaded on both venues and means "trading stopped", never "a payout vector exists": Kalshi's `status=closed`
filter matches *"any market past `close_time` that is not yet `finalized`"* — including `determined`, `disputed` and `amended`
— and Polymarket's Gamma `closed` means *"Market has resolved **or been closed**"*. Keep "trading has stopped" and "the payout
vector exists" as **separate fields**: a Kalshi market in `determined` is trading-halted and payout-unknown simultaneously.

**Resolved is not redeemed.** On the CTF the position stays an ERC-1155 balance until someone calls `redeemPositions` — a
user-initiated transaction, costing gas, and the point at which cash appears. Value the position at `Σ_i qty_i × num_i / den`
from finality and carry a distinct `redeemed_at`. The chain half is `fin-onchain`'s; note only that Polygon PoS has produced a
**157-block** reorg (2023-02-23, height 39,599,624) and one-block reorgs are routine, so a redemption credit on a single
confirmation is not safe.

**Kalshi settles net, and rounds.** *"Only net positions are settled (after netting)."* *"The actual payout
(`CollateralAmountChange`) is rounded to whole cents. `CollateralAmountChange + MiscFeeAmt` equals the pre-rounding settlement
value."* A directly testable reconciliation rule — and it means a settlement row's cash movement **alone** does not reconcile
to `contracts × settlement_value`. Book `MiscFeeAmt` as a fee, not a P&L discrepancy.

**When a result is reversed after you credited.** The two venues need different code:

- **Kalshi** — the result can legally change (`determined → disputed → amended`, settlement timer restarts). The correct
  behaviour is *exactly one* credit, of the amended result. If you already credited, the correction is a **reversing entry
  plus the new entry**, never an in-place update of the original — that half is `fin-ledger`. Key the credit on the market and
  a resolution version so a replayed settlement event cannot double-credit.
- **Polymarket / CTF** — there is no reversal. `reportPayouts` guards are `require(payoutNumerators[conditionId].length ==
  outcomeSlotCount)`, `require(payoutDenominator[conditionId] == 0, "payout denominator already set")`, and per-slot
  `require(payoutNumerators[conditionId][i] == 0)`. Whatever the oracle wrote is what every holder can redeem, forever. Any
  make-whole is a **new ledger entry against a make-whole account**, decided off-venue. In March 2025 a ~$7M market resolved
  YES with no agreement signed; the operator said it *"was resolved too soon"* and declined refunds because this was not a
  "market failure". Your ledger needs an answer for that case before it happens.

**Full collateralisation does not make fill reports self-consistent.** NautilusTrader issue #3221 records Polymarket reporting
`last_qty=5.012345` against `quantity=5.000000` — an overfill on a venue that cannot be short a cent. Record the venue's
quantity unclamped and close the risk gate; do not assert your way into a crash.

## In-band sentinels whose value is the modal real value

Every one is a valid-looking value in the same type as real data.

| Field | Sentinel | Also means |
|---|---|---|
| Polymarket `get-last-trade-price` | `"0.5"` — *"Returns default values of `\"0.5\"` for price … if no trades found"* | a genuine coin-flip market |
| `MarketDataLib.result()` (neg-risk) | `0` = not determined | question index 0 won |
| `orderStatus[hash].remaining` | `0` = never touched (`remaining == 0 ? order.makerAmount : remaining`) | fully filled |
| Kalshi `result` | `''` | — (enum is `['yes','no','scalar','']`) |
| Kalshi `settlement_value_dollars` | null until determination | — |
| CTF `payoutNumerators[id].length == 0` | not **prepared** | ≠ not resolved (`payoutDenominator == 0`) |

Polymarket's *displayed* price is the midpoint **unless** *"the spread is wider than `$0.10`"*, in which case it is the last
trade — a mark whose definition changes with liquidity. Do not use it as a mark.

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
    # buy 100 YES @ 0.99  ==  sell 100 NO @ 0.01 — the venue guarantees this
    assert fee_model(qty=100, price=Decimal("0.99")) == fee_model(qty=100, price=Decimal("0.01"))
    assert sum(fee_model(qty=1, price=Decimal("0.99")) for _ in range(100)) >= fee_model(100, Decimal("0.99"))

def test_flat_sell_reserves_the_complement(keeper):
    keeper.place(token="YES", side="SELL", qty=100, price=Decimal("0.40"))   # flat account
    assert keeper.reserved_collateral == Decimal("60")     # 100 × (1 − 0.40), not 40

def test_merge_beats_redeem_on_a_tie(ctf):
    ctf.report_payouts(condition, numerators=[1, 1])       # denominator 2
    assert ctf.redeem(yes=3, no=3) == 2                    # floor(3/2) + floor(3/2), per position id
    assert ctf.merge_then_withdraw(3) == 3

def test_amended_result_credits_exactly_once(settler, kalshi):
    for status, result in [("determined","yes"), ("disputed",None), ("amended","no"), ("finalized","no")]:
        kalshi.status(status, result=result); settler.poll()
    assert settler.credits(market) == [Credit(outcome="no", n=1)]   # one credit, the amended result
```

## What is verified here, and what is not

**Verified against primary sources** — contract source in `gnosis/conditional-tokens-contracts`, `Polymarket/ctf-exchange`,
`Polymarket/neg-risk-ctf-adapter`, `Polymarket/uma-ctf-adapter`, `Polymarket/py-clob-client`; `docs.polymarket.com`,
`docs.kalshi.com`, `docs.uma.xyz`; UMIP-107; the Kalshi fee schedule PDF dated "effective Oct 1, 2025" via the Wayback Machine
— every contract function name, `require` string, constant and formula quoted above, the fee formulas and rates, the Kalshi
lifecycle enum and its `amended` semantics, the tick-grid structures, the `[tick, 1−tick]` bound, and the sentinel table.

**Explicitly unverified — do not build on these:**

- Which of Polymarket's two contradictory order-book sort-order documents is in force (the contradiction is verified; the
  resolution is not). Same for `min_order_size` `"5"` vs `'1'`.
- How a neg-risk market whose oracle returns 0.5 is unwedged operationally.
- Behaviour at neg-risk question 257 (the `uint8` cast and unchecked increment are verified; the outcome is not).
- Any named, dated public example of a Kalshi `determined → disputed → amended` sequence — cite the mechanism, which is
  documented, not an incident. Likewise, no Augur mainnet fork instance is established.
- Contract addresses, fee rates and TTLs, dated 2026-08-24 and changing without notice.
