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
- **Recoverable under a declared contract.** The stream states which of the two contracts below it offers,
  and a consumer can check continuity within that contract. The two answer different questions, and
  neither is a substitute for the other.

Miss one condition and the corruption is silent: the transport sequence stays contiguous, because you
dropped the message before it was numbered, so no gap detector fires. The book is wrong until
the next snapshot, and every decision taken against it was taken against a state that never existed.

## The two contracts, and what each one actually promises

Declare which one this stream offers. They are not two spellings of one idea.

- **A self-contained state feed.** The stream has its own sequence space and its own snapshot, and it is
  continuous in that same space. A consumer recovers by re-reading state: take the snapshot, apply the
  conflated messages after it, and the book is right. It makes **no claim about which raw updates any
  message represents**, and it does not have to: the consumer never reasons about the raw stream. Do not
  require this feed to identify every raw update. That is a different contract, and demanding it of a
  state feed is asking for a field the design has no use for.
- **A raw-accounting feed.** The message names the raw coverage it accounts for, as a first and last raw
  sequence number or whatever the protocol provides, and the specification gives a checkable continuity or
  predecessor rule over that coverage. A consumer can then place every raw update: the coverage tiles the
  raw space, and coverage that does not continue from the previous message is a detected drop.

**A counter over the conflated stream proves only that no conflated message was lost.** That is worth
having, and it is all it is. It says nothing about which raw updates a message represents, so it does not
turn a state feed into a raw-accounting one, and publishing a raw range and a counter together is not
itself the obligation: the obligation is the contract you declared and the continuity rule that checks it.
Both contracts forbid the same thing: a conflated message published under one raw sequence number with the
rest of its coverage unaccounted for. The raw stream's gap detector then reports
contiguity across a hole, to every consumer at once, including those who never subscribed to the
conflated stream. A raw sequence number is never reused for different content, and the raw space is
never renumbered to close the hole conflation made.

## The encoder test

For each message type, ask whether applying the same message twice leaves the book in the same state as
applying it once. If yes for every field, the type is state-encoded and conflatable. If any field is a
change rather than a value, the type is delta-encoded and it is not.
