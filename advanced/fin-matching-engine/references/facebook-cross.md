# The Facebook cross of 18 May 2012, step by step

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

The worked failure behind the cutoff-or-bounded-batch rule, in the order the paragraphs of the SEC order
record it, ending in the reconciliation that was right and escalated to nobody.

**The interleaving, step by step** (SEC Rel. 34-69655, paragraphs inline):

| Time | Event | Consequence |
|---|---|---|
| 11:05:00 | Cross triggered; load-tested to 40,000 orders, members entered **over 496,000** (¶12) | one calculate-plus-validate pass takes **20 ms** against a usual **1-2 ms** (¶17) |
| pass 1→n | Each pass "incorporated only the **first** cancellation received during the first calculation" (¶9), so a cancel is always outstanding | "a separate recalculation for each of those cancellations… **A loop resulted**" (¶20); the app falls **19 minutes** behind the live stream (¶26) |
| 11:30:09 | Failover to an engine with "several lines of code that configured the validation check function" removed (¶23); acknowledged cancels were filled anyway, and telling members "**was not discussed**" (¶24 fn 4) | cross prints over the book **as of 11:11**; **38,000+** marketable orders excluded; **30,000+** stuck (¶26); "more sell shares than buy shares were cancelled" (¶28) leaves a **>3 million share short** |
| after | The Execution App holds the 11:30:09 view and cannot reconcile it against an 11:11 cross | "marked the cross as being in error and **did not disseminate confirmations**" (¶30); a crossed quote published (¶31) |

The last row is the one to design against: **a reconciliation whose only action is to withhold output is an availability failure
wearing a correctness check's clothes.** It was right, and it escalated to nobody.

Read the row sequence as a control list rather than as a story, because each row is a control that either did
not exist or existed and did not act. The load test was run against a volume an order of magnitude below what
members actually entered, so the first control that failed was a capacity assumption nobody restated as a
limit. The recalculation loop had no bound, so the second was a termination argument that existed only in
somebody's head. The failover to a build with the validation check removed was a deploy-time decision taken
under incident pressure, which is the worst moment to change what a system checks and the moment it most
often happens. The acknowledged cancels that filled anyway were a state machine contradicting a published
acknowledgement, and the decision not to tell members was made by nobody in particular.

The last row is the one to design against. A reconciliation compared two views, found they disagreed, and
correctly refused to publish confirmations. That is exactly what a correctness check is supposed to do, and
the outcome was still a two-hour blackout, because the check had one action available to it and that action
was to withhold output. A comparison that can only suppress is an availability failure wearing a correctness
check's clothes. Give every such check a second action, which is to escalate to a named owner with the two
numbers attached, and a stated maximum time it may sit in the suppressed state before somebody has to decide.
