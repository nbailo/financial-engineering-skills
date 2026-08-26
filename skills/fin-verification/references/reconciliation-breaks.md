# Breaks: classification, the break record, suspense, and the detect-test

What the comparison does with a difference once it has one: how it is classified, what the break row holds, how
long it may age before it escalates, what may lawfully be posted while it is open, and how to prove
the job detects anything at all.

## Contents

- Break classification: six classes, and the action each one implies
- The break record schema, the aged bucket, and the periodic sweep
- Suspense and clearing accounts: what may lawfully be posted to each, and what may not
- Alert routing and the detect-test

## Break classification

Six classes. The class decides the action; a job that emits one undifferentiated "mismatch" cannot route.

| Class | Detection predicate | Action |
|---|---|---|
| **Timing difference** | present in A, absent in B, and B's documented lag has not elapsed | hold in an `aging` state, re-evaluate next run; escalate only past the lag |
| **Missing-here** | authority has a record you do not | ingest it idempotently by the authority's key, then re-run. A processor-side reconciliation object surfaces **only via webhooks** on Stripe, so a job that never polls will never see it |
| **Missing-there** | you have a record the authority does not, past the lag | you may have acted on an effect that did not happen. Fail-closed on that scope; never re-send |
| **Amount mismatch** | same key, different amount beyond tolerance | raise the break, quarantine the amount so nothing spends or nets it, escalate. No corrective posting until a cause is established, and then only for the part that cause explains |
| **Attribution mismatch** | amounts net to zero but land on different accounts/instruments/currencies | the trial balance still balances, so nothing else will catch it. Always a break, never auto-corrected |
| **Duplicate** | two local records for one authority key, or vice versa | the join key was yours, not theirs. Fix the key; the duplicate itself is quarantined pending its reversal |

Two shapes that masquerade as amount mismatches and are not:

- **Partial capture.** Stripe emits a `charge` balance transaction for the **full authorized amount** plus a
  `refund` balance transaction for the uncaptured portion. A reconciler that reads `type=refund` as "customer
  was refunded" double-counts revenue reductions. Reconcile on `reporting_category`, not `type`; `adjustment`
  alone is overloaded across dispute debits, dispute reversals and refund failures, disambiguated only by
  `description`.
- **Force post / late presentment.** A clearing record can arrive with no matching authorization, or long
  after it, with amounts and dates that do not line up; the processor manufactures and backs out an
  authorization entry to make it post. Auth↔clearing matching must tolerate this by design.

## The break record schema and the aged bucket

```sql
CREATE TABLE recon_break (
  id              bigserial PRIMARY KEY,
  detected_at     timestamptz  NOT NULL DEFAULT now(),
  recon_name      text         NOT NULL,           -- which job
  class           text         NOT NULL CHECK (class IN
                    ('timing','missing_here','missing_there','amount','attribution','duplicate')),
  source_a        text         NOT NULL,           -- 'ledger:entries'
  source_b        text         NOT NULL,           -- 'adyen:settlement_details'
  authority_key   text         NOT NULL,           -- the counterparty's identifier
  local_ref       text,                            -- yours, non-unique, for humans
  amount          numeric(38,0) NOT NULL,          -- minor units, signed: a - b
  currency        char(3)      NOT NULL,
  account_id      bigint       REFERENCES accounts(id),
  status          text         NOT NULL DEFAULT 'open'
                    CHECK (status IN ('open','aging','escalated','resolved','swept')),
  suspense_txn_id bigint       REFERENCES ledger_transactions(id),
  resolved_at     timestamptz,
  resolution      text,
  UNIQUE (recon_name, authority_key, class)        -- re-running the job must not multiply rows
);
CREATE INDEX ON recon_break (status, detected_at) WHERE status <> 'resolved';
```

`amount` is `numeric(38,0)` in minor units, never `float`; mature projects still declare `Float` money
columns (freqtrade does), and the storage boundary is where the type survives or is lost.

**Aging.** `open → aging` when the authority's lag has not elapsed; `→ escalated` at a hard threshold stated in
the same unit as the cadence (not "soon", not "a few days"); `→ swept` by a **periodic sweep with a fixed
schedule** that expenses the residue to a named account and leaves the audit trail. The Federal Reserve's own
Difference account is swept monthly and expensed; the sweep is not an admission of defeat, it is what stops
the bucket from becoming an unbounded liability nobody reads.

**A break is never resolved by an automatic corrective write.** Repair is a separate, reviewed job with its own
entrypoint. An automatic corrective writer that is itself wrong writes the error into the authority's shape.

## Suspense and clearing accounts

A break whose cause is **established** waits for its approved correction in a real `suspense`/`clearing`
account **in the chart of accounts** (not a nullable column, not a log line). A difference you cannot yet
attribute does not go there. A disagreement with an external record never unbalanced your own journal, so
nothing has to be posted to keep the trial balance balancing, and posting it anyway moves the amount into an
account nobody reconciles and makes the next run report agreement. Square's Books is append-only:
*"there are no update statements for the tables presented on the diagram, only inserts"*, and errors are
corrected by **new balancing entries**.

Steady state is the assertion. Stripe: *"At steady state, terminal (nonclearing) reservoirs are full, and
intermediate (clearing) pipes are empty"*, so *"a single missing, late, or incorrect transaction immediately
creates a detectable accuracy issue with a simple query."* That converts reconciliation from a batch job into a
continuously queryable invariant:

```sql
SELECT account_id, currency, SUM(amount) AS bal
FROM entries JOIN accounts USING (account_id)
WHERE accounts.kind = 'clearing'
GROUP BY 1,2 HAVING SUM(amount) <> 0;   -- every row here is a break, per currency
```

`HAVING SUM(amount) <> 0` must be evaluated **per currency**: the accounting equation holds per currency, and
naive FX booking breaks it. Each clearing account carries a declared expected settlement window; a nonzero
balance older than that window escalates.

Reconcile on three axes, not one; Stripe's Data Quality Platform names them: **clearing** (did the flow reach
a terminal state?), **timeliness** (did the data arrive on time?), **completeness** (do we have all of it?).
Balance equality alone catches neither lateness nor self-consistent missing data. Prove completeness with
**order-independent checksums over bounded time windows** between the system of record and each derived store,
not row-by-row diffs: a single omission breaks the checksum (Uber's model).

## Alert routing and the detect-test

The alert destination is a **config key with no default that raises at import if unset**. "Page a human" and
"a channel with a named owner" are not things code can contain; they become comments, which is the exact
failure this rule exists to prevent.

```python
ALERT_SINK = os.environ["RECON_ALERT_SINK"]   # KeyError at import, not at 03:00 on a break
```

The alert also has to arrive somewhere a human reads. Knight emitted 97 "Power Peg disabled" emails in the
89 minutes before the open and nobody read them: the signal existed, and nothing proved it reached a
reader. A destination with no owner is the same defect as no destination.

Then prove it detects. The job running is not the same claim as the job finding anything.

```python
def test_recon_detects_seeded_amount_mismatch(fresh_migrated_db, fake_authority, alert_sink):
    # freshly migrated: an un-backfilled opening balance FAILS here instead of muting prod
    seed_local_entry(account="cust:42", amount_minor=10_00, currency="USD")
    fake_authority.record(psp_reference="PSP123", amount_minor=9_00, currency="USD")

    run_reconciliation(name="processor_settlement", as_of=date(2026, 3, 1))

    b = one(select_breaks(recon_name="processor_settlement"))
    assert (b.class_, b.amount, b.currency) == ("amount", 100, "USD")
    assert (b.source_a, b.source_b) == ("ledger:entries", "processor:settlement")
    assert b.authority_key == "PSP123"
    assert b.quarantined and b.suspense_txn_id is None  # unexplained: nothing posted away
    assert trial_balance_is_zero_per_currency()      # your own journal never unbalanced
    assert len(alert_sink.messages) == 1             # exactly one, not zero and not one per row

def test_recon_clean_run_produces_no_break_and_no_alert(fresh_migrated_db, fake_authority, alert_sink):
    ...
    assert select_breaks() == [] and alert_sink.messages == []
```

Both halves are required: the clean-run assertion is what stops a job that alerts on everything from passing.
