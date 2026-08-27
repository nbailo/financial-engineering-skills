# Evaluation

Three layers, and only the first two run in CI, because only they are deterministic.

| Layer | What it is | In CI |
| --- | --- | --- |
| `scripts/lint_routing_lexical.py` | word overlap between a task and eight descriptions | yes, labelled a proxy |
| `scripts/check_eval_dataset.py` | proves every fixture's oracle flips, with no model | yes |
| `scripts/run_patch_eval.py` | paired model calls against those fixtures | no, manual and paid |

## What the patch eval measures

One thing: whether a model, handed a repair task and the repository files, returns a patch an
executable oracle accepts, with and without the skill text the case declares.

It does **not** measure automatic skill routing, real Codex or Claude Code tool use, file-read
traces, review-quality prose, production readiness, or cross-provider model performance. Say so
wherever a result from it is quoted.

## Why it bypasses routing

The skills-on arm is *handed* the case's declared `SKILL.md` and references. Nothing decides whether
to load them. That is deliberate: routing and repair are different questions, and measuring them
together tells you which one moved only by accident. This layer answers "does the context help once
the model has it". Whether an agent finds that context on its own is unmeasured, here and anywhere
else in this repository.

## How the paired comparison works

Both arms are identical except for a `<skill_context>` block: same task, same repository files in
the same path order, same model, same reasoning effort, same output schema, same token limit, same
runner version. Nothing tells the model which arm it is in or that either is expected to do better.

A pair is keyed on case id, a digest over the whole fixture, repeat index, model, reasoning effort,
runner version, and the repository commit. Two records are compared only when every one of those
matches. Edit a fixture and its digest changes, so old records stop pairing with new ones instead of
being silently averaged together.

Arm order is counterbalanced from the case id and repeat index, so "skills-on went second" is not
confounded with the arm, and the schedule is reproducible rather than random.

A call that fails, times out, returns an incomplete response, or returns something the schema
rejects is **invalid**. Invalid is not a fail: it never enters the skills-on/off comparison, and it
is reported separately. Counting a 500 as a failed repair would make an outage look like evidence.

## Why repair cases use executable oracles

A grader that reads prose has to be told what a good answer looks like, and then it is grading
vocabulary. An oracle runs the repaired code and asks whether the money is right. A patch passes
only when it applies, the oracle exits zero, and it touched no path the case did not declare. The
model's `summary` field is recorded and never graded.

`scripts/check_eval_dataset.py` is what makes those oracles trustworthy. For each case it runs the
oracle against the planted defect and requires a **failure**, then applies the reference fix and
requires a **pass**. A fixture that passes on its own defect measures nothing, and one that cannot
pass after the reference fix cannot be won. It also refuses symlinks, caches and bytecode inside a
case, reads each oracle's syntax tree to refuse network, subprocess and dynamic-import shapes, and
rejects a fixture that tries to supply its own command. That import scan is a lint, not a sandbox:
fixtures in this repository are trusted executable test code, and review is the boundary.

## Running it

Dry run first. This reaches no network and needs no key:

```bash
python3 scripts/run_patch_eval.py --model <model> --effort <effort> \
    --repeats 3 --cases all --skills paired --dry-run
```

It prints the exact call count, an approximate input size, and the arm order per case. A real run
additionally requires `--yes`; without it the script stops after the same plan.

```bash
export OPENAI_API_KEY=...
python3 scripts/run_patch_eval.py --model <model> --effort <effort> \
    --repeats 3 --cases all --skills paired --yes
```

There is no default model and no default effort. A silent default spends money on a choice you did
not make.

## Cost and credential boundary

Every call is paid. The dry run tells you how many before you commit to any.

`OPENAI_API_KEY` is read only when a paid run begins, held in a local variable, and used for one
header. It is never printed, never written to a run directory, never passed to an oracle, and never
present in any child environment. Oracles run with an environment built from an empty allowlist, so
there is no inherited key, proxy, or Python path for a fixture to read. Requests set `"store": false`
and enable no tools: no web search, no shell, no code interpreter, no file search, no computer use.
The model returns a patch and nothing else.

Results are written under `.agent/eval-runs/<timestamp>/`, which is gitignored, with directories at
`0700` and files at `0600`. Each run directory carries one immutable manifest, so runs cannot be
appended into a single comparison.

## What remains unmeasured

- routing: whether an agent loads the right skill unprompted
- anything about agent tool use, file reads, or multi-turn behaviour
- review quality, as distinct from repair
- other providers and other runtimes
- whether a repair that satisfies an oracle is the repair a reviewer would want

## No baseline is published

No paired result appears in this repository. The infrastructure exists and is reproducible; the
measurement has not been run, reviewed, and published. Until it has, do not quote a number from it,
and do not describe these skills as measured to improve anything.

Background reading, both from OpenAI:
[evaluation best practices](https://developers.openai.com/api/docs/guides/evaluation-best-practices)
and [agent evals](https://developers.openai.com/api/docs/guides/agent-evals).
