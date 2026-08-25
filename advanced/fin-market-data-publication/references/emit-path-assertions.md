# Assertions on the publish path

> **Provenance**
> provider: SEC (Regulation NMS and one administrative order), Nasdaq (TotalView-ITCH), and two open-source
> codebases cited for the shape of their code
> surface: assertions on published quotes and aggregates, and the rulebooks that decide what they may assert
> version: 17 CFR 242.610 as published on 2026-08-25 · SEC Release 34-69655 (29 May 2013) · TotalView-ITCH 5.0
> verified_at: 2026-08-25
> sources: https://www.law.cornell.edu/cfr/text/17/242.610
> · https://www.sec.gov/files/litigation/admin/2013/34-69655.pdf
> · https://www.nasdaqtrader.com/content/technicalsupport/specifications/dataproducts/NQTVITCHspecification.pdf
> verified: the locked-and-crossed paragraph of Rule 610 and its exception clause, quoted below; paragraph 31
> of Release 34-69655, quoted below, including the "non-firm" marking on the SIP quote; the ITCH NOII Paired
> Shares definition, quoted below.
> unverified: nautilus_trader's profile settings and the `orders/mod.rs` line reference, and TigerBeetle's
> assertion density and TIGER_STYLE sentence. All were read in the 2026-08-24 pass, could not be re-fetched
> here, and are labelled inline where they appear.
> revalidate_when: Rule 610 is amended or re-lettered again (in the text read here the fee paragraph is (d)
> and locking and crossing is (e), which is not where older writing puts them), or either cited repository
> moves the code a line number names.

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
- Crossed and locked books: the rulebook decides what the assertion may say
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
   release profile by default, and a project can disable them everywhere. nautilus_trader was read in the
   2026-08-24 pass as setting `debug-assertions = false` in both its development and release profiles,
   compiling out the three debug assertions in its whole order model in every build it ships. That reading was
   **not re-verified on 2026-08-25**: check the current `Cargo.toml` before repeating it. The argument does
   not depend on it, because the profile default is the point.
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
- **Do not publish the level, and say that you are not publishing it.** Withholding on its own is invisible.
  A channel heartbeat keeps arriving and proves only that the channel is alive; it says nothing about one
  instrument, so it cannot age or invalidate the book a consumer holds for that symbol, and their staleness
  gate cannot separate a frozen instrument from a quiet one. They keep the last book you sent, indefinitely.
  Emit an explicit unavailable or invalidation event scoped to the instrument, and state in the specification
  what it invalidates and what ends it. Publishing a wrong number is worse than both, which is why
  withholding is still the first move and the announcement is the second.
- **Never clamp to zero.** A clamp is fabricated depth with no exception attached, and it is the single change
  most likely to be proposed as the fix, because it makes the symptom disappear.

Saturating instead of checking is a legitimate choice in exactly one situation: where a panic would abandon
in-flight obligations, so continuing with a defined value is safer than stopping. nautilus_trader was read as
using a saturating subtraction in its order model for that reason, at `orders/mod.rs:1366` in the 2026-08-24
pass; the line number is **not re-verified here** and a line number in a live repository rots within weeks,
so treat it as a shape to look for rather than a location. Where you saturate, the saturation is an event
on the same feed and in the same operational record, because a silent saturation is a fabricated number with
the exception thrown away.

The opposite choice is correct where nothing is in flight. TigerBeetle keeps assertions live in release and
overflow-checks every accumulator before mutating it: "Assertions downgrade catastrophic correctness bugs into
liveness bugs." That sentence, and the density figure of roughly one assertion per 10.6 lines of state
machine, come from the 2026-08-24 reading and were **not re-verified on 2026-08-25**. A liveness bug is
visible. A
debug assertion on a published aggregate is neither of the two: it is a correctness bug with the detector
compiled out.

## The assertion table

| Assertion | Expression | On breach |
|---|---|---|
| Level conservation | `published_qty(level) == Σ leaves_qty of resting orders at that level`, recomputed rather than accumulated | Freeze the instrument; do not publish |
| No underflow | a checked subtraction on every aggregate that leaves the process | Freeze the instrument; do not publish |
| Not crossed | the comparison your rulebook justifies, gated on session state, on every top-of-book publish | Freeze the instrument; publish the invalidation |
| Depth against executions | `Δ total_qty(level) == Σ executed qty at that level this cycle` | Freeze the instrument; escalate |
| Snapshot join point | `snapshot.as_of <= last_applied_sequence` at emit | Withhold the snapshot cycle |
| Volume conservation | `Δ session_volume == Σ volume-eligible quantity this cycle` | Freeze the statistic; do not publish |

Two properties of this table matter more than its rows. The first is that level conservation is stated as a
recomputation, not as a running total compared against itself: an aggregate checked against another aggregate
maintained by the same increments agrees with itself while both drift. The second is that every row names a
freeze scope, so a breach has a defined blast radius before it happens rather than a decision made under
pressure afterwards.

## Crossed and locked books

`best_bid <= best_ask` is the right assertion for a continuous executable book whose rules forbid a crossed
state, and the wrong assertion everywhere else. Crossing and locking are rulebook facts, not arithmetic ones,
which is why Regulation NMS does not forbid them outright. Rule 610's locked-and-crossed paragraph, which is
paragraph (e) in the text read on 2026-08-25 and is cited as (d) in older writing, requires each national securities exchange and national securities association to "establish, maintain, and
enforce written rules" obliging members to reasonably avoid displaying quotations that lock or cross protected
quotations, and it carves out "displaying quotations that lock or cross any protected or other quotation as
permitted by an exception contained in its rules". The exceptions are part of the rule, and they live in a
rulebook rather than in the comparison operator.

Three cases the flat assertion gets wrong:

- **An auction or pre-open book crosses by design.** Buy and sell interest that would execute rests on both
  sides until the auction runs, which is what an auction is for. Nasdaq publishes the fact on the same feed:
  the NOII message carries Paired Shares, "the total number of shares that are eligible to be matched at the
  Current Reference Price", before the cross has run.
- **A venue whose rules permit a locked market.** `bid == ask` passes `<=` and fails `<`, so the operator you
  choose is itself a statement about your rulebook. Write down which one you meant.
- **An aggregated or consolidated view.** A book assembled from several independently quoting venues locks and
  crosses in normal operation, and an assertion inherited from a single-venue publisher fires on healthy data.

So assert what your rulebook forbids, gate the assertion on the session state, and say in the specification
which states it holds in. In the state where crossing is forbidden the check is still worth what it costs,
which is one comparison on the bytes about to leave. Nasdaq's Facebook cross on 18 May 2012 is the worked
example: after the cross was marked in error, "the Prop Feed showed a stale, crossed quote (bid price higher
than ask price) for Facebook on the 'top of book' because orders from the cross were still appearing in the
Prop Feed" (SEC Release 34-69655, paragraph 31). Two details of that paragraph matter more than the headline.
The state was not impossible, it was stale: a published projection that had stopped tracking its source. And
the same paragraph records what a publisher can do about a quote it cannot stand behind, because the crossed
top of book reached the SIP "marked 'non-firm' such that it was not included in the SIP's calculation of the
national best bid and offer". An explicit marking travels with the data. Silence does not.

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
- Force the cached best bid above the best ask, in a session state where your rulebook forbids it, and assert
  the top-of-book publish is withheld and the invalidation event is emitted. Repeat in a state where the
  rulebook permits it and assert the publish proceeds.
- Stamp a snapshot with an as-of ahead of the applied sequence and assert the cycle is withheld.
- Emit an execution that the level aggregate does not account for and assert the mismatch is caught in the
  same cycle rather than at the next reset.

A test that only exercises the correct path proves the arithmetic, which was never in doubt. The property
under test is that the check exists, runs in the profile you ship, and stops the publish.
