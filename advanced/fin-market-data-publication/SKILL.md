---
name: fin-market-data-publication
description: >-
  BETA. Financial correctness for publishing a market-data feed you originate: sequencing and gap
  detection, session identity, resets, snapshot and incremental joins, A/B arbitration, recovery that
  terminates, conflation, book and volume filters, timestamps, deterministic publication. Use when your
  code emits the feed other systems build a book, volume or index from. To consume someone else's
  feed, use fin-exchange-integration.
license: MIT
---

# You publish the record

**BETA.** Opt-in, not installed with the six, still being hardened. A feed you originate is the only account
of what happened. An invariant in italics is stated in full in `fin-money-core`; the consuming half of every
mechanism here is `fin-exchange-integration`.

## When to use

Your process emits a stream other systems treat as the state of a market: quotes, book updates, trade prints,
session statistics, an index or a settlement figure. The test is authorship, not transport: if a number leaves
your perimeter and nobody else publishes the same fact, you are the record for it. Hints, never the
definition: a sequence number you assign, a session identity you mint, `snapshot`, `gap_fill`, `recovery`, a
fan-out, a heartbeat, conflation, a `top_of_book` aggregate that leaves the process.

## When not to

- The code reads somebody else's feed and keeps a local book from it: `fin-exchange-integration`.
- The change is inside the matcher, its order state or its journal: `fin-matching-engine`. The seam is the
  sequencer, and a publisher that repairs engine state is a second system of record for the same fact.
- The change decides how a mark, index or oracle is selected, or how a risk engine consumes one.
- The change is only amount arithmetic, rounding, operation identity or retry: `fin-money-core`.
- The numbers never leave the process: a backtest, a notebook, a dashboard nobody trades on.

## Workflow

1. Name what you publish, and who is the record for each field.
2. Name the identity that scopes the sequence space, and what mints a new one.
3. Name the counter consumers gap-detect on; say what the others are not valid for.
4. Prove each recovery mechanism ends: at truncation, at a rate limit, at the retention window.
5. Decide the snapshot join; stamp the as-of in the critical section that copies the book.
6. Decide conflation and backpressure as correctness questions, and publish the answers.
7. Put the assertions on the emit path, not behind a debug flag, with a freeze scope per breach.
8. Load only the references this change triggers, implement each control with its test, and fill the feed
   contract; a slot you cannot fill is one a consumer fills by guessing.

## Invariants

Terse on purpose: the argument and the sources are in the reference the trigger row names.

- **A feed is a contract about identity, order and completeness.**
- **Publish the counter gap detection runs on, with its arithmetic.** Specialises *operation identity*.
- **A liveness signal is an obligation you publish; its encoding is the protocol's.**
- **A reset is a message you send, never a fact inferred from a counter going backwards.**
- **A snapshot is the book as of a stated point, stamped with the copy.**
- **Two paths carrying one sequence space are byte-identical per sequence number.**
- **Recovery must terminate, and truncation is where it stops terminating.** Specialises *durable dedupe*.
- **Conflation is legitimate only where it is equivalent, bounded and recoverable.**
- **The raw print is one fact; eligibility is several filters over it, in several documents.**
- **Four questions, four measurements, and one timestamp cannot answer another's.**
- **The aggregate you publish is checked on the path that publishes it.** Specialises *rounding and
  conservation*.
- **Publication is deterministic, and the fan-out happens once, after the sequence is assigned.** Specialises
  *reconciliation*.
- **A statistic you publish is a method, and the method is part of the feed.** Specialises *authority*.

## References

When a trigger appears, read that file in order, never a summary. The rows are mechanical on purpose.

| file | read it immediately when |
|---|---|
| [feed-spec](references/feed-spec.md) | writing the specification consumers read, or filling the contract block |
| [feed-versioning](references/feed-versioning.md) | a live feed gains a field, a published number is tightened, or a constant gains data |
| [sequencing-obligations](references/sequencing-obligations.md) | you mint a session identity, pick the counter consumers gap-detect on, or stamp a time |
| [publication-obligations](references/publication-obligations.md) | one event fans out to several sinks, or nothing records what each sink got |
| [prints-and-eligibility](references/prints-and-eligibility.md) | a print, session volume, fee tier or index input is computed, or `ITCH` or `Printable` appears |
| [conflation-legality](references/conflation-legality.md) | `conflate`, `coalesce`, a last-value cache, or a delta field on a coalesced stream |
| [conflation-mechanics](references/conflation-mechanics.md) | a coalesced event carries `U` / `u` / `pu`, a slow subscriber is dropped, or a queue is bounded |
| [emit-checks](references/emit-checks.md) | `total_qty`, `debug_assert`, or `checked_sub` on a published unsigned aggregate |
| [breach-policy](references/breach-policy.md) | a failed check picks a freeze scope, a value is clamped to zero, or `best_bid` meets `best_ask` |
| [moldudp64](references/moldudp64.md) | `MoldUDP64`, `SoupBinTCP`, `MessageCount`, `0xFFFF`, or a ten byte session id |
| [moldudp64-rerequest](references/moldudp64-rerequest.md) | a re-request server answers a range, or a retransmission arrives on the live socket |
| [cme-recovery](references/cme-recovery.md) | a Market Recovery snapshot loop, `LastMsgSeqNumProcessed`, tag `369`, or a natural refresh |
| [cme-sequencing](references/cme-sequencing.md) | `RptSeq`, `MDIncrementalRefresh`, `35=X`, `269=J`, a channel reset, or an A and B line |
| [fix-session](references/fix-session.md) | `MsgSeqNum`, `ResendRequest`, `SequenceReset` or `GapFillFlag` |
| [fix-resend-flags](references/fix-resend-flags.md) | `ResetSeqNumFlag`, `PossDupFlag`, `OrigSendingTime`, `PossResend` or a FIXP `UUID` |
| [reg-nms-603a](references/reg-nms-603a.md) | a US national market system venue, `Reg NMS`, `603(a)`, `SIP`, or a consolidated feed |

## Output

Open an economic change with `authority: SELF · exposure: record`, then one entry per real finding:

```
FINDING   <the wrong economic outcome, concretely>
WHY       <the mechanism that produces it>
EVIDENCE  <file:line>
FIX       <the change that closes it>
TEST      <the property to assert>
```

A claimed control points at executable code and, where the risk needs it, a named test. A comment, a TODO, an
uncalled helper or a design note describing a control is the same defect as the missing control, reported as
`UNRESOLVED: <control> (<why>)`. Add `VERDICT SHIP | NO-SHIP: <control>` on a review or a ship decision.

**A FEED CONTRACT block is emitted by default,** because authority is SELF and no consumer can tell you that
you are wrong: its twelve slots and the template are in `references/feed-spec.md`. Fill only the slots the change
touches; one it touches and cannot fill **is the finding**. A sandbox feed nothing outside consumes drops
back to findings alone.
