# Snapshots, truncation, and the kill points recovery has to answer

A snapshot is state plus the journal position it is state as-of; without the position it is a backup. What
follows from that is a verification rule, a truncation rule, and an enumeration of the places a `kill -9`
lands. The crash table is a test rather than a design note, because the failure mode is silent: the persisted
value exists and goes unused on the exact crash it exists for.

## Snapshots and truncation

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
- **A snapshot does not free you from keeping the journal.** LMAX snapshots nightly and keeps the input stream.
  The input stream is what lets you ask what *changed* logic would have done, which is a different question
  from what you did: that run is shadow analysis or migration verification, and it is kept apart from
  authoritative recovery.
- **The `build` field above is necessary and not sufficient.** A journal spans deploys, so one build identity
  attached to a snapshot cannot say which reducer applied record 4,000,001. A journaled reducer-epoch event is
  what makes that mapping total.

## Crash points

Inject a real `kill -9` at each boundary and assert the post-recovery state. This has to be a test and not a
design note, because the failure mode is silent: journaling a write-ahead field before an external call is the
easy half, and reading it back on resume is the half that gets skipped; the persisted value exists and goes
unused on the exact crash it exists for.

| # | Kill point | Journal | Book | Outbox | Wire | Recovery must do |
|---|---|---|---|---|---|---|
| 1 | Command received, before append | absent | untouched | n/a | n/a | Nothing. The command is lost; the sender's timeout is indeterminate and resolves by its own client-order-id query, not by your guessing |
| 2 | After append, before flush returns | torn or absent tail | untouched | n/a | n/a | Truncate at the last valid CRC. Same outcome as 1 |
| 3 | After flush, before mutation | present | untouched | n/a | n/a | **Replay applies it.** This is the case the flush exists for |
| 4 | Mid-match, before commit | present | in-memory only, gone | none | n/a | Replay re-matches from the pre-command book, **with the reducer that was in force**; the result must equal what the pre-crash process would have produced, which is a claim about that reducer and not about the current build |
| 5 | After commit, before publish | present | durable | rows unpublished | nothing sent | Relay resumes from the lowest unpublished `seq`; consumers see a delayed, gap-free stream |
| 6 | Mid-publish: sent, `published_at` not updated | present | durable | row unpublished | sent once | Relay resends; **consumer dedupes on `seq`**. This is why the dedupe key must be consumer-visible |
| 7 | During recovery replay | present | partially rebuilt | n/a | n/a | Restart recovery from the snapshot; replay is idempotent, so a partial replay leaves nothing to undo |

Case 4 is the one an engine without input journaling gets wrong invisibly: it has the executions (it committed
them) or it does not (it did not), but it cannot answer *what the command was*, so it cannot tell a lost
command from a rejected one, and the participant's order state is unknowable rather than merely unknown.
