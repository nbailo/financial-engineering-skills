# Suspense, breaks, and repair

What a ledger does with a difference it cannot yet attribute: the break record and the quarantine that keep
the difference visible instead of posting it away.

## Contents

- Breaks: record, quarantine, and post only with a cause
- "Recompute from the whole ledger" is idempotent only if the recomputation is total
- Repair pipelines
- Review checklist

## Breaks: record, quarantine, and post only with a cause

A *reconciliation* difference (your record against someone else's) is not a conservation breach inside your own
journal. Every group still nets to zero, so your trial balance is untouched by it and nothing has to be posted
to keep it balancing. What the difference tells you is that one of the two sides is wrong and you do not yet
know which one.

**Raise a break record, then quarantine.** The record has to answer when the difference was detected, which
two sources disagree, by how much and in what currency, where it stands, who owns it and how old it is; a row
of `detected_at`, `source_a`, `source_b`, `amount`, `currency`, `status`, `owner` and an age is one shape that
answers all eight. The quarantine is whatever makes the affected item
unusable while the break is open: it cannot be spent, netted, swept or closed over, and every path whose answer
depends on the disputed quantity fails closed rather than guessing. The difference stays visible, and the next
run of the comparison reports it again.

**An automatic corrective posting for an unexplained difference converts a detectable break into an
undetectable one.** Booking the delta to suspense makes the books balance, moves the amount into an account
nobody reconciles, leaves the ledger asserting a figure no one can attribute, and makes the next comparison
report agreement. A corrective posting waits for an **authoritative cause**, covers only the amount that cause
explains, and carries whatever approval the scheme or your organisation requires; any residual stays an open
break. Where the governing accounting policy provides a suspense or difference account, what belongs in it is a
break with a known cause and a pending correction, so its balance stays attributable line by line. The Fed's
own manual separates the two destinations: the **Suspense** account holds *"miscellaneous debit items that are
temporarily held in abeyance pending disposition"* (items believed collectable) while the **Difference** account
absorbs *"an out-of-balance condition resulting from the normal operation of a department"* where resolution is
not economically feasible, and *"entries to this account are subject to reversal."* Neither description reaches
a difference you cannot explain, so a monthly sweep to expense is not a disposal route for one.

**A break is a reason to keep operating carefully, not a reason to halt.** The quarantine fails closed on the
disputed item; the rest of the system keeps serving. SEC Rule 17a-11(c) is the shipped precedent for the tempo:
a broker-dealer whose books are not current gives **same-day** notice and files the corrected computation
within **48 hours**, and nowhere in the rule is it told to cease operating. Copy that shape. The escalation
threshold is on the *age and size* of an open break, not on its existence, and a break that ages past its
threshold escalates to a named owner instead of expiring. A reconciliation that halts the whole system on the
first difference is switched off within a quarter, which is the same outcome as never having written it.

**Reconcile on three axes, not one.** A balance comparison alone misses the two failures that matter most:

| axis | the question | the detector |
|---|---|---|
| completeness | are all the records that should exist present on both sides? | a count and a set difference on the join key, not a sum: two missing records with offsetting amounts pass a sum |
| clearing | did every clearing and in-transit account return to zero? | a non-zero balance past the account's settlement window, which needs no external counterparty at all |
| balance | do the two sides agree on the amount for each joined record? | the per-record difference, raised as a break and quarantined against the record, not posted away |

The alert destination for all three is a configuration key with no default, so an unset destination fails at
startup rather than discarding the alert at the moment it fires.

## "Recompute from the whole ledger" is idempotent only if the recomputation is total

This design note is a common one:

> *"the webhook recomputes state from the whole ledger rather than applying deltas. That makes out-of-order and
> duplicate deliveries harmless"*

over a handler shaped like this:

```python
def handle_refund_webhook(evt):
    refund = Refund.get(evt.data.object.id)
    refund.status = evt.data.object.status                       # (1) unconditional per-row write
    order.refunded_cents = sum(r.amount for r in order.refunds   # (2) total recompute
                               if r.status == "succeeded")
    db.commit()
```

Line (2) is a genuine total recomputation. Line (1) is last-writer-wins wearing its clothes. Stripe *"doesn't
guarantee the delivery of events in the order that they're generated"*, so a `refund.failed` generated at t0 can
arrive after the `refund.succeeded` generated at t1; line (1) writes the stale status, and line (2) then
faithfully recomputes a total over a poisoned input. The note is the bug: it is where the author's belief lives,
and the belief was never tested against out-of-order delivery.

A recomputation is idempotent only if **all three** hold:

1. **Total input.** It reads the complete set of facts each run, not the ones the current message names. Every
   `WHERE` clause narrows the input set: `AND status = 'posted'` and `AND effective_at <= T` are fine; a
   missing `discarded_at IS NULL` or a filter on the triggering id is not.
2. **Pure output.** The result is a function of that set alone: no reads of the previous output, no `+=`.
3. **Atomic total write.** The whole derived object is replaced in one transaction, versioned, so a partial
   write cannot be observed or resumed.

Any per-row write that takes its value from the *event payload* rather than the fact set breaks (1), and must be
guarded to be monotone:

```python
updated = (db.query(Refund)
             .filter(Refund.id == evt.data.object.id,
                     Refund.status_event_at < evt.created)          # monotone guard
             .update({"status": evt.data.object.status,
                      "status_event_at": evt.created}))
if updated == 0:
    return          # a newer fact is already stored; recomputing is safe, applying is not
recompute_order_totals(order_id)                                     # then, and only then
```

NASDAQ's Facebook IPO cross is the same defect at venue scale: the revalidate-and-recompute loop *"incorporated
only the first cancellation received during the first calculation"* (the cursor advanced by one event per pass
instead of to the tail of the queue), so the recomputation was never total and could not converge while
cancellations arrived faster than a pass completed. Either consume the entire pending queue per pass, or freeze
the input set before computing. The ledger-side version: `SELECT SUM(amount) FROM entries WHERE account_id = ?`
is total and safe to re-run; `UPDATE balances SET balance = balance + :delta` is not, whatever the comment above
it says.

## Repair pipelines

A correction to committed state is not a psql session. Stripe routes repairs through workflows that
*"approximate a CI pipeline for ad-hoc data repair operations,"* requiring two-phase review before execution;
Knight ¶27 is the counter-example, where self-repair on a live system made the loss worse. The reviewed
artifact contains, at minimum: the exact `SELECT` identifying the affected rows and its **count**, run against
production and pasted in; the exact posting group to be written, legs enumerated, with `Σ per currency = 0`
shown; the expected trial-balance delta (zero) and the expected change to every clearing account (zero); a dry
run against a restored snapshot with the before/after balances of a named sample; and the reason code, the
approver, and the rollback posting that undoes it if the dry run's assumptions were wrong. While the repair is
pending, freeze writes to the affected aggregate and **keep reads and reconciliation queries serving**; do not
self-repair automatically. A per-aggregate `frozen_reason` checked on the write path only is the artifact.

## Review checklist

| Present in the diff? | Artifact |
|---|---|
| Any "recompute makes this idempotent" claim has an out-of-order + duplicate delivery test | the test |
| A reconciliation difference with no established cause | the break record, the quarantine that makes the affected item unspendable, and no corrective posting |
| A corrective posting closing a break | the authoritative cause named on the break record, the approval it required, and any residual left open |
| A suspense or difference account with a balance | every line attributable to a break whose cause is known and whose correction is pending |
