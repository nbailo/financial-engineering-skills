# Accrual, day counts, and the business date

The lookup material behind time-based money: the day-count fraction, the calendar that adjusts the dates it is
measured between, and the posting that recognises the result. Covers why the convention is contract data that must be
stored on the instrument, which conventions cannot be computed from two dates at all, the business-day adjustment
conventions and the versioned calendars they consume, the civil-date definition of a day that elapsed-seconds
arithmetic violates, the rounding directions that make an index or scaled-balance model conserve value, and the
corporate-action family that changes quantity and basis without anyone trading.

## Contents

1. **Conventions are data** — the convention *and its definitions version*; `30E/360` under the 2000 versus 2006 ISDA Definitions; rejecting a null convention.
2. **Conventions that need the schedule** — `ACT/ACT ICMA`, `30E/360 ISDA`, `ACT/365L`; why a `yearFraction(LocalDate, LocalDate, Convention)` signature is the defect.
3. **The day-count fraction table** — ACT/360, ACT/365F, ACT/ACT (ISDA, ICMA, AFB), 30/360 US and Bond Basis, 30E/360, 30E/360 ISDA, BUS/252, and the "Actual/365" alias.
4. **Period boundaries** — half-open `[start, end)`, the conventions that are the other way round, the additivity test.
5. **Golden vectors** — ISDA's own worked examples, plus the same principal and dates paying three different amounts.
6. **Business-day conventions and calendars** — `FOLLOWING`, `MODIFIED_FOLLOWING`, `PRECEDING`, `MODIFIED_PRECEDING`, `NEAREST`; unadjusted accrual versus adjusted payment dates.
7. **The business date** — the civil-date day, DST and the 23-hour day, month-end, the value-date timezone, cut-offs, clock monotonicity.
8. **The accrual posting and per-period idempotence** — the `(account, period)` constraint, the stored period marker, the scaled-balance index, back-dating.
9. **Path dependence** — iterative `index × (1 + r·Δt)` versus the exponential form that telescopes; per-second versus per-block versus per-day; closed-form drift.
10. **Rounding inside the accrual** — carrying the fraction forward versus truncating, the direction table by conversion, the residue account.
11. **Claim and settlement** — a claim as the difference of two monotone counters, closed in the payout's own transaction.
12. **Corporate actions and supply events** — ex/record/pay after T+1, splits that move quantity *and* basis, cash-in-lieu, ticker reuse, migrations, rebases, funding cliffs.

## 1 · Conventions are data

The instrument row carries the convention **and the definitions vintage**, because the same code is a different
algorithm in different vintages. FpML's `dayCountFraction` coding scheme (genericode v2-2) annotates `30E/360`
verbatim: *"Note that the algorithm defined for this day count fraction **has changed** between the 2000 ISDA
Definitions and 2006 ISDA Definitions."* It annotates `ACT/ACT.ICMA` as *"applicable for transactions booked under the
2006 ISDA Definitions. Transactions under the 2000 ISDA Definitions should use the ACT/ACT.ISMA code instead"* — same
economics, two codes, selected by vintage. The §4.16 paragraph letters moved too (`ACT/360` (d)→(e), `30/360` (e)→(f),
`30E/360` (f)→(g)), so **a citation of the form "ISDA §4.16(f)" is meaningless without the vintage.**

```sql
ALTER TABLE instruments
  ADD COLUMN day_count          text    NOT NULL,  -- FpML code: 'ACT/360', '30E/360.ISDA', …
  ADD COLUMN day_count_defs     text    NOT NULL,  -- 'ISDA2000' | 'ISDA2006' | 'ICMA251' | 'REGZ_APPJ'
  ADD COLUMN bd_convention      text    NOT NULL,  -- 'MODIFIED_FOLLOWING' | … (§6)
  ADD COLUMN calendar_ids       text[]  NOT NULL,  -- union of every relevant centre (§6)
  ADD COLUMN eom_flag           boolean NOT NULL,
  ADD COLUMN payment_frequency  text    NOT NULL,  -- required by ACT/ACT.ICMA, ACT/365L
  ADD COLUMN termination_date   date    NOT NULL,  -- required by 30E/360.ISDA
  ADD COLUMN value_date_tz      text    NOT NULL,  -- IANA zone (§7)
  ADD COLUMN capitalises_unpaid boolean NOT NULL;  -- actuarial vs U.S. Rule
```

No `DEFAULT`. A missing convention is a rejected instrument, not an `ACT/365` assumption: ACT/360 where the contract
says ACT/365F over-pays 365/360 = 1.0138889× the interest. Whether unpaid interest capitalises is likewise stored, not
assumed: Reg Z §1026.22(a)(1) permits *"either the actuarial method or the United States Rule method"*, and only the
former rolls unpaid finance charge into principal.

## 2 · Conventions that need the schedule

OpenGamma Strata's `DayCounts` javadoc states, verbatim, for `ACT_ACT_ICMA`, `THIRTY_E_360_ISDA` and `ACT_365L`: *"The
method `DayCount#yearFraction(LocalDate, LocalDate)` will **throw an exception because schedule information is
required for this day count**."* QuantLib reaches it structurally: `ActualActual::ISMA_Impl` takes a `Schedule`.

```java
BigDecimal yearFraction(LocalDate start, LocalDate end, DayCount convention);                 // DEFECT
BigDecimal yearFraction(LocalDate start, LocalDate end, DayCount convention, ScheduleInfo s); // correct
// ScheduleInfo: frequency, the period's start/end, the EOM flag, the termination date,
// and (for BUS/252) the holiday calendar.
```

A hand-rolled two-date version does not throw for `ACT/ACT.ICMA` — it returns a plausible wrong number. The schedule
leaks in even for 30/360: Strata's `THIRTY_U_360` doc reads *"This day count **has different rules depending on
whether the EOM rule applies or not**. The EOM rule is set in the `ScheduleInfo`."*

## 3 · The day-count fraction table

| FpML code | numerator | denominator | pure fn of `(d1,d2)`? | trap |
|---|---|---|---|---|
| `ACT/360` | actual days | 360 | yes | 365/360 ⇒ **+1.3889 %** of interest a year against ACT/365F |
| `ACT/365.FIXED` | actual days | 365 | yes | a leap year pays 366/365 = 1.0027397× nominal |
| `ACT/ACT.ISDA` | days in each year-portion | 365 or 366 per portion | yes | the fraction is a **sum of two fractions**, not one division |
| `ACT/ACT.ICMA` | actual days in period | days in period × periods per year | **no** — schedule | a regular period yields exactly `1/frequency`; stubs need notional periods |
| `ACT/ACT.AFB` | actual days | 366 if the period contains 29 Feb, else 365 | yes | one 29 Feb anywhere flips the whole denominator |
| `30/360` (bond basis, ISMA) | `360Δy + 30Δm + Δd` after adjustment | 360 | yes | adjustment table below |
| `30E/360` | same shape | 360 | yes | **algorithm differs between the 2000 and 2006 Definitions** |
| `30E/360.ISDA` | same shape | 360 | **no** — termination date | the Feb-EOM rule is suppressed *only* at maturity |
| `BUS/252` | *"The number of **Business Days** in the Calculation Period"* | 252 | no — calendar | the numerator is a holiday-calendar query (FpML) |

The 30/360 family is nine algorithms differing only on the 31st and on the last day of February. QuantLib's
day-of-month adjustments, applied before `360(yy2−yy1) + 30(mm2−mm1) + (dd2−dd1)`:

| Variant | Adjustment, in order |
|---|---|
| **US** | `if isLastOfFeb(d1) { if isLastOfFeb(d2) dd2=30; dd1=30 }`; then `if dd2==31 && dd1>=30: dd2=30`; then `if dd1==31: dd1=30`. QuantLib's own comment: `// NOTE: the order of checks is important` |
| **Bond basis / ISMA** | `if dd1==31: dd1=30`; then `if dd2==31 && dd1==30: dd2=30` |
| **European (`30E/360`)** | `if dd1==31: dd1=30`; `if dd2==31: dd2=30` |
| **ISDA / German (`30E/360.ISDA`)** | European, plus `if isLastOfFeb(d1): dd1=30`; `if d2 != terminationDate && isLastOfFeb(d2): dd2=30` |

Strata's history note explains the divergence: the US count *"originally started with just the two rules of '30/360
ISDA'. **At some later point, the last day of February EOM rules were added.**"* On aliasing, QuantLib warns verbatim
that *"According to ISDA, 'Actual/365' (without 'Fixed') is an alias for 'Actual/Actual (ISDA)'"*, and FpML 2.0's
`ACT/365.ISDA` became `ACT/ACT.ISDA` in FpML 3.0 — a bare `"Actual/365"` in a feed is ambiguous data, to be rejected
at ingest, not coerced.

## 4 · Period boundaries

ISDA's 1999 Actual/Actual paper defines a calculation period as *"from, and including, one period end date (or the
effective date) to, but excluding, the next period end date (or the termination date)"*. Half-open `[start, end)` is
dominant — **and not universal**. Strata, verbatim: `ACT_ACT_ISDA` and `ACT_ACT_ICMA` — *"The first day in the period
is included, the last day is excluded"*; `ACT_365_ACTUAL`, `NL_360`, `NL_365` — *"The first day in the period is
excluded, the last day is included."* Getting it wrong is exactly one day of interest at every boundary — twelve free
days a year on a monthly product.

The test is per convention: `yf(d0,d1) + yf(d1,d2) == yf(d0,d2)` over a fuzzed schedule, plus `sum(1 for p in periods
if p.contains(d1)) == 1`. Additivity does **not** hold for `ACT/ACT.ICMA` across a coupon boundary with unequal
periods, nor for any 30/360 variant where `d1` is a 31st — assert it only where the convention is additive, and assert
the schedule's total year fraction elsewhere.

## 5 · Golden vectors

All figures are ISDA's own, from *"The Actual/Actual Day Count Fraction"* (ISDA Market Conventions Survey paper, 3
June 1999): *"there are at least three different interpretations of actual/actual."*

**Same principal, same dates, three legal answers.** Semi-annual, notional £10,000, fixed rate 10 %, period 1 Nov 2003
→ 1 May 2004 (61 days in 2003, 121 days in 2004); the paper's short- and long-stub vectors belong in the same
fixture:

| Method | Year fraction | Interest |
|---|---|---|
| ACT/ACT **ISDA** | 61/365 + 121/366 | **£497.72** |
| ACT/ACT **ISMA** | 182 / (182 × 2) | **£500.00** |
| ACT/ACT **AFB** | 182 / 366 | **£497.27** |

**The ACT/360 spread, computed.** $1,000,000 at a nominal 5 %, one full year: ACT/360 pays **$50,694.44** against
ACT/365F's **$50,000.00** — $694.44 per $1 M per year of pure convention. In a leap year: ACT/360 **$50,833.33**,
ACT/365F **$50,136.99**, ACT/ACT ISDA **$50,000.00**.

## 6 · Business-day conventions and calendars

Strata's class doc states the shape: *"The convention, **in conjunction with a holiday calendar**, defines exactly how
the adjustment should be made."*

| Convention | Strata's definition, verbatim | trap |
|---|---|---|
| `FOLLOWING` | *"The adjusted date is the next business day."* | |
| `MODIFIED_FOLLOWING` | *"…the next business day **unless that day is in a different calendar month, in which case the previous business day is returned**."* | `while (!isBusinessDay(d)) d = d.plusDays(1)` is `FOLLOWING`, not this |
| `PRECEDING` | *"The adjusted date is the previous business day."* | |
| `MODIFIED_PRECEDING` | *"…unless that day is in a different calendar month, in which case the next business day is returned."* | |
| `NEAREST` | *"If the input is Sunday or Monday then the next business day is returned. Otherwise the previous business day is returned. … **Note that despite the name, the algorithm may not return the business day that is actually nearest.**"* | reimplementing from the name gives different dates |

**Adjusting the payment date is not adjusting the accrual date.** FpML models unadjusted period end dates (which drive
accrual) separately from adjusted dates (which drive settlement). Rolling the accrual end date with the payment date
changes the interest; rolling neither pays on a holiday. Store both `unadjusted_period_end` and `payment_date`.
**Calendars are versioned data**, not a hard-coded list: governments declare holidays with weeks of notice, under
`BUS/252` a calendar change is an *interest* change, an instrument references the union of every relevant centre, and
recomputing a past period loads the calendar version in force then.

## 7 · The business date

Regulation Z Appendix J defines a day for money purposes, verbatim: *"The number of days between 2 dates shall be
**the number of 24-hour intervals** between any point in time on the first date to the same point in time on the
second date."* That is a **civil-date** definition, incompatible with `elapsed_seconds / 86400`. On a spring-forward
day the wall-clock interval between the same local time on consecutive dates is **82,800 s (23 h)**; on a fall-back
day **90,000 s (25 h)**. Dividing elapsed seconds by 86,400 under-accrues 1/24 of a day in spring and over-accrues
1/24 in autumn.

The same appendix pins month arithmetic: *"**All months shall be considered equal.** Full months shall be measured
from any point in time on a given date of a given month to the same point in time on the same date of another month"*,
and where payments fall on the 29th or 30th, *"**the last day of February shall be used when applicable**"*. This is
why the EOM flag is schedule-level state and not a property of a date function: `plusMonths` without it walks 31 Jan →
28 Feb → 28 Mar and never returns to the month end, changing every subsequent period length. (The EU/UK APR formula
legislates three inconsistent year lengths at once — SI 2010/1011 Sch ¶3 assumes *"365 days (366 days for leap years),
52 weeks or 12 equal months"*.)

**Timezone.** The value date's timezone is a property of the product and usually not the server's: a deposit at 21:00
New York on 30 June is value-dated 30 June for a USD product and 1 July in UTC. If the accrual job stamps `now()` in
UTC and the statement resolves the business date in `America/New_York`, the deposit accrues in the wrong month.
*(Mechanism-derived; no primary source located.)* Every posting carries a resolved `business_date date NOT NULL`
computed from `(instrument.value_date_tz, product.cutoff_time)`, never `date.today()`, with the cut-off a named config
value.

**Monotonicity.** Derive elapsed time from the stored period marker and assert the difference is non-negative before
using it. `now() - last_update` after an NTP step-back is negative, and in unsigned arithmetic underflows to an
enormous interval that accrues a century of interest in one run. `block.timestamp` is monotone by consensus rule; a
server clock is not.

## 8 · The accrual posting and per-period idempotence

An accrual is a balanced posting, not a mutation. The run's shipped invariant is `Σ credited to customer accounts == Σ
debited to interest expense`, exact to the minor unit, per currency.

**Key the accrual on `(account, period)` and let the database refuse the second write.** The double-accrual bug is a
job with at-least-once delivery doing `balance += balance * rate * dt` with no period marker.

```sql
CREATE EXTENSION IF NOT EXISTS btree_gist;

CREATE TABLE accruals (
  account_id    bigint    NOT NULL REFERENCES accounts(id),
  period_start  date      NOT NULL,
  period_end    date      NOT NULL,                 -- exclusive (§4)
  period        daterange GENERATED ALWAYS AS (daterange(period_start, period_end, '[)')) STORED,
  amount_minor  bigint    NOT NULL,
  residue       numeric(38,20) NOT NULL,            -- carried forward (§10)
  business_date date      NOT NULL,                 -- resolved (§7)
  ledger_txn_id uuid      NOT NULL REFERENCES ledger_transactions(id),
  CONSTRAINT accruals_once  UNIQUE (account_id, period_start, period_end),
  CONSTRAINT accruals_nogap EXCLUDE USING gist (account_id WITH =, period WITH &&)
);
```

`accruals_once` makes the retry a no-op (`INSERT … ON CONFLICT ON CONSTRAINT accruals_once DO NOTHING`, in the same
transaction as the ledger postings). `accruals_nogap` catches the harder bug — a *different* period overlapping one
already accrued, which a plain unique constraint accepts. The writer catches SQLSTATE `23505` and `23P01`
(exclusion_violation) and resolves both to "already accrued". The test runs the job twice for one period and asserts
the second run wrote **zero** rows and **zero** postings. Three independent protocols converged on the same guard:

| System | Guard | Marker |
|---|---|---|
| Compound v2 `CToken.accrueInterest()` | `if (accrualBlockNumberPrior == currentBlockNumber) return NO_ERROR;` — *"Short-circuit accumulating 0 interest"* | block number |
| Compound III `Comet.accrueInternal()` | `uint timeElapsed = uint256(now_ - lastAccrualTime); if (timeElapsed > 0) { … }` | `block.timestamp` |
| Aave v3 `ReserveLogic.updateState()` | `if (reserveCache.reserveLastUpdateTimestamp == uint40(block.timestamp)) return;` — *"If time didn't pass since last stored timestamp, skip state update"* | `uint40` timestamp |

**The index / scaled-balance pattern exists so you do not touch N accounts per period.** Aave v3.5 docs, verbatim:
*"The aave protocol handles balances & yield by storing a `scaledBalance` and an index. The actual balance is then
derived by multiplying the `scaledBalance` by the `index` or by dividing the `amount` by the index."* Compound v2 does
the same with a global `borrowIndex` and a per-borrower `interestIndex` snapshot. Per-account work is O(1); **the
journal still needs one aggregate posting per period for the pool's own income** (Aave's `_accrueToTreasury`), or the
books do not balance.

**Read the accrued index, not the stored one.** Aave ships `getNormalizedIncome()` / `getNormalizedDebt()` for this —
`calculateLinearInterest(rate, timestamp).rayMul(reserve.liquidityIndex)` when the stored timestamp is stale. Reading
`reserve.liquidityIndex` directly yields a balance correct as of the last time anybody touched the reserve.

**Back-dating.** A scaled-balance model represents a back-dated principal change exactly by minting `amount /
index(d)` rather than `amount / index(now)` — but only if the index history is retained and the cash was genuinely
present since `d`; otherwise it inflates the scaled supply and so the aggregate claim on the pool. Without `index(d)`
the back-date becomes a catch-up posting in the current open period — which is what IFRS 9 B5.4.6 requires anyway: the
adjustment is *"recognised in profit or loss as income or expense"* now, never by restating a closed period. **Period
attribution is a correctness property separate from amount correctness**: June's accrual posted in July balances
perfectly and is wrong in every statement, interest certificate, covenant test and tax filing. Assert that for each
closed period, `Σ postings WHERE business_date <@ period` is unchanged after any correction run.

## 9 · Path dependence: per-second, per-block, per-day

Iterative `index ← index × (1 + r·Δt/Y)` compounds across touches — over N touches the factor is `∏(1 + r·Δtᵢ/Y)`,
which grows with N. The exponential form telescopes: `∏ e^(r·Δtᵢ/Y) = e^(r·ΣΔtᵢ/Y)`, independent of how the interval
was chopped. Which law applies is a product decision, stated in the spec.

| System / leg | Law | Path-dependent? | Consequence |
|---|---|---|---|
| Aave v3 supply index | `MathUtils.calculateLinearInterest` ⇒ `index × (1 + r·Δt/Y)` | **yes** | at r = 10 %, one touch a year gives 1.10; daily touches give `(1+0.10/365)^365 ≈ 1.10516` — **0.47 % more interest** for the same rate and year, purely from interaction frequency |
| Aave v3 debt index | `MathUtils.calculateCompoundedInterest` ⇒ `index × approx(e^(r·Δt/Y))` | no | telescopes, up to series error |
| Compound v2 `borrowIndex`; Comet `baseSupplyIndex` | `borrowIndexNew = simpleInterestFactor * borrowIndex + borrowIndex`; `baseSupplyIndex_ += mulFactor(baseSupplyIndex_, supplyRate * timeElapsed)` | **yes** | realized APY is a function of how often the contract is poked, and that is in no rate parameter |

The same protocol is path-dependent on one leg and path-independent on the other; **an off-chain model that assumes
one law for both legs drifts continuously and never converges.**

**Closed-form versus iterative.** Aave's `calculateCompoundedInterest` returns `RAY + x + x.rayMul(x/2 +
x.rayMul(x/6))` with `x = rate * exp / SECONDS_PER_YEAR` — the third-order Maclaurin series of `e^x`, deliberately
biased; the source comment reads *"The approximation **slightly underpays liquidity providers and undercharges
borrowers**, with the advantage of great gas cost reductions."* The error is cubic: at `x = 0.05` the series gives
1.0512708 against `e^x` = 1.0512711 (6.3 × 10⁻⁶ of the interest); at `x = 0.5`, 1.6458333 against
1.6487213 — **0.445 %**; at `x = 1.0` — **1.9 %** of the growth factor. Concentrated where it is least visible:
high-rate, low-activity positions. If reporting computes `P·e^{rt}` while the ledger accumulates per period the two
never agree, and whichever the customer sees becomes the liability — test the ledger against the closed form to a
stated tolerance.

**A block count is not a clock.** Compound v2: `uint public constant blocksPerYear = 2102400;` — 31,536,000 s /
2,102,400 = **15.0 s/block**, which Ethereum has never had. Pre-Merge ~13.2 s gave ~2,388,700 blocks/year (1.136×
nominal); post-Merge slots are 12 s ⇒ 2,628,000 blocks/year, so a market configured for "5 % APR" charges **6.25 %**
until the rate model is redeployed. Comet's answer is `block.timestamp` directly; never denominate a rate in blocks,
ticks or job invocations. And **`SECONDS_PER_YEAR` is a convention declaration**: Aave's `MathUtils.sol` carries an
"Ignoring leap years" dev comment above `uint256 internal constant SECONDS_PER_YEAR = 365 days;`, so a nominal 5.00 %
reserve realises **5.0137 %** across a leap year — exactly ACT/365F, and defensible; the failure is an off-chain
accounting system reconciling the same reserve on a 366-day basis.

## 10 · Rounding inside the accrual

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

Two review consequences. **(a) The default is a decision you did not make** — Comet's `principalValueBorrow` is
`(presentValue_ * BASE_INDEX_SCALE + baseBorrowIndex_ - 1) / baseBorrowIndex_`, an explicit ceiling, while Compound
v2's `borrowBalanceStoredInternal` truncates, rounding debt **down** in the borrower's favour on every read. **(b)
Directions come in pairs** — the StErMi audit found v3.5's debt-in-USD rounding changed in `GenericLogic` but *"not …
the `LiquidationLogic`"*, changing which liquidation branch is taken. Note the regime split: where a statute
specifies, it specifies mode, level and precision, never direction-of-benefit (Reg Z §1026.22(a)(2)'s APR tolerance is
explicitly *"above or below"*); an unregulated index protocol chooses direction, in the house's favour.

## 11 · Claim and settlement

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

## 12 · Corporate actions and supply events

**The date triple, post-T+1.** FINRA Rule 11140(b), verbatim: for a distribution of **less than 25 %** of the value of
the subject security, *"the date designated as the 'ex-dividend date' shall be **the record date if the record date
falls on a business day**, or the **first business day preceding the record date**"* if it is not. The T+1 compliance
date was **28 May 2024** (SEC Release 34-96930). Under T+2 the ordinary ex-date was one business day *before* the
record date, so **any `ex_date = record_date.minus_business_days(1)` written before mid-2024 is now off by one day** —
and one day is the entire entitlement question. For distributions of **25 % or more** the ordering inverts: *"the
ex-dividend date shall be the **first business day following the payable date**."* Large special dividends, stock
dividends and splits effected as distributions land here, so `assert ex_date <= record_date <= pay_date` rejects
valid events.

**The announced rate is provisional by design.** Rule 10b-17(b)(1)(v)(a) permits *"a **reasonable approximation** of
the per share distribution … so long as the **actual per share distribution is subsequently provided on the record
date**"*, and guarantees notice only *"no later than **10 days prior to the record date**"*. A corporate-action record
therefore has a lifecycle — announced → revised → final — stored as versioned rows keyed `(issuer_event_id, version)`,
the entitlement job reading the version in force at its own business date.

**A split moves quantity and average cost in one transaction.** For an `a:b` split, `qty ← qty × a/b` and `avg_cost ←
avg_cost × b/a`, so `qty × avg_cost` is invariant and unrealised PnL is preserved; realised PnL already booked is
**not** restated. If quantity is updated by one job and basis by another, every read between them is wrong by `a/b` —
for a 1:10 reverse split, a **10×** error in unrealised PnL, in the direction of a spurious gain. The
regulator-verified consequence is the Robinhood AWC (FINRA No. 2020066971201, 30 June 2021): the performance chart
*"either overstated customers' gains or understated customers' losses … including because Robinhood mistakenly did not
properly account for … cash and position movements caused by corporate actions"*, with *"**no internal system …
triggered any alerts**"*. Apply the same event to open orders, price history and every derived series, not only to
positions.

**Fractional shares in a reverse split are a disposal, not a rounding.** SEC investor education: *"In some reverse
stock splits, small shareholders are '**cashed out**' (receiving a proportionate amount of cash in lieu of partial
shares)."* Cash-in-lieu creates a cash movement, realises PnL against the disposed fraction's basis and reduces the
remaining basis; `new_qty = round(old_qty / 10)` conserves neither shares nor money — rounding up mints shares from
nothing, rounding down destroys them silently.

**Ticker symbols are not identity.** A symbol can be changed by its issuer and later reassigned by the exchange to an
unrelated company, splicing two issuers' prices, actions and positions together. Key on a permanent internal
instrument id and model the symbol as a time-bounded attribute (`symbol, valid_from, valid_to`). *(Mechanism; no
citable rule located.)* Delisting likewise kills the price feed, not the position: **a missing price is not a zero
price**.

**Crypto analogues.** A migration or fork snapshot height is a **record date**; the crediting event is a **pay date**;
the moment the old asset stops trading with the entitlement attached is an **ex-date**. A redenomination (1 : 1000) is
a split and takes the quantity-and-basis rule unchanged. An airdrop is a stock dividend with **zero cost basis** —
booking it at market value as income *and* at market value as basis double-counts.

**Rebasing tokens break `balanceOf` assumptions.** Lido, verbatim: *"stETH token balances get recalculated **daily**
when the Lido oracle reports the Consensus Layer ether balance update"*, and the integration rule: *"it's highly
recommended to **store and operate shares** rather than stETH public balances directly, because stETH balances change
both upon transfers, mints/burns, **and rebases**, while shares balances can only change upon transfers and
mints/burns."* A rebase is an accrual delivered as a balance mutation: a position keeper diffing `balanceOf` books a
phantom deposit; an accounting system must attribute it to a period as income. The event stream is not the ledger here
— `ScaledBalanceTokenBase._burnScaled`, verbatim: *"**In some instances, a burn transaction will emit a mint event if
the amount to burn is less than the interest that the user accrued**"*; and the `Transfer` event emits *"the input
amount"* while `BalanceTransfer` emits *"the precise scaled amount"*. **Σ `Transfer` values will never reconcile to
balance changes for a scaled or rebasing token** — reconcile on scaled-balance-and-index, or on `balanceOf` deltas.

**Perpetual funding is a cliff, not a continuous accrual.** Hyperliquid, verbatim: *"The funding rate on Hyperliquid
is paid **every hour**"*, *"Funding is **purely peer-to-peer and no fees are collected on the payments**"*, capped
*"at 4%/hour"*, with payment `position_size * oracle_price * funding_rate` using the **oracle** price. On a venue with
an 8-hourly schedule a position held 7 h 59 m inside an interval pays **zero**, and one held one second across the
funding timestamp pays the **full** interval — funding cost is not proportional to holding time, and a pro-rata
amortisation never matches the venue's cash. *(The 8-hourly schedule is the industry norm; any specific venue's
schedule is unverified here.)* Where funding is peer-to-peer you get a free continuous assertion: **Σ funding payments
across all accounts for an interval == 0**.




