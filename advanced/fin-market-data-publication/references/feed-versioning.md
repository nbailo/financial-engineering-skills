# What a missing slot costs, and what changing a published feed costs

Two halves of the same question. The first is what each unfilled slot in the specification does to a
consumer, section by section, because a checklist without consequences gets filled in with plausible words.
The second is which changes to a feed with external consumers are safe, which need a version, and which need
a new feed, plus the test that says whether the document is finished at all. Read this when a field is added,
a published number is tightened, a constant field is about to carry real data, or a first external consumer
is onboarding.

## Contents

- What each missing slot actually costs, section by section
- Facts that cannot be derived from the wire format, and therefore must be written down
- Versioning: what can change quietly, what needs a version, and what needs a new feed
- Conformance: the reference consumer written only from the document

---

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
