# Bitcoin value overflow (CVE-2010-5139) — the sum of outputs wrapped `uint64` before the "outputs ≤ inputs" check ran (2010-08-15)

**Domain:** Blockchain consensus, monetary policy | **Loss:** 184,467,440,737.09551616 BTC created from 0.5 BTC of inputs; 53 blocks orphaned; no lasting economic loss | **Failure class:** Representation — integer overflow before validation | **Skill:** fin-money-core

## What happened

In block 74638, a transaction created **184,467,440,737.09551616 BTC** across three outputs — two of
approximately 92.2 billion BTC each, plus 0.01 BTC to the miner — from inputs totalling 0.5 BTC. The
entire monetary policy of the network was briefly void: a 21-million-coin supply cap had just been
exceeded by roughly eight thousand times. A patched client shipped within about five hours, soft-
forking to reject overflowing transactions and any output total greater than 21 million BTC. The
good chain overtook the bad at block 74691, orphaning 53 blocks.

The number is not arbitrary. `184,467,440,737.09551616 × 10^8 = 2^64` exactly. That is the signature
of an unsigned 64-bit wraparound, and it is the whole bug.

## Root cause, in code terms

The validation was correct. It was applied to a value that had already lost the information it was
validating.

The check is `sum(outputs) <= sum(inputs)`. The accumulator for `sum(outputs)` was a `uint64` of
satoshi. An attacker constructs two outputs whose sum is exactly `2^64`, so the accumulator wraps to
**0**. The comparison then evaluates `0 <= 50000000` and passes. Both outputs are individually
enormous and individually representable; only their sum overflows, and the sum is the only thing
that was checked.

The canonical community record puts it plainly: "The code used for checking transactions before
including them in a block **didn't account for the case of outputs so large that they overflowed
when summed**."

The general shape:

```
total = 0
for out in tx.outputs:
    total += out.value          # unchecked accumulation in a fixed-width unsigned type
if total > sum_inputs:          # <-- validates a value that may already have wrapped
    reject(tx)
```

Every part of that is ordinary code. The defect is entirely in the order of operations: the
arithmetic happens first, the check happens second, and the arithmetic destroyed the information the
check needed. Two things fix it and only two — bound the operands *before* adding (`assert 0 <=
out.value <= MAX_MONEY` for each output, and `assert total <= MAX_MONEY - out.value` before each
addition), or perform the accumulation in a checked, saturating, or arbitrary-precision type where
wrapping is an error rather than a value.

The application-code analogues are exact, and none of them involve cryptocurrency: an `int`
accumulator over amounts in minor units; `int32` cents in a totals column; a database `SUM()` over a
column whose type is narrower than the aggregate; a fee or interest computation that multiplies
before it bounds.

## The invariant that was violated

```
# the money conservation rule, evaluated in a domain that cannot wrap
sum(outputs) <= sum(inputs) + subsidy

# and the per-value bound that makes the sum safe, checked BEFORE the arithmetic
forall output o:  0 <= o.value <= MAX_MONEY
forall partial sum s during accumulation:  0 <= s <= MAX_MONEY

# the general rule
validate(operands) THEN compute
NOT: compute THEN validate(result)
```

## Could an AI coding agent reviewing the diff have caught it?

**Yes. "Sum of user-controlled amounts in a fixed-width integer, then compared" is a mechanically
detectable pattern.**

The signal in a diff is a loop or a fold that accumulates a caller-supplied monetary quantity into a
fixed-width integer, followed by a comparison of the accumulated total against a limit — with no
per-operand bound and no checked arithmetic. The three sub-signals an agent should look for:

1. **Accumulation before validation.** Any `total += value` where `value` came from outside the
   trust boundary and `total` is later compared to something. The reviewing question is: "can this
   accumulation wrap, and if it does, does the subsequent comparison still mean what it says?"
2. **A per-element bound that exists but no partial-sum bound.** Checking each output against
   `MAX_MONEY` is necessary and insufficient; the sum of legal values can be illegal.
3. **Unchecked arithmetic in a language that offers a checked variant.** Rust's `checked_add`,
   Python's arbitrary-precision integers, a decimal type, a database `NUMERIC` — where the safe
   option exists and the wrapping one is used on a value path, that is the finding.

This is also the entry to reach for when justifying a general rule: the code was not exotic, the
attacker did nothing clever, and the check that was supposed to enforce the supply cap was present
and running.

## The rule

> **MUST — Perform bounds and overflow checks on the *operands*, before the arithmetic, or use
> checked/saturating/arbitrary-precision arithmetic.** Never validate a value that could already
> have wrapped.

> **MUST — Bound every partial sum, not only every element.** A collection of individually legal
> amounts can have an illegal total, and that is exactly the case an attacker constructs.

> **MUST — Represent monetary amounts as integers in the currency's minor unit or as an
> arbitrary-precision decimal, together with an explicit currency or asset code** — never as a
> binary floating-point number, and never as a bare number in an API field.

## Sources

- **Bitcoin Wiki, CVE-2010-5139, "Value overflow incident"** —
  <https://en.bitcoin.it/wiki/CVE-2010-5139>. **Primary-adjacent — the canonical community record.**
  Establishes: 15 August 2010, block 74638; a transaction creating
  **184,467,440,737.09551616 BTC** across three outputs; that the figure equals `2^64` satoshi
  exactly; the root cause verbatim ("The code used for checking transactions before including them
  in a block didn't account for the case of outputs so large that they overflowed when summed"); a
  patched client within approximately five hours; and the good chain overtaking the bad at block
  74691.
- **Related entry in this catalogue:** [Bitcoin duplicate-input inflation
  (CVE-2018-17144)](bitcoin-duplicate-input-2018.md) — the same supply invariant, broken eight years
  later by removing a check rather than by overflowing one.
