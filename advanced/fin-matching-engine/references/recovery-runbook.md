# What an operator needs to reproduce an incident from the journal alone

What an operator needs to reproduce a production incident on a laptop, from the journal alone. Anything
missing turns a deterministic engine into an undebuggable one at the worst possible moment.

## Recovery runbook artefacts

Two things make this list different from a backup policy. The first is that every item is an input to a
reproduction rather than a copy of a result: nothing here is a database dump, and the state a dump would carry
is derivable from what is here. The second is that the list is complete or it is nothing, because a
reproduction missing one of its inputs does not fail loudly. It produces a plausible history that differs from
the published one in ways nobody thinks to look for, and the operator reading it gets no signal that anything
is wrong.

Treat a missing artefact as a defect filed against the engine rather than as an inconvenience at incident
time, because the moment you need this list is the moment you can no longer add to it. Where an item is
genuinely absent, write down which question can no longer be answered without it. That sentence is what a
reviewer needs in order to decide whether the gap is acceptable, and it is also the thing that turns a
recovery runbook into a test somebody can run on a quiet afternoon rather than a document that is first read
under an outage.

| Artefact | Content | Why it is required |
|---|---|---|
| Journal segments | Length-prefixed, CRC'd input records, with the epoch in each segment header | The inputs. Without the CRC you cannot tell where a torn tail ends |
| Snapshot + manifest | State, `journal_seq_applied`, `ids`, `book_digest` | The starting point; the position is what makes replay reproducible |
| Build identity | git sha, compiler version, optimisation level, lockfile digest. Recorded **in the snapshot manifest**, not inferred | "Same journal, same build, same state" has three terms; the second is the one nobody records |
| Reducer epochs | The journaled `matcher_version`, `config_digest` and `build` covering every range of the journal | Without it the operator replays through today's build and gets a history that is not the one the venue published, with nothing in the output saying so |
| Authoritative decisions | The executions, priority assignments and minted identifiers as persisted immutable records, if the design persists its decisions immutably | They are what recovery loads. If they exist, the replay is a check on them rather than the source of them |
| Config as journaled events | Every band, tick, limit and instrument definition entered the core as an input event | A config file re-read at replay time is a different config, and the divergence looks like a matching bug |
| Injected time and seeds | The `TimeTick` values and the RNG seed, in-band in the journal | Replay must feed the same time; a laptop's clock is not the pre-crash clock |
| Emitted event capture | The wire bytes the engine actually sent, with their sequence numbers | The right-hand side of the byte-comparison. Capturing decoded events instead makes divergences invisible |
| The replay tool itself | Shipped with the engine, same version, runs offline, prints a first-divergence diff | LMAX's stated debugging procedure: copy the event sequence to a dev machine and replay it |

The completeness test for this list is one sentence and it is the same question the ENGINE CONTRACT block in
SKILL.md asks:
**hand an engineer these files and nothing else, and they reproduce the emitted event sequence byte for byte.**
If they need a database dump, a log grep or a config file from a host, the journal is not the authority; it is
a supplementary log, and the engine is not replayable regardless of what the design note says.

There is one cheap way to keep the list honest, and it is the only one that reliably works: schedule the
reproduction. Once a quarter, take a real journal range out of production, hand an engineer who did not write
the engine nothing but the artefacts named above, and have them reproduce the emitted sequence on a machine
with no access to the production network. Whatever they have to ask for is a missing row, and the list gains
it that afternoon rather than during the next incident, which is the only time it is expensive to discover.
