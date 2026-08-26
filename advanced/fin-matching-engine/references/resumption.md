# Resuming from the durable record, in a state the resume can trust

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

Resumption is a state-reconstruction problem, not a switch: the fate of every execution already produced is
decided before resuming, and book state comes from the durable record rather than from memory that happened
to survive. An execution that is committed and retrievable but undelivered is its own state, not a rounding
error between delivered and void. What a halt does to resting orders is a published rule, and three answers
are defensible; acknowledging a cancel and then filling the order is not among them.

## Resumption, busts and corrections

1. Every execution produced before the halt is in exactly one of **three** states, and the resume says which
   one each is in: **delivered** to both counterparties; **committed, durable and retrievable** in the record
   but not yet delivered; or **explicitly voided** by a cancellation referencing its original match identity.
   The middle state is the one a two-state model destroys. Counting it as delivered means nobody ever sends
   it, so the venue's books carry a trade the participants do not; counting it as void cancels a real
   execution that the durable record, and possibly a counterparty, already holds. The resume **redelivers**
   it from the record under its original match and execution identifiers, at least once, and consumers dedupe
   on those identifiers. Voiding it is a separate decision taken under the rulebook, with a cancellation of
   its own, never the default for anything that failed to reach a socket.
2. The published sequence has no gap and no committed-but-unemitted event, and replaying the persisted
   command stream **through the reducer version that produced it** reproduces the emitted sequence byte for
   byte. A replay through the build you are about to resume with answers a different question and must not
   be accepted as this one.
3. Book state is derived from the durable record, whether that is the journal replayed under its own reducer
   or a store of persisted authoritative decisions, and never from process memory that survived the incident.
4. The trip flag is cleared by an authority independent of the component that tripped it, with
   the cause recorded.
5. Every check bypassed during the incident is back on, and every output produced while one was off is
   quarantined and reconciled before it is treated as authoritative again.

Step 1 is the one that escalates when it is missing, and it is missing whenever the design admits only two
states. TSE escalated to a whole-day halt precisely because participants held undelivered fills and no rule
existed for post-halt resumption. Escalation to venue scope is correct only when you cannot establish what
counterparties hold, which is the failure mode of not having written step 1 down in advance. Being able to
list, from the durable record alone, every execution in the middle state is what keeps the decision at
instrument scope.

**What a halt does to resting orders is a published rule, and three answers are defensible**: they persist
into the reopening auction; they are cancelled at the halt with an explicit per-order cancellation; or they
persist while cancels are queued and acknowledged only after the reopening prints. Protocols encode all
three, and some add a window where a cancel is neither accepted nor rejected synchronously. Pick one,
publish it, pin it with a test. Acknowledging a cancel and then filling the order is not among the three: an
acknowledgement is a published state, and contradicting it is a correctness failure rather than a race.
