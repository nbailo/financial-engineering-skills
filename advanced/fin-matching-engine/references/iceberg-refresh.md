# Iceberg refresh: the arithmetic, the requeue, and what the feed reveals

> **Provenance**
> provider: Nasdaq, US equities · surface: OUCH 5.0 order entry and TotalView-ITCH 5.0 market data ·
> version: OUCH 5.0, "Updated October, 2025", revision 1.05 dated 7 October 2025; TotalView-ITCH 5.0, revision
> log ends 28 April 2023
> verified_at: 2026-08-25
> sources: https://www.nasdaqtrader.com/content/technicalsupport/specifications/TradingProducts/OUCH5.0.pdf ·
> https://www.nasdaqtrader.com/content/technicalsupport/specifications/dataproducts/NQTVITCHspecification.pdf
> verified: both PDFs were fetched and their text extracted and read directly on 2026-08-25, by two
> independent passes. OUCH 5.0: Replace always resetting time priority; the partial Cancel that retains time
> priority; Modify type M as priority-preserving and decrease-only, with "Increasing share amount is not
> allowed and requests to do so will be ignored"; Order Priority Update (type T) assigning a new order
> reference number. TotalView-ITCH 5.0: Order Replace omitting side, symbol and attribution; display shares
> reaching zero killing the order.
> unverified: the CME rows, the priority-destroying edit set and the display-quantity refresh and requeue
> rules were **not** re-read on 2026-08-25 (cmegroup.com did not answer across repeated attempts in two
> independent passes) and are quoted as an example of what such a rule states.
> revalidate_when: Nasdaq publishes an OUCH or TotalView-ITCH revision that touches Replace, Modify, Cancel or
> Order Priority Update, or before any CME-derived line here is copied into code.

A display-quantity order rests with display no larger than total. The refresh arithmetic and the requeue
position are rulebook entries; what is universal is when the refresh happens and what it must not leak.

## Iceberg and reserve orders

The refresh arithmetic and the requeue position are **rulebook entries**, and the two below are CME's, **not revalidated on 2026-08-25** and quoted as an example of what such a rule states
rather than as an answer you may implement: **refresh quantity** as the lesser of the configured display quantity, the
remainder when the remainder is ≤ display quantity, or the remaining display quantity on a partial fill; and **requeue at the
back**, "the Display Quantity order's priority is refreshed to be the lowest of the remaining orders at the price level (order
is placed at the end of the queue)" [CME Globex Matching Algorithm Steps]. What is universal is only what follows.

1. The refresh happens in the **same deterministic step** as the match that consumed the slice: never a timer, never a
   background task; either produces a book replay cannot reproduce. The refreshed slice takes a **new** `priority_seq` from the
   sequencer that numbered the aggressing command, so replay reproduces the requeue position exactly.
2. **Whether the refreshed slice can match the *same* aggressor that consumed the previous slice is a venue rule, and is not
   established by the sources behind this file.** If it is eligible within the same event, an iceberg fills twice while displayed
   orders behind it get nothing. Decide it, publish it, pin it with a replay test whose name states the choice
   (`iceberg_refresh_not_eligible_within_same_aggression`).

On an ITCH-shaped feed the consumer sees only display shares ("when the number of display shares for an order reaches zero, the
order is dead and should be removed from the book" [Nasdaq TotalView-ITCH 5.0]), so a publisher emitting Delete on exhaustion
and Add on refresh leaks the reserve as a repeating Add/Delete at one price. State in your feed spec that hidden quantity
exists and how a refresh appears.
