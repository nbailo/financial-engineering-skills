# NASDAQ Trade Through and SHO Through — a configuration edit that starved two compliance gates of their input, then a shared misreading that removed one (2011-10 and 2012-08)

**Domain:** Exchange pre-trade compliance controls | **Loss:** 2,004 trade-throughs initially reported as "no trade-throughs"; 3,820 executions / 1,745,623 shares / 92 securities in the second episode; "over 4,400 short sales that did not comply with the price test" across both; part of NASDAQ's $10,000,000 penalty | **Failure class:** Change, deploy & configuration (with missing control) | **Skill:** advanced/fin-matching-engine

## What happened

Two separate episodes in the same SEC order, neither of which is the Facebook IPO, and neither of
which is used by the research briefs. In October 2011 a technology change altered the configuration
files of the application computing the NBBO; the Trade Through and SHO Through applications — which
cancel or reprice orders violating the price tests — simply stopped operating because their input
had gone away. In August 2012, during an upgrade, an operations employee concluded the SHO Through
application could be removed; the designated second reviewer independently reached the same wrong
conclusion; and the daily configuration test's alert about the missing component was acknowledged,
after which it never fired again. The exchange ran without the control for seven trading days and
learned about it from a member inquiry.

## Root cause, in code terms

**Episode 1 — fail-open on a missing input (October 2011).**

> "However, in implementing this modification, NASDAQ's technology team **erroneously altered the
> configuration files** used by the application that computes the NBBO. As a result, **the Trade
> Through and SHO Through applications ceased operating** since they were not receiving the
> underlying NBBO data that allowed them to cancel or reprice orders that violated the price
> tests …" (¶50)

A control deprived of its reference data did not fail, did not alarm, and did not block. It stopped
having an opinion, and orders that it existed to cancel or reprice flowed through unmodified. The
control's absence produced *no* observable difference to anything except the outcome.

The consequence includes a second-order defect worth stating on its own: the first impact
assessment was materially wrong. NASDAQ reported approximately 595 offending short sales and
**"no trade-throughs"**; "in January 2012, NASDAQ determined that **2,004 trade-throughs had
occurred** on those dates (although NASDAQ did not report this finding to the Commission staff
until September 2012)" (¶51). A control that fails silently also destroys the evidence needed to
size its own failure.

**Episode 2 — four-eyes defeated by a shared misreading, then an alert silenced by acknowledgement
(August 2012).**

> "This employee **misinterpreted the instructions** associated with the upgrade and assumed that
> the SHO Through application was not needed and could be removed … According to NASDAQ's internal
> protocols …, **a second Operations Center employee was responsible for checking the work** of the
> employee who implemented the upgrade. **However, this second employee also misinterpreted the
> upgrade instructions** to mean that the SHO Through application could be removed." (¶52)

Two-person review does not test a misconception; it multiplies it. Both reviewers read the same
ambiguous instruction and reached the same conclusion, and the process recorded that as agreement.

> "On the morning of August 13, 2012, while running the daily configuration test …, Operations
> Center personnel **received a system alert** based on the fact that the SHO Through application
> was no longer part of the system. **Operations Center personnel acknowledged the alert but
> continued with the startup processes** because they also thought the SHO Through application
> could be removed. **As a result, there were no further alerts regarding the missing SHO Through
> application.**" (¶53)

This is the sharpest control-design finding in the order. The alert was correct, it fired at
exactly the right moment, and a single acknowledgement converted a *persistent condition* into a
*handled event*. The condition — a required component is absent — persisted across the seven
trading days from 13 to 21 August 2012 and was discovered only when a member asked a question
(¶54–55).

## The invariant that was violated

```
# a control deprived of its input must fail closed
control.input_unavailable => control.decision == REJECT   AND   alarm(owned, escalating)
NOT: control.input_unavailable => control silently returns no opinion

# a missing-component alert is a condition, not an event
alert_about(missing_component) re-fires on every subsequent start until component.present
acknowledge(alert) MUST NOT suppress re-evaluation

# review
reviewer.verifies(component.present_after_change) by observation
NOT: reviewer.re-reads(the same change instruction the implementer read)
```

## Could an AI coding agent reviewing the diff have caught it?

**Yes, for the code-shaped half — and this is one of the cleaner static findings in the catalogue.**

The **fail-open** is directly visible: a control whose evaluation depends on a reference-data feed,
with a branch (or an absent branch) in which unavailable reference data results in the control
returning "no action" rather than rejecting and alarming. The reviewing question — "what does this
gate do when its input is `None`?" — has exactly two acceptable answers, and "nothing" is not one
of them. The October 2011 change itself was a *configuration* edit, but the fail-open behaviour it
exposed lives in the control's source and is reviewable at any time, not only on the day the config
changed.

The **acknowledgement-suppresses-forever** pattern is likewise a code finding: an alert whose
`acknowledged` flag gates re-evaluation of an ongoing condition, rather than gating notification
volume. An agent seeing `if alert.acknowledged: return` around a *presence check* should flag it.

The **startup component-presence assertion** is the missing positive control: there is no test at
start-up that asserts the expected set of compliance applications is present and receiving data,
and refuses to start otherwise.

What an agent could not see: two humans reading an ambiguous upgrade instruction the same wrong way.
It can, however, flag that the review protocol as encoded requires re-reading the instruction rather
than observing the post-change system state.

## The rule

> **MUST — A control deprived of its input must fail closed and alarm.** Never let a configuration
> change, a missing feed, or an unavailable reference price turn a gate into a no-op. Where a
> control cannot evaluate, it rejects.

> **MUST — Assert at start-up that every required control component is present and receiving its
> input, and refuse to start the value path otherwise.**

> **SHOULD — An operator acknowledgement of an alert about a *missing component* must not suppress
> it.** The alert must re-fire on every subsequent start until the component returns.

> **SHOULD — Two-person review does not protect against a shared misreading of the change
> instructions.** Where a change can remove a component, require the reviewer to verify the
> component's presence after the change by observation, not by re-reading the instruction.

> **MUST — When a control is found to have been inoperative, the impact assessment must be
> reconstructed from data, not estimated.** NASDAQ's first count was "no trade-throughs"; the real
> figure was 2,004.

## Sources

- **SEC Admin. Proc. 34-69655, *In the Matter of The NASDAQ Stock Market LLC and NASDAQ Execution
  Services LLC*, 29 May 2013** —
  <https://www.sec.gov/files/litigation/admin/2013/34-69655.pdf>. **Primary.** ¶50 (the
  configuration files erroneously altered; Trade Through and SHO Through ceased operating for want
  of NBBO data), ¶51 (~595 short sales and "no trade-throughs" reported; 2,004 trade-throughs
  determined in January 2012, not reported to Commission staff until September 2012), ¶52 (the
  implementer and the designated checker both misinterpreted the upgrade instructions), ¶53 (the
  alert acknowledged; "there were no further alerts"), ¶55 (13–21 August 2012; 3,820 executions /
  1,745,623 shares / 92 securities; discovered by member inquiry), ¶59 ("over 4,400 short sales
  that did not comply with the price test").
- **Note.** These two episodes are in the same order as the Facebook IPO cross and carry the
  $10,000,000 penalty jointly with it; the order does not apportion the penalty between them.
