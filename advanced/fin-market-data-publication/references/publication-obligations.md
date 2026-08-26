# One sequencer, one fan-out, and the record that proves what left when

> **Provenance**
> provider: US Securities and Exchange Commission, cited for a settled administrative proceeding whose
> findings are code shapes
> surface: the 2012 proceeding against a venue's fan-out architecture, read for the four findings and for the
> separate charge that the venue could not prove the ordering it maintained
> version: SEC Release No. 34-67857, September 14, 2012, Administrative Proceeding File No. 3-15023
> verified_at: 2026-08-25
> sources: https://www.sec.gov/files/litigation/admin/2012/34-67857.pdf
> · https://www.sec.gov/news/press-release/2012-2012-189htm
> pinned: the order was downloaded and read as text on 2026-08-25.
> verified: every sentence in quotation marks below was read in the order that day. The four findings quoted
> are the order's: the internal architecture giving the depth-of-book feed a faster path than the path to the
> Network Processor, the second feed structured to operate independently of that system, the software issue
> that delayed the Network Processor path during periods of high trading volume in 2010, and the failure to
> retain the files carrying the transmission times. The disparity range and the 5,000,000 dollar civil money
> penalty are in the order; "first-ever SEC financial penalty against an exchange" is the SEC's own headline
> on the press release.
> corrected: one quotation was reshaped to the order's wording, which is that NYSE structured the other
> proprietary feed to operate independently of the system that sent data to the Network Processor.
> unverified: the publisher rules and the record shape proposed below are this repository's engineering
> advice, not the SEC's text. The jurisdictional obligation these findings arose under is stated in this
> skill's Regulation NMS reference, not here.
> revalidate_when: the Commission brings a newer proprietary-versus-consolidated timing action, or issues
> guidance that supersedes the 2012 findings.

Determinism is what makes two paths one feed and a recovery store an answer rather than a second opinion.
This file is the architectural property, the enforcement action whose findings read as code shapes, the
reason an unequal fan-out is a defect on sight rather than a latency question, and the per-message record
that is the only evidence a later comparison can read. It is jurisdiction-independent: whether one
destination is legally required to be no later than another is a separate question with a separate answer.

## Contents

- The obligation: byte-identical paths, one assignment point, one fan-out
- The NYSE enforcement action, and the four code shapes that produced it
- Why the breach was architectural rather than intentional
- The evidentiary failure, charged separately from the timing failure
- Publisher rules that follow
- The transmission-timing record, and the two ways it is written uselessly
- What a venue outside the US should take from this and what it should not

---

## The obligation

**Two paths carrying one sequence space are byte-identical per sequence number.**
Arbitration is by sequence number alone: the consumer keeps whichever copy arrived first and discards the
other unread. If the paths ever differ in content under the same sequence, through different conflation,
different batching boundaries or a field populated on one path only, the consumer's book becomes a function of
network jitter. Generate once, at one sequencer, and fan out bytes. No message may depend on being seen
exactly once in a way its sequence cannot identify. A gap is declared after arbitration, never before; say so,
or consumers run single-line and aim a recovery storm at your re-request path at the moment of highest load.

**Publication is deterministic, and the fan-out happens once, after the sequence is assigned.**
The same committed inputs produce the same bytes in the same order on every replica and every replay, or your
two paths are not the same feed and your recovery store does not answer with what you sent. Specialises
*reconciliation* in the form it takes with no external authority: the transmission-timing records below are
the only copy a later comparison can read.

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

That is what makes it a reviewable defect rather than a compliance topic: the unequal branch is visible in
the code, independent of whether either sink is currently slow, and it is latent at low volume and material
at high volume, which is exactly the volume at which the data is worth the most.

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

Take the architectural property, the review heuristic and the evidence requirement: being unable to
reconstruct what you sent and when is a problem in any jurisdiction, whether or not a regulator names it.

Do not take the specific ordering obligation. There may be no consolidated feed in your market, no processor
to be no later than, and no rule constraining which of your own destinations receives data first. Substitute
your own venue's rulebook and your regulator's requirements, and state the resulting obligation in your own
specification so that the architecture can be reviewed against something written down.
