# Replay: reordering harnesses, journal replay, and shadow diffing

Replaying one recorded stream is three proofs: that an event consumer reaches the same terminal state whatever
the arrival order, that an event-sourced core rebuilds from its journal byte for byte, and that a rewrite
agrees with the incumbent on production traffic before it takes over.

## Contents

- Duplication and reordering harnesses for event consumers
- Deterministic replay from a journal, and the same-seed byte-compare meta-test
- Shadow and dark-launch diffing for a money-path rewrite

## Duplication and reordering harnesses

Reconnect replay and at-least-once delivery are the normal case. Build the harness as transforms over one
recorded stream, asserting conservation **after every step, not only at the end**:

```python
@given(seed=st.integers())
def test_terminal_state_invariant(seed):
    rng, baseline = random.Random(seed), None
    for name, events in [("recorded",   list(SESSION)),
                         ("duplicated", [e for e in SESSION for _ in range(1 + (rng.random() < .3))]),
                         ("swapped",    swap_one_adjacent_pair(SESSION, rng)),
                         ("restarted",  SESSION)]:      # driver kills the consumer mid-stream
        c = Consumer()
        for i, e in enumerate(events):
            c.apply(e)                                  # asserted after EVERY step, and recomputed
            head = events[: i + 1]                      # independently of c's own accumulators
            assert c.position == sum_signed_fills(head), f"{name}@{i}"
            assert c.cash == -sum_notional(head) - sum_fees(head)
        baseline = baseline or c.snapshot()
        assert c.snapshot() == baseline, f"{name} diverged"
```

Then assert the **generator produces the interesting cases**: jqwik's `injectDuplicates()` and
`Statistics.coverage`, or FDB's `TEST(cond)` macros whose cross-run hit counts reveal whether a scenario is
generated at all. Two questions: does the generator ever emit a duplicate *at a terminal state*, and does it
ever restart *between* the effect and the outcome write? The first is the ghost-order resurrection: `if
existing is not None and ts < existing.update_time: return` skips the guard entirely once a terminal event has
popped the order, so a replayed pre-snapshot `PARTIALLY_FILLED` re-inserts a phantom open order, a common
defect in hand-rolled order-state trackers. The order-invariance form ships upstream: nautilus's
`test_avg_px_invariant_to_fill_arrival_order` asserts ascending and descending fill arrival produce
byte-identical `avg_px` (`crates/model/src/orders/mod.rs:1769`).

## Deterministic replay from a journal

LMAX's business logic processor is single-threaded, in-memory and event-sourced: "the current state of the
Business Logic Processor is entirely derivable by processing the input events", and a production bug is
diagnosed by copying "the sequence of events to their development environment and replay[ing] them there"
(martinfowler.com/articles/lmax.html). The precondition is a list of bans inside the core (no wall-clock reads,
no RNG, no I/O, no map-iteration-order dependence) and external interaction split into an output event plus a
later input event. Two tests make the claim real:

```python
def test_replay_reconstructs_live_state(journal, live_engine):
    replayed = Engine.from_journal(journal.read_all())
    assert replayed.serialize() == live_engine.serialize()   # byte-for-byte, not "approximately"

def test_same_seed_is_byte_identical(tmp_path):              # meta-test: prove determinism
    a, b = (run_sim(seed=0xC0FFEE, trace=tmp_path / n) for n in ("a.trace", "b.trace"))
    assert a.read_bytes() == b.read_bytes()
```

The second is not ceremony. S2, retrofitting deterministic simulation onto async Rust, needed exactly this
byte-compare of TRACE logs across two runs of one seed to locate its determinism leaks: HTTP timestamp headers
inserted by dependencies, Rust's DOS-hardened randomized `HashMap` iteration order, and dependency-internal
threads and clocks (s2.dev/blog/dst). Any one makes a "deterministic" replay emit false divergences until
somebody switches the detector off.

**Replaying a production event stream against a new build** is the strongest form: capture a window of real
input events, run the candidate offline over them, diff its output events against the incumbent's recorded ones:
no stubs, no sandbox, because the core makes no external calls.

## Shadow and dark-launch diffing

For a money-path rewrite at exposure `customer` or `record`, production traffic plus the incumbent is the
cheapest strong oracle
available.

- **Run the new implementation on the same inputs with its effects disabled**, not "a feature flag at 1%". If
  it cannot run with effects disabled, that is a design finding about the rewrite, not a reason to skip the
  diff.
- **Compare economic outputs, not internal state:** amount and currency in minor units, account or instrument,
  side/sign, fee, and the identity that would have been minted. Diffing intermediate fields trains people to
  ignore the diff.
- **Tolerance is zero, or one minor unit with a named reason**; where rounding differs, fix the rounding.
  Classify every divergence before cutting over: new-path bug, incumbent bug (common and valuable), or
  documented behaviour change.
- **Cut over on a stated volume of clean diff, not elapsed time**: "N million events with zero unexplained
  divergences, including one of each cassette shape above" is a different claim from "two weeks green". Keep the
  diff running after cutover with the old path in shadow; it becomes the rollback trigger.

Uber's dual-write migration used the same idea as its safety net: an `EntityChangeLog` consumer detects
**version gaps** and back-fills. The tolerance and volume thresholds above are mechanism-derived.
