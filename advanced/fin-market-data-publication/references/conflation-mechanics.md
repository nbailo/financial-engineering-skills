# Conflated events, the slow consumer, and the bound that has to hold globally

> **Provenance**
> provider: Binance, cited for one worked example of a covered-range field
> surface: spot WebSocket diff depth stream
> version: the documentation served at the URL below on 2026-08-25
> verified_at: 2026-08-25
> sources: https://developers.binance.com/docs/binance-spot-api-docs/web-socket-streams
> verified: the field descriptions quoted below, `U` as "First update ID in event" and `u` as "Final update ID
> in event", and that the stream is described as depth updates "used to locally manage an order book".
> unverified: the sentence describing each event as the absolute quantity for a price level, and the futures
> stream's third field carrying the previous event's final update id. Neither could be retrieved in this pass,
> because the futures documentation URL redirected to the docs index, so both are labelled inline below.
> Nothing here is a claim about how any Binance stream behaves today beyond the two quoted field descriptions.
> revalidate_when: the diff depth payload gains or renames a covered-range field, or the futures chain-link
> field is confirmed or removed.

What a publisher owes a consumer once it has dropped something, and what to do with a subscriber that cannot
keep up. A conflated stream stays contiguous while it loses events, so the covered range and the chain link
are the only things that let a consumer detect a drop or join a snapshot. Read this when a slow subscriber is
dropped or blocked, when an outbound queue bound is chosen, or when a coalesced event's fields are designed.
Whether the stream may be conflated at all is decided in this skill's conflation legality reference.

## Contents

- What a conflated message must say about the raw stream
- The covered-range identifier, and why a contiguous sequence is not a gap check on a conflated feed
- What conflation destroys even when it is legal: trades, message counts, ordering signals
- Load-dependent conflation, and why it makes the feed's semantics a function of load
- The four responses to a slow consumer, and what each one leaves reconstructable
- Publisher-side mechanics: the global memory bound, drop policy, disconnect and re-entry
- What to publish, and the tests that hold the policy in place

---

## What a conflated message must say about the raw stream

A conflated stream is a second feed, not a cheaper rendering of the first, and the property that makes it
usable is that a consumer can tell exactly which raw updates a conflated message accounts for. Two
mechanisms carry that property, and this skill's conflation legality reference is where the choice between
them is made: a covered raw sequence range on the message, or a counter of the conflated stream's own
published beside the raw one. Where the protocol has room for the range, the range alone is enough and a
second counter is not owed.

Neither mechanism permits a conflated message published under one raw sequence number with the rest of the
range unaccounted for. Never reuse a raw sequence number for different content, never renumber the raw
space to close the hole conflation made, and never let one raw number stand for a range it does not name.
Each of those turns the raw feed's gap detector into an instrument that reports contiguity across a hole,
and it reports it to every consumer at once, including the ones that never subscribed to the conflated
stream.

Where you do publish a second counter, publishing both is the whole obligation, plus one sentence saying
which one gap detection runs on and what the other is not valid for. CME does the negative half of this
explicitly on its conflated TCP group, which sends the per-instrument report sequence as a literal zero
rather than as a number that looks usable; the detail is in the CME reference, and the shape is what to
copy. A consumer that can see the counter is absent stops; a consumer handed a plausible wrong number does
not.

## The covered-range identifier

A conflated event has to say which updates it stands for, or a consumer cannot join it to a snapshot and
cannot detect a dropped event at all. Binance's spot diff depth stream is the worked example of the range
half: its payload carries `U`, documented as "First update ID in event", and `u`, "Final update ID in event",
on a stream described as depth updates "used to locally manage an order book". The chain half, a field
carrying the previous event's final update id, exists on the futures diff depth stream, and that field and the
sentence describing each event as the absolute quantity for a price level were **not re-verified in this
pass**, because the futures documentation URL redirected to the docs index on 2026-08-25. Treat the futures
detail as a shape to look for and check it before copying it.

The chain field exists because conflation defeats the naive gap check, and that argument stands on its own.
With only a first and last id per event, a consumer checks that this event's first id follows the previous
event's last id. That check passes across a server-side coalescing boundary even when the coalescing dropped
an event, because the surviving event's range was widened to cover what was dropped. Carrying the previous
event's last id explicitly turns the check into an assertion about the publisher's own chain rather than about
arithmetic the publisher can widen at will.

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

## The four responses, and what each leaves reconstructable

| Strategy | What the consumer can still reconstruct | Legitimate when | Must be published |
|---|---|---|---|
| Block the publisher | Everything | Only where a bounded buffer stops the backpressure before it reaches the sequencer or the matcher | The bound, and what happens when it is reached |
| Disconnect the consumer | Nothing, until they recover | The default, provided the recovery path can absorb the reconnects the policy causes | The disconnect reason and the recovery entry point |
| Conflate **state** (replace the pending update for a key with its latest absolute value) | The book at each observed instant, **not** the path between instants | The feed is absolute-quantity per key | That intermediate states are unobservable, plus a covered-range identifier per event |
| Conflate **deltas** (drop some) | Nothing correct, ever | Never | n/a |

Blocking is disqualified by where the backpressure ends, not by anything about the consumer. A publisher that
blocks on a full socket buffer applies backpressure through the fan-out into the sequencer and from there into
the matcher, so one slow subscriber changes the rate at which orders are matched for everybody and becomes a
participant in price formation. That is the case to refuse. Where a bounded buffer terminates the chain before
the sequencer, an archive, a replay sink or a downstream fan-out process being the usual cases, blocking is a
legitimate choice and the bound is the thing to publish. Trace the chain to its end before ruling either way,
because "blocking never works" is a claim about a topology rather than about blocking.

Disconnecting is the honest default: the consumer loses the stream, knows it lost the stream, and re-enters
through a recovery path you already had to build. It is not free, and calling it always correct hides the
cost. Disconnects correlate, because the burst that made one consumer slow made all of them slow, so the
policy has to be sized against the reconnect storm it produces rather than against one reconnect. A recovery
path that cannot absorb that storm converts a slow-consumer incident into a recovery-path incident at the
moment of highest load. And where a consumer is entitled to the feed by rule or contract, the entitlement does
not lapse because they were slow, so the answer there is a documented degraded mode rather than silence.

## Publisher-side mechanics

The queue in front of each consumer socket is bounded, and the bound is a published number. On overflow the
policy is one of the two legal responses above, chosen per stream and not per incident:

- On a state-encoded stream, coalesce into the pending entry for the key, so the queue holds at most one
  pending update per key. That bounds a key and nothing more. The bound that has to hold is GLOBAL: keys
  times subscribers, plus everything else the publisher is holding, against one budget for the process, with
  a stated policy for reaching it. Publish that number. A per-key bound looks like a memory bound right up to
  the day you list more instruments or one subscriber takes every symbol, and on that day it is not one.
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

Publish the conflation policy per stream: whether the stream is conflated at all, whether it is state or
delta encoded, the interval or trigger, the queue bound, whether a conflated message carries a covered raw
range or its own counter, the chain link, and the fact that intermediate states are unobservable. Publish
the disconnect reason codes, the recovery entry point for each, and, where blocking is the chosen policy on
a stream, the buffer that terminates the backpressure and what happens when it fills.

Three tests hold this in place:

- **Encoding test.** For every message type on a conflatable stream, assert that applying the message twice
  leaves the same state as applying it once. A new field that carries a delta fails this test on the day it
  is added, which is the day to catch it.
- **Raw-stream equivalence test.** Publish one input twice, raw and conflated, under a burst that exceeds the
  queue bound. Replay both into the same consumer implementation and assert that at every covered-range
  boundary the state built from the conflated stream equals the state the raw stream reached at that same
  point, and that every conflated event chains to the previous one. Comparing the conflated consumer against
  the publisher's own book is the weaker test, and it passes while the two are wrong together, because both
  are projections of the same code path. The raw stream is the only comparison that is not.
- **Trade passthrough test.** Under the same burst, assert that the count and the sum of quantities of trade
  prints received equals the count and sum emitted. Volume is the quantity that conflation is most likely to
  silently reduce.
