# Distributed locks and fencing

Mutual exclusion that has to hold across processes, replicas, and a holder that resumed after its lease
expired. A lock key computed differently in each interpreter excludes nothing, and a lease alone does not stop
a paused holder from acting. The single-database read-modify-write case is in `isolation-and-locking.md`.

## Cross-process lock keys: `hash()` is salted

```python
# found independently in 2 of 3 H-withdrawal reps
conn.execute("SELECT pg_advisory_xact_lock(%s)", (hash(chain) & 0x7FFFFFFF,))
```

Python's `hash()` is randomised per interpreter by `PYTHONHASHSEED` for `str`, `bytes` and `datetime` objects
(on by default since 3.3). Verified locally: `hash('ethereum')` returns a different value in every process,
while `hash(1) == 1` in all of them. So **every replica computes a different advisory-lock key**, the
fleet-wide mutual exclusion protects nothing, and the code passes every single-process test and every review
that does not know about the salt. One rep's design notes claim "two app servers or two workers cannot mint
the same nonce" directly above this line. The precision matters: the defect hashes a chain *name*, so it
manifests; had `chain` been an integer chain id, `hash()` would be the identity function and nothing would
show. State the rule as "require a stable digest regardless of the key's current type", never "don't hash
strings."

```python
import zlib
def advisory_key(namespace: str, subject: str) -> int:
    """Stable across processes, releases, and interpreters. Fits pg_advisory_xact_lock(bigint)."""
    lo = zlib.crc32(subject.encode("utf-8"))            # deterministic, documented, not salted
    hi = zlib.crc32(namespace.encode("utf-8"))
    return ((hi << 32) | lo) - (1 << 63)                # map into signed bigint
```

Or, where the key space is small and known, a checked-in integer registry
(`LOCK_NAMESPACE = {"withdrawal_nonce": 1, "payout_batch": 2}`) plus the two-argument
`pg_advisory_xact_lock(namespace_int, subject_int)`. Two properties either form must have:

- **Stable across deploys.** A digest whose input includes a version string, a pod name, a `uuid4()`, or an
  enum's `auto()` ordinal reintroduces the defect on the next release.
- **Namespaced.** PostgreSQL advisory locks share one key space per database across every caller in it, so two
  unrelated subsystems that both `crc32` a customer id silently serialise against each other. Reserve the
  high 32 bits for the namespace.

`pg_advisory_xact_lock` releases at `COMMIT`/`ROLLBACK` and is the right default; `pg_advisory_lock` releases
only on explicit unlock or session end and leaks on an exception path.

## Fencing tokens, and why a lease alone is insufficient

A lease with a TTL does not stop a paused holder from acting: a GC pause, an arbitrary packet delay, or a
clock jump can each outlive it. Kleppmann's sentence, "if the GC pause lasts longer than the lease expiry
period, and the client doesn't realise that it has expired, it may go ahead and make some unsafe change."
Redis's `gettimeofday` "is subject to discontinuous jumps in system time", so the lock service's own notion of
expiry is not reliable either. The shape (FM-17): worker 1 acquires the lease, GC-pauses past expiry, worker 2
takes the lease and posts entries, worker 1 resumes and posts *its* entries against a state that has moved.
**Both sets land, and no exception is raised anywhere.**

The fix is a monotonically increasing **fencing token** on every write, and the load-bearing half is where it
is checked: "the storage server remembers that it has already processed a write with a higher token number …
and so it rejects the request." A token the resource does not check is decoration.

```sql
-- enforcement at the resource, not at the lock service
UPDATE settlement_batches
   SET state = 'sealed', sealed_by_epoch = :epoch
 WHERE batch_id = :id
   AND COALESCE(sealed_by_epoch, -1) < :epoch;   -- rowcount 0 ⇒ a newer epoch already acted; abort, do not retry
```

Two further requirements the corpus establishes:

- **Advance the epoch on every unit of work, not only on failover.** A coarse epoch lets a delayed control
  message be applied to the *next* unit of work. Precisely KAFKA-17754: no ordering of `EndTxn` across
  connections plus rarely-bumped producer epochs meant a delayed commit landed on the following transaction,
  producing "aborted reads, lost writes, and torn transactions", triggered by *following the official
  documentation* (abort after a commit timeout) and by the Java client's own retries. KIP-890/TV2 bumps the
  epoch on every transaction and is the server default from Kafka 4.0.
- **A "single active writer" (matching engine, sequencer, settlement batcher) is enforced by the storage
  layer's token check, not by deployment discipline.** Kafka's `transactional.id` exists for this:
  `InitPidRequest` "bumps up the epoch of the PID, so that any previous zombie instance of the producer is
  fenced off", and without it "we can only guarantee idempotent production within a single producer session."
