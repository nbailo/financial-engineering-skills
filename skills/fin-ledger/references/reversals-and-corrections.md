# Reversals and corrections: changing a fact you already booked

How a ledger changes a fact it has already booked without editing anything. Covers where the mutability
boundary is enforced, the three shapes a correction takes and when each is right, the reversal links and the
constraint that stops a double reversal, the safety controls that block the clawback they exist to enable, and
the return that is not a mirror image at all. The concept is not the failure; a compensating entry
and a double-reversal guard are the parts most implementations get right unaided. Every artifact below is the
enforcement that goes missing.

## Contents

- Which states are mutable, and where the boundary is enforced
- The three correction shapes, and when each is right
- Reversal links and the double-reversal guard
- When the reversal cannot post: hostile counterparty, frozen account, spent funds
- When the reversal is economically impossible: the return is not the mirror image
- Discard, never delete
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

## Review checklist

| Present in the diff? | Artifact |
|---|---|
| `UNIQUE (reverses_transaction_id)` and the CAS on `reversed_by_transaction_id` | both, in the migration |
| Overdraft permitted on compensating posting types only, constraint retained | `negative_reason` or an equivalent typed exception |
| Reversal posts to a frozen account; only customer-initiated debits blocked | the initiator-gated check |
| `23514` (check) and `23505` (unique) mapped to typed errors on **every** posting entrypoint | not just `transfer()` |
