# One writer: fencing the second engine out of the record

The single-writer principle is a correctness property here rather than a throughput trick, and the mechanism
that enforces it has to live at the resource that holds the record, not at the service that hands out the
token.

## Failover and single-writer authority

The single-writer principle is a correctness property here, not a throughput trick. Two engines matching the
same book for even 200 ms produce **two different histories of a record that nothing external can adjudicate**:
you are the oracle, so there is no reconciliation that resolves the fork.

- **Epoch every acquisition, not every failover.** A coarse epoch lets a delayed control message land on the
  next unit of work; that is the KAFKA-17754 mechanism, where infrequent producer-epoch bumps plus unordered
  `EndTxn` across connections gave "aborted reads, lost writes, and torn transactions", fixed by bumping the
  epoch on every transaction.
- **Fence at the resource, not at the lock service.** Kleppmann's formulation is the load-bearing half:
  *"the storage server remembers that it has already processed a write with a higher token number… and so it
  rejects the request."* Concretely: the journal writer stamps `epoch` in every segment header and the storage
  layer (or the journal daemon, or a `WHERE epoch >= $1` guarded append) **rejects** an append carrying a lower
  epoch. A monotonic token the resource does not check is decoration. A lease with a TTL is not a fence: a GC
  pause, a packet delay or a clock jump outlives it.
- **Do not ask the stale writer to stop.** By the time you can ask it, it is paused; by the time it wakes, it
  has already appended. Kafka's `InitPidRequest` is the shape to copy: it *"Bumps up the epoch of the PID, so
  that any previous zombie instance of the producer is fenced off"*, with the invariant *"Exactly one active
  producer with a given TransactionalId."*
- **Replicate whatever the followers must replay, which for a deterministic core is the input stream.** LMAX
  multicasts the journaled input stream to followers, which derive identical state by applying it; a consensus
  group replicating its log achieves the same thing with the commit point moved into the protocol. Either way a
  promoted follower continues the same sequence space rather than renumbering, because renumbering re-issues
  identifiers consumers already booked.
- **Do not order failover by wall clock.** Ordering financial events by timestamps taken on different hosts is
  a data-loss mechanism; the systems that do order by time buy it explicitly, with a purpose-built clock or by
  refusing node clocks outright in favour of leader-based timestamping.

One consequence is worth stating separately, because it is the reason this section is not a throughput
discussion. When authority is EXTERNAL there is always a reconciliation that resolves a fork: you ask the
counterparty what it thinks happened, and its answer wins. Here there is no such call to make. Two writers
that both extended the record produce two histories, both internally consistent, both signed by your own
process, and nothing outside the building can say which one the participants hold. That is why the fence has
to be enforced by the resource rather than agreed between the writers, and why a promoted follower continues
the sequence space instead of starting a new one.
