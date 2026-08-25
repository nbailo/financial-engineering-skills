# Deriving a band from the right price, and refusing when the price is absent

> **Provenance**
> provider: US SEC and the UK FCA · surface: the administrative order against Goldman Sachs of 20 August 2013,
> and the Final Notice to Citigroup Global Markets of 17 May 2024 · version: SEC Rel. 34-70212; FCA Final
> Notice, Citigroup Global Markets Limited, 17 May 2024
> verified_at: not established
> sources: https://www.sec.gov/litigation/admin/2013/34-70212.pdf ·
> https://www.fca.org.uk/publication/final-notices/citigroup-global-markets-limited-2024.pdf
> verified: nothing here was re-read from a primary source in this pass.
> unverified: Goldman ¶25 and ¶30 on the pre-open band derived from the highest closing price of any listed
> option, and FCA ¶4.27 and ¶4.30 on the benchmark index price defaulting to -1, are carried on their inline
> attributions from an earlier pass. Both are cited as illustrations of a property, never as a procedure.
> revalidate_when: before either paragraph number is repeated outside this repository, or if either regulator
> republishes its document at a different URL.

A price band is a function of one instrument's own reference price and the current session state, and both
recurring failures are about that input rather than about the arithmetic: a bound aggregated over the whole
universe, and a missing price that becomes a number instead of a refusal.

## An inbound order is validated against its own instrument's economics

Specialises *hard limits* on the last hop before the book. Reject any order priced more than a configured band
away from **that instrument's own** reference price, by one derivation called from every session-state code
path, never from a cross-universe aggregate. A missing reference price rejects; it never substitutes a
sentinel, and a sentinel is never multiplied. Goldman Sachs, SEC order of 20 August 2013 ¶25 and ¶30: the
pre-market band's upper bound came from the highest closing price of *any* listed option, so a $1 order in any
name passed.

## Band derivation

A price band is a function of **that instrument's own reference price** and the current session state. One
derivation, called from every session-state code path, with the reference source stated per state.

| Session state | Reference price source |
|---|---|
| Continuous | the venue best, or the plan's reference price where one exists |
| Pre-open and post-close | the prior session's close **for that instrument**; if unavailable, reject |
| Auction or cross | the indicative price, or the auction collar reference |
| Halted | the last valid reference before the halt, frozen |

Two failure shapes recur, and both are about the input rather than the arithmetic.

**A bound aggregated over the universe is vacuous for every instrument except the most expensive one.**
Goldman Sachs, ¶25 and ¶30: in-hours the band was derived per series, but the pre-open path took another
branch whose upper bound was "1.5 times the highest closing price from the prior day **for any listed
option**", so orders "fell between $0.01 and $3,090" and passed in every name. The special case, not the
main path, is where the derivation gets replaced.

**A missing price rejects; it never substitutes a sentinel, and a sentinel is never multiplied.** FCA Final
Notice, Citigroup Global Markets, 17 May 2024, ¶4.27: an unavailable external feed meant a benchmark index
price "defaulted to -1", so a screen rendered the product of quantity and that sentinel as a large negative
notional that looked plausible to the trader; ¶4.30 records that the same missing data blanked the one
basket-level check, which then proceeded anyway. A control whose input is absent must fail closed, and a
control that silently degrades to no control is worse than one that is absent, because it reports success.
