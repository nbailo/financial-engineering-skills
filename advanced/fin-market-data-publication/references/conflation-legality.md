# Which fields conflation may touch, and the test that decides

Conflation replaces a pending update with a later one, and whether that is lossless is a property of the
encoding rather than a matter of degree. This file is the three conditions, the mechanical test to run
against your own encoder, and the reason a breach of any one of them is invisible from the consumer side.
What a conflated event then has to carry, and what to do with a consumer who cannot keep up, are in this
skill's conflation mechanics reference.

## The obligation

**Conflation is legitimate only where it is semantically equivalent, bounded and recoverable.**
Raw and conflated are two sequence spaces, never one: a conflated event carries its own counter, a raw
sequence number is never reused, renumbered or widened to cover events dropped before they were numbered, and
you publish both counters and say which one gap detection runs on. Equivalent: the surviving event leaves a
consumer where the events it replaced would have left them, which holds when every field carries an absolute
value for a key and fails on any field carrying a change, and the proof is a replay of the raw stream beside
the conflated one asserting equal state at every covered-range boundary, never a comparison against your own
book. Bounded: a published interval, and a queue bound that is global across every instrument and every
subscriber rather than per key, because a per-key pending slot bounds one key while instruments times
subscribers is the number that has to fit in memory. Recoverable: every event carries the range of updates it
stands for and a link to the event before it, so a drop is detectable and a snapshot still joins. Miss one
condition and the corruption is silent, because the conflated sequence stays contiguous and no gap detector
fires anywhere.

## The property that decides whether conflation is legal

**Conflation is legitimate only where it is semantically equivalent, bounded and recoverable.** State
encoding buys the first condition and neither of the other two, which is why a feed can be state-encoded and
still be conflated wrongly.

- **Equivalent.** A state-encoded event carries the absolute value for a key, so the latest event for that key
  is sufficient and every earlier one is redundant. A delta-encoded event carries a change, so every event is
  load-bearing and dropping one corrupts the book permanently. Equivalence is per field rather than per
  message: one field carrying a change, on an otherwise absolute message, makes that message unconflatable.
- **Bounded.** A published maximum interval and a published queue bound, so the age of what a consumer holds
  has a ceiling they can reason about. Unbounded conflation is indistinguishable from a stalled stream during
  exactly the burst that caused it, which is the period a consumer most needs to see.
- **Recoverable.** Every conflated event carries the range of updates it stands for and a link a consumer can
  check against the event they last received. Without both, a drop is undetectable and a snapshot cannot be
  joined to the stream, so nothing repairs the state afterwards.

The corruption is undetectable from the consumer side, which is what makes it the worst failure in this file.
The transport sequence is still contiguous, because you dropped the message before it was numbered, or
collapsed two numbered messages into one and renumbered. No gap detector fires. The book is simply wrong,
silently, until the next reset or snapshot, and every decision taken against it in between was taken against
a state that never existed.

The test to run against your own encoder is mechanical: for each message type, ask whether applying the same
message twice leaves the book in the same state as applying it once. If yes for every field, the type is
state-encoded and conflatable. If any field is a change rather than a value, the type is delta-encoded and it
is not.
