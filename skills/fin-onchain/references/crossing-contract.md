# The crossing contract: the evidence a deposit or withdrawal path owes before it ships

This reference carries the fuller output block for a review or a ship decision on a chain crossing, and the
per-model table that turns an observable in the repo into the rule for that chain. Emit it when you hold the
signing keys, when exposure is `customer` on a crediting or broadcast path, or when the change spans more than
one of identity, coverage, finality and amount. Below that bar the default finding entries in `SKILL.md` are
the whole output; a contract emitted over a one-line rounding change is noise that trains the reader to skip
it.

Every slot in the block carries a real `file:line` or the line does not appear. A slot the change does not
touch is not a finding and is not emitted. A slot you emit and cannot fill **is** the finding, and it is
reported as `UNRESOLVED: <control> (<why>)`.

## Contents

- **The per-model table**: the observable in the repo, the model it implies, and what changes about the rule.
- **The routing field**: chains where the address is not the whole destination, and the supported-pair table
  that grows into a loss.
- **The block**: the header line, the nine deposit slots, the four withdrawal slots.
- **What fills each deposit slot**: what "implemented" looks like, and the shape that fails the slot.
- **What fills each withdrawal slot**.
- **The seam into `fin-ledger`**: identity, staging, unwind, assertion.

## The per-model table

Account-nonce, UTXO and memo-tagged ledgers are three different correctness problems, not three spellings of
one. Key on what is observable in the repository, never on a general prior about "crypto deposits".

| Observable | Model | What changes |
|---|---|---|
| per-customer address is a forwarder / `CREATE2` proxy contract, or a sweep job exists | account + forwarder | **spendable = base address only; confirmed = base + every receive address.** A withdrawal authorised on `confirmed` cannot be funded. The sweep is paid in native gas the deposit address does not hold. |
| one shared address per asset plus a `memo` / `destination_tag` / `tag` column | memo-ID | BitGo documents deposits on these chains as **immediately available in the spendable balance**, with no sweep, no gas tank, no forwarder. `spendable == confirmed`. The failure moves entirely to untagged and mis-tagged deposits. |
| `UTXO`, `outpoint`, `vout`, `PSBT`, coin selection, `changeAddress` | UTXO | Identity is the **input set**, not the txid. Change below `min_viable_change` is **paid to the miner**, a total silent loss with no error. |

"Spendable is not confirmed" is true on forwarders and **false** on memo-ID chains: shipped unconditionally, it
is wrong half the time. The mechanics of each model live in
[custody-and-wallets.md](custody-and-wallets.md); the identity rules live in
[transaction-identity.md](transaction-identity.md).

## The routing field

On some ledgers the routing information that identifies the beneficiary lives beside the address, not inside
it. Where that is true, an instruction with a well-formed address and no routing field delivers funds that no
one can attribute, and the ledger reports success.

```
destination = address + (routing field, where the model requires one)
schema column -> API field -> per-chain validation before broadcast
supported (asset, ledger) set is gated on the routing field existing end to end
```

The failure path is a supported-pair table that grows. A service ships with

```python
SUPPORTED = {(USDT, ethereum), (USDT, tron)}
```

neither of which needs a tag, so no `memo` column is ever added. A later change adds a tag-addressed chain to
the same set. Nothing rejects, because nothing in the path knows a routing field exists, and the funds arrive
un-creditable at a shared address. Gate the set itself: a pair may not enter `SUPPORTED` unless the column,
the API field and the per-chain validator all exist for it.

The two protocol instances, with their enforcement asymmetry, are in
[custody-and-wallets.md](custody-and-wallets.md): XRPL's `DestinationTag` is a 32-bit unsigned integer the
**ledger itself** can require (`asfRequireDest`, rejecting with `tecDST_TAG_NEEDED`), while Stellar's typed
`Memo` and SEP-29 `memo_required` are enforced by the **sending** SDK only, so a Stellar integration needs an
operational path for untagged deposits. Publishing an X-address or an `M…` muxed account folds the tag into
the address and removes the class.

## The block

```
CHAIN CROSSING: <chain> · <account+forwarder | memo-id | utxo>

DEPOSIT   (emit only when the change touches a crediting path)
  dedupe key           ledger + branch + transaction + position, one unique constraint   file:line
  range completeness   how a short, capped or failed range is proven covered             file:line
  cursor guard         same predicate as the query, advanced only over a proven range    file:line
  reorg detector       the parent link chained against the stored block identity         file:line
  unwind               reversing entry keyed on the orphaned identity                    file:line
  amount source        measured delta or delivery metadata, not the event field          file:line
  self-transfer guard  originator not in the set of addresses we control                 file:line
  finality gate        depth, the loss budget it buys, and the unit it counts in         file:line
  routing field        column + API field + per-chain validation, or "model: n/a"        file:line

WITHDRAWAL   (emit only when the change touches a broadcast path)
  intent identity      the chain model's identity, minted and committed before the send  file:line
  broadcast handle set every handle emitted for the intent, and a confirmer reading all  file:line
  allocator lock       key derivation, and the span the lock holds                       file:line
  queue preconditions  ordering continuity, fee-paying reserve, absolute fee ceiling     file:line
```

## What fills each deposit slot

**dedupe key.** All four parts under one constraint: chain, block hash, transaction hash, position. Two parts
is above the bad bar and still wrong in both directions, and the four-part key only avoids the double credit
once the unwind has landed, which is why the credit path also asserts that no unreversed twin exists for the
transaction-level identity. Fails the slot: a unique constraint that omits the branch, or an
`ON CONFLICT DO NOTHING` whose result is never read. See [indexing.md](indexing.md).

**range completeness.** A classifier that separates covered from truncated, rejected and failed, with the
provider cap read from configuration keyed on provider, tier and chain. Fails the slot: a hardcoded cap, a
recursion whose base case returns an empty list, or an error branch that logs and continues.

**cursor guard.** The advance sits inside the same conditional and the same transaction as the work, and is a
compare-and-set rather than a blind write. Fails the slot: a guard that wraps the query while the save runs
unconditionally, or an address set snapshotted once outside a long drain.

**reorg detector.** The parent link of each processed unit asserted against the stored identity of the
previous one. Fails the slot: reliance on a removal flag from the source, a schema with no stored block hash,
or a head regression treated as a rollback. See [finality-and-reorgs.md](finality-and-reorgs.md).

**unwind.** A reversing balancing entry keyed on the orphaned identity, bounded by a retention floor, with a
halt above the floor. Fails the slot: a delete, an in-place edit, a bare debit, or a non-negativity constraint
that aborts the reversing write.

**amount source.** A measured balance delta, or the protocol's own delivery-metadata field, with the scale
read from the authority at runtime and cached per chain and address. Fails the slot: crediting the announced
amount for a token whose behaviour is not on an explicit record, or a hardcoded scale. See
[token-semantics.md](token-semantics.md).

**self-transfer guard.** The originator checked against the live set of addresses you control, queried inside
the credit transaction. Fails the slot: an originator column present and never read, or an address set loaded
into process memory at boot.

**finality gate.** A depth derived from a stated loss budget per chain and per amount, persisted with the
credit alongside the unit it counts in, and an alarm when the finality source stops advancing. Fails the slot:
a single constant across a chain list, a rollup credited on its own block count, or an absolute value taken
over a confirmation count that can go negative. See [finality-and-reorgs.md](finality-and-reorgs.md).

**routing field.** The column, the API field and the per-chain validator, or an explicit `model: n/a` for a
chain whose address is the whole destination. Fails the slot: a supported-pair table that admits a
tag-addressed chain with no column behind it.

## What fills each withdrawal slot

**intent identity.** Minted from the intent instance and committed before the first broadcast, using the
identity the chain model makes mutually exclusive. Fails the slot: an identity derived from a returned handle,
or an intent row flushed but not committed before the send.

**broadcast handle set.** Every handle ever emitted for the intent, stored as a set, with a confirmer that
reads the whole set and stops on the case where the sequence number is consumed by a handle that is not yours.
Fails the slot: a single current-handle column, or a superseded-handle column that is written and never read.

**allocator lock.** One lock spanning allocation, signing, broadcast and the durable record, keyed on stable
bytes identical in every replica. Fails the slot: a lock released before the broadcast, or a key derived from
a per-process value. See [custody-and-wallets.md](custody-and-wallets.md).

**queue preconditions.** Ordering continuity, a fee-paying reserve sized against the queued depth, and an
absolute fee ceiling denominated in the asset, each checked before every broadcast, each halting and paging on
failure. Fails the slot: a fee-rate ceiling with no absolute companion, or a submitter that keeps enqueuing
behind a blocked position.

**Inclusion is not one of the slots, because it is a property of the confirmer rather than a control you add.**
Check it anyway while filling the handle-set slot: a mined-but-reverted transaction consumes the sequence
number, burns the fee and emits no logs, so a success status on the receipt is required before any effect is
treated as having occurred, and the absence of an event is not proof that nothing moved. The classes of value
movement that emit no event at all, and the endpoints that expose them, are in [indexing.md](indexing.md).

## The seam into `fin-ledger`

This file owns the on-chain half of the boundary. `fin-ledger` owns what the books record; load it too when
the credit becomes a posting.

**Identity.** A deposit credit is exactly one balanced ledger transaction whose idempotency key is the
four-part log identity, never the transaction hash alone and never an increment of a balance column; the same
event re-observed after a reconnect, a backfill overlap or a provider failover is a no-op.

**Staging.** Observation posts to a per-user PENDING (unavailable) account; the credit policy's finality moves
it to AVAILABLE, which alone authorises withdrawal and onward transfer.

**Unwind.** A parent-link mismatch produces a reversing balancing entry keyed on the orphaned identity; a
reorg deeper than the indexer's rollback floor is an unrecoverable-state halt.

**Assertion.** A continuous reconciliation asserts that the value credited at or below the finalized height
equals the value observed on-chain arriving at addresses you control. Name the authority and the join key, and
ship it as a scheduled entrypoint with a fail-closed delivery path for a break: a reconciliation that runs in
production, or it does not exist.
