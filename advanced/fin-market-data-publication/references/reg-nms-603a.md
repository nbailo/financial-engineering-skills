# Timing fairness under US Regulation NMS (US-specific)

> **Provenance**
> provider: US Securities and Exchange Commission · surface: Regulation NMS Rule 603(a) as codified, and the
> Commission's own statement of what it prohibits
> version: 17 CFR 242.603 as in force on 2026-08-25; the prohibition sentence as stated in SEC Release No.
> 34-67857, September 14, 2012
> verified_at: 2026-08-25
> sources: https://www.ecfr.gov/current/title-17/section-242.603
> · https://www.sec.gov/files/litigation/admin/2012/34-67857.pdf
> pinned: the eCFR page is the current edition served on 2026-08-25; the order was downloaded and read as
> text the same day.
> verified: the current text of 603(a)(1) and (a)(2), with the "fair and reasonable" and "not unreasonably
> discriminatory" standards, was read on eCFR on 2026-08-25. The sentence describing what the rule prohibits
> is the order's own, in section III, and was read that day.
> unverified: the codified rule text does not itself state the timing prohibition; that reading is the
> Commission's, given in the Regulation NMS adopting release at 70 Fed. Reg. 37,496, 37,567 and 37,569, and
> quoted in the order. The adopting release itself was not opened here, so it is cited at second hand.
> Nothing was checked about any later enforcement action, about the market data infrastructure amendments now
> restructuring paragraph (b), or about how another jurisdiction's rules compare.
> revalidate_when: 17 CFR 242.603 is amended again; the Commission issues guidance that supersedes this
> reading; the consolidated-feed architecture the rule assumes is replaced by the competing-consolidator
> model; your venue comes under a regulator other than the SEC.

**This file states a United States requirement and applies only to venues subject to it.** Regulation NMS
governs exchanges and other trading centres in the US national market system for equities and options. Most
venues in the world are not subject to it, and nothing in this file should be read as a universal property of
market-data architecture. The generic architectural rule, that publication is deterministic and the fan-out
happens once after the sequence is assigned, is in the skill body and holds everywhere. Whether one
destination is legally required to be no later than another is a jurisdictional question, and this is the
answer for one jurisdiction.

If you are building outside the US national market system, read this as a worked example of what a regulator
can require and of how an architecture breaches a timing rule without anyone intending to, then apply your own
venue's rules and your own regulator's requirements.

## What Rule 603(a) requires

Reg NMS Rule 603(a) "prohibits an exchange from releasing data relating to quotes and trades to its customers
through proprietary feeds before it sends its quotes and trade reports for inclusion in the consolidated
feeds."

That sentence is the Commission's, from the 2012 settled administrative proceeding, and it is worth knowing
that it is a
reading rather than a transcription. The codified text at 17 CFR 242.603(a) says only that market data is
distributed on terms that are "fair and reasonable" and "not unreasonably discriminatory"; the timing
prohibition comes from the Regulation NMS adopting release, which states that "independently distributed data
could not be made available on a more timely basis than core data is made available to a Network processor."
A venue arguing about the boundary argues about that release, not about the two standards.

Two structural facts about the US market make this rule necessary and give it its shape. Consolidated quote
and trade data is distributed through processors operated on behalf of the whole market, and exchanges also
sell their own proprietary depth-of-book feeds carrying the same facts and more. A venue that reaches its own
paying customers first, on the same facts, has sold an advantage created by its position rather than by any
service. The rule constrains the ordering between those two paths, and it constrains it as an obligation on
the venue's own architecture, not as a best-efforts intention.

The enforcement action that applied this rule to a venue's fan-out, and the record a venue needs to answer
a timing question at all, are in this skill's publication obligations reference.
