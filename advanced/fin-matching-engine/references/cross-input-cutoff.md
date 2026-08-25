# Freezing the input set: a cutoff, or a bounded batch, and no third design

> **Provenance**
> provider: NASDAQ Stock Market LLC · surface: the SEC administrative proceeding over the Facebook IPO cross
> of 18 May 2012, and the remediation it records · version: SEC Rel. 34-69655, 29 May 2013
> verified_at: not established
> sources: https://www.sec.gov/litigation/admin/2013/34-69655.pdf
> verified: nothing here was re-read from the primary source in this pass.
> unverified: every paragraph number below (¶9, ¶12, ¶17, ¶20, ¶23, ¶24 fn 4, ¶26, ¶28, ¶30, ¶31, ¶65) is
> carried on its inline attribution from an earlier pass and was not re-checked against the order on
> 2026-08-25. The quoted remediation language is the load-bearing part of this file, so read it out of the
> order itself before citing it anywhere that matters.
> revalidate_when: before any paragraph number or quotation here is repeated outside this repository, or if
> the SEC republishes the order at a different URL.

Two designs terminate when a price has to be computed over a set that is still changing, and consuming one
event per pass is neither of them. Both designs below are the remediation an SEC order records after a cross
that looped, so they are what a regulator accepted rather than what this repository invented.

## An auction prices a finite input set: a cutoff or a bounded batch, never a queue it is losing to

Never compute a price over state concurrent changes can mutate between compute and print. Two designs
terminate and they are the only two. A **cutoff**: close the order ports for that instrument when the
calculation is triggered, and reject late arrivals with a typed error. A **bounded batch**: take the entire
pending queue into one recomputation, with a stated maximum number of passes whose fallback is the cutoff,
never another pass. Consuming one event per pass is neither: it is a livelock whenever the arrival rate
exceeds one per pass, and a component livelocked on its queue keeps accepting inputs it cannot process, so its
last output is arbitrarily stale. A bare retry ceiling only converts the hang into an abort that leaves the
cross unpriced. Assert input-set freshness at commit and carry that watermark onto the record you print. SEC
Rel. 34-69655, NASDAQ and the Facebook IPO, 18 May 2012: ¶20, *"because the system was designed to perform a
separate recalculation for each of those cancellations"*, so *"a loop resulted"*, and ¶65's remediation is
these two designs and no third.

## The cutoff-or-bounded-batch contract

Two designs terminate; consuming one event per pass is neither.

**A: Cutoff.** Close the order ports for that security when the calculation is triggered, the remediation NASDAQ agreed to
(SEC Rel. 34-69655 ¶65): "For IPO and Halt Crosses, NASDAQ will **close its order ports to new Cross orders and cancels** of
orders in the security involved in the Cross **after the calculation of the Cross is triggered**." Late arrivals are **rejected
with a typed error**, not silently queued. It terminates because the input set stops changing.

**B: Bounded batch.** Take the whole burst in one recomputation. Same paragraph: "For Opening and Closing Crosses, NASDAQ will
change its system to **take into account bursts of changes to orders that would affect the result of the Cross in one
recalculation of the Cross rather than in multiple recalculations**." A drain terminates only while arrivals stop for one compute
window, so it carries a **stated maximum pass count** whose fallback is design A: close the ports and price the set you have."

```python
# WRONG: the cursor advances by one event per pass (SEC 34-69655 ¶9, ¶20)
while True:
    price, volume = compute(book.snapshot())
    ev = pending.pop_one()                        # ONE cancellation
    if ev is None: break
    book.apply(ev)                                # ...and recompute

# RIGHT: drain to the tail, compute, commit only if nothing arrived meanwhile; the pass
# bound falls back to the cutoff, so the cross is priced over a finite, stated input set.
for _ in range(MAX_DRAIN_PASSES):                 # a stated number, not `while True`
    while (ev := pending.pop()) is not None:      # ENTIRE queue
        book.apply(ev)
    watermark = book.last_applied_seq
    price, volume = compute(book)
    if book.last_applied_seq == watermark:        # freshness at the point of commit
        commit(Cross(price, volume, input_watermark=watermark)); break
else:
    close_order_ports(symbol)                     # design A, reached deterministically
    while (ev := pending.pop()) is not None: book.apply(ev)
    watermark = book.last_applied_seq
    commit(Cross(*compute(book), input_watermark=watermark))
```

**A bare retry ceiling is not the fix**: aborting would have left the Facebook cross unpriced. The defect is a loop making
strictly less progress than the arrival rate, and the only two cures are stopping the arrivals or consuming all of them. **Carry the watermark onto the cross record**: the downstream confirmation
component compares *that number*, rather than recomputing a share count from its own view (the mismatch that produced the
two-hour confirmation blackout below).
