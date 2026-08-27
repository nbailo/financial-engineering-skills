# The identifier ownership matrix

These identifiers are distinct roles, each with its own counter, and **each with exactly one owner named in
the design**. The roles are what matters; a design with more of them because it separates two things this one
merged is not wrong for the count. The owners are not all the matcher: the matcher owns the identity of what it decided,
and a sequencer, a session layer or a publisher legitimately owns the rest. What binds is that the owner is
named, that no identifier is derived from another, and that any identifier a replay must reproduce is
generated from journaled state inside the deterministic region. The collapse is only ever visible in numbers
you have already published, which is what makes it unrecoverable.

## Contents

- One owner each, and the count is not the point
- The identifier roles, and where each is assigned: the matrix, and the core that assigns no wire sequence

## One owner each, and the count is not the point

**Each identifier has its own definition, its own name in the code, and exactly one named owner**, and none is
derived from another by reuse or by cast, because collapsing two is unrecoverable once consumers have booked
them. Count them if you like; a design that has six because it separates two things this one merged is not
wrong for having six. What is wrong is an identifier with no owner, or two roles sharing one counter.

The **command sequence** is one inbound command's place in the durable input log, internal to replay. The
**engine event sequence** is the order the core made its decisions, which is a different thing from the order
commands arrived and from the order anything was published.
The **match id** is one crossing event, carried identically by both sides and by any later correction of it.
The **execution id** is one side's leg of one match, the record a participant books. The **private session
sequence** numbers what one order-entry session was told, and is visible to that session alone. The **public
feed sequence** is the position in the stream you publish, which every consumer gap-detects and dedupes on. The table
below gives each role with the counter it comes from.

## The identifier roles, and where each is assigned

Every identifier a replay has to reproduce is an **input-derived output**: generated inside the deterministic
region, from journaled state only. That covers the match id, the execution id and anything else the emitted
sequence carries. An identifier assigned outside that region, a session counter on an outbound path being the
usual one, is not exempt from having an owner; it is exempt only from being reproduced by the core's replay,
and its own owner says how it is recovered. **Wire sequencing stays at the publisher boundary**: the number a
consumer gap-detects on is assigned where the bytes leave, by the publisher, and pulling it into the core
couples the core's replay to a transport concern that has nothing to do with matching. They are **distinct**,
and each has its own counter. Collapsing any two is unrecoverable once consumers have booked them, because the collapse is only
visible in the numbers you already published.

| Identifier | Scope | Owner, and where it assigns | What breaks if it doubles as another |
|---|---|---|---|
| **command sequence** | the engine's input log | by the sequencer, before the book is touched | replay loses the total order over commands, which is the thing replay is |
| **engine event sequence** | the core's own output log, in decision order | in the core, as each decision is journaled | the order decisions were made stops being recoverable, and a replay can reproduce a different interleaving that is equally consistent with the inputs |
| **match id** | one crossing event | in the core, once per match | the two sides of a trade cannot be paired, and a correction cannot name what it corrects |
| **execution id** | one side's leg of one match | in the core, once per leg | a participant cannot dedupe its own fills, and a bust cannot address one leg |
| **private session sequence** | one order-entry session | on that session's outbound path | one participant's gap detection fires on traffic that was never addressed to it |
| **public feed sequence** | one market-data stream | on the publish path, read from the committed record | every consumer's gap detection and dedupe key is wrong at the same moment |

The last two are the pair most often collapsed, because both read as "the sequence number". They advance at
different rates by construction: a session numbers what that participant was told, the feed numbers what
every consumer was told, and neither is a function of the other. Note what the Owner column does and does not
say. Nothing here requires one component to assign them all; it requires each role to have exactly one
component that does, written down, so that two components never advance the same counter and no counter is
left without an owner when a deployment is split.

```rust
// inside the core, after the command is journaled and dequeued
struct Ids { next_cmd: u64, next_event: u64, next_match: u64, next_exec: u64 }
//   The core assigns NO wire sequence. The publisher owns that counter at the publish
//   boundary, and the session layer owns one counter per live session, keyed by session id
//   and advanced only by messages sent to that session. Neither is derived from the other.

fn on_command(ids: &mut Ids, book: &mut Book, cmd: &Command, now: Nanos) -> Vec<Event> {
    let cmd_seq = ids.next_cmd; ids.next_cmd += 1;      // ordering, not an outbound identity
    let mut out = vec![];
    match validate(cmd, book) {
        Err(reason) => {
            // a reject is a journaled engine event and takes an event sequence like any other
            let event_seq = ids.next_event; ids.next_event += 1;
            out.push(Event::Rejected { event_seq, cmd_seq, cloid: cmd.cloid(), reason });
        }
        Ok(()) => {
            // ONE command can cross SEVERAL resting orders, and each crossing is its own
            // match. Allocating the match id before this loop gives every crossing the same
            // id, and a correction can then no longer name which one it corrects.
            for crossing in book.match_order(cmd, now) {
                let match_id = ids.next_match; ids.next_match += 1;   // one per crossing

                // a crossing has one leg per participant, and a leg is the record that
                // participant books and dedupes on, so each leg takes its own execution id
                for leg in crossing.legs() {           // aggressor and resting side
                    let exec_id  = ids.next_exec;  ids.next_exec  += 1;
                    let event_seq = ids.next_event; ids.next_event += 1;   // journal order
                    out.push(Event::Execution { event_seq, cmd_seq, match_id, exec_id, ..leg });
                }
            }
        }
    }
    out                        // no wire sequence anywhere above: the publisher owns that
}
```

- **Whether a reject consumes a sequence number is a protocol decision, not a correctness invariant**, and
  **which counter it consumes is part of that decision**. A reject is normally a private-session message
  and no business of the public feed; the example above gives it a command sequence and no feed sequence.
  What is invariant is that the rule is published, total and reproducible under replay, since a consumer can
  only distinguish a number you never assigned from a message it lost if you told it the convention. Pin it
  with a replay test named for the choice. Gap-freedom is also easy to assert in a design note and easy to
  lose on the wire: a single `let _ = tx.send(ev)` that can drop an event makes the claim true of the
  generator and false of the transport.
- **Recover a counter from its owner's durable record, never from a table that owner also writes.** For the
  counters inside the core, that is replay: after a snapshot at journal position P, `Ids` comes from the
  snapshot and is advanced by replaying P+1..end under the reducer in force for that range. Reading a
  `MAX(exec_id)` from a table you also write gives an ID space that diverges the moment a transaction rolls
  back. Where the design persists decisions immutably as immutable records, the identifiers come back with
  those records and the replay checks them rather than mints them. A counter owned outside the core follows the
  same rule against its owner's own durable record, and that owner states how it recovers.
- **Execution identity is assigned where the matching decision is made, not in the gateway.** A
  gateway-assigned sequence orders arrivals; it does not order *executions*, and executions are what the
  replay must reproduce. A session or feed counter may live in the component that owns that wire, since it
  numbers what that wire carried, and the design names that component rather than assuming the matcher.
