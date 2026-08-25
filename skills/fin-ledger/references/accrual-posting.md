# The accrual posting: idempotence, path dependence, and rounding

How a time-based amount becomes an entry: the balanced posting that recognises interest, fees or funding, the
period marker that makes a re-run post nothing, the compounding law that decides whether the answer depends on
how often the job ran, the rounding that makes a per-period accrual conserve value, and the claim expressed as
the difference of two monotone counters. The year fraction it multiplies is `day-count-conventions.md`.

## Contents

1. **The accrual posting and per-period idempotence**: the `(account, period)` constraint, the stored period marker, the scaled-balance index, back-dating.
2. **Path dependence**: iterative `index × (1 + r·Δt)` versus the exponential form that telescopes; per-second versus per-block versus per-day; closed-form drift.
3. **Rounding inside the accrual**: carrying the fraction forward versus truncating, the direction table by conversion, the residue account.
4. **Claim and settlement**: a claim as the difference of two monotone counters, closed in the payout's own transaction.

## 1 · The accrual posting and per-period idempotence

An accrual is a balanced posting, not a mutation. The run's shipped invariant is `Σ credited to customer accounts == Σ
debited to interest expense`, exact to the minor unit, per currency.

Recognition is an event, which is why it posts. IFRS 9 defines the effective interest method as the method
used in the *"allocation and recognition of the interest revenue or interest expense in profit or loss"*:
allocation across periods and recognition within one, both of them things a journal records and a mutated
balance field cannot.

**Key the accrual on `(account, period)` and let the database refuse the second write.** The double-accrual bug is a
job with at-least-once delivery doing `balance += balance * rate * dt` with no period marker.

```sql
CREATE EXTENSION IF NOT EXISTS btree_gist;

CREATE TABLE accruals (
  account_id    bigint    NOT NULL REFERENCES accounts(id),
  period_start  date      NOT NULL,
  period_end    date      NOT NULL,                 -- exclusive, see day-count-conventions.md
  period        daterange GENERATED ALWAYS AS (daterange(period_start, period_end, '[)')) STORED,
  amount_minor  bigint    NOT NULL,
  residue       numeric(38,20) NOT NULL,            -- carried forward (§3)
  business_date date      NOT NULL,                 -- resolved, see day-count-conventions.md
  ledger_txn_id uuid      NOT NULL REFERENCES ledger_transactions(id),
  CONSTRAINT accruals_once  UNIQUE (account_id, period_start, period_end),
  CONSTRAINT accruals_nogap EXCLUDE USING gist (account_id WITH =, period WITH &&)
);
```

`accruals_once` makes the retry a no-op (`INSERT … ON CONFLICT ON CONSTRAINT accruals_once DO NOTHING`, in the same
transaction as the ledger postings). `accruals_nogap` catches the harder bug: a *different* period overlapping one
already accrued, which a plain unique constraint accepts. The writer catches SQLSTATE `23505` and `23P01`
(exclusion_violation) and resolves both to "already accrued". The test runs the job twice for one period and asserts
the second run wrote **zero** rows and **zero** postings. Three independent protocols converged on the same guard:

| System | Guard | Marker |
|---|---|---|
| Compound v2 `CToken.accrueInterest()` | `if (accrualBlockNumberPrior == currentBlockNumber) return NO_ERROR;`, *"Short-circuit accumulating 0 interest"* | block number |
| Compound III `Comet.accrueInternal()` | `uint timeElapsed = uint256(now_ - lastAccrualTime); if (timeElapsed > 0) { … }` | `block.timestamp` |
| Aave v3 `ReserveLogic.updateState()` | `if (reserveCache.reserveLastUpdateTimestamp == uint40(block.timestamp)) return;`, *"If time didn't pass since last stored timestamp, skip state update"* | `uint40` timestamp |

**The index / scaled-balance pattern exists so you do not touch N accounts per period.** Aave v3.5 docs, verbatim:
*"The aave protocol handles balances & yield by storing a `scaledBalance` and an index. The actual balance is then
derived by multiplying the `scaledBalance` by the `index` or by dividing the `amount` by the index."* Compound v2 does
the same with a global `borrowIndex` and a per-borrower `interestIndex` snapshot. Per-account work is O(1); **the
journal still needs one aggregate posting per period for the pool's own income** (Aave's `_accrueToTreasury`), or the
books do not balance.

**Read the accrued index, not the stored one.** Aave ships `getNormalizedIncome()` / `getNormalizedDebt()` for this:
`calculateLinearInterest(rate, timestamp).rayMul(reserve.liquidityIndex)` when the stored timestamp is stale. Reading
`reserve.liquidityIndex` directly yields a balance correct as of the last time anybody touched the reserve.

**Back-dating.** A scaled-balance model represents a back-dated principal change exactly by minting `amount /
index(d)` rather than `amount / index(now)`, but only if the index history is retained and the cash was genuinely
present since `d`; otherwise it inflates the scaled supply and so the aggregate claim on the pool. Without `index(d)`
the back-date becomes a catch-up posting in the current open period, which is what IFRS 9 B5.4.6 requires anyway: the
adjustment is *"recognised in profit or loss as income or expense"* now, never by restating a closed period. **Period
attribution is a correctness property separate from amount correctness**: June's accrual posted in July balances
perfectly and is wrong in every statement, interest certificate, covenant test and tax filing. Assert that for each
closed period, `Σ postings WHERE business_date <@ period` is unchanged after any correction run.

## 2 · Path dependence: per-second, per-block, per-day

Iterative `index ← index × (1 + r·Δt/Y)` compounds across touches; over N touches the factor is `∏(1 + r·Δtᵢ/Y)`,
which grows with N. The exponential form telescopes: `∏ e^(r·Δtᵢ/Y) = e^(r·ΣΔtᵢ/Y)`, independent of how the interval
was chopped. Which law applies is a product decision, stated in the spec.

| System / leg | Law | Path-dependent? | Consequence |
|---|---|---|---|
| Aave v3 supply index | `MathUtils.calculateLinearInterest` ⇒ `index × (1 + r·Δt/Y)` | **yes** | at r = 10 %, one touch a year gives 1.10; daily touches give `(1+0.10/365)^365 ≈ 1.10516`, **0.47 % more interest** for the same rate and year, purely from interaction frequency |
| Aave v3 debt index | `MathUtils.calculateCompoundedInterest` ⇒ `index × approx(e^(r·Δt/Y))` | no | telescopes, up to series error |
| Compound v2 `borrowIndex`; Comet `baseSupplyIndex` | `borrowIndexNew = simpleInterestFactor * borrowIndex + borrowIndex`; `baseSupplyIndex_ += mulFactor(baseSupplyIndex_, supplyRate * timeElapsed)` | **yes** | realized APY is a function of how often the contract is poked, and that is in no rate parameter |

The same protocol is path-dependent on one leg and path-independent on the other; **an off-chain model that assumes
one law for both legs drifts continuously and never converges.**

**Closed-form versus iterative.** Aave's `calculateCompoundedInterest` returns `RAY + x + x.rayMul(x/2 +
x.rayMul(x/6))` with `x = rate * exp / SECONDS_PER_YEAR`: the third-order Maclaurin series of `e^x`, deliberately
biased; the source comment reads *"The approximation **slightly underpays liquidity providers and undercharges
borrowers**, with the advantage of great gas cost reductions."* The error is cubic: at `x = 0.05` the series gives
1.0512708 against `e^x` = 1.0512711 (6.3 × 10⁻⁶ of the interest); at `x = 0.5`, 1.6458333 against
1.6487213, **0.445 %**; at `x = 1.0`, **1.9 %** of the growth factor. Concentrated where it is least visible:
high-rate, low-activity positions. If reporting computes `P·e^{rt}` while the ledger accumulates per period the two
never agree, and whichever the customer sees becomes the liability; test the ledger against the closed form to a
stated tolerance.

**A block count is not a clock.** Compound v2: `uint public constant blocksPerYear = 2102400;` (31,536,000 s /
2,102,400 = **15.0 s/block**, which Ethereum has never had). Pre-Merge ~13.2 s gave ~2,388,700 blocks/year (1.136×
nominal); post-Merge slots are 12 s ⇒ 2,628,000 blocks/year, so a market configured for "5 % APR" charges **6.25 %**
until the rate model is redeployed. Comet's answer is `block.timestamp` directly; never denominate a rate in blocks,
ticks or job invocations. And **`SECONDS_PER_YEAR` is a convention declaration**: Aave's `MathUtils.sol` carries an
"Ignoring leap years" dev comment above `uint256 internal constant SECONDS_PER_YEAR = 365 days;`, so a nominal 5.00 %
reserve realises **5.0137 %** across a leap year: exactly ACT/365F, and defensible; the failure is an off-chain
accounting system reconciling the same reserve on a 366-day basis.

## 3 · Rounding inside the accrual

**Sub-minor-unit interest, worked.** Balance $100.00, nominal 2 %, ACT/365, posted daily. Exact daily interest is `100
× 0.02 / 365 = $0.0054795`.

| Policy | Per day | Over 365 days | Error vs exact $2.00 |
|---|---|---|---|
| Truncate to the cent each period | $0.00 | **$0.00** | −100 % |
| Round half-up to the cent each period | $0.01 | **$3.65** | **+82.5 %** |
| Carry the fraction forward, post whole cents | $0.00 / $0.01 | **$2.00** | exact |

Less degenerate: $9,999 at 3.65 % (exact annual $364.9635) gives **$361.35** truncated daily (−0.99 %), **$365.00**
half-up, **$364.96** carried. **The per-period error scales with the number of periods, not the balance.**

```python
from decimal import Decimal, ROUND_FLOOR

def accrue_period(principal_minor: int, rate: Decimal, yf: Decimal,
                  residue: Decimal) -> tuple[int, Decimal]:
    exact = Decimal(principal_minor) * rate * yf + residue     # fractional minor units
    whole = int(exact.to_integral_value(rounding=ROUND_FLOOR))
    return whole, exact - whole                                 # 0 <= residue < 1 minor unit
```

`residue` is persisted per account as `numeric(38,20)`; the invariant over any run of periods is `Σ posted +
residue_final == Σ exact`. A zero-amount period posts nothing and **is not an error**: the Aave v3.5 audit found an
accrual rounding to zero shares hitting `InvalidMintAmount()` and reverting the state update. An uncarried fraction is
still a liability: post it to a named residue account, as Stripe does for sub-unit invoice amounts (*"We credit or
debit any difference from rounding to the customer balance"*).

**Direction, for index and share conversions.** Aave v3.5's rationale opens by repudiating half-up: *"Historically
Aave has used half-up … **This has been a source of confusion and bugs in the past as it makes the rounding somewhat
chaotic**."* … *"the rounding error is still technically unbounded … The intention of this change is to always round
in favor of the protocol, to avoid insolvency situations."* The resulting `TokenMath.sol` table, aligned to
**ERC-4626**:

| Operation | Direction |
|---|---|
| supply `x` assets → shares | `floor(x / index)` |
| withdraw `x` assets → shares burned | `ceil(x / index)` |
| borrow `x` assets → debt shares | `ceil(x / index)` |
| repay `x` assets → debt shares burned | `floor(x / index)` |
| `balanceOf` | supply token `floor(shares × index)`; debt token `ceil(shares × index)` |
| collateral value in base currency | down; debt value in base currency, up |
| accrual to treasury | down (*"Rounding down to undermint to the treasury and keep the invariant healthy"*) |

Those directions exist so that *"`withdraw(aToken.balanceOf(user))` will always **redeem the full user balance**"*,
`repay(vToken.balanceOf(user))` *"will always **repay the full debt**"*, and *"tiny 'dust' debts"* cannot *"round down
to zero … making them invisible to Health Factor calculations."*

Two review consequences. **(a) The default is a decision you did not make**; Comet's `principalValueBorrow` is
`(presentValue_ * BASE_INDEX_SCALE + baseBorrowIndex_ - 1) / baseBorrowIndex_`, an explicit ceiling, while Compound
v2's `borrowBalanceStoredInternal` truncates, rounding debt **down** in the borrower's favour on every read. **(b)
Directions come in pairs**; the StErMi audit found v3.5's debt-in-USD rounding changed in `GenericLogic` but *"not …
the `LiquidationLogic`"*, changing which liquidation branch is taken. Note the regime split: where a statute
specifies, it specifies mode, level and precision, never direction-of-benefit (Reg Z §1026.22(a)(2)'s APR tolerance is
explicitly *"above or below"*); an unregulated index protocol chooses direction, in the house's favour.

## 4 · Claim and settlement

Express a claim as the difference between two monotone non-decreasing counters and close the gap in the same
transaction as the payout. `CometRewards.claimInternal`, verbatim:

```solidity
uint claimed = rewardsClaimed[comet][src];  uint accrued = getRewardAccrued(comet, src, config);
if (accrued > claimed) { uint owed = accrued - claimed;
                         rewardsClaimed[comet][src] = accrued;  doTransferOut(config.token, to, owed); }
```

Replaying it pays zero, with no dedupe table and no lock. In SQL: `cumulative_accrued` and `cumulative_paid` columns,
a payout of the difference, and `UPDATE … SET cumulative_paid = cumulative_accrued` inside the transaction that writes
the payout legs.

**The comparison operator is where this goes wrong.** Compound's Proposal 62 (29–30 Sep 2021) shipped `>` where `>=`
was required, in two places in the accrual/claim comparison; ~168,000 COMP was erroneously claimed. The failing input
is exactly equality, which no example-based test generates by accident: write `accrued == claimed` explicitly and
assert it pays zero without reverting. And **never skip an accrual you cannot pay**: Comet advances its rewards index
only `if (totalSupplyBase >= baseMinForRewards)`, so below the threshold those rewards are skipped, not deferred, and
never exist.
