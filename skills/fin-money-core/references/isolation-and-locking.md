# Isolation and locking

The mechanism behind *concurrency on authoritative state*: the window between reading a balance and
writing it, and the boundary that has to enclose both. Engine semantics are stated per `(engine, level)`,
because the label is not the guarantee. Locks that must hold across processes, and the window between an
external effect and the local record of it, are each their own subject.

## Contents

- [Per-engine isolation: what each level actually permits](#per-engine-isolation-what-each-level-actually-permits)
- [The two shapes, as SQL you can run](#the-two-shapes-as-sql-you-can-run)
- [The three correct fixes for check-then-act, and when each applies](#the-three-correct-fixes-for-check-then-act-and-when-each-applies)
- [The retry: what a 40001 handler must re-execute](#the-retry-what-a-40001-handler-must-re-execute)
- [The decorative transaction](#the-decorative-transaction)
- [Verifying the boundary encloses the check→act region](#verifying-the-boundary-encloses-the-checkact-region)
- [Artefact template: read-then-write](#artefact-template-read-then-write)

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
   holds row locks for the counterparty's latency. Solved not by a longer transaction but by persist intent,
   external effect, persist outcome, three phases with a crash point between each pair.
7. **Test with two processes, not two threads.** A single-process test passes for every one of the six
   instances above and every defect in the next section.

## Artefact template: read-then-write

One row per read-then-write on an authoritative quantity. The last column is what makes the row
falsifiable: if you cannot write the interleaving, you have not found the fix.

| site (`file:line`) | isolation | lock (key · subject · released where) | retry semantics | the breaking interleaving |
|---|---|---|---|---|
| `wallet.py:212` | PG RC | conditional `UPDATE`, no lock | none needed | none (predicate is in the write; rowcount asserted) |
| `withdraw.py:88` | PG RC | `FOR UPDATE` on `withdrawals.id`; **released at the `engine.begin()` dedent, line 94**; act at line 103 | none | admin `reject()` runs between 94 and 103; broadcast lands; balance reversed too |
| `limits.py:41` | PG RR | none | none | two `INSERT`s of different positions each pass `SUM(notional) <= limit` |
