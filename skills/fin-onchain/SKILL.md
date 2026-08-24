---
name: fin-onchain
description: Use when code crosses the chain boundary: ethers, viem, web3.py, @solana/web3.js, bitcoinjs-lib, xrpl, stellar-sdk, BitGo, Fireblocks; eth_getLogs, nonce, blockHash, logIndex, decimals(), destination_tag, memo, PSBT, changeAddress, gap limit, sweeps, deposit crediting, reorgs, withdrawal queues, bridges, oracles. Load it before writing the indexer, not after. Skip centralized venue APIs: fin-exchange-integration.
license: MIT
---

# On-chain integration and custody correctness

This skill is for the engineer whose correctness crosses the chain boundary: a deposit detector, a withdrawal
pipeline, a custody backend, an indexer that credits balances, a DeFi integration, a bridge relayer. It exists
because chain code fails while every component behaves exactly as specified: the node answered, the provider
returned 200, the event was real, the receipt said success, and the customer is credited the wrong number or
not at all. Ask on every line: **which of the three states am I reading, and which
am I writing?**

Boundary with siblings. The balance that backs a credit, the solvency invariant, and the double-entry shape
belong to `fin-ledger`. This skill hands it a crossing and stops. Order placement, filters, fills and books
on a venue you do not operate belong to `fin-exchange-integration`. **Contract-internal vulnerability classes
(reentrancy, access control, key management, upgrade authority) are out of scope for this entire suite**;
the right neighbour is Trail of Bits' `building-secure-contracts`. This skill owns integration and economic
correctness: identity, completeness, finality, amount provenance, and the wallet's own state.

> **`G1`–`G7`** are the always-on financial guardrails: **G1** economic-diff gate · **G2** a named risk is implemented or the process refuses to start · **G3** every comment claim checked against the code · **G4** an ambiguous external call has three phases and the first one COMMITs · **G5** enumerate legal `(state, event)` pairs, guard the version on the entity id, re-read from the authority · **G6** a watermark advances only past a verifiably covered range · **G7** the reconciliation runs in production or it does not exist. Install them with `scripts/install-guardrails.sh`; every rule below stands on its own without them.

## When this applies

Any of these in the diff or the repo: an import of `ethers`, `viem`, `wagmi`, `web3.py`, `@solana/web3.js`,
`solders`, `bitcoinjs-lib`, `xrpl`, `stellar-sdk`, or a BitGo / Fireblocks / Alchemy / Infura client; a call to
`eth_getLogs`, `eth_sendRawTransaction`, `eth_getTransactionReceipt`, `getSignatureStatuses`, `listsinceblock`;
a column or field named `tx_hash`, `block_hash`, `log_index`, `nonce`, `confirmations`, `from_address`,
`destination_tag`, `memo`, `derivation_path`, `outpoint`, `vout`; a cursor or watermark over block heights; a
sweep, gas-tank, coin-selection, address-derivation or withdrawal-batching path.

Not this skill: a REST or WebSocket client of a centralized venue → `fin-exchange-integration`. A fiat rail →
`fin-payments`. The posting, the balance and the solvency assertion → `fin-ledger`, via the seam below.

## Three states, not two

Chain state, ledger state, and **the wallet's own state**: which outputs are mine, which are spendable, which
nonce is next, which address index is next, which signing sessions are live. Almost every custody bug is a
divergence between the wallet's state and one of the other two, and the divergence raises nothing: no
exception, no non-200, no log line. Name which state each read and each write touches. Reading the chain and
writing the ledger without advancing the wallet is how a nonce is reused. Trusting the builder's `isChange`
flag instead of re-deriving the output from the chain is how a change output becomes a fee.

## The chain model decides the rule. Do not flatten it

Key on what is observable in the repo, never on a general prior.

| Observable | Model | What changes |
|---|---|---|
| per-customer address is a forwarder / `CREATE2` proxy contract, or a sweep job exists | account + forwarder | **spendable = base address only; confirmed = base + every receive address.** A withdrawal authorised on `confirmed` cannot be funded. The sweep is paid in native gas the deposit address does not hold. |
| one shared address per asset plus a `memo` / `destination_tag` / `tag` column | memo-ID | BitGo documents deposits on these chains as **immediately available in the spendable balance**, with no sweep, no gas tank, no forwarder. `spendable == confirmed`. The failure moves entirely to untagged and mis-tagged deposits. |
| `UTXO`, `outpoint`, `vout`, `PSBT`, coin selection, `changeAddress` | UTXO | Identity is the **input set**, not the txid. Change below `min_viable_change` is **paid to the miner** (a total, silent loss with no error). |

"Spendable ≠ confirmed" is true on forwarders and **false** on memo-ID chains. Shipping it unconditionally is
wrong half the time.

## Non-negotiables

**Transaction identity is `(chainId, from, nonce)` (never the tx hash), and the confirmer reads every hash
you ever broadcast for that nonce.** *Specialises G4 with the chain's own identity.* Store broadcast hashes
per nonce as a **set** and make the confirmer scan the whole set. A fee bump (≥10% on **both**
`maxFeePerGas` and `maxPriorityFeePerGas` at the same nonce: geth `PriceBump: 10`, Fireblocks demands 15%;
bumping one field returns `replacement transaction underpriced` and leaves the original stuck) mines under a
**new hash**. A confirmer that writes `replaces_tx_hash` and never reads it sees the original never confirm,
marks the withdrawal failed, and **re-credits a user whose funds already left**. `already known` from the node
means success. Off EVM the identity changes shape and the discipline does not: UTXO identity is the input set
(same inputs are mutually exclusive on-chain; different inputs both confirm), XRPL identity is
`(account, Sequence)`. Retry by varying only `Fee` and `LastLedgerSequence`, never the `Sequence`.

**An `eth_getLogs` range is covered only when you can prove it, and the cursor advances inside that proof.**
*Specialises G6 with the provider's cap.* Compare the returned count against your provider's documented cap
**for your tier and your chain, read from configuration**. Alchemy publishes a per-chain, per-tier table (free
tier 10 blocks on every chain; PAYG unlimited on major chains, 1,000 on some, 10,000 on others; 150 MB
response cap), so never hardcode one number. Any error, range rejection, or count at the cap is a **hole, not
an empty result**: halve the range and retry, and configure a multi-provider transport
(`fallback([http(a), http(b)])` in viem, an ordered provider list in ethers) so one provider's outage is not a
silent gap. **Guard the cursor write with the same predicate as the query.** An address-set guard that wraps
the query while `saveCursor` runs unconditionally sprints a fresh deploy's cursor to the safe head, and every
address registered afterwards can never see a deposit.

**`eth_getLogs` never sets `removed: true`. Detect reorgs by chaining parent hashes.** In go-ethereum
`Removed` is set only inside the reorg path feeding `core.RemovedLogsEvent`, which serves subscriptions and
`eth_getFilterChanges`; `FilterAPI.GetLogs` never sets it. A polling indexer therefore receives **no reorg
signal at all**. Store `block_hash` and `parent_hash` for every processed block and assert
`block.parent_hash == stored_hash[n-1]` before processing block n. An indexer with no `block_hash` column
cannot detect a reorg deeper than its confirmation lag, ever. Subscriptions are not a substitute: removals are
emitted only by the node that itself processed the reorg while you were connected, so a reconnect, a provider
failover, a node restart or a load-balanced pool loses them, and web3.js #1766 documents `removed=true`
delivered **twice** for the same log. Pin reads with EIP-1898 `{"blockHash": …, "requireCanonical": true}`, or
an `eth_call` at `latest` reads state from a different fork than the log came from.

**The log dedupe identity is `(chainId, blockHash, txHash, logIndex)`, all four under one unique constraint,
and the credit path checks for an unreversed twin.** `(txHash, logIndex)` carries a real unique constraint
and is still wrong twice over: it makes re-crediting after a reorg impossible, because the re-included log
collides with the orphaned row that `ON CONFLICT DO NOTHING` refuses to replace; and a transaction re-included
in a different block at a different `logIndex` passes the constraint and double-credits. The four-part key
avoids the double credit **only if the unwind already landed**, so before crediting a re-included log, assert
that **no unreversed credit exists for `(chainId, txHash, logIndex)`**.

**A reorg unwind is a reversing balancing entry keyed on the orphaned log identity, never a delete, never an
in-place edit, never a bare debit of the user's balance.** Three shapes to design out. A revert path that
debits blocks of legitimate deposits which `ON CONFLICT … DO NOTHING` then makes impossible to re-credit. A
`CHECK (amount >= 0)` that aborts the revert transaction and wedges the indexer in a permanent retry loop. A
rollback deeper than the indexer's floor (graph-node's `ETHEREUM_REORG_THRESHOLD` defaults to **250** blocks,
commented *"Blocks cannot be reverted below the reorg threshold"*), which is an **unrecoverable-state halt**
requiring a rebuild from a snapshot or genesis, not a rollback. Bitcoin Core's `listsinceblock` carries the
mirror trap: *"transactions that were re-added in the active chain will appear as-is in this array, and may
thus have a positive confirmation count"*. Read each `removed` entry's **current** `confirmations` before
debiting anything.

**Credit the delta you measured, not the number in the event.** Call `balanceOf(address)` before and after
(or read the token's own accounting entrypoint) and credit `after − before`. Fee-on-transfer and rebasing
tokens make `Transfer.value` differ from what arrived, and a token address that is env-overridable
(`USDC_ADDRESS` from config) makes this reachable even when today's asset is not fee-on-transfer. Read
`decimals()` **from the contract at runtime**, cached per `(chainId, address)`, never a hardcoded 18 or 6,
never a constant table. The same shape recurs off EVM: an XRPL `Payment` carries `Amount` (`DeliverMax` in
rippled API v2) **and** metadata `delivered_amount`, and under `tfPartialPayment` it returns `tesSUCCESS`
having delivered an arbitrarily small fraction. Credit `delivered_amount`. Handle non-reverting `false`
returns, calls to codeless addresses that succeed, and the approve race by setting the allowance to 0 first or
using `permit`, never by granting `type(uint256).max`.

**The withdrawal schema carries a `memo` / `destination_tag` column, the API accepts it, and the send path
validates it per chain before broadcast.** Chains whose exchange deposits are addressed by a shared address
plus a tag silently deliver un-creditable funds when the tag is absent. **Gate the supported-chain set on the
field's existence:** a `SUPPORTED = {(USDT, ethereum), (USDT, tron)}` table that grows to include a
tag-addressed chain without the column is the failure path. Two chains, with their types:
**XRP Ledger** uses `DestinationTag`, a **32-bit unsigned integer**; the receiving account can set `RequireDest`
(`asfRequireDest`) so the ledger itself rejects an untagged payment with `tecDST_TAG_NEEDED`.
**Stellar** uses `memo`, typed: `MEMO_TEXT` (**≤28 bytes**), `MEMO_ID` (**64-bit unsigned integer**),
`MEMO_HASH` (**32-byte hash**), `MEMO_RETURN` (32-byte hash of the transaction being refunded). SEP-29
`memo_required` is enforced by the **sending** SDK only (the network accepts memo-less payments regardless),
so a Stellar integration needs an operational path for untagged deposits, and Stellar's own docs now point at
**muxed accounts**, which a Stellar integration must also handle. Publishing an X-address or an `M…` muxed
account folds the tag into the address and removes the class.

## SEAM S2: onchain ↔ ledger

*This is the on-chain half of the boundary. `fin-ledger` owns what the books record; load it too when the credit becomes a posting.*

**(i) Identity.** A deposit credit is exactly one balanced ledger transaction whose idempotency key is
`(chainId, blockHash, txHash, logIndex)`, never the tx hash and never `balance += amount`; the same log
re-observed after a reconnect, a backfill overlap, or a provider failover is a no-op.
**(ii) Staging.** The credit posts on observation to a per-user **PENDING (unavailable)** account and moves
to **AVAILABLE** only at the credit policy's finality (L1 finality for rollups, not L2 block count; below the
policy depth, credit only inside a stated exposure cap you are willing to lose). Withdrawal and onward
transfer authorise from AVAILABLE alone.
**(iii) Unwind.** A reorg detected by parent-hash mismatch produces a reversing balancing entry keyed on the
orphaned log identity, never an in-place edit or a delete; a reorg deeper than the indexer's rollback floor is
an unrecoverable-state halt.
**(iv) Assertion.** A continuous reconciliation asserts `Σ credited at-or-below finalized height ==
Σ observed on-chain value deltas to deposit addresses`.

## Receipts prove less than they look like they prove

**`receipt.status == 0x1` is required before any effect is treated as having occurred, and a missing log is
not proof of failure.** A mined-but-reverted transaction **consumes the nonce, burns the gas and emits no
logs**. The absence of a `Transfer` log is not proof the send failed, and the presence of a receipt is not
proof it succeeded. Contract-initiated native-currency transfers emit no logs at all and are invisible to a
log-only indexer; detect them with `debug_traceBlock` / `trace_block` or by balance delta. Etherscan exposes
them through a **separate** endpoint (`action=txlistinternal`, not `action=txlist`) keyed on
`(hash, traceId)` (`hash` alone is not unique), and a successful parent transaction can contain a failed
internal call. An exchange that indexes only ERC-20 `Transfer` events plus top-level native transactions
systematically misses contract-originated deposits.

## A deposit from your own address is not a deposit

**Before crediting, assert `from_address NOT IN (our deposit addresses ∪ our hot / warm / cold addresses)`.**
Moving tokens between two addresses you own emits a **real** `Transfer` to a **real** deposit address and,
unchecked, mints a customer credit with no matching debit. The `from_address` column usually already exists
and is never read; make the credit path read it. Sweeps out of your own forwarders are the highest-volume
instance of this, and they look exactly like customer deposits.

## Confirmation depth is a loss budget, not a constant

**Derive the depth from a stated reorg-loss budget, per chain and per amount, and record the depth and the
budget alongside the credit.** "12 confirmations" is folklore: Polygon has produced a 157-block reorg, and
after the August 2020 attacks the ETC Cooperative asked integrators to raise confirmations above 12,000
because one attack reorganised over 7,000 blocks. **Never credit an L2 deposit on L2 block count**; wait for
the L1 batch to finalize. Circle CCTP standard transfers on Ethereum, Arbitrum, Base, OP, Unichain, World
Chain and X Layer wait **~65 Ethereum blocks, 15–19 minutes**, and its fast transfers are *"subject to a
global allowance to mitigate reorganization risks"*. If low latency is required, credit immediately **up to a
bounded global exposure you are willing to lose**, rather than lowering the depth globally. Alarm and degrade
explicitly if the `finalized` head stops advancing. Ethereum went non-finalizing for over an hour on
2023-05-12. On Bitcoin Core, `confirmations` goes **negative**: `-6` means the transaction was conflicted six
blocks ago. `abs(confirmations) >= N`, or storing `confirmations` in an unsigned column, converts a conflicted
transaction into a fully confirmed one.

## The nonce allocator is a single writer, and the lock proves it

**One writer per `(chainId, from)`, holding a lock that spans allocate → sign → broadcast → record in one
transaction, under a key byte-identical in every replica.** `pg_advisory_xact_lock(hash(chain) & 0x7FFFFFFF)`
fails **both** halves: Python's `hash()` is salted per interpreter for `str` (`hash('ethereum')` differs per
process while `hash(1) == 1` everywhere), so each replica takes a different lock; and
`with engine.begin(): SELECT … FOR UPDATE` releases at the dedent, before the sign and broadcast it exists to
protect. Use a stable digest of the UTF-8 key bytes and hold the transaction across the broadcast. Fireblocks solves
this by serialising: it *"can only process a single transaction per blockchain standard per vault account"*.
The throughput ceiling is one in-flight transaction per account, and sharding across accounts must happen
**before** nonces are assigned, never after.

## The withdrawal queue asserts its own health before broadcasting

**Before broadcast the queue checks its own preconditions and stops and pages, rather than broadcasting into a
wall.** **(1) Nonce continuity.** If `pendingNonce − latestNonce > 0` for longer than a configured interval,
**replace the lowest unmined nonce rather than submitting more transactions**: queued transactions behind a
gap are capped at geth's `AccountQueue = 64` and evicted after `Lifetime = 3h`, so one stuck low-fee
transaction at nonce N blocks every withdrawal behind it and then silently drops them.
**(2) Fee-paying balance.** The broadcasting account's native-gas balance covers a configured multiple of the
current worst-case fee for the queued depth; a hot wallet out of gas stops all withdrawals with no error
anyone reads. **(3) Absolute fee ceiling.** Every constructed transaction carries a fee cap denominated in
the asset, not only a fee-rate cap, because a rate cap does not bound the loss when the input amount is wrong.
Bitcoin Core ships both (`DEFAULT_TRANSACTION_MAXFEE` = 0.1 BTC absolute, `DEFAULT_MAX_RAW_TX_FEE_RATE` per
kvB); the Paxos payout of 2023-09-10 paid **19.82 BTC of fee on a 0.0081 BTC transfer**, 198× the absolute
cap, and either check would have refused to broadcast it. The interval and the gas multiple are configuration
with no default; the ceiling comes from your own worst-case payout size, never from a number in this file.

## Required output: the crossing contract

Any response that touches a deposit-crediting path or a broadcast path ends with the matching block, in
addition to the NAMED RISKS table. Fill every slot with a real `file:line`. A slot you cannot fill is the
finding. Write it into NAMED RISKS and implement it.

```
CHAIN CROSSING: <chain> · <account+forwarder | memo-id | utxo>

DEPOSIT
  dedupe key           (chainId, blockHash, txHash, logIndex), one unique constraint  file:line
  range completeness   how a short, capped or failed range is proven covered          file:line
  cursor guard         same predicate as the query, advanced only over a proven range file:line
  reorg detector       parent_hash chained against the stored block_hash              file:line
  unwind               reversing entry keyed on the orphaned log identity             file:line
  amount source        balanceOf delta / delivered_amount — not the event field       file:line
  self-transfer guard  from_address ∉ our addresses                                   file:line
  finality gate        depth, the loss budget it buys, L1-vs-L2 unit                  file:line
  memo / tag           column + API field + per-chain validation, or "model: n/a"     file:line

WITHDRAWAL
  intent identity      (chainId, from, nonce) | input set | (account, Sequence)       file:line
  broadcast hash set   every hash for the nonce, and the confirmer that reads all     file:line
  allocator lock       key derivation + the span it holds                             file:line
  queue preconditions  nonce continuity, gas balance, absolute fee cap                file:line
```

## References

Each row is an instruction, not a suggestion. When the literal appears, read the file **immediately** and
apply it in order. **Do not summarise it.**

| file | read it immediately when the code contains |
|---|---|
| [transaction-identity.md](references/transaction-identity.md) | `@solana/web3.js`, `solders`, `getSignatureStatuses`, `lastValidBlockHeight`, `AdvanceNonceAccount`, durable nonce; or `replaces_tx_hash`, RBF, CPFP, `maxPriorityFeePerGas`, `already known`; or EIP-712 `domain`, `verifyingContract`, a bridge `(sourceChain, destChain, nonce)` replay key |
| [finality-and-reorgs.md](references/finality-and-reorgs.md) | `confirmations`, `finalized`, `safe`, `reorg`, `parent_hash`, `ETHEREUM_REORG_THRESHOLD`, `listsinceblock`, `include_removed`, a per-chain `MIN_CONFIRMATIONS` table, an L2 `sequencer` or batch-poster reference |
| [indexing.md](references/indexing.md) | `eth_getLogs`, `fromBlock`, `toBlock`, `getPastLogs`, `createEventFilter`, `maxBlockRange`, a `cursor` / `watermark` / `last_processed_block` table, `txlistinternal`, `debug_traceBlock` |
| [token-semantics.md](references/token-semantics.md) | `decimals()`, `balanceOf`, `Transfer(`, `approve`, `permit`, `safeTransferFrom`, `SafeERC20`, rebasing, fee-on-transfer; or `latestRoundData`, `AggregatorV3Interface`, `updatedAt`, `answeredInRound`, `slot0`, `getReserves`, `priceFeed`, `oracle` |
| [custody-and-wallets.md](references/custody-and-wallets.md) | `UTXO`, `outpoint`, `vout`, `PSBT`, `changeAddress`, coin selection, dust; `derivationPath`, `xpub`, `gapLimit`, `importdescriptors`; `sweep`, `gasTank`, `forwarder`; `sequenceId`, `externalTxId`, `treatAsGrossAmount`, batched or aggregated withdrawals; an import of `bitcoinjs-lib`, `xrpl`, `stellar-sdk`, `bitgo`, or a Fireblocks SDK |

**COMPANION SKILL:** `fin-ledger` for the balance a crossing lands in and the solvency invariant over it.
