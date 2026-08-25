# A prevented match is a counterfactual, and what you report about it

> **Provenance**
> provider: Nasdaq, US equities · surface: OUCH 5.0 order entry, the AIQ tags and the AIQ Canceled message ·
> version: OUCH 5.0, "Updated October, 2025", revision 1.05 dated 7 October 2025
> verified_at: 2026-08-25
> sources: https://www.nasdaqtrader.com/content/technicalsupport/specifications/TradingProducts/OUCH5.0.pdf ·
> https://www.cftc.gov/PressRoom/PressReleases/8369-21
> verified: the OUCH 5.0 PDF was fetched and its text extracted and read directly on 2026-08-25, by two
> independent passes: the AIQ Strategy enumeration of Decrement Both, Decrement Both No Details, Cancel
> Oldest, Cancel Newest and Use Remover, offered at each of Firm, Organization, Affiliate and Match Any, plus
> `N = Disabled`, `* = use port default` and a separate AIQ Group ID; the AIQ Canceled fields Decrement Shares
> and Quantity prevented from trading, and the note on when the two diverge.
> unverified: Use Remover's semantics are not stated on the pages read and were not established. The Coinbase
> Exchange STP vocabulary, Kalshi's `self_trade_prevention_type` values and Binance `preventedQuantity` with
> its `TRANSFER` mode were recorded in earlier passes and not re-read here, so they are omitted rather than
> repeated as facts. CFTC v. Coinbase (March 2021) was not re-read and is carried on its inline attribution.
> revalidate_when: Nasdaq publishes an OUCH revision, or the AIQ Strategy enumeration or the AIQ Canceled
> fields change, or before this file is read as any venue's answer other than Nasdaq's.

What a self-match prevention actually produces is not a trade: no fill, no execution id, no volume. What
it does produce is a message with two quantities on it that diverge, and a scope you published.

**Report the counterfactual, as a counterfactual.** Nasdaq's AIQ Canceled message carries `Decrement Shares` ("incremental, not
cumulative"), `Quantity prevented from trading` ("Shares that would have executed if the trade would have occurred"), the price
it would have traded at and the liquidity flag it would have earned, and states when the first two diverge: "For 'Decrement
both' they are always the same. For 'Cancel oldest' they will be different if the incoming order is smaller than the resting
order." Above, cancel-oldest gives `Decrement Shares = 500` against `prevented = 300`.

- **A prevented match is not a trade.** No fill to either side, no `ExecID`, no match number, **excluded from published
  volume**. CFTC v. Coinbase (March 2021, $6.5M): two internally operated programs "matched orders with one another … resulting
  in trades between accounts owned by Coinbase", and that volume propagated into CME's Bitcoin Real Time Index, CoinMarketCap
  and the NYSE Bitcoin Index.
- **Scope is published, not assumed.** "The account family" is not a universal scope, and neither is the session, the strategy
  or the API key: OUCH 5.0's Firm, Organization, Affiliate and Match Any levels plus a Group ID are one venue's answer, and a
  different venue partitions differently. Whatever you publish, the decision is made **before** any execution is emitted for that
  pair, inside the deterministic step, and prevented quantity enters the remaining-quantity identity (OUCH Replace's total is
  "inclusive of previous executions **and Self Match Prevention decremented shares**"). Decrementing for STP without recording
  `stp_decremented` computes the wrong exposure on the chain's next replace.
