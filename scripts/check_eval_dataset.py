#!/usr/bin/env python3
"""Check the behavioral eval dataset under evals/behavioral/. No model runs here.

    python3 scripts/check_eval_dataset.py            check every case
    python3 scripts/check_eval_dataset.py --list     inventory only, no oracle runs

A paired eval compares a model's repair against an oracle. That comparison is worth exactly as much
as the fixtures are: a case whose oracle already passes measures nothing, because an agent scores it
by changing nothing, and a case whose oracle cannot pass after the reference fix is unwinnable. This
script proves neither is true, for every case, with nothing nondeterministic in the loop.

TRUST BOUNDARY, and the difference from the runner. Fixtures checked into this repository are
TRUSTED EXECUTABLE TEST CODE: they are reviewed like any other file here, and running them is
running code from this repo. So this script runs them on the host, in a temporary copy, with a child
environment built without inheritance and a hard timeout. That is deliberately weaker than the runner's
container, and it is allowed to be, because nothing model-generated is ever executed here.
scripts/run_patch_eval.py executes model-written code, so it refuses to run without Docker.

There is deliberately no --dataset option and no way to point this at a directory from anywhere
else, because that would turn a repository check into an arbitrary-code runner.

The import scan below is a LINT, not a sandbox. It reads the oracle's syntax tree and refuses a few
shapes that have no business in a hermetic fixture. It does not confine anything at run time; the
review of the fixture is the boundary.

The schema, the layered-context rule and the path confinement all come from run_patch_eval, so this
check and the runner cannot drift apart on what a valid case is.
"""
from __future__ import annotations

import argparse
import ast
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    import yaml
except ImportError:
    sys.exit("check_eval_dataset: PyYAML is required. "
             "pip install --require-hashes -r requirements.txt")

from run_patch_eval import (  # noqa: E402
    DATASET, INSTALLED_SKILLS, ROOT, ContextError, _confine, confined_reference,
    dataset_cases, load_spec, read_case_tree, validate_patch,
)

ORACLE_ENTRYPOINT = "oracle/test_oracle.py"
MIN_CASES_PER_SKILL = 2

# Modules a hermetic fixture has no reason to import. The point is not that these are dangerous in
# themselves; it is that a fixture reaching for any of them is no longer offline and deterministic,
# which is the property the whole dataset rests on.
BANNED_IMPORTS = frozenset({
    "socket", "ssl", "requests", "urllib", "urllib2", "urllib3", "httpx", "http",
    "http.client", "ftplib", "smtplib", "telnetlib", "poplib", "imaplib", "asyncio",
    "subprocess", "importlib", "ctypes", "multiprocessing", "shutil", "pty", "pickle",
})
BANNED_CALLS = frozenset({"eval", "exec", "compile", "__import__", "system", "popen",
                          "spawn", "fork", "execv", "execve"})

# Literal shapes that look like real credentials. A fixture must model money, never a secret.
CREDENTIAL_SHAPES = (
    ("live secret key", re.compile(r"\bsk_live_[A-Za-z0-9]{8,}")),
    ("aws access key", re.compile(r"\bAKIA[0-9A-Z]{12,}")),
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("anthropic key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{16,}")),
    ("openai key", re.compile(r"\bsk-[A-Za-z0-9]{32,}")),
    ("bearer token", re.compile(r"(?i)\bauthorization\s*[:=]\s*['\"]bearer\s")),
)

# Files that must never ship inside a case: caches, bytecode, editor state, VCS, generated output.
ARTEFACT_NAMES = frozenset({"__pycache__", ".git", ".DS_Store", ".idea", ".vscode",
                            ".pytest_cache", ".mypy_cache", ".ruff_cache", "node_modules"})
ARTEFACT_SUFFIXES = (".pyc", ".pyo", ".orig", ".rej", ".swp", ".swo", ".bak")

errors: list[str] = []


def err(msg: str) -> None:
    errors.append(msg)
    print(f"  FAIL {msg}")


def minimal_env(workdir: Path) -> dict[str, str]:
    """A child environment built without inheritance, not filtered from ours.

    Starting from an empty dict and naming what goes in cannot leak a variable nobody thought to
    deny, which is how an API key, a proxy, or a user site-packages path reaches a child that should
    not have it. HOME points inside the temporary copy so nothing reads or writes a real dotfile.
    """
    return {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": str(workdir),
        "TMPDIR": str(workdir),
        "LC_ALL": "C",
        "LANG": "C",
        "PYTHONPATH": "repo",            # the case's own repo/, and nothing else
        "PYTHONDONTWRITEBYTECODE": "1",  # no .pyc into the fixture or the copy
        "PYTHONNOUSERSITE": "1",         # ignore ~/.local site-packages
        "PYTHONHASHSEED": "0",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
    }


def run_oracle(workdir: Path, timeout: int) -> tuple[int, str]:
    """Run the one permitted entrypoint. A fixture never supplies a command."""
    try:
        done = subprocess.run(
            [sys.executable, "-S", ORACLE_ENTRYPOINT],
            cwd=workdir, env=minimal_env(workdir), timeout=timeout,
            capture_output=True, text=True,
        )
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout}s"
    return done.returncode, (done.stderr or done.stdout)[-800:]


def scan_oracle(path: Path, case_id: str) -> None:
    """Read the oracle's syntax tree and refuse shapes a hermetic fixture does not need."""
    source = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        err(f"{case_id}: oracle does not parse: {exc}")
        return

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in BANNED_IMPORTS or alias.name in BANNED_IMPORTS:
                    err(f"{case_id}: oracle imports '{alias.name}'; a fixture is offline and "
                        f"standard library only")
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] in BANNED_IMPORTS:
                err(f"{case_id}: oracle imports from '{node.module}'; a fixture is offline and "
                    f"standard library only")
        elif isinstance(node, ast.Call):
            name = None
            if isinstance(node.func, ast.Name):
                name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                name = node.func.attr
            if name in BANNED_CALLS:
                err(f"{case_id}: oracle calls '{name}'; dynamic execution is not permitted in a "
                    f"fixture")

    for label, pattern in CREDENTIAL_SHAPES:
        if pattern.search(source):
            err(f"{case_id}: oracle carries something shaped like a {label}; a fixture models "
                f"money, never a secret")


def scan_tree(case: Path, case_id: str) -> None:
    """No symlink, and no cache, bytecode, editor, VCS or generated artefact inside a case."""
    for path in sorted(case.rglob("*")):
        rel = path.relative_to(case)
        if path.is_symlink():
            err(f"{case_id}: {rel} is a symlink; a fixture is a plain tree")
            continue
        if path.name in ARTEFACT_NAMES or any(part in ARTEFACT_NAMES for part in rel.parts):
            err(f"{case_id}: {rel} is a cache or artefact and must not ship in a case")
        elif path.suffix in ARTEFACT_SUFFIXES:
            err(f"{case_id}: {rel} is a generated or editor file and must not ship in a case")


def check_patch_hygiene(patch_text: str, case_id: str) -> None:
    """A reference patch is a repository file too, and it has to read like one.

    A line carrying trailing whitespace is what `git diff --check` complains about, and in a diff it
    is almost always a blank context line written as a single space. Normalising those to genuinely
    empty lines keeps `git diff --check` silent without changing what the patch does.
    """
    lines = patch_text.split("\n")
    dirty = [i + 1 for i, line in enumerate(lines) if line != line.rstrip() and not line.strip()]
    if dirty:
        err(f"{case_id}: fix.patch has trailing whitespace on line(s) {dirty[:8]}; blank context "
            f"lines must be genuinely empty so `git diff --check` stays silent")
    if patch_text.endswith("\n\n"):
        err(f"{case_id}: fix.patch ends with a blank line")
    if patch_text and not patch_text.endswith("\n"):
        err(f"{case_id}: fix.patch does not end with a newline")


def check_case(case: Path, run_oracles: bool) -> str | None:
    """Returns the case's target_skill when the case is structurally sound, else None."""
    case_id = case.name
    print(f"\n{case_id}")

    if not (case / "case.yaml").is_file():
        err(f"{case_id}: no case.yaml")
        return None
    try:
        spec = load_spec(case)
    except (yaml.YAMLError, ContextError) as exc:
        err(f"{case_id}: {exc}")
        return None

    # timeout_seconds is range-checked inside load_spec, which raised above if it were wrong.
    if len(str(spec["task"]).split()) < 15:
        err(f"{case_id}: the task is too short to be a real request")

    # Every reference the treatment arm will carry must exist, and must resolve inside that skill's
    # own references/ directory. An absolute path, a '..' or a symlink cannot enter a prompt.
    for skill, refs in spec["references"].items():
        for ref in refs:
            if ref == "SKILL.md":
                err(f"{case_id}: SKILL.md is implied by the context lists and must not be listed")
                continue
            try:
                confined_reference(skill, ref)
            except ContextError as exc:
                err(f"{case_id}: {exc}")

    allowed = spec["allowed_paths"]
    if not isinstance(allowed, list) or not allowed:
        err(f"{case_id}: allowed_paths must list the files a patch may touch")
        allowed = []
    for declared in allowed:
        if not str(declared).startswith("repo/"):
            err(f"{case_id}: allowed path '{declared}' is outside repo/")
            continue
        try:
            _confine(case, str(declared), f"{case_id} allowed path", root=DATASET)
        except ContextError as exc:
            err(f"{case_id}: {exc}")
        name = Path(str(declared)).name
        if name.startswith("test_") or name.endswith(("_test.py", "_tests.py")) \
                or {"test", "tests"} & set(Path(str(declared)).parts):
            err(f"{case_id}: allowed path '{declared}' is a test file; a candidate patch must not "
                f"be able to edit anything that grades it")

    if not (case / "repo").is_dir():
        err(f"{case_id}: no repo/")
        return None
    if not (case / ORACLE_ENTRYPOINT).is_file():
        err(f"{case_id}: no {ORACLE_ENTRYPOINT}")
        return None
    if not (case / "fix.patch").is_file():
        err(f"{case_id}: no fix.patch")
        return None

    scan_tree(case, case_id)
    scan_oracle(case / ORACLE_ENTRYPOINT, case_id)
    try:
        known = {rel for rel, _ in read_case_tree(case)}
    except ContextError as exc:
        err(f"{case_id}: {exc}")
        return None

    patch_text = (case / "fix.patch").read_text(encoding="utf-8", errors="replace")
    check_patch_hygiene(patch_text, case_id)
    # The reference patch is held to exactly the rule a candidate patch is held to.
    _, reason = validate_patch(patch_text, tuple(str(p) for p in allowed), known)
    if reason is not None:
        err(f"{case_id}: fix.patch would be refused by the grader: {reason}")

    if not run_oracles:
        print(f"  ok   structure, target {spec['target_skill']}, "
              f"baseline {list(spec['baseline_context'] or []) or 'none'}")
        return spec["target_skill"]

    timeout = int(spec["timeout_seconds"])
    work = Path(tempfile.mkdtemp(prefix=f"evalcheck-{case_id}-"))
    try:
        copy = work / "case"
        shutil.copytree(case, copy, symlinks=False)

        # 1. the oracle must FAIL against the defect as shipped
        code, tail = run_oracle(copy, timeout)
        if code == 0:
            err(f"{case_id}: DATASET BUG, the oracle PASSES against repo/ as shipped. The defect "
                f"is not planted, or the oracle does not assert on it. A model scores this case "
                f"by changing nothing.")
        elif code == 124:
            err(f"{case_id}: the oracle {tail} against the defect")
        else:
            print(f"  ok   oracle fails on the planted defect (exit {code})")

        # 2. the reference fix must apply. No --unsafe-paths: git itself refuses a path outside the
        #    working area, which is one more thing that has to go wrong before a patch escapes.
        applied = subprocess.run(["git", "apply", "--check", "fix.patch"],
                                 cwd=copy, env=minimal_env(work), capture_output=True, text=True)
        if applied.returncode != 0:
            err(f"{case_id}: fix.patch does not apply: {applied.stderr.strip()[:200]}")
            return spec["target_skill"]
        subprocess.run(["git", "apply", "fix.patch"], cwd=copy, env=minimal_env(work),
                       check=True, capture_output=True)
        print("  ok   fix.patch applies")

        # 3. and the oracle must PASS once it has
        code, tail = run_oracle(copy, timeout)
        if code != 0:
            err(f"{case_id}: DATASET BUG, the oracle still fails after the reference fix "
                f"(exit {code}). The case cannot be won: {tail.strip()[:200]}")
        else:
            print("  ok   oracle passes after the reference fix")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return spec["target_skill"]


def main() -> int:
    ap = argparse.ArgumentParser(description="Check the behavioral eval dataset.")
    ap.add_argument("--list", action="store_true",
                    help="inventory and structure only; do not run any oracle")
    args = ap.parse_args()

    if not DATASET.is_dir():
        print(f"check_eval_dataset: no dataset at {DATASET.relative_to(ROOT)}")
        return 1
    cases = dataset_cases()
    if not cases:
        err("evals/behavioral/ has no cases")
        return 1
    # Dot-entries are editor or tooling state, never fixtures. Naming them keeps the skip visible
    # rather than silent, so a directory that should have been a case cannot vanish from the suite.
    strays = sorted(p.name for p in DATASET.iterdir() if p.name.startswith("."))
    if strays:
        print(f"skipping non-case entries in evals/behavioral/: {strays}")

    seen: dict[str, int] = {name: 0 for name in INSTALLED_SKILLS}
    for case in cases:
        target = check_case(case, run_oracles=not args.list)
        if target in seen:
            seen[target] += 1

    print("\ncoverage, counted by the skill the treatment arm introduces")
    for name, count in seen.items():
        print(f"  {name:<26} {count}")
        if count < MIN_CASES_PER_SKILL:
            err(f"{name} is the target of {count} case(s); every installed skill needs at least "
                f"{MIN_CASES_PER_SKILL}")

    print(f"\nsuite\n  {len(cases)} case(s), oracle entrypoint fixed at {ORACLE_ENTRYPOINT}")
    if errors:
        print(f"\nFAILED: {len(errors)} error(s)")
        return 1
    print("\nPASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
