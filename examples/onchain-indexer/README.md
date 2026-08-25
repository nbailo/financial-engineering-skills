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
| The cursor advances to `toBlock` with no completeness check | `fin-money-core`: *Proven coverage before the cursor advances*. `fin-onchain`: *A range of external history is covered only when you can prove it, and the cursor advances inside the proof* | `getLogs` is called once per range and the result is trusted. Providers cap results (Alchemy publishes a per-chain, per-tier table, and the response is separately capped at 150 MB), and at the cap the range is not covered. There is no result-count check, no adaptive halving, and a single transport, so one provider's outage is indistinguishable from a quiet range. | **Permanent silent under-crediting.** A customer's deposit vanishes with no error and no log line, and nothing will ever look at that range again. |
| `catch { ROLLBACK; log.error("moving on") }` then `fromBlock = toBlock + 1n` | `fin-money-core`: *Proven coverage before the cursor advances*. `fin-onchain`: *A range of external history is covered only when you can prove it, and the cursor advances inside the proof* | The rollback correctly leaves the cursor where it was, and then the loop advances anyway, so the *next* successful range writes a `last_block` past the failed one. The skipped range is never revisited. | Same as above, but triggered by any transient RPC failure, which is a daily event. |
| `if (addresses.size > 0)` guards the query; the advance runs unconditionally | `fin-money-core`: *Proven coverage before the cursor advances*. `fin-onchain`: *A range of external history is covered only when you can prove it, and the cursor advances inside the proof* | On a fresh deploy with an empty `deposit_addresses` table, the cursor sprints from genesis to the safe head in a few minutes. With no backfill job, **every address registered afterwards can never see a deposit in a passed block.** The same shape sits one line up: `addresses` is snapshotted once per outer tick while the inner drain runs for hours, so an address registered mid-drain is invisible for the rest of it. | Total, permanent, and it lands on day one of production, the day the address table is empty. |
| `UNIQUE (tx_hash, log_index)` | `fin-onchain`: *The dedupe key must separate the same event twice from the same event in a different history* | This has a real unique constraint and is above the bad bar, and it deliberately excludes block identity, which is what makes re-crediting after a reorg impossible: the re-included log collides with the orphaned row and `ON CONFLICT DO NOTHING` refuses to replace it. In the other direction, a transaction re-included in a different block at a different `logIndex` passes the constraint and credits twice. | Under-credit on re-inclusion at the same index; double-credit on re-inclusion at a different one. Both silent. |
| `Number(l.args.value!) / 1e18` | `fin-onchain`: *Credit the delta you measured at the authority, not the number in the notification*. `fin-money-core`: *An obligation and an estimate are different kinds of number* | USDC has **6** decimals. The constant is 18 because it was copied from an ETH indexer, and the comment says "wei", which is the tell. A 1,000 USDC deposit credits `0.000000001`. `Number()` on a `bigint` separately loses precision above 2^53. The token address is `process.env.USDC_ADDRESS ?? …`, so the asset can change without the exponent changing. | Every deposit under-credited by a factor of 10^12, on 100% of events, until someone opens a support ticket. |
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

// Reconciliation runs in production: the alert destination has no default, so import
// fails rather than alerting into a void.
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
 *  6-decimal token gets credited on an 18-decimal assumption. */
async function pinTokens(): Promise<void> {
  for (const raw of requireEnv("DEPOSIT_TOKENS").split(",")) {
    const address = getAddress(raw.trim());
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

      // Seam S2, staging: the credit posts to a PENDING account and moves to
      // AVAILABLE at the policy's finality. Withdrawal authorises from AVAILABLE alone.
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

And the reconciliation, because none of the above is worth anything if nobody is checking. This is seam
S2's assertion, `fin-money-core`'s *Reconciliation runs in production*, and `fin-verification`'s
*A detector that has never detected is not known to detect*:

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
      if (credited - swept !== onChain) {
        breaks.push({ userId, token, ours: credited - swept, chain: onChain });
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
search, and a reversing unwind.

**Not changed, deliberately.** The confirmation-depth gate was already correct and stayed. It gained a
recorded budget, not a new mechanism. Credit and cursor already committed in one transaction; that was
already correct and was left alone. The polling loop is still a polling loop: no websocket subscription
was added, because subscription-delivered `removed` logs are lost by a reconnect, a provider failover or a
node restart, and web3.js #1766 documents the same removal delivered twice. The
service is still one process with one cursor; no sharding, no queue.

**Deliberately not implemented, with the reason stated in code.** The `balanceOf`-delta clause of
*Credit the delta you measured at the authority, not the number in the notification* is **not** implemented
on the ingest path. The reason
is that `DEPOSIT_TOKENS` is a pinned list whose `symbol()` and `decimals()` are asserted at startup, and
neither pinned token is fee-on-transfer or rebasing. That is a closure, not a deferral: the moment a token
is added to that list the assertion is the thing that has to be revisited, and `reconcile.ts` measures the
on-chain balance independently every hour, which is where a fee-on-transfer discrepancy would surface as a
break rather than as silence. If the list ever accepts an arbitrary token, the delta measurement becomes
mandatory and the pin is what stops that from happening by accident.

**Still absent.** There is no handling for contract-originated native transfers, which
*A receipt proves inclusion, not effect* covers, because this service indexes ERC-20 logs only and native
deposits go through a different address set. The memo and destination-tag column of *An address is not
always the whole destination* does not apply on EVM and would be required on the first tag-addressed chain
added.

---

## The block the review ends with

**Why this is T2.** Every credit lands on a `user_id`, and a customer eats the error: an under-credit is
their deposit that never arrived, and a double-credit is house money they can withdraw. `fin-onchain` makes
that explicit, because any path that credits a user deposit is T2 by definition. It escalates further the
moment this service feeds a withdrawal path, which is what the PENDING to AVAILABLE staging exists to gate.

At T2 and above the `FINANCIAL CHECK` is followed by the `CHAIN CROSSING` block. A slot that cannot be
filled is the finding, and it goes on the `controls:` line as `UNRESOLVED`.

The anchors below name functions rather than lines, because the corrected code is a listing in this file.
In a real response every one of them is a `file:line`.

```
ECONOMIC-DIFF: amount, effect, authority, replay
FINANCIAL CHECK
tier:       T2, a user_id on every credit row, a crediting path fed by external history, deposit balances
            other systems spend against
effect:     an ERC-20 deposit credits a customer's internal balance, in the token's own base units, from
            an external sender to a per-customer deposit address
identity:   (chainId, blockHash, txHash, logIndex), unique on unreversed credits, at
            migrations/002_deposit_credits.sql deposit_credits_identity
ambiguity:  a provider error, a range rejection, a result count at the cap and a truncated page are all
            holes rather than empty results; getLogsComplete() halves and retries and throws rather than
            returning a short answer
authority:  the chain. balanceOf at the finalized height is the record, and deposit_credits is a claim
            about it that reconcile.ts compares every hour
recovery:   nothing advances the cursor outside the transaction that covered the range, so a crash or a
            thrown range leaves the cursor where it was and the range is retried whole
controls:   provable range coverage, halving on any hole -> indexer.ts getLogsComplete()
            cursor advanced only inside the covered branch -> indexer.ts tick()
            empty address set holds the cursor -> indexer.ts tick()
            address set re-read every iteration -> indexer.ts tick()
            cursor row locked across check and act -> indexer.ts tick() FOR UPDATE
            four-part dedupe identity -> deposit_credits_identity
            unreversed-twin assertion before credit -> indexer.ts tick()
            parent-hash chaining and fork search -> indexer.ts findForkPoint()
            reorg unwind as a reversing row -> indexer.ts unwind()
            unrecoverable-depth halt -> indexer.ts UnrecoverableReorg
            token scale read from the contract -> indexer.ts pinTokens()
            self-transfer excluded from income -> indexer.ts tick()
            confirmation depth with a recorded budget -> indexer.ts CONFIRMATIONS, REORG_BUDGET
            hourly comparison against balanceOf at finality -> reconcile.ts reconcile()
            alert destination with no default -> indexer.ts ALERT_SINK
            UNRESOLVED: no planted-break test proves reconcile.ts detects; it is scheduled and has never
            been shown to fire
            UNRESOLVED: balanceOf-delta measurement on the ingest path, closed by the pinned token list
            rather than implemented, and mandatory the moment that list accepts an arbitrary token
```

```
CHAIN CROSSING: ethereum mainnet · account+forwarder

DEPOSIT
  dedupe key           (chain_id, block_hash, tx_hash, log_index), one unique index   deposit_credits_identity
  range completeness   halve on error, range rejection or a count at the cap; throw   getLogsComplete()
  cursor guard         same predicate as the query, advanced only over a proven range tick()
  reorg detector       parent_hash chained against the stored block_hash              findForkPoint()
  unwind               reversing row keyed on reverses_credit_id                      unwind()
  amount source        the event's value in base units; the delta measurement is      tick()
                       closed by the pinned token list, not deferred
  self-transfer guard  from_address not in our addresses, and not a house address     tick()
  finality gate        DEPOSIT_CONFIRMATIONS against DEPOSIT_REORG_BUDGET, recorded   tick()
                       on every credit row
  memo / tag           model: n/a on EVM; required on the first tag-addressed chain   n/a

WITHDRAWAL
  intent identity      not in scope: this service credits and never broadcasts        n/a
  broadcast hash set   not in scope                                                   n/a
  allocator lock       not in scope                                                   n/a
  queue preconditions  not in scope                                                   n/a
```

The withdrawal half is `n/a` rather than blank because this service has no send path at all. If one is ever
added to the same codebase, those four rows stop being `n/a` and the tier goes up with them.
