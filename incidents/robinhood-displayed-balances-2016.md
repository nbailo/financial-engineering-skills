# Robinhood — displayed balances computed on a second code path, doubled, mislabelled, and marked at zero when a price was missing (2016-09 → 2021-05)

**Domain:** Retail brokerage, margin, ledger display | **Loss:** $57,000,000 FINRA fine + $12.6 million restitution — the largest FINRA penalty at the time | **Failure class:** State divergence (with sentinel escape and label drift) | **Skill:** fin-ledger

## What happened

Over roughly four and a half years, Robinhood displayed systematically false account information to
millions of customers. The values shown were not approximations or rounding artefacts; several were
wrong by an exact factor of two. FINRA's AWC records a customer, identified as Customer A, who the
day before he died was shown a cash balance of **−$730,165.72** against an actual balance of
**−$365,530.60**. He took his own life in June 2020.

Two populations and two periods are involved, and they are routinely merged in secondary accounts.
They are different:

- **Inaccurate cash balances:** "**more than 135,000 customers**", **December 2019 – June 2020**.
  Read the AWC precisely here: Robinhood "either doubled these customers' actual negative cash
  balances **or** inflated their cash balances by displaying buying power as 'cash'", and which one
  a customer saw depended on account type. The doubling is the sub-population of Instant customers
  and Gold customers who had toggled margin off; Customer A is in that sub-population.
- **Doubled "negative buying power":** "**approximately 4.2 million customers**", **September 2016 –
  September 2020**.

## Root cause, in code terms

**Derived display quantities were computed on a code path independent of the booked ledger.** The
AWC's finding is that Robinhood's website and applications "**displayed negative cash balances that
were double those customers' actual negative cash balances**", and separately displayed
"inaccurate 'negative buying power' values in amounts that were **double those customers' negative
cash balances**".

The exact factor of two is the diagnostic signal. A display path that is off by a *random* amount is
usually reading the wrong record; a display path that is off by exactly 2× is applying the same
debit twice — once in the base figure it reads and once again in the adjustment it computes on top.
The second doubling compounds it: buying power was derived from the already-doubled cash figure, so
its error is the product of two independent applications of the same mistake.

**A missing price was returned as zero in a valuation.** Footnote 15:

> "The August 10, 2018 errors were caused by **a coding mistake in one of Robinhood's internal
> systems, which caused some securities to be incorrectly returned as having zero value for purposes
> of mark-to-market valuations.**"

A security marked at zero collapses account equity, which produces a margin deficiency, which
produces a margin call against a customer whose portfolio was never impaired. Zero is a legal price.
"Unknown" is not zero, and the two must not share a representation.

**A label asserted the wrong financial quantity.**

> "From December 2014 to June 2020, Robinhood **used the label 'equity,' rather than 'market
> value,'** to describe a customer's position in a particular stock. As a result …, from September
> 2016 to June 2020, Robinhood displayed to **approximately 664,000** 'Robinhood Gold' customers who
> had borrowed on margin 'equity' figures that in fact **represented the market value** of the
> customers' holdings."

For an unlevered account, equity and market value coincide. For a margin account they do not, and
the difference is exactly the borrowed amount — which is the number a margin customer most needs.
The same class of error appears elsewhere in the AWC: buying power rendered under a "cash" label.

**Corporate actions desynchronised every derived figure.** Splits, dividends and mergers processed
late or incorrectly corrupted portfolio balance, cash balance, buying power and total return. The
performance chart "**either overstated customers' gains or understated customers' losses** …
including because Robinhood mistakenly **did not properly account for cash dividends** that had been
paid to customers, various cash movements in customer accounts, and **cash and position movements
caused by corporate actions**."

**Nothing detected any of it.** "**No internal system at Robinhood triggered any alerts regarding
these events** …"

## The invariant that was violated

```
# display and ledger are the same number
forall monetary quantity x shown to a user:
    displayed(x) == ledger(x)              # asserted in test, not by convention
i.e. the display READS the authoritative record; it does not RECOMPUTE it

# missing price
price_unavailable => valuation RAISES  (or propagates an explicit absent value)
NOT: price_unavailable => 0
and: 0 is a legal price, distinguishable from absent, at the type level

# labels
label(field) names the exact financial quantity held
equity != market_value    for any account with a debit balance
cash   != buying_power

# corporate actions
apply(corporate_action) is atomic over {positions, cash, cost_basis, every derived figure}
and a reconciliation fails if any derived figure disagrees afterwards
```

## Could an AI coding agent reviewing the diff have caught it?

**Yes — three of the four are direct findings.**

**The second computation path.** A display or API-response function that recomputes a monetary
quantity from components, when an authoritative value for that quantity already exists in the
ledger, is a structural review finding. The signal in a diff is a `buying_power` or
`available_cash` expression assembled from a balance plus adjustments in presentation code, rather
than a read of the field the ledger owns. The correct shape is that exactly one component computes
the quantity and everything else reads it — and that a test asserts `displayed == ledger` over
generated account states. That test is what would have caught the exact-2× signature immediately.

**The zero mark.** `return 0` (or `?? 0`, or `float(price or 0)`) in a mark-to-market or valuation
function is one of the most direct findings available to a reviewer. The consequence chain —
zero mark → collapsed equity → margin deficiency → margin call — follows from the code without any
domain input.

**The label.** A field named `equity` populated from a market-value computation is visible at the
assignment. This is a rename with financial meaning, and an agent that treats a field name as an
assertion about *which* quantity is held will flag the mismatch.

**Corporate actions** are the "partly" part: an agent can see that a split handler updates positions
but not cost basis, or that a dividend handler does not touch the performance series. What it cannot
see is whether the operational pipeline runs them in time.

## The rule

> **MUST — A monetary figure shown to a user must be read from the same authoritative source as the
> booked ledger, and a test must assert their consistency.** Never let a display path recompute a
> quantity the ledger already owns.

> **MUST — A missing, stale, or unavailable price must propagate as an explicit absent or error
> value.** It must never default to `0`, a null coerced to zero, or the last known price without an
> explicit staleness policy.

> **MUST — A field's label must name the exact financial quantity it holds.** Do not use "equity"
> for market value, "cash" for buying power, or "balance" for available balance.

> **MUST — Corporate actions (splits, dividends, mergers) must be applied atomically to positions,
> cash, cost basis, and every derived performance figure, with a reconciliation that fails if any
> derived figure disagrees.**

> **MUST — Emit anomalous-condition signals as alerts on a monitored channel with an owner.** Four
> and a half years of doubled balances triggered no internal alert of any kind.

## Sources

- **FINRA AWC No. 2020066971201, *Robinhood Financial LLC*, 30 June 2021** — AWC PDF:
  <https://www.finra.org/sites/default/files/fda_documents/2020066971201%20Robinhood%20Financial%20LLC%20CRD%20165998%20AWC%20rjr.pdf>;
  (The 30 June 2021 FINRA press release is no longer served at its original URL — HTTP 404 as of
  this pass; the AWC PDF above is live and is the authoritative text.)
  **Primary.** Establishes at pp.8 the inaccurate cash balances (">135,000 customers",
  December 2019 – June 2020, "either doubled … or inflated … by displaying buying power as 'cash'")
  and Customer A's −$730,165.72 against an actual −$365,530.60; at pp.8
  the doubled negative buying power ("approximately 4.2 million customers", September 2016 –
  September 2020); at fn 15 the August 10 2018 zero-value mark-to-market coding mistake; at pp.13
  the "equity" versus "market value" label and the ~664,000 Robinhood Gold margin customers
  (September 2016 – June 2020); at pp.13 the performance chart overstating gains and understating
  losses, and the unaccounted cash dividends and corporate-action movements; at pp.12 that no
  internal system triggered any alerts. $57 million fine plus $12.6 million restitution.
- **Correction applied.** Secondary accounts commonly state that ~4.2 million customers saw doubled
  balances for four years. That merges two distinct findings: the doubled negative *cash* balance
  affected >135,000 customers over seven months; the doubled negative *buying power* affected ~4.2
  million over four years.
