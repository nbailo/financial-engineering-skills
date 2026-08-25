# Deterministic simulation, and what a simulator cannot find

The stack for a system with no external oracle to reconcile against: what simulation buys, what it costs, the
one principled trigger, and why none of it subsumes an adversarial pass someone else wrote.

## Deterministic simulation testing

**The principled trigger is authority SELF: no external oracle exists to reconcile against.** A bot reconciles against
the exchange, a payments integrator against the processor. A matching engine, custodian or system-of-record
ledger cannot (it *is* the oracle), so a bug is undetectable after the fact and the proof burden moves before
deployment, into a simulator. Complexity, team size and importance are not triggers.

**What it buys** is reproducibility first, fault coverage second. FoundationDB found deterministic reproduction
so much more productive than production debugging that when a bug escaped, the team improved the simulator until
it reproduced there and only then debugged (foundationdb.org/files/fdb-paper.pdf). TigerBeetle measured "3.3
seconds of VOPR simulation gives you 39 minutes of real-world testing time", about an hour of simulation per
month of real time; FDB reports roughly 10:1. **What it costs** is closing the system to nondeterminism:

| Cost | FoundationDB | madsim / turmoil (Rust) |
|---|---|---|
| Concurrency, time, entropy | all code deterministic, multithreaded concurrency avoided, "one database node is deployed per core"; time and randomness from the Flow runtime | single-threaded executor scheduling every spawned task, plus libc overrides for `clock_gettime`, `getrandom`, `getentropy`, `CCRandomGenerateBytes` |
| Dependencies | "unable to test third-party libraries or dependencies, or even first-party code not implemented in Flow. As a consequence, we have largely avoided taking dependencies on external systems"; and "Simulation is not able to reliably detect performance issues" | every external environment must be simulated; one missed source of entropy destroys determinism |

**Three techniques people skip, and they are the ones that make it work.** `buggification` injects
unusual-but-legal behaviour at named points *inside production code* (a legal error, a delay, an unusual tuning
parameter), biasing execution toward the dangerous path. FDB **randomises tuning parameters** so "specific
performance tuning values do not accidentally become necessary for correctness". And `TEST(cond)` coverage
macros, whose hit counts across runs say whether a scenario is generated at all.

**Antithesis** collapses part of that cost by supplying the deterministic environment ("The Antithesis
environment is fully deterministic. This makes every bug we find perfectly reproducible"), which is why *buying*
DST is recommended rather than wasteful at exposure `customer` while building it is not. The Raft result calibrates expectations:
one minimal property ("all replicas apply the same sequence of commands in the same order", via a hash-chained
state machine), partitions alone, three bugs in an hour. Vendor source; note the incentive.

**Protocol-aware DST** is the authority-SELF-and-you-wrote-consensus-or-storage extension: plain DST checks *system-level*
invariants, and the marginal value is in *per-replica internal* ones (cross-replica commit checksum equality,
LSM metadata checksum equality across levels, byte-for-byte superblock/grid/client-reply equality) plus deep
liveness properties such as "replicas should never wind up in a state where they need to coordinate" when logs
are uncorrupted (tigerbeetle.com/blog/2026-08-20-protocol-aware-dst/).

## Fuzzer blind spots, and why DST does not subsume an adversarial pass

sled's simulation guide claims the approach yields systems "Jepsen will not find bugs in". TigerBeetle ran the
VOPR "24/7 on 1024 cores" and Jepsen still found two safety bugs plus seven crashes. Both misses generalise:

- **Generator blind spot.** Both merge-capable fuzzers generated queries sharing a common prefix in their target
  fields, so matching objects were always *consecutive in each index* and the zig-zag merge join's **probe**
  branch was never exercised; multi-predicate queries silently returned truncated suffixes, 1 result where 9
  were expected (#2544). "The VOPR's seemingly sophisticated approach to query generation created a blind spot
  that hid a real bug." The trading analogue: a generator whose fills always sum to the order quantity never
  executes the over-fill and residual-dust branches.
- **Fault-model blind spot.** The VOPR corrupted whole sectors: always caught by checksums, always repaired.
  Jepsen flipped single bits in unused padding, which passed the checksums and tripped a defensive assertion,
  panicking a replica that could have repaired itself (#2681a/b).
- **The model was the finding.** Jepsen's checker was a ~1,600-line single-threaded Clojure reference model of
  accounts, transfers and error codes, written from the documentation by someone who did not share the
  implementers' assumptions: the one property an internal simulator structurally cannot have.

**A simulator injects the faults its author imagined and generates the cases its generator produces.** When authority is
SELF, both DST and an external adversarial pass are required; the second's value is that someone else wrote it.
