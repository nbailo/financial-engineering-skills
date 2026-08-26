---
name: fin-payments
description: >-
  Financial correctness for payment processor and rail integrations: authorization and capture,
  refund ceilings, disputes, payouts, webhook ordering and replay, fee treatment and
  settlement-report reconciliation. Use when reviewing an integration with Stripe, Adyen,
  PayPal, Square or Modern Treasury, or an ACH, SEPA, RTP or FedNow rail. For balances and
  postings use fin-ledger.
license: MIT
---

# Payments: someone else's state machine, seen through three channels that disagree

You mirror objects whose lifecycle the processor owns, and you learn about them from the synchronous response,
the pushed event, and the settlement report. The question this skill answers: can your copy of an object let
money move when the processor's copy says otherwise?

## When to use

The code hands an instruction to a counterparty that moves other people's money, then mirrors objects it does
not control: an authorization that may or may not become money, a reversal with a lifecycle of its own, a
counterparty-initiated debit against value you already booked, a disbursement to a third party, and a periodic
report stating what actually settled. Wherever your copy of one of those drives a fulfilment, a credit or a
further payment, this skill applies.

Literals are routing hints, not the definition. The change imports or calls `stripe`, `adyen`, `braintree`,
`paypal`, `squareup`, `checkout`, `plaid`, `moderntreasury`; or names `PaymentIntent`, `charges.create`,
`Refund.create`, `capture`, `dispute`, `chargeback`, `payout`, `transfer_group`, `pspReference`,
`merchantReference`, `Idempotency-Key`, `balance_transaction`; or defines a route that receives provider
events; or reads a settlement, balance-transaction or payout report; or names a rail (`ACH`, `nacha`, `SEPA`,
`pain.001`, `pacs.008`, wire, `RTP`, `FedNow`).

## When not to

A table of entries, postings, journals or balances that **you** own, and the debit/credit rules over it, go to
`fin-ledger`; load it alongside this skill when a capture, refund or dispute becomes a posting. Orders and fills
on a trading venue go to `fin-exchange-integration`. Chain deposits, confirmations and withdrawals go to
`fin-onchain`. Amount arithmetic, retry classification and rollout on a money path with no processor in it go
to `fin-money-core`.

This skill already specialises the money-core invariants that apply on a processor path, so load
`fin-money-core` alongside it only for a cross-domain mechanism this skill does not cover, and load
`fin-verification` only when tests, proof or reconciliation are actually changing, when the ask is a review or a
ship decision, or when an invariant below calls for stronger proof of the mechanism in scope. Customer exposure
alone triggers neither.

## Workflow

1. Name the economic effect and who is out of pocket when it goes wrong: the payer, the merchant, the platform,
   or a connected account.
2. Enumerate the legal transitions of every object you mirror, and mark which corrections each terminal state
   can still receive.
3. Treat every push as a notification. Re-read the object from the processor before any decision that moves
   value, and make the ordering guard the write.
4. Compute every ceiling from the processor's own numbers, net of everything in flight and of every open claim.
5. Decide fee, currency and reversal treatment, and where each of them lands in the books.
6. Reconcile against the settlement report, joined on the processor's own identifier, on a schedule, in
   production.
7. Load only the references this change needs, then implement each control and its test before calling the path
   complete.

## Invariants

### A pushed event names an object; only the authority holds that object's state

Specialises *authority*: the processor's copy is the record, and the payload is not that copy. A notification
says something about an object may have changed, never what the object is now. Verify authenticity, re-read the
object from the processor, then decide, then write. Attribution copied off the payload pays whoever the object
pointed at when the event was queued, and the queue can be days deep.

### Ordering is per object, on the authority's own sequence, and the guard is the write

Specialises *concurrency on authoritative state*: the guarded write covers the whole check-to-act section. What
differs here is the clock. Where the authority's sequence value is coarser than its event rate, the watermark
carries the last applied value **and** the identities already applied at that value, admitting an unseen
identity at the same value. A comparison that rejects at equality drops the sibling event that says the money
actually moved, and the object then sits non-terminal forever, with no error and no log line.

### The event identity dedupes the delivery; the object and the transition dedupe the effect

Specialises *durable dedupe*: the dedupe row is written in the same transaction as the effect. Two identities
are in play. A provider redelivering a notification sends the **same** event identity, so a unique index on that
identity is correct and cheap, and it solves transport redelivery and nothing else. Separately generated event
objects carry distinct identities and can describe one underlying object transition, and one object can be
reported under more than one event type, so the effect has to be idempotent on the **object identity and the
transition**, which is what actually protects the balance. Record durably what you applied, never that you saw
it: an event you could not resolve, because the object is unknown or a dependency does not exist yet, is a hole
in your state rather than a processed event, and committing it to the dedupe table stops all redelivery and
makes the miss permanent. Dead-letter it, alert, and leave it undeduped.

### The refund ceiling is the processor's number, net of everything in flight

Specialises *hard limits*: the ceiling rejects the reversal rather than observing it. It derives from what the
processor says it captured, minus completed reversals, minus reversals in flight, minus whatever a counterparty
claim has already returned or may still return, per object and per currency. Your own order record is not an
input. Model the reversal and the claim as one combined exposure against that capture: what you return plus what
the network takes can never exceed what you took. Whether a reversal is permitted while a claim is open, and
what it does to that claim, is the provider's documented answer for that claim type on that rail, read from the
claim object rather than assumed. Refusing every reversal strands legitimate customer credits; allowing every
reversal pays the customer twice. Where the claim is denominated in a currency you did not capture in, no
subtraction exists, so refuse and route it to a human. Anything in flight that you do not count reserves
nothing, so two concurrent reversals each pass a check that neither would pass alone.

### A terminal state of someone else's object accepts economic corrections

Specialises *authority*, which names the window in which two systems may legitimately disagree; here that window
stays open for months. Enumerate the legal `(state, event)` pairs and reject everything else with an explicit
error, never silence. A terminal state accepts exactly the events by which the counterparty corrects a fact you
already booked, and nothing else; a *status* message never re-opens it. The correction lands as a new balancing
entry against the closed record, never as an edit to the original.

### Reversal fee treatment is a term of the contract, confirmed against settlement

Specialises *rounding and conservation*: the parts still sum to the total, but a reversal is not automatically
the mirror of the original. Whether the original processing cost comes back, whether the reversal carries a fee
of its own, and how a split reverses are properties of the provider and the rail, and they vary by merchant
agreement and over time. Take the treatment from the contract and confirm it against the settlement lines for
that reversal. Never assume the reversal mirrors the principal, and never hardcode either answer: mirroring
credits back a fee the processor may not have returned, and assuming retention when it was returned overstates
expense by the same mechanism. Both sides of your own entry balance either way, so nothing fails; the gap
surfaces only in the settlement reconciliation, per reversal, forever.

### The identity you send the counterparty expires and is scoped

Specialises *operation identity* and *ambiguous outcomes*: minted from the intent instance, committed before the
call, reused unchanged by every retry of that decision. What a processor adds is that its memory of the identity
is finite and scoped, and both the retention and the scope boundary are that provider's own numbers rather than
a property of idempotency keys. Read them per provider, pin the key to the scope it was minted under, bound the
same-key retry loop well inside the documented retention, and assert that bound against the provider's number.
Past it, stop, mark the attempt UNKNOWN, and resolve by reading. Never mint a fresh identity for the same intent.

### Finality is a property of the rail, not of your API surface

Specialises *hard limits*: on an irreversible rail the only control is what happens before the send, so verify
the destination and bound the amount, blocking rather than warning. Expose "request return of funds", never
"cancel" or "reverse", and model the receiving party's right to refuse as a first-class outcome with its own
state. An interface offering an unwind that does not exist teaches operators and downstream systems that the
money is recoverable, and the modelling error becomes a support promise you cannot keep.

### The settlement record is the money; everything else asserts things about it

Specialises *reconciliation*: the comparison ships as a scheduled entrypoint, reads through a path independent
of the writer, and fails closed to a destination with no default. Two things differ here. Join on the
counterparty's own identifier: yours is a grouping attribute you chose, and unless the provider enforces
uniqueness on it, one of yours maps to several of theirs.
And close the books on settlement data with the rail's reversal tail, never on payment-object state, or late
reversals land against a period you already closed.

## References

Each row is a standing instruction: when its predicate holds, read the file and apply it. Do not summarise it.

| file | read it when |
|---|---|
| [lifecycle-states.md](references/lifecycle-states.md) | the change branches on a processor status: `PaymentIntent` `status`, `requires_capture`, `payment_intent.payment_failed`, `canceled`, an Adyen `resultCode` (`Received`, `Authorised`, `PartiallyAuthorised`), `originalReference`, or `cancelOrRefund` |
| [capture-and-amounts.md](references/capture-and-amounts.md) | the change captures, sizes or times a capture: `capture`, `capture_method`, `amount_to_capture`, `final_capture`, `multicapture`, `capture_before`, `increment_authorization`, `overcapture`, `/captures`, or a currency minor-unit or amount-ceiling table |
| [idempotency-keys.md](references/idempotency-keys.md) | the change mints, reuses or bounds a key sent to a processor: `Idempotency-Key`, `PayPal-Request-Id`, `apiRequestKey`, `Idempotent-Replayed`, a retry loop around a processor call, or a 409 or Adyen 704 branch |
| [webhook-endpoint.md](references/webhook-endpoint.md) | the change defines or edits the route that receives provider events, or names `Stripe-Signature`, `construct_event`, `constructEvent`, `HmacValidator`, `notificationItems`, `[accepted]`, raw-body access, `APPEND_SLASH`, `force_ssl`, or a checkout success or `return_url` page |
| [webhook-processing.md](references/webhook-processing.md) | the change applies a stored event: `event.id`, `(event.type, data.object.id)`, `(eventCode, pspReference)`, `processed_events`, `event.created` or `eventDate` ordering, a watermark or applied-ids guard, a dead-letter path, or a `retrieve` before a value-moving write |
| [webhook-recovery.md](references/webhook-recovery.md) | the change adds or edits a job that lists provider events since a cursor or re-reads non-terminal objects, or depends on a delivery horizon, a manual resend window, or key retention |
| [refunds.md](references/refunds.md) | the change contains `refund`, `Refund.create`, `/refunds`, `refundable`, `amount_captured`, `refund.failed`, `failure_reason`, `REFUND_FAILED`, or books a reversal's principal or fee |
| [disputes.md](references/disputes.md) | the change contains `dispute`, `chargeback`, `charge.dispute.`, `evidence_details`, `NOTIFICATION_OF_CHARGEBACK`, `CHARGEBACK_REVERSED`, `funds_reinstated`, or an early-fraud-warning handler |
| [rails-reversibility.md](references/rails-reversibility.md) | the code names a rail or a pull-back: `ACH`, `nacha`, an R-code (`R01`, `R10`), `SEPA`, Recall, RFRO, wire, `RTP`, `FedNow`, `camt.056`, or a destination check or amount bound before an irreversible send |
| [iso20022-messages.md](references/iso20022-messages.md) | the change builds, parses or correlates a scheme message: `pain.001`, `pacs.008`, `pacs.002`, `pacs.004`, `camt.029`, `camt.053`, `EndToEndId`, `TxId`, `UETR`, `MsgId`, `TxSts`, `CdtDbtInd` or `XchgRate` |
| [settlement-feeds.md](references/settlement-feeds.md) | the change reads settlement data: `balance_transaction`, `reporting_category`, `available_on`, an `adjustment` description, a Settlement details CSV, `Modification Reference`, a payout batch, or presentment-versus-settlement currency columns |
| [reconciliation-and-close.md](references/reconciliation-and-close.md) | the change writes or edits the reconciliation job, a break or suspense table, a period close or revenue-recognition gate, an auth-to-clearing matcher, or a transfer (`source_transaction`, `transfer_group`, `reverse_transfer`) |
| [controls-and-evidence.md](references/controls-and-evidence.md) | the task is a review or a ship decision on a payments path, or you need the test property for a control you are about to claim |

## Output

When the change is economic, open with two fields on one line, and omit the line entirely when it is not:

```
authority: EXTERNAL (Stripe) · exposure: customer
```

Authority is per quantity. The usual pair here is EXTERNAL, the processor, with exposure customer. Where one
authority covers every quantity in scope, emit the single line. Where it does not, emit `authority: MIXED` and
qualify the quantities that differ, one line each, two or three at most:

```
authority: MIXED · exposure: customer
  settlement state       EXTERNAL (Stripe)
  internal store credit  SELF
```

Exposure becomes `record` when your books rather than the processor's report are what other systems consume. A
finding may carry its own authority where that is what makes it a finding.

Then one entry per real finding:

```
FINDING   <the wrong economic outcome, concretely>
WHY       <the mechanism that produces it>
EVIDENCE  <file:line>
FIX       <the change that closes it>
TEST      <the property to assert>

VERDICT   SHIP | NO-SHIP: <the unresolved control>
```

The verdict line appears only when the task is a review or a ship decision. No findings means one or two
sentences saying so and why the change is safe. Never emit a slot for a concept the change does not touch. A
control you claim points at executable code that a value-moving path reaches; anything absent is reported as
`UNRESOLVED: <control> (<why>)`, never as a completed row.

Add the control table from [controls-and-evidence.md](references/controls-and-evidence.md) only when exposure is
`record`, when a payout, withdrawal or crediting path changes, when two or more processors or rails are in
scope, or when the change touches three or more of the invariants above at once.
