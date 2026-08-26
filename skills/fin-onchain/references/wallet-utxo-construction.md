# UTXO transaction construction, fees, and safe signing

> **Provenance**
> provider: Bitcoin Core and BIP-174, with Fireblocks for the request-level fee caps · surface: what `getbalances` means, coin selection and the change threshold, dust by script type, fee circuit breakers, and the checks a signer owes a builder
> version: the Core source constants and RPC definitions as cited in the body, with no commit pinned; BIP-174 as published.
> verified_at: not established
> sources: https://github.com/bitcoin/bips/blob/master/bip-0174.mediawiki · https://github.com/bitcoin/bitcoin/blob/master/src/wallet/spend.cpp · https://github.com/bitcoin/bitcoin/blob/master/src/wallet/coinselection.cpp · https://github.com/bitcoin/bitcoin/blob/master/src/policy/policy.cpp
> verified: none in this pass. No sentence below was re-read against a source for the 2026-08-25 review pass.
> unverified: all of it. This file predates the provenance requirement and was not re-checked in the 2026-08-25 review pass, so its claims carry the confidence of their original sourcing and no more, with no date you can check. That includes the worked case, which the body labels as verified on chain: no explorer and no node was queried for the 2026-08-25 review pass, and the quoted statement from the payment company was not reopened. The dust figures and the default fee caps are source constants with no pinned commit, which is the other place a silent drift would hide. The URLs above are where a recheck starts; each of them resolved on 2026-08-25, and nothing in any of them was read against a claim in this file.
> revalidate_when: Core changes the wallet fee ceiling, the raw transaction fee-rate ceiling, the dust relay fee or the change threshold computation; `getbalances` gains or renames a bucket; BIP-174 changes its change-detection guidance or what a signer must require for segwit amounts.

A Bitcoin fee is the residue of inputs minus outputs, so every arithmetic error in output construction is paid
to the miner in a completely valid transaction. The signer's job is to doubt the builder.

## Contents

- **What "balance" means**: five Bitcoin Core buckets, `getreceivedbyaddress`, and the one that may fund a
  payout.
- **UTXO construction**: `min_viable_change`, the implicit fee, dust by script type, the Paxos transaction.
- **Fee circuit breakers**: absolute caps and rate caps; Bitcoin Core's two, Fireblocks' three.
- **Signing safely**: BIP-174 change detection; the segwit v0 amount lie and `PSBT_IN_NON_WITNESS_UTXO`.

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
