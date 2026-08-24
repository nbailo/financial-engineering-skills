# Adoption strategy

Audience is the maintainers of this repository. None of this is copy for the README.

The project has three assets no comparable suite has — an incident catalogue built from primary documents, a
set of per-venue divergence tables that exist nowhere else, and a deliberately small rule set — and one
structural liability no amount of good content fixes: the layer we ship into is a shared, budgeted,
silently-degrading resource we do not control. This document is about spending the first on the second.
Status today: seven skills, a 7,834-byte always-on block, 20 incident files behind a 51-row mapping table, a
rule spine, and **no published evaluation of any of it in the configuration a user would actually install**.

## 1. The distribution problem, stated honestly

Every skills-capable runtime pre-loads `name` + `description` for every installed skill into the system prompt,
and that listing is budgeted:

| Runtime | Budget | Overflow behaviour |
|---|---|---|
| Claude Code | 1% of the context window (character budget); per-entry cap 1,536 chars | Shortens descriptions **starting with the skills you invoke least** |
| Codex / ChatGPT | `min(2% of context window, 8,000 characters)` | Shortens descriptions first, then **omits skills** with a warning |

Three properties matter more than anything about our content. **It is shared, not allocated:** a user running
`superpowers` (14 skills), Trail of Bits' `building-secure-contracts` (11) and a dozen personal skills has
spent most of it before we arrive, and our seven descriptions total 2,955 characters — 37% of Codex's hard
8,000-character ceiling from one suite.

**Overflow selects against us specifically.** Eviction order is least-invoked first, and a newly installed
suite is by definition the least-invoked population in the directory; climbing that ordering requires
invocations, which requires not having been evicted. **And the failure is silent, indistinguishable from never
having written the skill.** Truncation strips a description's trailing text, where the discriminating keywords
live: no error, no log line, nothing in the transcript. `/doctor` and `/context` surface it in Claude Code;
nothing surfaces it elsewhere.

An unresolved reading of the primary documentation makes it worse. Claude Code documents a *character*
budget that "scales at 1% of the model's context window", and its override `SLASH_COMMAND_TOOL_CHAR_BUDGET`
as a fixed character count; if that is literally 1% *in characters*, a 200k-token window yields ~2,000
characters of listing — less than our suite alone. The architecture assumes the permissive reading
(~4 chars/token → ~8,000, which matches the 8,000-character ceiling Codex states outright); the strict one
puts us over budget on a single-suite install, and the documentation does not say which is right.

### What follows

1. **The skill layer cannot be the primary delivery vehicle, and we designed around that.** G1–G7 live in
   `AGENTS.md` because passive context has no decision point, no ordering dependency and no shared budget.
   Adoption is therefore *guardrails-first* everywhere, including README ordering.
2. **Seven skills is the permanent ceiling.** `scope-adjudication.md` already refused three well-argued
   claimants. Not revisitable on a domain's importance — only on evidence that an existing dispatch row is
   being skipped in practice. An eighth description spends ~420 characters of somebody else's budget.
3. **Two of the seven are the designated casualties.** `fin-matching-and-settlement` is almost certainly our
   least-invoked entry; `fin-verification` is plausibly second and overlaps hosts' bundled review commands.
   The documented fallback (R4/R6) merges `fin-verification` into `fin-money-core` for ~80 core lines — a live
   option to be settled by trigger evals, not a defeat.
4. **Installs are a vanity metric.** What matters is the fraction carrying the guardrails block, and we ship
   no telemetry, so we cannot measure it. The design must be robust to its own absence — which is why every
   non-negotiable rule is restated in the first 60 lines of its skill.

## 2. Installation paths, ranked by friction


| # | Path | Reaches | Writes `AGENTS.md`? | Friction |
|---:|---|---|---|---|
| 1 | `npx skills add` | 77 agents, auto-detected | **No** | One command, no clone |
| 2 | Claude Code plugin marketplace | Claude Code only | No | Two slash commands |
| 3 | `scripts/install-guardrails.sh` | Everything reading `AGENTS.md` / `CLAUDE.md` / `copilot-instructions.md` | **Yes** | Requires a clone today |
| 4 | git submodule + symlinks | Everything, pinned | No (run #3 after) | Four commands, vendored |
| 5 | Manual copy | Whatever you copy into | No | Forks silently, rots |

**1 — `npx skills add`.** Detects which of 77 agents are installed and symlinks each agent directory to one
canonical copy, so a single `update` fixes every runtime. Document `--copy` as the thing you do not want.

```bash
npx skills add nbailo/financial-engineering-skills          # this project
npx skills add nbailo/financial-engineering-skills -g       # every project on this machine
npx skills add nbailo/financial-engineering-skills -a claude-code -a codex -a cursor
npx skills add nbailo/financial-engineering-skills -y       # CI, no prompts
npx skills update                                          # later
```

**2 — Claude Code plugin.** Namespaced `plugin:skill`, so it cannot collide with the user's personal or
project skills — the safest path for a suite meant to coexist, and the only one that can carry a
`SessionStart` hook (§6).

```
/plugin marketplace add nbailo/financial-engineering-skills
/plugin install financial-engineering-skills@financial-engineering-skills
```

**3 — the guardrails, where the value is.** Idempotent between markers, preserves existing content,
`--uninstall` removes it byte-for-byte, writes `AGENTS.md`, `CLAUDE.md` and `.github/copilot-instructions.md`
(which outranks `AGENTS.md` in Copilot's own precedence order and is the only reliable path to Copilot). It is
also the ugliest command in the README — a clone into `/tmp` and a script run from it, asking for that trust
in the same breath as telling people a skill is executable code.

```bash
git clone https://github.com/nbailo/financial-engineering-skills /tmp/fes \
  && /tmp/fes/scripts/install-guardrails.sh .
```

**4 — submodule**, for teams that vendor and want a pinned SHA. **5 — manual copy:** document it, discourage
it; a copy diverges with no runtime signal, the failure class the suite exists to teach against.

```bash
git submodule add https://github.com/nbailo/financial-engineering-skills .fin-skills
mkdir -p .agents .claude && ln -s ../.fin-skills/skills .agents/skills \
  && ln -s ../.fin-skills/skills .claude/skills
bash .fin-skills/scripts/install-guardrails.sh .
```

### The gap, and what to do about it

`npx skills add` installs skills. It does not write the consumer's `AGENTS.md`. A user who runs only the
headline command gets the weaker half of the architecture: seven files on disk, every one of which
contributes nothing at all unless the model chooses to open it on the turn that matters.

That argument does not depend on how good the content is. A skill is discretionary load sitting behind a
description that competes for a shared, truncatable budget (§1); an always-on block is passive context with
no decision point, no ordering dependency and no budget to lose. The two failure modes are not comparable —
wrong guardrails are read and are wrong, absent guardrails are simply absent. How much of the value the
always-on layer actually carries is exactly what R2 asks and what the evaluation proposed in §3 has to
settle. Ranked responses:

1. **Reorder the README so guardrails is step 1.** It is step 2 today, and the caveat that the skills
   command leaves `AGENTS.md` untouched belongs on the *skills* command, not as a justification bolted to the
   guardrails command. An afternoon's work and the highest-leverage change available.
2. **Ship the guardrails as a no-clone one-liner** — an npm package whose `bin` runs
   `install-guardrails.sh`, so the README says `npx fin-guardrails` and nothing else.
3. **Detect and say so, once.** One standing line in each `SKILL.md` body: if the guardrails markers are
   absent from the repo's `AGENTS.md`, say so in the NAMED RISKS table and continue. A required output slot,
   not a prohibition — our own wording doctrine (`rules.md` §5.1) applies to our tooling.
4. **Ask upstream.** `vercel-labs/skills` has no post-install hook a suite can declare, and every suite with
   an always-on layer has this problem. Near-zero cost to file; block nothing on it. And **do not** make the
   skills refuse to run without the guardrails: hostile, unmeasurable from inside a skill, and it defeats the
   redundancy that makes skills-only degrade rather than fail.

## 3. Proving it works

This decides whether a skeptical engineer installs, and none of it is built. What follows is a proposal for a
public evaluation: realistic tasks in the repository, scoring criteria published alongside them, results
committed as data, and enough tooling that a contributor can run the whole thing against their own model and
their own API key and get numbers we did not choose.

**The tasks.** Eight families of the prompt someone actually types — issue a refund, reconnect a market-data
socket and resume, credit a deposit when it confirms, compute realised PnL across two venues, close a period
on a ledger, settle a match, index an on-chain log, queue a withdrawal. Each ships with a written list of the
specific defects it is looking for, every one a checkable assertion with a severity, so "did the suite help"
is answered by something other than reading the diff and feeling good about it. The checks are written before
the run and committed with the task.

**The harness.**

```
evals/
  tasks/<family>/PROMPT.md      # the task, verbatim, as a user would type it
  tasks/<family>/checks.json    # check id, assertion, severity
  arms/                         # control | skills-only | guardrails-only | both
  grade/rubric.md               # PASS / PARTIAL / FAIL definitions, published
  grade/grader-prompt.md        # the LLM grader, shipped so results are reproducible
  gate-corpus.json              # real diffs labelled economic / not-economic
  trigger-queries.json          # ~20 per skill: 8-10 positive, 8-10 near-miss negative
  results/<date>-<model>-<sha>.json ; run.py ; grade.py
```

**Four arms, not two,** because the two-layer architecture's central claim *is* skills-only vs
guardrails-only; a two-arm harness confirms the suite works while leaving the actual design decision
unresolved. **Repeat every cell enough times to see the variance.** These tasks are non-deterministic, and the
interesting cases are the ones where the same model gets it right sometimes — precisely where wording has
leverage and where a single run tells you nothing. **Three models:** one small and one large in the same
family (a rule binding only on the strongest model is not a rule) and one non-Anthropic runtime, since the
suite claims cross-runtime reach and has tested none of it. Report per-model; never pool.

**Metrics — publish all six, not the headline.**

1. **Check pass rate** per task family and suite mean, per arm.
2. **Spread** (`max run − min run`) per check. *Convergence is the success metric:* a SHIP-strength check
   that still spreads under guidance means that rule's wording is descriptive, not binding — a bug in the
   rule, not noise in the run. Almost nobody publishes it, and it separates a real rule from a nice sentence.
3. **Invocation rate**, separating "the rule is wrong" from "the rule never arrived". Two different defects
   with two different fixes, and the second is invisible unless it is counted.
4. **Token and wall-clock delta** per arm; a rule that raises cost without raising pass rate is deleted.
5. **Both-arms-pass and both-arms-fail counts.** The first inflate the with-skill number and are deletion
   candidates; the second mean a broken assertion or a task the model cannot do at all.
6. **Gate false-negative rate** on `gate-corpus.json`: everything routes through G1, so a gate that
   under-fires reproduces the never-loaded failure with a respectable justification attached.

### Publication, and running it yourself

`evals/results/` committed as JSON plus a generated `docs/evaluation.md`. **Raw transcripts for at least one
run per (task, arm), committed** — a skeptic must read what the agent wrote, not our summary. Beside every
rule in `rules.md`, a link to the result that bears on it, rendered `unevaluated` where that is the truth.
**Publish the arm that loses** — the checks that passed in every arm anyway, the checks that got worse under
guidance, the families where the suite changed nothing. Not humility; the only reason to believe the rest.

```bash
python3 evals/run.py --task refund --arm both --runs 5 --model <id>     # cost and runtime printed first
python3 evals/run.py --all --arms control,skills-only,guardrails-only,both && python3 evals/grade.py results/
```

Grading is two-stage: machine assertions where the check is mechanical, the shipped LLM grader where it is
not, plus a manual spot-check — *read every flagged match by hand, because template echoes and quoted
counter-examples masquerade as hits.* Contributors submit results files, not claims.

**Limits to state in the eval document itself.** The tasks are greenfield, single-file, well-scoped prompts;
they cannot reach the four settings `rules.md` §1.4 names as where a rule still matters even though a prompt
like these never exercises it — an ORM/driver boundary, a wire schema, an analytics boundary, a multi-host
deploy. We wrote the tasks, the checks and the rules, which is not independence however carefully it is done;
writing the checks before the run helps and does not fix it, so invite an outside re-grade of one family.

## 4. Content that earns adoption on its own

Ranked by expected value, which is not ranked by effort.

**1. The incident catalogue — highest standalone pull, by a distance.** The only artefact here useful to
someone who will never install a skill, which makes it the only one linkable from somewhere we do not control.
Three properties do the work. It is *corrective*: the "Corrections you should know about" table overturns
seven widely circulated claims against the primary documents — the flash-crash report naming Waddell
& Reed (the string appears zero times in it), its "$1 trillion" loss (zero times), malleability causing Mt.
Gox's 850,000 BTC (Decker and Wattenhofer instrumented the network and found 1,811.58 BTC in conflict sets in
the year to 2014-02-07), and four more. That is a reason to cite us instead of the blog post people would
otherwise cite, and citations are distribution. It has a
*boundary*: 30 Yes / 18 Partly / 3 No on agent-detectability, the three "No" verdicts named. And an *exclusion
list* doing the same job in the other direction. Actions: stable per-incident anchors, a machine-readable
`incidents.json`, a canonical URL that does not move between releases.

**2. "What we deliberately cut and why."** Second, and what makes a skeptic keep reading. A long list of
candidate rules was rejected on one criterion — *competent code and current SDK defaults already get this
right, so a rule restating it spends attention the rules that matter need.* `Decimal` and minor units,
tick/step rounding, `MIN_NOTIONAL`, lock ordering, isolation levels, webhook signature verification, event-id
dedupe, seven of nine matching-engine cancel semantics, confirmation depth: all considered, all cut. Credible
precisely because it is a negative claim about our own work, and useful to anyone writing any skill in any
domain. Publish as a standalone page naming every cut and its reason, not a README paragraph.

**3. Per-venue divergence tables.** Third by pull, first by durability.
`references/venues/divergence-matrix.md` carries client-order-ID uniqueness scope, retention after terminal
state, which endpoint accepts the client ID, ambiguity codes, cancel/replace atomicity and filter models as
one table. Each vendor documents only itself, so it exists nowhere else. It is also our highest-maintenance
artefact, with a half-life: a stale cell is worse than a blank one because it will be trusted. Ship per-cell
dates, the "the venue file is the newer text wins" note, and re-verify before quoting.

**4. Before/after code reviews.** Highest conversion when they land — the README's retry example is the whole
pitch in twelve lines — and the most fabrication-prone format we have. Hard rule: a before/after may use only
code a model actually produced on a published eval task, linked to its committed transcript; no hand-written
"before" that no model produced. Without that rule this becomes marketing, and the temptation here is
structural. The funnel: the catalogue brings people in, the cut-list makes them believe us, the divergence
tables make a practitioner install.


## 5. Community contribution model

**What a good contribution looks like.**

- **An incident with a primary citation.** Format in `incidents/README.md`: a primary source (regulator
  order, court filing, first-party postmortem, or a commit), the loss figure *as the source states it*, the
  violated invariant as a checkable predicate, an honest agent-detectable verdict, and the rule it motivates.
  A story that changes no code does not belong, whatever the size of the loss.
- **Evidence that falsifies a rule.** The highest-value type, and the only one that can *remove* content. A
  task that exercises the rule, run repeatedly with no guidance under the §3 scoring criteria; if the model
  does not exhibit the failure, the rule is deleted. Deletion is the mechanism this rule set depends on, and
  it has to stay open to strangers or it stops working.
- **A divergence cell with a dated citation**, and **tooling bugs**: installer, validator, harness.

**The review bar.** `python3 scripts/validate.py` passes, mechanically and non-negotiably. A primary source,
or it does not merge — not a blog post citing a blog post. And **behaviour-shaping text is not restructured
without before/after eval evidence**: adopt superpowers' standing policy in `CONTRIBUTING.md`, so PRs
rewording rules to comply with external style guidance are declined without eval results. That rejects
good-faith work, including a lot of agent-generated work, and is correct anyway.

**Keeping the rule set from bloating** is where every checklist project dies, and aspiration will not prevent
it. Four mechanisms, all mechanical:

1. **Budgets in CI, already partly built.** `validate.py` enforces 500 lines per `SKILL.md`, 430 chars per
   description, 3,000 chars suite-wide, 8,192 bytes for `AGENTS.md`. Extend it with a **per-skill rule count**
   read from `shared/invariants.yaml` and fail the build when the count rises.
2. **Admission requires eviction.** With the rule count in CI, a PR adding a rule must either name the rule it
   replaces or show the budget has room. Not a review norm someone enforces under social pressure — a red
   build.
3. **Every rule names the failure it prevents.** A rule that cannot point to an incident, a primary source
   documenting the trap, or a task on which the model exhibits the failure is not merged; a rule kept where
   no task exhibits it carries a mandatory "kept because" clause naming the setting where it still fails.
   `rules.md` already carries that discipline per rule.
4. **Annual re-evaluation.** Models improve. A rule earning its tokens today may be redundant next year, at
   which point it is pure cost pushing real rules toward the compaction cliff. Re-run the no-guidance arm on
   each major model release and delete every rule whose failure has stopped occurring. It is the only
   mechanism here that makes the rule set shrinkable rather than merely appendable.

**The G1–G7 block is closed.** Seven standing rules, ≤8KB, no additions: a candidate always-on rule either
fits inside an existing G-rule's text or goes in a skill. That block costs tokens on every turn of every
user's session — the one budget where our growth is a direct tax on everyone who installed us.

## 6. Ecosystem compatibility

| Runtime | Reached today by | Ship now | Maintenance |
|---|---|---|---|
| Claude Code | `.claude/skills/`, `CLAUDE.md` symlink, plugin | all three | low |
| Codex / ChatGPT | `.agents/skills/`, `AGENTS.md` | as-is | low |
| Cursor | `.agents/skills/`, `.claude/skills/` back-compat, `AGENTS.md` | as-is | none |
| Copilot / VS Code | `.agents/skills/`, `.claude/skills/`, `.github/copilot-instructions.md` | **full block, not a pointer** | low |
| Amp | `.agents/skills/`, `.claude/skills/`, `AGENTS.md` | as-is | none |
| Gemini CLI | `.agents/skills/`, `AGENTS.md` | as-is | none |

Everything above is covered by two symlinked directories and one always-on file; the incremental cost of the
current cross-runtime story is approximately zero, which is the point of the lowest-common-denominator
artefact.

**One change owed.** `.github/copilot-instructions.md` is a three-line pointer to `AGENTS.md` today. Copilot
ranks `copilot-instructions.md` *above* `AGENTS.md` in its own precedence order, and agent instruction files
are documented as not supported by all Copilot features. A pointer relies on the agent choosing to open what
it points at — the discretionary-load failure this design exists to avoid. Write the full block.

**Defer.**

- **`gemini-extension.json`, `.codex-plugin/`, and the rest of the per-harness manifest set.** superpowers
  ships nine. Each is a release-time chore with a silent failure mode, and each reaches a runtime
  `.agents/skills/` already reaches. Rule: ship a manifest only when it reaches a runtime nothing else does.
- **The `SessionStart` hook.** Real value — the difference between installed and activated — and real cost: it
  must emit exactly one JSON key per detected harness, because Claude Code reads both `additional_context` and
  `hookSpecificOutput.additionalContext` without deduplication and will double-inject; superpowers also hit a
  bash 5.3 heredoc hang doing it. Plugin path only, only after the harness shows it moves invocation rate,
  never load-bearing.
- **claude.ai / Skills API upload.** A separate surface that does not sync, with no network access. Our value
  is in a repository next to code. Defer indefinitely.
- **`.cursor/rules/*.mdc` and `.github/instructions/*.instructions.md`** — divergent copies of content already
  delivered, for runtimes already covered; they rot, and the rot is invisible. **`.cursorrules`** never: it is
  absent from Cursor's current docs, which is not a citation for deprecation, so do not assert one.

## 7. Anti-goals

**Rule-count growth as a metric.** Adding rules is the cheapest action available and the most expensive
outcome: every rule competes for attention inside a 5,000-token compaction window and pushes the rules that
matter closer to the cliff. Our headline number is the count of candidate rules *deleted* on evidence, not
the count shipped. If a release note leads with how much was added, something has gone wrong.

**SEO content.** "Top 10 fintech coding mistakes" attracts readers who will not install, dilutes the
catalogue's citation weight, and is the exact genre this project exists as a counterexample to. Every page we
publish survives the same evidence audit as an incident file, or does not ship.

**Badge collecting.** "Works with 77 agents" is a claim about a CLI's agent table, not about us; we have
verified behaviour on approximately none of them. The honest claim is that we install into the directories
those runtimes document, plus — once the evaluation runs — the two or three we actually ran it on. Same for
stars, awesome-lists, and marketplace listings that require no proof.

**Expanding into general security.** The distinction *is* the product. The first reentrancy section in
`fin-onchain` makes us a worse `building-secure-contracts` and collapses the framing the suite rests on —
*every component behaved exactly as specified.* Route out by name in the README; keep the exclusion list in
`incidents/README.md`, where Wormhole is the worked example of a real bug that is somebody else's.

**Also:** telemetry in the installer; a hosted service; a certification; a numeric "financial correctness
score"; accepting an incident because it is famous rather than because it changes code.

## 8. Staged plan

### v0.1 — what exists now

Seven skills of roughly 240–340 lines each, 2,955 of 3,000 description characters, `AGENTS.md` at 7,834 of
8,192 bytes, G1–G7, 20 incident files behind a 51-row mapping table, `docs/rules.md`, `scope-adjudication.md`,
`scripts/validate.py`, `scripts/install-guardrails.sh`, both plugin manifests, and the `.agents/skills` +
`.claude/skills` symlinks. Not built, despite being specified in `architecture.md` §10: `evals/` (nothing at
all), `shared/invariants.yaml` and the four checkers depending on it, any CI workflow (`.github/` holds one
file), `CONTRIBUTING.md`, the npm package, the hook. Status in one line: **fully specified, not yet
evaluated.**

### Gate v0.1 → v0.2 — three artefacts, not a date

1. The four-arm harness exists and has run on two models across **at least the refund, reconnect-and-resume
   and realised-PnL families** — the three that most directly exercise the rules the suite leads with —
   results committed.
2. `gate-corpus.json` exists and G1's **false-negative rate is published**. Everything routes through the
   gate, and it is currently five questions derived from failure modes rather than from real diffs.
3. The results are published whatever they say — including `skills-only ≈ control`, and including
   `guardrails-only ≈ control`. If the second holds, R2 is falsified and the architecture is wrong in its most
   load-bearing decision. Better to learn that now than after asking people to install.

Nothing else ships before this: not more incidents, not an eighth skill, not a marketplace listing.

### v0.2 — evaluated, narrower, easier to install

- `evals/` complete; `docs/evaluation.md` generated; every rule in `rules.md` carrying a link to the result
  that bears on it, with `unevaluated` rendered where that is true.
- **Rules deleted on the eval data.** If a full run produces zero deletions, the harness is not
  discriminating, and that is the finding.
- `npx fin-guardrails`, the README reordered so guardrails is step 1, and the full block written into
  `.github/copilot-instructions.md`.
- `CONTRIBUTING.md` stating the rejection reasons up front, plus admission-requires-eviction and the
  rule-count budget in CI. `incidents.json`, stable anchors, and the cut-list as a standalone page.
- The `fin-verification` question settled on trigger-eval evidence: keep, or merge into `fin-money-core`.

### Gate v0.2 → v1.0

1. **Convergence.** Every SHIP-strength check passing on every run, with zero spread, on at least two
   models. A SHIP check that still spreads means that rule's wording is descriptive rather than binding — a
   v0.2 bug, not a release note.
2. **Trigger evals pass.** ≥0.5 on positives, <0.5 on near-miss negatives, 20 labelled queries per skill,
   60/40 train/validation, with the four known-hard near-misses in by name: *"add a refund button"*, *"track
   my position across two exchanges"*, *"credit the user when the deposit confirms"* (must load **both**
   `fin-onchain` and `fin-ledger`; loading one is a failure), and *"write a script that reads a CSV of
   transactions and uploads to Postgres"* (must trigger nothing).
3. **Gate false-negative rate below a published threshold**, survivors listed rather than summarised.
4. **At least one external contribution merged under the review bar**, proving the bar is workable by someone
   who did not write it — the one condition here that is a judgement call.

### v1.0 — what it means

Stable rule identifiers, and semver on the rule spine: deleting a rule is a major, changing a rule's text with
eval evidence is a minor, an incident or divergence cell is a patch. A published evaluation with committed raw
transcripts, a documented deprecation path for rules, and the annual re-evaluation scheduled against major
model releases. v1.0 is explicitly **not** more content — it is the same content with evidence attached and a
working mechanism for taking it away again. Not in v1.0 in any circumstance: an eighth skill, a hosted
anything, general security coverage, or a manifest for a runtime `.agents/skills/` already reaches.
