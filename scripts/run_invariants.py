#!/usr/bin/env python3
"""Run the deterministic invariant fixtures under evals/invariants/.

    python3 scripts/run_invariants.py            # run every fixture
    python3 scripts/run_invariants.py --list     # show what each one pins

Each fixture pins the arithmetic behind one corrected rule. A case is an exact Decimal
expression, a comparison operator and a value, so a run is deterministic: no model, no
network, no clock, no randomness. That is the whole point. A rule stated in prose can rot
silently, and the sentence that replaced it reads just as confidently as the one that was
wrong. These fixtures are the part a future edit cannot quietly break.

At least one case per fixture is written to FAIL against the rule it replaced. A green run
therefore means more than "the arithmetic holds"; it means the specific wrong answer the
suite used to give is still wrong.

The expression namespace is restricted to Decimal, the rounding constants and three
builtins. It is not a sandbox and is not trying to be one: these fixtures live in this
repository and are reviewed like any other file here. The restriction exists so a fixture
cannot quietly grow a dependency on a clock, a random source or the filesystem, because
any of those would end the property the file is here to provide.
"""
from __future__ import annotations

import argparse
import glob
import sys
from decimal import (
    ROUND_CEILING, ROUND_DOWN, ROUND_FLOOR, ROUND_HALF_EVEN, ROUND_HALF_UP,
    ROUND_UP, Decimal, getcontext,
)
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("run_invariants: PyYAML is required. pip install --require-hashes -r requirements.txt")

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "evals" / "invariants"

# 28 is the Python default and is far more precision than any of these need. It is pinned
# here rather than inherited so a fixture cannot pass on one machine and fail on another
# because something earlier in the process changed the context.
getcontext().prec = 28

NAMESPACE = {
    "Decimal": Decimal,
    "ROUND_HALF_UP": ROUND_HALF_UP,
    "ROUND_HALF_EVEN": ROUND_HALF_EVEN,
    "ROUND_DOWN": ROUND_DOWN,
    "ROUND_UP": ROUND_UP,
    "ROUND_FLOOR": ROUND_FLOOR,
    "ROUND_CEILING": ROUND_CEILING,
    "min": min,
    "max": max,
    "abs": abs,
}

OPS = {
    "gt": (lambda a, b: a > b, ">"),
    "lt": (lambda a, b: a < b, "<"),
    "ge": (lambda a, b: a >= b, ">="),
    "le": (lambda a, b: a <= b, "<="),
    "eq": (lambda a, b: a == b, "=="),
}

REQUIRED_FIXTURE_KEYS = ("id", "invariant", "source", "cases")
REQUIRED_CASE_KEYS = ("name", "expr", "op", "value", "why")

errors: list[str] = []


def err(msg: str) -> None:
    errors.append(msg)
    print(f"  FAIL {msg}")


def check_source(path: Path, source: str) -> None:
    """A fixture that cites a file which no longer exists is not pinning anything."""
    target = source.split(":", 1)[0].strip()
    if not (ROOT / target).exists():
        err(f"{path.name}: source '{target}' does not exist")


def run_fixture(path: Path, show: bool) -> tuple[int, int]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        err(f"{path.name}: is not a YAML mapping")
        return 0, 0

    for key in REQUIRED_FIXTURE_KEYS:
        if key not in raw:
            err(f"{path.name}: missing required key '{key}'")
            return 0, 0

    if raw["id"] != path.stem:
        err(f"{path.name}: id '{raw['id']}' does not match the filename")

    check_source(path, str(raw["source"]))

    cases = raw["cases"]
    if not isinstance(cases, list) or len(cases) < 2:
        err(f"{path.name}: needs at least two cases, one of which fails against the old rule")
        return 0, 0

    print(f"\n{raw['id']}")
    if show:
        print(f"  pins: {raw['invariant']}")
        print(f"  from: {raw['source']}")

    passed = 0
    for case in cases:
        missing = [k for k in REQUIRED_CASE_KEYS if k not in case]
        if missing:
            err(f"{path.name}: case is missing {missing}")
            continue

        name, expr, op = case["name"], str(case["expr"]), case["op"]

        if op not in OPS:
            err(f"{path.name}: '{name}': unknown op '{op}'")
            continue

        # A float literal anywhere makes the result inexact and the fixture pointless,
        # which is exactly the class of defect this whole repository is about.
        if "float(" in expr or "Decimal(0." in expr or "Decimal(1." in expr:
            err(f"{path.name}: '{name}': expression uses a float; every number must be Decimal('...')")
            continue

        try:
            got = eval(expr, {"__builtins__": {}}, dict(NAMESPACE))  # noqa: S307
        except Exception as exc:  # noqa: BLE001
            err(f"{path.name}: '{name}': expression raised {type(exc).__name__}: {exc}")
            continue

        if not isinstance(got, Decimal):
            err(f"{path.name}: '{name}': expression produced {type(got).__name__}, not Decimal")
            continue

        want = Decimal(str(case["value"]))
        compare, symbol = OPS[op]
        if compare(got, want):
            passed += 1
            print(f"  ok   {name}")
        else:
            err(f"{path.name}: '{name}': got {got}, expected {symbol} {want}")
            print(f"       why it matters: {case['why']}")

    return passed, len(cases)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--list", action="store_true", help="print what each fixture pins")
    args = ap.parse_args()

    paths = sorted(Path(p) for p in glob.glob(str(FIXTURES / "*.yaml")))
    if not paths:
        print(f"run_invariants: no fixtures found under {FIXTURES.relative_to(ROOT)}")
        return 1

    total_passed = total_cases = 0
    for path in paths:
        p, c = run_fixture(path, args.list)
        total_passed += p
        total_cases += c

    print()
    if errors:
        print(f"FAILED: {len(errors)} error(s), {total_passed}/{total_cases} case(s) passed")
        return 1
    print(f"PASS: {len(paths)} fixture(s), {total_cases} case(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
