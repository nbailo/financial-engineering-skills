# Corrections, reversals, and bitemporal balances

How a ledger changes a fact it has already booked without editing anything, and how a balance reported last
Tuesday stays the balance reported last Tuesday. Covers the three shapes a correction takes and when each is
right, the reversal links and the constraint that stops a double reversal, the separation of `effective_at`
from `created_at`/`posted_at` and the query that makes a historical balance stable under later writes, and what
a correction aimed at a closed and reported period does instead. The concept is not the failure; a compensating
entry and a double-reversal guard are the parts most implementations get right unaided. Every artifact below is
the enforcement that goes missing.

## Contents

- Which states are mutable, and where the boundary is enforced
- The three correction shapes, and when each is right
- Reversal links and the double-reversal guard
- When the reversal cannot post: hostile counterparty, frozen account, spent funds
- When the reversal is economically impossible: the return is not the mirror image
- Discard, never delete
- Bitemporality: two timestamps, and the query each supports
- Back-dating into a closed period
- Period close as a state
- The audit trail that reconstructs a balance at any point in time
- Suspense, breaks, and the monthly sweep
- "Recompute from the whole ledger" is idempotent only if the recomputation is total
- Repair pipelines
- Review checklist

## Which states are mutable, and where the boundary is enforced

"Immutable" in every shipped ledger means **immutable once posted**, not immutable from creation. Modern
Treasury states it flatly: *"A ledger transaction is mutable while pending and immutable once posted."* Its
transaction object carries states `pending` / `posted` / `archived`, and `archived` has its own machine-readable
reason set (e.g. `balance_lock_failure`). TigerBeetle and Uber take the opposite position: immutable from
creation, with a pending transfer modelled as a *separate* immutable record resolved by another immutable one
("an order once written can't be changed in any shape or form"). Both are consistent. What is not consistent is
a schema that says `-- append-only` in a comment and grants `UPDATE` to the application role:

```sql
-- Mutable while pending, frozen at post. Enforced, not documented.
CREATE OR REPLACE FUNCTION freeze_posted() RETURNS trigger AS $$
BEGIN
  IF OLD.status = 'posted' THEN
    RAISE EXCEPTION 'ImmutableTransaction: % is posted', OLD.id USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;
CREATE TRIGGER t_freeze_posted BEFORE UPDATE OR DELETE ON ledger_transactions
  FOR EACH ROW EXECUTE FUNCTION freeze_posted();
REVOKE UPDATE, DELETE ON entries FROM app_rw;   -- legs are never mutable at all
```

Fowler's `Accounting Patterns` does the same at the object level with `ImmutableEntryException` /
`ImmutableTransactionException`. A test asserts `has_table_privilege('app_rw','entries','UPDATE')` is false;
a grant is a fact a test can read, and "we don't do that" is not.

## The three correction shapes, and when each is right

| Shape | What is written | Preserves the original amount? | Right when |
|---|---|---|---|
| **Reverse and replace** | reversal group (exact opposing legs, `posting_type='reversal'`, `reverses_transaction_id` set), then a **new** group with the correct amounts and its own `effective_at` | Yes: both the original and its negation stay queryable | The original was wrong in a way a reader must be able to see: wrong account, wrong currency, wrong counterparty, wrong direction |
| **Difference adjustment** | one group for `corrected_balance − original_balance`, `posting_type='adjustment'`, correlated to the original | Yes, but the original stays the reported figure and the delta is separate | The original was right at the time and an input was later revised (a rate restatement, a re-estimated accrual, a re-priced fee). Fowler's `adjustAccount()` computes exactly `correctedAccount.balance() − originalAccount.balance()` |
| **Opposing correction transfer** | a single opposing transfer carrying a dedicated correction `code` and the original's correlation id | Yes | The ledger has no group/transaction envelope; TigerBeetle's recipe: transfers are immutable, corrections are *additional* opposing transfers tagged with `Transfer.code` and `user_data_128` pointing at the original |

**None of them is `UPDATE entries SET amount = :corrected WHERE id = :id`.** The balance may land right; the
history is then unreproducible, prior statements cannot be regenerated, and every downstream consumer that read
the old value is silently inconsistent.

**And none of them is an ad-hoc delta.** A posting of `-1_23` with `description = 'fix'`, no
`reverses_transaction_id`, no `adjusts_transaction_id` and no reason code is arithmetically indistinguishable
from a correction and forensically useless. The distinguishing artifact is the *link plus the reason code*, not
the sign of the amount. **Re-posting** is the case people conflate with both: replaying the *original* event
through a fixed posting rule. That is legitimate only against a group that was never posted (still `pending`, or
rejected before commit). Once posted, a re-post is a second economic fact and must reverse the first or it
double-counts.

## Reversal links and the double-reversal guard

Links are bidirectional; the uniqueness constraint goes on the **reverses** side.

```sql
ALTER TABLE ledger_transactions
  ADD COLUMN posting_type text NOT NULL DEFAULT 'ordinary'
      CHECK (posting_type IN ('ordinary','reversal','adjustment','replacement')),
  ADD COLUMN reverses_transaction_id    uuid REFERENCES ledger_transactions(id),
  ADD COLUMN reversed_by_transaction_id uuid REFERENCES ledger_transactions(id),
  -- one original can be reversed at most once. Postgres treats NULLs as distinct,
  -- so this permits unlimited ordinary rows. Do NOT write NULLS NOT DISTINCT here.
  ADD CONSTRAINT one_reversal_per_original UNIQUE (reverses_transaction_id),
  ADD CONSTRAINT reversal_names_its_target CHECK (
        (posting_type = 'reversal') = (reverses_transaction_id IS NOT NULL));
```

The back-pointer is a compare-and-set in the same transaction as the reversal insert, rowcount checked:

```sql
UPDATE ledger_transactions
   SET reversed_by_transaction_id = :reversal_id
 WHERE id = :original_id
   AND status = 'posted'
   AND reversed_by_transaction_id IS NULL;   -- 0 rows ⇒ already reversed ⇒ abort
```

Two operators clicking the same button, or one operator plus an idempotency retry, hit either
`one_reversal_per_original` (23505) or the zero-rowcount branch. Both resolve to a typed
`AlreadyReversed { original_id, reversal_id }` returning the *existing* reversal, not a raw `UniqueViolation`,
and not a second reversal.

**The status flag is not the guard.** This is a common defect: an unvalidated idempotency replay lets
`reverse_transfer` mark a transfer reversed **while writing zero compensating entries**. Marking is a status
write; reversing is a posting. Assert the second: after `reverse(t)`, `Σ legs(reversal) == −Σ legs(t)` per
currency, and the reversal group is non-empty.

## When the reversal cannot post: hostile counterparty, frozen account, spent funds

The clawback path is where ordinary safety controls become the failure. Three cases, in the order they bite:

**1 · `CHECK (balance_cents >= 0)` makes `allow_overdraft=True` dead code.** The constraint is easy to add and its
interaction with clawback is easy to miss: it is written correctly, and it rejects the very debit the reversal
path exists to write. The fix is not deleting the constraint; an agent reading "permit the clawback" and "the
constraint is the control" together will drop it outright, which is worse than either failure alone. Keep it and
name the exception:

```sql
ALTER TABLE balances
  ADD COLUMN negative_reason text
      CHECK (negative_reason IN ('reversal','adjustment','chargeback')),
  ADD CONSTRAINT balance_nonneg_unless_compensating
      CHECK (balance_cents >= 0 OR negative_reason IS NOT NULL);
```

The chokepoint sets `negative_reason` only from the driving group's `posting_type`. An ordinary customer debit
leaves it `NULL`, the CHECK fires, and the overdraft is still impossible; where the balance is derived rather
than materialised, a partial index on `entries (account_id) WHERE posting_type = 'ordinary'` backs the
equivalent per-type check. Then make sure the raw violation cannot escape mid-clawback: `psycopg` raises
`CheckViolation` (SQLSTATE `23514`), and a `try/except CheckViolation` that wraps only `transfer()` (not
`reverse()`) turns an operator's clawback into a 500 with the reversal half-written.

**2 · `AccountNotActive` on a frozen account blocks the standard fraud flow.** The flow is: freeze the
recipient, then claw back. An account-status check that rejects *all* postings to a frozen account forces an
unfreeze, which reopens exactly the drain window the freeze existed to close. Gate on the **initiator**, not the
account state (`if account.status == "frozen" and initiator == "customer": raise AccountFrozen(...)`),
so reversals, adjustments and clawbacks post against frozen and closed accounts while customer-initiated debits
do not.

**3 · The funds are gone.** The clawback posts, the customer balance goes negative, and that negative number is
not an error state; it is a **receivable**, and it must be represented as one. The debit lands on the customer
liability account; the credit is the reversal's counterparty leg; the resulting negative customer balance is
carried with `negative_reason='reversal'` and an owner, and it either recovers (a later deposit nets it off) or
it is written off to a bad-debt expense account by an explicit, approved posting. What it must never be is
silently clamped to zero (that destroys money with no journal entry explaining it), nor left as an unnamed
negative that the next solvency assertion reports as a conservation breach.

## When the reversal is economically impossible: the return is not the mirror image

Past the point where you control both sides, "reverse it" stops being an operation you can perform at all.
ISO 20022's `ExternalPaymentCancellationRejection1Code` is the catalogue of ways a recall fails *after* you have
optimistically shown the customer "cancelled", verbatim:

| Code | Meaning |
|---|---|
| `AM04` | "Amount of funds available to cover specified message amount is insufficient." *(the beneficiary already spent it)* |
| `ARDT` | "Cancellation not accepted as the transaction has already been returned." |
| `PTNA` | "the cancellation request cannot be accepted because the payment instruction has been passed to the next agent." |
| `NOAS` | "No response from beneficiary (to the cancellation request)." |
| `INDM` | "Payment cancellation request cannot be accepted until an indemnity agreement is established." |
| `ADAC` / `RQDA` | the beneficiary's debit authority has not been given / is required. |

And when money does come back it arrives as a fresh `pacs.004`, whose `PaymentTransaction118` makes
`RtrdIntrBkSttlmAmt` (the **returned** amount) mandatory and separate from the optional
`OrgnlIntrBkSttlmAmt`, alongside its own `IntrBkSttlmDt`, an optional `CompstnAmt` and unbounded `ChrgsInf`. So
a return has a new id, a new value date, a possibly different amount, possibly different charges, possibly an
FX leg, and **you cannot book it as a reversal**, which asserts same amount, same currency, same fee
treatment, same date. Book it as an independent, forward-dated transaction that *references* the original, and
post the residual `OrgnlIntrBkSttlmAmt − RtrdIntrBkSttlmAmt ± CompstnAmt ± charges` to a real P&L or receivable
account. Same shape for a marketplace clawback whose counterparty balance is short: recovery is not guaranteed,
so the shortfall is a receivable from that counterparty, not an assumed reversal.

The two Citi cases bracket the range. The $81 trillion mis-credit (April 2024, intended $280) was caught ~90
minutes after processing and **reversed hours later; no funds left the bank**. The $894M Revlon wire left the
building, and recovery stopped being a ledger operation and became litigation. The system boundary, not the
size of the error, decides which mechanism you get.

## Discard, never delete

An entry that should never have existed is stamped, not removed: `discarded_at timestamptz`, `discarded_by`,
`discard_reason`. `DELETE` is revoked. The discard must remain visible in the entry list, in the audit trail,
and in any as-of query whose knowledge time precedes it; a statement already sent to a customer contained that
entry and must still be reproducible.

Discard is for an entry that was never economically true (a double-ingested webhook, a test posting on prod).
Reversal is for an entry that *was* true and is no longer. If money moved externally on the strength of it, it
is a reversal.

## Bitemporality: two timestamps, and the query each supports

Three timestamps, two axes. `effective_at` is **valid time**: when the fact was economically true.
`created_at` / `posted_at` is **transaction time**: when the system learned it. Fowler's base `AccountingEvent`
carries exactly this pair as `whenOccurred` and `whenNoticed`; Modern Treasury separates `effective_at` (*"the
time at which the ledger transaction happened for reporting purposes"*) from `posted_at`.

```sql
CREATE TABLE entries (
  id             uuid PRIMARY KEY,
  transaction_id uuid NOT NULL REFERENCES ledger_transactions(id),
  account_id     uuid NOT NULL,
  amount         bigint NOT NULL,          -- minor units, signed
  currency       text   NOT NULL,
  effective_at   timestamptz NOT NULL,     -- valid time: when it was economically true
  created_at     timestamptz NOT NULL DEFAULT clock_timestamp(),  -- transaction time
  discarded_at   timestamptz,              -- transaction time of the retraction
  posting_type   text NOT NULL);
CREATE INDEX ON entries (account_id, currency, effective_at) INCLUDE (amount);
```

| Question the report is asking | Predicate | Stable under later writes? |
|---|---|---|
| Balance now | `effective_at <= now() AND discarded_at IS NULL` | n/a |
| Balance as of `T`, as the books understand it today | `effective_at <= T AND (discarded_at IS NULL OR discarded_at >= T)` | **No, and correctly so**: a back-dated entry changes it |
| What the statement said when we sent it on `S` | `effective_at <= T AND created_at <= S AND (discarded_at IS NULL OR discarded_at > S)` | **Yes**: frozen on both axes |
| Everything we learned about period `P` after closing it | `effective_at < :period_end AND created_at >= :close_at` | this is the prior-period-adjustment worklist |

The one-axis form is what the ledger's public as-of endpoint serves; the two-axis form is what regenerates a
document you already sent. Both are needed and they answer different questions; a customer disputing a
statement is asking the third row, and answering with the second row produces a number that has legitimately
changed since, which reads as tampering.

**`SELECT SUM(amount) ... WHERE created_at <= T` is the defect.** A back-dated entry with `effective_at < T`
arriving tomorrow is excluded today and included tomorrow, so the same historical balance query returns two
different answers on two days and disagrees with the closed books on both. It is not probed by any test whose
fixtures are all created in order, which is why it survives review.

## Back-dating into a closed period

Three accepted patterns, in decreasing order of preference:

| Pattern | Mechanism | Cost |
|---|---|---|
| **Dated correction in the current period** | Post now, `effective_at = now()`, correlated to the original; the closed period's totals never move | The economics land in the wrong period. Acceptable when the amount is below the period's materiality threshold |
| **Prior-period adjustment** | Post now with `effective_at` inside the closed period *and* a `period_adjustment_for` column naming that period; the closed period's as-of totals move, and every consumer of them is notified | Restated statements, and a reconciliation against anything derived from the old totals |
| **Reopen** | An explicit, approved state transition back to `open`, the correction, then a re-close that supersedes the first close record | The most disruptive; reserve it for material misstatements caught before external filing |

The standards' own answer is the first one. IFRS 9 B5.4.6, verbatim: *"If an entity revises its estimates of
payments or receipts …, it shall **adjust the gross carrying amount** … The entity recalculates the gross
carrying amount … as the present value of the estimated future contractual cash flows that are discounted at
the financial instrument's **original effective interest rate** …. **The adjustment is recognised in profit or
loss as income or expense.**"* Recompute at the original rate; recognise the difference **now**; do not restate.
The Federal Reserve does the same with unresolvable differences: the Difference account balance *"should be
removed and applied to current expense monthly and at year-end **regardless of the year in which the
differences originated**."*

The assertion that keeps this honest, run after every correction batch:

```sql
-- for each closed period, the sum of postings EFFECTIVE in it must not have moved
SELECT id, frozen_total_minor, recomputed FROM (
  SELECT p.id, p.frozen_total_minor,
         (SELECT COALESCE(SUM(e.amount),0) FROM entries e
            JOIN ledger_transactions t ON t.id = e.transaction_id
           WHERE e.currency = p.currency AND e.discarded_at IS NULL
             AND e.effective_at >= p.starts_at AND e.effective_at < p.ends_at
             AND t.period_adjustment_for IS NULL) AS recomputed
    FROM accounting_periods p WHERE p.status = 'closed') x
 WHERE recomputed <> frozen_total_minor;   -- must return zero rows
```

Period attribution is a correctness property distinct from amount correctness. June's accrual posted in July
balances perfectly and is wrong in every statement, interest certificate, covenant test and tax filing.

## Period close as a state

Close is a row, not a cron job that happens to have finished. Mechanism-level shape (no single citable spec;
this is the structure the sources imply, not a quoted schema):

```sql
CREATE TABLE accounting_periods (
  id                 text PRIMARY KEY,          -- '2026-06'
  currency           text NOT NULL,
  starts_at          timestamptz NOT NULL,
  ends_at            timestamptz NOT NULL,      -- half-open [starts_at, ends_at)
  status             text NOT NULL CHECK (status IN ('open','closing','closed','reopened')),
  closed_at          timestamptz,  closed_by text,
  frozen_total_minor bigint,                    -- the trial-balance figure at close
  entry_checksum     bytea,                     -- order-independent digest of the member entries
  superseded_by      text REFERENCES accounting_periods(id));
```

What close forbids: **no new entry may be written with `effective_at` inside a `closed` period unless
`period_adjustment_for` names that period and the write carries an approver.** Enforce it in the posting
chokepoint, not in review:

```sql
IF EXISTS (SELECT 1 FROM accounting_periods p
            WHERE p.status = 'closed' AND p.currency = NEW.currency
              AND NEW.effective_at >= p.starts_at AND NEW.effective_at < p.ends_at)
   AND NEW.period_adjustment_for IS NULL THEN
  RAISE EXCEPTION 'ClosedPeriodWrite: % falls in a closed period', NEW.effective_at;
END IF;
```

What close does **not** do: it does not resolve pending holds. A hold carries its own intrinsic `expires_at`
and expires on its own clock across the boundary: the same property TigerBeetle documents for account closure,
where closing does not cancel already-pending transfers and they can still time out. Nor does close make the
*rows* immutable; the append-only grant does that. Close makes a **range of the valid-time axis** off-limits to
new writes. Reopen is a transition with the same audit weight as the close: a new period row superseding the old
via `superseded_by`, both retained, so "what did we report at the time" stays answerable.

## The audit trail that reconstructs a balance at any point in time

Four questions, four columns, on the transaction (not the entry; the legs inherit):

| Question | Column | Notes |
|---|---|---|
| Who | `actor_type` (`customer`/`operator`/`system`/`job`), `actor_id`, `approved_by` | `approved_by` is `NOT NULL` for every `posting_type <> 'ordinary'` |
| When | `created_at`, `effective_at`, `posted_at`, `discarded_at` | all four; `now()` is not one of them at read time |
| Why | `reason_code` (a closed enum), `reason_text`, `request_id` | a closed enum is what makes breaks aggregatable; free text alone is not |
| From what | `idempotency_key`, `caused_by_event_id`, `reverses_transaction_id`, `adjusts_transaction_id`, `period_adjustment_for` | the linkage that makes any balance re-derivable |

FTX is the negative case and it is precise: `borrow = $65,000,000,000` with `allow_negative = true` on exactly
one account, and *"because database logs were not kept, the debtors could not determine when or by whom the
value was set."* Field-level audit logging on any per-account override of a solvency, credit-limit or
liquidation check is a forensic prerequisite, not a nicety. Stripe's counter-position: *"Ledger's immutability
ensures we can audit and reproduce any data point at any time."* The property test: for a random
`(account_id, T)`, the balance derived from entries under the as-of predicate equals the snapshot the system
reported at `T`, after an arbitrary interleaving of back-dated entries, reversals and discards.

## Suspense, breaks, and the monthly sweep

A correction driven by a *reconciliation* difference (your record versus someone else's) is not a
conservation breach and does not halt anything. It posts to a real `suspense`/`clearing` account **in the chart
of accounts** so the trial balance still balances, and raises a `break` row: `detected_at`, `source_a`,
`source_b`, `amount`, `currency`, `status`, plus an owner and an age. The Fed's own manual distinguishes the two
destinations: the **Suspense** account holds *"miscellaneous debit items that are temporarily held in abeyance
pending disposition"* (items believed collectable) while the **Difference** account absorbs *"an
out-of-balance condition resulting from the normal operation of a department"* where resolution is not
economically feasible, and *"entries to this account are subject to reversal."* Unresolved differences are
expensed monthly, not carried forever and not escalated into an outage. Route an unmatched item to a named
owner and never auto-adjust it: an automatic correcting posting for an unexplained difference converts a
detectable break into an undetectable one.

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
| `UNIQUE (reverses_transaction_id)` and the CAS on `reversed_by_transaction_id` | both, in the migration |
| Overdraft permitted on compensating posting types only, constraint retained | `negative_reason` or an equivalent typed exception |
| Reversal posts to a frozen account; only customer-initiated debits blocked | the initiator-gated check |
| `23514` (check) and `23505` (unique) mapped to typed errors on **every** posting entrypoint | not just `transfer()` |
| As-of balance query filters `effective_at` **and** the discard window | the query, not the ORM default |
| No write with `effective_at` inside a `closed` period without `period_adjustment_for` + approver | chokepoint guard |
| Closed-period total assertion runs after every correction batch | scheduled job + alert key |
| Every non-ordinary posting carries `actor_id`, `approved_by`, `reason_code` | `NOT NULL` constraints |
| Any "recompute makes this idempotent" claim has an out-of-order + duplicate delivery test | the test |
