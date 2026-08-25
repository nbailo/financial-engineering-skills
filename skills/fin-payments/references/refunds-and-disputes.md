# Refunds and disputes

The verb row that carries the most incident weight. Covers the refundable-ceiling arithmetic in full, the
refund status graph including its asynchronous failure path, the dispute lifecycle on both Stripe and Adyen,
and the two distinct double-credit bugs that arise when a refund and a chargeback race each other. Also
carries the ledger shape of a refund, which is not the mirror of the charge.

## Contents

- The refundable ceiling, per charge and per currency, with worked partial-refund examples
- Cancel vs refund on an uncaptured intent; phantom refunds emitted by a partial capture
- Refund status graph: `pending`, `succeeded`, `failed`, `canceled`, `requires_action`, and the cycles
- The 30-day return-of-funds window and how the reversal is booked
- `failure_reason` values and the branch each one implies
- Refund vs reversal: statement behaviour, absent ARN, network fee differences, reconciliation impact
- Refund liquidity: available balance only, non-card refunds failing, negative-balance bank debits
- Stripe dispute lifecycle: `warning_*` inquiries, evidence windows, one-shot submission, auto-close at 120 days
- Adyen dispute event codes and the funds-debited / funds-restored pairs
- Late wins, `charge.dispute.funds_reinstated`, and multiple disputes on one payment
- Refund-then-chargeback vs chargeback-then-refund: different bugs, same double credit
- Early fraud warnings as a business-policy input, never a code default
- The refund ledger group: principal reversed, fee expensed, proportional Connect reversals

---

## The refundable ceiling, per charge and per currency

`refundable = amount_captured − already_refunded − pending_refunds − disputed_amount`, computed in your code,
per `(charge_id, currency)`. Four inputs, each with a wrong-but-plausible source:

| term | read it from | never from | why |
|---|---|---|---|
| `amount_captured` | `Charge.amount_captured` (Stripe) / sum of `CAPTURE` webhooks with `success=true` (Adyen) | `PaymentIntent.amount`, `Charge.amount`, `orders.amount_cents` | `PaymentIntent.amount` is the *authorized* amount; on a partial capture the two differ and your own order total was never the thing the processor took |
| `already_refunded` | your own `refunds` rows for that charge, status `succeeded` | `Charge.amount_refunded`, or `SUM(balance_transaction.amount) WHERE type='refund'` | `amount_refunded` is the processor's view of *its* object graph; it knows nothing about your store credit, a goodwill credit issued on a second processor, or a manual back-office credit. The balance-transaction sum is worse: a partial capture emits a `type=refund` line for the *uncaptured* portion (Stripe, balance-transaction types) |
| `pending_refunds` | your own rows, status `pending` **and** `requires_action` | omitting them | Stripe blocks a second refund only after its own object graph updates; in-flight money that reserves nothing is FM-18's mechanism |
| `disputed_amount` | the dispute object, **as a gate** | subtracting `dispute.amount` from a charge-currency ceiling | the dispute is denominated at *dispute-time* FX and may not be in the charge's currency at all. When the currencies differ there is no arithmetic to do; the correct behaviour is the gate below |

**The dispute term is a gate before it is a subtrahend.** While any dispute on the charge is not in a closed
status, `refundable = 0` for every non-dispute-process path. Stripe: *"You can't issue a refund outside the
dispute process while the dispute is open."* Once the dispute closes `lost`, the money is already gone;
subtract what the **settlement report** debited in your settlement currency, not `dispute.amount`.

### Worked example: partially captured charge with everything in flight

USD charge, authorized 12000 (USD 120.00), captured 9000. Then: one `succeeded` refund of 2000, one `pending`
refund of 1500, one open dispute for 4000.

```
amount_captured      9000
− already_refunded  −2000   (succeeded only)
− pending_refunds   −1500   (pending + requires_action)
− disputed_amount   −4000   (…and the dispute is OPEN, so the gate fires first)
                    ------
refundable           1500   → gated to 0 while the dispute is open
```

Three wrong answers the same code base produces:

| computation | result | over-refund |
|---|---|---|
| `paymentIntent.amount − already_refunded` | 10000 | 8500 |
| `charge.amount_refunded < charge.amount` (FM-18's check) | passes for any amount ≤ 10000 | up to 8500 |
| `SUM(bt.amount) WHERE type='refund'` as `already_refunded` | 2000 + 3000 phantom = 5000 → ceiling 4000 | under-refunds, then a support agent overrides it manually |

The Revolut US acquiring loss (exploited 2022, ~$23M gross / ~$20M net, disclosed 2023-07) is this ceiling
missing across a system boundary: a **declined** authorization has `amount_captured = 0`, and the refund path
funded the credit from house money instead. It was found by a partner bank reporting less cash than expected,
not by any internal control (FT, reported via Payments Dive, "Criminals stole $20M from Revolut via payment
loophole", 2023-07).

### The gate, as code

```python
OPEN_DISPUTE_STATUSES = {"warning_needs_response", "warning_under_review",
                         "needs_response", "under_review"}   # see the dispute section: verify this enum
IN_FLIGHT_REFUND_STATUSES = {"pending", "requires_action"}

def refund_ceiling_minor(conn, charge_id: str, currency: str) -> int:
    # Serialize refund issuance on this charge. Postgres rejects FOR UPDATE in the same
    # SELECT as an aggregate: ERROR: FOR UPDATE is not allowed with aggregate functions
    conn.execute("SELECT 1 FROM charges WHERE id = %s FOR UPDATE", (charge_id,))

    if conn.execute("""SELECT 1 FROM disputes
                        WHERE charge_id = %s AND status = ANY(%s) LIMIT 1""",
                    (charge_id, list(OPEN_DISPUTE_STATUSES))).fetchone():
        return 0                                    # gate, not arithmetic; currencies may differ

    row = conn.execute("""
        SELECT c.amount_captured_minor
             - COALESCE(SUM(r.amount_minor) FILTER (WHERE r.status = 'succeeded'), 0)
             - COALESCE(SUM(r.amount_minor) FILTER (WHERE r.status = ANY(%s)),      0)
          FROM charges c
          LEFT JOIN refunds r ON r.charge_id = c.id AND r.currency = c.currency
         WHERE c.id = %s AND c.currency = %s
         GROUP BY c.amount_captured_minor""",
        (list(IN_FLIGHT_REFUND_STATUSES), charge_id, currency)).fetchone()
    return row[0] if row else 0
```

`amount_captured_minor` is refreshed from `stripe.Charge.retrieve(charge_id)` inside the same unit of work,
not from the webhook payload, not from an `orders` row. Currency is part of the key, not an assumption: a
charge and its dispute can carry different currency codes, and a `refunds` row in another currency must not
join into the sum.

## Cancel vs refund on an uncaptured intent; phantom refunds from a partial capture

**`requires_capture` is not refundable.** Stripe: *"the charge attached to the PaymentIntent remains
uncaptured and can't be refunded directly. You must cancel the PaymentIntent."* Cancellable statuses:
`requires_payment_method`, `requires_confirmation`, `requires_action`, `requires_capture`, and `processing`
for US bank accounts. Adyen exposes `/cancelOrRefund` precisely because the merchant often does not know which
side of the capture boundary it is on, but that endpoint **must not** be used on a payment with multiple
partial captures, and it does not accept split instructions, so platform code must read capture state and
branch to `/cancels` or `/refunds` itself.

**A partial capture emits a refund that is not a refund.** Stripe writes a `charge` balance transaction for
the full authorized amount plus a `refund` balance transaction for the uncaptured portion. Reconciler code
shaped `if bt.type == "refund": revenue -= bt.amount` reports refunds that never happened and understates
revenue twice over. Two guards: use `reporting_category`, not `type`; and require every "refund" line to
correlate to a `Refund` object id (`re_…`) before it is allowed to reduce revenue. A `refund` balance
transaction with no `Refund` object is a capture artifact, not a credit to a customer.

## Refund status graph

Enumerate the legal `(status, event)` pairs and reject everything else with an explicit default arm. Do not
model this as "terminal states are absorbing": `succeeded` is corrected by a later economic fact.

| from | event | to | economic effect |
|---|---|---|---|
| n/a | `Refund.create` → `refund.created` | `pending` | **none.** money has not left |
| `pending` | `refund.updated` | `succeeded` | principal leaves your processor balance |
| `pending` | `refund.failed` | `failed` | principal returns to your balance (see 30-day window) |
| `pending` | `refund.updated` | `canceled` | never sent; no money moved |
| `pending` | `refund.updated` | `requires_action` | customer's bank returned the funds; a new instrument or action is needed |
| `requires_action` | `refund.updated` | `pending` | re-attempted |
| `requires_action` | `refund.updated` | `requires_action` | re-armed after another bank return: **self-loop, not a no-op** |
| `succeeded` | `refund.failed` | `failed` | **reversal of a transaction you already closed**, up to 30 days later |
| anything else | n/a | n/a | `raise InvalidRefundTransition`; do not silently ignore |

The self-loop and the `succeeded → failed` edge are the two arms that hand-written state machines omit. A
`match`/`switch` with no default arm turns an omitted edge into a silent no-op; the refund then sits `pending`
forever while your books say the customer was paid.

Two ordering hazards land on this graph specifically. Stripe's `created` is second-granularity, and
`refund.created` and `refund.updated` for the same `re_…` routinely share a second, so the per-object
watermark must hold `(created, applied_event_ids)` and admit an event at the same `created` whose id is not in
the set. And event-id dedupe does not cover it: a redelivered `refund.created` arrives with a fresh `event.id`
your `processed_events` table has never seen.

Adyen's shape is different and needs its own arms: `REFUND` with `success=false` is a *validation* failure
arriving synchronously-ish; **`REFUND_FAILED` arrives days later after an initially successful refund**; and
`REFUNDED_REVERSED` means the funds came back because the shopper's bank details were invalid. Both of the
latter two are `succeeded → failed` edges under different names.

## The 30-day return-of-funds window and how the reversal is booked

Stripe, on a failed refund: *"the bank returns the refunded amount to us and we add it back to your Stripe
account balance. This process can take up to 30 days from the post date."*

Book it as a **new balancing entry on the closed transaction, never as an edit or a delete**:

```
t0   refund succeeded            Dr  Revenue (contra)          4000
                                 Cr  Processor clearing        4000
t0+19d  refund.failed            Dr  Processor clearing        4000
                                 Cr  Revenue (contra)          4000
        …and re-open the customer obligation that the refund closed
```

The second half is the part that gets skipped. If `refund.created` (or `succeeded`) decremented store credit,
closed an RMA, released a hold or marked an order `refunded`, the `failed` handler must restore every one of
those; otherwise the customer is un-refunded, the merchant holds the money, and nothing in either system says
so (FM-20). Period close therefore cannot run on payment-object state: the reversal tail for refund failure is
up to 30 days, and it lands *after* the month it belongs to.

## `failure_reason` values and the branch each one implies

Verified set (Stripe refunds documentation): `charge_for_pending_refund_disputed`, `declined`,
`expired_or_canceled_card`, `insufficient_funds`, `lost_or_stolen_card`, `merchant_request`, `unknown`.

| `failure_reason` | what actually happened | branch |
|---|---|---|
| `charge_for_pending_refund_disputed` | a dispute landed while this refund was pending | **do not reissue.** Stripe's guidance is to accept or challenge the dispute instead, "to avoid duplicate reimbursements to the customer". Route to the dispute queue and restore the obligation the refund closed |
| `declined` | issuer refused the credit | retry is not automatically safe; alternate rail (ACH/cheque/store credit) under a human decision |
| `expired_or_canceled_card` | instrument is gone | collect a new destination; the money is on your balance, and it is a liability you now owe |
| `lost_or_stolen_card` | same, plus a fraud signal | do not silently re-credit the same customer record without review |
| `insufficient_funds` | **your** balance, not the customer's | top up / wait; this is the liquidity branch below, and it is retryable once funded |
| `merchant_request` | someone cancelled it | audit which operator, and whether the obligation was restored |
| `unknown` | no branch exists | dead-letter with an alert; never treat as "done" |

Every arm restores the downstream side effects. A `failure_reason` handled as a log line is FM-20 with better
observability.

## Refund vs reversal

A refund issued shortly after the charge may be processed by the network as a **reversal** rather than a
credit. Observable differences that break real code:

| | credit-shaped refund | reversal-shaped refund |
|---|---|---|
| cardholder statement | original charge stays, a separate credit line appears | **original charge drops off**; no credit line |
| ARN | issued | **none available** |
| network fees | credit-transaction fees | different |
| support script | "you'll see a credit in 5–10 days" | wrong: there will be no credit line to see |
| tracing | look up by ARN | **ARN lookup returns nothing** (FM-31) |

Model the two shapes distinctly on the refund record, because the customer-facing message and the tracing path
both branch on it, and a support tool that reports "refund not sent" because it found no ARN generates a
duplicate refund request from an agent who believes the first one failed.

## Refund liquidity

Refunds are funded from **your available processor balance**, not from the original charge. Stripe: *"Refunds
use your available Stripe balance (not including pending amounts)."* Consequences that are correctness, not
ops:

- Insufficient balance → **card refunds go pending**; **non-card refunds fail outright**. Same call, two
  different failure shapes, decided by a number you did not pass in.
- Stripe may debit your bank account to clear a resulting negative balance.
- A batch refund job firing N refunds at once succeeds for the first M and fails for the rest, producing a
  half-refunded cohort (FM-21). The batch needs a per-item outcome record and a resumable retry, not a single
  job status.
- Refund issuance is therefore **balance-dependent**: check the ceiling *and* the funding, and never treat a
  refund enqueued as a refund made.

## Stripe dispute lifecycle

- **Money moves at the start, not at the end.** The network debits the disputed amount plus the dispute fee
  immediately on creation and holds them for the whole lifecycle. On a loss, *"No money moves from your
  perspective. Stripe has already credited the issuer when they initiated the chargeback."* Code that books
  the loss at `charge.dispute.closed` books it in the wrong period and double-counts against the settlement
  report, which debited it at creation.
- **Inquiries are the `warning_*` family** and are not yet chargebacks. Inquiries **auto-close at 120 days**.
  `warning_needs_response` is the one that carries a deadline.
- **Windows:** response window 7–21 days; issuer decision 60–75 days; full lifecycle 2–3 months. Dispute
  eligibility is ~120 days from payment, **starting at the event date for future-dated services**; local
  payment methods typically allow 180 days. Drive every deadline from the processor-supplied
  `evidence_details.due_by`, never from a constant; a missed window is an automatic loss.
- **Evidence submission is one-shot**, forwarded immediately, and non-editable. Build the whole package before
  the call. A partial submission to "save progress" spends the only submission you get.
- Some disputes are unchallengeable and close as `lost` immediately; that path must not look like a bug to
  your alerting.

*Unverified:* the full Stripe dispute `status` enum is not established by this project's research; only the
`warning_*` family, `won` and `lost` are. Confirm the exact set against the API reference before writing an
exhaustive match, and until then keep a deny-by-default arm that alerts on an unrecognised status rather than
falling through to "not disputed".

## Adyen dispute event codes

Verified from Adyen's dispute-notification documentation:

| `eventCode` | meaning | funds |
|---|---|---|
| `NOTIFICATION_OF_FRAUD` | issuer fraud report (network fraud feed), before any dispute | none |
| `REQUEST_FOR_INFORMATION` | retrieval request | none |
| `NOTIFICATION_OF_CHARGEBACK` | chargeback incoming; defense period opens | none yet |
| `INFORMATION_SUPPLIED` | your defense documents were received | none |
| `CHARGEBACK` | chargeback executed | **funds debited** |
| `SECOND_CHARGEBACK` | issuer re-chargebacks after representment | funds debited again |
| `CHARGEBACK_REVERSED` | you won / issuer withdrew | **funds restored** |
| `PREARBITRATION_WON` / `PREARBITRATION_LOST` | pre-arbitration outcome | per outcome |
| `DISPUTE_DEFENSE_PERIOD_ENDED` | the window closed | none |

The funds-moving pair is `CHARGEBACK` / `CHARGEBACK_REVERSED`; everything else is protocol position, not
money. Join these on `pspReference`/`originalReference`; Adyen explicitly warns that duplicate webhook events
carry *the same* `eventCode` and `pspReference`, so dedupe on that pair plus `eventDate`, and note that
`merchantReference` is yours and **is not unique** (a retried payment produces two `pspReference`s under one
`merchantReference`).

## Late wins, `charge.dispute.funds_reinstated`, and multiple disputes

A dispute in `lost` **can later flip to `won`**, driven by the issuer outside the normal lifecycle, with funds
returned via `charge.dispute.funds_reinstated`. This is the same shape as the corrected terminal-state rule:
a terminal state is never re-opened by a *status* message, but it is corrected by an *economic* one. The
write-off you booked at close must accept a reinstatement afterwards; with no handler, the money lands in the
balance unattributed and shows up as an unexplained settlement break months later (FM-23).

Multiple disputes on one payment are possible, so `disputes` is a table keyed on the dispute id with a foreign
key to the charge, not a nullable `dispute_id` column on `charges`, and not a boolean. Store
`(dispute_amount_minor, dispute_currency)` separately from `(charge_amount_minor, charge_currency)`.

And do **not** write `assert dispute.amount == charge.amount`. FX drift between purchase and dispute, issuer
aggregation of several recurring charges into one dispute, partial disputes, and full disputes of partially
refunded charges each break it, and the assertion crashes the handler, so the dispute goes unanswered past
its 7–21 day window and is lost by default. The disputed amount crossed a network: it is an operating error
wearing a programmer-error costume, and it needs a fail-closed guard that alerts, not an `assert`.

## Refund-then-chargeback vs chargeback-then-refund

Two different bugs, one outcome: the customer holds two credits for one purchase.

| | refund → chargeback | chargeback → refund |
|---|---|---|
| sequence | you credit; cardholder (already in motion) files anyway | dispute is open; a credit is issued anyway |
| does the processor stop it? | **no**: nothing prevents a chargeback on a refunded charge | Stripe blocks the API path; **not** blocked on a back-office path, a second processor, store credit, or a manual bank credit |
| your bug | the refund check passed because it only looked at `amount_refunded` | the refund check passed because it never queried `disputes` |
| bank-debit variant | n/a | Stripe, on SEPA DD / Bacs / ACH DD / ACSS / AU BECS / NZ: *"there's a risk of double refund… the customer might receive two credits for the same transaction"* |
| pending-refund variant | n/a | the refund fails with `charge_for_pending_refund_disputed`; the interlock worked, but if you decremented store credit at `refund.created` your ledger already double-credited |
| remedy | represent with prior-credit evidence, within `evidence_details.due_by` | reverse the internal credit; do not attempt to reverse the network credit |

The single guard that covers both directions is the ceiling above: one `(charge_id, currency)` ledger of
remaining refundable that debits refunds **and** dispute holds, evaluated under `SELECT … FOR UPDATE` on the
charge row, on every path that can credit a customer, including the internal store-credit path that never
calls the processor at all.

*Unverified:* the Mastercard Chargeback Guide is the primary source for "credit previously issued"
representment mechanics; it is referenced but was not fetched by this project's research pass. Do not encode
specific reason codes or defense requirements from memory; read the current guide.

## Early fraud warnings as a business-policy input

An early fraud warning (issuer fraud feed; Adyen surfaces the same signal as `NOTIFICATION_OF_FRAUD`) says a
dispute is *likely*, not that one exists. Refunding on an EFW is right **before** a dispute exists and wrong
after, and Stripe's own guidance attaches an explicit economic threshold: refund when the charge is at or
below your dispute fee, do not when it is more than ~35% above it.

That is a business-policy input, not a code default. Encode it as a required config key with **no default**,
so a deployment that has not made the decision fails loudly rather than silently auto-refunding:

```python
EFW_AUTO_REFUND_CEILING_MINOR = require_config("payments.efw_auto_refund_ceiling_minor")  # no default
```

Whatever the policy, the refund it triggers goes through the same ceiling and the same dispute gate as any
other refund. An EFW handler that calls `Refund.create` directly is the chargeback-then-refund column above,
automated.

*Unverified:* the exact Stripe event name for the early-fraud-warning object is not established by this
project's research. Read it off the webhook event catalogue rather than guessing.

## The refund ledger group

Stripe, verbatim: *"Stripe's processing fees from the original transaction aren't returned"*, and a refund may
itself incur a fee. The refund group is therefore **not** the mirror of the charge group.

Charge of 10000 with a 320 processing fee (`balance_transaction`: `amount` 10000, `fee` 320, `net` 9680):

```
CHARGE                      Dr  Processor clearing      9680
                            Dr  Processing fee expense   320
                            Cr  Revenue                10000

PARTIAL REFUND of 4000      Dr  Revenue (contra)        4000
                            Cr  Processor clearing      4000
                            (fee expense: UNTOUCHED)

REFUND FEE, if charged      Dr  Processing fee expense     X
                            Cr  Processor clearing         X
```

The wrong version reverses the charge's group proportionally, crediting 128 of fee expense that the processor
never returned. Nothing fails; the processor clearing account drifts by exactly that amount per refund, and it
surfaces only in the settlement reconciliation, as a break with no obvious cause, which is then "fixed" with
a tolerance, which then hides real breaks.

**Connect reversals are proportional.** `refund_application_fee` and `reverse_transfer` on a partial refund
reverse *in proportion*, not in full: a 4000 refund of a 10000 destination charge carrying a 1000 application
fee reverses 40% of the transfer and 400 of the application fee. Book the proportional amounts the API
returns; do not recompute them locally and do not assume the platform recovers the whole fee. And the reversal
must be in the same unit of work as the refund. Stripe: *"Stripe debits your platform for refunds to
destination charge or separate charge and transfer payments. Reverse the transfers associated with these
charge types to recover the refund amount from your connected accounts."* A transfer reversal can itself fail
for lack of funds on the connected account, so an unrecovered reversal is a **receivable from the connected
account**, not a completed clawback.
