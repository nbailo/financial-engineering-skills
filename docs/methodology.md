# How the rules were built, and what is not claimed

Every rule in this suite traces to something a reader can check. This file says what that means in practice.

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

What runs in this repository is deterministic: the worked-example tests under `examples/`, and a lexical
routing lint over the skill descriptions. The lint scores word overlap between a task and each description.
No model runs in either one.

No model-based effectiveness benchmark exists here, and no skills-on/off baseline is published or claimed.
Nothing in this repository measures whether an agent produces better work with these skills than without
them. A proper runtime evaluation system is deferred to a separate future change.

What you can check today is the primary source under each rule, the provenance block on the references that
carry one (`docs/providers.md`), and the worked examples in `examples/`.

## Deferred, and deliberately not in this change

Named here so they are tracked rather than forgotten. None is started.

- **A runtime evaluation system.** Model-based effectiveness measurement, a skills-on/off paired baseline,
  and a behavioral fixture dataset with hidden oracles. A prototype existed and was removed from this change
  because it was unfinished; nothing here measures agent behaviour today.
- **Token budgeting.** The loaded-path band, the ratchet and how advanced paths are accounted for.
- **Public and private assurance documents.** What is published about how the rules are checked, and what
  stays internal.
- **The routing-block installer**, including its time-of-check to time-of-use behaviour on the files it edits.
- **Installation, release policy and supply chain.** Install commands, commit-pinning guidance, release and
  tag wording, `SECURITY.md`, the secret scanner, workflow permission policy, Dependabot, the plugin
  manifests and distribution, and the provider-drift workflow. Untouched here on purpose.

## The output contract

`docs/architecture.md` section 6 states it, and F14 in `docs/failure-taxonomy.md` is the failure mode its
`UNRESOLVED` rule exists for.

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
