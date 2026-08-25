"""pytest entry point. The tests are plain unittest cases, so pytest is optional.

Its only job is to put the example on sys.path and install the network guard before any test
module is imported.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from tests import netguard  # noqa: E402

netguard.install()
