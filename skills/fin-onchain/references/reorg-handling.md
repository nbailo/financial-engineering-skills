# Reorg detection, unwind, and rollback floors

A history that can be rewritten does not reliably tell you it was rewritten, so the detector is something you
store rather than something you receive.

## Contents

- **Reorg detection**: parent-hash chaining as the only transport-independent detector; why
  `eth_getLogs` never sets `removed: true` (go-ethereum `core.RemovedLogsEvent` vs `FilterAPI.GetLogs`);
  why subscriptions lose removals across reconnects and failovers; EIP-1898 block-hash-pinned reads.
- **Reorg unwind**: the reversing balancing entry keyed on the orphaned log identity; the three failure
  shapes (mass debit of legitimate deposits, `CHECK (amount >= 0)` wedging the retry loop, delete-and-
  re-credit blocked by `ON CONFLICT DO NOTHING`); the unreversed-twin assertion on re-credit.
- **Rollback floors**: graph-node `ETHEREUM_REORG_THRESHOLD` default 250 and "Blocks cannot be reverted
  below the reorg threshold"; recognising an unrecoverable-state halt and the rebuild path it implies.
- **Bitcoin Core's reorg feed**: `listsinceblock` re-walking from a fork point, `include_removed`, and the
  trap that re-added transactions appear in `removed` with a positive confirmation count; negative
  `confirmations` (`-6` = conflicted six blocks ago) and the unsigned-column bug it produces.

## Reorg detection

**`eth_getLogs` never sets `removed: true`.** In go-ethereum, `Removed` (`core/types/log.go`) is set only
inside the reorg path feeding `core.RemovedLogsEvent` (`core/blockchain.go`, `collectReceiptsAndLogs`), which
serves `eth/filters` subscriptions and `eth_getFilterChanges`; `eth/filters/api.go`'s `GetLogs` never sets
it. A polling indexer receives **no reorg signal at all**, worth stating loudly, because the opposite claim
appears in vendor docs, blog posts and model output. Ponder makes the field useless in the other direction:
it handles reorgs internally, so in its handler view `removed` is always `false` (partially verified: from
the docs index, not a dedicated page).

Subscriptions are not a substitute: removals are emitted only by the node that *itself* processed the reorg
while you were connected, so a reconnect, provider failover, node restart or load-balanced pool loses them
entirely, and web3.js issue #1766 documents `removed=true` delivered **twice** for the same log, neither
guaranteed-once nor guaranteed-at-least-once. `newHeads` + fetch-logs-by-`blockHash` is better than range
polling (pinned, EIP-1898-compatible) but still delivers no removals; parent-hash chaining stays mandatory.

The only detector that survives every transport is parent-hash chaining:

```sql
CREATE TABLE indexed_block (
  chain_id     BIGINT      NOT NULL,
  height       BIGINT      NOT NULL,
  block_hash   BYTEA       NOT NULL,
  parent_hash  BYTEA       NOT NULL,
  processed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (chain_id, height)
);
```

```python
def accept_block(cur, chain_id: int, blk) -> None:
    cur.execute("SELECT block_hash FROM indexed_block WHERE chain_id=%s AND height=%s",
                (chain_id, blk.number - 1))
    row = cur.fetchone()
    if row is None:
        raise GapError(chain_id, blk.number - 1)          # a hole, not a fresh start
    if row[0] != blk.parent_hash:
        common = walk_back_to_common_ancestor(cur, chain_id, blk)   # bounded by ROLLBACK_FLOOR
        unwind_to(cur, chain_id, common)                  # reversing entries, see below
    process(cur, blk)
```

Three things this code must get right:

- **`walk_back_to_common_ancestor` is bounded**: past the rollback floor it raises rather than walking on.
- **Head regression is not a reorg.** A load-balanced pool serving height H then H−3 is indistinguishable
  from a reorg. Guard with "head must not regress" and re-query the *same* provider before unwinding, or
  cross-provider skew rolls back correct data.
- **Reads must be pinned to the fork the log came from.** EIP-1898 supplies
  `{"blockHash": "0x…", "requireCanonical": true}` as the block parameter; its motivation is exactly this
  failure: *"if there is a re-org in between when the balance of the sender is queried via `eth_getBalance`
  and when the balance of the recipient is queried, the balances may not reconcile."* A non-canonical block
  with `requireCanonical: true` returns error **`-32000`**; block-not-found returns **`-32001`**. graph-node
  passes the block hash to `eth_call` for this reason. An `eth_call` at `latest` after processing a log from
  block N reads a possibly different fork, and the stored price or TVL becomes a mixture of two histories
  that never coexisted.

Vendors publishing their detector agree: QuickNode Streams flags a reorg when *"a new block's parent hash
does not match the hash of the previous block that was streamed"*, re-delivers those blocks, and requires
Postgres sinks to upsert. TRM adds semantic **and positional** dedup, positional because indices shift when
a transaction is re-included in a different block.

## Reorg unwind

An unwind is a **reversing balancing entry keyed on the orphaned log identity**, never a delete, never an
in-place edit, never a bare debit of the user's balance. The dedupe identity is all four of
`(chain_id, block_hash, tx_hash, log_index)`, under one unique constraint.

```sql
-- unwind: one reversing entry per orphaned credit, idempotent on the orphaned identity
INSERT INTO ledger_entry (txn_id, account_id, direction, amount, reverses_entry_id, reason)
SELECT gen_random_uuid(), e.account_id,
       CASE e.direction WHEN 'credit' THEN 'debit' ELSE 'credit' END,
       e.amount, e.id, 'reorg_unwind'
  FROM ledger_entry e
  JOIN deposit_credit d ON d.entry_id = e.id
 WHERE d.chain_id = $1 AND d.block_hash = $2
   AND NOT EXISTS (SELECT 1 FROM ledger_entry r WHERE r.reverses_entry_id = e.id);
```

Three shapes to design out:

| Shape | Mechanism | Symptom |
|---|---|---|
| Mass debit of legitimate deposits | the revert path debits every credit in a block range, then `ON CONFLICT … DO NOTHING` refuses to re-insert the re-included ones | customers debited for deposits that are still on chain, permanently un-re-creditable |
| `CHECK (amount >= 0)` aborts the reversal | the reversing entry is written as a negative amount into a column with a non-negativity check | the unwind transaction aborts, the indexer retries forever, the double-count stands and the pipeline stops advancing |
| Rollback deeper than the floor | the walk-back exceeds the indexer's retained history | not a rollback: an unrecoverable-state halt (next section) |

The four-part key avoids a double credit **only if the unwind already landed**. A reorg detected late (the
replacement block processed before the orphan is reversed) produces a row with a different `block_hash` that
passes the unique constraint and credits twice. So the credit path carries a second assertion:

```sql
-- before crediting a (possibly) re-included log
SELECT 1 FROM deposit_credit d
 WHERE d.chain_id = $1 AND d.tx_hash = $2 AND d.log_index = $3
   AND NOT EXISTS (SELECT 1 FROM ledger_entry r WHERE r.reverses_entry_id = d.entry_id);
-- a row here means an unreversed twin exists: unwind first, or skip; never credit.
```

When the credit had not yet reached policy depth, the reversal unwinds the PENDING (unavailable) posting,
which is the whole point of staging: a reorg below the credit depth never touches a spendable balance. Above
the depth, it debits a balance that may already be gone; that is the loss `reorg_loss_budget` priced.

## Rollback floors

Every indexer has a depth below which it cannot revert, and the failure is silent unless you assert on it.
graph-node's is explicit: `ETHEREUM_REORG_THRESHOLD` defaults to **250** blocks in `graph/src/env/mod.rs`,
commented *"Blocks cannot be reverted below the reorg threshold."* History below the threshold is pruned, so
the rollback is not merely disabled; the data required to perform it is gone.

Set the floor deeper than the worst observed reorg **for that specific chain** and alarm when a reorg
approaches it. Polygon's 157-block reorg fits inside a 250-block window; a 64-block window on Polygon
does not.

Exceeding the floor is a **halt**, not a best-effort partial rollback:

```python
if (head - common_ancestor) > ROLLBACK_FLOOR:
    mark_chain_state(chain_id, "UNRECOVERABLE")   # stop crediting, stop advancing the cursor
    page(f"reorg depth {head - common_ancestor} > floor {ROLLBACK_FLOOR} on chain {chain_id}")
    raise UnrecoverableIndexerState(chain_id, common_ancestor)
```

Resuming means rebuilding from a snapshot at or below the common ancestor, or from genesis, not restarting
the process. The default alternative is worse: the indexer crashes, restarts, resumes on the new chain, and
carries stale entities from the orphaned one forward indefinitely. If latency matters, run a fast unfinalized
view for UX and a settled finalized view for accounting, and reconcile money against the settled view only.

## Bitcoin Core's reorg feed

`listsinceblock` re-walks from the fork point if the supplied blockhash has left the main chain, and with
`include_removed` returns a `removed` array. The trap is documented verbatim in
`src/wallet/rpc/transactions.cpp`:

> *"transactions that were re-added in the active chain will appear as-is in this array, and may thus have a
> positive confirmation count."*

Debiting everything in `removed` therefore reverses credits that are still valid. The correct read is per
entry:

```python
for tx in result["removed"]:
    if tx["confirmations"] > 0:
        continue                      # re-mined into the active chain; still a valid credit
    reverse_credit(tx["txid"])        # genuinely orphaned
```

The second trap is the sign of `confirmations`, from the same file: *"Negative confirmations means the
transaction conflicted that many blocks ago."* So `-6` is the worst state a transaction can be in, not a
near-miss. Two bugs follow mechanically:

- `abs(confirmations) >= N` turns a transaction conflicted six blocks ago into a fully confirmed one.
- an unsigned column (`INTEGER UNSIGNED`, `u32`, protobuf `uint32`) either rejects the write or wraps it to a
  very large positive number, which then trivially clears any depth threshold.

Store it signed and gate on `confirmations >= N`, with no absolute value anywhere in the expression.
