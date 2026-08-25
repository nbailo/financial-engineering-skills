# Seams: where a venue client meets a ledger, and where it meets a venue

A seam is a place where a change crosses out of this skill's domain into another one, and where the two skills
must not contradict each other. Two seams matter for a venue client: a fill becoming a posting, and a process
that is simultaneously a venue for its own clients and a client of somebody else's venue. This file also
carries the fuller venue contract block, which is the output shape for a review that crosses either seam.

## Contents

- Seam: exchange and ledger, and the join key that makes a fill post once
- What posts as a journal entry and what does not
- Busted trades and the clearly-erroneous window
- Seam: venue and client, and why one declaration cannot cover both halves
- Knight Capital sat exactly on this boundary
- The venue contract block, and when both contracts are emitted

## Seam: exchange and ledger

A fill becomes a posting. The venue is the authority for the fill; the ledger is the authority for the
balance; neither answers the other's question. Reconciling your ledger against your ledger proves nothing, and
reconciling your position against the venue says nothing about whether the money was booked.

**The join key is the venue's own trade identifier.** The ledger transaction id derives from it, so the same
fill arriving on the stream and again on the poll posts exactly once. Deriving the transaction id from a local
sequence, an insertion timestamp or a row id breaks the seam: the two arrivals get two ids, both post, and the
balance is wrong by the size of the fill with no error anywhere.

`fin-ledger` states this seam from the balance side. If the two statements ever disagree, that is a defect in
the skill suite to report, not a judgement call to make at the keyboard.

## What posts as a journal entry and what does not

| Quantity | Posts? | Why |
|---|---|---|
| Realized PnL | yes | it is a settled economic fact at the moment of the fill |
| Fees and commissions | yes, in the currency the venue charged them in | a cost in a third unit is still part of the outcome |
| Funding payments | yes, deduped by the venue's settlement or income id | an accrual that actually moved value |
| Position | **no** | a position is revisable; a posted entry is not |
| Unrealized PnL | **no** | it is a valuation, not an obligation |

The rule underneath the table: post what is settled, value what is not. A position that has been posted into
the books has to be un-posted when it changes, and an un-posting is a correction, which is exactly the audit
trail you did not want to spend on an ordinary price move.

## Busted trades and the clearly-erroneous window

A fill busted by the venue inside its clearly-erroneous window is a **new balancing entry**, never an edit to
the original one. The original fill happened, was reported, and may already have driven a hedge; erasing the
row destroys the only record of why the hedge exists. Model the bust the same way the order state machine
models a fill void: a negative-quantity event against the specific trade id, folded over rather than applied
in place. The mechanics are in [order-state-machine.md](order-state-machine.md) under "Late fills and fill
voids".

## Seam: venue and client

A client reconciles against the venue. A venue has nothing to reconcile against. That difference is the whole
reason the two skills exist separately, and it is stated in the two fields this suite reports:

| | authority | exposure | primary proof |
|---|---|---|---|
| The client half | EXTERNAL, the venue | `own`, or `customer` when the money is not yours | reconciliation against the venue, by client order identity |
| The venue half | SELF | `record`, because other systems consume the orders and fills you assign | replay, determinism and conservation assertions, because there is nothing to reconcile against |

Where one process is both, the classic shape being a broker OMS that is the system of record for its clients'
orders while simultaneously trading as a client of an exchange, **split the change and assess the halves
separately**. One declaration cannot cover both, because the two halves have different authorities and
different proof obligations:

- The **client half** reconciles by client order identity against the exchange, on the cadence and with the
  tolerance the exchange's replication lag dictates.
- The **venue half** requires order-by-order rejection on the last hop before the order is routed, a
  deterministic core, and deterministic simulation of that core. `fin-matching-and-settlement`, shipped
  separately, owns those requirements and states this seam from the venue side.

The tell that a codebase is sitting on this seam: an order object that is sometimes an instruction you sent
and sometimes an instruction somebody sent you, distinguished by a nullable field. A second tell is a single
reconciliation job that reads both an exchange endpoint and an internal table and calls the result agreement,
when in fact it has compared the client half against its authority and the record half against nothing.

Split the review the same way you would split the code. Report the two fields once per half, name the
authority each half answers to, and let each half carry its own unresolved controls. A merged declaration
tends to inherit the weaker obligation, because the client half has a reconciliation available and that
availability reads as proof for the whole system.

## Knight Capital sat exactly on this boundary

Knight was a client of the exchanges and simultaneously the system of record for the orders its own clients
sent it. The 45-minute failure crossed both halves: an emitter sending child orders to venues without regard
to the executions already received, and a "33 Account" holding positions the firm's own systems could not
match to a parent order. The client half had no working reconciliation between executions received and orders
still to send; the record half had a bucket that nobody read.

Both halves appear in this suite's references: the emitter side in
[execution-algorithms.md](execution-algorithms.md), which quotes the SEC order on the termination state the
emitter could not read, and the unmatched-position side in
[order-state-machine.md](order-state-machine.md) under "Synthesised events, deferred reports, orders you never
sent". The relevant engineering conclusion for the seam is narrower than either: a control that lives on one
half of a dual-role process does not protect the other half, and a review that declares one set of obligations
for the whole codebase will miss whichever half it did not declare.

## The venue contract block

The default output is one entry per real finding. Emit this fuller block **only** when exposure is `customer`
or `record`, or the change adds a second venue adapter, because those are the cases where the reviewer needs
the whole integration contract rather than the delta.

```
VENUE CONTRACT
venue + account: which venue, which account, whether identity is scoped per-account
id semantics:    correlation only, or documented dedupe, with the retention window and its source
unknown set:     the responses treated as UNKNOWN, and the query that resolves each
recovery:        how position, fills and the book are rebuilt after a gap, and what is blocked meanwhile
reconciliation:  the scheduled comparison, its join key, its cadence and its tolerance unit
risk gate:       what closes it, what stays callable while closed, what reopens it
```

Emit only the slots this change touches. A slot the change touches and cannot fill **is the finding**, and it
is reported as `UNRESOLVED: <control> (<why>)`. A slot the change does not touch is omitted rather than filled
with a reassuring sentence.

Where authority is SELF, add the per-technique evidence table whose shape `fin-verification` owns: replay,
determinism, conservation and fault injection at crash boundaries, each with the artefact that demonstrates
it. The venue-of-record equivalent of this block is the engine contract in `fin-matching-and-settlement`, and
a process that is both a venue and a client of another venue emits both, one per half, never one merged block
covering the two.
