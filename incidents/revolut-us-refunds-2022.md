# Revolut US — declined transactions refunded out of the firm's own funds because two networks disagreed about what "declined" meant (2021-12 → spring 2022)

**Domain:** Fintech, card authorisation and settlement | **Loss:** ~$23M taken, ~$20M net — roughly two-thirds of Revolut's 2021 annual net profit (FT reporting) | **Failure class:** Provisional value made spendable / cross-system state divergence | **Skill:** fin-payments

## What happened

Differences between Revolut's US and European payment systems meant that when a transaction was
**declined**, the refund was nevertheless settled — and settled **from Revolut's own funds**.
Organised groups worked out the pattern, deliberately attempted large purchases that would decline,
and withdrew the erroneous refunds at ATMs. The flaw was identified in late 2021, exploited from
early 2022, and closed around spring 2022. Roughly $23M was taken and about $20M was not recovered.
The company required multi-million-dollar capital injections from its parent.

**How it was found is the most important fact in the entry:** a US partner bank told Revolut it was
holding less cash than expected. Detection was outsourced to a counterparty's cash-position report.
No internal control fired.

**Sourcing.** No primary source is available. The underlying reporting is the Financial Times
(paywalled), summarised by trade press. The mechanism as described below should be attributed to FT
reporting and not stated as an engineering postmortem.

## Root cause, in code terms

**Two settlement/authorisation message models with different semantics, connected without a
reconciling invariant.** In one system's message vocabulary a particular outcome meant "this
authorisation was declined and no funds were reserved"; in the other's the corresponding message was
processed down a path that produced a customer refund. The two state machines were joined by an
adapter, and nothing downstream asserted the relationship between them.

**The refund path did not require that the funds it released be funds an authorisation had
reserved.** This is the code-level defect, independent of the message-format question. A refund
handler that computes an amount and credits a customer balance, without a mandatory link to a
specific prior capture and a bound of that capture's amount, will happily pay out against an
authorisation that never captured — or against no authorisation at all. When the two systems'
messages disagreed, the delta was silently funded from the firm's own balance sheet, month after
month.

**There was no three-way reconciliation.** Internal ledger cash versus processor/network report
versus partner-bank balance is a daily arithmetic check. Running it would have surfaced a non-zero
break within a day. Instead the break accumulated for months until the partner bank noticed.

## The invariant that was violated

```
# every refund must be funded from the capture it reverses
forall refund r:
    exists capture c such that
        r.capture_id == c.id
        AND c.state == CAPTURED
        AND sum(refunds_for(c)) <= c.amount

# the decline path must not be able to reach the credit path
reachable(state=DECLINED, credit_customer) == false

# and the break must be visible daily
internal_ledger_cash == processor_report_cash == partner_bank_cash    # reconciled on a cadence
non_zero_break => alert(owned)
```

## Could an AI coding agent reviewing the diff have caught it?

**Partly — and the half it can catch is the half that mattered.**

Reviewing a refund handler, an agent can check two things mechanically:

1. **Is the refund linked to a specific capture, and bounded by it?** A refund endpoint whose
   request carries `{customer_id, amount}` rather than `{capture_id, amount}`, or one that looks up
   an authorisation but does not assert `state == CAPTURED` and `sum(refunds) <= capture.amount`, is
   a direct finding. The correct shape makes an unfunded refund unrepresentable.
2. **Can the decline path reach the credit path?** This is a reachability question over the payment
   state machine. If `DECLINED` has an edge — direct or via a shared handler — that terminates in a
   customer credit, the state machine itself is the bug. An agent can enumerate the transitions.

The cross-network semantic divergence is harder and is where the "partly" bites. An agent cannot
know that two card networks assign different meanings to superficially similar messages. But **"two
message formats, one state machine, one adapter, no invariant tying them together"** is a visible
structural smell, and the correct review comment is to demand the invariant: for every message
translated across the seam, what predicate holds on both sides?

The reconciliation gap is also visible: a codebase with no scheduled job comparing internal cash to
the partner bank's reported balance is missing a control, and its absence is as reviewable as its
presence.

## The rule

> **MUST — When a payment is declined, reversed, or charged back in one system, the refund must be
> funded from the same reserved or captured funds that the original authorisation created.** Never
> fall back to the operator's own balance when two systems' messages disagree.

> **MUST — A refund must carry a mandatory reference to the capture it reverses, and must assert
> `capture.state == CAPTURED` and `sum(refunds_for(capture)) <= capture.amount` before crediting.**

> **MUST — Run an automated three-way reconciliation (internal ledger ↔ processor/network report ↔
> bank/custody balance) on a fixed cadence, and alert on any non-zero break.** Do not rely on a
> counterparty telling you your cash is short.

> **MUST — Where two systems with different message semantics are joined by an adapter, state the
> invariant that holds on both sides of the seam and assert it.** An adapter without an invariant is
> a translation, not a contract.

## Sources

- **Payments Dive, "Criminals stole $20M from Revolut via payment loophole"** —
  <https://www.paymentsdive.com/news/criminals-stole-20m-revolut-payment-loophole-neobank/686316/>.
  **Secondary**, reporting the Financial Times. Establishes: US and European systems handled
  decline/refund messaging differently; declined transactions were refunded from Revolut's own
  funds; organised groups deliberately made purchases that would decline and cashed out at ATMs;
  ~$23M taken and ~$20M net loss (≈two-thirds of 2021 net profit); identified late 2021, exploited
  from early 2022, closed ~spring 2022; **discovered only because a US partner bank reported holding
  less cash than expected**.
- **Financial Times** — the underlying report (paywalled), plus Revolut's annual report for the
  financial figures.
- **No primary source.** There is no regulator finding and no company postmortem. The mechanism
  should be attributed to FT reporting. The rule it motivates — a refund is funded by its capture —
  is standard and independently well-founded; this incident is evidence that the rule is violated in
  production at a large regulated fintech, not a code-level case study.
