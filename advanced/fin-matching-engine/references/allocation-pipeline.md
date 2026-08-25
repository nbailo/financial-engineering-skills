# The allocation pipeline is data, and its last step distributes exactly

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

An allocation rule is a pipeline of steps configured per product, not one algorithm, and the property that
makes it safe is structural: the last step distributes exactly. Expressing the pipeline as data is what gives
that property somewhere to be asserted, at config load rather than at match time.

## Allocation as a pipeline of steps

**Everything attributed to CME in this section is historical and non-actionable**: cmegroup.com did not answer on 2026-08-25
(see the provenance block), so what follows is an example of the *shape* a published allocation rule takes, never as a rule to
implement. Carried unverified: "Algorithm steps are sequenced such that all quantity is allocated by the end of the algorithm
process", a step vocabulary of TOP → LMM → Split → FIFO/Pro-Rata → Leveling → FIFO, and product algorithm identifiers drawn
from `{A, C, F, K, O, Q, S, T}` [CME Globex Matching Algorithm Steps, **not revalidated**].

What binds is arithmetic rather than any venue: allocation is **a pipeline of steps configured per product**, not one
algorithm, and every step but the last may round.

Whatever your steps are, express them as **data**, one record per step with its parameters, and assert the terminating
property at config load rather than at match time:

```python
for instrument, pipeline in PIPELINES.items():   # pipeline loaded from the instrument definition
    assert pipeline and pipeline[-1].exact, (    # the last step must distribute exactly
        f"{instrument}: ends in {pipeline[-1].kind}, which rounds down")
```

`if algo == "PRO_RATA": ... elif algo == "FIFO": ...` cannot express a split or a TOP-then-pro-rata pipeline and has nowhere to
hang that assertion. It also makes config into code, and the EDGA/EDGX finding is that the engine's behaviour *is* the filed
rule ("Technical specifications are not a substitute for exchange rules", SEC Rel. 34-74032), so the product → pipeline map is
a published artefact.
