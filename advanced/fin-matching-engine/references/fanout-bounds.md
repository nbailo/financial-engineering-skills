# A bound keyed to the inbound unit, and the aggregate every per-item limit needs

> **Provenance**
> provider: US SEC and the UK FCA · surface: the administrative order against Knight Capital Americas of
> 16 October 2013, and the Final Notice to Citigroup Global Markets of 17 May 2024 · version: SEC Rel.
> 34-70694; FCA Final Notice, Citigroup Global Markets Limited, 17 May 2024
> verified_at: not established
> sources: https://www.sec.gov/litigation/admin/2013/34-70694.pdf ·
> https://www.fca.org.uk/publication/final-notices/citigroup-global-markets-limited-2024.pdf
> verified: nothing here was re-read from a primary source in this pass.
> unverified: Knight ¶21 on the absent control comparing orders leaving SMARS with those that entered it, and
> ¶23 and ¶24 on the account whose limit was linked to no automated control; and FCA ¶4.18(a), ¶4.33 on the
> basket of 349 orders totalling roughly US$196bn. All are carried on their inline attributions from an
> earlier pass.
> revalidate_when: before any paragraph number here is repeated outside this repository, or if either
> regulator republishes its document at a different URL.

Every transformation that turns one input into many outputs carries a counter keyed to that inbound unit and
a hard bound checked before the send. The companion rule is the one that gets missed: per-item limits are
satisfiable by an unbounded number of items, so whatever the items sum into is bounded too.

## A fan-out bound lives on the emit path, keyed to the inbound unit

Specialises *hard limits*, keyed to the inbound unit rather than to a batch. The bound is checked **before
the send**, never by a monitor, which is always one interval behind an unbounded loop. On breach: set a
flag the emit path reads before every send, cancel resting orders, disconnect order entry, keep risk and
drop-copy alive; reset authority is independent of the component that tripped, and disabling the failing check
is never the mitigation. Knight Capital, SEC Rel. 34-70694 ¶21: no *"control to compare orders leaving SMARS
with those that entered it"*.

## Per-item limits need an aggregate companion

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
it", and ¶23 and ¶24, the account accumulating the resulting position had a limit "linked to no automated
controls", which makes the limit a label. An error or suspense account is a real account with an owner, an
aging policy and a threshold wired to a gate that rejects.
