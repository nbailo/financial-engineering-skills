#!/usr/bin/env python3
"""Paired patch evaluation: does the relevant skill context help a model repair a planted defect?

MANUAL ONLY. This script costs money and is never run in CI. No workflow invokes it.

    python3 scripts/run_patch_eval.py --model M --effort E --repeats 3 --cases all --dry-run
    python3 scripts/run_patch_eval.py --model M --effort E --repeats 3 --cases all --yes

WHAT IT MEASURES
    Whether a model, handed a repair task plus the repository files, produces a patch that an
    executable oracle accepts, with and without the skill text the case declares. Both arms are
    identical apart from that text.

WHAT IT DOES NOT MEASURE
    Automatic skill routing. Real Codex or Claude Code tool use. File-read traces. Review-quality
    prose. Production readiness. Cross-provider model performance. Routing is deliberately bypassed:
    the skills-on arm is HANDED the case's declared skill material, so this says nothing about
    whether an agent would have found it on its own.

CREDENTIAL BOUNDARY
    OPENAI_API_KEY is read only when a paid run actually begins, held in a local variable, and used
    for one header. It is never printed, never written to the run directory, never passed to an
    oracle, and never present in any child environment. Oracles run with an environment built from
    nothing, so there is no inherited key, proxy or Python path to leak into a fixture.

Standard library only. No package dependency and no telemetry.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("run_patch_eval: PyYAML is required. pip install --require-hashes -r requirements.txt")

RUNNER_VERSION = "1"
API_URL = "https://api.openai.com/v1/responses"
ROOT = Path(__file__).resolve().parent.parent
DATASET = ROOT / "evals" / "behavioral"
RUNS = ROOT / ".agent" / "eval-runs"

EFFORTS = ("minimal", "low", "medium", "high")
MAX_OUTPUT_TOKENS = 16000
REQUEST_TIMEOUT = 300

PATCH_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["patch", "summary"],
    "properties": {
        "patch": {"type": "string", "description": "a unified diff against the given files"},
        "summary": {"type": "string", "description": "one or two sentences on what was changed"},
    },
}

INSTRUCTIONS = (
    "You are repairing a defect in a small repository. Read the task and the files, then return a "
    "unified diff that implements the repair.\n"
    "Rules for the diff: paths are relative and begin with repo/; use ---/+++ headers with a/ and "
    "b/ prefixes; change only what the repair needs; do not add files outside repo/; do not emit a "
    "binary patch. Return only the JSON object the schema describes."
)


# --------------------------------------------------------------------------- fixtures


def load_cases(selector: str) -> list[dict]:
    if not DATASET.is_dir():
        sys.exit(f"run_patch_eval: no dataset at {DATASET.relative_to(ROOT)}")
    wanted = None if selector == "all" else {s.strip() for s in selector.split(",") if s.strip()}
    cases = []
    for path in sorted(d for d in DATASET.iterdir() if d.is_dir()):
        if wanted is not None and path.name not in wanted:
            continue
        spec = yaml.safe_load((path / "case.yaml").read_text(encoding="utf-8"))
        spec["_dir"] = path
        spec["_digest"] = case_digest(path)
        cases.append(spec)
    if wanted is not None:
        missing = wanted - {c["id"] for c in cases}
        if missing:
            sys.exit(f"run_patch_eval: no such case(s): {sorted(missing)}")
    if not cases:
        sys.exit("run_patch_eval: selection matched no cases")
    return cases


def case_digest(case: Path) -> str:
    """A digest over everything the model could see or the oracle could judge.

    Pairing is only meaningful across identical fixtures, so this covers case.yaml, every file under
    repo/, the oracle and the reference patch. Edit any of them and the key changes, which is what
    stops an old record being compared against a new one.
    """
    h = hashlib.sha256()
    for path in sorted(case.rglob("*")):
        if path.is_dir() or path.is_symlink():
            continue
        h.update(str(path.relative_to(case)).encode())
        h.update(path.read_bytes())
    return h.hexdigest()[:16]


def repo_files(case: Path) -> list[tuple[str, str]]:
    """The repository as the model sees it, in stable path order so both arms match byte for byte."""
    out = []
    base = case / "repo"
    for path in sorted(base.rglob("*")):
        if path.is_file() and not path.is_symlink():
            out.append((f"repo/{path.relative_to(base).as_posix()}",
                        path.read_text(encoding="utf-8", errors="replace")))
    return out


def skill_context(spec: dict) -> str:
    """The exact SKILL.md and declared references, from the current checkout."""
    skill_dir = ROOT / "skills" / spec["skill"]
    parts = [f"# {spec['skill']}\n\n" + (skill_dir / "SKILL.md").read_text(encoding="utf-8")]
    for ref in spec["references"]:
        parts.append(f"\n\n# {spec['skill']} / references / {ref}\n\n"
                     + (skill_dir / "references" / ref).read_text(encoding="utf-8"))
    return "".join(parts)


def build_input(spec: dict, arm: str) -> str:
    """Identical in both arms except for the skill_context block. Nothing hints at an expectation."""
    blocks = [f"<task>\n{spec['task'].strip()}\n</task>"]
    if arm == "on":
        blocks.append(f"<skill_context>\n{skill_context(spec)}\n</skill_context>")
    files = "\n\n".join(f"<file path=\"{p}\">\n{t}\n</file>" for p, t in repo_files(spec["_dir"]))
    blocks.append(f"<repository>\n{files}\n</repository>")
    return "\n\n".join(blocks)


# --------------------------------------------------------------------------- pairing


def pair_key(spec: dict, repeat: int, model: str, effort: str, commit: str) -> str:
    return "|".join([spec["id"], spec["_digest"], str(repeat), model, effort,
                     RUNNER_VERSION, commit])


def arm_order(case_id: str, repeat: int) -> tuple[str, str]:
    """Counterbalanced deterministically, so arm order is not confounded with the arm.

    Derived from the case id and the repeat index, not from a clock or a random source, so two runs
    of the same selection order the arms the same way and the schedule is reproducible.
    """
    seed = hashlib.sha256(f"{case_id}:{repeat}".encode()).digest()[0]
    return ("on", "off") if seed % 2 == 0 else ("off", "on")


def repo_commit() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                             capture_output=True, text=True, check=True)
        return out.stdout.strip()
    except (subprocess.CalledProcessError, OSError):
        return "unknown"


# --------------------------------------------------------------------------- the call


def call_model(api_key: str, model: str, effort: str, prompt: str) -> tuple[str, dict]:
    """One Responses API call. Returns (status, record-fragment). Never logs the key."""
    body = {
        "model": model,
        "instructions": INSTRUCTIONS,
        "input": prompt,
        "store": False,
        "max_output_tokens": MAX_OUTPUT_TOKENS,
        "reasoning": {"effort": effort},
        "text": {"format": {"type": "json_schema", "name": "patch_response",
                            "strict": True, "schema": PATCH_SCHEMA}},
    }
    request = urllib.request.Request(
        API_URL, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            payload = json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        # The body can echo request detail; keep the status only, never the headers.
        return "invalid", {"reason": f"http {exc.code}", "latency_s": round(time.monotonic() - started, 2)}
    except (urllib.error.URLError, TimeoutError) as exc:
        return "invalid", {"reason": f"transport: {type(exc).__name__}",
                           "latency_s": round(time.monotonic() - started, 2)}
    except json.JSONDecodeError:
        return "invalid", {"reason": "response was not json",
                           "latency_s": round(time.monotonic() - started, 2)}

    latency = round(time.monotonic() - started, 2)
    usage = payload.get("usage") or {}
    record = {
        "latency_s": latency,
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "status": payload.get("status"),
    }
    if payload.get("status") == "incomplete":
        record["reason"] = "incomplete response"
        return "invalid", record

    text = extract_text(payload)
    if text is None:
        record["reason"] = "no output text"
        return "invalid", record
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        record["reason"] = "structured output did not parse"
        return "invalid", record
    if not isinstance(parsed, dict) or not isinstance(parsed.get("patch"), str) \
            or not parsed["patch"].strip():
        record["reason"] = "no patch in structured output"
        return "invalid", record

    record["patch"] = parsed["patch"]
    record["summary"] = parsed.get("summary", "")   # recorded, never graded
    return "ok", record


def extract_text(payload: dict) -> str | None:
    if isinstance(payload.get("output_text"), str) and payload["output_text"].strip():
        return payload["output_text"]
    for item in payload.get("output") or []:
        for chunk in item.get("content") or []:
            if chunk.get("type") in ("output_text", "text") and chunk.get("text"):
                return chunk["text"]
    return None


# --------------------------------------------------------------------------- grading


def patch_is_safe(patch: str, allowed: list[str]) -> str | None:
    """Structural rejection before anything is applied. Returns a reason, or None if acceptable."""
    if "GIT binary patch" in patch or "literal 0" in patch:
        return "binary patch"
    if "120000" in patch:
        return "symlink mode in patch"
    allowed_set = set(allowed)
    touched = set()
    for line in patch.splitlines():
        for prefix in ("--- ", "+++ "):
            if not line.startswith(prefix):
                continue
            target = line[len(prefix):].split("\t")[0].strip()
            if target == "/dev/null":
                break
            for lead in ("a/", "b/"):
                if target.startswith(lead):
                    target = target[len(lead):]
            if target.startswith("/"):
                return f"absolute path in patch: {target}"
            if ".." in Path(target).parts:
                return f"path traversal in patch: {target}"
            if not target.startswith("repo/"):
                return f"patch header outside repo/: {target}"
            touched.add(target)
            break
    stray = sorted(touched - allowed_set)
    if stray:
        return f"touches undeclared paths: {stray}"
    if not touched:
        return "patch changes nothing"
    return None


def minimal_env(workdir: Path) -> dict[str, str]:
    """Built from nothing. No API key, no proxy, no user site, no inherited Python path."""
    return {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": str(workdir), "TMPDIR": str(workdir),
        "LC_ALL": "C", "LANG": "C",
        "PYTHONPATH": "repo",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONHASHSEED": "0",
    }


def grade(spec: dict, patch: str) -> tuple[str, str]:
    """Apply to a temporary copy and run the oracle. Returns (outcome, detail)."""
    reason = patch_is_safe(patch, spec["allowed_paths"])
    if reason is not None:
        return "fail", reason

    work = Path(tempfile.mkdtemp(prefix=f"patcheval-{spec['id']}-"))
    try:
        copy = work / "case"
        shutil.copytree(spec["_dir"], copy, symlinks=False)
        (copy / "candidate.patch").write_text(patch if patch.endswith("\n") else patch + "\n",
                                              encoding="utf-8")
        env = minimal_env(work)
        check = subprocess.run(["git", "apply", "--check", "--unsafe-paths", "candidate.patch"],
                               cwd=copy, env=env, capture_output=True, text=True)
        if check.returncode != 0:
            return "fail", f"git apply --check: {check.stderr.strip()[:160]}"
        subprocess.run(["git", "apply", "--unsafe-paths", "candidate.patch"],
                       cwd=copy, env=env, check=True, capture_output=True)
        (copy / "candidate.patch").unlink()

        try:
            done = subprocess.run([sys.executable, "-S", "oracle/test_oracle.py"],
                                  cwd=copy, env=env, timeout=int(spec["timeout_seconds"]),
                                  capture_output=True, text=True)
        except subprocess.TimeoutExpired:
            return "fail", "oracle timed out"
        if done.returncode != 0:
            return "fail", f"oracle exit {done.returncode}"
        return "pass", "oracle passed"
    except subprocess.CalledProcessError as exc:
        return "fail", f"apply failed: {str(exc)[:120]}"
    finally:
        shutil.rmtree(work, ignore_errors=True)


# --------------------------------------------------------------------------- reporting


def summarise(records: list[dict], repeats: int) -> None:
    by_key: dict[str, dict[str, dict]] = {}
    for rec in records:
        by_key.setdefault(rec["pair_key"], {})[rec["arm"]] = rec

    complete = {k: v for k, v in by_key.items() if set(v) == {"on", "off"}}
    dropped = len(by_key) - len(complete)

    total = len(records)
    invalid = sum(1 for r in records if r["outcome"] == "invalid")
    applied = sum(1 for r in records if r["outcome"] in ("pass", "fail")
                  and r.get("detail") not in (None, "") and r["outcome"] == "pass")

    print("\n" + "=" * 72)
    print(f"calls requested {total}   completed {total - invalid}   invalid {invalid}")
    print(f"pairs formed {len(by_key)}   complete {len(complete)}   dropped unmatched {dropped}")
    print(f"repeats per case: {repeats}")

    for arm in ("off", "on"):
        arm_records = [r for r in records if r["arm"] == arm and r["outcome"] != "invalid"]
        passes = sum(1 for r in arm_records if r["outcome"] == "pass")
        applies = sum(1 for r in arm_records if r.get("applied"))
        n = len(arm_records)
        rate = f"{passes}/{n}" if n else "0/0"
        arate = f"{applies}/{n}" if n else "0/0"
        print(f"  skills-{arm:<3} correct-fix {rate:>7}   patch-applied {arate:>7}")

    print("\npaired outcomes, one row per (case, repeat)")
    print(f"  {'case':<44} {'rep':>3}  {'off':<5} {'on':<5}  order")
    both_pass = both_fail = on_only = off_only = 0
    for key in sorted(complete):
        pair = complete[key]
        case_id = pair["on"].get("case", "?")
        repeat = str(pair["on"].get("repeat", "?"))
        o, n = pair["off"]["outcome"], pair["on"]["outcome"]
        print(f"  {case_id:<44} {repeat:>3}  {o:<5} {n:<5}  {pair['on']['order']}")
        if o == "pass" and n == "pass":
            both_pass += 1
        elif o == "fail" and n == "fail":
            both_fail += 1
        elif n == "pass" and o == "fail":
            on_only += 1
        elif o == "pass" and n == "fail":
            off_only += 1

    print(f"\n  both pass {both_pass}   both fail {both_fail}   "
          f"on-only wins {on_only}   off-only wins {off_only}")
    print("  These counts are the result. There is no single uplift number here on purpose: with "
          f"{len(complete)} complete pairs at {repeats} repeats, a ratio would imply a precision "
          "this sample does not have.")

    lat = [r["latency_s"] for r in records if r.get("latency_s")]
    inp = [r["input_tokens"] for r in records if r.get("input_tokens")]
    outp = [r["output_tokens"] for r in records if r.get("output_tokens")]
    if lat:
        print(f"\nlatency median {statistics.median(lat):.1f}s")
    if inp:
        print(f"input tokens median {int(statistics.median(inp))} (API reported)")
    if outp:
        print(f"output tokens median {int(statistics.median(outp))} (API reported)")
    print("Routing is bypassed: the skills-on arm is handed its material. This measures repair "
          "under given context, not whether an agent would find that context.")
    print("=" * 72)


# --------------------------------------------------------------------------- main


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", required=True,
                    help="model id; there is no default, because a silent default spends money "
                         "on something you did not choose")
    ap.add_argument("--effort", required=True, choices=EFFORTS, help="reasoning effort")
    ap.add_argument("--repeats", type=int, default=3,
                    help="repeats per case per arm (default 3)")
    ap.add_argument("--cases", required=True,
                    help="'all' or a comma-separated list of case ids")
    ap.add_argument("--skills", choices=("paired", "on", "off"), default="paired")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan and the input-size estimate, then exit without network")
    ap.add_argument("--yes", action="store_true",
                    help="required for a paid run; without it nothing reaches the network")
    args = ap.parse_args()

    if args.repeats < 1:
        sys.exit("run_patch_eval: --repeats must be at least 1")

    cases = load_cases(args.cases)
    arms = ("off", "on") if args.skills == "paired" else (args.skills,)
    calls = len(cases) * args.repeats * len(arms)
    commit = repo_commit()

    print(f"paired patch eval   runner v{RUNNER_VERSION}   commit {commit[:12]}")
    print(f"model {args.model}   effort {args.effort}   repeats {args.repeats}   arms {list(arms)}")
    print(f"{len(cases)} case(s) x {args.repeats} repeat(s) x {len(arms)} arm(s) = {calls} call(s)")

    approx = 0
    for spec in cases:
        base = len(spec["task"]) + sum(len(t) for _, t in repo_files(spec["_dir"]))
        for arm in arms:
            extra = len(skill_context(spec)) if arm == "on" else 0
            approx += (base + extra) * args.repeats
    print(f"approximate input size: ~{approx // 4000}k tokens across all calls "
          f"(rough: characters / 4, NOT an API count and NOT a price)")

    if args.dry_run or not args.yes:
        if not args.dry_run:
            print("\nNo --yes, so nothing was sent. Re-run with --yes to make these calls.")
        else:
            print("\nDry run: no network access was attempted.")
        for spec in cases:
            for repeat in range(args.repeats):
                first, second = arm_order(spec["id"], repeat)
                order = [a for a in (first, second) if a in arms]
                print(f"  would run {spec['id']} repeat {repeat} arms {order}")
        return 0

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        sys.exit("run_patch_eval: OPENAI_API_KEY is not set. It is read only at this point, held "
                 "in memory, and never written anywhere.")

    RUNS.mkdir(parents=True, exist_ok=True)
    run_dir = RUNS / time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    run_dir.mkdir(mode=0o700)
    manifest = {
        "runner_version": RUNNER_VERSION, "model": args.model, "effort": args.effort,
        "repeats": args.repeats, "arms": list(arms), "commit": commit,
        "cases": [{"id": c["id"], "digest": c["_digest"], "skill": c["skill"]} for c in cases],
        "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    write_private(run_dir / "manifest.json", json.dumps(manifest, indent=2))

    records: list[dict] = []
    for spec in cases:
        for repeat in range(args.repeats):
            for arm in [a for a in arm_order(spec["id"], repeat) if a in arms]:
                key = pair_key(spec, repeat, args.model, args.effort, commit)
                status, frag = call_model(api_key, args.model, args.effort,
                                          build_input(spec, arm))
                rec = {"pair_key": key, "case": spec["id"], "arm": arm, "repeat": repeat,
                       "order": "-".join(arm_order(spec["id"], repeat)),
                       "model": args.model, "effort": args.effort,
                       "runner_version": RUNNER_VERSION, "commit": commit,
                       "digest": spec["_digest"],
                       "latency_s": frag.get("latency_s"),
                       "input_tokens": frag.get("input_tokens"),
                       "output_tokens": frag.get("output_tokens")}
                if status != "ok":
                    rec.update(outcome="invalid", detail=frag.get("reason", "unknown"),
                               applied=False)
                else:
                    outcome, detail = grade(spec, frag["patch"])
                    rec.update(outcome=outcome, detail=detail,
                               applied=detail == "oracle passed" or "oracle exit" in detail,
                               summary=frag.get("summary", ""))
                    write_private(run_dir / f"{spec['id']}.{arm}.{repeat}.patch", frag["patch"])
                records.append(rec)
                print(f"  {spec['id']:<44} {arm:<3} r{repeat} -> {rec['outcome']}")
                write_private(run_dir / f"{spec['id']}.{arm}.{repeat}.json",
                              json.dumps(rec, indent=2))

    write_private(run_dir / "records.json", json.dumps(records, indent=2))
    summarise(records, args.repeats)
    print(f"\nrun directory: {run_dir}")
    return 0


def write_private(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    os.chmod(path, 0o600)


if __name__ == "__main__":
    sys.exit(main())
