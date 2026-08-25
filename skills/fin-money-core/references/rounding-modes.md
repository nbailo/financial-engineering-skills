# Rounding modes and prescribed regimes

Which of the three rounding problems you have, what each mode name actually does, and the regimes where
the mode and the *level* are prescribed by law rather than chosen. Everything here assumes integer minor units
or an exact decimal; a rounding argument over a `float` is about the wrong number.

## Contents

- Which of the three rounding problems you have
- Rounding mode names, and what each one actually does
- Where the direction is not yours to choose
- Interest accrual: the day-count fraction is contract data, not a coding choice
- Review checklist

## Which of the three rounding problems you have

Answer this before choosing a mode. The three columns need different code and have different correctness
arguments; the commonest defect here is applying one column's answer in another.

| | **Exchange / conversion** | **Prescribed calculation** | **Split / allocation** |
|---|---|---|---|
| **Observable trigger** | two representations of one value are traded, and the counterparty chooses when and how often (shares↔assets, LP tokens↔reserves, base↔quote, points↔cash) | a statute, regulation, scheme rule or contract names the mode, the level, or the day-count | one exact total is divided among N parts |
| **What goes wrong** | a repeatable free-money loop, profitable at 1 wei × ∞ calls | non-compliance; a two-sided tolerance breached systematically in one direction | value created or destroyed; `Σ parts ≠ total` |
| **Rule** | opposing directions per leg: **floor what the system pays out, ceil what it collects** | **copy the specified mode AND level** from the instrument/jurisdiction | **direction is irrelevant**; largest-remainder + exact-sum assert |
| **Residue goes to** | the pool/vault/ledger holding the conservation invariant | wherever the specification says | distributed one minor unit at a time; nothing is left |
| **Canonical source** | EIP-4626 Security Considerations; OZ `ERC4626.sol`; Balancer `_upscale` | CJEU C-302/07; Reg (EC) 1103/97 Arts. 4–5; IRS i1040; 12 CFR 1030.3(f), 1026.22(a); FpML/ISDA | Fowler `Money.allocate`; Dinero.js v2 `allocate`; IRS "round only the total" |

"Round in the house's favour" is the **Exchange** column only, and wrong even there as one global helper;
the sources say *opposing* directions per leg. In a Prescribed calculation it is a supervisory finding; in a
Split it destroys conservation.

## Rounding mode names, and what each one actually does

Two families engineers conflate: **round-to-nearest** modes differ *only at exact midpoints*; **directed**
modes ignore the midpoint and always go one way.

| Family | Python `decimal` | Java `RoundingMode` | .NET | IEEE 754-2019 | Use for |
|---|---|---|---|---|---|
| nearest, ties away from 0 | `ROUND_HALF_UP` | `HALF_UP` | `MidpointRounding.AwayFromZero` | `roundTiesToAway` | IRS filing; euro conversion; VAT where the state mandates it |
| nearest, ties to even | `ROUND_HALF_EVEN` | `HALF_EVEN` | `MidpointRounding.ToEven` (default) | `roundTiesToEven` | statistical/interbank contexts that want zero cumulative bias |
| toward −∞ | `ROUND_FLOOR` | `FLOOR` | n/a | `roundTowardNegative` | the leg the system **pays out** |
| toward +∞ | `ROUND_CEILING` | `CEILING` | n/a | `roundTowardPositive` | the leg the system **collects** |
| toward 0 (truncate) | `ROUND_DOWN` | `DOWN` | n/a | `roundTowardZero` | almost never; a directed mode whose bias flips with the sign |

Sources: Python `decimal` docs (default `prec=28`, `ROUND_HALF_EVEN`); Java `RoundingMode` (which calls
`HALF_UP` "the rounding mode commonly taught at school" and `HALF_EVEN` "Banker's rounding"); .NET
`MidpointRounding`; OZ exposes the two a vault needs as `Math.Rounding.Floor` / `.Ceil`.

**`FLOOR`/`CEILING` are not mirror images across zero; `DOWN`/`UP` are.** "Round in the customer's favour"
means CEILING on a payout and FLOOR on a charge (different modes), and the two swap the moment the amount
goes negative (a refund, a credit, a reversal). That is why "we always round our way" is not one helper.

**Direction without a scale is a no-op**: `ceil` at 18 decimals on an 18-decimal token rounds nothing. Name
the scale at every call site (`quantize(x, Decimal("0.01"), ROUND_HALF_UP)`, `mulDiv(a, b, c, Ceil)`) and
resolve it from runtime metadata: exponent 0 for JPY/KRW/VND/CLP/ISK/UGX, 3 for BHD/KWD/JOD/OMR/TND, 4 for
CLF, and Stripe's charge, payout and display scales differ within one currency.

## Where the direction is not yours to choose

In every regime below the authority fixes the **mode**, the **level** (per line / per basket / per invoice /
per return), or the **precision and tolerance**, and **none** says "round toward the party doing the
computing". Mode and level both change the answer, so both are per-jurisdiction/per-instrument configuration,
never constants.

| Regime | What is fixed | Verbatim / normative |
|---|---|---|
| **EU VAT** | *nothing* at EU level: Member States fix it; round-**up** may be mandatory | CJEU C-302/07 *J D Wetherspoon* (5 Mar 2009): EU law "contains **no specific requirement concerning the method of rounding**"; it "does not require that taxable persons be allowed to round down"; it "**does not preclude** … a national rule which requires an amount of VAT to be rounded up whenever the fraction … is at or above 0.50"; each Member State determines "**the level at which the rounding** … may or must occur" |
| **Euro conversion** | rate precision, direction, path, intermediate precision, final mode = **half-up** | Council Reg (EC) 1103/97 Arts. 4–5 as restated by the European Commission: rate is **6 significant figures**; "it is **prohibited to round or truncate the conversion rate**"; bilateral rates "cannot be used"; convert *through* EUR, round the EUR leg "to at least three decimals"; "€1.264 becomes €1.26 … €1.265 becomes €1.27" |
| **US federal tax** | **half-up**, and the **level is the total** | IRS Form 1040 instructions: "Drop amounts under 50 cents and increase amounts from 50 to 99 cents to the next dollar"; if you round, "add the amounts **including cents**, then **round off only the total**" |
| **Truth in Savings (Reg DD)** | APY/APYE/rate **rounded to the nearest 0.01%**, two decimals; two-sided accuracy tolerance **±0.05%** | 12 CFR 1030.3(f)(1)–(2); APY formula fixed by Appendix A: `APY = 100[(1 + Interest/Principal)^(365/Days) − 1]` |
| **Truth in Lending (Reg Z)** | APR accurate to **±1/8 of 1 percentage point**; **±1/4** for irregular transactions | 12 CFR 1026.22(a)(2)–(3) |
| **Interest accrual** | the **day-count fraction** is a contract term with a normative definition | FpML `dayCountFraction` coding scheme → ISDA Definitions (see below) |

Half-up is **symmetric** (it favours the payer half the time), so house-favouring rounding on a euro
conversion or a tax line is non-compliant and trivially auditable. And "above **or** below" is a two-sided
tolerance: systematically landing on the house-favouring edge is a supervisory finding even when every
individual number is within tolerance.

*(Unverified, do not cite as authority: HMRC VAT Notice 700 §17.5–17.7, the UK concession said to let invoice
traders (not retailers) round total VAT **down** to a whole penny; gov.uk truncates that section in every
fetch. The C-302/07 operative part above is a secondary rendering: EUR-Lex and InfoCuria render empty to
fetch tooling; CELEX `62007CJ0302`, companion `62006CJ0484` *Koninklijke Ahold*.)*

## Interest accrual: the day-count fraction is contract data, not a coding choice

`ACT/360` and `30/360` over the same period produce **different interest amounts**. FpML's
`dayCountFraction` scheme (<https://www.fpml.org/coding-scheme/day-count-fraction>) enumerates the values and
points each at its normative definition:

| FpML code | 2021 ISDA Definitions | 2006 ISDA Definitions |
|---|---|---|
| `1/1` · `ACT/ACT.ISDA` · `ACT/365.FIXED` | §4.6.1(i) · (ii) · (iv) | §4.16(a) · (b) · (d) |
| `ACT/360` · `30/360` | §4.6.1(v) · (vi) | §4.16(e) · (f) |
| `30E/360`: *"the algorithm defined for this day count fraction has changed between the 2000 ISDA Definitions and 2006 ISDA Definitions"* | §4.6.1(vii) | §4.16(g) |
| `30E/360.ISDA` | §4.6.1(viii) | §4.16(h) |

Three consequences: (i) store the convention **on the instrument** and carry it into the calculation, never
default it, never infer it from the currency; (ii) the `30E/360` note proves the algorithm behind a *stable
code* changed between definition versions, so the **definitions version** is contract data too; (iii) test
the engine against the convention named on the trade, not "a year". *(ISDA body text is paywalled: the
day-adjustment algorithms are not verified here; only the enumeration and section references are.)*

## Review checklist

| Check | Fails when |
|---|---|
| Every rounding call site names a scale **and** a mode | `round(x)`, `int(x)`, `x // 1`, `mulDown(x, f)` with no scale argument |
| The prescribed mode and **level** are configuration | a hardcoded `ROUND_HALF_UP`, or per-line rounding where the statute names the total |
| The day-count convention and definitions version live on the instrument | an accrual that divides by `365` or `360` inline |
