#!/usr/bin/env python3
"""Run the example's tests with the standard library alone.

    python3 examples/prediction-market-bot/run_tests.py

The cases are unittest.TestCase, so `pytest examples/prediction-market-bot` runs them too
where pytest happens to be installed. Neither path adds a dependency, and both install the
network guard before importing a test module.
"""
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from tests import netguard  # noqa: E402

netguard.install()


def main() -> int:
    suite = unittest.TestLoader().discover(start_dir=str(HERE / "tests"),
                                           top_level_dir=str(HERE))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
