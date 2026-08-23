# Cetus `checked_shlw` — an overflow guard with the wrong threshold constant and the wrong comparison operator (2025-05-22)

**Domain:** Concentrated-liquidity AMM arithmetic, Sui/Move | **Loss:** approximately $223M drained; ~$162M frozen by Sui validators; ~$61M likely permanently lost | **Failure class:** Representation — an overflow guard that certifies overflowing values as safe | **Skill:** fin-money-core

## What happened

On 22 May 2025 an attacker drained roughly $223M from Cetus on Sui by opening a liquidity position
of enormous size for a deposit of approximately **one token unit**. The defect was not in Cetus's
pool logic. It was in `checked_shlw()`, a general-purpose overflow guard in the `integer_mate`
library, which reported that a value would not overflow when it would — after which the shift
silently discarded the high bits, and the pool's own deposit calculation faithfully computed the
required amount from a number that had lost most of its magnitude.

## Root cause, in code terms

`checked_shlw()` guards a left-shift-by-64 on a `u256`. To be safe, a value must be strictly less
than `2^192` before the shift. The guard got two things wrong, independently:

1. **The threshold constant was wrong.** It used the mask `0xffffffffffffffff << 192` where the
   correct threshold is `1 << 192`. The mask is enormously larger than the real boundary — it is
   the top 64 bits set and shifted, not the boundary itself — so an entire range of values above
   `2^192` sat below the mask and were pronounced safe.
2. **The comparison operator was wrong.** It compared with `>` where `>=` was required, so even at
   the boundary itself the guard was off by one.

Both errors are invisible everywhere except at the boundary. Every ordinary value passes correctly.
Every test at a typical magnitude passes. The function reports `no overflow`, the shift executes,
the high bits are truncated, and the caller receives a plausible-looking small number with no error
anywhere in the chain.

Downstream, `get_delta_a` — which computes the token-A amount required for a given liquidity in a
given tick range — was fed the truncated numerator and computed a required deposit of approximately
**1 unit** for a position with liquidity `1.0365e34` in the tick range 300000–300200. The attacker
flash-loaned about 10.02M haSUI, swapped roughly 5.77M SUI to depress the pool price into the range
they wanted, opened the position for ~1 unit, then removed the position and withdrew real reserves.

The structural point: a guard that returns "safe" for unsafe input is worse than no guard at all. No
guard leaves the danger visible and forces the caller to think about it. A wrong guard is a positive
assertion of safety that every caller correctly relies on.

## The invariant that was violated

```
# what the guard must actually guarantee
checked_shlw(n) reports OVERFLOW  for every n >= 2^192
checked_shlw(n) reports OK        for every n <  2^192

# the boundary case, stated explicitly because it is the one that was wrong
n == 2^192           => OVERFLOW      (this requires >=, not >)
n == 2^192 - 1       => OK
n == 2^192 + 1       => OVERFLOW

# the general rule for any guard
threshold_constant(guard) is derived from the operation being guarded
NOT: chosen by resemblance to a mask of the right width
```

## Could an AI coding agent reviewing the diff have caught it?

**Yes. This is the single most agent-detectable incident in the catalogue, and the reason is that
the correct check is derivable from the operation being guarded.**

The reviewing procedure is entirely mechanical:

1. **Read the operation.** A left shift by 64 on a `u256` overflows exactly when the operand is
   greater than or equal to `2^(256-64) = 2^192`.
2. **Read the guard's threshold and compare.** The guard uses `0xffffffffffffffff << 192`. That is
   not `2^192`; it is `(2^64 - 1) × 2^192`. An agent that computes both values sees they differ by
   19 orders of magnitude. The threshold does not match the operation, and no domain knowledge is
   needed to say so.
3. **Read the operator.** Overflow at the threshold means the boundary value itself must be
   rejected, so the comparison is `>=`. A `>` admits exactly one value that overflows.
4. **Ask for the boundary tests.** For any guard, there are exactly three tests that matter —
   `threshold - 1`, `threshold`, `threshold + 1` — and their absence is a finding in its own right.

The generalisable review question is short: **"what is the exact largest safe input, and is there a
test for it?"** Asked of `checked_shlw`, it finds both errors immediately. Asked of any guard, it
finds the class.

This entry is also the strongest available argument that off-by-one review is not pedantry on a
money path. The difference between `>` and `>=` here is $223 million.

## The rule

> **MUST — When writing or reviewing an overflow guard, prove the boundary case by construction.**
> Derive the exact threshold from the operation being guarded, check it with `>=` semantics, and
> unit-test `threshold - 1`, `threshold`, `threshold + 1`.

> **MUST — A guard's threshold constant must be derived from the operation, not chosen for
> resembling a mask of the right width.** `1 << 192` and `0xffffffffffffffff << 192` look similar
> and differ by a factor of `2^64 - 1`.

> **MUST — Treat every `>` / `>=` and `<` / `<=` on a value path as a boundary decision requiring a
> test at equality.** The same class produced Compound's Proposal 62 over-distribution (`>` where
> `>=` was required, in two places, in a payout guard).

> **MUST — An arithmetic guard that can return "safe" for an unsafe value is a more serious defect
> than an absent guard**, because every caller is entitled to rely on it.

## Sources

- **BlockSec, "Cetus Incident: One Unchecked Shift Drains $223M"** —
  <https://blocksec.com/blog/cetus-incident-one-unchecked-shift-drains-223m-largest>. **Secondary
  but code-level.** Establishes: 22 May 2025; that `checked_shlw()` in `integer_mate` guarded a
  left-shift-by-64 using the mask `0xffffffffffffffff << 192` instead of `1 << 192`, **and**
  compared with `>` rather than `>=`; that values which would overflow were reported as safe and the
  shift silently truncated the high bits; and that `get_delta_a` then computed a required token-A
  amount of approximately **1 unit** for a position with liquidity `1.0365e34` in the tick range
  300000–300200. Also establishes the attack setup (a flashloan of 10,024,321.28 haSUI and a swap
  of 5,765,124.79 SUI to depress the pool price) and the ~$223M loss. BlockSec's prose says the
  guard used "an incorrect constant **and comparison**"; the specific `>`-where-`>=`-was-required
  reading comes from the vulnerable-versus-patched code comparison it reproduces, not from the
  narrative.
- **Not established by BlockSec.** The article says nothing about funds being frozen or
  unrecoverable — "frozen", "validator", "$162M" and "$61M" do not appear in it. The ~$162M frozen
  by Sui validators and the ~$61M treated as unrecoverable come from Cetus's and the Sui
  Foundation's own statements and should be cited to those, not to this analysis.
- **Same class, different protocol:** Compound Proposal 62 (29–30 September 2021), where `>` was
  used where `>=` was required in the new Comptroller's accrual comparison, in two places,
  permitting ~168,000 COMP (~$50M) to be claimed erroneously. Reported by CryptoSlate and The Block
  and attributed to auditor Kurt Barry; no official postmortem naming the exact function was
  located, so the class is established and the precise line is not.
