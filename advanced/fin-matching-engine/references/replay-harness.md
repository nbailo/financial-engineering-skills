# The harness that licenses the word replayable, and what simulation adds

One artefact decides whether "deterministic and replayable" is a claim or a fact: a test that replays a named
seed and byte-compares the emitted wire bytes against a golden sequence. Deterministic simulation testing is
the strongest instrument on top of it, and it is not free. Both sections say what they do not cover, because
that is where the surviving bugs are.

## The replay harness

This is the artefact that licenses the word "replayable". Without it, delete the sentence.

```python
def test_replay_is_byte_identical(tmp_path):
    seed = 0xC0FFEE                       # named in the test, not derived from the clock
    # Synthetic. Both sides are built by tests/fixtures/build_opening_cross.py; neither is a capture.
    cmds = load_journal("fixtures/synthetic/opening-cross-residue.journal")
    live = read_emitted("fixtures/synthetic/opening-cross-residue.golden-events")

    engine = Engine(seed=seed, snapshot=None, reducer=REDUCER_UNDER_TEST)   # pinned
    replayed = []
    for rec in cmds:
        assert crc32c(rec.raw) == rec.crc          # the fixture itself is verified
        replayed.extend(engine.apply(rec.command, now=rec.injected_time))

    assert len(replayed) == len(live), first_divergence(replayed, live)
    for i, (a, b) in enumerate(zip(replayed, live)):
        if a.wire_bytes != b.wire_bytes:
            raise AssertionError(diff_report(i, a, b))   # index, field-by-field, hex of both
```

- **Fixtures are synthetic and built by a checked-in generator.** A dated capture from a production session is
  a debugging artefact, not a fixture: it carries participant identity, a reviewer cannot regenerate it, and
  its right-hand side is whatever build emitted it on the day, so re-baselining it silently absorbs exactly
  the divergence between the reducer that decided and the build running today. Name each fixture for the scenario it exercises, generate both sides
  from a named builder, and regenerate the golden side only through a reviewed command that records why.
- **Byte-compare the wire encoding, not a decoded struct.** Comparing decoded structs hides padding, encoding
  and field-order divergence: the class the "uninitialised memory" hazard produces.
- **Report the first divergence with context**, not a boolean: sequence index, the two payloads in hex, and the
  first differing field name. A replay test that fails with `False != True` will be disabled within a week.
- **Run it from a snapshot boundary too**, not only from journal position 0. The bug lives at the seam.
- **Determinism meta-test, separately:** run the same seed twice and compare. A run-to-run divergence is a
  core-purity bug; a live-vs-replay divergence is either that or a logic change since capture, and they have
  different owners.
- **Seeds are permanent regression tests.** A seed that ever produced a divergence is checked in by name, and
  a property-testing tool's own example database does not count: CI discards it between runs.

## Deterministic simulation testing

When authority is SELF no external oracle exists, so the proof burden moves before deployment. DST is the
strongest available instrument and it is not free.

**What it costs.** FoundationDB runs *"a deterministic simulation of an entire FoundationDB cluster within a
single-threaded process"* and states the limits plainly: *"Simulation is not able to reliably detect
performance issues… It is also unable to test third-party libraries or dependencies, or even first-party code
not implemented in Flow."* The retrofit price, as measured by S2, is libc-level shims plus hunting the leaks
listed among the core's determinism hazards.

**What it buys.** Perfect repeatability, and time compression: FDB reports roughly 10:1 real-to-simulated;
TigerBeetle reports *"3.3 seconds of VOPR simulation gives you 39 minutes of real-world testing time"*, at
named fault levels from no faults to per-replica storage corruption, running continuously.

**The two things people skip, which are the two that matter.**

- **`buggify`-style injection points inside production code**: at named points, return an unusual-but-legal
  error, add a delay, or pick an unusual tuning parameter, deliberately making rare-but-legal behaviour
  common. Randomise tuning parameters too, so no tuning value becomes load-bearing for correctness.
- **Coverage counters on the generator, not the code.** FDB's `TEST(cond)` macros report whether a scenario is
  generated at all. This is the binding constraint: TigerBeetle's own fuzzer gave every query *"a common
  prefix for each query's target fields"*, so matching objects were always consecutive in each index and a
  real query bug shipped. The venue analogue: a generator whose fills always sum exactly to the order
  quantity never exercises the residual, the over-allocation or the cancel-during-recompute path.

**It does not replace adversarial external testing.** Jepsen found safety bugs and crashes in the most
DST-invested database in existence: the simulator corrupted whole sectors, which always failed checksums and
always took the repair path, while Jepsen flipped single bits **in padding**, which passed the checksum and
hit an assertion. **A simulator injects the faults its author imagined.**
