# Which assertions survive the release build, and what a saturation owes you

Two systems in the same problem domain ship opposite answers, and both are defensible, so the decision is per
path and it is written down. What is not optional is the published aggregate: a quantity that leaves the
process as depth or volume is checked in the binary you deploy, and where it saturates instead, the saturation
is itself an event.

## An aggregate you publish is checked in the build you ship

Specialises *rounding and conservation*. Any quantity leaving the process as depth, volume or an aggregate is
checked where it is computed, in the binary you deploy, not by an assertion the release build strips. An
underflow or overflow is a conservation breach: halt that transformation at the smallest scope, do not
publish, do not clamp silently, and where you saturate rather than check, **emit the saturation**, because a
saturated aggregate with no exception attached is a lie. `level.total_qty -= qty` on a `u64` guarded only by a
debug assertion wraps to roughly 1.8e19 in a **release** build and is published as depth. Both shipping
answers, with their build settings, are below.

## Assertion policy

Both answers ship today in the same problem domain. The decision is per path, and it is written down.

| | TigerBeetle | nautilus_trader |
|---|---|---|
| Build | `build.zig:110` → `.preferred_optimize_mode = .ReleaseSafe`; Zig `ReleaseSafe` keeps `assert()` live | `Cargo.toml` `[profile.release]` `debug-assertions = false` (:499), `overflow-checks = false` (:500), `panic = "abort"` (:503), and the same in `[profile.dev]` (:439/:440) |
| Density on the money path | 487 `assert(` in 5,166 lines of `src/state_machine.zig`: ~1 per 10.6 lines, all live in production | 3 `debug_assert!` in the whole production order model (`crates/model/src/orders/mod.rs:1320`, `:1333`, `:1341`), 0 always-on `assert!` |
| Accumulators | `sum_overflows` (`state_machine.zig:5144-5149`) checked **before** the account is mutated; dedicated codes `overflows_debits_posted`, `overflows_credits_pending`, … | `saturating_add` / `saturating_sub` on quantities (`orders/mod.rs:1270`, `:1366`) |
| On breach | crash | nothing in production (the `debug_assert!` is not in the binary) |
| On bad *input* | typed result code, always on | `Result<_, OrderError>`, always on |
| Holds unmanaged exposure if it dies? | No: declining a transfer is free | Yes: a halt leaves positions unhedged |

TigerBeetle's rationale (`docs/TIGER_STYLE.md:104-113`): *"The only correct way to handle corrupt code is to
crash. **Assertions downgrade catastrophic correctness bugs into liveness bugs.**"* And the clause usually
dropped when this is quoted (`:136-137`): *"The golden rule of assertions is to assert the positive space that
you do expect AND to assert the negative space that you do not expect."*

**The rule this yields.** Validation of inputs you can decline is always-on and returns a typed rejection.
Assertion of derived internal state may be compiled out **only in a process that would still be holding
unmanaged obligations if it crashed**, and in that process the arithmetic saturates and **emits the
saturation as an event**, because a saturated aggregate with no exception attached is a lie. A `debug_assert`
on a published aggregate is neither of the two: `level.total_qty -= qty` on a `u64` wraps to ~1.8e19 in a
release build where the assertion no longer exists, and that number is published as depth.

Note the counter-pressure, from Jepsen's TigerBeetle report: an assertion placed on state the *recovery path is
designed to tolerate* converts a repairable fault into an outage; the padding-byte crash is exactly that.
Assert impossible-state; repair recoverable-state; and keep fail-fast (kill the process) distinct from
fail-closed (stop taking new risk, keep cancels and drop-copy serving).
