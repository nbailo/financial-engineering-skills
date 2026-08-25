# CME MDP 3.0: two counters, the channel reset, and A/B line arbitration

> **Provenance**
> provider: CME Group · surface: Market Data Platform (MDP) 3.0 as documented for client systems
> version: MDP 3.0, read as pages of the CME Group Client Systems Wiki, which carries no edition number. Page
> id, version and last modified: Channel Reset 457326922 v5 2026-06-09 · Incremental Feed Arbitration
> 457672396 v2 2024-12-23 · Book Recovery Methods 457705188 v3 2025-01-10 · Market Data Snapshot Full
> Recovery 457736274 v3 2026-02-19 · MBP and MBOFD Market Recovery 457672425 v4 2025-10-01.
> verified_at: 2026-08-25
> sources: https://cmegroupclientsite.atlassian.net/wiki/spaces/EPICSANDBOX/pages/457326922
> · .../457672396 · .../457705188 · .../457736274 · .../457672425
> pinned: each page was read through the wiki's content API on 2026-08-25, which returns its version and
> last-modified timestamp in the same response as the body.
> verified: every sentence in quotation marks below was read on one of those five pages that day, including
> the per-instrument gap sentence, the RptSeq field description with its conflated TCP sentence, the Channel
> Reset identification sentence, both sentences enumerating what the reset clears and deletes, the two
> snapshot omissions, the three arbitration sentences and the tag 60 definition.
> unverified: nothing quoted here. www.cmegroup.com still refuses a non-browser client with HTTP 403, so the
> wiki is where a recheck starts.
> revalidate_when: CME publishes an MDP version above 3.0; the Channel Reset page changes what it clears or
> renumbers; the conflated group starts carrying a real RptSeq; the arbitration contract changes; a cited
> page moves past the version recorded above.

Two counters on one feed with different scopes, a reset that renumbers one of them and empties the
aggregates behind it, and two independent lines carrying one sequence space. This is the shape to copy for
any feed carrying more than one counter or more than one path, and the consumer bug in the middle is the one
a silent reset causes. Read it when the repository publishes a per-instrument sequence beside a channel
sequence, emits a reset, or sends the same bytes down two lines.

## Contents

- Two counters: the channel packet sequence and the per-instrument report sequence
- What the conflated group does to the per-instrument counter
- Channel reset: what it clears, what it renumbers, what it does not resend
- The consumer code both arms of which are wrong after a silent reset
- The three publisher obligations the snapshot algorithm creates
- A/B line arbitration and the byte-identity requirement it imposes
- Transact time, and the cross-stream comparison it is used for

---

## Two counters

| Counter | Scope | Where it lives | Receiver arithmetic | Reset event |
|---|---|---|---|---|
| Packet sequence | per channel, per packet | packet header | one increment per packet | Channel Reset |
| `83-RptSeq` | per instrument, per book update | market-data entry repeating group | per-instrument continuity check | resets to **1 per instrument** |

The packet sequence detects transport loss for the whole channel. The report sequence detects that one
instrument's book updates are contiguous, and the book recovery methods page states what having it buys a
consumer: "If there is a gap between sequence numbers, it indicates that data was missed for the instrument
when packet loss occurred. If there is no gap, the data can be used immediately, and it also indicates that
the book for this instrument still has a correct, current state."

That last clause is the design lesson from the positive side. Publish only a channel counter and a consumer
who loses one datagram learns nothing about which instruments it carried, so the safe response is to
invalidate every book on the channel, turning one dropped packet into a full re-snapshot at the moment your
recovery path is busiest. Publish a per-instrument counter too and a consumer keeps the books the gap provably
did not touch, but only if the reset semantics below are published as well, because a counter that renumbers
without warning is worse than no counter.

## What the conflated group does to the per-instrument counter

The snapshot's field table documents `83-RptSeq` as the "Sequence number of the last Market Data entry
processed for the instrument", then adds a sentence that matters more than its length suggests: "The MDP
Conflated TCP market data group sends a RptSeq value of zero."

A conflated distribution of the same market therefore does not carry the counter the unconflated one uses for
per-instrument continuity, and a consumer moving between the two cannot carry a gap check across. That is the
concrete form of the rule in the skill body: raw and conflated are two sequence spaces, and a counter meaning
something on one is published as an explicit non-value on the other rather than quietly reused. Decide the
same question for your own feed in the specification, not in the encoder.

## Channel reset

"A Market Data Incremental Refresh (tag 35-MsgType=X) message with tag 269-MDEntryType=J and tag 1180-ApplID
present will identify a Channel Reset occurrence." It is the model to copy because it enumerates exactly what
a reset invalidates and what it leaves alone.

| Cleared by the reset | **Not** resent by the reset |
|---|---|
| The order book | Settlement prices, and daily statistics generally |
| Current session trade volume | Instrument definitions, which are a separate feed |
| Highest and lowest trade price | n/a |
| Indicative opening price | n/a |
| `83-RptSeq` for MBP updates, renumbered to **1 per instrument** | n/a |
| The channel's Market Recovery snapshots, which are deleted | n/a |

The page states the first column and the settlement exclusion together: "The order book, trade volume,
high/low trade price, and indicative opening price for the instrument should be emptied on the client system
for the impacted channel. Daily statistics such as Settlement prices are not resent during Channel Reset." And
the deletions: "The Market Data Snapshot Full Refresh (tag 35-MsgType=W) messages on the Market Recovery feed
will be deleted for the impacted channel. Tag 83-RptSeq for Market by Price (MBP) book updates resets to 1 for
each instrument repeating group."

Two publisher obligations fall out, and neither depends on FIX. The reset must clear derived aggregates in the
same commit that renumbers the counter, or you publish a session volume with no trades behind it and a high or
low belonging to a book that no longer exists. And it must be emitted before the first message carrying the
new numbering, in the same ordered stream, not on a side channel and not as an operational notice.

## The consumer code that both arms get wrong

```rust
// Consumer code your feed will be parsed by. Both arms are wrong after a reset.
if rpt_seq != last_rpt_seq + 1 { panic!("gap"); }   // crashes on the reset
if rpt_seq <= last_rpt_seq      { return; }         // silently drops EVERY post-reset update
```

The second arm reaches production. The book freezes at its pre-reset state while trading continues, nothing
logs an error, and the consumer's staleness gate does not fire because messages are still arriving. It is a
consumer bug and you cause it, by renumbering a counter without a message that says so. The same hazard exists
in your own publisher: any assertion over the report sequence needs the reset in its state machine.

## The three publisher obligations the snapshot algorithm creates

- **Stamp the as-of atomically with the book copy.** Read the channel sequence after cloning the level book
  and you stamp the snapshot newer than it is, and the drop step then makes every consumer delete real updates in the
  range you skipped. One critical section must produce both the entries and the sequence they are valid as of.
  Where that is genuinely unavailable, err old rather than new, and state whether re-applying an
  already-included update is idempotent: it is only on an absolute-quantity feed.
- **Say what the snapshot does not carry.** MDP says it in one sentence, "Client systems will not recover any
  missed statistics on the Market Recovery feed", and sends consumers elsewhere for a whole category, since
  "client systems must subscribe to the Instrument Definition feed to determine if any new instruments have
  been defined." Every field visible on the incremental and absent from the snapshot is one consumers hold
  stale, and the specification is the only place that can be said.
- **State the direction of the join in the specification, not only in the code.** The drop step is the whole
  contract: which buffered updates are dropped and which are applied. A consumer who infers the direction gets
  it backwards half the time, and both halves are silent.

## A/B line arbitration

MDP publishes two independent paths carrying the same sequence space, and states the consumer contract: "Any
given packet sent on both feeds can arrive first on either feed depending upon network carrier and independent
of CME Group." Consumers are told to "Discard a packet if the sequence number has already been processed", and
that "If a sequence number gap is detected, this indicates a packet was lost on both the Incremental Feed A
and Incremental Feed B."

Duplicate tolerance is therefore a publisher requirement rather than a consumer nicety, and it constrains what
you may send:

- **Byte-identical per sequence number on both lines.** Arbitration is by sequence number alone, and the
  consumer keeps whichever copy arrived first and discards the other unread. If the lines ever differ in
  content under the same sequence, through different conflation, different batching boundaries producing
  different groupings under the same first-message sequence, or a field populated on one path only, the
  consumer's book becomes a function of network jitter. Generate once, at one sequencer, and fan out bytes.
- **No apply-once semantics beyond the sequence number.** Every message must be safely discardable as a
  duplicate from its sequence alone. A message whose effect depends on being seen exactly once, and which
  cannot be identified as a duplicate from its sequence, is unimplementable on an A/B feed.
- **A gap is declared after arbitration, never before.** Unstated, consumers run single-line, treat ordinary
  single-path loss as a gap, and generate a recovery storm against your recovery path at the moment of highest
  load, on a path never exercised at that rate.

## Transact time

Tag `60-TransactTime` is an event time, not a send time: the snapshot field table defines it as the "Timestamp
of the last event security participated in, sent as number of nanoseconds since Unix epoch", and the recovery
page describes it as communicating "the start transaction time of the last event for the instrument". MDP uses
it for a job no other timestamp can do, comparing the same instrument across two streams. Step 4 above defers
an instrument when snapshot and incremental disagree on it, because the disagreement means the two views were
taken at different points and the join would produce a book that never existed.

Two rules for your own feed follow. Carry an event time on both the incremental and the snapshot, populated
from the same clock at the same point in the matcher, and state its epoch. And use it, not the send time,
wherever a consumer compares two messages about the same object, because a resent message carries a fresh send
time for old content and the comparison then reports a difference that is an artefact of the transport.
