# EVM replacement, stuck nonces, and dropped transactions

> **Provenance**
> provider: go-ethereum, Fireblocks and BitGo · surface: replacement pricing and the mempool defaults behind it, stuck-nonce detection, and the custodian vocabularies for dropped, replaced and confirmed transactions
> version: the go-ethereum `legacypool` defaults as cited in the body, with no commit pinned; the Fireblocks and BitGo documentation as read in the original pass, undated.
> verified_at: not established
> sources: https://github.com/ethereum/go-ethereum/blob/master/core/txpool/errors.go · https://github.com/ethereum/go-ethereum · https://developers.fireblocks.com/reference/transaction-substatuses · https://developers.bitgo.com/
> verified: none in this pass. No sentence below was re-read against a source for the 2026-08-25 review pass.
> unverified: all of it. This file predates the provenance requirement and was not re-checked in the 2026-08-25 review pass, so its claims carry the confidence of their original sourcing and no more, with no date you can check. The numbers here are node and custodian policy, not protocol constants, and the file says so; what the missing date costs you is any assurance that the policy numbers quoted are still the current defaults. The URLs above are where a recheck starts; each of them resolved on 2026-08-25, and nothing in any of them was read against a claim in this file.
> revalidate_when: go-ethereum changes `PriceBump`, `AccountSlots`, `AccountQueue` or `Lifetime`, or renames an error string a submitter classifies on; Fireblocks changes the dropped-transaction sub-statuses or its bump percentage; BitGo changes the stuck-transaction endpoint or the fill-nonce intent; you run against a chain whose node defaults differ from geth's.

On EVM the identity is `(chainId, from, nonce)`, so every recovery decision is arithmetic on that nonce rather
than a hash lookup.

## Contents

- **EVM replacement**: the >=10% bump on both fee fields, worked; geth `PriceBump: 10` vs Fireblocks 15%;
  `replacement transaction underpriced`; `already known` means success.
- **EVM mempool eviction and stuck nonces**: `AccountSlots`/`AccountQueue`/`Lifetime`; `pendingNonce` minus
  `latestNonce` as the observable; BitGo `GET /potentialStuckTxs` and `intentType: "fillNonce"`.
- **Dropped, replaced, and confirmed racing each other**: Fireblocks `DROPPED_BY_BLOCKCHAIN` and its three
  causes; `INVALID_NONCE_TOO_LOW`, `INVALID_NONCE_FOR_RBF`, `GAS_PRICE_TOO_LOW_FOR_RBF`.

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
