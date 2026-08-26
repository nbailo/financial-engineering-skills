# Evaluation

Three layers run against this repository. Each proves a different thing, and none of them proves that
the suite makes an agent better.

## 1. Lexical routing lint

```
python3 scripts/lint_routing_lexical.py
```

Scores word overlap between each task in `evals/routing-cases.yaml` and the eight skill descriptions.
No model runs.

It proves that a description still carries the vocabulary of the tasks it owns. That is the regression
this suite has shipped before: a description gets shortened for the listing budget, the venue names go
with it, and the skill stops triggering on work it owns.

It does not prove how an agent routes. The runner prints that on every run, and a case the matcher
cannot decide is reported as SKIP rather than counted as a pass. Treat it as a labelled proxy.

## 2. Dataset check

```
python3 scripts/eval_dataset_check.py
```

Checks every case under `evals/behavioral/`: the oracle fails on the repository as shipped, passes once
`fix.patch` is applied, and the planted repository is hermetic.

It proves each fixture can measure something. An oracle that already passes on the defect measures
nothing, and one that cannot pass is unwinnable. It does not prove that any agent finds the defect.

Layers 1 and 2 run in CI on every push. Layer 3 does not.

## 3. Runtime evals

```
python3 scripts/eval_runtime.py routing    --skills paired --repeat 3 --dry-run
python3 scripts/eval_runtime.py behavioral --mode review --skills paired --repeat 3 --yes
python3 scripts/eval_runtime.py behavioral --mode repair --repeat 3 --yes
```

Runs a real agent CLI over the same fixtures, writes one JSON line per run into a run directory, and
reports activation recall and precision, false-positive rate, critical-defect recall, correct-fix rate,
regression rate, and token and latency medians with p90.

This layer is manual. It never runs in CI, it gates nothing, it spends money, and it refuses to start
in a non-interactive shell without `--yes`. Any CLI can drive it through `--runtime-cmd`.

Two properties of it matter more than its numbers.

**Observed over self-report.** Which skills loaded is derived from file reads in the trace. The agent's
own account of what it used is recorded separately as `self_report` and feeds no metric. The two
disagree often enough that the report prints the divergence rate.

**Variance over means.** Agent output is nondeterministic, so the default is three repeats per case and
the report names every case whose verdict changed between them. A number from one run is an anecdote,
and the tool says so rather than printing it bare.

## What is not published

No paired baseline is recorded here. Nothing in this repository states a measured improvement over an
agent working without the skills, because no such comparison has been run and published. `--skills
paired` records one on the same model and the same reasoning setting; until a run directory holds both
arms, the report prints `no baseline recorded` instead of a number.

Prompt classes are labelled `direct`, `indirect`, `incomplete`, `mixed-domain`, `hard-negative` and
`adversarial`. A case may declare its own; otherwise the harness derives one and records that it did.
The class spread is printed before a run, including the classes the selection has none of.

`scripts/validate.py` is a structural check on the artefact. It is not an evaluation of behaviour.
