# Timing fairness under US Regulation NMS (US-specific)

> **Provenance**
> provider: US Securities and Exchange Commission · surface: Regulation NMS Rule 603(a) as codified, and the 2012 settled administrative proceeding that applied it to a venue's fan-out architecture
> version: 17 CFR 242.603 as in force on 2026-08-25; SEC Release No. 34-67857, September 14, 2012, Administrative Proceeding File No. 3-15023
> verified_at: 2026-08-25
> sources: https://www.sec.gov/files/litigation/admin/2012/34-67857.pdf
> · https://www.sec.gov/news/press-release/2012-2012-189htm
> · https://www.ecfr.gov/current/title-17/section-242.603
> pinned: the order was downloaded and read as text on 2026-08-25; the eCFR page is the current edition served that day.
> verified: every sentence in quotation marks in this file was read in the order on 2026-08-25. The sentence describing what the rule prohibits is the order's own, in section III. The four findings quoted are the order's: the internal architecture giving the depth-of-book feed a faster path than the path to the Network Processor, the second feed structured to operate independently of that system, the software issue that delayed the Network Processor path during periods of high trading volume in 2010, and the failure to retain the files carrying the transmission times. The disparity range and the 5,000,000 dollar civil money penalty are in the order; "first-ever SEC financial penalty against an exchange" is the SEC's own headline on the press release. The current text of 603(a)(1) and (a)(2), with the "fair and reasonable" and "not unreasonably discriminatory" standards, was read on eCFR.
> corrected: one quotation was reshaped to the order's wording, which is that NYSE structured the other proprietary feed to operate independently of the system that sent data to the Network Processor.
> unverified: the codified rule text does not itself state the timing prohibition; that reading is the Commission's, given in the Regulation NMS adopting release at 70 Fed. Reg. 37,496, 37,567 and 37,569, and quoted in the order. The adopting release itself was not opened here, so it is cited at second hand. Nothing was checked about any later enforcement action, about the market data infrastructure amendments now restructuring paragraph (b), or about how another jurisdiction's rules compare. The publisher rules and the record shape proposed below are this repository's engineering advice, not the SEC's text.
> revalidate_when: 17 CFR 242.603 is amended again; the Commission brings a newer proprietary-versus-consolidated timing action, or issues guidance that supersedes the 2012 findings; the consolidated-feed architecture the rule assumes is replaced by the competing-consolidator model; your venue comes under a regulator other than the SEC.

**This file states a United States requirement and applies only to venues subject to it.** Regulation NMS
governs exchanges and other trading centres in the US national market system for equities and options. Most
venues in the world are not subject to it, and nothing in this file should be read as a universal property of
market-data architecture. The generic architectural rule, that publication is deterministic and the fan-out
happens once after the sequence is assigned, is in the skill body and holds everywhere. Whether one
destination is legally required to be no later than another is a jurisdictional question, and this is the
answer for one jurisdiction.

If you are building outside the US national market system, read this as a worked example of what a regulator
can require and of how an architecture breaches a timing rule without anyone intending to, then apply your own
venue's rules and your own regulator's requirements.

## Contents

- What Rule 603(a) requires, and of whom
- The NYSE enforcement action, and the four code shapes that produced it
- Why the breach was architectural rather than intentional
- The evidentiary failure, charged separately from the timing failure
- Publisher rules that follow, for venues in scope
- What a venue outside the US should take from this and what it should not

---

## What Rule 603(a) requires

Reg NMS Rule 603(a) "prohibits an exchange from releasing data relating to quotes and trades to its customers
through proprietary feeds before it sends its quotes and trade reports for inclusion in the consolidated
feeds."

That sentence is the Commission's, from the order discussed below, and it is worth knowing that it is a
reading rather than a transcription. The codified text at 17 CFR 242.603(a) says only that market data is
distributed on terms that are "fair and reasonable" and "not unreasonably discriminatory"; the timing
prohibition comes from the Regulation NMS adopting release, which states that "independently distributed data
could not be made available on a more timely basis than core data is made available to a Network processor."
A venue arguing about the boundary argues about that release, not about the two standards.

Two structural facts about the US market make this rule necessary and give it its shape. Consolidated quote
and trade data is distributed through processors operated on behalf of the whole market, and exchanges also
sell their own proprietary depth-of-book feeds carrying the same facts and more. A venue that reaches its own
paying customers first, on the same facts, has sold an advantage created by its position rather than by any
service. The rule constrains the ordering between those two paths, and it constrains it as an obligation on
the venue's own architecture, not as a best-efforts intention.

## The NYSE enforcement action

SEC Release 34-67857, dated 2012-09-14, penalised NYSE and NYSE Euronext 5M USD, the first-ever SEC financial
penalty against an exchange, for breaching the rule. The findings map directly onto code shapes a reviewer can
recognise without any regulatory knowledge:

| Finding | The code shape that produces it |
|---|---|
| "NYSE's internal architecture gave its real-time depth-of-book proprietary feed a path to customers that was faster than the path used to send quotes to the Network Processor" | Two sinks fed from separate queues after a fan-out, with different serialisation cost |
| "NYSE structured the other proprietary feed to operate independently of the system that sent data to the Network Processor" | A sink that does not inherit the shared path's delays, so it wins whenever that path is slow |
| A load-dependent software defect delayed the consolidated path under high volume | Backpressure applied to one sink only, and it was the one that must not be last |
| NYSE could not prove compliance: it had not retained the transmission-timing files | No durable per-message record of when each sink was handed the bytes |

Disparities ranged "from single-digit milliseconds to, on occasion, multiple seconds."

## Why it was architectural

No finding required intent, a decision, or a person choosing to advantage one customer. The breach was a
property of a fan-out whose two branches had different queueing and different serialisation cost, and it
appeared under load, because that is when the difference between two branch costs stops being negligible.

That is what makes it a reviewable defect rather than a compliance topic. A fan-out to two sinks with
different queueing or serialisation is visible in the code, independent of whether either sink is currently
slow, and it does not become a problem gradually: it is latent at low volume and material at high volume,
which is exactly the volume at which the data is worth the most.

The load-dependent defect deserves separate attention. Backpressure applied to one branch only is the same
class of design error as blocking a publisher on a slow consumer, and it produces the same result: a condition
in one component silently reorders the outputs of another. If any branch of your fan-out can be slowed by
something downstream of it, the ordering guarantee across branches does not exist under load.

## The evidentiary failure

The inability to prove compliance was charged as its own finding. That is the part most likely to be missed
in an architecture review, because the system can be correct and still fail here: without a durable per-message
record of when each sink was handed the bytes, a venue cannot demonstrate the ordering it maintains, and
cannot investigate a complaint that it did not.

Retention is therefore a design requirement rather than an operational preference. The record has to be
produced at the fan-out point, at the same instant as the hand-off, and kept for as long as a question about
it can be asked.

## Publisher rules, for venues in scope

- **Fan out from one point, after the sequence number is assigned.** One serialised artefact, one hand-off
  point, every sink downstream of it.
- **Timestamp each sink hand-off at that point**, from one clock, and write the record durably.
- **Make the consolidated sink no later than any proprietary sink by construction.** Same buffer, same
  ordering, and where either must be first, it is the consolidated one. A guarantee that depends on both
  paths being fast is not a guarantee.
- **Retain the transmission-timing records**, because the evidentiary failure was charged separately from the
  timing failure and can be breached on its own.
- **Treat a divergent fan-out as a defect on sight** in code review, without waiting for a measurement.

## The transmission-timing record

The record that answers the evidentiary question is small and has to be produced at the fan-out point rather
than reconstructed later from logs:

| Field | Why it is needed |
|---|---|
| Sequence number assigned to the artefact | Joins the record to the exact bytes published |
| Sink identifier | Distinguishes the consolidated path from each proprietary path |
| Hand-off timestamp, one clock for all sinks | Timestamps from different clocks cannot be compared, so a per-sink clock answers nothing |
| Byte count or artefact digest | Proves the sinks were handed the same content, not only handed something at the same time |

Two failure modes to check for. A record written by each sink after it has serialised its own copy measures a
different event on each branch, and the difference between two different events is not a disparity
measurement. And a sampled record, kept for one message in N, cannot answer a question about a specific quote,
which is the only question anyone ever asks.

Test the ordering property the way you would test any other invariant. Drive the fan-out with a synthetic
slowdown on one branch and assert that the ordering guarantee still holds, and that the timing record shows it
holding. An ordering guarantee only ever tested with all branches healthy is untested.

## What to take from this outside the US

Take the architectural property and the review heuristic: publish once, fan out once, timestamp the hand-offs,
and treat unequal branches as a defect. Take the evidence requirement as well, because being unable to
reconstruct what you sent and when is a problem in any jurisdiction, whether or not a regulator names it.

Do not take the specific ordering obligation. There may be no consolidated feed in your market, no processor
to be no later than, and no rule constraining which of your own destinations receives data first. Substitute
your own venue's rulebook and your regulator's requirements, and state the resulting obligation in your own
specification so that the architecture can be reviewed against something written down.
