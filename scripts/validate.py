#!/usr/bin/env python3
"""Validate the skill suite against the Agent Skills spec and this repo's budgets.

Spec limits enforced (agentskills.io/specification):
  - name: 1-64 chars, ^[a-z0-9]([a-z0-9-]*[a-z0-9])?$, no consecutive hyphens,
    must equal the parent directory, no reserved words.
  - description: 1-1024 chars, no XML-like tags.
  - Legal frontmatter keys only. Any other key hard-fails package_skill.py,
    claude.ai upload, and the Skills API.

Repo budgets. The constants below are the only statement of these numbers; nothing
restates them, because a restated budget is a claim that rots (see MAX_SKILL_LINES,
MAX_DESC, TOTAL_DESC_BUDGET, AGENTS_BUDGET):
  - SKILL.md line cap, because a SKILL.md is loaded whole on every activation.
  - Per-description and suite-total caps, because the skill listing budget is shared
    with every other suite the user has installed.
  - AGENTS.md size cap: it carries routing discipline only, never financial rules.
  - skills/ holds exactly the six installed skills; advanced/ is not part of the product,
    and is validated the same way with its own size ceilings (MAX_ADVANCED_LINES,
    MAX_ADVANCED_TOKENS) and its descriptions excluded from the installed listing budget.
  - references/ one level deep; any reference over 100 lines carries its own
    table of contents, because a partially-read file must still reveal structure.
  - Every provider or protocol reference carries a provenance block: provider, surface,
    version, a verified_at that is either a date which parses and is not in the future or
    the literal "not established", at least one source URL, and a revalidate_when trigger.
    An undated block must also carry an unverified: line, since that line is then the only
    statement of what its claims rest on. PROVENANCE_DEBT, the list of files that predate
    the rule, is empty; a new provider reference with no block is an error.
  - The reference web: no orphans, no trigger row pointing at a missing file, no backticked
    filename that resolves nowhere, and no reference pointing at another reference.

Usage:
  scripts/validate.py                       structure, budgets, provenance, reference web
  scripts/validate.py --strict              promote the documented debt warnings to errors
  scripts/validate.py --provenance-report [--max-age-days N]
                                            report evidence age only; changes nothing, exits 0
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

# advanced/ is opt-in and never appears in the installed listing, so its descriptions are
# deliberately kept out of TOTAL_DESC_BUDGET: they cost nothing until somebody copies the
# directory in. They still cost context once loaded, and an opt-in file is the only file a
# reader gets for that domain, so they carry their own ceilings. Both numbers sit above the
# largest advanced file as it stood on 2026-08-25 (304 lines, ~6,300 estimated tokens) so
# they catch growth. They are a ceiling, not a certificate that the present size is right:
# an advanced skill above MAX_SKILL_LINES is also reported as a warning, so the gap between
# what an installed skill is allowed and what an opt-in one costs stays visible on every run.
MAX_ADVANCED_LINES = 320
MAX_ADVANCED_TOKENS = 7000

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
    if installed and lines > MAX_SKILL_LINES:
        err(f"SKILL.md is {lines} lines (max {MAX_SKILL_LINES})")
    if not installed:
        check_advanced_budgets(skill_md)

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
        err("contains an @-reference — force-loads the file and burns context")

    for ref in sorted(set(re.findall(r"\]\((references/[^)]+\.md)\)", text))):
        target = skill_md.parent / ref
        if not target.is_file():
            continue        # check_reference_web owns missing targets, and says so once
        if ref.count("/") > 2:
            warn(f"reference nested {ref.count('/')} deep: {ref} — deep chains get partially read")
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
                    f"{len(body.split())} words of prose) — SKILL.md points an agent at it"
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
        report(f"{rel}:{line} cites the retired id {m.group(0)} — cite the rule by name")

    if binding and "install-guardrails" in text:
        line = text[: text.index("install-guardrails")].count("\n") + 1
        err(f"{rel}:{line} implies the guardrail install is needed — skills are self-sufficient")

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

    # A version stated anywhere else must agree with the plugin's.
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


def approx_tokens(text: str) -> int:
    """Estimate BPE tokens. This is an ESTIMATE, not a tokenizer, and it is allowed to be.

    Standard library only, so there is no real tokenizer here. Each whitespace-separated word
    costs ceil(len/4) tokens, at least one. On the English prose in this repo that lands
    within roughly 15% of a real BPE count, always on the same side for the same text, which
    is all a growth ceiling needs. MAX_ADVANCED_TOKENS is set with that margin folded in, so
    a file near the limit is genuinely near it. Do not quote this number as a fact anywhere.
    """
    return sum(max(1, -(-len(word) // 4)) for word in text.split())


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


def check_advanced_budgets(skill_md: Path) -> None:
    """advanced/ pays its own budget, and the gap to the installed cap stays visible."""
    text = skill_md.read_text(encoding="utf-8")
    rel = skill_md.relative_to(ROOT)
    lines = text.count("\n") + 1
    tokens = approx_tokens(text)
    print(f"  {rel}: {lines} lines (max {MAX_ADVANCED_LINES}), "
          f"~{tokens} estimated tokens (max {MAX_ADVANCED_TOKENS})")
    if lines > MAX_ADVANCED_LINES:
        err(f"{rel} is {lines} lines (advanced max {MAX_ADVANCED_LINES})")
    elif lines > MAX_SKILL_LINES:
        warn(f"{rel} is {lines} lines, past the {MAX_SKILL_LINES} an installed skill gets; "
             f"opt-in buys headroom, it does not remove the cost")
    if tokens > MAX_ADVANCED_TOKENS:
        err(f"{rel} is ~{tokens} estimated tokens (advanced max {MAX_ADVANCED_TOKENS}); the "
            f"estimator is approximate, the overrun is not")


def provenance_report(max_age_days: int) -> int:
    """Report the age of every provenance block. Never edits one, never opens anything.

    This is what the scheduled drift job runs. A stale block is not a defect in the file: it
    is a statement that nobody has re-read the source since a date, which is exactly what the
    block is for. Only a human re-reading the source may move verified_at, so this prints and
    exits 0 whatever it finds. Rewriting a date here would destroy the one fact it carries.
    """
    from datetime import date

    today = date.today()
    # Sort key only. UNPARSED sorts above NO_DATE so a malformed date, which is a defect, is
    # never buried under the blocks that declare themselves undated, which are not.
    UNPARSED, NO_DATE = 10 ** 6, 10 ** 6 - 1
    rows: list[tuple[int, str, str, str]] = []
    for md in _all_references():
        fields = parse_provenance(md.read_text(encoding="utf-8"))
        if fields is None:
            continue
        rel = str(md.relative_to(ROOT))
        trigger = fields.get("revalidate_when", "")
        got, problem = provenance_age(fields)
        if got is None:
            if no_date_declared(fields):
                rows.append((NO_DATE, rel, PROVENANCE_NO_DATE, trigger))
            else:
                rows.append((UNPARSED, rel, fields.get("verified_at", "(none)"), problem))
            continue
        rows.append(((today - got).days, rel, got.isoformat(), trigger))

    rows.sort(reverse=True)
    # An undated block is not stale: staleness is an age, and it has none. It is counted on
    # its own line so that clearing the backlog cannot be mistaken for dating it.
    undated = [r for r in rows if r[0] == NO_DATE]
    stale = [r for r in rows if r[0] != NO_DATE and r[0] > max_age_days]
    print(f"provenance blocks: {len(rows)}")
    print(f"staleness threshold: {max_age_days} days (today {today.isoformat()})")
    print(f"past threshold: {len(stale)}")
    print(f"no date claimed: {len(undated)}")
    print()
    for age, rel, when, trigger in rows:
        if age == NO_DATE:
            mark, shown = "NODATE", "no date"
        elif age == UNPARSED:
            mark, shown = "STALE ", "unparsed"
        else:
            mark = "STALE " if age > max_age_days else "ok    "
            shown = f"{age}d"
        print(f"{mark}{shown:>9}  verified_at {when}  {rel}")
        if (age >= NO_DATE or age > max_age_days) and trigger:
            print(f"                    revalidate_when: {trigger}")
    if stale or undated:
        print()
        print("Re-read each source above and, only if the source still says what the file "
              "says, move verified_at by hand. This job never edits a fact.")
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

    binding = sorted(ROOT.glob("skills/*/SKILL.md")) + advanced_skills
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
    check_repo_consistency()

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
