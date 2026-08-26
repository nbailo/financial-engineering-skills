# Three messages, one word: intended total, chain-cumulative and decrement

> **Provenance**
> provider: Nasdaq, US equities · surface: OUCH 5.0 order entry and TotalView-ITCH 5.0 market data ·
> version: OUCH 5.0, "Updated October, 2025", revision 1.05 dated 7 October 2025;
> TotalView-ITCH 5.0, revision log ends 28 April 2023
> verified_at: 2026-08-25
> sources: https://www.nasdaqtrader.com/content/technicalsupport/specifications/TradingProducts/OUCH5.0.pdf ·
> https://www.nasdaqtrader.com/content/technicalsupport/specifications/dataproducts/NQTVITCHspecification.pdf
> verified: both Nasdaq PDFs were fetched and their text extracted and read directly on 2026-08-25, by two
> independent passes: Cancel carrying an "intended order size" with zero cancelling the balance; Replace
> `Quantity` as "Total number of shares liable, inclusive of previous executions and Self Match Prevention
> decremented shares on this order chain"; the 500/600 double-liability worked example; ITCH Modify effects
> being cumulative.
> unverified: nothing below rests on a source that was not read in this pass. How a counterparty's own
> execution reports carry quantity is not covered here at all: reading somebody else's reports is the client
> side, and fin-exchange-integration owns it.
> revalidate_when: Nasdaq publishes an OUCH or TotalView-ITCH revision touching Cancel, Replace or Modify
> quantity semantics.

Three messages from two protocols published by the same vendor use "quantity" for three different things.
Reading one as another moves `leaves` the wrong way, which surfaces much later as an exposure error rather
than as a parse failure. The conversions and the assertions that catch a mis-read are below.

## Quantity conventions that share one word

| Message | Field | Semantics | Convert to `leaves` |
|---|---|---|---|
| OUCH **Cancel Order** | intended order size | "the maximum number of shares that can be executed **in total** after the cancel is applied… Entering a zero here will cancel any remaining open shares" | `leaves = max(0, intended_total − cum_exec)`; `0` ⇒ `leaves = 0` |
| OUCH **Replace** | Quantity | "Total number of shares liable, **inclusive of previous executions and Self Match Prevention decremented shares** on this order chain" | `leaves = chain_total − cum_exec − stp_decremented` |
| ITCH **Order Cancel** / **Modify** | Cancelled Shares | a **decrement**; multiple Modify messages for one order reference are **cumulative** | `leaves = leaves − decrement` |

Each row is a convention **your own protocol** publishes, and the three above are one vendor's. What the rows
have in common is the shape of the mistake: a total read as a decrement, or a decrement read as a total, is a
number that parses and then moves `leaves` by an amount nobody chose. Write the conversion per inbound message
type, name it, and let nothing else touch `leaves`.

Nasdaq's worked example for Replace, read directly today: 500 entered, 100 executed; a Replace carrying 500 leaves **400**
exposed, a Replace carrying 600 exposes **500**. The stated rationale: "This may seem a bit confusing at first, but **it
inhibits the risk of double-liability throughout the order/replace chain**." Newtype these (`IntendedTotalQty`,
`ChainCumulativeQty`, `DecrementQty`) so each conversion is a named function and the compiler refuses the mix-up. Then assert
on every quantity-bearing inbound, before the book is touched:

```python
assert 0 <= new_leaves <= chain_total - cum_exec - stp_decremented
assert not (msg.is_cancel and new_leaves > order.leaves), "a Cancel may only reduce"
assert not (new_leaves > order.leaves and order.priority_seq == old_priority_seq), \
       "leaves increased without a priority reset"     # the priority-destroying set
```

The last two catch the convention mis-read, a decrement read as a total or the reverse, as `leaves` moving the wrong way, long
before it surfaces as an exposure error.
