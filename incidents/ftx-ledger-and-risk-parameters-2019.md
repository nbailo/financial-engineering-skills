# FTX — customer balances credited by hand from an operator action, and a per-account flag that made the solvency check opt-out (2019-07 → 2022-11)

**Domain:** Crypto exchange, customer ledger and risk parameters | **Loss:** approximately $8 billion hole in customer fiat | **Failure class:** Provisional value made spendable / missing conservation | **Skill:** fin-ledger

## What happened

Two engineering decisions, both several years old by the time FTX failed, made the collapse
expressible in software. First, customer fiat deposits were wired to bank accounts controlled by
Alameda Research and credited to customer accounts on FTX's ledger by hand; the aggregate was
carried as an internal account called `fiat@ftx` and reached up to $8 billion. Second, the field
governing how far an account could go net-negative before auto-liquidation was a per-customer
number, and a boolean existed that exempted an account from liquidation entirely. Both were set for
Alameda alone.

This entry is not about fraud. Fraud is not detectable in a diff. It is about the code shapes that
made the fraud representable, each of which a reviewer can and should refuse on its own terms.

## Root cause, in code terms

**1. The ledger credit came from an operator action, not from a settlement event.** The CFTC's
amended complaint records that customer fiat deposits were wired to bank accounts owned by Alameda
(some in the name of "North Dimension") and that "**Alameda personnel manually credited FTX customer
accounts with the corresponding amount of fiat currency on FTX internal ledger system**". Customers
saw balances posted to their FTX accounts "even though the fiat deposits actually remained in
Alameda-controlled bank accounts". The credit had no settlement reference — no bank transaction, no
chain transaction, nothing an automated reconciliation could join against.

**2. The resulting liability was never re-homed.** After FTX opened its own FBO bank accounts around
August 2020, the historical `fiat@ftx` balances were **never transferred**. An architecture change
happened; the liability account it was supposed to retire stayed. It was later reallocated to a
sub-account deliberately not identifiable as Alameda's — recorded in the system as "FTX fiat old",
and referred to internally as "our Korean friend's account" — which also carried the allow-negative
flag.

**3. The solvency invariant was implemented as a per-account configurable exemption.** From John J.
Ray III's First Interim Report:

- `borrow` was a **per-customer field** in the FTX.com customer database controlling how negative a
  balance could go before auto-liquidation. Most retail customers: **0**. Preferred market makers:
  up to **$150 million**. **Alameda alone: $65,000,000,000.**
- `can_withdraw_below_borrow` was added to the codebase on **23 July 2019**; `allow_negative` on
  **31 July 2019**. Together they let a flagged account withdraw unlimited assets while net-negative
  and exempted it from auto-liquidation. As of the petition date, **Alameda was the only customer on
  FTX.com for which `allow_negative` was set to `true`.**

The shape is `if (account.allow_negative) skip_liquidation()`. A system-level invariant — the
exchange holds at least what it owes — was expressed as a property each account could individually
opt out of, with no system-wide ceiling on the sum of the exemptions.

**4. There was no audit trail on the risk parameters.** The debtors' report states that **because
database logs were not retained, it is not possible to determine when or by whom the $65 billion
`borrow` value was set.** Field-level audit logging on risk parameters is a forensic prerequisite,
not a nicety: the single most important question about the most important number in the company is
unanswerable.

**5. The general ledger was manual and months behind.** 35 FTX entities used QuickBooks as their
general ledger; data was moved into it by hand; crypto positions "went untracked"; entries were made
months late; and a catch-all account named "**Ask My Accountant**" absorbed what did not fit.

## The invariant that was violated

```
# system-level solvency, not per-account
sum(customer_balances) <= assets_actually_custodied
and this predicate has NO per-account exemption

# provenance of every credit
forall credit c on a customer balance:
    exists settlement_event e with an external reference (bank txn id | chain txid)
    such that c derives from e
NOT: c derives from an operator action or a UI confirmation

# risk parameters
forall risk_parameter p (borrow limit, credit line, liquidation exemption):
    p is a bounded, typed limit with a hard system-wide ceiling
    AND every change to p writes an append-only audit record (who, when, old, new)

# state modelling
provisional / credited / settled are DISTINCT states
withdrawal and onward transfer are permitted only against SETTLED
```

## Could an AI coding agent reviewing the diff have caught it?

**Yes, for every code-shaped part of it — and this is the entry where "the agent cannot detect
fraud, but it can refuse the code that expresses it" is clearest.**

**The exemption flag.** A diff that adds a boolean field to an account and uses it to bypass a
liquidation, margin, or solvency check is a finding an agent should state flatly and refuse. The
signal is `if account.<flag>: return` or `skip` immediately before the invariant that every other
path enforces. No amount of business justification changes the review comment: if the limit must
vary, it is a bounded typed limit with a system-wide ceiling, not an exemption.

**The unbounded per-account limit.** A `borrow` field with no maximum, and no assertion that
`sum(borrow_limits) <= some_system_capacity`, is a static finding. An agent does not need to know
that one account holds $65bn; it needs to observe that nothing prevents it.

**The manual credit endpoint.** A code path that increments a customer balance from an admin or
operator action, without a settlement reference, is directly reviewable. The correct shape has the
credit function require a settlement-event identifier as a mandatory argument, so that an unbacked
credit is unrepresentable rather than merely discouraged.

**The missing audit trail.** A mutation of a risk parameter with no append-only record of who
changed it, when, from what, to what, is visible in the diff that adds the setter.

What an agent cannot do is judge intent, or notice that a liability account survived an architecture
change it was supposed to end with. That second one is worth a rule anyway: an architecture change
that supersedes a liability account must migrate or explicitly retire it.

## The rule

> **MUST NOT — Introduce a per-account flag that exempts an account from a solvency, credit-limit,
> or liquidation check.** If a limit must vary, model it as a bounded, typed limit with a hard
> system-wide ceiling and an append-only audit record of every change (who, when, old value, new
> value).

> **MUST — Derive the ledger credit from a settlement event emitted by the settlement system, not
> from an operator action or a UI confirmation.** Make the settlement reference a required argument
> of the credit function.

> **MUST — Model "authorised", "credited/provisional" and "settled/final" as distinct states, and
> never allow withdrawal or onward transfer against a balance that is only provisional.**

> **MUST — Never delete or fail to record an audit trail of changes to risk parameters, limits, or
> entitlements.** If you cannot answer "who set this number and when", the number is not governed.

> **MUST — Assert the global conservation invariant (`sum(all balances) + fees == sum(deposits) −
> sum(withdrawals)`) in a test and in production reconciliation**, not only at the entity level.

> **MUST — An architecture change that supersedes a liability account must migrate or explicitly
> retire the old account.** FTX opened its own FBO accounts and left `fiat@ftx` in place for two
> years.

## Sources

- **`CFTC v. Samuel Bankman-Fried, et al.`, Case 1:22-cv-10503-PKC (S.D.N.Y.), Amended Complaint
  filed 21 Dec 2022** —
  <https://www.cftc.gov/media/8021/enfsamuelbankmanfriedcomplaint121322/download>. **Primary.**
  ¶¶46–50, 76, 80–81 establish: fiat deposits wired to Alameda/North Dimension bank accounts;
  "**Alameda personnel manually credited FTX customer accounts with the corresponding amount of
  fiat currency on FTX internal ledger system**"; the aggregate recorded as `fiat@ftx`, holding up
  to **$8 billion**; that after FTX opened its own FBO accounts (~Aug 2020) the historical
  `fiat@ftx` balances were **never transferred**; and the later reallocation to a sub-account not
  identifiable as Alameda's (system note "FTX fiat old"), which also carried the allow-negative
  flag.
- **First Interim Report of John J. Ray III on control failures at the FTX exchanges**, Case
  22-11068-JTD Doc 1242-1 (9 Apr 2023) —
  <https://www.fishmanhaygood.com/wp-content/uploads/2023/04/April-9-Debtor-Report-on-Failure-of-Internal-Controls-1.pdf>.
  **Primary.** Establishes: `borrow` as a per-customer field controlling how negative a balance
  could go before auto-liquidation (most retail 0; preferred market makers up to $150M; **Alameda
  $65 billion**); `can_withdraw_below_borrow` added **23 Jul 2019** and `allow_negative` **31 Jul
  2019**; that Alameda was the only account with `allow_negative = true`; that **because database
  logs were not kept it is not possible to determine when or by whom the $65bn borrow value was
  set**; and that 35 FTX entities used QuickBooks as their general ledger, populated manually and
  months late, with crypto positions untracked and a catch-all account named "Ask My Accountant".
