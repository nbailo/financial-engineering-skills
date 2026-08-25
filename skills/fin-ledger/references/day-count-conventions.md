# Day counts, calendars, and the business date

The lookup material behind the year fraction: the day-count convention, the calendar that adjusts the dates it
is measured between, and the civil date the result is attributed to. Covers the convention as contract data on
the instrument, the conventions that cannot be computed from two dates at all, the business-day adjustment
conventions and the versioned calendars they consume, and the civil-date definition of a day that
elapsed-seconds arithmetic violates. The posting that recognises it is `accrual-posting.md`.

## Contents

1. **Conventions are data**: the convention *and its definitions version*; `30E/360` under the 2000 versus 2006 ISDA Definitions; rejecting a null convention.
2. **Conventions that need the schedule**: `ACT/ACT ICMA`, `30E/360 ISDA`, `ACT/365L`; why a `yearFraction(LocalDate, LocalDate, Convention)` signature is the defect.
3. **The day-count fraction table**: ACT/360, ACT/365F, ACT/ACT (ISDA, ICMA, AFB), 30/360 US and Bond Basis, 30E/360, 30E/360 ISDA, BUS/252, and the "Actual/365" alias.
4. **Period boundaries**: half-open `[start, end)`, the conventions that are the other way round, the additivity test.
5. **Golden vectors**: ISDA's own worked examples, plus the same principal and dates paying three different amounts.
6. **Business-day conventions and calendars**: `FOLLOWING`, `MODIFIED_FOLLOWING`, `PRECEDING`, `MODIFIED_PRECEDING`, `NEAREST`; unadjusted accrual versus adjusted payment dates.
7. **The business date**: the civil-date day, DST and the 23-hour day, month-end, the value-date timezone, cut-offs, clock monotonicity.

## 1 · Conventions are data

The instrument row carries the convention **and the definitions vintage**, because the same code is a different
algorithm in different vintages. FpML's `dayCountFraction` coding scheme (genericode v2-2) annotates `30E/360`
verbatim: *"Note that the algorithm defined for this day count fraction **has changed** between the 2000 ISDA
Definitions and 2006 ISDA Definitions."* It annotates `ACT/ACT.ICMA` as *"applicable for transactions booked under the
2006 ISDA Definitions. Transactions under the 2000 ISDA Definitions should use the ACT/ACT.ISMA code instead"*: same
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

A hand-rolled two-date version does not throw for `ACT/ACT.ICMA`; it returns a plausible wrong number. The schedule
leaks in even for 30/360: Strata's `THIRTY_U_360` doc reads *"This day count **has different rules depending on
whether the EOM rule applies or not**. The EOM rule is set in the `ScheduleInfo`."*

## 3 · The day-count fraction table

| FpML code | numerator | denominator | pure fn of `(d1,d2)`? | trap |
|---|---|---|---|---|
| `ACT/360` | actual days | 360 | yes | 365/360 ⇒ **+1.3889 %** of interest a year against ACT/365F |
| `ACT/365.FIXED` | actual days | 365 | yes | a leap year pays 366/365 = 1.0027397× nominal |
| `ACT/ACT.ISDA` | days in each year-portion | 365 or 366 per portion | yes | the fraction is a **sum of two fractions**, not one division |
| `ACT/ACT.ICMA` | actual days in period | days in period × periods per year | **no**: schedule | a regular period yields exactly `1/frequency`; stubs need notional periods |
| `ACT/ACT.AFB` | actual days | 366 if the period contains 29 Feb, else 365 | yes | one 29 Feb anywhere flips the whole denominator |
| `30/360` (bond basis, ISMA) | `360Δy + 30Δm + Δd` after adjustment | 360 | yes | adjustment table below |
| `30E/360` | same shape | 360 | yes | **algorithm differs between the 2000 and 2006 Definitions** |
| `30E/360.ISDA` | same shape | 360 | **no**: termination date | the Feb-EOM rule is suppressed *only* at maturity |
| `BUS/252` | *"The number of **Business Days** in the Calculation Period"* | 252 | no: calendar | the numerator is a holiday-calendar query (FpML) |

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
`ACT/365.ISDA` became `ACT/ACT.ISDA` in FpML 3.0; a bare `"Actual/365"` in a feed is ambiguous data, to be rejected
at ingest, not coerced.

## 4 · Period boundaries

ISDA's 1999 Actual/Actual paper defines a calculation period as *"from, and including, one period end date (or the
effective date) to, but excluding, the next period end date (or the termination date)"*. Half-open `[start, end)` is
dominant, **and not universal**. Strata, verbatim: `ACT_ACT_ISDA` and `ACT_ACT_ICMA`: *"The first day in the period
is included, the last day is excluded"*; `ACT_365_ACTUAL`, `NL_360`, `NL_365`: *"The first day in the period is
excluded, the last day is included."* Getting it wrong is exactly one day of interest at every boundary: twelve free
days a year on a monthly product.

The test is per convention: `yf(d0,d1) + yf(d1,d2) == yf(d0,d2)` over a fuzzed schedule, plus `sum(1 for p in periods
if p.contains(d1)) == 1`. Additivity does **not** hold for `ACT/ACT.ICMA` across a coupon boundary with unequal
periods, nor for any 30/360 variant where `d1` is a 31st; assert it only where the convention is additive, and assert
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
ACT/365F's **$50,000.00**: $694.44 per $1 M per year of pure convention. In a leap year: ACT/360 **$50,833.33**,
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
legislates three inconsistent year lengths at once: SI 2010/1011 Sch ¶3 assumes *"365 days (366 days for leap years),
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
