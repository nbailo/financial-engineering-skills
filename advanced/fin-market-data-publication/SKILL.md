---
name: fin-market-data-publication
description: >-
  Financial correctness for publishing a market-data feed you originate: sequencing and gap detection,
  session identity, resets, snapshot and incremental joins, A/B arbitration, recovery that terminates,
  conflation, book and volume filters, timestamps and deterministic publication. Use when your code
  emits the feed other systems build a book, volume or index from. To consume someone else's
  feed, use fin-exchange-integration.
license: MIT
---

# You publish the record

A feed you originate is the only account of what happened. A consumer that mis-built a book from it traded on
that book, and nothing outside your process can tell them, or you, that the book was wrong. The proof burden
sits before publication: a sequencing scheme a receiver can gap-detect without false positives, a recovery
path that terminates, a snapshot that states what it is a snapshot of, and checks on the emit path itself.
The consuming half of every mechanism here belongs to `fin-exchange-integration`. This file is what you must
publish so that a correct consumer can exist at all.

This skill is not installed with the six and does not contradict them: an invariant named in italics is
stated in full in `fin-money-core`, and this file adds only what changes when you are the publisher.

## When to use

Your process emits a stream other systems treat as the state of a market: quotes, book updates, trade prints,
session statistics, an index or a settlement figure. The test is authorship, not transport. If a number leaves
your perimeter and no other party publishes the same fact independently, you are the record for it, on
multicast, a WebSocket, a REST poll or a file drop alike.

Routing hints, never the definition: a sequence number your code assigns (`seq_num`, `RptSeq`, `MsgSeqNum`,
`update_id`); a session or channel identity you mint; `snapshot`, `incremental`, `retransmit`, `gap_fill`,
`recovery`; a publisher, feed handler, encoder or fan-out serialising book or trade events; conflation or a
last-value cache in front of a consumer socket; a heartbeat emitter; a `top_of_book`, `best_bid` / `best_ask`
or depth aggregate that leaves the process; a printable or eligibility flag; an index, mark or settlement
figure computed from your own prints.

## When not to

- The code reads somebody else's feed and maintains a local book from it. `fin-exchange-integration` owns
  that side, including reconnect, backfill and staleness gates on an inbound stream.
- The change is inside the matcher: crossing resting orders, allocation, auctions, order state or the journal
  the engine replays from. That is `fin-matching-engine`, loaded alongside this file when one change spans
  both. The seam is the sequencer: what the engine commits is the engine's, what leaves the process is this
  file's.
- The change is only amount arithmetic, rounding direction, operation identity or retry classification.
  `fin-money-core` states those in full.
- The numbers never become an obligation and never leave the process: a backtest over recorded feed captures,
  a research notebook, an internal dashboard nobody trades against.

## Workflow

1. Name what you publish and who is the record for each field: which quantities you author, and which you
   relay from an upstream that remains their authority.
2. Name the identity that scopes the sequence space, and what mints a new one.
3. Name the counter a consumer gap-detects on and write down its arithmetic. Document every other counter for
   what it is not valid for.
4. Enumerate the recovery mechanisms you offer and prove each terminates: at truncation, at a rate limit and
   at the end of a retention window.
5. Decide the snapshot join: which stream's sequence the as-of names, which direction the join runs, what the
   snapshot omits, and stamp the as-of in the same critical section as the book copy.
6. Decide conflation and backpressure as a correctness question, not a performance one, and write the answer
   into the published specification.
7. Put the assertions on the emit path, not behind a debug flag, and decide the freeze scope for each breach.
8. Load only the references this change triggers, implement each control with its test, and fill the feed
   contract in Output. A slot you cannot fill is a slot a consumer fills by guessing.

## Invariants

**A feed is a contract about identity, order and completeness.**
A consumer can only detect the gaps you taught it to detect. Session identity scopes the sequence space, the
sequence orders it, and the completeness rule says what a gap invalidates. Everything a consumer must infer,
you have failed to publish, and their inference becomes a position. None of it is recoverable after the fact.

**Publish the counter gap detection runs on, and publish its arithmetic.**
Counters per message, per packet, per instrument and per session routinely coexist on one feed and are not
interchangeable. Monotonicity is not the obligation; arithmetic compatibility is. Where a packet carries a
count of messages and the header numbers only the first, a receiver doing `expected += 1` per datagram drifts
the instant you batch, which is exactly what you do under load, so batching is a published behaviour rather
than an internal optimisation. A per-channel counter detects transport loss for the whole channel; a
per-instrument counter detects that this instrument's updates are contiguous. Publish only the first and every
consumer must invalidate every book on any loss. Changing the scheme is a new feed version, never a quiet
deploy. Specialises *operation identity*.

**A control message with no payload still carries sequence.**
A heartbeat is the only thing that distinguishes "nothing is trading" from "your delivery stopped", and the
consumer's guess is always the first. Emit heartbeats through quiet periods at a stated interval, carrying the
next expected sequence, and publish the interval: every consumer's liveness timeout derives from it, and
tightening it later changes their false-positive rate with no message saying so. An end-of-session message
commits that the sequence is closed. Serve recovery requests while the session drains, never emit further
messages under a closed identity, and mint a new one instead.

**A reset is a message you send, never a fact a consumer infers from a counter going backwards.**
Both arms a consumer can write are wrong after a silent reset, and the silent arm reaches production:
rejecting a lower sequence as stale freezes the book at its pre-reset state while trading continues, and
nothing logs an error. Emit the reset before the first message carrying the new numbering, in the same ordered
stream, not on a side channel and not as an operations notice. Enumerate what it clears, what it renumbers and
what it does not resend. The publisher-side mirror: the reset clears derived aggregates in the same commit
that resets the counter, or you publish a session volume figure with no trades behind it.

**A snapshot is the book as of a stated point in the incremental stream, and the point is stamped with the copy.**
Without that point the snapshot is unusable, because the consumer cannot know which buffered updates it
already contains. Read the sequence after cloning the book and you stamp the snapshot newer than it is; the
consumer then discards real updates in the range you skipped.

```rust
let entries = book.snapshot_entries();                 // WRONG: as_of is read after the copy, so
let as_of   = channel_seq.load(Ordering::Relaxed);     // [copy_point, as_of) is lost by every consumer
let (entries, as_of) = book.snapshot_with_seq();       // RIGHT: one critical section produces both
```

Where atomicity is genuinely unavailable, err old rather than new, and state whether re-applying an
already-included update is idempotent: it is only on an absolute-quantity feed, and on a delta-encoded feed
erring old double-applies. Say what the snapshot omits, because every field visible on the incremental and
absent from the snapshot is one consumers will silently hold stale.

**Two paths carrying one sequence space are byte-identical per sequence number.**
Arbitration is by sequence number alone: the consumer keeps whichever copy arrived first and discards the
other unread. If the paths ever differ in content under the same sequence, through different conflation,
different batching boundaries or a field populated on one path only, the consumer's book becomes a function of
network jitter. Generate once, at one sequencer, and fan out bytes. No message may depend on being seen
exactly once in a way its sequence cannot identify. A gap is declared after arbitration, never before. Say so,
or consumers run single-line, treat ordinary single-path loss as a gap, and aim a recovery storm at your
re-request path at the moment of highest load.

**Recovery must terminate, and truncation is where it stops terminating.**
For every mechanism you offer, name the terminating condition and publish the parameter it depends on:
retained depth for retransmission, maximum cycle period for a snapshot loop, refresh interval and absolute
(not delta) content for a natural refresh. Publish the truncation rule and any rate limit, because a limit you
enforce and do not publish turns a recoverable gap into a silent stall, and a loop assuming one request closes
one gap wedges forever at truncation. Treating a truncated response as a covered range is *durable dedupe*
failing inside your own publisher: the cursor advances past a range it never covered. Session restart is the
last resort, because a new identity strands everything unrecovered under the old one.

**Conflation is safe only on a state-encoded feed, and it changes what the feed is.**
Dropping one delta on a delta-encoded feed corrupts the book permanently with no gap visible anywhere: the
sequence stays contiguous, so no consumer can detect it. Where the feed carries an absolute quantity per key,
conflation is legitimate and every conflated event carries the range of updates it covers. Trades are never
conflatable: each print is an economic fact with its own identity, and collapsing two destroys volume, VWAP
and every trade-based signal. A conflated feed has last-value-cache semantics, so anything derived from the
count or order of updates is invalid on it, and consumers build those things unless you say so. It must not
vary silently with load, or a backtest built on quiet-period captures does not describe the open. Blocking the
publisher is never the answer, because it back-pressures the matcher; disconnecting a slow consumer always is,
because a gap is visible and recoverable.

**One feed, two filters: book-eligible and volume-eligible are different sets.**
Publish which filter applies to which downstream computation. Trade prints are not book events, and counting
them as book updates double-counts depth. An execution a later bulk print will cover is excluded from
published volume, or the same quantity is counted twice. Matches between one beneficial owner are not volume:
CFTC v. Coinbase, March 2021, 6.5M USD, where self-matched volume propagated into third-party indices. A field
you deliberately hold constant is part of the contract. State the constant and its effective date, and
populate it from a named encoder constant with a test asserting constancy. You cannot un-constant it on the
same feed version: the day it carries real data, every consumer that special-cased it silently produces
different output and nothing says the semantics changed. A field constant by accident becomes variable by
accident.

**Staleness is computed from the time the event happened, not the time you sent it.**
Three timestamps do three jobs: event time set by the matcher when the book event occurred, send time set at
serialisation, receive time set by the consumer. Only event time belongs in `now - t > max_age`. Send time is
disqualified by retransmission, since a resent message carries a fresh send time for old content. Receive time
is disqualified because it exists only when a message arrives, so it can never detect a publisher that went
quiet; that is the heartbeat's job. For the subtraction to be computable at all, publish an unambiguous epoch,
timezone and daylight-saving rule, and state what your clock is disciplined to. Leave either unstated and the
age carries an unbounded constant error, so a `max_age` gate either never fires or always fires.

**The aggregate you publish is checked on the path that publishes it.**
The default shape of a hand-written price level is wrong here: an aggregate decremented with unchecked
unsigned subtraction, guarded by an assertion the release build compiles out, on the path that publishes
depth. The rationalisation is always that the quantity cannot exceed the aggregate by construction. It was by
construction; drift is the bug you are hunting. Use a checked subtraction that returns an error, and on breach
freeze at the smallest scope containing it, withhold the level, and never clamp to zero, because a clamp is
fabricated depth with no exception attached. Where a panic would abandon in-flight obligations and you
saturate instead, emit the saturation as an event on the feed. Assert `best_bid <= best_ask` on every
top-of-book publish: a crossed book is arithmetically impossible, and NASDAQ published one to the world on
18 May 2012 when the Facebook cross was marked in error. Specialises *rounding and conservation*.

**Publication is deterministic, and the fan-out happens once, after the sequence is assigned.**
The same committed inputs produce the same bytes in the same order on every replica and every replay, or your
two paths are not the same feed and your recovery store does not answer with what you sent. Assign the
sequence at one point, fan out from that point, timestamp each sink hand-off there, and retain those records.
A fan-out to two sinks with different queueing or serialisation cost is a reviewable defect on sight, whether
or not either sink is currently slow, because the disparity is structural and shows up under the load you did
not test. Whether one destination must be no later than another depends on the venue's rules and its
regulator; where that duty exists, the evidentiary failure is charged separately from the timing failure.
Specialises *replay*.

**A number you publish and also act on is an input an adversary can pay to move.**
Separate the number that reports from the number that decides, and name which is the record for each
calculation. A statistic you compute from your own feed and then consume in your own risk, margin, valuation
or auction logic is an input any participant can bid the cost of moving. Size the controls so the cost of
moving it exceeds the payoff of the position it settles, and publish the inputs and the method, because a
consumer who cannot recompute your figure cannot tell it moved for a reason that was not trading. Hyperliquid's
JELLY market, 26 March 2025: a thinly traded price the venue's own risk calculations consumed was moved
deliberately, and the venue absorbed the result. Nor is the exposure confined to your perimeter, because where
your figure feeds somebody else's index your filter errors leave with it. Specialises *authority*.

## References

Each row is an instruction: when the trigger appears, read that file and apply it in order, never a summary.
The triggers are mechanical on purpose. A change to conflation policy must not load a message layout.

| file | read it immediately when |
|---|---|
| [conflation-and-backpressure.md](references/conflation-and-backpressure.md) | the code contains `conflate`, `coalesce`, a last-value cache, a ring buffer that overwrites, a slow-consumer drop or disconnect policy, an outbound queue high-water mark, or an event carrying a range of covered update ids such as `U` / `u` / `pu` |
| [emit-path-assertions.md](references/emit-path-assertions.md) | the code contains `total_qty`, `debug_assert`, `-=` or `checked_sub` / `saturating_sub` on an unsigned aggregate, `best_bid` / `best_ask`, a top-of-book publish, or a depth figure recomputed anywhere other than where it is emitted |
| [feed-specification.md](references/feed-specification.md) | you are writing or reviewing the document consumers read, onboarding a first external consumer, versioning a feed, or any slot in the feed contract cannot be answered from the repository |
| [nasdaq-itch-and-moldudp64.md](references/nasdaq-itch-and-moldudp64.md) | the code contains `MoldUDP64`, `SoupBinTCP`, `MessageCount`, a 10-byte session id, `0xFFFF`, a re-request server, `ITCH`, `TotalView`, `OUCH`, `Printable`, `Order Reference Number`, `Buy/Sell Indicator`, or "nanoseconds since midnight" |
| [cme-mdp-recovery.md](references/cme-mdp-recovery.md) | the code contains `MDIncrementalRefresh`, `35=X`, `269=J`, `RptSeq`, `LastMsgSeqNumProcessed`, `TransactTime`, `ApplID`, SBE templates, a Market Recovery snapshot channel, MBP or MBO book depth, or A/B line arbitration |
| [fix-session-sequencing.md](references/fix-session-sequencing.md) | the code contains `MsgSeqNum`, `ResendRequest`, `SequenceReset`, `GapFillFlag`, `ResetSeqNumFlag`, `PossDupFlag`, `OrigSendingTime`, `SendingTime`, FIXP, a session `UUID`, or a Not Applied message |
| [us-reg-nms-timing-fairness.md](references/us-reg-nms-timing-fairness.md) | the venue is a US national market system equity or option venue, or the code contains `Reg NMS`, `603(a)`, `SIP`, a consolidated feed, a Network Processor, `CTA`, `UTP`, or a proprietary depth-of-book feed published alongside a consolidated one |

## Output

Open an economic change with one line. This skill's usual pair follows from what it applies to:

```
authority: SELF · exposure: record
```

Then one entry per real finding, and nothing for a concept the change does not touch:

```
FINDING   <the wrong economic outcome, concretely>
WHY       <the mechanism that produces it>
EVIDENCE  <file:line>
FIX       <the change that closes it>
TEST      <the property to assert>
```

A claimed control points at executable code and, where the risk needs it, a named test. A comment, a TODO, an
uncalled helper or a design note describing a control is the same defect as the missing control, reported as
`UNRESOLVED: <control> (<why>)`. No findings is one or two sentences saying so and why the change is safe. Add
`VERDICT   SHIP | NO-SHIP: <the unresolved control>` only when the task is a review or a ship decision.

**This skill also emits a fuller block, and it emits it by default,** because authority is SELF: no consumer
can tell you that you are wrong, so the evidence has to be internal, and findings alone do not show whether it
exists. Fill only the slots this change touches. A slot it touches and cannot fill **is the finding**,
reported as `UNRESOLVED:`. A slot it does not touch is omitted, never left blank.

```
FEED CONTRACT
- Identity:    <what scopes the sequence space> · new identity minted by <event> at <file:line>
- Sequence:    <the counter consumers gap-detect on> · arithmetic <expected += n> · assigned at <file:line>
               · other counters <name> scoped to <scope>, NOT valid for <use>
- Control:     heartbeat every <interval> carrying <what> · end-of-session drains for <window>
- Reset:       announced by <message> · clears <list> · renumbers <list> · does NOT resend <list>
- Snapshot:    as-of names <stream>.<counter>, stamped with the copy at <file:line> · cycle <period>
               · omits <fields>
- Recovery:    <mechanism> terminates on <condition> · retained depth <n> · truncation and rate limit in <doc>
- Arbitration: <A/B or none> · byte-identity produced at <file:line> · gap declared after arbitration
- Conflation:  <none | state-encoded on <streams>> · covered-range field <name> · trades excluded
- Filters:     book-eligible <set> · volume-eligible <set> · constant <field>=<value> since <date>, test <name>
- Time:        event time <field, epoch, timezone, DST rule>, authoritative for staleness · clock <source>
- Emit checks: <assertion> at <file:line> · on breach <freeze scope> · saturation emitted at <file:line>
- Fan-out:     single point at <file:line> · sinks <list> · hand-off records retained at <location>
```

Two cases drop back to findings alone: a sandbox feed no outside system consumes and that feeds no index, mark
or settlement figure, and a read-only path over published records that republishes nothing. Both become
`exposure: record` the day an outside consumer arrives, or the number becomes someone else's input.
