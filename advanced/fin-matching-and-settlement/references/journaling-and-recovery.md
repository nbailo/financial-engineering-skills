# Input journaling, replay and recovery

The mechanics behind the claim "deterministic and replayable": what exactly gets written, in what order,
relative to the book mutation and the outbound publish; how a replay harness proves the claim by byte-comparing
an emitted event sequence rather than asserting it in a comment; and how a single-writer engine recovers,
snapshots and fails over without emitting two different histories. This is the file for the durability half of
the venue contract: the half that is routinely sequenced correctly in memory and then never persisted, so a
restart invents a second history.

## Contents

1. **What is an input**: the inbound command stream versus derived outputs; why journaling outputs does not give replay.
2. **Ordering and flush**: journal-and-flush before the book is touched; mutation and executions in one commit; publish from committed state.
3. **The publish check**: binding the send result, the transactional outbox, at-least-once with a consumer-visible dedupe key.
4. **The deterministic core**: the banned-construct table and the injection point that replaces each one.
5. **Identity assignment inside the core**: sequence numbers, ExecID and match numbers as input-derived outputs.
6. **The replay harness**: seeded replay, byte-comparison, first-divergence report, and the CI test that licenses the word "replayable".
7. **Snapshots and truncation**: snapshot content, its journal position, verify-by-replay, and what may be deleted.
8. **Crash points**: the enumerated kill boundaries and the expected post-recovery state at each.
9. **Failover and single-writer authority**: epochs, fencing at the storage layer, and the two-writers-briefly history fork.
10. **Deterministic simulation testing**: what it costs, what it buys when authority is SELF, and what it still misses.
11. **Assertion policy**: live-in-release versus saturate-and-emit versus compiled-out, decided per path.
12. **Recovery runbook artefacts**: what an operator needs to reproduce a production incident from the journal alone.

---

## 1. What is an input

An **input** is anything that, if you did not have it, would make the next state transition unreproducible.
For a matching engine that is a strictly larger set than "orders".

| Journal as input | Never journal as the record | Why |
|---|---|---|
| New / cancel / replace / mass-cancel commands, with the arrival order the sequencer assigned | Executions, book deltas, top-of-book | Outputs are a *function* of inputs; journaling them bakes the current matching logic into the durable record |
| Session events: logon, logout, disconnect, cancel-on-disconnect trigger | The in-memory `Vec<Execution>` you built this pass | It vanishes on crash by definition |
| Admin/control commands: halt, resume, band change, instrument definition, config load | The config *file* read at startup | A file re-read at recovery time may differ from the one the pre-crash process read |
| Injected time: a `TimeTick` event carrying the timestamp the core is allowed to see | `Instant::now()` inside the core | See §4 |
| Injected randomness: a seed event, or the drawn value itself | `rand::thread_rng()` inside the core | Same |
| External responses modelled as inbound events (a credit-check reply, an index price) | A blocking RPC inside the core | LMAX splits every external interaction into an output event plus a later input event |

LMAX (Fowler, *The LMAX Architecture*, 2011): *"the current state of the Business Logic Processor is entirely
derivable by processing the input events"*; the journaler stores all input events durably; recovery is
replay-from-snapshot; a production bug is diagnosed by copying the event sequence to a development machine and
replaying it there. The Business Logic Processor is single-threaded and in-memory, with **no automated rollback
facility**, which is the reason validation must complete *before* state mutation, not after.

**Journaling outputs is the failure mode with the longest half-life.** It looks like event sourcing, passes a
"we have a durable log" review, and then cannot rebuild state after a matching-logic fix, cannot answer "what
would this order have done", and has permanently frozen a matching bug into the only record you own.

## 2. Ordering and flush

The write path is these four steps in this order. SKILL.md's *authoritative state is reproducible from durable,
ordered inputs* states the rule; this is the mechanism.

```
1  seq = sequencer.next()                      // inside the deterministic core, see §5
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

## 3. The publish check and the outbox

`let _ = tx.send(ev);` discards exactly the failure you need. In Rust, `tokio::sync::mpsc::Sender::send`
returns `Result<(), SendError<T>>`; the error case is *receiver dropped*, i.e. the market-data publisher
thread died and every subsequent execution is being silently swallowed. `try_send` additionally returns
`TrySendError::Full(T)`, i.e. the consumer is slower than the engine and you are about to drop depth updates
under exactly the load where they matter. Go's `ch <- ev` has the mirror shape: a send on a closed channel
panics, and a send on a full unbuffered channel blocks the matching thread. **Neither "it cannot fail" nor "it
is in-process" is true.** Bind the result; on `Err`, halt that transformation at the smallest scope (SKILL.md's
level 2 or 3) rather than publishing an incomplete history.

The channel is not the durability mechanism, though. The outbox is:

```sql
CREATE TABLE feed_outbox (
  seq          BIGINT      PRIMARY KEY,           -- the core's sequence number, NOT a bigserial
  instrument   TEXT        NOT NULL,
  payload      BYTEA       NOT NULL,              -- the encoded wire message, byte-exact
  payload_crc  INTEGER     NOT NULL,
  committed_at TIMESTAMPTZ NOT NULL DEFAULT now(),-- observability only; never an ordering key
  published_at TIMESTAMPTZ                        -- NULL = not yet sent
);
CREATE INDEX ON feed_outbox (seq) WHERE published_at IS NULL;
```

- **`seq` is assigned by the core, not by the database.** A `BIGSERIAL`/`SEQUENCE` is not transactional, is not
  derived from the journal, and will not reproduce under replay, so a sequence-generated feed number makes the
  replay byte-comparison fail for a reason that has nothing to do with matching.
- **`payload` is the encoded bytes, not a struct to be re-encoded at send time.** Re-encoding at send time
  re-introduces every determinism hazard in §4 on the publish path and makes "byte for byte" unverifiable.
- **The relay is at-least-once and that is the correct design.** The outbox moves the atomicity boundary into
  one transaction; it does not remove duplicates (microservices.io, *Transactional outbox*). A crash between
  `send()` and `UPDATE published_at` republishes. So the dedupe key must be **consumer-visible**: the feed
  sequence number itself. Consumers discard an already-processed sequence, the same rule A/B feed arbitration
  already forces on them.
- **Never renumber on recovery.** A republished execution carries its original `seq`, `ExecID` and match
  number. Corrections travel as a Trade Cancel referencing the original match number, never as a re-issue.

## 4. The deterministic core: banned constructs and their injection points

The core is the region between "a command is dequeued in sequence order" and "the resulting events are
appended to the outbox". Replay is a property of that region only.

| Hazard | Concrete form | What it breaks | Injection point |
|---|---|---|---|
| Wall clock | `Instant::now()`, `System.currentTimeMillis()`, `time.Now()`, `NOW()` in an in-core query | Timestamps in emitted events differ on replay; TIF/GTD expiry evaluates differently | A `TimeTick` input event; the core reads `ctx.now` only |
| Monotonic clock | latency measurement inside the core | Same, plus it leaks into a rate limiter's decisions | Measure outside the core, or emit the measurement as an output |
| RNG | tie-break jitter, randomised STP victim selection, UUID v4 for `ExecID` | Different ExecIDs on replay | Seeded PRNG whose seed is a journaled input; or derive the ID (§5) |
| Hash-map iteration order | iterating `HashMap<OrderId, Order>` to age out orders or build a snapshot message | Rust's `HashMap` is DOS-hardened with a per-process random seed; Go randomises map range order deliberately; Python's `set`/`str` hashing varies with `PYTHONHASHSEED` | `BTreeMap`/`IndexMap`/sorted key vector, or an explicit intrusive queue that carries priority |
| Floating point | a price or a pro-rata weight in `f64` | FMA contraction, x87 80-bit intermediates, `-ffast-math`, and libm differences across hosts make the same expression give different last bits on the replay machine | Integer minor units / fixed-point decimals; if a float is unavoidable, it is an input, journaled at full precision |
| Uninitialised memory | padding bytes inside a `#[repr(C)]` event struct that is `memcpy`'d to the wire or checksummed | The payload and its CRC differ run to run even though every field is equal; the byte-comparison fails on bytes no field owns | Zero the buffer explicitly, or serialise field by field into a length-known encoding |
| Address-dependent ordering | sorting by pointer, Java's identity `hashCode`, Python `id()`, iterating a set of object references | ASLR reorders equal-priority orders between runs | Sort by an explicit `(price, seq)` key that came from the journal |
| Thread scheduling | matching on a pool; a `select!` over two channels | The interleaving is the input, and it is not journaled | Single writer; if you shard, one writer per shard and no cross-shard match |
| I/O in the core | a credit-check RPC, a Redis lookup, a file read | The response is an input that was never journaled | Output event now, input event later (LMAX's split) |
| Locale / timezone | `strftime`, `%s` formatting, a date rendered with the host TZ | Emitted text differs by host | Format outside the core; store instants as integers |

LMAX's stated bans are the same list narrowed to the two that bite first: **external service calls and clock
reads are forbidden inside the business logic**, and both are modelled as request/response event pairs.

**How you find the leaks you did not think of.** S2, retrofitting DST onto async Rust, shimmed libc
(`clock_gettime`, `getrandom`, `getentropy`, `CCRandomGenerateBytes`) and *still* found determinism leaking out
through HTTP timestamp headers inserted by dependencies, Rust's randomised `HashMap` iteration, and
dependency-internal threads and clocks. Their detector is the cheapest thing in this file to copy: **run the
same seed twice and byte-compare the TRACE logs.** Seventeen issues, including ACID violations, came out of it.

## 5. Identity assignment inside the core

Every identifier other systems consume because you assigned it (feed `seq`, `ExecID`, match number, trade id)
is an **input-derived output**. It must be generated inside the deterministic region, from journaled state
only, so that replay reproduces it.

```rust
// inside the core, after the command is journaled and dequeued
struct Ids { next_seq: u64, next_exec: u64, next_match: u64 }

fn on_command(ids: &mut Ids, book: &mut Book, cmd: &Command, now: Nanos) -> Vec<Event> {
    let seq = ids.next_seq; ids.next_seq += 1;          // consumed on REJECT as well as accept
    match validate(cmd, book) {
        Err(reason) => vec![Event::Rejected { seq, cloid: cmd.cloid(), reason }],
        Ok(()) => {
            let mut out = vec![];
            for fill in book.match_order(cmd, now) {
                let exec_id = ids.next_exec; ids.next_exec += 1;
                let match_no = ids.next_match; ids.next_match += 1;
                out.push(Event::Execution { seq: { let s = ids.next_seq; ids.next_seq += 1; s },
                                            exec_id, match_no, ..fill });
            }
            out
        }
    }
}
```

- **Sequence numbers are consumed on rejects.** A gap-free stream that skips rejects is a stream whose consumers
  cannot tell a reject from a lost packet. Gap-freedom is easy to assert in a design note and easy to lose on
  the wire: a single `let _ = tx.send(ev)` that can drop an event makes the claim true of the generator and
  false of the transport.
- **Recover `Ids` by replay, never from a counter table.** After a snapshot at journal position P, `Ids` comes
  from the snapshot and is advanced by replaying P+1..end. Reading a `MAX(exec_id)` from a table you also write
  gives you an ID space that diverges the moment a transaction rolls back.
- **The generator lives beside the matcher, not in the gateway.** A gateway-assigned sequence orders arrivals;
  it does not order *executions*, and executions are what the replay must reproduce.

## 6. The replay harness

This is the artefact that licenses the word "replayable". Without it, delete the sentence.

```python
def test_replay_is_byte_identical(tmp_path):
    seed = 0xC0FFEE                       # named in the test, not derived from the clock
    cmds = load_journal("fixtures/2026-03-11-open.journal")   # captured from production
    live = read_emitted("fixtures/2026-03-11-open.events")    # what the engine actually sent

    engine = Engine(seed=seed, snapshot=None)
    replayed = []
    for rec in cmds:
        assert crc32c(rec.raw) == rec.crc          # the fixture itself is verified
        replayed.extend(engine.apply(rec.command, now=rec.injected_time))

    assert len(replayed) == len(live), first_divergence(replayed, live)
    for i, (a, b) in enumerate(zip(replayed, live)):
        if a.wire_bytes != b.wire_bytes:
            raise AssertionError(diff_report(i, a, b))   # index, field-by-field, hex of both
```

- **Byte-compare the wire encoding, not a decoded struct.** Comparing decoded structs hides padding, encoding
  and field-order divergence: the class §4 row "uninitialised memory" produces.
- **Report the first divergence with context**, not a boolean: sequence index, the two payloads in hex, and the
  first differing field name. A replay test that fails with `False != True` will be disabled within a week.
- **Run it from a snapshot boundary too**, not only from journal position 0. The bug lives at the seam.
- **Determinism meta-test, separately:** run the same seed twice and compare. A run-to-run divergence is a
  core-purity bug; a live-vs-replay divergence is either that or a logic change since capture. They have
  different owners and the harness should not conflate them.
- **Seeds are permanent regression tests.** A seed that ever produced a divergence is checked in by name.
  Hypothesis makes this automatic (its example database defaults to `.hypothesis/examples`, and `derandomize`
  and `print_blob` become `True` automatically in CI so a failure comes back with a `reproduce_failure` blob),
  but the blob is worthless unless the directory is persisted between CI runs.

## 7. Snapshots and truncation

A snapshot is *state plus the journal position it is state as-of*. Without the position it is not a snapshot,
it is a backup.

```
snapshot_manifest {
  journal_seq_applied: u64,     // the LAST input record included, inclusive
  ids: { next_seq, next_exec, next_match },
  book_digest: [u8; 32],        // hash over the canonical serialisation of every level
  build: { git_sha, rustc_version, opt_level, lockfile_digest },
  crc: u32
}
```

- **Take it at a command boundary**, between two applications, never mid-match. There is no consistent
  intermediate state to capture in a single-threaded core, which is precisely what makes the boundary cheap.
- **Recovery is snapshot + tail:** load the latest snapshot whose CRC validates, then replay
  `journal_seq_applied + 1 .. end`. Re-applying a record that the snapshot already includes must be impossible
  by construction (the position tells you), and re-applying the *tail* twice (because you crashed during
  recovery) must produce identical state. Test recovery from an arbitrary snapshot boundary, not only from a
  clean shutdown.
- **Verify a snapshot before you trust it**: replay from the *previous* snapshot forward to this one's position
  and assert `book_digest` equality. That equality is the cheapest continuous proof that the core is still
  deterministic; TigerBeetle's protocol-aware DST does the analogous thing at replica level, asserting
  cross-replica commit-checksum equality and byte-for-byte superblock and client-reply equality rather than
  only system-level invariants.
- **Truncation rule:** a journal segment may be deleted only when (a) it is entirely below the position of a
  snapshot that has been verified by the replay above, and (b) at least one older verified snapshot survives.
  One snapshot plus truncation to it is a single point of failure for the entire history.
- **A snapshot does not free you from keeping the journal.** LMAX snapshots nightly and keeps the input stream;
  the input stream is what lets you re-derive state under *changed* logic, which is the whole reason for
  journaling inputs rather than outputs.

## 8. Crash points

Inject a real `kill -9` at each boundary and assert the post-recovery state. This has to be a test and not a
design note, because the failure mode is silent: journaling a write-ahead field before an external call is the
easy half, and reading it back on resume is the half that gets skipped; the persisted value exists and goes
unused on the exact crash it exists for.

| # | Kill point | Journal | Book | Outbox | Wire | Recovery must do |
|---|---|---|---|---|---|---|
| 1 | Command received, before append | absent | untouched | n/a | n/a | Nothing. The command is lost; the sender's timeout is indeterminate and resolves by its own client-order-id query, not by your guessing |
| 2 | After append, before flush returns | torn or absent tail | untouched | n/a | n/a | Truncate at the last valid CRC. Same outcome as 1 |
| 3 | After flush, before mutation | present | untouched | n/a | n/a | **Replay applies it.** This is the case the flush exists for |
| 4 | Mid-match, before commit | present | in-memory only, gone | none | n/a | Replay re-matches from the pre-command book; result must equal what the pre-crash process would have produced |
| 5 | After commit, before publish | present | durable | rows unpublished | nothing sent | Relay resumes from the lowest unpublished `seq`; consumers see a delayed, gap-free stream |
| 6 | Mid-publish: sent, `published_at` not updated | present | durable | row unpublished | sent once | Relay resends; **consumer dedupes on `seq`**. This is why the dedupe key must be consumer-visible |
| 7 | During recovery replay | present | partially rebuilt | n/a | n/a | Restart recovery from the snapshot; replay is idempotent, so a partial replay leaves nothing to undo |

Case 4 is the one an engine without input journaling gets wrong invisibly: it has the executions (it committed
them) or it does not (it did not), but it cannot answer *what the command was*, so it cannot tell a lost
command from a rejected one, and the participant's order state is unknowable rather than merely unknown.

## 9. Failover and single-writer authority

The single-writer principle is a correctness property here, not a throughput trick. Two engines matching the
same book for even 200 ms produce **two different histories of a record that nothing external can adjudicate**:
you are the oracle, so there is no reconciliation that resolves the fork.

- **Epoch every acquisition, not every failover.** A coarse epoch lets a delayed control message land on the
  next unit of work; that is the KAFKA-17754 mechanism, where infrequent producer-epoch bumps plus unordered
  `EndTxn` across connections gave "aborted reads, lost writes, and torn transactions", fixed by KIP-890/TV2
  bumping the epoch on every transaction (server default from Kafka 4.0).
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
- **Replicate the input stream, not the state.** LMAX multicasts the journaled input stream to followers, which
  derive identical state by applying it. A follower promoted after a fence replays its tail and continues the
  same sequence space: no renumbering, because renumbering re-issues identifiers consumers already booked.
- **Do not order failover by wall clock.** Ordering financial events by timestamps taken on different hosts is
  a data-loss mechanism; the systems that do order by time buy it explicitly (Spanner's clock; TigerBeetle
  refuses node clocks and uses *"leader-based timestamping"*).

## 10. Deterministic simulation testing

When authority is SELF no external oracle exists, so the proof burden moves before deployment. DST is the
strongest available instrument and it is not free.

**What it costs.** FoundationDB runs *"a deterministic simulation of an entire FoundationDB cluster within a
single-threaded process"* (all code deterministic, multithreading avoided, one node per core) and states the
limits plainly: *"Simulation is not able to reliably detect performance issues… It is also unable to test
third-party libraries or dependencies, or even first-party code not implemented in Flow. As a consequence, we
have largely avoided taking dependencies on external systems."* The retrofit price, as measured by S2, is
libc-level shims plus hunting the leaks in §4.

**What it buys.** Perfect repeatability (*"Determinism is crucial in that it allows perfect repeatability of a
simulated run"*), and time compression: FDB reports roughly 10:1 real-to-simulated; TigerBeetle reports
*"3.3 seconds of VOPR simulation gives you 39 minutes of real-world testing time"*, at named fault levels:
*City Breeze* (no faults), *Red Desert* (crashes, flaky network, high latency, no corruption), *Radioactive*
(up to 8% read-path and 9% write-path storage corruption per replica), all running on 1024 cores 24/7.

**The two things people skip, which are the two that matter.**

- **`buggify`-style injection points inside production code**: at named points, return an unusual-but-legal
  error, add a delay, or pick an unusual tuning parameter, deliberately making rare-but-legal behaviour
  common. Randomise tuning parameters too, so no tuning value becomes load-bearing for correctness. FDB's
  *swizzle-clogging* (clog nodes sequentially, unclog in random order) is described as *"particularly good at
  finding deep issues that only happen in the rarest real-world cases."*
- **Coverage counters on the generator, not the code.** FDB's `TEST(cond)` macros report whether a scenario is
  generated at all. This is the binding constraint: TigerBeetle's own fuzzer gave every query *"a common prefix
  for each query's target fields"*, so matching objects were always consecutive in each index, the zig-zag merge
  join's probe path never ran, and a real query bug shipped: *"The VOPR's seemingly sophisticated approach to
  query generation created a blind spot that hid a real bug."* The venue analogue: a generator whose fills
  always sum exactly to the order quantity never exercises the residual, the over-allocation or the
  cancel-during-recompute path.

**It does not replace adversarial external testing.** sled's guide claimed simulation yields systems *"Jepsen
will not find bugs in"*; Jepsen then found two safety bugs and seven crashes in the most DST-invested database
in existence. The mechanism generalises: the VOPR corrupted whole sectors, which always failed checksums and
always took the repair path, while Jepsen flipped single bits **in padding**, which passed the checksum and hit
an assertion. **A simulator injects the faults its author imagined.**

## 11. Assertion policy

Both answers ship today in the same problem domain. The decision is per path, and it is written down.

| | TigerBeetle | nautilus_trader |
|---|---|---|
| Build | `build.zig:110` → `.preferred_optimize_mode = .ReleaseSafe`; Zig `ReleaseSafe` keeps `assert()` live | `Cargo.toml` `[profile.release]` `debug-assertions = false` (:499), `overflow-checks = false` (:500), `panic = "abort"` (:503), and the same in `[profile.dev]` (:439/:440) |
| Density on the money path | 487 `assert(` in 5,166 lines of `src/state_machine.zig`: ~1 per 10.6 lines, all live in production | 3 `debug_assert!` in the whole production order model (`crates/model/src/orders/mod.rs:1320`, `:1333`, `:1341`), 0 always-on `assert!` |
| Accumulators | `sum_overflows` (`state_machine.zig:5144-5149`) checked **before** the account is mutated; dedicated codes `overflows_debits_posted`, `overflows_credits_pending`, … | `saturating_add` / `saturating_sub` on quantities (`orders/mod.rs:1270`, `:1366`) |
| On breach | crash | nothing in production (the `debug_assert!` is not in the binary) |
| On bad *input* | typed result code, always on | `Result<_, OrderError>`, always on |
| Holds unmanaged exposure if it dies? | No: declining a transfer is free | Yes: a halt leaves positions unhedged |

TigerBeetle's rationale (`docs/TIGER_STYLE.md:104-113`): *"Assertions detect programmer errors. Unlike
operating errors, which are expected and which must be handled, assertion failures are unexpected. The only
correct way to handle corrupt code is to crash. **Assertions downgrade catastrophic correctness bugs into
liveness bugs.**"* And the clause usually dropped when this is quoted (`:136-137`): *"The golden rule of
assertions is to assert the positive space that you do expect AND to assert the negative space that you do not
expect."*

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

## 12. Recovery runbook artefacts

What an operator needs to reproduce a production incident on a laptop, from the journal alone. Anything missing
here turns a deterministic engine into an undebuggable one at the worst possible moment.

| Artefact | Content | Why it is required |
|---|---|---|
| Journal segments | Length-prefixed, CRC'd input records, with the epoch in each segment header | The inputs. Without the CRC you cannot tell where a torn tail ends |
| Snapshot + manifest | State, `journal_seq_applied`, `ids`, `book_digest` | The starting point; the position is what makes replay reproducible |
| Build identity | git sha, compiler version, optimisation level, lockfile digest. Recorded **in the snapshot manifest**, not inferred | "Same journal, same build, same state" has three terms; the second is the one nobody records |
| Config as journaled events | Every band, tick, limit and instrument definition entered the core as an input event | A config file re-read at replay time is a different config, and the divergence looks like a matching bug |
| Injected time and seeds | The `TimeTick` values and the RNG seed, in-band in the journal | Replay must feed the same time; a laptop's clock is not the pre-crash clock |
| Emitted event capture | The wire bytes the engine actually sent, with their sequence numbers | The right-hand side of the byte-comparison. Capturing decoded events instead makes divergences invisible |
| The replay tool itself | Shipped with the engine, same version, runs offline, prints a first-divergence diff | LMAX's stated debugging procedure is exactly this: copy the event sequence to a dev machine and replay it |

The completeness test for this list is one sentence and it is the same question the ENGINE CONTRACT block in
SKILL.md asks:
**hand an engineer these files and nothing else, and they reproduce the emitted event sequence byte for byte.**
If they need a database dump, a log grep or a config file from a host, the journal is not the authority; it is
a supplementary log, and the engine is not replayable regardless of what the design note says.
