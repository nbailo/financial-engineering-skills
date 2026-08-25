# Banned constructs inside the core, and the injection point that replaces each

Replay is a property of one region: between the moment a command is dequeued in sequence order and the
moment the resulting events are appended to the outbox. Every row below is a way a value that is not in the
journal reaches a decision inside that region, and each has an injection point that puts it back in the
journal where replay can reproduce it.

## Determinism is demonstrated by replay, never asserted in a comment

Same inputs, same outputs, same emitted sequence, including the identities you mint. Keep the core free of
wall-clock reads, randomness, I/O and map-iteration-order dependence, and assign every identifier **inside**
the core so replay reproduces it. A correction is a **new** event referencing the original identity, never a
renumber and never an un-emit. Either a replay test names its seed and byte-compares the emitted sequence, or
the claim of replayability goes.

## The deterministic core: banned constructs and their injection points

The core is the region between "a command is dequeued in sequence order" and "the resulting events are
appended to the outbox". Replay is a property of that region only.

| Hazard | Concrete form | What it breaks | Injection point |
|---|---|---|---|
| Wall clock | `Instant::now()`, `System.currentTimeMillis()`, `time.Now()`, `NOW()` in an in-core query | Timestamps in emitted events differ on replay; TIF/GTD expiry evaluates differently | A `TimeTick` input event; the core reads `ctx.now` only |
| Monotonic clock | latency measurement inside the core | Same, plus it leaks into a rate limiter's decisions | Measure outside the core, or emit the measurement as an output |
| RNG | tie-break jitter, randomised STP victim selection, UUID v4 for `ExecID` | Different ExecIDs on replay | Seeded PRNG whose seed is a journaled input; or derive the ID from journaled state |
| Hash-map iteration order | iterating `HashMap<OrderId, Order>` to age out orders or build a snapshot message | Rust's `HashMap` is DOS-hardened with a per-process random seed; Go randomises map range order deliberately; Python's `set`/`str` hashing varies with `PYTHONHASHSEED` | `BTreeMap`/`IndexMap`/sorted key vector, or an explicit intrusive queue that carries priority |
| Floating point | a price or a pro-rata weight in `f64` | FMA contraction, x87 80-bit intermediates, `-ffast-math`, and libm differences across hosts make the same expression give different last bits on the replay machine | Integer minor units / fixed-point decimals; if a float is unavoidable, it is an input, journaled at full precision |
| Uninitialised memory | padding bytes inside a `#[repr(C)]` event struct that is `memcpy`'d to the wire or checksummed | The payload and its CRC differ run to run even though every field is equal; the byte-comparison fails on bytes no field owns | Zero the buffer explicitly, or serialise field by field into a length-known encoding |
| Address-dependent ordering | sorting by pointer, Java's identity `hashCode`, Python `id()`, iterating a set of object references | ASLR reorders equal-priority orders between runs | Sort by an explicit `(price, seq)` key that came from the journal |
| Thread scheduling | matching on a pool; a `select!` over two channels | The interleaving is the input, and it is not journaled | Single writer; if you shard, one writer per shard and no cross-shard match |
| I/O in the core | a credit-check RPC, a Redis lookup, a file read | The response is an input that was never journaled | Output event now, input event later (LMAX's split) |
| Locale / timezone | `strftime`, `%s` formatting, a date rendered with the host TZ | Emitted text differs by host | Format outside the core; store instants as integers |

LMAX's stated bans are the same list narrowed to the two that bite first: **external service calls and clock
reads are forbidden inside the business logic**, and both are modelled as request/response event pairs.

Read the table as an audit, not as a list of prohibitions. Every row names a value that is not in the journal
and that nevertheless reaches a decision inside the region, so the question to ask of a line of core code is
where the value it reads came from and whether that source is an input record. A construct is safe inside the
core when each of its inputs is either journaled or derived from journaled state, and unsafe otherwise, which
is why the injection point in the last column is always the same move: turn an ambient read into a value the
journal carries, and the hazard becomes ordinary input. The rows are roughly ordered by how often they survive
review. The last few are the ones nobody ever wrote down as a decision, so they arrive by accident, through a
dependency that starts a thread of its own, a base image with a different locale, or a compiler flag somebody
set for throughput. That is also why the audit has to be repeated after a dependency bump: nothing in the
engine's own diff shows a new clock read inside a library it calls.

**How you find the leaks you did not think of.** S2, retrofitting DST onto async Rust, shimmed libc
(`clock_gettime`, `getrandom`, `getentropy`, `CCRandomGenerateBytes`) and *still* found determinism leaking out
through HTTP timestamp headers inserted by dependencies, Rust's randomised `HashMap` iteration, and
dependency-internal threads and clocks. Their detector is the cheapest thing in this file to copy: **run the
same seed twice and byte-compare the TRACE logs.** Seventeen issues, including ACID violations, came out of it.
