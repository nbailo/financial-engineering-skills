# Pre-trade risk controls, halts and resumption

> **Provenance**
> provider: Nasdaq, US equities · surface: TotalView-ITCH 5.0, the Broken Trade message and the Clearly
> Erroneous Policy it names · version: TotalView-ITCH 5.0, revision log ends 28 April 2023
> verified_at: 2026-08-25
> sources: https://www.nasdaqtrader.com/content/technicalsupport/specifications/dataproducts/NQTVITCHspecification.pdf
> · https://www.onixs.biz/fix-dictionary/4.4/tagNum_856.html (third-party FIX dictionary, not a primary source)
> verified: fetched and read directly today, section 1.5.3 of TotalView-ITCH 5.0: the Broken Trade message
> is sent "whenever an execution on Nasdaq is broken", an execution "may be broken if it is found to be
> “clearly erroneous” pursuant to Nasdaq’s Clearly Erroneous Policy", "A trade break is final; once a trade
> is broken, it cannot be reinstated", the message carries the Match Number "of the execution that was
> broken" referencing a previously transmitted Order Executed, Order Executed With Price or Trade Message,
> and a book-building consumer "may ignore these messages as they have no impact on the current book".
> unverified: whether any venue other than Nasdaq makes a bust final; whether a rulebook that adjusts a
> trade price instead of cancelling it exists (cmegroup.com timed out on two attempts today, so CME Rule 588
> was NOT read); the FIX TradeReportType enumeration in section 7 was read only in a third-party FIX
> dictionary, never in a FIX Trading Community document. NOT re-fetched in this pass, and carried on their
> inline attributions alone: SEC Rel. 34-63241, SEC Rel. 34-70694, the SEC order against Goldman Sachs of
> 20 August 2013, FCA Final Notice Citigroup Global Markets 17 May 2024, Compound Proposal 62, the TSE
> report of 1 October 2020.
> revalidate_when: Nasdaq revises TotalView-ITCH or its Clearly Erroneous Policy; or before section 7 is
> relied on for any venue other than Nasdaq.

The controls that must run before an order reaches the book, and what happens to the obligations already
outstanding when one of them trips. Every rule here is a property, not an operating model: the specific
thresholds, the escalation path and the people involved belong to the venue that runs the engine, and this
file deliberately prescribes none of them. What it does prescribe is where a control sits relative to the
authoritative effect, what a trip leaves behind, and what has to be true before the engine accepts an order
again. Public enforcement actions appear as illustrations of a property, never as a procedure to copy.

## Contents

1. **Reject before the effect**: synchronous rejection in the path that creates the obligation, why the
   measurement basis is what was entered rather than what came back, and the three exposures a fill moves.
2. **Band derivation**: per instrument, per session state, the cross-universe bound, and the sentinel price
   that a missing feed substitutes.
3. **Per-item limits need an aggregate companion**: counters keyed to the inbound unit, and the basket that
   passes because every item in it passed.
4. **Separate gates**: risk-increasing and risk-reducing paths, and what stays callable while halted.
5. **Automatic to trip, independent and auditable to reset**: exercise latency, bulk override, and who is
   allowed to clear a flag.
6. **One decomposition of the word halt**: six levels, and the obligation state each leaves behind.
7. **Resumption, busts and corrections**: reconstruct from the authority before accepting again; whether a
   bust is final, and whether it is even the remedy, comes from the protocol and the rulebook.

---

## 1. Reject before the effect

A limit is a control only if it runs synchronously in the path that creates the authoritative effect and
returns a refusal. An alert, a dashboard, a post-execution screen and a periodic capital calculation all
observe the effect after it exists. The refusal is typed, it names which limit refused, and the path that
creates the effect is unreachable except through it.

**The measurement basis is what was entered, not what came back.** SEC Rule 15c3-5's adopting release (Rel.
34-63241) states it as directly as it can be stated: controls must be applied on an "automated, pre-trade
basis, before orders are routed", and compliance is assessed "on the basis of exposure from orders entered
… rather than relying on a post-execution, after-the-fact determination", "on the basis of orders entered
rather than executions obtained". A US rule is cited here because it says the thing precisely; the property
is not jurisdictional. An engine that sums executions measures a quantity smaller than its own exposure by
the size of the open working book, and the gap grows exactly when the book grows.

The counter-example is a firm whose capital utilisation was "only calculated … every 30 minutes", alerting
on a percentage threshold, with "no automated process to prevent the entry of additional orders" on breach
(SEC order against Goldman Sachs, 20 August 2013, ¶12). Every number in that sentence was correct. None of
them rejected anything.

Duplicate detection belongs in the same path and is calibrated per counterparty rather than globally: what
is a duplicate for one participant is normal traffic for another.

### Three exposures, not one

The word "exposure" names three different quantities, and a fill moves two of them in a single step. A gate
keeping one counter is wrong in one direction or the other, and which direction depends on which of the
three the author had in mind when they wrote it.

| Exposure | What it counts | What a fill does to it | What else moves it |
|---|---|---|---|
| **Working order** | `Σ leaves` over live orders, at the price each would execute at | **decrements** it by the filled quantity | increments at accept; decrements on an acknowledged cancel, a reject, an expiry, and on a self-match-prevention decrement |
| **Filled position** | the net position and its notional | **increments** it by the same filled quantity, signed | a bust or a correction, retroactively; nothing else |
| **Settlement** | delivery and payment obligations already created, until clearing or settlement finality | creates one on each side | settlement, novation to a clearing house, or an explicit void |

Three consequences, and they are the whole of it.

- **A fill is one atomic transition, not two events.** The execution, the `leaves` decrement and the
  position move commit together. A design that emits the execution and moves the position in one step and
  decrements `leaves` in another has a window in which the same quantity is committed twice, and a crash
  inside that window makes the double permanent.
- **Do not net the first two into one counter.** Incrementing a position without decrementing `leaves`
  double-counts the same quantity; decrementing `leaves` without booking the position under-counts it. A
  credit or capital check is written against working **plus** filled, and it says so at its definition.
- **Settlement exposure outlives the other two.** A round trip that ends flat is zero position and two live
  delivery obligations. A gate reading net position alone reports no exposure at exactly the moment two
  deliveries are outstanding, which is the moment a counterparty failure costs the most.

A fourth number is often kept beside these: cumulative notional entered over a session, used as a throughput
or credit-consumption cap. It is monotone by construction and it is not exposure. Gating it as if it were
produces a limit that can only ever tighten, and an engine that refuses orders on a flat book.

## 2. Band derivation

A price band is a function of **that instrument's own reference price** and the current session state. One
derivation, called from every session-state code path, with the reference source stated per state.

| Session state | Reference price source |
|---|---|
| Continuous | the venue best, or the plan's reference price where one exists |
| Pre-open and post-close | the prior session's close **for that instrument**; if unavailable, reject |
| Auction or cross | the indicative price, or the auction collar reference |
| Halted | the last valid reference before the halt, frozen |

Two failure shapes recur, and both are about the input rather than the arithmetic.

**A bound aggregated over the universe is vacuous for every instrument except the most expensive one.**
Goldman Sachs, ¶25 and ¶30: in-hours the band was derived per series, but the pre-open path took another
branch whose upper bound was "1.5 times the highest closing price from the prior day **for any listed
option**", so orders "fell between $0.01 and $3,090" and passed in every name. The special case, not the
main path, is where the derivation gets replaced.

**A missing price rejects; it never substitutes a sentinel, and a sentinel is never multiplied.** FCA Final
Notice, Citigroup Global Markets, 17 May 2024, ¶4.27: an unavailable external feed meant a benchmark index
price "defaulted to -1", so a screen rendered the product of quantity and that sentinel as a large negative
notional that looked plausible to the trader; ¶4.30 records that the same missing data blanked the one
basket-level check, which then proceeded anyway. A control whose input is absent must fail closed, and a
control that silently degrades to no control is worse than one that is absent, because it reports success.

## 3. Per-item limits need an aggregate companion

A bounded transformation carries a counter keyed to its **inbound unit** and a hard bound checked on the
emit path, before the send, rather than by a monitor reading a metric afterwards. The unit is whatever the
amplification is per: a parent order, an inbound message, an instrument, a session.

Per-item limits are satisfiable by an unbounded number of items. The FCA notice is the clean statement of
the gap: the firm's hard blocks were per item, a basket of 349 orders totalling roughly US$196bn passed
through them, and the notice concludes that "had a **basket level** wave notional hard block limit been in
place … the trading incident would not have occurred" (¶4.18(a), ¶4.33). Wherever a per-item bound exists,
ask what aggregate the items sum into and bound that too, at the level a participant, a firm or a venue can
actually be harmed at.

The companion failure is having no comparison at all between what entered a component and what left it.
Knight Capital, SEC Rel. 34-70694 ¶21: no "control to compare orders leaving SMARS with those that entered
it", and "no procedures in place to halt SMARS's operations in response to its own aberrant activity". ¶23
and ¶24 add the second half: the account accumulating the resulting position had a limit "linked to no
automated controls", which makes the limit a label. An error or suspense account is a real account with an
owner, an aging policy and a threshold wired to a gate that rejects.

## 4. Separate gates

Risk-increasing and risk-reducing paths are gated by **different** flags. A single boolean covering both
turns every incident into a choice between accepting new risk and being unable to reduce the risk already
held.

**Which risk-reducing paths must stay callable is decided per component, not universally.** The matching
engine's list is short, and it is the only one this file requires: while the increasing gate is shut,
`cancel` and a replace that only reduces quantity or moves a price away from the market stay callable for
every resting order, and a test exercises them in the shut state.

`close`, `flatten`, `settle` and `reconcile` belong to whatever component holds the positions, the cash or
the clearing relationship. In an engine that matches and does not clear, they are somebody else's
entrypoints; requiring the matcher to expose them adds surface that then has to be correct under exactly the
conditions the gate exists for, and a `flatten` implemented inside a matcher is a matcher that can create
exposure while its increasing gate is shut. Name the component that owns each risk-reducing path, and gate
it there, with the test in that component.

The design worth copying is a market-wide pause that still runs its closing transaction and still
disseminates a quote marked unexecutable rather than withholding it: stopping new exposure is separable from
stopping the machinery that resolves existing exposure. Where an invariant can be **momentarily** false
during a named intermediate state, give the check a bounded self-heal window before it escalates, because a
check that fires on a legitimately intermediate state is an availability bug wearing a correctness check's
clothes.

## 5. Automatic to trip, independent and auditable to reset

**Exercise latency is measured in the same units as the loss.** A pause that takes longer to exercise than
the loss takes to accrue is documentation. Compound's Proposal 62, 29 and 30 September 2021, is the extreme
case: a distribution bug let users claim far more than they had accrued, there were "no admin controls or
community tools to disable the COMP distribution", and any change had to pass a seven-day governance
process. Roughly $50M was claimed while everyone watched. Every path that distributes, mints, credits or
transfers value needs a pause exercisable faster than that path can move value.

**Automatic to trip.** Rate-based breakers that halt traffic above a rate are cheap and they work. What
fails is the reset. Goldman ¶8 and ¶31: control personnel "repeatedly lifted the circuit breakers blocks"
during the incident, while still investigating the cause, and the employee who lifted one of them had
authored the policy he was breaching. The property that follows is not a role or a form. It is that the
authority to clear a trip is **independent of the component and the person that tripped it**, that clearing
requires a recorded determination about the cause rather than a bare resume, and that the record is
auditable afterwards. How that authority is constituted is the operator's decision, not this file's.

**A warning that can be bulk-overridden is not a control.** FCA ¶4.28 to ¶4.31: a single dialog presented
711 warning messages with only the first 18 lines visible without scrolling and two buttons, one of which
dismissed all of them; ¶4.15 notes the system "did not require a trader to scroll down through the list".
One action clearing N warnings has an effective threshold of infinity. If a warning is worth raising, it is
either blocking or it is per-item acknowledgeable.

## 6. One decomposition of the word halt

Three different things get called a halt, and they are not levels of each other: an **incident risk gate**
inside your own system, a **venue or session halt** that is a published trading state for an instrument, and
**engine quiescence**, where the process stops accepting and producing and drains. A venue halt is a
rulebook entry with participants reading it off a feed; an incident gate is an operational decision nobody
outside sees. Deciding one and implementing the other is the recurring mistake.

**The six levels below are this file's decomposition. They are not an industry standard, no venue publishes
them, and nothing downstream will recognise the numbers.** Their value is that they force the two questions
a bare "halt" hides: what is still being produced, and what happens to what was already produced. Use these
names or your own, but name the level in the code and in the incident channel, and say which of the three
kinds above it is. Only level 5 is quiescence and only level 6 stops the process; levels 1 to 4 leave the
engine running and stop something specific.

| # | Level | Existing obligations | Reset authority |
|---|---|---|---|
| 1 | Reject this operation, typed | untouched | none needed, per call |
| 2 | Freeze one aggregate (symbol, account) | untouched | scoped to that aggregate |
| 3 | Fail-closed: no new or increasing exposure. `cancel` and reduce-only replace stay hot in the matcher; position-closing and settlement paths stay hot in whichever component owns them (§4) | actively managed | successful reconciliation, never a timer |
| 4 | Cancel-all and disconnect order entry; risk, position and drop-copy stay up | actively managed | independent of the tripping component |
| 5 | Quiesce: stop accepting **and** producing, drain in-flight, deliver or explicitly void everything produced | drained, then frozen | independent of the tripping component |
| 6 | Process abort | **abandoned** | none; only where nothing is in flight |

**Severing the transport is not a halt.** Cutting the participant network while the engine keeps matching
accumulates executions nobody can see. TSE, 1 October 2020: the network was cut while matching continued,
and the post-incident report records that no contingency plan existed for halting trading in that case.
Cutting the network does not stop the matching engine; it stops you finding out what the matching engine
did.

## 7. Resumption, busts and corrections

Resumption is a state-reconstruction problem, not a switch. Decide the fate of in-flight and undelivered
executions **before** resuming, not during, and reconstruct from the durable record rather than from memory
that happened to survive.

1. Every execution produced before the halt is either delivered to both counterparties or explicitly voided
   with a cancellation referencing its original match identity. There is no third state.
2. The published sequence has no gap and no committed-but-unemitted event, and replaying the persisted
   command stream **through the reducer version that produced it** reproduces the emitted sequence byte for
   byte. A replay through the build you are about to resume with answers a different question and must not
   be accepted as this one.
3. Book state is derived from the durable record, whether that is the journal replayed under its own reducer
   or a store of persisted authoritative decisions, and never from process memory that survived the incident.
4. The trip flag is cleared by the independent authority of §5, with the cause recorded.
5. Every check bypassed during the incident is back on, and every output produced while one was off is
   quarantined and reconciled before it is treated as authoritative again.

Step 1 is the one that escalates when it is missing. TSE escalated to a whole-day halt precisely because
participants held undelivered fills and no rule existed for post-halt resumption. Escalation to venue scope
is correct only when you cannot establish what counterparties hold, which is the failure mode of not having
written step 1 down in advance.

**What a halt does to resting orders is a published rule, and three answers are defensible**: they persist
into the reopening auction; they are cancelled at the halt with an explicit per-order cancellation; or they
persist while cancels are queued and acknowledged only after the reopening prints. Protocols encode
different answers, from a defined window where a cancel is neither accepted nor rejected synchronously, to a
reactivation that cancels all resting orders, to a closed state that rejects every order operation
including cancellation. Pick one, publish it, and pin it with a test. Acknowledging a cancel and then
filling the order is not among the three: an acknowledgement is a published state, and contradicting it is a
correctness failure rather than a race.

**Whether a bust is final, and whether a bust is even the remedy, is a protocol and rulebook question.**
What is universal is only the shape: a correction is out of band from the book, it references the match
identity of an execution already transmitted, and it neither rewrites the book nor renumbers anything.

Nasdaq publishes the strict answer for its own market, and this repo read it directly on 2026-08-25. The
Broken Trade message is sent "whenever an execution on Nasdaq is broken"; an execution "may be broken if it
is found to be “clearly erroneous” pursuant to Nasdaq’s Clearly Erroneous Policy"; and **"A trade break is
final; once a trade is broken, it cannot be reinstated"** [Nasdaq TotalView-ITCH 5.0, section 1.5.3]. The
message carries the Match Number of the broken execution, referencing a previously transmitted Order
Executed, Order Executed With Price or Trade Message, and the specification tells a consumer that builds
only a book that it may ignore breaks entirely because they "have no impact on the current book".

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
