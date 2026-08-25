# Rounding, allocation, and boundary arithmetic

You arrived here because the diff contains `round(`, `floor`, `ceil`, `trunc`, `int(`, `quantize(`, `//`,
`* rate`, `/ total`, `split`, `allocate`, `pro_rata`, `convert`, or `%` on a money value. This file supplies
the mechanism behind MC3: the per-operation direction table for an exchange, the largest-remainder algorithm
with its arithmetic written out, the regimes where mode and *level* are prescribed by law, and the
boundary-test triple that finds the bug class invisible at every input except one. Everything here assumes
integer minor units or an exact decimal; a rounding argument over a `float` is about the wrong number.

## Contents

- Which of the three rounding problems you have: the dispatch table
- Rounding mode names, and what each one actually does
- Directed rounding as a security property: the pool/vault direction table
- The round-trip property test, and the exhaustive small-domain search that proves it
- Direction is necessary, not sufficient: empty denominators and the first depositor
- One helper on both legs: Balancer V2 ComposableStablePool
- Where the direction is not yours to choose: statute, regulation, contract
- Interest accrual: the day-count fraction is contract data, not a coding choice
- Largest-remainder allocation, with complete worked arithmetic
- Where the residue goes, and when there isn't one
- Boundary testing at `threshold-1 / threshold / threshold+1`: the Cetus shape
- Truncation is a biased rounding, and the bias flips with sign
- Integer-division traps, by language
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
| toward 0 (truncate) | `ROUND_DOWN` | `DOWN` | n/a | `roundTowardZero` | almost never; see the truncation section |

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

## Directed rounding as a security property: the pool/vault direction table

EIP-4626, Security Considerations, verbatim:

> "Vault implementers should be aware of the need for **specific, opposing rounding directions across the
> different mutable and view methods**, as it is considered most secure to **favor the Vault itself during
> calculations over its users** … The only functions where the preferred rounding direction would be
> ambiguous are the `convertTo` functions … it is specified that these functions MUST both always round
> _down_."

Read the two middle columns: the **derived** side of the trade is what gets rounded, against the user, every
time.

| Operation | User supplies | User receives | Computed quantity | Direction | OZ `ERC4626.sol` |
|---|---|---|---|---|---|
| `deposit(assets)` | assets (exact) | shares (derived) | shares out | **Floor** | `_convertToShares(a, Math.Rounding.Floor)` |
| `mint(shares)` | assets (derived) | shares (exact) | assets in | **Ceil** | `_convertToAssets(s, Math.Rounding.Ceil)` |
| `withdraw(assets)` | shares (derived) | assets (exact) | shares in | **Ceil** | `_convertToShares(a, Math.Rounding.Ceil)` |
| `redeem(shares)` | shares (exact) | assets (derived) | assets out | **Floor** | `_convertToAssets(s, Math.Rounding.Floor)` |
| `convertToShares` / `convertToAssets` | n/a | n/a | view/oracle estimate, not an obligation | **Floor**, both, by spec | n/a |

The same table governs an FX booking, a points-to-cash redemption, a fee taken in a different asset from the
one quoted, a base↔quote conversion. One sentence: **floor the amount the system pays out, ceil the amount
it collects, measured in the unit the counterparty receives or supplies, per leg.**

Note the *shape* of the preview spec: `previewDeposit` "MUST return as close to and **no more than** the
exact amount of Vault shares that would be minted"; `previewMint` and `previewWithdraw` "no fewer than";
`previewRedeem` "no more than". Those are **inequalities against the truth**, not rounding modes: the
falsifiable form, and the form to assert in tests.

## The round-trip property test, and the exhaustive small-domain search

The direction table is not an aesthetic preference; an exhaustive search over a tiny state space proves it.
`TS, TA ∈ [0,40)`, `x ∈ [1,40)`, 64,000 cases, reproduced locally against the OZ formulas:

```python
def cdiv(a, b): return -(-a // b)                            # ceil, positive ints
ok = bad = 0
for TS in range(40):
  for TA in range(40):
    for x in range(1, 40):                                   # deposit x assets, redeem what you got
      s  = (x * (TS + 1)) // (TA + 1)                        # deposit -> Floor
      if (s * ((TA + x) + 1)) // ((TS + s) + 1) > x: ok += 1
      s2 = cdiv(x * (TS + 1), TA + 1)                        # both legs Ceil == the bug
      if cdiv(s2 * ((TA + x) + 1), (TS + s2) + 1) > x: bad += 1
# correct (Floor/Floor): ok  ==      0 profitable round-trips
# inverted (Ceil/Ceil):  bad == 50,068 profitable round-trips
```

Ship that as a property test, not a comment, and ship the **multi-actor** form, which survives a real
extraction: for an adversarially ordered sequence by *different* principals, assert `Σ outputs ≤ Σ inputs` per
asset and that no user-initiated round trip returns more than it put in. A single-actor test passes on designs
that leak when A deposits and B withdraws. Second property test, from the Balancer post-mortem: run **N ≥ 50
minimum-magnitude operations inside one atomic transaction** and assert the pool is not worse off: the
attacker packed 65 micro-swaps (amounts as small as 17 units) into one `batchSwap`, so losses accumulated
against transient internal balances before settlement and no per-operation tolerance test could see them.

## Direction is necessary, not sufficient: empty denominators and the first depositor

Correct direction with a manipulable denominator still loses everything. OZ's `CAUTION` block on
`ERC4626.sol`, verbatim: *"In empty (or nearly empty) ERC-4626 vaults, deposits are at high risk of being
stolen through frontrunning with a 'donation' to the vault that inflates the price of a share."*

Worked in wei, `shares = assets * totalSupply / totalAssets`, **no** virtual offset:

```
attacker: deposit 1 wei      -> totalSupply = 1,      totalAssets = 1
attacker: transfer 10e18 directly to the vault (bypasses deposit(), so totalSupply unchanged)
                             -> totalSupply = 1,      totalAssets = 10e18 + 1
victim:   deposit 5e18       -> shares = 5e18 * 1 / (10e18+1) = 0        <- floor to ZERO shares
attacker: redeem 1 share     -> assets = 1 * (15e18+1) / 1  = 15,000,000,000,000,000,001
          attacker outlay 10e18+1, attacker take 15e18+1 -> +5e18 profit, victim gets nothing
```

Same arithmetic with OZ's virtual units (`+1` on `totalAssets()`, `+10 ** _decimalsOffset()` on
`totalSupply()`):

| `_decimalsOffset()` | victim's shares | attacker's redeem | attacker P&L on a 10e18 donation |
|---|---|---|---|
| none (no virtual units) | 0 | 15,000,000,000,000,000,001 | **+5e18** |
| `0` (OZ default) | 0 | 7,500,000,000,000,000,001 | **−2.5e18** |
| `3` | 999 | 5,001,667,222,407,469,157 | **−5e18** |

The default offset does not stop the victim losing; it makes the attack **unprofitable**, which is exactly
what OZ claims ("analysis shows that the default offset (0) makes it non-profitable"); a larger offset makes
it "orders of magnitude more expensive than it is profitable", at the cost that virtual shares capture a very
small part of the yield.

**The companion predicate for every share/price ratio:** *can the denominator reach 0 or 1, and can a third
party inflate the numerator by a direct transfer that bypasses the accounting entrypoint?* If yes, add
virtual shares/assets, seed-and-burn at launch, or an initialisation deposit burned to the vault. This class
recurred through **Hundred Finance → Midas Capital** (>$10M combined, 2023) and then **Onyx Protocol** (1–2
Nov 2023, $2.1M / 1,164 ETH) *after* the mitigation was documented; the Compound-V2 zero-supply
exchange-rate bug and the ERC-4626 first-depositor attack are one shape, four incidents.

## One helper on both legs: Balancer V2 ComposableStablePool

3 Nov 2025, >$120M (reported $128.6M, 8+ chains, 25+ forks inheriting it). The code documents its own bug:

```solidity
function _upscale(uint256 amount, uint256 scalingFactor) pure returns (uint256) {
    /* Upscale rounding wouldn't necessarily always go in the same direction:
       in a swap for example the balance of token in should be rounded up,
       and that of token out rounded down. This is the only place where we
       round in the same direction for all amounts, as the impact of this
       rounding is expected to be minimal. */
    return FixedPoint.mulDown(amount, scalingFactor);
}
```

OpenZeppelin's analysis: *"These functions always round-down (`mulDown`) independently from the direction of
the swap"*, and *"if amounts are orders of magnitude less than `scalingFactors` ones, the precision loss
becomes non-negligible."* The safety argument was **conditional** ("there's no rounding error unless
`_scalingFactor()` is overriden"), and `ComposableStablePool` overrode it to fold in a live exchange rate
(e.g. `1.058132408689971699e18`), silently deleting the precondition.

Three grep-able smells fall out: (1) one rounding helper (`mulDown`, `round()`, `floor()`, a shared `_q()`)
applied to **both** legs of an exchange; (2) a safety comment predicated on a value a subclass, config row or
later migration can change: `_scalingFactor()`, `decimals()`, a `rate_provider`; (3) a batch primitive that
settles only at the end of a sequence, so intermediate rounding lands on transient balances no invariant
check sees.

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

## Largest-remainder allocation, with complete worked arithmetic

Naive per-part rounding does not conserve. Matt Foemmel's conundrum: split 5¢ 30/70 half-up and you get
`round(1.5)=2` plus `round(3.5)=4`, **6¢ out of 5¢**; three ways, each part rounds to 2¢, 6¢ again; floor
everything and $100.00 three ways loses 1¢.

The algorithm (largest remainder / Hamilton), **in integer minor units only, no decimals, no floats**:

```python
def allocate(total: int, weights: list[int], ids: list[str]) -> list[int]:
    D = sum(weights)                                    # D > 0 required; D == 0 is a caller bug, raise
    base = [(total * w) // D for w in weights]          # exact integer arithmetic
    remainder = total - sum(base)                       # 0 <= remainder < len(weights), always
    order = sorted(range(len(weights)),
                   key=lambda i: (-((total * weights[i]) % D), ids[i]))   # STABLE, DETERMINISTIC
    for i in order[:remainder]:
        base[i] += 1
    assert sum(base) == total                           # postcondition: assert it, do not comment it
    return base
```

Worked, every column:
```
total = 10499 minor units, weights = [3333, 3333, 3334]   (D = 10000)
  base_i      = 10499*3333//10000, 10499*3333//10000, 10499*3334//10000  = [3499, 3499, 3500]
  Σ base      = 10498                         -> remainder = 10499 - 10498 = 1
  (T*w_i)%D   = [3167, 3167, 3666]            -> descending: idx2 (3666), then idx0/idx1 tie -> id order
  +1 to idx2  -> [3499, 3499, 3501]           -> Σ = 10499  ✓

total = 5,  weights = [30, 70]   -> base [1, 3], rem 1, residues [50, 50], tie broken on id -> [2, 3]  Σ=5 ✓
total = 100,weights = [1, 1, 1]  -> base [33,33,33], rem 1, residues [1,1,1]                -> [34,33,33] Σ=100 ✓
total = 1003, weights = [1, 1]   -> [502, 501]  (this is Dinero.js v2's own documented example)
total = -500, weights = [1, 1, 1]-> base [-167,-167,-167], rem 1                            -> [-166,-167,-167] Σ=-500 ✓
```

**The invariant is `sum(parts) == whole`, exactly, asserted at runtime: for every input including
`total = 0`, `total < 0`, `len(weights) == 1`, and a weight of `0`.** A zero weight must receive 0: base 0,
residue 0, so it sorts last and never collects a remainder unit; test that rather than trust it.

**The tie-break is load-bearing.** `order` must be a total order over a stable key: declared sort position,
then entity id; never `dict` insertion order, `set` iteration order, or a hash-map walk. An unstable
tie-break makes the *same* input split differently on different runs, breaking replay, reconciliation and
idempotent retry; the retry lands a different cent on a different seller.

**Negative totals break this in every language whose `/` truncates.** Python's `//` floors, so
`remainder ∈ [0, n)` holds for negative totals and the code above is correct as written. In C, Go, Rust, Java
and JavaScript integer division truncates toward zero, `base` comes out *larger* than the floor, `remainder`
goes **negative**, the `order[:remainder]` loop silently does nothing, and `Σ parts` misses by up to `n-1`
minor units. Use `Math.floorDiv` (Java) / `div_euclid` (Rust), or allocate `abs(total)` and negate.

## Where the residue goes, and when there isn't one

Two situations get confused, and only one has a leftover.

**(a) Splitting an exact total.** Largest-remainder distributes the whole remainder one minor unit at a time:
nothing is left over and **no rounding account is involved**. Split code that posts to a rounding account is
not conserving; find the bug.

**(b) Applying a rate, then reconciling lines against the total.** Rounding at two levels produces a real
difference that has to land somewhere:
```
lines: 19.99, 4.95, 0.70 USD;  tax rate 5%;  HALF_UP to 0.01
  per line:   0.9995->1.00,  0.2475->0.25,  0.0350->0.04     Σ lines = 1.29
  aggregate:  25.64 * 0.05 = 1.2820 -> 1.28
  difference = 0.01  <- this is not noise; it is a posting
```

Choose one of exactly two policies and write it down: **round once at the aggregate** (the IRS instruction:
"add the amounts including cents, then round off only the total") *or* **sum the rounded lines and post the
difference to a named account**. Stripe does the second: where a payout must be a multiple of 100 minor units
(HUF, TWD; ISK/UGX payouts must end in `00`) it "automatically rounds that amount to the nearest number
evenly divisible by 100. We credit or debit any difference from rounding to the customer balance." **The
residue is posted somewhere named, never dropped**; a discarded residue is an unbalanced ledger, and a
"kept" residue with no account is theft with extra steps.

The observable form of "the party the residue accrues to" is **the party whose balance the conservation or
solvency check reads**: the pool in a vault, the platform's named rounding-difference account in a processor,
whoever the statute names. If no such account exists in the schema, the design is wrong before you reach the
rounding decision. Magnitude, from Cowlishaw's telco example: 5% tax on a $0.70 call in binary64 gives
`0.70 * 1.05 == 0.7349999999999999` → `0.73`, exact decimal gives `0.7350` → `0.74`. "Taken over a million
transactions of this kind … these systematic errors add up to an **overcharge of more than $20** … over a
whole year the error then **exceeds $5 million**."

## Boundary testing at `threshold-1 / threshold / threshold+1`: the Cetus shape

Cetus, 22 May 2025, **$223M**. `checked_shlw()` in `integer_mate` guarded a left-shift-by-64 on a `u256`;
overflow occurs iff `n ≥ 2^192`. The guard used the mask `0xffffffffffffffff << 192` where `1 << 192` was
intended, **and** compared with `>` where `>=` was required (BlockSec, "Cetus Incident: One Unchecked Shift
Drains $223M").

```
correct guard:   overflow  <=>  n >= (1 << 192)                    = 2^192
shipped guard:   overflow  <=>  n >  (0xffffffffffffffff << 192)   = 2^256 - 2^192
```
Two independent errors, each invisible except at the boundary:

| input | shipped guard says | truth | shipped `>` with the *correct* mask |
|---|---|---|---|
| `2^192 - 1` | safe | safe ✓ | safe ✓ |
| `2^192` | **safe** | overflows | **safe** ✗: a single-point defect in a 2²⁵⁶ domain |
| `2^192 + 1` | **safe** | overflows | rejected ✓ |
| `2^256 - 2^192` | **safe** | overflows | rejected ✓ |

The wrong mask widened the hole from one value to essentially the entire overflowing range; the `>` alone
would still have left exactly one exploitable input. Values that *would* overflow were certified safe, the
shift silently truncated the high bits, and `get_delta_a` computed a required token-A deposit of **~1 unit**
for a position with liquidity `1.0365e34` in the tick range 300000–300200.

**Therefore: every threshold, mask, cap, tick boundary and comparison operator on a money path is unit-tested
at `threshold-1`, `threshold`, `threshold+1`, with the threshold written as a *constructed expression*
(`1 << 192`, `10**scale`, `MAX / rate`) rather than a literal.** The same operator error appears with no
overflow in sight: Compound Proposal 62 (29–30 Sep 2021) used `>` where `>=` was required, **in two places**,
in the new Comptroller; ~168,000 COMP (~$50M) was claimed within hours, bounded at ~280,000 only because the
Comptroller held a limited balance. Off-by-one in accrual is high-yield because every test written from
non-boundary values passes.

## Truncation is a biased rounding, and the bias flips with sign

Truncation toward zero is not a mild rounding: expected error **−0.5 ulp for positive values** and **+0.5
ulp for negative** ones, against ≈ 0 for round-to-nearest. Over N operations truncation drifts by
~`0.5·N·ulp`, nearest by ~`O(√N)·ulp`. Empirical proof: the Vancouver Stock Exchange index, 1982–83, at
~3,000 truncations/day × 473 trading days × 0.0005 ≈ **710 index points** against an observed 574-point gap.

Because the bias reverses sign, a system that truncates "in the house's favour" on charges becomes
customer-favouring (or unbounded) on refunds, credits and reversals: the second reason a single global
rounding helper cannot exist. Truncation of a **scaled float** is the specific killer, because ordinary
retail prices are already below their decimal value as binary64:

| literal | `round(v*100)` | `int(v*100)` | exact value of `Decimal(float(v))*100` |
|---|---|---|---|
| `1.005` | 100 | **100** | 100.4999999999999893418589636 |
| `1.15` | 115 | **114** | 114.9999999999999911182158030 |
| `8.475` | 848 | **847** | 847.4999999999999644728632120 |
| `1.115` | 112 | **111** | 111.4999999999999991118215803 |

`int()` / `floor()` / `trunc()` on a scaled float loses a whole cent on three of these four. The Bitcoin
community's canonical conversion rule says the same: use `long(round(value * 1e8))`, because "if you truncate
instead of doing proper rounding … your software will display the value '0.1 BTC' as '0.09999999 BTC' (or,
worse, '0.09 BTC')" (sound for BTC only because `21e6 × 1e8 = 2.1e15 < 2^53`, and catastrophic verbatim on
an 18-decimal ERC-20). And `round()` is not a rescue: `round(1.005, 2) == 1.0` and `round(2.675, 2) == 2.67`
in Python and most languages, because `2.675` as a double is `2.67499999999999982…`, not a bug in `round`,
just the literal being the wrong number before `round` ran.

## Integer-division traps, by language

| Language | `-7 / 2` (integer) | `-7 % 2` | Floor-division primitive |
|---|---|---|---|
| Python | `-7 // 2 == -4` (floors) | `1` | `//` is already floor; `math.trunc` for the other |
| Go | `-3` (truncates) | `-1` | none built in |
| Rust | `-3` (truncates) | `-1` | `i64::div_euclid` → `-4`, `rem_euclid` → `1` |
| Java | `-3` (truncates, JLS §15.17.2) | `-1` | `Math.floorDiv` / `Math.floorMod` |
| JavaScript | `Math.trunc(-7/2) === -3`, `(-7/2)|0 === -3` | `-1` | `Math.floor(-7/2) === -4` |
| Solidity | truncates toward zero (moot for `uint`, not for `int`) | n/a | none; `mulDiv(..., Rounding)` in OZ `Math` |

Verified locally for Python, Go, Rust and Node; Java from the JLS. Four rules follow.

- **Never write `x / y` on money and call the result rounded.** In every truncating language that expression
  is `ROUND_DOWN`, a *directed* mode, chosen by accident, and wrong for negatives.
- **`a * b / c` order matters.** Multiply first, divide last, in a width that cannot overflow the product:
  `(total * weight) // D`, never `total * (weight // D)` and never `total * (weight / D)` through a float.
- **`%` on money is exact only on integers.** Venue filters are literal exact-decimal modulo,
  `price % tickSize == 0` (Binance `PRICE_FILTER`), `quantity % stepSize == 0` (`LOT_SIZE`); and float
  modulo does not satisfy them: `0.29 % 0.01 == 0.009999999999999974`, `4.7 % 0.1 == 0.09999999999999992`.
  Represent price as an integer count of ticks, or a decimal at the instrument's declared scale.
- **A decimal type removes representation error, not rounding error.** At the default 28-digit context
  `Decimal(1)/Decimal(3)*3 == Decimal("0.9999999999999999999999999999")`, so every division, percentage,
  rate and pro-rata step still needs an explicit `quantize(scale, mode)` and an explicit remainder policy;
  run inexact operations inside a `localcontext()` with a declared `prec`. SQL Server's `money` truncates at
  **intermediate** steps (fixed scale 4): divide by 10, multiply by 10, and you do not get back the value.

## Review checklist

| Check | Fails when |
|---|---|
| Every rounding call site names a scale **and** a mode | `round(x)`, `int(x)`, `x // 1`, `mulDown(x, f)` with no scale argument |
| The exchange has two direction constants, not one helper | one `_q()` / `mulDown()` / `floor()` used on both legs |
| The share/price ratio guards its denominator | `assets * totalSupply / totalAssets` with no `totalSupply == 0` guard and no virtual offset or seed-and-burn |
| The prescribed mode and **level** are configuration | a hardcoded `ROUND_HALF_UP`, or per-line rounding where the statute names the total |
| The day-count convention and definitions version live on the instrument | an accrual that divides by `365` or `360` inline |
| The split asserts conservation at runtime | `assert sum(parts) == total` missing, or replaced by a comment or a tolerance |
| The split's tie-break is a declared total order | sorting by residue alone; iterating a `dict`/`set`/map |
| The residue has a named account, or the rate is applied once at the aggregate | a difference computed and then discarded, logged, or absorbed into the last line |
| Boundary tests exist at `threshold-1 / threshold / threshold+1` | any mask, cap, tick boundary or `>`/`>=` on a money path with only mid-range test values |
| Property tests: multi-actor `Σ outputs ≤ Σ inputs`, and N ≥ 50 dust operations in one transaction | only single-actor, single-operation tests |
