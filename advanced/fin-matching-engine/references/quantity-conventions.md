# Three messages, one word: intended total, chain-cumulative and decrement

> **Provenance**
> provider: Nasdaq, US equities · surface: OUCH 5.0 order entry and TotalView-ITCH 5.0 market data, with one
> FIX 4.4 field · version: OUCH 5.0, "Updated October, 2025", revision 1.05 dated 7 October 2025;
> TotalView-ITCH 5.0, revision log ends 28 April 2023
> verified_at: 2026-08-25
> sources: https://www.nasdaqtrader.com/content/technicalsupport/specifications/TradingProducts/OUCH5.0.pdf ·
> https://www.nasdaqtrader.com/content/technicalsupport/specifications/dataproducts/NQTVITCHspecification.pdf ·
> https://www.onixs.biz/fix-dictionary/4.4/tagNum_151.html (third-party FIX dictionary, not a primary source)
> verified: both Nasdaq PDFs were fetched and their text extracted and read directly on 2026-08-25, by two
> independent passes: Cancel carrying an "intended order size" with zero cancelling the balance; Replace
> `Quantity` as "Total number of shares liable, inclusive of previous executions and Self Match Prevention
> decremented shares on this order chain"; the 500/600 double-liability worked example; ITCH Modify effects
> being cumulative.
> unverified: FIX `LeavesQty(151)` was read on 2026-08-25 in a third-party dictionary only (onixs.biz, FIX
> 4.4), never in a FIX Trading Community document, and the row below records it as such. Whether `CumQty` and
> `AvgPx` carry across a cancel/replace chain is established by nothing read here.
> revalidate_when: Nasdaq publishes an OUCH or TotalView-ITCH revision touching Cancel, Replace or Modify
> quantity semantics, or before the FIX row is relied on for a counterparty.

Three messages from two protocols published by the same vendor use "quantity" for three different things,
and a fourth reading arrives over FIX. Reading one as another moves `leaves` the wrong way, which surfaces
much later as an exposure error rather than as a parse failure. The conversions and the assertions that catch
a mis-read are below.

## Quantity conventions that share one word

Three messages from two protocols published by the same vendor use "quantity" for three different things.

| Message | Field | Semantics | Convert to `leaves` |
|---|---|---|---|
| OUCH **Cancel Order** | intended order size | "the maximum number of shares that can be executed **in total** after the cancel is applied… Entering a zero here will cancel any remaining open shares" | `leaves = max(0, intended_total − cum_exec)`; `0` ⇒ `leaves = 0` |
| OUCH **Replace** | Quantity | "Total number of shares liable, **inclusive of previous executions and Self Match Prevention decremented shares** on this order chain" | `leaves = chain_total − cum_exec − stp_decremented` |
| ITCH **Order Cancel** / **Modify** | Cancelled Shares | a **decrement**; multiple Modify messages for one order reference are **cumulative** | `leaves = leaves − decrement` |
| FIX **ExecutionReport** | `LeavesQty(151)` | "Quantity open for further execution. If the OrdStatus <39> is 'Canceled', 'DoneForTheDay', 'Expired', 'Calculated', or 'Rejected' … then LeavesQty <151> could be 0, otherwise LeavesQty <151> = OrderQty <38> - CumQty <14>" | take `LeavesQty` as given; never recompute it from the quantity you submitted |

The FIX row was read on 2026-08-25 in a third-party dictionary (onixs.biz, FIX 4.4), **not** in a FIX Trading Community
document, so treat the wording as a prompt to open your counterparty's rules of engagement rather than as the protocol. The
identity is also conditional on `OrdStatus`, which is the half that gets dropped when it is quoted as `OrderQty = CumQty +
LeavesQty`. **Whether `CumQty` and `AvgPx` carry across a cancel/replace chain is not established by anything read here**: it
is a per-counterparty answer, and reading it wrong moves `leaves` by the whole executed quantity.

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
