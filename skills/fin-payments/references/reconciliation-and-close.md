# Reconciliation and close

The comparison job, and what an open break stops. Covers the reversal tail that decides when a period may
close, the break classes and their aged bucket, the auth-to-clearing matcher, the scheduled entrypoint with
its independent read path and fail-closed alert destination, and the onward disbursements whose outcome is
visible only in the feed.

## Contents

- The reversal tail by rail, and the period-close rule that follows from it
- Break classification and the aged-break bucket; what an unexplained break blocks
- Auth ↔ clearing matching: force posts, late presentment, amounts and dates that do not line up
- The scheduled entrypoint: independent read path, alert destination with no default, runbook
- Onward disbursement: `transfer_group` vs `source_transaction`, async funding, clawbacks as receivables

---

## The reversal tail and the period-close rule

| rail / event | tail |
|---|---|
| card dispute | ~120 days from payment; **180** for many local payment methods; **from the event date** for future-dated services |
| dispute late win (`lost` → `won`, `charge.dispute.funds_reinstated`, `CHARGEBACK_REVERSED`) | **unbounded** |
| ACH unauthorized consumer debit (e.g. R10) | **60 calendar days** after settlement |
| ACH administrative return (R01 NSF, R02–R04, R09) | **2 banking days** after the settlement date |
| refund failure | up to **30 days** from the post date |
| `payment_unreconciled` sweep | **90 days** |
| connected-account negative balance | **180 days** |

The rule that follows: **close the period on settlement data at a stated cut-off, and book everything that
arrives afterwards as a new entry in the current period that references the original, never as an edit to the
closed period.** A refund that fails 29 days after close, a dispute that flips to `won` two years later, and a
`payment_unreconciled` sweep at day 90 are all normal, and all three are new facts about an old transaction.

Two consequences for code:

1. **Do not mark a payment immutable before its tail expires.** An `is_final` flag set on `charge.succeeded` is
   a lie with a 120-day fuse. Carry `tail_expires_at`, derived per rail and per payment method, and let the
   reversal handlers key off it.
2. **Size a provision, do not assume zero.** The open tail is a liability with a known distribution. A close
   that books expected chargebacks at zero is not conservative; it moves P&L between periods.

## Break classification

Every comparison produces one of these. Each has an owner, an age, and an action. None of them is a tolerance.

| class | detection predicate | action |
|---|---|---|
| `missing_local` | settlement row whose `source_id` matches no local payment | create from settlement, alert; **never auto-fulfil** |
| `missing_settlement` | local terminal payment past its method's settlement window with no settlement row | alert; hold revenue recognition for that row |
| `amount_mismatch` | matched ids, differing `amount_minor` or `currency` | fail closed; settlement is authority for **cash**, but never silently overwrite the local economic record |
| `fee_mismatch` | `net_minor ≠ amount_minor − fee_minor`, or fee components do not sum to the total | parser bug or unmodelled fee line |
| `category_unknown` | `reporting_category` / Adyen `Type` outside the pinned enumeration | halt the mapping; do not bucket as "other" |
| `description_unparsed` | `type = adjustment` matching no pinned pattern | halt |
| `duplicate_settlement` | two settlement rows for one authorization | the Coinbase/Visa/Worldpay 2018 shape: a network-side reclassification re-presented settled authorizations while the reversals were still in flight, and customers were charged 2–17 times. Alert on `posted_count > 1` per authorization |
| `unmatched_clearing` | clearing record with no matching authorization | force post: **expected**, route to review, not an error |
| `aged` | any open break older than its class SLA | escalate; blocks close |

**An unexplained break is a liability, not a rounding difference.** Route it to an explicit suspense account
with an owner and an age; never drop it and never auto-adjust it. The aged bucket is what gives the job teeth:
an open break past its SLA **blocks period close, blocks revenue recognition for the affected rows, blocks
payout of the affected merchant's balance, and blocks any automated clawback**. A reconciliation whose only
output is a dashboard number has no teeth, and drifts to green by attrition.

The failure pattern to name explicitly: a marketplace split of 100.00 three ways at 33.33 leaves 0.01
unallocated; over a payout batch this becomes persistent drift between `sum(transfers)` and `charge.amount`,
which breaks the reconciliation job, which is then "fixed" by adding a tolerance, which then hides real errors.
Allocate remainders explicitly (largest-remainder, or a designated residual account) so the comparison stays
exact. **Any nonzero tolerance in a money reconciliation is a decision to stop detecting a class of error.**

## Auth ↔ clearing matching

Clearing records arrive without a matching authorization (**force post**), long after it (**late presentment**),
and for a different amount (tips, overcapture, incremental authorization, partial capture). The processor will
manufacture and back out an authorization entry to make the settlement post.

```python
# WRONG: false unmatched records, and worse, false MATCHES across two similar transactions.
match = find_auth(card_fingerprint=c.card, amount=c.amount, date=c.date)

# RIGHT: network identifiers first, then a scored candidate set with a human queue.
# Never auto-net a scored match; a wrong match moves money between two customers.
```

Match on the network's own identifiers where the feed carries them, degrade to a scored candidate set, and
route anything below the confidence floor to review. Design the matcher to be **1:N and N:0 tolerant**: one
authorization can clear in several presentments, and a valid clearing can have no authorization at all.

One trap specific to refunds: a refund issued shortly after the charge may be processed as a **reversal**: the
original charge drops off the statement, no credit line appears, and **no ARN is produced**. Support tooling
that traces a refund by ARN reports "refund not sent" for a refund that completed correctly. Model
reversal-shaped and credit-shaped refunds as distinct so tracing and customer messaging are right.

## The scheduled entrypoint

This is *reconciliation runs in production*, in payments form. The comparison ships as a scheduled entrypoint or it does not exist; SQL in a
comment, a docstring, or a "worth running as a cron" note counts as absent.

```python
# recon/settlement_recon.py: entrypoint, not a helper.

# reconciliation runs in production: the alert destination is a config key with NO
# default, and it raises at import when unset.
RECON_ALERT_CHANNEL = os.environ["RECON_ALERT_CHANNEL"]

def run(window_start: datetime, window_end: datetime) -> None:
    # 1. INDEPENDENT READ PATH. Page the processor with the reconciler's own credential.
    #    Never read the table the webhook handler populated: a writer bug is invisible
    #    to a reader that shares the writer's path.
    window = {"gte": int(window_start.timestamp()), "lt": int(window_end.timestamp())}
    rows, cursor = [], None
    while True:
        page = stripe.BalanceTransaction.list(created=window, limit=100, starting_after=cursor)
        rows.extend(page.data)
        # proven coverage before the cursor advances: a truncated page, an error, or a
        # count at the documented cap is a HOLE,
        # not an empty result. The cursor advances only when the range was covered.
        if not page.has_more:
            break
        cursor = page.data[-1].id

    # 2. Local side read from the LEDGER, not from the payment service's cache.
    breaks = compare(rows, ledger.read_window(window_start, window_end))

    # 3. Every break persisted with class, owner and age. Nothing is logged and dropped.
    persist_breaks(breaks)
    if breaks:
        alert(RECON_ALERT_CHANNEL, summarize(breaks))
    # 4. The cursor advances only after breaks are durably persisted, in the same transaction.
    advance_recon_cursor(window_end)
```

Non-negotiable properties of that job:

- **Independent read path both sides.** The processor side pages the API (or ingests the report file) with its
  own credential; the local side reads the ledger. If the reconciler shares a service, session or cache with
  the writer, it validates the writer against itself.
- **The alert destination has no default.** An unset channel must raise at import, not silently no-op in
  production. A reconciliation that cannot reach a human is a reconciliation that does not run.
- **Reconcile on three axes, not one.** *Completeness*: are all records present, proven with order-independent
  checksums over bounded windows rather than row-by-row diffs. *Clearing*: did every flow reach a terminal
  state; every clearing and suspense account returns to zero at steady state, which converts reconciliation from
  a batch job into a continuously queryable invariant. *Timeliness*: did the data arrive inside its window.
  Balance equality alone hides both missing and late data that happens to be self-consistent.
- **A break blocks.** Wire the aged-break count into the close gate, not only into a dashboard.
- **Write the runbook next to the job.** One section per break class: the query that lists it, the decision it
  needs, and who makes it. A class with no runbook entry will be closed by whoever is on call by re-running the
  job until it goes green.

The failure this exists to catch has a name. Revolut lost ~$23M gross / ~$20M net to a flaw that refunded
declined transactions out of its own funds, and it was detected when **a US partner bank reported holding less
cash than expected**, by external reconciliation, not by any internal control. The settlement report is that
control, built before you need it.

## Onward disbursement: transfers, `transfer_group`, and clawbacks

Money leaving to a third party is the case where recovery is a **claim rather than a certainty**, so the
settlement feed is the only place the outcome is visible.

**Returning value you already disbursed onward requires reversing the onward disbursement in the same unit of
work.** In a marketplace the onward disbursement is the connected-account transfer, so the refund and its
reversal commit together, and an unrecovered reversal is a receivable rather than a completed clawback. The
Stripe wording and the proportional-reversal arithmetic are in `refunds.md`; the settlement-side
shadow is `connect_collection_transfer`, in `settlement-feeds.md`, which is what a 180-day negative balance
looks like in the feed.

**A grouping attribute the counterparty offers for reporting creates no economic linkage.** Stripe's
`transfer_group` is a reporting label: *"it doesn't affect any standard functionality"*. It causes no reversal,
it joins nothing at settlement, and a platform that treats it as the link between a charge and its transfer has
built a relationship the processor will not honour. Only `source_transaction` creates a real dependency between
a charge and a transfer, and it is the field a reversal actually follows.

| field | what it is | what it does at settlement |
|---|---|---|
| `transfer_group` | a label you chose, attached to several objects | nothing; grouping and display only |
| `source_transaction` | a declared funding dependency on a specific charge | the transfer waits for the charge to settle, and reversals follow it |

**Never create a transfer against a payment whose method settles asynchronously** (ACH, SEPA Direct Debit)
until it has settled. Stripe: *"Stripe doesn't automatically reverse a transfer if the associated async payment
fails… your platform's balance is debited."* The failure lands up to 60 days later as a return, by which time
the connected account may be empty.

**Transfers are not auto-retried**, and a transfer reversal can itself fail for lack of funds on the connected
account. Model clawbacks as **receivables**, not as guaranteed recoveries: carry the unrecovered amount as an
open balance with an owner and an age, and reconcile it against `connect_collection_transfer` lines in the
settlement feed rather than assuming the platform got its money back.
