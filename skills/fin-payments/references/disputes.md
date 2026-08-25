# Disputes and chargebacks

A counterparty-initiated debit against value you already booked, on a lifecycle you do not drive and a clock
that stays open for months. Covers the dispute lifecycle on Stripe and on Adyen, the economic corrections a
closed dispute still receives, and the two distinct double-credit bugs that arise when a refund and a
chargeback race each other.

## Contents

- Stripe dispute lifecycle: `warning_*` inquiries, evidence windows, one-shot submission, auto-close at 120 days
- Adyen dispute event codes and the funds-debited / funds-restored pairs
- Late wins, `charge.dispute.funds_reinstated`, and multiple disputes on one payment
- Refund-then-chargeback vs chargeback-then-refund: different bugs, same double credit
- Early fraud warnings as a business-policy input, never a code default

---

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

The single guard that covers both directions is the refundable ceiling in `refunds.md`: one
`(charge_id, currency)` ledger of
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
