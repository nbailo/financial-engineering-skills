#!/usr/bin/env python3
"""Run this example's tests with the standard library alone. The cases are
unittest.TestCase, so `pytest examples/payment-ledger-reconciliation` runs them too where
pytest happens to be installed; neither path adds a dependency, and both install the
network guard before importing a test module.
"""
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from tests import netguard  # noqa: E402

netguard.install()

if __name__ == "__main__":
    suite = unittest.TestLoader().discover(str(HERE / "tests"), top_level_dir=str(HERE))
    sys.exit(0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1)
