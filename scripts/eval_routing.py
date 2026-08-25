#!/usr/bin/env python3
"""Deprecated name. The runner is scripts/lint_routing_lexical.py.

This file used to BE the runner, and the name was wrong in a way that mattered. "eval_routing"
reads as an evaluation of routing, and the thing it does is score word overlap between a task
and eight frontmatter descriptions. No model runs. Nothing here observes an agent choosing a
skill. Calling that an eval invited exactly one misreading, that a green run is evidence the
agent routes correctly, and a green run is no such evidence.

The file survives as a forwarder because scripts, CI configurations and documentation outside
this repository may still call it by this path. It forwards every argument and returns the
runner's exit status unchanged, so nothing that depended on it breaks. New callers should use
the real name.
"""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

RUNNER = Path(__file__).resolve().parent / "lint_routing_lexical.py"

if __name__ == "__main__":
    print("scripts/eval_routing.py is the old name for scripts/lint_routing_lexical.py, "
          "which is what runs below.", file=sys.stderr)
    if not RUNNER.is_file():
        print(f"missing runner: {RUNNER}", file=sys.stderr)
        raise SystemExit(2)
    # run_path rather than an import, so sys.argv, __main__ semantics and the exit status are
    # exactly what a direct invocation of the runner would produce.
    sys.argv[0] = str(RUNNER)
    runpy.run_path(str(RUNNER), run_name="__main__")
