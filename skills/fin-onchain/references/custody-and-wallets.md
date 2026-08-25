# The wallet's own state: custody, construction, and withdrawal orchestration

This reference covers the third state (the wallet's own view of which outputs are mine, which are spendable,
which nonce is next, which address index is next, which signing sessions are live) and the ways it diverges
from the chain and from the ledger. It carries UTXO transaction construction where a change output sent to a
fee is a total silent loss, derivation paths and gap limits, the sweep and gas-tank architecture that makes
`spendable` and `confirmed` different numbers on forwarder chains, memo and tag deposit routing, and
withdrawal batching with partial failure.

## Contents

- **The three states, named**: chain, wallet, ledger; what a divergence looks like and why nothing throws.
- **What "balance" means**: five Bitcoin Core buckets, `getreceivedbyaddress`, spendable vs confirmed.
- **UTXO construction**: `min_viable_change`, the implicit fee, dust by script type, the Paxos transaction.
- **Fee circuit breakers**: absolute caps and rate caps; Bitcoin Core's two, Fireblocks' three.
- **Signing safely**: BIP-174 change detection; the segwit v0 amount lie and `PSBT_IN_NON_WITNESS_UTXO`.
- **Derivation and discovery**: gap limit 20, history not balances, descriptor `range`, `purpose'`.
- **Address reuse as an accounting hazard**: `avoid_reuse`, the `used` bucket, why `(address, amount)` dies.
- **Sweeps and gas tanks**: forwarders, the FX-exposed sweep cost, three fee sources, the invisible fee.
- **Memo and tag deposit routing**: `DestinationTag`, Stellar memo types, tag assignment and collisions.
- **Chain reserves and minimums**: why "withdraw max" is wrong; the error codes; coinbase maturity.
- **Withdrawal orchestration**: gross vs net; the two batch architectures; per-recipient idempotency.
- **Asynchronous rejecting gates**: screening, freezes, authorisation expiry, returned VASP transfers.
- **Uncreditable deposits**: funds you hold that belong to no customer, and the house account they need.
- **Hot/warm/cold and multisig operations**: in-flight ledgering, quorum liveness, two-sided reconciliation.
- **The sequence allocator and the outbound queue**: the single-writer lock and its span; the two ways a
  lock key stops being a lock; the three preconditions a broadcast must clear before it is sent.

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

## What "balance" means

Bitcoin Core's `getbalances.mine` returns five distinct numbers (`src/wallet/rpc/coins.cpp`), and only one of
them may fund a withdrawal:

| bucket | Core's own definition | may fund a payout |
|---|---|---|
| `trusted` | "outputs created by the wallet or confirmed outputs" | yes |
| `untrusted_pending` | "outputs created by others that are in the mempool" | **no**: replaceable; since Core 28.0 `-mempoolfullrbf` defaults to 1, so `nSequence` no longer tells you whether an unconfirmed transaction can be replaced |
| `immature` | coinbase outputs below maturity | no |
| `nonmempool` | "sum of coins that are spent by transactions not in the mempool (usually an over-estimate…)" | no |
| `used` | present only with `avoid_reuse`; outputs on already-used addresses | policy decision, not a default |

`getreceivedbyaddress` is **not** in this list and is not a balance: it is "the total amount received by the
given address", a monotone lifetime counter that never decreases when the funds are spent. Reporting it as a
balance reports money you already moved.

```python
# attribution and availability are different queries and must never share a column name
received_lifetime = rpc.getreceivedbyaddress(addr, min_conf)   # who sent what: monotone, not a balance
spendable         = rpc.getbalances()["mine"]["trusted"]       # what a payout may draw on
```

On the account+forwarder model the same split appears at the custodian's API. BitGo, `docs/consolidations`:
"For account-based assets, the spendable balance of a wallet is the balance of the assets in the base address.
The spendable balance is the total amount available for withdrawal. The confirmed balance is the balance of
the base address plus all the receive addresses." Authorising a withdrawal against `confirmed` authorises
money that is sitting in forwarder contracts and has not been swept.

On memo-ID chains it is inverted: BitGo lists ~20 chains (XRP, Stellar, Cosmos, EOS, Hedera, TON, ICP, SEI,
Injective, …) where "all deposited assets are immediately available in the spendable balance": no forwarder,
no sweep, no gas tank, `spendable == confirmed`. Shipping "spendable ≠ confirmed" unconditionally is wrong on
exactly these chains; shipping the memo-ID assumption is wrong on all the others.

## UTXO construction

**A Bitcoin fee is defined as `Σ inputs − Σ outputs`; there is no fee field.** Every arithmetic error in
output construction is therefore paid to the miner, silently, in a completely valid transaction.

```cpp
// src/wallet/coinselection.cpp: SelectionResult::GetChange(), the load-bearing line
if (change < min_viable_change) { return 0; }   // caller emits NO change output; residue becomes fee
// src/wallet/spend.cpp ~L1179-1194: where that threshold comes from
m_change_fee      = effective_feerate.GetFee(change_output_size);
min_viable_change = std::max(change_spend_fee + 1, dust);
```

Correct at the 500-satoshi scale, ruinous at the 5-BTC scale, **through the identical code path**. The only
defence is asserting the constructed output set against the intended one by count and by `(script, amount)`,
so that a dropped change output is a refusal rather than a donation.

The dust floor inside that `max()` is a fee-rate function of the output's script type, not a constant
(`src/policy/policy.cpp`, `GetDustThreshold()`, `DUST_RELAY_TX_FEE{3000}` sat/kvB):

| output type | dust at the default `dustRelayFee` | Core's reasoning |
|---|---|---|
| P2PKH | **546 sat** | "If you'd pay more in fees than the value of the output to spend something, then we consider it dust" |
| P2WPKH | **294 sat** | "A typical spendable segwit P2WPKH txout is 31 bytes big": witness-discounted spend cost |
| P2TR | same formula; Core's comment notes it "was kept to not further reduce the dust level" | cheaper to spend than the formula assumes |

Hard-coding 546 over-rejects valid P2WPKH payouts and under-rejects when `dustRelayFee` is raised.

### Worked case: Paxos, block 807,057, 2023-09-10 (verified on-chain)

txid `d5392d474b4c436e1c9d1f4ff4be5f5f9bb0eb2e26b61d2781751474b7e870fd`, mined by F2Pool.

| | value (BTC) | note |
|---|---|---|
| input 0 | **19.89514072** | P2WPKH `bc1qr35hws365juz5rtlsjtvmulu97957kqvr3zpw3`, `nSequence 0xfffffffd` |
| output 0 | 0.06595313 | change, back to the input's own address |
| outputs 1–4 | 0.00256543 + 0.00207202 + 0.00193031 + 0.00153351 = **0.00810127** | four recipients |
| Σ outputs | 0.07405440 | |
| **fee** | **19.82108632** | 242.25 vB ⇒ ~8,182,079 sat/vB |

The change output is *present and correct in form*. `Σ outputs` is what the change would have been had the
selected input been worth ≈0.0741 BTC; the real UTXO was 268× larger. The builder held a wrong input value,
and because the fee is a residual the entire discrepancy became fee with no error raised anywhere. Paxos:
"Paxos overpaid the BTC network fee on Sept. 10, 2023. This only impacted Paxos' corporate operations."

The pre-signing assertion that refuses it (note that every input value is **re-fetched from the chain**, not
taken from the builder):

```python
prevouts = [chain.get_txout(i.txid, i.vout) for i in psbt.inputs]   # authority = chain, not builder
sum_in   = sum(p.value_sat for p in prevouts)
sum_out  = sum(o.value_sat for o in psbt.outputs)
fee      = sum_in - sum_out                       # the fee is a residual; compute it, never trust it

assert fee == expected_fee_sat,          (fee, expected_fee_sat)
assert fee <= MAX_ABSOLUTE_FEE_SAT,      fee          # denominated in the asset, not in sat/vB
assert len(psbt.outputs) == len(intended_outputs)     # catches GetChange() == 0 dropping the change
assert {(o.script, o.value_sat) for o in psbt.outputs} == intended_output_set
```

## Fee circuit breakers

| control | where | shape | default |
|---|---|---|---|
| `DEFAULT_TRANSACTION_MAXFEE` | `src/wallet/wallet.h` (`-maxtxfee`) | **absolute**, per transaction | `COIN / 10` = 0.1 BTC |
| `DEFAULT_MAX_RAW_TX_FEE_RATE` | `src/node/transaction.h`, used by `sendrawtransaction` / `testmempoolaccept` (`maxfeerate`) | **rate** | `COIN / 10` per kvB |
| `HIGH_TX_FEE_PER_KB` | `src/wallet/wallet.h` | warning threshold | `COIN / 100` per kB |
| `failOnLowFee`, `maxFee`, `maxTotalFee` | Fireblocks `POST /transactions` | request-level caps | caller-supplied |

Both Core checks would have refused the Paxos transaction (198× the absolute cap; ~8.18M sat/vB against a
10,000 sat/vB rate cap). **The absolute cap is still the one you cannot omit**: a legitimate 400-input
consolidation at a legitimate rate stays under any sane rate ceiling while paying an absolute fee no operator
would authorise, and a rate ceiling cannot bound the loss when the *input amount* is wrong. Set the absolute
ceiling from your own worst-case payout size; there is no correct default here.

## Signing safely

**The change output is the most dangerous output in the transaction.** BIP-174's "Change Detection" prescribes
that the *signer* re-derives it: take the BIP-32 path claimed in `PSBT_OUT_BIP32_DERIVATION`, derive the key
from a **globally supplied xpub** the signer already trusts, and require the result to equal the key in the
output script. For multi-key outputs, reconstruct the entire script policy from the inputs being signed and
require an exact match. An `isChange` boolean from the builder protects against nothing; it is the builder
asserting its own correctness to the one component whose job is to doubt it.

**Segwit v0's sighash lets a builder lie about other inputs' amounts, and the lie converts directly into
fee.** BIP-174, verbatim: "The sighash algorithm for Segwit specified in BIP 143 is known to have an issue
where an attacker could trick a user to sending Bitcoin to fees if they are able to convince the user to sign
a malicious transaction multiple times. This is possible because the amounts in `PSBT_IN_WITNESS_UTXO` of
other segwit inputs can be modified without effecting the signature for a particular input." The defence is
stated in the same BIP as ordinary practice: require the full previous transaction in
`PSBT_IN_NON_WITNESS_UTXO`, hash it, check the txid matches the outpoint, and read the amount from there.

```
for each input:
    require psbt.input[i].non_witness_utxo is not None
    assert sha256d(non_witness_utxo) == input[i].prevout.txid       # binds the amount to a real txid
    amount[i] = non_witness_utxo.vout[input[i].prevout.n].value
for each output flagged as change:
    assert derive(global_xpub[fingerprint], claimed_path) == pubkey_in(output.script)
    assert script_policy(output) == script_policy_reconstructed_from(inputs_being_signed)
```

## Derivation and discovery

BIP-44: "Address gap limit is currently set to 20. If the software hits 20 unused addresses in a row, it
expects there are no used addresses beyond this point and stops searching the address chain. **We scan just
the external chains**, because internal chains receive only coins that come from the associated external
chains." And: "the algorithm works with the **transaction history**, not account balances."

Two symmetric bugs, on the two sides of that rule:

| side | bug | consequence |
|---|---|---|
| issuance | handing out more than 20 consecutive **unused** external addresses | a restored or third-party wallet stops scanning at the gap; every deposit beyond it is invisible to discovery |
| scanning | walking **balances** instead of transaction history | discovery truncates at the first address that received and was fully spent |

The issuance-side monitor, alarming well before 20 (`used` must be derived from history, not balance):

```sql
-- consecutive unused EXTERNAL (change = 0) addresses at the tip of the issued range.
-- alarm at >= 15; refuse to issue at >= 20.
SELECT count(*) AS consecutive_unused FROM deposit_addresses
WHERE  wallet_id = $1 AND change = 0
  AND  address_index > coalesce((SELECT max(address_index) FROM deposit_addresses
                                 WHERE wallet_id = $1 AND change = 0 AND ever_seen_in_a_tx), -1);
```

Bitcoin Core's descriptor path has the same failure with a different number: `importdescriptors` called
without `range` logs "Range not given, using default keypool range" and imports only
`[0, DEFAULT_KEYPOOL_SIZE)` = **indices 0–999** (`src/wallet/rpc/backup.cpp`, `scriptpubkeyman.h`). A
watch-only descriptor silently stops observing at index 999.

`purpose'` selects an entirely different address set from the same seed: `44'` P2PKH, `49'` P2WPKH nested in
P2SH, `84'` native P2WPKH (`bc1q…`), over an otherwise identical
`m / purpose' / coin_type' / account' / change / address_index`. A restore at the wrong purpose reports a zero
balance *correctly*, and reconciliation declares a loss that does not exist. Store the full path including
`purpose'` with every issued address and re-derive it at issuance; the dangerous "fix" is re-issuing deposit
addresses that no longer match the ones customers already hold.

## Address reuse as an accounting hazard

Core exposes `avoid_reuse` (adding the `used` balance bucket) and `-avoidpartialspends`: "Group outputs by
address, selecting many (possibly all) or none, instead of selecting on a per-output basis." The accounting
consequence is independent of the privacy one: **on a reused deposit address, `(address, amount)` stops
identifying a deposit**; two customers paying 0.02 BTC to the same address are indistinguishable without the
outpoint. Key deposits on `(txid, vout)`, or on EVM `(chainId, blockHash, txHash, logIndex)`; never on an
address-plus-amount pair, and never issue one address to two customers.

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

## Memo and tag deposit routing

| | XRP Ledger | Stellar |
|---|---|---|
| field | `DestinationTag` | `Memo` |
| type | **32-bit unsigned integer** | `MEMO_TEXT` ≤ **28 bytes** · `MEMO_ID` **uint64** · `MEMO_HASH` **32 bytes** · `MEMO_RETURN` 32-byte hash of the refunded transaction |
| enforcement | **protocol-side.** `asfRequireDest` (`lsfRequireDestTag`) makes the ledger reject an untagged Payment | **sender-side only.** SEP-29 `config.memo_required = "1"` is a `MANAGE_DATA` entry checked by the *sending* SDK; the network accepts memo-less payments regardless |
| failure code | `tecDST_TAG_NEEDED` (143): "The Payment transaction omitted a destination tag, but the destination account has the `lsfRequireDestTag` flag enabled" | none; the payment succeeds and is unattributable |
| removes the class entirely | **X-address** (tag folded into the address) | **muxed account** `M…` (CAP-27); Stellar's own docs now point at these for pooled-account differentiation |

An XRPL integration can push the problem into consensus and should. A Stellar integration **must** ship an
operational path for untagged deposits (an unattributed-deposit liability account with a named owner and an
age, plus an identification workflow) because SEP-29 cannot prevent them.

XRPL on assignment: "Assigning tags in numerical order provides less privacy to customers. Since all XRP
Ledger transactions are public, assigning tags in this way can make it possible to guess which tags correspond
to various users' addresses," and "To be safe, check for collisions with old tags before using a new tag."
**A tag collision is a mis-credit, a silent transfer of value between two real customers.**

```python
TAG_LO, TAG_HI = 1_000_000, 4_294_967_295   # DestinationTag is uint32; below TAG_LO reserved for house use
def assign_tag(customer_id: int) -> int:
    for salt in range(64):                  # a collision is recoverable, not an exception
        h   = blake2b(f"{customer_id}:{salt}".encode(), key=TAG_SECRET, digest_size=8).digest()
        tag = TAG_LO + int.from_bytes(h, "big") % (TAG_HI - TAG_LO)
        if db.insert_if_absent("destination_tags", tag=tag, customer_id=customer_id):   # UNIQUE(tag)
            return tag
    raise TagSpaceExhausted(customer_id)    # never fall through to a sequential counter
```

The `UNIQUE` constraint is the collision check; the salt loop is what makes a collision recoverable instead of
an exception. A Stellar `MEMO_ID` is uint64 and takes the same construction over a wider range.

## Chain reserves and minimums

"Withdraw max" is `spendable − reserve − (fee if the withdrawal is net)`; the reserve is per-chain
configuration. Omitting it fails the withdrawal; a wrong one strands funds or reaps the account.

| rule | chains | surfaced as |
|---|---|---|
| destination account must be created with a minimum amount | Fireblocks: "Only the Polkadot, Kusama, and Westend (testnet) blockchains have this limitation" | `NEED_MORE_TO_CREATE_DESTINATION` |
| a minimum must remain in the source wallet at all times | same family | `INSUFFICIENT_RESERVED_FUNDING`: "Resubmit the transaction with an amount that leaves at least one DOT" |
| destination does not exist and the payment is below the account reserve | XRP Ledger | `tecNO_DST_INSUF_XRP` |
| coinbase outputs unspendable for **100 confirmations** | Bitcoin | `SPEND_COINBASE_TOO_EARLY`; Core's separate `immature` balance bucket |

## Withdrawal orchestration

**Gross versus net is an explicit statement, never an inference.** Fireblocks `treatAsGrossAmount` defaults to
`false`: the recipient receives `amount` and the sender pays the fee on top. With `true` the fee comes out of
`amount`, and the degenerate case is `AMOUNT_TOO_SMALL`: "the transaction fee is higher than the net transfer
amount on a gross transaction". Inverting the flag produces a per-withdrawal shortfall or overpayment that is
invisible per transaction and unbounded in aggregate: store the choice on the payout row and assert the
recipient's observed delta against it in reconciliation.

The two batching architectures have opposite failure modes, and one rule survives both:

| architecture | atomicity | the failure | what it breaks |
|---|---|---|---|
| **one transaction, many outputs** (UTXO batch, EVM multicall) | atomic on-chain: all recipients paid or none | one txid covers N ledger rows; an RBF replacement or a drop invalidates all N at once | if each row independently times out and re-issues, N double payouts |
| **one request, many transactions** (Fireblocks aggregated) | **not** atomic. Fireblocks `PARTIALLY_FAILED`: "One or more aggregated transactions submitted as a single operation have failed… aggregated transactions are submitted to the blockchain network individually and can be partially or fully completed or fail" | some recipients paid, some not | retrying the *request* re-pays everyone who succeeded |

**The idempotency key is per-recipient-payout, and the batch is a grouping over those keys, never a
replacement for them.**

```sql
CREATE TABLE payouts (
  payout_id       uuid PRIMARY KEY,
  customer_id     bigint        NOT NULL,
  chain           text          NOT NULL,
  asset           text          NOT NULL,
  amount_minor    numeric(38,0) NOT NULL,          -- integer minor units; never a float column
  treat_as_gross  boolean       NOT NULL,          -- stated, not inferred
  destination     text          NOT NULL,
  memo            text,                            -- NULL only where the chain has no tag concept at all
  external_tx_id  text          NOT NULL UNIQUE,   -- custodian-side key, one per PAYOUT, never per batch
  batch_id        uuid          REFERENCES payout_batches(batch_id),   -- grouping only
  txid            text, vout integer,              -- txid may change (RBF); it is never the identity
  CONSTRAINT one_leg_per_payout UNIQUE (batch_id, customer_id, destination, amount_minor)
);
```

Reconcile each leg independently against `(txid, vout)` or the leg's own `externalTxId`, and retry only unpaid
legs under their own keys.

## Asynchronous rejecting gates

Between "customer requested" and "broadcast" sit gates that reject asynchronously, hours later, for reasons
that have nothing to do with the chain. Each one needs a reversal path bound to it.

| status / sub-status | meaning | required ledger action |
|---|---|---|
| `PENDING_AML_SCREENING` | screening runs before signing; the debit is already taken | the clearing account holds it; alert on age |
| `REJECTED_AML_SCREENING` | terminal | reverse the customer debit |
| `AUTO_FREEZE` / `FROZEN_MANUALLY` | "Any associated assets will not be available until an Admin-level user unfreezes them" | asset is not spendable; exclude from the withdrawable-reserve assertion |
| `PENDING_AUTHORIZATION` | "If no action is taken for two hours, the transaction will fail" | **a withdrawal can fail for a calendar reason, not a chain reason**; expiry is a first-class outcome with its own reversal |
| `QUEUED` behind `PENDING_SIGNATURE` | one unsigned request head-of-lines every later payout on the account | alarm on queue-head age, not on individual payout age |
| `BLOCKED_BY_POLICY` | carries the policy rule number, matched by "the first-match principle" | log the rule id with the payout; inserting a rule silently changes the outcome of existing paths |
| `COMPLETED_BUT_3RD_PARTY_REJECTED` | "completed on the blockchain as shown on block explorers but rejected by the associated third-party platform" | you paid and were not credited: book an **open receivable**, not a settled transfer |

The screening clock and the transaction clock run independently (Fireblocks: "Under certain circumstances, a
transaction's screening status can appear as Pending even when its transaction status shows as Completed"), so
neither may be used to infer the other.

**A returned travel-rule transfer is not a deposit.** When a counterparty VASP rejects and returns funds, the
inbound arrives at an address you control and looks exactly like a customer deposit; crediting it leaves the
ledger holding both a debit for the original withdrawal and an unrelated credit for the same money. Match
returned transfers against the originating payout (amount, asset, counterparty address,
`travelRuleMessageId`) before any crediting rule runs.

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

## The sequence allocator and the outbound queue

Where the external ledger orders your instructions by a number you assign (an EVM nonce, an XRPL `Sequence`),
that number is authoritative state with exactly one writer. The lock must span **allocation through to the
durable record of what was sent**, because everything between those two points is the window in which a second
writer allocates the same number.

```python
# WRONG, in two independent ways
with engine.begin() as cx:                              # (2) the span ends at the dedent
    cx.execute("SELECT pg_advisory_xact_lock(:k)", {"k": hash(chain) & 0x7FFFFFFF})   # (1) the key
    nonce = cx.execute("SELECT next_nonce FROM wallet WHERE chain = :c FOR UPDATE", ...).scalar()
signed = sign(build_tx(nonce))                          # outside the lock
broadcast(signed)                                       # outside the lock
```

**(1) The key is not a lock.** Python's `hash()` is salted per interpreter for `str`, so `hash('ethereum')`
differs in every process while `hash(1) == 1` everywhere; each replica therefore takes a *different* advisory
lock and all of them succeed at once. Derive the key from a stable digest of the UTF-8 key bytes, never from
the language's own hash of a string.

**(2) The span ends before the effect.** `with engine.begin():` commits and releases at the dedent, so
`SELECT … FOR UPDATE` is released before the sign and the broadcast it exists to protect. Hold the transaction
across the broadcast, and write the record of what was sent inside it.

Fireblocks solves the same problem by serialising and documents the cost: it "can only process a single
transaction per blockchain standard per vault account". The throughput ceiling is therefore one in-flight
transaction per account, and **sharding across accounts must happen before nonces are assigned, never after**:
a shard chosen after allocation splits one number space across two writers, which is the original bug with
extra steps.

### Three preconditions, checked before every broadcast

A queue that keeps submitting into a condition that guarantees failure converts one stuck instruction into a
silent outage. Each precondition halts the queue and pages; none of them may be a warning.

| precondition | the assertion | what it prevents |
|---|---|---|
| **ordering continuity** | the pending-minus-latest gap is zero, or has been non-zero for less than a configured interval | queued transactions behind a gap are capped at geth's `AccountQueue` (64) and evicted after `Lifetime` (3h). One stuck low-fee transaction at nonce N blocks every withdrawal behind it and then silently drops them |
| **fee-paying reserve** | the broadcasting account's native-gas balance covers a configured multiple of the current worst-case fee for the queued depth | a hot wallet out of gas stops all withdrawals with no error anyone reads |
| **absolute fee ceiling** | every constructed transaction carries a cap denominated in the asset, not only a fee-rate cap | a rate cap cannot bound the loss when the *input amount* is wrong; this is the Paxos shape above |

The remediation for a nonce gap is to **replace the lowest unmined nonce, not to submit more transactions**;
the detector, the geth pool defaults behind it and BitGo's `fillNonce` equivalent are in
[transaction-identity.md](transaction-identity.md). The interval and the gas multiple are configuration with
no default. The absolute ceiling comes from your own worst-case payout size; Bitcoin Core's
`DEFAULT_TRANSACTION_MAXFEE` and `DEFAULT_MAX_RAW_TX_FEE_RATE` in the fee-circuit-breaker table above show the
shape of shipping both, and either one would have refused the Paxos transaction.
