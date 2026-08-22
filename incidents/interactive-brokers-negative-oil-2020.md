# Interactive Brokers — a price type that could not represent a negative number, on the day WTI settled at −$37.63 (2020-04-20)

**Domain:** Futures brokerage, order entry, margin | **Loss:** $82,570,000 restitution to customers plus a $1,750,000 civil monetary penalty (CFTC Release 8432-21) | **Failure class:** Representation — domain range | **Skill:** fin-money-core

## What happened

On 20 April 2020 the May 2020 NYMEX WTI crude oil futures contract traded below zero for the first
time in its history and settled at **−$37.63 per barrel**. Interactive Brokers' electronic trading
system could not display negative prices, and customers could not enter negative-priced limit
orders. Customers holding long positions could not see the market they were in and could not act on
it. Internal margin requirements were not correctly enforced on a pre-trade basis for the contract.
Positions were liquidated at prices customers had never been shown, and some customers were billed
for large deficits. The CFTC found that IBKR "**was on notice of the possibility of negative oil
futures prices prior to April 20, 2020**, but did not adequately prepare and configure its
electronic trading system."

Thomas Peterffy publicly put IBKR's total cost at roughly $104–113 million ("It's a $113m mistake on
our part"); that figure is his own and is not a regulatory finding.

## Root cause, in code terms

**A domain assumption baked into a type, and then into everything downstream of it.** The system was
built on `price > 0`. That assumption is rarely written down as an assertion; it is expressed
implicitly, in a set of choices each of which looks harmless:

- an **unsigned or absolute-valued representation** for price, or a signed one that no code path
  ever produces a negative value into;
- **input validation** on order entry of the form `if price <= 0: reject` — which is correct for
  equities and wrong for a physically-settled commodity future;
- **formatters and renderers** that lay out a currency figure without provisioning for a sign, so
  a negative value renders as a positive one, as an error, or not at all;
- **margin and risk arithmetic** that uses `abs(price)` or treats price as a magnitude, so exposure
  computed for a short position at a negative price has the wrong sign;
- and, at the mathematical layer, **option pricing models whose support excludes the region**.

That last one is not an implementation bug and is worth separating. Black-76-family models compute
`log(F/K)`, which is undefined for `F <= 0` or `K <= 0`. When WTI went negative, the model itself —
not its code — had no answer. CME Clearing's response was the correct one: switch the options
pricing and valuation model to **Bachelier** (arithmetic/normal), which admits negative underlyings
and negative strikes, effective for the margin cycle at end of trade date 22 April 2020, reverting
31 August 2020. **The remedy for a model whose domain excludes a reachable state is to change the
model, not to clamp the input to a small positive epsilon.**

The failure therefore spans the whole stack — parse, store, validate, display, order-entry, margin,
and pricing model — because the assumption was made once, at the type, and inherited everywhere.

## The invariant that was violated

```
# representation
price : Signed                                  # end to end: parse, store, transport, render, margin
NOT: price : Unsigned  |  price : "assumed positive"

# validation
order_entry.validate(price) checks TICK and BAND
NOT: order_entry.validate(price) checks price > 0

# arithmetic
no abs(price) on a signed-domain instrument
exposure(position, price) has the correct sign for price < 0

# models
forall pricing_model m, instrument i:
    reachable_state_space(i) subset_of domain(m)
and where it is not: assert the domain explicitly and FAIL LOUDLY, or select another model
NOT: clamp(F, epsilon, inf) to keep log(F/K) defined
```

## Could an AI coding agent reviewing the diff have caught it?

**Yes — this is one of the most statically visible defects in the catalogue.**

Every one of these is greppable:

- an **unsigned integer or unsigned decimal type** for a price field;
- `abs(` applied to a price or to a price difference on a margin path;
- `price > 0` or `price >= 0` as an *entry validation* rather than a tick or band check;
- a currency formatter that assembles a string from a magnitude and a currency symbol with no sign
  handling;
- `log(` or `sqrt(` or division by a price in a pricer, with no domain assertion.

The one judgement an agent must make is the domain question: *can this instrument's price go
negative?* For a physically-settled commodity future the answer is yes, and it has been yes since
storage was finite. For an interest rate or a spread the answer is yes. For an equity it is no. An
agent asked that question about a physically settled commodity future should answer yes — and where
it genuinely cannot decide, the correct output is not silence but a demand: state the instrument's
reachable range and assert it.

The behavioural rule matters as much as the detection: an agent that finds `log(F/K)` in a pricer
must **not** propose clamping `F`. Clamping converts an honest failure into a silently wrong number,
which is strictly worse.

## The rule

> **MUST — Never assume a price, rate, or spread is non-negative.** Use a signed type end to end —
> parsing, storage, transport, display, order entry, and margin — and never apply `abs()` to a price
> on a valuation or exposure path.

> **MUST — For any pricing formula containing `log(price)`, `sqrt(price)`, or division by price,
> either assert the domain explicitly and fail loudly, or select a model whose domain admits the
> full reachable range.** Do not clamp the input to keep the formula defined.

> **MUST — Order-entry validation must check tick size and price bands, not sign.** A rejection rule
> of the form `price <= 0` encodes an instrument assumption in a place that no longer knows which
> instrument it is validating.

> **MUST — When an instrument's reachable value range is in question, state it and assert it.** A
> domain's value range can change under you, and a type that cannot represent the new range is a
> total loss, not a degradation.

## Sources

- **CFTC Release 8432-21, *In re Interactive Brokers LLC*, 28 Sept 2021** —
  <https://www.cftc.gov/PressRoom/PressReleases/8432-21>. **Primary.** Establishes that IBKR's
  electronic trading system could not display negative prices and that customers could not enter
  negative-priced limit orders; that internal margin requirements were not correctly enforced on a
  pre-trade basis for WTI; the $82.57 million restitution and $1.75 million penalty; and the finding
  that IBKR **was on notice of the possibility of negative prices before 20 April 2020**.
- **NYMEX settlement.** The May 2020 WTI contract settled at **−$37.63/bbl** on 20 April 2020.
- **CME Clearing Advisories 20-152 (8 Apr 2020), 20-160 (15 Apr 2020), 20-171 (21 Apr 2020)** —
  e.g. <https://www.cmegroup.com/notices/clearing/2020/04/Chadv20-171.html>. **Secondary as to
  wording.** The switch of the options pricing and valuation model from the Whaley/Black-76 family
  (log-based) to Bachelier (normal, arithmetic) to accommodate negative underlying futures prices
  and negative strikes, effective for the margin cycle at end of trade date 22 April 2020 and
  reverted 31 August 2020. The CME pages are JS-rendered and the advisory PDFs returned HTTP 403 to
  every retrieval attempt during research; the model names, direction, products and dates are
  corroborated across multiple independent reports and are consistent with the mathematics, but the
  advisory text itself was **not read**. Any skill quoting the advisory should re-fetch
  Chadv20-152 / 160 / 171 first.
- **Secondary, attributed:** Thomas Peterffy's own estimate of IBKR's total cost at ~$104–113
  million. Not a regulatory figure.
