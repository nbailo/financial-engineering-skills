# The three states, sweeps, and wallet accounting

Beyond the chain and your own books there is the wallet's own view of which outputs are its own and what is
spendable. A divergence between the three raises nothing, so a two-sided reconciliation is the only detector.

## Contents

- **The three states, named**: chain, wallet, ledger; what a divergence looks like and why nothing throws.
- **Spendable versus confirmed at the custodian**: forwarder chains, and the memo-ID chains where the two
  numbers are equal.
- **Sweeps and gas tanks**: forwarders, the FX-exposed sweep cost, three fee sources, the invisible fee.
- **Uncreditable deposits**: funds you hold that belong to no customer, and the house account they need.
- **Hot/warm/cold and multisig operations**: in-flight ledgering, quorum liveness, two-sided reconciliation.

## The three states, named

| state | authority | read through | a wrong read produces |
|---|---|---|---|
| **chain** | consensus | `getblock`, `gettxout`, `eth_getLogs`, `eth_getBalance`, `listsinceblock` | crediting what did not arrive, or missing what did |
| **wallet** | your own DB plus the node's wallet | `getbalances`, `listunspent`, the allocated-nonce table, the address-index counter, live signing sessions | reused nonce, double-spent UTXO set, change to fee, deposits past the gap |
| **ledger** | double-entry postings | balanced transactions and their idempotency keys | phantom credit, unreversed debit, understated house expense |

None of the three raises when it diverges from another: a stale UTXO in the wallet's set produces a *valid*
transaction, and a nonce the wallet thinks is free produces a *valid* signature. The only detector is a
reconciliation that reads the chain independently of the library maintaining the wallet, in both directions;
see the break table at the end of this file.

## Spendable versus confirmed at the custodian

On the account+forwarder model the split between what may fund a payout and what is merely confirmed
appears at the custodian's API. BitGo, `docs/consolidations`:
"For account-based assets, the spendable balance of a wallet is the balance of the assets in the base address.
The spendable balance is the total amount available for withdrawal. The confirmed balance is the balance of
the base address plus all the receive addresses." Authorising a withdrawal against `confirmed` authorises
money that is sitting in forwarder contracts and has not been swept.

On memo-ID chains it is inverted: BitGo lists ~20 chains (XRP, Stellar, Cosmos, EOS, Hedera, TON, ICP, SEI,
Injective, …) where "all deposited assets are immediately available in the spendable balance": no forwarder,
no sweep, no gas tank, `spendable == confirmed`. Shipping "spendable ≠ confirmed" unconditionally is wrong on
exactly these chains; shipping the memo-ID assumption is wrong on all the others.

## Sweeps and gas tanks

Most EVM chains have no native multi-receive-address concept, so a per-customer deposit address is a forwarder
contract whose only capability is sending to the wallet's base address. Three consequences a ledger must model:

1. **The sweep is denominated in a different asset from the deposit.** BitGo: "When you send an ERC-20 token,
   for example, the gas fee is paid in the chain's native asset (ETH for Ethereum, MATIC for Polygon, etc.).
   Your gas tank holds that native asset." Fireblocks surfaces the failure as `INSUFFICIENT_FUNDS_FOR_FEE`.
   The cost of accepting a deposit is `gas_used × gas_price × FX(native → accounting currency)`, an
   FX-exposed number that keeps moving after you quoted the deposit as free.
2. **A deposit can be worth less than the gas to move it.** Those balances are confirmed, are not spendable,
   and must be classified out of the asset side of the solvency invariant rather than counted at face value.
3. **The fee may be paid by an account that is neither leg of the transfer.** BitGo names three sources:

| fee source | who holds it | visible in a wallet-delta reconciliation |
|---|---|---|
| base address | the wallet itself, in native coin | yes |
| **enterprise gas tank** | a separate address BitGo "funds separately from your wallet balance" | **no** |
| input UTXOs | taken from the value of the transaction's own inputs | yes, as a reduced output total |

The gas-tank case is the one that corrupts the books quietly. A sweep of 1,000 USDC moves exactly 1,000 USDC
between two USDC accounts while consuming ETH from a third account that appears in neither. A reconciliation
that sums the deltas of the two USDC accounts balances perfectly, and house expense is understated without
bound. Both legs must be posted:

```
DR  onchain:base:USDC                 1000.000000
CR  onchain:forwarder:0xab…:USDC      1000.000000     # value-neutral: no customer liability account appears
DR  expense:network-fees:ETH             0.00214
CR  onchain:gas-tank:ETH                 0.00214      # the leg a wallet-delta reconciliation cannot see
```

## Uncreditable deposits

Some funds arrive on-chain, at an address you control, and belong to no customer. Kraken publishes the policy
verbatim: "Cryptocurrency deposits sent via MWEB (including peg out) on Litecoin, PrivateSend on Dash,
CoinJoin, or other obfuscation tools will not be credited and may be lost," alongside "We do not accept
deposits of ZCash block rewards."

This is a **ledger state, not an exception handler**: an on-chain asset with no customer liability behind it.
Give it a house/unattributed account with a named owner and an age (the same treatment untagged Stellar
deposits get) and keep it off the asset side of any solvency assertion backing customer liabilities. The same
classification absorbs frozen assets, dust below the cost of sweeping, and inaccessible legacy wallets. If the
invariant counts them as reserves, the invariant is lying.

## Hot / warm / cold accounting and multisig operations

**An unmined send is not a settlement event.** The debit and the payout intent are written atomically at
request time; the broadcast writes nothing; the confirmation moves the clearing account back to zero.

```
t0  request accepted   DR liability:customer:42:BTC        0.50000000
                       CR clearing:withdrawals-in-flight    0.50000000
t1  broadcast          (no posting: a broadcast is not a settlement event)
t2  confirmed at depth DR clearing:withdrawals-in-flight    0.50000000
                       DR expense:network-fees:BTC          0.00022000
                       CR onchain:hot:BTC                   0.50022000
```

`clearing:withdrawals-in-flight` has an expected steady-state balance of zero, and a nonzero balance beyond
its settlement window is the alert. Internal moves (sweep, hot→cold, cold→hot, consolidation) are
value-neutral between on-chain asset accounts plus a fee expense, and **must not touch a customer liability
account at all**.

**The signing quorum is wallet state with a liveness cost.** BitGo's on-chain wallets are uniformly 2-of-3
(user, backup, BitGo) for both multisig and MPC/TSS, so two independent parties must be reachable for any
spend. OKEx suspended *all* withdrawals on 2020-10-16 because a single key holder was unreachable: an
unassemblable quorum is economically identical to lost keys for its duration. `[PARTIALLY VERIFIED: the
contemporaneous report was not retrievable in the research pass; treat the duration as unverified.]`

Signing sessions have their own lifetime and their own idempotency. BitGo's recovery contract turns an
ambiguous signing error into a decidable branch on a caller-supplied `sequenceId`: "If BitGo has not
registered the `sequenceId`, you can safely retry the original withdrawal. However, if BitGo has registered
the `sequenceId` and the `state` response field is `pendingDelivery`, then continue following the steps on
this page to rebuild, re-sign, and send the transaction request." Collapsing those branches into "retry the
withdrawal" duplicates the payout in exactly the `pendingDelivery` case; BitGo's own sample code treats more
than one request sharing a `sequenceId` as a bug to be reported.

Reconcile the wallet against **both** neighbours, and read the direction of the break:

| break | what it means | action |
|---|---|---|
| wallet says an output is unspent, chain says spent | a second signer, a restored snapshot, or a stale UTXO cache | stop signing on that wallet, resync from the chain, page |
| chain balance > ledger assets, at a deposit address | uncredited deposit, untagged deposit, or a returned transfer | classify into house/unattributed; never auto-credit |
| ledger assets > chain balance | phantom credit or a missed reorg reversal | halt withdrawals for the asset before investigating |
| `Σ spendable < Σ withdrawable customer balances` | unswept forwarders, empty gas tank, or frozen assets | throttle withdrawals; this is **not** implied by the solvency identity |

The asset side of every one of these must be computed from block data or an independent node: a reconciliation
that reads both sides from the same custodian API cannot detect a custodian-side bug, which is the only class
of bug it exists to find.
