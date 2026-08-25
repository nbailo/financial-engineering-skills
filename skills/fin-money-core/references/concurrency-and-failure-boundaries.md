# Concurrency and failure boundaries

The mechanism behind two SKILL.md rules: *the lock, the key, the subject, the duration* and *every external
money call is three phases and the first one COMMITs*. Everything here concerns the two windows in which
correct-looking code produces an economically wrong outcome: between reading a balance and writing it, and
between an external effect and the local record of it. Engine semantics are stated per `(engine, level)`,
because the label is not the guarantee.

## Contents

- [Per-engine isolation: what each level actually permits](#per-engine-isolation-what-each-level-actually-permits)
- [The two shapes, as SQL you can run](#the-two-shapes-as-sql-you-can-run)
- [The three correct fixes for check-then-act, and when each applies](#the-three-correct-fixes-for-check-then-act-and-when-each-applies)
- [The retry: what a 40001 handler must re-execute](#the-retry-what-a-40001-handler-must-re-execute)
- [The decorative transaction](#the-decorative-transaction)
- [Verifying the boundary encloses the check→act region](#verifying-the-boundary-encloses-the-checkact-region)
- [Cross-process lock keys: `hash()` is salted](#cross-process-lock-keys-hash-is-salted)
- [Fencing tokens, and why a lease alone is insufficient](#fencing-tokens-and-why-a-lease-alone-is-insufficient)
- [Persist intent → external effect → persist outcome](#persist-intent--external-effect--persist-outcome)
- [The dual-write problem and the transactional outbox](#the-dual-write-problem-and-the-transactional-outbox)
- [Compensation is not rollback](#compensation-is-not-rollback)
- [Step 9 / Step 10 artefact templates](#step-9--step-10-artefact-templates)

---

## Per-engine isolation: what each level actually permits

"REPEATABLE READ" names at least three different things. PostgreSQL RR is snapshot isolation; MySQL InnoDB RR
is weaker than SI; the 1995 ANSI critique means locking-RR. Berenson et al. Remark 9 proves locking-RR and SI
are **incomparable** (SI forbids A3 phantoms and permits A5B write skew, locking-RR does the reverse), so any
mental model that orders isolation levels on a single ladder is wrong exactly at the step money cares about.

| `(engine, level)` | Lost update (read-modify-write in app code) | Write skew (A5B) | Phantom / predicate insert | Serialization anomaly | Retry obligation |
|---|---|---|---|---|---|
| **PG Read Committed** (default) | **Permitted.** `UPDATE` re-evaluates its `WHERE` against the *new* row version, but the application's arithmetic used the old one | Permitted | Permitted | Permitted | none |
| **PG Repeatable Read** (= snapshot isolation) | Prevented by first-updater-wins: the loser aborts with `could not serialize access due to concurrent update`, SQLSTATE **40001** | **Permitted** | Prevented for *reads* under PG's snapshot; **not** prevented as a constraint violation across disjoint inserts | Permitted | must retry 40001 |
| **PG Serializable (SSI)** | Prevented | Prevented | Prevented | Prevented | must retry 40001 (`could not serialize access due to read/write dependencies among transactions`) |
| **MySQL InnoDB REPEATABLE READ** (default) | **Permitted in practice**: Jepsen 8.0.34 observed 198 instances involving 446 of 9,048 committed transactions | Permitted | Gap/next-key locks on range conditions; record-only lock for a unique index with a unique search condition | Permitted (G2-item, G-single, non-monotonic views all observed) | none by default |
| **MySQL InnoDB SERIALIZABLE** | Prevented (plain `SELECT` becomes `SELECT … LOCK IN SHARE MODE`) | Prevented | Prevented | Prevented | must handle deadlock `1213` / SQLSTATE 40001 |
| **CockroachDB READ COMMITTED** | Permitted; mitigation is `SELECT … FOR UPDATE` | Permitted | `FOR UPDATE` does **not** prevent phantom reads caused by inserted rows | Permitted | "never returns `RETRY_SERIALIZABLE`" |

**Why MySQL RR is not serializable and not even snapshot isolation.** Jepsen's root-cause sentence:
*"writing a row modifies the transaction's local copy of the data."* A transaction's own write is folded into
its read view, so a later read returns a value no consistent snapshot ever contained; Jepsen recorded 126 of
9,048 transactions with internal-consistency violations, values "out of thin air." The manual gives the second
half: non-locking reads use the snapshot from the first read, but "the locking statements use the most recent
state of the database to use locking", and it warns against mixing the two in one RR transaction because
"typically in such cases you want SERIALIZABLE." So `SELECT balance` then
`UPDATE … WHERE balance >= :amt` evaluates the guard against a state the application never saw.

Two facts survive whatever level you pick:

- **The label can be voided by the topology.** AWS RDS MySQL read replicas violated serializability purely
  because `replica_preserve_commit_order` defaults to `OFF` (Jepsen, MySQL 8.0.34). A balance read from an
  async replica and written to the primary is covered by *no* isolation level: read from the primary.
- **"We set SERIALIZABLE" is not a proof.** PostgreSQL 12.3's SSI conflict detector could "incorrectly
  identify an updating transaction's transaction ID (XID) as responsible for both the original and updated
  versions of a tuple" and permitted G2-item, in code essentially untouched since SSI landed in 2011
  (Jepsen, PostgreSQL 12.3; fixed the next minor release).

## The two shapes, as SQL you can run

**Lost update: one row, two readers.** The bug is the arithmetic in the application, not the SQL.

```sql
-- session A and session B, interleaved, MySQL default REPEATABLE READ
BEGIN;
SELECT balance_minor FROM accounts WHERE id = 42;   -- both read 10000
-- application computes 10000 - 3000 = 7000
UPDATE accounts SET balance_minor = 7000 WHERE id = 42;
COMMIT;                                             -- both commit; one debit vanishes
```

Under PG Repeatable Read the second `UPDATE` aborts with 40001 (first-updater-wins). Under PG Read Committed
the second `UPDATE` blocks, then re-reads the *new* row version and writes `7000` over it anyway, because the
literal `7000` was computed from the stale read. Under MySQL RR neither happens.

**Write skew: one invariant, two rows.** Berenson's canonical case is a bank: "account balances are allowed
to go negative as long as the sum of commonly held balances remains non-negative." PostgreSQL's SSI wiki gives
the identical worked example: $500 checking + $500 savings, two concurrent $900 withdrawals, both succeed
under REPEATABLE READ, both are caught under SERIALIZABLE.

```sql
-- invariant: SUM(balance_minor) OVER (customer 7) >= 0
BEGIN ISOLATION LEVEL REPEATABLE READ;
SELECT SUM(balance_minor) FROM accounts WHERE customer_id = 7;   -- A and B both see 100000
UPDATE accounts SET balance_minor = balance_minor - 90000 WHERE id = 'checking';  -- txn A
UPDATE accounts SET balance_minor = balance_minor - 90000 WHERE id = 'savings';   -- txn B
COMMIT;  -- disjoint write sets ⇒ first-committer-wins never fires ⇒ invariant off by 80000
```

**Predicate write skew via INSERT** is the same failure with no row to lock. Check `SUM(notional) <= limit`
over open positions, then `INSERT` a new position: the two transactions insert *different* rows, so no
row-level conflict exists. Snapshot isolation permits it (Berenson's "sum of task hours ≤ 8" example), and
`SELECT … FOR UPDATE` does not help. CockroachDB: "locking reads do **not** prevent phantom reads that are
caused by the insertion of new rows." Only SERIALIZABLE, a predicate lock, or a row lock on the **parent** row
representing the invariant (the account, the limit row) prevents it.

## The three correct fixes for check-then-act, and when each applies

TigerBeetle's own docs name the race for the general case: "these requests are not atomic and the account's
balance may change between the lookup and the transfer."

| Fix | Shape | Use when | Fails when |
|---|---|---|---|
| **1. Single atomic conditional `UPDATE`**: move the predicate into the write | `UPDATE … SET bal = bal - :amt WHERE id = :id AND bal >= :amt`, then branch on **rowcount** | The invariant is confined to **one row** and the new value is a pure function of the old one | The invariant spans rows (write skew), or the decision needs a value you must show the caller before writing |
| **2. `SELECT … FOR UPDATE` held for the whole critical section** | Lock every row the invariant reads, in a deterministic order, inside a transaction that does not commit until after the act | The invariant spans a **fixed, enumerable set of existing rows**, or the act is a multi-statement sequence | The invariant is over a *predicate* that a concurrent `INSERT` can join (phantoms); or the act is an external call (see [the decorative transaction](#the-decorative-transaction)) |
| **3. `SERIALIZABLE` + retry that re-reads** | `BEGIN ISOLATION LEVEL SERIALIZABLE`; on 40001 discard **all** results and re-execute the whole unit from the first read | The invariant is an aggregate or a predicate (`SUM(...) <= limit`, "no row exists such that …"), or you cannot enumerate the rows in advance | Retry is not generalized (PostgreSQL: you cannot predict which transactions will conflict); or the transaction has already produced an external side effect |
| *(variant of 1)* **Optimistic `lock_version`** | Caller passes the version it read; `UPDATE … WHERE version = :v`; rowcount 0 ⇒ conflict | Hot rows where a pessimistic lock is the throughput ceiling (fee, tax, FX-liquidity, clearing accounts appear in a large fraction of all transactions) | The caller cannot be trusted to re-read on conflict |

Fix 1 is not "better SQL"; it is a different correctness argument: the predicate and the mutation become one
operation, so no isolation level is load-bearing. **Assert the affected row count.**

```python
cur.execute(
    "UPDATE accounts SET balance_minor = balance_minor - %s "
    "WHERE id = %s AND currency = %s AND balance_minor >= %s",   # currency is part of the predicate:
    (amt, account_id, ccy, amt),                                 # `bal >= amt` means nothing across currencies
)
if cur.rowcount == 0:
    raise InsufficientFunds(account_id)       # not `return`, not `pass`, not 200 OK
assert cur.rowcount == 1
```

Rowcount 0 means *insufficient funds or row missing*, must be distinguished, and is never idempotent success.
PostgreSQL's documented Read Committed miss (`UPDATE website SET hits = hits + 1` concurrent with
`DELETE FROM website WHERE hits = 10` deletes nothing, even though a `hits = 10` row exists before and after)
is the general form of "0 rows is not proof".

## The retry: what a 40001 handler must re-execute

PostgreSQL: applications "must be prepared to retry transactions due to serialization failures", and (the
usually-missed clause) **"Any query results from a failed serializable transaction must be ignored."**

```python
for attempt in range(5):
    try:
        with conn.transaction():                 # BEGIN ISOLATION LEVEL SERIALIZABLE
            bal = read_balance(conn, acct)       # the READ is inside the retried unit
            if bal < amt:
                raise InsufficientFunds(acct)
            write_balance(conn, acct, bal - amt)
        break
    except psycopg.errors.SerializationFailure:  # SQLSTATE 40001
        sleep(backoff_with_jitter(attempt))
        continue
else:
    raise Unresolved(acct)                       # bounded, terminates in "unknown", never infinite
```

The defect this shape prevents is the read hoisted out of the loop: `balance = read_balance()` above
`for attempt in range(3):`, so every retry rewrites `balance - amt` from a stale value. TiDB shipped it *as a
database default*: two retry mechanisms (`tidb_disable_txn_auto_retry`, `tidb_retry_limit`, both on)
re-executed a transaction's writes while returning "the reads from the aborted transaction", losing 64 of 378
insertions (Jepsen, TiDB 2.1.7). MongoDB retried transactions regardless of `retryWrites`, and SERVER-48307
applied writes twice (Jepsen, MongoDB 4.2.6). **A retry that reuses stale reads is a blind re-application.**
Before relying on any driver-, ORM- or engine-level automatic retry on a money path, verify it re-executes the
reads; if you cannot verify it, disable it.

Codes a money-path retry classifier must separate:

| Code | Engine | Meaning | Action |
|---|---|---|---|
| `40001` | PG (`serialization_failure`), MySQL (`ER_LOCK_DEADLOCK`, errno 1213) | Conflict; nothing committed | Re-execute the **whole** unit including reads |
| `40P01` | PG (`deadlock_detected`) | Deadlock; this transaction was chosen as victim | Re-execute; also fix lock ordering |
| `1205` / `HY000` | MySQL (`ER_LOCK_WAIT_TIMEOUT`) | Lock wait exceeded | Re-execute; **verify** the row state first if the statement was a bare `UPDATE` |
| `23505` | PG (`unique_violation`) | The idempotency race resolved against you | Resolve to the winner's stored result; never surface raw |

Deterministic lock ordering (always lock account ids ascending) turns most deadlocks into waits, and is the
one thing the retry loop cannot do for you.

## The decorative transaction

**The lock is taken and released before the critical section it was meant to protect.** No test detects it
(every statement is individually correct and the lock is real), so it survives review in these shapes:

| # | Instance | What is actually protected |
|---|---|---|
| 1 | `with db.engine.begin() as conn: SELECT … FOR UPDATE`: the lock dies at the dedent, before the sign and broadcast | nothing after the dedent |
| 2 | `poll_batch()` does `FOR UPDATE SKIP LOCKED LIMIT 50`, `fetchall()`, then closes the transaction | nothing; N confirmer instances process the same 50 rows |
| 3 | **Headline money loss (H-withdrawal#2):** admin `reject()` drops the row lock between the status check and `reverse()` | nothing; the withdrawal is broadcast in the window, `reverse()`'s guard still accepts `'broadcast'`, and the user keeps both the coins and the balance |
| 4 | Confirmer uses `async with session_factory()` with no `session.begin()` | nothing; `FOR UPDATE` outside a transaction holds no lock |
| 5–6 | `asyncio.Lock()` declared and never `await`-ed (twice, in two different reps) | nothing |

Instance 3 is the shape to internalise: every statement is correct, the lock is real, the status check is
real, and the money is gone because the *act* is outside the boundary the *check* was made inside. Instance 2
adds a lesson: `SKIP LOCKED`'s entire guarantee is the open transaction, so closing it converts `SKIP LOCKED`
into a plain `LIMIT`. Claim by writing, not by locking:

```sql
UPDATE payouts SET claimed_by = :worker, claimed_at = now()
 WHERE id IN (
   SELECT id FROM payouts WHERE claimed_by IS NULL AND status = 'ready'
   ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 50
 )
RETURNING id;                    -- the claim survives the COMMIT because it is a row state, not a lock
```

## Verifying the boundary encloses the check→act region

Mechanical, and executable on a diff:

1. **Find the act**: the single statement that changes the amount someone is entitled to or sends the
   irreversible instruction (`UPDATE … balance`, `create_order`, `charges.create`, `send_raw_transaction`).
2. **Find the check**: the read whose value the act depends on (the balance, the status, the nonce).
3. **Print the boundary.** Walk outward from *both* lines to the nearest enclosing `BEGIN` / `with
   conn.transaction()` / `session.begin()` / `@transaction.atomic` / `engine.begin()`. **If they are not the
   same block object, the lock is decorative**; a block ending between the check and the act is the defect.
4. **Confirm the block is a transaction at all.** `async with session_factory()` opens a *session*, not a
   transaction; `conn.cursor()` is not a transaction; autocommit makes every statement its own transaction,
   so `SELECT … FOR UPDATE` releases at the semicolon.
5. **Confirm the subject.** The key you locked is the key the act mutates. `FOR UPDATE` on the withdrawal row
   while broadcasting a transaction keyed on the *nonce* satisfies duration and determinism and still races.
6. **Confirm no external call is inside.** For external effects steps 3 and 6 conflict on purpose: an HTTP
   call must **not** be lexically inside a transaction block; it rolls back the intent row on exception and
   holds row locks for the counterparty's latency. Solved not by a longer transaction but by
   [persist intent → effect → outcome](#persist-intent--external-effect--persist-outcome).
7. **Test with two processes, not two threads.** A single-process test passes for every one of the six
   instances above and every defect in the next section.

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

## Persist intent → external effect → persist outcome

Two Generals: there is exactly one sound shape, and it is not a bigger transaction.

```
1. BEGIN; INSERT intent(id, idem_key, target, request_bytes, state='INFLIGHT'); COMMIT;   ← COMMIT, not flush()
2. response = provider.call(request_bytes, idempotency_key=idem_key)                      ← outside any txn block
3. BEGIN; UPDATE intent SET state='DONE'/'FAILED', provider_ref=…; <state change>; COMMIT;
```

- **`flush()` is not persistence.** Two control-experiment reps wrote the intent row inside an open
  transaction and then `db.rollback()` on the exact ambiguous timeout the row exists for: one with the
  docstring *"Reserve a local row first so we always have a record even if the Stripe call times out"*,
  contradicted four lines later by `db.rollback()`.
- **No `with session.begin()` / `engine.begin()` / `@transaction.atomic` may lexically enclose step 2.** Such
  a block rolls back on exception with no `rollback` token anywhere in the diff, so the defect is invisible to
  a grep and invisible to review. This is why step 6 of the boundary check is a separate check.
- **Crash points, and the recovery action for each** (this is the Step 10 artefact):

| Crash between | State on disk | Recovery action |
|---|---|---|
| 1 and 2 | `INFLIGHT`, no effect | Query the provider by `idem_key`; not found ⇒ re-send the stored bytes under the same key |
| inside 2 (timeout / socket close / 5xx) | `INFLIGHT`, effect **unknown** | Query by `idem_key`. Never re-send blind, never mark failed |
| 2 and 3 | `INFLIGHT`, effect **happened** | Query by `idem_key`, converge to the recorded outcome |
| inside 3 | `INFLIGHT`, effect happened | Same as above; step 3 is idempotent because it is keyed on `idem_key` |

- **Every field written pre-effect is read by the recovery path.** A startup
  `resolve_unresolved_intents()` that loads each `INFLIGHT` row, queries the counterparty with the persisted
  identity, and converges to exactly one effect. The common shape of this bug: `phase=BUY_PLACED` is journalled
  before the POST and `buy_order_id` written *after*, so resume calls `get_order(None)` → `ValueError`. The persisted
  client id (the entire point) was never read on the crash it existed for. **A persisted identity no code
  path reads back is the same defect as not persisting it.**
- **Bound the retries and terminate in a state, not a loop.** Jepsen flagged TigerBeetle clients that
  "continuously retry requests until they receive a reply" as an unresolved hazard: infinite retry converts
  definite errors into indefinite ones. The terminal state is `UNRESOLVED` plus an alert, not `FAILED`.

## The dual-write problem and the transactional outbox

A database write and a message publish as two independent operations fails two ways, **neither raising an
exception**:

```python
with db.transaction():          # FM-12
    debit_account(...)
publish("payment.debited", ...) # process dies here: the ledger moved, nothing downstream knows

publish("payment.debited", ...) # FM-13
with db.transaction():
    debit_account(...)          # constraint violation ⇒ rollback; downstream credits a debit that never happened
```

Kleppmann on the reordering variant: the two datastores "are inconsistent with each other, and they will
permanently remain inconsistent", and "you probably won't even notice … because no errors occurred."

The outbox moves the atomicity boundary inside one transaction:

```sql
CREATE TABLE outbox (
  id bigserial PRIMARY KEY, aggregate text NOT NULL,
  aggregate_id text NOT NULL,                 -- the business identity consumers dedupe on
  event_type text NOT NULL, payload jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  published_at timestamptz                    -- NULL ⇒ unpublished
);
BEGIN;                                        -- one transaction, all three writes
  UPDATE accounts SET balance_minor = balance_minor - :amt WHERE id = :src AND balance_minor >= :amt;
  INSERT INTO entries (...) VALUES (...);
  INSERT INTO outbox (aggregate, aggregate_id, event_type, payload) VALUES ('transfer', :transfer_id, 'transfer.posted', :payload);
COMMIT;
```

**What the outbox does not do: remove duplicates.** The relay reads unpublished rows, publishes, then marks
them published, and can crash between the two, so delivery is at-least-once forever. Consumers must dedupe on
the **business identity of the effect**, not the transport's message id: Stripe states that two separate
`Event` objects can describe the same underlying fact, so dedupe on `data.object.id` + `event.type`. That
dedupe row commits in the same transaction as the balance mutation it protects; an in-memory `_seen_ids` set
evaporates on restart, which is exactly when the redelivery arrives.

Do not reach for XA/2PC instead without an operator-owned transaction manager: PostgreSQL states
`PREPARE TRANSACTION` "is not intended for use in applications or interactive sessions", that a lingering
prepared transaction "continues to hold whatever locks it held" and blocks `VACUUM` to the point that it
"could cause the database to shut down to prevent transaction ID wraparound", and recommends
`max_prepared_transactions = 0`.

## Compensation is not rollback

A saga is a sequence of local transactions where a business-rule failure triggers "a series of compensating
transactions that undo the changes." microservices.io states the deficiency on the same page: sagas are **ACD,
not ACID**: "Lack of isolation (the 'I' in ACID) … concurrent execution of multiple sagas and transactions
can [cause] data anomalies." A balance can be observed and acted on in a state later compensated away.

Three properties that separate compensation from rollback:

1. **A compensation is a new economic fact, appended after the original and visible to anyone who looked in
   between**, with its own fees, FX, tax and timestamp. Never model it as an `UPDATE`/`DELETE` of the original
   posting; the original was observable, and erasing it corrupts history and breaks reconciliation.
2. **A compensation is delivered at-least-once like everything else**, so it carries its own idempotency key
   *derived from the action it compensates*. `refund(order)` executed twice refunds twice.
3. **For an economically irreversible effect there is no compensating action at all**: only a new transfer in
   the opposite direction, requiring the counterparty still to hold the funds, be solvent, and be reachable.
   After settlement finality, an on-chain send, or a cleared card capture, none of that is guaranteed, and
   the saga reaches a state with no terminal transition.

The design decision must be made **before** the irreversible step. Helland's formulation: use a *tentative
operation* with an explicit right to cancel: "Essential to a tentative operation, is the right to cancel …
Every tentative operation eventually confirms or cancels." That is **reserve → confirm/cancel**, not
**do → undo**. TigerBeetle implements exactly this natively: a `pending` transfer reserves into
`debits_pending`/`credits_pending` and leaves posted balances untouched; resolution is post, void, or expiry
by timeout and happens **exactly once** (`pending_transfer_already_posted` / `pending_transfer_already_voided`
/ `pending_transfer_expired`), the resolving transfer being a *new* record carrying a `pending_id`
back-reference. And "compensation failed" is a reachable state needing a terminal transition and a human
escalation path, not a retry loop.

## Step 9 / Step 10 artefact templates

Step 9: one row per read-then-write on an authoritative quantity. The last column is what makes the row
falsifiable: if you cannot write the interleaving, you have not found the fix.

| site (`file:line`) | isolation | lock (key · subject · released where) | retry semantics | the breaking interleaving |
|---|---|---|---|---|
| `wallet.py:212` | PG RC | conditional `UPDATE`, no lock | none needed | none (predicate is in the write; rowcount asserted) |
| `withdraw.py:88` | PG RC | `FOR UPDATE` on `withdrawals.id`; **released at the `engine.begin()` dedent, line 94**; act at line 103 | none | admin `reject()` runs between 94 and 103; broadcast lands; balance reversed too |
| `limits.py:41` | PG RR | none | none | two `INSERT`s of different positions each pass `SUM(notional) <= limit` |

Step 10 uses the crash-point table above as its four canonical rows. A row with no recovery action says the
money is unrecoverable.
