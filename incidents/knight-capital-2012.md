# Knight Capital SMARS — a loop termination counter moved out of the loop, re-armed by a repurposed flag on one of eight servers (2012-08-01)

**Domain:** US equities order routing | **Loss:** $460,000,000 (SEC 34-70694 ¶17); $12,000,000 penalty | **Failure class:** Change, deploy & configuration (with missing conservation and state divergence) | **Skill:** fin-money-core

## What happened

Between 09:30 and approximately 10:15 ET, Knight's SMARS order router processed 212 incoming
parent orders and emitted "over 4 million executions in 154 stocks for more than 397 million
shares" (¶1, ¶17). Knight was left roughly $3.5bn net long in 80 stocks and $3.15bn net short in
74. The realised loss was $460 million and the firm did not survive as an independent company.
Knight had begun deploying new code for NYSE's Retail Liquidity Program on 27 July, in stages
across eight SMARS servers. Attempts to mitigate during the event made it worse. The SEC found
violations of Rule 15c3-5(b), (c)(1)(i), (c)(1)(ii), (e)(1) and (e)(2), plus Reg SHO Rules 200(g)
and 203(b).

## Root cause, in code terms

Three defects composed, none of which was introduced on the day.

**1. A termination condition relocated out of the generator that used it (2005).** Power Peg, a
strategy discontinued in 2003, generated child orders in a loop and stopped when a *cumulative
quantity* counter showed the parent order was filled. In 2005 Knight "moved the tracking of
cumulative shares function in the Power Peg code to an earlier point in the SMARS code sequence"
and "did not retest the Power Peg code after moving the cumulative quantity function to determine
whether Power Peg would still function correctly if called" (¶14). The Commission's own
counterfactual (¶41) is that Knight had "**inadvertently disabled the cumulative quantity
functionality** in the Power Peg code". Nothing failed for seven years, because Power Peg was
dormant.

**2. A flag repurposed while its old consumer was still deployed (2012).** The new RLP code
"repurposed a flag that was formerly used to activate the Power Peg code" (¶13). The Power Peg
code itself "**remained present and callable**". The intention was to delete Power Peg so that the
flag would engage RLP instead.

**3. A partial deploy made the two meanings coexist.** "One of Knight's technicians did not copy
the new code to one of the eight SMARS computer servers. Knight did not have a second technician
review this deployment … Knight had no written procedures that required such a review" (¶15). On
that eighth server, `flag = yes` still meant Power Peg.

The state that would have stopped it existed elsewhere in the firm and could not be read from where
it was needed — the single most code-relevant sentence in the order:

> "Because the cumulative quantity function had been moved, this server continuously sent child
> orders, in rapid sequence, for each incoming parent order without regard to the number of share
> executions Knight had already received from trading centers. **Although one part of Knight's
> order handling system recognized that the parent orders had been filled, this information was not
> communicated to SMARS.**" (¶16)

Four controls that should have contained it did not exist or did not fire:

- **No in/out reconciliation at the router.** "Knight did not have sufficient controls to monitor
  the output from SMARS, such as **a control to compare orders leaving SMARS with those that
  entered it.** Knight also did not have procedures in place to halt SMARS's operations in
  response to its own aberrant activity." (¶21) 212 in, millions out.
- **The price collar did not cover the code path in use.** A 9.5% collar against the NBB/NBO
  existed, but "it did not apply to orders — such as the 212 orders described above — that Knight
  received before the market open and intended to send to participate in the opening auction" (¶21).
- **The suspense account was mixed-purpose and unlinked.** The "33 Account" temporarily held
  multiple position types, including fills that could not be matched to an unfilled parent
  quantity. It had a $2 million gross limit that Knight "did not link … to any automated controls"
  (¶23), and because it pooled sources, "Knight personnel could not quickly determine the nature or
  source of the positions accumulating" in it (¶24).
- **The risk screen was post-execution and degraded under load.** PMON was "a **post-execution**
  position monitoring system", not linked to order entry, generating no automated alerts, not
  displaying the limits themselves, and it "experienced delays during high volume events, **such as
  the one experienced on August 1**, resulting in reports that were inaccurate" (¶25).

Two further mechanisms are worth stating exactly because summaries garble them.

**The rollback widened the blast radius.** "Knight uninstalled the new RLP code from the seven
servers where it had been deployed correctly. **This action worsened the problem, causing
additional incoming parent orders to activate the Power Peg code that was present on those
servers**" (¶27). Reverting the new code restored the old, defective path on every node.

**The 97 warning emails came from a different order flow.** From about 08:01, an internal system
generated automated "BNET reject" e-mails identifying an error described as **"Power Peg disabled"**;
97 were sent before the 09:30 open, and "Knight did not design these types of messages to be system
alerts, and Knight personnel generally did not review them" (¶19). Footnote 6 records that the
orders producing those e-mails "were **distinct from** the 212 incoming parent orders". The 212
produced no warning at all — the canary was a different code path exercising the same defect.

## The invariant that was violated

```
# at the router, per parent order
sum(child_order_qty(parent)) <= parent.qty
count(orders_out) / count(orders_in) <= K          # K a configured, small constant

# on the terminating path
generator.terminates_on(state) => state is readable by generator   # not by a sibling component

# across the fleet
forall nodes n1, n2: semantics_of(flag, n1) == semantics_of(flag, n2)

# on the exposure path
aggregate_exposure(firm) <= capital_threshold      # measured on orders ENTERED, pre-trade
```

## Could an AI coding agent reviewing the diff have caught it?

**Partly — and it would have caught the two diffs that mattered.**

The **2005 diff** is a textbook review finding: a counter is moved to an earlier point in a
sequence and reused by another application. The signal in the diff is that `cumulativeQuantity` (or
its equivalent) acquires a *second* reader while an existing reader uses it as a loop termination
condition. The reviewing question — "who else reads this counter, and does each of them still
terminate?" — is mechanical, and it is exactly the kind of refactor an agent performs casually.

The **2012 diff** is statically detectable: the flag being set to arm RLP has a second consumer in
the tree, and that consumer is a dead-but-callable strategy. Grep the flag, find two consumers,
observe that one is supposed to be deleted and is not. "Dead code that is still reachable from
production input" is a finding, not a style note.

What no agent could see is the **deployment**: that the build reached seven of eight servers. The
diff was correct; the fleet was not. That gap — correctness as a property of the deployed fleet
rather than of the source — is why change-safety *evidence* is a separate discipline from review.

An agent can also read the surrounding code for the missing controls: a router with no comparison
of outbound to inbound instruction counts, a price collar whose branch excludes the pre-open path,
and a monitoring tool that is only consulted after execution.

## The rule

> **MUST — Never repurpose an existing flag, enum value, or wire field to mean something new while
> any deployed consumer of the old meaning still exists.** Introduce a new value; delete the old
> branch in a separate, later change.

> **MUST — When moving, extracting, or hoisting a counter or accumulator that another component
> uses as a loop-termination or completion condition, identify every reader of that state and prove
> each still terminates.** If a terminating loop depends on state owned by another component, it
> must read that state on the terminating path, and a test must fail if the state becomes
> unreachable.

> **MUST — At any boundary where one inbound instruction can generate many outbound instructions,
> assert a bound on the fan-out (outbound count, notional, and shares) per inbound instruction, and
> halt — not log — when it is exceeded.**

> **MUST — Never implement rollback of a partial deployment by reverting the new code on the
> healthy nodes.** Roll forward, or take the divergent node out of rotation.

> **MUST — Route error, reject and exception messages from a money path into an alerting channel
> with a named owner and a threshold.** A message stream written only to email or a log is not a
> control.

> **MUST — Error and suspense accounts must be single-purpose and linked to automated limits that
> block new order entry when breached.** Never pool unmatched fills, error positions and live
> positions in one account.

## Sources

- **SEC Admin. Proc. 34-70694, *In the Matter of Knight Capital Americas LLC*, 16 Oct 2013** —
  <https://www.sec.gov/files/litigation/admin/2013/34-70694.pdf> (mirror:
  <http://www.headlandstech.jp/static/file/34-70694.pdf>). **Primary.** Establishes every quoted
  passage above: ¶13 (dead code "present and callable", repurposed flag), ¶14 (the moved counter,
  no retest), ¶15 (7 of 8, no second technician, no written procedure), ¶16 (fill state not
  communicated to SMARS), ¶17 (212 / 4M+ executions / 154 stocks / 397M+ shares / $460M), ¶19 and
  fn 6 (97 "Power Peg disabled" e-mails, distinct from the 212), ¶21 (no orders-out vs orders-in
  comparison; no halt procedure; the 9.5% collar not applying to opening-auction orders), ¶23–24
  (the 33 Account), ¶25 (PMON), ¶27 (the rollback that worsened it), ¶33 (the separate October 2011
  LMM test-data incident, "nearly $7.5 million"), ¶41 (the retest counterfactual), ¶44 (narrow
  remediation), ¶49 (violations), §IV.C ($12,000,000).
- **SEC Rel. 34-63241, *Risk Management Controls for Brokers or Dealers with Market Access* (Rule
  15c3-5 adopting release), 3 Nov 2010; 75 Fed. Reg. 69792** —
  <https://www.sec.gov/rules/final/2010/34-63241.pdf>. **Primary.** Establishes that pre-trade,
  automated controls are mandatory and that "the broker-dealer **must assess compliance with the
  applicable threshold on the basis of exposure from orders entered** … **rather than relying on a
  post-execution, after-the-fact determination**" — which is precisely what PMON was.
