# The write order: journal, flush, commit, then publish from the committed record

One write path that satisfies the durable-ordered-input property, in the order the steps have to occur.
The ordering binds; that the record is a local journal and the queue a table is this example's choice.

**The journal, outbox and relay below are one worked implementation, not the only correct one.** What binds is
the property: the command is durable and ordered before the state it changes is touched, the state change and
the obligations it creates commit atomically, the publisher reads committed state, and exactly one writer may
extend the record. A replicated log with quorum acknowledgement, or a consensus group whose commit point is
that log, meets the same property by a different mechanism, and every mechanical detail here should be read as
one way to satisfy it rather than as a required schema.

## Ordering and flush

SKILL.md's *authoritative state is reproducible from durable, ordered inputs* states the property, and the
path below is one way to satisfy it.

```
1  seq = sequencer.next()                      // inside the core; one of five counters
2  journal.append(Record{seq, cmd_bytes, crc32c(len ‖ cmd_bytes)})
   journal.flush()                             // durable BEFORE the book is touched
3  tx = db.begin()
     book.apply(cmd)                           // mutation
     tx.insert_executions(execs)               // resulting executions
     tx.insert_outbox(events_for(execs))       // outbound feed rows, same transaction
   tx.commit()
4  relay: SELECT … FROM outbox WHERE published_at IS NULL ORDER BY seq
        → send(); BIND AND CHECK THE RESULT; then UPDATE published_at
```

Load-bearing details:

- **Step 2 flushes.** An append into a page-cache buffer is not a journal. And a flush failure is **fatal to
  the process, not retryable**: on Linux before 4.13 a writeback error marked buffers clean, so a retried
  `fsync()` returned success with the data gone; from 4.13, `fsync()` only reports writeback errors that
  occurred after the current `open()`, so a close-and-reopen hides the earlier error. PostgreSQL's answer was
  to **PANIC on `fsync()` failure** (PostgreSQL wiki, *Fsync Errors*, 2018). Any engine that catches a flush
  error and continues has a silent-corruption path.
- **Each record is self-describing**: length prefix, payload, checksum. A crash mid-append leaves a torn tail;
  recovery truncates at the last record whose length and checksum both validate, and replays nothing beyond it.
  Without the checksum you cannot distinguish a torn tail from a corrupt middle.
- **Step 3 is one commit.** The book mutation and the executions it produced are one atomic unit. Two
  transactions is the dual-write anti-pattern with money in it.
- **Step 4 reads committed state.** The publisher must never be handed the in-memory `execs` vector directly,
  because then a crash between commit and publish loses events that the durable record says happened.

The reason to write the order down as an order, rather than as a set of things that all have to happen, is
that every one of the four steps is individually defensible in the wrong sequence. Journaling after the book
moves still gives you a journal. Committing the executions in a second transaction still commits them.
Publishing from the vector you already have in hand is faster and looks identical in every test where nothing
crashes. What the sequence buys is that each crash boundary has exactly one answer, and the crash-point table
elsewhere in this skill is that answer enumerated. If the ordering is not written down, the answer at each
boundary depends on which of the four steps happened to be reached, which is not a design.
