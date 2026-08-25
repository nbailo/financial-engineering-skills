# The stored balance: materialisation, openings, and contention

A balance no reader folds out of the journal on every read, and what that costs: the write that shares the
entry's transaction, the checkpoint that keeps a rebuild cheap, the opening posting that decides whether
per-account reconciliation works on day one, the concurrency shape of hot accounts, and the read latency that
is a correctness budget.

## Contents

1. **Materialised balances**: why they exist, how they drift, the checkpoint pattern, how to rebuild.
2. **Opening balances**: the un-backfilled opening that breaks per-account reconciliation on day one.
3. **Hot-account contention**: the four fixes, their costs, and single-writer partitioning.
4. **Balance-read latency as a correctness property**: stand-in processing.

## 1 · Materialised balances

You cannot fold the whole journal on every read. Monzo defines a balance as the sum of entries over an address
group `(legal_entity, namespace, name, currency, account_id)`; at Uber's volume (a trillion entries) that fold
is not a read path at all. Materialisation is legitimate; the constraint is *how*. **The write** goes in the
**same transaction** as the entry `INSERT`, carrying a monotonic version; Square Books caches the balance on the
book row inside the same Cloud Spanner transaction as the journal entry, with a version counter. The drift shape
is `INSERT INTO ledger_entries …; COMMIT;` then a separate `UPDATE account_balances …`. A crash or a partial
retry between them is permanent and silent.

**The verifier** is a scheduled job that recomputes order-independently and compares:

```sql
SELECT b.account_id, b.currency, b.posted_minor, COALESCE(SUM(e.amount_minor), 0) AS recomputed_minor
  FROM account_balances b LEFT JOIN ledger_entries e USING (account_id, currency)
 GROUP BY b.account_id, b.currency, b.posted_minor
HAVING b.posted_minor <> COALESCE(SUM(e.amount_minor), 0);
```

Uber runs offline order-independent checksums over time windows comparing source-of-truth to derived tables; a
single missing entry breaks the checksum. **The recompute does not fix the balance in place.** It raises a
`break` row and posts the difference to a suspense account; an in-place repair destroys the evidence of how the
drift arose and silently absorbs the next one.

**The checkpoint pattern.** Recomputing from inception does not scale either, so Monzo materialises *blocks of
consecutive entries with a stored running sum*, only for hot balances:

```sql
CREATE TABLE balance_checkpoints (
  account_id uuid NOT NULL, currency char(3) NOT NULL,
  through_seq bigint NOT NULL,          -- append sequence, NOT effective_at
  running_sum_minor bigint NOT NULL,
  PRIMARY KEY (account_id, currency, through_seq)
);
-- rebuild = latest checkpoint + the tail after it
SELECT c.running_sum_minor + COALESCE(SUM(e.amount_minor), 0)
  FROM balance_checkpoints c
  LEFT JOIN ledger_entries e ON e.account_id = c.account_id
        AND e.currency = c.currency AND e.seq > c.through_seq
 WHERE (c.account_id, c.currency, c.through_seq) IN (
         SELECT account_id, currency, MAX(through_seq) FROM balance_checkpoints
          WHERE account_id = $1 AND currency = $2 GROUP BY 1, 2)
 GROUP BY c.running_sum_minor;
```

**Checkpoints are keyed on the append sequence, never on `effective_at`.** A back-dated entry arrives after a
checkpoint whose `through_seq` already covers it in append order but not in economic order: correct for the
current balance, wrong for an as-of balance, which folds entries with
`WHERE effective_at <= T AND (discarded_at IS NULL OR discarded_at >= T)` and uses no checkpoint. Uber's entity
changelog can recreate an entity's ledger since inception; **if you cannot drop `account_balances` and rebuild
it from `ledger_entries`, you do not have a materialised balance, you have a second source of truth.**

## 2 · Opening balances

A per-account reconciliation compares `SUM(entries)` against an independently-read figure. If the ledger's
history starts at a migration cutover and the pre-cutover balance was never posted, **every account with a
non-zero legacy balance breaks on the first run**: the job fires thousands of alerts on day one, someone mutes the
channel, and the control is dead before it detects anything. The reconciliation SQL can be flawless and the
control still dead on arrival: the defect is in the opening data the job reads, never in the query.

The fix is a posting, not a special case in the reconciler. Beancount's `Equity:Opening-Balances` exists so a
truncated history still balances: one balanced transaction per account, dated at the cutover.

```sql
-- migration, runs BEFORE the reconciliation job is first scheduled; two legs per transaction
-- (customer account, equity:opening-balances), idempotency key 'opening:<account>:<currency>'
INSERT INTO ledger_transactions (id, effective_at, posting_type, idempotency_key)
SELECT gen_uuid(), '2026-01-01T00:00:00Z', 'opening_balance',
       'opening:' || l.account_id || ':' || l.currency
  FROM legacy_balances l WHERE l.balance_minor <> 0;
```

Two properties to test: after the migration `SUM(entries) == legacy_balance` for **every** account, and the
opening transactions themselves net to zero per currency against the equity account. Then run the
reconciliation in CI against a **freshly-migrated** database seeded with one known discrepancy, and assert it
produces exactly one `break` row and one alert, so an un-backfilled opening fails the test rather than muting
production.

*Measured: the near-miss wrote flawless reconciliation SQL, ran it nowhere, and its per-account comparison
was broken on day one by un-backfilled openings. Transfer and reversal arithmetic is written correctly
unaided; journals that do not balance and reconciliations that never run ship at close to 100%.*

## 3 · Hot-account contention

Fee, tax, FX-liquidity and clearing accounts appear in a large fraction of transactions, so contention is
structural. TigerBeetle states it directly: business transactions *"don't shard well"* and row locks on hot
accounts *"bring the system's performance to a crawl."* Sharding by account id does not help; the hot account
sits on one side of nearly every transfer.

| fix | mechanism | cost |
|---|---|---|
| Pessimistic | `SELECT … FOR UPDATE` on the account row, then update | serialises every transfer touching the hot account; the throughput ceiling |
| Higher isolation | Repeatable Read / Serializable | obliges a **generalized** `SQLSTATE 40001` retry of the whole transaction; PostgreSQL warns you cannot predict which will conflict |
| Optimistic version | caller passes the expected `lock_version` per entry; mismatch → rollback and fail (Modern Treasury) | pushes the retry to the caller; needs a typed failure (`balance_lock_failure`) the caller can act on |
| Predicate in the write | conditional `UPDATE … WHERE`, TigerBeetle `balancing_debit`/`balancing_credit`, or the 3-transfer balance-conditional linked chain | no read-then-write at all; limited to predicates the write can express |

**Single-writer partitioning** is the fifth answer and the one that scales for the hot side. Uber uses
**serialized batch writes** on hot ledger entities; TigerBeetle executes all transfers sequentially on one core
under strict serializability, the only isolation level it offers. Route every posting touching a hot account
through one writer keyed on `(account_id, currency)`, let it accumulate N postings in a short window, and apply
**one** balance mutation per batch; the journal still receives N immutable entry rows, because the batching is
on the materialised balance only. Beyond throughput, that writer is the natural home for the per-currency
conservation check on the set, and one writer per key makes the balance row's monotonic version trivially
correct.

## 4 · Balance-read latency as a correctness property

Monzo's stated reason for materialising: delayed balance reads force card **stand-in processing**, which risks
*"unauthorized negative balances or missed fraud checks."* When the authoritative read misses the network's
deadline the fallback is not "a slower answer"; it is *approve without checking*, by a component you do not
control. Latency on the authorising read is a correctness budget; the response to blowing it is a decline policy
you chose, not a stand-in you inherited.
