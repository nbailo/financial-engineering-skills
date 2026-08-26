# UTXO replacement: BIP-125, full-RBF, and CPFP

> **Provenance**
> provider: Bitcoin Core and BIP-125, with BitGo and Fireblocks for the custodial surfaces · surface: the replacement rules, the full-RBF default, RBF against CPFP as an identity decision, and the mempool topology limits that bound both
> version: BIP-125 as published; Bitcoin Core 28.0 for the `-mempoolfullrbf` default change; the policy constants as cited in the body, with no commit pinned.
> verified_at: not established
> sources: https://github.com/bitcoin/bips/blob/master/bip-0125.mediawiki · https://github.com/bitcoin/bitcoin/blob/master/src/policy/policy.h · https://github.com/bitcoin/bitcoin
> verified: none in this pass. No sentence below was re-read against a source for the 2026-08-25 review pass.
> unverified: all of it. This file predates the provenance requirement and was not re-checked in the 2026-08-25 review pass, so its claims carry the confidence of their original sourcing and no more, with no date you can check. The five rules are quoted from a BIP that does not change, but the policy that enforces them does, and the constants quoted from the Core source are the part most likely to have moved. The URLs above are where a recheck starts; each of them resolved on 2026-08-25, and nothing in any of them was read against a claim in this file.
> revalidate_when: Core changes a policy constant quoted here, the ancestor and descendant limits, the standard weight ceiling or the TRUC limits; the full-RBF default changes again; a newer policy document supersedes BIP-125; your custodian changes how it signals replaceability or accelerates a stuck transaction.

On a UTXO chain the identity of an intent is its input set, so a fee bump publishes a new txid over the same
coins and invalidates every ledger row keyed on the old one.

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

**The property is that a fee bump must not invalidate the identity your ledger is keyed on.** RBF satisfies it
where the ledger is keyed on the intent; where N ledger rows reference the parent txid, RBF invalidates all N
at once and CPFP is the bump that leaves them alone. Either way the batch's idempotency key is
per-recipient-payout, with the batch as a grouping over those keys, never a replacement for them.

Mempool topology caps how much a replacement or a sweep chain can do:
`DEFAULT_ANCESTOR_LIMIT{25}` / `DEFAULT_DESCENDANT_LIMIT{25}` (`src/policy/policy.h`; Fireblocks surfaces the
rejection as `TOO_LONG_MEMPOOL_CHAIN`, *"too many unconfirmed transactions pending from an address"*),
`MAX_STANDARD_TX_WEIGHT{400'000}` = 100 kvB, and for v3/TRUC transactions `TRUC_MAX_VSIZE{10'000}` with a
single child of at most `TRUC_CHILD_MAX_VSIZE{1000}`. Validate a batch against these **before signing** and
treat a relay rejection as "not sent, and provably not sent".
