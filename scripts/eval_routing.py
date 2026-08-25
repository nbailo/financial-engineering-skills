#!/usr/bin/env python3
"""Score the routing evals in evals/routing-cases.yaml.

WHAT THIS MEASURES, precisely. Before an agent opens any skill it sees one thing per skill:
the frontmatter description. This runner scores lexical overlap between a task and each of
the eight descriptions, weighted so a word that appears in one description counts for more
than a word that appears in five. That is a proxy for routing, not routing. The real router
is a language model reading those descriptions, and a language model resolves synonyms,
negations and clause structure that this scorer cannot see.

SO WHAT IS IT GOOD FOR. One regression, and it is the one this suite keeps having: a
description gets shortened to fit the listing budget, the venue names or the domain nouns go
with it, and the skill silently stops triggering on tasks it owns. Lexical overlap detects
exactly that, because the words are simply gone. Treat a FAIL here as "the description no
longer speaks this task's vocabulary", never as "the agent would route this wrong".

WHERE IT IS BLIND, stated so nobody reads a green run as more than it is:
  1. A description can carry every right word and still read ambiguously. This says nothing
     about that.
  2. A negative that the description cannot express. A backtest names Binance, fills and PnL;
     no description of a venue-client skill can be written so that a lexical scorer rejects
     it. Those cases declare `decided_by: exclusion` in the fixture, and are scored against a
     different, genuinely mechanical question: does the skill body carry an explicit exclusion
     that this task matches? A case with no mechanical question at all declares
     `decided_by: unscorable` and is reported as SKIP, never as a pass.
  3. Reference routing is scored against the literal trigger tokens in each SKILL.md
     References section. Those rows are deliberately written as literals, so this half is a
     much closer match to how the choice is actually made than the description half is.

Exit status is 1 if any case FAILs, so this can gate a change to a description. SKIPs and the
stated blind spots do not fail the run; they are printed every time so they stay visible.

Usage:
  scripts/eval_routing.py            score every case
  scripts/eval_routing.py --verbose  also print the per-skill score table for each case
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("PyYAML is required: pip install pyyaml", file=sys.stderr)
    raise SystemExit(2)

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "evals" / "routing-cases.yaml"

# A skill is predicted when it reaches both bars. REL_FLOOR is the one that does the work:
# routing is comparative, so what matters is whether a skill holds its own against the leader,
# not its raw score. ABS_FLOOR only suppresses the degenerate case where every skill scores
# near zero and the leader wins on noise.
#
# REL_FLOOR is this runner's one arbitrary number, so it is stated rather than buried. A task
# that legitimately loads two skills gives the secondary one far less of its vocabulary than
# the primary: "prove this is ready to ship, reconciled against the processor" is mostly
# verification words with a thin seam of payments words. A quarter of the leader's
# discriminative mass is the line below which a skill is not about the task at all. Every run
# prints how the verdicts would change at a stricter floor, so the choice stays inspectable
# instead of being trusted.
REL_FLOOR = 0.25
ABS_FLOOR = 1.0
SENSITIVITY_FLOORS = (0.35, 0.45)

# Words that appear in a financial-correctness description without discriminating between
# any two of them. Left explicit rather than computed, because a stopword list computed from
# eight documents mostly encodes those eight documents.
STOPWORDS = frozenset("""
a an and are as at be been but by can code correctness for from has have how i in into is it
its me my need not of on one or our so than that the their them then there these they this
to use used uses using want was what when where which who why will with without you your
financial change changes touches touch alongside review reviewing building build builds
""".split())

RED, YEL, GRN, CYA, BLD, OFF = (
    "\033[31m", "\033[33m", "\033[32m", "\033[36m", "\033[1m", "\033[0m")
if not sys.stdout.isatty():
    RED = YEL = GRN = CYA = BLD = OFF = ""


def _stem(word: str) -> str:
    """Strip the three suffixes that would otherwise split one concept into three terms.

    Deliberately crude and deliberately conservative. A router that treats "rounding",
    "rounds" and "round" as unrelated words is a worse model of the real one than a stemmer
    that occasionally over-merges, and every rule below has a length guard so short words are
    left alone. This is not linguistics; it is enough normalisation that a description saying
    "rounding" matches a task saying "rounds".
    """
    if len(word) > 5 and word.endswith("ing"):
        return word[:-3]
    if len(word) > 4 and word.endswith("ed"):
        return word[:-2]
    if len(word) > 4 and word.endswith("es") and not word.endswith(("ses", "zes")):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def terms(text: str) -> set[str]:
    """Words worth routing on, plus the subwords of every identifier.

    `newClientOrderId` yields the whole token AND `new`, `client`, `order`, `id`. Both halves
    matter: a trigger row naming the literal wants to match a task naming the literal, and a
    task that says "unrealized PnL" in prose should still reach a row that says
    `unrealizedProfit`. Keeping only the whole token loses the second; keeping only the
    subwords loses the first.
    """
    out: set[str] = set()
    for raw in re.findall(r"[A-Za-z][A-Za-z0-9_]{2,}", text):
        low = raw.lower()
        pieces = [low] + [p.lower() for p in
                          re.findall(r"[A-Z]?[a-z]{3,}|[A-Z]{3,}", raw.replace("_", " "))]
        for piece in pieces:
            stem = _stem(piece)
            if len(stem) > 2 and stem not in STOPWORDS and piece not in STOPWORDS:
                out.add(stem)
    return out


def load_skills() -> dict[str, dict[str, object]]:
    """Every skill an agent could load: the installed six and the two opt-in ones."""
    out: dict[str, dict[str, object]] = {}
    for skill_md in sorted(ROOT.glob("skills/*/SKILL.md")) + sorted(
            ROOT.glob("advanced/*/SKILL.md")):
        text = skill_md.read_text(encoding="utf-8")
        m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
        if not m:
            continue
        meta = yaml.safe_load(m.group(1)) or {}
        name = str(meta.get("name", skill_md.parent.name))
        desc = " ".join(str(meta.get("description", "")).split())
        out[name] = {
            "path": skill_md,
            "installed": skill_md.parts[-3] == "skills",
            "description": desc,
            "terms": terms(name.replace("-", " ") + " " + desc),
            "body": text[m.end():],
            "triggers": parse_triggers(text),
        }
    return out


def parse_triggers(text: str) -> dict[str, set[str]]:
    """The References section, one entry per reference: its trigger tokens.

    Two row shapes exist in this repo and BOTH must be read. One skill writes list rows,
    `- [file.md](references/file.md): the diff does X, names Y`; the other seven write table
    rows, `| [file.md](references/file.md) | the change contains X, Y |`. An earlier version
    of this function matched only the list shape and returned an empty trigger set for seven
    of eight skills without complaining, which made every reference assertion against those
    skills vacuously true. main() now refuses to score a skill that parsed to zero rows.
    """
    rows: dict[str, set[str]] = {}
    pattern = (r"(?m)^\s*(?:[-*]\s+|\|\s*)\[[^\]]+\]\(references/([^)]+\.md)\)"
               r"\s*[:|]?\s*(.*)$")
    for m in re.finditer(pattern, text):
        trigger = m.group(2).strip().strip("|").strip()
        rows.setdefault(m.group(1), set()).update(terms(trigger))
    return rows


def idf(docs: list[set[str]]) -> dict[str, float]:
    """Weight per term: 1 over the number of documents containing it.

    A term in one description is worth 1.0 and routes on its own. A term in four is worth
    0.25 and only contributes in company. No logarithm: with eight documents the log flattens
    the exact distinction this is here to make.
    """
    weights: dict[str, float] = {}
    for doc in docs:
        for term in doc:
            weights[term] = weights.get(term, 0.0) + 1.0
    return {t: 1.0 / n for t, n in weights.items()}


def score_skills(task: str, skills: dict[str, dict[str, object]]) -> dict[str, float]:
    weights = idf([s["terms"] for s in skills.values()])          # type: ignore[index]
    task_terms = terms(task)
    return {name: round(sum(weights.get(t, 0.0)
                            for t in task_terms & s["terms"]), 3)  # type: ignore[operator]
            for name, s in skills.items()}


def predict(scores: dict[str, float], rel_floor: float = REL_FLOOR) -> list[str]:
    best = max(scores.values()) if scores else 0.0
    if best < ABS_FLOOR:
        return []
    return sorted(n for n, v in scores.items()
                  if v >= ABS_FLOOR and v >= rel_floor * best)


def score_references(task: str, skill: dict[str, object]) -> list[tuple[str, float]]:
    triggers: dict[str, set[str]] = skill["triggers"]              # type: ignore[assignment]
    if not triggers:
        return []
    weights = idf(list(triggers.values()))
    task_terms = terms(task)
    scored = [(ref, round(sum(weights.get(t, 0.0) for t in task_terms & toks), 3))
              for ref, toks in triggers.items()]
    return sorted((r for r in scored if r[1] > 0.0), key=lambda r: -r[1])


class Result:
    __slots__ = ("case", "status", "lines")

    def __init__(self, case: str, status: str) -> None:
        self.case = case
        self.status = status
        self.lines: list[str] = []

    def say(self, line: str) -> None:
        self.lines.append(line)


def run_case(case: dict, skills: dict[str, dict[str, object]], verbose: bool) -> Result:
    task = " ".join(str(case.get("task", "")).split())
    expected = set(case.get("expect_skills") or [])
    forbidden = set(case.get("forbid_skills") or [])
    decided_by = str(case.get("decided_by", "description"))
    scores = score_skills(task, skills)
    predicted = set(predict(scores))

    unknown = (expected | forbidden) - set(skills)
    if unknown:
        res = Result(case["id"], "FAIL")
        res.say(f"fixture names skills that do not exist: {sorted(unknown)}")
        return res

    if decided_by == "unscorable":
        res = Result(case["id"], "SKIP")
        res.say(f"not mechanically scorable: {case.get('reason', 'no reason given')}")
        res.say(f"lexical prediction was {sorted(predicted) or '[]'}")
        return res

    if decided_by == "exclusion":
        # The description cannot express this negative, so ask the question that IS
        # answerable: do the skills whose territory this task borrows carry an explicit
        # exclusion it matches?
        #
        # `exclusion_required_in` names those skills, and naming them is the honest part. A
        # bag of words cannot see "it never places an order and never writes a balance", so a
        # task about pricing options drags in fin-ledger on the word "balance" alone. That is
        # the matcher failing to read a negation, not fin-ledger failing to exclude anything,
        # and charging it as a defect would be inventing work. Skills outside the list are
        # reported as matcher noise so the over-prediction is still visible.
        probe = re.compile(str(case["exclusion_probe"]))
        required = list(case.get("exclusion_required_in") or [])
        res = Result(case["id"], "PASS")
        res.say(f"lexical prediction {sorted(predicted) or '[]'}; decided by exclusion text")
        if expected:
            res.say("a case decided by exclusion must expect no skills")
            res.status = "FAIL"
            return res
        if not required:
            res.say("no exclusion_required_in; nothing is actually being checked")
            res.status = "FAIL"
            return res
        for name in required:
            if name not in skills:
                res.say(f"  {name}: named in exclusion_required_in but no such skill")
                res.status = "FAIL"
                continue
            body = str(skills[name]["body"])
            hit = probe.search(body)
            if hit:
                line = body[: hit.start()].count("\n") + 1
                rel = Path(str(skills[name]["path"])).relative_to(ROOT)
                res.say(f"  {name}: excluded at {rel} body line {line} ({hit.group(0)!r})")
            else:
                res.say(f"  {name}: NO exclusion matching {probe.pattern} in its body; this "
                        f"skill owns the task's vocabulary and nothing sends the task away")
                res.status = "FAIL"
        noise = sorted(set(predicted) - set(required))
        if noise:
            res.say(f"  matcher noise, not charged: {noise} (the task states its own "
                    f"negation in prose and a bag of words cannot read it)")
        return res

    res = Result(case["id"], "PASS")
    missing = sorted(expected - predicted)
    extra = sorted(predicted & forbidden)
    if missing:
        res.status = "FAIL"
        for name in missing:
            res.say(f"expected {name}, not predicted "
                    f"(score {scores.get(name, 0.0)}, leader "
                    f"{max(scores, key=scores.get)} at {max(scores.values())})")
    # A forbidden skill that the words drag in may still be ruled out by an explicit redirect
    # in its own description or body: fin-market-data-publication ends with "To consume
    # someone else's feed, use fin-exchange-integration", and that sentence IS the control.
    # Where a case declares the probe, a redirect turns a lexical over-prediction into a
    # reported limit; a forbidden skill with no redirect is still a failure.
    forbid_probe = case.get("forbid_probe")
    probe = re.compile(str(forbid_probe)) if forbid_probe else None
    for name in extra:
        text = str(skills[name]["description"]) + "\n" + str(skills[name]["body"])
        hit = probe.search(text) if probe else None
        if hit:
            res.say(f"forbidden {name} predicted (score {scores[name]}) but redirects "
                    f"explicitly ({hit.group(0)!r}); counted as a matcher limit")
        else:
            res.status = "FAIL"
            res.say(f"forbidden {name} was predicted (score {scores[name]}) and nothing in "
                    f"it sends the task elsewhere")
    surplus = sorted(predicted - expected - forbidden)
    if surplus:
        # Not a failure. The fixture states what MUST load and what must NOT; anything else
        # is a judgement call this scorer has no standing to make.
        res.say(f"also predicted, neither required nor forbidden: {surplus}")

    # Reference routing, scored against the literal trigger rows.
    want_refs = list(case.get("expect_references") or [])
    deny_refs = list(case.get("forbid_references") or [])
    if want_refs or deny_refs:
        # Scope the candidate references to the skills the case says should load. Reference
        # routing happens after a skill is opened, so ranking a Polymarket trigger row
        # against a payments trigger row models nothing: those rows are never in front of
        # the agent at the same moment. Pool from predicted skills only when the case names
        # no expected skill at all.
        pool = sorted(expected) or sorted(predicted)
        ranked: list[tuple[str, float]] = []
        for name in pool:
            ranked.extend(score_references(task, skills[name]))
        ranked.sort(key=lambda r: -r[1])
        top = [r for r, _ in ranked[: max(len(want_refs) + 3, 6)]]
        for ref in want_refs:
            if ref not in top:
                res.status = "FAIL"
                got = dict(ranked).get(ref)
                res.say(f"reference {ref} not in the top {len(top)} trigger matches "
                        f"(score {got if got is not None else 0.0}); top: {top[:5]}")
        # A forbidden reference is not required to score zero; trigger rows for two venues in
        # one domain share vocabulary by construction. What must hold is that it never
        # outranks a reference the task actually needs, and that it is not the top match when
        # the case names no expected reference.
        ranks = {r: i for i, (r, _) in enumerate(ranked)}
        # Only rank the forbidden against required references that actually scored. A
        # required reference that scored nothing is already a failure above, and comparing
        # against its absence would report the same defect twice in different words.
        placed_wanted = [ranks[r] for r in want_refs if r in ranks]
        worst_wanted = max(placed_wanted) if placed_wanted else -1
        for ref in deny_refs:
            place = ranks.get(ref)
            if place is None:
                continue
            if placed_wanted and place < worst_wanted:
                res.status = "FAIL"
                res.say(f"reference {ref} is forbidden yet outranks a required one "
                        f"(rank {place + 1} against {worst_wanted + 1})")
            elif not want_refs and place == 0:
                res.status = "FAIL"
                res.say(f"reference {ref} is forbidden and is the top trigger match")

    if verbose:
        table = sorted(scores.items(), key=lambda kv: -kv[1])
        res.say("scores: " + ", ".join(f"{n}={v}" for n, v in table))
    return res


def main() -> int:
    verbose = "--verbose" in sys.argv[1:]
    if not FIXTURE.is_file():
        print(f"missing fixture: {FIXTURE}", file=sys.stderr)
        return 2
    fixture = yaml.safe_load(FIXTURE.read_text(encoding="utf-8")) or {}
    cases = fixture.get("cases") or []
    skills = load_skills()
    if not skills:
        print("no skills found", file=sys.stderr)
        return 2

    # A skill whose References section parsed to nothing would make every reference
    # assertion against it pass by default. That is the one failure mode this runner must
    # never have, so it is fatal rather than a warning.
    empty = sorted(n for n, s in skills.items()
                   if not s["triggers"] and "## References" in str(s["body"]))
    if empty:
        print(f"trigger rows failed to parse for {empty}; every reference assertion against "
              f"them would pass vacuously", file=sys.stderr)
        return 2

    print(f"{BLD}routing evals{OFF}  {len(cases)} cases against {len(skills)} skills "
          f"({sum(1 for s in skills.values() if s['installed'])} installed, "
          f"{sum(1 for s in skills.values() if not s['installed'])} opt-in)")
    print(f"matcher: weighted lexical overlap with each frontmatter description; "
          f"predict when score >= {ABS_FLOOR} and >= {REL_FLOOR} of the leader")
    print()

    results = [run_case(c, skills, verbose) for c in cases]
    for res in results:
        colour = {"PASS": GRN, "FAIL": RED, "SKIP": CYA}[res.status]
        print(f"  {colour}{res.status}{OFF} {res.case}")
        for line in res.lines:
            print(f"       {line}")

    failed = [r for r in results if r.status == "FAIL"]
    skipped = [r for r in results if r.status == "SKIP"]
    by_exclusion = sum(1 for c in cases if c.get("decided_by") == "exclusion")
    covered: set[str] = set()
    for c in cases:
        covered.update(c.get("expect_skills") or [])
    uncovered_positive = sorted(set(skills) - covered)
    negative: set[str] = set()
    for c in cases:
        negative.update(c.get("forbid_skills") or [])
        if not (c.get("expect_skills") or []):
            negative.update(skills)
    uncovered_negative = sorted(set(skills) - negative)

    print()
    print(f"{BLD}coverage{OFF}")
    print(f"  positive case for every skill: "
          f"{'yes' if not uncovered_positive else 'NO, missing ' + str(uncovered_positive)}")
    print(f"  negative case for every skill: "
          f"{'yes' if not uncovered_negative else 'NO, missing ' + str(uncovered_negative)}")
    print()
    print(f"{BLD}limits of this run{OFF}")
    print(f"  {len(cases) - by_exclusion - len(skipped)} cases scored on description overlap, "
          f"which is a proxy for routing and not routing itself")
    print(f"  {by_exclusion} cases the description cannot decide, checked against the "
          f"exclusion text in the skill body instead")
    print(f"  {len(skipped)} cases not mechanically scorable and reported as SKIP")
    print(f"{BLD}sensitivity{OFF}")
    print(f"  REL_FLOOR is {REL_FLOOR}. Verdicts that would change at a stricter floor:")
    for floor in SENSITIVITY_FLOORS:
        changed = []
        for case in cases:
            if case.get("decided_by") in ("exclusion", "unscorable"):
                continue
            scores = score_skills(" ".join(str(case.get("task", "")).split()), skills)
            base, alt = set(predict(scores)), set(predict(scores, floor))
            if base != alt:
                lost = sorted(base - alt)
                changed.append(f"{case['id']} loses {lost}")
        print(f"    at {floor}: " + ("; ".join(changed) if changed else "nothing changes"))
    print()
    if failed:
        print(f"{RED}FAILED{OFF}  {len(failed)} of {len(cases)} cases: "
              f"{', '.join(r.case for r in failed)}")
        return 1
    print(f"{GRN}PASS{OFF}  {len(cases) - len(skipped)} scored, {len(skipped)} skipped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
