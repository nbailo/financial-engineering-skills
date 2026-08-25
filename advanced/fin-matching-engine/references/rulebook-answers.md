# The five answers your own rulebook owns, and nothing else does

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

Five answers this engine depends on are not derivable from first principles, and each has more than one
defensible answer shipping today. The invariant is not which you pick. It is that you picked it deliberately,
that it is in your published rules, and that a test named for the choice pins it.

**Separate the two kinds of statement across this skill's references.** Conservation, determinism and the
residue bound are properties of allocation and hold wherever quantity is divided. Execution-price convention,
the priority-destroying edit set, iceberg refresh eligibility, the auction tie-break ladder and
self-match-prevention semantics are **rules of a model**, and every venue-specific claim is an example of what
such a rule looks like rather than the answer for your engine. Yours comes from your own rulebook, and a test
named for the choice is what proves the code implements the rule you published.

## A venue-specific answer comes from a published rulebook, and the code names the one it implements

Five answers this engine depends on are **not** derivable from first principles, and each has more than one
defensible answer shipping today. The invariant is not which you pick, but that you picked it deliberately,
that it is in your published rules, and that a test named for the choice pins it: a convention taken from
memory produces a book consistent with itself and at odds with your own rulebook.

| Question | What is universal | The answer is yours to publish |
|---|---|---|
| What price does an execution print at? | one price per execution, identical on both sides, from the book state the match consumed | resting-price, single-uncrossing-price and midpoint models all ship |
| Which amendments destroy time priority? | the destroying set is one named set, loaded from your filed text, sole writer of the priority key | the economically invisible edits, an account-number change among them, differ by venue |
| When does an iceberg slice refresh, and may it match the aggressor that consumed the last slice? | the refresh is inside the same deterministic step as the match, never on a timer | eligibility within the same aggression is a venue rule, and both answers ship |
| How is an auction tie broken once executable volume is maximised? | maximising executable volume is the only universal criterion; a stated final rule makes the selection **total** | the imbalance, side and reference-price ladder is the shape such rules take, not any venue's quoted text |
| Both sides of a match are the same economic party | decided before any execution is emitted; a prevented match is a counterfactual, not a fill | scope and strategy are two published parts with no neutral default: Nasdaq OUCH 5.0 alone enumerates five strategies across four scope levels |
