#!/usr/bin/env python3
"""Check the behavioral eval dataset under evals/behavioral/. No model runs here.

    python3 scripts/check_eval_dataset.py            check every case
    python3 scripts/check_eval_dataset.py --list     inventory only, no oracle runs

A paired eval compares a model's repair against an oracle. That comparison is worth exactly as much
as the fixtures are: a case whose oracle already passes measures nothing, because an agent scores it
by changing nothing, and a case whose oracle cannot pass after the reference fix is unwinnable. This
script proves neither is true, for every case, with nothing nondeterministic in the loop.

TRUST BOUNDARY, stated plainly. Fixtures checked into this repository are TRUSTED EXECUTABLE TEST
CODE. They are reviewed like any other file here, and running them is running code from this repo.
There is deliberately no --dataset option and no way to point this at a directory from anywhere
else, because that would turn a repository check into an arbitrary-code runner.

The import scan below is a LINT, not a sandbox. It reads the oracle's syntax tree and refuses a few
shapes that have no business in a hermetic fixture. It does not confine anything at run time and it
is not a security boundary; the review of the fixture is the boundary. What the run-time controls
below do provide is narrower and real: no inherited credential, no inherited proxy, no inherited
Python path, no bytecode written, and a hard timeout.
"""
from __future__ import annotations

import argparse
import ast
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("check_eval_dataset: PyYAML is required. "
             "pip install --require-hashes -r requirements.txt")

ROOT = Path(__file__).resolve().parent.parent
DATASET = ROOT / "evals" / "behavioral"

INSTALLED_SKILLS = (
    "fin-money-core", "fin-exchange-integration", "fin-payments",
    "fin-ledger", "fin-onchain", "fin-verification",
)

REQUIRED_KEYS = ("id", "skill", "references", "task", "allowed_paths",
                 "timeout_seconds", "defect", "oracle_proves")

ORACLE_ENTRYPOINT = "oracle/test_oracle.py"

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
    """A child environment built from nothing, not filtered from ours.

    An allowlist that starts empty cannot leak a variable nobody thought to deny, which is the way
    an API key, a proxy, or a user site-packages path reaches a child that should not have it.
    HOME points inside the temporary copy so nothing reads or writes a real dotfile.
    """
    return {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": str(workdir),
        "TMPDIR": str(workdir),
        "LC_ALL": "C",
        "LANG": "C",
        "PYTHONPATH": "repo",           # the case's own repo/, and nothing else
        "PYTHONDONTWRITEBYTECODE": "1",  # no .pyc into the fixture or the copy
        "PYTHONNOUSERSITE": "1",        # ignore ~/.local site-packages
        "PYTHONHASHSEED": "0",
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
                root = alias.name.split(".")[0]
                if root in BANNED_IMPORTS or alias.name in BANNED_IMPORTS:
                    err(f"{case_id}: oracle imports '{alias.name}'; a fixture is offline and "
                        f"standard library only")
        elif isinstance(node, ast.ImportFrom):
            mod = (node.module or "").split(".")[0]
            if mod in BANNED_IMPORTS:
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


def patch_paths(patch_text: str) -> set[str]:
    """The repo-relative paths a unified diff touches, read from its own headers."""
    touched: set[str] = set()
    for line in patch_text.splitlines():
        for prefix in ("--- a/", "+++ b/", "--- ", "+++ "):
            if line.startswith(prefix):
                candidate = line[len(prefix):].split("\t")[0].strip()
                if candidate in ("/dev/null", ""):
                    break
                for lead in ("a/", "b/"):
                    if candidate.startswith(lead):
                        candidate = candidate[len(lead):]
                touched.add(candidate)
                break
    return touched


def check_case(case: Path, run_oracles: bool) -> None:
    case_id = case.name
    print(f"\n{case_id}")

    manifest = case / "case.yaml"
    if not manifest.is_file():
        err(f"{case_id}: no case.yaml")
        return
    try:
        spec = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        err(f"{case_id}: case.yaml does not parse: {exc}")
        return
    if not isinstance(spec, dict):
        err(f"{case_id}: case.yaml is not a mapping")
        return

    missing = [k for k in REQUIRED_KEYS if k not in spec]
    if missing:
        err(f"{case_id}: case.yaml is missing {missing}")
        return
    extra = sorted(set(spec) - set(REQUIRED_KEYS))
    if extra:
        err(f"{case_id}: case.yaml carries unknown keys {extra}; the schema is closed so a "
            f"fixture cannot smuggle in a command or a mode")

    if spec["id"] != case_id:
        err(f"{case_id}: id is '{spec['id']}' and does not match the directory")
    if spec["skill"] not in INSTALLED_SKILLS:
        err(f"{case_id}: skill '{spec['skill']}' is not one of the six installed skills")
    if not isinstance(spec["timeout_seconds"], int) or not 1 <= spec["timeout_seconds"] <= 300:
        err(f"{case_id}: timeout_seconds must be an integer between 1 and 300")

    task = str(spec.get("task", ""))
    if len(task.split()) < 15:
        err(f"{case_id}: the task is too short to be a real request")

    # The references the skills-on arm will carry must exist in the checkout it loads from.
    refs = spec.get("references") or []
    if not isinstance(refs, list) or not refs:
        err(f"{case_id}: references must be a non-empty list naming the skills-on material")
    else:
        for ref in refs:
            if ref == "SKILL.md":
                err(f"{case_id}: SKILL.md is implied by 'skill' and must not be listed")
            elif not (ROOT / "skills" / spec["skill"] / "references" / ref).is_file():
                err(f"{case_id}: reference '{ref}' does not exist under skills/{spec['skill']}/")

    allowed = spec.get("allowed_paths") or []
    if not isinstance(allowed, list) or not allowed:
        err(f"{case_id}: allowed_paths must list the files a patch may touch")
    for declared in allowed:
        if not str(declared).startswith("repo/"):
            err(f"{case_id}: allowed path '{declared}' is outside repo/")
        elif not (case / declared).is_file():
            err(f"{case_id}: allowed path '{declared}' does not exist")

    if not (case / "repo").is_dir():
        err(f"{case_id}: no repo/")
        return
    oracle = case / ORACLE_ENTRYPOINT
    if not oracle.is_file():
        err(f"{case_id}: no {ORACLE_ENTRYPOINT}")
        return
    patch_file = case / "fix.patch"
    if not patch_file.is_file():
        err(f"{case_id}: no fix.patch")
        return

    scan_tree(case, case_id)
    scan_oracle(oracle, case_id)

    patch_text = patch_file.read_text(encoding="utf-8", errors="replace")
    if "GIT binary patch" in patch_text or "literal 0" in patch_text:
        err(f"{case_id}: fix.patch is a binary patch")
    if "120000" in patch_text:
        err(f"{case_id}: fix.patch changes a symlink mode")
    stray = sorted(p for p in patch_paths(patch_text) if p not in set(map(str, allowed)))
    if stray:
        err(f"{case_id}: fix.patch touches {stray}, which allowed_paths does not declare")

    if not run_oracles:
        print(f"  ok   structure, {len(refs)} reference(s), skill {spec['skill']}")
        return

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

        # 2. the reference fix must apply, touching only what it declared
        applied = subprocess.run(
            ["git", "apply", "--check", "--unsafe-paths", "fix.patch"],
            cwd=copy, env=minimal_env(work), capture_output=True, text=True,
        )
        if applied.returncode != 0:
            err(f"{case_id}: fix.patch does not apply: {applied.stderr.strip()[:200]}")
            return
        subprocess.run(["git", "apply", "--unsafe-paths", "fix.patch"],
                       cwd=copy, env=minimal_env(work), check=True, capture_output=True)
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


def main() -> int:
    ap = argparse.ArgumentParser(description="Check the behavioral eval dataset.")
    ap.add_argument("--list", action="store_true",
                    help="inventory and structure only; do not run any oracle")
    args = ap.parse_args()

    if not DATASET.is_dir():
        print(f"check_eval_dataset: no dataset at {DATASET.relative_to(ROOT)}")
        return 1

    cases = sorted(d for d in DATASET.iterdir() if d.is_dir())
    if not cases:
        err("evals/behavioral/ has no cases")
        return 1

    for case in cases:
        check_case(case, run_oracles=not args.list)

    print("\ncoverage")
    seen: dict[str, int] = {name: 0 for name in INSTALLED_SKILLS}
    for case in cases:
        try:
            spec = yaml.safe_load((case / "case.yaml").read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            continue
        if spec.get("skill") in seen:
            seen[spec["skill"]] += 1
    for name, count in seen.items():
        print(f"  {name:<26} {count}")
        if count < 2:
            err(f"{name} has {count} case(s); every installed skill needs at least two")

    print(f"\nsuite\n  {len(cases)} case(s), oracle entrypoint fixed at {ORACLE_ENTRYPOINT}")
    if errors:
        print(f"\nFAILED: {len(errors)} error(s)")
        return 1
    print("\nPASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
