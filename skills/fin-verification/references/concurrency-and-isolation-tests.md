# Concurrency on a money row: the barrier double-spend

The test that finds a lost update across two transactions is a two-connection barrier test, run at the
isolation level the money transaction actually runs at.

## Concurrency: the barrier double-spend, and the narrow band for loom/jcstress

**A lost update across two transactions is not a data race, and no race detector will ever find it.** Go's
detector "only finds races that happen at runtime, so it can't find races in code paths that are not executed";
loom cannot see any type that is not a loom replacement type; jcstress is probabilistic. None of them observes
two database connections. The canonical double-spend is a transaction-isolation defect: `SELECT balance` →
check in application code → `UPDATE balance = computed`.

PostgreSQL's documented boundary is the decisive detail. Under READ COMMITTED a single
`UPDATE accounts SET balance = balance - :amt WHERE id = :id AND balance >= :amt` is safe (the row is re-fetched
and the `WHERE` re-evaluated against the updated version) while the SELECT-then-compute-then-UPDATE form is not.
Under REPEATABLE READ / SERIALIZABLE, "applications using this level must be prepared to retry transactions due
to serialization failures" (SQLSTATE `40001`), and an untested retry path is where a "safe" isolation level
becomes a dropped payment.

Write the reproduction as a **two-connection barrier test**, never as a loop of threads hoping to hit the window:

```python
def test_concurrent_debit_cannot_overdraw(pg_dsn):
    barrier = threading.Barrier(2)
    results = []
    def attempt():
        with psycopg.connect(pg_dsn) as c:
            c.execute("BEGIN ISOLATION LEVEL READ COMMITTED")
            c.execute("SELECT balance FROM accounts WHERE id = %s", (ACC,))   # both read 100
            barrier.wait()                                                     # both are now inside
            try:
                results.append(debit(c, ACC, 100))                             # the code under test
                c.commit()
            except SerializationFailure:                                       # 40001: a legal outcome
                c.rollback(); results.append("retry")
    ts = [threading.Thread(target=attempt) for _ in range(2)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert balance(ACC) == 0                                       # exactly one debit landed
    assert sorted(results) in ([False, True], ["retry", True])     # the loser failed, typed
```

The barrier makes it deterministic, and therefore a regression test rather than a flake. Assert the final
balance **and** that the loser got a typed failure: a silent overwrite and a rejected second debit both leave
exactly one transaction reporting success.

**loom and jcstress apply to one thing: hand-written lock-free data structures.** loom exhaustively permutes
concurrent executions under the C11 memory model with state reduction (based on CDSChecker); jcstress
(`@JCStressTest`, `@State`, `@Actor`, `@Result`, `@Outcome`) is probabilistic and "requires substantial time to
catch all the cases." Almost no financial application code contains a lock-free structure; if your concurrency
is a database transaction or a mutex, both report nothing, and loom will not even see the code.
`fc.scheduledModelRun` is the closer analogue for async application code; it explores promise-resolution
orderings, which is where a JS money path's interleaving bugs live.
