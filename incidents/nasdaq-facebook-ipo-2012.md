# NASDAQ Facebook IPO cross — a revalidation loop that consumed one event per pass, then a mitigation that deleted the check (2012-05-18)

**Domain:** Exchange auction/cross computation and market data | **Loss:** $10,000,000 penalty; $62M voluntary accommodation fund; $10.8M error-account profit; a $35.3M haircut producing a $26.5M net-capital deficiency at NES | **Failure class:** Concurrency & ordering | **Skill:** advanced/fin-matching-and-settlement

## What happened

The Facebook IPO cross was scheduled for 11:05. It did not complete. NASDAQ's engineers diagnosed a
loop in the cross computation, failed over to a duplicate matching engine with the offending
validation check removed, and ran the cross at 11:30:09 — using an order book as it stood at 11:11.
More than 38,000 marketable orders entered between 11:11 and 11:30:09 were excluded; roughly 8,000
were released into the market at 11:30 and more than 30,000 became "stuck", neither executed nor
released, for over two hours. Confirmations were not disseminated. NASDAQ ended the day holding an
unauthorised short position of more than 3 million Facebook shares. Members' losses were larger
than NASDAQ's; the widely quoted UBS figure of ~$356M is **not** in the SEC order, which quantifies
only NASDAQ/NES figures and the accommodation programme.

## Root cause, in code terms

**The pipeline.** The `IPO Cross Application` computes a price and volume over the order set. The
matching engine then runs a **validation check** confirming that no order used in that computation
was cancelled during it. On failure, recompute.

**The defect is a progress-rate bug, not a missing retry ceiling.** Each recomputation advanced the
input cursor by exactly one cancellation:

> "This second calculation by the IPO Cross Application, if necessary, **incorporated only the
> first cancellation received during the first calculation** … Thus, if there were multiple orders
> cancelled during the first IPO Cross Application's calculation, the validation check performed
> after the second calculation would fail again and the IPO Cross Application would need to be run
> a third time in order to include the second cancellation …" (¶9)

> "During the next price/volume calculation four more cancellations arrived. And **because the
> system was designed to perform a separate recalculation for each of those cancellations**, the
> validation check failed each time … **A loop resulted.**" (¶20)

Convergence therefore required the arrival rate of cancellations to fall below one per
recomputation. It did not. The computation took 20 milliseconds against a normal 1–2 (¶17), and
NASDAQ had load-tested the cross to 40,000 orders in the test security while members entered "over
496,000 orders" into the Facebook cross (¶12).

**The mitigation deleted the correctness check.** The engineers proposed failing over to a
duplicate matching engine and "**removed several lines of code that configured the validation check
function from the failover system**". The order notes NASDAQ "had not tested for this situation"
and normally uses failovers "as duplicates of its existing systems … **rather than as the vehicle
for launching a new, modified version of those systems**" (¶23). The SVP/INET who authorised it
"was unaware of the existence of the validation check" before that morning (¶22).

**Nothing was "promoted"; no replica was behind.** This is the point every summary garbles. The
19-minute staleness was an **input backlog inside the livelocked primary**:

> "the IPO Cross Application's inability to escape the loop caused by the validation check up until
> the failover, had caused the IPO Cross Application to fall 19 minutes behind the orders received
> by NASDAQ. As a result, when NASDAQ switched to the failover system at 11:30:09 a.m., the IPO
> Cross Application calculated the price and volume of the cross based on the orders and
> cancellations received **up until 11:11 a.m.**" (¶26)

Deleting the validation check removed the only thing that would have detected that staleness.

**Acknowledged cancels were filled anyway, and telling anyone was never discussed.** Footnote 4 to
¶24: an alternative "would have been to inform those members who had entered cancellations … that
their orders had not been successfully cancelled, **even though NASDAQ's system had, immediately
upon submission, acknowledged these members' cancellations**. However, this alternative **was not
discussed by any of the participants** on the Code Blue call." Because more sell than buy shares
were cancelled in that window, NASDAQ took the other side: a short position of more than 3 million
shares valued at ~$129 million (¶28), which yielded a $10.8 million error-account profit as the
price fell (¶40).

**The downstream reconciler was right and told nobody.**

> "the Execution App, **having not been affected by the validation loop**, viewed orders and
> cancels up until the time the cross occurred at 11:30:09 a.m. The Execution App was therefore
> unable to reconcile its share count with the cross as executed … Almost immediately after the
> bulk print was released, **the Execution App marked the cross as being in error and did not
> disseminate confirmations** for orders executed in the cross." (¶30)

Its only action was to withhold output. Members could not determine what position they held. The
same divergence published a stale **crossed quote (bid > ask)** at top of book on the proprietary
feed and the SIP (¶31) — an arithmetically impossible state and a one-line assertion.

Two signals were available and unused: NASDAQ's Chief Economist noticed the "approximately **6.3
million share difference**" between the final indicative volume (82 million) and the print (75.7
million), which "indicated … that there was still a problem with the cross … **but NASDAQ did not
address this issue**"; and "**NASDAQ could have run a real-time status check of its applications**,
which would have indicated that the cross executed at 11:30 a.m. did not include any orders entered
after 11:11 a.m." (¶27).

**NASDAQ's own agreed remediation names the two implementable fixes** (¶65): "**close its order
ports to new Cross orders and cancels … after the calculation of the Cross is triggered**", and for
other crosses "**take into account bursts of changes to orders that would affect the result of the
Cross in one recalculation of the Cross rather than in multiple recalculations**." Neither is
"bound the retries" — a retry ceiling would have aborted the Facebook cross, not completed it.

## The invariant that was violated

```
# progress
revalidate_pass consumes ALL pending events        # or: input_set is frozen before compute begins
NOT: revalidate_pass consumes exactly one event

# freshness at commit
input_set(computation).max_timestamp >= commit_time - epsilon     # asserted AT commit

# acknowledgement
ack(cancel) => cancel is honoured  OR  the acknowledgement is retracted to the member before fill

# reconciliation
reconcile_mismatch => raise(owned_alert)           # withholding output is not a response

# market data
forall published_quote q: q.bid <= q.ask
```

## Could an AI coding agent reviewing the diff have caught it?

**Yes — and it would have caught more than one of them.**

The **loop** is visible in the source. The signal is a revalidation branch whose recompute step
advances an input cursor by a single element rather than to the current tail of the queue, inside a
loop with no terminal action. The reviewing question — "if events arrive faster than one per pass,
does this terminate?" — is answerable from the code alone. Note that an agent applying the popular
folk version of this incident would propose the *wrong* fix: a retry ceiling makes the cross abort.
The correct findings are "freeze the input set" or "drain the queue in one pass".

The **check deletion** is the most reviewable diff in the entire catalogue: a change that removes
lines configuring a validation function from a failover configuration, shipped during an incident.
An agent should refuse it outright.

The **acknowledged-then-discarded cancel** is a state-machine finding: a recovery path that
includes orders whose cancellation was already ACKed, with no notification branch.

The **reconciliation that only gates output** is a classic review pattern — a mismatch is detected,
a flag is set, output is suppressed, and no alert is raised.

The **crossed quote** is a one-line assertion on the publish path.

What an agent could not have caught: that the cross had been load-tested to 40,000 orders when
production would carry 496,000, unless the test fixture and the expected production scale are both
in the repository — which is exactly why the test-adequacy question belongs to a verification pass
rather than a code review.

## The rule

> **MUST — A revalidate-and-recompute loop must consume the entire pending event queue in one pass,
> or the input set must be frozen before the computation begins.** A pass that consumes one event
> is a livelock whenever the arrival rate exceeds one event per pass. Bounding retries is not the
> fix; it converts the hang into an abort.

> **MUST — Never disable a validation or reconciliation check to force completion during an
> incident.** If the check is blocking, halt the operation. If a check is ever disabled, every
> output produced afterwards must be quarantined and reconciled before it is treated as
> authoritative.

> **MUST — Assert the freshness of a computation's input set at the point of commit, not at the
> point the computation started.**

> **MUST — Never acknowledge a cancellation, amendment or write to a counterparty before it is
> durable and unconditional.** If a recovery path may discard acknowledged mutations, it must
> notify every affected party explicitly.

> **MUST — A reconciliation failure must raise an owned, escalating alert.** Withholding output is
> not a response.

> **MUST — Assert `bid <= ask` on every quote before publication.**

## Sources

- **SEC Admin. Proc. 34-69655, *In the Matter of The NASDAQ Stock Market LLC and NASDAQ Execution
  Services LLC*, 29 May 2013** —
  <https://www.sec.gov/files/litigation/admin/2013/34-69655.pdf>. **Primary.** Establishes ¶9 and
  ¶20 (one cancellation per pass — the mechanism), ¶12 (40,000-order test cap vs 496,000+ in
  production), ¶17 (20 ms vs 1–2 ms), ¶22 (the SVP/INET was unaware the check existed), ¶23 (the
  failover with validation-check lines removed; untested for this situation), ¶24 fn 4 (the
  acknowledged cancels, and that notifying members "was not discussed"), ¶26 (the 19-minute backlog
  *inside the primary*; >38,000 excluded, ~8,000 released, >30,000 "stuck"), ¶27 (the 6.3M-share
  discrepancy noticed and not addressed; the available real-time status check), ¶28 and ¶40 (the
  >3M-share short, ~$129M, $10.8M error-account profit), ¶30 (the Execution App withholding
  confirmations), ¶31 (the crossed quote), ¶42 ($35.3M haircut, $26.5M NES deficiency), ¶65 (the
  agreed remediation), §IV ($10,000,000 penalty; $62M accommodation).
- **Correction applied.** This is widely described as a failover that promoted a *lagging replica*, and
  as each recomputation "snapshotting the cancels known at its start". Both are contradicted by ¶9,
  ¶20, ¶23 and ¶26 of the order. The rule text above follows the order and NASDAQ's own ¶65 remediation.
- **Not established by the order.** UBS's frequently cited ~$356M loss does not appear in 34-69655.
