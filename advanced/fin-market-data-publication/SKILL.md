---
name: fin-market-data-publication
description: >-
  BETA. Publishing a market-data feed you ORIGINATE: sequencing and gap detection, session
  identity, resets, snapshot and incremental joins, A/B arbitration, recovery that terminates,
  conflation, book and volume filters, timestamps, deterministic publication. Use when your code
  emits the feed other systems build a book, volume or index from. NOT for someone else's feed:
  use fin-exchange-integration.
license: MIT
---

# You publish the record

**BETA.** Opt-in, not installed with the six, still being hardened.

**For the team that ORIGINATES a feed.** You emit the canonical public or downstream record. **Not for
anyone consuming one.** A bot on a venue's websocket, a local book kept from someone else's updates, a
snapshot joined on their sequence numbers: that is consumption, and fin-exchange-integration owns it.

Every venue-specific answer here is a published rule shown as an example, never the answer for yours.
No venue's internal architecture, risk limits or unpublished mechanics belong in this skill. An
invariant in italics is stated in full in `fin-money-core`.

## When to use

Your process emits a stream other systems treat as the state of a market: quotes, book updates, trade
prints, session statistics, an index or a settlement figure. The test is authorship, not transport: a
number leaves your perimeter and nobody else publishes the same fact. Hints, never the definition: a
sequence number you assign, a session identity you mint, `snapshot`, `gap_fill`, `recovery`, a fan-out,
conflation, a `top_of_book` aggregate that leaves the process.

## When not to

- **The code consumes a feed instead of originating one: `fin-exchange-integration`.** A websocket
  subscriber, a local book from someone else's updates, a join on `U`, `u` or a `lastUpdateId` they
  assigned. Sequencing work on somebody else's numbering is still consumption.
- FIX order-entry session behaviour, rather than a feed carried on one: `fin-exchange-integration`.
- Inside the matcher, its order state or its journal: `fin-matching-engine`. The seam is the sequencer.
- How a mark, index or oracle is selected, or how a risk engine consumes one.
- Only amount arithmetic, rounding, operation identity or retry: `fin-money-core`.
- The numbers never leave the process: a backtest, a notebook, a dashboard nobody trades on.

## Workflow

1. Name what you publish, and who is the record for each field.
2. Name the identity that scopes the sequence space, and what mints a new one.
3. Name the counter consumers gap-detect on, and what the others are not valid for.
4. Prove each recovery mechanism ends: truncation, rate limit, retention window.
5. Decide the snapshot join; stamp the as-of in the section that copies the book.
6. Decide conflation and backpressure as correctness questions, and publish the answers.
7. Put the assertions on the emit path, with a freeze scope per breach.
8. Load only the references this change triggers; implement each control with its test.

## Invariants

- **A feed is a contract about identity, order and completeness.**
- **Publish the counter gap detection runs on, with its arithmetic.** Specialises *operation identity*.
- **A liveness signal is an obligation you publish; its encoding is the protocol's.**
- **A reset is a message you send, never a fact inferred from a counter going backwards.**
- **A snapshot is the book as of a stated point, stamped with the copy.**
- **Two paths carrying one sequence space are byte-identical per sequence number.**
- **Recovery must terminate, and truncation is where it stops terminating.** Specialises *durable dedupe*.
- **Conflation is legitimate only where it is equivalent, bounded and recoverable:** a consumer can
  tell exactly which raw updates a conflated message accounts for.
- **The raw print is one fact; eligibility is several filters over it, in several documents.**
- **Four questions, four measurements, and one timestamp cannot answer another's.**
- **The aggregate you publish is checked on the path that publishes it,** and a corrupted or saturated
  one is withheld or marked, never published as authoritative. Specialises *rounding and conservation*.
- **Publication is deterministic, and the fan-out happens once, after the sequence is assigned.**
  Specialises *reconciliation*.
- **A statistic you publish is a method, and the method is part of the feed.** Specialises *authority*.

## References

A literal below appears in the code, the repo or the task text: load that reference before assessing it.

- [feed-spec](references/feed-spec.md): an explicit design, review or ship-readiness task, and only then: the specification consumers read
- [feed-versioning](references/feed-versioning.md): a live feed gains a field, a published number is tightened, a constant gains data
- [sequencing-obligations](references/sequencing-obligations.md): a session identity you mint, the gap-detection counter, a timestamp
- [publication-obligations](references/publication-obligations.md): one event fans out to several sinks, no record of what each sink got
- [prints-and-eligibility](references/prints-and-eligibility.md): `ITCH`, `Printable`, session volume, fee tier, index input
- [conflation-legality](references/conflation-legality.md): `conflate`, `coalesce`, a last-value cache, a delta field on a coalesced stream
- [conflation-mechanics](references/conflation-mechanics.md): `U` / `u` / `pu`, a slow subscriber, a bounded outbound queue
- [emit-checks](references/emit-checks.md): `total_qty`, `debug_assert`, `checked_sub` on a published unsigned aggregate
- [breach-policy](references/breach-policy.md): a freeze scope, a clamp to zero, `best_bid` meets `best_ask`
- [moldudp64](references/moldudp64.md): `MoldUDP64`, `SoupBinTCP`, `MessageCount`, `0xFFFF`, a ten byte session id
- [moldudp64-rerequest](references/moldudp64-rerequest.md): a re-request server answers a range, a retransmission on the live socket
- [cme-recovery](references/cme-recovery.md): a Market Recovery snapshot loop, `LastMsgSeqNumProcessed`, tag `369`, natural refresh
- [cme-sequencing](references/cme-sequencing.md): `RptSeq`, `35=X`, `269=J`, a channel reset, an A and B line
- [fix-session](references/fix-session.md): `MsgSeqNum`, `ResendRequest`, `SequenceReset`, `GapFillFlag` on a feed you publish
- [fix-resend-flags](references/fix-resend-flags.md): `ResetSeqNumFlag`, `PossDupFlag`, `OrigSendingTime`, `PossResend`
- [reg-nms-603a](references/reg-nms-603a.md): a US national market system venue, `Reg NMS`, `603(a)`, `SIP`

## Output

Open with `authority: SELF · exposure: record`, then one entry per real finding:

```
FINDING   <the wrong economic outcome, concretely>
WHY       <the mechanism that produces it>
EVIDENCE  <file:line>
FIX       <the change that closes it>
TEST      <the property to assert>
```

A claimed control points at executable code and, where the risk needs it, a named test. A comment, a
TODO or an uncalled helper describing one is the same defect as the missing control, reported as
`UNRESOLVED: <control> (<why>)`. Add `VERDICT SHIP | NO-SHIP: <control>` on a review or ship decision.

**On a design, review or ship-readiness task, emit a FEED CONTRACT block,** because nobody outside can
tell you that you are wrong: its slots and template are in `references/feed-spec.md`. Fill only the
slots the change touches; one it touches and cannot fill **is the finding**. Any other task is findings
alone.
