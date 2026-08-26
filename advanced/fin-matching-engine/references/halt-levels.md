# Three things called a halt, and six levels that say what each leaves behind

> **Provenance**
> provider: Tokyo Stock Exchange / JPX · surface: the public report on the outage of 1 October 2020 ·
> version: the TSE incident report of 1 October 2020
> verified_at: not established
> sources: https://www.jpx.co.jp/english/corporate/news/news-releases/0060/20201019-01.html
> verified: nothing here was re-read from a primary source in this pass.
> unverified: the TSE account, that the participant network was cut while matching continued and that no
> contingency plan existed for halting trading in that case, is carried on its inline attribution from an
> earlier pass. The six levels are this repository's own decomposition: no venue publishes them, nothing
> downstream will recognise the numbers, and they are not a standard.
> revalidate_when: before the TSE account is repeated outside this repository, or if JPX republishes the
> report at a different URL.

Three different things get called a halt, and they are not levels of each other. Deciding one and
implementing another is the recurring mistake, so the code says which it means. The six levels below force the
two questions a bare "halt" hides: what is still being produced, and what happens to what was already
produced.

## Halt names three different things, and the code says which one

They differ in scope, in reset authority and in what they leave obligations in:

- an **incident risk gate**: an operational decision inside your own system that nobody outside sees, which
  stops new or increasing exposure while what is outstanding stays managed, cleared by an authority
  independent of the component that tripped it;
- a **venue or session halt**: a published market state for an instrument or session that participants read
  off a feed, whose effect on resting orders, cancels and the reopening is a rulebook entry;
- **engine quiescence**: the process stops accepting **and** producing, drains what is in flight, and
  resolves everything already produced, redelivering what is committed but undelivered and explicitly voiding
  the rest under the rulebook.

Severing the transport is none of the three: it abandons in-flight executions participants cannot see. The
table below decomposes these into six levels, each mapped to the fate it leaves obligations in; **that
decomposition is this repo's own, not an industry standard**, and it earns its place by forcing the two
questions a bare "halt" hides, what is still produced and what happens to what was produced already.

Which risk-reducing path stays callable is decided per level and per component. The matcher owes that `cancel`
and a replace that only reduces quantity or moves price away stay callable for every resting order while the
increasing gate is shut, gated by a **different** flag and exercised by a test in that state. TSE/JPX, 1
October 2020, escalated to a whole-day halt because participants held undelivered fills and no rule for
resumption existed.

## One decomposition of the word halt

Three different things get called a halt, and they are not levels of each other: an **incident risk gate**
inside your own system, a **venue or session halt** that is a published trading state for an instrument, and
**engine quiescence**, where the process stops accepting and producing and drains. A venue halt is a
rulebook entry with participants reading it off a feed; an incident gate is an operational decision nobody
outside sees. Deciding one and implementing the other is the recurring mistake.

The distinction that decides everything downstream is **operational incident versus market state**. An
incident gate is your own decision about your own system: nobody outside sees it, it is not on any feed, its
reset authority is internal, and it is cleared by a recorded determination about a cause. A venue or session
halt is a **published market state**: participants read it, quote against it, and your rulebook already says
what happens to their resting orders, their cancels and the reopening. Implementing one while meaning the
other is how an internal incident silently becomes a market event, or a market state gets cleared by whoever
noticed it first.

**The six levels below are this file's decomposition. They are not an industry standard, no venue publishes
them, and nothing downstream will recognise the numbers.** Their value is that they force the two questions
a bare "halt" hides: what is still being produced, and what happens to what was already produced. Name the
level in the code and in the incident channel, and say which of the three kinds above it is. Only level 5 is
quiescence and only level 6 stops the process; levels 1 to 4 leave the engine running.

| # | Level | Existing obligations | Reset authority |
|---|---|---|---|
| 1 | Reject this operation, typed | untouched | none needed, per call |
| 2 | Freeze one aggregate (symbol, account) | untouched | scoped to that aggregate |
| 3 | Fail-closed: no new or increasing exposure. `cancel` and reduce-only replace stay hot in the matcher; position-closing and settlement paths stay hot in whichever component owns them | actively managed | successful reconciliation, never a timer |
| 4 | Cancel-all and disconnect order entry; risk, position and drop-copy stay up | actively managed | independent of the tripping component |
| 5 | Quiesce: stop accepting **and** producing, drain in-flight, redeliver what is committed but undelivered, explicitly void the rest | drained, then frozen | independent of the tripping component |
| 6 | Process abort | **abandoned** | none; only where nothing is in flight |

**Severing the transport is not a halt.** Cutting the participant network while the engine keeps matching
accumulates executions nobody can see. TSE, 1 October 2020: the network was cut while matching continued,
and the post-incident report records that no contingency plan existed for halting trading in that case.
Cutting the network does not stop the engine; it stops you finding out what the engine did.
