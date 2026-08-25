# Transaction identity, replacement, and re-submission

This reference covers what identifies a value-moving intent on each chain model, and what a re-submission
therefore means. It carries the per-model identity (EVM `(chainId, from, nonce)`, UTXO input set, XRPL
`(account, Sequence)`, Solana signature over a recent blockhash), the replacement rules and fee-bump
thresholds each mempool enforces, the node and custodian error strings that classify an ambiguous broadcast,
and the two identity systems layered on top of chain identity: EIP-712 signed orders and cross-chain message
replay keys. It exists because the tx hash is the field everyone stores and it is not the identity of
anything.

## Contents

- **Identity per chain model**: the four identities in one table; why a tx hash identifies a *serialization*.
- **The broadcast-hash set**: the schema and the confirmer that reads the whole set.
- **EVM replacement**: the ≥10% bump on both fee fields, worked; geth `PriceBump: 10` vs Fireblocks 15%;
  `replacement transaction underpriced`; `already known` means success.
- **EVM mempool eviction and stuck nonces**: `AccountSlots`/`AccountQueue`/`Lifetime`; `pendingNonce −
  latestNonce` as the observable; BitGo `GET /potentialStuckTxs` and `intentType: "fillNonce"`.
- **Dropped, replaced, and confirmed racing each other**: Fireblocks `DROPPED_BY_BLOCKCHAIN` and its three
  causes; `INVALID_NONCE_TOO_LOW`, `INVALID_NONCE_FOR_RBF`, `GAS_PRICE_TOO_LOW_FOR_RBF`.
- **UTXO replacement**: BIP-125's five rules verbatim; Core 28.0 full-RBF by default and the death of
  `nSequence` as a signal; RBF vs CPFP; ancestor/descendant limits.
- **Solana submission and retry**: rebroadcast identical bytes; re-sign only past `lastValidBlockHeight`;
  `getSignatureStatuses` with `searchTransactionHistory`; durable nonces' asymmetric failure.
- **XRPL submission**: provisional vs final by result code; the persist-before-submit record set; retry by
  varying only `Fee` and `LastLedgerSequence`; `AccountTxnID` and the two-active-senders stop condition.
- **Request-scoped idempotency at custodians**: Fireblocks `externalTxId` vs BitGo `sequenceId`; the 24-hour
  `Idempotency-Key` and why it is a different mechanism.
- **EIP-712 signed orders**: the domain separator; per-protocol cancellation (Seaport counter, 0x salt floor,
  Permit2 vs EIP-2612 nonce spaces); validated orders outliving their signatures; expiry as a control.
- **Cross-chain message identity**: `(sourceChain, destChain, nonce)` consumed atomically with the effect;
  duplicate relayer delivery as the default case; source/destination reorg coupling.

## Identity per chain model

A transaction hash is a digest of one *serialization* of one *attempt*: change a fee field, a blockhash or an
input and the hash changes while the intent does not. What the consensus layer makes mutually exclusive is the
identity below, a far stronger guarantee than any application-level dedupe you can write.

| Model | Identity of the intent | What the chain guarantees | What re-signing does |
|---|---|---|---|
| EVM | `(chainId, from, nonce)` | At most one tx per `(from, nonce)` is ever canonical | Same nonce → mutually exclusive. New nonce → **both can mine** |
| UTXO (Bitcoin) | The **input set** (`(txid, vout)` outpoints) | Two txs spending a common outpoint are mutually exclusive | Same inputs → one wins. **Different inputs → both confirm** |
| XRPL | `(account, Sequence)` | A `Sequence` is consumed exactly once per account | Same `Sequence` → mutually exclusive. New `Sequence` → double payment |
| Solana | The signature over the message, which **includes `recentBlockhash`** | The runtime dedupes identical signatures while the blockhash is in the last **151** blockhashes | New blockhash → **new signature**; if the old blockhash has not expired, **both execute** |

Two consequences that people get backwards:

- On UTXO chains, "re-signing the withdrawal" is safe **only** if coin selection is deterministic and picks
  the same outpoints; a wallet that re-runs selection after a new deposit arrives may pick a different input
  set, and both transactions confirm. Fireblocks reports the losing side of a same-input race as
  `DOUBLE_SPENDING`: *"Only the initial transaction is processed in this case"* (Fireblocks sub-statuses).
- On EVM, a private-mempool or bundle submission is not visible to `eth_getTransactionByHash` until inclusion.
  Inferring "dropped, resend at a new nonce" from a null result is a double-send. The nonce check
  (`eth_getTransactionCount(from, "latest")`) still works; the mempool lookup does not.

## The broadcast-hash set

Every hash you ever broadcast for one intent belongs to a set, and the confirmer reads the set, not a column.
The measured failure: an implementation writes `replaces_tx_hash`, documents that the confirmer will *"also
match a receipt for the superseded tx"*, and never reads the column: the original never confirms, the
withdrawal is marked failed, and the user is re-credited on funds that already left.

```sql
CREATE TABLE withdrawal_intent (
  intent_id     uuid PRIMARY KEY,
  chain_id      integer     NOT NULL,
  from_address  bytea       NOT NULL,
  nonce         bigint      NOT NULL,          -- EVM nonce; XRPL Sequence; NULL on UTXO
  input_set     bytea[]     NULL,              -- UTXO: sorted (txid||vout) outpoints
  UNIQUE (chain_id, from_address, nonce)       -- the identity, enforced
);

CREATE TABLE broadcast_attempt (
  intent_id     uuid        NOT NULL REFERENCES withdrawal_intent,
  tx_hash       bytea       NOT NULL,
  broadcast_at  timestamptz NOT NULL,
  max_fee       numeric     NOT NULL,          -- wei
  max_prio_fee  numeric     NOT NULL,          -- wei
  PRIMARY KEY (intent_id, tx_hash)             -- a set, not a "current" pointer
);
```

The confirmer, in order: (1) read `latestNonce = eth_getTransactionCount(from, "latest")`; (2) if
`latestNonce <= intent.nonce`, nothing at this nonce has mined: the intent is still live, whatever the hash
lookups say; (3) otherwise the nonce is consumed, so fetch `eth_getTransactionReceipt` for **every** hash in
`broadcast_attempt` and settle on the one that returns non-null; (4) if the nonce is consumed and no hash in
your set has a receipt, someone else replaced your transaction: that is an unreconciled economic fact, not a
retry condition. Stop and page.

## EVM replacement

geth's `legacypool` `DefaultConfig` sets `PriceBump: 10`, *"Minimum price bump percentage to replace an
already existing transaction (nonce)"*. For a type-2 (EIP-1559) transaction the bump must be satisfied on
**both** fee fields independently. Bumping only `maxFeePerGas` is the classic silent no-op.

```
original:    maxFeePerGas = 30 gwei   maxPriorityFeePerGas = 1 gwei
replacement: maxFeePerGas = 50 gwei   maxPriorityFeePerGas = 1 gwei     ← rejected

  maxFeePerGas          50 >= ceil(30 * 110 / 100) = 33   ✓
  maxPriorityFeePerGas   1 >= ceil( 1 * 110 / 100) =  2   ✗   (integer ceiling: 1.1 → 2 wei-exact math)
  → eth_sendRawTransaction returns "replacement transaction underpriced"
  → the ORIGINAL stays in the pool at 30/1 and stays stuck
```

Compute both thresholds with ceiling integer arithmetic on wei, not floats, and re-check the result before
signing. Bump amounts to know:

| Source | Threshold | Note |
|---|---|---|
| go-ethereum `legacypool` `DefaultConfig.PriceBump` | **10%** | The public-mempool floor for most EVM nodes |
| Fireblocks `GAS_PRICE_TOO_LOW_FOR_RBF` | **15%** | *"Resubmit the RBF transaction with a gas price at least 15% higher than the original."* |
| EIP-1559 `BASE_FEE_MAX_CHANGE_DENOMINATOR = 8` | base fee moves **±12.5% per block** | A 10% bump does not keep up with one block of rising base fee (derived, and the reason bumps often need repeating) |

**The replacement threshold is a policy of whichever mempool you are talking to, not a protocol constant.**
Read it from configuration per chain and per custodian.

Error strings from `go-ethereum/core/txpool/errors.go` that a submitter must classify, not log:

| String | Means | Correct response |
|---|---|---|
| `already known` | Your exact transaction is in the pool | **Success.** Do not re-sign, do not bump, do not raise |
| `replacement transaction underpriced` | Bump failed; original still live | Recompute both thresholds and re-bump. Never re-sign at a new nonce |
| `transaction underpriced` | Below the pool's minimum, not a replacement | Raise the fee at the same nonce |
| `nonce too low` | The nonce is already consumed on chain | Resolve via receipt lookup over the whole hash set |
| `account limit exceeded` | You hit `AccountQueue`/`AccountSlots` | Stop submitting. You have a nonce gap; see below |
| `exceeds block gas limit` | Malformed intent | Do not retry |

A timeout on `eth_sendRawTransaction` is ambiguous in the dangerous direction: the node may have accepted and
gossiped it. Re-broadcasting the **identical signed bytes** is always safe and returns `already known`.
Re-signing at a new nonce on timeout produces two live transactions.

## EVM mempool eviction and stuck nonces

A transaction whose nonce is above the account's next executable nonce sits in the *queued* (non-executable)
sub-pool and cannot mine until the gap fills. geth `legacypool` defaults:

| Field | Default | Effect |
|---|---|---|
| `AccountSlots` | **16** | Executable (pending) slots guaranteed per account |
| `AccountQueue` | **64** | Non-executable slots per account; beyond it, `account limit exceeded` |
| `GlobalSlots` | 4096 + 1024 | Pool-wide executable capacity |
| `GlobalQueue` | 1024 | Pool-wide non-executable capacity |
| `Lifetime` | **3h** | *"Maximum amount of time an account can remain stale in the non-executable pool"*; then the queued transactions are **dropped with no notification** |

There is no "your transaction was dropped" event on any EVM chain. The only observable is arithmetic:

```
gap = eth_getTransactionCount(from, "pending") - eth_getTransactionCount(from, "latest")
```

If `gap > 0` for longer than a configured interval, the remediation is to **replace the lowest unmined nonce**,
not to submit more transactions behind it. Submitting more fills `AccountQueue`, and at the 3-hour
`Lifetime` the whole backlog vanishes; when someone finally replaces nonce N, a three-hour queue of payouts
executes at once at prices from a different market.

BitGo exposes this as a first-class endpoint rather than an inference (BitGo *Withdraw: nonce holes*):
`GET /potentialStuckTxs` returns `"cause": "nonceHole"`, a `stuckTx` webhook fires, and the remediation is a
deliberate no-op payment with `intentType: "fillNonce"` at the exact stuck nonce. Named causes: *"Dropped
Messages… Asynchronous Execution… Crashes and Restarts — A participant may lose track of the last used nonce
if no persistence mechanism exists."* Off a custodian, you own both the detector and the fill transaction.

## Dropped, replaced, and confirmed racing each other

Fireblocks' `DROPPED_BY_BLOCKCHAIN` is one status with three causes that have **identical observable symptoms
and different correct responses** (Fireblocks sub-statuses reference): *"Nonce replacement (RBF) — A
higher-fee transaction with the same nonce replaced the original… Mempool eviction — The transaction was
removed from the mempool due to a low gas price, network congestion, or a timeout… Network rejection — The
transaction was rejected by the node before it could be included in a block."*

| Cause | Chain truth | Correct response |
|---|---|---|
| Nonce replacement | Another tx **at your nonce** may have mined | Resolve by nonce + full hash set. **Never re-issue at a new nonce until the nonce is proven unconsumed** |
| Mempool eviction | Nonce unconsumed; nothing happened | Re-broadcast identical bytes, or bump at the same nonce |
| Network rejection | Never entered the pool | Fix the transaction; the nonce is free |

The three sub-statuses that classify a cancel/bump race:

- `INVALID_NONCE_TOO_LOW`: *"The nonce chosen for this transaction already belongs to a transaction that was
  previously confirmed on this account. This may occur when an RBF or drop transaction is used to replace a
  pending transaction that was completed before being replaced."* **You tried to cancel a payout and the
  original landed anyway.** This is an accounting event: the money left. Do not re-issue.
- `INVALID_NONCE_FOR_RBF`: *"Fireblocks did not find a matching pending transaction… RBF transactions can
  only be used with transactions that are not Failed or Confirmed."*
- `GAS_PRICE_TOO_LOW_FOR_RBF`: the 15% bump above.

Fireblocks also documents that *"you may receive multiple updates to the Completed status"* (on appearance
and again on further confirmations), so the `COMPLETED` handler must be idempotent on the intent id.

## UTXO replacement

BIP-125's five replacement rules, verbatim from `bitcoin/bips/bip-0125.mediawiki`:

1. The original transactions signal replaceability explicitly or through inheritance (*"any of its inputs have
   an nSequence number less than (0xffffffff - 1)"*).
2. *"The replacement transaction may only include an unconfirmed input if that input was included in one of
   the original transactions."*
3. *"The replacement transaction pays an absolute fee of at least the sum paid by the original transactions."*
4. It pays for its own bandwidth at the node's minimum relay fee: *"if the minimum relay fee is 1
   satoshi/byte and the replacement transaction is 500 bytes total, then the replacement must pay a fee at
   least 500 satoshis higher than the sum of the originals."*
5. At most **100** transactions may be evicted by the replacement.

**Bitcoin Core 28.0 changed the default of `-mempoolfullrbf` from 0 to 1** (28.0 release notes). Rule 1 is
therefore a *relay policy* on nodes that still run opt-in RBF, and on a full-RBF node **every** unconfirmed
transaction is replaceable regardless of `nSequence`. Any credit policy that reads `nSequence` to decide
whether a zero-conf deposit is safe is reading a field that no longer carries that meaning. Note also that
your own sweeps signal RBF by default: Core sets `nSequence = maxint-2` (`src/wallet/spend.cpp`, comment:
*"BIP125 defines opt-in RBF as any nSequence < maxint-1, so we use the highest possible value in that range"*)
with `DEFAULT_WALLET_RBF = true`, and BitGo's *"Consolidation transactions for bitcoin automatically opt-in to
acceleration using Replace-By-Fee (RBF)."*

RBF versus CPFP is an accounting choice, not only a fee choice. BitGo: *"only the transaction sender can
create an RBF transaction, and miners confirm only one of the two transactions… One key benefit of RBF over
CPFP is that it doesn't require the presence of a change output in the original stuck transaction."*

| | RBF | CPFP |
|---|---|---|
| Parent txid | **Invalidated**: a new txid confirms | **Preserved** |
| Precondition | Sender-only; replacement must satisfy BIP-125 rules 2–5 | Requires a spendable output of the parent (usually the change output) |
| Cost | One transaction's fee, raised | Two transactions' fees |
| Ledger impact for a batched payout (one tx, N recipients) | All N ledger rows keyed on the parent txid are invalidated at once | All N rows keep their key |

**Prefer CPFP for a batch payout whose ledger rows reference the txid; use RBF only where the ledger is keyed
on intent.** And whichever you use, the batch's idempotency key must be per-recipient-payout, with the batch
as a grouping over those keys, never a replacement for them.

Mempool topology caps how much a replacement or a sweep chain can do:
`DEFAULT_ANCESTOR_LIMIT{25}` / `DEFAULT_DESCENDANT_LIMIT{25}` (`src/policy/policy.h`; Fireblocks surfaces the
rejection as `TOO_LONG_MEMPOOL_CHAIN`, *"too many unconfirmed transactions pending from an address"*),
`MAX_STANDARD_TX_WEIGHT{400'000}` = 100 kvB, and for v3/TRUC transactions `TRUC_MAX_VSIZE{10'000}` with a
single child of at most `TRUC_CHILD_MAX_VSIZE{1000}`. Validate a batch against these **before signing** and
treat a relay rejection as "not sent, and provably not sent".

## Solana submission and retry

The signature covers the message, and the message contains `recentBlockhash`, so **re-signing is a different
transaction**. The blockhash is valid while it is within the last **151** blockhashes, *"about 60 to 90
seconds"* (solana.com confirmation guide).

```python
sig = client.send_raw_transaction(signed_bytes)          # identical bytes every time
while True:
    st = client.get_signature_statuses([sig], search_transaction_history=True).value[0]
    if st is not None:
        if st.err is not None:
            raise LandedAndFailed(sig, st.err)   # fee paid, slot consumed; NOT retryable
        if st.confirmation_status in ("confirmed", "finalized"):
            return sig
    # not found, or found but not yet confirmed
    if client.get_block_height(commitment="confirmed") > last_valid_block_height:
        break                                    # ONLY now may you re-sign
    client.send_raw_transaction(signed_bytes)    # rebroadcast, idempotent
    sleep(2)
re_sign_with_fresh_blockhash()
```

Four things this encodes:

- **Rebroadcast the identical signed bytes.** The runtime dedupes identical signatures inside the validity
  window, so rebroadcast is idempotent; re-signing is not. Solana's retry guide is explicit: *"Before
  re-signing any transaction, it is very important to ensure that the initial transaction's blockhash has
  expired"*; otherwise both versions can land and the user *"unintentionally sent the same transaction
  twice."*
- **`searchTransactionHistory: true`.** Without it, `getSignatureStatuses` searches only the recent status
  cache (active slots plus `MAX_RECENT_BLOCKHASHES` rooted slots). Treating a cache miss as "not executed, safe
  to retry" is a double-spend.
- **`err != null` is landed-and-failed.** The fee was paid and the blockhash/nonce consumed. It is an
  accounting outcome, not a retry condition. And `confirmations: null` on a status means *"the transaction is
  rooted and finalized by a supermajority of stake"*; null is the strongest state, not the weakest.
- **The RPC will stop rebroadcasting for you.** RPC nodes rebroadcast every ~2 s until finality or blockhash
  expiry, but *"if the outstanding rebroadcast queue size is greater than 10,000 transactions, newly submitted
  transactions are dropped"*, and *"if an RPC node can't determine when your transaction expires, it will only
  forward your transaction one time and afterwards will then drop the transaction."* Your loop, not theirs.

**Durable nonces** replace `recentBlockhash` with a nonce-account value, and their failure semantics are
asymmetric (solana.com durable-nonces doc). `AdvanceNonceAccount` must be **instruction index 0**, or the
transaction is treated as an ordinary blockhash transaction and fails on staleness. A **validation** failure
(nonce already used, account missing, authority unsigned) means *"the entire transaction is dropped. No fees
collected, no state changes."* An **execution** failure after validation still advances the nonce and collects
fees: *"This prevents the transaction from being replayed."* So a durable-nonce transaction that "failed" may
have consumed the nonce, and the next transaction built on the old value is dropped, not retried. The docs
warn durable nonces *"may be deprecated in a future release"*.

One more identity trap: **track blocks by hash, not slot number.** A slot number does not distinguish two
blocks produced for the same slot on competing forks; the blockhash does.

## XRPL submission

XRPL publishes the clearest withdrawal-correctness spec of any chain, and it names the failure directly:
*"an application that fails to find a prior successful payment transaction might erroneously submit another
transaction, duplicating the original payment"* (XRPL, *Reliable Transaction Submission*).

**Provisional is not final.** *"A transaction that succeeded initially could still fail, and a transaction
that failed initially could still succeed."* Finality is per result code (XRPL, *Finality of Results*):

| Result | Final when |
|---|---|
| `tesSUCCESS` | Included in a **validated** ledger |
| any `tec` | Included in a validated ledger: a *failed* transaction is also final, and it **did** destroy the transaction cost |
| any `tem` | Final unless the protocol changes |
| `tefPAST_SEQ` | Another transaction with the same `Sequence` is validated |
| `tefMAX_LEDGER` | A validated ledger exceeds `LastLedgerSequence` **and** the transaction is in none of them |

*"Any other transaction result is potentially not final."* A pipeline that acts on the first result it sees
will be wrong in both directions.

**Persist before you submit**, and drive recovery from the record: `(transaction hash, LastLedgerSequence,
sender address, sender Sequence, latest validated ledger index at submission, application data)`. On restart,
for each persisted transaction with no validated result, query by hash and branch on whether the account's
current `Sequence` has already passed the transaction's `Sequence`.

**Retry by varying only `Fee` and `LastLedgerSequence`.** XRPL: *"it is more likely that a new transaction is
likely to succeed if you change only the LastLedgerSequence and possibly the Fee and submit again. **Use the
same Sequence number as the original transaction.**"* Setting `LastLedgerSequence` at all (the docs' automation
guidance is last validated + 4) is what converts "never mined" from an unbounded state into a decidable
terminal one.

**Two active senders on one account is a specifically anticipated disaster,** and the prescribed response is a
halt, not a degradation: *"If you have two or more transaction-sending systems in an active/passive failover
configuration, it's possible that the passive system mistakenly believes the active system has failed…
Any different transactions with the same Sequence numbers have failed permanently… you are in an unexpected
state and should stop processing until you have determined why that has happened; otherwise, your system might
send multiple transactions trying to do the same thing."* XRPL also offers `AccountTxnID`, which chains each
transaction to the hash of the account's previous one, a protocol-level guard that makes a split-brain fail
closed instead of double-paying.

## Request-scoped idempotency at custodians

Two custody platforms converged on a request-scoped token and gave it **opposite** semantics. A client that
handles one as if it were the other has defeated the mechanism.

| | Fireblocks `externalTxId` | BitGo `sequenceId` | Fireblocks `Idempotency-Key` (HTTP header) |
|---|---|---|---|
| Scope | One transaction, forever | One transaction request | One HTTP request |
| On reuse | *"Fireblocks will automatically reject all future transactions with the same ID"*: **HTTP 400** | Look it up and branch on `state` | Replays the first response, *"including error responses"* |
| Lifetime | No documented expiry | No documented expiry | **24 hours**, then *"generate a new key"* |
| Resolution after an ambiguous send | `GET /transactions/external_tx_id/{id}` | If not registered, *"you can safely retry the original withdrawal"*; if registered and `state` is `pendingDelivery`, rebuild, re-sign, and send the same request | N/A |

**Treat a duplicate-id rejection as evidence the payout exists.** A 400 on `externalTxId` means the
transaction was created; Fireblocks' own motivation is *"situations where, even though a submitted
transaction responds with an error due to an internet outage, the transaction was still sent to and processed
on the blockchain."* A client that reads the 400 as a failure and re-issues under a fresh id has converted the
safety mechanism into a double payment.

The HTTP key and the transaction token are **not interchangeable**. A retry at T+25 h carrying the same
`Idempotency-Key` is a brand-new request as far as the server is concerned; only `externalTxId` still protects
it. Send both.

## EIP-712 signed orders

An off-chain signed order has its own identity, layered on top of chain identity, and its own cancellation
semantics. The domain separator is the replay boundary and `version` is part of it. EIP-712: *"Signatures
from different versions are not compatible"*; `chainId` is *"the EIP-155 chain id. The user-agent should
refuse signing if it does not match the currently active chain"*; `verifyingContract` is *"the address of the
contract that will verify the signature."* Omitting `chainId` makes a signature valid on **every** EVM chain
where that address exists. EIP-2612's security considerations add the residual case: *"If `DOMAIN_SEPARATOR`
embeds `chainId` at deployment rather than reconstructing it per signature, future chain splits could enable
replay attacks across chains."*

Cancellation is protocol-specific and **non-interchangeable**. Getting it wrong means an order you believe is
cancelled is still fillable.

| Protocol | Identity / nonce space | Cancel mechanism | The trap |
|---|---|---|---|
| **Seaport** | Per-offerer `counter` inside the signed struct | `incrementCounter` cancels **all** the offerer's open orders | Since v1.2 the counter advances *"by a quasi-random value derived from the last block hash"*; you **cannot** compute `old + 1`; read `getCounter(offerer)` after the transaction. Bulk-signed orders must be cancelled individually or nuked wholesale. Cancelling a private order **publishes its parameters** |
| **0x v4** | `salt`, per `(maker, makerToken, takerToken)` | `cancelPairLimitOrders(makerToken, takerToken, minValidSalt)`: a monotonic floor; *"the new salt [must] be >= the old salt"* | Random salts make bulk cancel unusable. Salts must encode time monotonically **at issuance**, not at cancel time |
| **Permit2 (AllowanceTransfer)** | `uint48 nonce` per `(owner, token, spender)`; must equal current, increments by 1 | `invalidateNonces`, requires `newNonce > oldNonce`, rejects a jump `> type(uint16).max` (`ExcessiveInvalidation`) | Two concurrently issued permits for the same triple **cannot both be valid**. Two clocks: `expiration` bounds the allowance, `sigDeadline` bounds the signature (`AllowanceExpired`) |
| **EIP-2612** | One sequential nonce per owner, **across all permits for that token** | Consume the nonce | A service issuing permits concurrently for the same user races itself |

Two failures that survive a correct cancel design:

- **A validated order outlives its signature.** Seaport orders that were `validate`d or partially filled skip
  signature validation on later fills, so an EIP-1271 smart-account order stays fillable after the signature
  stops being valid. It must be **explicitly cancelled**. Seaport's `OrderValidator` codes name the states:
  800 (cancelled), 1102/1103 (signature counter below/above current).
- **Expiry is a correctness parameter, not a UX field.** An order with no `validTo`/`expiry`/`deadline` is a
  perpetual free option written against you; the same control makes a stuck-then-mined-hours-later swap fail
  instead of filling at a price from a different world. Serialise issuance per nonce space so you never sign
  two live orders at the same nonce.

## Cross-chain message identity

**Scope replay protection to `(sourceChainId, destChainId, messageNonce)` and consume it in the same atomic
step as the effect.** Never let a default or zero value satisfy the validity check: Nomad initialised
`_committedRoot` to `bytes32(0)`, which set `confirmAt[bytes32(0)] = 1`, so `acceptableRoot(0)` returned true
for any unproven message: >$190M, and then **hundreds of copycats who simply copied the calldata and
substituted their own recipient address**. The post-exploit failure mode was *replay*, and replay was trivially
available because no per-message consumption gate worked.

**Duplicate relayer delivery is the default case, not the exception.** Multiple relayers competing to deliver
the same message is normal operation. The destination contract must be idempotent on the message id, and the
off-chain accounting must be idempotent on the **same key**, never on the delivery transaction hash, which
differs per relayer.

**Derive the credit from an observed value delta, not from an event the caller shaped.** Qubit's QBridge
`deposit` on the Ethereum side accepted a call with **no ETH attached**, still emitted the deposit event, and
the BSC side minted qXETH against it: ~$80M. And verifying that a check ran is not verifying it ran on your
data: Wormhole's `load_instruction_at` confirmed a secp256k1 verification instruction existed but not that it
came from the real Instructions sysvar, so a forged account satisfied the check in a different context:
120,000 wETH.

**Source and destination reorgs are coupled, and the coupling is a design parameter.** Minting on the
destination before the source is final produces destination tokens with no backing; the source-chain
confirmation depth **is** the capital-at-risk parameter. Circle's CCTP encodes both sides explicitly: standard
transfers wait for hard finality (~65 Ethereum blocks, 15–19 min, on Ethereum/Arbitrum/Base/OP/Unichain/World
Chain/X Layer), while fast transfers credit at 1–2 confirmations and are *"subject to a global allowance to
mitigate reorganization risks"*. That is the generalizable shape: `credit_immediately_up_to(X) &&
wait_for_finality_above(X)`, where X is a number you have decided you are willing to lose. The OP Stack
interop model states the coupling as an invariant (*"should a reorg happen, either both the source and
destination transactions remain or both of them revert"*), which is a property of **that** protocol, not one
to assume of a bridge you did not verify.

One address-level corollary: **the same address is not the same owner on another chain.** Wintermute sent 20M
OP to a Safe address they controlled on mainnet but not on Optimism; the old ProxyFactory used `CREATE`
(address = f(factory, deployer nonce)), so an attacker replayed Safe deployments on Optimism until the nonce
produced the identical address. EOAs are the exception; **contracts are not**. Verify destination-side control
per chain before sending.
