# Pro-rata arithmetic, and the residue integer division always leaves

> **Provenance**
> provider: CME Group, Globex matching algorithms · surface: the published allocation-step rule text ·
> version: not established, the page did not answer
> verified_at: not established
> sources: https://www.cmegroup.com/confluence/display/EPICSANDBOX/Globex+Matching+Algorithm+Steps ·
> Databento, "CME matching algorithms explained", https://databento.com/blog/cme-matching-algorithms
> verified: nothing here was read from a primary source in this pass. The arithmetic is a property of the
> operation rather than of a venue: integer floor division, the conservation assertion, and the bound that a
> residue is strictly smaller than the participant count all hold with no vendor claim behind them.
> unverified: **every CME claim below is historical and non-actionable.** cmegroup.com did not answer on
> 2026-08-25 across repeated attempts in two independent passes, so "Fractional lots received are rounded
> **down** prior to allocation", "Pro Rata is never the last step of an algorithm due to the required
> rounding" and the Pro-Rata Minimum form are quoted as an example of what such a rule looks like and **must
> not be copied into code**. The one-extra-lot bound attributed to Databento was not re-read either; the loop
> below is the proof that does not need it.
> revalidate_when: cmegroup.com answers and the matching-algorithm rule text can be read directly, or before
> any CME-derived line here is copied into code.

How an aggressing quantity is split across resting orders at one price, why integer division always leaves
a residue that a second and exact pass has to assign, and what makes both passes reproducible from the
journal. Conservation and the residue bound are properties of the arithmetic and hold wherever quantity is
divided, so nothing here is a venue's choice except the leftover pass the last step hands off to.

## Allocation is not finished until the residue has an owner

Specialises *rounding and conservation*: rounding down cannot distribute everything, so the residue pass is
part of the algorithm, not a follow-up, and pro-rata is never the last step. Do the arithmetic in integers,
multiplying before dividing; define the leftover pass, most commonly FIFO by time priority but always the pass
your own rules name; and assert `Σ allocations == min(aggressing quantity, Σ resting quantity)` before any
execution is emitted. Both passes are **deterministic**: the tie-break key is total and reproducible from the
journal, never the iteration order of a map whose order is unspecified, which is enough for two replicas to
hand the residue to different participants from one stream. Three quantity conventions share one word, an
intended total after a cancel, a chain-cumulative total on a replace and a decrement on a modify; reading one
as another moves `leaves` the wrong way, and the conversions are a rulebook entry.

## Pro-rata arithmetic and the residue

Integer floor division cannot distribute a quantity that does not divide, so **a pro-rata step is never the last step**: the
remainder it leaves is real quantity with no owner until a later exact step assigns it. That is arithmetic, not a venue rule,
and it is the reason the pipeline assertion above is worth writing. One published rule text says the same thing in its own
words, carried here unverified and as illustration only: "Fractional lots received are rounded **down** prior to allocation"
and "Pro Rata is never the last step of an algorithm due to the required rounding" [CME Globex Matching Algorithm Steps, **not
revalidated**]. Do the arithmetic in integers, **multiply before dividing**:

```python
alloc_i = (leaves_i * match_qty) // total_leaves       # correct
alloc_i = int(match_qty * (leaves_i / total_leaves))   # WRONG: float division then truncation
```

`leaves_i / total_leaves` is a binary float; the product can land a hair *above* the true value, `int()` then returns one lot
more than the exact floor, and that lot makes `Σ alloc > match_qty` and over-fills the aggressor. Worked case, an aggressing
buy of 100 lots against four resting sells at one price, total resting 997:

| Order | Priority | `leaves` | `leaves × 100 // 997` | Residue pass (below) | Final |
|---|---|---|---|---|---|
| A | t0 | 500 | 50 | +1 | 51 |
| B | t1 | 300 | 30 | 0 | 30 |
| C | t2 | 120 | 12 | 0 | 12 |
| D | t3 | 77 | 7 | 0 | 7 |
| | | **997** | **99** | **1** | **100** |

The residue is bounded by `n − 1` for `n` participants and is zero only when every quotient divides exactly. Assert
conservation in the function that builds the executions, before any is emitted:

```rust
let allocated: u64 = allocations.values().try_fold(0u64, |a, q| a.checked_add(*q))
    .ok_or(EngineError::AllocationOverflow)?;
let target = aggressing_qty.min(total_leaves);   // NOT aggressing_qty alone: the book caps it
if allocated != target {   // conservation breach: freeze the book, do not publish, do not clamp
    return Err(EngineError::AllocationResidue { allocated, target });
}
```

**A minimum-allocation threshold, where your rules define one.** A threshold that zeroes small allocations is a venue rule and
this file establishes none: the form recorded from an earlier unverified pass is `{displayed working qty}/{total working lots}
× {match qty} ≥ PR Min` against the **original denominator** [CME, **not revalidated**]. Whatever your rule says, the zeroed
quantity flows into the leftover pass, conservation still has to hold, and a threshold makes allocation **non-monotone in
`match_qty`**: a participant can take zero on a 100-lot aggressor and a positive allocation on a 101-lot one, which an allocator fuzzer has to generate for.
