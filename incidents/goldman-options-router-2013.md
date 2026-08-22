# Goldman Sachs options router — a $1.00 placeholder price on internal-only objects, routed as live orders by one misconfigured symbol stripe (2013-08-20)

**Domain:** Listed options order routing | **Loss:** ~$38 million realised after clearly-erroneous relief, against "up to a potential $500 million loss" from the executions (SEC 34-75331 ¶5); $7,000,000 penalty | **Failure class:** Sentinel escape | **Skill:** fin-exchange-integration

## What happened

Between 09:30 and roughly 09:47 on 20 August 2013, GSCO sent thousands of $1.00 limit orders in
listed options to the exchanges. Approximately 1.5 million option contracts — about 150 million
underlying shares — executed before the flow was stopped. The orders originated as internal
indications of interest ("axes") that were never supposed to reach an exchange at all. A rate-based
circuit breaker did trip, and control personnel repeatedly cleared it.

## Root cause, in code terms

**A placeholder price on an object designed never to leave the building.**

> "The system designed by Eq-Dat used an algorithm to generate axes. **Each axe contained a
> placeholder price of $1**, though the system was designed so that the price would adjust based on
> whatever customer order it executed against. The axes were then sent to a workflow server, which
> separated the axes into one of two '**stripes**' based on the ticker symbol of the underlying
> equity … Options whose underlying equity symbols were in the ranges **A-H and L-Z** flowed through
> one stripe, while options with underlying symbols in the **I-K** range flowed through another."
> (¶18)

The intended invariant is stated explicitly:

> "**Axes are not intended to go to the exchanges unless paired with a customer order**; they are
> intended to remain in the matching engine and search for customer orders to pair-off against."
> (¶16)

That invariant was enforced by configuration, not by type.

**The configuration reclassified one shard's objects as externally routable.**

> "**The misconfiguration coded the orders flowing through the server responsible for the I-K range
> as actual live orders, rather than as axes.** The configuration work was performed by a Mission
> Control employee **who did not fully understand the technical operation** of the new Axe options
> order flow at the time he performed the configuration. **This employee's work was not reviewed by
> the Eq-Dat team or anyone else at GSCO, nor was such a review required** by GSCO's written
> policies regarding software change management." (¶22)

**The tests covered the other shards.**

> "the Eq-Dat personnel who tested the new order flow system during the week prior to August 20
> **sent test axes through the execution server that handled the A-H and L-Z ranges – which was
> configured correctly – but not the I-K stripe. As a result, those tests did not reveal the
> misconfiguration.**" (¶23)

And the flow was enabled without telling the team that monitors it: "**Eq-Dat did not inform anyone
from Mission Control … that the axe order flow would begin on August 20**, nor was Eq-Dat required
to do so by any formal written policy" (¶24).

**The safety net had a strictly weaker off-hours branch — and its bound was computed across the
wrong universe.** During market hours Sigma Options applied price bands of ±100% of the NBBO for
options quoted below $1 and ±50% at or above $1 (¶25). Pre-market:

> "**during pre-market hours, Sigma Options employed a 'default' price check, which allowed the
> transmittal of options orders with any price greater than $0.01 and less than 1.5 times the
> highest closing price from the prior day for any listed option.**" (¶25)

> "The orders were not stopped by the default price check in Sigma Options because they were
> **priced at $1, which fell between $0.01 and $3,090** (which was the highest closing price for any
> listed option on the prior day, multiplied by 1.5)." (¶30)

The validator's upper bound is the maximum close **across the entire universe of listed options**,
not per series. That single aggregation choice makes the band useless for every instrument except
the single most expensive one.

**Three earlier near-misses changed nothing.** "between November 2011 and August 20, 2013, **there
were at least three instances in which GSCO's DMA clients sent erroneous options orders during the
pre-market period. However, none of these incidents caused GSCO to evaluate whether it should adjust
the parameters of the default price check.**" (¶27)

**The control that worked was cleared by hand, repeatedly, against written policy.**

> "These circuit breakers existed to prevent erroneous orders by halting all message traffic to the
> exchanges once that traffic had exceeded a certain rate. However, on August 20, **the firm's
> control personnel repeatedly lifted the circuit breakers blocks between 8:44 a.m. and 9:32 a.m.**,
> thereby permitting additional erroneous orders to be sent to the exchanges. **Before lifting the
> circuit breaker blocks, the control personnel did not obtain authorization from the responsible
> technology employees, as required under written firm policies.**" (¶8)

At 9:01 a Mission Control employee "(**who had authored the Mission Control circuit breaker
policy**) noticed the block and lifted it" without speaking to Eq-Dat (¶31), continuing a
pre-existing practice of lifting blocks "**while still investigating the cause**" (¶9).

**And the firm-wide capital control was a 30-minute batch, alert-only, over an incomplete input
set.** GSCO "only calculated its open equities and options orders and executions every 30 minutes",
alerting at 75% of the threshold, with "**no automated process to prevent the entry of additional
orders**"; the calculation excluded flow from a number of business units and "**also did not include
certain open (but unexecuted) orders**" (¶12).

## The invariant that was violated

```
# representation
forall outbound_order o: o.price is a real quoted price
NOT: o.price may be a placeholder that some upstream stage promised to overwrite

# type-level enforcement of the routing invariant
type Axe   { price: None }      # structurally cannot carry a sendable price
type Order { price: Price }     # and cannot be constructed from an Axe without a customer match

# validators
strictness(validator[pre_market]) >= strictness(validator[continuous])
price_band(instrument) is computed from instrument's OWN reference price
NOT: from max(reference_price) over the whole listed universe

# tests
forall shard s in routing_shards: exercised_by_test(s)

# automated blocks
clear(block) requires named_authorising_role  AND  recorded_root_cause
```

## Could an AI coding agent reviewing the diff have caught it?

**Yes — three separate findings from the source alone, and a fourth from the test suite.**

**The magic `1.00`.** A literal placeholder price assigned to a structure that is capable of
reaching an exchange is a direct finding. The correct shape is an un-priced internal indication
carrying a distinct type or a null price, so that constructing an outbound order from it is a type
error rather than a configuration outcome. An agent seeing `price = 1.00  # placeholder, adjusted on
match` in a structure that shares a serialiser with outbound orders should refuse it.

**The weaker off-hours branch.** A branch where `if pre_market: use default_check()` and
`default_check` is strictly more permissive than the regular-hours validator is visible in the
control flow. So is the aggregation defect inside it — `max(close_price for opt in all_listed
options)` where the bound should be per-series. Both are readable without any market data.

**The single-shard test.** A test suite that exercises one symbol-range partition of a routing path
and asserts nothing about the others is a coverage finding an agent can make from the fixtures:
the routing table has three stripes; the tests reference two.

**The configuration review gap** is visible as policy rather than code — but "this configuration
file controls whether objects are externally routable and has no required reviewer" is a legitimate
review comment on a repository whose CODEOWNERS or change policy does not cover it.

What an agent could not see: that control personnel lifted a working circuit breaker five times in
48 minutes without the authorisation their own policy required. That is the control that was
actually functioning, and it was defeated by people, not by code.

## The rule

> **MUST — A placeholder or sentinel price must be structurally impossible to send to an external
> venue.** Represent an un-priced internal indication with a distinct type or a null price, never
> with a magic number like `1.00`.

> **MUST — Pre-market, after-hours, auction and opening code paths must apply validation at least
> as strict as the continuous-trading path.** If reference prices are unavailable, fail closed
> rather than falling back to a permissive default.

> **MUST — A validation band must be computed per instrument, never from a cross-universe
> aggregate.**

> **MUST — A configuration that is sharded by key range (symbol, region, account bucket) must be
> covered by a test that exercises *every* shard**, and coverage must be asserted per shard.

> **MUST — Configuration changes affecting order routing, pricing or limits require review by a
> second person who understands the semantics, and the reviewer must be recorded.**

> **MUST — Clearing an automated block must require a named authorising role and a recorded
> root-cause determination.** Clearing while still investigating must be structurally impossible.

> **SHOULD — Remediation of an incident must be assessed against the defect class, not the specific
> trigger, and the assessment must be recorded.** Three prior pre-market erroneous-order incidents
> produced no re-evaluation of the default price check.

## Sources

- **SEC Admin. Proc. 34-75331, *In the Matter of Goldman, Sachs & Co.*, 30 June 2015** —
  <https://www.sec.gov/files/litigation/admin/2015/34-75331.pdf>. **Primary.** ¶5 (~1.5 million
  contracts ≈150 million underlying shares; "up to a potential $500 million loss"; ~$38 million
  realised), ¶8–9 and ¶31 (the circuit breakers repeatedly lifted 8:44–9:32 without required
  authorisation, by the author of the policy; the prior practice of lifting while investigating),
  ¶12 (the 30-minute capital calculation, 75% alert, no automated prevention, incomplete input
  set), ¶16 (axes are not intended to go to the exchanges unless paired), ¶18 (the $1 placeholder
  and the A-H / L-Z and I-K stripes), ¶22 (the misconfiguration; the configurer who "did not fully
  understand"; no review, none required), ¶23 (only A-H and L-Z tested), ¶24 (Mission Control not
  informed), ¶25 and ¶30 (the pre-market default band; $0.01 to $3,090), ¶27 (three prior
  pre-market incidents, no re-evaluation), fn 4 (the agreed remediation: secondary review of code
  *and configuration*; verification in production "including all relevant trading flows or
  stripes"; notification of changes; personnel available when changes first take effect); penalty
  $7,000,000.
- **Note on release numbers.** The Goldman options-router order is **34-75331**. `34-70329` is
  sometimes cited for it and does not correspond to it; do not use that reference.
