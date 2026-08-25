# How the rules were built, and what is not claimed

Every rule in this suite traces to something a reader can check. This file says what that means in practice,
what the rules were selected against, and what the suite deliberately does not do.

## What a rule is sourced from

**Primary sources, not blog posts.** Exchange and processor API documentation read directly from the
vendors. Protocol specifications: FIX, Nasdaq OUCH and ITCH, CME MDP, ISO 20022. TigerBeetle, PostgreSQL
isolation semantics, Jepsen. Regulator documents read in full rather than through press coverage. Reading
the CFTC and SEC report on 6 May 2010 in full is how `incidents/flash-crash-2010.md` can state that the
string "Waddell" appears zero times in it, that it identifies the seller only as "a large fundamental trader
(a mutual fund complex)", and that it carries no "$1 trillion" figure.

**Real source code, not only documentation.** ccxt, freqtrade, hummingbot, nautilus_trader, TigerBeetle and
the Stripe SDKs were cloned and read. That is how `skills/fin-exchange-integration/references/ccxt.md` can
state, at a pinned commit, that the retry funnel does not discriminate on HTTP method: a `POST` create-order
is retried on the same terms as a `GET` ticker, under the identical client order id.

**Cited incidents, corrected against the primary text.** Twenty are in `incidents/`, each mapped to the
rule it motivates, each opening with its own sourcing warning where the public record is press reporting
rather than a regulator's finding. Three shapes recur:

- Nobody attacked Knight Capital. A repurposed flag, dead code left in the binary, and a deploy that reached
  seven of eight servers cost $460M in about 45 minutes (SEC order 34-70694).
- Nobody attacked Citigroup when a customer account was credited with $81 trillion where $280 was intended.
  The path had no plausibility ceiling anywhere on it. It was caught and reversed, so the realised loss was
  zero, which is the only reason it is a near miss rather than a failure.
- Revolut's US and European systems disagreed for months about what "declined" meant, and refunded declined
  transactions out of the firm's own funds. Organised groups then farmed the pattern deliberately, for
  roughly $23M taken and $20M unrecovered. The divergence was there before anyone exploited it, and a
  partner bank's cash-position report is what surfaced it, not an alert of Revolut's own.

**Rules attacked rather than admired.** "Terminal states must be absorbing" sounds obvious and is wrong:
nautilus_trader deliberately ships `(Canceled, Filled) => Filled`, annotated `// Real world possibility`,
because a fill and a cancel acknowledgement cross on the wire and the fill is real money. A rule that turns
one reasonable implementation into a universal prescription is a defect even when the implementation is a
good one.

## What the rules were selected against

Candidate rules were graded against a no-guidance control run: realistic financial coding tasks, several
repetitions each, scored per probe, with the model given no financial guidance at all. Roughly half of what
was measured, the model already got right unaided. A rule written for those is token waste that pulls
attention away from the probes it fails every time, so those rules were cut. What the suite keeps is the
probes that never passed and the ones where repetitions disagreed.

## What is not measured

The control run is a working artefact and is not published in this repository, so take it as method rather
than as evidence. There is no published head-to-head score for this suite against a baseline, and none is
claimed. What you can check is the primary source under each rule, the provenance block on the references
that carry one (`docs/providers.md`), and the worked examples in `examples/`.

## The output contract

Where a change is economic, a review opens with one line naming whose copy of each quantity is the record
and whose money is at stake, then one entry per real finding and nothing for a concept the change does not
touch:

```
authority: EXTERNAL (Binance) · exposure: own

FINDING   the wrong economic outcome, concretely
WHY       the mechanism that produces it
EVIDENCE  file:line
FIX       the change that closes it
TEST      the property to assert
```

A control the agent names but does not build is reported as `UNRESOLVED: <control> (<why>)`, never as a
completed checklist row. That single rule addresses the most common failure mode in generated money code:
naming the correct control accurately, then writing a comment instead of implementing it.

## What the suite deliberately does not cover

- **Security.** It will not find injection, broken auth or leaked secrets. It asks a different question:
  can the system produce an incorrect economic outcome while every component behaves exactly as specified?
- **Smart-contract auditing.** `fin-onchain` covers integration correctness. Reentrancy and access control
  belong to a contract-audit tool.
- **A ban on floating point.** Float is the correct type for greeks, implied vol, Monte Carlo and backtest
  statistics. The rule is about obligations, not about finance.
- **Institutional ceremony for small projects.** A 300-line bot trading its author's own capital is asked
  for a handful of tests and a daily comparison against the venue, not for deterministic simulation.
- **Clearing, netting and settlement finality; liquidation waterfalls; venue-operated resolution.** These
  sit outside the whole repository, `advanced/` included. They were deleted rather than relocated, so
  nothing here routes to them and no pointer promises otherwise.
