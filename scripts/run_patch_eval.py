#!/usr/bin/env python3
"""Paired behavioral evaluation: does the target skill help a model repair a planted defect?

MANUAL ONLY. This script costs money and is never run in CI. No workflow invokes it.

    python3 scripts/run_patch_eval.py --sandbox-selftest
    python3 scripts/run_patch_eval.py --model M --effort E --cases all --dry-run
    python3 scripts/run_patch_eval.py --model M --effort E --cases all --yes --max-calls 72

WHAT IT MEASURES
    Whether a model, handed a repair task plus the repository files, produces a patch that an
    executable oracle accepts. Each case names one target_skill and two contexts: the baseline arm
    carries baseline_context, the treatment arm carries the same context plus the target skill. For
    an ordinary domain case the baseline is empty. For a fin-verification case the baseline is the
    relevant domain skill, because verification is layered on top of domain knowledge, not
    substituted for it.

WHAT IT DOES NOT MEASURE
    Automatic skill routing. Real Codex or Claude Code tool use. File-read traces. Review-quality
    prose. Production readiness. Cross-provider model performance. Routing is deliberately bypassed:
    the treatment arm is HANDED the case's declared skill material, so this says nothing about
    whether an agent would have found it on its own.

TRUST BOUNDARIES
    A model-generated patch is untrusted code. It is never executed on the host. It is applied to a
    materialised copy of a frozen fixture and the oracle runs inside a Docker container pinned by
    immutable digest, with no network, no capabilities, an unprivileged numeric user, a read-only
    root filesystem, a read-only case mount, a bounded tmpfs, and hard process, memory, CPU and
    wall-clock limits. There is no local fallback: without Docker a paid run refuses to start.

    OPENAI_API_KEY is read only when a paid run actually begins, held in a local variable, and used
    for one header. It is never printed, never written to the run directory, never passed to an
    oracle, and never present in any child environment, host or container.

Standard library only. No package dependency and no telemetry.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

try:
    import yaml
except ImportError:
    sys.exit("run_patch_eval: PyYAML is required. pip install --require-hashes -r requirements.txt")

RUNNER_VERSION = "2"
API_URL = "https://api.openai.com/v1/responses"
ROOT = Path(__file__).resolve().parent.parent
DATASET = ROOT / "evals" / "behavioral"
SKILLS = ROOT / "skills"
RUNS = ROOT / ".agent" / "eval-runs"

ARMS = ("baseline", "treatment")
EFFORTS = ("minimal", "low", "medium", "high")
INSTALLED_SKILLS = ("fin-money-core", "fin-exchange-integration", "fin-payments",
                    "fin-ledger", "fin-onchain", "fin-verification")
MAX_OUTPUT_TOKENS = 16000
REQUEST_TIMEOUT = 300
MAX_REPEATS = 10                # a ceiling on how much a single flag can spend
MAX_RETRIES = 2                 # per call, on 429/5xx/transport only
BACKOFF_BASE_S = 2.0
BACKOFF_CEILING_S = 30.0
RETRY_AFTER_CEILING_S = 60.0

# The sandbox image, pinned by immutable digest. This digest is an OCI image index covering
# linux/amd64 and linux/arm64, so it means the same thing on either architecture.
# python:3.12-slim-bookworm is the tag it resolved to when it was pinned; the digest is what runs.
SANDBOX_IMAGE = ("python@sha256:"
                 "0f5b26b9518d002b6173fd61daad821fa340635ebfec5bba471013f9ca114579")
SANDBOX_IMAGE_TAG_WHEN_PINNED = "python:3.12-slim-bookworm"
SANDBOX_PIDS = 64
SANDBOX_MEMORY = "512m"
SANDBOX_CPUS = "1.0"
SANDBOX_TMPFS_SIZE = "64m"
SANDBOX_TMPFS = "/sandbox"
SANDBOX_CASE = "/case"
SANDBOX_UID = "65534:65534"

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
    "Rules for the diff: modify existing files only; paths are relative and begin with repo/; use "
    "---/+++ headers with a/ and b/ prefixes; change only what the repair needs; do not add, "
    "delete, rename or copy files; do not change file modes; do not emit a binary patch. Return "
    "only the JSON object the schema describes."
)

MANIFEST_KEYS = ("id", "target_skill", "baseline_context", "treatment_context", "references",
                 "task", "allowed_paths", "timeout_seconds", "defect", "oracle_proves")


class ContextError(RuntimeError):
    """A fixture, skill path or patch that must not reach a prompt, a mount or the grader."""


# --------------------------------------------------------------------------- path confinement


def _confine(base: Path, relative: str, what: str) -> Path:
    """Resolve `relative` under `base`, refusing anything that could leave it.

    Absolute paths, traversal and symlinks are refused before resolution, and the resolved result
    is required to stay under the resolved base. Every path that reaches a prompt, a container
    mount or the grader goes through here, so '../../etc/hosts' and a symlinked reference are
    rejected in one place rather than in six.
    """
    if not isinstance(relative, str) or not relative.strip():
        raise ContextError(f"{what}: empty path")
    posix = PurePosixPath(relative)
    if posix.is_absolute() or relative.startswith("/") or relative.startswith("\\"):
        raise ContextError(f"{what}: absolute path {relative!r}")
    if re.match(r"^[A-Za-z]:", relative):
        raise ContextError(f"{what}: drive-qualified path {relative!r}")
    if any(part in ("..", "") for part in posix.parts):
        raise ContextError(f"{what}: path traversal in {relative!r}")

    base_resolved = base.resolve(strict=True)
    walk = base_resolved
    for part in posix.parts:
        walk = walk / part
        if walk.is_symlink():
            raise ContextError(f"{what}: {relative!r} passes through a symlink")
    resolved = walk.resolve()
    if not resolved.is_relative_to(base_resolved):
        raise ContextError(f"{what}: {relative!r} resolves outside {base_resolved}")
    if not resolved.is_file():
        raise ContextError(f"{what}: {relative!r} is not an existing regular file")
    return resolved


def confined_reference(skill: str, ref: str) -> Path:
    """A declared reference, confined to that skill's own references/ directory."""
    if skill not in INSTALLED_SKILLS:
        raise ContextError(f"skill {skill!r} is not one of the installed skills")
    return _confine(SKILLS / skill / "references", ref, f"{skill} reference")


def confined_skill_md(skill: str) -> Path:
    if skill not in INSTALLED_SKILLS:
        raise ContextError(f"skill {skill!r} is not one of the installed skills")
    return _confine(SKILLS / skill, "SKILL.md", f"{skill} SKILL.md")


# --------------------------------------------------------------------------- freezing


@dataclass(frozen=True)
class FrozenCase:
    """Everything about one case, read once and never read from disk again during the run.

    `files` carries the whole fixture as bytes, so grading materialises the case from memory. That
    is what makes a run insensitive to an edit landing mid-run: the prompts, the digest and the
    graded tree all come from one snapshot, so the two arms of a pair differed in one thing.
    """
    id: str
    target_skill: str
    baseline_context: tuple[str, ...]
    treatment_context: tuple[str, ...]
    task: str
    allowed_paths: tuple[str, ...]
    timeout_seconds: int
    files: tuple[tuple[str, bytes], ...]
    fixture_digest: str
    baseline_prompt: str
    treatment_prompt: str
    prompt_digest: str = ""
    references: tuple[tuple[str, tuple[str, ...]], ...] = field(default_factory=tuple)

    def prompt(self, arm: str) -> str:
        return self.baseline_prompt if arm == "baseline" else self.treatment_prompt

    def context(self, arm: str) -> tuple[str, ...]:
        return self.baseline_context if arm == "baseline" else self.treatment_context


def _digest(*parts: bytes) -> str:
    """Length-prefixed, so no concatenation of one set of pieces can collide with another."""
    h = hashlib.sha256()
    for blob in parts:
        h.update(str(len(blob)).encode())
        h.update(b"\0")
        h.update(blob)
    return h.hexdigest()


def read_case_tree(case: Path) -> tuple[tuple[str, bytes], ...]:
    """Every file in the case, as bytes, in stable path order. Refuses symlinks outright."""
    out: list[tuple[str, bytes]] = []
    for path in sorted(case.rglob("*")):
        rel = path.relative_to(case).as_posix()
        if path.is_symlink():
            raise ContextError(f"{case.name}: {rel} is a symlink; a fixture is a plain tree")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ContextError(f"{case.name}: {rel} is not a regular file")
        out.append((rel, path.read_bytes()))
    return tuple(out)


def load_spec(case: Path) -> dict:
    """The closed schema, and the one rule that makes the comparison layered rather than binary."""
    spec = yaml.safe_load((case / "case.yaml").read_text(encoding="utf-8"))
    if not isinstance(spec, dict):
        raise ContextError(f"{case.name}: case.yaml is not a mapping")
    missing = [k for k in MANIFEST_KEYS if k not in spec]
    if missing:
        raise ContextError(f"{case.name}: case.yaml is missing {missing}")
    extra = sorted(set(spec) - set(MANIFEST_KEYS))
    if extra:
        raise ContextError(f"{case.name}: case.yaml carries unknown keys {extra}")
    if spec["id"] != case.name:
        raise ContextError(f"{case.name}: id {spec['id']!r} does not match the directory")

    baseline = list(spec["baseline_context"] or [])
    treatment = list(spec["treatment_context"] or [])
    target = spec["target_skill"]
    if target not in INSTALLED_SKILLS:
        raise ContextError(f"{case.name}: target_skill {target!r} is not an installed skill")
    if treatment != baseline + [target]:
        raise ContextError(f"{case.name}: treatment_context must be baseline_context with "
                           f"target_skill appended last, got {treatment}")
    if target in baseline:
        raise ContextError(f"{case.name}: target_skill {target!r} is already in baseline_context, "
                           f"so the arms would not differ")
    refs = spec["references"]
    if not isinstance(refs, dict):
        raise ContextError(f"{case.name}: references must be a mapping of skill to file list")
    for skill in treatment:
        listed = refs.get(skill)
        if not isinstance(listed, list) or not listed:
            raise ContextError(f"{case.name}: references has no non-empty list for {skill!r}")
    unknown = sorted(set(refs) - set(treatment))
    if unknown:
        raise ContextError(f"{case.name}: references names {unknown}, "
                           f"which is not in treatment_context")
    return spec


def skill_block(skill: str, refs: list[str]) -> str:
    """One skill's SKILL.md plus its declared references, every path confined before it is read."""
    parts = [f"# {skill}\n\n" + confined_skill_md(skill).read_text(encoding="utf-8")]
    for ref in refs:
        body = confined_reference(skill, ref).read_text(encoding="utf-8")
        parts.append(f"\n\n# {skill} / references / {ref}\n\n{body}")
    return "".join(parts)


def build_prompt(task: str, files: tuple[tuple[str, bytes], ...], context_text: str) -> str:
    """Identical in both arms except for the skill_context block. Nothing hints at an expectation."""
    blocks = [f"<task>\n{task.strip()}\n</task>"]
    if context_text:
        blocks.append(f"<skill_context>\n{context_text}\n</skill_context>")
    shown = "\n\n".join(
        f"<file path=\"{rel}\">\n{blob.decode('utf-8', 'replace')}\n</file>"
        for rel, blob in files if rel.startswith("repo/"))
    blocks.append(f"<repository>\n{shown}\n</repository>")
    return "\n\n".join(blocks)


def dataset_cases() -> list[Path]:
    """The case directories, in stable order. A dot-entry is tooling state, never a case."""
    return sorted(d for d in DATASET.iterdir() if d.is_dir() and not d.name.startswith("."))


def freeze_cases(selector: str, model: str, effort: str, commit: str) -> list[FrozenCase]:
    """Read every fixture and every skill file exactly once, and build both prompts up front.

    After this returns, nothing on disk is consulted again for prompt content. A fixture or skill
    file edited mid-run cannot change what an arm was sent, which is the only way the two arms of a
    pair are guaranteed to have differed in exactly one thing.
    """
    if not DATASET.is_dir():
        raise ContextError(f"no dataset at {DATASET}")
    wanted = None if selector == "all" else {s.strip() for s in selector.split(",") if s.strip()}
    frozen: list[FrozenCase] = []
    for case in dataset_cases():
        if wanted is not None and case.name not in wanted:
            continue
        spec = load_spec(case)
        files = read_case_tree(case)
        fixture_digest = _digest(*(rel.encode() + b"\0" + blob for rel, blob in files))[:32]

        refs = {str(k): [str(v) for v in vs] for k, vs in spec["references"].items()}
        baseline_text = "\n\n".join(skill_block(s, refs[s]) for s in spec["baseline_context"])
        treatment_text = "\n\n".join(skill_block(s, refs[s]) for s in spec["treatment_context"])

        task = str(spec["task"])
        baseline_prompt = build_prompt(task, files, baseline_text)
        treatment_prompt = build_prompt(task, files, treatment_text)
        digest = _digest(RUNNER_VERSION.encode(), INSTRUCTIONS.encode(),
                         baseline_prompt.encode(), treatment_prompt.encode(),
                         model.encode(), effort.encode(), commit.encode(),
                         fixture_digest.encode())[:32]

        for declared in spec["allowed_paths"]:
            if not isinstance(declared, str) or not declared.startswith("repo/"):
                raise ContextError(f"{case.name}: allowed path {declared!r} is outside repo/")
            _confine(case, declared, f"{case.name} allowed path")

        frozen.append(FrozenCase(
            id=spec["id"], target_skill=spec["target_skill"],
            baseline_context=tuple(spec["baseline_context"] or []),
            treatment_context=tuple(spec["treatment_context"]),
            task=task, allowed_paths=tuple(spec["allowed_paths"]),
            timeout_seconds=int(spec["timeout_seconds"]),
            files=files, fixture_digest=fixture_digest,
            baseline_prompt=baseline_prompt, treatment_prompt=treatment_prompt,
            prompt_digest=digest,
            references=tuple((k, tuple(v)) for k, v in sorted(refs.items())),
        ))

    if wanted is not None:
        missing = wanted - {c.id for c in frozen}
        if missing:
            raise ContextError(f"no such case(s): {sorted(missing)}")
    if not frozen:
        raise ContextError("selection matched no cases")
    return frozen


# --------------------------------------------------------------------------- pairing and schedule


def pair_key(run_id: str, case: FrozenCase, repeat: int) -> str:
    """Binds the run, the case, the repeat and the exact frozen prompts of both arms.

    The prompt digest covers the common instructions, both complete prompts, the model, the
    reasoning effort, the runner version, the fixture content and the repository commit. Two
    records pair only when all of that was identical, so nothing that changed between them can be
    read as an effect of the skill.
    """
    return f"{run_id}|{case.id}|{repeat}|{case.prompt_digest}"


def arm_order(case_ids: tuple[str, ...], case_id: str, repeat: int) -> tuple[str, str]:
    """Counterbalanced by position in a stable ordering of case ids, alternating by repeat.

    With an even number of cases this gives exactly half the calls each starting arm, and no case
    leads with the same arm on every repeat. It is a schedule, not a draw, so two runs of the same
    selection order the arms the same way.
    """
    order = sorted(case_ids)
    if case_id not in order:
        raise ContextError(f"{case_id} is not in the schedule")
    if (order.index(case_id) + repeat) % 2 == 0:
        return ("baseline", "treatment")
    return ("treatment", "baseline")


def schedule(cases: list[FrozenCase], repeats: int, arms: tuple[str, ...]
             ) -> list[tuple[FrozenCase, int, tuple[str, ...]]]:
    ids = tuple(c.id for c in cases)
    plan = []
    for case in cases:
        for repeat in range(repeats):
            plan.append((case, repeat,
                         tuple(a for a in arm_order(ids, case.id, repeat) if a in arms)))
    return plan


def repo_commit() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                             capture_output=True, text=True, check=True)
        return out.stdout.strip()
    except (subprocess.CalledProcessError, OSError):
        return "unknown"


def require_clean_tree() -> None:
    """A run records the commit it ran against; an uncommitted edit would make that record wrong."""
    try:
        out = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                             capture_output=True, text=True, check=True)
    except (subprocess.CalledProcessError, OSError) as exc:
        sys.exit(f"run_patch_eval: cannot read git status ({exc}); a paid run needs a clean tree")
    if out.stdout.strip():
        sys.exit("run_patch_eval: the working tree is not clean, and a run is anchored to a "
                 "commit. Commit or stash first.\n" + out.stdout.rstrip())


# --------------------------------------------------------------------------- the call


def _retry_after_seconds(headers) -> float | None:
    try:
        raw = headers.get("Retry-After") if headers is not None else None
    except AttributeError:
        return None
    if not raw:
        return None
    try:
        return max(0.0, min(float(raw), RETRY_AFTER_CEILING_S))
    except (TypeError, ValueError):
        return None


def _backoff(attempt: int) -> float:
    return min(BACKOFF_BASE_S * (2 ** attempt), BACKOFF_CEILING_S)


def call_model(api_key: str, model: str, effort: str, prompt: str,
               sleeper=time.sleep) -> tuple[str, dict]:
    """One Responses API call, with bounded retries. Returns (status, fragment). Never logs the key.

    Only a 429, a 5xx and a transport timeout are retried, at most MAX_RETRIES times, with
    exponential backoff under a hard ceiling and Retry-After honoured when the server sends one. A
    permanent 4xx and a schema failure are never retried: repeating them cannot change the answer
    and would only spend money. A response is accepted only when its status is exactly "completed"
    AND it carries valid structured output; everything else is invalid, which is not a fail.
    """
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
    payload_bytes = json.dumps(body).encode()
    attempts = 0
    started = time.monotonic()
    while True:
        request = urllib.request.Request(
            API_URL, data=payload_bytes,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
                payload = json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            # The body can echo request detail; keep the status only, never the headers.
            if (exc.code == 429 or 500 <= exc.code < 600) and attempts < MAX_RETRIES:
                wait = _retry_after_seconds(getattr(exc, "headers", None))
                attempts += 1
                sleeper(wait if wait is not None else _backoff(attempts - 1))
                continue
            return "invalid", {"reason": f"http {exc.code}", "attempts": attempts + 1,
                               "latency_s": round(time.monotonic() - started, 2)}
        except (TimeoutError, urllib.error.URLError) as exc:
            transient = isinstance(exc, TimeoutError) or isinstance(
                getattr(exc, "reason", None), (TimeoutError, OSError))
            if transient and attempts < MAX_RETRIES:
                attempts += 1
                sleeper(_backoff(attempts - 1))
                continue
            return "invalid", {"reason": f"transport: {type(exc).__name__}",
                               "attempts": attempts + 1,
                               "latency_s": round(time.monotonic() - started, 2)}
        except json.JSONDecodeError:
            return "invalid", {"reason": "response was not json", "attempts": attempts + 1,
                               "latency_s": round(time.monotonic() - started, 2)}
        break

    usage = payload.get("usage") or {}
    status = payload.get("status")
    record = {
        "latency_s": round(time.monotonic() - started, 2), "attempts": attempts + 1,
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "api_status": status,
    }
    if status != "completed":
        known = {"incomplete", "failed", "cancelled", "queued", "in_progress"}
        record["reason"] = f"response {status}" if status in known \
            else f"response carried an unknown status {status!r}"
        return "invalid", record

    text = extract_text(payload)
    if text is None:
        record["reason"] = "completed response carried no output text"
        return "invalid", record
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        record["reason"] = "structured output did not parse"
        return "invalid", record
    if not isinstance(parsed, dict) or not isinstance(parsed.get("patch"), str) \
            or not parsed["patch"].strip():
        record["reason"] = "completed response carried no patch"
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


# --------------------------------------------------------------------------- patch validation

# Metadata this evaluator version does not support. Each entry moves, creates or retypes a file,
# and the grader only understands edits to files the case already declared. A patch carrying any of
# it is refused before git sees it, which is why `git apply --unsafe-paths` is not used anywhere:
# that flag honours a rename target outside the working directory, and the answer is not to pass it.
REJECTED_METADATA = (
    ("rename from ", "renames"),
    ("rename to ", "renames"),
    ("copy from ", "copies"),
    ("copy to ", "copies"),
    ("new file mode ", "new files"),
    ("deleted file mode ", "deleted files"),
    ("old mode ", "mode changes"),
    ("new mode ", "mode changes"),
    ("similarity index ", "renames or copies"),
    ("dissimilarity index ", "renames or copies"),
    ("GIT binary patch", "binary patches"),
    ("Binary files ", "binary patches"),
    ("Subproject commit ", "submodules"),
)


def _strip_prefix(target: str) -> str:
    for lead in ("a/", "b/"):
        if target.startswith(lead):
            return target[len(lead):]
    return target


def _check_path(raw: str, allowed: set[str], known_files: set[str], where: str) -> str:
    target = _strip_prefix(raw.split("\t")[0].strip().strip('"'))
    if not target:
        raise ContextError(f"the {where} header carries no path")
    if target == "/dev/null":
        raise ContextError("the patch adds or deletes a file (/dev/null header)")
    if target.startswith("/") or PurePosixPath(target).is_absolute():
        raise ContextError(f"absolute path in patch: {target}")
    if ".." in PurePosixPath(target).parts:
        raise ContextError(f"path traversal in patch: {target}")
    if not target.startswith("repo/"):
        raise ContextError(f"patch header outside repo/: {target}")
    if target not in known_files:
        raise ContextError(f"the patch targets {target}, which is not a file in this case")
    if target not in allowed:
        raise ContextError(f"the patch touches an undeclared path: {target}")
    return target


def validate_patch(patch: str, allowed: tuple[str, ...],
                   known_files: set[str]) -> tuple[set[str], str | None]:
    """Parse every path-bearing line and refuse anything the grader cannot reason about.

    Returns (paths, reason); reason is None when the patch is acceptable. Every `diff --git`,
    `---`, `+++` and rename/copy line is read, not only the first pair, so a second hidden diff
    block or a rename appended after an allowed hunk is caught here rather than by git.
    """
    allowed_set = set(allowed)
    touched: set[str] = set()
    hunks = 0
    try:
        for line in patch.splitlines():
            for marker, label in REJECTED_METADATA:
                if line.startswith(marker):
                    raise ContextError(f"this evaluator does not accept {label}")
            if line.startswith(("index ", "mode ")) and "120000" in line:
                raise ContextError("symlink mode in patch")
            if line.startswith("@@"):
                hunks += 1
            elif line.startswith("diff --git "):
                halves = line[len("diff --git "):].strip().split(" b/")
                if len(halves) != 2:
                    raise ContextError(f"unparseable diff header: {line[:80]}")
                left = _check_path(halves[0], allowed_set, known_files, "diff")
                right = _check_path("b/" + halves[1], allowed_set, known_files, "diff")
                if left != right:
                    raise ContextError(f"the patch moves {left} to {right}")
                touched.add(left)
            elif line.startswith("--- ") or line.startswith("+++ "):
                touched.add(_check_path(line[4:], allowed_set, known_files,
                                        "source" if line.startswith("--- ") else "target"))
    except ContextError as exc:
        return set(), str(exc)

    if not touched:
        return set(), "the patch names no file inside repo/"
    if not hunks:
        return set(), "the patch carries no hunk, so it changes nothing"
    return touched, None


def inventory(root: Path) -> dict[str, str]:
    """Every path under root with a content digest, so an unexpected change cannot hide."""
    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if path.is_symlink():
            out[rel] = "symlink"
        elif path.is_dir():
            out[rel] = "dir"
        elif path.is_file():
            out[rel] = "file:" + hashlib.sha256(path.read_bytes()).hexdigest()
        else:
            out[rel] = "other"
    return out


def inventory_drift(before: dict[str, str], after: dict[str, str],
                    declared: set[str]) -> str | None:
    """Compare the whole tree, not only the paths the patch admitted to touching."""
    appeared = sorted(set(after) - set(before))
    if appeared:
        return f"the patch created {appeared}"
    vanished = sorted(set(before) - set(after))
    if vanished:
        return f"the patch removed {vanished}"
    for rel, kind in after.items():
        was = before[rel]
        if was == kind:
            continue
        if was.split(":")[0] != kind.split(":")[0]:
            return f"{rel} changed type from {was.split(':')[0]} to {kind.split(':')[0]}"
        if rel not in declared:
            return f"the patch changed an undeclared path: {rel}"
    return None


# --------------------------------------------------------------------------- the sandbox


def docker_binary() -> str | None:
    return shutil.which("docker")


def docker_cli_env() -> dict[str, str]:
    """The environment for the docker CLI on the HOST, so it can reach its own daemon.

    This is not what the container gets; the container gets only the variables named in
    sandbox_command(). PATH and HOME are what the CLI needs to find itself and its context, and the
    DOCKER_* names are the documented ways to point it at a daemon. Nothing else is forwarded, so an
    API key, a proxy or a cloud credential in the caller's environment stops here.
    """
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin"),
           "HOME": os.environ.get("HOME", "/tmp")}
    for name in ("DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG",
                 "DOCKER_CERT_PATH", "DOCKER_TLS_VERIFY"):
        value = os.environ.get(name)
        if value:
            env[name] = value
    return env


def sandbox_preflight() -> str | None:
    """Returns a reason the sandbox is unusable, or None. There is no local fallback on purpose."""
    binary = docker_binary()
    if binary is None:
        return "docker is not on PATH"
    try:
        probe = subprocess.run([binary, "version", "--format", "{{.Server.Version}}"],
                               env=docker_cli_env(), capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"docker is not usable: {type(exc).__name__}"
    if probe.returncode != 0:
        return f"the docker daemon is not reachable: {probe.stderr.strip()[:160]}"
    present = subprocess.run([binary, "image", "inspect", SANDBOX_IMAGE],
                             env=docker_cli_env(), capture_output=True, text=True)
    if present.returncode != 0:
        print(f"  pulling the pinned sandbox image {SANDBOX_IMAGE}")
        pull = subprocess.run([binary, "pull", "--quiet", SANDBOX_IMAGE],
                              env=docker_cli_env(), capture_output=True, text=True, timeout=900)
        if pull.returncode != 0:
            return f"cannot pull the pinned image: {pull.stderr.strip()[:160]}"
    return None


def sandbox_command(binary: str, name: str, case_root: Path, argv: list[str]) -> list[str]:
    """The one container invocation this evaluator makes. Every flag here is load-bearing.

    The container receives the patched synthetic fixture, its oracle, and the fixed command that
    runs that oracle. It does not receive the repository root, a home directory, the Docker socket,
    git or SSH configuration, any credential, or any other host path.
    """
    return [
        binary, "run", "--rm", "--name", name,
        "--network", "none",                        # no outbound anything
        "--user", SANDBOX_UID,                      # unprivileged numeric user
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--read-only",                              # root filesystem is not writable
        "--pids-limit", str(SANDBOX_PIDS),
        "--memory", SANDBOX_MEMORY, "--memory-swap", SANDBOX_MEMORY,
        "--cpus", SANDBOX_CPUS,
        "--tmpfs", f"{SANDBOX_TMPFS}:rw,noexec,nosuid,nodev,size={SANDBOX_TMPFS_SIZE}",
        "--mount", f"type=bind,source={case_root},target={SANDBOX_CASE},readonly",
        "--workdir", SANDBOX_CASE,
        # The container's whole environment, stated here and nowhere else.
        "-e", "PYTHONPATH=repo", "-e", "PYTHONDONTWRITEBYTECODE=1",
        "-e", "PYTHONNOUSERSITE=1", "-e", "PYTHONHASHSEED=0",
        "-e", f"HOME={SANDBOX_TMPFS}", "-e", f"TMPDIR={SANDBOX_TMPFS}",
        "-e", "LC_ALL=C", "-e", "LANG=C",
        "--entrypoint", "python3",
        SANDBOX_IMAGE, *argv,
    ]


def run_in_sandbox(case_root: Path, argv: list[str], timeout: int, label: str) -> tuple[int, str]:
    """Run python3 inside the pinned container. Returns (exit code, tail of output).

    Exit 124 means the wall clock ran out. The container is then killed explicitly, because `docker
    run` in the foreground does not stop the container when the CLI it fronted is killed.
    """
    binary = docker_binary()
    if binary is None:
        raise ContextError("docker disappeared between preflight and this call")
    safe = re.sub(r"[^A-Za-z0-9_.-]", "-", label)[:40]
    name = f"patcheval-{safe}-{secrets.token_hex(4)}"
    try:
        done = subprocess.run(sandbox_command(binary, name, case_root, argv),
                              env=docker_cli_env(), capture_output=True,
                              text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        subprocess.run([binary, "rm", "-f", name], env=docker_cli_env(),
                       capture_output=True, text=True)
        return 124, f"the oracle exceeded its {timeout}s wall clock"
    return done.returncode, ((done.stderr or "") + (done.stdout or ""))[-800:]


def materialise(case: FrozenCase, dest: Path,
                include: tuple[str, ...] = ("repo/", "oracle/")) -> None:
    """Write the frozen fixture out from memory, not from disk.

    fix.patch and case.yaml are deliberately left out: the reference answer has no business inside
    a container that is grading a candidate's answer.
    """
    for rel, blob in case.files:
        if not rel.startswith(include):
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blob)
        target.chmod(0o644)
    for path in [dest, *dest.rglob("*")]:
        if path.is_dir():
            path.chmod(0o755)


def _git_env(work: Path) -> dict[str, str]:
    return {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(work), "TMPDIR": str(work),
            "LC_ALL": "C", "LANG": "C",
            "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}


# --------------------------------------------------------------------------- grading


def blank_grade() -> dict:
    return {"patch_valid": False, "patch_applied": False,
            "oracle_completed": False, "oracle_passed": False,
            "outcome": "fail", "reason": ""}


def grade(case: FrozenCase, patch: str) -> dict:
    """Validate, apply to a materialised copy, run the oracle in the pinned container.

    Every field is explicit. Nothing downstream infers whether the patch applied by reading a
    string: a patch that applies and then times out reports patch_applied true and oracle_completed
    false, which is a different fact from a patch that never applied at all.
    """
    result = blank_grade()
    known = {rel for rel, _ in case.files}
    touched, reason = validate_patch(patch, case.allowed_paths, known)
    if reason is not None:
        result["reason"] = reason
        return result
    result["patch_valid"] = True

    work = Path(tempfile.mkdtemp(prefix=f"patcheval-{case.id}-"))
    try:
        work.chmod(0o755)
        copy = work / "case"
        copy.mkdir(mode=0o755)
        materialise(case, copy)
        before = inventory(copy)

        patch_file = work / "candidate.patch"
        patch_file.write_text(patch if patch.endswith("\n") else patch + "\n", encoding="utf-8")
        for stage in (["--check"], []):
            done = subprocess.run(["git", "apply", *stage, str(patch_file)],
                                  cwd=copy, env=_git_env(work), capture_output=True, text=True)
            if done.returncode != 0:
                result["reason"] = (f"git apply{' --check' if stage else ''}: "
                                    f"{done.stderr.strip()[:160]}")
                return result

        drift = inventory_drift(before, inventory(copy), touched)
        if drift is not None:
            result["reason"] = drift
            return result
        result["patch_applied"] = True
        for path in [copy, *copy.rglob("*")]:
            path.chmod(0o755 if path.is_dir() else 0o644)

        code, tail = run_in_sandbox(copy, ["-S", "oracle/test_oracle.py"],
                                    case.timeout_seconds, case.id)
        if code == 124:
            result["reason"] = tail
            return result
        result["oracle_completed"] = True
        if code == 0:
            result.update(oracle_passed=True, outcome="pass",
                          reason="the oracle accepted the patch")
        else:
            result["reason"] = f"the oracle rejected the patch (exit {code})"
        return result
    except ContextError as exc:
        result["reason"] = str(exc)
        return result
    finally:
        shutil.rmtree(work, ignore_errors=True)


# --------------------------------------------------------------------------- sandbox self-test


SELFTEST_NETWORK = """
import socket, sys
try:
    socket.create_connection(("1.1.1.1", 53), timeout=4)
except OSError as exc:
    print("outbound tcp:", type(exc).__name__); sys.exit(0)
print("outbound tcp: REACHABLE"); sys.exit(1)
"""

# {root} is this repository's absolute path on the host. It is interpolated as a plain string the
# container is asked to look for; naming it is the point, because "no host path is visible" is only
# a real claim if the one path that would matter most is named.
SELFTEST_HOST = """
import os, sys
paths = [p for p in ("/Users", "/home/runner", "/var/run/docker.sock", "/root/.ssh",
                     "/root/.docker", "/root/.gitconfig", "/host", {root!r}) if os.path.exists(p)]
env = sorted(k for k in os.environ
             if "SELFTEST" in k or "OPENAI" in k or "AWS" in k or "PROXY" in k.upper())
writable = []
for path in ("/etc/selftest-probe", "/case/selftest-probe", "/usr/selftest-probe"):
    try:
        open(path, "w").write("x"); writable.append(path)
    except OSError:
        pass
readable_secrets = []
for path in ("/etc/shadow", "/etc/gshadow"):
    try:
        open(path, "rb").read(1); readable_secrets.append(path)
    except OSError:
        pass
open("/sandbox/probe", "w").write("x")
print("host paths visible:", paths)
print("host environment visible:", env)
print("writable outside the tmpfs:", writable)
print("privileged files this user can read:", readable_secrets)
print("case mount:", sorted(os.listdir("/case")))
print("tmpfs writable: yes")
sys.exit(0 if not (paths or env or writable or readable_secrets) else 1)
"""

SELFTEST_PIDS = """
import sys, threading
stop, made = threading.Event(), 0
try:
    for _ in range(4096):
        threading.Thread(target=stop.wait, daemon=True).start(); made += 1
except RuntimeError as exc:
    print("stopped by:", type(exc).__name__)
stop.set()
print("tasks created before the limit stopped us:", made)
sys.exit(0 if made < 4096 else 1)
"""


def _selftest_probe(copy: Path, label: str, script: str, expectation: str) -> int:
    code, tail = run_in_sandbox(copy, ["-S", "-c", script], 120, "selftest")
    print(f"  {'ok  ' if code == 0 else 'FAIL'} {label} is {expectation}")
    for line in tail.strip().splitlines():
        print(f"         {line}")
    return 0 if code == 0 else 1


def sandbox_selftest() -> int:
    """Prove the container is confined, then prove every shipped oracle still runs inside it."""
    print(f"sandbox self-test   image {SANDBOX_IMAGE}")
    print(f"                    pinned from tag {SANDBOX_IMAGE_TAG_WHEN_PINNED}")
    reason = sandbox_preflight()
    if reason is not None:
        print(f"FAILED: {reason}")
        return 1
    try:
        cases = freeze_cases("all", "selftest", "selftest", repo_commit())
    except ContextError as exc:
        print(f"FAILED: {exc}")
        return 1

    failures = 0
    probe_dir = Path(tempfile.mkdtemp(prefix="patcheval-selftest-"))
    try:
        probe_dir.chmod(0o755)
        copy = probe_dir / "case"
        copy.mkdir(mode=0o755)
        materialise(cases[0], copy)
        os.environ["SELFTEST_FAKE_CREDENTIAL"] = "must-not-appear-inside-the-container"
        print("\nconfinement")
        failures += _selftest_probe(copy, "outbound network", SELFTEST_NETWORK, "unavailable")
        failures += _selftest_probe(copy, "host files and host environment",
                                    SELFTEST_HOST.format(root=str(ROOT)), "unavailable")
        failures += _selftest_probe(copy, "process count", SELFTEST_PIDS, "bounded")
    finally:
        os.environ.pop("SELFTEST_FAKE_CREDENTIAL", None)
        shutil.rmtree(probe_dir, ignore_errors=True)

    print(f"\nthe {len(cases)} shipped oracles, run inside the container")
    for case in cases:
        work = Path(tempfile.mkdtemp(prefix="patcheval-selftest-case-"))
        try:
            work.chmod(0o755)
            copy = work / "case"
            copy.mkdir(mode=0o755)
            materialise(case, copy)
            defect_code, _ = run_in_sandbox(copy, ["-S", "oracle/test_oracle.py"],
                                            case.timeout_seconds, case.id)
            patch_file = work / "fix.patch"
            patch_file.write_bytes(dict(case.files).get("fix.patch", b""))
            applied = subprocess.run(["git", "apply", str(patch_file)], cwd=copy,
                                     env=_git_env(work), capture_output=True, text=True)
            for path in [copy, *copy.rglob("*")]:
                path.chmod(0o755 if path.is_dir() else 0o644)
            fixed_code, tail = run_in_sandbox(copy, ["-S", "oracle/test_oracle.py"],
                                              case.timeout_seconds, case.id)
            good = defect_code not in (0, 124) and applied.returncode == 0 and fixed_code == 0
            failures += 0 if good else 1
            print(f"  {'ok  ' if good else 'FAIL'} {case.id:<44} "
                  f"defect exit {defect_code}, fixed exit {fixed_code}")
            if not good:
                print(f"         git apply rc {applied.returncode} "
                      f"{applied.stderr.strip()[:160]}")
                print(f"         {tail.strip()[:400]}")
        finally:
            shutil.rmtree(work, ignore_errors=True)

    print(f"\n{'FAILED: ' + str(failures) + ' check(s)' if failures else 'PASS'}")
    return 1 if failures else 0


# --------------------------------------------------------------------------- reporting


def pair_records(records: list[dict], run_id: str) -> tuple[dict, list[dict]]:
    """Group into pairs, admitting only well-formed ones. Returns (complete, excluded).

    A pair is complete when both arms are present exactly once, both belong to THIS run, and
    neither is invalid. Everything else is listed and then kept out of every comparison below: an
    invalid arm is a call that did not happen, and pairing a non-event against a real one would let
    an outage read as evidence.
    """
    grouped: dict[str, list[dict]] = {}
    excluded: list[dict] = []
    for rec in records:
        if rec.get("run_id") != run_id:
            excluded.append({"pair_key": str(rec.get("pair_key", "?")),
                             "why": "the record belongs to another run"})
            continue
        grouped.setdefault(rec["pair_key"], []).append(rec)

    complete: dict[str, dict[str, dict]] = {}
    for key, group in grouped.items():
        by_arm: dict[str, dict] = {}
        duplicated = False
        for rec in group:
            if rec["arm"] in by_arm:
                duplicated = True
            by_arm[rec["arm"]] = rec
        if duplicated:
            excluded.append({"pair_key": key, "why": "a duplicate arm in the same run"})
        elif set(by_arm) != set(ARMS):
            excluded.append({"pair_key": key,
                             "why": f"only the {sorted(by_arm)} arm reached a record"})
        elif [a for a in ARMS if by_arm[a]["outcome"] == "invalid"]:
            bad = ", ".join(a for a in ARMS if by_arm[a]["outcome"] == "invalid")
            excluded.append({"pair_key": key, "why": f"the {bad} arm was invalid"})
        else:
            complete[key] = by_arm
    return complete, excluded


def _median(values: list) -> str:
    clean = [v for v in values if isinstance(v, (int, float))]
    return f"{statistics.median(clean):.1f}" if clean else "n/a"


def _tally(values) -> dict[str, int]:
    out: dict[str, int] = {}
    for value in values:
        out[str(value)] = out.get(str(value), 0) + 1
    return out


def summarise(records: list[dict], repeats: int, run_id: str, complete_run: bool = True) -> None:
    complete, excluded = pair_records(records, run_id)
    invalid = [r for r in records if r["outcome"] == "invalid"]
    completed = [r for r in records if r["outcome"] != "invalid"]
    in_pairs = [r for pair in complete.values() for r in pair.values()]

    print("\n" + "=" * 78)
    print(f"run {run_id}   {'complete' if complete_run else 'INCOMPLETE'}")
    print(f"calls made {len(records)}   completed {len(completed)}   invalid {len(invalid)}")
    print(f"complete pairs {len(complete)}   excluded pairs {len(excluded)}   repeats {repeats}")
    if excluded:
        print("\nexcluded pairs, which are counted nowhere below")
        for item in sorted(excluded, key=lambda r: r["pair_key"])[:24]:
            print(f"  {item['pair_key'][:52]:<52} {item['why']}")

    print("\nmarginal arm rates, oracle-accepted / calls")
    print(f"  {'':<11}{'all completed calls':>21}{'calls in complete pairs':>27}")
    for arm in ARMS:
        every = [r for r in completed if r["arm"] == arm]
        paired = [r for r in in_pairs if r["arm"] == arm]
        every_rate = f"{sum(1 for r in every if r['oracle_passed'])}/{len(every)}"
        paired_rate = f"{sum(1 for r in paired if r['oracle_passed'])}/{len(paired)}"
        print(f"  {arm:<11}{every_rate:>21}{paired_rate:>27}")

    print(f"\npaired outcomes, one row per complete pair (n = {len(complete)})")
    print(f"  {'case':<44} {'rep':>3}  {'baseline':<9} {'treatment':<9} order")
    both_pass = both_fail = treatment_only = baseline_only = 0
    for key in sorted(complete, key=lambda k: (complete[k]["baseline"]["case"],
                                               complete[k]["baseline"]["repeat"])):
        b, t = complete[key]["baseline"], complete[key]["treatment"]
        print(f"  {b['case']:<44} {b['repeat']:>3}  {b['outcome']:<9} {t['outcome']:<9} "
              f"{b['order']}")
        if b["oracle_passed"] and t["oracle_passed"]:
            both_pass += 1
        elif not b["oracle_passed"] and not t["oracle_passed"]:
            both_fail += 1
        elif t["oracle_passed"]:
            treatment_only += 1
        else:
            baseline_only += 1
    print(f"\n  both pass {both_pass}   both fail {both_fail}   "
          f"treatment-only wins {treatment_only}   baseline-only wins {baseline_only}")
    print(f"  These counts are the result. There is no single uplift number here on purpose: with "
          f"{len(complete)} complete pairs at {repeats} repeats, a ratio would imply a precision "
          f"this sample does not have.")

    print("\ncost, over calls inside complete pairs")
    for arm in ARMS:
        rows = [r for r in in_pairs if r["arm"] == arm]
        print(f"  {arm:<11} latency {_median([r.get('latency_s') for r in rows]):>7}s   "
              f"input {_median([r.get('input_tokens') for r in rows]):>9}   "
              f"output {_median([r.get('output_tokens') for r in rows]):>8}   (API reported)")
    deltas: dict[str, list] = {"latency_s": [], "input_tokens": [], "output_tokens": []}
    for pair in complete.values():
        for name, sink in deltas.items():
            b, t = pair["baseline"].get(name), pair["treatment"].get(name)
            if isinstance(b, (int, float)) and isinstance(t, (int, float)):
                sink.append(t - b)
    print(f"  {'paired delta':<11} treatment minus baseline, median: "
          f"latency {_median(deltas['latency_s'])}s   "
          f"input {_median(deltas['input_tokens'])}   output {_median(deltas['output_tokens'])}")
    if invalid:
        print(f"  {'invalid':<11} n {len(invalid)}   "
              f"latency median {_median([r.get('latency_s') for r in invalid])}s   "
              f"(reported here and counted nowhere else)")
        for why, count in sorted(_tally(r.get("reason", "?") for r in invalid).items()):
            print(f"    {count:>3}  {why}")

    print("\nRouting is bypassed: the treatment arm is handed its material. This measures repair "
          "under given\ncontext, not whether an agent would find that context.")
    print("=" * 78)


def write_private(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    os.chmod(path, 0o600)


# --------------------------------------------------------------------------- main


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", help="model id; there is no default, because a silent default "
                                    "spends money on something you did not choose")
    ap.add_argument("--effort", choices=EFFORTS, help="reasoning effort; no default, same reason")
    ap.add_argument("--repeats", type=int, default=3,
                    help=f"repeats per case per arm (default 3, ceiling {MAX_REPEATS})")
    ap.add_argument("--cases", default="all", help="'all' or a comma-separated list of case ids")
    ap.add_argument("--skills", choices=("paired", *ARMS), default="paired",
                    help="'paired' runs both arms; a single arm is a probe, not a comparison")
    ap.add_argument("--max-calls", type=int,
                    help="hard ceiling on planned calls; required for a paid run")
    ap.add_argument("--max-invalid", type=int, default=6,
                    help="stop the run once this many calls come back invalid (default 6)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan and the input-size estimate, then exit without network")
    ap.add_argument("--yes", action="store_true",
                    help="required for a paid run; without it nothing reaches the network")
    ap.add_argument("--sandbox-selftest", action="store_true",
                    help="prove the container is confined and every shipped oracle runs inside it")
    return ap


def main() -> int:
    args = build_parser().parse_args()
    if args.sandbox_selftest:
        return sandbox_selftest()

    if not args.model or not args.effort:
        sys.exit("run_patch_eval: --model and --effort are required")
    if not 1 <= args.repeats <= MAX_REPEATS:
        sys.exit(f"run_patch_eval: --repeats must be between 1 and {MAX_REPEATS}")

    paid = args.yes and not args.dry_run
    if paid:
        require_clean_tree()
    commit = repo_commit()
    try:
        cases = freeze_cases(args.cases, args.model, args.effort, commit)
    except ContextError as exc:
        sys.exit(f"run_patch_eval: {exc}")

    arms = ARMS if args.skills == "paired" else (args.skills,)
    plan = schedule(cases, args.repeats, arms)
    calls = sum(len(ordered) for _, _, ordered in plan)

    print(f"paired patch eval   runner v{RUNNER_VERSION}   commit {commit[:12]}")
    print(f"model {args.model}   effort {args.effort}   repeats {args.repeats}   arms {list(arms)}")
    print(f"{len(cases)} case(s) x {args.repeats} repeat(s) x {len(arms)} arm(s) = {calls} call(s)")
    approx = sum(len(case.prompt(arm)) for case, _, ordered in plan for arm in ordered)
    print(f"approximate input size: ~{approx // 4000}k tokens across all calls "
          f"(rough: characters / 4, NOT an API count and NOT a price)")
    print(f"sandbox image: {SANDBOX_IMAGE}")
    starts = _tally(ordered[0] for _, _, ordered in plan if ordered)
    print("arm order: " + ", ".join(f"{v} {k}-first" for k, v in sorted(starts.items())))

    if not paid:
        for case, repeat, ordered in plan:
            print(f"  would run {case.id} repeat {repeat} arms {list(ordered)}")
        print("\nDry run: no network access was attempted." if args.dry_run else
              "\nNo --yes, so nothing was sent. A paid run needs --yes and --max-calls.")
        return 0

    if args.max_calls is None:
        sys.exit(f"run_patch_eval: a paid run requires an explicit --max-calls ceiling. The plan "
                 f"above is {calls} call(s).")
    if calls > args.max_calls:
        sys.exit(f"run_patch_eval: the plan is {calls} call(s) and --max-calls is "
                 f"{args.max_calls}. Raise the ceiling deliberately or narrow the selection.")
    if args.max_invalid < 1:
        sys.exit("run_patch_eval: --max-invalid must be at least 1")

    reason = sandbox_preflight()
    if reason is not None:
        sys.exit(f"run_patch_eval: {reason}. Grading executes model-written code, so it happens in "
                 f"the pinned container or it does not happen. There is no local fallback.")

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        sys.exit("run_patch_eval: OPENAI_API_KEY is not set. It is read only at this point, held "
                 "in memory, and never written anywhere.")

    run_id = secrets.token_hex(16)
    RUNS.mkdir(parents=True, exist_ok=True)
    run_dir = RUNS / f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{run_id[:8]}"
    run_dir.mkdir(mode=0o700)
    manifest = {
        "run_id": run_id, "runner_version": RUNNER_VERSION, "model": args.model,
        "effort": args.effort, "repeats": args.repeats, "arms": list(arms), "commit": commit,
        "sandbox_image": SANDBOX_IMAGE, "max_calls": args.max_calls,
        "max_invalid": args.max_invalid, "planned_calls": calls,
        "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "complete": False, "stopped_because": None,
        "cases": [{"id": c.id, "target_skill": c.target_skill,
                   "baseline_context": list(c.baseline_context),
                   "treatment_context": list(c.treatment_context),
                   "fixture_digest": c.fixture_digest, "prompt_digest": c.prompt_digest}
                  for c in cases],
    }
    write_private(run_dir / "manifest.json", json.dumps(manifest, indent=2))
    expected = {c.id: c.prompt_digest for c in cases}
    ids = tuple(c.id for c in cases)

    records: list[dict] = []
    invalid_seen = 0
    stopped = None
    for case, repeat, ordered in plan:
        if stopped:
            break
        for arm in ordered:
            status, frag = call_model(api_key, args.model, args.effort, case.prompt(arm))
            rec = {
                "run_id": run_id, "pair_key": pair_key(run_id, case, repeat),
                "case": case.id, "arm": arm, "repeat": repeat,
                "order": "-".join(arm_order(ids, case.id, repeat)),
                "target_skill": case.target_skill,
                "baseline_context": list(case.baseline_context),
                "treatment_context": list(case.treatment_context),
                "model": args.model, "effort": args.effort, "runner_version": RUNNER_VERSION,
                "commit": commit, "fixture_digest": case.fixture_digest,
                "prompt_digest": case.prompt_digest,
                "latency_s": frag.get("latency_s"), "attempts": frag.get("attempts"),
                "api_status": frag.get("api_status"),
                "input_tokens": frag.get("input_tokens"),
                "output_tokens": frag.get("output_tokens"),
                **blank_grade(),
            }
            if rec["prompt_digest"] != expected[case.id]:
                sys.exit("run_patch_eval: a record's prompt digest does not match the manifest; "
                         "refusing to write a run whose pairing cannot be trusted")
            if status != "ok":
                rec.update(outcome="invalid", reason=frag.get("reason", "unknown"))
                invalid_seen += 1
            else:
                rec.update(grade(case, frag["patch"]))
                rec["summary"] = frag.get("summary", "")   # recorded, never graded
                write_private(run_dir / f"{case.id}.{arm}.{repeat}.patch", frag["patch"])
            records.append(rec)
            write_private(run_dir / f"{case.id}.{arm}.{repeat}.json", json.dumps(rec, indent=2))
            print(f"  {case.id:<44} {arm:<9} r{repeat} -> {rec['outcome']:<7} "
                  f"{rec['reason'][:40]}")
            if invalid_seen >= args.max_invalid:
                stopped = (f"{invalid_seen} invalid call(s) reached the --max-invalid ceiling of "
                           f"{args.max_invalid}")
                print(f"\nSTOPPING: {stopped}. Records already written are kept.")
                break

    write_private(run_dir / "records.json", json.dumps(records, indent=2))
    manifest["complete"] = stopped is None
    manifest["stopped_because"] = stopped
    manifest["finished_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    write_private(run_dir / "manifest.json", json.dumps(manifest, indent=2))
    summarise(records, args.repeats, run_id, complete_run=stopped is None)
    print(f"\nrun directory: {run_dir}")
    return 0 if stopped is None else 1


if __name__ == "__main__":
    sys.exit(main())
