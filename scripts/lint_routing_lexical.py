#!/usr/bin/env python3
"""The lexical routing lint: score evals/routing-cases.yaml against the eight descriptions.

WHAT THIS IS, and the name is the claim. It is a LINT over the words in each skill's
frontmatter description, not an evaluation of routing. Before an agent opens any skill it sees
one thing per skill: that description. This runner scores lexical overlap between a task and
each of the eight descriptions, weighted so a word appearing in one description counts for
more than a word appearing in five. A language model reading those same descriptions resolves
synonyms, negations and clause structure that a bag of words cannot see, so agreement here is
not evidence that the agent routes correctly.

WHAT IT IS GOOD FOR. One regression, and it is the one this suite keeps having: a description
is shortened to fit the listing budget, the venue names or the domain nouns go with it, and
the skill silently stops triggering on tasks it owns. The words are simply gone, and lexical
overlap sees that immediately. Read a FAIL as "the description no longer speaks this task's
vocabulary", never as "the agent would route this wrong".

UNEXPECTED ACTIVATION FAILS. A skill this lint predicts that the case did not ask for is a
failure unless the case lists it in `allow_skills`. Tolerating over-prediction silently was
the older behaviour and it measured nothing: any description could grow to match everything
and the run stayed green. An allowlist entry is a recorded, reviewed statement that this
matcher pulls in that skill for that task, and the count of them is printed on every run, so
the debt is a number somebody can watch rather than a habit.

WHERE IT IS BLIND, stated so nobody reads a green run as more than it is:
  1. A description can carry every right word and still read ambiguously. This says nothing
     about that.
  2. A negative that the description cannot express. A backtest names Binance, fills and PnL;
     no description of a venue-client skill can be written so that a lexical scorer rejects
     it. Those cases declare `decided_by: exclusion` and are scored against a different,
     genuinely mechanical question: does the skill body carry an explicit exclusion that this
     task matches? A case with no mechanical question at all declares `decided_by: unscorable`
     and is reported as SKIP, never as a pass.
  3. Reference routing is scored against the literal trigger tokens in each SKILL.md
     References section. Those rows are written as literals, so this half is a much closer
     match to how the choice is actually made than the description half is.

MODEL AND RUNTIME ROUTING ARE NOT MEASURED HERE, and no file in this repository measures them
today. If one is added it stays a separate script recording the model, its version, the exact
prompts and the raw results, and it is not a required CI check: a gate whose verdict depends
on a model's mood on the day is a gate that gets disabled. This lint is deterministic, which
is the only reason it can gate anything.

Exit status is 1 if any case FAILs or coverage is short. SKIPs and the stated blind spots do
not fail the run; they are printed every time so they stay visible.

Usage:
  scripts/lint_routing_lexical.py            score every case
  scripts/lint_routing_lexical.py --verbose  also print the per-skill score table per case
  scripts/lint_routing_lexical.py --strict   also fail on allowlist entries nothing predicts
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

# Fixture coverage, enforced rather than reported. Five positive and five negative cases per
# skill, across all eight. One case per skill proves a description contains its own nouns and
# nothing more; five tasks written in five vocabularies is the smallest set where a shortened
# description reliably breaks something. The floor on the total follows from the per-skill
# floors and is stated so that dropping a skill cannot quietly drop the requirement with it.
MIN_POSITIVE_PER_SKILL = 5
MIN_NEGATIVE_PER_SKILL = 5
MIN_CASES = 80

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
# instead of being trusted. It has not been moved to make cases pass; the allowlists carry
# what it over-predicts, in the open.
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
    __slots__ = ("case", "status", "lines", "allowed_used", "allowed_stale")

    def __init__(self, case: str, status: str) -> None:
        self.case = case
        self.status = status
        self.lines: list[str] = []
        self.allowed_used: list[str] = []
        self.allowed_stale: list[str] = []

    def say(self, line: str) -> None:
        self.lines.append(line)


def run_case(case: dict, skills: dict[str, dict[str, object]], verbose: bool,
             strict: bool) -> Result:
    task = " ".join(str(case.get("task", "")).split())
    expected = set(case.get("expect_skills") or [])
    forbidden = set(case.get("forbid_skills") or [])
    allowed = set(case.get("allow_skills") or [])
    decided_by = str(case.get("decided_by", "description"))
    scores = score_skills(task, skills)
    predicted = set(predict(scores))

    unknown = (expected | forbidden | allowed) - set(skills)
    if unknown:
        res = Result(case["id"], "FAIL")
        res.say(f"fixture names skills that do not exist: {sorted(unknown)}")
        return res
    if allowed & forbidden:
        res = Result(case["id"], "FAIL")
        res.say(f"{sorted(allowed & forbidden)} is both allowed and forbidden")
        return res
    if allowed & expected:
        res = Result(case["id"], "FAIL")
        res.say(f"{sorted(allowed & expected)} is both expected and allowed; an allowlist is "
                f"for skills the case does not ask for")
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
        # and charging it as a defect would be inventing work. Skills outside the list still
        # have to be allowlisted, so the over-prediction is recorded rather than waved through.
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
        _charge_unexpected(res, predicted, set(required) | allowed, allowed, strict)
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

    _charge_unexpected(res, predicted, expected | forbidden | allowed, allowed, strict)

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


def _charge_unexpected(res: Result, predicted: set[str], accounted: set[str],
                       allowed: set[str], strict: bool) -> None:
    """Every predicted skill the case did not account for is a failure.

    `accounted` is what the case has already spoken about: expected, forbidden, allowlisted,
    or named in exclusion_required_in. Anything else is a skill this lint says would load and
    the case never mentions, and that is the thing the older runner printed as "also
    predicted" and passed.

    An allowlist entry nothing predicts is the opposite defect and is reported too. It is a
    note about a matcher behaviour that has since changed, and left alone it turns into
    permission for a future over-prediction that nobody reviewed. It does not fail the run by
    default, because the run going red on somebody TIGHTENING a description is an incentive
    pointed the wrong way; --strict fails on it, for the pass that cleans the fixture up.
    """
    surplus = sorted(predicted - accounted)
    if surplus:
        res.status = "FAIL"
        res.say(f"unexpected activation: {surplus}. Either the description pulls in a skill "
                f"that should not load, or the case should list it in allow_skills")
    res.allowed_used = sorted(allowed & predicted)
    res.allowed_stale = sorted(allowed - predicted)
    if res.allowed_stale:
        res.say(f"allow_skills lists {res.allowed_stale}, which nothing predicts; delete the "
                f"entry" + (" [strict]" if strict else ""))
        if strict:
            res.status = "FAIL"


def main() -> int:
    argv = sys.argv[1:]
    verbose = "--verbose" in argv
    strict = "--strict" in argv
    if not FIXTURE.is_file():
        print(f"missing fixture: {FIXTURE}", file=sys.stderr)
        return 2
    fixture = yaml.safe_load(FIXTURE.read_text(encoding="utf-8")) or {}
    cases = fixture.get("cases") or []
    skills = load_skills()
    if not skills:
        print("no skills found", file=sys.stderr)
        return 2

    ids = [str(c.get("id", "")) for c in cases]
    duplicate = sorted({i for i in ids if ids.count(i) > 1})
    if duplicate or not all(ids):
        print(f"case ids must exist and be unique; duplicates: {duplicate}", file=sys.stderr)
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

    print(f"{BLD}lexical routing lint{OFF}  {len(cases)} cases against {len(skills)} skills "
          f"({sum(1 for s in skills.values() if s['installed'])} installed, "
          f"{sum(1 for s in skills.values() if not s['installed'])} opt-in)")
    print(f"matcher: weighted lexical overlap with each frontmatter description; "
          f"predict when score >= {ABS_FLOOR} and >= {REL_FLOOR} of the leader")
    print("no model is run here, and nothing below is evidence about how an agent routes")
    print()

    results = [run_case(c, skills, verbose, strict) for c in cases]
    for res in results:
        colour = {"PASS": GRN, "FAIL": RED, "SKIP": CYA}[res.status]
        print(f"  {colour}{res.status}{OFF} {res.case}")
        for line in res.lines:
            print(f"       {line}")

    failed = [r for r in results if r.status == "FAIL"]
    skipped = [r for r in results if r.status == "SKIP"]
    by_exclusion = sum(1 for c in cases if c.get("decided_by") == "exclusion")

    positive: dict[str, int] = {name: 0 for name in skills}
    negative: dict[str, int] = {name: 0 for name in skills}
    for c in cases:
        for name in c.get("expect_skills") or []:
            if name in positive:
                positive[name] += 1
        # A case that requires a skill to carry an exclusion this task matches is testing the
        # same thing a forbid does: that the task must not end up there.
        for name in list(c.get("forbid_skills") or []) + list(
                c.get("exclusion_required_in") or []):
            if name in negative:
                negative[name] += 1

    allow_total = sum(len(r.allowed_used) for r in results)
    stale_total = sum(len(r.allowed_stale) for r in results)

    print()
    print(f"{BLD}coverage{OFF}  minimum {MIN_POSITIVE_PER_SKILL} positive and "
          f"{MIN_NEGATIVE_PER_SKILL} negative per skill, {MIN_CASES} cases in total")
    short = False
    for name in sorted(skills):
        pos, neg = positive[name], negative[name]
        bad = pos < MIN_POSITIVE_PER_SKILL or neg < MIN_NEGATIVE_PER_SKILL
        short = short or bad
        mark = f"{RED}SHORT{OFF}" if bad else "ok   "
        print(f"  {mark} {name}: {pos} positive, {neg} negative")
    if len(cases) < MIN_CASES:
        short = True
        print(f"  {RED}SHORT{OFF} {len(cases)} cases, minimum {MIN_CASES}")

    print()
    print(f"{BLD}limits of this run{OFF}")
    print(f"  {len(cases) - by_exclusion - len(skipped)} cases scored on description overlap, "
          f"which is a proxy for routing and not routing itself")
    print(f"  {by_exclusion} cases the description cannot decide, checked against the "
          f"exclusion text in the skill body instead")
    print(f"  {len(skipped)} cases not mechanically scorable and reported as SKIP")
    print(f"  {allow_total} over-activation(s) recorded in allow_skills and not charged; "
          f"{stale_total} allowlist entry(ies) nothing predicts")
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
    if failed or short:
        if failed:
            print(f"{RED}FAILED{OFF}  {len(failed)} of {len(cases)} cases: "
                  f"{', '.join(r.case for r in failed)}")
        if short:
            print(f"{RED}FAILED{OFF}  fixture coverage is short of the stated minimum")
        return 1
    print(f"{GRN}PASS{OFF}  {len(cases) - len(skipped)} scored, {len(skipped)} skipped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
