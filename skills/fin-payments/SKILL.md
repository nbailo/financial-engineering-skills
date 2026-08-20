---
name: fin-payments
description: Use when the diff imports stripe, adyen, braintree, paypal, squareup, checkout or moderntreasury, or names PaymentIntent, charges.create, Refund.create, capture, dispute, chargeback, payout, transfer_group, pspReference, merchantReference, Idempotency-Key, balance_transaction, a processor webhook route, a settlement report, or ACH/SEPA/wire/RTP/FedNow/pain.001. Skip postings and balances tables: use fin-ledger.
license: MIT
---

# Payments run on a state machine you do not own

A payment integration is a distributed state machine owned by the processor and observed through **three
mutually inconsistent channels**: the synchronous API response, the webhook, and the settlement report. Each
processor says its own synchronous response is not authoritative (Adyen: *"The status of a payment can
sometimes change after you get the result code, so we recommend that you do not use the result code to update
your order management system."*), neither Stripe nor Adyen guarantees webhook ordering, and only the
settlement/balance record is the money. Two disciplines follow, and every rule below is mechanism for one of
them: **never let an absence of information become information**, and **never treat a notification as state;
treat it as a trigger to go read state.**

**The seam.** This skill owns the processor lifecycle and what the processor asserts. `fin-ledger` owns what
your books record: double-entry mechanics, chart of accounts, normality, period close. When a diff crosses
that line, load both and apply the seam rule at the end of this file.

## When this applies

The diff imports or calls `stripe`, `adyen`, `braintree`, `paypal`, `squareup`, `checkout`, `plaid`,
`moderntreasury`; or names `PaymentIntent`, `charges.create`, `Refund.create`, `capture`, `dispute`,
`chargeback`, `payout`, `transfer_group`, `pspReference`, `merchantReference`, `Idempotency-Key`,
`balance_transaction`; or defines a route that receives provider events; or reads a settlement,
balance-transaction or payout report; or names a rail (`ACH`, `nacha`, `SEPA`, `pain.001`, `pacs.008`, wire,
`RTP`, `FedNow`).

**Not this skill.** A table named `entries`, `postings`, `journal` or `balances` that you own, and the
debit/credit rules over it → `fin-ledger`. Orders and fills on a trading venue → `fin-exchange-integration`.
Chain deposits, confirmations and withdrawals → `fin-onchain`.

## The non-negotiables

### The webhook is a trigger; the API is current state

On every webhook, call the API for the object the event names (`stripe.Refund.retrieve(id)`,
`stripe.PaymentIntent.retrieve(id)`, `stripe.Charge.retrieve(id)`, Adyen's payment-details endpoint) and make
**every ledger move, every fulfilment and every order attribution from that response**, never from
`event.data.object`. Stripe Event objects are immutable and rendered at the account's API version *at event
time*; with a 3-day retry horizon the payload can be arbitrarily stale by the time you process it. `metadata`
read off the payload attributes money to whatever the object looked like when the event was queued. This is
G5's re-read clause in processor terms: the handler must contain a literal `retrieve` call between the
signature check and the first write.

### Order effects by the object, not by the event

Persist a per-object watermark keyed on the object id (`re_…`, `pi_…`, `ch_…`) holding the last applied
`created` **and the set of event ids already applied at that `created`**. Stripe's `created` is
second-granularity, and `refund.created` and `refund.updated` on the same `re_…` routinely share a second, so
a bare `if event.created <= wm: return` discards the `succeeded` event and the refund is **pending forever**.
Admit an event at the same `created` unless its id is already in the persisted set. Event-id dedupe cannot
substitute for this: a late `refund.created` carries a fresh `event.id` your `processed_events` table has
never seen, and it re-arms the money branch. Never order by the `Stripe-Signature` timestamp. It is
regenerated per delivery attempt. G5's version guard still applies, and the guard **is** the write.

### Mark processed when applied; dead-letter when unresolvable

Insert the row into `processed_events` **only after the effect was actually applied, in the same transaction
as the effect**. An event you could not resolve (unknown object, missing order, a dependency not yet created)
goes to a dead-letter table with an alert and is **NOT** marked processed, so the provider's redelivery
still reaches you. Committing an unresolvable event to the dedupe table stops all redelivery and makes the
miss permanent. The dedupe mechanism then works against recovery instead of for it. Durably record *what
happened*; never durably record *"I saw this"* for something you did not apply.

### The refund ceiling is the captured amount, minus everything in flight

Compute `refundable = captured_amount − already_refunded − pending_refunds − disputed_amount`, per charge and
per currency, **in your own code**, from the charge's `amount_captured`, never from `paymentIntent.amount`,
never from your own `orders.amount_cents`. Count `pending` refunds against the ceiling, not only `succeeded`
ones, or in-flight money reserves nothing. **Refuse the refund while any dispute on that charge is open** and
while another refund on it is `pending`. Stripe: *"You can't issue a refund outside the dispute process while
the dispute is open"*, and for bank-debit methods (SEPA Direct Debit, Bacs, ACH Direct Debit, ACSS, AU BECS,
NZ bank account debits), *"there's a risk of double refund… the customer might receive two credits for the
same transaction."*

### The processing fee is not refunded

A refund returns the **principal**. Stripe, verbatim: *"Stripe's processing fees from the original transaction
aren't returned"*, and a refund fee may be charged on top. The ledger group for a refund therefore reverses
the principal leg and **leaves the fee expensed**. It is not the mirror image of the charge's group.
Reversing the charge's full group on refund creates a permanent, silent, per-refund gap that surfaces only in
the settlement reconciliation below. `refund_application_fee` and `reverse_transfer` on a Connect charge are
**proportional**: a partial refund reverses proportionally. Refunds draw on your available Stripe balance
(*"not including pending amounts"*), so a refund can itself go pending, or fail outright on a non-card method,
when the balance is short.

### Reconcile against the settlement report, joined on the processor's identifier

Ship a **scheduled entrypoint** that joins the processor's settlement record — Stripe balance transactions
(use `reporting_category`, not `type`) or Adyen's Settlement details report — against your own records on the
**processor's own identifier**: `pspReference` / `balance_transaction.id`. `merchantReference` is **not
unique** and is a grouping attribute only. Stripe's `adjustment` type is overloaded across dispute debits,
dispute reversals and refund failures, disambiguated only by `description`. **Parse it, do not infer.** Close
the books on settlement data with a reversal tail, never on payment-object state: cards ≥120 days (180 for
many local methods, and *from the event date* for future-dated services), ACH unauthorized returns 60 calendar
days, refund failure up to 30 days, dispute late-wins unbounded. This is G7's named authority for payments.

## authorize · capture

**Cancel, do not refund, an intent in `requires_capture`.** Stripe: *"the charge attached to the PaymentIntent
remains uncaptured and can't be refunded directly. You must cancel the PaymentIntent."* A refund against
uncaptured funds returns money that was never taken. Cancellable statuses are `requires_payment_method`,
`requires_confirmation`, `requires_action`, `requires_capture`, and `processing` for US bank accounts.

**A partial capture destroys the remainder.** The uncaptured balance is *released, not held*, and cannot be
captured later, unless multicapture is enabled (Stripe) or Adyen Support has enabled multiple partial
captures on the account. The same API call has opposite consequences depending on account configuration that
is not visible in the diff, so the code asserts the mode it requires rather than assuming it. Under Stripe
multicapture, set `capture_method='manual'` and pass `final_capture=False` on every non-final capture:
`final_capture` defaults to `True` and the first omission releases the remaining authorization.

**Read the authorization deadline from the processor's response, never a constant.** Use the server-supplied
`capture_before`. Windows are brand-, channel- and CIT/MIT-specific (Visa CNP MIT is 4 days 18 hours,
card-present Mastercard/Amex/Discover 2 days, Klarna 28 calendar days to midnight), and Stripe and the network
classify MIT vs CIT from cardholder-participation signals, *"not solely on API parameters like `off_session`"*.

**Incremental authorization takes an absolute new total, not a delta.** `amount=1500` against a 1000
authorization means "make it 1500". On decline the original authorization survives *and* any field updates
piggy-backed into the same call are discarded. Re-apply them explicitly. Increments do not extend the capture
deadline.

**A partial capture pollutes the settlement report.** Stripe emits a `charge` balance transaction for the full
authorized amount plus a `refund` balance transaction for the uncaptured portion. A reconciler that maps
`type=refund` → "the customer was refunded" reports refunds that never happened and double-counts revenue
reductions.

## refund

**`refund.created` means pending, not paid.** Money is not gone until the refund reports `succeeded`, and a
`succeeded` refund can still move to `failed` afterwards. Stripe: *"the bank returns the refunded amount to
us and we add it back to your Stripe account balance. This process can take up to 30 days from the post
date."* The status set is `pending`, `succeeded`, `failed`, `canceled`, `requires_action`, and a
`requires_action` refund can cycle back to `requires_action` from `pending` when the customer's bank returns
the funds. The reversal lands as a **new balancing entry on a transaction you already closed**, never as an
edit to the original.

**`failure_reason` is a branch, not a log line.** The verified set is `charge_for_pending_refund_disputed`,
`declined`, `expired_or_canceled_card`, `insufficient_funds`, `lost_or_stolen_card`, `merchant_request`,
`unknown`. `charge_for_pending_refund_disputed` is the dispute-landed-while-refund-pending race, and Stripe's
guidance there is to accept or challenge the dispute rather than reissue the refund.

**Send an idempotency key on the refund call itself.** Generate a fresh key only after a **non-409 4xx**, a
validation error the processor rejected before its idempotency layer. After a timeout, a socket close or a
5xx, the same key is re-sent unchanged.

**A refund issued shortly after the charge may be processed as a reversal.** The original charge drops off the
statement, no credit line appears, **no ARN is produced**, and network fees differ. Support copy promising
"a credit in 5–10 days" is wrong for reversals, and reconciliation keyed on ARN finds nothing. On Adyen the
equivalents are `REFUND_FAILED`, arriving days after an initially successful refund, and `REFUNDED_REVERSED`
when the shopper's bank details were invalid.

## dispute · chargeback

**Do not assert `dispute.amount == charge.amount`.** FX drift between purchase and dispute, issuer aggregation
of several recurring charges into one dispute, partial disputes, and full disputes of partially refunded
charges all break it. **The assertion crashes the handler, so the dispute goes unanswered past its
evidence window and is lost by default.** The disputed amount crossed a network. It is an operating error
wearing a programmer-error costume, and it needs a fail-closed guard, not an `assert`. Store the dispute
amount and currency separately from the charge amount and currency.

**Dispute outcomes are not immutable.** `lost` can flip to `won` ("late wins"), and
`charge.dispute.funds_reinstated` must be accepted after you have already written the loss off. Multiple
disputes per payment are possible. Adyen's sequence is `NOTIFICATION_OF_CHARGEBACK` → `CHARGEBACK` (funds
debited) → `CHARGEBACK_REVERSED` (funds restored), with `SECOND_CHARGEBACK`, `PREARBITRATION_WON` and
`PREARBITRATION_LOST` beyond it. On a Stripe loss no money moves at close. Stripe credited the issuer when
the chargeback was initiated. Evidence submission is one-shot and non-editable.

## payout · transfer

**A marketplace refund reverses the connected-account transfer in the same unit of work as the refund.**
Stripe: *"Stripe debits your platform for refunds to destination charge or separate charge and transfer
payments. Reverse the transfers associated with these charge types to recover the refund amount from your
connected accounts."* A refund without the reversal takes the money from the platform.

`transfer_group` is a reporting label: *"it doesn't affect any standard functionality"*. It causes no
reversal and joins nothing at settlement; only `source_transaction` creates a real dependency.

**Never create a transfer against a payment whose method settles asynchronously** (ACH, SEPA Direct Debit)
until it has settled: *"Stripe doesn't automatically reverse a transfer if the associated async payment
fails… your platform's balance is debited."* Transfers are not auto-retried, and a transfer reversal can
itself fail for lack of funds on the connected account. Model clawbacks as receivables, not as guaranteed
recoveries.

## The idempotency key you send to a processor does not survive

G4 already requires the key to be minted from the intent instance and committed before the call. What is
specific to a processor is that the key **expires and is scoped**, so the retry loop must be bounded by wall
clock. The key does not survive retention expiry (Stripe ≥24 h, Adyen ≥7 d, PayPal capture 6 h, Open Banking
24 h, AWS Powertools 1 h default), cross-region failover (Adyen: keys *"will not be checked for duplication in
other regions"*), the rate limiter (429), the auth layer (401), most validation errors (400), or a different
processor. **500s ARE cached** and mean indeterminate. Stripe states there is no client-side algorithm that
resolves it, and Stripe's back office may roll the charge forward to the network afterwards, surfacing the
object only by webhook.

- Bound the same-key retry loop by wall clock to well inside the documented retention, and **store that bound
  as an asserted constant next to the provider's number**:
  `STRIPE_KEY_RETENTION = timedelta(hours=24)` … `assert RETRY_HORIZON < STRIPE_KEY_RETENTION`.
- Past the bound, stop retrying, mark the attempt **UNKNOWN**, and resolve by querying the processor for your
  own reference or by reading the settlement report. Never mint a fresh key to try again.
- Pin the key to the `(provider, endpoint/region, credential)` recorded at mint time.
- Encode the key to the narrowest length limit across every provider you target (≤255 Stripe · ≤64 Adyen ·
  ≤64 AWS · ≤40 Open Banking) and validate at construction.
- HTTP 409, and Adyen's 422/409 with error code **704**, mean "the operation is already in flight": back off
  and **read**, never retry as a write.
- Stripe returns `Idempotent-Replayed: true` on a replay and PayPal signals the same with 200 vs 201 on
  capture. Branch on it, or every downstream side effect (emails, fulfilment, ledger writes) fires twice for
  one correctly deduplicated payment.

## Irreversible rails

**Verify the destination before a send on any irreversible rail.** Wires, RTP and FedNow are final when
recorded and there is no reversal message. A return is a fresh `pacs.004` that the receiving institution may
decline outright (`RJCR`: the funds will not be returned). Expose "request return of funds", never "cancel"
or "reverse", and model the counterparty's right to refuse.

## Seam S1: payments ↔ ledger

*Stated byte-identically in `fin-ledger`.*

Every payment state transition emits **exactly one balanced ledger transaction** whose id derives from the
payment's idempotency key. Every clearing account between payment states returns to zero, monitored as a
continuous assertion. **Never derive a balance by scanning payment objects.** Authorizations are reserved
amounts in the payments layer, not ledger entries; only captures, refunds, disputes, fees and settlement
adjustments post.

## Required rows in the NAMED RISKS table

G2 already requires a NAMED RISKS table on any response touching a money path. When the diff touches a charge,
capture, refund, dispute, payout, transfer or webhook path, these rows are **required**, each with a real
`file:line`. A row you cannot fill is work not yet done, not a row to delete.

| risk | implemented at file:line | test name |
|---|---|---|
| refund ceiling from `amount_captured`, net of `pending` refunds and open disputes | | |
| `retrieve` of the named object from the processor API inside the handler | | |
| per-object `(created, applied_event_ids)` watermark, applied as the write | | |
| unresolvable event dead-lettered and alerted, not marked processed | | |
| refund ledger group reverses principal and leaves the fee expensed | | |
| scheduled settlement-report reconciliation, alert destination a config key with no default | | |
| transfer reversal in the same unit of work (required when the diff creates or reverses a `Transfer`) | | |
| destination verification (required when the send is a wire, RTP or FedNow payment) | | |

## References

Each row is a standing instruction: when its predicate is true, read the file **immediately** and apply it in
order. Do not summarise it.

| file | read it when |
|---|---|
| [processor-lifecycles.md](references/processor-lifecycles.md) | the diff names `PaymentIntent`, `requires_capture`, `capture_method`, `capture_before`, `increment_authorization`, `multicapture`, `final_capture`, Adyen `/captures`, `/cancels`, `cancelOrRefund`, or a result code (`Authorised`, `Received`, `Pending`, `Refused`, `PartiallyAuthorised`) |
| [webhooks.md](references/webhooks.md) | the diff defines or edits a route that receives provider events, or names `Stripe-Signature`, `construct_event`, `constructEvent`, `HmacValidator`, `notificationItems`, `[accepted]`, `processed_events` |
| [refunds-and-disputes.md](references/refunds-and-disputes.md) | the diff contains `refund`, `Refund.create`, `/refunds`, `refundable`, `dispute`, `chargeback`, `charge.dispute.`, `NOTIFICATION_OF_CHARGEBACK`, `evidence`, or an early-fraud-warning handler |
| [settlement-and-reconciliation.md](references/settlement-and-reconciliation.md) | the diff reads `balance_transaction`, `reporting_category`, `payout`, a settlement or payout report, an ARN, or closes an accounting period |
| [rails.md](references/rails.md) | the code names a rail or scheme message: `ACH`, `nacha`, an R-code (`R01`, `R10`), `SEPA`, `pain.001`, `pacs.008`, `pacs.004`, `camt.056`, `EndToEndId`, `TxId`, `UETR`, wire, `RTP`, `FedNow` |
