# What keeps and what destroys queue position on an amend

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

Queue position is the promise a book makes to the orders resting in it, so the set of edits that destroys it
is a published rule applied in exactly one place, and it is the only writer of the priority key. Which edits
are in the set is a rulebook entry; that the set is data loaded from your filed text is not.

## Priority preservation matrix

**The rows below are two operators' published answers, not a universal rule**, and only the Nasdaq rows were read in this pass.
What does not vary is the structure: the priority-destroying set is one named set, loaded from your filed rule text, applied in
one place, and the only writer of the priority key. The economically invisible edits are where venues differ, and where a
reader's assumption is most likely to be wrong.

| Operation | Priority | Source |
|---|---|---|
| OUCH **Replace** (any) | **Lost. Always.** "Replacing an order always gives it a new timestamp for its time priority on the book." | Nasdaq OUCH 5.0 |
| OUCH **Cancel** reducing quantity | **Preserved.** "If you wish you simply partially cancel an order and retain its time priority, send a Cancel Order Message instead." | Nasdaq OUCH 5.0 |
| OUCH **Modify** (type M) | **Preserved**: exists "for modifications that will not affect order priority on the book"; quantity **decrease** only plus a narrow set of sell/short side changes. "Increasing share amount is not allowed and requests to do so will be ignored." | Nasdaq OUCH 5.0 |
| CME modify: quantity **increase**, **price change**, or **account number change** | **Lost** | CME Globex Matching Algorithm Steps, **not revalidated**; see below |
| ITCH **Order Replace** (what you publish) | New order reference number; remaining shares of the original no longer accessible; side, symbol and attribution are **not in the message**; the consumer retains them from the original Add | Nasdaq TotalView-ITCH 5.0 |
| System-initiated repricing | Priority may change with no client action; `Order Priority Update` (type T): "as a result of the updated priority, a new order reference number will be assigned" | Nasdaq OUCH 5.0 |

Carried from an earlier pass and **not revalidated on 2026-08-25**, so read it as an example of what such a rule says rather
than as a rule you may implement: "A modified order loses its timestamp priority when any of these values are modified:
**Increase of working quantity** of the order. **Change of price**. **Change of account number**." [CME].

An account-number change is the interesting shape whatever your venue decides: economically invisible (same instrument, side,
price, size) and it silently costs queue position, so a reader who guesses guesses wrong in one direction or the other. The
structural rule is that the set is data loaded from your filed text, never a chain of `if`s in the amend handler and never a
literal typed from memory:

```rust
// Loaded at config load from the filed rule text; the matcher never spells the set inline.
let destroying: &EditFieldSet = &rules.priority_destroying_edits;
// inside apply_amend, after order.apply_fields(edit)?:
if edit.touched().iter().any(|f| destroying.contains(f)) {
    reset_priority(order, seq);        // the ONLY writer of order.priority_seq
}
```

Then write the test that whichever edits your rules name do move the order to the back of its level, and that the others do
not. That test is the rule; the table without it is a comment.
