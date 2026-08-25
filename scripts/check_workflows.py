#!/usr/bin/env python3
"""Check the CI configuration against the properties SECURITY.md claims for it.

SECURITY.md states five things about this repository's automation. A statement about a
control that nothing verifies is the failure mode this whole repo argues against, so each
one is a check here, and the policy cites this file by name:

  1. Every action is pinned to a full 40-character commit SHA, GitHub-owned actions
     included, and carries the release tag it was resolved from in a trailing comment.
     A tag is a moving pointer: whoever can move it can run their code in the job.
  2. Every workflow declares permissions at workflow level, and that declaration is
     read-only. Write is granted on the single job that needs it, never fleet-wide.
  3. Every job declares timeout-minutes. Without it a hung job holds a runner for six
     hours, which is both a bill and a window.
  4. Every actions/checkout step sets persist-credentials: false, so the job token is not
     left in .git/config for later steps to find and use.
  5. Every pip install in a workflow passes --require-hashes, so an artifact this
     repository has not pinned by digest cannot enter a job.

Plus the Dependabot posture the policy describes: monthly, grouped, one routine pull
request at a time, and no routine version updates for pip.

Usage:  python3 scripts/check_workflows.py
Exit 0 when every check passes, 1 on the first failure found. Run it locally before
pushing a workflow change; CI runs it as the workflow-hygiene job.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("PyYAML is required: pip install --require-hashes -r requirements.txt",
          file=sys.stderr)
    sys.exit(2)

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
DEPENDABOT = ROOT / ".github" / "dependabot.yml"

# owner/repo[/subpath]@<40 hex>  followed by  # <tag>
PINNED = re.compile(
    r"^[A-Za-z0-9][\w.-]*/[\w.-]+(?:/[\w./-]+)?@[0-9a-f]{40}\s+#\s*\S+")
USES_LINE = re.compile(r"^\s*(?:-\s*)?uses:\s*(.+?)\s*$")
PIP_INSTALL = re.compile(r"\bpip\d*\s+install\b|\bpip3?\s+install\b")

problems: list[str] = []


def fail(where: str, msg: str) -> None:
    problems.append(f"{where}: {msg}")


def check_pins(path: Path) -> None:
    """Read pins from the raw text: the trailing tag comment does not survive a YAML parse."""
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        m = USES_LINE.match(line)
        if not m:
            continue
        ref = m.group(1)
        if ref.startswith("./"):     # a local composite action, versioned with this repo
            continue
        if not PINNED.match(ref):
            fail(f"{path.relative_to(ROOT)}:{n}",
                 f"uses: {ref}\n    pin it to a full 40-character commit SHA with the "
                 f"release tag in a trailing comment, for example\n"
                 f"    uses: owner/repo@<sha>  # v1.2.3")


def check_permissions(path: Path, wf: dict) -> None:
    rel = path.relative_to(ROOT)
    perms = wf.get("permissions")
    if perms is None:
        fail(str(rel), "declares no workflow-level permissions; state them explicitly, "
                       "read-only, so a new job cannot inherit write by accident")
        return
    if not isinstance(perms, dict):
        fail(str(rel), f"workflow-level permissions is {perms!r}; write it as an explicit "
                       f"mapping of scopes so each one is visible in review")
        return
    for scope, level in perms.items():
        if level not in ("read", "none"):
            fail(str(rel), f"workflow-level permissions grants {scope}: {level}. "
                           f"Workflow level is read-only here; grant write on the one job "
                           f"that needs it")


def check_jobs(path: Path, wf: dict) -> None:
    rel = path.relative_to(ROOT)
    for job_id, job in (wf.get("jobs") or {}).items():
        if not isinstance(job, dict):
            continue
        where = f"{rel} job {job_id}"
        if "timeout-minutes" not in job:
            fail(where, "has no timeout-minutes")
        perms = job.get("permissions")
        if perms is not None and not isinstance(perms, dict):
            fail(where, f"permissions is {perms!r}; list the scopes explicitly")
        for step in job.get("steps") or []:
            if not isinstance(step, dict):
                continue
            uses = str(step.get("uses") or "")
            if uses.startswith("actions/checkout@"):
                with_ = step.get("with") or {}
                if with_.get("persist-credentials") is not False:
                    fail(where, "checks out without persist-credentials: false, which "
                                "leaves the job token in .git/config")
            run = str(step.get("run") or "")
            if PIP_INSTALL.search(run) and "--require-hashes" not in run:
                fail(where, "runs pip install without --require-hashes; install from "
                            "requirements.txt so every artifact is pinned by digest")


def check_dependabot() -> None:
    if not DEPENDABOT.is_file():
        fail(".github/dependabot.yml", "is missing")
        return
    cfg = yaml.safe_load(DEPENDABOT.read_text(encoding="utf-8")) or {}
    updates = cfg.get("updates") or []
    seen = {}
    for entry in updates:
        eco = entry.get("package-ecosystem")
        seen[eco] = entry
        limit = entry.get("open-pull-requests-limit")
        if limit is None or limit > 1:
            fail(f"dependabot.yml [{eco}]",
                 f"open-pull-requests-limit is {limit}; keep it at 1 or 0 so routine "
                 f"updates cannot arrive faster than they are reviewed")
        if not entry.get("groups"):
            fail(f"dependabot.yml [{eco}]", "opens ungrouped pull requests; group them")
        if entry.get("ignore"):
            fail(f"dependabot.yml [{eco}]",
                 "carries an ignore rule; a permanent ignore hides a real advisory. If it "
                 "is temporary, the reason and expiry belong in a comment beside it and "
                 "this check must be revisited when it is removed")
    if "github-actions" not in seen:
        fail("dependabot.yml", "does not watch github-actions, so a pinned SHA would "
                               "never be offered a security update")
    elif (seen["github-actions"].get("schedule") or {}).get("interval") != "monthly":
        fail("dependabot.yml [github-actions]", "is not on a monthly schedule")
    # pip is configured only because a hash-pinned manifest exists to update.
    if (ROOT / "requirements.txt").is_file() and "pip" not in seen:
        fail("dependabot.yml", "requirements.txt exists but pip is not configured, so a "
                               "vulnerable pinned dependency would go unreported")
    if "pip" in seen and seen["pip"].get("open-pull-requests-limit") != 0:
        fail("dependabot.yml [pip]",
             "routine version updates are enabled; this repository takes pip security "
             "updates only, which open-pull-requests-limit: 0 expresses")


def main() -> int:
    if not WORKFLOWS.is_dir():
        print("no .github/workflows directory", file=sys.stderr)
        return 1
    files = sorted(WORKFLOWS.glob("*.yml")) + sorted(WORKFLOWS.glob("*.yaml"))
    if not files:
        print("no workflows found", file=sys.stderr)
        return 1
    for path in files:
        wf = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        check_pins(path)
        check_permissions(path, wf)
        check_jobs(path, wf)
    check_dependabot()

    for line in problems:
        print(f"  {line}")
    if problems:
        print(f"\nFAILED: {len(problems)} problem(s) in {len(files)} workflow(s)")
        return 1
    print(f"workflow hygiene: {len(files)} workflow(s) checked, pins, permissions, "
          f"timeouts, checkout credentials, hashed installs and Dependabot all as stated "
          f"in SECURITY.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
