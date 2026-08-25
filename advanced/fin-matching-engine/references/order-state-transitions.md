# Legal transitions, and every refusal made explicit

> **Provenance**
> provider: Nasdaq, US equities · surface: OUCH 5.0 order entry, the cancel and replace interleavings ·
> version: OUCH 5.0, "Updated October, 2025", revision 1.05 dated 7 October 2025
> verified_at: 2026-08-25
> sources: https://www.nasdaqtrader.com/content/technicalsupport/specifications/TradingProducts/OUCH5.0.pdf ·
> https://www.sec.gov/litigation/admin/2013/34-69655.pdf
> verified: the OUCH 5.0 PDF was fetched and its text extracted and read directly on 2026-08-25, by two
> independent passes: "There is no “too late to cancel” message since by the time you received it, you would
> already have gotten the execution. Superfluous Cancel Order Messages are silently ignored"; and the
> new-order, replace and execution interleaving reproduced below.
> unverified: SEC Rel. 34-69655 ¶24 fn 4 was not re-read in this pass and is carried on its inline
> attribution.
> revalidate_when: Nasdaq publishes an OUCH revision touching cancel, replace or the acknowledgement model, or
> before the silence convention is read as any venue's answer other than Nasdaq's.

The engine owns the state machine, so which pairs are legal is a decision it makes rather than one it reads
off a message. What is left after that is what a refusal puts on the wire, and whether an acknowledgement you
have already sent can be contradicted by a later execution.

## The transitions this engine owns

Cancel/fill races, idempotent cancel, the answer to a cancel of a terminal order, ownership checks and time
priority are **obligations of this engine**. Some are rulebook entries: Nasdaq OUCH 5.0 answers a cancel
arriving after the fill with silence, *"Superfluous Cancel Order Messages are silently ignored."* That
transition is still enumerated and still refused internally; silence decides only what leaves the process.

## Only enumerated transitions are legal, and every refusal is explicit

Specialises *authority* seen from the authority's side, and *concurrency on authoritative state*, because the
re-read and the act sit in one transaction. Enumerate the legal `(state, event)` pairs and refuse everything
else with a typed error the engine records and counts; never let a pair fall through unhandled. What that
refusal puts on the wire is the protocol's choice, and silence is a legitimate one. Never take an inbound
message's assertion about state as the state: re-read the entity from the committed store inside the
transaction that acts on it. An acknowledgement is itself a transition: once you have told a participant an
order is cancelled, executing it afterwards contradicts a published state. Pre-market, auction, halt and
continuous are states too, not flags read opportunistically. NASDAQ, SEC Rel. 34-69655 ¶24 fn 4: cancels
*"acknowledged"* *"immediately upon submission"* were filled anyway.
