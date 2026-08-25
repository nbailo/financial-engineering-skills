# Assertions on the publish path

The checks that must survive into the shipped binary, because they run on the last hop before a number leaves
your process and becomes somebody else's book. A depth figure, a top-of-book quote and a session volume are
each an aggregate maintained by increments and decrements over a long-running process, and the failure mode of
every such aggregate is drift. Drift that reaches the wire is not recoverable: the consumer applied it, traded
on it, and has no independent source to check it against.

## Contents

- The default shape of a hand-written price level, and why it is wrong on this path
- Two independent reasons a debug assertion is not in the shipped binary
- The checked form, and the error type it needs
- Breach policy: freeze scope, the ban on clamping, and saturation as a published event
- The assertion table: what to assert on the emit path and the halt level each implies
- Crossed books, and why one line of arithmetic is the whole check
- Where the checks live, and how to test that they fire

---

## The default shape, and why it is wrong here

```rust
struct PriceLevel { price: i64, total_qty: u64, orders: VecDeque<OrderId> }

impl PriceLevel {
    fn fill(&mut self, qty: u64) {
        debug_assert!(qty <= self.total_qty);   // "qty <= total_qty by construction"
        self.total_qty -= qty;                  // release: wraps to ~1.8e19, published as depth
    }
}
```

This is the shape a competent engineer writes by default, and it is wrong specifically because the aggregate
leaves the process. The comment is the tell. Naming the failure correctly and then answering it with a
debug-only check is the standard mistake, and the rationalisation is always the same sentence: the quantity
cannot exceed the aggregate by construction. It was by construction. Drift is the bug you are hunting, and a
check that only runs when the bug is absent is not a check.

## Two independent reasons the guard is not in the shipped binary

1. **A debug assertion compiles to nothing unless debug assertions are enabled.** They are not enabled in a
   release profile by default, and a project can disable them everywhere. nautilus_trader sets
   `debug-assertions = false` in both its development and release profiles, so the three debug assertions in
   its whole order model are compiled out in every build it ships.
2. **Release profiles disable integer overflow checks.** In Rust's default release profile the subtraction
   wraps rather than panicking, so an unsigned aggregate that goes below zero becomes a number near `2^64` and
   is published as depth. The two reasons are independent: fixing the profile does not fix the assertion, and
   enabling assertions does not fix the arithmetic.

The consequence to check in review is narrow and mechanical. Any arithmetic on an aggregate that leaves the
process must be explicit about what happens on underflow, in the profile that ships, and the answer must not
depend on a build flag.

## The checked form

```rust
#[derive(Debug)]
pub struct DepthBreach { pub price: i64, pub have: u64, pub take: u64 }

impl PriceLevel {
    fn fill(&mut self, qty: u64) -> Result<u64, DepthBreach> {
        let remaining = self.total_qty
            .checked_sub(qty)
            .ok_or(DepthBreach { price: self.price, have: self.total_qty, take: qty })?;
        self.total_qty = remaining;
        Ok(remaining)
    }
}
```

The error carries the three numbers an operator needs to decide what happened: which key, what the aggregate
held, and what was taken from it. An error type that carries only a message forces the investigation to start
from a log line, and the log line is written after the state has already moved on.

## Breach policy

On an error from an emit-path check, three rules, in this order:

- **Halt the transformation at the smallest scope that contains it.** Freeze the affected instrument, not the
  channel and not the process. A process-wide halt on a single bad level converts one wrong number into an
  outage for every consumer of every instrument.
- **Do not publish the level.** A frozen instrument publishing nothing is a condition a consumer can detect
  through the heartbeat and the staleness gate. A frozen instrument publishing a wrong number is not.
- **Never clamp to zero.** A clamp is fabricated depth with no exception attached, and it is the single change
  most likely to be proposed as the fix, because it makes the symptom disappear.

Saturating instead of checking is a legitimate choice in exactly one situation: where a panic would abandon
in-flight obligations, so continuing with a defined value is safer than stopping. nautilus_trader uses a
saturating subtraction at `orders/mod.rs:1366` for that reason. Where you saturate, the saturation is an event
on the same feed and in the same operational record, because a silent saturation is a fabricated number with
the exception thrown away.

The opposite choice is correct where nothing is in flight. TigerBeetle keeps assertions live in release, at a
density of roughly one per 10.6 lines of state machine, and overflow-checks every accumulator before mutating
it: "Assertions downgrade catastrophic correctness bugs into liveness bugs." A liveness bug is visible. A
debug assertion on a published aggregate is neither of the two: it is a correctness bug with the detector
compiled out.

## The assertion table

| Assertion | Expression | On breach |
|---|---|---|
| Level conservation | `published_qty(level) == Σ leaves_qty of resting orders at that level`, recomputed rather than accumulated | Freeze the instrument; do not publish |
| No underflow | a checked subtraction on every aggregate that leaves the process | Freeze the instrument; do not publish |
| Not crossed | `best_bid <= best_ask` on every top-of-book publish | Freeze the instrument; do not publish |
| Depth against executions | `Δ total_qty(level) == Σ executed qty at that level this cycle` | Freeze the instrument; escalate |
| Snapshot join point | `snapshot.as_of <= last_applied_sequence` at emit | Withhold the snapshot cycle |
| Volume conservation | `Δ session_volume == Σ volume-eligible quantity this cycle` | Freeze the statistic; do not publish |

Two properties of this table matter more than its rows. The first is that level conservation is stated as a
recomputation, not as a running total compared against itself: an aggregate checked against another aggregate
maintained by the same increments agrees with itself while both drift. The second is that every row names a
freeze scope, so a breach has a defined blast radius before it happens rather than a decision made under
pressure afterwards.

## Crossed books

Assert `best_bid <= best_ask` on every top-of-book publish. It is one comparison, it costs nothing, and a
crossed book is an arithmetically impossible state that says the publisher's view of the book is already
wrong. NASDAQ published one to the world on 18 May 2012, when the Facebook cross was marked in error and the
proprietary feed carried a stale crossed quote at top of book. There is no situation in which shipping a
crossed quote is better than publishing nothing for that instrument.

The check belongs at the emit boundary, not in the book. A book that is internally consistent can still be
serialised into a crossed quote by a stale cached best price, a partially applied update, or a snapshot copied
across two locks. The point of the check is that it runs on the bytes about to leave.

## Where the checks live, and how to test that they fire

Put the assertions in the encoder or the publisher, on the values being written, and not in the matcher. Two
reasons: the matcher's state can be right while the published projection is wrong, and a check inside the
matcher is a check on the path that has an alternative source of truth, whereas the published bytes have none.

Test the detector, not the happy path. For each row of the table, plant the corresponding break and assert the
system refuses to publish:

- Decrement a level by more than it holds and assert the instrument freezes and no depth message is emitted.
- Force the cached best bid above the best ask and assert the top-of-book publish is withheld.
- Stamp a snapshot with an as-of ahead of the applied sequence and assert the cycle is withheld.
- Emit an execution that the level aggregate does not account for and assert the mismatch is caught in the
  same cycle rather than at the next reset.

A test that only exercises the correct path proves the arithmetic, which was never in doubt. The property
under test is that the check exists, runs in the profile you ship, and stops the publish.
