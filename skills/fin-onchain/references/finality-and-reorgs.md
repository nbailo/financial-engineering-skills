# Finality, confirmation policy, and reorg handling

This reference covers the four distinct states a chain observation can be in (seen, included, confirmed,
final), how each chain tells you which one you are in, and how to price the depth at which you credit. It
carries the per-chain observed reorg depths, the L1-versus-L2 unit distinction that makes a rollup credit
policy correct or wrong, the exposure-capped fast-credit pattern, the indexer rollback floors below which a
reorg is an unrecoverable-state halt rather than a rollback, and the mechanics of unwinding a credit that has
already been posted.

## Contents

- **Four states, and the API that distinguishes them**: seen (mempool, RBF-replaceable, evictable);
  included (in a block; on EVM this includes reverted transactions); confirmed (N deep, an economic
  parameter); final (Ethereum `finalized`, XRPL validated ledger, Bitcoin probabilistic depth).
- **Depth as a loss budget**: deriving N per chain and per amount from a stated reorg-loss budget; recording
  the depth and the budget with the credit; per-chain observed depths (Polygon 157 blocks; ETC >7,000 blocks
  in one 2020 attack and the >12,000-confirmation guidance that followed; Kraken's 4 for BTC).
- **L1 versus L2 units**: why an L2 deposit is never credited on L2 block count; Circle CCTP's published
  policy (~65 Ethereum blocks, 15–19 minutes, for standard transfers on Ethereum/Arbitrum/Base/OP/Unichain/
  World Chain/X Layer) as the worked example; the L2 sequencer-uptime dependency.
- **Fast credit inside a bounded exposure**: crediting below the policy depth against a stated global
  allowance you are willing to lose, rather than lowering the depth globally; where the cap is enforced and
  what happens when it is exhausted.
- **Non-finalizing chains**: alarming and degrading when the `finalized` head stops advancing (Ethereum,
  over an hour, 2023-05-12); what a crediting pipeline should do while it waits.
- **XRPL's per-result-code finality table**: `tesSUCCESS` and any `tec` final on inclusion in a validated
  ledger; `tem` final unless the protocol changes; `tefPAST_SEQ`; `tefMAX_LEDGER`; "any other transaction
  result is potentially not final"; provisional results that flip in both directions.
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
- **51% attacks as an operational input** (Bitcoin Gold 2018/2020, Ethereum Classic 2019/2020): what changes
  in a confirmation policy when the chain's hashrate is rentable.

## Four states, and the API that distinguishes them

Most crediting bugs are a two-state model (`pending` / `confirmed`) applied to a four-state world. Name the
state in the column, not a comment.

| State | Meaning | What can still undo it |
|---|---|---|
| **seen** | in a mempool, or accepted by a sequencer | RBF replacement, eviction under queue pressure, `Lifetime` expiry, node restart, provider rotation. No event is emitted when it is dropped. |
| **included** | in a block | the block is orphaned. On EVM this state says nothing about success: a reverted transaction is *included*, consumes the nonce, burns the gas, and emits no logs. |
| **confirmed** | N blocks deep | a reorg deeper than N. N is an economic parameter, not a constant. |
| **final** | irreversible under the chain's own finality rule | on Ethereum, an attacker burning ≥1/3 of staked ETH; on Bitcoin, nothing: the state is probabilistic, never categorical. |

Per chain, the API that answers "which state am I in":

| Chain | Included | Confirmed | Final |
|---|---|---|---|
| Ethereum L1 | `eth_getTransactionReceipt` non-null **and** `receipt.status == 0x1` | `head − receipt.blockNumber` | block tag `finalized`; justified→finalized spans two epochs ≈ **12.8 min** (ethereum.org, PoS finality). `safe` is the intermediate tag. |
| OP Stack (OP Mainnet, Base) | sequencer receipt = *unsafe* | *safe* = batch posted to L1 | *finalized* = the L1 batch block is finalized. OP docs publish unsafe 2–4 s, safe 5–10 min (up to 12 h), finalized 15–30 min (up to 12 h). |
| Arbitrum | sequencer receipt (soft) | batch posted | L1 finality of the batch. Arbitrum docs: *"soft finality depends on this assumption [the Sequencer behaves correctly]"*. |
| Solana | `getSignatureStatuses` returns a slot | commitment `confirmed` | commitment `finalized`; `getSignatureStatuses` returns `confirmations: null`, which *"means the transaction is rooted and finalized by a supermajority of stake"*. |
| XRPL | `submit` returned a provisional code | n/a (no depth concept) | inclusion in a **validated ledger**, per the result-code table below. |
| Bitcoin | `confirmations == 0` | `confirmations >= N` | never categorical; N is priced against hashrate. |

Two states that read as success and are not. On Solana, `err != null` means the transaction landed and
failed: fees paid, blockhash consumed, no state change from the failing instruction onward; and at
commitment `processed`, *"roughly 5% of blocks don't finalize"* (solana.com), so `processed` is never an
economic decision point. On EVM, a receipt exists for reverted transactions, `receipt.status == 0x1` is the
only success signal, and the **absence of a `Transfer` log is not proof of failure**; contract-initiated
native transfers emit no logs at all.

## Depth as a loss budget

`MIN_CONFIRMATIONS = 6` across a chain list is not a policy; it is an unpriced bet on the cheapest chain in
the list. Write the depth as a derived quantity with the budget beside it, and persist both on the credit row
so a later reconciliation can answer "what did we think this was worth".

```sql
-- confirmation policy is data, not a constant in the crediting service
CREATE TABLE credit_policy (
  chain_id            BIGINT      NOT NULL,
  asset               TEXT        NOT NULL,
  amount_band_upper   NUMERIC(38,0) NOT NULL,   -- base units; band is (prev_upper, this]
  depth_unit          TEXT        NOT NULL      -- 'l1_blocks' | 'l2_blocks' | 'finalized_tag' | 'validated_ledger'
                      CHECK (depth_unit IN ('l1_blocks','l2_blocks','finalized_tag','validated_ledger')),
  depth               INTEGER     NOT NULL,
  reorg_loss_budget   NUMERIC(38,0) NOT NULL,   -- what a reorg at this depth costs us, stated
  rationale_ref       TEXT        NOT NULL,     -- doc/ADR that argues the number
  PRIMARY KEY (chain_id, asset, amount_band_upper)
);

-- and the credit records which row it was written under
ALTER TABLE deposit_credit
  ADD COLUMN policy_depth       INTEGER       NOT NULL,
  ADD COLUMN policy_depth_unit  TEXT          NOT NULL,
  ADD COLUMN policy_budget      NUMERIC(38,0) NOT NULL,
  ADD COLUMN observed_at_height BIGINT        NOT NULL;
```

Observed depths that falsify "12 confirmations":

| Chain | Observed / published | Source |
|---|---|---|
| Ethereum L1 | ~1% of blocks reorg; deepest public event **7 blocks**, 2022-05-25 08:55 UTC, epoch 121471, slots 74→82 (late proposal + uneven proposer-boost rollout) | Envio (vendor, secondary); beacon-chain post-mortem analysis |
| Polygon PoS | one-block reorgs routine; **157-block** reorg 2023-02-23 at height **39,599,624**: a bad-merkle-root bug partitioned the P2P layer so producers could not communicate | Polygon forum thread 11388 (operator, primary). Polygon's own sensor-network data: 0.23% of blocks double-signed at the same height, 19% sealed out of turn |
| Base | *"only one Base block has reorged, affecting 0.0000003% of transactions"*; *"There has never been a reorg of L2 blocks that were batched to Ethereum L1"* | docs.base.org transaction-finality (primary) |
| OP Stack | L2 reorg depth *"bounded by the L1 finality delay (2 L1 beacon epochs, or approximately 13 minutes)"* | specs.optimism.io derivation (primary) |
| Ethereum Classic | one August-2020 attack reorganised *"over 7000 blocks which corresponds to approximately 2 days of mining"*; the ETC Cooperative asked integrators to *"raise confirmations to >12K"* | secondary reporting; treat the 12K figure as guidance, not a spec |
| Bitcoin | Kraken credits BTC at **4** confirmations (~40 min) | support.kraken.com deposit-processing-times (primary). Their per-asset table for ETH/USDT/SOL did not render on fetch; **unverified**, do not copy a number for those |

Two consequences: the correct ETC number was only knowable *after* an attack, so a chain with rentable
hashrate has no static safe depth; and the per-amount band matters more than the per-chain number: the same
chain at $50 and at $5M is two different bets.

## L1 versus L2 units

The chain that can reorg an L2 deposit away is **L1**, so a policy denominated in L2 blocks measures the
wrong thing, and an OP Stack interop invalidation replaces an invalid block with a **deposit-only
replacement block**, in which every sequencer-ordered transaction ceases to exist. Circle publishes the
clearest credit policy in the industry, and it counts in Ethereum blocks:

| CCTP path | Ethereum | Arbitrum / Base / OP / Unichain / World Chain / X Layer | Solana | Starknet |
|---|---|---|---|---|
| Standard (hard finality) | ~65 Ethereum blocks, **15–19 min** | ~65 **Ethereum** blocks: L2 confirmations are not counted at all | 32 | n/a |
| Fast | 2 confs | 1 conf | 2–3 | 4 |

Source: developers.circle.com/cctp/required-block-confirmations. The fast row carries its own qualifier:
*"Fast Transfers are subject to a global allowance to mitigate reorganization risks."*

The check in code is a unit assertion, not a number:

```python
def is_creditable(dep: Deposit, pol: CreditPolicy, heads: Heads) -> bool:
    if pol.depth_unit == "l2_blocks":
        raise PolicyError(f"l2_blocks is not a finality unit for chain {dep.chain_id}")
    if pol.depth_unit == "finalized_tag":
        # the L2 block's own L1 origin must be finalized, not the L2 head
        return dep.l1_origin_block <= heads.l1_finalized
    if pol.depth_unit == "l1_blocks":
        return (heads.l1_latest - dep.l1_origin_block) >= pol.depth
    ...
```

`dep.l1_origin_block` is the load-bearing column: without it an L2 deposit row physically cannot be gated on
L1 finality and the pipeline counts L2 blocks whatever the policy says. Populate it at observation from the
L2 block's L1 origin (OP Stack: the epoch's L1 block; Arbitrum: the batch's L1 inclusion block).

A rollup soft confirmation is a **trust assumption stated by the operator**, not a chain guarantee: Arbitrum
documents that a malicious sequencer *"could reorder or censor certain transactions before they achieve hard
finality"*, and publishes the escape hatch with its deadline: `forceInclude` on `SequencerInbox` after 24
hours. A withdrawal path whose only route is "the sequencer accepts my transaction" couples availability to
solvency.

## Fast credit inside a bounded exposure

If the product needs sub-minute credit, the correct lever is a **cap on cumulative unfinalized exposure**,
not a smaller global depth: lowering the depth changes the probability of a loss, while the cap changes its
maximum size, the quantity you can actually underwrite. Enforce it in the same transaction that writes the
credit, or it does not bind under concurrency:

```sql
-- one row per (chain_id, asset); the lock serialises fast credits per chain
BEGIN;
SELECT allowance_base_units, outstanding_base_units
  FROM fast_credit_allowance
 WHERE chain_id = $1 AND asset = $2
   FOR UPDATE;                                    -- not FOR SHARE, not a read outside the txn

-- application asserts outstanding + amount <= allowance, else fall through to full-depth wait
UPDATE fast_credit_allowance
   SET outstanding_base_units = outstanding_base_units + $3
 WHERE chain_id = $1 AND asset = $2
   AND outstanding_base_units + $3 <= allowance_base_units;
-- 0 rows updated => cap exhausted => do NOT credit fast; leave the deposit PENDING
INSERT INTO deposit_credit (...) VALUES (...);
COMMIT;
```

The release is skipped more often than the acquire: at full policy depth, decrement `outstanding_base_units`
by the same amount keyed on the credit id, so a retry cannot double-release. A cap that is never released
stops all fast credit within hours and reads as a latency regression. Exhaustion is not an error (the
deposit waits at full depth), but give it a metric name (`fast_credit_capacity_exhausted_seconds`), or the
allowance gets raised under pressure with no record of what the new number buys.

Fireblocks supports a zero-confirmation Deposit Control & Confirmation Policy and documents the consequence:
you *"may receive a webhook notification for the Completed status when the transaction appears on the
blockchain as well as on its first or further confirmations"*: `COMPLETED` is **not** an at-most-once event.
If you credit below finality, the reversal path is not optional; it is the path you chose.

## Non-finalizing chains

On 2023-05-11 and 2023-05-12 the Ethereum beacon chain kept producing blocks while finality stalled (25
minutes, then over an hour, epochs ~200,551–200,555) from attestations to old beacon blocks overflowing
Prysm's state-regeneration cache. Code that blocks on "finalized head > X" with no timeout stalls the whole
crediting pipeline and emits nothing an on-call engineer can act on.

1. **Alarm on the head not advancing, not on its height.** Record `finalized_head_height` and the timestamp
   of its last *change*; alarm on `now − last_advance > threshold`. A stalled head returns the same height
   successfully forever, so a naive health check stays green.
2. **Keep observing and keep staging.** Deposits still post to the per-user PENDING (unavailable) account;
   only the PENDING→AVAILABLE transition blocks.
3. **Degrade explicitly.** Continuing to credit during non-finality is a fast-credit decision and belongs
   under the allowance cap above, not an ad-hoc fallback to `latest − 12` invented during the incident.
4. **Alert on staged-but-uncreditable notional, in currency.** That number is what the exposure decision
   will actually be made on.

`safe` is not a substitute: it derives from the same fork-choice machinery, so falling back to it during a
finality incident changes the guarantee without changing the code that consumes it.

## XRPL's per-result-code finality table

XRPL publishes finality as a function of the result code, and the code `submit` returns is provisional in
both directions: *"A transaction that succeeded initially could still fail, and a transaction that failed
initially could still succeed."* (xrpl.org, finality-of-results.)

| Result class | Final when |
|---|---|
| `tesSUCCESS` | included in a **validated ledger** |
| any `tec` | included in a validated ledger: a *failed* transaction is also final, and it did destroy the transaction cost |
| any `tem` | final unless the protocol itself changes |
| `tefPAST_SEQ` | another transaction with the same `Sequence` is validated |
| `tefMAX_LEDGER` | a validated ledger exceeds `LastLedgerSequence` **and** the transaction is in none of them |
| anything else | *"Any other transaction result is potentially not final."* |

Consequences for a crediting or withdrawal pipeline:

- A `tec` means the transaction is on the ledger and **cost you the fee**, not a "didn't happen", and not
  retryable as if it were. `tecDST_TAG_NEEDED` (143) is the one to expect on deposits into an account with
  `lsfRequireDestTag` set.
- `tefMAX_LEDGER` is the only clean "this attempt is dead" signal and it requires `LastLedgerSequence` on
  every submission. Without it there is no terminal negative state and the confirmer polls indefinitely.
- Documented causes of a flip: canonical-order re-execution changing which of two competing transactions
  consumes an offer, and a payment that tentatively failed for insufficient funds succeeding later because a
  funding transaction sorted ahead of it. Acting on the first result you see is wrong in both directions.

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

## 51% attacks as an operational input

A rentable-hashrate chain has no static safe depth, and the depth that was correct became wrong without any
code change on your side. The catalogue, with sourcing quality marked:

| Event | Date | Economic outcome | Confidence |
|---|---|---|---|
| Bitcoin Gold | 2018-05 | *"388,000 BTG (worth approximately US$18 million) was stolen from several cryptocurrency exchanges"*; repeated 2020-01; later delisted from Bittrex | **secondary** (Wikipedia): use the mechanism, treat the figure as reported |
| Ethereum Classic | 2019-01-05→08 | Gate.io detected 7 rollback transactions, ~40k ETC lost (~$200k); attacker moved 54,200 ETC total; $100k returned 2019-01-10 | **partially verified**: direct fetches returned 429/403 |
| Ethereum Classic | 2020-08 | OKEx absorbed a **$5.6M** double-spend loss, suspended ETC deposits and withdrawals, froze five accounts; the month's third attack reorganised >7,000 blocks | **secondary** |

The mechanism is identical in all three: the exchange credited at a depth the attacker could out-mine, the
depositor traded into a different asset and withdrew it, and the deposit block was then reorganised away. The
exchange's own code behaved exactly as specified throughout. What changes in the policy:

1. **Past a point, depth stops working.** ETC's post-attack guidance of >12,000 confirmations is roughly two
   days of block production, an unusable deposit product. The honest options there are a per-chain deposit
   cap, a withdrawal delay on the *outbound* asset, or delisting.
2. **The exit asset is the exposure.** The attacker's profit is the *other* asset withdrawn, not the reorged
   deposit. Gate the withdrawal of a different asset on the deposit that funded it reaching full depth,
   which requires the withdrawal path to be able to trace funding lots, not just read a net balance.
3. **Re-derive after every incident on that chain and record it.** The `rationale_ref` column in
   `credit_policy` exists so "why 4 and not 40" has an answer that is not a git blame.
4. **Suspension is a supported state, not an outage.** Both OKEx and Bittrex reached for it. A per-chain
   `deposits_suspended` / `withdrawals_suspended` flag read by the crediting path is cheap in advance and
   impossible to build calmly during an attack.
