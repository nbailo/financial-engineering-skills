# Which price prints, and what an aggressor walking the book produces

> **Provenance**
> provider: Nasdaq, US equities, and Coinbase Exchange · surface: OUCH 5.0 order entry, and Coinbase's
> matching-engine documentation · version: OUCH 5.0, "Updated October, 2025", revision 1.05 dated 7 October
> 2025
> verified_at: 2026-08-25
> sources: https://www.nasdaqtrader.com/content/technicalsupport/specifications/TradingProducts/OUCH5.0.pdf ·
> https://docs.cdp.coinbase.com/exchange/docs/matching-engine
> verified: the OUCH 5.0 PDF was fetched and its text extracted and read directly on 2026-08-25, by two
> independent passes: the AIQ Strategy enumeration of five strategies at each of Firm, Organization, Affiliate
> and Match Any, plus `N = Disabled` and `* = use port default`, which is the row for self-match prevention
> below.
> unverified: the Coinbase resting-price sentence, the CME "FIFO as an exception to the algorithm in place"
> line and CME's priority-destroying edit set were **not** re-read on 2026-08-25 (cmegroup.com did not answer
> across repeated attempts in two independent passes), and are quoted as examples of what such a rule says.
> The tie-break ladder is this repo's description of the shape such rules take and quotes no venue.
> revalidate_when: Nasdaq publishes an OUCH revision or the AIQ Strategy enumeration changes, or Coinbase
> revises its matching-engine documentation, or before any line here is read as your own venue's answer.

An execution carries exactly one price, recorded identically to both sides, derived from the book state
the match consumed. Which price that is comes from your published rules, and the answers differ.

## Execution price convention

**Whose price prints is a rule of the model you implement, not a property of matching.** What is universal is that an execution
carries exactly one price, recorded identically to both sides, derived from the book state the match consumed. Which price that
is comes from the venue's published rules, and the published answers differ:

| Model | Execution price | Consequence |
|---|---|---|
| Continuous order book, where the operator documents it | the **resting** order's price | price improvement accrues to the aggressor |
| Call auction or cross | the single uncrossing price | no execution in that cross carries any other price |
| Midpoint or periodic-auction models | a reference-derived price | neither side's limit appears on the print |

The continuous-book convention is the one the worked examples in this file use, and at least one operator states it explicitly:
"Orders are matched against existing order book orders at the price of the order **on the book**, not at the price of the taker
order" [Coinbase Exchange matching-engine documentation, **not revalidated**]. It is not universal and it does not describe an
auction. Read your own rules, then pin the answer with a test named for the convention.

Under the continuous-book convention:

| Case | Book | Incoming | Prints |
|---|---|---|---|
| Buy aggressor improves | sell 200 @ 100.50 | buy 200 @ **101.00** | 200 @ **100.50**: buyer pays 0.50 under its limit |
| Sell aggressor improves | buy 200 @ 100.00 | sell 200 @ **99.00** | 200 @ **100.00**: seller receives 1.00 over its limit |
| Walks two levels | sell 100 @ 100.50, sell 100 @ 100.75 | buy 200 @ 101.00 | **two** prints: 100 @ 100.50 and 100 @ 100.75 |

The third row is the one that gets collapsed: an aggressor walking `k` levels produces `k` executions at `k` prices, and the
average is **derived** for a client blotter, never a print, never a feed message, never the price on an execution.

**The exhaust-the-book case.** When `aggressing_qty ≥ Σ leaves` every resting order fills in full, so no allocation rule can
change the outcome; venues therefore short-circuit it, and one is recorded as using "FIFO as an exception to the algorithm in
place" [CME, **not revalidated**]. Whether yours short-circuits or not, it is a different code path, and a suite exercising
only the configured algorithm never runs it. Generate `aggressing_qty ∈ {Σ leaves − 1, Σ leaves, Σ leaves + 1}`: the equality
case is where the off-by-one lives, and where a `Σ alloc == aggressing_qty` assertion (rather than `min(...)`) fires falsely.
