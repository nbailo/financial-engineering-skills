# CME MDP 3.0: sequencing, reset, snapshot and arbitration

> **Provenance**
> provider: CME Group · surface: Market Data Platform (MDP) 3.0: the two sequence counters, channel reset, snapshot and incremental recovery, and A/B line arbitration
> version: MDP 3.0. The original pass recorded no document revision or publication date, so there is no edition to compare a recheck against.
> verified_at: not established
> sources: https://www.cmegroup.com/market-data/
> verified: none in this pass. No sentence below was re-read against a source for v0.5.0.
> unverified: all of it. This file predates the provenance requirement and was not re-checked in the v0.5.0 pass, so its claims carry the confidence of their original sourcing and no more, with no date you can check. Two things make a recheck harder than usual and are worth knowing before you rely on a quotation here: no URL for the specific MDP 3.0 market data and market recovery documents was recorded when the file was written, and cmegroup.com refused a non-browser client on 2026-08-25 with HTTP 403, so even the location above was not opened. Start from the market data index in a browser and find the MDP 3.0 pages in CME's client systems documentation.
> revalidate_when: CME publishes an MDP version above 3.0; the Channel Reset message changes what it clears or renumbers; the snapshot stops naming its join point in the incremental stream; the published A/B arbitration contract changes.

Protocol behaviour for CME's Market Data Platform, and the model worth copying when you design your own
recovery. MDP is the most completely specified public example of the four mechanisms in this domain: two
independent counters, an explicit reset that enumerates what it invalidates, a snapshot that names its join
point in the incremental stream, and an A/B feed whose arbitration rule is published as a consumer contract.
Read this when the repository names these constructs or reimplements their shapes.

## Contents

- Two counters: the channel packet sequence and the per-instrument report sequence
- Channel reset: the message, what it clears, what it renumbers, what it does not resend
- The consumer code both arms of which are wrong after a silent reset
- Snapshot and incremental: the join key, the loop, and the direction of the join
- The three publisher obligations the snapshot algorithm creates
- A/B line arbitration and the byte-identity requirement it imposes
- Transact time, and the cross-stream comparison it is used for

---

## Two counters

MDP publishes a packet sequence number per channel and a report sequence number per instrument, and they
answer different questions.

| Counter | Scope | Where it lives | Receiver arithmetic | Reset event |
|---|---|---|---|---|
| Packet sequence | per channel, per packet | packet header | `expected += 1` per packet | Channel Reset (`35=X`) |
| `83-RptSeq` | per instrument, per book update | inside each market-data entry repeating group | per-instrument continuity check | resets to **1 per instrument** on `269=J` |

The packet sequence detects transport loss for the whole channel. The report sequence detects that a
particular instrument's book updates are contiguous. CME is explicit about the consequence of having only the
first: on a packet gap "it should be assumed that all books maintained in the client system may no longer have
the correct, latest state".

That sentence is the design lesson. Publish only a channel counter and you have forced every consumer to
invalidate every book on any loss, which turns a single dropped datagram into a full re-snapshot of the
channel at the moment your recovery path is busiest. Publish a per-instrument counter as well and a consumer
can recover per instrument, but only if the reset semantics below are published too, because a per-instrument
counter that renumbers without warning is worse than no per-instrument counter at all.

## Channel reset

Channel Reset is sent as `35=X` with `269-MDEntryType=J` (Empty Book) and tag `1180 ApplID`, and it signals
that books on the channel are to be discarded. It is the model to copy because it enumerates exactly what a
reset invalidates and what it leaves alone:

| Cleared by the reset | **Not** resent by the reset |
|---|---|
| The book (Empty Book) | Settlement prices |
| Session trade volume | Instrument definitions, which are a separate feed |
| Session high and low | Statistics not carried on the incremental |
| Indicative opening price | n/a |
| `83-RptSeq`, renumbered to **1 per instrument** | n/a |
| The channel's Market Recovery snapshots, which are deleted | n/a |

Two publisher-side obligations fall out. The reset must clear derived aggregates in the same commit that
renumbers the counter, or you publish a session volume with no trades behind it and a high or low that belongs
to a book that no longer exists. And the reset must be emitted before the first message carrying the new
numbering, in the same ordered stream, not on a side channel and not as an operational notice.

## The consumer code that both arms get wrong

```rust
// Consumer code your feed will be parsed by. Both arms are wrong after a reset.
if rpt_seq != last_rpt_seq + 1 { panic!("gap"); }   // crashes on the reset
if rpt_seq <= last_rpt_seq      { return; }         // silently drops EVERY post-reset update
```

The second arm is the one that reaches production. The book freezes at its pre-reset state while trading
continues, nothing anywhere logs an error, and the consumer's staleness gate does not fire because messages
are still arriving. It is a consumer bug, and you cause it by renumbering a counter without a message that
says so. The same hazard exists inside your own publisher: any assertion you hold over the report sequence has
to have the reset in its state machine.

## Snapshot and incremental

A snapshot is not the book right now. It is the book as of a stated point in the incremental stream, and
without that point it is unusable, because the consumer cannot know which buffered incrementals it already
contains. CME states the join key precisely: the snapshot's `369-LastMsgSeqNumProcessed` "corresponds to the
packet sequence number on the Incremental feed."

The consumer algorithm your snapshot has to support:

1. Subscribe to the incremental and **queue** it. Subscribe to Instrument Definition separately.
2. Join the snapshot loop and process **one full iteration starting at snapshot sequence 1**. Loop order is
   not guaranteed, so a partial iteration is not a recovery.
3. Per instrument, compare tag `369` against the queued incremental packet sequence numbers.
4. If a `SecurityID` appears in both, compare tag `60-TransactTime`. If unequal, **defer that instrument to
   the next snapshot cycle**.
5. **Drop all cached incremental updates with packet sequence below `369`.** Apply the remainder in order.
6. Books go live **per instrument** as each recovers. All books are assumed incorrect until they do.

## The three publisher obligations this creates

- **Stamp the as-of atomically with the book copy.** Reading the channel sequence after cloning the level book
  stamps the snapshot as newer than it is, and step 5 then makes every consumer delete real updates in the
  range you skipped. One critical section must produce both the entries and the sequence they are valid as of.
  Where that is genuinely unavailable, err old rather than new, and state whether re-applying an
  already-included update is idempotent: it is only on an absolute-quantity feed.
- **Say what the snapshot does not carry.** The snapshot loop does not recover statistics. Every field a
  consumer can see on the incremental but not on the snapshot is a field they will silently hold stale, and
  the only place that can be stated is the specification.
- **State the direction of the join in the specification, not only in the code.** "All book updates in the
  latest Market Data Incremental Refresh message must be processed before the order book can be considered
  valid" is a sentence a consumer needs in writing, because it is what makes their book valid at all.

## A/B line arbitration

MDP publishes two independent paths carrying the same sequence space, and states the consumer contract: any
packet "can arrive first on either feed depending upon network carrier and independent of CME Group"; discard
a packet whose sequence has already been processed; a sequence gap means the packet was lost on **both**.

Duplicate tolerance is therefore a publisher requirement rather than a consumer nicety, and it constrains what
you are allowed to send:

- **Byte-identical per sequence number on both lines.** Arbitration is by sequence number alone, and the
  consumer keeps whichever copy arrived first and discards the other unread. If the lines ever differ in
  content under the same sequence, through different conflation, different batching boundaries producing
  different message groupings under the same first-message sequence, or a field populated on one path only,
  the consumer's book becomes a function of network jitter. Generate once, at one sequencer, and fan out bytes.
- **No apply-once semantics beyond the sequence number.** Every message must be safely discardable as a
  duplicate on the basis of its sequence alone. A message whose effect depends on being seen exactly once, and
  which cannot be identified as a duplicate from its sequence, is unimplementable on an A/B feed.
- **A gap is declared after arbitration, never before.** If the specification does not say this, consumers
  will run single-line, treat ordinary single-path loss as a gap, and generate a recovery storm against your
  recovery path at the moment of highest load, on a path that has never been exercised at that rate.

## Transact time

Tag `60-TransactTime` is the time the book event occurred, set by the matching engine, and MDP uses it for a
job that no other timestamp can do: comparing the same instrument across two streams. Step 4 of the snapshot
algorithm defers an instrument when the snapshot and the incremental disagree on transact time, because the
disagreement means the two views were taken at different points and the join would silently produce a book
that never existed.

Two rules for your own feed follow. Carry an event time on both the incremental and the snapshot, populated
from the same clock at the same point in the matcher. And use it, not the send time, wherever a consumer is
expected to compare two messages about the same object, because a resent message carries a fresh send time for
old content and the comparison then reports a difference that is an artefact of the transport.
