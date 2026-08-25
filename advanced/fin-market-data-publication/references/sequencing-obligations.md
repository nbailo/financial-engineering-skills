# Identity, the counter, liveness, the reset, the snapshot join, recovery and time

> **Provenance**
> provider: Nasdaq, cited once for a timestamp sentence and for what the same specification does not settle
> surface: timestamp semantics on a published feed; nothing else in this file is provider-specific
> version: TotalView-ITCH 5.0
> verified_at: 2026-08-25
> sources: https://www.nasdaqtrader.com/content/technicalsupport/specifications/dataproducts/NQTVITCHspecification.pdf
> pinned: the specification was downloaded on 2026-08-25 and read as extracted text.
> verified: the Data Types sentence quoted in the timestamp section was read in that document on 2026-08-25,
> and each message's field table repeats the phrase and adds nothing.
> unverified: which midnight an ITCH timestamp counts from. That is a verified absence from the specification
> read here rather than an open question about the venue's behaviour, and the answer exists outside the
> document. Every other section of this file is transport-independent and quotes nobody.
> revalidate_when: the ITCH Data Types section gains an epoch, a timezone or a daylight-saving statement, or
> your own clock source changes what it is disciplined to.

The seven decisions that make a feed gap-detectable, recoverable and comparable at all, stated without any
transport: what scopes the sequence space and what mints a new one, which counter a consumer runs its
arithmetic on and what every other counter is not valid for, how a quiet market is distinguished from a
stopped delivery, how a renumbering is announced rather than inferred, how a snapshot names the point it is a
snapshot of, what makes a recovery mechanism end, and which timestamp answers which question. Every named
protocol in this skill is one encoding of these seven, and none of those encodings is the obligation.

## Contents

- A feed is a contract about identity, order and completeness
- The counter gap detection runs on, and its arithmetic
- A liveness signal is an obligation you publish; its encoding belongs to the protocol
- A reset is a message you send, and withholding one instrument is the same obligation at a smaller scope
- A snapshot is the book as of a stated point, and the point is stamped with the copy
- Recovery must terminate, and truncation is where it stops terminating
- Four questions, four measurements, and one timestamp cannot answer another's

---

**A feed is a contract about identity, order and completeness.**
A consumer detects only the gaps you taught it to detect. Session identity scopes the sequence space, the
sequence orders it, and the completeness rule says what a gap invalidates. Everything a consumer must infer,
you failed to publish, their inference becomes a position, and none of it is recoverable afterwards.

**Publish the counter gap detection runs on, and publish its arithmetic.**
Counters per message, per packet, per instrument and per session routinely coexist on one feed and are not
interchangeable. Monotonicity is not the obligation; arithmetic compatibility is. Where a packet carries a
count of messages and the header numbers only the first, `expected += 1` per datagram drifts the instant you
batch, so batching is published behaviour rather than an internal optimisation. A per-channel counter detects
transport loss for the whole channel; a per-instrument counter detects that one instrument's updates are
contiguous. Publish only the first and every consumer must invalidate every book on any loss. Changing the
scheme is a new feed version, never a quiet deploy. Specialises *operation identity*.

**A liveness signal is an obligation you publish; its encoding belongs to the protocol.**
A heartbeat separates "nothing is trading" from "your delivery stopped", and a consumer with no liveness
signal guesses the first. Emit one through quiet periods at a stated interval and publish the interval,
because every consumer's liveness timeout derives from it. What it carries is a protocol decision: a
payload-free packet carrying the next expected sequence, a session message consuming a sequence number of its
own, and a transport frame outside the sequence space are three contracts with three gap arithmetics. Say
which you emit and what it covers, and copy the obligation rather than another protocol's encoding, because a
magic message count means heartbeat only on a transport that numbers messages and can send a packet carrying
none. A heartbeat on a channel of many instruments says the channel is alive and says nothing about any one
of them, so it can neither age nor invalidate the book a consumer holds for a single symbol. An end-of-session
message commits that the sequence is closed: serve recovery while the session drains, never publish under a
closed identity, and mint a new one instead.

MoldUDP64's payload-free heartbeat and its end-of-session packet are that transport's encodings of this
obligation, and neither is a property of feeds in general. A payload-free packet
carrying the next expected number is available precisely because this transport numbers messages and can send
a packet containing none; a session protocol that numbers every message gives its heartbeat a sequence number
of its own, and a WebSocket feed may carry a liveness frame outside the sequence space entirely. The receiver
arithmetic differs in all three. Copy the obligation, which is that quiet is distinguishable from dead and the
interval is published, and check that your transport can carry the encoding before copying that.

**A reset is a message you send, never a fact a consumer infers from a counter going backwards.**
Both arms a consumer can write are wrong after a silent reset, and the silent one reaches production:
rejecting a lower sequence as stale freezes the book at its pre-reset state while trading continues, with no
error logged anywhere. Emit the reset before the first message carrying the new numbering, in the same ordered
stream, never on a side channel or as an operations notice, and enumerate what it clears, what it renumbers
and what it does not resend. The encoding is the protocol's: a control message on the incremental, a
session-level sequence reset and a new session identity differ in what stays recoverable across them, so state
which you send. The publisher-side mirror is that the reset clears derived aggregates in the same commit that
resets the counter, or you publish a session volume with no trades behind it. Withholding one instrument is
the same obligation at a smaller scope: stop publishing a symbol silently and every consumer holds the last
book you sent indefinitely, because silence is what a quiet market looks like. Emit an explicit unavailable or
invalidation event scoped to that instrument, and state what it invalidates and what ends it.

## The snapshot is the book as of a stated point, stamped with the copy

**A snapshot is the book as of a stated point in the incremental stream, and the point is stamped with the copy.**
Without that point the snapshot is unusable, because the consumer cannot know which buffered updates it
already contains. Clone the entries, then read the sequence, and you have stamped it newer than it is:
everything in `[copy_point, as_of)` is discarded by every consumer as already included, and those updates were
real. One critical section produces both the entries and the sequence they are valid as of, or the pair is a
lie about a point in time. Where atomicity is genuinely unavailable, err old rather than new, and state
whether re-applying an already-included update is idempotent: it is only on an absolute-quantity feed, and on
a delta-encoded feed erring old double-applies. Say what the snapshot omits, because every field visible on
the incremental and absent from the snapshot is one consumers hold stale.

## Recovery must terminate, and truncation is where it stops terminating

**Recovery must terminate, and truncation is where it stops terminating.**
For every mechanism you offer, name the terminating condition and publish the parameter it depends on:
retained depth for retransmission, maximum cycle period for a snapshot loop. A mechanism that rebuilds state
opportunistically from the live stream has no terminating condition at all, so it is a concurrent
optimisation and never the answer to a gap. Publish the truncation rule and any rate limit, because a limit
you enforce and do not publish turns a recoverable gap into a silent stall, and a loop assuming one request
closes one gap wedges forever at truncation. Treating a truncated response as a covered range is *durable
dedupe* failing inside your own publisher: the cursor advances past a range it never covered. Session restart
is the last resort, because everything unrecovered stays addressable only under the identity you left.

## Four questions, four measurements, and one timestamp cannot answer another's

**Four questions, four measurements, and one timestamp cannot answer another's.**
Event age is now minus the event time the matcher set, and it is the only input to a staleness gate on
content. Send latency is send time minus event time, and measures how long your publisher held the event.
Receive latency is receive time minus send time, exists only for messages that arrived, and means nothing
across clocks disciplined to different references. Transport liveness is that nothing arrived within the
published heartbeat interval, and needs no timestamp at all. The defect is answering one question with
another's measurement: retransmission gives old content a fresh send time, so send time is disqualified as an
age input, and receive time can never detect a publisher that went quiet. Publish an unambiguous epoch,
timezone and daylight-saving rule per timestamp, state what your clock is disciplined to, and say which
timestamp is authoritative for staleness, or the age carries an unbounded constant error and a `max_age` gate
either never fires or always fires.

### One specification's timestamp sentence, and what it does not settle

ITCH timestamps are defined as nanoseconds since midnight. The Data Types section of the specification read on
2026-08-25 says exactly "Timestamps are represented as nanoseconds since midnight", and each message's field
table repeats that phrase and adds nothing. Which midnight, in which timezone, and what happens across a
daylight-saving transition are stated nowhere in that document. That is a verified absence in this document
and **not** a claim that the behaviour is undefined: the answer exists outside the specification, and a
consumer who infers it from the data will infer it wrong twice a year.

The obligation it illustrates is unconditional for your own feed. Write the epoch, the timezone and the
daylight-saving rule into the specification, and state what your clock is disciplined to. A consumer computing
staleness subtracts your timestamp from their clock, so an unstated discipline gives the result an unbounded
constant error.
