# Seams: where a payment, a chain log or a fill becomes a posting

Three boundaries where an economic fact is produced by a system the ledger does not control, and has to become
exactly one balanced group inside a system that does. Each seam is stated from both sides: the same boundary
appears in `fin-payments`, `fin-onchain` and `fin-exchange-integration` in those skills' own vocabulary. A
contradiction between the two statements is a defect in the skill suite, to be reported rather than resolved by
judgement at the keyboard.

The shared shape at all three seams: the upstream system owns a *revisable* view (a payment's state, a block's
inclusion, a position), the ledger owns an *append-only* view, and the translation between them is one
idempotency key derived from the upstream identity plus a staging account that holds value while the upstream
fact is still revisable.

## Contents

1. **S1 payments to ledger**: one balanced group per state transition, clearing accounts that return to zero.
2. **S2 onchain to ledger**: log identity, the PENDING to AVAILABLE staging rule, reorg unwind, the assertion.
3. **S3 exchange to ledger**: fills post, positions do not; stream and poll post once; bust as a reversal.
4. **What all three share**: the derived key, the staging account, and the reconciliation each one owes.

## 1 · S1 · payments to ledger

**Every payment state transition emits exactly one balanced ledger transaction**, and the ledger transaction's
id derives from the payment's idempotency key. One transition, one group. Two groups for one transition means
the second is a duplicate credit waiting for a redelivery to expose it; zero groups means the processor's own
object is being used as the balance.

**Never derive a balance by scanning payment objects.** Summing charges, refunds and payouts out of the
processor's API produces a number that changes when the processor backfills, re-serialises or re-scopes an
object, and it silently omits every economic fact that has no object (a fee assessed on the account rather than
the charge, an adjustment posted by the processor's operations team, a reserve). The processor's objects drive
postings; the postings are the balance.

**Which transitions post, and which do not.** An authorization is a reserved amount in the payments layer, not
a ledger entry: nothing has moved, and reserving against the merchant's own funds is a hold in the payments
layer's model. Captures, refunds, disputes, fees and settlement adjustments post. A change of an authorization's
expiry, its partial-capture remainder release, or a status field the processor recomputes does not post.

**Every clearing account between payment states returns to zero**, monitored as a continuous assertion rather
than as a report someone reads. The path from "captured" to "settled in the bank account" runs through
`clearing:processor:<name>:<currency>`, and a non-zero balance in that account past its settlement window is
the detector: it means a capture posted and its settlement never did, or the reverse. This is the seam's own
reconciliation, and it needs no external join key, which is why it is the cheapest control at this boundary and
the first one to ship.

Failure this prevents: a refund webhook redelivered after a deploy posts a second refund group, the customer's
liability account goes further negative than the original charge, and no clearing account moves, because both
groups were internally balanced. Only the derived-id rule stops it.

## 2 · S2 · onchain to ledger

**(i) Identity.** A deposit credit is exactly one balanced ledger transaction whose idempotency key is
`(chainId, blockHash, txHash, logIndex)`, never the tx hash alone and never `balance += amount`. The tx hash is
not unique per credit: one transaction can carry many transfer logs to the same address, and on some chains a
hash can appear in two competing blocks. The same log re-observed after a reconnect, a backfill overlap, or a
provider failover is then a no-op rather than a second credit.

**(ii) Staging.** The credit posts on observation into a per-user **PENDING (unavailable)** account, and moves
to **AVAILABLE** only when the credit policy's finality is reached. For a rollup that means L1 finality, not an
L2 block count: an L2 block is not final because the sequencer says it is. Below the policy depth, credit only
inside a stated exposure cap you are willing to lose outright, and record that cap as a number the code reads,
not as a paragraph. Withdrawal and onward transfer authorise from AVAILABLE alone, which is the whole point of
keeping the two accounts separate.

**(iii) Unwind.** A reorg detected by parent-hash mismatch produces a reversing balancing entry keyed on the
orphaned log identity: never an in-place edit and never a delete, because the credit was reported to the
customer and the reversal is the fact that has to be explainable afterwards. A reorg deeper than the indexer's
rollback floor is an unrecoverable-state halt, not a best-effort resync: past that depth the indexer cannot
enumerate what it credited from data it still holds.

**(iv) Assertion.** A continuous reconciliation asserts `Σ credited at-or-below finalized height == Σ observed
on-chain value deltas to deposit addresses`. The height bound is what makes the assertion checkable: without it
the two sides disagree by exactly the in-flight window and the alert is muted within a week.

## 3 · S3 · exchange to ledger

**Fills are the economically-final fact. Realized PnL, fees and funding post as journal entries; positions do
not.** A position is a derived, revisable quantity: it changes when a fill arrives, when a fill is corrected,
and when the venue re-states its own view. Posting it turns every re-derivation into a ledger mutation. Post
what is final, derive what is not.

**The ledger transaction id derives from the venue's `trade_id`**, so the same fill arriving on both the
websocket stream and the REST poll posts **once**. Both paths are load-bearing: the stream is fast and lossy
across reconnects, the poll is complete and late. Neither can be dropped, so the seam has to be idempotent by
construction rather than by only ever using one source.

**A fill reported as final can be busted** inside the venue's clearly-erroneous window. Booked economic history
therefore has to accept retroactive reversal as a **new balancing entry**, keyed on the busted trade's identity,
with the original entry untouched. The position is revisable; the entry is not editable. A design that treats
"the venue said final" as "the entry can never change" fails the first bust, and the usual repair is a manual
`UPDATE` against an append-only table.

Failure this prevents: a bust arrives, an operator edits the original fill's entry to zero, the trial balance
still balances, and the fee and funding entries that referenced the original fill now describe a trade that no
longer exists in the journal.

## 4 · What all three seams share

| | S1 payments | S2 onchain | S3 exchange |
|---|---|---|---|
| upstream identity the key derives from | the payment's idempotency key | `(chainId, blockHash, txHash, logIndex)` | the venue's `trade_id` |
| staging account holding revisable value | `clearing:processor:<name>:<ccy>` | per-user PENDING (unavailable) | none needed: a fill is final on arrival |
| what makes the fact revisable | redelivery, backfill, processor restatement | reorg below the rollback floor | the clearly-erroneous bust window |
| the continuous assertion | every clearing account returns to zero | `Σ credited ≤ finalized height == Σ on-chain deltas` | local realized PnL and fees converge to the venue's own figures |
| the correction shape | a reversing group keyed on the same upstream id | a reversing entry keyed on the orphaned log | a reversing entry keyed on the busted trade |

Three rules hold at every seam:

1. **The key is derived, not minted.** A key generated at the moment of posting makes redelivery a duplicate. A
   key derived from the upstream identity makes redelivery a no-op, which is the only version of at-least-once
   delivery a ledger survives.
2. **Value that is still revisable sits in an account that does not authorise.** The distinction between
   PENDING and AVAILABLE at S2 is the same distinction as a non-zero clearing account at S1: value present in
   the books, not yet spendable, and visible as a number rather than as a flag.
3. **The upstream system is the authority for the fact, and the ledger is the authority for the posting.** When
   they disagree, the reconciliation says which quantity is being compared and on which join key. A seam
   without a named join key has no reconciliation, only a hope that both sides were written by the same person.
