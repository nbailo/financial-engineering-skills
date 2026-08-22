# May 6 2010 Sell Algorithm — a participation-rate target driven by a metric its own fills inflated, with no price bound and no time bound (2010-05-06)

**Domain:** Execution algorithms, futures and equities | **Loss:** the report publishes no single-firm figure; 75,000 E-Mini contracts (~$4.1bn) sold in 20 minutes, >20,000 trades / 5.5 million shares subsequently broken | **Failure class:** Pricing & execution integrity | **Skill:** fin-exchange-integration

## What happened

A large fundamental seller executed a 75,000-contract E-Mini S&P sell program through an automated
algorithm. The E-Mini fell sharply, equities dislocated, buy-side depth collapsed, and market and
marketable-limit orders executed against stub quotes at prices tens of percent away from prevailing
values. The CME's Stop Logic Functionality paused E-Mini trading for five seconds at 2:45:28 p.m.,
after which prices stabilised. Over 20,000 trades representing 5.5 million shares were later broken
under the exchanges' clearly-erroneous rules.

**Two things this report does not say.** The string "**Waddell**" appears **zero** times; the report
identifies the seller only as "a large fundamental trader (a mutual fund complex)" and "the large
Fundamental Seller". The identification is press reporting, not a regulatory finding. The string
"**trillion**" also appears **zero** times; there is no "$1 trillion of market value" figure in it.
The report's own quantifications are ~2 billion shares traded between 2:40 and 3:00 p.m. with volume
"exceeding $56 billion", and "over 98% of all shares were executed at prices within 10% of their
2:40 p.m. value".

## Root cause, in code terms

**The algorithm's rate input was a function its own output inflated.**

> "This large fundamental trader chose to execute this sell program via an automated execution
> algorithm ('Sell Algorithm') that was programmed to feed orders into the June 2010 E-Mini market
> to **target an execution rate set to 9% of the trading volume calculated over the previous
> minute, but without regard to price or time.**" (p.2)

The loop closes on itself: selling raises one-minute volume; higher volume raises the 9% target;
the higher target produces more selling.

> "**The Sell Algorithm used by the large Fundamental Seller responded to the increased volume by
> increasing the rate at which it was feeding the orders into the market**, even though orders that
> it already sent to the market were arguably not yet fully absorbed by fundamental buyers or
> cross-market arbitrageurs. In fact, **especially in times of significant volatility high trading
> volume is not a reliable indicator of market liquidity.**" (p.14)

**The report names the two missing throttles verbatim**, in footnote 24:

> "…some traders feed orders into the market based on volume-weighted average price ('VWAP')
> algorithms that are designed to obtain an average price over a specified period of time and
> therefore have a **built-in time throttle that prevents an unexpectedly fast execution that can
> cause significant market impact. Other such throttles include a limit price that would prevent
> executions at unfavorable prices.**"

**The comparison run is more qualified than the folklore.** The same firm had executed a comparable
program before using "**a combination of manual trading entered over the course of a day and several
automated execution algorithms** which took into account price, time, and volume", and on that
occasion "it took **more than 5 hours** … to execute **the first 75,000 contracts** of a large sell
program". On 6 May the volume-only algorithm executed the program "in **just 20 minutes**" (p.2).
The contrast is real; note that it is 5 hours for the *first* 75,000 contracts of a larger program,
and that the earlier execution was not purely algorithmic.

**Volume was not liquidity, and the report measures it.** Between 2:45:13 and 2:45:27, HFTs traded
over 27,000 contracts — about 49% of total volume — "while **buying only about 200 additional
contracts net**". At the same time buy-side depth in the E-Mini "fell to about **$58 million, less
than 1% of its depth from that morning's level**" (p.15). A rate driver reading volume saw a market
at record activity; a rate driver reading depth at the prices it intended to trade would have seen
the opposite.

## The invariant that was violated

```
# every execution algorithm
algo.has_price_bound  AND  algo.has_time_bound            # in addition to any participation target
NOT: participation_rate is the only constraint

# the rate driver
rate_input  is independent of  own_executions             # exclude own fills, or damp
i.e.  d(rate_input)/d(own_fill)  ==  0   (or bounded and damped)

# liquidity measurement
liquidity_estimate := depth_at(intended_prices)
NOT: liquidity_estimate := traded_volume
```

## Could an AI coding agent reviewing the diff have caught it?

**Yes — categorically, and with the primary source on its side.**

The signal in the diff is structural and requires no market knowledge: an execution algorithm class
or configuration object that carries a participation-rate field (`participation_rate`,
`target_pct_of_volume`, `pov`) and **no** `limit_price` / `max_price_deviation` field and **no**
`max_duration` / `min_interval` / `end_time` field. That is a complete, decidable check on the
structure of the config. An agent can state the finding categorically because the official report
names the two missing throttles itself — this is one of the rare cases where the regulator has
already written the review comment.

The second finding is equally mechanical: a rate computation whose input is a market-wide aggregate
(`volume_last_minute`, `trades_last_n`) that the algorithm's own fills contribute to, with no term
subtracting or damping own-trade contribution. The reviewing question is "does this algorithm's
output appear in its own input?" — a data-flow question, answerable from the source.

The third is the liquidity proxy: `volume` used where `depth` is meant. Visible wherever a sizing
decision reads a traded-volume series rather than book depth at the prices it intends to cross.

What an agent could **not** do is adjudicate the causal narrative. CME objected that the trades
were ~1.3% of E-Mini volume; Nanex argued the algorithm always posted above the market and never
took liquidity; the 2015 Sarao spoofing prosecution offers a third account. **None of that changes
the rule.** Whatever else was happening, a participation-rate target with no price bound and no time
bound is independently indefensible, and the report names the missing throttles. Cite the rule, not
the causal claim.

## The rule

> **MUST — An execution algorithm must carry a price bound and a time bound in addition to any
> participation-rate target.** A participation-only algorithm must be rejected in review.

> **MUST — Never use a metric that the algorithm's own actions inflate as the algorithm's rate
> input** without excluding or damping the own-trade contribution.

> **SHOULD — Do not use traded volume as a proxy for available liquidity.** Measure book depth at
> the prices you intend to trade.

## Sources

- **CFTC/SEC staffs, *Findings Regarding the Market Events of May 6, 2010*, Report to the Joint
  Advisory Committee on Emerging Regulatory Issues, 30 Sept 2010** —
  <https://www.sec.gov/files/marketevents-report.pdf>. **Primary.** Establishes p.2 (the 9%
  trailing-one-minute target "without regard to price or time"; 75,000 contracts; the >5-hour
  comparison run and its composition; the 20-minute execution), p.14 (the positive-feedback
  paragraph and "high trading volume is not a reliable indicator of market liquidity"), p.15 (27,000
  contracts / 49% / ~200 net between 2:45:13 and 2:45:27; buy-side depth ~$58 million, <1% of the
  morning's level), p.4 (the CME Stop Logic five-second pause at 2:45:28), fn 24 (the time throttle
  and the limit price, verbatim), §II.3.b (>20,000 trades / 5.5 million shares broken).
- **Verified absences** (exhaustive string search of the full report text): "Waddell" — zero
  occurrences in the report text; "trillion" — zero occurrences in the report text.
- **Contested attribution, for completeness and not relied on:** CME Group's rebuttal that the
  trades were ~1.3% of E-Mini volume; Nanex's argument that the algorithm "never took nor required
  liquidity"; the 2015 DOJ prosecution of Navinder Sarao for spoofing the same contract. These are
  secondary and they do not bear on the rule.
