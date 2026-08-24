#!/usr/bin/env python3
"""Validate the skill suite against the Agent Skills spec and this repo's budgets.

Spec limits enforced (agentskills.io/specification):
  - name: 1-64 chars, ^[a-z0-9]([a-z0-9-]*[a-z0-9])?$, no consecutive hyphens,
    must equal the parent directory, no reserved words.
  - description: 1-1024 chars, no XML-like tags.
  - Legal frontmatter keys only. Any other key hard-fails package_skill.py,
    claude.ai upload, and the Skills API.

Repo budgets (docs/architecture.md):
  - SKILL.md <= 500 lines; description <= 430 chars; suite total <= 3000 chars,
    because the skill listing budget is shared with every other suite installed.
  - AGENTS.md <= 8 KB: it is in context on every turn.
  - references/ one level deep; any reference over 100 lines carries its own
    table of contents, because a partially-read file must still reveal structure.

Usage: scripts/validate.py [--quiet]
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEGAL_KEYS = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
RESERVED = ("anthropic", "claude")
MAX_SKILL_LINES = 500
MAX_DESC = 430
SPEC_MAX_DESC = 1024
TOTAL_DESC_BUDGET = 3000
MIN_REF_LINES = 120
MIN_REF_PROSE_WORDS = 400
AGENTS_BUDGET = 8192

RED, YEL, GRN, BLD, OFF = "\033[31m", "\033[33m", "\033[32m", "\033[1m", "\033[0m"
if not sys.stdout.isatty():
    RED = YEL = GRN = BLD = OFF = ""

errors: list[str] = []
warnings: list[str] = []


def err(msg: str) -> None:
    errors.append(msg)
    print(f"  {RED}FAIL{OFF} {msg}")


def warn(msg: str) -> None:
    warnings.append(msg)
    print(f"  {YEL}WARN{OFF} {msg}")


def parse_frontmatter(text: str) -> tuple[dict[str, str], str] | tuple[None, str]:
    """Minimal YAML frontmatter reader: top-level scalars and folded scalars only."""
    if not text.startswith("---\n"):
        return None, "frontmatter must start on line 1"
    end = text.find("\n---", 3)
    if end == -1:
        return None, "unterminated frontmatter"
    body = text[4:end]
    fields: dict[str, str] = {}
    key = None
    for raw in body.split("\n"):
        m = re.match(r"^([A-Za-z][A-Za-z0-9_-]*):[ \t]*(.*)$", raw)
        if m:
            key = m.group(1)
            fields[key] = m.group(2).strip()
        elif key and raw.strip() and raw[:1] in " \t":
            fields[key] = (fields[key] + " " + raw.strip()).strip()
    for k, v in fields.items():
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            fields[k] = v[1:-1]
    return fields, ""


def check_skill(skill_md: Path) -> int:
    """Validate one SKILL.md. Returns the description length for the suite budget."""
    directory = skill_md.parent.name
    print(f"\n{BLD}{directory}{OFF}")
    text = skill_md.read_text(encoding="utf-8")
    fields, problem = parse_frontmatter(text)
    if fields is None:
        err(problem)
        return 0

    for k in fields:
        if k not in LEGAL_KEYS:
            err(f"illegal frontmatter key {k!r} — hard-fails packaging and the Skills API")

    name = fields.get("name", "")
    if not name:
        err("missing required field: name")
    else:
        if name != directory:
            err(f"name {name!r} != directory {directory!r}")
        if len(name) > 64:
            err(f"name is {len(name)} chars (max 64)")
        if not re.fullmatch(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?", name):
            err("name must match ^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")
        if "--" in name:
            err("name contains consecutive hyphens")
        for word in RESERVED:
            if word in name:
                err(f"name contains reserved word {word!r}")

    desc = fields.get("description", "")
    if not desc:
        err("missing required field: description")
    else:
        n = len(desc)
        if n > SPEC_MAX_DESC:
            err(f"description is {n} chars (spec max {SPEC_MAX_DESC})")
        elif n > MAX_DESC:
            warn(f"description is {n} chars (repo budget {MAX_DESC})")
        if re.search(r"<[A-Za-z/][^>]*>", desc):
            err("description contains XML-like tags (rejected by Claude-side loaders)")
        if not re.match(r"(?i)^(use when|trigger)", desc):
            warn("description should lead with trigger conditions ('Use when …' / 'TRIGGER …')")

    lines = text.count("\n") + 1
    if lines > MAX_SKILL_LINES:
        err(f"SKILL.md is {lines} lines (max {MAX_SKILL_LINES})")

    if re.search(r"(?:^|[^\w])@(?:skills|references)/", text):
        err("contains an @-reference — force-loads the file and burns context")

    for ref in sorted(set(re.findall(r"\]\((references/[^)]+\.md)\)", text))):
        target = skill_md.parent / ref
        if not target.is_file():
            err(f"broken reference: {ref}")
            continue
        if ref.count("/") > 2:
            warn(f"reference nested {ref.count('/')} deep: {ref} — deep chains get partially read")
        rtext = target.read_text(encoding="utf-8")
        rlines = rtext.count("\n") + 1
        if rlines > 100:
            head = "\n".join(rtext.split("\n")[:40])
            if not re.search(r"(?i)(##\s*)?(contents|table of contents)", head):
                warn(f"{ref} is {rlines} lines with no table of contents in its first 40 lines")
        # A contents-only stub passes every structural check while delivering nothing.
        # The dispatch line in SKILL.md promises the agent this file answers the question;
        # an outline does not. Require prose under the headings, not just headings.
        if rlines < MIN_REF_LINES:
            body = re.sub(r"(?m)^\s*(#{1,6}\s.*|[-*]\s.*|\|.*)$", "", rtext)
            if len(body.split()) < MIN_REF_PROSE_WORDS:
                err(
                    f"{ref} is a contents-only stub ({rlines} lines, "
                    f"{len(body.split())} words of prose) — SKILL.md points an agent at it"
                )

    # Every reference file on disk should be reachable from its SKILL.md.
    refs_on_disk = {
        str(p.relative_to(skill_md.parent))
        for p in (skill_md.parent / "references").rglob("*.md")
    } if (skill_md.parent / "references").is_dir() else set()
    linked = set(re.findall(r"\]\((references/[^)]+\.md)\)", text))
    for orphan in sorted(refs_on_disk - linked):
        warn(f"unreferenced file: {orphan} — nothing points an agent at it")

    if not errors:
        print(f"  {GRN}ok{OFF}   {lines} lines, description {len(desc)} chars")
    return len(desc)


def main() -> int:
    skills = sorted((ROOT / "skills").glob("*/SKILL.md"))
    if not skills:
        print(f"{RED}no skills found under skills/{OFF}")
        return 1

    total_desc = sum(check_skill(s) for s in skills)

    print(f"\n{BLD}suite{OFF}")
    print(f"  skills: {len(skills)}")
    print(f"  total description chars: {total_desc} (budget {TOTAL_DESC_BUDGET})")
    if total_desc > TOTAL_DESC_BUDGET:
        err("total description budget exceeded — the listing is shared with every other installed suite")

    # A skill that cites a repo path or a CI job which does not exist is making a
    # promise the artefact does not keep. This shipped once (shared/seams/, a seam-diff
    # CI job, both fictional) and was only caught by an external reviewer.
    # In skills/ and AGENTS.md a dangling path misleads an agent mid-task, so it is an
    # error. In docs/ it is usually a roadmap proposal, so it is a warning the maintainer
    # must keep honest. This shipped once (shared/seams/ and a seam-diff CI job, both
    # fictional, asserted as fact inside SKILL.md files) and only an external reviewer caught it.
    def cited_paths(md: Path) -> set[str]:
        found = set()
        for path in re.findall(r"`((?:shared|scripts|evals|skills|docs)/[A-Za-z0-9_./<>-]+)`",
                               md.read_text(encoding="utf-8")):
            if "<" in path or ">" in path:   # a template, not a literal path
                continue
            if not (ROOT / path).exists():
                found.add(path)
        return found

    binding = sorted(ROOT.glob("skills/*/SKILL.md"))
    if (ROOT / "AGENTS.md").is_file():
        binding.append(ROOT / "AGENTS.md")
    for md in binding:
        for path in sorted(cited_paths(md)):
            err(f"cited path does not exist: {path} (in {md.relative_to(ROOT)})")
    for md in sorted((ROOT / "docs").glob("*.md")):
        for path in sorted(cited_paths(md)):
            warn(f"cited path does not exist: {path} (in {md.relative_to(ROOT)}) — mark it as proposed")

    agents = ROOT / "AGENTS.md"
    if not agents.is_file():
        err("AGENTS.md is missing — the always-on layer is the highest-value artefact")
    else:
        size = agents.stat().st_size
        print(f"  AGENTS.md: {size} bytes (budget {AGENTS_BUDGET})")
        if size > AGENTS_BUDGET:
            err("AGENTS.md exceeds 8KB — it is in context on every turn")

    print()
    if errors:
        print(f"{RED}FAILED{OFF} — {len(errors)} error(s), {len(warnings)} warning(s)")
        return 1
    print(f"{GRN}PASS{OFF} — {len(warnings)} warning(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
