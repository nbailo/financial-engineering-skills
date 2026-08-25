# Amount provenance: what the recipient actually received

The amount in a notification is a number the emitting code chose. What the recipient holds afterwards is
decided by the asset's or the protocol's own accounting, and a measured delta reads that only where
attribution is isolated.

## Contents

- **Amount provenance: the asset's own accounting**: where a measured delta attributes and where it does
  not, and the block-delta reconciliation an off-chain indexer can actually run.
- **The same shape off EVM**: XRPL `delivered_amount`, Stellar path payments.

## Amount provenance: the asset's own accounting

`Transfer.value` is a number the sender's contract chose to emit. What the recipient actually holds afterwards
is decided by the token's own code. These are the same number for most tokens on most days, which is exactly
why the divergence is never caught in staging. The invariant is that the credited number comes from the
asset's authoritative semantics, never from the notification's amount field. A measured balance delta is one
mechanism for reading those semantics, and it attributes correctly exactly where the movement it measures is
isolated.

| Condition inside the measurement window | Does the delta attribute to this transfer |
|---|---|
| one transfer, one account, nothing else moving that balance | yes; this is the case the pattern exists for |
| a second transfer to or from the same account | **no**; the delta is the net of both and splits nowhere |
| a rebasing or elastic-supply asset | **no**; the balance moved for a supply event with no transfer behind it |
| fee-on-transfer | the delta is the amount you can spend, and it is not `Transfer.value`; which of the two the counterparty considers delivered is the token's own accounting to answer, not yours |
| a transfer hook that reenters and moves the balance again | **no**; the two reads straddle the transfer, which is the reentrancy point |
| any unrelated state change on that account inside the window | **no**; the delta carries it too |

Where attribution is not isolated, the authoritative amount is the protocol's own accounting for that
transfer, and a delta is a cross-check rather than the source.

**Inside a contract you control**, the two reads and the transfer are atomic with respect to everything except
the token itself, so attribution is isolated unless the token reenters:

```solidity
uint256 before = IERC20(token).balanceOf(address(this));
IERC20(token).safeTransferFrom(msg.sender, address(this), amount);
uint256 received = IERC20(token).balanceOf(address(this)) - before;  // credit THIS
// `amount` is now only useful as an upper bound; never let it reach the accounting call.
```

**Off-chain, per log, attribution is not isolated and the delta is not available.** `balanceOf` is a state
read at a block boundary, so an indexer crediting one `Transfer` out of a block containing three cannot
attribute a per-log delta from state reads alone. The honest design is a two-layer one, and it is what the
SEAM S2(iv) assertion buys you:

1. **Credit** `Transfer.value`, but only for tokens on a record whose `fee_on_transfer` and `rebasing` flags
   are both explicitly `false`, recorded by a human at onboarding. An unrecorded token does not credit.
2. **Assert per block, per deposit address**, that `Σ credited == balanceOf(addr) delta across that block`,
   with both reads pinned by EIP-1898 block hash. On mismatch: quarantine the address, do not credit, page.

```python
BLK  = {"blockHash": blk.hash.hex(),        "requireCanonical": True}   # EIP-1898
PREV = {"blockHash": blk.parentHash.hex(),  "requireCanonical": True}   # the parent, not number-1

before = token.functions.balanceOf(addr).call(block_identifier=PREV)
after  = token.functions.balanceOf(addr).call(block_identifier=BLK)
delta  = after - before
credited = sum(int(log["data"], 16) for log in transfers_to(addr, blk))

if delta != credited:                       # fee-on-transfer, rebase, or a log you did not decode
    quarantine(addr, blk, delta, credited); return
```

`requireCanonical: true` makes a non-canonical block an error (`-32000`) instead of a wrong number; a missing
block is `-32001` (EIP-1898). Reading `balanceOf` at `latest` while processing a log from block N is the
defect EIP-1898 was written for: *"if there is a re-org in between when the balance of the sender is queried
… and when the balance of the recipient is queried, the balances may not reconcile."*

Three consequences that are schema decisions:

- **A `Transfer` with `value == 0` is a legal, normal transfer.** EIP-20: *"Transfers of 0 values MUST be
  treated as normal transfers and fire the `Transfer` event."* A zero-value credit is a no-op row, not an
  error and not a reason to skip the dedupe insert.
- **The token address must be a fixed, per-environment constant that is code-reviewed, not `os.environ`.**
  `USDC_ADDRESS` read from config is what makes the whole fee-on-transfer path reachable on a system whose
  only asset today is not fee-on-transfer. If the address is configurable, the guards are mandatory.
- **Rebasing tokens move balances with no `Transfer` at all**, so step 2 above fires spuriously on every
  rebase. For those, credit in the token's internal non-rebasing unit (a share or scaled-balance accessor)
  and store the accessor name in the token record. *(stETH's `sharesOf` and Aave's `scaledBalanceOf` are the
  commonly cited accessors; the exact signatures are **not established by the sources behind this file**;
  read them off the deployed contract before writing against them.)*

---

## The same shape off EVM

The requested-versus-delivered split is not an ERC-20 quirk. It is a protocol feature on two of the chains
most commonly used for exchange deposits.

**XRP Ledger.** A `Payment` carries `Amount` (renamed `DeliverMax` in rippled API v2 *"to make the field
name more specific to its behavior and help prevent the misunderstandings and exploit described below"*) and
the transaction metadata carries `delivered_amount`. With `tfPartialPayment` set, `Amount` is a **maximum**,
and the transaction returns `tesSUCCESS` having delivered an arbitrarily small fraction. XRPL publishes the
exploit as a numbered procedure, verbatim:

> 1. The malicious actor sends a Payment transaction to the institution. This transaction has a large
>    `Amount` field and has the `tfPartialPayment` flag enabled.
> 2. The partial payment succeeds (result code `tesSUCCESS`) but actually delivers a very small amount of the
>    currency specified.
> 3. The vulnerable institution reads the transaction's `Amount` field without looking at the `Flags` field
>    or `delivered_amount` metadata field.
> 4. The vulnerable institution credits the malicious actor in an external system … for the full `Amount`,
>    despite only receiving a much smaller `delivered_amount` in the XRP Ledger.
> 5. The malicious actor withdraws as much of the balance as possible to another system before the vulnerable
>    institution notices the discrepancy.

Two traps behind the obvious one:

- `delivered_amount` is *"generated on-demand for the request, and is not included in the binary format for
  transaction metadata, nor is it used when calculating the hash of the transaction metadata."* A pipeline
  that re-derives state from stored binary metadata **will not have the field at all**, and the fallback
  someone writes is `Amount`, which is the exploit.
- For partial payments in ledgers before **2014-01-20** the field is the literal string `"unavailable"`.
  A parser that coerces it gets `0` or throws. **Quarantine, never coerce, never fall back to `Amount`.**

The remediation XRPL itself names is the reconciliation invariant, not a field fix: *"Never process a
withdrawal if the total balance you hold in the XRP Ledger does not match your expected assets and
obligations."* That is the same assertion as SEAM S2(iv), stated by the protocol maintainers.

**Cross-currency and Stellar.** An XRPL cross-currency payment delivers through order books where *"the
exchange rate when trading currencies may vary"*; a Stellar path payment can deliver a **different asset**
than the one the sender named. In both cases the field naming the sender's intent and the field naming the
delivery are different fields. The generalisation for every chain in this file: **the credited number comes
from the protocol's own accounting for the delivery, which is the delivery-metadata field wherever the
protocol publishes one and a measured delta where attribution is isolated. It is never the amount the sender
requested.**
