# An ERC-20 deposit indexer that credits customer accounts

A polling service that watches USDC `Transfer` events into a set of per-customer deposit addresses and
credits the matching internal balance. Every exchange, custodian and on-ramp has one; it is usually the
second thing built after the withdrawal path, by whoever is comfortable with the RPC layer. The version
below is realistic code written under time pressure, and the one-line summary of what it hides is:
**double-crediting is closed and under-crediting is wide open.**

---

## Before

```sql
-- migrations/001_deposits.sql
CREATE TABLE deposit_addresses (address text PRIMARY KEY, user_id text NOT NULL);
CREATE TABLE indexer_cursor    (id int PRIMARY KEY, last_block bigint NOT NULL);
CREATE TABLE balances          (user_id text PRIMARY KEY, amount numeric NOT NULL DEFAULT 0);

CREATE TABLE deposits (
    id           bigserial PRIMARY KEY,
    tx_hash      text      NOT NULL,
    log_index    integer   NOT NULL,
    block_number bigint    NOT NULL,
    user_id      text      NOT NULL,
    amount       numeric   NOT NULL,
    UNIQUE (tx_hash, log_index)
);
```

```typescript
// indexer.ts: credit USDC deposits to customer accounts.
import { createPublicClient, http, parseAbiItem, getAddress } from "viem";
import { mainnet } from "viem/chains";
import { Pool } from "pg";
import pino from "pino";

const log = pino();

const CONFIRMATIONS = 12n;
const MAX_BLOCK_RANGE = 2_000n;
const POLL_MS = 12_000;
const USDC = getAddress(
  process.env.USDC_ADDRESS ?? "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
);

const TRANSFER = parseAbiItem(
  "event Transfer(address indexed from, address indexed to, uint256 value)",
);

const client = createPublicClient({
  chain: mainnet,
  transport: http(process.env.RPC_URL!),
});
const pool = new Pool({ connectionString: process.env.DATABASE_URL });

async function loadDepositAddresses(): Promise<Map<string, string>> {
  const { rows } = await pool.query("SELECT address, user_id FROM deposit_addresses");
  return new Map(rows.map((r) => [getAddress(r.address), r.user_id]));
}

async function tick(): Promise<void> {
  const head = await client.getBlockNumber();
  const safeHead = head - CONFIRMATIONS;

  const cursor = await pool.query("SELECT last_block FROM indexer_cursor WHERE id = 1");
  let fromBlock = BigInt(cursor.rows[0].last_block) + 1n;

  const addresses = await loadDepositAddresses();

  while (fromBlock <= safeHead) {
    const span = fromBlock + MAX_BLOCK_RANGE - 1n;
    const toBlock = span < safeHead ? span : safeHead;

    const tx = await pool.connect();
    try {
      await tx.query("BEGIN");

      if (addresses.size > 0) {
        const logs = await client.getLogs({
          address: USDC,
          event: TRANSFER,
          args: { to: [...addresses.keys()] as `0x${string}`[] },
          fromBlock,
          toBlock,
        });

        for (const l of logs) {
          if (l.removed) continue; // dropped by a reorg

          const userId = addresses.get(getAddress(l.args.to!));
          if (!userId) continue;

          const amount = Number(l.args.value!) / 1e18; // wei -> units

          await tx.query(
            `INSERT INTO deposits (tx_hash, log_index, block_number, user_id, amount)
             VALUES ($1, $2, $3, $4, $5)
             ON CONFLICT (tx_hash, log_index) DO NOTHING`,
            [l.transactionHash, l.logIndex, Number(l.blockNumber), userId, amount],
          );
          await tx.query(
            `UPDATE balances SET amount = amount + $1 WHERE user_id = $2`,
            [amount, userId],
          );
        }
      }

      // credit and cursor advance commit together
      await tx.query("UPDATE indexer_cursor SET last_block = $1 WHERE id = 1", [
        Number(toBlock),
      ]);
      await tx.query("COMMIT");
    } catch (err) {
      await tx.query("ROLLBACK");
      log.error({ err, fromBlock, toBlock }, "range failed; moving on");
    } finally {
      tx.release();
    }

    fromBlock = toBlock + 1n;
  }
}

async function main(): Promise<void> {
  for (;;) {
    await tick().catch((err) => log.error({ err }, "tick failed"));
    await new Promise((r) => setTimeout(r, POLL_MS));
  }
}

void main();
```

---

## What the suite catches

| Defect | Rule | What actually happens | Loss shape |
|---|---|---|---|
| The cursor advances to `toBlock` with no completeness check | `fin-money-core`: *durable dedupe*. `fin-onchain`: *A range of external history is covered only when you can prove it, and the cursor advances inside the proof* | `getLogs` is called once per range and the result is trusted. Providers cap results (Alchemy publishes a per-chain, per-tier table, and the response is separately capped at 150 MB), and at the cap the range is not covered. There is no result-count check, no adaptive halving, and a single transport, so one provider's outage is indistinguishable from a quiet range. | **Permanent silent under-crediting.** A customer's deposit vanishes with no error and no log line, and nothing will ever look at that range again. |
| `catch { ROLLBACK; log.error("moving on") }` then `fromBlock = toBlock + 1n` | `fin-money-core`: *durable dedupe*. `fin-onchain`: *A range of external history is covered only when you can prove it, and the cursor advances inside the proof* | The rollback correctly leaves the cursor where it was, and then the loop advances anyway, so the *next* successful range writes a `last_block` past the failed one. The skipped range is never revisited. | Same as above, but triggered by any transient RPC failure, which is a daily event. |
| `if (addresses.size > 0)` guards the query; the advance runs unconditionally | `fin-money-core`: *durable dedupe*. `fin-onchain`: *A range of external history is covered only when you can prove it, and the cursor advances inside the proof* | On a fresh deploy with an empty `deposit_addresses` table, the cursor sprints from genesis to the safe head in a few minutes. With no backfill job, **every address registered afterwards can never see a deposit in a passed block.** The same shape sits one line up: `addresses` is snapshotted once per outer tick while the inner drain runs for hours, so an address registered mid-drain is invisible for the rest of it. | Total, permanent, and it lands on day one of production, the day the address table is empty. |
| `UNIQUE (tx_hash, log_index)` | `fin-onchain`: *The dedupe key separates the same event twice from the same event on a different branch* | This has a real unique constraint and is above the bad bar, and it deliberately excludes block identity, which is what makes re-crediting after a reorg impossible: the re-included log collides with the orphaned row and `ON CONFLICT DO NOTHING` refuses to replace it. In the other direction, a transaction re-included in a different block at a different `logIndex` passes the constraint and credits twice. | Under-credit on re-inclusion at the same index; double-credit on re-inclusion at a different one. Both silent. |
| `Number(l.args.value!) / 1e18` | `fin-onchain`: *The credited amount comes from the asset's own accounting, not from the notification that announced it*. `fin-money-core`: *exact representation* | USDC has **6** decimals. The constant is 18 because it was copied from an ETH indexer, and the comment says "wei", which is the tell. A 1,000 USDC deposit credits `0.000000001`. `Number()` on a `bigint` separately loses precision above 2^53. The token address is `process.env.USDC_ADDRESS ?? …`, so the asset can change without the exponent changing. | Every deposit under-credited by a factor of 10^12, on 100% of events, until someone opens a support ticket. |
| `if (l.removed) continue` | `fin-onchain`: *A history that can be rewritten does not reliably tell you it was rewritten*. `fin-money-core`: *A comment is a claim*. `fin-verification`: *The design notes are a numbered list of claims, each bound to a test or deleted* | `eth_getLogs` **never** sets `removed`. In go-ethereum that field is set only inside the reorg path feeding `core.RemovedLogsEvent`, which serves subscriptions and `eth_getFilterChanges`; `FilterAPI.GetLogs` never sets it. A polling indexer receives no reorg signal at all. The line is dead code that reads as reorg handling, and no `block_hash` is stored anywhere, so a reorg deeper than the confirmation lag is undetectable forever. | Whatever a reorg does, undetected. The line's real cost is that it stops anyone from writing the code that would work. |

---

## After

```sql
-- migrations/002_deposit_credits.sql
CREATE TABLE deposit_credits (
    id                 bigserial     PRIMARY KEY,
    chain_id           integer       NOT NULL,
    block_number       bigint        NOT NULL,
    block_hash         bytea         NOT NULL,
    tx_hash            bytea         NOT NULL,
    log_index          integer       NOT NULL,
    token              bytea         NOT NULL,
    token_decimals     smallint      NOT NULL,
    user_id            text          NOT NULL,
    amount_raw         numeric(78,0) NOT NULL,   -- the token's OWN base units
    confirmations      integer       NOT NULL,   -- recorded with the credit
    reorg_budget       text          NOT NULL,
    reverses_credit_id bigint        REFERENCES deposit_credits(id),
    created_at         timestamptz   NOT NULL DEFAULT now()
);

-- The dedupe identity is four-part, and it applies to credits only, so a reorg
-- unwind can post a reversing row against the same log identity.
CREATE UNIQUE INDEX deposit_credits_identity
    ON deposit_credits (chain_id, block_hash, tx_hash, log_index)
 WHERE reverses_credit_id IS NULL;
CREATE UNIQUE INDEX deposit_credits_one_reversal
    ON deposit_credits (reverses_credit_id) WHERE reverses_credit_id IS NOT NULL;
CREATE INDEX deposit_credits_twin
    ON deposit_credits (chain_id, tx_hash, log_index);

-- The reorg signal we have to build ourselves, because the chain never sends one.
CREATE TABLE processed_blocks (
    chain_id    integer NOT NULL,
    number      bigint  NOT NULL,
    hash        bytea   NOT NULL,
    parent_hash bytea   NOT NULL,
    PRIMARY KEY (chain_id, number)
);
```

```typescript
// indexer.ts
import {
  createPublicClient, erc20Abi, fallback, getAddress, hexToBytes, http, parseAbiItem,
  type Address, type Log,
} from "viem";
import { mainnet } from "viem/chains";
import { Pool, type PoolClient } from "pg";
import pino from "pino";

const log = pino();

function requireEnv(name: string): string {
  const v = process.env[name];
  if (v === undefined) throw new Error(`${name} must be set`);
  return v;
}

async function alert(text: string): Promise<void> {
  log.error({ text }, "ALERT");
  await fetch(ALERT_SINK, { method: "POST", body: JSON.stringify({ text }) });
}

const CHAIN_ID = mainnet.id;

// The result cap is per-provider AND per-tier AND per-chain. Alchemy's published
// table is 10 blocks on the free tier for every chain, and unlimited / 1,000 / 10,000
// on PAYG depending on the chain. Read it from config; never hardcode one number.
const LOG_RESULT_CAP = Number(requireEnv("RPC_LOG_RESULT_CAP"));
const MAX_BLOCK_RANGE = BigInt(requireEnv("RPC_MAX_BLOCK_RANGE"));

// Confirmation depth is a loss budget, not a constant: the depth comes from a stated
// reorg-loss budget, and both are recorded with the credit. "12 confirmations" is
// folklore; Polygon has produced a 157-block reorg.
const CONFIRMATIONS = BigInt(requireEnv("DEPOSIT_CONFIRMATIONS"));
const REORG_BUDGET = requireEnv("DEPOSIT_REORG_BUDGET");
const ROLLBACK_FLOOR = Number(requireEnv("DEPOSIT_ROLLBACK_FLOOR"));

// Reconciliation: the alert destination has no default, so import fails rather than
// alerting into a void.
const ALERT_SINK = requireEnv("INDEXER_ALERT_SINK");

const TRANSFER = parseAbiItem(
  "event Transfer(address indexed from, address indexed to, uint256 value)",
);

// One provider's outage must not be a silent gap.
const client = createPublicClient({
  chain: mainnet,
  transport: fallback([
    http(requireEnv("RPC_URL_PRIMARY")),
    http(requireEnv("RPC_URL_SECONDARY")),
  ]),
});
const pool = new Pool({ connectionString: requireEnv("DATABASE_URL") });

class IncompleteRange extends Error {}
class UnrecoverableReorg extends Error {}

// ---------------------------------------------------------------- the asset, pinned

const TOKENS = new Map<Address, { symbol: string; decimals: number }>();

/** Scale is read from the contract at runtime and cached per (chainId, address).
 *  An env-overridable token address plus a hardcoded exponent is exactly how a
 *  6-decimal token gets credited on an 18-decimal assumption.
 *
 *  DEPOSIT_TOKENS is a record, not a list of addresses:
 *    [{"address":"0x…","feeOnTransfer":false,"rebasing":false}]
 *  Those two flags are not discoverable from the contract, so they are a human entry
 *  made at onboarding. They are load-bearing: `Transfer.value` is the asset's own
 *  accounting for that transfer only where the asset neither rebases nor takes a fee
 *  inside the transfer. An unrecorded token does not credit. */
async function pinTokens(): Promise<void> {
  for (const t of JSON.parse(requireEnv("DEPOSIT_TOKENS"))) {
    const address = getAddress(t.address);
    if (t.feeOnTransfer !== false || t.rebasing !== false) {
      throw new Error(`${address}: Transfer.value is not this asset's own accounting`);
    }
    const [symbol, decimals] = await Promise.all([
      client.readContract({ address, abi: erc20Abi, functionName: "symbol" }),
      client.readContract({ address, abi: erc20Abi, functionName: "decimals" }),
    ]);
    TOKENS.set(address, { symbol, decimals });
    log.info({ address, symbol, decimals }, "pinned token");
  }
}

// ------------------------------------------------------------ completeness, provable

/** Return logs only for a range we can prove we covered.
 *
 *  Any error, provider range rejection, or result count at the documented cap is a
 *  HOLE, not an empty result. Halve and retry; if a single block still cannot be
 *  proved complete, throw, because the caller must not advance past it. */
async function getLogsComplete(
  fromBlock: bigint, toBlock: bigint, tokens: Address[], recipients: Address[],
): Promise<Log[]> {
  const out: Log[] = [];
  const stack: Array<[bigint, bigint]> = [[fromBlock, toBlock]];

  while (stack.length > 0) {
    const [lo, hi] = stack.pop()!;
    let logs: Log[];
    try {
      logs = await client.getLogs({
        address: tokens, event: TRANSFER,
        args: { to: recipients }, fromBlock: lo, toBlock: hi,
      });
    } catch (err) {
      if (lo === hi) throw new IncompleteRange(`block ${lo}: ${String(err)}`);
      const mid = lo + (hi - lo) / 2n;
      stack.push([mid + 1n, hi], [lo, mid]);
      continue;
    }
    if (logs.length >= LOG_RESULT_CAP) {
      if (lo === hi) throw new IncompleteRange(`block ${lo} exceeds the result cap`);
      const mid = lo + (hi - lo) / 2n;
      stack.push([mid + 1n, hi], [lo, mid]);
      continue;
    }
    out.push(...logs);
  }
  out.sort((a, b) =>
    a.blockNumber! === b.blockNumber!
      ? a.logIndex! - b.logIndex!
      : Number(a.blockNumber! - b.blockNumber!));
  return out;
}

// ------------------------------------------------------------------ reorg detection

/** `eth_getLogs` never signals a reorg, so we chain block identity ourselves.
 *
 *  We store (number, hash, parent_hash) for every block we credited in AND for every
 *  range boundary, then re-read those blocks by number. A reorg replaces a contiguous
 *  suffix of the chain, so any fork below a stored boundary changes that boundary's
 *  hash, so checking the stored set is sufficient and costs a handful of getBlock
 *  calls rather than one per block. */
async function findForkPoint(tx: PoolClient): Promise<bigint | null> {
  const { rows } = await tx.query(
    `SELECT number, hash FROM processed_blocks
      WHERE chain_id = $1 ORDER BY number DESC LIMIT $2`,
    [CHAIN_ID, ROLLBACK_FLOOR],
  );
  if (rows.length === 0) return null;

  for (const row of rows) {
    const onChain = await client.getBlock({ blockNumber: BigInt(row.number) });
    if (Buffer.from(hexToBytes(onChain.hash!)).equals(row.hash)) {
      return BigInt(row.number);
    }
    log.warn({ block: row.number }, "block identity mismatch; walking back");
  }
  // A rollback deeper than the indexer's floor is an unrecoverable-state halt, not a
  // rollback. graph-node's ETHEREUM_REORG_THRESHOLD carries the same comment.
  throw new UnrecoverableReorg(`deeper than the ${ROLLBACK_FLOOR}-block floor`);
}

/** An unwind is a reversing entry keyed on the orphaned log identity. Never a DELETE,
 *  never an in-place edit, never a bare debit of the user's balance. */
async function unwind(tx: PoolClient, forkPoint: bigint): Promise<void> {
  const { rowCount } = await tx.query(
    `INSERT INTO deposit_credits
        (chain_id, block_number, block_hash, tx_hash, log_index, token,
         token_decimals, user_id, amount_raw, confirmations, reorg_budget,
         reverses_credit_id)
     SELECT c.chain_id, c.block_number, c.block_hash, c.tx_hash, c.log_index, c.token,
            c.token_decimals, c.user_id, -c.amount_raw, c.confirmations,
            c.reorg_budget, c.id
       FROM deposit_credits c
      WHERE c.chain_id = $1 AND c.block_number > $2 AND c.reverses_credit_id IS NULL
        AND NOT EXISTS (SELECT 1 FROM deposit_credits r WHERE r.reverses_credit_id = c.id)`,
    [CHAIN_ID, Number(forkPoint)],
  );
  await tx.query(
    `DELETE FROM processed_blocks WHERE chain_id = $1 AND number > $2`,
    [CHAIN_ID, Number(forkPoint)],
  );
  await tx.query(
    `UPDATE indexer_cursor SET last_block = $1 WHERE id = 1`, [Number(forkPoint)],
  );
  await alert(`reorg unwound to ${forkPoint}: ${rowCount} credits reversed`);
}

// -------------------------------------------------------------------------- the tick

async function tick(): Promise<boolean> {
  const head = await client.getBlockNumber();
  const safeHead = head - CONFIRMATIONS;

  const cursorRead = await pool.query("SELECT last_block FROM indexer_cursor WHERE id = 1");
  const lastBlock = BigInt(cursorRead.rows[0].last_block);
  const fromBlock = lastBlock + 1n;
  if (fromBlock > safeHead) return false;

  // Re-read the address set every iteration, not once per outer loop. An inner drain
  // runs for hours and an address registered mid-drain must be visible.
  const addressRows = await pool.query("SELECT address, user_id FROM deposit_addresses");
  const addresses = new Map<Address, string>(
    addressRows.rows.map((r) => [getAddress(r.address), r.user_id]),
  );

  // A branch that skips the work skips the advance. With an empty address table on a
  // fresh deploy, the alternative is a cursor that sprints to the head and permanently
  // strands every address registered afterwards.
  if (addresses.size === 0) {
    log.warn({ fromBlock }, "no deposit addresses; cursor held at last_block");
    return false;
  }

  const span = fromBlock + MAX_BLOCK_RANGE - 1n;
  const toBlock = span < safeHead ? span : safeHead;
  const tokens = [...TOKENS.keys()];

  // Completeness is established BEFORE the transaction opens; no RPC round trip is
  // held inside it. If this throws, nothing was committed and nothing advanced.
  const logs = await getLogsComplete(fromBlock, toBlock, tokens, [...addresses.keys()]);

  // Block identity for every block we are about to credit in, plus the range boundary.
  // Fetched here so no RPC round trip is held open inside the write transaction.
  const wanted = [...new Set([...logs.map((l) => l.blockNumber!), toBlock])];
  const headers = await Promise.all(
    wanted.map((n) => client.getBlock({ blockNumber: n })),
  );

  const tx = await pool.connect();
  try {
    await tx.query("BEGIN");

    // The cursor row is locked for the whole check -> act section, and the act mutates
    // the key that was locked.
    const held = await tx.query(
      "SELECT last_block FROM indexer_cursor WHERE id = 1 FOR UPDATE");
    if (BigInt(held.rows[0].last_block) !== lastBlock) {
      await tx.query("ROLLBACK");
      return true; // someone else moved it; re-derive and try again
    }

    const forkPoint = await findForkPoint(tx);
    if (forkPoint !== null && forkPoint < lastBlock) {
      await unwind(tx, forkPoint);
      await tx.query("COMMIT");
      return true;
    }

    for (const l of logs) {
      const to = getAddress((l as any).args.to as Address);
      const userId = addresses.get(to);
      if (userId === undefined) continue;

      const from = getAddress((l as any).args.from as Address);
      // Moving tokens between two addresses we own is not a deposit.
      if (addresses.has(from) || (await isHouseAddress(tx, from))) {
        log.info({ tx: l.transactionHash }, "internal transfer; not credited");
        continue;
      }

      const token = getAddress(l.address as Address);
      const meta = TOKENS.get(token)!;

      // The four-part key only avoids the double credit once the unwind has landed.
      // Assert no UNREVERSED credit exists for this log before crediting a re-included
      // one.
      const twin = await tx.query(
        `SELECT c.id FROM deposit_credits c
          WHERE c.chain_id = $1 AND c.tx_hash = $2 AND c.log_index = $3
            AND c.reverses_credit_id IS NULL
            AND NOT EXISTS (
                SELECT 1 FROM deposit_credits r WHERE r.reverses_credit_id = c.id)`,
        [CHAIN_ID, hexToBytes(l.transactionHash!), l.logIndex],
      );
      if (twin.rowCount! > 0) {
        log.warn({ tx: l.transactionHash }, "unreversed twin exists; not crediting");
        continue;
      }

      // The amount is stored in the token's own base units, as an exact integer.
      // Nothing divides. Display scaling happens at the edge, from token_decimals.
      const inserted = await tx.query(
        `INSERT INTO deposit_credits
            (chain_id, block_number, block_hash, tx_hash, log_index, token,
             token_decimals, user_id, amount_raw, confirmations, reorg_budget)
         VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
         ON CONFLICT DO NOTHING RETURNING id`,
        [CHAIN_ID, Number(l.blockNumber), hexToBytes(l.blockHash!),
         hexToBytes(l.transactionHash!), l.logIndex, hexToBytes(token),
         meta.decimals, userId, ((l as any).args.value as bigint).toString(),
         Number(CONFIRMATIONS), REORG_BUDGET],
      );
      if (inserted.rowCount === 0) continue; // already credited from this exact block

      // Staging, SEAM S2(iv): observed, final and spendable are three quantities. The
      // credit posts to a PENDING account and moves to AVAILABLE at the policy's
      // finality, and only AVAILABLE authorises a withdrawal.
      await creditPending(tx, userId, token, (l as any).args.value as bigint,
                          inserted.rows[0].id);
    }

    for (const h of headers) {
      await tx.query(
        `INSERT INTO processed_blocks (chain_id, number, hash, parent_hash)
         VALUES ($1,$2,$3,$4) ON CONFLICT (chain_id, number) DO NOTHING`,
        [CHAIN_ID, Number(h.number), hexToBytes(h.hash!), hexToBytes(h.parentHash)],
      );
    }

    // The advance is inside the same conditional and the same transaction that covered
    // the range. There is no other statement in this file that writes it.
    await tx.query("UPDATE indexer_cursor SET last_block = $1 WHERE id = 1",
                   [Number(toBlock)]);
    await tx.query("COMMIT");
    return true;
  } catch (err) {
    await tx.query("ROLLBACK");
    await alert(`indexer range ${fromBlock}-${toBlock} failed: ${String(err)}`);
    throw err; // stop the loop. A caught error must never advance the cursor.
  } finally {
    tx.release();
  }
}

async function main(): Promise<void> {
  await pinTokens();
  for (;;) {
    let progressed = true;
    while (progressed) progressed = await tick();
    await new Promise((r) => setTimeout(r, Number(requireEnv("POLL_MS"))));
  }
}

void main();
```

And the reconciliation, because none of the above is worth anything if nobody is checking. This is
`fin-onchain`'s *The chain-to-ledger reconciliation names the authority and the join key, and runs in
production*, `fin-money-core`'s *reconciliation*, and `fin-verification`'s *A detector that has never
detected is not known to detect*:

```typescript
// reconcile.ts: scheduled. Compares what we credited against what the chain shows.
//   0 */1 * * *  node dist/reconcile.js
export async function reconcile(): Promise<Break[]> {
  const finalized = await client.getBlock({ blockTag: "finalized" });
  const breaks: Break[] = [];

  for (const [address, userId] of await depositAddresses()) {
    for (const [token, meta] of TOKENS) {
      // Independent path: the chain's own view, not our log table.
      const onChain = await client.readContract({
        address: token, abi: erc20Abi, functionName: "balanceOf", args: [address],
        blockNumber: finalized.number!,
      });
      const { rows } = await pool.query(
        `SELECT COALESCE(SUM(amount_raw), 0)::text AS credited
           FROM deposit_credits
          WHERE chain_id = $1 AND user_id = $2 AND token = $3 AND block_number <= $4`,
        [CHAIN_ID, userId, hexToBytes(token), Number(finalized.number)],
      );
      const credited = BigInt(rows[0].credited);
      const swept = await sweptOut(userId, token, finalized.number!);
      // Transfers this service deliberately did not credit still move the chain
      // balance. Leaving house and internal inflows out of the comparison manufactures
      // a break on every sweep-back, and a reconciliation that cries wolf gets muted,
      // which costs more than the breaks it was built to find.
      const internal = await internalIn(userId, token, finalized.number!);
      const ours = credited + internal - swept;
      if (ours !== onChain) {
        breaks.push({ userId, token, ours, chain: onChain });
      }
    }
  }
  if (breaks.length > 0) await alert(`deposit reconciliation: ${breaks.length} breaks`);
  return breaks;
}
```

---

## What changed, and what did not

**Changed.** The `getLogs` call became a range that halves itself until every sub-range returns strictly
below the provider's configured cap, over a two-provider `fallback` transport, and it throws rather than
returning a short answer. The `catch … moving on` became a `throw`: a failed range stops the loop, and
nothing in the file writes `last_block` except the one statement inside the covered branch. The empty
address set now returns without advancing, and the address map is re-read every iteration. The dedupe
identity became `(chainId, blockHash, txHash, logIndex)` with an unreversed-twin assertion on the credit
path. Amounts are stored as the token's own base-unit integers with the `decimals()` read from the
contract at startup. `if (l.removed)` was deleted and replaced with stored block identity, a fork-point
search, and a reversing unwind, which is *An unwind is a reversing entry keyed on what it reverses, never
an erasure*. A transfer whose sender is an address the house controls no longer credits, under *Value
arriving from an address you control is not income*, and `reconcile.ts` counts those inflows so a
sweep-back does not manufacture a break on every comparison.

**Not changed, deliberately.** The confirmation-depth gate was already correct and stayed. It gained a
recorded budget, not a new mechanism. Credit and cursor already committed in one transaction; that was
already correct and was left alone. The polling loop is still a polling loop: no websocket subscription
was added, because subscription-delivered `removed` logs are lost by a reconnect, a provider failover or a
node restart, and web3.js #1766 documents the same removal delivered twice. The
service is still one process with one cursor; no sharding, no queue.

**Closed by the asset's own semantics, not by a measurement.** The ingest path credits `Transfer.value`
and never measures a `balanceOf` delta. Under *The credited amount comes from the asset's own accounting,
not from the notification that announced it* that is the correct source rather than a shortcut: for an
ERC-20 that neither rebases nor takes a fee inside the transfer, `value` **is** the protocol's accounting
for that transfer. A measured delta is the other mechanism, and off-chain it attributes correctly only
where the movement is isolated in the window measured. `balanceOf` is a state read at a block boundary, so
an indexer crediting one `Transfer` out of a block containing three cannot attribute a per-log delta from
state reads at all.

What the two flags in `DEPOSIT_TOKENS` buy is exactly that reading, and they are a human entry rather than
a discovery: `symbol()` and `decimals()` come off the contract, transfer semantics do not. An unrecorded
token throws at startup instead of crediting. `reconcile.ts` is the cross-check that would surface a
mistake in that entry as a break rather than as silence.

**Not implemented, and named.** The per-block assertion that `Σ credited == balanceOf(addr)` delta across
that block, both reads pinned by block hash, quarantining the address on a mismatch. That is the layer
that would *prevent* a bad credit rather than detect it an hour later, and `reconcile.ts` is an hourly,
detect-only substitute for it.

**Still absent.** There is no handling for contract-originated native transfers, which *Inclusion is not
effect* covers, because this service indexes ERC-20 logs only and native deposits go through a different
address set. The memo and destination-tag half of *The chain model decides the identity rule, and
flattening it is wrong half the time* does not apply on EVM and would be required on the first
tag-addressed chain added.

---

## The output the review ends with

Every credit lands on a `user_id` and a customer eats the error: an under-credit is their deposit that
never arrived, and a double-credit is house money they can withdraw. The chain holds the record of what
moved and answers questions about it, so for inclusion, the branch and the value that arrived authority is
EXTERNAL and `balanceOf` at the finalized height is what this service is compared against. Two quantities
are not on the chain: the liability the books carry for each customer, and which customer owns a deposit
address at all. No oracle holds either, so both are SELF, and their evidence is replay over this service's
own history rather than a comparison with anybody. The stakes rise again the moment this service feeds a
withdrawal path, which is what the PENDING to AVAILABLE staging exists to gate.

`fin-onchain` asks for its fuller crossing contract when you hold the signing keys, when exposure is
`customer` on a crediting or broadcast path, or when the change spans more than one of identity, coverage,
finality and amount. This service holds no keys and broadcasts nothing, and it spans all four, so the
anchors below are the ones that contract would have carried.

`EVIDENCE` names functions rather than lines, because the code under review is a listing in this file. In a
real response every one of them is a `file:line`.

```
authority: MIXED · exposure: customer
  inclusion, branch and arrived value    EXTERNAL (Ethereum mainnet)
  customer liabilities, address mapping  SELF

FINDING   A customer's deposit vanishes with no error and no log line, and nothing will ever look at that
          range again. Permanent silent under-crediting.
WHY       `getLogs` is called once per range and the result is trusted. Providers cap results (Alchemy
          publishes a per-chain, per-tier table, and the response is separately capped at 150 MB), and at
          the cap the range is not covered. There is no result-count check, no adaptive halving, and a
          single transport, so one provider's outage is indistinguishable from a quiet range.
EVIDENCE  indexer.ts tick(), the single getLogs call and the unconditional cursor UPDATE that follows it
FIX       indexer.ts getLogsComplete() classifies every response as covered, rejected, failed or at the
          documented cap, halves and retries, and throws rather than returning a short answer; the cap and
          the range come from config (RPC_LOG_RESULT_CAP, RPC_MAX_BLOCK_RANGE) rather than a constant, and
          the transport is a two-provider fallback.
TEST      A range whose provider returns exactly the cap is subdivided rather than accepted, and a range
          that cannot be proved complete at single-block width throws instead of advancing the cursor.

FINDING   Any transient RPC failure, a daily event, permanently skips a block range: the next successful
          range writes a `last_block` past the failed one and nothing revisits it.
WHY       The rollback correctly leaves the cursor where it was, and then `fromBlock = toBlock + 1n`
          advances the loop anyway. A branch that skipped the work did not skip the advance.
EVIDENCE  indexer.ts tick(), the catch clause logging "range failed; moving on" and the loop increment
          below it
FIX       The catch alerts and rethrows, stopping the loop: indexer.ts tick(). No statement in the file
          writes `last_block` except the one inside the branch that covered the range.
TEST      A failed range leaves the cursor unchanged and is retried whole on the next tick.

FINDING   On a fresh deploy with an empty `deposit_addresses` table the cursor sprints from genesis to the
          safe head in minutes, so every address registered afterwards can never see a deposit in a passed
          block. Total, permanent, and it lands on day one of production.
WHY       `if (addresses.size > 0)` guards the query while the advance runs unconditionally, and there is
          no backfill job. The same shape sits one line up: `addresses` is snapshotted once per outer tick
          while the inner drain runs for hours, so an address registered mid-drain is invisible for the
          rest of it.
EVIDENCE  indexer.ts tick(), the `addresses.size > 0` guard and the loadDepositAddresses() call outside
          the drain loop
FIX       An empty address set returns without advancing, and the address map is re-read every iteration:
          indexer.ts tick().
TEST      With no deposit addresses registered the cursor does not move, and an address registered
          mid-drain is credited for blocks indexed after its registration.

FINDING   A reorged deposit is either under-credited or credited twice, silently. Re-inclusion at the same
          index is refused; re-inclusion at a different index credits again.
WHY       `UNIQUE (tx_hash, log_index)` is a real constraint that excludes block identity. The re-included
          log collides with the orphaned row and `ON CONFLICT DO NOTHING` refuses to replace it; a
          transaction re-included in a different block at a different `logIndex` passes the constraint
          entirely.
EVIDENCE  migrations/001_deposits.sql deposits UNIQUE (tx_hash, log_index)
FIX       A four-part identity that carries the branch, unique on unreversed credits only, so an unwind can
          post a reversing row against the same log:
          migrations/002_deposit_credits.sql deposit_credits_identity, plus the unreversed-twin assertion
          in indexer.ts tick() that stops a re-included log crediting before its unwind has landed.
TEST      A log re-included on a different branch credits exactly once after the unwind, and re-including
          it at a different logIndex does not produce a second credit.

FINDING   Every deposit is under-credited by a factor of 10^12, on 100% of events, until someone opens a
          support ticket.
WHY       USDC has 6 decimals. The constant is 18 because it was copied from an ETH indexer, and the
          comment says "wei", which is the tell: a 1,000 USDC deposit credits `0.000000001`. `Number()` on
          a `bigint` separately loses precision above 2^53, and `process.env.USDC_ADDRESS ?? …` lets the
          asset change without the exponent changing.
EVIDENCE  indexer.ts tick(), `const amount = Number(l.args.value!) / 1e18`
FIX       Store the token's own base units as an exact integer and never divide on the ingest path:
          migrations/002_deposit_credits.sql amount_raw numeric(78,0), with `decimals()` and `symbol()`
          read from the contract at startup and cached per (chainId, address) in indexer.ts pinTokens().
          Display scaling happens at the edge, from the recorded token_decimals.
TEST      A 1,000 USDC deposit credits 1_000_000_000 base units, and a token entered without both
          semantics flags recorded false fails startup rather than crediting.

FINDING   A reorg deeper than the confirmation lag is undetectable forever, and the line that looks like
          reorg handling is what stops anyone writing the code that would work.
WHY       `eth_getLogs` never sets `removed`. In go-ethereum that field is set only inside the reorg path
          feeding `core.RemovedLogsEvent`, which serves subscriptions and `eth_getFilterChanges`;
          `FilterAPI.GetLogs` never sets it. A polling indexer receives no reorg signal at all, and no
          `block_hash` is stored anywhere.
EVIDENCE  indexer.ts tick(), `if (l.removed) continue; // dropped by a reorg`
FIX       Build the signal: store (number, hash, parent_hash) for every block credited in and every range
          boundary in migrations/002_deposit_credits.sql processed_blocks, search for the fork point in
          indexer.ts findForkPoint(), unwind with a reversing row keyed on reverses_credit_id in
          indexer.ts unwind(), and halt on indexer.ts UnrecoverableReorg below the configured rollback
          floor rather than rolling back past it.
TEST      A fork below the last processed block is detected by stored block identity alone, produces
          reversing rows rather than deletes, and a fork deeper than DEPOSIT_ROLLBACK_FLOOR halts instead
          of silently continuing.

UNRESOLVED: planted-break test proving reconcile.ts detects a discrepancy (it is scheduled and has never
been shown to fire; a detector that has never detected is not known to detect)
UNRESOLVED: the per-block assertion that credited value equals the balanceOf delta for that address across
that block, both reads pinned by block hash, quarantining on mismatch (the recorded fee-on-transfer and
rebasing flags are what make Transfer.value the asset's own accounting, and nothing verifies the entry at
run time; reconcile.ts detects the consequence an hour later rather than preventing the credit)

VERDICT   NO-SHIP: planted-break test proving reconcile.ts detects a discrepancy
```

`NO-SHIP` on a page whose six findings are all closed is the point. The reconciliation is written,
scheduled and reads through an independent path, and none of that is evidence that it fires. Feeding it a
known discrepancy on a known address, against a freshly migrated store, is the cheapest test here and the
one that protects every control above it.

Four things the retired block listed as `n/a` are simply absent now: intent identity, the broadcast hash
set, the allocator lock and the queue preconditions. This service credits and never broadcasts, so a
concept it does not touch gets no slot. Adding a send path to this codebase brings all four back, and moves
exposure with them.
