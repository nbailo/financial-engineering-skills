# When an emit-path check fires, and what an assertion is allowed to claim

> **Provenance**
> provider: SEC, for the locked-and-crossed rule and one administrative order; Nasdaq, for the auction
> message that publishes a crossed state on purpose
> surface: freeze scope and the marking of a number a publisher cannot stand behind, and what a top-of-book
> assertion may claim
> version: 17 CFR 242.610 as published on 2026-08-25 · SEC Release 34-69655 (29 May 2013) · TotalView-ITCH 5.0
> verified_at: 2026-08-25
> sources: https://www.law.cornell.edu/cfr/text/17/242.610
> · https://www.sec.gov/files/litigation/admin/2013/34-69655.pdf
> · https://www.nasdaqtrader.com/content/technicalsupport/specifications/dataproducts/NQTVITCHspecification.pdf
> verified: the locked-and-crossed paragraph of Rule 610 and its exception clause, quoted below; paragraph 31
> of Release 34-69655, quoted below, including the "non-firm" marking on the SIP quote; the ITCH NOII Paired
> Shares definition, quoted below.
> unverified: nothing quoted here. Which paragraph letter the locked-and-crossed provision carries has moved
> before and is stated below as it was read, not as older writing cites it.
> revalidate_when: Rule 610 is amended or re-lettered again (in the text read here the fee paragraph is (d)
> and locking and crossing is (e), which is not where older writing puts them), or your venue's rulebook
> changes which session states permit a locked or crossed quote.

What a publisher does with a number that failed a check: how far to freeze, and what to say while frozen. It
carries the catalogue of emit-path checks with the halt level each implies, the planted breaks that prove each
detector works, and the one assertion that is a rulebook fact rather than arithmetic.

## Contents

- The obligation both halves of this file specialise
- Breach policy: freeze scope, the ban on clamping, and never presenting a bad number as authoritative
- The assertion table: what to assert on the emit path and the halt level each implies
- Where the checks live, which side of the matcher seam owns them, and how to test that they fire
- Crossed and locked books: the rulebook decides what the assertion may say

---

## The obligation

**The aggregate you publish is checked on the path that publishes it.**
Both halves of this file specialise that. The first is what to do when the check fires, which is the breach
policy below. The second is what the check may claim in the first place, which is a rulebook question rather
than a law of arithmetic, and is the crossed-and-locked section at the end. Why the default hand-written
decrement fails in the profile you ship is in this skill's emit checks reference.

## Breach policy

On an error from an emit-path check, three rules, in this order:

- **Halt the transformation at the smallest scope that contains it.** Freeze the affected instrument, not the
  channel and not the process. A process-wide halt on a single bad level converts one wrong number into an
  outage for every consumer of every instrument.
- **Do not publish the level, and say that you are not publishing it.** Withholding on its own is invisible.
  A channel heartbeat keeps arriving and proves only that the channel is alive; it says nothing about one
  instrument, so it cannot age or invalidate the book a consumer holds for that symbol, and their staleness
  gate cannot separate a frozen instrument from a quiet one. They keep the last book you sent, indefinitely.
  Emit an explicit unavailable or invalidation event scoped to the instrument, and state in the specification
  what it invalidates and what ends it. Publishing a wrong number is worse than both, which is why
  withholding is still the first move and the announcement is the second.
- **Never clamp to zero.** A clamp is fabricated depth with no exception attached, and it is the single change
  most likely to be proposed as the fix, because it makes the symptom disappear.
- **A corrupted or saturated aggregate is never presented as authoritative.** This is the rule the other three
  exist to serve, and it is the one a hurried fix breaks. A value that failed a check, a value that was
  clamped, and a value a saturating operator produced are all numbers with the exception thrown away, and
  publishing any of them as ordinary depth tells every consumer it is as good as the number before it. Two
  outcomes are allowed: withhold it, or publish it marked, in a field the specification defines, as not firm
  and not usable for a priced decision. The SEC order below records a venue doing exactly the second thing
  with a quote it could not stand behind, and the marking is what kept the bad quote out of the national best
  bid and offer. Silence would not have.

Saturating instead of checking is a legitimate choice in exactly one situation: where a panic would abandon
in-flight obligations, so continuing with a defined value is safer than stopping. Where you saturate, the
saturation is an event on the same feed and in the same operational record, and the saturated figure is
marked, because a silent saturation is a fabricated number with the exception thrown away.

Where nothing is in flight, the opposite choice is right: stop. An assertion that halts turns a correctness
bug into a liveness bug, and a liveness bug is visible to everybody within seconds. A debug assertion on a
published aggregate is neither: it is a correctness bug with the detector compiled out of the build you ship.

## The assertion table

| Assertion | Expression | On breach |
|---|---|---|
| Level conservation | `published_qty(level) == Σ leaves_qty of resting orders at that level`, recomputed rather than accumulated | Freeze the instrument; do not publish |
| No underflow | a checked subtraction on every aggregate that leaves the process | Freeze the instrument; do not publish |
| Not crossed | the comparison your rulebook justifies, gated on session state, on every top-of-book publish | Freeze the instrument; publish the invalidation |
| Depth against executions | `Δ total_qty(level) == Σ executed qty at that level this cycle` | Freeze the instrument; escalate |
| Snapshot join point | `snapshot.as_of <= last_applied_sequence` at emit | Withhold the snapshot cycle |
| Volume conservation | `Δ session_volume == Σ volume-eligible quantity this cycle` | Freeze the statistic; do not publish |

Two properties of this table matter more than its rows. The first is that level conservation is stated as a
recomputation, not as a running total compared against itself: an aggregate checked against another aggregate
maintained by the same increments agrees with itself while both drift. The second is that every row names a
freeze scope, so a breach has a defined blast radius before it happens rather than a decision made under
pressure afterwards.

## Where the checks live, and how to test that they fire

Put the assertions in the encoder or the publisher, on the values being written, and not in the matcher. Two
reasons: the matcher's state can be right while the published projection is wrong, and a check inside the
matcher is a check on the path that has an alternative source of truth, whereas the published bytes have none.

Ownership runs one way across that seam and it is worth stating as a rule, because the tempting fix breaks it.
When an emit-path check fires, the publisher withholds and announces. It does not reach back into the book to
repair the aggregate, recompute the matcher's state, or suppress the event that produced the breach. A
publisher permitted to correct what it publishes is a second system of record for the same fact, and the two
records diverge silently, because nothing compares them. The publisher's output is a projection, and the fix
for a wrong projection is upstream of it.

Test the detector, not the happy path. For each row of the table, plant the corresponding break and assert the
system refuses to publish:

- Decrement a level by more than it holds and assert three things together: the instrument freezes, no depth
  message is emitted, and the explicit unavailable or invalidation event for that instrument is. The third
  assertion is the one usually missing, and without it the test passes on a publisher that goes silent, which
  is the failure the event exists to prevent.
- Assert the same event ends: drive the instrument back to a good state and assert the feed says so, because
  an unavailable signal with no defined end leaves every consumer holding an invalid book forever.
- Force the cached best bid above the best ask, in a session state where your rulebook forbids it, and assert
  the top-of-book publish is withheld and the invalidation event is emitted. Repeat in a state where the
  rulebook permits it and assert the publish proceeds.
- Stamp a snapshot with an as-of ahead of the applied sequence and assert the cycle is withheld.
- Emit an execution that the level aggregate does not account for and assert the mismatch is caught in the
  same cycle rather than at the next reset.

A test that only exercises the correct path proves the arithmetic, which was never in doubt. The property
under test is that the check exists, runs in the profile you ship, and stops the publish.

## Crossed and locked books

`best_bid <= best_ask` is the right assertion for a continuous executable book whose rules forbid a crossed
state, and the wrong assertion everywhere else. Crossing and locking are rulebook facts, not arithmetic ones,
which is why Regulation NMS does not forbid them outright. Rule 610's locked-and-crossed paragraph, which is
paragraph (e) in the text read on 2026-08-25 and is cited as (d) in older writing, requires each national securities exchange and national securities association to "establish, maintain, and
enforce written rules" obliging members to reasonably avoid displaying quotations that lock or cross protected
quotations, and it carves out "displaying quotations that lock or cross any protected or other quotation as
permitted by an exception contained in its rules". The exceptions are part of the rule, and they live in a
rulebook rather than in the comparison operator.

Three cases the flat assertion gets wrong:

- **An auction or pre-open book crosses by design.** Buy and sell interest that would execute rests on both
  sides until the auction runs, which is what an auction is for. Nasdaq publishes the fact on the same feed:
  the NOII message carries Paired Shares, "the total number of shares that are eligible to be matched at the
  Current Reference Price", before the cross has run.
- **A venue whose rules permit a locked market.** `bid == ask` passes `<=` and fails `<`, so the operator you
  choose is itself a statement about your rulebook. Write down which one you meant.
- **An aggregated or consolidated view.** A book assembled from several independently quoting venues locks and
  crosses in normal operation, and an assertion inherited from a single-venue publisher fires on healthy data.

So assert what your rulebook forbids, gate the assertion on the session state, and say in the specification
which states it holds in. In the state where crossing is forbidden the check is still worth what it costs,
which is one comparison on the bytes about to leave. Nasdaq's Facebook cross on 18 May 2012 is the worked
example: after the cross was marked in error, "the Prop Feed showed a stale, crossed quote (bid price higher
than ask price) for Facebook on the 'top of book' because orders from the cross were still appearing in the
Prop Feed" (SEC Release 34-69655, paragraph 31). Two details of that paragraph matter more than the headline.
The state was not impossible, it was stale: a published projection that had stopped tracking its source. And
the same paragraph records what a publisher can do about a quote it cannot stand behind, because the crossed
top of book reached the SIP "marked 'non-firm' such that it was not included in the SIP's calculation of the
national best bid and offer". An explicit marking travels with the data. Silence does not.

The check belongs at the emit boundary, not in the book. A book that is internally consistent can still be
serialised into a crossed quote by a stale cached best price, a partially applied update, or a snapshot copied
across two locks. The point of the check is that it runs on the bytes about to leave.
