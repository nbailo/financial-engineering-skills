# Citigroup Global Markets — a unit-vs-notional field error whose confirmation screen was neutralised by a price feed defaulting to −1 (2022-05-02)

**Domain:** Delta 1 basket trading, pre-trade risk controls | **Loss:** US$48m to CGML (FCA ¶2.8); FCA penalty £27,766,200, stated as £39,666,000 before the settlement discount | **Failure class:** Sentinel escape (with unit ambiguity and a missing aggregate control) | **Skill:** fin-exchange-integration

## What happened

A CGML Delta 1 trader intended to sell a US$58m basket hedging an index position. A US$444bn basket
of 349 equity orders across 13 European countries was created instead. Per-item hard blocks
suspended 58 orders worth US$248bn; 291 orders worth US$196bn proceeded to the CitiSmart algorithm,
of which 284 orders / US$189bn were accepted. US$1.4bn executed before the trader cancelled at
09:10:30. The FCA records one quantified market effect: "the **MSCI Europe ex UK Index fell just
over 4%**, compared to its previous close, within five minutes of the erroneous basket starting to
trade" (¶2.8). The frequently repeated "OMX Stockholm 30 fell ~8%" figure is **not** in the Notice.

## Root cause, in code terms

**The input error.**

> "Rather than entering 58 million into the '**Notional**' field, which would have created a basket
> of equities with a notional of US$58m, the trader entered 58 million into the '**Quantity**'
> field. This had the effect of creating **a basket equivalent to 58 million units of the Index,
> which equated to 349 stock orders, across 13 European countries, with a total notional size of
> US$444bn.**" (¶4.26)

Two adjacent fields accept the same literal and mean different things. That alone is the classic
units defect. But the fat finger would have been caught, and the decisive mechanism is the next
paragraph — **the single most implementable finding in the Notice, and one absent from every
secondary account of this incident**:

> "Ordinarily, the Value at Benchmark field (ValAtBM) on the PTE screen displays the value of the
> relevant basket at a specified benchmark … In this case, PTE defaulted to the option 'Strike'.
> The default 'Strike' option was programmed to determine the price of the Index at the prior day's
> close, by reference to an **external data feed**. However, **as data from that external feed was
> unavailable, the price of the value of the Index instead defaulted to -1** rather than the
> benchmark price which was US$7684.40. **The quantity of units was therefore multiplied by -1.**
> There were number of other fields on the PTE screen in which the total notional value of the
> basket was correctly displayed. However, the trader only checked the the ValAtBM on PTE to
> confirm the size of the basket. When the trader checked the value of the inputted basket, they
> were presented with a figure of **negative 58 million** for the value of the basket (58 million
> multiplied by -1). **The trader saw a ValAtBM of -58,000,000, which was the number they expected
> to see**, and thus they clicked Execute … **Had the data feed been available, ValAtBM would have
> shown a basket of approximately US$444bn** i.e., the true notional value of the basket." (¶4.27)

Three compounding code defects in one paragraph:

1. A missing-data path substituted the sentinel `-1` into a **price** variable rather than failing.
2. `quantity × price` was computed and rendered with no validity check on `price`.
3. The resulting display was coincidentally equal in magnitude to the user's own input, so the
   confirmation step confirmed nothing. The control did not merely fail to fire — it actively
   reassured.

**The same missing feed blinded the one basket-level control that existed:**

> "The orders in the basket triggered a US$100m wave notional soft block. However, on the day of the
> trading incident **due to the lack of market data with which to calculate the index value, the
> wave notional soft block did not display the notional value of the order. It stated, 'Due to lack
> of market data, Wave notional cannot be found'.**" (¶4.30)

**Per-item limits do not bound an aggregate.** The hard blocks were Order Notional US$2bn *per item*
and Order Quantity 200m shares *per item*; the soft blocks were Wave Notional US$100m (basket
total), Order Notional US$50m per item, Order ADV 30% per item (¶4.16–4.17). 349 items each under
the per-item cap summed to US$444bn.

**The hard block that would have stopped it existed in another region for nine years:**

> "**A Wave Notional hard block** that would cancel basket trades that exceeded a total value limit
> … This was in contrast to the Firm's **New York Delta 1 Trading Desk that did have a wave notional
> hard block of this type, which was first implemented in May 2013. The wave notional hard block was
> not rolled out to the EMEA instance of PTE.** As at 2 May 2022 the level … for the New York Delta
> 1 desk was set at **US$4bn**." (¶4.18(a))

> "**Critically, had a basket level wave notional hard block limit been in place within PTE the
> trading incident would not have occurred** … **A control of this nature had been present in the US
> for some nine years.**" (¶4.33)

A second parity gap: a 95% ADV hard block existed for DMA flow and not for DSA flow — "If the same
ADV hard block limit of 95% which applied to DMA flows had been applied to DSA flows, the hard block
would have cancelled all the orders" (¶4.34).

**The warning dialog was unreadable and overridable in one click.** "At 08:56 a 'Trade Limit
Warning' pop-up appeared … This presented **711 warning messages**, listed in a single alert. **Only
the first 18 lines** … were visible in the pop-up without scrolling … **in batches of 18 at a
time**" (¶4.28). The options were "**Override soft warnings**" or "**Cancel all**", and the trader
clicked Override "**without scrolling down and reviewing all the 711 warning messages**" (¶4.31).

**And it was caught by a position-versus-expectation mismatch, not by any control.** "At 09:07, the
trader reviewed EQRMS. The trader was **expecting to see a long US$1.075bn notional delta** …
However, **the trader saw a short delta of US$800m, which was rapidly increasing** … Recognising
something was wrong, the trader returned to PTE and discovered the error" (¶4.40).

## The invariant that was violated

```
# the decisive one
price_feed.unavailable => every computation reading it FAILS
NOT: price := -1          # or 0, or null coerced to zero, or last-known without a staleness policy

# the confirmation
confirmation_screen renders derived_notional computed independently of the field the user typed
AND refuses to render when any input to that derivation is unavailable

# limits
exists hard_limit at the level at which the user acts (basket / wave / session)
NOT: only per-item hard limits, however many items

# control parity
forall deployments d1, d2 of the same desk/product:
    controls(d1) == controls(d2)   or a dated, owned exception exists

# override affordance
no single control overrides N warnings at once
```

## Could an AI coding agent reviewing the diff have caught it?

**Yes — and the sentinel is the finding that matters most.**

The **`-1` default** is a direct, mechanical finding: a lookup against an external price feed with a
fallback that substitutes a numeric literal into a price variable. `price = feed.get(sym) or -1`,
`price = feed.get(sym, -1)`, `getPriceOrDefault(-1)` — every shape of this is greppable, and the
correct behaviour (raise, and let the caller fail closed) is unambiguous. The second half of the
finding is just as reviewable: a downstream expression `quantity * price` rendered to a user with no
validity check on `price`.

The **limits configuration** is a static comparison. An agent reading the limit config can see that
every per-item limit is `hard: true` while the only aggregate (basket-level) limit is `soft: true`.
That is a one-glance finding.

**Control parity across deployments** is mechanical diffing: the same limits file exists for two
regions and one contains a key the other does not. An agent asked "do these two environment configs
declare the same control set?" answers definitively.

The **override dialog** is visible in the UI code: a single action that clears all soft warnings,
with a list rendered into a fixed-height container and no forced scroll or per-item acknowledgement.

The **units confusion between `Quantity` and `Notional`** is visible in the form model: two numeric
fields of the same primitive type, with no derived cross-check displayed back to the user and no
unit in the type.

What an agent could not see: that the trader would check only one of several fields on the screen,
or that an alert desk had just been handed the pager because of scheduled leave (¶4.41) and did not
escalate 284 information alerts (¶4.36, ¶4.42).

## The rule

> **MUST — A missing or unavailable market-data input must fail the computation that depends on it.
> Never substitute a sentinel (`-1`, `0`, a null coerced to zero) into a price or rate variable.**

> **MUST — A confirmation screen for a money-moving or risk-taking action must display the *derived*
> economic quantity (notional, total, net effect), computed independently of the field the user
> typed, and must refuse to render when any input to that derivation is unavailable.**

> **MUST — A basket or batch operation must have an aggregate hard limit, not only per-item limits.**
> Per-item limits are satisfiable by an unbounded number of items.

> **MUST — When a control exists in one deployment of a system, its absence in any other deployment
> is a defect requiring a dated, owned exception.** Treat control parity across instances as a
> testable invariant.

> **MUST — A bulk-override affordance must not exist.** Warnings must be acknowledged individually,
> or the operation must be cancelled.

> **MUST — Never let two adjacent input fields accept the same literal with different units without
> a derived cross-check displayed back to the user.** Encode the unit in the type — `Shares`,
> `Notional<USD>` — so a quantity cannot be passed where a value is expected.

## Sources

- **FCA Final Notice, *Citigroup Global Markets Limited*, ref. 124384, dated 17 May 2024** —
  <https://www.fca.org.uk/publication/final-notices/citigroup-global-markets-limited-2024.pdf>;
  press release (22 May 2024) <https://www.fca.org.uk/news/press-releases/fca-fines-cgml-27-million>.
  **Primary.** ¶2.8 (US$48m loss; MSCI Europe ex UK Index fell just over 4% within five minutes),
  ¶4.15 (the system did not require scrolling), ¶4.16–4.17 (the hard and soft thresholds),
  ¶4.18(a) and ¶4.33 (the New York wave-notional hard block from May 2013, US$4bn as at 2 May 2022,
  never rolled out to EMEA; "the trading incident would not have occurred"), ¶4.18(b) and ¶4.34
  (the 95% ADV hard block on DMA and not DSA), ¶4.20 and ¶4.37 (the "price move on arrival" control
  calibrated at 15%, suspending only 8 orders / US$2.4bn), ¶4.26 (Quantity vs Notional), **¶4.27
  (ValAtBM, the external feed, the −1 default, and the trader seeing the number they expected)**,
  ¶4.28 and ¶4.31 (711 warnings, 18 visible, batches of 18, a single "Override soft warnings"),
  ¶4.30 ("Due to lack of market data, Wave notional cannot be found"), ¶4.36 / ¶4.41 / ¶4.42 (284
  information alerts; the desk handover at 08:48; no escalation), ¶4.38 (US$1.4bn executed),
  ¶4.39 (the funnel table), ¶4.40 (the EQRMS delta mismatch that actually caught it), ¶1.1 and
  ¶2.12 (£27,766,200; £39,666,000 without the discount; Principles 2 and 3 and MAR 7A.3.2).
- **Corrections applied.** The Notice is dated **17 May 2024**, not 22 May (that is the press
  release). Use the Notice's own funnel table (¶4.39): hard blocks suspended **US$248bn**, 291
  orders / US$196bn proceeded to CitiSmart, 7 / US$7bn were rejected there, and **US$189bn** was
  received. The widely repeated "controls stopped US$255bn" **is** the FCA's own figure — it
  appears in the Notice's narrative at ¶4.6 and in the press release — but it is a rounded
  aggregate (444 − 189) that collapses two control stages into one, so quote ¶4.39 instead. The
  "OMX Stockholm 30 fell ~8%" figure is press-sourced, not regulatory; the only index move the
  Notice quantifies is MSCI Europe ex UK, "just over 4%" (¶2.8).
- **Companion PRA action.** The PRA imposed a separate penalty of **£33,880,000** on CGML on the
  same day (22 May 2024) for related matters; this is stated in the FCA's own press release
  (above). The PRA Final Notice itself was not retrieved, so cite the amount to the FCA press
  release, not to a PRA document.
