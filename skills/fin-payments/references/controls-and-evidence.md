# Controls and evidence

The evidence a payments change owes before it ships, keyed on a predicate you can evaluate from the diff alone.
Each row names one risk, the control that closes it, and the property a test has to assert. Nothing here is a
checklist to be filled in for its own sake: a row whose predicate is false is not emitted at all, and a row
whose predicate is true and whose location column is empty is work that has not been done yet.

## Contents

- The control table, keyed on a predicate in the diff
- What counts as evidence, and what an absent control is reported as
- Test properties, one per control
- The payments and ledger seam: what a payment state transition owes the books
- When a richer evidence block is warranted, and when a finding list is enough

---

## The control table, keyed on a predicate in the diff

Emit only the rows whose predicate this change matches. Each emitted row carries a real `file:line`.

| emit when the diff… | the control | the property to assert |
|---|---|---|
| issues or sizes a reversal | refund ceiling computed from the authority's captured amount (Stripe: `amount_captured`), net of reversals in flight (Stripe: `pending` and `requires_action`) and gated on open claims | a refund exceeding the ceiling is refused, and a refund is refused outright while a dispute on the charge is open |
| handles a pushed event | the object is re-read from its own authority inside the handler, before the first value-moving decision | with a payload whose status disagrees with the live object, the effect follows the live object |
| handles a pushed event | per-object watermark on the authority's own sequence value **plus** the identities already applied at that value, the guarded `UPDATE` being the write | two events sharing one coarse timestamp on one object both apply, exactly once each |
| handles a pushed event | an event that cannot be resolved is dead-lettered and alerted, and is **not** marked processed | after an unresolvable delivery, a redelivery of the same event still reaches the handler |
| posts a reversal or a fee to the books | the reversal ledger group books the fee treatment the provider or rail contract states, confirmed against that reversal's settlement lines | after a partial reversal, the processor clearing account still ties to the settlement net |
| writes, reports or closes a settled quantity | a scheduled settlement reconciliation reading through a path independent of the writer, with a fail-closed alert destination | a planted discrepancy in one row is detected and blocks the close |
| creates or reverses an onward disbursement | the disbursement reversal commits in the same unit of work as the refund | a refund whose transfer reversal fails leaves no committed refund without a recorded receivable |
| sends over an irreversible rail | destination verification and a hard maximum-plausible-amount bound before the send, blocking rather than warning | a payment above the bound is refused, not flagged |

Every control the change needs but this table does not match is still owed, and is reported the same way.

## What counts as evidence, and what an absent control is reported as

A claimed control points at executable code that a value-moving path actually reaches. A comment, a TODO, a
design note, a defined-but-uncalled helper and a `...` stub are all the same thing: the control is absent.

- **Implemented.** `<control> at <file>:<line>`, and where the risk warrants it, the test name.
- **Absent.** `UNRESOLVED: <control> (<why>)`. Never a completed row, never a tolerance, never a follow-up
  ticket standing in for the location.
- **Asserted in prose.** Any property claimed in a comment or docstring on this path is either named here with
  the test that proves it, or the sentence is deleted. The asserted invariant is repeatedly exactly where the
  bug lives, and the assertion is what let it survive self-review.

The location column is the whole point. The single highest-frequency failure in this domain is naming the
correct control accurately and then writing a comment instead of implementing it.

## Test properties, one per control

State the property, then instantiate it in whatever framework the repository already uses. These are the
payments-specific ones; the generic identity and retry properties belong to the money-core layer.

1. **The ceiling holds under concurrency.** Two refund requests issued concurrently against one charge, whose
   amounts sum to more than the captured amount net of everything in flight, produce exactly one success.
2. **The dispute gate fires before the arithmetic.** With an open dispute in a currency different from the
   charge, the refund path refuses rather than attempting a cross-currency subtraction.
3. **The re-read wins over the payload.** Feed the handler a payload rendered at an older status than the live
   object; the ledger effect matches the live object.
4. **The coarse-clock tie admits.** Deliver `created` and `updated` events for one object sharing one second,
   in order and then reversed; the terminal state is reached exactly once in both orderings.
5. **Redelivery after an unresolvable event still works.** Deliver an event whose dependency does not exist,
   then create the dependency and redeliver; the effect applies.
6. **The fee treatment ties to settlement.** After a partial reversal, the sum of ledger movements on the
   processor clearing account equals the settlement net for that charge, to the unit.
7. **A terminal state accepts its corrections.** A refund reported `succeeded` and later `failed` restores the
   customer obligation, the store credit and the order state that the success closed.
8. **A planted break is detected.** Alter one settled amount on the local side; the reconciliation reports it,
   classifies it, and blocks the close.
9. **The irreversible send refuses.** An amount above the plausibility bound, or a destination that failed
   verification, raises before any transmission begins.

## The payments and ledger seam

This is the processor half of the boundary. `fin-ledger` owns what the books record; load it alongside this
skill when a capture, refund or dispute becomes a posting.

- Every payment state transition emits **exactly one balanced ledger transaction**, whose id derives from the
  payment's own operation identity, so a redelivered event cannot post twice.
- Every clearing account between payment states returns to zero, monitored as a continuous assertion rather
  than as a month-end check. A clearing account that never returns to zero is a break that has not been
  classified yet.
- **Never derive a balance by scanning payment objects.** `SELECT sum(amount) FROM payments WHERE status =
  'succeeded'` is wrong for a partial capture, wrong for a refund, wrong for a dispute, and wrong for every
  settlement line that has no payment object at all.
- Authorizations are **reserved amounts in the payments layer, not ledger entries**. Only captures, refunds,
  disputes, fees and settlement adjustments post.
- The reversal tail decides when the books may close, not the payment object's lifecycle state. The tail is in
  `reconciliation-and-close.md`, per rail.

## When a richer evidence block is warranted

The default output is a list of findings. Emit the control table above as well only when one of these holds:

- **Exposure is `record`.** Your books, rather than the processor's report, are what other systems consume, so
  no external authority can tell you that you are wrong about the quantity in question.
- **A payout, withdrawal or crediting path is added or changed.** Value leaves to a third party, or arrives
  from one on a push you do not control, and the error is paid by someone who is not you.
- **Two or more processors or rails are in scope.** The reconciliation join key stops being obvious, and
  identity scope becomes per-provider.
- **The change is genuinely complex**: it touches three or more of the mechanisms in the table at once.

A single-processor sandbox integration with no live credential path does not warrant it, and neither does a
read-only report importer over your own transactions. In those cases the finding list, with a verdict line if
the ask was a review, is the whole output.
