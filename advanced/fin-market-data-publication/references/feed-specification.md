# The feed specification

The document a consumer reads before writing a line of code against your feed, and the thing that decides
whether a correct consumer can exist at all. A consumer cannot be correct against an underspecified feed: a
slot you leave unfilled is a slot they fill by guessing, and their guess becomes a position. This file is the
checklist, what each slot costs when it is missing, and the rules for changing a feed that consumers have
already been built against.

## Contents

- The checklist, in the order a consumer needs it
- What each missing slot actually costs, section by section
- Facts that cannot be derived from the wire format, and therefore must be written down
- Versioning: what can change quietly, what needs a version, and what needs a new feed
- Conformance: the reference consumer written only from the document

---

## The checklist

```
SEQUENCING
  [ ] Which counter gap-detection runs on (per-message / per-packet / per-instrument), and its arithmetic
  [ ] Whether packets carry multiple messages, and how the count is encoded
  [ ] Every additional counter published, its scope, and what it is NOT valid for
CONTROL
  [ ] Heartbeat interval, what a heartbeat carries, and whether it consumes a sequence number
  [ ] What a heartbeat covers: the channel, or the state of any one instrument
  [ ] End-of-session semantics and how long recovery requests remain serviceable
RESET
  [ ] The reset message and its trigger
  [ ] Exactly what a reset clears, what it renumbers, and what it does NOT resend
  [ ] How ONE instrument is withdrawn and restored, and what that event invalidates
SNAPSHOT
  [ ] The join key, and which stream's sequence it names
  [ ] Direction of the join: which buffered updates are dropped and which applied
  [ ] Snapshot cycle period, loop-order guarantees (or their absence), and fields the snapshot omits
GAP
  [ ] Whether A/B arbitration must precede gap declaration
  [ ] Recovery address, request format, retained depth, truncation rule, rate limit
  [ ] What a gap invalidates: this instrument, or every book on the channel
CONTENT
  [ ] Which message types are book-eligible; which are volume-eligible; the whole volume-bearing set
  [ ] Which rulebook decides eligibility, and which figures are computed from which set
  [ ] Every deliberately-constant field, its value, and its effective date
  [ ] Conflation policy: which streams, state- or delta-encoded, the interval, the bound, the chain link
  [ ] The slow-consumer policy per stream, and the recovery load it implies
TIME
  [ ] Each timestamp's meaning, epoch, timezone, DST rule, and clock discipline
  [ ] Which measurement each supports: event age, send latency, receive latency
  [ ] Which timestamp is authoritative for staleness
```

## What each missing slot costs

**Sequencing.** Omit the arithmetic and consumers assume one increment per datagram. That assumption holds
until you batch, which you do under load, so the feed appears correct in testing and produces false gaps at
the open. Omit the scope of a secondary counter and consumers gap-detect on the wrong one: a per-instrument
counter used as a channel gap detector reports a gap on every instrument that was simply quiet.

**Control.** Omit the heartbeat interval and every consumer picks their own liveness timeout, so the
false-positive rate of the whole consumer population is a number you do not know and cannot change. Omit what
the heartbeat covers and consumers read a channel-level liveness signal as a statement about each instrument
on it, which it is not: a heartbeat cannot age or invalidate the book they hold for one symbol, and only an
event scoped to that symbol can. Omit the end-of-session window and consumers stop requesting recovery while
the ranges are still available, or keep requesting after the store has dropped them.

**Reset.** Omit what the reset clears and consumers keep a session volume, a high, a low or an indicative
price that has no trades behind it. Omit that it renumbers a counter and a consumer's monotonicity check
either crashes or, worse, silently discards every message after the reset.

**Snapshot.** Omit the join key and the snapshot is unusable, because the consumer cannot know which buffered
updates it already contains. Omit the direction of the join and half the consumers apply updates they should
have dropped while the other half drop updates they should have applied. Omit the cycle period and a consumer
recovering from a gap cannot tell a slow loop from a stalled one, so they wait forever or restart in a loop.

**Gap.** Omit the arbitration rule and consumers run single-line, treat ordinary single-path loss as a gap,
and aim a recovery storm at the recovery path at the moment of highest load, on a code path that has never
been exercised at that rate. Omit the truncation rule and a recovery loop wedges at the truncation boundary.
Omit the rate limit you enforce and a recoverable gap becomes a silent stall.

**Content.** Omit the book-eligible and volume-eligible sets and consumers double-count depth by treating
prints as book events, or double-count volume by counting an execution and the bulk print that covers it.
Omit a deliberately-constant field and consumers write branches whose other arm is dead code, and build
reports on a value that means nothing.

**Time.** Omit the epoch, timezone or daylight-saving rule for a timestamp and a consumer infers them, wrongly
twice a year. Omit the clock discipline and the age they compute carries an unbounded constant error, so a
staleness gate built on it either never fires or always fires. Omit which timestamp is authoritative and they
will use the one that is always present, which is the send time, which is the one retransmission invalidates.
Say which measurement each timestamp is for, too, because event age, send latency and receive latency are
three different subtractions and only the first belongs in a gate on content.

## Facts that cannot be derived from the wire format

Every line of the checklist is there because it cannot be read off the bytes. Three categories are worth
naming, because they are the ones a publisher assumes are obvious:

- **Behaviour under conditions that have not occurred yet.** Batching under load, conflation under
  backpressure, truncation of a large recovery response, and the reset. A consumer testing against a quiet
  feed sees none of them, and a specification that documents only the steady state documents the easy half.
- **Negative facts.** What the snapshot does not carry, what a counter is not valid for, what a reset does not
  resend, which message types are not book events. A consumer cannot observe an absence and conclude it is
  intentional.
- **Numbers that are policy rather than protocol.** Heartbeat interval, retained depth, cycle period, rate
  limit, conflation interval. These have no representation on the wire at all, and each is load-bearing in a
  consumer's own control loop.

## Versioning

Three tiers, and the boundaries are not negotiable once a feed has external consumers:

1. **Additive and safe.** A new message type, or a new field appended where the encoding makes appended
   fields skippable. Consumers that ignore it are still correct. Document it and state the effective date.
2. **Requires a version.** Any change to sequencing arithmetic, reset semantics, the snapshot join, the
   conflation policy, the book-eligible or volume-eligible sets, or a timestamp's meaning. These change what
   existing code computes without changing what it parses, so nothing fails loudly.
3. **Requires a new feed.** Un-constanting a deliberately-constant field. The day it carries real data, every
   consumer that special-cased the constant silently produces different output, and nothing in the message
   says the semantics changed. There is no way to signal it in band, because the signal is the field itself.

Tightening a published number is a version change even when it looks like an improvement. A shorter heartbeat
interval changes every consumer's false-positive rate. A shallower retransmission store changes which gaps are
recoverable. A faster snapshot cycle changes the timeout a consumer set against it.

## Conformance

The specification is finished when someone can write a correct consumer from it without reading your code.
Test that claim rather than asserting it:

- Have an engineer with no access to the publisher implement a consumer from the document alone, then replay
  a capture that contains a reset, a batched packet, a truncated recovery response, a conflation burst and a
  session end. Every divergence from the publisher's own book is a defect in the document, not in the reader.
- Keep that consumer as a conformance test and run it against every release. It fails on the day a change
  crosses one of the version boundaries above, which is the day you need to know.
- Publish the capture alongside the document. A consumer who can replay a known-good stream can distinguish
  their bug from yours, and every hour they save is an hour your support path does not spend.
