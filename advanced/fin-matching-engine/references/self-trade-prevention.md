# Self-match prevention: scope and strategy as two published choices

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

Self-match prevention is the one decision in the matcher with no neutral default and two published halves.
Scope decides whose orders count as the same party. Strategy decides what happens when two of them meet. Both
are read from configuration per instrument, per port or per order, and neither has a default worth taking.

## Self-match prevention is venue configuration, never a compiled-in constant

The scope that decides which orders are the same party and the strategy applied when two of them meet are two
separate published choices, read per instrument, per port or per order as your protocol allows. Where a value is absent
the fallback is itself published (Nasdaq OUCH 5.0 spells the absent value `* = use port default`, and `N =
Disabled` is a value, not an absence) or the order is rejected; it is never a mode picked in code. Pin the
exact pair with a test named for it. A prevented match is not a trade: a counterfactual, never a fill and
never volume. CFTC v. Coinbase, March 2021: two internally operated programs *"matched orders with one
another"*, and that volume propagated into third-party indices.

## Self-trade prevention as the implementer

**Getting either the scope or the strategy from memory produces a print your own rulebook does not authorise,** and it is observable both in the tape and in
what the counterparty is told. So this is a rulebook entry, not an implementation detail, and the pair is pinned by a test named
for the exact choice.

Nasdaq OUCH 5.0 publishes both as order-level options with a port default, and this is the part read directly on 2026-08-25:
the `AIQ Strategy` tag enumerates **Decrement Both**, **Decrement Both No Details**, **Cancel Oldest**, **Cancel Newest** and
**Use Remover**, offered at each of four scope levels, **Firm**, **Organization**, **Affiliate** and **Match Any**, with
`N = Disabled` and `* = use port default`; a separate `AIQ Group ID` tag exists "to enable self match prevention at a more
granular level within a given matching level". Five strategies over four scopes is twenty published combinations on one venue,
which is the measure of how little a default is worth here. **Use Remover's semantics were not established in this pass**: the
name is in the enumeration and the behaviour is not stated on the pages read, so do not infer it.

Resting R = 500 and incoming I = 300, the same party under whatever scope you published, same price:

| Strategy | Resting after | Incoming after | Aggressor continues? |
|---|---|---|---|
| **decrement-both** | 200 | 0 | no (I exhausted) |
| **cancel-oldest** | cancelled in full (500 removed) | 300 | **yes** |
| **cancel-newest** | 500, untouched | cancelled in full | no |
| **cancel-both** | cancelled in full | cancelled in full | no |

Vendor vocabularies differ for the same four rows: two-letter codes on one venue, a required enum on entry at another, a
cumulative prevented-quantity field and an account-to-account transfer mode at a third. Earlier passes recorded the specific
spellings; none was re-read here, so they are omitted rather than repeated as facts. Read the vocabulary out of the venue
document you are implementing, and expect the degenerate cases (equal sizes, and which side's instruction wins when the two
differ) to be answered differently by each.
