#!/usr/bin/env python3
"""Check the behavioral eval dataset under evals/behavioral/.

    python3 scripts/eval_dataset_check.py                  full check; every oracle runs twice
    python3 scripts/eval_dataset_check.py --list           inventory table, no oracle runs
    python3 scripts/eval_dataset_check.py --dataset DIR    check a dataset that lives elsewhere
    python3 scripts/eval_dataset_check.py --timeout N      time box for one oracle run

No model, no network, no API key. The behavioral suite grades an agent, and a grade is only
worth reading if the fixtures are sound, so soundness is decided here by running code rather
than by reading a case and believing it.

Two checks carry the dataset. The oracle must FAIL against repo/ as shipped, and it must PASS
once fix.patch is applied. A fixture whose oracle already passes measures nothing: an agent
that changes not one line scores the point. A fixture that still fails after the reference fix
is unwinnable: the agent scores zero whatever it writes. Both are dataset bugs, both are hard
errors here, and the message says which of the two happened.

Everything runs in a copy made with tempfile.mkdtemp. The checked tree is never written to,
so this is safe to run against a working tree that other people are editing.
"""
from __future__ import annotations

import argparse
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("eval_dataset_check: PyYAML is required. pip install --require-hashes -r requirements.txt")

ROOT = Path(__file__).resolve().parent.parent
DATASET = ROOT / "evals" / "behavioral"

SKILLS = (
    "fin-money-core",
    "fin-exchange-integration",
    "fin-payments",
    "fin-ledger",
    "fin-onchain",
    "fin-verification",
)
MODES = ("review", "repair")
MIN_CASES_PER_SKILL = 2   # one case per skill is an anecdote; two is the floor for a signal

TOP_KEYS = ("id", "skill", "mode", "task", "defect", "expect_findings", "hard_negatives", "oracle")
DEFECT_KEYS = ("summary", "mechanism", "file")
FINDING_KEYS = ("id", "must_mention", "why")
ORACLE_KEYS = ("command", "fails_on_defect", "passes_on_fix")

SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
DEFAULT_TIMEOUT = 60      # a hung fixture must fail CI, never hold it

# repo/ is a planted defect, not a product. Small and readable is the point, so the band is a
# warning: a case one line over is still a usable case.
REPO_MIN_LINES, REPO_MAX_LINES = 40, 200

# A task that names the skill, or asks for the bug, tells the agent the answer. The whole
# measurement is whether routing and review find the defect from ordinary work.
TASK_TELLS = (
    "find the bug", "find the defect", "find the vulnerability", "spot the bug",
    "there is a bug", "there's a bug", "the bug is", "planted", "intentional bug",
    "what's wrong with this", "whats wrong with this",
)

BANNED_ROOTS = ("socket", "requests", "urllib", "httpx", "ftplib", "telnetlib", "smtplib")
IMPORT_STMT = re.compile(r"(?m)^[ \t]*(import|from)[ \t]+([A-Za-z_][\w.]*)(?:[ \t]+import[ \t]+(.+))?$")
DYNAMIC_IMPORT = re.compile(
    r"(?:__import__|import_module)\(\s*['\"]([A-Za-z_][\w.]*)['\"]"
)

# Prefixes, not whole secrets. A fixture carrying a credential-shaped literal teaches the
# reviewer under test that a literal like this is normal, and it survives into training data
# and into screenshots.
CREDENTIAL_PATTERNS = (
    (re.compile(r"sk_live|rk_live"), "a live processor key prefix"),
    (re.compile(r"(?i)\b(api[_-]?key|api[_-]?secret|access[_-]?token|client[_-]?secret)['\"]?\s*[=:]\s*['\"]"), "a credential assigned as a literal"),
    (re.compile(r"\bAKIA"), "an AWS access key id prefix"),
    (re.compile(r"-----BEGIN"), "a PEM block header"),
)

# An absolute filesystem path makes the case pass on the machine that wrote it and nowhere
# else. An API route is also a literal starting with a slash, so only paths that look like
# filesystem paths count: a known root, or a filename suffix.
FS_ROOTS = ("/Users/", "/home/", "/root/", "/tmp/", "/private/", "/var/", "/etc/", "/opt/",
            "/mnt/", "/media/", "/srv/", "/usr/", "/proc/", "/dev/", "/Volumes/", "/System/",
            "/Library/", "/Applications/", "/bin/", "/sbin/")
STRING_LITERAL = re.compile(r"""['"]([^'"\n]*)['"]""")
PATHY_SUFFIX = re.compile(r"\.(py|json|ya?ml|csv|txt|log|db|sqlite3?|ini|toml|env|pem|cfg)$")
HOME_PATH = re.compile(r"""expanduser\s*\(|Path\.home\s*\(|['"]~/""")

# Only files an oracle or a fixture would actually read. A stray binary is not scanned.
TEXT_SUFFIXES = {".py", ".json", ".yaml", ".yml", ".txt", ".csv", ".md", ".ini", ".toml", ".cfg"}

RED, YEL, GRN, BLD, OFF = "\033[31m", "\033[33m", "\033[32m", "\033[1m", "\033[0m"
if not sys.stdout.isatty():
    RED = YEL = GRN = BLD = OFF = ""

errors: list[str] = []
warnings: list[str] = []

# The case name prints only once something is said about that case, so --list stays an
# inventory rather than a wall of empty headings.
_header: str | None = None


def heading(text: str) -> None:
    global _header
    _header = text


def _flush() -> None:
    global _header
    if _header is not None:
        print(f"\n{BLD}{_header}{OFF}")
        _header = None


def err(msg: str) -> None:
    _flush()
    errors.append(msg)
    print(f"  {RED}FAIL{OFF} {msg}")


def warn(msg: str) -> None:
    _flush()
    warnings.append(msg)
    print(f"  {YEL}WARN{OFF} {msg}")


def ok(msg: str) -> None:
    _flush()
    print(f"  {GRN}ok{OFF}   {msg}")


def note(text: str) -> None:
    """Output that belongs under the current case, such as the tail of a failing run."""
    _flush()
    print(text)


def text_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*")
                  if p.is_file() and p.suffix in TEXT_SUFFIXES and "__pycache__" not in p.parts)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


# ── case.yaml ─────────────────────────────────────────────────────────────────────────────

def _string(where: str, data: dict, key: str) -> str | None:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        err(f"{where}: '{key}' must be a non-empty string")
        return None
    return value


def check_case_yaml(case_dir: Path) -> dict | None:
    """Parse and type-check one case.yaml. Returns the mapping, or None if it is unusable."""
    name = case_dir.name
    path = case_dir / "case.yaml"
    if not path.is_file():
        err(f"{name}: case.yaml is missing")
        return None
    try:
        data = yaml.safe_load(read(path))
    except yaml.YAMLError as exc:
        err(f"{name}: case.yaml is not valid YAML ({str(exc).splitlines()[0]})")
        return None
    if not isinstance(data, dict):
        err(f"{name}: case.yaml must be a mapping")
        return None

    for key in TOP_KEYS:
        if key not in data:
            err(f"{name}: case.yaml is missing required key '{key}'")
    # The contract says exactly these keys. An unknown key is a typo in a required one often
    # enough that guessing which is not worth it; both readings are a dataset bug.
    for key in sorted(set(data) - set(TOP_KEYS)):
        err(f"{name}: case.yaml has unknown key '{key}'")

    case_id = _string(name, data, "id")
    if case_id is not None and case_id != name:
        err(f"{name}: id '{case_id}' does not equal the directory name")

    skill = _string(name, data, "skill")
    if skill is not None and skill not in SKILLS:
        err(f"{name}: skill '{skill}' is not one of the six installed skills")

    mode = data.get("mode")
    if isinstance(mode, str):        # a single mode is unambiguous; normalise and carry on
        mode = [mode]
    if not isinstance(mode, list) or not mode:
        err(f"{name}: 'mode' must be a non-empty list drawn from {list(MODES)}")
    else:
        for entry in mode:
            if entry not in MODES:
                err(f"{name}: mode '{entry}' is not one of {list(MODES)}")
        if len(set(mode)) != len(mode):
            err(f"{name}: 'mode' repeats a value")
        data["mode"] = mode

    task = _string(name, data, "task")
    if task is not None:
        low = task.lower()
        for tell in TASK_TELLS:
            if tell in low:
                err(f"{name}: task says '{tell}'; the prompt must read as ordinary work")
        for named in SKILLS:
            if named in low:
                err(f"{name}: task names the skill '{named}'; that is the routing decision under test")

    defect = data.get("defect")
    if not isinstance(defect, dict):
        err(f"{name}: 'defect' must be a mapping with {list(DEFECT_KEYS)}")
    else:
        for key in DEFECT_KEYS:
            _string(f"{name}: defect", defect, key)
        target = defect.get("file")
        if isinstance(target, str) and target.strip():
            if not target.startswith("repo/"):
                err(f"{name}: defect.file '{target}' must be a path under repo/")
            elif not (case_dir / target).is_file():
                err(f"{name}: defect.file '{target}' does not exist")

    findings = data.get("expect_findings")
    if not isinstance(findings, list) or not findings:
        err(f"{name}: 'expect_findings' must be a non-empty list")
    else:
        seen: set[str] = set()
        for finding in findings:
            if not isinstance(finding, dict):
                err(f"{name}: each expect_findings entry must be a mapping with {list(FINDING_KEYS)}")
                continue
            for key in FINDING_KEYS:
                if key not in finding:
                    err(f"{name}: expect_findings entry is missing '{key}'")
            fid = finding.get("id")
            if isinstance(fid, str) and SLUG.match(fid):
                if fid in seen:
                    err(f"{name}: expect_findings id '{fid}' appears twice")
                seen.add(fid)
            else:
                err(f"{name}: expect_findings id '{fid}' must be a kebab-case slug")
            _string(f"{name}: expect_findings[{fid}]", finding, "why")
            literals = finding.get("must_mention")
            if not isinstance(literals, list) or not literals:
                err(f"{name}: expect_findings[{fid}].must_mention must be a non-empty list of literals")
                continue
            for literal in literals:
                if not isinstance(literal, str) or not literal.strip():
                    err(f"{name}: expect_findings[{fid}].must_mention holds an empty literal")
                elif len(literal.strip()) < 3:
                    warn(f"{name}: must_mention literal '{literal}' is too short to grade on")
            if len({str(x).lower() for x in literals}) != len(literals):
                warn(f"{name}: expect_findings[{fid}].must_mention repeats a literal")

    negatives = data.get("hard_negatives")
    if not isinstance(negatives, list) or not negatives:
        err(f"{name}: 'hard_negatives' must be a non-empty list; a case with no plausible "
            f"wrong answer cannot separate a reviewer from a guesser")
    else:
        for negative in negatives:
            if not isinstance(negative, str) or not negative.strip():
                err(f"{name}: each hard_negatives entry must be a non-empty string")

    oracle = data.get("oracle")
    if not isinstance(oracle, dict):
        err(f"{name}: 'oracle' must be a mapping with {list(ORACLE_KEYS)}")
    else:
        command = _string(f"{name}: oracle", oracle, "command")
        if command:
            script = oracle_script(command)
            if script is None:
                err(f"{name}: oracle.command '{command}' names no script to run")
            elif not (case_dir / script).is_file():
                err(f"{name}: oracle.command points at '{script}', which does not exist")
        for key in ("fails_on_defect", "passes_on_fix"):
            if oracle.get(key) is not True:
                err(f"{name}: oracle.{key} must be the boolean true; this run decides whether it is")

    return data


def oracle_script(command: str) -> str | None:
    """The .py the command runs, so a missing oracle is caught before anything is copied."""
    try:
        argv = shlex.split(command)
    except ValueError:
        return None
    for token in argv:
        if token.endswith(".py"):
            return token
    return None


# ── hermetic ──────────────────────────────────────────────────────────────────────────────

def check_hermetic(case_dir: Path) -> None:
    """repo/ must run the same on any machine, offline, forever.

    A fixture that reaches the network, or reads a path only the author has, does not fail
    honestly. It fails on whoever runs it next, and the failure looks like a wrong answer
    from the agent under test rather than a broken case.
    """
    name = case_dir.name
    repo = case_dir / "repo"
    if not repo.is_dir():
        err(f"{name}: repo/ is missing")
        return
    files = text_files(repo)
    if not files:
        err(f"{name}: repo/ holds no readable source")
        return
    if not any(p.suffix == ".py" for p in files):
        err(f"{name}: repo/ holds no Python source")

    total = 0
    for path in files:
        body = read(path)
        rel = path.relative_to(case_dir)
        total += len(body.splitlines())

        if path.suffix == ".py":
            for kind, module, imported in IMPORT_STMT.findall(body):
                root = module.split(".")[0]
                if root in BANNED_ROOTS:
                    err(f"{name}: {rel} imports '{module}'; repo/ is standard library and offline")
                elif module == "http" and kind == "from" and re.search(r"\bclient\b", imported or ""):
                    err(f"{name}: {rel} imports http.client; repo/ is offline")
                elif module.startswith("http.client"):
                    err(f"{name}: {rel} imports http.client; repo/ is offline")
            for module in DYNAMIC_IMPORT.findall(body):
                if module.split(".")[0] in BANNED_ROOTS or module.startswith("http.client"):
                    err(f"{name}: {rel} imports '{module}' dynamically; repo/ is offline")

        for pattern, what in CREDENTIAL_PATTERNS:
            if pattern.search(body):
                err(f"{name}: {rel} carries {what}; a fixture must not model a real credential")
        if HOME_PATH.search(body):
            err(f"{name}: {rel} resolves a home directory; the case must not depend on who runs it")
        for literal in STRING_LITERAL.findall(body):
            if not literal.startswith("/"):
                continue
            if literal.startswith(FS_ROOTS) or PATHY_SUFFIX.search(literal):
                err(f"{name}: {rel} carries the absolute path '{literal}'")

    # A copy for a run ignores these, so they cannot change a result. They still travel with
    # the case and a shipped .pyc is one more thing a reader has to decide to ignore.
    if any(p.is_dir() and p.name == "__pycache__" for p in repo.rglob("*")):
        warn(f"{name}: repo/ ships a __pycache__ directory")

    if not REPO_MIN_LINES <= total <= REPO_MAX_LINES:
        warn(f"{name}: repo/ is {total} lines; the band is {REPO_MIN_LINES} to {REPO_MAX_LINES}")

    oracle_dir = case_dir / "oracle"
    for path in text_files(oracle_dir) if oracle_dir.is_dir() else []:
        body = read(path)
        rel = path.relative_to(case_dir)
        for kind, module, _imported in IMPORT_STMT.findall(body):
            root = module.split(".")[0]
            if root == "pytest":
                err(f"{name}: {rel} imports pytest; the oracle runs as a plain script")
            elif root in BANNED_ROOTS:
                err(f"{name}: {rel} imports '{module}'; the oracle is offline too")
        # An oracle that reads the wall clock passes today and fails on a slow CI box.
        if re.search(r"time\.time\(|datetime\.(now|today|utcnow)\(", body):
            warn(f"{name}: {rel} reads the clock; a clock-dependent assertion is not deterministic")


def check_patch(case_dir: Path) -> None:
    """fix.patch may only touch repo/. A patch that edits the oracle rigs its own result."""
    name = case_dir.name
    patch = case_dir / "fix.patch"
    if not patch.is_file():
        err(f"{name}: fix.patch is missing")
        return
    body = read(patch)
    targets = set()
    for line in body.splitlines():
        if line.startswith(("--- ", "+++ ")):
            target = line[4:].split("\t")[0].strip()
            if target in ("/dev/null", ""):
                continue
            targets.add(re.sub(r"^[ab]/", "", target))
    if not targets:
        err(f"{name}: fix.patch names no file; it is not a unified diff")
        return
    for target in sorted(targets):
        if not target.startswith("repo/"):
            err(f"{name}: fix.patch touches '{target}'; the reference fix may only change repo/")


# ── the two that matter ───────────────────────────────────────────────────────────────────

def run_oracle(work: Path, command: str, timeout: int) -> tuple[int | None, str]:
    """Run the oracle with cwd = the case directory and repo/ on sys.path."""
    argv = shlex.split(command)
    if argv and argv[0] in ("python", "python3"):
        # The interpreter running this checker, so the result does not depend on PATH.
        argv[0] = sys.executable
    env = dict(os.environ)
    parts = [str(work / "repo")]
    if env.get("PYTHONPATH"):
        parts.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(parts)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        done = subprocess.run(argv, cwd=work, env=env, timeout=timeout,
                              capture_output=True, text=True)
    except subprocess.TimeoutExpired:
        return None, f"timed out after {timeout}s"
    except OSError as exc:
        return None, f"could not run: {exc}"
    return done.returncode, (done.stdout + done.stderr)


def tail(output: str, lines: int = 12) -> str:
    kept = [ln for ln in output.strip().splitlines() if ln.strip()][-lines:]
    return "\n".join(f"       {ln}" for ln in kept)


def check_oracle_pair(case_dir: Path, command: str, timeout: int) -> None:
    """Prove the case measures something, in a throwaway copy.

    Before the fix the oracle must fail, after it the oracle must pass. Nothing else in this
    file can establish that, and no amount of reading the case can either.
    """
    name = case_dir.name
    tmp = Path(tempfile.mkdtemp(prefix="eval-dataset-check-"))
    try:
        work = tmp / name
        shutil.copytree(case_dir, work,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git"))

        code, output = run_oracle(work, command, timeout)
        if code is None:
            err(f"{name}: oracle did not run against repo/ as shipped ({output})")
            return
        if code == 0:
            err(f"{name}: DATASET BUG, the oracle PASSES against repo/ as shipped. The defect "
                f"is not planted, or the oracle does not assert on it. An agent scores this "
                f"case by changing nothing.")
            return
        ok(f"oracle fails on repo/ as shipped (exit {code})")

        try:
            applied = subprocess.run(["git", "apply", "-p1", "fix.patch"], cwd=work,
                                     capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            # git is the contract's own apply command, so its absence is a broken runner,
            # not a broken case. Say so rather than blaming the fixture.
            err(f"{name}: could not run 'git apply' ({exc}); the checker needs git on PATH")
            return
        if applied.returncode != 0:
            err(f"{name}: fix.patch does not apply with 'git apply -p1'")
            note(tail(applied.stdout + applied.stderr))
            return
        ok("fix.patch applies")

        code, output = run_oracle(work, command, timeout)
        if code is None:
            err(f"{name}: oracle did not run after fix.patch ({output})")
            return
        if code != 0:
            err(f"{name}: DATASET BUG, the oracle still FAILS after fix.patch (exit {code}). "
                f"The case is unwinnable: the reference fix does not satisfy it.")
            note(tail(output))
            return
        ok("oracle passes with fix.patch applied (exit 0)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── report ────────────────────────────────────────────────────────────────────────────────

def print_list(rows: list[tuple[str, str, str, str]]) -> None:
    width_id = max([len(r[0]) for r in rows] + [4])
    width_skill = max([len(r[1]) for r in rows] + [5])
    width_mode = max([len(r[2]) for r in rows] + [4])
    print(f"  {'case'.ljust(width_id)}  {'skill'.ljust(width_skill)}  "
          f"{'mode'.ljust(width_mode)}  defect")
    for case_id, skill, mode, summary in rows:
        print(f"  {case_id.ljust(width_id)}  {skill.ljust(width_skill)}  "
              f"{mode.ljust(width_mode)}  {summary}")


def print_coverage(counts: dict[str, int]) -> None:
    print(f"\n{BLD}coverage{OFF}")
    width = max(len(s) for s in SKILLS)
    for skill in SKILLS:
        count = counts.get(skill, 0)
        mark = " " if count >= MIN_CASES_PER_SKILL else "<"
        print(f"  {skill.ljust(width)}  {count} {mark}")
    for skill in SKILLS:
        if counts.get(skill, 0) < MIN_CASES_PER_SKILL:
            err(f"{skill}: {counts.get(skill, 0)} case(s); the floor is {MIN_CASES_PER_SKILL}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Check the behavioral eval dataset.")
    ap.add_argument("--list", action="store_true",
                    help="print the case table and skip the oracle runs")
    ap.add_argument("--dataset", default=str(DATASET),
                    help="dataset root (default evals/behavioral)")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT,
                    help=f"seconds for one oracle run (default {DEFAULT_TIMEOUT})")
    args = ap.parse_args()

    dataset = Path(args.dataset).resolve()
    if not dataset.is_dir():
        print(f"{RED}FAILED{OFF}: no dataset at {args.dataset}")
        return 1

    case_dirs = sorted(p for p in dataset.iterdir()
                       if p.is_dir() and not p.name.startswith(".") and p.name != "__pycache__")
    if not case_dirs:
        print(f"{RED}FAILED{OFF}: {dataset} holds no cases")
        return 1

    counts: dict[str, int] = {}
    rows: list[tuple[str, str, str, str]] = []

    for case_dir in case_dirs:
        heading(case_dir.name)
        data = check_case_yaml(case_dir)
        check_hermetic(case_dir)
        check_patch(case_dir)

        if data is None:
            continue
        skill = data.get("skill")
        if isinstance(skill, str) and skill in SKILLS:
            counts[skill] = counts.get(skill, 0) + 1
        mode = data.get("mode")
        modes = ",".join(mode) if isinstance(mode, list) else str(mode)
        defect = data.get("defect") if isinstance(data.get("defect"), dict) else {}
        rows.append((case_dir.name, str(skill), modes, str(defect.get("summary", "?"))))

        oracle = data.get("oracle")
        command = oracle.get("command") if isinstance(oracle, dict) else None
        if args.list or not isinstance(command, str):
            continue
        script = oracle_script(command)
        if script is None or not (case_dir / script).is_file():
            continue   # already reported; running it would only repeat the same fact
        # The defect file is normally the file the fix changes. Where it is not, one of the
        # two is describing a different case than the other.
        target = defect.get("file")
        patch = case_dir / "fix.patch"
        if isinstance(target, str) and patch.is_file() and target not in read(patch):
            warn(f"{case_dir.name}: fix.patch does not touch defect.file '{target}'")
        check_oracle_pair(case_dir, command, args.timeout)

    if args.list:
        print(f"\n{BLD}cases{OFF}")
        if rows:
            print_list(rows)

    print_coverage(counts)

    print(f"\n{BLD}suite{OFF}")
    print(f"  cases: {len(case_dirs)} on disk, {len(rows)} parsed")
    if not args.list:
        print(f"  oracle runs: two per parsed case, each time boxed at {args.timeout}s")

    print()
    if errors:
        print(f"{RED}FAILED{OFF}: {len(errors)} error(s), {len(warnings)} warning(s)")
        return 1
    print(f"{GRN}PASS{OFF}: {len(warnings)} warning(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
