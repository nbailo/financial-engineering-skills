---
name: fin-onchain
description: >-
  Financial correctness for blockchain integrations: deposits, withdrawals, custody, indexers,
  finality, reorgs, transaction identity, nonces, token amount semantics, and on-chain to
  off-chain reconciliation. Use when building or reviewing systems that read chain state or move
  value on-chain, including ethers, viem, web3.py, Solana, Bitcoin, XRPL, Fireblocks and BitGo.
  For centralized venue APIs use fin-exchange-integration.
license: MIT
---

# The chain crossing: value enters a history that is not final when you first see it

Your code holds one side of a boundary. The other side is a ledger you do not operate, whose recent history can be
rewritten, whose notifications arrive out of order and incomplete, and whose amounts are computed by code you did not
write. Chain code fails while every component behaves as specified: the node answered, the provider returned 200, the
event was real, the receipt said success, and the customer is credited the wrong number or not at all.

This skill owns identity, completeness, finality, amount provenance and the wallet's own state. The balance a crossing
lands in, the double-entry shape and the solvency invariant belong to `fin-ledger`; orders, filters, fills and books
on a venue you do not operate belong to `fin-exchange-integration`. Contract-internal vulnerability classes
(reentrancy, access control, key management, upgrade authority) are out of scope for this entire suite; the neighbour
there is Trail of Bits' `building-secure-contracts`.

## Workflow

1. Identify the economic crossing: what value enters or leaves your control, and in which direction.
2. Identify the authoritative external state and the local state that mirrors it.
3. Identify the durable identity of the transaction or event, and what makes it unique.
4. Determine ambiguity, retries and replay behaviour for anything you broadcast.
5. Determine finality and reversal behaviour, and what depth of history you are betting on.
6. Determine reconciliation between the chain and your own books.
7. Load only the references relevant to this implementation.
8. Implement the controls and their tests before declaring the path complete.

## When this applies

Your code turns observations of a ledger someone else maintains into obligations you owe, or turns an obligation you
owe into an instruction that ledger executes. The external history is provisional when you read it, the instruction is
ambiguous once you send it, and the amount that arrives is decided by the asset's own code, not by the message
announcing it. Crediting a deposit or broadcasting a holder's withdrawal is T2; holding the signing authority is T3.

Routing hints, not the definition of the domain: an import of `ethers`, `viem`, `wagmi`, `web3.py`, `@solana/web3.js`,
`solders`, `bitcoinjs-lib`, `xrpl`, `stellar-sdk`, or a BitGo / Fireblocks / Alchemy / Infura client; a call to
`eth_getLogs`, `eth_sendRawTransaction`, `eth_getTransactionReceipt`, `getSignatureStatuses`, `listsinceblock`; a
column or field named `tx_hash`, `block_hash`, `log_index`, `nonce`, `confirmations`, `from_address`,
`destination_tag`, `memo`, `derivation_path`, `outpoint`, `vout`; a cursor or watermark over block heights; a sweep,
gas-tank, coin-selection, address-derivation or withdrawal-batching path.

Not this skill: a REST or WebSocket client of a centralized venue goes to `fin-exchange-integration`, a fiat rail to
`fin-payments`, and the posting, the balance and the solvency assertion to `fin-ledger`, via the seam below.

## Core rules

### Observed value, final value and spendable value are three different quantities

Value you have seen is not value that will survive, and value that will survive is not value the holder may move.
Model the three separately and let only the third authorise an outbound effect. Ask on every line: which of the three
am I reading, and which am I writing?

**Shape**

```
observe(event)               -> record as provisional, unavailable
finality policy satisfied    -> move provisional to available, same transaction
outbound authorisation reads available only
```

Collapsing the three into one number means a rewrite of external history debits a balance the holder has already
spent. The gap between provisional and available is the only place the loss can be absorbed.

**How it appears:** Observation posts to a per-user PENDING (unavailable) account; the credit policy's finality moves
it to AVAILABLE, which alone authorises withdrawal and onward transfer (L1 finality for rollups, never L2 block
count). Below the policy depth, credit only inside a stated exposure cap you are willing to lose.

### The instrument that spends holds state that neither the chain nor your books reconcile

Beyond the external ledger and your own books there is a third state: which funds the signer believes are its own,
which are committed, which sequence number and address index are next, which signing sessions are live. Divergence
here raises nothing: no exception, no non-200, no log line.

**Shape**

```
external ledger  |  local books  |  signer's own view
every read and every write names which of the three it touches
```

Reading the chain and writing the ledger without advancing the wallet is how a nonce is reused. Trusting the builder's
own flag about which output is yours, instead of re-deriving the output from the chain, is how a change output becomes
a fee.

**How it appears:** Which outputs are mine, which are spendable, which nonce is next, which address index is next;
the builder's `isChange` flag versus re-derivation from the chain. Change below `min_viable_change` is paid to the
miner: a total, silent loss with no error.

### The identity of an outbound intent is one you chose; the handle the network returns is not stable

Mint the identity from the intent instance and commit it before broadcasting. The network may confirm that intent
under a handle you did not predict, or accept a modified resubmission of it, so the confirmer must read every handle
the intent ever produced. This specialises *durable intent before the external effect* with the chain's own identity.

**Shape**

```
mint identity from intent -> COMMIT intent row -> broadcast -> append every returned handle
confirm(intent) = scan the whole set of handles ever emitted for that identity
```

Treating the returned handle as the identity makes a repriced or rebuilt resubmission look like a different
transaction. The confirmer sees the original never confirm, marks the withdrawal failed, and re-credits a holder whose
funds already left.

**How it appears**

- EVM identity is `(chainId, from, nonce)`, never the tx hash. Store broadcast hashes per nonce as a **set** and make
  the confirmer scan the whole set.
- A fee bump needs at least 10% on **both** `maxFeePerGas` and `maxPriorityFeePerGas` at the same nonce (geth
  `PriceBump: 10`; Fireblocks demands 15%). Bumping one field returns `replacement transaction underpriced` and leaves
  the original stuck. The bump mines under a **new hash**, so a confirmer that writes `replaces_tx_hash` and never
  reads it is the defect above. `already known` from the node means success.
- UTXO identity is the input set: the same inputs are mutually exclusive on-chain, different inputs both confirm. XRPL
  identity is `(account, Sequence)`; retry by varying only `Fee` and `LastLedgerSequence`, never the `Sequence`.

### A range of external history is covered only when you can prove it, and the cursor advances inside the proof

A query against someone else's history returns fewer results than exist, for reasons that are not errors. Classify
every response as covered, truncated, rejected or failed, and advance the durable cursor only inside the conditional
and transaction that established coverage. This specialises *proven coverage before the cursor advances* with the
provider's cap.

**Shape**

```
query(range) -> classify: covered | truncated at cap | rejected | errored
covered      -> process, then advance cursor under the same predicate as the query
otherwise    -> split the range and retry; never advance
```

A result count at the documented cap is a hole, not an empty result. The failure is permanent silent under-crediting:
value vanishes with no error and no log line.

**How it appears**

- Compare the returned count against your provider's documented cap **for your tier and your chain, read from
  configuration**. Alchemy publishes a per-chain, per-tier table (free tier 10 blocks on every chain; PAYG unlimited
  on major chains, 1,000 on some, 10,000 on others; 150 MB response cap), so never hardcode one number.
- Any error or range rejection: halve the range and retry, and configure a multi-provider transport
  (`fallback([http(a), http(b)])` in viem, an ordered provider list in ethers) so one provider's outage is not a
  silent gap.
- Guard the cursor write with the same predicate as the query. An address-set guard that wraps the query while
  `saveCursor` runs unconditionally sprints a fresh deploy's cursor to the safe head, and every address registered
  afterwards can never see a deposit.

### A history that can be rewritten does not reliably tell you it was rewritten

Detect the rewrite yourself, by chaining each observed unit of history to the one you already stored. A "removed"
signal from the source is an optimisation, never the detector.

**Shape**

```
store (unit id, parent id) for every processed unit
before processing unit n: assert parent_id(n) == stored id(n-1)
mismatch -> unwind from the divergence point; do not advance
```

The removal signal is emitted only by the node that processed the rewrite while you were connected.

**How it appears**

- `eth_getLogs` never sets `removed: true`. In go-ethereum `Removed` is set only inside the reorg path feeding
  `core.RemovedLogsEvent`, which serves subscriptions and `eth_getFilterChanges`; `FilterAPI.GetLogs` never sets it. A
  polling indexer therefore receives **no reorg signal at all**.
- Store `block_hash` and `parent_hash` for every processed block and assert `block.parent_hash == stored_hash[n-1]`
  before processing block n. An indexer with no `block_hash` column cannot detect a reorg deeper than its confirmation
  lag, ever.
- Subscriptions are not a substitute: a reconnect, a provider failover, a node restart or a load-balanced pool loses
  removals, and web3.js #1766 documents `removed=true` delivered **twice** for the same log. Pin reads with EIP-1898
  `{"blockHash": …, "requireCanonical": true}`, or an `eth_call` at `latest` reads state from a different fork than
  the log came from.

### The dedupe key must separate the same event twice from the same event in a different history

The uniqueness key includes the branch of history the event was observed on, and the crediting path checks for an
unreversed twin before it acts.

**Shape**

```
unique(ledger id, branch id, transaction id, position within transaction)
before credit: assert no unreversed credit exists for (transaction id, position)
```

**How it appears**

- The key is `(chainId, blockHash, txHash, logIndex)`, all four under one unique constraint.
- `(txHash, logIndex)` carries a real unique constraint and is still wrong twice over: it makes re-crediting after a
  reorg impossible, because the re-included log collides with the orphaned row that `ON CONFLICT DO NOTHING` refuses
  to replace; and a transaction re-included in a different block at a different `logIndex` passes the constraint and
  double-credits.
- The four-part key avoids the double credit **only if the unwind already landed**, so before crediting a re-included
  log, assert that **no unreversed credit exists for `(chainId, txHash, logIndex)`**.

### An unwind is a new reversing entry keyed on what it reverses, never an erasure

Reverse a booked effect with a balancing entry keyed on the orphaned identity: never a delete, never an in-place edit,
never a bare debit of the holder's balance. Below your own retention floor there is nothing to reverse against, and
the correct behaviour is to halt.

**Shape**

```
divergence detected -> for each affected effect: reversing entry keyed on the orphaned identity
depth > retention floor -> unrecoverable-state halt, not a rollback
```

Three shapes to design out. A revert path that debits blocks of legitimate deposits which `ON CONFLICT … DO NOTHING`
then makes impossible to re-credit. A `CHECK (amount >= 0)` that aborts the revert transaction and wedges the indexer
in a permanent retry loop. A rollback deeper than the indexer's floor (graph-node's `ETHEREUM_REORG_THRESHOLD`
defaults to **250** blocks, commented *"Blocks cannot be reverted below the reorg threshold"*), which requires a
rebuild from a snapshot or genesis.

**How it appears:** Bitcoin Core's `listsinceblock` carries the mirror trap: *"transactions that were re-added in the
active chain will appear as-is in this array, and may thus have a positive confirmation count"*. Read each `removed`
entry's **current** `confirmations` before debiting anything.

### Credit the delta you measured at the authority, not the number in the notification

The amount in an event is a claim made by the code that emitted it. The quantity you owe is the change in the balance
the asset's own authority reports, and the scale of that quantity is read from the authority at runtime rather than
assumed.

**Shape**

```
read balance at authority (before) -> observe transfer -> read balance (after)
credit (after - before); scale comes from the authority, cached per (ledger, asset)
```

**How it appears**

- Call `balanceOf(address)` before and after (or read the token's own accounting entrypoint) and credit `after -
  before`. Fee-on-transfer and rebasing tokens make `Transfer.value` differ from what arrived, and a token address
  that is env-overridable (`USDC_ADDRESS` from config) makes this reachable today. Read `decimals()` **from the
  contract at runtime**, cached per `(chainId, address)`, never a hardcoded 18 or 6, never a constant table.
- An XRPL `Payment` carries `Amount` (`DeliverMax` in rippled API v2) **and** metadata `delivered_amount`, and under
  `tfPartialPayment` it returns `tesSUCCESS` having delivered an arbitrarily small fraction. Credit
  `delivered_amount`.
- Handle non-reverting `false` returns, calls to codeless addresses that succeed, and the approve race by setting the
  allowance to 0 first or using `permit`, never by granting `type(uint256).max`.

### Value arriving from an account you control is not income

Before any credit, assert the originator is outside your own perimeter: your own movements are indistinguishable from
a customer's at the protocol level.

**Shape**

```
before credit: assert originator NOT IN (every address we control)
```

A credited internal movement mints a liability with no matching debit, and shows up days later in a solvency
reconciliation, if one exists.

**How it appears**

- Assert `from_address NOT IN (our deposit addresses ∪ our hot / warm / cold addresses)`. Moving tokens between two
  addresses you own emits a **real** `Transfer` to a **real** deposit address. The `from_address` column usually
  already exists and is never read; make the credit path read it. Sweeps out of your own forwarders are the
  highest-volume instance, and they look exactly like customer deposits.

## The chain model decides the rule. Do not flatten it

Account-nonce, UTXO and memo-tagged ledgers are three different correctness problems, not three spellings of one. Key
on what is observable in the repo, never on a general prior.

| Observable | Model | What changes |
|---|---|---|
| per-customer address is a forwarder / `CREATE2` proxy contract, or a sweep job exists | account + forwarder | **spendable = base address only; confirmed = base + every receive address.** A withdrawal authorised on `confirmed` cannot be funded. The sweep is paid in native gas the deposit address does not hold. |
| one shared address per asset plus a `memo` / `destination_tag` / `tag` column | memo-ID | BitGo documents deposits on these chains as **immediately available in the spendable balance**, with no sweep, no gas tank, no forwarder. `spendable == confirmed`. The failure moves entirely to untagged and mis-tagged deposits. |
| `UTXO`, `outpoint`, `vout`, `PSBT`, coin selection, `changeAddress` | UTXO | Identity is the **input set**, not the txid. Change below `min_viable_change` is **paid to the miner** (a total, silent loss with no error). |

"Spendable is not confirmed" is true on forwarders and **false** on memo-ID chains: shipped unconditionally, it is
wrong half the time.

## An address is not always the whole destination

On some ledgers the routing information that identifies the beneficiary lives beside the address, not inside it. Where
that is true, an instruction with a well-formed address and no routing field delivers funds that no one can attribute,
and the ledger reports success.

**Shape**

```
destination = address + (routing field, where the model requires one)
schema column -> API field -> per-chain validation before broadcast
supported (asset, ledger) set is gated on the routing field existing end to end
```

A `SUPPORTED = {(USDT, ethereum), (USDT, tron)}` table that grows to include a tag-addressed chain without the column
is the failure path: nothing rejects, and the funds arrive un-creditable.

**How it appears**

- **XRP Ledger** uses `DestinationTag`, a **32-bit unsigned integer**; the receiving account can set `RequireDest`
  (`asfRequireDest`) so the ledger itself rejects an untagged payment with `tecDST_TAG_NEEDED`.
- **Stellar** uses `memo`, typed: `MEMO_TEXT` (**at most 28 bytes**), `MEMO_ID` (**64-bit unsigned integer**),
  `MEMO_HASH` (**32-byte hash**), `MEMO_RETURN` (32-byte hash of the transaction being refunded). SEP-29
  `memo_required` is enforced by the **sending** SDK only (the network accepts memo-less payments regardless), so a
  Stellar integration needs an operational path for untagged deposits, and Stellar's own docs now point at **muxed
  accounts**, which the integration must also handle. Publishing an X-address or an `M…` muxed account folds the tag
  into the address and removes the class.

## Confirmation depth is a loss budget, not a constant

Depth is a price you pay in latency for a bound on how much you can lose to a rewrite. Derive it from a stated loss
budget, per chain and per amount, and record the depth and the budget alongside the credit so the bet is auditable
afterwards.

**Shape**

```
depth = f(chain rewrite history, amount at risk, stated loss budget)
credit below depth only inside a bounded global exposure you accept losing
finality source stops advancing -> alarm and degrade explicitly
```

A hardcoded constant is a bet whose size nobody wrote down, usually copied from a different chain.

**How it appears**

- "12 confirmations" is folklore: Polygon has produced a 157-block reorg, and after the August 2020 attacks the ETC
  Cooperative asked integrators to raise confirmations above 12,000 because one attack reorganised over 7,000 blocks.
- **Never credit an L2 deposit on L2 block count**; wait for the L1 batch to finalize. Circle CCTP standard transfers
  on Ethereum, Arbitrum, Base, OP, Unichain, World Chain and X Layer wait **~65 Ethereum blocks, 15 to 19 minutes**,
  and its fast transfers are *"subject to a global allowance to mitigate reorganization risks"*. Alarm and degrade
  explicitly if the `finalized` head stops advancing: Ethereum went non-finalizing for over an hour on 2023-05-12.
- On Bitcoin Core, `confirmations` goes **negative**: `-6` means the transaction was conflicted six blocks ago.
  `abs(confirmations) >= N`, or storing `confirmations` in an unsigned column, converts a conflicted transaction into
  a fully confirmed one.

## The sequence allocator is a single writer, and the lock outlives the effect

Where the external ledger orders your instructions by a number you assign, that number is authoritative state with
exactly one writer. The lock must span allocation through to the durable record of what was sent, under a key that is
byte-identical in every replica.

**Shape**

```
lock(ledger, sender) spans: allocate -> sign -> broadcast -> record, one transaction
lock key derived from stable bytes, identical in every process
```

A lock released before the broadcast protects nothing; a key that differs per process is not a lock.

**How it appears**

- `pg_advisory_xact_lock(hash(chain) & 0x7FFFFFFF)` fails **both** halves: Python's `hash()` is salted per interpreter
  for `str` (`hash('ethereum')` differs per process while `hash(1) == 1` everywhere), so each replica takes a
  different lock; and `with engine.begin(): SELECT … FOR UPDATE` releases at the dedent, before the sign and broadcast
  it exists to protect.
- Use a stable digest of the UTF-8 key bytes and hold the transaction across the broadcast. Fireblocks solves this by
  serialising: it *"can only process a single transaction per blockchain standard per vault account"*. The throughput
  ceiling is one in-flight transaction per account, and sharding across accounts must happen **before** nonces are
  assigned, never after.

## The outbound queue proves it can succeed before it adds to the wall

A queue that keeps submitting into a condition that guarantees failure converts one stuck instruction into a silent
outage. Check the preconditions, then stop and page rather than broadcast.

**Shape**

```
before each broadcast: assert ordering is not blocked
                       assert the fee-paying reserve covers the queued depth
                       assert an absolute fee ceiling in the asset, not only a rate
any assertion fails -> halt the queue and page; do not submit more
```

**How it appears**

- **Nonce continuity.** If `pendingNonce - latestNonce > 0` for longer than a configured interval, **replace the
  lowest unmined nonce rather than submitting more transactions**: queued transactions behind a gap are capped at
  geth's `AccountQueue = 64` and evicted after `Lifetime = 3h`, so one stuck low-fee transaction at nonce N blocks
  every withdrawal behind it and then silently drops them.
- **Fee-paying balance.** The broadcasting account's native-gas balance covers a configured multiple of the current
  worst-case fee for the queued depth; a hot wallet out of gas stops all withdrawals with no error anyone reads.
- **Absolute fee ceiling.** Every constructed transaction carries a fee cap denominated in the asset, not only a
  fee-rate cap, because a rate cap does not bound the loss when the input amount is wrong. Bitcoin Core ships both
  (`DEFAULT_TRANSACTION_MAXFEE` = 0.1 BTC absolute, `DEFAULT_MAX_RAW_TX_FEE_RATE` per kvB); the Paxos payout of
  2023-09-10 paid **19.82 BTC of fee on a 0.0081 BTC transfer**, 198 times the absolute cap, and either check would
  have refused to broadcast it. The interval and the gas multiple are configuration with no default; the ceiling comes
  from your own worst-case payout size, never from a number in this file.

## A receipt proves inclusion, not effect

Inclusion in the external ledger says the instruction was processed, not that it did what you intended, and the
absence of a notification is not proof that nothing happened.

**Shape**

```
effect occurred = included AND status says success AND the effect itself is observable
no notification  -> unknown, not "did not happen"
```

**How it appears**

- `receipt.status == 0x1` is required before any effect is treated as having occurred. A mined-but-reverted
  transaction **consumes the nonce, burns the gas and emits no logs**. The absence of a `Transfer` log is not proof
  the send failed, and the presence of a receipt is not proof it succeeded.
- Contract-initiated native-currency transfers emit no logs at all and are invisible to a log-only indexer; detect
  them with `debug_traceBlock` / `trace_block` or by balance delta.
- Etherscan exposes them through a **separate** endpoint (`action=txlistinternal`, not `action=txlist`) keyed on
  `(hash, traceId)` (`hash` alone is not unique), and a successful parent transaction can contain a failed internal
  call. An exchange that indexes only ERC-20 `Transfer` events plus top-level native transactions systematically
  misses contract-originated deposits.

## Seam S2: onchain and ledger

*The on-chain half of the boundary. `fin-ledger` owns what the books record; load it too when the credit becomes a
posting.*

**(i) Identity.** A deposit credit is exactly one balanced ledger transaction whose idempotency key is `(chainId,
blockHash, txHash, logIndex)`, never the tx hash and never `balance += amount`; the same log re-observed after a
reconnect, a backfill overlap or a provider failover is a no-op. **(ii) Staging.** Observation posts to a per-user
**PENDING (unavailable)** account; the credit policy's finality moves it to **AVAILABLE**, which alone authorises
withdrawal and onward transfer. **(iii) Unwind.** A parent-hash mismatch produces a reversing balancing entry keyed on
the orphaned log identity; a reorg deeper than the indexer's rollback floor is an unrecoverable-state halt. **(iv)
Assertion.** A continuous reconciliation asserts `Σ credited at-or-below finalized height == Σ observed on-chain value
deltas to deposit addresses`. Name the authority and the join key and ship it as a scheduled entrypoint:
*reconciliation runs in production*, or it does not exist.

## Output

Every economic change ends with this block: seven labels, this order, one line each except `controls`.

```
FINANCIAL CHECK
tier:       T<n>, and the signal that placed it there
effect:     what moves value, from whom to whom, in what unit
identity:   the stable identity of the intent, durably recorded at file:line
ambiguity:  which counterparty responses are UNKNOWN, and how they resolve
authority:  whose copy of each quantity is the record
recovery:   what a crash or restart between the effect and the local commit does
controls:   <control> -> <file:line>, one per line; at T2 and above also `· <test name>`
            UNRESOLVED: <control> (<why>), for anything not implemented
```

Every control named is a real `file:line` or an explicit `UNRESOLVED:` line; a described control with no location is a
defect. This replaces any risk table below T2.

### T2 and above: add the crossing contract

Crediting a user deposit is T2, as is broadcasting a holder's withdrawal: the chain reconciles both. Holding the
signing authority is T3: which outputs are yours, the nonces or sequence numbers other systems consume, and any mint
or burn authority are the signer's own view, the third state no external oracle holds. Add this block, emitting only
the slots this change touches: the DEPOSIT half when the change touches a crediting path, the WITHDRAWAL half when it
touches a broadcast path, and neither half when it touches neither. An emitted slot carries a real `file:line`; an
emitted slot you cannot fill is the finding, and goes on the `controls` line as `UNRESOLVED`. Slots the change does
not touch are not findings. At T3, add the per-technique evidence table that `fin-verification` owns.

```
CHAIN CROSSING: <chain> · <account+forwarder | memo-id | utxo>

DEPOSIT   (emit only when the change touches a crediting path)
  dedupe key           (chainId, blockHash, txHash, logIndex), one unique constraint  file:line
  range completeness   how a short, capped or failed range is proven covered          file:line
  cursor guard         same predicate as the query, advanced only over a proven range file:line
  reorg detector       parent_hash chained against the stored block_hash              file:line
  unwind               reversing entry keyed on the orphaned log identity             file:line
  amount source        balanceOf delta or delivered_amount, not the event field       file:line
  self-transfer guard  from_address not in our addresses                              file:line
  finality gate        depth, the loss budget it buys, L1-vs-L2 unit                  file:line
  memo / tag           column + API field + per-chain validation, or "model: n/a"     file:line

WITHDRAWAL   (emit only when the change touches a broadcast path)
  intent identity      (chainId, from, nonce) | input set | (account, Sequence)       file:line
  broadcast hash set   every hash for the nonce, and the confirmer that reads all     file:line
  allocator lock       key derivation + the span it holds                             file:line
  queue preconditions  nonce continuity, gas balance, absolute fee cap                file:line
```

## References

Each row is an instruction. When the literal appears, read the file **immediately** and apply it in order. **Do not
summarise it.**

| file | read it immediately when the code contains |
|---|---|
| [transaction-identity.md](references/transaction-identity.md) | `@solana/web3.js`, `solders`, `getSignatureStatuses`, `lastValidBlockHeight`, `AdvanceNonceAccount`, durable nonce; or `replaces_tx_hash`, RBF, CPFP, `maxPriorityFeePerGas`, `already known`; or EIP-712 `domain`, `verifyingContract`, a bridge `(sourceChain, destChain, nonce)` replay key |
| [finality-and-reorgs.md](references/finality-and-reorgs.md) | `confirmations`, `finalized`, `safe`, `reorg`, `parent_hash`, `ETHEREUM_REORG_THRESHOLD`, `listsinceblock`, `include_removed`, a per-chain `MIN_CONFIRMATIONS` table, an L2 `sequencer` or batch-poster reference |
| [indexing.md](references/indexing.md) | `eth_getLogs`, `fromBlock`, `toBlock`, `getPastLogs`, `createEventFilter`, `maxBlockRange`, a `cursor` / `watermark` / `last_processed_block` table, `txlistinternal`, `debug_traceBlock` |
| [token-semantics.md](references/token-semantics.md) | `decimals()`, `balanceOf`, `Transfer(`, `approve`, `permit`, `safeTransferFrom`, `SafeERC20`, rebasing, fee-on-transfer; or `latestRoundData`, `AggregatorV3Interface`, `updatedAt`, `answeredInRound`, `slot0`, `getReserves`, `priceFeed`, `oracle` |
| [custody-and-wallets.md](references/custody-and-wallets.md) | `UTXO`, `outpoint`, `vout`, `PSBT`, `changeAddress`, coin selection, dust; `derivationPath`, `xpub`, `gapLimit`, `importdescriptors`; `sweep`, `gasTank`, `forwarder`; `sequenceId`, `externalTxId`, `treatAsGrossAmount`, batched or aggregated withdrawals; an import of `bitcoinjs-lib`, `xrpl`, `stellar-sdk`, `bitgo`, or a Fireblocks SDK |
