# Integer arithmetic traps

The arithmetic underneath a rounding decision, in a language that truncates. Truncation is a directed mode
chosen by accident, its bias flips with the sign, and the guard that keeps a value in range is invisible at
every input except the boundary. The modes themselves are named in `rounding-modes.md`.

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
| Boundary tests exist at `threshold-1 / threshold / threshold+1` | any mask, cap, tick boundary or `>`/`>=` on a money path with only mid-range test values |
