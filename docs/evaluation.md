# Evaluation

Three layers, and only the first two run in CI, because only they are deterministic.

| Layer | What it is | In CI |
| --- | --- | --- |
| `scripts/lint_routing_lexical.py` | word overlap between a task and eight descriptions | yes, labelled a proxy |
| `scripts/check_eval_dataset.py` | proves every fixture's oracle flips, with no model | yes |
| `scripts/run_patch_eval.py` | paired model calls against those fixtures | no, manual and paid |

## What the patch eval measures

One thing: whether a model, handed a repair task and the repository files, returns a patch an
executable oracle accepts, with and without the skill the case targets.

It does **not** measure automatic skill routing, real Codex or Claude Code tool use, file-read
traces, review-quality prose, production readiness, or cross-provider model performance. Say so
wherever a result from it is quoted.

## Baseline and treatment

Each case names one `target_skill` and two contexts. The **baseline** arm is handed
`baseline_context`; the **treatment** arm is handed `treatment_context`, which is
`baseline_context` with the target skill appended. The difference between the two arms is therefore
exactly one skill, by construction — `check_eval_dataset.py` refuses a case where it is not.

| Case kind | baseline_context | treatment_context |
| --- | --- | --- |
| ordinary domain case | empty | the domain skill |
| `fin-verification` case | the relevant domain skill | that domain skill **and** `fin-verification` |

The verification rows matter. `fin-verification` is layered on top of domain knowledge, not
substituted for it, so comparing "no skills" against "verification alone" would measure the domain
skill's absence and call it a verification effect. Coverage means two cases in which the treatment
arm introduces each target skill; that is what the coverage table at the end of the dataset check
counts.

## Why it bypasses routing

The treatment arm is *handed* the skill material its case declares. Nothing decides whether to load
it. That is deliberate: routing and repair are different questions, and measuring them together
tells you which one moved only by accident. This layer answers "does the context help once the
model has it". **There is no automatic routing measurement here or anywhere else in this
repository** — the lexical lint in the table above is word overlap between strings, not an agent
deciding anything.

## How the paired comparison works

Both arms are identical except for the `<skill_context>` block: same task, same repository files in
the same path order, same model, same reasoning effort, same output schema, same token limit, same
runner version. Nothing tells the model which arm it is in or that either is expected to do better.

Before the first call the runner requires a clean git tree, then **freezes** everything: every
fixture is read into memory as bytes, every skill and reference file is read once, and both arm
prompts are built and held. Nothing on disk is consulted again for prompt content, and grading
materialises the fixture from that same snapshot. An edit landing mid-run cannot change what an arm
was sent.

A run gets one cryptographically random **run id**. It appears in the manifest, in every record, in
the pair key and in the output filenames. A pair key binds the run id, the case, the repeat index
and a digest over the exact frozen prompts — the common instructions, both complete prompts, the
model, the effort, the runner version, the fixture content and the commit. Records pair only within
one run and only when all of that matched. Duplicate arms, records from another run, and records
whose prompt digest differs from the manifest are all refused.

The manifest is **written once when the run starts and finalised with a single completion flag when
it ends**. It is not immutable, and this document does not claim it is; what it provides is a fixed
statement of identity — run id, model, effort, commit, per-case prompt digests — that every record
is checked against.

Arm order is counterbalanced from a stable ordering of case ids, alternating by repeat index. For
the shipped suite of 12 cases at 3 repeats the schedule is exactly **18 baseline-first and 18
treatment-first**, and no case leads with the same arm on all three repeats.

## Invalid calls are excluded, never counted

A call that fails, times out, returns anything other than `status: completed`, or returns a
completed response without valid structured output is **invalid**. Invalid is not a fail. A pair
containing an invalid arm is listed under excluded pairs and then appears nowhere else: not in the
paired outcome table, not in the both-pass / both-fail / treatment-only / baseline-only counts, not
in the paired latency or token comparison, and not in the displayed complete-pair sample size.
Counting a 500 as a failed repair would make an outage look like evidence.

Marginal arm rates are reported twice on purpose, and the two numbers differ whenever anything was
excluded:

- over **all completed individual calls**, and
- over **calls belonging to valid complete pairs**.

Invalid-call latency is reported separately from either.

Retries are bounded: a 429, a 5xx and a transport timeout are retried at most twice, with
exponential backoff under a hard ceiling and `Retry-After` honoured when the server sends one. A
permanent 4xx and a schema failure are never retried.

## Why repair cases use executable oracles

A grader that reads prose has to be told what a good answer looks like, and then it is grading
vocabulary. An oracle runs the repaired code and asks whether the money is right. The structured
output carries two fields: `patch`, which is graded, and `summary`, which is recorded and **never**
graded.

Grading reports explicit fields rather than inferring anything from a message: `patch_valid`,
`patch_applied`, `oracle_completed`, `oracle_passed`, `outcome`, `reason`. A patch that applies and
whose oracle then times out reports `patch_applied` true and `oracle_completed` false, which is a
different fact from a patch that never applied.

A patch is refused before git sees it if it carries a rename, a copy, a new or deleted file, a mode
change, a symlink mode, a binary payload, a submodule line, an absolute path, a `..`, a path outside
`repo/`, a path the case does not declare, or a path that is not an existing file in the case. Every
path-bearing line is parsed, not just the first pair, so a second diff block appended after an
allowed hunk is caught. `git apply --unsafe-paths` is used nowhere: plain `git apply` refuses to
write outside the working area, which is one more thing that has to fail before a patch escapes.
After applying, the complete file inventory is compared against the original and the run fails if
anything was added, removed, retyped, or changed outside the declared paths.

`scripts/check_eval_dataset.py` is what makes those oracles trustworthy. For each case it runs the
oracle against the planted defect and requires a **failure**, then applies the reference fix and
requires a **pass**. A fixture that passes on its own defect measures nothing, and one that cannot
pass after the reference fix cannot be won. It also refuses symlinks, caches and bytecode inside a
case, reads each oracle's syntax tree to refuse network, subprocess and dynamic-import shapes,
rejects a fixture that tries to supply its own command, refuses a test file in `allowed_paths` so a
candidate cannot edit its own grader, and holds the reference patch to exactly the rule a candidate
patch is held to. That import scan is a lint, not a sandbox: fixtures in this repository are trusted
executable test code, and review is the boundary.

## The sandbox

Model-generated code is untrusted and is never executed on the host. `scripts/run_patch_eval.py`
requires Docker and **refuses to start without it**. There is no local fallback, because a minimal
environment is not a sandbox.

Each oracle runs in a container from an image pinned by immutable digest, with:

- `--network none`
- an unprivileged numeric user (`65534:65534`)
- `--cap-drop ALL` and `--security-opt no-new-privileges`
- a read-only root filesystem
- the patched case mounted read-only
- a writable `tmpfs`, bounded, `noexec,nosuid,nodev`
- a process limit, a memory limit and a CPU limit
- a hard wall-clock timeout, after which the container is killed rather than left running
- no host environment and no credential forwarding; the container's whole environment is eight
  `PYTHON*`/locale variables named in one place

The container receives the patched synthetic fixture, its oracle, and the fixed command that runs
that oracle. It does not receive the repository root, a home directory, the Docker socket, git
credentials, SSH configuration, API credentials, or any other host path. `fix.patch` and `case.yaml`
are deliberately left out of the mount: the reference answer has no business inside a container that
is grading a candidate's answer.

```bash
python3 scripts/run_patch_eval.py --sandbox-selftest
```

That proves outbound network is unavailable, host files and host environment are unavailable, the
process count is bounded, and every shipped oracle still fails on its defect and passes on its
reference fix **inside the container**. It costs nothing and needs no key. It is not in CI, because
CI does not need Docker for anything else.

`check_eval_dataset.py` runs on the host instead, with a child environment **built without
inheritance** — an empty dict that names what goes in, so a variable nobody thought to deny cannot
leak — plus `PYTHONDONTWRITEBYTECODE`, no user site-packages, only the case's own `repo/` on
`PYTHONPATH`, and a hard timeout. That is deliberately weaker than the container, and it is allowed
to be: nothing model-generated is ever executed there.

## Running it

Dry run first. This reaches no network and needs no key:

```bash
python3 scripts/run_patch_eval.py --model <model> --effort <effort> \
    --repeats 3 --cases all --skills paired --dry-run
```

It prints the exact call count, an approximate input size, the pinned image and the arm order per
case. A real run additionally requires `--yes` **and** an explicit `--max-calls` ceiling that the
plan must fit under:

```bash
export OPENAI_API_KEY=...
python3 scripts/run_patch_eval.py --model <model> --effort <effort> \
    --repeats 3 --cases all --skills paired --yes --max-calls 72
```

There is no default model and no default effort. A silent default spends money on a choice you did
not make. `--repeats` has a hard ceiling, retries are bounded, and `--max-invalid` stops the run
once that many calls come back invalid — the records already written are kept and the manifest is
marked incomplete.

## Cost and credential boundary

Every call is paid. The dry run tells you how many before you commit to any, and reports an
approximate input size as characters divided by four. **No monetary estimate is printed anywhere**,
because prices change and a stale number in a script is worse than no number.

`OPENAI_API_KEY` is read only when a paid run begins, held in a local variable, and used for one
header. It is never printed, never written to a run directory, never passed to an oracle, and never
present in any child environment, host or container. Requests set `"store": false` and enable no
tools: no web search, no shell, no code interpreter, no file search, no computer use.

Results are written under `.agent/eval-runs/<timestamp>-<run-id>/`, which is gitignored, with
directories at `0700` and files at `0600`.

## What remains unmeasured

- routing: whether an agent loads the right skill unprompted
- anything about agent tool use, file reads, or multi-turn behaviour
- review quality, as distinct from repair
- other providers and other runtimes
- whether a repair that satisfies an oracle is the repair a reviewer would want
- determinism across fresh interpreters: oracles assert order-independence and repeat-stability
  in-process, with `PYTHONHASHSEED` fixed, rather than by spawning a second process

## No baseline is published

No paired result appears in this repository, and the root README makes no claim about one. The
infrastructure exists and is reproducible; the measurement has not been run, reviewed and published.
Until it has, do not quote a number from it, and do not describe these skills as measured to improve
anything.

Background reading, both from OpenAI:
[evaluation best practices](https://developers.openai.com/api/docs/guides/evaluation-best-practices)
and [agent evals](https://developers.openai.com/api/docs/guides/agent-evals).
