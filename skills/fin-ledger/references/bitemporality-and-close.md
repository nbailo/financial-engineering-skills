# Bitemporal balances and the accounting period

How a balance reported last Tuesday stays the balance reported last Tuesday: the separation of `effective_at`
from `created_at`/`posted_at` and the query each report is really asking for, what a correction aimed at a
closed and reported period does instead, close as a row rather than a finished cron job, and the audit
columns that make any historical balance re-derivable.

## Contents

- Bitemporality: two timestamps, and the query each supports
- Back-dating into a closed period
- Period close as a state
- The audit trail that reconstructs a balance at any point in time
- Review checklist

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

## Review checklist

| Present in the diff? | Artifact |
|---|---|
| As-of balance query filters `effective_at` **and** the discard window | the query, not the ORM default |
| No write with `effective_at` inside a `closed` period without `period_adjustment_for` + approver | chokepoint guard |
| Closed-period total assertion runs after every correction batch | scheduled job + alert key |
| Every non-ordinary posting carries `actor_id`, `approved_by`, `reason_code` | `NOT NULL` constraints |
