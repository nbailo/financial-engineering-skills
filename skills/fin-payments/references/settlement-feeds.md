# Settlement feeds

The settlement record is the only channel that is the money, and the one most integrations never build.
Covers the shape of each processor's settlement data, which column is the accounting column, the report
lines that share an amount and differ only in free text, the join keys that are actually unique, and the
currency pairs a payment has to carry.

## Contents

- Stripe balance transactions: `reporting_category` vs `type`, `pending` → `available`, the fee and net columns
- The overloaded `adjustment` type: dispute debits, dispute reversals, refund failures; parsing `description`
- Sweeps and specials: `payment_unreconciled` at 90 days, `connect_collection_transfer` at 180 days,
  `payment_reversal`, `payment_failure_refund`, `refund_failure`
- Adyen Settlement details report: columns, `Psp Reference` / `Modification Reference`, payout batches,
  InvoiceDeductions and DepositCorrections
- Join on the processor's identifier; `merchantReference` as a grouping attribute and why it is not unique
- Settlement lag by scheme and method; weekend consolidation
- Fee, tax and FX lines: presentment vs settlement vs payment-method currency

---

## Stripe balance transactions

Stripe names balance transactions *"our recommended starting point for reporting on your account's balance
activity"* (docs.stripe.com/reports/balance-transaction-types). One balance transaction is one movement of the
merchant's Stripe balance. A `Charge`, a `PaymentIntent` and a `Refund` are lifecycle objects; a
`BalanceTransaction` is cash.

Ingest raw, never update, and join on the processor's id:

```sql
CREATE TABLE stripe_balance_txn_raw (
  id                 text        PRIMARY KEY,      -- txn_...
  reporting_category text        NOT NULL,         -- the accounting column
  type               text        NOT NULL,         -- the API-shape column; NOT the accounting column
  source_id          text,                         -- ch_... re_... py_... du_... tr_...
  amount_minor       bigint      NOT NULL,         -- signed, settlement currency, integer minor units
  fee_minor          bigint      NOT NULL,
  net_minor          bigint      NOT NULL,
  currency           char(3)     NOT NULL,         -- SETTLEMENT currency, not presentment
  status             text        NOT NULL,         -- 'pending' | 'available'
  available_on       timestamptz,
  created            timestamptz NOT NULL,
  description        text,
  payout_id          text,
  ingested_at        timestamptz NOT NULL DEFAULT now(),
  raw                jsonb       NOT NULL
);
CREATE INDEX ON stripe_balance_txn_raw (source_id);
CREATE INDEX ON stripe_balance_txn_raw (payout_id);
```

Three properties of these columns are load-bearing.

**`reporting_category`, not `type`.** Stripe ships both and tells you to reconcile on `reporting_category`.
`type` is shaped by how the API produced the row and collapses economically distinct events onto one label; the
canonical case is the partial capture below. A reconciler whose `CASE` statement switches on `type` is wrong
before it has read a single row.

**`net_minor = amount_minor − fee_minor`, and the identity is assertable.** Assert it per row. A row where it
fails is a parser bug or a fee line you have not modelled; either way it is a break, not a rounding artefact.

**`status` is `pending` then `available`, and `available_on` is when.** A `pending` balance transaction is real
cash that you cannot yet pay out. Refunds draw on the available balance *"not including pending amounts"*
(Stripe, refunds documentation), so a refund can go pending or fail outright on a short balance while your
gross-revenue number looks healthy. Report on `created` for revenue and on `available_on` for liquidity; using
one where the other belongs is the payments-layer form of the `BookgDt` / `ValDt` confusion.

**Payout tie-out is the tightest invariant available.** For every payout `P`,
`Σ net_minor WHERE payout_id = P` must equal the payout's amount. This is the only assertion in the stack whose
right-hand side is cash landing in a bank account. Ship it.

### The partial capture that reports as a refund

Authorize 100.00 USD, capture 60.00. Stripe emits **two** balance transactions: a `charge` for the full
authorized 10000 minor units, and a second row for the uncaptured 4000 whose `type` is `refund`
(docs.stripe.com/reports/balance-transaction-types). No customer was refunded. `reporting_category`
distinguishes the two; `type` does not.

```python
# WRONG: the phantom refund. Understates revenue, and a customer-facing
# "refunds issued" report shows refunds that never happened.
if bt.type == "refund":
    revenue_minor -= bt.amount

# RIGHT: switch on reporting_category, and correlate to a real Refund object.
# An unmapped category HALTS the run; it never falls through to a default bucket.
handler = CATEGORY_HANDLERS.get(bt.reporting_category)
if handler is None:
    raise UnmappedReportingCategory(bt.id, bt.reporting_category)
```

Do not hardcode a category list from any document, this one included. Pin the enumeration from your own data
and fail on drift:

```sql
SELECT reporting_category, type, count(*), sum(amount_minor)
FROM stripe_balance_txn_raw
WHERE created >= now() - interval '90 days'
GROUP BY 1, 2 ORDER BY 3 DESC;
```

Store the result as a fixture and re-run it in CI. A new pair appearing is a schema change in the money feed,
which is exactly the event you want an alert for.

## The overloaded `adjustment` type

`adjustment` is used for **dispute debits, dispute reversals and refund failures**, and is disambiguated only by
the free-text `description` field (docs.stripe.com/reports/balance-transaction-types). All three can carry the
same amount with opposite economic meaning: a dispute debit takes the money, a dispute reversal ("late win")
returns it, a refund failure returns money you had already told the customer was theirs.

The discipline is **parse, do not infer**:

```python
ADJUSTMENT_PATTERNS = [
    (re.compile(r"..."), AdjustmentKind.DISPUTE_DEBIT),
    (re.compile(r"..."), AdjustmentKind.DISPUTE_REVERSAL),
    (re.compile(r"..."), AdjustmentKind.REFUND_FAILURE),
]

def classify_adjustment(bt) -> AdjustmentKind:
    for pattern, kind in ADJUSTMENT_PATTERNS:
        if pattern.search(bt.description or ""):
            return kind
    raise UnparsedAdjustment(bt.id, bt.description)   # break, not a default
```

Two rules around that function. First, the patterns are **not shipped in this file**: `description` copy is not
a versioned API surface, and a regex quoted from a document is a wrong error code waiting to happen. Derive
them from your own account's rows and pin them in a fixture. Second, never sign the row by inference:
`amount_minor` already carries the sign; a classifier that decides direction from the description will invert a
late win into a second loss.

Cross-check every `adjustment` against the lifecycle object it should correspond to: a `DISPUTE_DEBIT` with no
`du_…` dispute in your local store, or a `REFUND_FAILURE` with no `re_…` refund in a non-`succeeded` state, is a
break. The `adjustment` row and the object are two independent assertions about the same fact; reconciling them
against each other is what makes the free-text parse safe to depend on.

## Sweeps and specials

These lines appear only in the balance/settlement feed. Nothing in the payment-object graph will tell you they
happened, which is why a month-end revenue figure computed from `charge.status == 'succeeded'` is structurally
wrong (docs.stripe.com/reports/balance-transaction-types).

| line | what it means | window |
|---|---|---|
| `payment_unreconciled` | funds received that Stripe could not attribute to a payment are swept | **90 days** unreconciled |
| `connect_collection_transfer` | platform collects against a connected account's negative balance | negative balance held **180 days** |
| `payment_reversal` | the payment itself was reversed, not refunded | rail-dependent |
| `payment_failure_refund` | an async payment failed after crediting | rail-dependent |
| `refund_failure` | a refund came back; the money is yours again | up to **30 days** from the post date |

`refund_failure` and `payment_failure_refund` both **restore the customer's obligation**. If your fulfilment or
store-credit path fired on `refund.created`, the settlement feed is where you find out you gave the goods away.
`connect_collection_transfer` is the settlement-side shadow of a clawback that a platform modelled as
guaranteed: a transfer reversal fails when the connected account's balance is short, and the deficit then sits
for 180 days as a receivable you are carrying whether or not your books say so.

## Adyen Settlement details report

Adyen names the **Settlement details report** as the transaction-level reconciliation record containing *"all
balance movements that explain the financial standing of your merchant account"*: settled payments, refunds,
chargebacks, fees, **InvoiceDeductions** and **DepositCorrections**
(docs.adyen.com/reporting/settlement-reconciliation/settlement-details-report). InvoiceDeductions and
DepositCorrections have no webhook and no payment object. They exist only here.

The report is a CSV delivered per **payout batch**. Its economic shape is a gross leg, an FX conversion, and a
fee decomposition, ending in a net leg:

- gross amount and gross currency (debit and credit in separate columns, so the sign is positional)
- an exchange rate
- net amount and net currency
- fee components broken out: commission, markup, scheme fees, interchange

Do **not** hardcode the column headers from memory or from this file. Adyen's report layout is versioned and
account-configurable. Pin the header row in a test:

```bash
head -1 settlement_detail_report_batch_"$BATCH".csv | tr ',' '\n' | nl
# commit the output as a fixture; fail the ingest job when it differs
```

**The row-unique key is not a single documented column.** Derive it and let the database prove you right:

```sql
-- If this index ever conflicts, that IS the duplicate-settlement break. Do not widen it away.
CREATE UNIQUE INDEX adyen_settlement_row_uq
  ON adyen_settlement_raw (batch_number, psp_reference, modification_reference, type, seq_in_batch);
```

`Psp Reference` on a modification line is the **original payment**; `Modification Reference` is the capture,
refund or chargeback itself. Joining a refund line to your local refund on `Psp Reference` maps every refund of
a payment onto the same local row and silently nets them.

## Join on the processor's identifier

**`merchantReference` is not unique.** Adyen describes it as *"an identifier assigned by you"* and does not
enforce uniqueness. A retried payment attempt for one order produces two `pspReference`s under one
`merchantReference`, and both may settle, one of them as a duplicate you now have to detect. The same holds for
any `metadata.order_id` you attach to a Stripe object.

| join direction | key | correctness |
|---|---|---|
| settlement row → your payment | `pspReference` / `balance_transaction.source` | **the join** |
| settlement row → your order | `merchantReference` / `metadata.order_id` | grouping attribute only |
| your order → settlement | fan-out, 1:N, N ≥ 0 | never assume 1:1 |

```sql
-- Correct: the processor's id is the key; yours is carried for grouping and display only.
SELECT b.id, b.reporting_category, b.amount_minor, b.currency, p.id AS local_payment_id
FROM   stripe_balance_txn_raw b
LEFT   JOIN payments p ON p.processor_charge_id = b.source_id
WHERE  b.created >= $1 AND b.created < $2;
```

`LEFT JOIN`, not `INNER JOIN`. An `INNER JOIN` deletes exactly the rows the reconciliation exists to find: the
settlement line with no local record.

## Settlement lag

The lag is real, scheme- and method-dependent, and Adyen documents it explicitly, including **weekend
consolidation**: batches that would fall on non-business days are merged, so a naive "expected settlement date
= capture date + N" produces a wave of false breaks every Monday. Drive the expectation from a business-day
calendar per merchant account and per payment method, never from a constant.

| divergence | window |
|---|---|
| sync response vs webhook (Adyen async modifications) | seconds → minutes; `REFUND_FAILED` **days** |
| webhook vs API state (ordering) | milliseconds → 3 days (Stripe retry horizon) |
| API state vs balance/settlement | hours → days, scheme- and method-dependent |
| capture vs card settlement finality | ~1 business day expected; late presentment beyond that *(directional; not confirmed against Visa Core Rules)* |

A payment that has not settled inside its method's window is a `missing_settlement` break, not a row to wait
quietly on. Absence of information is not information.

## Fee, tax and FX lines

Presentment currency ≠ settlement currency ≠ payment-method currency, and a dispute is denominated at the
**dispute-time** FX rate, not the purchase-time rate. Storing one amount per payment makes the dispute
impossible to reconcile.

Persist all three pairs, plus the dispute pair, as separate columns, never one amount and a rate you intend to
re-apply later:

```sql
ALTER TABLE payments
  ADD COLUMN presentment_amount_minor bigint  NOT NULL,
  ADD COLUMN presentment_currency     char(3) NOT NULL,
  ADD COLUMN settlement_amount_minor  bigint,
  ADD COLUMN settlement_currency      char(3),
  ADD COLUMN method_currency          char(3);  -- disputes carry their own pair
```

Book the fee gross-of-decomposition and keep the components. Adyen breaks a settlement line into commission,
markup, scheme fees and interchange; Stripe gives a single `fee_minor` with a breakdown on the object. Merging
them into one "processing cost" number makes it impossible to answer why margin moved, and makes
`fee_mismatch` undetectable.

**The refund fee gap, worked.** Charge 100.00 USD; at an illustrative 2.9% + $0.30 the fee is 320 minor units.
Refund 40.00. Stripe's processing fee from the original transaction **is not returned**, and a refund fee may be
charged on top.

| balance transaction | `reporting_category` | `amount_minor` | `fee_minor` | `net_minor` |
|---|---|---|---|---|
| `txn_1` | `charge` | +10000 | 320 | +9680 |
| `txn_2` | `refund` | −4000 | 0 | −4000 |
| | | | **settlement net** | **+5680** |

A refund ledger group that mirrors the charge group reverses 40% of the fee (128 minor units) back into
revenue. Books say +5808, settlement says +5680, and the 128 is a permanent, silent, per-refund gap. It has no
other detector: every double-entry check passes, because the transaction still balances. Reverse the principal
leg and **leave the fee expensed**.

Also currency-specific and easy to get wrong at the payout boundary: Stripe treats **HUF and TWD** as
two-decimal for charges but **zero-decimal for payouts**, requiring payout amounts divisible by 100, and
requires **ISK and UGX** (nominally zero-decimal) to be sent as two-decimal values ending in `00`. A `Money`
type keyed only on ISO 4217 exponent will strand an unpayable residue in a HUF balance and will be 100× wrong
on ISK.
