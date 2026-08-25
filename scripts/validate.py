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
  - AGENTS.md <= 2 KB: it carries routing discipline only, never financial rules.
  - references/ one level deep; any reference over 100 lines carries its own
    table of contents, because a partially-read file must still reveal structure.

Usage: scripts/validate.py [--quiet]
"""
from __future__ import annotations

import re
import sys

try:
    import yaml
except ImportError:  # strict path unavailable; the fallback below is weaker
    yaml = None
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
AGENTS_BUDGET = 2048   # routing reinforcement only; a rule layer cannot fit in 2 KB

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
    """Read the frontmatter, preferring a real YAML parser.

    The hand-rolled fallback exists so the validator runs with no dependencies, but
    it is deliberately NOT the primary path: it happily accepts frontmatter that a
    real YAML parser rejects. An unquoted description containing ": " parses fine
    here and blows up in every actual loader, which then falls back to empty
    metadata — the skill loads with no description to match against and effectively
    never triggers. That shipped once. Install PyYAML in CI so the strict path runs.
    """
    if yaml is not None:
        m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
        if not m:
            return None, "frontmatter must start on line 1 and be terminated by ---"
        try:
            data = yaml.safe_load(m.group(1))
        except yaml.YAMLError as e:
            first = str(e).splitlines()[0]
            return None, f"frontmatter is not valid YAML ({first}) — loaders fall back to EMPTY metadata"
        if not isinstance(data, dict):
            return None, "frontmatter must be a mapping"
        return {k: ("" if v is None else str(v)) for k, v in data.items()}, ""
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
        # v0.2: descriptions lead with the semantic router (what kind of correctness this is)
        # and carry the trigger clause after it, because a description that opens with a grep
        # pattern routes on spelling. The trigger clause still has to be there: a description
        # that never says when to use the skill leaves the agent to guess.
        if not re.search(r"(?i)\buse (it |them )?(when|alongside|for)\b|\btrigger\b", desc):
            warn("description never says when to use the skill — add a 'Use when …' clause")

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



FINANCIAL_CHECK_LABELS = ("tier", "effect", "identity", "ambiguity", "authority",
                          "recovery", "controls")

RULE_NAMES = (
    "the economic-diff gate",
    "implemented, not described",
    "a comment is a claim",
    "durable intent before the external effect",
    "arrival order is not occurrence order",
    "proven coverage before the cursor advances",
    "reconciliation runs in production",
)



def em_dashes_outside_quotes(text: str) -> list[int]:
    """Positions of em dashes that are the author's own punctuation.

    An em dash inside a quotation stays. Editing punctuation inside quoted text falsifies
    the quote, and this repo's whole argument is that a citation you cannot check is worth
    nothing. Six of these are real: BitGo's nonce-hole causes, Fireblocks'
    DROPPED_BY_BLOCKCHAIN sub-statuses, Chainlink's answeredInRound deprecation note, and
    Square Books on signed amounts.

    Fenced blocks and inline code spans are masked before counting quote marks, because a
    JSON example full of double quotes would otherwise flip the parity for the rest of the
    file and hide every later em dash.
    """
    masked = list(text)
    in_fence = False
    i = 0
    while i < len(text):
        if text.startswith("```", i):
            in_fence = not in_fence
            for k in range(i, min(i + 3, len(text))):
                masked[k] = " "
            i += 3
            continue
        if in_fence:
            if text[i] != "\n":
                masked[i] = " "
        elif text[i] == "`":
            j = text.find("`", i + 1)
            if j == -1:
                j = i
            for k in range(i, j + 1):
                if masked[k] != "\n":
                    masked[k] = " "
            i = j + 1
            continue
        i += 1

    out, open_quote = [], False
    for idx, ch in enumerate(masked):
        if ch == '"':
            open_quote = not open_quote
        elif ch == "—" and not open_quote:
            out.append(idx)
    return out

def check_prose(md: Path, binding: bool) -> None:
    """Style and vocabulary rules that survived a refactor and must not regress.

    The G1-G7 ids were retired in v0.2. They were opaque, they only resolved against an
    always-installed block, and a rule that needs an external glossary to be read is a rule
    that stops working when the glossary is absent. Rules are now cited by name.
    """
    text = md.read_text(encoding="utf-8")
    rel = md.relative_to(ROOT)
    report = err if binding else warn

    # G2-item and G-single are Adya isolation-anomaly names, not the retired rule ids.
    for m in re.finditer(r"(?<![A-Za-z0-9])G[1-7](?![0-9A-Za-z-])", text):
        line = text[: m.start()].count("\n") + 1
        report(f"{rel}:{line} cites the retired id {m.group(0)} — cite the rule by name")

    if binding and "install-guardrails" in text:
        line = text[: text.index("install-guardrails")].count("\n") + 1
        err(f"{rel}:{line} implies the guardrail install is needed — skills are self-sufficient")

    for pos in em_dashes_outside_quotes(text):
        line = text[:pos].count("\n") + 1
        report(f"{rel}:{line} contains an em dash")


def check_structure(skill_md: Path) -> None:
    """The workflow is the first thing an agent reads, so it is the first H2."""
    text = skill_md.read_text(encoding="utf-8")
    rel = skill_md.relative_to(ROOT)
    h2s = re.findall(r"(?m)^## (.+)$", text)
    if not h2s:
        err(f"{rel} has no H2 sections")
        return
    if h2s[0].strip().lower() != "workflow":
        err(f"{rel} opens with '## {h2s[0]}' — the first H2 must be '## Workflow', "
            f"because it is what tells the agent what to do")
    if "FINANCIAL CHECK" not in text:
        err(f"{rel} never shows the FINANCIAL CHECK block — it is the default output at T0 and T1")
    else:
        # Eight independent rewrites converged on this block. Pin it: a reader who has two
        # skills loaded must not be shown two different default output contracts, and `tier`
        # has to be emitted because it is what gates the T2+ escalation.
        block = text.split("FINANCIAL CHECK\n", 1)[1].split("```", 1)[0]
        labels = re.findall(r"(?m)^([a-z]+):", block)
        if labels != list(FINANCIAL_CHECK_LABELS):
            err(f"{rel} FINANCIAL CHECK labels are {labels}, expected "
                f"{list(FINANCIAL_CHECK_LABELS)}")
        if "UNRESOLVED: <control> (<why>)" not in block:
            err(f"{rel} FINANCIAL CHECK is missing the UNRESOLVED form "
                f"'UNRESOLVED: <control> (<why>)' — a described control with no location "
                f"is the defect this line exists to catch")


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

    for md in binding:
        check_prose(md, binding=True)
    for ref in sorted(ROOT.glob("skills/*/references/**/*.md")):
        check_prose(ref, binding=True)
    for skill_md in skills:
        check_structure(skill_md)
    for md in sorted((ROOT / "docs").glob("*.md")) + sorted(ROOT.glob("examples/**/*.md")) \
            + [ROOT / "README.md"]:
        if md.is_file():
            check_prose(md, binding=False)

    agents = ROOT / "AGENTS.md"
    if not agents.is_file():
        err("AGENTS.md is missing — it carries the optional routing block")
    else:
        size = agents.stat().st_size
        print(f"  AGENTS.md: {size} bytes (budget {AGENTS_BUDGET})")
        if size > AGENTS_BUDGET:
            err("AGENTS.md exceeds 2KB — substantive rules belong in the skills, not in an\n            always-installed block. Move the content into the skill that owns it.")

    print()
    if errors:
        print(f"{RED}FAILED{OFF} — {len(errors)} error(s), {len(warnings)} warning(s)")
        return 1
    print(f"{GRN}PASS{OFF} — {len(warnings)} warning(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
