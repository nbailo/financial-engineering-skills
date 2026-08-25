---
name: fin-payments
description: >-
  Financial correctness for payment processor and rail integrations: authorization and capture,
  refund ceilings, disputes and chargebacks, payouts and transfers, webhook ordering and replay,
  fee treatment and settlement reconciliation. Use when building or reviewing an integration
  with Stripe, Adyen, PayPal, Square or Modern Treasury, or with ACH, SEPA, wire, RTP,
  FedNow and pain.001. For balances and postings use fin-ledger.
license: MIT
---

# The object lifecycle belongs to the processor and reaches you out of order

You observe a distributed state machine that someone else runs, through three mutually inconsistent channels:
the synchronous response, the pushed event, and the settlement report. Two disciplines follow, and every rule
below is mechanism for one of them: never let an absence of information become information, and never treat a
notification as state, treat it as a trigger to go read state. `fin-ledger` owns what your books record once
the processor's fact lands; load it too when a capture, refund or dispute becomes a posting.

## Workflow

1. Name the economic effect and the party who is out of pocket if it goes wrong: the payer, the merchant, the
   platform, or a connected account.
2. Map the lifecycle you do not own. Enumerate the legal transitions of every object you mirror, and mark
   which corrections each of its terminal states can still receive.
3. Treat every push as a notification. Re-read the object from the processor before any decision that moves
   value.
4. Establish identity, ordering and dedupe per object, with the guard being the write.
5. Compute every ceiling from the processor's own numbers, net of everything in flight.
6. Decide fee, currency and reversal treatment, and where each of them lands in your books.
7. Reconcile against the settlement report, which is the money, joined on the processor's own identifier.
8. Load only the processor or rail references this implementation needs, then implement the controls and
   their tests before calling the path complete.

## When this applies

The code hands an instruction to a counterparty that moves other people's money and then mirrors objects whose
lifecycle it does not control: an authorization that may or may not become money, a reversal with a lifecycle
of its own, a counterparty-initiated debit against value you already booked, a disbursement to a third party,
and a periodic report stating what actually settled. Anywhere your copy of one of those objects drives a
fulfilment, a credit or a further payment, this skill applies.

These literals are routing hints for the economic-diff gate, not the definition. The diff imports or calls
`stripe`, `adyen`, `braintree`, `paypal`, `squareup`, `checkout`, `plaid`, `moderntreasury`; or names
`PaymentIntent`, `charges.create`, `Refund.create`, `capture`, `dispute`, `chargeback`, `payout`,
`transfer_group`, `pspReference`, `merchantReference`, `Idempotency-Key`, `balance_transaction`; or defines a
route that receives provider events; or reads a settlement, balance-transaction or payout report; or names a
rail (`ACH`, `nacha`, `SEPA`, `pain.001`, `pacs.008`, wire, `RTP`, `FedNow`).

Not this skill. A table of entries, postings, journals or balances that you own, and the debit/credit rules
over it, go to `fin-ledger`. Orders and fills on a trading venue go to `fin-exchange-integration`. Chain
deposits, confirmations and withdrawals go to `fin-onchain`.

## Core rules

### A pushed event names an object; only the authority holds that object's state

A notification says something about an object may have changed. It does not say what the object is now. Every
decision that moves value, attributes money, or releases goods reads the object from the party that owns it,
at the moment you act.

**Shape**

```
verify authenticity -> read object from its authority -> decide -> write
                          (never from the pushed payload)
```

The payload is a snapshot rendered when the event was queued, under whatever schema was current then, and the
queue can be days deep. Attribution fields copied off it describe a past version of the object. Two events for
the same object can be in flight at once, so the payload is not even a consistent view of one instant. This is
*arrival order is not occurrence order*, in processor terms.

**How it appears**: between the signature check and the first write, the handler re-reads the object from the
processor and uses that response, not the pushed payload, for every value-moving decision. The check is that a
read of the authority occurs on that path, whatever the call is spelled: `stripe.Refund.retrieve(id)`,
`stripe.PaymentIntent.retrieve(id)`, `stripe.Charge.retrieve(id)`, Adyen's payment-details endpoint, or the
same read behind an internal port that wraps them. Make every ledger move, every fulfilment and every order
attribution from that response, never from `event.data.object`. Stripe Event objects are immutable and
rendered at the account's API version *at event time*; with a 3-day retry horizon the payload can be
arbitrarily stale by the time you process it, and `metadata` read off the payload attributes money to whatever
the object looked like when the event was queued. Each processor says the same about its own synchronous
response. Adyen: *"The status of a payment can sometimes change after you get the result code, so we recommend
that you do not use the result code to update your order management system."*

### Ordering is per object, and the guard is the write

Order effects by the object's own identity and the authority's own sequence value, never by the order in which
deliveries reached you. Where that sequence value is coarser than the event rate, the guard must also carry
the identities already applied at that value.

**Shape**

```
per object id: {last applied sequence, ids applied at that sequence}
admit when sequence > last, or sequence == last and this id is unseen
UPDATE watermark WHERE object_id = :id AND (sequence, id) not yet applied
proceed only on rowcount 1, in the transaction that applies the effect
```

A comparison that rejects at equality drops every later sibling sharing the coarse value, and the dropped
sibling is usually the one that says the money actually moved. The object then sits in a non-terminal state
forever, with no error and no log line.

**How it appears**: persist a per-object watermark keyed on the object id (`re_…`, `pi_…`, `ch_…`) holding
the last applied `created` **and the set of event ids already applied at that `created`**. Stripe's `created`
is second-granularity, and `refund.created` and `refund.updated` on the same `re_…` routinely share a second,
so a bare `if event.created <= wm: return` discards the `succeeded` event and the refund is **pending
forever**. Admit an event at the same `created` unless its id is already in the persisted set. Event-id dedupe
cannot substitute for this: a late `refund.created` carries a fresh `event.id` your `processed_events` table
has never seen, and it re-arms the money branch. Never order by the `Stripe-Signature` timestamp. It is
regenerated per delivery attempt.

### Durably record what you applied, never that you saw it

The dedupe record exists to prevent a second application of an effect that happened. It must not become a
record that you once received something you never applied, because that silently cancels the counterparty's
only recovery mechanism.

**Shape**

```
applied:      effect + dedupe row, one transaction
unresolvable: dead-letter row + alert, dedupe row NOT written, redelivery still reaches you
```

An event you could not resolve (unknown object, missing order, a dependency not yet created) is a hole in your
state, not a processed event. Committing it to the dedupe table stops all redelivery and makes the miss
permanent, so the dedupe mechanism works against recovery instead of for it.

**How it appears**: insert the row into `processed_events` **only after the effect was actually applied, in
the same transaction as the effect**. Route the unresolvable event to a dead-letter table with an alert and do
**not** mark it processed.

### A ceiling is computed from the authority's own numbers, net of everything in flight

The maximum you may return, release or disburse derives from what the authority says actually settled, minus
completed reversals, minus reversals in flight, minus anything currently under counterparty claim. Your own
order record is not an input.

**Shape**

```
ceiling = authority.settled_amount
        - reversals completed - reversals in flight - amount under claim
refuse while any claim on this object is open
```

Anything in flight that is not counted reserves nothing, so two concurrent reversals each pass a ceiling check
that neither would pass if the other were visible. Computing from your own order total instead of the
authority's settled amount refunds money that was never taken.

**How it appears**: compute `refundable = captured_amount − already_refunded − pending_refunds −
disputed_amount`, per charge and per currency, **in your own code**, from the charge's `amount_captured`,
never from `paymentIntent.amount`, never from your own `orders.amount_cents`. Count `pending` refunds against
the ceiling, not only `succeeded` ones. **Refuse the refund while any dispute on that charge is open** and
while another refund on it is `pending`. Stripe: *"You can't issue a refund outside the dispute process while
the dispute is open"*, and for bank-debit methods (SEPA Direct Debit, Bacs, ACH Direct Debit, ACSS, AU BECS,
NZ bank account debits), *"there's a risk of double refund… the customer might receive two credits for the
same transaction."* The failure this ceiling exists to catch has a name: the Revolut US acquiring loss
(exploited 2022, ~$23M gross / ~$20M net) refunded declined transactions out of the firm's own funds, and was
found by a partner bank's cash-position report rather than by any internal control (FT, reported via Payments
Dive). See [refunds-and-disputes.md](references/refunds-and-disputes.md).

### A reversal is not the mirror image of the original

Undoing an economic event does not undo every leg of it. The cost of the original stays incurred, the reversal
can carry a cost of its own, and a reversal of a split payment reverses each share proportionally rather than
wholesale.

**Shape**

```
original: principal in, cost expensed
reversal: principal out, original cost still expensed, reversal cost possibly added
```

Reversing the original's full group creates a permanent, silent, per-reversal gap. Nothing in the application
notices it, because both sides of your own entry balance. It surfaces only in the settlement reconciliation.

**How it appears**: a refund returns the **principal**. Stripe, verbatim: *"Stripe's processing fees from the
original transaction aren't returned"*, and a refund fee may be charged on top. The ledger group for a refund
therefore reverses the principal leg and **leaves the fee expensed**. `refund_application_fee` and
`reverse_transfer` on a Connect charge are **proportional**: a partial refund reverses proportionally. Refunds
draw on your available Stripe balance (*"not including pending amounts"*), so a refund can itself go pending,
or fail outright on a non-card method, when the balance is short.

### A terminal state of someone else's object is not terminal

Enumerate the legal `(state, event)` pairs for every object you mirror and reject everything else with an
explicit error, never silence. A terminal state accepts exactly the corrections by which the counterparty
fixes a fact you already booked, and nothing else. A status message never re-opens it.

**Shape**

```
legal(state, event)? -> no  -> explicit rejection, alerted
                     -> yes -> apply as a NEW balancing entry against the closed record
```

The correction arrives after you have written the outcome off, sometimes months after. If your model treats
the first terminal state as final, the correction is either dropped silently or crashes the handler, and both
outcomes lose the money quietly.

**How it appears**

- A refund reports `succeeded` and can still move to `failed` afterwards. The status set is `pending`,
  `succeeded`, `failed`, `canceled`, `requires_action`, and a refund can cycle back to `requires_action` from
  `pending` when the customer's bank returns the funds. Adyen's equivalents are `REFUND_FAILED`, arriving days
  after an initially successful refund, and `REFUNDED_REVERSED` when the shopper's bank details were invalid.
- Dispute outcomes are not immutable. `lost` can flip to `won` ("late wins"), and
  `charge.dispute.funds_reinstated` must be accepted after you have already written the loss off. Multiple
  disputes per payment are possible.
- The correction lands as a **new balancing entry on a transaction you already closed**, never as an edit to
  the original.

### The identity you send the counterparty expires and is scoped

*Durable intent before the external effect* requires an identity minted from the intent instance and committed
before the call. What a processor adds is that its memory of that identity is finite and partitioned, so the
retry window is bounded by wall clock and by the scope the identity was minted under.

**Shape**

```
mint from intent -> COMMIT -> call with identity -> record outcome
past the retention bound: stop, mark UNKNOWN, query by your own reference. Never mint a new identity.
```

Once retention expires or the request crosses the scope boundary, the counterparty no longer recognises the
identity, and a resend is a fresh instruction, not a retry. An ambiguous response inside the window is
UNKNOWN and is resolved by reading, never by writing again.

**How it appears**: the key does not survive retention expiry (Stripe ≥24 h, Adyen ≥7 d, PayPal capture 6 h,
Open Banking 24 h, AWS Powertools 1 h default), cross-region failover (Adyen: keys *"will not be checked for
duplication in other regions"*), the rate limiter (429), the auth layer (401), most validation errors (400),
or a different processor. **500s ARE cached** and mean indeterminate. Stripe states there is no client-side
algorithm that resolves it, and Stripe's back office may roll the charge forward to the network afterwards,
surfacing the object only by webhook.

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
- Send an identity on the reversal call too, not only on the original. Generate a fresh one only after a
  **non-409 4xx**, a validation error the processor rejected before its idempotency layer. After a timeout, a
  socket close or a 5xx, the same key is re-sent unchanged.

### Finality is a property of the rail, not of your API surface

Some instructions cannot be undone by any message. For those, the only control is what happens before the
send, and the interface you expose must not promise an unwind that does not exist.

**Shape**

```
irreversible rail: validate destination -> send -> (no reversal exists)
recall attempt   : a NEW request the receiving party may refuse
```

An interface offering "cancel" or "reverse" over a final rail teaches operators, and every downstream system,
that the money is recoverable. It is not, and the modelling error becomes a support promise you cannot keep.

**How it appears**: verify the destination before a send on any irreversible rail. Wires, RTP and FedNow are
final when recorded and there is no reversal message. A return is a fresh `pacs.004` that the receiving
institution may decline outright (`RJCR`: the funds will not be returned). Expose "request return of funds",
never "cancel" or "reverse", and model the counterparty's right to refuse.

### The settlement record is the money; everything else is an assertion about it

Every economic quantity you report names an external authority, a join key, and a scheduled comparison that
runs in production. Join on the counterparty's own identifier, never on yours, and close on the settlement
record rather than on lifecycle state.

**Shape**

```
scheduled job -> read authority's settlement record (independent of the writer)
              -> join on THEIR identifier -> compare -> aged break buckets -> alert
cursor advances only over a report fully ingested
```

Your identifier is a grouping attribute you chose and can repeat. Their identifier is the one the money moved
under. Closing on lifecycle state closes before the reversal tail has run out, so late reversals land against
a closed period. This is *reconciliation runs in production*: the same comparison written as SQL in a comment
counts as absent.

**How it appears**: ship a **scheduled entrypoint** that joins the processor's settlement record (Stripe
balance transactions, using `reporting_category`, not `type`; or Adyen's Settlement details report) against
your own records on the **processor's own identifier**: `pspReference` / `balance_transaction.id`.
`merchantReference` is **not unique** and is a grouping attribute only. Stripe's `adjustment` type is
overloaded across dispute debits, dispute reversals and refund failures, disambiguated only by `description`.
**Parse it, do not infer.** Close the books on settlement data with a reversal tail, never on payment-object
state: cards ≥120 days (180 for many local methods, and *from the event date* for future-dated services), ACH
unauthorized returns 60 calendar days, refund failure up to 30 days, dispute late-wins unbounded. The alert
destination is a config key with no default that raises at import if unset. The report importer obeys *proven
coverage before the cursor advances*: a truncated page, a provider range rejection, or a report still being
written is a hole, not an empty day.

## Lifecycle detail by object

Layer-3 material. Each item instantiates a rule above; none of them replaces one.

### A held amount is not money, and taking part of it can destroy the rest (authorize and capture)

**Cancel, do not refund, an intent that has not captured.** Stripe: *"the charge attached to the PaymentIntent
remains uncaptured and can't be refunded directly. You must cancel the PaymentIntent."* A refund against
uncaptured funds returns money that was never taken. Cancellable statuses are `requires_payment_method`,
`requires_confirmation`, `requires_action`, `requires_capture`, and `processing` for US bank accounts.

**A partial capture destroys the remainder.** The uncaptured balance is *released, not held*, and cannot be
captured later, unless multicapture is enabled (Stripe) or Adyen Support has enabled multiple partial captures
on the account. The same API call has opposite consequences depending on account configuration that is not
visible in the diff, so the code asserts the mode it requires rather than assuming it. Under Stripe
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
`type=refund` to "the customer was refunded" reports refunds that never happened and double-counts revenue
reductions.

### A reversal has a lifecycle of its own, and its failure reasons are branches (refund)

**The creation of a reversal is not its completion.** Money is not gone until the refund reports `succeeded`;
`refund.created` means pending, not paid. Stripe: *"the bank returns the refunded amount to us and we add it
back to your Stripe account balance. This process can take up to 30 days from the post date."*

**The authority's stated reason for a failed reversal selects your next action, so it is a branch, not a log
line.** The verified `failure_reason` set is `charge_for_pending_refund_disputed`, `declined`,
`expired_or_canceled_card`, `insufficient_funds`, `lost_or_stolen_card`, `merchant_request`, `unknown`.
`charge_for_pending_refund_disputed` is the dispute-landed-while-refund-pending race, and Stripe's guidance
there is to accept or challenge the dispute rather than reissue the refund.

**A refund issued shortly after the charge may be processed as a reversal.** The original charge drops off the
statement, no credit line appears, **no ARN is produced**, and network fees differ. Support copy promising
"a credit in 5–10 days" is wrong for reversals, and reconciliation keyed on ARN finds nothing.

### A counterparty debits value you already booked, on a clock you do not control (dispute and chargeback)

**Two amounts that reached you over different network hops for the same economic event are not equal by
construction.** Do not assert `dispute.amount == charge.amount`. FX drift between purchase and dispute, issuer
aggregation of several recurring charges into one dispute, partial disputes, and full disputes of partially
refunded charges all break it. **The assertion crashes the handler, so the dispute goes unanswered past its
evidence window and is lost by default.** The disputed amount crossed a network: it is an operating error
wearing a programmer-error costume, and it needs a fail-closed guard, not an `assert`. Store the dispute
amount and currency separately from the charge amount and currency.

**Adyen's sequence** is `NOTIFICATION_OF_CHARGEBACK` → `CHARGEBACK` (funds debited) → `CHARGEBACK_REVERSED`
(funds restored), with `SECOND_CHARGEBACK`, `PREARBITRATION_WON` and `PREARBITRATION_LOST` beyond it. On a
Stripe loss no money moves at close: Stripe credited the issuer when the chargeback was initiated. Evidence
submission is one-shot and non-editable.

### Money leaving to a third party, whose recovery is a claim rather than a certainty (payout and transfer)

**Returning value you already disbursed onward requires reversing the onward disbursement in the same unit
of work.** In a marketplace the onward disbursement is the connected-account transfer, so the refund and its
reversal commit together. Stripe: *"Stripe debits your platform for refunds to destination charge or separate
charge and transfer payments. Reverse the transfers associated with these charge types to recover the refund
amount from your connected accounts."* A refund without the reversal takes the money from the platform.

**A grouping attribute the counterparty offers for reporting creates no economic linkage; only a reference
the counterparty's own settlement record joins on does.** How it appears: `transfer_group` is a reporting
label, *"it doesn't affect any standard functionality"*, causing no reversal and joining nothing at
settlement; only `source_transaction` creates a real dependency.

**Never create a transfer against a payment whose method settles asynchronously** (ACH, SEPA Direct Debit)
until it has settled: *"Stripe doesn't automatically reverse a transfer if the associated async payment
fails… your platform's balance is debited."* Transfers are not auto-retried, and a transfer reversal can
itself fail for lack of funds on the connected account. Model clawbacks as receivables, not as guaranteed
recoveries.

## Seam S1: payments and ledger

*This is the processor half of the boundary. `fin-ledger` owns what the books record; load it too when the
refund becomes a posting.*

Every payment state transition emits **exactly one balanced ledger transaction** whose id derives from the
payment's idempotency key. Every clearing account between payment states returns to zero, monitored as a
continuous assertion. **Never derive a balance by scanning payment objects.** Authorizations are reserved
amounts in the payments layer, not ledger entries; only captures, refunds, disputes, fees and settlement
adjustments post.

## Output

Default for every economic change at T0 and T1. Seven labels, this order, one line each except `controls`.

```
FINANCIAL CHECK
tier:       T<n>, and the signal that placed it there
effect:     what moves value, from whom to whom, in what unit
identity:   the stable identity of the intent, durably recorded at file:line
ambiguity:  which processor responses are UNKNOWN, and how they resolve
authority:  whose copy of each quantity is the record
recovery:   what a crash or restart between the call and the local commit does
controls:   <control> -> <file:line>, one per line; at T2 and above also `· <test name>`
            UNRESOLVED: <control> (<why>), for anything not implemented
```

`controls` is where *implemented, not described* is enforced below T2: every control you name is either a real
`file:line` or an explicit `UNRESOLVED:` line. A described control with no location is a defect. Any property
asserted in a comment or docstring on this path is either named here with the test that proves it, or deleted
(*a comment is a claim*).

### T2 and above

A payments integration is T2 by default: there is a customer or a connected account on the other side of the
money, a crediting webhook or a payout path is usually present, and someone else eats the error. A
single-merchant sandbox integration with no live credential path is not T2, and neither is a read-only report
importer over your own transactions.

At T2 and above, add the block below to the `FINANCIAL CHECK`, emitting **only the rows whose predicate this
diff matches**. Each emitted row carries a real `file:line`; a row you cannot fill is work not yet done, not a
row to delete. Every control the diff needs but the table does not match is covered by the `controls:` line.

**PAYMENTS CONTRACT**

| risk (emit when the predicate holds) | implemented at file:line | test name |
|---|---|---|
| refund ceiling from the authority's captured amount, net of reversals in flight and open claims (`amount_captured` less `pending` refunds and disputes), when the diff issues or sizes a reversal | | |
| the object re-read from its own authority inside the handler, before the first value-moving decision (a `retrieve` call), when the diff handles a pushed event | | |
| per-object watermark on the authority's own sequence value plus the identities already applied at that value, the guarded `UPDATE` being the write (`(created, applied_event_ids)`), when the diff handles a pushed event | | |
| unresolvable event dead-lettered and alerted, not marked processed, when the diff handles a pushed event | | |
| reversal ledger group reverses principal and leaves the fee expensed, when the diff posts a reversal or a fee to the books | | |
| scheduled settlement-report reconciliation, alert destination a config key with no default, when the diff writes, reports or closes a settled quantity | | |
| onward-disbursement reversal in the same unit of work, when the diff creates or reverses one (a `Transfer`) | | |
| destination verification before send, when the send is over an irreversible rail (wire, RTP, FedNow) | | |

At T3, add the per-technique evidence table that `fin-verification` defines, and load that skill alongside
this one.

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
