# Conflation and backpressure

What a publisher is allowed to drop, and what it owes a consumer once it has dropped something. A slow
consumer forces a choice, and the choice is a correctness decision rather than a performance one: two of the
four available responses leave the consumer able to reconstruct a correct book, one leaves them provably
unable to, and one back-pressures the matcher. This file decides which is which, and states what has to be
published alongside a conflated stream so that a consumer can still detect loss.

## Contents

- The four responses to a slow consumer, and what each one leaves reconstructable
- State-encoded and delta-encoded feeds: the one property that decides whether conflation is legal
- The covered-range identifier, and why a contiguous sequence is not a gap check on a conflated feed
- What conflation destroys even when it is legal: trades, message counts, ordering signals
- Load-dependent conflation, and why it makes the feed's semantics a function of load
- Publisher-side mechanics: bounded queues, drop policy, disconnect and re-entry
- What to publish, and the tests that hold the policy in place

---

## The four responses, and what each leaves reconstructable

| Strategy | What the consumer can still reconstruct | Legitimate when | Must be published |
|---|---|---|---|
| Block the publisher | Everything | Never on a feed: it back-pressures the matching engine | n/a |
| Disconnect the consumer | Nothing, until they recover | Always acceptable: a gap is visible and recoverable | The disconnect reason and the recovery entry point |
| Conflate **state** (replace the pending update for a key with its latest absolute value) | The book at each observed instant, **not** the path between instants | The feed is absolute-quantity per key | That intermediate states are unobservable, plus a covered-range identifier per event |
| Conflate **deltas** (drop some) | Nothing correct, ever | Never | n/a |

Blocking is disqualified for a reason that has nothing to do with the consumer. A publisher that blocks on a
full socket buffer applies backpressure through the fan-out into the sequencer and from there into the
matcher, so one slow subscriber changes the rate at which orders are matched for everybody. The slow consumer
becomes a participant in price formation. Disconnecting is the honest answer: the consumer loses the stream,
knows it lost the stream, and re-enters through a recovery path you already had to build.

## The property that decides whether conflation is legal

**Conflation is safe only on a state-encoded feed.** A state-encoded event carries the absolute value for a
key, so the latest event for that key is sufficient and every earlier one is redundant. A delta-encoded event
carries a change, so every event is load-bearing and dropping one corrupts the book permanently.

The corruption is undetectable from the consumer side, which is what makes it the worst failure in this file.
The transport sequence is still contiguous, because you dropped the message before it was numbered, or
collapsed two numbered messages into one and renumbered. No gap detector fires. The book is simply wrong,
silently, until the next reset or snapshot, and every decision taken against it in between was taken against
a state that never existed.

The test to run against your own encoder is mechanical: for each message type, ask whether applying the same
message twice leaves the book in the same state as applying it once. If yes for every field, the type is
state-encoded and conflatable. If any field is a change rather than a value, the type is delta-encoded and it
is not.

## The covered-range identifier

A conflated event has to say which updates it stands for, or a consumer cannot join it to a snapshot and
cannot detect a dropped event at all. Binance's depth stream is the worked example of doing this correctly:
"The data in each event is the absolute quantity for a price level", each event carries the range of update
ids it covers as a first and last id, and the futures stream adds a third field carrying the previous event's
last id.

The third field exists precisely because conflation defeats the naive gap check. With only a first and last
id per event, a consumer checks that this event's first id follows the previous event's last id. That check
passes across a server-side coalescing boundary even when the coalescing dropped an event, because the
surviving event's range was widened to cover what was dropped. Carrying the previous event's last id
explicitly turns the check into an assertion about the publisher's own chain rather than about arithmetic the
publisher can widen at will.

Three consequences for your own feed:

- Publish the covered range on every conflated event, not only on the ones where conflation actually
  happened. A field that appears sometimes is a field consumers do not code for.
- Publish a chain link that a consumer can check against the previous event they received, not only a range
  the publisher computes.
- State the snapshot join in the same terms. A snapshot whose as-of point is expressed in a counter the
  conflated stream does not carry cannot be joined to it at all.

## What conflation destroys even when it is legal

- **Trades are never conflatable.** Each print is an economic fact with its own identity, and collapsing two
  prints into one destroys volume, VWAP and every trade-based signal derived from the feed. Conflate quotes
  and levels; pass prints through untouched. A stream that carries both needs the conflation boundary drawn
  inside it, and the boundary published.
- **A conflated feed has last-value-cache semantics, and that changes what it is.** Anything derived from the
  *count* or the *order* of updates stops being valid: message-rate signals, queue-position estimates,
  order-level event reconstruction. Consumers build those things on any feed that looks like an event stream,
  so if yours is not one, the specification has to say so in those words.
- **The path between two observed states is gone and cannot be recovered.** A consumer asking "did the book
  ever cross" or "how long did that level rest" is asking a question the conflated feed cannot answer. If the
  answer matters to anyone, publish an unconflated stream alongside and let them choose.

## Load-dependent conflation

If depth is conflated under load and not otherwise, the feed's semantics are a function of load. A consumer's
backtest built on quiet-period captures does not describe the feed they trade against at the open, and the
difference appears exactly when their exposure is largest. Two acceptable designs, and no third:

1. The stream is always conflated, at a stated interval, and every event carries its covered range.
2. The stream is never conflated, and a consumer who cannot keep up is disconnected.

A hybrid is acceptable only if the transition is itself an event on the stream, carrying the interval that is
now in force, so a consumer can see the semantics change rather than infer it from message rates.

## Publisher-side mechanics

The queue in front of each consumer socket is bounded, and the bound is a published number. On overflow the
policy is one of the two legal responses above, chosen per stream and not per incident:

- On a state-encoded stream, coalesce into the pending entry for the key. The queue holds at most one pending
  update per key, which is what makes the memory bound hold under any consumer speed.
- On a delta-encoded stream, disconnect, with a reason code the consumer can log, and let them re-enter
  through recovery.

Two publisher-side failures to look for in review. The first is a coalescing map keyed on something coarser
than the update's own key, which merges updates that were not about the same thing. The second is a drop
policy that fires per message rather than per key, which is delta dropping wearing a conflation label: the
tell is a queue that discards the oldest entry on overflow rather than replacing a pending entry.

Backpressure must also be measured. The depth of each consumer queue, the number of coalesce events and the
number of disconnects are operational figures a publisher needs, and the first two are the only evidence that
a conflation policy is behaving as documented.

## What to publish, and the tests

Publish the conflation policy per stream: whether the stream is conflated at all, whether it is state or delta
encoded, the interval or trigger, the covered-range fields, and the fact that intermediate states are
unobservable. Publish the disconnect reason codes and the recovery entry point for each.

Three tests hold this in place:

- **Encoding test.** For every message type on a conflatable stream, assert that applying the message twice
  leaves the same state as applying it once. A new field that carries a delta fails this test on the day it
  is added, which is the day to catch it.
- **Coalescing test.** Drive the publisher with a burst that exceeds the queue bound, then assert the
  consumer's reconstructed book equals the publisher's book, and that every event received carries a covered
  range that chains to the previous one.
- **Trade passthrough test.** Under the same burst, assert that the count and the sum of quantities of trade
  prints received equals the count and sum emitted. Volume is the quantity that conflation is most likely to
  silently reduce.
