# Contributing

The six skills under `skills/` are the installed product. `advanced/` holds two opt-in BETA skills,
`fin-matching-engine` and `fin-market-data-publication`, which are not shipped by a default
`npx skills add`. A change to `advanced/` never changes what an ordinary user installs.

Read `docs/architecture.md` first. It explains the three-level hierarchy, the runtime budgets, and why a
rule lives where it lives. Run `python3 scripts/validate.py` before you open a PR.

## Adding a rule

Decide the level before you write the sentence.

- `fin-money-core` states a generic invariant **once, in full**. There are ten of them.
- A domain skill states only what is *different* in its domain and cites the money-core invariant by name:
  `Specialises *ambiguous outcomes*: ...`. The validator fails a citation that does not resolve.
- A reference carries provider semantics, error codes, protocol detail and worked implementations.

Four things a rule must do.

1. **State a property, not one implementation.** "An operation identity is owned atomically by exactly one
   writer" is a rule. "Use a Postgres unique index" is one mechanism, and belongs in a reference as an
   example.
2. **Earn its lines.** A `SKILL.md` is loaded whole on every activation and stays in the conversation. If
   the guidance is something a competent engineer already applies, cut it.
3. **Carry evidence.** A primary source, a cited incident, or source code read at a pinned commit.
4. **Say what is unverified.** Provider behaviour you could not confirm is labelled unverified in place,
   not stated flatly and not omitted.

A rule that turns one reasonable implementation into a universal prescription is a defect, even when the
implementation is a good one. Prefer deleting or weakening an overconfident rule over keeping it.

## Adding a reference

A reference is loaded only when its mechanism appears in the change, so it needs a trigger row in its
skill's `## References` table keyed on a **literal string**: an import name, an endpoint path, a column
identifier, a wire-protocol message name, an error code. Never "if this is complex" and never "see
references for details".

- **One hop only.** A reference never links to another reference. The validator fails a chain.
- **Floors.** Roughly 120 lines, or 400 words of prose outside headings, lists and tables. A
  contents-only stub fails: the skill promised the agent an answer.
- **Over 100 lines, open with a table of contents,** so a truncated read still reveals the scope.
- **Split only where the triggers are genuinely independent.** If a change needs the whole file, it stays
  one file.

## Adding an incident

The most valuable contribution. An incident exists to justify a rule an agent can act on. A story that
changes no code does not belong, whatever the size of the loss.

Write `incidents/<slug>-<year>.md` and add a row to the mapping table in `incidents/README.md`. Six parts,
in this order:

1. What happened.
2. What the software actually did.
3. The violated invariant, as a checkable predicate.
4. An honest verdict on whether an agent reviewing the diff would have caught it. "No" is an acceptable
   and useful answer; `incidents/README.md` has a whole section of them.
5. The rule it motivates, named the way the owning skill names it.
6. Sources, primary documents first.

## The bar for sources

**Accepted.** Vendor API documentation and protocol specifications read directly (FIX, Nasdaq OUCH, CME
MDP, ISO 20022). Regulator and court texts read in full: SEC orders, FCA Final Notices, CFTC complaints,
FINRA AWCs, opinions. Source code read at a **pinned commit or tag**, cited as repository, path and
revision. Named, dated incidents with a citable primary source.

**Rejected.**

- A blog post, conference talk, forum thread or model output as the only source.
- A figure that is widely repeated but absent from the primary text. The CFTC-SEC flash-crash report names
  no firm and contains no "$1 trillion" figure. `incidents/README.md` lists the corrections already applied.
- One provider's behaviour asserted as universal. Keep the generic property in the skill and move the
  provider-specific behaviour into that provider's reference with its citation.
- An incident with no rule attached, and a rule with no failure it prevents.

## Running validation

```bash
pip install pyyaml        # enables the strict frontmatter parser
python3 scripts/validate.py
```

It enforces the Agent Skills spec (legal frontmatter keys, name rules, description length) and this repo's
budgets: 210 lines per `SKILL.md`, 430 characters per description and 2,600 characters across the suite,
and 2 KB for `AGENTS.md`. It also checks that `skills/` holds exactly the six installed skills, that the
six share one section order, that every money-core invariant citation resolves, that nothing in `skills/`
links into `advanced/`, that references are one hop deep and meet their floors, that no `@`-reference
force-loads a file, that every cited repository path exists, that no em dash appears outside a quotation,
that any version string in `README.md` or `docs/` matches `.claude-plugin/plugin.json`, and that any budget
number restated in prose equals the constant the validator enforces.

CI runs the same script. It also runs `scripts/test-install-guardrails.sh`, the hostile suite for
`scripts/install-guardrails.sh`, on Linux and on macOS: refusal of symlinked, non-regular, multiply linked
and ambiguously marked targets, unique temporary files inside the destination, preserved modes and preserved
bytes outside the block, replacement by rename, full rollback after a partial failure and after a signal, and
a byte-identical install-then-uninstall round trip. Run it locally before changing that script. Every tracked
`*.sh` file is also run through shellcheck.

## Examples

`examples/` holds five before/after reviews: a trading bot, a payment flow, a ledger, an on-chain
indexer, and a prediction-market bot. Each cites the rules it catches by the name the owning skill gives them. If a rule change alters
what the agent should produce on one of those paths, update the example in the same PR. An example that
disagrees with the skills is a defect in the example.

## The standard this repo applies to itself

**A comment is a claim.** The skills tell agents that a control described in a comment is not implemented,
and that every asserted property is checked against the code in the same pass. The same holds here: a
budget, a count, a version or a product shape stated in prose must match what the code enforces and what the
tree contains. The validator catches the cheap cases. The rest is on the PR.

Style: short declarative sentences, present tense, no em dash outside a quotation.

MIT licensed. Contributions are accepted under the same license.
