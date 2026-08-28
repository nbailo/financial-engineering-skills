"""Importing this package installs the network guard, whichever runner imported it."""
import sys
from pathlib import Path

_EXAMPLE_ROOT = Path(__file__).resolve().parent.parent
if str(_EXAMPLE_ROOT) not in sys.path:
    sys.path.insert(0, str(_EXAMPLE_ROOT))

from . import netguard  # noqa: E402

netguard.install()
