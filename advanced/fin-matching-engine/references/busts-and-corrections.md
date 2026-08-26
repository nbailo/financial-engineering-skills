# Busting a trade: what a correction may touch, and what it may not

> **Provenance**
> provider: Nasdaq, US equities · surface: TotalView-ITCH 5.0, the Broken Trade message and the Clearly
> Erroneous Policy it names · version: TotalView-ITCH 5.0, revision log ends 28 April 2023
> verified_at: 2026-08-25
> sources: https://www.nasdaqtrader.com/content/technicalsupport/specifications/dataproducts/NQTVITCHspecification.pdf ·
> https://www.onixs.biz/fix-dictionary/4.4/tagNum_856.html (third-party FIX dictionary, not a primary source)
> verified: the specification PDF was fetched and its text extracted and read directly on 2026-08-25, by two
> independent passes. Section 1.5.3 of TotalView-ITCH 5.0: the Broken Trade message is sent "whenever an
> execution on Nasdaq is broken", an execution "may be broken if it is found to be “clearly erroneous”
> pursuant to Nasdaq's Clearly Erroneous Policy", "A trade break is final; once a trade is broken, it cannot
> be reinstated", the message carries the Match Number "of the execution that was broken", and a book-building
> consumer "may ignore these messages as they have no impact on the current book".
> unverified: whether any venue other than Nasdaq makes a bust final; whether a rulebook that adjusts a trade
> price instead of cancelling it exists (**cmegroup.com did not answer on 2026-08-25 across repeated attempts
> in two independent passes, so CME Rule 588 was NOT read and nothing here rests on it**); and the FIX
> `TradeReportType(856)` enumeration, read only in a third-party FIX dictionary and never in a FIX Trading
> Community document, which is why it is stated below as a prompt rather than as a fact. The TSE report of
> 1 October 2020 was not re-read and is carried on its inline attribution.
> revalidate_when: Nasdaq revises TotalView-ITCH or its Clearly Erroneous Policy; or before the FIX
> enumeration is relied on for any venue other than Nasdaq.

A correction is out of band from the book, references the match identity of an execution already
transmitted, and neither rewrites the book nor renumbers anything. Whether it is final is a rulebook answer.

**Whether a bust is final, and whether a bust is even the remedy, is a protocol and rulebook question.**
Only the shape above is universal.

Nasdaq publishes the strict answer for its own market, read directly on 2026-08-25: the Broken Trade message
is sent "whenever an execution on Nasdaq is broken"; an execution "may be broken if it is found to be
“clearly erroneous” pursuant to Nasdaq’s Clearly Erroneous Policy"; and **"A trade break is final; once a
trade is broken, it cannot be reinstated"** [Nasdaq TotalView-ITCH 5.0, section 1.5.3]. The message carries
the Match Number of the broken execution, and the specification tells a consumer that builds only a book
that it may ignore breaks entirely because they "have no impact on the current book".

**Do not generalise that sentence to your venue.** Two things this file did not establish, and therefore
does not assert: whether rulebooks that adjust a trade's price instead of cancelling it exist and how they
bound the adjustment (cmegroup.com timed out on both attempts on 2026-08-25, so no rule text was read); and
the FIX enumeration, where a third-party FIX dictionary lists `TradeReportType(856)` values including
`6 = Trade Report Cancel` and `5 = No/Was`, which if correct would mean a correction is itself something
that can be superseded rather than a terminal act. Neither claim was verified against a primary source.
Treat both as prompts to read your own rulebook, not as facts about it, and write down three answers before
the first correction is ever emitted: which remedy your rules permit, whether a remedy can be revoked, and
whether a correction can itself be corrected.

The consequence downstream does not depend on which answer you picked: position, margin, P&L and tax are
revisable after the fact while the book and the journal are not, so any component deriving a number from
fills must accept a retroactive bust or correction rather than assuming its inputs are stable.
