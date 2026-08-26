# Disputes and chargebacks

> **Provenance**
> provider: Stripe and Adyen · surface: the card dispute lifecycle, dispute and inquiry statuses, what a refund does to a claim that is already open, and the dispute notification codes
> version: unversioned vendor documentation, as served at the URLs below on the date given
> verified_at: 2026-08-26
> sources: https://docs.stripe.com/disputes/how-disputes-work · https://docs.stripe.com/api/disputes/object · https://docs.stripe.com/disputes/responding · https://docs.stripe.com/disputes/best-practices · https://docs.stripe.com/refunds · https://docs.adyen.com/online-payments/refund/
> verified: every sentence quoted below was read at these URLs on 2026-08-26, along with the eight dispute status values, the inquiry statuses and their 120-day auto-close, the debit at claim creation, the response and decision windows, one-shot evidence submission, unchallengeable claims, more than one claim per payment, the late-win path, and the early-fraud-warning thresholds. The Adyen items are the three claim-related refusal reasons and the captured-amount bound, from its refund page on the same date.
> unverified: the Adyen `eventCode` table below, which predates this pass and was not reopened in it; the Mastercard representment mechanics, whose primary source is the Chargeback Guide and was not fetched; the Stripe event name for the early-fraud-warning object. Nothing here covers a local payment method: Klarna, PayPal and the rest run their own claim rules, which nobody read for this file.
> revalidate_when: Stripe adds or retires a dispute status value, or changes what `is_charge_refundable` reports; a network changes its inquiry phase, as Visa and Mastercard already have; Adyen changes its refund refusal reasons or its dispute event codes; you add a local payment method or a bank-debit rail, whose claim rules are that provider's own.

A counterparty-initiated debit against value you already booked, on a lifecycle you do not drive and a clock
that stays open for months.

## Contents

- Stripe dispute lifecycle: `warning_*` inquiries, evidence windows, one-shot submission, auto-close at 120 days
- Adyen dispute event codes and the funds-debited / funds-restored pairs
- Late wins, `charge.dispute.funds_reinstated`, and multiple disputes on one payment
- Refunding a payment that already carries a claim: the answer is the provider's, and they differ
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

The `status` enum, read from the API reference on 2026-08-26, is `warning_needs_response`,
`warning_under_review`, `warning_closed`, `needs_response`, `under_review`, `won`, `lost` and `prevented`.
`warning_closed` is an inquiry that never escalated, and `prevented` is *"a dispute that was prevented from
becoming a formal chargeback"*. Keep a deny-by-default arm anyway: an unrecognised status alerts rather than
falling through to "not disputed", because this enum has grown before and the fall-through direction pays.

## Adyen dispute event codes

Sourced from Adyen's dispute-notification documentation in an earlier pass, and not reopened on 2026-08-26:

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

## Refunding a payment that already carries a claim

Whether you may refund while a claim is open is the provider's answer, for that claim type on that rail, and
the two providers here answer differently. Read it off the claim object. Do not compile either answer into the
refund path, and do not infer it from the presence of a row in your own `disputes` table.

**Stripe, formal chargeback.** *"You can't issue a refund outside the dispute process while the dispute is
open."* The per-claim answer is a field on the dispute object, `is_charge_refundable`: *"If true, it's still
possible to refund the disputed payment. After the payment has been fully refunded, no further funds are
withdrawn from your Stripe account as a result of this dispute."* Refresh that field with the object and branch
on it, because it is the provider telling you which of its own rules applies to this claim today.

**Stripe, inquiry (the `warning_*` family).** A refund is one of the two documented ways to end the case:
*"You can resolve the case without incurring a dispute fee by either: Providing satisfactory evidence that
answers the dispute type for the inquiry [or] Issuing a full refund."* It buys less than it looks, because
*"Inquiries on partially refunded charges can still escalate to a chargeback"*, so the refund that resolves is
the full one. A blanket block keyed on "a dispute row exists" refuses exactly this refund, which is the
cheapest resolution available to either side.

**Adyen.** The bound is the balance left on the payment, not the existence of a claim. The documented refusal
reasons are *"Already partially disputed, new requested refund amount too high"*, *"Partially refunded and
partially disputed, no balance available for new requested refund"* and *"Already fully disputed, no balance
available for new requested refund"*, over a request whose own bound is that *"The `value` must be the same or,
in case of a partial refund, less than the captured `amount`."* A partial chargeback therefore leaves a
refundable remainder, and refusing it is your rule, not Adyen's.

What survives both answers is the arithmetic: model refund and claim as one exposure against the capture, so
the total returned never exceeds the total taken. Your ceiling controls your side of that and no more. The
network can still exceed it from its side, since *"a customer can dispute a payment for the full amount even if
they've already received a partial refund"*, and the response to that is evidence of the credit already issued,
inside the deadline.

## Refund-then-chargeback vs chargeback-then-refund

Two different bugs, one outcome: the customer holds two credits for one purchase.

| | refund → chargeback | chargeback → refund |
|---|---|---|
| sequence | you credit; cardholder (already in motion) files anyway | dispute is open; a credit is issued anyway |
| does the processor stop it? | **no**: nothing prevents a chargeback on a refunded charge | **sometimes, and per provider**: Stripe blocks the API path for an open chargeback while documenting a full refund as a way to resolve an inquiry, and Adyen refuses only past the remaining balance. Never blocked on a back-office path, a second processor, store credit, or a manual bank credit |
| your bug | the refund check passed because it only looked at `amount_refunded` | the refund check passed because it never queried `disputes` |
| bank-debit variant | n/a | Stripe, on SEPA DD / Bacs / ACH DD / ACSS / AU BECS / NZ: *"there's a risk of double refund… the customer might receive two credits for the same transaction"* |
| pending-refund variant | n/a | the refund fails with `charge_for_pending_refund_disputed`; the interlock worked, but if you decremented store credit at `refund.created` your ledger already double-credited |
| remedy | represent with prior-credit evidence, within `evidence_details.due_by` | reverse the internal credit; do not attempt to reverse the network credit |

The single guard that covers both directions is a refundable ceiling: one `(charge_id, currency)` ledger of
remaining refundable that debits refunds **and** claim amounts, evaluated under `SELECT … FOR UPDATE` on the
charge row, on every path that can credit a customer, including the internal store-credit path that never
calls the processor at all. A blanket refusal is not that guard. It leaves the arithmetic uncomputed, so the
paths it does not cover, which are the ones that produce this bug, still credit whatever they like.

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

Whatever the policy, the refund it triggers goes through the same combined-exposure ceiling as any other
refund. An EFW handler that calls `Refund.create` directly is the chargeback-then-refund column above,
automated.

*Unverified:* the exact Stripe event name for the early-fraud-warning object is not established by this
project's research. Read it off the webhook event catalogue rather than guessing.
