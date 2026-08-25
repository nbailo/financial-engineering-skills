# Five identifiers, five counters, and what collapsing any two costs

Every identifier other systems consume because you assigned it is an input-derived output: generated inside
the deterministic region, from journaled state only, so replay reproduces it. There are five, they are
distinct, and each has its own counter. The collapse is only ever visible in numbers you have already
published, which is what makes it unrecoverable.

## You mint five distinct identifiers

**You mint five distinct identifiers: five counters, five definitions, five names in the code**, and none is
derived from another by reuse or by cast, because collapsing two is unrecoverable once consumers have booked
them. The **command sequence** is one inbound command's place in the durable input log, internal to replay.
The **match id** is one crossing event, carried identically by both sides and by any later correction of it.
The **execution id** is one side's leg of one match, the record a participant books. The **private session
sequence** numbers what one order-entry session was told, and is visible to that session alone. The **public
feed sequence** is the position in the stream you publish, which every consumer gap-detects and dedupes on. The table
below gives the five with the counter each comes from.

## The five identifiers, and where each is assigned

Every identifier other systems consume because you assigned it is an **input-derived output**: generated
inside the deterministic region, from journaled state only, so replay reproduces it. There are **five**, they
are **distinct**, and each has its own counter. Collapsing any two is unrecoverable once consumers have
booked them, because the collapse is only visible in the numbers you already published.

| Identifier | Scope | Assigned | What breaks if it doubles as another |
|---|---|---|---|
| **command sequence** | the engine's input log | by the sequencer, before the book is touched | replay loses the total order over commands, which is the thing replay is |
| **match id** | one crossing event | in the core, once per match | the two sides of a trade cannot be paired, and a correction cannot name what it corrects |
| **execution id** | one side's leg of one match | in the core, once per leg | a participant cannot dedupe its own fills, and a bust cannot address one leg |
| **private session sequence** | one order-entry session | on that session's outbound path | one participant's gap detection fires on traffic that was never addressed to it |
| **public feed sequence** | one market-data stream | on the publish path, read from the committed record | every consumer's gap detection and dedupe key is wrong at the same moment |

The last two are the pair most often collapsed, because both read as "the sequence number". They advance at
different rates by construction: a session numbers what that participant was told, the feed numbers what
every consumer was told, and neither is a function of the other.

```rust
// inside the core, after the command is journaled and dequeued
struct Ids { next_cmd: u64, next_match: u64, next_exec: u64, next_feed: u64 }
//   plus one next_session counter per live session, keyed by session id, advanced only by
//   messages sent to that session; it is never derived from next_feed.

fn on_command(ids: &mut Ids, book: &mut Book, cmd: &Command, now: Nanos) -> Vec<Event> {
    let cmd_seq = ids.next_cmd; ids.next_cmd += 1;      // ordering, not an outbound identity
    match validate(cmd, book) {
        Err(reason) => vec![Event::Rejected { cmd_seq, cloid: cmd.cloid(), reason }],
        Ok(()) => {
            let mut out = vec![];
            let match_no = ids.next_match; ids.next_match += 1;   // ONE per crossing event
            for fill in book.match_order(cmd, now) {
                let exec_id = ids.next_exec; ids.next_exec += 1;  // one per leg
                out.push(Event::Execution { feed_seq: { let s = ids.next_feed;
                                                        ids.next_feed += 1; s },
                                            cmd_seq, match_no, exec_id, ..fill });
            }
            out
        }
    }
}
```

- **Whether a reject consumes a sequence number is a protocol decision, not a correctness invariant**, and
  **which of the five it consumes is part of that decision**. A reject is normally a private-session message
  and no business of the public feed; the example above gives it a command sequence and no feed sequence.
  What is invariant is that the rule is published, total and reproducible under replay, since a consumer can
  only distinguish a number you never assigned from a message it lost if you told it the convention. Pin it
  with a replay test named for the choice. Gap-freedom is also easy to assert in a design note and easy to
  lose on the wire: a single `let _ = tx.send(ev)` that can drop an event makes the claim true of the
  generator and false of the transport.
- **Recover all five counters by replay, never from a counter table.** After a snapshot at journal position
  P, `Ids` comes from the snapshot and is advanced by replaying P+1..end under the reducer in force for that
  range. Reading a `MAX(exec_id)` from a table you also write gives an ID space that diverges the
  moment a transaction rolls back. Where the design persists decisions immutably as immutable records, the
  identifiers come back with those records and the replay checks them rather than mints them.
- **The generator lives beside the matcher, not in the gateway.** A gateway-assigned sequence orders arrivals;
  it does not order *executions*, and executions are what the replay must reproduce.
