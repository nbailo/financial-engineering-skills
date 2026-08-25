# Identity layered on top of chain identity

An off-chain signed order and a cross-chain message each carry an identity the chain does not enforce for you,
with their own replay boundary and their own cancellation semantics.

## Contents

- **EIP-712 signed orders**: the domain separator; per-protocol cancellation (Seaport counter, 0x salt
  floor, Permit2 vs EIP-2612 nonce spaces); validated orders outliving their signatures; expiry as a control.
- **Cross-chain message identity**: `(sourceChain, destChain, nonce)` consumed atomically with the effect;
  duplicate relayer delivery as the default case; source/destination reorg coupling.

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

**The credit comes from the value the protocol itself recorded as received, not from an event the caller
shaped.** Qubit's QBridge
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
