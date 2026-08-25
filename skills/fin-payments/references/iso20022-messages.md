# ISO 20022 messages

> **Provenance**
> provider: ISO 20022, via the registration authority's message definitions and external code sets · surface: the `pacs.002` status ladder, identity fields across `pacs`, `pain` and `camt`, return correlation, and amount serialisation
> version: the external code set edition named in the body, with 27 status values as of that publication. No schema version was pinned per message.
> verified_at: not established
> sources: https://www.iso20022.org/
> verified: none in this pass. No sentence below was re-read against a source for v0.5.0.
> unverified: all of it. This file predates the provenance requirement and was not re-checked in the v0.5.0 pass, so its claims carry the confidence of their original sourcing and no more, with no date you can check. The status ladder is the sharpest case: the file's own argument is that the code list is an open enum republished on a quarterly cadence, which means the count quoted below is a snapshot of a moving list and nobody has re-read it since. The host did not answer this client on 2026-08-25, the connection timed out, so even the location above was not opened; a recheck needs the message catalogue and the external code sets file from the registration authority, and then the usage guideline of whichever scheme you actually send to.
> revalidate_when: ISO publishes an external code set edition that adds or retires a `TxSts` value; a scheme you send to publishes a usage guideline that narrows or redefines these identity fields; the UETR requirement or its uniqueness rule changes; a rail you use moves to a newer message version.

Message-level identity, status and amount, read out of the schemas. The correlation key for a return is
decided when the original is minted, the status ladder is an open enum whose default branch is where the
money goes wrong, and the amount carries two validation layers. Read it when a `pacs`, `camt` or `pain`
message is built, parsed or correlated.

## Contents

- The `pacs.002` status ladder: what each `TxSts` licenses, and why it is an open enum
- ISO 20022 identity: `EndToEndId`, `TxId`, `UETR`; who mints each, `Max35Text`, the UUIDv4 constraint
- Correlating a return to its original: `OrgnlInstrId` / `OrgnlEndToEndId` / `OrgnlTxId` / `OrgnlUETR`
- `MsgId` is per-message and per-hop, and is not a payment identity
- Amount precision at serialisation: currency-specific fraction digits and where the remainder is booked

---

## The `pacs.002` status ladder

`TxSts` is typed `ExternalPaymentTransactionStatus1Code`, whose entire schema constraint is
`minLength 1 / maxLength 4`; the values live in a separate file ISO republishes quarterly (27 values as of
publication 3Q2025). **It is an open enum. A closed enum compiled into your code will meet a value it has
never seen, and the default branch is where the money goes wrong.** Store unknown codes verbatim, route to a
review queue, and fail *closed*: do not resend, do not credit.

| Code | ISO definition (verbatim) | What it licenses |
|---|---|---|
| `ACSP` | "…the payment instruction has been **accepted for execution**" | nothing economic |
| `ACWC` | "Instruction is accepted but **a change will be made**, such as date or remittance not sent" | re-read the returned `OrgnlTxRef`; your submitted values are not what will happen |
| `ACWP` | "accepted **without being posted to the creditor customer's account**" | interbank leg may be done; beneficiary is not paid |
| `BLCK` | "…funds will **neither be posted to the Creditor's account, nor be returned to the Debtor**" | money in limbo by design; model it |
| `ACSC` | "**Settlement completed.** … **Warning: this status is provided for transaction status reasons, not for financial information**" | the standard itself tells you not to book on it |
| `ACCC` | "Settlement on the **creditor's account** has been completed" | the beneficiary is paid |
| `PDNG` | "…pending. Further checks and status update will be performed" | **non-terminal**; keep the timer running |
| `RJCT` | "Payment instruction has been rejected" | terminal for this instruction |

`ACSP` → `ACSC` → `ACCC` is a three-stage progression and only the last is what a business person means by
"paid". At the **group** level `ACSC` means "settlement on the **debtor's** account has been completed";
the same four letters at two levels of the same message denote two different events. Group status also carries
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
| `InstrId` | instructing agent | **one hop**, legitimately replaced at every hop | never correlate on it alone |
| `EndToEndId` | the **initiating party** (ordering customer) | passed unchanged to the creditor | if you are the corporate, this is your anchor with the beneficiary |
| `TxId` | the **first agent** | unique within the clearing chain | bank-side |
| `UETR` | the instructing agent that **first creates the interbank payment** | end-to-end across all agents and all message types | if you are a bank/PSP you mint it once; if you are a corporate you receive it |
| `ClrSysRef` | the clearing system | n/a | reference data |

`UUIDv4Identifier` is a *constrained* UUIDv4, verbatim:

```xml
<xs:pattern value="[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}"/>
```

Three traps. **Lowercase only**; any "normalisation" that upper-cases identifiers corrupts the field
(Go/Java/.NET default `String()` output is already lowercase). **The version nibble must be `4`**; a UUIDv5
derived by hashing `(debtor, amount, date, reference)` is rejected, which forecloses the obvious deterministic-
idempotency trick unless you deliberately force the version and variant bits onto the hash output and document
that the value is not random. **The variant nibble must be `8`, `9`, `a` or `b`.**

`Max35Text` is verbatim `minLength 1 / maxLength 35`. A canonical hyphenated UUID is **36** characters, so
`EndToEndId = str(uuid4())` is a schema violation. Strip the hyphens (32) or use a shorter application
reference; never truncate, which destroys uniqueness invisibly until it collides.

The invariant that makes retries safe end-to-end: **the UETR is minted once, written durably before the first
transmission, and reused verbatim on every retransmission: after a timeout, a process crash, or a region
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
uetr = str(uuid.uuid4())            # lowercase, version nibble 4; never .upper()
e2e  = uetr.replace("-", "")        # 32 chars; str(uuid4()) is 36 and fails Max35Text
with conn.transaction():            # COMMIT before the socket is opened
    conn.execute(INSERT_OUTBOUND, uetr, e2e, ccy, amt, value_date)
transmit(build_pacs008(uetr=uetr, end_to_end_id=e2e, ...))   # retransmit reuses both verbatim
```

## Correlating a return to its original

`pacs.002` (`PaymentTransaction110`), `camt.056` (`PaymentTransaction106`), `camt.029`
(`PaymentTransaction102`) and `pacs.004` (`PaymentTransaction118`) all carry the same quartet, and **every
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
not in `PaymentTransaction118`; the exact CBPR+ rule for it is **unverified here** (swift.com unreachable in
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
the customer "cancelled". `Case5` even carries `ReopCaseIndctn`; the investigation is not monotone.

## `MsgId` is not a payment identity

`GroupHeader90` carries `MsgId` (`Max35Text`), `CreDtTm`, `NbOfTxs` (`Max15NumericText`) and `CtrlSum`
(`DecimalNumber`). `NbOfTxs` and `CtrlSum` are **control totals over the message**, not over the business
event. Deduplicating on `MsgId` deduplicates *retransmissions of one message*, a transport concern. It does
**not** deduplicate a re-submission of the same payment under a fresh `MsgId`, which is exactly the Santander
Christmas-Day 2021 shape: a scheduled run submitted twice, ~75,000 payments and ~£130M duplicated, recovered
only by negotiation with receiving banks. Batch-level idempotency is a unique index on the *business* batch
identity, not on `MsgId`.

Statements re-send too: `AccountStatement9` carries `ElctrncSeqNb`, `LglSeqNb`, `RptgSeq`, `StmtPgntn` and
`CpyDplctInd` typed `CopyDuplicate1Code` with enumerations exactly `CODU`, `COPY`, `DUPL`. Ingestion must be
idempotent on `(Acct, ElctrncSeqNb)` and must not double-post a `DUPL`.

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

Four consequences. It is `xs:decimal`, **never a float**; an IEEE-754 double cannot round-trip 18 significant
digits, and the residue this suite cares about is the *storage and wire boundary*: a SQL `Float` column, an
ORM float field, a protobuf `double`, a JSON round-trip. Amounts are **unsigned** (`minInclusive = 0`);
direction is carried structurally or by `CdtDbtInd`, restricted to exactly `CRDT` / `DBIT`. `Ccy` is a
**required attribute on the amount element**; an amount without a currency is not representable. And
validation is **two-layered**: the schema bound (18/5) *and* the ISO 4217 per-currency exponent, which is
stricter and varies at runtime. `<InstdAmt Ccy="EUR">10.403</InstdAmt>` is rejected: "Too many decimal digits
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
*references* the original via `OrgnlUETR`. Do not derive the effective FX rate from `XchgRate` either; it is
typed `BaseOneRate` (`totalDigits 11`, `fractionDigits 10`), leaving **one integer digit**, so the maximum
representable value is `9.9999999999` and a USD/JPY rate of 155.23 cannot be put in the field. Derive the rate
from the two amounts that are present and treat `XchgRate` as advisory.

Finally, a `camt.053` statement line's signed value is a **three**-field function:
`sign(CdtDbtInd) × Amt × (RvslInd ? −1 : +1)`. Reading `Amt` alone, or `Amt` and `CdtDbtInd` while ignoring
`RvslInd`, silently inverts every reversal on the statement.
