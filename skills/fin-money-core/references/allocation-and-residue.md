# Allocation and residue

One exact total divided among N parts, or a rate applied per line and again at the total. Direction is
irrelevant here and conservation is the whole property: the largest-remainder algorithm with its arithmetic
written out, and the two places a real difference has to land.

## Contents

- Largest-remainder allocation, with complete worked arithmetic
- Where the residue goes, and when there isn't one
- Review checklist

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

**The boundary of that invariant is the split itself**, and `whole` is the amount actually available to
distribute. A fee, a withholding, a network cost or a platform cut taken on the way out crosses that boundary:
it is either one of the parts, or already subtracted from `whole` before the call, and the assert says which.
`sum(parts) == gross` over a split that also pays a fee is not a conservation check; it is a check that fails
on a correct implementation, and it fails until someone widens it into a tolerance.

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

## Review checklist

| Check | Fails when |
|---|---|
| The split asserts conservation at runtime | `assert sum(parts) == total` missing, or replaced by a comment or a tolerance |
| The split's tie-break is a declared total order | sorting by residue alone; iterating a `dict`/`set`/map |
| The residue has a named account, or the rate is applied once at the aggregate | a difference computed and then discarded, logged, or absorbed into the last line |
