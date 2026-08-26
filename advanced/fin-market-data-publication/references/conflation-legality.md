# Which fields conflation may touch, and the test that decides

Conflation replaces a pending update with a later one, and whether that is lossless is a property of the
encoding rather than a matter of degree. This file is the three conditions, the two mechanisms that
satisfy the third, and the test that decides. What a conflated event then has to carry, and what to do
with a consumer who cannot keep up, are in this skill's conflation mechanics reference.

## The obligation

**Conflation is legitimate only where it is semantically equivalent, bounded and recoverable.** State
encoding buys the first condition and neither of the other two, which is why a feed can be state-encoded
and still be conflated wrongly.

- **Equivalent.** A state-encoded event carries the absolute value for a key, so the latest event for
  that key is sufficient and every earlier one is redundant. A delta-encoded event carries a change, so
  every event is load-bearing and dropping one corrupts the book permanently. Equivalence is per field:
  one field carrying a change, on an otherwise absolute message, makes that message unconflatable. The
  proof is a replay of the raw stream beside the conflated one, never a comparison against your book.
- **Bounded.** A published maximum interval and a published queue bound, global across every instrument
  and every subscriber rather than per key, because a per-key slot bounds one key and instruments times
  subscribers is what has to fit in memory. Unbounded conflation is indistinguishable from a stalled
  stream during exactly the burst that caused it.
- **Recoverable.** A consumer can tell exactly which raw updates a conflated message accounts for, and
  can check that no conflated message went missing. Two mechanisms deliver that, and either is enough.

Miss one condition and the corruption is silent: the transport sequence stays contiguous, because you
dropped the message before it was numbered, so no gap detector fires. The book is wrong until
the next snapshot, and every decision taken against it was taken against a state that never existed.

## The two mechanisms that make a conflated stream recoverable

The property is one sentence: **a consumer must be able to tell exactly which raw updates a conflated
message accounts for.** Which mechanism carries it is a protocol question, not a correctness one, and
there is no universal requirement for a second counter.

- **A covered raw sequence range on the message.** Where the protocol has room for the field, the
  conflated message names the first and last raw sequence number it collapses. That is sufficient on its
  own: the ranges tile the raw space, a consumer reads off the message which raw updates it accounts
  for, and a range that does not begin where the previous one ended is a detected drop.
- **A counter of its own for the conflated stream.** Where the protocol cannot carry a range, the
  conflated stream gets its own counter in its own space, published beside the raw one, with a sentence
  saying which counter gap detection runs on and what the other is not valid for. A counter still needs
  the join to the raw space written into the specification, because a snapshot names a point in one
  space and the consumer has to place it in the other.

Both need a link a consumer can check against the message they last received, or a dropped conflated
message is undetectable. Both forbid the same thing: a conflated message published under one raw
sequence number with the rest of the range unaccounted for. The raw stream's gap detector then reports
contiguity across a hole, to every consumer at once, including those who never subscribed to the
conflated stream. A raw sequence number is never reused for different content, and the raw space is
never renumbered to close the hole conflation made.

## The encoder test

For each message type, ask whether applying the same message twice leaves the book in the same state as
applying it once. If yes for every field, the type is state-encoded and conflatable. If any field is a
change rather than a value, the type is delta-encoded and it is not.
