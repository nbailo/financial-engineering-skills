# Rails

Reversibility and its window, per rail, as a lookup. The single load-bearing sentence lives in SKILL.md —
verify the destination before a send on an irreversible rail — and everything here supports classifying a
rail before the send path is written: who can pull money back, on what clock, and whether the counterparty is
obliged to return it. Also carries ISO 20022 message-level identity, because the correlation key for a return
is decided when the original is minted.

## Contents

- Reversibility and window by rail: a single classification table
- Card: authorization holds, capture as the money-moving event, refund vs reversal, chargeback windows
- ACH: R-code taxonomy; `R10` unauthorized at 60 calendar days after settlement; administrative returns
  (`R01`, `R02`–`R04`, `R09`) within 2 banking days
- ACH provisionality: received funds are not final for the return window, and what that blocks downstream
- SEPA: Reject vs Return vs Recall vs RFRO — who initiates each, when, and what each obliges
- Wire, RTP and FedNow: finality on record, `pacs.004` as a fresh payment, `RJCR` refusal
- The `pacs.002` status ladder: what each `TxSts` licenses, and why it is an open enum
- ISO 20022 identity: `EndToEndId`, `TxId`, `UETR` — who mints each, `Max35Text`, the UUIDv4 constraint
- Correlating a return to its original: `OrgnlInstrId` / `OrgnlEndToEndId` / `OrgnlTxId` / `OrgnlUETR`
- `MsgId` is per-message and per-hop, and is not a payment identity
- Destination verification before an irreversible send
- Amount precision at serialisation: currency-specific fraction digits and where the remainder is booked

## Reversibility and window by rail

The three columns that decide the send path are **who can initiate a pull-back**, **on what clock**, and
**whether the far side is obliged to comply**. A rail where the third column is "no" is irreversible in every
sense that matters to code, regardless of whether a request message exists.

| Rail | pull-back mechanism | initiated by | clock | far side obliged? |
|---|---|---|---|---|
| Card — chargeback | issuer debit, already executed when you hear of it | cardholder → issuer | ~120 days; 180 for many local methods; **from the event date** for future-dated services | you may contest, once |
| Card — refund | new credit transaction (or a reversal, below) | you | your choice, subject to the ceiling | n/a |
| ACH debit — unauthorized consumer | return | Receiver's RDFI | **60 calendar days** after settlement | yes, within the window |
| ACH debit — administrative | return (`R01`, `R02`–`R04`, `R09`) | RDFI | **2 banking days** of the settlement date | yes, within the window |
| SEPA SCT — Reject | pre-settlement refusal | any PSP in the chain | before inter-PSP settlement | n/a — never settled |
| SEPA SCT — Return | post-settlement credit back | Beneficiary PSP | scheme-defined | yes, when the PSP returns |
| SEPA SCT — Recall / RFRO | request to the Beneficiary PSP | Originator PSP / Originator | Beneficiary PSP must **answer**, not return | **no** |
| SEPA SDD — refund | pull-back right of the debtor | Debtor | longer than a card dispute for unauthorised collections | yes |
| Fedwire / wire | none in the protocol | — | — | **no** — recovery is legal, not technical |
| FedNow | `camt.056` return request → fresh `pacs.004` | sender's participant | return requests **within 60 calendar days** of settlement; RFP-warranty claims **95 days**; `FRAD` and `WNTB` exempt from the 60-day guideline | **no** — `RJCR` is a valid answer |
| RTP (TCH) | request for return, same shape as FedNow | sender's participant | *not sourced in this pass — do not hard-code a number* | **no** |

Sources: card windows and the reversal case, Stripe docs; ACH windows, Nacha Operating Rules **via a secondary
page** (`nacha.org` returns 403 to automated fetch — the 60-calendar-day and 2-banking-day figures are
corroborated but not read from the primary rulebook); SEPA taxonomy, EPC SCT Rulebook EPC125-05 and
EPC135-18 v6.0; FedNow, *FedNow Service Operating Procedures*, 24 June 2025.

**The EPC's "Beneficiary PSP answers a Recall/RFRO within 15 banking business days" figure is UNVERIFIED** —
it comes from a guidance-document abstract, not the rulebook PDF. Ship the taxonomy; read the day count out of
config, not out of a constant in your code.

## Card

Authorization is a **hold that expires**; capture is the money-moving event. SKILL.md carries the capture
rules (cancel rather than refund an uncaptured intent; partial capture releases the remainder; read
`capture_before` from the response). Two rail-level facts belong here:

**A refund issued shortly after the charge may be processed as a reversal, not a credit.** The original charge
drops off the cardholder's statement, no separate credit line appears, **no ARN is produced**, and network fees
differ. Any reconciliation keyed on ARN finds nothing for these; any support copy promising "a credit in 5–10
days" is wrong. Model reversal-shaped and credit-shaped refunds as distinct, because their reconciliation join
keys differ.

**Clearing records need not match an authorization 1:1.** Force posts arrive with no matching auth; late
presentments arrive days after it; captured amounts differ from authorized (tips, overcapture, incremental).
Matching keyed on `(card, exact_amount, same_day)` produces false unmatched records and — worse — false
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
than guessed. **Do not write a hard-coded `R29` 60-day rule** — `R29` is a corporate return on the
2-banking-day clock, and the widely circulated "R10/R29 both 60 days" pairing inverts it.

Nacha's unauthorized-return-rate threshold is **0.5% on a rolling 60-day basis** (same secondary provenance as
the windows above). If your product can generate debits at scale, that ratio is a production metric with a
kill-switch attached, not a compliance footnote.

## ACH provisionality

Funds "received" over ACH are **provisional for the full return window** — two months for unauthorized
consumer debits. Everything downstream of an ACH credit must be gated on that, and the gate is a state, not a
comment:

- **Do not create a connected-account transfer against an ACH-funded charge.** Stripe: *"Stripe doesn't
  automatically reverse a transfer if the associated async payment fails… your platform's balance is
  debited."* `source_transaction` with a delayed-notification method produces exactly this. The failure lands
  60 days later as an `R10` return, the transfer is not auto-reversed, and the recovery is a clawback
  receivable against a connected account that may be empty.
- **Do not release irreversible value inside the window** — a wire or FedNow send out, a crypto withdrawal, a
  gift-card issuance — funded by an ACH credit whose return clock has not expired. That composition converts a
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
| **Reject** | any PSP in the chain | **before** inter-PSP settlement | nothing to return — the payment never settled |
| **Return** | Beneficiary PSP | **after** settlement | the Beneficiary PSP moves the money back |
| **Recall** | Originator PSP | after settlement | the Beneficiary PSP must **answer**; it is **not obliged to return** |
| **RFRO** | Originator (the customer), via its PSP | after settlement | a request for a Recall; same non-obligation |

SDD (pull) is a separate taxonomy — refunds, returns, rejects, refusals, reversals — with debtor refund rights
for unauthorised collections that run considerably longer than a card dispute.

The engineering consequence: a Recall has a **three-valued, non-terminal outcome**, and the UI must never show
the payment as cancelled until the answer arrives. See `camt.029` below for the actual code sets.

## Wire, RTP and FedNow: finality on record

FedNow *Operating Procedures* (24 June 2025), verbatim: a transaction **"settles with finality when the FedNow
Service records the debit and credit."** There is **no reversal primitive**. A return is a *new* `pacs.004`
payment. A return is *requested* with `camt.056`, and the receiving participant may answer:

| Answer | Meaning |
|---|---|
| `IPAY` | "the result of an investigation is, or will be, the initiation of a payment instruction" — accepted |
| `PECR` | "a requested cancellation has been partially executed" |
| `PDCR` | "a requested cancellation is pending" |
| `RJCR` | **"Return request rejected and funds will not be returned."** |

`WNTB` (warranty-breach) return requests **"are not cancellation requests for purposes of UCC Article 4A"** —
`FRAD` and `WNTB` are exempt from the 60-day return-request guideline.

**`ACWP` — Accept Without Posting** — lets a receiving participant settle with finality while withholding
availability from its customer pending screening, with a **next-business-day-midnight** deadline to post or
return. A two-state payment model cannot represent `ACWP`, and `ACWP` is precisely the state where money has
settled and nobody's customer has it.

**Fedwire's ISO 20022 migration was a single-day, no-fallback cutover: go-live 14 July 2025, legacy FAIM format
"no longer support[ed]" from that date** (Federal Reserve Financial Services, Fedwire ISO 20022 timeline).
There was no coexistence period on Fedwire Funds.

Expose **"request return of funds"**, never "cancel" or "reverse", and model the counterparty's right to
refuse as a first-class outcome with its own state and its own P&L consequence.

## The `pacs.002` status ladder

`TxSts` is typed `ExternalPaymentTransactionStatus1Code`, whose entire schema constraint is
`minLength 1 / maxLength 4` — the values live in a separate file ISO republishes quarterly (27 values as of
publication 3Q2025). **It is an open enum. A closed enum compiled into your code will meet a value it has
never seen, and the default branch is where the money goes wrong.** Store unknown codes verbatim, route to a
review queue, and fail *closed*: do not resend, do not credit.

| Code | ISO definition (verbatim) | What it licenses |
|---|---|---|
| `ACSP` | "…the payment instruction has been **accepted for execution**" | nothing economic |
| `ACWC` | "Instruction is accepted but **a change will be made**, such as date or remittance not sent" | re-read the returned `OrgnlTxRef`; your submitted values are not what will happen |
| `ACWP` | "accepted **without being posted to the creditor customer's account**" | interbank leg may be done; beneficiary is not paid |
| `BLCK` | "…funds will **neither be posted to the Creditor's account, nor be returned to the Debtor**" | money in limbo by design — model it |
| `ACSC` | "**Settlement completed.** … **Warning: this status is provided for transaction status reasons, not for financial information**" | the standard itself tells you not to book on it |
| `ACCC` | "Settlement on the **creditor's account** has been completed" | the beneficiary is paid |
| `PDNG` | "…pending. Further checks and status update will be performed" | **non-terminal** — keep the timer running |
| `RJCT` | "Payment instruction has been rejected" | terminal for this instruction |

`ACSP` → `ACSC` → `ACCC` is a three-stage progression and only the last is what a business person means by
"paid". At the **group** level `ACSC` means "settlement on the **debtor's** account has been completed" — the
same four letters at two levels of the same message denote two different events. Group status also carries
`PART` ("a number of transactions have been accepted, whereas another number … have not"), so a bulk file has
a partial outcome that must be resolved transaction-by-transaction.

## ISO 20022 identity: who mints what

`PaymentIdentification7`, verbatim from `pacs.008.001.08.xsd`:

```xml
<xs:complexType name="PaymentIdentification7">
    <xs:sequence>
        <xs:element maxOccurs="1" minOccurs="0" name="InstrId"    type="Max35Text"/>
        <xs:element                             name="EndToEndId" type="Max35Text"/>
        <xs:element maxOccurs="1" minOccurs="0" name="TxId"       type="Max35Text"/>
        <xs:element maxOccurs="1" minOccurs="0" name="UETR"       type="UUIDv4Identifier"/>
        <xs:element maxOccurs="1" minOccurs="0" name="ClrSysRef"  type="Max35Text"/>
    </xs:sequence>
</xs:complexType>
```

Only `EndToEndId` lacks `minOccurs="0"`. **`UETR` is optional at the schema level** and mandatory only by
scheme rule (CBPR+, Fedwire, FedNow, T2). A parser that assumes it is present "because the standard requires
it" is relying on a scheme rule.

| Field | Minted by | Scope | Your role |
|---|---|---|---|
| `InstrId` | instructing agent | **one hop** — legitimately replaced at every hop | never correlate on it alone |
| `EndToEndId` | the **initiating party** (ordering customer) | passed unchanged to the creditor | if you are the corporate, this is your anchor with the beneficiary |
| `TxId` | the **first agent** | unique within the clearing chain | bank-side |
| `UETR` | the instructing agent that **first creates the interbank payment** | end-to-end across all agents and all message types | if you are a bank/PSP you mint it once; if you are a corporate you receive it |
| `ClrSysRef` | the clearing system | — | reference data |

`UUIDv4Identifier` is a *constrained* UUIDv4, verbatim:

```xml
<xs:pattern value="[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}"/>
```

Three traps. **Lowercase only** — any "normalisation" that upper-cases identifiers corrupts the field
(Go/Java/.NET default `String()` output is already lowercase). **The version nibble must be `4`** — a UUIDv5
derived by hashing `(debtor, amount, date, reference)` is rejected, which forecloses the obvious deterministic-
idempotency trick unless you deliberately force the version and variant bits onto the hash output and document
that the value is not random. **The variant nibble must be `8`, `9`, `a` or `b`.**

`Max35Text` is verbatim `minLength 1 / maxLength 35`. A canonical hyphenated UUID is **36** characters, so
`EndToEndId = str(uuid4())` is a schema violation. Strip the hyphens (32) or use a shorter application
reference — never truncate, which destroys uniqueness invisibly until it collides.

The invariant that makes retries safe end-to-end: **the UETR is minted once, written durably before the first
transmission, and reused verbatim on every retransmission — after a timeout, a process crash, or a region
failover.**

```sql
CREATE TABLE outbound_payment (
    uetr             uuid          PRIMARY KEY,          -- minted here, once, before transmit
    end_to_end_id    varchar(35)   NOT NULL UNIQUE,      -- Max35Text; 32-char hex, not str(uuid4())
    instr_id         varchar(35),                        -- ours for hop 1 only
    ccy              char(3)       NOT NULL,
    intrbk_sttlm_amt numeric(18,5) NOT NULL CHECK (intrbk_sttlm_amt >= 0),  -- mirrors the XSD
    intrbk_sttlm_dt  date          NOT NULL,             -- ISODate in the rail's calendar, not now()
    tx_sts           varchar(4),                         -- open enum: store verbatim
    transmitted_at   timestamptz
);
```

```python
uetr = str(uuid.uuid4())            # lowercase, version nibble 4 — never .upper()
e2e  = uetr.replace("-", "")        # 32 chars; str(uuid4()) is 36 and fails Max35Text
with conn.transaction():            # COMMIT before the socket is opened
    conn.execute(INSERT_OUTBOUND, uetr, e2e, ccy, amt, value_date)
transmit(build_pacs008(uetr=uetr, end_to_end_id=e2e, ...))   # retransmit reuses both verbatim
```

## Correlating a return to its original

`pacs.002` (`PaymentTransaction110`), `camt.056` (`PaymentTransaction106`), `camt.029`
(`PaymentTransaction102`) and `pacs.004` (`PaymentTransaction118`) all carry the same quartet — and **every
one of them is optional**, `TxSts` included:

```xml
<xs:element maxOccurs="1" minOccurs="0" name="OrgnlInstrId"    type="Max35Text"/>
<xs:element maxOccurs="1" minOccurs="0" name="OrgnlEndToEndId" type="Max35Text"/>
<xs:element maxOccurs="1" minOccurs="0" name="OrgnlTxId"       type="Max35Text"/>
<xs:element maxOccurs="1" minOccurs="0" name="OrgnlUETR"       type="UUIDv4Identifier"/>
```

Correlation is therefore a **preference-ordered lookup over a set of keys**, and a message that correlates to
nothing is quarantined, never dropped and never auto-applied:

```python
KEYS = ("OrgnlUETR", "OrgnlTxId", "OrgnlEndToEndId", "OrgnlInstrId")  # most→least specific

def correlate(txn) -> Row | None:
    for k in KEYS:
        v = txn.get(k)
        if v and (row := lookup_outbound(k, v)):
            return row
    quarantine(txn, reason="uncorrelated_r_transaction")   # alert; do NOT credit, do NOT resend
    return None
```

`pacs.004.001.10.xsd` contains exactly **one** occurrence of the string `UETR`, at line 942, and it is
`OrgnlUETR`. The return's own end-to-end identity lives in the Business Application Header / scheme fields,
not in `PaymentTransaction118` — the exact CBPR+ rule for it is **unverified here** (swift.com unreachable in
the research pass). What the schema does establish is that a return has its own `GrpHdr/MsgId` and its own
`RtrId`, and that `OrgnlUETR` is a *reference*, not the return's identity.

`camt.029` answers a `camt.056` with a per-transaction `CancellationIndividualStatus1Code` restricted to
exactly `RJCR` / `ACCR` / `PDCR`, plus a message-level confirmation from the open
`ExternalInvestigationExecutionConfirmation1Code` set (`CNCL`, `RJCR`, `PDCR`, `PECR`, `IPAY`, `FTNA`, `IDUP`,
`ICOV`, `MCOV`). Note `RJCR` appears in **both** sets; branch on the field, not on the string.

Rejection reasons are the operational reality, from `ExternalPaymentCancellationRejection1Code`, verbatim:
`ARDT` "the transaction has already been returned" · `PTNA` "passed to the next agent" · `NOOR` "Original
transaction … never received" · `NOAS` "No response from beneficiary" · `AGNT` an agent refuses · `CUST` a
creditor decision · `LEGL` regulatory rules · `AM04` "Amount of funds available … is insufficient" (the
beneficiary spent it) · `INDM` blocked "until an indemnity agreement is established" · `ADAC` / `RQDA` debit
authority not given / required. Every one is a way for a recall to fail **after** you optimistically showed
the customer "cancelled". `Case5` even carries `ReopCaseIndctn` — the investigation is not monotone.

## `MsgId` is not a payment identity

`GroupHeader90` carries `MsgId` (`Max35Text`), `CreDtTm`, `NbOfTxs` (`Max15NumericText`) and `CtrlSum`
(`DecimalNumber`). `NbOfTxs` and `CtrlSum` are **control totals over the message**, not over the business
event. Deduplicating on `MsgId` deduplicates *retransmissions of one message* — a transport concern. It does
**not** deduplicate a re-submission of the same payment under a fresh `MsgId`, which is exactly the Santander
Christmas-Day 2021 shape: a scheduled run submitted twice, ~75,000 payments and ~£130M duplicated, recovered
only by negotiation with receiving banks. Batch-level idempotency is a unique index on the *business* batch
identity, not on `MsgId`.

Statements re-send too: `AccountStatement9` carries `ElctrncSeqNb`, `LglSeqNb`, `RptgSeq`, `StmtPgntn` and
`CpyDplctInd` typed `CopyDuplicate1Code` with enumerations exactly `CODU`, `COPY`, `DUPL`. Ingestion must be
idempotent on `(Acct, ElctrncSeqNb)` and must not double-post a `DUPL`.

## Destination verification before an irreversible send

Structural validation — IBAN check digits, a routing-number checksum, schema validation of the message —
proves the string is **well-formed**. It does not prove the account belongs to the intended beneficiary, and
on an irreversible rail the difference is the entire loss. The 3Q2025 code set added six Verification-of-Payee
statuses to `ExternalPaymentTransactionStatus1Code` (`RCVC`, `RVNA`, `RVNM`, `RVMC`, `RVNC`, `RVCM`) alongside
confirmation-of-funds (`ACFC`, `ACFW`); their individual verbatim definitions were not read in this pass, so
branch on them from the published code-set file at your target scheme's version rather than from memory.

Two guards belong in the send path itself, both motivated by Citibank/Revlon (11 August 2020: ~$894M of
principal wired when ~$7.8M of interest was due; the wires settled with finality; recovery turned on the
*discharge-for-value* doctrine and two years of litigation, not on any protocol undo):

1. **Confirm on the derived money movement, not on the operator's inputs.** The confirmation surface renders
   the computed debits and credits — beneficiary, account, currency, amount, value date — as they will be
   serialised into the message. Citi's six-eye process passed because the screen showed intent, not effect.
2. **Assert a hard maximum-plausible-amount bound before submission, and block rather than warn.** The Revlon
   payment was ~100× the scheduled interest on a facility not due for three years; a bound derived from the
   payment's own schedule would have refused it. A bound that merely raises a review flag is a bound that a
   busy operator clears.

Finality is a **legal** predicate. Any design whose recovery story is "we'll just reverse it" is relying on an
outcome it does not control.

## Amount precision at serialisation

`ActiveCurrencyAndAmount`, verbatim and identical across `pacs.008`, `pacs.002`, `pacs.004`, `pacs.009`,
`camt.053`, `camt.056` and `camt.029`:

```xml
<xs:simpleType name="ActiveCurrencyAndAmount_SimpleType">
    <xs:restriction base="xs:decimal">
        <xs:fractionDigits value="5"/>
        <xs:totalDigits value="18"/>
        <xs:minInclusive value="0"/>
    </xs:restriction>
</xs:simpleType>
<!-- extension adds: <xs:attribute name="Ccy" type="ActiveCurrencyCode" use="required"/> -->
```

Four consequences. It is `xs:decimal`, **never a float** — an IEEE-754 double cannot round-trip 18 significant
digits, and the residue this suite cares about is the *storage and wire boundary*: a SQL `Float` column, an
ORM float field, a protobuf `double`, a JSON round-trip. Amounts are **unsigned** (`minInclusive = 0`);
direction is carried structurally or by `CdtDbtInd`, restricted to exactly `CRDT` / `DBIT`. `Ccy` is a
**required attribute on the amount element** — an amount without a currency is not representable. And
validation is **two-layered**: the schema bound (18/5) *and* the ISO 4217 per-currency exponent, which is
stricter and varies at runtime. `<InstdAmt Ccy="EUR">10.403</InstdAmt>` is rejected — "Too many decimal digits
given. Maximum of 2 may be present for the given currency". Zero-decimal currencies (JPY, KRW, VND, CLP, ISK,
UGX, XOF/XAF/XPF, BIF, DJF, GNF, KMF, PYG, RWF, VUV) admit no fraction at all; three-decimal currencies (KWD,
BHD, JOD, OMR, TND) admit three.

So the rounding to the currency exponent happens **before** the message is built, and the remainder it drops
has to be booked somewhere named. The same is true of the return residual, which is not a rounding artefact at
all. `PaymentTransaction118` makes **`RtrdIntrBkSttlmAmt` mandatory** while `OrgnlIntrBkSttlmAmt` is optional,
and carries a separate `CompstnAmt`, unbounded `ChrgsInf`, its own `IntrBkSttlmDt` and its own `XchgRate`.
Worked:

```
original pacs.008:  IntrBkSttlmAmt        EUR 100,000.00   IntrBkSttlmDt 2026-03-02
return pacs.004:    RtrdIntrBkSttlmAmt    EUR  99,975.00   IntrBkSttlmDt 2026-03-11
                    ChrgsInf/Amt          EUR      25.00
                    CompstnAmt            EUR      12.34

residual = 100,000.00 − 99,975.00 = 25.00  → fee expense, NOT a rounding difference
compensation                       12.34   → interest income
net P&L                           −12.66   booked on 2026-03-11, not on 2026-03-02
```

A return cannot be modelled as reversing the original entry: reversing implies the same amount, date, currency
and fee treatment, and **all four can differ**. Book it as an independent, forward-dated transaction that
*references* the original via `OrgnlUETR`. Do not derive the effective FX rate from `XchgRate` either — it is
typed `BaseOneRate` (`totalDigits 11`, `fractionDigits 10`), leaving **one integer digit**, so the maximum
representable value is `9.9999999999` and a USD/JPY rate of 155.23 cannot be put in the field. Derive the rate
from the two amounts that are present and treat `XchgRate` as advisory.

Finally, a `camt.053` statement line's signed value is a **three**-field function:
`sign(CdtDbtInd) × Amt × (RvslInd ? −1 : +1)`. Reading `Amt` alone, or `Amt` and `CdtDbtInd` while ignoring
`RvslInd`, silently inverts every reversal on the statement.
