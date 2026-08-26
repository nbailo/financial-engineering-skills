#!/usr/bin/env python3
"""Runtime evaluation: drive a real agent CLI over the fixtures and record what it did.

MANUAL ONLY. This script never runs in CI and it gates nothing. No workflow invokes it, no hook
invokes it, and no exit status it returns is a merge condition. Wiring it into CI would be a
mistake: it spends money, it needs a credentialed CLI on the machine, and its output is
nondeterministic, so a red run would be noise rather than a signal.

WHY IT EXISTS. The other two evaluation layers here run no model. `scripts/lint_routing_lexical.py`
scores word overlap between a task and eight frontmatter descriptions.
`scripts/eval_dataset_check.py`
checks that the behavioural fixtures are well formed and that each oracle fails on the planted
defect and passes on the reference fix. Neither one observes an agent. This one does.

WHAT IT MEASURES. Which SKILL.md files an agent opened, which reference files it opened inside
them, and whether its output named the economic defect the fixture planted. Activation comes from
the trace, never from the agent's account of itself: a file read is an event, a claim is prose.
Both are recorded, in separate fields, and only the event is counted. `self_report` exists so a
reader can see how far the two diverge, which on some runs is far.

WHAT IT DOES NOT MEASURE. Anything, from one run. Agent output varies between repetitions of the
identical prompt, so the default is three repeats per case, and every figure is printed with its
per-case variance. With --repeat 1 the report says in plain words that the numbers are anecdotes.
Skills-on figures alone say nothing about whether the skills helped; that needs --skills paired,
and until a paired run exists the report prints "no baseline recorded" rather than a number.

CREDENTIALS AND NETWORK. The harness holds no credential. It passes the current environment
through to the CLI it invokes and lets that CLI find its own key the way it normally does; it
never reads a credential variable by name, never writes one, and never prints one. Before a trace,
a recorded command line or a run record reaches disk it is passed through a redactor for
credential shapes. The harness itself opens no socket and sends no telemetry anywhere: the only
outbound traffic on a run is whatever the agent CLI makes on its own account.

USAGE
    python3 scripts/eval_runtime.py routing    --repeat 3 --skills paired --dry-run
    python3 scripts/eval_runtime.py routing    --repeat 3 --skills paired --yes
    python3 scripts/eval_runtime.py behavioral --mode review --repeat 3 --skills paired --yes
    python3 scripts/eval_runtime.py behavioral --mode repair --repeat 3 --skills on --yes
    python3 scripts/eval_runtime.py report --run-dir .agent/eval-runs/<stamp>

    # any other agent CLI, no code change required
    python3 scripts/eval_runtime.py routing --yes --runtime-cmd \
        'codex exec --model {model} --cd {cwd} {prompt}'

A run costs money, so it asks before it starts. An interactive shell is prompted; a
non-interactive one is refused unless --yes is passed, which is the second reason a CI job
cannot run this by accident.

Exit status: 0 when the requested runs completed and were recorded, 2 when the harness cannot
measure anything (CLI absent, fixture absent, fixture invalid, spend not confirmed). It never
fabricates a result and never simulates a runtime.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shlex
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

try:
    import yaml
except ImportError:
    print("PyYAML is required: pip install pyyaml", file=sys.stderr)
    raise SystemExit(2)

ROOT = Path(__file__).resolve().parent.parent
ROUTING_FIXTURE = ROOT / "evals" / "routing-cases.yaml"
BEHAVIORAL_DIR = ROOT / "evals" / "behavioral"
SKILL_DIRS = (ROOT / "skills", ROOT / "advanced")

# Run output lands in the gitignored local scratch area. Traces quote repository content and
# whatever the agent wrote about it, and a run directory can reach tens of megabytes, so the
# default is somewhere a clone never carries. --run-dir overrides it.
DEFAULT_RUN_ROOT = ROOT / ".agent" / "eval-runs"

SCHEMA = "fin-eval-runtime/1"
DEFAULT_TIMEOUT_S = 900
ORACLE_TIMEOUT_S = 60

BLD, RED, GRN, YEL, OFF = "\033[1m", "\033[31m", "\033[32m", "\033[33m", "\033[0m"
if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
    BLD = RED = GRN = YEL = OFF = ""


# --------------------------------------------------------------------------- redaction

# Credential shapes, redacted before anything is written or printed. The list mirrors
# scripts/secret-scan.sh so that a shape the push-time scanner blocks is also a shape a trace
# cannot carry. The two assignment patterns are the general case: a value long enough to be a
# credential, attached to a name that says it is one. A miss here is a leak, so the patterns
# are deliberately loose and the replacement keeps only the pattern name.
REDACTIONS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("pem-private-key",
     re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]{0,20000}?-----END [A-Z ]*PRIVATE KEY-----")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}")),
    ("github-fine-grained-pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}")),
    ("aws-access-key-id", re.compile(r"\b(?:AKIA|ASIA|ABIA|ACCA)[A-Z0-9]{16}\b")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
    ("stripe-key", re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}")),
    ("google-api-key", re.compile(r"\bAIza[A-Za-z0-9_-]{35}\b")),
    ("anthropic-api-key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}")),
    ("openai-key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{32,}")),
    ("npm-token", re.compile(r"\bnpm_[A-Za-z0-9]{30,}")),
    ("json-web-token", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]*")),
    ("hex-private-key", re.compile(r"\b(?:0x)?[0-9a-fA-F]{64}\b")),
    ("assigned-credential", re.compile(
        r"(?i)\b(?:api[_-]?key|api[_-]?secret|access[_-]?token|auth[_-]?token|refresh[_-]?token"
        r"|client[_-]?secret|secret[_-]?key|password|passphrase|mnemonic)"
        r"[A-Za-z0-9_]*\s*[=:]\s*[\"'\\]{0,3}([A-Za-z0-9/+=_.-]{20,})")),
    ("bearer-header", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{20,}")),
)

_HOME = str(Path.home())


def sanitize(text: str) -> str:
    """Replace credential shapes and the operator's home path with fixed markers."""
    out = text
    for name, pattern in REDACTIONS:
        out = pattern.sub(f"[redacted:{name}]", out)
    if _HOME and _HOME not in ("/", ""):
        out = out.replace(_HOME, "~")
    return out


# --------------------------------------------------------------------------- trace reading

# Path shapes that count as evidence. Both an installed layout (.claude/skills/fin-ledger/...)
# and a repository-relative one (skills/fin-ledger/...) reduce to the same pair of names.
SKILL_FILE_RE = re.compile(
    r"(?:skills|advanced)/(fin-[a-z0-9-]+)/(SKILL\.md|references/[A-Za-z0-9_./-]+\.md)")

READ_TOOLS = frozenset({
    "read", "view", "file_read", "read_file", "open_file", "cat", "glob", "grep", "search_files",
    "notebookread", "str_replace_editor", "str_replace_based_edit_tool", "text_editor",
})
SHELL_TOOLS = frozenset({"bash", "shell", "terminal", "run_command", "execute_command", "exec"})
SHELL_READ_RE = re.compile(r"\b(?:cat|head|tail|sed|awk|less|more|nl|bat|rg|grep|wc|find|ls)\b")
SKILL_TOOLS = frozenset({"skill", "skills", "use_skill", "load_skill", "invoke_skill"})


def _walk(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)


def _strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from _strings(value)


def iter_json_lines(text: str):
    """Every line of the stream that parses as a JSON object or array.

    Written for stream-json and tolerant of anything else: a CLI that prints plain text simply
    yields nothing here, and the run is recorded with no observed activation and a note saying
    the trace carried no parseable tool events. That is an honest zero, not a failure.
    """
    for line in text.splitlines():
        line = line.strip()
        if not line or line[0] not in "{[":
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def tool_calls(objects) -> list[tuple[str, object]]:
    """(tool name, payload) for every tool invocation the trace contains."""
    out: list[tuple[str, object]] = []
    for obj in objects:
        for node in _walk(obj):
            name = node.get("name") or node.get("tool") or node.get("tool_name")
            if not isinstance(name, str):
                continue
            payload = None
            for key in ("input", "args", "arguments", "parameters", "tool_input"):
                if key in node:
                    payload = node[key]
                    break
            typed = node.get("type") in ("tool_use", "tool_call", "function_call")
            if payload is None and not typed:
                continue
            out.append((name, payload if payload is not None else {}))
    return out


def channel_for(tool: str) -> str:
    low = tool.lower().replace("-", "_")
    if low in SKILL_TOOLS:
        return "skill-tool"
    if low in READ_TOOLS:
        return "file-read"
    if low in SHELL_TOOLS:
        return "shell"
    return "other"


def observe(text: str) -> dict:
    """Derive activation from the trace.

    The primary signal is a FILE READ naming a path under skills/ or advanced/. That is the
    event that puts skill text into the agent's context, and it is the only channel counted in
    a metric. A shell command that reads such a path counts on the same terms, because `sed -n
    1,80p skills/fin-ledger/SKILL.md` is a file read wearing a different tool name.

    A skill-dispatch tool is recorded in its own field. It is an observed event too, but what it
    puts in the context depends on the runtime, so it is kept out of the counted signal rather
    than quietly folded into it.
    """
    objects = list(iter_json_lines(text))
    calls = tool_calls(objects)

    evidence: list[dict] = []
    selected: set[str] = set()
    references: set[str] = set()
    invocations: set[str] = set()

    for tool, payload in calls:
        channel = channel_for(tool)
        blob = " ".join(_strings(payload))
        if channel == "skill-tool":
            for name in re.findall(r"fin-[a-z0-9-]+", blob):
                invocations.add(name)
            continue
        if channel == "shell" and not SHELL_READ_RE.search(blob):
            continue
        if channel not in ("file-read", "shell"):
            continue
        for skill, rest in SKILL_FILE_RE.findall(blob):
            selected.add(skill)
            if rest != "SKILL.md":
                references.add(f"{skill}/{rest[len('references/'):]}")
            evidence.append({"channel": "file-read", "tool": tool, "path": f"{skill}/{rest}"})

    return {
        "selected_skills": sorted(selected),
        "loaded_references": sorted(references),
        "skill_tool_invocations": sorted(invocations),
        "evidence": evidence[:200],
        "tool_calls": len(calls),
        "parseable_events": len(objects),
    }


def final_text(text: str) -> str:
    """The agent's last word: the result field where the CLI emits one, else assistant text."""
    objects = list(iter_json_lines(text))
    for obj in reversed(objects):
        if not isinstance(obj, dict) or obj.get("type") != "result":
            continue
        if isinstance(obj.get("result"), str):
            return obj["result"]
    chunks: list[str] = []
    for obj in objects:
        for node in _walk(obj):
            if node.get("type") == "text" and isinstance(node.get("text"), str):
                chunks.append(node["text"])
    if chunks:
        return "\n".join(chunks)
    return text


def token_usage(text: str) -> dict | None:
    """Token counts read from usage blocks in the trace, or None when the CLI reports none."""
    totals = Counter()
    found = False
    for obj in iter_json_lines(text):
        for node in _walk(obj):
            usage = node.get("usage")
            if not isinstance(usage, dict):
                continue
            for key in ("input_tokens", "output_tokens", "cache_read_input_tokens",
                        "cache_creation_input_tokens"):
                value = usage.get(key)
                if isinstance(value, int):
                    totals[key] += value
                    found = True
    if not found:
        return None
    out = dict(totals)
    out["total"] = sum(totals.values())
    return out


# --------------------------------------------------------------------------- fixtures

def known_skills(include_advanced: bool = True) -> dict[str, Path]:
    """Every skill in the tree, or only the six an install actually carries.

    advanced/ is opt-in for a user, so installing it into the workspace by default would let an
    agent load a skill no ordinary reader has, and charge a false positive nobody could hit.
    """
    out: dict[str, Path] = {}
    for base in (SKILL_DIRS if include_advanced else SKILL_DIRS[:1]):
        for skill_md in sorted(base.glob("*/SKILL.md")):
            out[skill_md.parent.name] = skill_md.parent
    return out


CLASS_STOPWORDS = frozenset("""
a an and are as at be by for from has have in into is it its of on or that the their them they
this to use used using when where which with your you not no also any all one two more most
code system change touch touches value someone owed call moves defer domain skill skills
""".split())


def _class_terms(text: str) -> set[str]:
    words = re.findall(r"[A-Za-z][A-Za-z0-9.+_]{2,}", text.lower())
    return {w.rstrip(".") for w in words} - CLASS_STOPWORDS


def _description_of(path: Path) -> str:
    text = (path / "SKILL.md").read_text(encoding="utf-8")
    match = re.search(r"(?ms)^description:\s*(.+?)^\S", text)
    return match.group(1) if match else text[:1500]


def distinctive_terms(skills: dict[str, Path]) -> dict[str, set[str]]:
    """Terms that belong to at most two skill descriptions, mapped to the skills carrying them.

    This is the vocabulary a task can name to ask for a skill in that skill's own words:
    binance, chargeback, reorg, tigerbeetle, holds. A term that half the descriptions carry
    identifies nothing, so it is dropped. Deriving the set from the descriptions on every run
    keeps it from going stale the way a hardcoded vendor list would.
    """
    index: dict[str, set[str]] = defaultdict(set)
    for name, path in skills.items():
        for term in _class_terms(_description_of(path)):
            index[term].add(name)
    return {term: owners for term, owners in index.items() if len(owners) <= 2}


PROMPT_CLASSES = ("direct", "indirect", "incomplete", "mixed-domain", "hard-negative",
                  "adversarial")


def classify(case: dict, index: dict[str, set[str]]) -> tuple[str, str]:
    """(class, source). A declared prompt_class wins; otherwise the label is derived here.

    The derivation, in order, so that a reader can argue with it:
      hard-negative  the case expects no skill at all
      adversarial    the case says the surface vocabulary pulls somewhere it must not go,
                     either through decided_by: exclusion or through a forbid_probe
      mixed-domain   two or more skills are expected
      incomplete     under thirty words, which is a prompt that leaves the work underspecified
      direct         the task names a term distinctive to the skill it expects
      indirect       it expects a skill and names none of that skill's distinctive terms

    A derived label is a reading of the case, not a property of it, which is why the source is
    recorded beside it and printed in the report. Add `prompt_class:` to a case to overrule it.
    """
    declared = case.get("prompt_class")
    if isinstance(declared, str) and declared in PROMPT_CLASSES:
        return declared, "declared"

    # A routing case names the skills it expects; a behavioural case names the one skill it
    # belongs to. Both answer the same question here, which is what the task had to say for
    # itself before any skill was chosen.
    expect = list(case.get("expect_skills") or [])
    if not expect and isinstance(case.get("skill"), str):
        expect = [case["skill"]]
    task = str(case.get("task") or "")

    if "expect_skills" in case and not expect:
        return "hard-negative", "derived"
    if case.get("decided_by") == "exclusion" or case.get("forbid_probe"):
        return "adversarial", "derived"
    if len(expect) >= 2:
        return "mixed-domain", "derived"
    if len(task.split()) < 30:
        return "incomplete", "derived"
    if not expect:
        return "indirect", "derived"
    wanted = set(expect)
    for term in _class_terms(task):
        if wanted & index.get(term, set()):
            return "direct", "derived"
    return "indirect", "derived"


def load_routing_cases() -> list[dict]:
    fixture = yaml.safe_load(ROUTING_FIXTURE.read_text(encoding="utf-8")) or {}
    return list(fixture.get("cases") or [])


def declared_modes(case: dict) -> list[str]:
    """The modes a case says it supports, whether it wrote them as a list or as one word.

    `mode: repair` and `mode: [review, repair]` both appear in the fixtures. Matching a scalar
    with `in` would make "review" match "review-only" and any other string that contains it, so
    the value is split into names first.
    """
    mode = case.get("mode")
    if isinstance(mode, str):
        return [m for m in re.split(r"[,\s\[\]]+", mode) if m]
    return [str(m) for m in (mode or [])]


def load_behavioral_cases() -> list[dict]:
    cases: list[dict] = []
    for case_yaml in sorted(BEHAVIORAL_DIR.glob("*/case.yaml")):
        data = yaml.safe_load(case_yaml.read_text(encoding="utf-8")) or {}
        data["_dir"] = case_yaml.parent
        cases.append(data)
    return cases


# --------------------------------------------------------------------------- workspaces

def install_skills(workspace: Path, include_advanced: bool) -> None:
    target = workspace / ".claude" / "skills"
    target.mkdir(parents=True, exist_ok=True)
    for name, path in known_skills(include_advanced).items():
        shutil.copytree(path, target / name, dirs_exist_ok=True)


def build_argv(cfg, prompt: str, prompt_file: Path, cwd: Path, allow_edits: bool) -> list[str]:
    if cfg.runtime_cmd:
        argv = []
        for token in shlex.split(cfg.runtime_cmd):
            argv.append(token
                        .replace("{prompt_file}", str(prompt_file))
                        .replace("{prompt}", prompt)
                        .replace("{cwd}", str(cwd))
                        .replace("{model}", cfg.model or "")
                        .replace("{reasoning}", cfg.reasoning or "")
                        .replace("{skills}", cfg.skills_arm))
        return argv
    argv = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose"]
    if cfg.model:
        argv += ["--model", cfg.model]
    if allow_edits:
        argv += ["--permission-mode", "acceptEdits"]
    argv += list(cfg.extra_args)
    return argv


def invoke(argv: list[str], cwd: Path, timeout: int) -> tuple[str, int, float, str | None]:
    """Run the CLI. Returns (stdout+stderr, exit code, wall seconds, note)."""
    started = time.monotonic()
    try:
        proc = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True,
                              timeout=timeout, env=os.environ.copy())
        elapsed = time.monotonic() - started
        blob = proc.stdout + ("\n" + proc.stderr if proc.stderr else "")
        return blob, proc.returncode, elapsed, None
    except subprocess.TimeoutExpired as exc:
        elapsed = time.monotonic() - started
        partial = (exc.stdout or "") + (exc.stderr or "")
        if isinstance(partial, bytes):
            partial = partial.decode("utf-8", "replace")
        return partial, 124, elapsed, f"timed out after {timeout}s"


# --------------------------------------------------------------------------- grading

def grade_routing(case: dict, observed: dict) -> dict:
    expected = set(case.get("expect_skills") or [])
    forbidden = set(case.get("forbid_skills") or [])
    allowed = set(case.get("allow_skills") or [])
    selected = set(observed["selected_skills"])

    missing = sorted(expected - selected)
    forbidden_loaded = sorted(selected & forbidden)
    unexpected = sorted(selected - expected - allowed - forbidden)
    tolerated = sorted(selected & allowed - expected)

    want_refs = list(case.get("expect_references") or [])
    got_refs = observed["loaded_references"]
    ref_hits = [r for r in want_refs if any(g.endswith("/" + r) or g.endswith(r) for g in got_refs)]

    return {
        "status": "pass" if not missing and not forbidden_loaded and not unexpected else "fail",
        "expected": sorted(expected),
        "missing": missing,
        "forbidden_loaded": forbidden_loaded,
        "unexpected": unexpected,
        "tolerated": tolerated,
        "references_expected": want_refs,
        "references_hit": ref_hits,
    }


def grade_review(case: dict, answer: str) -> dict:
    low = answer.lower()
    hit, missed = [], []
    for finding in case.get("expect_findings") or []:
        literals = [str(x) for x in (finding.get("must_mention") or [])]
        fid = str(finding.get("id", "unnamed"))
        (hit if literals and all(x.lower() in low for x in literals) else missed).append(fid)
    total = len(hit) + len(missed)
    return {
        "status": "pass" if total and not missed else "fail",
        "findings_hit": hit,
        "findings_missed": missed,
        "critical_recall": (len(hit) / total) if total else None,
        # Hard negatives are prose, not literals, so no regex decides them. They are carried into
        # the record for a human to read the trace against, and no metric is computed from them.
        "hard_negatives_for_manual_review": list(case.get("hard_negatives") or []),
    }


def run_python(args: list[str], cwd: Path, pythonpath: Path) -> tuple[int, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(pythonpath) + os.pathsep + env.get("PYTHONPATH", "")
    try:
        proc = subprocess.run([sys.executable] + args, cwd=str(cwd), capture_output=True,
                              text=True, timeout=ORACLE_TIMEOUT_S, env=env)
        return proc.returncode, (proc.stdout + proc.stderr)[-4000:]
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {ORACLE_TIMEOUT_S}s"


def grade_repair(case_dir: Path, work: Path) -> dict:
    """Apply the hidden oracle to the workspace the agent edited.

    The oracle directory is copied in only after the agent has finished, so nothing under
    oracle/ was ever visible to the agent under test. Regression is measured against checks that
    passed on the shipped repo: byte-compilation of every module, plus oracle/test_regression.py
    where a case ships one. Compile-only is a weak check and the report says so.
    """
    shutil.copytree(case_dir / "oracle", work / "oracle", dirs_exist_ok=True)
    repo = work / "repo"

    compile_code, _ = run_python(["-m", "compileall", "-q", str(repo)], work, repo)
    oracle_code, oracle_tail = run_python(["oracle/test_oracle.py"], work, repo)

    extra_code = None
    if (work / "oracle" / "test_regression.py").is_file():
        extra_code, _ = run_python(["oracle/test_regression.py"], work, repo)

    regression = compile_code != 0 or (extra_code is not None and extra_code != 0)
    return {
        "status": "pass" if oracle_code == 0 and not regression else "fail",
        "oracle_after": "pass" if oracle_code == 0 else "fail",
        "oracle_tail": sanitize(oracle_tail)[-1200:],
        "compiles": compile_code == 0,
        "regression_check": ("compile+test_regression.py" if extra_code is not None
                            else "compile-only"),
        "regression": regression,
    }


def precheck_case(case_dir: Path) -> tuple[bool, str]:
    """The contract in one assertion: the oracle must fail on the repo as shipped."""
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        shutil.copytree(case_dir / "repo", work / "repo")
        shutil.copytree(case_dir / "oracle", work / "oracle")
        code, _ = run_python(["oracle/test_oracle.py"], work, work / "repo")
        if code == 0:
            return False, "oracle passes on the repo as shipped, so the case measures nothing"
        repo = work / "repo"
        compile_code, _ = run_python(["-m", "compileall", "-q", str(repo)], work, repo)
        if compile_code != 0:
            return False, ("repo as shipped does not byte-compile, so no regression check "
                           "is possible")
    return True, ""


# --------------------------------------------------------------------------- the run loop

class Config:
    def __init__(self, args):
        self.runtime_cmd: str | None = args.runtime_cmd
        self.model: str | None = args.model
        self.reasoning: str | None = args.reasoning
        self.extra_args: list[str] = args.extra_arg or []
        self.timeout: int = args.timeout
        self.include_advanced: bool = bool(getattr(args, "advanced", False))
        self.skills_arm = "on"

    @property
    def runtime_label(self) -> str:
        if self.runtime_cmd:
            return Path(shlex.split(self.runtime_cmd)[0]).name
        return "claude"


def write_record(handle, record: dict) -> None:
    """One sanitized NDJSON line. A record that no longer parses after redaction is replaced
    by a stub naming the case, because a broken line would break the report for every case."""
    line = sanitize(json.dumps(record, sort_keys=True))
    try:
        json.loads(line)
    except json.JSONDecodeError:
        line = json.dumps({"schema": SCHEMA, "case": record.get("case"),
                           "error": "record dropped: redaction broke the JSON"})
    handle.write(line + "\n")
    handle.flush()


def do_run(cfg: Config, run_dir: Path, handle, suite: str, mode: str, case: dict,
           case_id: str, prompt: str, prompt_class: tuple[str, str], arm: str,
           index: int, case_dir: Path | None) -> dict:
    cfg.skills_arm = arm
    notes: list[str] = []

    tmp = Path(tempfile.mkdtemp(prefix=f"fin-eval-{case_id}-"))
    try:
        work = tmp / "work"
        if case_dir is not None:
            shutil.copytree(case_dir / "repo", work / "repo")
            cwd = work / "repo"
        else:
            work.mkdir(parents=True)
            cwd = work
        if arm == "on":
            install_skills(cwd, cfg.include_advanced)
        prompt_file = tmp / "prompt.txt"
        prompt_file.write_text(prompt, encoding="utf-8")

        argv = build_argv(cfg, prompt, prompt_file, cwd, allow_edits=(mode == "repair"))
        blob, code, elapsed, note = invoke(argv, cwd, cfg.timeout)
        if note:
            notes.append(note)

        observed = observe(blob)
        answer = final_text(blob)
        if observed["parseable_events"] == 0:
            notes.append("trace carried no parseable JSON events; observed activation is a "
                         "floor, not a count")

        if arm == "on":
            shutil.rmtree(cwd / ".claude", ignore_errors=True)

        if suite == "routing":
            verdict = grade_routing(case, observed)
        elif mode == "review":
            verdict = grade_review(case, answer)
        else:
            verdict = grade_repair(case_dir, work)

        trace_name = f"{case_id}__{mode}__skills-{arm}__{index}.txt"
        (run_dir / "traces" / trace_name).write_text(sanitize(blob), encoding="utf-8")

        known = set(known_skills(True))
        claimed = sorted({n for n in re.findall(r"fin-[a-z0-9-]+", answer)} & known)

        record = {
            "schema": SCHEMA,
            "recorded_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "suite": suite,
            "mode": mode,
            "case": case_id,
            "prompt_class": prompt_class[0],
            "prompt_class_source": prompt_class[1],
            "repeat_index": index,
            "runtime": cfg.runtime_label,
            "runtime_argv": [sanitize(a) for a in argv],
            "model": cfg.model,
            "reasoning": cfg.reasoning,
            "skills": arm,
            "observed": observed,
            "self_report": {
                "skills_named_in_output": claimed,
                "note": "self-report is recorded for contrast and is used in no metric",
            },
            "verdict": verdict,
            "tokens": token_usage(blob),
            "latency_s": round(elapsed, 3),
            "exit_code": code,
            "trace": f"traces/{trace_name}",
            "notes": notes,
        }
        write_record(handle, record)
        return record
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------- reporting

def pctile(values: list[float], q: float):
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    idx = max(0, min(len(ordered) - 1, math.ceil(q * len(ordered)) - 1))
    return ordered[idx]


def fmt_ratio(hit: int, total: int, when_empty: str = "nothing to count") -> str:
    return f"{hit}/{total} = {hit / total:.2f}" if total else when_empty


def report(records: list[dict]) -> None:
    if not records:
        print("no runs recorded")
        return

    arms = defaultdict(list)
    for rec in records:
        arms[rec.get("skills", "on")].append(rec)

    repeats = Counter((r["case"], r["mode"], r.get("skills")) for r in records)
    single = [k for k, n in repeats.items() if n < 2]

    print(f"\n{BLD}what this report is{OFF}")
    print("  runs of a real agent CLI, graded against the fixtures, recorded one JSON line each")
    print("  activation counts file reads under skills/ and advanced/, never the agent's claim")
    print("  this harness is manual, gates nothing, and its numbers are not a release condition")
    if single:
        print(f"  {YEL}{len(single)} case/arm combination(s) ran once{OFF}; a single run is an "
              f"anecdote, not a measurement. Use --repeat 3 or more.")

    for arm in ("on", "off"):
        block = arms.get(arm) or []
        print(f"\n{BLD}skills {arm}{OFF}  {len(block)} run(s)")
        if not block:
            print("  no baseline recorded" if arm == "off" else "  no runs recorded")
            continue
        report_arm(block)

    print(f"\n{BLD}paired comparison{OFF}")
    on_runs, off_runs = arms.get("on") or [], arms.get("off") or []
    if not on_runs or not off_runs:
        print("  no baseline recorded")
        print("  a comparison needs both arms of one run, on the same model and the same")
        print("  reasoning setting: --skills paired. Without it nothing here says whether the")
        print("  skills changed the outcome, and no number below should be read as if it did.")
    else:
        for label, getter in (("routing pass rate", _routing_pass),
                              ("critical-defect recall", _critical_recall),
                              ("correct-fix rate", _fix_rate)):
            on_v, off_v = getter(on_runs), getter(off_runs)
            if on_v is None and off_v is None:
                print(f"  {label}: not measured in this run")
            elif on_v is None or off_v is None:
                print(f"  {label}: no baseline recorded")
            else:
                print(f"  {label}: skills on {on_v:.2f}, skills off {off_v:.2f}, "
                      f"difference {on_v - off_v:+.2f}")
        print("  a difference here is one sample of a nondeterministic system, not a result")

    print(f"\n{BLD}per-case variance{OFF}  repeats of one case that did not agree")
    grouped = defaultdict(list)
    for rec in records:
        grouped[(rec["case"], rec["mode"], rec.get("skills"))].append(
            rec.get("verdict", {}).get("status", "unknown"))
    unstable = 0
    for key, statuses in sorted(grouped.items()):
        counts = Counter(statuses)
        if len(counts) > 1:
            unstable += 1
            spread = ", ".join(f"{k} x{v}" for k, v in sorted(counts.items()))
            print(f"  {YEL}unstable{OFF} {key[0]} [{key[1]}, skills {key[2]}]: {spread}")
    total_groups = len(grouped)
    stable = total_groups - unstable
    print(f"  {stable}/{total_groups} case/arm combination(s) gave the same verdict every repeat")

    print(f"\n{BLD}by prompt class{OFF}")
    by_class = defaultdict(list)
    for rec in records:
        by_class[rec.get("prompt_class", "unlabelled")].append(rec)
    for name in PROMPT_CLASSES:
        block = by_class.get(name) or []
        if not block:
            continue
        passed = sum(1 for r in block if r.get("verdict", {}).get("status") == "pass")
        derived = sum(1 for r in block if r.get("prompt_class_source") == "derived")
        print(f"  {name:<14} {fmt_ratio(passed, len(block))}   ({derived} of {len(block)} "
              f"label(s) derived by the harness, not declared by the case)")


def _routing_pass(block: list[dict]) -> float | None:
    rows = [r for r in block if r.get("suite") == "routing"]
    if not rows:
        return None
    return sum(1 for r in rows if r["verdict"]["status"] == "pass") / len(rows)


def _critical_recall(block: list[dict]) -> float | None:
    values = [r["verdict"]["critical_recall"] for r in block
              if r.get("mode") == "review"
              and r.get("verdict", {}).get("critical_recall") is not None]
    return statistics.mean(values) if values else None


def _fix_rate(block: list[dict]) -> float | None:
    rows = [r for r in block if r.get("mode") == "repair"]
    if not rows:
        return None
    return sum(1 for r in rows if r["verdict"]["status"] == "pass") / len(rows)


def report_arm(block: list[dict]) -> None:
    routing = [r for r in block if r.get("suite") == "routing"]
    if routing:
        want = got = right = 0
        selected_total = 0
        fp_runs = 0
        ref_want = ref_hit = 0
        for rec in routing:
            verdict = rec["verdict"]
            expected = set(verdict["expected"])
            selected = set(rec["observed"]["selected_skills"])
            want += len(expected)
            right += len(expected & selected)
            selected_total += len(selected)
            got += len(selected & (expected | set(verdict["tolerated"])))
            if verdict["forbidden_loaded"]:
                fp_runs += 1
            ref_want += len(verdict["references_expected"])
            ref_hit += len(verdict["references_hit"])
        print(f"  activation recall     {fmt_ratio(right, want, 'no case expected a skill')}")
        print(f"  activation precision  "
              f"{fmt_ratio(got, selected_total, 'no skill was loaded, so precision is undefined')}")
        print(f"  false-positive rate   {fmt_ratio(fp_runs, len(routing))} "
              f"(a forbidden skill was loaded)")
        print(f"  reference recall      "
              f"{fmt_ratio(ref_hit, ref_want, 'no case named a reference')}")

    reviews = [r for r in block if r.get("mode") == "review"]
    if reviews:
        hits = sum(len(r["verdict"]["findings_hit"]) for r in reviews)
        total = hits + sum(len(r["verdict"]["findings_missed"]) for r in reviews)
        print(f"  critical-defect recall {fmt_ratio(hits, total, 'no case named a finding')}")
        print(f"  {len(reviews)} review run(s) carry hard negatives that no metric scores; "
              f"read them in the record")

    repairs = [r for r in block if r.get("mode") == "repair"]
    if repairs:
        fixed = sum(1 for r in repairs if r["verdict"]["oracle_after"] == "pass")
        regressed = sum(1 for r in repairs if r["verdict"]["regression"])
        weak = sum(1 for r in repairs if r["verdict"]["regression_check"] == "compile-only")
        print(f"  correct-fix rate      {fmt_ratio(fixed, len(repairs))}")
        print(f"  regression rate       {fmt_ratio(regressed, len(repairs))}")
        if weak:
            print(f"  {weak} repair run(s) had only byte-compilation as a regression check, "
                  f"which catches little. Ship oracle/test_regression.py to strengthen it.")

    latencies = [r["latency_s"] for r in block if isinstance(r.get("latency_s"), (int, float))]
    tokens = [r["tokens"]["total"] for r in block if isinstance(r.get("tokens"), dict)]
    if latencies:
        print(f"  latency seconds       median {pctile(latencies, 0.5):.1f}, "
              f"p90 {pctile(latencies, 0.9):.1f}")
    if tokens:
        print(f"  tokens per run        median {pctile(tokens, 0.5):.0f}, "
              f"p90 {pctile(tokens, 0.9):.0f}")
    else:
        print("  tokens per run        not reported by this CLI")

    divergent = [r for r in block
                 if sorted(r.get("self_report", {}).get("skills_named_in_output") or [])
                 != sorted(r["observed"]["selected_skills"])]
    print(f"  self-report divergence {fmt_ratio(len(divergent), len(block))} run(s) where the "
          f"agent's own account of which skills it used differs from the files it opened")


# --------------------------------------------------------------------------- entry points

def resolve_cli(cfg: Config) -> list[str]:
    argv = build_argv(cfg, "probe", Path("probe.txt"), ROOT, allow_edits=False)
    binary = argv[0]
    if shutil.which(binary) is None:
        print(f"agent CLI not found on PATH: {binary}", file=sys.stderr)
        print("This harness runs a real agent. It does not simulate one, so there is nothing "
              "to report without the CLI.", file=sys.stderr)
        print("Install it, or pass --runtime-cmd with a template for the CLI you have.",
              file=sys.stderr)
        raise SystemExit(2)
    return argv


def confirm(args, planned: int) -> None:
    """A manual harness that spends a real budget asks before it spends it.

    Without this, one mistyped command starts a paid run over every case in the fixture. --yes
    is how an operator says the spend was deliberate. A non-interactive shell that did not pass
    it is refused rather than guessed at, which is also what keeps this out of a CI job: a
    workflow cannot answer the question and cannot pass the flag by accident.
    """
    if getattr(args, "yes", False):
        return
    if not sys.stdin.isatty():
        print(f"refusing to start {planned} paid agent run(s) without --yes", file=sys.stderr)
        print("this harness invokes a real CLI against a real account. Pass --yes when the "
              "spend is deliberate, or --dry-run to see the plan.", file=sys.stderr)
        raise SystemExit(2)
    answer = input(f"start {planned} agent run(s) against the real CLI? [y/N] ").strip().lower()
    if answer not in ("y", "yes"):
        print("nothing was run")
        raise SystemExit(2)


def announce_classes(cases: list[dict], index: dict[str, set[str]]) -> None:
    """Print the class spread of the selection, and name the classes it has none of.

    A class with no case in it is a hole in the fixture. Saying so before the run stops the
    report reading as coverage the selection never had.
    """
    spread = Counter(classify(c, index)[0] for c in cases)
    print("prompt classes: " + ", ".join(f"{name} {spread[name]}" for name in PROMPT_CLASSES
                                         if spread[name]))
    absent = [name for name in PROMPT_CLASSES if not spread[name]]
    if absent:
        print(f"no case in this selection is labelled {', '.join(absent)}; that is a gap in the "
              f"fixture, and the report cannot speak for those classes")


def open_run_dir(args) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = Path(args.run_dir) if args.run_dir else (DEFAULT_RUN_ROOT / stamp)
    (run_dir / "traces").mkdir(parents=True, exist_ok=True)
    return run_dir


def write_manifest(run_dir: Path, cfg: Config, args, cases: int) -> None:
    manifest = {
        "schema": SCHEMA,
        "started_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "suite": args.suite,
        "mode": getattr(args, "mode", "routing"),
        "runtime": cfg.runtime_label,
        "runtime_cmd": sanitize(cfg.runtime_cmd) if cfg.runtime_cmd else None,
        "model": cfg.model,
        "reasoning": cfg.reasoning,
        "skills": args.skills,
        "repeat": args.repeat,
        "cases": cases,
        "manual_only": True,
        "gates_nothing": True,
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def cmd_routing(args) -> int:
    if not ROUTING_FIXTURE.is_file():
        print(f"missing fixture: {ROUTING_FIXTURE}", file=sys.stderr)
        return 2
    cases = load_routing_cases()
    if args.cases:
        wanted = set(args.cases.split(","))
        cases = [c for c in cases if str(c.get("id")) in wanted]
    index = distinctive_terms(known_skills())
    if args.prompt_class:
        cases = [c for c in cases if classify(c, index)[0] == args.prompt_class]
    if args.limit:
        cases = cases[: args.limit]
    installed = set(known_skills(args.advanced))
    blocked = sorted({str(c.get("id")) for c in cases
                      if set(c.get("expect_skills") or []) - installed})
    cases = [c for c in cases if str(c.get("id")) not in blocked]
    if not cases:
        print("no routing cases selected", file=sys.stderr)
        return 2

    cfg = Config(args)
    arms = {"on": ["on"], "off": ["off"], "paired": ["on", "off"]}[args.skills]
    planned = len(cases) * args.repeat * len(arms)
    print(f"{BLD}runtime routing eval{OFF}  {len(cases)} case(s) x {args.repeat} repeat(s) "
          f"x {len(arms)} arm(s) = {planned} run(s)")
    print(f"runtime {cfg.runtime_label}, model {cfg.model or 'CLI default'}, "
          f"reasoning {cfg.reasoning or 'not declared'}")
    if not cfg.reasoning:
        print("reasoning setting is not declared, so a paired comparison is like-for-like only "
              "if the CLI default did not move between arms")
    where = "skills/ and advanced/" if args.advanced else \
        "skills/ only, which is what an install gives a user"
    print(f"workspace carries {len(installed)} skill(s): {where}")
    if blocked:
        print(f"{len(blocked)} case(s) expect a skill this workspace does not install and are "
              f"not run: {', '.join(blocked)}. Add --advanced to include advanced/.")
    announce_classes(cases, index)
    resolve_cli(cfg)
    if args.dry_run:
        for case in cases:
            print(f"  would run {case.get('id')} [{classify(case, index)[0]}]")
        return 0
    confirm(args, planned)

    run_dir = open_run_dir(args)
    write_manifest(run_dir, cfg, args, len(cases))
    records: list[dict] = []
    with (run_dir / "runs.ndjson").open("a", encoding="utf-8") as handle:
        for case in cases:
            case_id = str(case.get("id"))
            label = classify(case, index)
            for arm in arms:
                for repeat_no in range(1, args.repeat + 1):
                    rec = do_run(cfg, run_dir, handle, "routing", "routing", case, case_id,
                                 str(case.get("task") or ""), label, arm, repeat_no, None)
                    records.append(rec)
                    ok = rec["verdict"]["status"] == "pass"
                    mark = (GRN + "pass" + OFF) if ok else (RED + "fail" + OFF)
                    print(f"  {mark} {case_id} [{label[0]}, skills {arm}, run {repeat_no}] "
                          f"selected {rec['observed']['selected_skills'] or 'none'}")
    report(records)
    print(f"\nrun directory: {run_dir}")
    return 0


def cmd_behavioral(args) -> int:
    if not BEHAVIORAL_DIR.is_dir():
        print(f"missing fixture directory: {BEHAVIORAL_DIR}", file=sys.stderr)
        return 2
    cases = load_behavioral_cases()
    if args.cases:
        wanted = set(args.cases.split(","))
        cases = [c for c in cases if str(c.get("id")) in wanted]
    declined = [str(c.get("id")) for c in cases if args.mode not in declared_modes(c)]
    cases = [c for c in cases if args.mode in declared_modes(c)]
    installed = set(known_skills(args.advanced))
    blocked = sorted({str(c.get("id")) for c in cases
                      if isinstance(c.get("skill"), str) and c["skill"] not in installed})
    cases = [c for c in cases if str(c.get("id")) not in blocked]
    if args.limit:
        cases = cases[: args.limit]
    if not cases:
        print(f"no behavioural cases declare mode {args.mode}", file=sys.stderr)
        return 2

    cfg = Config(args)
    arms = {"on": ["on"], "off": ["off"], "paired": ["on", "off"]}[args.skills]
    index = distinctive_terms(known_skills())
    print(f"{BLD}runtime behavioural eval{OFF}  mode {args.mode}, {len(cases)} case(s) "
          f"x {args.repeat} repeat(s) x {len(arms)} arm(s)")
    if declined:
        print(f"{len(declined)} case(s) do not declare mode {args.mode} and are not run: "
              f"{', '.join(declined)}")
    print(f"runtime {cfg.runtime_label}, model {cfg.model or 'CLI default'}, "
          f"reasoning {cfg.reasoning or 'not declared'}")
    if blocked:
        print(f"{len(blocked)} case(s) belong to a skill this workspace does not install and "
              f"are not run: {', '.join(blocked)}. Add --advanced to include advanced/.")
    announce_classes(cases, index)
    resolve_cli(cfg)

    usable: list[dict] = []
    for case in cases:
        ok, why = precheck_case(case["_dir"])
        if not ok:
            print(f"  {YEL}skip{OFF} {case.get('id')}: {why}")
            continue
        usable.append(case)
    if not usable:
        print("no usable behavioural cases", file=sys.stderr)
        return 2
    if args.dry_run:
        for case in usable:
            print(f"  would run {case.get('id')} [{classify(case, index)[0]}]")
        return 0
    confirm(args, len(usable) * args.repeat * len(arms))

    run_dir = open_run_dir(args)
    write_manifest(run_dir, cfg, args, len(usable))
    records: list[dict] = []
    with (run_dir / "runs.ndjson").open("a", encoding="utf-8") as handle:
        for case in usable:
            case_id = str(case.get("id"))
            label = classify(case, index)
            for arm in arms:
                for repeat_no in range(1, args.repeat + 1):
                    rec = do_run(cfg, run_dir, handle, "behavioral", args.mode, case, case_id,
                                 str(case.get("task") or ""), label, arm, repeat_no, case["_dir"])
                    records.append(rec)
                    ok = rec["verdict"]["status"] == "pass"
                    mark = (GRN + "pass" + OFF) if ok else (RED + "fail" + OFF)
                    print(f"  {mark} {case_id} [{args.mode}, skills {arm}, run {repeat_no}]")
    report(records)
    print(f"\nrun directory: {run_dir}")
    return 0


def cmd_report(args) -> int:
    path = Path(args.run_dir) / "runs.ndjson"
    if not path.is_file():
        print(f"no run records at {path}", file=sys.stderr)
        return 2
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            print(f"{YEL}skipping unparseable record line{OFF}", file=sys.stderr)
    report(records)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="eval_runtime.py",
        description="Manual runtime evaluation. Runs a real agent CLI, records what it read and "
                    "what it produced, and reports the result with its variance. Never runs in "
                    "CI and gates nothing.")
    subs = parser.add_subparsers(dest="suite", required=True)

    def shared(sub):
        sub.add_argument("--runtime-cmd", default=None,
                         help="command template for any agent CLI. Placeholders: {prompt}, "
                              "{prompt_file}, {cwd}, {model}, {reasoning}, {skills}. Overrides "
                              "the built-in claude invocation.")
        sub.add_argument("--model", default=None, help="model id, passed through and recorded")
        sub.add_argument("--reasoning", default=None,
                         help="reasoning setting, recorded so a paired run is like-for-like")
        sub.add_argument("--extra-arg", action="append", default=[],
                         help="extra argument for the built-in claude invocation, repeatable")
        sub.add_argument("--repeat", type=int, default=3,
                         help="repetitions per case per arm (default 3). One is not a measurement.")
        sub.add_argument("--skills", choices=("on", "off", "paired"), default="on")
        sub.add_argument("--cases", default=None, help="comma separated case ids")
        sub.add_argument("--limit", type=int, default=None)
        sub.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S)
        sub.add_argument("--run-dir", default=None,
                         help=f"where records and traces land (default {DEFAULT_RUN_ROOT}/<stamp>)")
        sub.add_argument("--dry-run", action="store_true",
                         help="print the plan and exit without invoking the agent")
        sub.add_argument("--advanced", action="store_true",
                         help="also install advanced/ into the workspace. Off by default, "
                              "because an install gives a user skills/ only.")
        sub.add_argument("--yes", action="store_true",
                         help="confirm the spend. Without it an interactive shell is asked and "
                              "a non-interactive one is refused.")

    routing = subs.add_parser("routing", help="replay evals/routing-cases.yaml against a runtime")
    shared(routing)
    routing.add_argument("--prompt-class", choices=PROMPT_CLASSES, default=None)
    routing.set_defaults(func=cmd_routing)

    behavioral = subs.add_parser("behavioral", help="run evals/behavioral/ cases")
    shared(behavioral)
    behavioral.add_argument("--mode", choices=("review", "repair"), default="review")
    behavioral.set_defaults(func=cmd_behavioral)

    rep = subs.add_parser("report", help="re-print the report for a recorded run directory")
    rep.add_argument("--run-dir", required=True)
    rep.set_defaults(func=cmd_report)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
