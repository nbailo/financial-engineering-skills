# Computing an uncrossing price, and publishing what the computation saw

> **Provenance**
> provider: Nasdaq, US equities · surface: TotalView-ITCH 5.0 market data, the LULD Auction Collar message ·
> version: TotalView-ITCH 5.0, revision log ends 28 April 2023
> verified_at: 2026-08-25
> sources: https://www.nasdaqtrader.com/content/technicalsupport/specifications/dataproducts/NQTVITCHspecification.pdf ·
> https://www.sec.gov/litigation/admin/2013/34-69655.pdf
> verified: the TotalView-ITCH 5.0 PDF was fetched and its text extracted and read directly on 2026-08-25, by
> two independent passes: the LULD Auction Collar message exists and carries a reference price, an upper and a
> lower threshold and an extension count.
> unverified: the LULD extension mechanics themselves were not established. SEC Rel. 34-69655 was not re-read
> in this pass and its paragraph numbers are carried on their inline attributions. The tie-break ladder is
> this repo's description of the shape published auction rules take and quotes no venue's filed text.
> revalidate_when: Nasdaq publishes a TotalView-ITCH revision touching the LULD Auction Collar, or before the
> tie-break ladder is read as any venue's rule.

The uncrossing algorithm over a frozen input set, the one universal criterion in it, and the four places a
venue's own filed text has to supply an answer. What you publish before the cross is a promise about the
computation, so the print is asserted against the last indicative rather than merely followed by it.

## Auction and cross computation

Given an input set already frozen, by a cutoff or by a bounded drain:

1. **Candidate prices** = every distinct limit price in the auction book, plus the reference price; market orders count at the
   extremes, not at a price of their own. The set is finite by construction and bounded by the number of distinct resting
   prices, which is what makes step 3 a scan rather than a search that can fail to terminate.
2. Per candidate `p`: `cum_buy(p)` = buy interest willing to pay ≥ `p`; `cum_sell(p)` = sell interest willing to accept ≤ `p`;
   `exec(p) = min(cum_buy, cum_sell)`; `imbalance(p) = cum_buy − cum_sell` (signed).
3. **Maximise `exec(p)`**, the only universal criterion.
4. Break ties. **The ladder below is the shape published auction rules take, not a quotation of any venue's rule, and yours
   must come from your own filed text**: minimum `|imbalance(p)|`, then the side of the residual imbalance, then proximity to
   the reference price, then a stated final rule making the selection **total**.
5. Round to tick in a **stated direction** and re-verify `exec` at the rounded price; rounding can move the price off the
   maximising point.
6. Allocate at that single price through the configured pipeline; the conservation assertion applies unchanged, plus **exactly one
   price appears on every execution in the cross**.

Assert before the print: `exec(p*) ≥ exec(p)` for every candidate, and, on the residual book, `best_bid < best_ask`. A crossed
book after an uncrossing is arithmetically impossible and one line to check; NASDAQ's proprietary feed published a crossed
top-of-book for over two hours on 18 May 2012 (SEC 34-69655 ¶31).

**Indicative price and imbalance.** What you publish pre-cross is a promise about the computation: publish indicative price,
indicative volume, imbalance quantity and side, and **assert the print against the last indicative**. NASDAQ's indicative
volume was 82 million against a 75.7 million share print, a gap "NASDAQ did not address … during the minutes and hours
following the cross" (¶27). Indicative state is also resettable state: if you publish an indicative price, publish its
clearing, and the reset mechanics belong to the feed rather than to this file.

**LULD auction collar.** A reopening auction after a LULD pause prices inside a published collar, and Nasdaq TotalView-ITCH 5.0
carries a dedicated **LULD Auction Collar** message (read directly on 2026-08-25: reference price, upper and lower thresholds,
and an extension count) so the collar is *delivered*, not derived downstream. The extension mechanics themselves are
venue-specific and **not established** here. Publish the reference price and both bounds; equals-versus-crosses at a band edge
is where the off-by-ones live.
