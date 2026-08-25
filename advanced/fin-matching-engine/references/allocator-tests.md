# The properties an allocator keeps, and the generator that has to reach them

> **Provenance**
> provider: CME Group, Globex matching algorithms · surface: the published allocation-step rule text and the
> product algorithm identifiers · version: not established, the page did not answer
> verified_at: not established
> sources: https://www.cmegroup.com/confluence/display/EPICSANDBOX/Globex+Matching+Algorithm+Steps ·
> https://www.sec.gov/litigation/admin/2015/34-74032.pdf
> verified: nothing here was read from a primary source in this pass.
> unverified: **every CME claim below is historical and non-actionable** for the reason recorded across this
> skill's references: cmegroup.com did not answer on 2026-08-25 across repeated attempts in two independent
> passes. So "Algorithm steps are sequenced such that all quantity is allocated by the end of the algorithm
> process", the TOP, LMM, Split, FIFO/Pro-Rata, Leveling step vocabulary and the identifier set are an example
> of the shape a published rule takes and **must not be copied into code**. SEC Rel. 34-74032 (EDGA/EDGX) and
> the TigerBeetle fuzzer account were not re-read in this pass and are carried on their inline attributions.
> revalidate_when: cmegroup.com answers and the step vocabulary can be read directly, or before any
> CME-derived line here is copied into code.

Conservation, cap, priority monotonicity, determinism under shuffle and the residue bound, written as
assertions with the bug each catches. The generator, not the assertion list, is the binding constraint.

## Property tests for an allocator

| Property | Assertion | Bug it catches |
|---|---|---|
| Conservation | `Σ alloc == min(aggressing_qty, Σ leaves)` | floor-and-stop pro-rata; a leftover pass out of slots |
| Cap and sign | `0 ≤ alloc_i ≤ leaves_i` | one-extra-lot loop allocating past an order's size |
| Priority monotonicity | equal `leaves` at one price ⇒ `priority_seq_i < priority_seq_j` implies `alloc_i ≥ alloc_j` | residue assigned to the wrong end of the queue; reversed comparator |
| Determinism under shuffle | shuffling the input list, priority keys preserved, yields an identical allocation map | `HashMap` iteration; dependence on arrival container order |
| Residue bound | `residue < participant_count`, and each participant gains **at most one** extra lot | a residue pass that runs twice; a pro-rata denominator error |
| Price | every execution's price is the one your published convention selects | charging the aggressor its own limit under a resting-price convention |
| Pipeline termination | `pipeline[-1].exact` for every configured product | a product configured to end in Pro-Rata |
| Self-trade prevention | the published scope and strategy, exercised at `R > I`, `R = I` and `R < I`, with the counterfactual recorded and excluded from volume | a strategy assumed from memory; the equal-size degenerate case; a scope that is narrower than the one published |
| Exposure transfer | after each fill, `Δ working_leaves == −Δ filled_position`; in the same commit as the execution where the position store shares that transaction, and otherwise derived from the execution id by a step that is idempotent when replayed twice | a position booked without decrementing `leaves`, which double-counts against a credit limit; a derivation that adds the same execution twice |

```python
resting = st.lists(st.tuples(st.integers(1, 10_000), st.integers(0, 1_000_000)), min_size=1, max_size=12) \
           .map(lambda xs: [Order(leaves=q, priority_seq=s) for q, s in xs])

@example(orders=[Order(500, 0), Order(300, 1), Order(120, 2), Order(77, 3)], aggr=100)  # the 997-lot residue case, pinned
@given(orders=resting, aggr=st.integers(1, 30_000))
@settings(max_examples=2000)
def test_allocator(orders, aggr):
    alloc = allocate(orders, aggr, PIPELINE_PRO_RATA_THEN_FIFO)
    assert sum(alloc.values()) == min(aggr, sum(o.leaves for o in orders))
    assert all(0 <= alloc[o.id] <= o.leaves for o in orders)
    shuffled = orders[:]; random.Random(0).shuffle(shuffled)
    assert allocate(shuffled, aggr, PIPELINE_PRO_RATA_THEN_FIFO) == alloc
```

One property-testing behaviour to plan around, because it silently ends exploration: a counterexample survives only if it is
committed as an `@example(...)`, since the local example database is discarded by CI, which is why the residue case is pinned above.

The generator is the binding constraint, not the assertions. Assert coverage *on the generator*: some fraction of cases hitting
`aggr > Σ leaves`, some producing a nonzero residue, some placing a participant exactly on the Pro-Rata Minimum boundary,
some with two orders of equal `leaves` and adjacent `priority_seq`. TigerBeetle shipped a query bug because its fuzzer always
generated objects consecutive in the index; an allocator fuzzer whose quantities always sum exactly to the aggressor never runs
the residue pass at all.
