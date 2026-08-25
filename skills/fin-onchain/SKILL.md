---
name: fin-onchain
description: >-
  Financial correctness for on-chain deposits, withdrawals and value-moving transactions:
  crediting, finality and reorgs, transaction identity, nonces, custody and wallet state, token
  amount semantics, and chain-to-ledger reconciliation. Use when building or reviewing code that
  reads chain state or moves value on-chain, including ethers, viem, web3.py, Solana, Bitcoin,
  XRPL, Fireblocks and BitGo.
license: MIT
---

# The chain crossing

Your code holds one side of a boundary with a ledger you do not operate. Its recent history can be rewritten,
its notifications arrive out of order and incomplete, and its amounts are decided by code you did not write.
This skill answers whether an observation of that ledger can become the wrong obligation, or an obligation the
wrong instruction, while the node answers, the provider returns 200 and the receipt says success.

## When to use

Your code turns observations of someone else's ledger into obligations you owe, or turns an obligation you owe
into an instruction that ledger executes. The external history is provisional when you read it, the
instruction is ambiguous once you send it, and the amount that arrives is decided by the asset's own code
rather than by the message announcing it.

Routing hints, not the definition of the domain: an import of `ethers`, `viem`, `wagmi`, `web3.py`,
`@solana/web3.js`, `solders`, `bitcoinjs-lib`, `xrpl`, `stellar-sdk`, or a BitGo / Fireblocks / Alchemy /
Infura client; a call to `eth_getLogs`, `eth_sendRawTransaction`, `eth_getTransactionReceipt`,
`getSignatureStatuses`, `listsinceblock`; a column named `tx_hash`, `block_hash`, `log_index`, `nonce`,
`confirmations`, `from_address`, `destination_tag`, `memo`, `derivation_path`, `outpoint`, `vout`; a cursor or
watermark over block heights; a sweep, gas-tank, coin-selection, address-derivation or withdrawal-batching
path.

## When not to

A REST or WebSocket client of a centralized venue goes to `fin-exchange-integration`, a fiat rail to
`fin-payments`, and the posting, the balance and the solvency assertion to `fin-ledger`, which you load
alongside this skill once the credit becomes a posting. Contract-internal vulnerability classes (reentrancy,
access control, key management, upgrade authority) are out of scope for this entire suite; the neighbour there
is Trail of Bits' `building-secure-contracts`.

## Workflow

1. Name the crossing: what value enters or leaves your control, in which direction, and in whose favour.
2. Name the chain model from what the repo shows (account-nonce, UTXO, memo-tagged) and read the identity rule
   off it, because the model decides the rule and the three models disagree.
3. On an inbound path, separate observed, final and spendable, and check which of the three authorises each
   outbound effect.
4. On an outbound path, mint the identity from the intent, commit it, broadcast, then record every handle the
   network returns for it.
5. For every read of external history, establish that the range and the branch are covered before anything is
   credited, and advance the cursor only inside that proof.
6. Load only the references whose mechanism appears in this change.
7. Implement each control and its test. Anything you will not implement is reported as UNRESOLVED, or the path
   is made uncallable.
8. Name the external authority and the join key for the chain-to-ledger reconciliation, and ship it as a
   scheduled entrypoint with a fail-closed break path.

## Invariants

**Observed, final and spendable are three quantities, and only the third authorises an outbound effect.**
Value you have seen is not value that will survive, and value that will survive is not value the holder may
move. Observation posts to a per-user unavailable account, the finality policy moves it to available, and only
available funds a withdrawal. Collapsing the three means a rewrite of external history debits a balance the
holder already spent; the gap is the only place that loss can be absorbed. Specialises *authority*.

**The chain model decides the identity rule, and flattening it is wrong half the time.**
Account-nonce, UTXO and memo-tagged ledgers are three correctness problems, not three spellings of one. Key on
what the repo shows: a per-customer forwarder or proxy contract, a shared address plus a `memo` or
`destination_tag` column, or coin selection over outpoints. "Spendable is not confirmed" is true on forwarders
and false on memo-ID chains. Specialises *operation identity*.

**A broadcast is an ambiguous external effect, and the handle the network returns is not the identity.**
Mint the identity from the intent instance, commit it, broadcast, then append every handle the network emits
for it, and let the confirmer scan the whole set. Treating the returned handle as the identity makes a
repriced or rebuilt resubmission look like a different transaction: the confirmer sees the original never
confirm, marks the withdrawal failed, and re-credits a holder whose funds already left. Specialises *ambiguous
outcomes*.

**The instrument that spends holds a third state that neither the chain nor your books reconcile.**
Beyond the external ledger and your own books there is the signer's own view: which outputs it believes are
its own, which are committed, which nonce or sequence number and address index are next, which signing
sessions are live. Divergence here raises nothing. Reading the chain and writing the ledger without advancing
the wallet is how a nonce is reused; trusting the builder's own change flag instead of re-deriving the output
from the chain is how change becomes a fee.

**A history that can be rewritten does not reliably tell you it was rewritten.**
Store the parent link with every processed unit and assert it against what you already stored before
processing the next one. A removal signal from the source is an optimisation, never the detector: it is
emitted only by the node that processed the rewrite while you were connected. An indexer with no stored block
hash cannot detect a reorg deeper than its confirmation lag, ever.

**The dedupe key separates the same event twice from the same event on a different branch.**
The uniqueness key includes the branch of history the event was observed on, and the crediting path asserts
that no unreversed twin exists for the transaction-level identity before it acts. Without the branch,
re-crediting after a reorg is impossible in one direction and a double credit in the other. Specialises
*durable dedupe*.

**An unwind is a reversing entry keyed on what it reverses, never an erasure.**
Reverse a booked effect with a balancing entry keyed on the orphaned identity: never a delete, never an
in-place edit, never a bare debit of the holder's balance. A non-negativity constraint that aborts the
reversing write wedges the indexer in a permanent retry loop. Below your own retention floor there is nothing
to reverse against, and the correct behaviour is an unrecoverable-state halt, not a rollback.

**Credit the delta you measured at the asset's authority, not the number in the notification.**
The amount in an event is a claim made by the code that emitted it. Read the balance the asset's own authority
reports before and after, or the protocol's own delivery-metadata field, and credit the difference. Read the
scale from the authority at runtime, cached per ledger and asset; a hardcoded scale is a silent power-of-ten
error in a payout. Specialises *exact representation*.

**Value arriving from an address you control is not income.**
Assert the originator is outside your own perimeter before any credit. Your own movements are
indistinguishable from a customer's at the protocol level, and the gas top-up that funds a sweep lands on a
customer's deposit address with a real amount. A credited internal movement mints a liability with no matching
debit, and surfaces days later in a solvency reconciliation, if one exists.

**A range of external history is covered only when you can prove it, and the cursor advances inside the proof.**
Classify every response as covered, truncated at the provider's documented cap, rejected or failed, and
advance the durable cursor only inside the same conditional and the same transaction that established
coverage. A result count at the cap is a hole, not an empty result, and a branch that skips the query must
skip the advance. The failure is permanent silent under-crediting, with no error and no log line.

**The sequence allocator is a single writer, and the lock outlives the effect.**
Where the external ledger orders your instructions by a number you assign, that number is authoritative state
with exactly one writer. One lock spans allocation, signing, broadcast and the durable record of what was
sent, under a key derived from stable bytes that are identical in every replica. A lock released before the
broadcast protects nothing. Specialises *concurrency on authoritative state*.

**The outbound queue proves it can succeed before it adds to the wall.**
Before each broadcast, assert that ordering is not blocked, that the fee-paying reserve covers the queued
depth, and that an absolute fee ceiling denominated in the asset holds, not only a fee-rate ceiling. Any
assertion failing halts the queue and pages rather than submitting more, because a queue submitting into a
condition that guarantees failure converts one stuck instruction into a silent outage. Specialises *hard
limits*.

**Inclusion is not effect.**
Inclusion in the external ledger says the instruction was processed, not that it did what you intended: a
mined-but-reverted transaction consumes the sequence number, burns the fee and emits no logs. The absence of a
notification is not proof that nothing happened, because value moves on some paths with no event at all.

**The chain-to-ledger reconciliation names the authority and the join key, and runs in production.**
Compare the value observed on-chain to your addresses at or below your finality point against the value
credited, on a schedule, through a read path independent of the writer, and fail closed on a break. Both sides
must not come from the same provider API, or the reconciliation cannot detect the class of bug it exists to
find. Specialises *reconciliation*.

## References

Each row is an instruction. When the trigger appears, read the file immediately and apply it in order. Do not
summarise it.

| file | read it immediately when |
|---|---|
| [indexing.md](references/indexing.md) | the code contains `eth_getLogs`, `fromBlock`, `getPastLogs`, `createEventFilter`, `maxBlockRange`, a `cursor` / `watermark` / `last_processed_block` table, `ON CONFLICT DO NOTHING` on a deposit row, `txlistinternal` or `debug_traceBlock` |
| [finality-and-reorgs.md](references/finality-and-reorgs.md) | the code contains `confirmations`, `finalized`, `safe`, `reorg`, `parent_hash`, `ETHEREUM_REORG_THRESHOLD`, `listsinceblock`, `include_removed`, a per-chain `MIN_CONFIRMATIONS` table, or an L2 `sequencer` or batch-poster reference |
| [transaction-identity.md](references/transaction-identity.md) | the code contains `@solana/web3.js`, `solders`, `getSignatureStatuses`, `lastValidBlockHeight`, `AdvanceNonceAccount`, durable nonce; or `replaces_tx_hash`, RBF, CPFP, `maxPriorityFeePerGas`, `already known`; or EIP-712 `domain`, `verifyingContract`, a bridge `(sourceChain, destChain, nonce)` replay key |
| [custody-and-wallets.md](references/custody-and-wallets.md) | the code contains `UTXO`, `outpoint`, `vout`, `PSBT`, `changeAddress`, coin selection, dust; `derivationPath`, `xpub`, `gapLimit`, `importdescriptors`; `sweep`, `gasTank`, `forwarder`, a nonce or sequence allocator lock, a withdrawal queue; `sequenceId`, `externalTxId`, `treatAsGrossAmount`, batched withdrawals; an import of `bitcoinjs-lib`, `xrpl`, `stellar-sdk`, `bitgo`, or a Fireblocks SDK |
| [token-semantics.md](references/token-semantics.md) | the code contains `decimals()`, `balanceOf`, `Transfer(`, `approve`, `permit`, `safeTransferFrom`, `SafeERC20`, rebasing, fee-on-transfer; or `latestRoundData`, `AggregatorV3Interface`, `updatedAt`, `answeredInRound`, `slot0`, `getReserves`, `priceFeed`, `oracle` |
| [crossing-contract.md](references/crossing-contract.md) | the task is a review or a ship decision on a crediting or broadcast path, or you hold the signing keys, or you need the per-model table that turns an observable in the repo into the rule for that chain |

## Output

When the change is economic, report the two fields on one line:

    authority: EXTERNAL (<chain>) · exposure: own | customer | record

The usual pair here is authority EXTERNAL, because the chain is the record and can tell you that you are
wrong, and exposure `customer`, because a crediting or withdrawal path holds someone else's funds. Holding the
signing authority moves it to authority SELF: nothing outside holds the wallet's own view of which outputs are
its own and which nonce is next.

Then one entry per real finding, and nothing for a concept this change does not touch:

    FINDING   <the wrong economic outcome, concretely>
    WHY       <the mechanism that produces it>
    EVIDENCE  <file:line>
    FIX       <the change that closes it>
    TEST      <the property to assert>

A control you claim points at executable code and, where the risk needs it, a test. A comment, a TODO, an
uncalled helper or a design note describing a control is the same defect as the missing control; report it as
`UNRESOLVED: <control> (<why>)`. No findings means one or two sentences saying so and why the change is safe.

Add a final `VERDICT SHIP` or `VERDICT NO-SHIP: <the unresolved control>` only when the task is a review or a
ship decision.

Emit the fuller crossing contract from [crossing-contract.md](references/crossing-contract.md) when authority
is SELF, when exposure is `customer` on a crediting or broadcast path, or when the change spans more than one
of identity, coverage, finality and amount.
