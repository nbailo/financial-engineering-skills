# Pre-trade risk controls, halts and resumption

The controls that must run before an order reaches the book, and what happens to the obligations already
outstanding when one of them trips. Every rule here is a property, not an operating model: the specific
thresholds, the escalation path and the people involved belong to the venue that runs the engine, and this
file deliberately prescribes none of them. What it does prescribe is where a control sits relative to the
authoritative effect, what a trip leaves behind, and what has to be true before the engine accepts an order
again. Public enforcement actions appear as illustrations of a property, never as a procedure to copy.

## Contents

1. **Reject before the effect**: synchronous rejection in the path that creates the obligation, and why the
   measurement basis is what was entered rather than what came back.
2. **Band derivation**: per instrument, per session state, the cross-universe bound, and the sentinel price
   that a missing feed substitutes.
3. **Per-item limits need an aggregate companion**: counters keyed to the inbound unit, and the basket that
   passes because every item in it passed.
4. **Separate gates**: risk-increasing and risk-reducing paths, and what stays callable while halted.
5. **Automatic to trip, independent and auditable to reset**: exercise latency, bulk override, and who is
   allowed to clear a flag.
6. **The six meanings of halt**: the obligation state each level leaves behind.
7. **Resumption and busts**: reconstruct from the authority before accepting again; the bust is out of band
   and final.

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

So the exposure counter increments at submit, and decrements on an acknowledged cancel, a reject or an
expiry. Never on a fill alone: a fill converts working exposure into a position, it does not reduce what
has been committed. The counter-example is a firm whose capital utilisation was "only calculated … every 30
minutes", alerting on a percentage threshold, with "no automated process to prevent the entry of additional
orders" on breach (SEC order against Goldman Sachs, 20 August 2013, ¶12). Every number in that sentence was
correct. None of them rejected anything.

Duplicate detection belongs in the same path and is calibrated per counterparty rather than globally: what
is a duplicate for one participant is normal traffic for another.

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

Risk-increasing and risk-reducing paths are gated by **different** flags. When the increasing gate is shut,
cancel, replace-down, close, flatten, settle and reconcile stay callable, and a test exercises them in the
shut state. A single boolean covering both turns every incident into a choice between accepting new risk and
being unable to reduce the risk already held.

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

## 6. The six meanings of halt

`halt ⇒ engine quiesced ∧ everything already produced delivered or explicitly voided`, at the smallest scope
that contains the breach. The word is ambiguous, so name the level in the code and in the incident channel.

| # | Level | Existing obligations | Reset authority |
|---|---|---|---|
| 1 | Reject this operation, typed | untouched | none needed, per call |
| 2 | Freeze one aggregate (symbol, account) | untouched | scoped to that aggregate |
| 3 | Fail-closed: no new or increasing exposure; cancel, close, flatten, settle and reconcile stay hot | actively managed | successful reconciliation, never a timer |
| 4 | Cancel-all and disconnect order entry; risk, position and drop-copy stay up | actively managed | independent of the tripping component |
| 5 | Quiesce: stop accepting **and** producing, drain in-flight, deliver or explicitly void everything produced | drained, then frozen | independent of the tripping component |
| 6 | Process abort | **abandoned** | none; only where nothing is in flight |

**Severing the transport is not a halt.** Cutting the participant network while the engine keeps matching
accumulates executions nobody can see. TSE, 1 October 2020: the network was cut while matching continued,
and the post-incident report records that no contingency plan existed for halting trading in that case.
Cutting the network does not stop the matching engine; it stops you finding out what the matching engine
did.

## 7. Resumption and busts

Resumption is a state-reconstruction problem, not a switch. Decide the fate of in-flight and undelivered
executions **before** resuming, not during, and reconstruct from the durable record rather than from memory
that happened to survive.

1. Every execution produced before the halt is either delivered to both counterparties or explicitly voided
   with a cancellation referencing its original match identity. There is no third state.
2. The published sequence has no gap and no committed-but-unemitted event, and replaying the persisted
   command stream reproduces the emitted sequence byte for byte.
3. Book state is derived from the journal, not from process memory that survived the incident.
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

**A bust is out of band from the book and it is final.** It references the match identity of a previously
transmitted execution, it does not rewrite the book, and it cannot be reinstated once applied. The
consequence for everything downstream is that position, margin, P&L and tax are revisable after the fact
while the book and the journal are not, so any component deriving a number from fills must accept a
retroactive bust rather than assuming its inputs are stable.
