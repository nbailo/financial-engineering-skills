# What counts as an input, and why journaling outputs is not replay

The durability half of the venue contract, and the half that is routinely sequenced correctly in memory and
then never persisted, so a restart invents a second history. What an input is turns out to be a strictly
larger set than "orders": anything that, if you did not have it, would make the next state transition
unreproducible.

## Authoritative state is reproducible from durable, ordered inputs

Specialises *operation identity*: the intent is the inbound command, the first externally visible effect is
the execution everyone else books, and five parts bind rather than any one architecture.

- The inbound command is durable and ordered **before** the state it changes is touched.
- The state change and the obligations it creates commit as one atomic step.
- What you publish is read from the committed record, never from the in-memory vector that produced it.
- Exactly one writer may extend that record, and the resource holding it **rejects** a stale-epoch write
  rather than trusting a displaced writer to have stopped.
- Recovery reproduces what this engine **decided**. Either the immutable execution facts, the executions and
  every identifier you minted, are persisted and recovery loads them, or every journal record is covered by a
  journaled reducer identity, matcher version, configuration digest and build, and replay applies the version
  **and the configuration** then in force. Replay through changed logic or changed configuration is shadow
  analysis or migration verification, never authoritative recovery.

If the emission is the only record, a process that dies mid-send cannot state what it executed. LMAX states
the property as *"the current state of the Business Logic Processor is entirely derivable by processing the
input events"*. Durability is the half engines skip: `let _ = tx.send(ev);` drops a published execution on the
excuse *"it is in-process, it cannot fail"*, and two engines on one book for 200 ms produce two irreconcilable
histories.

## What is an input

An **input** is anything that, if you did not have it, would make the next state transition unreproducible.
For a matching engine that is a strictly larger set than "orders".

| Journal as input | Not an input | Why |
|---|---|---|
| New / cancel / replace / mass-cancel commands, with the arrival order the sequencer assigned | Executions, book deltas, top-of-book | These are outputs, a *function* of the inputs, so they cannot serve as the input stream: from outputs alone you cannot say what a command was, and you cannot replay. They are still persisted, as immutable authoritative decisions, which is a second record with a different job |
| Session events: logon, logout, disconnect, cancel-on-disconnect trigger | The in-memory `Vec<Execution>` you built this pass | It vanishes on crash by definition |
| Admin/control commands: halt, resume, band change, instrument definition, config load | The config *file* read at startup | A file re-read at recovery time may differ from the one the pre-crash process read |
| Injected time: a `TimeTick` event carrying the timestamp the core is allowed to see | `Instant::now()` inside the core | A clock read inside the core is a determinism hazard of its own |
| Injected randomness: a seed event, or the drawn value itself | `rand::thread_rng()` inside the core | Same |
| External responses modelled as inbound events (a credit-check reply, an index price) | A blocking RPC inside the core | LMAX splits every external interaction into an output event plus a later input event |

LMAX (Fowler, *The LMAX Architecture*, 2011): *"the current state of the Business Logic Processor is entirely
derivable by processing the input events"*; the journaler stores all input events durably; recovery is
replay-from-snapshot; a production bug is diagnosed by copying the event sequence to a development machine and
replaying it there. The Business Logic Processor is single-threaded and in-memory, with **no automated rollback
facility**, which is the reason validation must complete *before* state mutation, not after.

**Journaling outputs *instead of* inputs is the failure mode with the longest half-life.** It looks like event
sourcing, passes a "we have a durable log" review, and then cannot rebuild state after a matching-logic fix,
cannot answer "what would this order have done", and has permanently frozen a matching bug into the only
record you own.

**Its mirror image is subtler and ships more often**: journaling the inputs correctly, and then re-deriving
authoritative state at recovery time through whatever build happens to be deployed. That passes the same
review, keeps passing it, and produces a history that is internally consistent, digest-clean and **not the
one the venue published**. Pinning the reducer that decided, or persisting
the decisions themselves, is the rule that separates the two.
