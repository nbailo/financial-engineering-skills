# Authoritative recovery, migration verification and shadow replay are three runs

Recovery has to reproduce what this engine decided, not what today's build would decide from the same
inputs. Two designs give you that, and the run that does neither is a different artefact with a name.

**One rule binds before any of the mechanism does.** Recovery has to reproduce what this engine actually
decided, not what today's build would decide from the same inputs. So either the authoritative decisions are
persisted immutably and recovery loads them, or every journal record is covered by a journaled reducer identity
and replay applies the version that was in force. Replaying a command stream through changed matching logic is
shadow analysis or migration verification, never authoritative recovery, and nothing elsewhere overrides that.

## Authoritative recovery versus shadow replay

Recovery must reproduce what this engine **decided**, not what the currently deployed build would decide from
the same inputs. Three things have to come back unchanged for that to hold: the **original reducer**, the
**configuration** it ran under, and the **immutable execution facts** it produced. They stop agreeing with a
re-derivation the first time any of the three moves. Journaling inputs
and re-deriving is the right architecture; it becomes a correctness bug the moment the reducer that
re-derives is not the reducer that decided. The bug is silent by construction, which is what makes it worth a
section: the replay completes, the digests agree with each other, and nothing in the run compares its output
to what was actually emitted to participants.

Two designs satisfy the property. A system may use both, and a venue usually should.

**A. Persist the decisions.** The executions, the priority assignments and every identifier you minted are
written as immutable authoritative records in the same commit that produced them, and recovery **loads**
them rather than re-deriving them. Replay then rebuilds only what is needed to keep going: the book, the
queues, the counters. Reach for this whenever the answer to "what did we execute" has to survive a
matching-logic change, which for a venue is always, and note that it is not in tension with journaling
inputs. The input stream is still the record of what you were asked to do; the decision store is the record
of what you did.

**B. Pin the reducer.** Every journal record is covered by a preceding journaled event carrying the identity
of the reducer that applied it, and recovery applies each record with the version that was in force. The
cost is real and should be stated at the design review: the deployable set of reducer versions is retained
for as long as the journal is, and a build you can no longer produce is a range of history you can no longer
recover.

```
reducer_epoch {                    // journaled as an input event on every deploy and every config change
  effective_from_seq: u64,         // the first input record this reducer version applies to
  matcher_version:    "2026.08.3", // the version of the matching logic itself
  config_digest:      [u8; 32],    // over every band, tick, limit and instrument definition in force
  build:              { git_sha, compiler_version, opt_level, lockfile_digest }
}
```

**What you may not do** is replay the command stream through the current build and treat the result as the
authoritative history. It is a *different* history that happens to start from the same inputs, and every
downstream consumer has already booked the first one. Where the two disagree, the participants are right and
the recovered state is wrong, and no assertion inside the recovering process can detect that.

Replay under changed logic is still worth running. It is a different artefact, and the fix is to name it as
one at the point it is produced, in the tool's output and in the file name, so nothing downstream can mistake
the third row here for the first:

| Run | Reducer | What the output is | What it may be used for |
|---|---|---|---|
| **Authoritative recovery** | the one that decided, or the persisted decisions themselves | the history the venue already published | resuming, answering a participant, settling, reporting |
| **Migration verification** | a candidate reducer, against a captured command stream | a diff against the emitted sequence | deciding whether a change is behaviour-preserving before it ships |
| **Shadow analysis** | any reducer | a counterfactual | "what would this have done", capacity work, incident forensics |

A byte-comparison that fails after a deliberate matching change is **migration verification reporting
correctly**. Re-baselining the golden file to make CI green deletes the only description of what the change
did to the tape. The divergence is the change's specification: someone reads it, and someone signs it off.

**This does not weaken the replay harness, it scopes it.** That harness proves one reducer version
is reproducible. It says nothing about whether version N+1 agrees with version N, and a suite that only ever
replays the current build against a golden file regenerated by the current build proves nothing at all.
