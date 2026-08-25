# Two gates, not one, and who is allowed to clear a trip

> **Provenance**
> provider: US SEC, the UK FCA and Compound Labs · surface: the administrative order against Goldman Sachs of
> 20 August 2013, the Final Notice to Citigroup Global Markets of 17 May 2024, and Compound governance
> Proposal 62 · version: SEC Rel. 34-70212; FCA Final Notice, 17 May 2024; Compound Proposal 62, September
> 2021
> verified_at: not established
> sources: https://www.sec.gov/litigation/admin/2013/34-70212.pdf ·
> https://www.fca.org.uk/publication/final-notices/citigroup-global-markets-limited-2024.pdf ·
> https://compound.finance/governance/proposals/62
> verified: nothing here was re-read from a primary source in this pass.
> unverified: Goldman ¶8 and ¶31 on control personnel repeatedly lifting circuit-breaker blocks, FCA ¶4.28 to
> ¶4.31 on the 711 warnings dismissed by one button, and the Compound Proposal 62 account of a seven-day
> governance path with no admin control, are all carried on their inline attributions from an earlier pass.
> revalidate_when: before any paragraph number here is repeated outside this repository, or if any of the
> three documents moves.

Risk-increasing and risk-reducing paths are gated by different flags, because a single boolean covering both
turns every incident into a choice between accepting new risk and being unable to reduce the risk already
held. Which reducing paths stay callable is decided per component. How a trip is cleared is decided
independently of whatever tripped it.

## Separate gates

Risk-increasing and risk-reducing paths are gated by **different** flags. A single boolean covering both
turns every incident into a choice between accepting new risk and being unable to reduce the risk already
held.

**Which risk-reducing paths must stay callable is decided per component, not universally.** The matching
engine's list is short, and it is the only one this file requires: while the increasing gate is shut,
`cancel` and a replace that only reduces quantity or moves a price away from the market stay callable for
every resting order, and a test exercises them in the shut state.

`close`, `flatten`, `settle` and `reconcile` belong to whatever component holds the positions, the cash or
the clearing relationship. Requiring a matcher that does not clear to expose them adds surface that then has
to be correct under exactly the conditions the gate exists for, and a `flatten` inside a matcher is a matcher
that can create exposure while its increasing gate is shut. Name the component that owns each risk-reducing
path, and gate it there, with the test in that component.

The design worth copying is a market-wide pause that still runs its closing transaction and still
disseminates a quote marked unexecutable: stopping new exposure is separable from stopping the machinery
that resolves existing exposure. Where an invariant can be **momentarily** false during a named intermediate
state, give the check a bounded self-heal window before it escalates, because a check that fires on a
legitimately intermediate state is an availability bug wearing a correctness check's clothes.

## Automatic to trip, independent and auditable to reset

**Exercise latency is measured in the same units as the loss.** A pause that takes longer to exercise than
the loss takes to accrue is documentation. Compound's Proposal 62, September 2021, is the extreme case: a
distribution bug let users claim more than they had accrued, there were "no admin controls or community
tools to disable the COMP distribution", and any change had to pass a seven-day governance process. Every
path that distributes, mints, credits or transfers value needs a pause exercisable faster than that path can
move value.

**Automatic to trip.** Rate-based breakers that halt traffic above a rate are cheap and they work. What
fails is the reset. Goldman ¶8 and ¶31: control personnel "repeatedly lifted the circuit breakers blocks"
during the incident, while still investigating the cause, and the employee who lifted one of them had
authored the policy he was breaching. The property that follows is not a role or a form. It is that the
authority to clear a trip is **independent of the component and the person that tripped it**, that clearing
requires a recorded determination about the cause rather than a bare resume, and that the record is
auditable afterwards. How that authority is constituted is the operator's decision, not this file's.

**A warning that can be bulk-overridden is not a control.** FCA ¶4.28 to ¶4.31: a single dialog presented
711 warning messages with only the first 18 lines visible, and two buttons, one of which dismissed all of
them. One action clearing N warnings has an effective threshold of infinity. If a warning is worth raising,
it is either blocking or it is per-item acknowledgeable.
