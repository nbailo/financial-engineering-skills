# Rail reversibility

Reversibility and its window, per rail, as a lookup. Everything here supports classifying a rail before the
send path is written: who can pull money back, on what clock, and whether the counterparty is obliged to
return it. A rail whose far side is not obliged is irreversible in every sense that matters to code,
regardless of whether a request message exists.

## Contents

- Reversibility and window by rail: a single classification table
- Card: authorization holds, capture as the money-moving event, refund vs reversal, chargeback windows
- ACH: R-code taxonomy; `R10` unauthorized at 60 calendar days after settlement; administrative returns
  (`R01`, `R02`–`R04`, `R09`) within 2 banking days
- ACH provisionality: received funds are not final for the return window, and what that blocks downstream
- SEPA: Reject vs Return vs Recall vs RFRO (who initiates each, when, and what each obliges)
- Wire, RTP and FedNow: finality on record, `pacs.004` as a fresh payment, `RJCR` refusal
- Destination verification before an irreversible send

---

## Reversibility and window by rail

The three columns that decide the send path are **who can initiate a pull-back**, **on what clock**, and
**whether the far side is obliged to comply**. A rail where the third column is "no" is irreversible in every
sense that matters to code, regardless of whether a request message exists.

| Rail | pull-back mechanism | initiated by | clock | far side obliged? |
|---|---|---|---|---|
| Card: chargeback | issuer debit, already executed when you hear of it | cardholder → issuer | ~120 days; 180 for many local methods; **from the event date** for future-dated services | you may contest, once |
| Card: refund | new credit transaction (or a reversal, below) | you | your choice, subject to the ceiling | n/a |
| ACH debit: unauthorized consumer | return | Receiver's RDFI | **60 calendar days** after settlement | yes, within the window |
| ACH debit: administrative | return (`R01`, `R02`–`R04`, `R09`) | RDFI | **2 banking days** of the settlement date | yes, within the window |
| SEPA SCT: Reject | pre-settlement refusal | any PSP in the chain | before inter-PSP settlement | n/a: never settled |
| SEPA SCT: Return | post-settlement credit back | Beneficiary PSP | scheme-defined | yes, when the PSP returns |
| SEPA SCT: Recall / RFRO | request to the Beneficiary PSP | Originator PSP / Originator | Beneficiary PSP must **answer**, not return | **no** |
| SEPA SDD: refund | pull-back right of the debtor | Debtor | longer than a card dispute for unauthorised collections | yes |
| Fedwire / wire | none in the protocol | n/a | n/a | **no**: recovery is legal, not technical |
| FedNow | `camt.056` return request → fresh `pacs.004` | sender's participant | return requests **within 60 calendar days** of settlement; RFP-warranty claims **95 days**; `FRAD` and `WNTB` exempt from the 60-day guideline | **no**: `RJCR` is a valid answer |
| RTP (TCH) | request for return, same shape as FedNow | sender's participant | *not sourced in this pass; do not hard-code a number* | **no** |

Sources: card windows and the reversal case, Stripe docs; ACH windows, Nacha Operating Rules **via a secondary
page** (`nacha.org` returns 403 to automated fetch; the 60-calendar-day and 2-banking-day figures are
corroborated but not read from the primary rulebook); SEPA taxonomy, EPC SCT Rulebook EPC125-05 and
EPC135-18 v6.0; FedNow, *FedNow Service Operating Procedures*, 24 June 2025.

**The EPC's "Beneficiary PSP answers a Recall/RFRO within 15 banking business days" figure is UNVERIFIED**;
it comes from a guidance-document abstract, not the rulebook PDF. Ship the taxonomy; read the day count out of
config, not out of a constant in your code.

## Card

Authorization is a **hold that expires**; capture is the money-moving event. The capture rules are their own
subject: cancel rather than refund an uncaptured intent, partial capture releases the remainder, and
`capture_before` is read from the response. Two rail-level facts belong here:

**A refund issued shortly after the charge may be processed as a reversal, not a credit.** The original charge
drops off the cardholder's statement, no separate credit line appears, **no ARN is produced**, and network fees
differ. Any reconciliation keyed on ARN finds nothing for these; any support copy promising "a credit in 5–10
days" is wrong. Model reversal-shaped and credit-shaped refunds as distinct, because their reconciliation join
keys differ.

**Clearing records need not match an authorization 1:1.** Force posts arrive with no matching auth; late
presentments arrive days after it; captured amounts differ from authorized (tips, overcapture, incremental).
Matching keyed on `(card, exact_amount, same_day)` produces false unmatched records and, worse, false
*matches* across two similar transactions. Match on the processor's own identifier and treat amount/date as
attributes to reconcile, not as the key.

## ACH: the R-code taxonomy

ACH is **pull-based** and returns arrive after the fact on a per-code clock. The two clocks that decide your
downstream design:

| Code | Nacha title | Window | Class |
|---|---|---|---|
| `R10` | "Originator not known and/or not authorized to Debit Receiver's Account" *(verbatim)* | **60 calendar days** after settlement | unauthorized |
| `R01` | insufficient funds (NSF) | **2 banking days** of the settlement date | administrative |
| `R02` | *title not established by the research read for this file* | 2 banking days | administrative |
| `R03` | *ditto* | 2 banking days | administrative |
| `R04` | *ditto* | 2 banking days | administrative |
| `R09` | *ditto* | 2 banking days | administrative |

Codes `R02`–`R04` and `R09` are grouped with `R01` as administrative returns on the 2-banking-day clock; their
verbatim Nacha titles were not obtained from a primary source in this pass, so they are left unfilled rather
than guessed. **Do not write a hard-coded `R29` 60-day rule**; `R29` is a corporate return on the
2-banking-day clock, and the widely circulated "R10/R29 both 60 days" pairing inverts it.

Nacha's unauthorized-return-rate threshold is **0.5% on a rolling 60-day basis** (same secondary provenance as
the windows above). If your product can generate debits at scale, that ratio is a production metric with a
kill-switch attached, not a compliance footnote.

## ACH provisionality

Funds "received" over ACH are **provisional for the full return window**, two months for unauthorized
consumer debits. Everything downstream of an ACH credit must be gated on that, and the gate is a state, not a
comment:

- **Do not create a connected-account transfer against an ACH-funded charge.** Stripe: *"Stripe doesn't
  automatically reverse a transfer if the associated async payment fails… your platform's balance is
  debited."* `source_transaction` with a delayed-notification method produces exactly this. The failure lands
  60 days later as an `R10` return, the transfer is not auto-reversed, and the recovery is a clawback
  receivable against a connected account that may be empty.
- **Do not release irreversible value inside the window** (a wire or FedNow send out, a crypto withdrawal, a
  gift-card issuance) funded by an ACH credit whose return clock has not expired. That composition converts a
  reversible inbound leg into an irreversible outbound one and is the shape of nearly every ACH-funded fraud
  loss.
- **Do not close an accounting period on ACH payment state.** The reversal tail is ≥60 days; the period close
  reconciles against the settlement record with that tail, and a return lands as a **new** balancing entry on
  a transaction you already closed, never as an edit.

Represent this as two fields, not one: the payment's lifecycle state (`succeeded`) and its economic finality
(`provisional_until = settlement_date + 60 days`). Code that reads `status == 'succeeded'` as "the money is
ours" has no way to express the sixty days in between.

## SEPA: Reject vs Return vs Recall vs RFRO

Four different things, routinely collapsed into one "cancel" button.

| R-transaction | Initiated by | When | What it obliges |
|---|---|---|---|
| **Reject** | any PSP in the chain | **before** inter-PSP settlement | nothing to return: the payment never settled |
| **Return** | Beneficiary PSP | **after** settlement | the Beneficiary PSP moves the money back |
| **Recall** | Originator PSP | after settlement | the Beneficiary PSP must **answer**; it is **not obliged to return** |
| **RFRO** | Originator (the customer), via its PSP | after settlement | a request for a Recall; same non-obligation |

SDD (pull) is a separate taxonomy (refunds, returns, rejects, refusals, reversals) with debtor refund rights
for unauthorised collections that run considerably longer than a card dispute.

The engineering consequence: a Recall has a **three-valued, non-terminal outcome**, and the UI must never show
the payment as cancelled until the answer arrives.

## Wire, RTP and FedNow: finality on record

FedNow *Operating Procedures* (24 June 2025), verbatim: a transaction **"settles with finality when the FedNow
Service records the debit and credit."** There is **no reversal primitive**. A return is a *new* `pacs.004`
payment. A return is *requested* with `camt.056`, and the receiving participant may answer:

| Answer | Meaning |
|---|---|
| `IPAY` | "the result of an investigation is, or will be, the initiation of a payment instruction"; accepted |
| `PECR` | "a requested cancellation has been partially executed" |
| `PDCR` | "a requested cancellation is pending" |
| `RJCR` | **"Return request rejected and funds will not be returned."** |

`WNTB` (warranty-breach) return requests **"are not cancellation requests for purposes of UCC Article 4A"**;
`FRAD` and `WNTB` are exempt from the 60-day return-request guideline.

**`ACWP` (Accept Without Posting)** lets a receiving participant settle with finality while withholding
availability from its customer pending screening, with a **next-business-day-midnight** deadline to post or
return. A two-state payment model cannot represent `ACWP`, and `ACWP` is precisely the state where money has
settled and nobody's customer has it.

**Fedwire's ISO 20022 migration was a single-day, no-fallback cutover: go-live 14 July 2025, legacy FAIM format
"no longer support[ed]" from that date** (Federal Reserve Financial Services, Fedwire ISO 20022 timeline).
There was no coexistence period on Fedwire Funds.

Expose **"request return of funds"**, never "cancel" or "reverse", and model the counterparty's right to
refuse as a first-class outcome with its own state and its own P&L consequence.

## Destination verification before an irreversible send

Structural validation (IBAN check digits, a routing-number checksum, schema validation of the message)
proves the string is **well-formed**. It does not prove the account belongs to the intended beneficiary, and
on an irreversible rail the difference is the entire loss. The 3Q2025 code set added six Verification-of-Payee
statuses to `ExternalPaymentTransactionStatus1Code` (`RCVC`, `RVNA`, `RVNM`, `RVMC`, `RVNC`, `RVCM`) alongside
confirmation-of-funds (`ACFC`, `ACFW`); their individual verbatim definitions were not read in this pass, so
branch on them from the published code-set file at your target scheme's version rather than from memory.

Two guards belong in the send path itself, both motivated by Citibank/Revlon (11 August 2020: ~$894M of
principal wired when ~$7.8M of interest was due; the wires settled with finality; recovery turned on the
*discharge-for-value* doctrine and two years of litigation, not on any protocol undo):

1. **Confirm on the derived money movement, not on the operator's inputs.** The confirmation surface renders
   the computed debits and credits (beneficiary, account, currency, amount, value date) as they will be
   serialised into the message. Citi's six-eye process passed because the screen showed intent, not effect.
2. **Assert a hard maximum-plausible-amount bound before submission, and block rather than warn.** The Revlon
   payment was ~100× the scheduled interest on a facility not due for three years; a bound derived from the
   payment's own schedule would have refused it. A bound that merely raises a review flag is a bound that a
   busy operator clears.

Finality is a **legal** predicate. Any design whose recovery story is "we'll just reverse it" is relying on an
outcome it does not control.
