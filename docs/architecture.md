# Architecture

What a contributor needs to know before changing a skill. It is not a design record. Where this file and
the shipped skills disagree, the skills are the product and this file is the defect.

## 1. The three-level hierarchy

    fin-money-core  ->  domain skill  ->  triggered reference

- **`fin-money-core`** owns the generic invariants and states each one **once, in full**. Section 4 names
  them.
- **A domain skill** states only what is *different* in its domain, and cites the money-core invariant it
  specialises by name. It never restates the general rule.
- **A reference** carries provider semantics, error codes, protocol detail, incident evidence, and worked
  implementations in a specific language or framework.

**The rule against duplication.** If a sentence could move down one level without losing anything, move it.
A rule with two homes drifts, and nothing in CI compares them. A domain skill cites by name, italic:

> Specialises *ambiguous outcomes*: the venue's documented semantics for that endpoint decide what the
> response proves, and nothing beyond it may be inferred.

It restates the *specialisation* in its own vocabulary rather than pointing at a sibling for the general rule,
because a domain skill must be fully useful with `fin-money-core` absent.

## 2. The runtime budget, and why 220 lines matters

A `SKILL.md` is loaded **whole**, every time the skill activates, and it stays in the conversation for the
rest of the session. Every line is a per-turn tax on a task that may only need three of them.

- **210 lines per `SKILL.md`**, target 150 to 200. Enforced by `scripts/validate.py`.
- **430 characters per description, and 2,600 characters across the suite.** The listing budget is a
  percentage of the context window, it is shared with every other suite the user has installed, and
  overflow drops descriptions starting with the least-invoked skill, which is always a newly installed one.
- **After compaction only the first 5,000 tokens of each skill are re-attached.** Anything that must not be
  missed sits near the top of the file, which is why `## Workflow` is the first H2 in every skill.

A `SKILL.md` contains only frontmatter, a title, two or three lines naming the economic position this code
is in, and six H2 sections in this order: `## Workflow` (five to eight numbered imperative steps),
`## When to use` (the situations, then the literals as routing hints), `## When not to` (and which skill
instead), `## Invariants` (domain-critical only, two to six lines each), `## References` (a narrow trigger
table), `## Output` (compact, applicability-driven). `scripts/validate.py` fails any other shape. Incident
narratives, vendor matrices, error-code catalogues, test templates and worked examples belong in references:
deleting a cited fact is a defect, and loading it when it is not relevant is also a defect.

## 3. Routing and composition

The budget above is per skill; what decides the cost of a turn is how many load at once.

- **The domain skill normally wins.** It already specialises the money-core invariants that apply to its
  domain, in its own vocabulary. An ordinary Binance order change loads `fin-exchange-integration` alone.
- **`fin-money-core` loads alongside** only for a genuine cross-domain or core mechanism the domain skill
  does not cover: amount arithmetic and rounding direction, the retry classification of an outbound call, a
  money-path rollout.
- **`fin-verification` is never auto-loaded by exposure.** It loads when tests, proof or reconciliation are
  actually being changed, when the ask is review, readiness or verification, or when the domain skill
  demands stronger proof for the mechanism in scope. Customer money existing is not a trigger.
- **At most two domain skills plus `fin-verification`** for one task. A backend that credits on-chain
  deposits into a ledger is `fin-onchain` and `fin-ledger`, because both mechanisms are in the diff.

Each skill states this in one line under `## When not to`. `AGENTS.md` carries the routing table and one
standing instruction, and nothing else.

## 4. The ten money-core invariants

`fin-money-core` states each of these in full, once. Every other skill cites them by these exact names and
adds only the domain specialisation.

| Name | The invariant | Specialised by |
|---|---|---|
| exact representation | A value a counterparty can demand is exact. A value nobody can demand may be a float. | every domain skill |
| rounding and conservation | Direction comes from the operation, the residue has a named owner, and split parts sum to the original total. | ledger, onchain, payments |
| operation identity | One identity per economic decision, owned atomically by one writer, durable before the first externally visible effect, reused by every retry of that decision. | exchange, payments, onchain |
| ambiguous outcomes | A response that does not prove the outcome is UNKNOWN. Resolve by asking the authority about the identity you sent. | exchange, payments, onchain |
| durable dedupe | Dedupe state is exactly as durable as what it protects, and is written in the same transaction as the effect. | exchange, payments, onchain |
| concurrency on authoritative state | No unprotected read-modify-write on a value someone is owed. The guard is the write, and it covers the whole check-to-act section. | ledger, onchain |
| authority | Authority is a property of a quantity. Name whose copy of each quantity is the record, and the window in which two systems may legitimately disagree. | every domain skill |
| reconciliation | Compare against the authority on a schedule, through a path independent of the writer, and fail closed when a break is found. | verification, then every domain skill for its own source |
| hard limits | A limit rejects the operation rather than observing it, and at least one limit is an aggregate over the batch. | exchange, ledger |
| rollout | Reusing a live flag, enum or shared helper changes what already-deployed consumers decide, without changing their code. | verification |

Beyond these ten, each domain owns what does not translate: order state machines and venue filters in
`fin-exchange-integration`; capture, refund and dispute lifecycles in `fin-payments`; double-entry, holds and
period close in `fin-ledger`; finality, reorgs and token semantics in `fin-onchain`; proof mechanisms in
`fin-verification`. **State the property that must hold, then give the mechanism as one example among
several.**

| Say this | Not this |
|---|---|
| An operation identity is owned atomically by exactly one writer | a Postgres unique index |
| A reconciliation break has a configured, fail-closed delivery path | an import-time environment-variable exception |
| Fill-derived state converges: sort into the venue's canonical economic order, then fold | the fold is order-independent, so arrival order does not matter |
| Credited value comes from the asset's authoritative accounting for that transfer | always measure the balance delta |
| UNKNOWN exposure enters a bounded containment policy whose automatic actions are risk-reducing across every plausible unresolved state | always flatten the position |

## 5. Authority and exposure

The retired tier scale was one ordinal encoding two unrelated things. It is split into two fields:

    authority:  EXTERNAL | SELF | MIXED
    exposure:   own | customer | record

- **EXTERNAL.** An outside system holds the truth for that quantity and can tell you that you are wrong: a
  bot's venue, a payment processor, a chain.
- **SELF.** Nothing outside can: a system-of-record ledger, a matching engine, a custody signer's view of
  its own funds and nonces.
- **exposure.** Whose money is lost when the code is wrong: `own` capital, a `customer`'s funds, or the
  integrity of a `record` other systems consume.

**Authority is a property of a quantity, not of a process.** One service can hold external authority for
settlement state, self authority for the liabilities it originates and for its wallet nonce and signing
state, and external authority for chain inclusion. One scalar for the codebase picks the wrong oracle for
whichever quantity lost. Where a single authority covers everything in scope, emit one line; where it does
not, emit `MIXED` and qualify the quantities that differ, one line each:

    authority: EXTERNAL (Binance) · exposure: own

    authority: MIXED · exposure: customer
      settlement state      EXTERNAL (Stripe)
      internal liabilities  SELF

Two or three qualifiers, never a taxonomy and never a matrix. A single finding may carry its own authority
where that is what makes it a finding. Both fields are reported only when the change is economic, and neither
changes *which* rules apply. **Exposure decides how much evidence. Authority decides which kind.**

## 6. The output contract

Default output is one entry per real finding, and where the task is a review or a ship decision, one final
line:

    FINDING   the wrong economic outcome, concretely
    WHY       the mechanism that produces it
    EVIDENCE  file:line
    FIX       the change that closes it
    TEST      the property to assert

    VERDICT   SHIP  |  NO-SHIP: <the unresolved control>

- Emit only findings that exist. No findings means one or two sentences saying so and why the change is
  safe. Never emit an empty slot or a section for a concept the change does not touch.
- **Implemented, not described.** A claimed control points at executable code and, where the risk requires it,
  a test. Comments, TODOs, unused helpers and design prose are not evidence, and an absent control is reported
  as `UNRESOLVED: <control> (<why>)`, never as a completed checklist row.
- A larger evidence block is emitted **only** when authority is SELF for the quantity at risk, or exposure is
  `record`, or the change is genuinely complex. Each skill states its own threshold in one sentence.

## 7. What `fin-verification` owns

`fin-verification` describes **proof mechanisms**. It holds no required-technique matrix keyed on exposure,
and nothing is required merely because customer money exists.

- **The domain skill names the property** that must be proven for the mechanism in scope.
- **`fin-verification` names the mechanism that proves it,** and asks whether the control runs in production,
  detects a discrepancy planted in it, and survives a restart.
- **Authority decides the primary oracle.** EXTERNAL gets reconciliation against the authority. SELF gets
  replay, determinism and conservation, because there is nothing to reconcile against.
- **Exposure adjusts depth,** not the list of techniques.
- **The expensive mechanisms are conditional on the mechanism actually being changed.** Model-based testing,
  fault injection, mutation testing, race detectors, shadow systems and deterministic simulation are never
  demanded by exposure alone; deterministic simulation belongs to systems whose correctness depends on
  self-authoritative state machines, matching, consensus or distributed ordering.

Separating depth from technique is what keeps the bar for a small live bot compact: a handful of properties
and a scheduled comparison against the venue.

## 8. How references are triggered

A reference is loaded only when its mechanism appears in the change. Trigger rows are narrow and mechanical.

- A trigger row keys on a **literal string already present in the diff, the repo, or the task text**: an
  import name, an endpoint path, a column identifier, a wire-protocol message name, a lifecycle verb. It is
  imperative and forbids paraphrase: *the code imports `binance` or hits `api.binance.com` -> read the
  Binance order reference **immediately** and follow it in order. Do not summarize it; apply it.*
- A **judgement** predicate is never a trigger. Never "if this is complex", never "see references for
  details". Those produce load-nothing or load-everything.
- **One hop only.** A reference never points at another reference. On a second hop the agent previews with
  `head -100` instead of reading the file and silently acquires an incomplete rule set. Any reference over
  100 lines opens with a table of contents so a truncated preview still reveals its scope.
- **Split a reference where its triggers are genuinely independent,** never for aesthetics. If a change
  needs the whole file it stays one file, and after a split the trigger table must still fit the budget.
- Anything the agent must not miss lives in `SKILL.md` itself, near the top, never in a reference. No
  `@file` references anywhere; `@` force-loads.

The test of a trigger table: a rounding change must not pull in webhook recovery, venue reconnect logic,
reconciliation design or deterministic simulation.

## 9. What is in `advanced/`, and why it is not installed

`advanced/fin-matching-engine/` holds the skill for code that **is** the venue: matching against resting
orders, allocation and residue, auctions, self-trade prevention, price bands, halt and resume, and
single-writer recovery. `advanced/fin-market-data-publication/` holds the skill for code that publishes a
feed it originates. Neither is installed by `npx skills add`, neither is in the routing table, and neither
consumes the shared description budget. Its audience is a small fraction of the users of the other six, and a description that
sits in every listing is paid for by everyone. Shipping it by default would also make the package read as a
toolkit for building trading venues, which it is not: the common case is being a venue's *client*, and that
is `fin-exchange-integration`. Nothing in `skills/` may link into it, because an installed copy would not
have it. `scripts/validate.py` enforces that rule and the count of six.

## 10. Layout and the validator

`skills/` holds the six installed skills, each a `SKILL.md` plus a `references/` directory one level deep.
`advanced/` is outside the installed product. `AGENTS.md` is the optional routing block, under 2 KB, and
`CLAUDE.md` is a symlink to it. `.agents/skills` and `.claude/skills` are symlinks so every harness reads
one canonical tree. `CONTRIBUTING.md` has the rest of the repository layout and the submission bar.

`scripts/validate.py` enforces the frontmatter spec, the budgets above, the shared section shape, reference
floors and one-hop depth, the six-skill count, the absence of the retired output block and the retired tier
scale, that every cited path exists, and that no em dash appears outside a quotation. It also applies this
repo's own standard that a comment is a claim: a version, a budget number or a product-shape claim restated
in prose must equal what the code enforces and what the tree contains, which covers the plugin and
marketplace descriptions and the single id scheme in `docs/failure-taxonomy.md`. `CONTRIBUTING.md` lists the
checks in full.

## 11. The evidence for the delivery model

Vercel published a leakage-hardened eval over Next.js 16 APIs absent from model training data
([*AGENTS.md outperforms Skills in our agent evals*](https://vercel.com/blog/agents-md-outperforms-skills-in-our-agent-evals),
2026-01-27). A no-documentation baseline scored 53%. The same documentation packaged as an Agent Skill scored
53% too, because "in 56% of eval cases, the skill was never invoked". Inlined into `AGENTS.md` it scored 100%.

**The default failure of a skill is that it is never loaded**, silently, with no signal in the transcript,
and that is worst for a correctness suite: an agent reaches for a skill when it judges a task beyond it, and
every agent believes it can already write `create_order` and `balance -= amount`. So descriptions are
semantic routers matching the situation the code is in rather than a keyword, and `AGENTS.md` is offered as
optional always-on reinforcement. Rules still do not live there: `npx skills add` does not write the
consumer's `AGENTS.md`, so a rule that lives only in it is absent in the shipped configuration, which is the
same silent failure relocated.
