# The leftover pass: who gets the lot that division could not place

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

Floor division leaves a residue that is real quantity with no owner, so the pass that assigns it is part
of the algorithm. Which pass you run is published; that the pass is deterministic is not optional.

## The leftover pass

Default and most common: **FIFO by time priority**, one lot at a time down the queue. The one-extra-lot bound is a property of
that loop rather than a citation: a floor-division residue is strictly less than the participant count, and a loop that adds at
most one lot per participant therefore terminates with every participant up at most one lot. (A secondary source states the
same bound for one venue [Databento, "CME matching algorithms explained", **not revalidated**]; the loop below is the proof you
can run.) Alternatives that ship elsewhere, largest-remainder, round-robin from a rotating cursor, allocate-to-TOP, are
defensible; **none is a default you may pick silently**, because the choice is observable in the print.

```python
def assign_residue(allocations, resting_in_priority_order, residue):
    for o in resting_in_priority_order:            # a stable, total order; see below
        if residue == 0: break
        if allocations[o.id] < o.leaves:           # never allocate past the order's own size
            allocations[o.id] += 1; residue -= 1
    assert residue == 0, "residue exceeded eligible one-lot slots"
```

The tie-break key must be **total, deterministic and reproducible from the journal**:

| Key component | Source | Why not the obvious alternative |
|---|---|---|
| `price` | the resting order | n/a |
| `priority_seq` | the sequence assigned when priority was (re)established | wall-clock nanoseconds tie under batching, and a clock read in the core makes replay non-reproducible |
| `order_id` | last resort, only if ids are assigned in arrival order | a UUID or hashed id makes allocation depend on the id generator |

Never iterate a `HashMap`/`dict` whose order is unspecified or randomised: Rust's `HashMap` and Python's `set` vary across
processes or seeds, enough for two replicas to allocate the residue to different participants from one input stream. Use a
`BTreeMap`, a sorted `Vec`, or an intrusive queue per level.
