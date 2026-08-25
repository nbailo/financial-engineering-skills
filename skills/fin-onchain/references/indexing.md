# Event indexing, backfills, and cursor discipline

This reference covers how to read a range of chain history and prove you read all of it, which is the half of
indexing that fails silently. It carries the provider range and response caps and how to read them from
configuration rather than hardcoding one, adaptive halving and multi-provider failover, the cursor-guard
asymmetries that let a fresh deploy skip every deposit, the four-part dedupe identity, and the classes of
value movement that emit no log at all and are therefore invisible to a log-only indexer.

## Contents

- **Provider caps are conditional**: Alchemy's per-chain, per-tier `eth_getLogs` table; go-ethereum's own
  structural limits; reading the cap from configuration keyed on `(provider, tier, chainId)`.
- **Completeness checking**: the classification table for what a response means; adaptive halving and the
  terminating case; why an empty array and a hole are indistinguishable without the cap.
- **Provider failover**: `fallback([http(a), http(b)])` in viem, `FallbackProvider` in ethers; why a single
  transport turns one provider's outage into a permanent gap; disagreement at the head.
- **Cursor discipline**: the guard is the write; the empty-address-set sprint; the stale-address-map sprint;
  the compare-and-set advance; backfill overlap as the cheap margin; one iteration in order.
- **Dedupe identity**: the four-column unique constraint, the DDL, why `(txHash, logIndex)` is wrong in two
  directions, the unreversed-twin assertion, and `(txHash, traceId)` for internal transfers.
- **Value that emits no log**: contract-initiated native transfers; the trace methods and their
  availability; Etherscan's separate `txlistinternal` endpoint; the balance-diff fallback and its confound.
- **Self-transfers and internal flows**: the `from_address` assertion, the gas-top-up that credits a
  customer, and why the address set must be queried rather than snapshotted.
- **Reorg-aware storage layout**: the block table, the columns the credit table needs, and what becomes
  impossible when each one is missing.
- **Head handling**: indexing lag versus credit-policy depth; the monotonic-head guard; EIP-1898 pinned
  reads and the two error codes.
- **Grep list**

## Provider caps are conditional

There is no number to hardcode. The cap is a function of `(provider, tier, chain)` and at least one provider
publishes cells in that table that differ by two orders of magnitude.

| Source | Limit | Shape |
|---|---|---|
| Alchemy, free tier | **10 blocks**, every chain | block range |
| Alchemy, PAYG | **unlimited** on major chains; **1,000** on some; **10,000** on others | block range, per chain |
| Alchemy, any tier | **150 MB** | response size |
| go-ethereum `eth/filters/api.go` | `errBlockHashWithRange`: `blockHash` may not be combined with `fromBlock`/`toBlock` | structural |
| go-ethereum `eth/filters/api.go` | `maxTopics`, `logQueryLimit` on topic and address list sizes | structural |

Two consequences the code has to carry. **(1)** A self-hosted node publishes no per-tier range table at all;
the limits you meet there are the structural ones above plus your own RPC timeout, so "it worked in staging
against our own geth" proves nothing about the managed endpoint in production. **(2)** `unlimited` is not the
absence of a limit; it moves the binding constraint to the 150 MB response cap, which is a function of how
busy the range was, not of how wide it was. A range that returned 8,000 logs yesterday can fail today.

```python
# Configuration, not a constant. Absent cell => refuse to start, do not guess.
LOG_RANGE_CAP: dict[tuple[str, str, int], int | None] = {
    ("alchemy", "free", 1):     10,      # documented: 10 blocks, every chain, free tier
    ("alchemy", "payg", 1):     None,    # documented unlimited on major chains
    ("alchemy", "payg", 8453):  None,
    # None => no block-range cap; completeness is proven by response size, never by width
}

def range_cap(provider: str, tier: str, chain_id: int) -> int | None:
    try:
        return LOG_RANGE_CAP[(provider, tier, chain_id)]
    except KeyError:
        raise RuntimeError(f"no documented eth_getLogs cap for {provider}/{tier}/{chain_id}")
```

## Completeness checking

An `eth_getLogs` response carrying zero logs and an `eth_getLogs` call that never covered the range are the
same bytes on the wire. The only thing that separates them is a predicate you evaluate before you commit
progress.

| Observation | Classification | Action |
|---|---|---|
| HTTP 200, `len(logs) < cap`, range width ≤ configured cap | **covered** | commit, advance the cursor |
| Any transport error, timeout, non-200, JSON-RPC error object | **hole** | halve and retry; never advance |
| Provider range rejection (any message) | **hole** | halve and retry |
| Response-size rejection | **hole** | halve and retry |
| `len(logs) ==` a documented maximum **result count** | **hole** | halve and retry |
| Range width > the configured cap for this `(provider, tier, chain)` | **programmer error** | refuse to issue the call |

Do not pattern-match the provider's error *message* to decide whether the failure was a range problem. The
strings are undocumented, differ per provider and change without notice; every error is a hole, and the same
halving ladder resolves all of them. What you gain by classifying the message is one round trip; what you
lose by classifying it wrong is a customer's deposit.

```python
def fetch_range(client, addresses, from_block: int, to_block: int, cap: int | None) -> list[Log]:
    """Returns logs ONLY for a range provably covered. Raises otherwise; never returns []
    to mean 'could not read'."""
    width = to_block - from_block + 1
    if cap is not None and width > cap:
        raise ProgrammerError(f"width {width} exceeds configured cap {cap}")
    try:
        logs = client.get_logs(address=addresses, from_block=from_block, to_block=to_block)
    except (RpcError, TransportError, TimeoutError):
        if from_block == to_block:
            raise UncoverableBlock(from_block)      # escalate: page by address subset, or page and alarm
        mid = from_block + (to_block - from_block) // 2
        return (fetch_range(client, addresses, from_block, mid, cap)
                + fetch_range(client, addresses, mid + 1, to_block, cap))
    if MAX_RESULT_COUNT is not None and len(logs) >= MAX_RESULT_COUNT:
        raise Truncated(from_block, to_block)       # caller halves; a single block that truncates escalates
    return logs
```

The termination case matters more than the halving. At width 1 there is nothing left to halve, so a
single-block failure must **escalate** (split the address list, split the topic filter, or stop the indexer
and page) and must never fall through to a `return []`. A recursion whose base case returns an empty list is
the same bug as the unconditional cursor advance, one level down.

## Provider failover

One transport means one provider's outage is one indefinite hole, and every retry in the loop is spent
against the endpoint that is down.

```ts
// viem
import { createPublicClient, fallback, http } from 'viem'
const client = createPublicClient({
  chain: mainnet,
  transport: fallback([http(PRIMARY_URL), http(SECONDARY_URL)], { retryCount: 2 }),
})
```

```ts
// ethers v6
const provider = new ethers.FallbackProvider(
  [{ provider: new ethers.JsonRpcProvider(PRIMARY_URL),   priority: 1, weight: 1 },
   { provider: new ethers.JsonRpcProvider(SECONDARY_URL), priority: 2, weight: 1 }],
  network, { quorum: 1 },
)
```

Failover buys availability and costs consistency, and the cost lands exactly where indexers are weakest.
Providers sit at different heights; a fallback that moves a request from provider A to provider B mid-loop can
return a head **lower** than the head you already observed, and a naive indexer reads that as a rollback (see
*Head handling*). Two independent providers disagreeing about the log set of a range **below the finalized
head** is not a retryable condition; it is a break: alarm, and do not credit from either answer until it is
resolved by a third read. Sampling this deliberately (re-read 1-in-N finalized ranges through the secondary
and compare the four-part identity set) is the cheapest reconciliation an indexer can run, and it is the only
thing that detects a provider quietly serving a pruned or partial log index.

## Cursor discipline

The rule is *proven coverage before the cursor advances*: **a watermark moves only inside the same conditional and the same transaction that
verifiably covered the range.** Two failure shapes, both found in real generated indexers, both silent.

```ts
// WRONG: the guard wraps the query, the advance runs unconditionally.
if (addresses.size > 0) {
  const logs = await client.getLogs({ address: [...addresses], fromBlock, toBlock })
  await creditAll(tx, logs)
}
await saveCursor(tx, { lastBlock: toBlock })
```

On a fresh deploy `deposit_addresses` is empty, so the query never runs, and the cursor sprints to the safe
head anyway. Every address registered afterwards can never see a deposit in a passed block, and with no
backfill job that is permanent. Nothing raises. The second shape is the same asymmetry in time:
`loadDepositAddresses()` snapshotted once per outer loop while an inner drain runs for hours, so addresses
registered during the drain are absent from the filter while the cursor advances past their funding blocks.
**Re-read every set the loop filters on at the loop's own cadence.**

Make the advance a compare-and-set, so the guard *is* the write and a second worker cannot double-advance:

```sql
-- one transaction: credits, block rows, and this UPDATE. Proceed only on rowcount = 1.
UPDATE indexer_cursor
   SET last_block = $2,          -- to_block, proven covered
       last_hash  = $3,          -- hash of to_block, for the next iteration's parent check
       updated_at = now()
 WHERE chain_id   = $1
   AND last_block = $4;          -- from_block - 1: the range you actually started from
```

Rowcount 0 means someone else moved the cursor: roll back, re-read, do not retry blindly. Because the advance
and the credits share a transaction, a crash between them is impossible in either direction.

Backfill overlap is then free and should be deliberate. Re-reading a covered range must be a **no-op**, which
is exactly what the four-part unique constraint below buys you; so start every iteration at
`last_block - OVERLAP + 1` rather than `last_block + 1`. The overlap costs duplicate reads and buys coverage
of the one-block window where a crash landed between the RPC response and the commit.

One iteration, in order:

1. `head ← eth_blockNumber`; reject and alarm if `head < last_observed_head` (monotonic-head guard).
2. `to_block ← min(head - INDEX_LAG, last_block + range_cap)`; return if `to_block < from_block`.
3. Re-read the address set (cadence: every iteration).
4. Fetch the block headers for `from_block … to_block`; assert `header[n].parent_hash == stored_hash[n-1]`,
   starting from `last_hash`. A mismatch exits to the unwind path, not to the credit path.
5. `fetch_range(...)`: raises rather than returning `[]` on any hole.
6. **One transaction:** insert block rows, insert log rows with `ON CONFLICT DO NOTHING RETURNING id`, post
   credits for the rows that actually inserted, compare-and-set the cursor. Commit.

## Dedupe identity

```sql
CREATE TABLE deposit_log (
  id            bigserial     PRIMARY KEY,
  chain_id      integer       NOT NULL,
  block_hash    bytea         NOT NULL,
  block_number  bigint        NOT NULL,
  tx_hash       bytea         NOT NULL,
  log_index     integer       NOT NULL,
  from_address  bytea         NOT NULL,
  to_address    bytea         NOT NULL,
  token         bytea         NOT NULL,
  amount        numeric(78,0) NOT NULL,   -- uint256 max is 78 decimal digits; never float, never bigint
  observed_at   timestamptz   NOT NULL DEFAULT now(),
  reversed_by   bigint        REFERENCES deposit_log(id),
  CONSTRAINT deposit_log_identity UNIQUE (chain_id, block_hash, tx_hash, log_index)
);
CREATE INDEX deposit_log_replay ON deposit_log (chain_id, tx_hash, log_index) WHERE reversed_by IS NULL;
```

`(tx_hash, log_index)` under a real unique constraint is above the bad bar and still wrong in **two
directions**:

| Direction | Mechanism | Result |
|---|---|---|
| Under-credit | The orphaned row survives the reorg. The re-included log collides with it and `ON CONFLICT DO NOTHING` refuses to replace it | The re-included deposit is **never credited**; the chain says the customer paid |
| Over-credit | The transaction is re-included in a different block at a **different** `logIndex` | The constraint passes, and the deposit is credited **twice** |

The four-part key fixes the first and fixes the second **only if the unwind has already landed**. A reorg
detected late (the credit written, the parent-hash check not yet run) produces a fresh row that satisfies
the constraint and credits again. So the credit path carries a second, explicit assertion: no *unreversed*
twin exists for the transaction-level identity.

```sql
-- inside the same transaction as the credit
WITH ins AS (
  INSERT INTO deposit_log (chain_id, block_hash, block_number, tx_hash, log_index, ...)
  VALUES ($1, $2, $3, $4, $5, ...)
  ON CONFLICT ON CONSTRAINT deposit_log_identity DO NOTHING
  RETURNING id
)
SELECT id FROM ins;
-- Zero rows returned means the exact log identity was already stored: stop, credit nothing.
```

`ON CONFLICT DO NOTHING` returning zero rows is the *only* reliable signal that the insert was a duplicate;
`rowcount` from a plain `INSERT … DO NOTHING` is 0 in both the "conflicted" and the "nothing to do" case, and
a separate `SELECT` before the insert is a TOCTOU that two concurrent workers both pass. Then, before
crediting:

```sql
SELECT 1 FROM deposit_log
 WHERE chain_id = $1 AND tx_hash = $2 AND log_index = $3
   AND reversed_by IS NULL AND id <> $4
 LIMIT 1;   -- any row => a live credit for this log already exists; this is a re-include, not a new deposit
```

**Internal transfers do not have a `logIndex`**; they are not logs. Their identity is `(chainId, tx_hash,
trace_id)`: a single transaction routinely contains several internal transfers to your addresses, so the
parent hash alone credits four transfers once, or credits the same one twice when it arrives from two
endpoints (`docs.etherscan.io/api-reference/endpoint/txlistinternal`). Give them their own table with their
own unique constraint; do not force them into the log table with a synthetic `log_index`.

## Value that emits no log

| Movement | ERC-20 `Transfer` log | Top-level tx with `to == addr` | Trace / internal-tx endpoint |
|---|---|---|---|
| ERC-20 transfer to a deposit address | yes | no (`to` is the token) | n/a |
| Native transfer, EOA → deposit address | **no** | yes | yes |
| Native transfer, contract → deposit address (router, Safe, another exchange's batching contract) | **no** | **no** | yes |
| Native transfer inside a failed internal call, successful parent | **no** | **no** | yes, with its own error flag |

Row 3 is the deposit an exchange systematically loses. A user withdrawing from a DEX router, a smart-contract
wallet, or another exchange's batch payout sends native currency by `CALL`; there is no event to index and the
transaction's `to` is the router, not you. An indexer built from `Transfer` logs plus a scan of
`block.transactions[].to` sees nothing, and the failure surfaces as a support ticket and a manual credit.

Three detection paths, in the order you should prefer them:

1. **Traces**: `debug_traceBlockByNumber` with `{"tracer": "callTracer"}`, or `trace_block` on clients that
   expose the OpenEthereum trace namespace. Both are debug namespaces: they are not enabled on every node,
   not offered on every provider tier, and are the most expensive call in the indexer. Confirm availability
   against the endpoint you will actually run on **before** designing the credit path around them.
2. **A block explorer's internal-transaction endpoint**: Etherscan exposes these through
   `action=txlistinternal`, a **separate** endpoint from `action=txlist`. Records carry `hash`, `traceId` and
   their own `isError`. Two things follow: `hash` is not a unique key, and a successful parent transaction can
   contain a failed internal call, so `receipt.status == 0x1` does not mean this internal value moved.
3. **Balance diffing**: `eth_getBalance(addr, n) − eth_getBalance(addr, n-1)`, pinned by block hash. Usable
   only for addresses you never *send* from, because gas spent by the address itself confounds the delta; on
   a pure deposit address (the forwarder case, where sweeps are initiated by a different signer) it is exact
   and cheap. It gives you an amount, not a counterparty, so it cannot feed the self-transfer check below on
   its own.

## Self-transfers and internal flows

**Before crediting, assert `from_address NOT IN (our deposit addresses ∪ our hot / warm / cold addresses)`.**
Moving value between two addresses you own emits a real `Transfer` to a real deposit address; unchecked, it
mints a customer credit with no matching debit. The `from_address` column usually already exists in the schema
and is never read.

The highest-volume instance is your own sweep machinery, in both directions:

- **Gas top-up, hot wallet → deposit address.** On an account+forwarder model the sweep is paid in native
  currency the deposit address does not hold, so the hot wallet funds it first. That transfer's `to` is a
  customer's deposit address and its amount is real. A native-deposit detector with no `from_address` check
  credits the customer with your gas, every sweep, forever.
- **Sweep, deposit address → hot wallet.** Correctly not a customer credit, but it is the transaction that
  makes the deposit-address balance drop to zero, so any reconciliation that compares credited balances
  against deposit-address balances must net it out on the same key.

Query the address tables inside the credit transaction. Do **not** load the set into a process-local `Set` at
boot: that is the stale-address-map sprint again, and a forwarder deployed an hour ago will not be in it.

```sql
SELECT EXISTS (SELECT 1 FROM owned_address WHERE chain_id = $1 AND address = $2) AS is_ours;
-- $2 = deposit_log.from_address. is_ours => record the row, post no customer credit.
```

Record the row either way. A self-transfer you drop entirely is a hole in the reconciliation that asserts
`Σ credited == Σ observed value deltas to deposit addresses`; a self-transfer you record and classify is a
line item that nets to zero.

## Reorg-aware storage layout

```sql
CREATE TABLE indexed_block (
  chain_id     integer NOT NULL,
  number       bigint  NOT NULL,
  hash         bytea   NOT NULL,
  parent_hash  bytea   NOT NULL,
  PRIMARY KEY (chain_id, number)
);
-- before processing block n:  assert header(n).parent_hash == indexed_block(chain_id, n-1).hash
```

`eth_getLogs` **never** sets `removed: true`; in go-ethereum `Removed` is set only inside the reorg path
feeding `core.RemovedLogsEvent`, which serves subscriptions and `eth_getFilterChanges`; `FilterAPI.GetLogs`
never sets it. A polling indexer gets no reorg signal at all, so the parent-hash chain above is not a
belt-and-braces addition; it is the entire detector.

| Missing column | What becomes impossible |
|---|---|
| `indexed_block.hash` | Detecting any reorg deeper than the confirmation lag. Ever. There is no recovery path that does not involve a rebuild |
| `indexed_block.parent_hash` | Detecting a reorg without an extra header fetch per block; and detecting one at the *first* block after a restart |
| `deposit_log.chain_id` | Distinguishing the same `tx_hash` on two chains; deterministic deployments and replayed transactions make this a live collision, not a theoretical one |
| `deposit_log.reversed_by` | Distinguishing "never credited" from "credited then reversed". This is the column the unreversed-twin assertion reads; without it, re-crediting after a reorg is a coin flip between double-credit and no credit |
| depth + policy recorded with the credit | Answering "which credits were below finality when this reorg landed" without re-deriving the whole history |

Retain `indexed_block` rows to at least your rollback floor. graph-node's `ETHEREUM_REORG_THRESHOLD` defaults
to **250** blocks, with the comment *"Blocks cannot be reverted below the reorg threshold"*; history below
the threshold is pruned, so a deeper reorg is an **unrecoverable-state halt** requiring a rebuild from a
snapshot, not a rollback. Prune your block table to a shallower depth than your rollback window and you have
imported that halt without configuring it.

## Head handling

Three different numbers, routinely collapsed into one, and collapsing them is how an indexer becomes both slow
and unsafe:

| Number | Protects | Set from |
|---|---|---|
| `INDEX_LAG`: how far behind `head` you index | The indexer's own rollback work | The reorg depth you are willing to unwind cheaply |
| Credit-policy depth | Customer money | A stated reorg-loss budget, per chain and per amount |
| `OVERLAP`: how far back each iteration re-reads | Crash-window coverage | One iteration's worth of blocks, plus margin |

Index shallow and credit deep. Setting `INDEX_LAG` to the credit depth delays every downstream view for no
safety gain, because the credit gate is a separate predicate on the row you already stored.

**Monotonic-head guard.** Across a load-balanced pool or a fallback transport, consecutive requests hit nodes
at different heights. If `eth_blockNumber` returns a head below the last observed head, discard the response;
do not treat it as a rollback and do not unwind anything. Only a parent-hash mismatch is a reorg.

**Pin state reads to the log's block.** If you process a log from block N and then `eth_call` at `latest`, you
may read state from a different fork; EIP-1898's stated motivation is exactly this: *"if there is a re-org in
between when the balance of the sender is queried via `eth_getBalance` and when the balance of the recipient
is queried, the balances may not reconcile."* Pass the block-hash parameter instead:

```json
{"method": "eth_call",
 "params": [{"to": "0x…", "data": "0x70a08231…"},
            {"blockHash": "0x…", "requireCanonical": true}]}
```

Two error codes are specified and both are information you want: **`-32000`**, the block is not canonical and
you asked for canonical, i.e. the fork you indexed is gone; **`-32001`**, block not found, i.e. this node has
not seen that block yet (usually height skew, not a reorg). A `-32000` here is the reorg notification
`eth_getLogs` refused to give you. Not every provider implements EIP-1898; where it is missing, re-fetch the
block by hash and compare it against `indexed_block` before trusting a `latest` read.

## Grep list

| Literal in the diff | What to check |
|---|---|
| `getLogs`, `get_logs`, `getPastLogs`, `createEventFilter` | Is the range cap read from config keyed on `(provider, tier, chain)`? Is every error a hole? |
| `maxBlockRange`, `CHUNK_SIZE`, `BLOCK_BATCH` | Chunking is the passing half. The failing half is what happens when a chunk errors |
| `saveCursor`, `last_processed_block`, `watermark`, `checkpoint` | Same conditional and same transaction as the range that covered it; compare-and-set, not blind `SET` |
| `if (addresses.size > 0)`, `if not addresses: return` | Does the advance sit inside the same guard? |
| `loadDepositAddresses()`, a `Set` built at boot | Re-read at the loop's cadence, or a mid-drain registration is lost |
| `ON CONFLICT DO NOTHING` | Is the four-part constraint named? Is `RETURNING` used to detect the duplicate? |
| `UNIQUE (tx_hash, log_index)` | Wrong in both directions; add `chain_id` and `block_hash` |
| `log.removed`, `removed === true` | Never set by `eth_getLogs`; the detector is the parent-hash chain |
| `block_hash`, `parent_hash` absent from the schema | The indexer cannot detect a reorg deeper than its lag, ever |
| `from_address` present and never read | The self-transfer credit; the gas top-up is the high-volume case |
| `block.transactions` iteration, `tx.to == addr` | Misses every contract-initiated native deposit |
| `debug_traceBlock`, `trace_block`, `txlistinternal` | Availability on the actual endpoint; `(hash, traceId)` identity; independent `isError` |
| `eth_call` / `readContract` at `latest` inside a log handler | Pin to the log's `blockHash` with `requireCanonical: true` |
