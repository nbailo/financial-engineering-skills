#!/usr/bin/env python3
"""Validate the skill suite against the Agent Skills spec and this repo's budgets.

Spec limits enforced (agentskills.io/specification):
  - name: 1-64 chars, ^[a-z0-9]([a-z0-9-]*[a-z0-9])?$, no consecutive hyphens,
    must equal the parent directory, no reserved words.
  - description: 1-1024 chars, no XML-like tags.
  - Legal frontmatter keys only. Any other key hard-fails package_skill.py,
    claude.ai upload, and the Skills API.

Repo budgets. The BUDGET POLICY block below is the only statement of these numbers; nothing
restates them, because a restated budget is a claim that rots:
  - SKILL.md line cap, because a SKILL.md is loaded whole on every activation.
  - Per-description and suite-total caps, because the skill listing budget is shared
    with every other suite the user has installed.
  - AGENTS.md size cap: it carries routing discipline only, never financial rules.
  - skills/ holds exactly the six installed skills; advanced/ is not part of the product,
    and is validated the same way, with its descriptions excluded from the installed
    listing budget and its LOADED PATHS measured against the same canonical band.
  - references/ at most one directory level deep; any reference over 100 lines carries its
    own table of contents, because a partially-read file must still reveal structure.
  - Every provider or protocol reference carries a provenance block: provider, surface,
    version, a verified_at that is either a date which parses and is not in the future or
    the literal "not established", at least one source URL, and a revalidate_when trigger.
    An undated block must also carry an unverified: line, since that line is then the only
    statement of what its claims rest on. PROVENANCE_DEBT, the list of files that predate
    the rule, is empty; a new provider reference with no block is an error.
  - The reference web: no orphans, no trigger row pointing at a missing file, no backticked
    filename that resolves nowhere, no in-file anchor that resolves to no heading, and no
    reference pointing at another reference.
  - Trigger rows: one row per reference, no duplicate target, no two rows a router cannot
    tell apart, and link text that names the file it links to.
  - No hidden control characters, and no agent or editor artefacts in the shipped tree.
    Installing a skill copies a directory; it does not run `git archive`, so a .gitignore
    entry does not stop a stray file from shipping.

Usage:
  scripts/validate.py                       structure, budgets, provenance, reference web
  scripts/validate.py --strict              promote the documented debt to errors, including
                                            every loaded path held by the ratchet
  scripts/validate.py --provenance-report [--max-age-days N]
                                            the provider drift report: classify every
                                            provenance block into one of three states and
                                            exit non-zero when a REQUIRED source is past its
                                            revalidation trigger. Edits nothing, ever.
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
MAX_SKILL_PROSE_LINES = 210  # what an agent actually reads: everything but the trigger table
MAX_SKILL_LINES = 300        # hard ceiling on the whole file, trigger table included
MAX_DESC = 430
SPEC_MAX_DESC = 1024
TOTAL_DESC_BUDGET = 2600
MIN_REF_LINES = 120
MIN_REF_PROSE_WORDS = 400
AGENTS_BUDGET = 2048   # routing reinforcement only; a rule layer cannot fit in 2 KB
DEV_VERSION = "0.0.0"  # a tree between releases; pairs with a '## Unreleased' changelog entry

# ══════════════════════════════════════════════════════════════════════════════════════
# BUDGET POLICY, canonical. One statement for the whole repo; nothing restates it.
# ══════════════════════════════════════════════════════════════════════════════════════
#
# WHAT IS MEASURED. Not a directory. A LOADED PATH: the tokens an agent actually pulls into
# its context to answer one task, which is SKILL.md plus the references that task triggers.
# Summing a directory measures material nobody loads; summing SKILL.md alone ignores the
# reference it exists to dispatch to. Neither number is the cost the user pays.
#
# WHERE THE REPRESENTATIVE TASK COMES FROM. It is not invented here. Each path is a case in
# evals/routing-cases.yaml that names a skill and the references that skill should open for
# it, so the budget is measured against a task the routing lint already asserts is real. A
# skill with no such case has no measurable loaded path, and that is an error rather than a
# silent pass: an unmeasured budget is not a budget.
#
# THE NUMBERS.
#   Entry skills (skills/*) stay compact and are governed by the line caps above, which bound
#   the file that loads on every activation. Their loaded paths are measured and printed, so
#   the cost of the installed product is visible on every run.
#
#   Advanced paths (advanced/*) target the band below. advanced/ is opt-in and never appears
#   in the installed listing, so its descriptions stay out of TOTAL_DESC_BUDGET: they cost
#   nothing until somebody copies the directory in. Once copied in they cost context like any
#   other file, and an opt-in file is the only file a reader gets for that domain, so the band
#   applies to them exactly as it would to an installed one.
LOADED_PATH_TARGET_MIN = 2500
LOADED_PATH_TARGET_MAX = 3500          # the target every path should aim at
LOADED_PATH_ADVANCED_CEILING = 5000    # the hard gate for an opt-in advanced path

# Why the advanced ceiling is not the target, with the arithmetic that forced it.
#
# A loaded path costs: SKILL.md prose + one dispatch row per reference + the one reference
# the task opens. With C tokens of reference content spread over N files and ~32 tokens per
# dispatch row, that is prose + 32N + C/N, which is minimised at N = sqrt(C/32) and has a
# floor of prose + 2*sqrt(32C). No decomposition beats that floor; splitting further just
# moves cost from the reference into the table.
#
# fin-market-data-publication has C small enough to land inside 3500, and it does.
# fin-matching-engine carries C = 45,268 tokens of sourced evidence, giving a floor of
# ~4,291. The measured mean after splitting into 36 references is 4,307, which is 0.4% off
# the computed floor: the decomposition is already optimal, not lazy. Reaching 3500 would
# require C <= ~20,400, i.e. deleting about 55% of the sourced evidence, and the only
# material large enough to matter is provenance and worked incident citation.
#
# So: 3500 stays the target and is met where the evidence base allows. Advanced paths gate
# at 5000, and LOADED_PATH_RATCHET still forbids growth. The alternative considered and
# rejected was splitting fin-matching-engine into two advanced skills, which would reach the
# band but adds a product abstraction the release contract forbids.

# THE RATCHET, and what it admits. Every advanced path is far over the band today, by a factor
# of three to five. The material predates the policy, and cutting a sourced protocol reference
# by two thirds is a content decision rather than a validator's, so the gap is recorded here
# instead of being enforced away or hidden.
#
# Each entry is a CEILING THAT MAY ONLY FALL: the path's measured cost on 2026-08-25, rounded
# up to the next 500 estimated tokens. The rounding is the tolerance for an editorial pass, and
# 500 tokens is roughly two kilobytes of prose, so growth that clears it is a real addition
# rather than a rewritten paragraph. A path with no entry here is held to the band, not to a
# ceiling of its own. A recorded path that grows past its ceiling fails the run. Every run
# prints how far over the BAND each recorded path is, never how far under its ceiling, so a
# ratcheted path is never mistaken for a compliant one, and --strict fails on all of them.
# Deleting an entry is the only way to close one, and deleting it requires the path to fit.
LOADED_PATH_RATCHET: dict[str, int] = {
    # case id in evals/routing-cases.yaml -> ceiling in estimated tokens.
    # Measured 2026-08-25 and rounded up to the next 500; the comment carries the raw
    # measurement, so a re-measure that has moved is visible without running anything.
    # fin-matching-engine, re-measured after the reference decomposition: SKILL.md cut from 6,632 to
    # 3,050 estimated tokens, and its three references split into thirty-six narrow ones. Still over
    # the band, and by how much is printed on every run; the ceilings below only record that the debt
    # is now a quarter of what it was.
    'matching-engine-core':             4800,  # 4,707 = SKILL.md 3,050 + pro-rata-residue 1,657
    'matching-engine-journal-replay':   4000,  # 3,986 = SKILL.md 3,050 + failover-fencing 936
    'matching-engine-risk-halts':       4300,  # 4,279 = SKILL.md 3,050 + busts-and-corrections 1,229
    'market-data-publisher':           3500,  # 9,237 = SKILL.md 6,785 + feed-specification 2,452
    'market-data-itch-sequencing':     3500,  # 12,284 = SKILL.md 6,785 + nasdaq-itch-and-moldudp64 5,499
    'market-data-cme-recovery':        3500,  # 10,794 = SKILL.md 6,785 + cme-mdp-recovery 4,009
    'market-data-conflation':          3300,  # 10,813 = SKILL.md 6,785 + conflation-and-backpressure 4,028
    'market-data-fix-session':         3300,  # 10,036 = SKILL.md 6,785 + fix-session-sequencing 3,251
    'market-data-reg-nms-fairness':    3500,  # 9,922 = SKILL.md 6,785 + us-reg-nms-timing-fairness 3,137
    'market-data-emit-assertions':     3400,  # 10,996 = SKILL.md 6,785 + emit-path-assertions 4,211
}
# ══════════════════════════════════════════════════════════════════════════════════════

# Evidence goes stale in silence. This is the age at which --provenance-report calls a block
# out for revalidation. The report never edits a fact; a stale date is a prompt to re-read
# the source, and only a human re-read may move verified_at.
PROVENANCE_MAX_AGE_DAYS = 90

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
    metadata, and the skill loads with no description to match against and effectively
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
            return None, f"frontmatter is not valid YAML ({first}); loaders fall back to EMPTY metadata"
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


def check_skill(skill_md: Path, installed: bool = True) -> int:
    """Validate one SKILL.md. Returns the description length for the suite budget.

    The two advanced skills go through exactly this function. They shipped an invariant
    citation naming a money-core rule that does not exist, and it survived review precisely
    because advanced/ sat outside every structural check. Same shape, same frontmatter rules,
    same citation resolution; only the size ceilings and the description budget differ, and
    an advanced description is returned to the caller which then discards it.
    """
    directory = skill_md.parent.name
    print(f"\n{BLD}{directory}{OFF}" + ("" if installed else f" {YEL}(advanced, opt-in){OFF}"))
    text = skill_md.read_text(encoding="utf-8")
    fields, problem = parse_frontmatter(text)
    if fields is None:
        err(problem)
        return 0

    for k in fields:
        if k not in LEGAL_KEYS:
            err(f"illegal frontmatter key {k!r}: hard-fails packaging and the Skills API")

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
            warn("description never says when to use the skill; add a 'Use when …' clause")

    lines = text.count("\n") + 1
    if installed and lines > MAX_SKILL_LINES:
        err(f"SKILL.md is {lines} lines (max {MAX_SKILL_LINES})")
    # An advanced SKILL.md has no line cap of its own. What it costs is charged where the cost
    # is actually paid, in check_loaded_paths, against the one canonical band.

    # The budget exists because a SKILL.md loads whole, but a trigger row and a line of prose
    # are not the same cost. A row is scanned once to decide what to open; prose is read.
    # Counting them together made splitting a reference compete with explaining a rule, and a
    # skill answered by compressing its prose. Budget them separately so that stops happening.
    prose = [ln for ln in text.split("\n")
             if not re.match(r"^\s*\|?\s*[-*]?\s*\[[^\]]+\]\((?:\.\./)*references/", ln)]
    if installed and len(prose) > MAX_SKILL_PROSE_LINES:
        err(f"SKILL.md has {len(prose)} lines outside the trigger table "
            f"(max {MAX_SKILL_PROSE_LINES})")

    if re.search(r"(?:^|[^\w])@(?:skills|references)/", text):
        err("contains an @-reference: it force-loads the file and burns context")

    for ref in sorted(set(re.findall(r"\]\((references/[^)]+\.md)\)", text))):
        target = skill_md.parent / ref
        if not target.is_file():
            continue        # check_reference_web owns missing targets, and says so once
        if ".." in ref.split("/"):
            err(f"trigger row reaches outside the skill with {ref}; an installed copy carries "
                f"one skill directory and the link would dangle")
        rtext = target.read_text(encoding="utf-8")
        rlines = rtext.count("\n") + 1
        if rlines > 100:
            # 60 lines, not 40: a provenance block legitimately runs twelve wrapped lines
            # before the prose starts, and the contents belong after it, not above it.
            head = "\n".join(rtext.split("\n")[:60])
            if not re.search(r"(?i)(##\s*)?(contents|table of contents)", head):
                warn(f"{ref} is {rlines} lines with no table of contents in its first 60 lines")
        # A contents-only stub passes every structural check while delivering nothing.
        # The dispatch line in SKILL.md promises the agent this file answers the question;
        # an outline does not. Require prose under the headings, not just headings.
        if rlines < MIN_REF_LINES:
            body = re.sub(r"(?m)^\s*(#{1,6}\s.*|[-*]\s.*|\|.*)$", "", rtext)
            if len(body.split()) < MIN_REF_PROSE_WORDS:
                err(
                    f"{ref} is a contents-only stub ({rlines} lines, "
                    f"{len(body.split())} words of prose); SKILL.md points an agent at it"
                )

    # Orphans and missing trigger targets are check_reference_web's job: it sees every
    # skill at once, so it can also answer the questions one skill cannot, namely whether a
    # reference points at another reference and whether those pointers form a cycle.

    if not errors:
        print(f"  {GRN}ok{OFF}   {lines} lines, description {len(desc)} chars")
    return len(desc)



SECTIONS = ("When to use", "When not to", "Workflow", "Invariants", "References", "Output")

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
        report(f"{rel}:{line} cites the retired id {m.group(0)}; cite the rule by name")

    if binding and "install-guardrails" in text:
        line = text[: text.index("install-guardrails")].count("\n") + 1
        err(f"{rel}:{line} implies the guardrail install is needed; skills are self-sufficient")

    for pos in em_dashes_outside_quotes(text):
        line = text[:pos].count("\n") + 1
        report(f"{rel}:{line} contains an em dash")



def check_invariant_citations(skill_md: Path) -> None:
    """A domain skill cites a money-core invariant by name and adds only the specialisation.

    The hierarchy only works if the name resolves. A citation of a rule fin-money-core no longer
    states sends the reader to nothing, and it is invisible: the prose still reads correctly.
    """
    if skill_md.parent.name == "fin-money-core":
        return
    core = ROOT / "skills" / "fin-money-core" / "SKILL.md"
    if not core.is_file():
        return

    def norm(s: str) -> str:
        return " ".join(s.split()).strip().rstrip(".:,").lower()

    known = {norm(n) for n in re.findall(r"\*\*([A-Za-z][^*]{2,60})\*\*",
                                        core.read_text(encoding="utf-8"))}
    text = skill_md.read_text(encoding="utf-8")
    for m in re.finditer(r"[Ss]pecialises \*([^*]+)\*", text):
        if norm(m.group(1)) not in known:
            line = text[: m.start()].count("\n") + 1
            err(f"{skill_md.relative_to(ROOT)}:{line} cites *{' '.join(m.group(1).split())}*, "
                f"which fin-money-core does not state")

def check_structure(skill_md: Path) -> None:
    """The six installed skills share one shape, so a reader with two of them loaded is never
    guessing which section answers what.

    Routing comes before the workflow on purpose: if the skill does not apply, everything after
    the second section is wasted context, and "When not to" is what sends the agent elsewhere.
    """
    text = skill_md.read_text(encoding="utf-8")
    rel = skill_md.relative_to(ROOT)
    h2s = [h.strip() for h in re.findall(r"(?m)^## (.+)$", text)]
    if h2s != list(SECTIONS):
        err(f"{rel} sections are {h2s}, expected {list(SECTIONS)}")

    if "FINANCIAL CHECK" in text:
        err(f"{rel} still shows the retired FINANCIAL CHECK block. v0.3 output is one entry per real "
            f"finding (FINDING / WHY / EVIDENCE / FIX / TEST), emitted only where a finding exists")
    for label in ("FINDING", "EVIDENCE", "FIX"):
        if label not in text:
            err(f"{rel} never shows the {label} line of the default output")
    if re.search(r"(?m)^\s*\|?\s*T[0-3]\b", text) or "Financial tier:" in text:
        err(f"{rel} still uses the T0-T3 tier scale. v0.3 reports two orthogonal fields: "
            f"authority (EXTERNAL|SELF) and exposure (own|customer|record)")



def check_repo_consistency() -> None:
    """Cheap structural checks that stop documentation drifting away from the code.

    This repo tells agents that a comment is a claim. These apply the same rule to the
    repo: every restated budget, version and product-shape claim must match what the code
    enforces and what the tree actually contains. Nothing here tries to judge financial
    correctness; that is not a job for regex.
    """
    import json

    plugin_p = ROOT / ".claude-plugin" / "plugin.json"
    market_p = ROOT / ".claude-plugin" / "marketplace.json"
    installed = sorted(p.parent.name for p in (ROOT / "skills").glob("*/SKILL.md"))
    advanced = sorted(p.name for p in (ROOT / "advanced").glob("*/") if p.is_dir())

    version = None
    if plugin_p.is_file():
        plugin = json.loads(plugin_p.read_text(encoding="utf-8"))
        version = plugin.get("version")
        if not version:
            err("plugin.json declares no version")
        # The advanced skill is deliberately not installed, so advertising it in the
        # plugin or marketplace description promises something the install does not give.
        for label, path in (("plugin.json", plugin_p), ("marketplace.json", market_p)):
            if not path.is_file():
                continue
            blob = path.read_text(encoding="utf-8").lower()
            for adv in advanced:
                bare = adv.replace("fin-", "").replace("-", " ")
                if adv in blob or bare in blob:
                    err(f"{label} advertises '{adv}', which lives in advanced/ and is not installed")

    # The changelog's top entry is a version claim like any other: it says what this tree is.
    # A tree whose changelog announces a release its plugin manifest has not reached ships as
    # the older version and documents itself as the newer one.
    #
    # A tree between releases states that in both places at once: plugin.json carries DEV_VERSION
    # and the newest changelog entry is '## Unreleased'. Either half alone is the same defect in
    # a new direction. DEV_VERSION under a version heading lets an unreleased tree name a release
    # it never cut; a real version under 'Unreleased' hides a shipped version behind a placeholder.
    changelog = ROOT / "CHANGELOG.md"
    if version and changelog.is_file():
        body = changelog.read_text(encoding="utf-8")
        top = re.search(r"(?m)^##\s+(.+?)\s*$", body)
        unreleased = top is not None and top.group(1).strip().lower() == "unreleased"
        headings = re.findall(r"(?m)^##\s+\[?v?(\d+\.\d+\.\d+)\]?", body)
        if version == DEV_VERSION:
            if not unreleased:
                err(f"plugin.json says {DEV_VERSION}, so CHANGELOG.md's newest entry must be "
                    f"'## Unreleased'")
        elif unreleased:
            err(f"CHANGELOG.md's newest entry is Unreleased, plugin.json says {version}")
        elif not headings:
            err("CHANGELOG.md has no version heading, so nothing states what this tree is")
        elif headings[0] != version:
            err(f"CHANGELOG.md's newest entry is v{headings[0]}, plugin.json says {version}")
        seen_versions: list[str] = []
        for got in headings:
            if got in seen_versions:
                err(f"CHANGELOG.md lists v{got} twice")
            seen_versions.append(got)

    # A version stated anywhere else must agree with the plugin's. This stays on under
    # DEV_VERSION, where it is the check that catches a 'v0.4.2' left in prose after the tag it
    # named stopped existing. Naming a target release in prose therefore drops the v: 'a 1.0.0
    # release' is a plan, 'v1.0.0' is a claim that this tree is one.
    if version:
        for md in [ROOT / "README.md"] + sorted((ROOT / "docs").glob("*.md")):
            if not md.is_file():
                continue
            body = md.read_text(encoding="utf-8")
            for m in re.finditer(r"\bv(\d+\.\d+\.\d+)\b", body):
                if m.group(1) != version:
                    line = body[: m.start()].count("\n") + 1
                    err(f"{md.relative_to(ROOT)}:{line} says v{m.group(1)}, "
                        f"plugin.json says {version}")

    # Restated budget numbers must equal the constants they describe.
    budgets = {
        r"(\d+)\s+lines per `?SKILL\.md": MAX_SKILL_PROSE_LINES,
        r"(\d[\d,]*)\s+(?:chars|characters) across the suite": TOTAL_DESC_BUDGET,
        r"(\d+)\s+characters per description": MAX_DESC,
    }
    for md in [ROOT / "README.md"] + sorted((ROOT / "docs").glob("*.md")):
        if not md.is_file():
            continue
        body = md.read_text(encoding="utf-8")
        for rx, expected in budgets.items():
            for m in re.finditer(rx, body):
                got = int(m.group(1).replace(",", ""))
                if got != expected:
                    line = body[: m.start()].count("\n") + 1
                    err(f"{md.relative_to(ROOT)}:{line} states {got} where the validator "
                        f"enforces {expected}")

    # One canonical failure taxonomy. Two id schemes in one document is two taxonomies.
    tax = ROOT / "docs" / "failure-taxonomy.md"
    if tax.is_file():
        body = tax.read_text(encoding="utf-8")
        # Class ids appear both as headings and as bold lead-ins, so scan both: the second
        # scheme hid in the bold leads while the headings looked consistent.
        schemes = {m.group(1) for m in
                   re.finditer(r"(?m)^(?:#{2,4}\s+\**|\*\*)([A-Z])\d+[ .\u00b7:]", body)}
        if len(schemes) > 1:
            err(f"docs/failure-taxonomy.md runs {len(schemes)} id schemes "
                f"({', '.join(sorted(schemes))}); exactly one is canonical")

    # The one-hop rule moved to check_reference_web, which applies it to advanced/ too and
    # sees plain-text pointers, not only markdown links.


# ----------------------------------------------------------------------------------------
# Provenance, the reference web, and the advanced skills.
#
# Everything below answers one question the earlier checks do not: can a reader tell where a
# claim came from, and can an agent reach the file a trigger row promises it? A reference
# nothing points at is dead weight nobody loads. A trigger row pointing at a missing file is
# a promise the artefact does not keep. A dated vendor claim with no source is a fact that
# cannot be rechecked, which is the failure this whole repo argues against.
# ----------------------------------------------------------------------------------------

# Fields the spec requires at the top of a provider or protocol reference. `pinned`,
# `verified` and `unverified` are strongly encouraged and reported when absent, but they are
# not required here: a file can honestly have nothing pinned. The six below always apply.
PROVENANCE_REQUIRED = ("provider", "surface", "version", "verified_at", "sources",
                       "revalidate_when")
PROVENANCE_EXPECTED = ("verified", "unverified")

# The one non-date `verified_at` value this validator accepts, and it is accepted because the
# alternative is worse. A file written before the provenance requirement has real sources
# behind it and no date anybody can stand behind; the only two ways to give it a block are to
# invent a date or to say plainly that there is none. Inventing one is the failure this whole
# check exists to prevent, so the literal below is a valid, explicit non-answer: it parses, it
# never expires into a false freshness, and --provenance-report lists it as carrying no date
# rather than as young evidence. It buys nothing else. A block using it still has to name its
# sources and its revalidation trigger, and it must additionally carry an `unverified:` line,
# because with no date that line is the only place the reader learns what the file's claims
# rest on. Any other unparseable value is still an error.
PROVENANCE_NO_DATE = "not established"

# A reference is provider-specific when it makes dated claims about one named vendor or wire
# protocol. Detection reads the file STEM and its H1 only. Body text is deliberately not
# scanned: a cross-venue reference names four venues by design, and scanning bodies would
# demand a Polymarket provenance block from a file whose whole point is that it is not about
# Polymarket. The stem and the title are where an author declares what a file is about.
PROVIDER_TOKENS = (
    "binance", "okx", "bybit", "kraken", "coinbase", "deribit", "hyperliquid", "alpaca",
    "polymarket", "kalshi", "limitless", "ccxt", "fix", "ouch", "itch", "moldudp64",
    "cme", "mdp", "nasdaq", "iso20022", "stripe", "adyen", "paypal", "square",
    "tigerbeetle", "formance", "fireblocks", "bitgo", "chainlink", "erc20", "evm",
    "solana", "xrpl", "utxo", "hip4", "reg-nms",
)
_PROVIDER_RX = re.compile(r"(?<![a-z0-9])(" + "|".join(PROVIDER_TOKENS) + r")(?![a-z0-9])")

# Provenance debt. Empty since 2026-08-25, when the last eighteen files on it gained blocks.
# Three of those blocks carry a real verified_at, because their sources were opened that day:
# the ccxt reference against a pinned commit, the order-entry FIX reference against the FIX 4.4
# dictionary and Binance's own FIX document, and the Regulation NMS file against the SEC order
# and the current CFR text. The other fifteen carry `verified_at: not established`, which is
# the honest answer for material written from real sources in an earlier pass and re-read by
# nobody since. An empty set is the point of the list, not a bug in it: a NEW provider
# reference with no block is now an error, with no backlog to hide in. Re-adding a path here
# would need the same justification the original entries had, which is that the file already
# shipped before the rule existed.
PROVENANCE_DEBT: frozenset[str] = frozenset()

# Backticked `*.md` names that are NOT files in this repo and never will be. Two groups, both
# load-bearing: third-party documentation pages cited by their filename, and this repo's own
# research notes, which live under a gitignored .research/ and are absent from a clone. An
# unresolvable name outside this set is a stale pointer, which is how a split file leaves
# prose aimed at a path that no longer exists.
EXTERNAL_DOC_MENTIONS = frozenset({
    # Binance spot API documentation, cited by the filename each page carries in its repo.
    "errors.md", "enums.md", "rest-api.md", "filters.md", "fix-api.md",
    "web-socket-streams.md", "faqs/stp_faq.md", "faqs/commission_faq.md",
    # Binance's own changelog. Named here rather than left to resolve, because this repo has
    # a CHANGELOG.md of its own and an accidental match is not a check.
    "CHANGELOG.md",
    # .research/ notes: gitignored, so present locally and absent in CI. Resolving against
    # them would make this check pass on a laptop and fail on a clean checkout.
    "verified-source-code.md", "money-representation.md", "payments-processors.md",
})


# The pre-tokenizer. This is the FIRST STAGE OF A REAL BPE TOKENIZER, not a word split: byte
# level BPE (GPT-2, cl100k_base, and the Claude tokenizers that follow the same design) never
# merges across these boundaries, so every piece this yields is at least one token and the
# pieces are exactly the units the merge table then works on. The alternation below is the
# cl100k_base pattern with the two Unicode property classes rewritten in what `re` supports:
# `[^\W\d_]` is "a letter" and `\d` is "a digit".
#
#   1. English contractions, which the vocabulary carries whole.
#   2. An optional leading non-letter (in practice the space) plus a run of letters. The
#      leading space is part of the token, which is why " balance" and "balance" differ.
#   3. Digits in groups of at most three. cl100k splits numbers this way, so 1234567 is
#      three tokens and not one.
#   4. A run of punctuation or symbols, optionally preceded by a space.
#   5. Newline runs and trailing whitespace.
_PRETOKEN_RX = re.compile(
    r"(?i:'s|'t|'re|'ve|'m|'ll|'d)"
    r"|[^\r\n\w]?[^\W\d_]+"
    r"|\d{1,3}"
    r"| ?[^\s\w]+[\r\n]*"
    r"|\s*[\r\n]+"
    r"|\s+(?!\S)"
    r"|\s+",
    re.UNICODE,
)


# The three calibration constants, set together. See estimate_tokens.
_WORD_WHOLE_LETTERS = 5    # letters a word may have and still be one vocabulary entry
_MERGE_LETTERS = 4         # letters per sub-word merge past that
_PUNCT_PER_TOKEN = 2       # punctuation characters per merged token


def _piece_tokens(piece: str) -> int:
    """Tokens one pre-token piece costs, under a stated model of the merge table."""
    core = piece.strip()
    if not core:                                   # whitespace run: one token, plus one per
        return max(1, len(piece) // 16)            # ~16 further columns of indentation
    if core[0].isdigit():
        return 1                                   # rule 3 already capped this at three digits
    letters = [c for c in core if c.isalpha()]
    if letters:
        if any(ord(c) > 0x2E80 for c in letters):
            # CJK and the scripts above it are not merged into words. Roughly one token per
            # character, and often more, so charge per character rather than per word.
            return max(1, len(letters))
        n = len(letters)
        # A short common English word with its leading space is one vocabulary entry. Past
        # that the piece is built from sub-word merges of about four letters each. The two
        # constants are the calibration point; see estimate_tokens for what they are set
        # against.
        return 1 if n <= _WORD_WHOLE_LETTERS else 1 + -(-(n - _WORD_WHOLE_LETTERS) // _MERGE_LETTERS)
    # Punctuation and symbols. Common pairs are merged (`](`, `**`, `://`), most triples are
    # not, so half the run length is the closest single constant.
    return max(1, -(-len(core) // _PUNCT_PER_TOKEN))


def estimate_tokens(text: str) -> int:
    """Estimate the tokens a model would charge for this text.

    WHAT THIS IS. The real pre-tokenization pass above, plus a stated cost model for each
    piece it produces. It is an estimate and it is named one, but it is not a character count
    wearing a token's name: it counts the units a byte-level BPE tokenizer actually operates
    on, so it charges a markdown table, an identifier like `newClientOrderId`, a URL and a run
    of CJK the way a tokenizer does and a characters-over-four rule does not.

    THE COST MODEL. Byte-level BPE holds common whole words in its vocabulary with their
    leading space, and builds everything else from sub-word merges. So a short word costs one
    token; a longer or rarer one costs one plus a merge every few letters; digits are already
    capped at three per piece by the pre-tokenizer; punctuation merges in pairs; CJK is
    charged per character.

    THE CALIBRATION, and it is against a source you can open. Anthropic's published glossary
    states, of Claude's tokenizer: "For Claude, a token approximately represents 3.5 English
    characters, though the exact number can vary depending on the language used."
    (https://platform.claude.com/docs/en/docs/about-claude/glossary, read 2026-08-25.) The
    three constants above are set so that over every markdown file under skills/ and
    advanced/ this estimator lands at 3.49 characters per estimated token, which is that
    figure, reached from the conservative side. Recheck it with:

        python3 -c "import sys; sys.path.insert(0,'scripts'); \
            from pathlib import Path; from validate import estimate_tokens; \
            t=''.join(p.read_text() for p in list(Path('skills').rglob('*.md')) \
                + list(Path('advanced').rglob('*.md'))); \
            print(len(t)/estimate_tokens(t))"

    WHAT IT IS NOT. It is not a tokenizer, and no number it produces is quoted anywhere as a
    fact about a model's billing. Anchoring the average to a published ratio does not make any
    single file's number exact, and the structural model is what keeps a markdown table or a
    long identifier from being priced as though it were prose. It is used for one purpose:
    comparing a loaded path against a budget and detecting growth in that path. For that it
    has to be stable, structural, and anchored to something checkable, and it is all three.
    """
    return sum(_piece_tokens(p) for p in _PRETOKEN_RX.findall(text))


def parse_provenance(text: str) -> dict[str, str] | None:
    """Return the provenance block's fields, or None when the file has no block.

    The block is a markdown blockquote, and its fields wrap: `sources:` routinely runs over
    six lines. Fields are also written two ways, one per line and several on one line joined
    by a middot, so the parser joins the quote into one string and splits on `name:` at a
    line start or after a separator. A value therefore ends where the next field begins.
    """
    lines = text.split("\n")
    start = next((i for i, ln in enumerate(lines) if "**Provenance**" in ln), None)
    if start is None:
        return None
    body = []
    for ln in lines[start + 1:]:
        if not ln.startswith(">"):
            break
        body.append(ln[1:].strip())
    joined = "\n".join(body)
    keys = [(m.start(), m.end(), m.group(1))
            for m in re.finditer(r"(?m)(?:^|\u00b7\s*)([a-z_]+):", joined)]
    fields: dict[str, str] = {}
    for idx, (kstart, kend, key) in enumerate(keys):
        stop = keys[idx + 1][0] if idx + 1 < len(keys) else len(joined)
        fields[key] = " ".join(joined[kend:stop].split()).strip(" \u00b7")
    return fields


def provenance_age(fields: dict[str, str]) -> tuple[object | None, str]:
    """Parse verified_at into a date. Returns (date, problem); at most one is meaningful.

    The explicit non-answer returns neither: no date, and no problem. A caller therefore has
    three cases to tell apart, not two, and `no_date_declared` below is how it asks.
    """
    from datetime import date

    raw = fields.get("verified_at", "").strip()
    if not raw:
        return None, "provenance block has no verified_at"
    if raw == PROVENANCE_NO_DATE:
        return None, ""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", raw)
    if not m:
        return None, f"verified_at {raw!r} is not an ISO date"
    try:
        got = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None, f"verified_at {raw!r} is not a real date"
    if got > date.today():
        return None, (f"verified_at {got.isoformat()} is in the future; a date nobody could "
                      f"have read the source on is not evidence")
    return got, ""


def no_date_declared(fields: dict[str, str]) -> bool:
    """True when the block says, in the one accepted form, that there is no date."""
    return fields.get("verified_at", "").strip() == PROVENANCE_NO_DATE


# The three states a provenance block can be in, and the only three. Keys are what a block
# writes in `classification:`; values are what the drift report prints.
PROVENANCE_STATES = {
    "revalidated": "currently revalidated",
    "pending": "sourced but revalidation pending",
    "illustrative": "illustrative or historical only",
}
# `historical` is accepted as a synonym for `illustrative` because both words describe the
# same claim about the content: it is not a statement about how a provider behaves today.
PROVENANCE_CLASS_SYNONYMS = {"historical": "illustrative"}


def classify_provenance(fields: dict[str, str], verified: object | None,
                        today: object, max_age_days: int) -> tuple[str, str]:
    """Which of the three states this block is in, and why.

    Only one direction may be declared. A file may declare itself illustrative, which is a
    file giving up a claim, and the report believes it. A file may NOT declare itself
    revalidated: that is a claim about an act somebody performed on a date, so it is read off
    verified_at and nowhere else. A block asserting a revalidation its date cannot support is
    reported in the state its evidence supports, with the contradiction printed.
    """
    declared = fields.get("classification", "").strip().lower()
    declared = PROVENANCE_CLASS_SYNONYMS.get(declared, declared)
    fresh = verified is not None and (today - verified).days <= max_age_days  # type: ignore[operator]

    if declared == "illustrative":
        return "illustrative", "declared illustrative or historical; nothing here expires"
    if declared == "revalidated" and not fresh:
        return "pending", ("the block declares classification: revalidated, and its "
                           "verified_at does not support that")
    if fresh:
        return "revalidated", ""
    if verified is None and not no_date_declared(fields) and fields.get("verified_at"):
        return "pending", "verified_at does not parse as a date"
    return "pending", ""


def provider_marked(md: Path) -> str:
    """The provider or protocol token that marks this file, or "" for a cross-venue file."""
    stem = md.stem.lower()
    text = md.read_text(encoding="utf-8")
    h1 = next((ln for ln in text.split("\n") if ln.startswith("# ")), "").lower()
    hit = _PROVIDER_RX.search(stem) or _PROVIDER_RX.search(h1)
    return hit.group(1) if hit else ""


def check_provenance(md: Path, strict: bool) -> None:
    """Every dated vendor claim names its source, its date and what would invalidate it."""
    rel = str(md.relative_to(ROOT))
    text = md.read_text(encoding="utf-8")
    fields = parse_provenance(text)

    if fields is None:
        token = provider_marked(md)
        if not token:
            return
        msg = (f"{rel} is provider-specific ({token}) and carries no provenance block: "
               f"no source, no verified_at, nothing to recheck it against")
        if rel in PROVENANCE_DEBT and not strict:
            warn(msg + " [known debt]")
        else:
            err(msg)
        return

    if rel in PROVENANCE_DEBT:
        warn(f"{rel} now has a provenance block; delete it from PROVENANCE_DEBT")

    for field in PROVENANCE_REQUIRED:
        if not fields.get(field):
            err(f"{rel} provenance block has no {field}:")
    for field in PROVENANCE_EXPECTED:
        if not fields.get(field):
            warn(f"{rel} provenance block states no {field}:; say plainly what was and was "
                 f"not established")

    # A block with no date is accepted, and it pays for that by having to say what the claims
    # rest on instead. Without the date, `unverified:` is the whole disclosure, so its absence
    # is an error here rather than the warning it is for a dated block.
    if no_date_declared(fields) and not fields.get("unverified"):
        err(f"{rel} verified_at is {PROVENANCE_NO_DATE!r} and the block states no "
            f"unverified:; with no date, that line is the only thing telling a reader what "
            f"these claims rest on")

    if "verified_at" in fields:
        _, problem = provenance_age(fields)
        if problem:
            err(f"{rel} {problem}")

    if fields.get("sources") and not re.search(r"https?://", fields["sources"]):
        err(f"{rel} provenance sources: carries no URL; a source you cannot open is not one")

    trigger = fields.get("revalidate_when", "")
    if trigger and len(trigger.split()) < 4:
        warn(f"{rel} revalidate_when is {trigger!r}; name the concrete change that "
             f"invalidates this file, not a period")

    # The optional classification field. A typo here would silently move a file into a state
    # the drift report does not fail on, which is the one way this field could do harm.
    declared = fields.get("classification", "").strip().lower()
    if declared:
        resolved = PROVENANCE_CLASS_SYNONYMS.get(declared, declared)
        if resolved not in PROVENANCE_STATES:
            err(f"{rel} classification: {declared!r} is not one of "
                f"{sorted(PROVENANCE_STATES) + sorted(PROVENANCE_CLASS_SYNONYMS)}")
        elif resolved == "illustrative" and not fields.get("unverified"):
            err(f"{rel} declares classification: illustrative and states no unverified:; a "
                f"file that opts out of revalidation has to say what it is not claiming")


# ----------------------------------------------------------------------------------------
# The reference web: what points at what, and whether it is there.
# ----------------------------------------------------------------------------------------

def _reference_roots() -> list[Path]:
    return sorted([p for p in ROOT.glob("skills/*/SKILL.md")] +
                  [p for p in ROOT.glob("advanced/*/SKILL.md")])


def _all_references() -> list[Path]:
    out: list[Path] = []
    for skill_md in _reference_roots():
        refs = skill_md.parent / "references"
        if refs.is_dir():
            out.extend(sorted(refs.rglob("*.md")))
    return out


def _resolve_mention(token: str, origin: Path) -> Path | None:
    """Where a backticked `x.md` would have to live for the sentence to be true.

    Search order is the order a reader would try: beside the file, under the skill's
    references root, in the skill directory, then at the repo root.
    """
    refs_root = origin.parent
    while refs_root.name and refs_root.name != "references":
        refs_root = refs_root.parent
    skill_dir = refs_root.parent if refs_root.name == "references" else origin.parent
    for base in (origin.parent, refs_root, skill_dir, ROOT):
        if not base.name and base != ROOT:
            continue
        candidate = base / token
        if candidate.is_file():
            return candidate
    return None


def check_reference_web(strict: bool) -> None:
    """Orphans, missing targets, stale plain-text pointers, chains and cycles.

    Severity is split on one distinction, and the split is deliberate. A markdown LINK from
    one reference to another is a hyperlink an agent follows, so it is the chain this repo
    forbids and it is an error. A backticked MENTION in prose is a cross-reference a human
    reads; it is still a second hop and it is still reported, but making it an error today
    would turn about sixty existing prose sentences red, which would get the rule switched
    off rather than obeyed. Run with --strict to treat mentions as errors too.

    The mention scanner has a real limit worth stating: it cannot tell a repo path from a
    vendor documentation filename, so EXTERNAL_DOC_MENTIONS lists the ones in the tree by
    hand. A new vendor filename cited in prose will be reported as missing until it is either
    fixed or added there. That is the intended direction: a wrong pointer is louder than a
    maintained list.
    """
    print(f"\n{BLD}reference web{OFF}")
    references = _all_references()
    by_path = {p.resolve() for p in references}

    linked_anywhere: set[Path] = set()
    edges: dict[Path, set[tuple[Path, str]]] = {p.resolve(): set() for p in references}

    # Trigger rows: what each SKILL.md promises exists.
    for skill_md in _reference_roots():
        text = skill_md.read_text(encoding="utf-8")
        rel = skill_md.relative_to(ROOT)
        for link in sorted(set(re.findall(r"\]\((references/[^)]+\.md)\)", text))):
            target = skill_md.parent / link
            if not target.is_file():
                err(f"{rel} trigger row points at {link}, which does not exist")
                continue
            linked_anywhere.add(target.resolve())

    for ref in references:
        rel = ref.relative_to(ROOT)
        if ref.resolve() not in linked_anywhere:
            err(f"{rel} is an orphan: no SKILL.md trigger row points at it, so no agent "
                f"ever loads it")

    # Plain-text pointers and reference-to-reference edges.
    scanned = list(references) + _reference_roots()
    for src in scanned:
        rel = src.relative_to(ROOT)
        text = src.read_text(encoding="utf-8")
        is_ref = src.name != "SKILL.md"

        for link in re.findall(r"\]\(([^)]+\.md)\)", text):
            if link.startswith(("http", "#", "mailto")):
                continue
            target = (src.parent / link).resolve()
            if is_ref and target in by_path and target != src.resolve():
                err(f"{rel} links to another reference ({link}); references are one hop "
                    f"from a skill, never a chain")
                edges[src.resolve()].add((target, "link"))

        chained: dict[str, int] = {}
        for m in re.finditer(r"`([A-Za-z0-9_./-]+\.md)`", text):
            token = m.group(1)
            if token in EXTERNAL_DOC_MENTIONS or Path(token).name in EXTERNAL_DOC_MENTIONS:
                continue
            line = text[: m.start()].count("\n") + 1
            target = _resolve_mention(token, src)
            if target is None:
                err(f"{rel}:{line} names `{token}`, which does not resolve to any file; a "
                    f"pointer left behind by a rename sends the reader nowhere")
                continue
            resolved = target.resolve()
            if is_ref and resolved in by_path and resolved != src.resolve():
                chained.setdefault(token, line)
                edges[src.resolve()].add((resolved, "mention"))
        # One line per source file, not one per sentence. Sixty near-identical warnings is a
        # wall nobody reads, and the unit a maintainer actually fixes is the file.
        if chained:
            shown = ", ".join(f"`{t}`:{ln}" for t, ln in sorted(chained.items()))
            msg = (f"{rel} points at {len(chained)} other reference(s) in prose ({shown}); "
                   f"references are one hop from a skill")
            (err if strict else warn)(msg)

    _report_cycles(edges, strict)


def _report_cycles(edges: dict[Path, set[tuple[Path, str]]], strict: bool) -> None:
    """Cycles in the reference graph, reported once each.

    A cycle is the worst shape a chain takes: an agent following pointers never reaches a
    leaf. Severity follows the same rule as the edges themselves. A cycle containing at least
    one markdown link is already an error through its edges; a cycle made only of prose
    mentions is a warning here and an error under --strict.
    """
    found: dict[tuple[str, ...], bool] = {}

    def walk(node: Path, path: list[Path], kinds: list[str]) -> None:
        for target, kind in sorted(edges.get(node, ()), key=lambda e: str(e[0])):
            if target in path:
                cycle = path[path.index(target):]
                names = [str(q.relative_to(ROOT)) for q in cycle]
                pivot = names.index(min(names))
                key = tuple(names[pivot:] + names[:pivot])
                all_kinds = kinds[path.index(target):] + [kind]
                found.setdefault(key, all(k == "mention" for k in all_kinds))
                continue
            if len(path) < 6:
                walk(target, path + [target], kinds + [kind])

    for node in sorted(edges):
        walk(node, [node], [])

    if not found:
        return
    prose_only = all(found.values())
    msg = (f"reference graph contains {len(found)} cycle(s)"
           + (" made only of prose mentions" if prose_only else ""))
    (warn if prose_only and not strict else err)(msg)
    for key in sorted(found):
        short = " -> ".join(Path(name).name for name in key + (key[0],))
        print(f"        {short}")


# ----------------------------------------------------------------------------------------
# Loaded paths: what one task actually costs.
# ----------------------------------------------------------------------------------------

def _load_routing_cases() -> list[dict]:
    """The routing fixture, which is where the representative tasks live.

    Read here rather than restated, because a representative task written into this file
    would be a second fixture that nothing checks. The cases below are the same ones the
    lexical routing lint asserts against, so a reference set used as a budget input has
    already been asserted to be the set that task should open.
    """
    fixture = ROOT / "evals" / "routing-cases.yaml"
    if not fixture.is_file():
        err("evals/routing-cases.yaml is missing; loaded paths cannot be measured, and an "
            "unmeasured budget is not a budget")
        return []
    if yaml is None:
        err("PyYAML is not installed, so the loaded-path budget cannot be measured. Install "
            "it (pip install pyyaml) rather than running without this check")
        return []
    data = yaml.safe_load(fixture.read_text(encoding="utf-8")) or {}
    return list(data.get("cases") or [])


def check_loaded_paths(strict: bool) -> None:
    """Measure every declared loaded path against the one canonical band.

    A path is SKILL.md plus the references one case says that task should open. The band and
    the ratchet are stated once, in the BUDGET POLICY block at the top of this file; this
    function only measures and compares. Entry skills are measured and printed because the
    cost of the installed product should be visible, and are governed by the line caps rather
    than the band. Advanced paths are held to the band, or to their recorded ratchet ceiling.
    """
    print(f"\n{BLD}loaded paths{OFF}  SKILL.md plus the references one task triggers")
    cases = _load_routing_cases()
    skills = {p.parent.name: p for p in _reference_roots()}
    measured: dict[str, list[tuple[str, int]]] = {name: [] for name in skills}
    over_band: list[str] = []

    for case in cases:
        refs = list(case.get("expect_references") or [])
        expected = list(case.get("expect_skills") or [])
        if not refs or not expected:
            continue
        case_id = str(case.get("id", "(unnamed case)"))
        for name in expected:
            skill_md = skills.get(name)
            if skill_md is None:
                continue                      # the routing lint owns unknown skill names
            mine = [r for r in refs if (skill_md.parent / "references" / r).is_file()]
            if not mine:
                continue                      # those references belong to the other skill
            total = estimate_tokens(skill_md.read_text(encoding="utf-8"))
            for ref in mine:
                total += estimate_tokens(
                    (skill_md.parent / "references" / ref).read_text(encoding="utf-8"))
            measured[name].append((case_id, total))

            installed = skill_md.parts[-3] == "skills"
            shown = f"{name} + {len(mine)} ref(s) via {case_id}"
            if installed:
                continue                      # summarised per skill below, not line by line

            ceiling = LOADED_PATH_RATCHET.get(case_id)
            # Everything past the `installed` continue above is an advanced path, so the
            # advanced ceiling applies here and the 3500 target applies to installed skills.
            band_max = LOADED_PATH_ADVANCED_CEILING
            if ceiling is None:
                if total > band_max:
                    err(f"{shown} is ~{total} tokens, past the {band_max} ceiling an "
                        f"advanced path gates at; it carries no ratchet entry, so the "
                        f"ceiling is what it must meet")
                else:
                    print(f"  {shown}: ~{total} tokens (band "
                          f"{LOADED_PATH_TARGET_MIN}-{LOADED_PATH_TARGET_MAX})")
                continue
            if len(expected) != 1:
                err(f"{case_id} is ratcheted but names {len(expected)} skills; a ratchet entry "
                    f"is keyed on the case id and must therefore measure exactly one path")
            if total > ceiling:
                err(f"{shown} is ~{total} tokens against a recorded ceiling of {ceiling}; a "
                    f"ratcheted path may only shrink")
            else:
                over = total - band_max
                mark = f"{RED}OVER CEILING{OFF}" if over > 0 else "ok"
                target_gap = total - LOADED_PATH_TARGET_MAX
                note = "" if target_gap <= 0 else f", {target_gap} over the {LOADED_PATH_TARGET_MAX} target"
                print(f"  {shown}: ~{total} tokens, ratchet {ceiling}, {mark}{note}")
                if over > 0:
                    over_band.append(f"{shown} (~{total}, ceiling {band_max})")

    # Entry skills, summarised. The cost of the installed product is a number that should be
    # visible on every run, but sixty lines of it is a wall nobody reads. The widest path is
    # the one worth naming, because it is what a user actually pays on a bad day.
    for name, paths in sorted(measured.items()):
        if not paths:
            err(f"{name} has no case in evals/routing-cases.yaml naming both the skill and the "
                f"references it should open, so its loaded path is never measured")
            continue
        if skills[name].parts[-3] != "skills":
            continue
        widest = max(paths, key=lambda p: p[1])
        smallest = min(paths, key=lambda p: p[1])
        print(f"  {name}: {len(paths)} measured, ~{smallest[1]} to ~{widest[1]} tokens "
              f"(widest via {widest[0]})")

    # A ratchet entry whose case no longer measures anything is a recorded exemption with
    # nothing behind it, and the next case to take that id would inherit it silently.
    ratcheted = {cid for paths in measured.values() for cid, _ in paths}
    for stale in sorted(set(LOADED_PATH_RATCHET) - ratcheted):
        err(f"LOADED_PATH_RATCHET has an entry for {stale!r}, which measures no loaded path; "
            f"the case was renamed or removed, so delete the entry")

    if over_band:
        print(f"  {len(over_band)} advanced path(s) are over the canonical band and are held "
              f"only by the ratchet in LOADED_PATH_RATCHET. They are not compliant; they are "
              f"prevented from growing. --strict fails the run on them.")
        if strict:
            for line in over_band:
                err(f"loaded path over the canonical band: {line}")


# ----------------------------------------------------------------------------------------
# What is in the tree that should not be, and what is in the bytes that cannot be seen.
# ----------------------------------------------------------------------------------------

# Where an install actually looks. Everything under these two is copied verbatim by anyone
# installing the suite, which is the whole reason the artefact check exists: a copy is a
# directory copy, not `git archive`, so a .gitignore entry does not stop a stray file from
# travelling with the skill.
PRODUCT_DIRS = ("skills", "advanced")

# The rest of the repository that ships to a reader of the project. Local scratch directories
# are deliberately NOT walked: .agent/, .research/ and .omc/ are gitignored working areas that
# no install and no clone carries, and walking them would report a maintainer's notes as a
# defect. Anything that escapes those directories lands in the scan below.
SUPPORT_DIRS = ("scripts", "evals", "docs", "examples", "incidents", ".github",
                ".claude-plugin")

# Artefact names. Three groups: OS and editor droppings, Python runtime output, and the
# scratch files agents leave behind. A name here inside the scanned tree is an error, not a
# warning, because every one of them is trivially deletable and none of them has a reason to
# be committed.
ARTEFACT_NAMES = frozenset({
    ".DS_Store", "Thumbs.db", "desktop.ini", "nohup.out", ".pytest_cache",
    "__pycache__", ".ipynb_checkpoints", ".mypy_cache", ".ruff_cache",
})
ARTEFACT_SUFFIXES = (".pyc", ".pyo", ".orig", ".rej", ".bak", ".swp", ".swo", ".tmp",
                     ".blk", ".log", ".patch~")
ARTEFACT_PATTERNS = (
    re.compile(r"~$"),                       # editor backup: file.md~
    re.compile(r"^#.*#$"),                   # emacs autosave
    re.compile(r"^\.#"),                     # emacs lock file
    re.compile(r"(?i)^(scratch|tmp|temp|untitled|conversation|transcript)[-_.]"),
    re.compile(r"(?i)\.(claude|agent|codex|cursor|aider)\.(md|json|yaml|txt)$"),
    re.compile(r"(?i)^(agent|claude)[-_]?(scratch|notes|output|state)\b"),
)

# Characters that are invisible, or that render as something other than what they are. Tab and
# newline are the two the format uses. Everything else in this set is either a control
# character, a formatting character with no visible glyph, or a space that is not a space, and
# any of them can make a file read differently to a human than to a parser.
# Written as codepoints rather than as literals, so this file does not itself contain the
# characters it exists to find.
_HIDDEN_NAMED = {
    chr(0x00A0): "NO-BREAK SPACE",
    chr(0x00AD): "SOFT HYPHEN",
    chr(0x2007): "FIGURE SPACE",
    chr(0x202F): "NARROW NO-BREAK SPACE",
    chr(0x205F): "MEDIUM MATHEMATICAL SPACE",
    chr(0x3000): "IDEOGRAPHIC SPACE",
    chr(0xFEFF): "ZERO WIDTH NO-BREAK SPACE (BOM)",
}


def _git_visible() -> set[Path] | None:
    """Files a clone carries or is about to: tracked, plus untracked and not ignored.

    This is the set that decides whether an artefact matters OUTSIDE the product surface.
    Running the example test suite leaves .pyc files under examples/; git ignores them, no
    clone ever sees them, and reporting them would be reporting the maintainer's own working
    directory. Inside skills/ and advanced/ the question is different and git cannot answer
    it, because an install copies a directory and copies ignored files with it.

    Returns None when git cannot answer, and the caller then scans everything and says so:
    scanning too much is noisy, and scanning nothing is a check that silently stopped running.
    """
    import subprocess
    try:
        out = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files", "-co", "--exclude-standard", "-z"],
            capture_output=True, check=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return {ROOT / name for name in out.stdout.decode("utf-8").split("\0") if name}


def _scan_files() -> list[Path]:
    """Every file a clone carries, product surface first."""
    visible = _git_visible()
    out: list[Path] = []
    for name in PRODUCT_DIRS + SUPPORT_DIRS:
        base = ROOT / name
        if base.is_dir():
            out.extend(sorted(p for p in base.rglob("*") if not p.is_dir()))
    out.extend(sorted(p for p in ROOT.glob("*") if p.is_file() and not p.is_symlink()))
    if visible is None:
        return out
    return [p for p in out if p in visible]


def check_artefacts() -> None:
    """Stray files: OS droppings, Python runtime output, editor backups, agent scratch.

    Two rules, and the first is the stricter one. Inside skills/ and advanced/ the ONLY legal
    files are SKILL.md and references/**/*.md, so anything else is reported whatever it is
    called; a file with an innocent name is still a file the user did not ask to install.
    Outside those, the artefact names are what is reported.
    """
    print(f"\n{BLD}tree{OFF}")
    stray = 0
    if _git_visible() is None:
        print("  git could not list the tree, so every file under the scanned directories is "
              "examined, ignored ones included")
    for base_name in PRODUCT_DIRS:
        base = ROOT / base_name
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            rel = p.relative_to(ROOT)
            if p.is_symlink():
                err(f"{rel} is a symlink; an installed copy may not carry it")
                stray += 1
                continue
            if p.is_dir():
                if p.name in ARTEFACT_NAMES:
                    err(f"{rel} is a runtime artefact directory inside the installed product")
                    stray += 1
                continue
            parts = p.relative_to(base).parts
            if base_name == "advanced" and parts == ("README.md",):
                continue                     # the opt-in directory explains itself
            is_skill = len(parts) == 2 and parts[1] == "SKILL.md"
            in_refs = len(parts) >= 3 and parts[1] == "references" and p.suffix == ".md"
            if not (is_skill or in_refs):
                err(f"{rel} is not SKILL.md or a reference; installing this skill would copy it")
                stray += 1
            elif in_refs and len(parts) > 4:
                # references/<file>.md or references/<group>/<file>.md, and no deeper. A
                # reference is one hop from a skill, so a path that needs two directories to
                # describe it is describing a chain the skill cannot dispatch to.
                err(f"{rel} nests {len(parts) - 3} directories under references/; at most one "
                    f"grouping directory is supported")
                stray += 1

    for p in _scan_files():
        rel = p.relative_to(ROOT)
        name = p.name
        hit = (name in ARTEFACT_NAMES
               or name.endswith(ARTEFACT_SUFFIXES)
               or any(rx.search(name) for rx in ARTEFACT_PATTERNS)
               or any(part in ARTEFACT_NAMES for part in rel.parts[:-1]))
        if hit:
            err(f"{rel} is an editor, runtime or agent artefact and is in the shipped tree")
            stray += 1
    print(f"  {stray} stray or artefact file(s)")


def check_hidden_characters() -> None:
    """Control characters, invisible formatting characters, and spaces that are not spaces.

    Reported by codepoint and position, because that is the only useful form: the whole
    problem with these is that the file looks correct. Carriage returns are included. A file
    that arrives with CRLF renders identically and changes every line the next editor touches.
    """
    print(f"\n{BLD}bytes{OFF}")
    hits = 0
    for p in _scan_files():
        rel = p.relative_to(ROOT)
        try:
            raw = p.read_bytes()
        except OSError as e:
            err(f"{rel} cannot be read ({type(e).__name__})")
            hits += 1
            continue
        # A file holding a NUL byte is not text and this check has nothing to say about it.
        # Whether it belongs in the tree at all is check_artefacts' question, and inside the
        # product surface that check already answers it for every file that is not a .md.
        if b"\x00" in raw[:8192]:
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as e:
            err(f"{rel} is not valid UTF-8 ({e.reason} at byte {e.start})")
            hits += 1
            continue
        for idx, ch in enumerate(text):
            if ch in "\n\t":
                continue
            code = ord(ch)
            name = ""
            if ch == "\r":
                name = "CARRIAGE RETURN"
            elif code < 0x20 or code == 0x7F:
                name = "CONTROL CHARACTER"
            elif ch in _HIDDEN_NAMED:
                name = _HIDDEN_NAMED[ch]
            elif 0x200B <= code <= 0x200F or 0x2028 <= code <= 0x202E \
                    or 0x2060 <= code <= 0x2064 or 0x2066 <= code <= 0x2069 or code == 0x061C:
                name = "ZERO-WIDTH OR BIDIRECTIONAL FORMATTING CHARACTER"
            if not name:
                continue
            line = text[:idx].count("\n") + 1
            col = idx - (text.rfind("\n", 0, idx) + 1) + 1
            err(f"{rel}:{line}:{col} contains U+{code:04X} {name}")
            hits += 1
            break                            # one report per file; the fix is a whole-file one
    print(f"  {hits} file(s) with hidden or control characters")


# ----------------------------------------------------------------------------------------
# Trigger rows and in-file anchors.
# ----------------------------------------------------------------------------------------

# Words that carry no routing signal, so a trigger row made only of these is a row the router
# cannot use. Kept short on purpose: this list decides only whether a row is EMPTY of signal,
# never which row wins, so it does not need to be a real stopword list.
_ROW_NOISE = frozenset("""
a an and are as at be but by can for from has have in into is it its of on or that the their
them then there these they this to use uses using was what when where which who why will with
you your change changes changed touch touches touching code diff file files line lines review
add adds adding edit edits reads read name names new one two any all
""".split())

_ROW_RX = re.compile(r"(?m)^\s*(?:[-*]\s+|\|\s*)\[([^\]]+)\]\(references/([^)]+\.md)\)"
                     r"\s*[:|]?\s*(.*)$")

# Two rows a router cannot tell apart. Sibling rows in this repo peak at 0.32 similarity, so
# the line below is far above anything the current material produces and would fire on a row
# copied from its neighbour and half-edited, which is how a duplicate row actually arrives.
MAX_ROW_SIMILARITY = 0.75
MIN_ROW_TOKENS = 3


def _row_tokens(text: str) -> set[str]:
    out = set()
    for word in re.findall(r"[A-Za-z][A-Za-z0-9_]{2,}", text):
        for piece in [word] + re.findall(r"[A-Z]?[a-z]{3,}|[A-Z]{3,}", word):
            low = piece.lower()
            if len(low) > 3 and low not in _ROW_NOISE:
                out.add(low)
    return out


def _strip_fences(text: str) -> str:
    """Blank out fenced code blocks, keeping line numbers.

    A trigger row inside a fence is an illustration, not a promise, and reading it as one
    would make an example look like a reference the skill dispatches to.
    """
    out, in_fence = [], False
    for line in text.split("\n"):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            out.append("")
            continue
        out.append("" if in_fence else line)
    return "\n".join(out)


def check_trigger_rows() -> None:
    """One row per reference, and no two rows the router cannot separate.

    What a trigger row promises is: open THIS file when the task looks like THAT. Three ways
    that promise breaks without any link being broken. Two rows can name the same file, so
    which one fires is undefined. Two rows can carry the same words, so the router has nothing
    to choose on. And a row's visible text can name a different file from the one it links to,
    which misroutes the reader while the link itself resolves.
    """
    print(f"\n{BLD}trigger rows{OFF}")
    total = 0
    for skill_md in _reference_roots():
        rel = skill_md.relative_to(ROOT)
        text = _strip_fences(skill_md.read_text(encoding="utf-8"))
        rows: dict[str, set[str]] = {}
        for m in _ROW_RX.finditer(text):
            label, target, trigger = m.group(1), m.group(2), m.group(3)
            line = text[: m.start()].count("\n") + 1
            total += 1
            shown = label.strip().strip("`")
            if shown not in (target, Path(target).name, Path(target).stem,
                             Path(target).with_suffix("").as_posix()):
                err(f"{rel}:{line} link text {shown!r} names a different file from the link "
                    f"target {target!r}")
            if target in rows:
                err(f"{rel}:{line} is a second trigger row for {target}; which row decides is "
                    f"then undefined")
            tokens = _row_tokens(trigger.strip().strip("|"))
            if len(tokens) < MIN_ROW_TOKENS:
                err(f"{rel}:{line} trigger for {target} carries {len(tokens)} routing "
                    f"token(s) (min {MIN_ROW_TOKENS}); there is nothing for a task to match")
            rows[target] = tokens
        names = sorted(rows)
        for i, first in enumerate(names):
            for second in names[i + 1:]:
                a, b = rows[first], rows[second]
                if not a or not b:
                    continue
                similarity = len(a & b) / len(a | b)
                if similarity >= MAX_ROW_SIMILARITY:
                    err(f"{rel} trigger rows for {first} and {second} are {similarity:.0%} the "
                        f"same words; a router cannot choose between them")
    print(f"  {total} trigger row(s) across {len(_reference_roots())} skills")


def _heading_anchors(text: str) -> set[str]:
    """The anchors GitHub generates for this file's headings.

    The algorithm is GitHub's: drop inline HTML and link syntax, lowercase, remove everything
    that is not a word character, a space or a hyphen, then replace each space with a hyphen,
    NOT each run of spaces. A repeated heading gets -1, -2 and so on.
    """
    seen: dict[str, int] = {}
    anchors: set[str] = set()
    for m in re.finditer(r"(?m)^#{1,6}\s+(.*?)\s*$", text):
        head = re.sub(r"<[^>]+>", "", m.group(1))
        head = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", head)
        slug = re.sub(r"[^\w\s-]", "", head.lower()).replace(" ", "-")
        n = seen.get(slug, 0)
        seen[slug] = n + 1
        anchors.add(slug if n == 0 else f"{slug}-{n}")
    return anchors


def check_anchors() -> None:
    """A contents list that points at a heading which is not there.

    These files carry their own tables of contents because an agent reads them in parts. An
    anchor that resolves to nothing sends that read to the top of the file instead of to the
    section, silently, and a renamed heading is how it happens.
    """
    print(f"\n{BLD}anchors{OFF}")
    total = broken = 0
    for md in _all_references() + _reference_roots():
        text = md.read_text(encoding="utf-8")
        anchors = _heading_anchors(text)
        for m in re.finditer(r"\]\(#([^)]+)\)", text):
            total += 1
            if m.group(1) not in anchors:
                line = text[: m.start()].count("\n") + 1
                err(f"{md.relative_to(ROOT)}:{line} links to #{m.group(1)}, which is not a "
                    f"heading in this file")
                broken += 1
    print(f"  {total} in-file anchor(s), {broken} resolving to nothing")


def provenance_report(max_age_days: int) -> int:
    """The provider drift report: three states, and only one of them is green.

    This is what the scheduled drift job runs. It never edits a fact. Only a human re-reading
    a source may move verified_at, and a job that wrote the date would destroy the single
    thing the block carries: not "CI checked this", but "a person read this URL on this day".

    THE THREE STATES, and they are exhaustive.

      currently revalidated          a verified_at that parses, no older than the threshold.
                                     The claims rest on a source somebody opened recently.
      sourced but revalidation       real sources, and no current re-read: either no date at
        pending                      all, or one past the threshold. The material may well
                                     still be right. Nobody has checked, and nobody may say
                                     it has been.
      illustrative or historical     the block declares that its content is not a live claim
        only                         about a provider's current behaviour, so nothing here
                                     expires. Declared with `classification: illustrative`,
                                     never inferred, because inferring it would let a file
                                     opt out of revalidation by going quiet.

    WHY IT CAN FAIL. A report that is green while its evidence is stale is worse than no
    report: it converts "nobody has looked" into "somebody checked". So a REQUIRED block, which
    is every block that has not declared itself illustrative, past its revalidation trigger
    fails this run. Clearing it takes one of two honest acts: re-read the source and move the
    date, or reclassify the file as illustrative and stop citing it as current.

    WHAT IT CANNOT EVALUATE, stated because the gap matters. `revalidate_when` names the
    concrete change that invalidates a file, and no job can tell whether that change has
    happened. The age threshold is a proxy for it and nothing more. A block inside the
    threshold can be wrong the day a venue ships a breaking change.
    """
    from datetime import date

    today = date.today()
    # Sort key only. UNPARSED sorts above NO_DATE so a malformed date, which is a defect, is
    # never buried under the blocks that declare themselves undated, which are not.
    UNPARSED, NO_DATE = 10 ** 6, 10 ** 6 - 1
    rows: list[tuple[int, str, str, str, str, str]] = []
    for md in _all_references():
        fields = parse_provenance(md.read_text(encoding="utf-8"))
        if fields is None:
            continue
        rel = str(md.relative_to(ROOT))
        trigger = fields.get("revalidate_when", "")
        got, problem = provenance_age(fields)
        state, note = classify_provenance(fields, got, today, max_age_days)
        if got is None:
            if no_date_declared(fields):
                rows.append((NO_DATE, rel, PROVENANCE_NO_DATE, trigger, state, note))
            else:
                rows.append((UNPARSED, rel, fields.get("verified_at", "(none)"), problem,
                             state, note))
            continue
        rows.append(((today - got).days, rel, got.isoformat(), trigger, state, note))

    rows.sort(reverse=True)
    by_state = {key: [r for r in rows if r[4] == key] for key in PROVENANCE_STATES}
    failing = [r for r in rows if r[4] == "pending"]

    print(f"provider drift report  ({today.isoformat()})")
    print(f"provenance blocks: {len(rows)}")
    print(f"revalidation threshold: {max_age_days} days")
    print()
    for key, label in PROVENANCE_STATES.items():
        print(f"  {label}: {len(by_state[key])}")
    print()
    for age, rel, when, trigger, state, note in rows:
        if age == NO_DATE:
            shown = "no date"
        elif age == UNPARSED:
            shown = "unparsed"
        else:
            shown = f"{age}d"
        mark = {"revalidated": "ok     ", "pending": "PENDING", "illustrative": "illustr"}[state]
        print(f"{mark} {shown:>9}  verified_at {when}  {rel}")
        if note:
            print(f"                    {note}")
        if state == "pending" and trigger:
            print(f"                    revalidate_when: {trigger}")
    print()
    if failing:
        print(f"{len(failing)} required source(s) are past their revalidation trigger. Either "
              f"re-read the source and move verified_at by hand, or declare the file "
              f"`classification: illustrative` and stop presenting it as current. This job "
              f"never does either for you.")
        return 1
    print("every required source is currently revalidated")
    return 0


def main() -> int:
    argv = sys.argv[1:]
    strict = "--strict" in argv
    if "--provenance-report" in argv:
        age = PROVENANCE_MAX_AGE_DAYS
        if "--max-age-days" in argv:
            at = argv.index("--max-age-days") + 1
            if at >= len(argv) or not argv[at].isdigit():
                print("--max-age-days needs a whole number of days", file=sys.stderr)
                return 2
            age = int(argv[at])
        return provenance_report(age)

    skills = sorted((ROOT / "skills").glob("*/SKILL.md"))
    advanced_skills = sorted((ROOT / "advanced").glob("*/SKILL.md"))
    if not skills:
        print(f"{RED}no skills found under skills/{OFF}")
        return 1

    total_desc = sum(check_skill(s) for s in skills)
    # Advanced descriptions are validated and then dropped on the floor: they never reach a
    # listing, so charging them to the shared budget would price a cost nobody pays.
    for adv in advanced_skills:
        check_skill(adv, installed=False)

    print(f"\n{BLD}suite{OFF}")
    print(f"  skills: {len(skills)} installed, {len(advanced_skills)} advanced (opt-in)")
    print(f"  total description chars: {total_desc} (budget {TOTAL_DESC_BUDGET}, "
          f"advanced excluded)")
    if total_desc > TOTAL_DESC_BUDGET:
        err("total description budget exceeded; the listing is shared with every other installed suite")

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

    binding = sorted(ROOT.glob("skills/*/SKILL.md")) + advanced_skills
    if (ROOT / "AGENTS.md").is_file():
        binding.append(ROOT / "AGENTS.md")
    for md in binding:
        for path in sorted(cited_paths(md)):
            err(f"cited path does not exist: {path} (in {md.relative_to(ROOT)})")

    for md in sorted((ROOT / "docs").glob("*.md")):
        for path in sorted(cited_paths(md)):
            warn(f"cited path does not exist: {path} (in {md.relative_to(ROOT)}); mark it as proposed")

    for md in binding:
        check_prose(md, binding=True)
    for ref in _all_references():
        check_prose(ref, binding=True)
        check_provenance(ref, strict)
    # advanced/ gets the same section shape and the same citation resolution as the installed
    # six. The invalid `Specialises *replay*` citation shipped for exactly one reason: this
    # loop used to read skills/ only.
    for skill_md in skills + advanced_skills:
        check_structure(skill_md)
        check_invariant_citations(skill_md)
    for md in sorted((ROOT / "docs").glob("*.md")) + sorted(ROOT.glob("examples/**/*.md")) \
            + [ROOT / "README.md"]:
        if md.is_file():
            check_prose(md, binding=False)

    installed = {p.parent.name for p in skills}
    expected = {"fin-money-core", "fin-exchange-integration", "fin-payments",
                "fin-ledger", "fin-onchain", "fin-verification"}
    if installed != expected:
        err(f"skills/ must hold exactly the six installed skills. "
            f"unexpected: {sorted(installed - expected) or 'none'}; "
            f"missing: {sorted(expected - installed) or 'none'}")
    # An installed copy contains skills/ only, so a LINK into advanced/ dangles for every user.
    # Naming it in prose is fine and is how "When not to" routes a reader away.
    for md in ROOT.glob("skills/**/*.md"):
        body = md.read_text(encoding="utf-8")
        if re.search(r"\]\([^)]*advanced/", body) or re.search(r"(?m)^\s*\[[^\]]+\]:\s*\S*advanced/", body):
            err(f"{md.relative_to(ROOT)} links into advanced/, which is not installed with the product")

    check_reference_web(strict)
    check_trigger_rows()
    check_anchors()
    check_loaded_paths(strict)
    check_artefacts()
    check_hidden_characters()
    check_repo_consistency()

    agents = ROOT / "AGENTS.md"
    if not agents.is_file():
        err("AGENTS.md is missing; it carries the optional routing block")
    else:
        size = agents.stat().st_size
        print(f"  AGENTS.md: {size} bytes (budget {AGENTS_BUDGET})")
        if size > AGENTS_BUDGET:
            err("AGENTS.md exceeds 2KB; substantive rules belong in the skills, not in an\n            always-installed block. Move the content into the skill that owns it.")

    print()
    if errors:
        print(f"{RED}FAILED{OFF}: {len(errors)} error(s), {len(warnings)} warning(s)")
        return 1
    print(f"{GRN}PASS{OFF}: {len(warnings)} warning(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
