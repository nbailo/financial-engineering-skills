"""pytest entry point. Its only job is to put the example on sys.path and install the
network guard before any test module is imported."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from tests import netguard  # noqa: E402

netguard.install()
