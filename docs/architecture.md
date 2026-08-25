# Architecture

What a contributor needs to know before changing a skill. It is not a design record. Where this file and
the shipped skills disagree, the skills are the product and this file is the defect.

## 1. The three-level hierarchy

    fin-money-core  ->  domain skill  ->  triggered reference

- **`fin-money-core`** owns the generic invariants and states each one **once, in full**. Section 3 names
  them.
- **A domain skill** states only what is *different* in its domain, and cites the money-core invariant it
  specialises by name. It never restates the general rule.
- **A reference** carries provider semantics, error codes, protocol detail, incident evidence, and worked
  implementations in a specific language or framework.

**The rule against duplication.** If a sentence could move down one level without losing anything, move it.
The same rule must not appear at two levels, let alone three. A rule with two homes drifts, and nothing in CI
compares them.

Citation form inside a domain skill, italic and by name:

> Specialises *ambiguous outcomes*: the venue's documented semantics for that endpoint decide what the
> response proves, and nothing beyond it may be inferred.

A domain skill must still be fully useful with `fin-money-core` absent. That is the reason a domain skill
restates the *specialisation* in its own vocabulary rather than pointing at a sibling for the general rule:
nothing a skill needs in order to be correct lives in another skill.

## 2. The runtime budget, and why 220 lines matters

A `SKILL.md` is loaded **whole**, every time the skill activates, and it stays in the conversation for the
rest of the session. Every line is a per-turn tax on a task that may only need three of them.

- **220 lines maximum per `SKILL.md`**, target 150 to 200. Enforced by `scripts/validate.py`.
- **430 characters per description, 2,600 across the six installed skills.** The listing budget is a
  percentage of the context window, it is shared with every other suite the user has installed, and overflow
  drops descriptions starting with the least-invoked skills, which is always a newly installed suite.
- **After compaction only the first 5,000 tokens of each skill are re-attached**, newest-first inside a
  fixed budget. Anything that must not be missed sits near the top of the file, which is why `## Workflow`
  is the first H2 in every skill.
- **At most two domain skills plus `fin-verification`** should be loaded for one task.

A `SKILL.md` contains only:

    ---frontmatter---
    # Title
    two or three lines: the economic position this code is in, and the question this skill answers

    ## Workflow          five to eight numbered imperative steps
    ## When to use       the situations, then the literals as routing hints
    ## When not to       and which skill instead
    ## Invariants        domain-critical only, each two to six lines
    ## References        a narrow trigger table
    ## Output            compact, applicability-driven

Long incident narratives, regulatory case studies, vendor matrices, API error-code catalogues, framework
catalogues, large test templates and worked examples belong in references. Preserve all of it. Deleting a
cited fact is a defect. Loading it when it is not relevant is also a defect.

## 3. The ten money-core invariants

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
| authority | Name whose copy of each quantity is the record, and the window in which two systems may legitimately disagree. | every domain skill |
| reconciliation | Compare against the authority on a schedule, through a path independent of the writer, and fail closed when a break is found. | verification, then every domain skill for its own source |
| hard limits | A limit rejects the operation rather than observing it, and at least one limit is an aggregate over the batch. | exchange, ledger |
| rollout | Reusing a live flag, enum or shared helper changes what already-deployed consumers decide, without changing their code. | verification |

Beyond these ten, each domain owns what does not translate: order state machines and venue filters in
`fin-exchange-integration`; capture, refund and dispute lifecycles in `fin-payments`; double-entry,
holds and period close in `fin-ledger`; finality, reorgs and token semantics in `fin-onchain`; proof
technique in `fin-verification`.

**Invariants, not implementation prescriptions.** State the property that must hold, then give the mechanism
as one example among several.

| Say this | Not this |
|---|---|
| An operation identity is owned atomically by exactly one writer | a Postgres unique index |
| A reconciliation break has a configured, fail-closed delivery path | an import-time environment-variable exception |
| Fill-derived state is replay-safe and arrival-order-correct | recompute from the entire fill history on every update |
| UNKNOWN exposure enters a bounded containment policy whose automatic actions are risk-reducing across every plausible unresolved state | always flatten the position |

## 4. Two axes, replacing the old tier scale

One ordinal used to encode two unrelated things. It is split into two fields:

    authority:  EXTERNAL | SELF
    exposure:   own | customer | record

- **authority = EXTERNAL.** An outside system holds the truth and can tell you that you are wrong: a trading
  bot's venue, a payment processor, a chain. Reconciliation against that authority is the primary proof, and
  it is available.
- **authority = SELF.** Nothing outside can tell you that you are wrong: a system-of-record ledger, a
  matching engine, a custody signer's own view of its funds and nonces. Replay, determinism and conservation
  assertions are the proof, because reconciliation has nothing to reconcile against.
- **exposure.** Whose money is lost when the code is wrong: `own` capital, a `customer`'s funds, or the
  integrity of a `record` other systems consume.

**Exposure decides how much evidence. Authority decides which kind.** Both are reported on one line, and
only when the change is economic:

    authority: EXTERNAL (Binance) · exposure: own

There is no matrix of sixteen cells. Two fields, stated plainly. Neither field changes *which* rules apply.

## 5. The output contract

Default output is one entry per real finding:

    FINDING   the wrong economic outcome, concretely
    WHY       the mechanism that produces it
    EVIDENCE  file:line
    FIX       the change that closes it
    TEST      the property to assert

and, where the task is a review or a ship decision, one final line:

    VERDICT   SHIP  |  NO-SHIP: <the unresolved control>

Rules:

- Emit only findings that exist. No findings means one or two sentences saying so and why the change is safe.
  Do not emit empty slots, and never emit a section for a concept the change does not touch.
- **Implemented, not described.** A claimed control points at executable code and, where the risk requires
  it, a test. Comments, TODOs, unused helpers and design prose are not evidence. A control that is absent is
  reported as `UNRESOLVED: <control> (<why>)`, never as a completed checklist row.
- A larger contract or evidence block is emitted **only** when authority is SELF, or exposure is `record`,
  or the change is genuinely complex. Each skill states its own threshold in one sentence.

The previous seven-label ritual block is retired. It forced every economic change to answer for identity,
ambiguity, recovery and reconciliation whether or not the change touched them, which taught the reader to
fill in tables. `scripts/validate.py` fails any skill that reintroduces it.

## 6. How references are triggered

A reference is loaded only when its mechanism appears in the change. Trigger rows are narrow and mechanical.

- A trigger row keys on a **literal string already present in the diff, the repo, or the task text**: an
  import name, an endpoint path, a table or column identifier, a wire-protocol message name, or a lifecycle
  verb. It is written imperatively and forbids paraphrase.

  > the code imports `binance` or hits `api.binance.com` -> read the Binance reference **immediately** and
  > follow it in order. Do not summarize it; apply it.

- A **judgement** predicate is never a trigger. Never "if this is complex", never "for more detail", never
  "see references for details". Those produce load-nothing or load-everything.
- **One hop only.** A reference never points at another reference. On a second hop the agent previews with
  `head -100` instead of reading the file and silently acquires an incomplete rule set. Any reference over
  100 lines opens with a table of contents so a truncated preview still reveals its scope.
- Anything the agent must not miss lives in `SKILL.md` itself, near the top, never in a reference.

The test of a trigger table: a rounding change must not pull in webhook recovery, venue reconnect logic,
reconciliation design or deterministic simulation. A Binance order-submission timeout change may legitimately
pull in the generic ambiguity invariant, the exchange skill's recovery rules, and the Binance reference.

No `@file` references anywhere. `@` force-loads and burns context.

## 7. What is in `advanced/`, and why it is not installed

`advanced/fin-matching-and-settlement/` holds the skill for code that **is** the venue: matching against
resting orders, pro-rata allocation, auctions, self-trade prevention, price bands and halts, market-data
publication, netting, settlement and liquidation.

It is not installed by `npx skills add`, is not in the routing table, and does not consume the shared
description budget. Two reasons. First, its audience is a small fraction of the users of the other six, and a
description that sits in every listing is paid for by everyone. Second, shipping it by default makes the
package read as a toolkit for building institutional trading venues, which it is not: the common case is
being a venue's *client*, and that is `fin-exchange-integration`.

Nothing in `skills/` may link into it, because an installed copy would not have it. `scripts/validate.py`
enforces both that rule and the count of six.

## 8. Layout and the validator

```
skills/                     six skills, each SKILL.md plus references/
AGENTS.md                   optional routing block, under 2 KB (CLAUDE.md is a symlink)
advanced/                   material outside the installed product
incidents/                  cited incidents mapped to the rules they motivate
examples/                   before and after on four money paths
docs/architecture.md        this file
docs/failure-taxonomy.md    the thirteen ways a correct-looking system produces a wrong number
scripts/validate.py         run before every PR
.agents/skills, .claude/skills   symlinks so every harness reads one canonical tree
```

`scripts/validate.py` enforces the frontmatter spec, the description budgets, 220 lines per `SKILL.md`,
reference floors and one-hop depth, the six-skill count, the absence of the retired output block and the
retired tier scale, that every cited path exists, and that no em dash appears outside a quotation.

## 9. The evidence for the delivery model

Vercel published a leakage-hardened eval over Next.js 16 APIs absent from model training data
([*AGENTS.md outperforms Skills in our agent evals*](https://vercel.com/blog/agents-md-outperforms-skills-in-our-agent-evals),
2026-01-27). A no-documentation baseline scored 53%. The same documentation packaged as an Agent Skill also
scored 53%, because "in 56% of eval cases, the skill was never invoked". The same knowledge inlined into
`AGENTS.md` scored 100%.

**The default failure of a skill is that it is never loaded**, silently, with no signal in the transcript, and
that is worst for a correctness suite: an agent reaches for a skill when it judges a task beyond it, and every
agent believes it can already write `create_order` and `balance -= amount`. So descriptions are written as
semantic routers that match the situation the code is in rather than a keyword, and `AGENTS.md` is offered as
optional always-on reinforcement.

**Rules still do not live in `AGENTS.md`.** `npx skills add` does not write the consumer's `AGENTS.md`, so a
rule that lives only there is absent in the shipped configuration, which is the same silent failure relocated.
`AGENTS.md` carries routing and one standing instruction, states no rule a skill does not state in full, and
uninstalling it costs routing reliability and nothing else.
