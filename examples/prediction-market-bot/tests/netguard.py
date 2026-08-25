"""Deny network access for the whole test process.

This example is offline by construction: the only counterparty is an object in this process.
The guard exists so that stays true. A future change that reaches for `requests`, a raw
socket, or a DNS lookup fails loudly here instead of quietly acquiring a live dependency,
and there is no switch in this file that turns it back on.

Installed from three places so no runner misses it: tests/__init__.py (any import of the
package), conftest.py (pytest), and run_tests.py (unittest). Installing twice is a no-op.
"""
from __future__ import annotations

import socket

# AF_UNIX stays open because nothing in this example uses it and a runner may. Every address
# family that can leave the machine is refused.
_ALLOWED_FAMILIES = {getattr(socket, "AF_UNIX", None)}

_installed = False


class NetworkAccessDenied(RuntimeError):
    """Raised instead of opening anything. The tests assert on this type."""


def _refuse(name: str):
    def refuse(*_args, **_kwargs):
        raise NetworkAccessDenied(
            f"{name} was called. This example runs offline and has no live mode.")
    return refuse


def install() -> bool:
    """Patch the socket module in place. Returns True the first time, False after."""
    global _installed
    if _installed:
        return False

    real_init = socket.socket.__init__

    def guarded_init(self, family=socket.AF_INET, *args, **kwargs):
        if family not in _ALLOWED_FAMILIES:
            raise NetworkAccessDenied(
                f"a socket in family {family!r} was opened. This example runs offline and "
                f"has no live mode.")
        return real_init(self, family, *args, **kwargs)

    socket.socket.__init__ = guarded_init
    socket.socket.connect = _refuse("socket.socket.connect")
    socket.socket.connect_ex = _refuse("socket.socket.connect_ex")
    socket.create_connection = _refuse("socket.create_connection")
    socket.getaddrinfo = _refuse("socket.getaddrinfo")
    _installed = True
    return True
