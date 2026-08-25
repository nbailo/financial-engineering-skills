# Address derivation, discovery, and deposit routing

A deposit address is issued once and watched forever. The rules deciding which addresses exist, which a scan
can still find, and what carries the beneficiary where the address does not, are accounting rules.

## Contents

- **Derivation and discovery**: gap limit 20, history not balances, descriptor `range`, `purpose'`.
- **Address reuse as an accounting hazard**: `avoid_reuse`, the `used` bucket, why `(address, amount)` dies.
- **Memo and tag deposit routing**: `DestinationTag`, Stellar memo types, tag assignment and collisions.

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
