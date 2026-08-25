---
name: fin-payments
description: >-
  Financial correctness for payment processor and rail integrations: authorization and capture,
  refund ceilings, disputes and chargebacks, payouts and transfers, webhook ordering and replay,
  fee treatment and settlement reconciliation. Use when building or reviewing an integration
  with Stripe, Adyen, PayPal, Square or Modern Treasury, or with ACH, SEPA, wire, RTP and
  FedNow. For balances and postings use fin-ledger.
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

## Workflow

1. Name the economic effect and who is out of pocket when it goes wrong: the payer, the merchant, the platform,
   or a connected account.
2. Enumerate the legal transitions of every object you mirror, and mark which corrections each terminal state
   can still receive.
3. Treat every push as a notification. Re-read the object from the processor before any decision that moves
   value, and make the ordering guard the write.
4. Compute every ceiling from the processor's own numbers, net of everything in flight, and gate it on open
   claims.
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

### Record durably what you applied, never that you saw it

Specialises *durable dedupe*: the dedupe row is written in the same transaction as the effect. The domain
difference is redelivery. An event you could not resolve, because the object is unknown or a dependency does
not exist yet, is a hole in your state rather than a processed event; committing it to the dedupe table stops
all redelivery and makes the miss permanent. Dead-letter it, alert, and leave it undeduped.

### The refund ceiling is the processor's number, net of everything in flight

Specialises *hard limits*: the ceiling rejects the reversal rather than observing it. It derives from what the
processor says it captured, minus completed reversals, minus reversals in flight, minus anything under
counterparty claim, per object and per currency. Your own order record is not an input. An open claim is a gate
before it is a subtrahend: refuse while any dispute on that object is open. Anything in flight that you do not
count reserves nothing, so two concurrent reversals each pass a check that neither would pass alone.

### A terminal state of someone else's object accepts economic corrections

Specialises *authority*, which names the window in which two systems may legitimately disagree; here that window
stays open for months. Enumerate the legal `(state, event)` pairs and reject everything else with an explicit
error, never silence. A terminal state accepts exactly the events by which the counterparty corrects a fact you
already booked, and nothing else; a *status* message never re-opens it. The correction lands as a new balancing
entry against the closed record, never as an edit to the original.

### The processing fee is not returned by a reversal

Specialises *rounding and conservation*: the parts still sum to the total, but the reversal is not the mirror of
the original. The principal comes back, the original processing cost stays expensed, a reversal fee may be added
on top, and a reversal of a split payment reverses each share proportionally rather than wholesale. Mirroring
the original group credits back fee expense the processor never returned. Both sides of your own entry balance,
so nothing fails; the gap surfaces only in the settlement reconciliation, per reversal, forever.

### The identity you send the counterparty expires and is scoped

Specialises *operation identity* and *ambiguous outcomes*: minted from the intent instance, committed before the
call, reused unchanged by every retry of that decision. What a processor adds is that its memory of the identity
is finite and partitioned by region, credential and endpoint. Bound the same-key retry loop well inside the
documented retention and assert that bound against the provider's own number. Past it, stop, mark the attempt
UNKNOWN, and resolve by reading. Never mint a fresh identity for the same intent.

### Finality is a property of the rail, not of your API surface

Specialises *hard limits*: on an irreversible rail the only control is what happens before the send, so verify
the destination and bound the amount, blocking rather than warning. Expose "request return of funds", never
"cancel" or "reverse", and model the receiving party's right to refuse as a first-class outcome with its own
state. An interface offering an unwind that does not exist teaches operators and downstream systems that the
money is recoverable, and the modelling error becomes a support promise you cannot keep.

### The settlement record is the money; everything else asserts things about it

Specialises *reconciliation*: the comparison ships as a scheduled entrypoint, reads through a path independent
of the writer, and fails closed to a destination with no default. Two things differ here. Join on the
counterparty's own identifier, never on yours, because yours is a grouping attribute you chose and can repeat.
And close the books on settlement data with the rail's reversal tail, never on payment-object state, or late
reversals land against a period you already closed.

## References

Each row is a standing instruction: when its predicate holds, read the file and apply it. Do not summarise it.

| file | read it when |
|---|---|
| [processor-lifecycles.md](references/processor-lifecycles.md) | the change names `PaymentIntent`, `requires_capture`, `capture_method`, `capture_before`, `increment_authorization`, `multicapture`, `final_capture`, an Adyen result code or modification endpoint (`/captures`, `/cancels`, `cancelOrRefund`), or an `Idempotency-Key`, `PayPal-Request-Id`, replay or 409 branch |
| [webhooks.md](references/webhooks.md) | the change defines or edits a route that receives provider events, or names `Stripe-Signature`, `construct_event`, `constructEvent`, `HmacValidator`, `notificationItems`, `[accepted]`, `processed_events`, or a sweeper over changed objects |
| [refunds-and-disputes.md](references/refunds-and-disputes.md) | the change contains `refund`, `Refund.create`, `/refunds`, `refundable`, `dispute`, `chargeback`, `charge.dispute.`, `NOTIFICATION_OF_CHARGEBACK`, `evidence`, or an early-fraud-warning handler |
| [settlement-and-reconciliation.md](references/settlement-and-reconciliation.md) | the change reads `balance_transaction`, `reporting_category`, `payout`, `transfer_group`, `source_transaction`, a settlement or payout report, an ARN, or closes an accounting period |
| [rails.md](references/rails.md) | the code names a rail or scheme message: `ACH`, `nacha`, an R-code (`R01`, `R10`), `SEPA`, `pain.001`, `pacs.008`, `pacs.004`, `camt.056`, `EndToEndId`, `TxId`, `UETR`, wire, `RTP`, `FedNow` |
| [controls-and-evidence.md](references/controls-and-evidence.md) | the task is a review or a ship decision on a payments path, or you need the test property for a control you are about to claim |

## Output

When the change is economic, open with two fields on one line, and omit the line entirely when it is not:

```
authority: EXTERNAL (Stripe) · exposure: customer
```

The usual pair here is authority EXTERNAL, the processor, and exposure customer. Exposure becomes `record` when
your books rather than the processor's report are what other systems consume. Authority becomes SELF only for a
quantity no processor report can contradict, such as an internal store credit.

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
sentences saying so and why the change is safe. Never emit a slot for a concept the change does not touch.

A control you claim points at executable code that a value-moving path reaches. A comment, a TODO, a design
note or an uncalled helper **is** the missing control, and anything absent is reported as
`UNRESOLVED: <control> (<why>)`, never as a completed row.

Add the control table from [controls-and-evidence.md](references/controls-and-evidence.md) only when exposure is
`record`, when a payout, withdrawal or crediting path changes, when two or more processors or rails are in
scope, or when the change touches three or more of the invariants above at once.
