# Rejecting before the effect, and measuring what was entered

> **Provenance**
> provider: US SEC · surface: Rule 15c3-5's adopting release, and the administrative order against Goldman
> Sachs of 20 August 2013 · version: SEC Rel. 34-63241 (3 November 2010); SEC Rel. 34-70212 (20 August 2013)
> verified_at: not established
> sources: https://www.sec.gov/rules/final/2010/34-63241.pdf ·
> https://www.sec.gov/litigation/admin/2013/34-70212.pdf
> verified: nothing here was re-read from a primary source in this pass.
> unverified: the two quoted phrases from the 15c3-5 release ("automated, pre-trade basis, before orders are
> routed" and the exposure-from-orders-entered standard) and Goldman ¶12 on the 30-minute capital calculation
> are carried on their inline attributions from an earlier pass. A US rule is cited because it states the
> property precisely; the property itself is not jurisdictional and no part of this file is legal advice.
> revalidate_when: before either quotation is repeated outside this repository, or if the SEC republishes
> either document at a different URL.

A limit is a control only if it runs synchronously in the path that creates the authoritative effect and
returns a refusal. What it measures is the second half of the same question, and measuring what came back
rather than what was entered is short of the firm's own exposure by the whole open book.

Every rule here is a property, not an operating model: the specific thresholds, the escalation path and the
people involved belong to the venue that runs the engine, and this file deliberately prescribes none of them.
What it does prescribe is where a control sits relative to the authoritative effect. Public enforcement
actions appear as illustrations of a property, never as a procedure to copy.

## Reject before the effect

A limit is a control only if it runs synchronously in the path that creates the authoritative effect and
returns a refusal. An alert, a dashboard, a post-execution screen and a periodic capital calculation all
observe the effect after it exists. The refusal is typed, it names which limit refused, and the path that
creates the effect is unreachable except through it.

**The measurement basis is what was entered, not what came back.** SEC Rule 15c3-5's adopting release (Rel.
34-63241) states it directly: controls must be applied on an "automated, pre-trade basis, before orders are
routed", and compliance is assessed "on the basis of exposure from orders entered … rather than relying on a
post-execution, after-the-fact determination". A US rule is cited because it says the thing precisely; the
property is not jurisdictional. An engine that sums executions measures a quantity smaller than its own
exposure by the size of the open working book, and the gap grows when the book grows.

The counter-example is a firm whose capital utilisation was "only calculated … every 30 minutes", alerting
on a percentage threshold, with "no automated process to prevent the entry of additional orders" on breach
(SEC order against Goldman Sachs, 20 August 2013, ¶12). Every number was correct; none of them rejected
anything. Duplicate detection belongs in the same path and is calibrated per counterparty: what is a
duplicate for one participant is normal traffic for another.
