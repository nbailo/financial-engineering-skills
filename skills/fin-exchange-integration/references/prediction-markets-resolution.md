# Prediction markets: conversion, resolution and settlement

The half of a binary venue that has no analogue in a CLOB integration. Negative-risk conversion mints and
releases collateral outside the complete-set invariant; the two venues have **opposite finality semantics**;
cancellation is not a universally available operation as a market closes; and on one venue a result you have
already credited can legally change.

## Contents

- Negative-risk conversion: the arithmetic, the wrapped collateral, the burn address that is not `address(0)`
- Resolution: dispute discards the proposal, TOO EARLY reopens the market, `[1,1]` is a legal payout vector, `amended`
  restarts a settlement timer, the CTF commit is irreversible
- Open orders at resolution, and the states in which you cannot cancel
- Open positions at resolution, and a balance you credited before the result changed
- Required assertions, as code; and what is not established here

**Dating.** Every venue fact below was read from primary sources (contract source, vendor docs, the archived Kalshi
fee-schedule PDF) in a pass dated **2026-08-24**. Fee rates, addresses, tick grids and lifecycle enums are volatile;
re-verify anything you hard-code.

## Negative-risk conversion

`NegRiskAdapter.convertPositions(marketId, indexSet, amount)` converts `k` NO shares across a market of `N` mutually exclusive
outcomes into `(k − 1)` collateral **and one YES share in each of the other `(N − k)` outcomes**. In sequence, from the
contract: count the NO legs in `indexSet` (`noPositionCount = k`); `wcol.mint((N − k) · amount)`, **wrapped collateral minted
out of thin air**; `splitPosition` that into `(N − k)` complete sets; transfer every resulting NO token to
`NO_TOKEN_BURN_ADDRESS` along with the caller's `k` NO tokens; `release((k − 1) · amount)` of underlying collateral to the
caller; hand over the `(N − k)` YES tokens. Fees are taken on **both** legs: `feeAmount = (_amount * md.feeBips()) /
FEE_DENOMINATOR`, with `(k − 1) · feeAmount` in collateral and `feeAmount` of each YES token going to the vault.

Four facts that break indexers and solvency alarms:

- **`WrappedCollateral` is deliberately not 1:1 backed.** `mint(uint256)` mints with no deposit; `release(address,uint256)`
  sends underlying out with no burn. Both are `onlyOwner` (the adapter). `WCOL.totalSupply()` is **not** a measure of
  collateral held.
- **`NO_TOKEN_BURN_ADDRESS` is an ordinary address**, `address(bytes20(bytes32(keccak256("NO_TOKEN_BURN_ADDRESS"))))`, not
  `address(0)`, and tokens are *transferred* there, not `_burn`ed. Exclude it from every outstanding-supply, open-interest and
  required-collateral sum, or your solvency alarm fires permanently after the first conversion.
- **Reading the result takes two calls.** `MarketDataLib.result()` is *"if the market has not been determined, returns zero"*,
  and returns zero for "question index 0 won". `getDetermined()` is separate; `getResult()` alone credits holders of the
  first outcome on every undetermined market.
- **The whole thing rests on one flag.** `MarketStateManager._reportOutcome`: `if (_outcome == true) { if (data.determined())
  revert MarketAlreadyDetermined; ... }`. If two questions in one market could both resolve YES, the `k` burned NO tokens
  would have been worth `k − 2` and the pool is short. Reporting `false` is **not** guarded by `determined()`: false reports
  are unlimited and unordered.

**Reference data is mutable here.** Augmented neg-risk markets carry *placeholder* outcomes clarified mid-life via the
bulletin-board contract (*"Only trade on named outcomes"*; *"The 'Other' outcome's definition changes as placeholders are
clarified"*), so a pricing model keyed to a snapshot of the outcome set is silently wrong after a clarification. A market is
capped at 256 questions (`questionId = marketId + uint8(index)`) and `incrementQuestionCount` *"does _not_ check to see if the
questionCount is already at the maximum value"*; **[UNVERIFIED]** what happens at question 257.

## Resolution

The lifecycle has no analogue in a CLOB integration, and the two venues have **opposite finality semantics**.

| Event | Polymarket (UMA → CTF) | Kalshi |
|---|---|---|
| Challenge window | Polymarket docs say **2 hours**; UMA describes `ManagedOptimisticOracleV2` with a proposer whitelist and extensions that are *"not a fixed length and [are] ended by Risk Labs market review team"*, e.g. ~15 min for unflagged sports. **The two primary sources disagree: do not hard-code a window** | `settlement_timer_seconds` exposed on the market |
| Dispute | first proposal is **discarded**; `priceDisputed` → `_reset` writes a **new** `requestTimestamp` | status `determined → disputed` |
| Re-determination | resolves per the **2nd** request; *"at most 2 OO Requests at a time for a question"* | status `amended`: *"Re-determined after a dispute. **Settlement timer restarts.**"* |
| "Too early" | `_resolve` returns `_reset(...)` when the OO price equals `_ignorePrice() = type(int256).min` (UMIP-107 p4); UMA: *"a subsequent oracle request is created and the market stays open"* | not modelled |
| Admin override | `flag()` sets `manualResolutionTimestamp = block.timestamp + SAFETY_PERIOD` (**1 hour**) and pauses; `resolveManually` then accepts only `[0,1]`, `[1,0]`, `[1,1]` | n/a |
| Finality | `reportPayouts` is a **one-shot on-chain write** | only `finalized` is terminal |
| Reversal | **none exists at the CTF layer** | `amended` is the reversal path |

**A dispute makes your cached proposal the wrong answer, not a stale one.** UMA, verbatim: *"if the first request for a given
market is disputed, a 2nd request is made with the same rules. The settlement of the prediction market then ignores the 1st
request and only resolves as per the 2nd request."* `_reset` overwrites `questionData.requestTimestamp` with
`block.timestamp`, so the OO request key `(requester, identifier, timestamp, ancillaryData)` **mutates**: a watcher holding
the old timestamp queries a dead request and reports "no price available" forever, or credits the discarded answer.

**"Resolve did not resolve" is a normal state, not an error.** Handle the P4 magic number as a transition to "re-requested":
do not retry-until-success, do not alert as a failure, do not close the position.

**The payout is a vector, not a winner.** `reportPayouts` requires only `den > 0`: `[1,1]` (each token redeems for $0.50) and
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
corresponds to Ruud, p2 to Djokovic"*. Read the label↔index mapping from the market's own resolution data; on Gamma,
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
| Kalshi, trading pause | rejected | n/a | allowed | n/a |
| Kalshi, `inactive → active` | n/a | n/a | n/a | **all resting orders cancelled on reactivation** |
| Polymarket, marketable order in a delay window (sports) | n/a | n/a | **refused** while status is `delayed` | n/a |
| Polymarket, partially filled order | n/a | n/a | only the unfilled portion | filled portion is not cancellable |

Set `cancel_order_on_pause = true` on Kalshi orders unless you have a written reason not to; it can only be set at entry, and
the pause is exactly the state in which you cannot set it retroactively. Polymarket's dead-man switch is short: heartbeats
every **5 s**, all resting orders cancelled after **10 s** of silence. A GC pause, a slow reconcile or a redeploy crosses it;
treat a 10-second stall as a full-book-loss event and rebuild from the venue's open-orders endpoint.

## Open positions at resolution, and a balance you already credited

**Credit only on the venue's explicit finality signal**: `payoutDenominator[conditionId] > 0` on the CTF, or `status ==
finalized` on Kalshi. Not on `closed`, `determined`, `disputed`, `amended`, a proposed OO price, or a UI "resolved" label.
`closed` is overloaded on both venues and means "trading stopped", never "a payout vector exists": Kalshi's `status=closed`
filter matches *"any market past `close_time` that is not yet `finalized`"* (including `determined`, `disputed` and `amended`)
and Polymarket's Gamma `closed` means *"Market has resolved **or been closed**"*. Keep "trading has stopped" and "the payout
vector exists" as **separate fields**: a Kalshi market in `determined` is trading-halted and payout-unknown simultaneously.

**Resolved is not redeemed.** On the CTF the position stays an ERC-1155 balance until someone calls `redeemPositions`, a
user-initiated transaction, costing gas, and the point at which cash appears. Value the position at `Σ_i qty_i × num_i / den`
from finality and carry a distinct `redeemed_at`. The chain half is `fin-onchain`'s; note only that Polygon PoS has produced a
**157-block** reorg (2023-02-23, height 39,599,624) and one-block reorgs are routine, so a redemption credit on a single
confirmation is not safe.

**Kalshi settles net, and rounds.** *"Only net positions are settled (after netting)."* *"The actual payout
(`CollateralAmountChange`) is rounded to whole cents. `CollateralAmountChange + MiscFeeAmt` equals the pre-rounding settlement
value."* A directly testable reconciliation rule, and it means a settlement row's cash movement **alone** does not reconcile
to `contracts × settlement_value`. Book `MiscFeeAmt` as a fee, not a P&L discrepancy.

**When a result is reversed after you credited.** The two venues need different code:

- **Kalshi**: the result can legally change (`determined → disputed → amended`, settlement timer restarts). The correct
  behaviour is *exactly one* credit, of the amended result. If you already credited, the correction is a **reversing entry
  plus the new entry**, never an in-place update of the original; that half is `fin-ledger`. Key the credit on the market and
  a resolution version so a replayed settlement event cannot double-credit.
- **Polymarket / CTF**: there is no reversal. `reportPayouts` guards are `require(payoutNumerators[conditionId].length ==
  outcomeSlotCount)`, `require(payoutDenominator[conditionId] == 0, "payout denominator already set")`, and per-slot
  `require(payoutNumerators[conditionId][i] == 0)`. Whatever the oracle wrote is what every holder can redeem, forever. Any
  make-whole is a **new ledger entry against a make-whole account**, decided off-venue. In March 2025 a ~$7M market resolved
  YES with no agreement signed; the operator said it *"was resolved too soon"* and declined refunds because this was not a
  "market failure". Your ledger needs an answer for that case before it happens.

**Full collateralisation does not make fill reports self-consistent.** NautilusTrader issue #3221 records Polymarket reporting
`last_qty=5.012345` against `quantity=5.000000`, an overfill on a venue that cannot be short a cent. Record the venue's
quantity unclamped and close the risk gate; do not assert your way into a crash.

## Required assertions

```python
# tests/test_binary_venue_invariants.py

def test_amended_result_credits_exactly_once(settler, kalshi):
    for status, result in [("determined","yes"), ("disputed",None), ("amended","no"), ("finalized","no")]:
        kalshi.status(status, result=result); settler.poll()
    assert settler.credits(market) == [Credit(outcome="no", n=1)]   # one credit, the amended result
```

## What is not established here

**Explicitly unverified, do not build on these:**

- How a neg-risk market whose oracle returns 0.5 is unwedged operationally.
- Behaviour at neg-risk question 257 (the `uint8` cast and unchecked increment are verified; the outcome is not).
- Any named, dated public example of a Kalshi `determined → disputed → amended` sequence; cite the mechanism, which is
  documented, not an incident. Likewise, no Augur mainnet fork instance is established.
