# The unsigned aggregate on the emit path, and the check that survives the release build

> **Provenance**
> provider: the Cargo reference, for the profile defaults the argument rests on
> surface: build profiles, and what they do to a debug assertion and to integer overflow checking
> version: the Cargo profiles page as served on 2026-08-25
> verified_at: 2026-08-25
> sources: https://doc.rust-lang.org/cargo/reference/profiles.html
> verified: the dev and release profile defaults for `debug-assertions` and `overflow-checks`, read on the
> Cargo reference on 2026-08-25.
> unverified: nothing. The previous edition of this file cited two open-source codebases for profile settings,
> an assertion density and a line number. None could be re-fetched on 2026-08-25, so all of it was deleted
> rather than kept behind a label: the argument never depended on it, because the profile defaults are the
> point and they are now sourced.
> revalidate_when: the Cargo reference changes a profile default quoted here, or your language's release
> profile stops disabling overflow checking.

The check that must survive into the shipped binary, because it runs on the last hop before a number leaves
your process and becomes somebody else's book. Drift that reaches the wire is not recoverable: the consumer
applied it, traded on it, and has no independent source to check it against. What to do when the check fires
is in this skill's breach policy reference.

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

1. **A debug assertion compiles to nothing unless debug assertions are enabled.** The Cargo reference gives
   `debug-assertions = true` as a dev-profile default and `debug-assertions = false` as a release-profile
   default, so the check you wrote runs in the build you test and not in the build you ship. A project is also
   free to turn them off in every profile, and some do.
2. **Release profiles disable integer overflow checks.** The same page gives `overflow-checks = true` for dev
   and `overflow-checks = false` for release, so in the shipped build the subtraction wraps rather than
   panicking, an unsigned aggregate that goes below zero becomes a number near `2^64`, and that number is
   published as depth. The two reasons are independent: fixing the profile does not fix the assertion, and
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
