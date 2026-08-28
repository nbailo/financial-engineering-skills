"""Deny network access for the whole test process.

The only counterparty here is an object in this process, and this guard exists so that
stays true: a future change reaching for a socket or a DNS lookup fails loudly instead of
quietly acquiring a live dependency, and no switch in this file turns it back on. Installed
from three places so no runner misses it - tests/__init__.py (any import of the package),
conftest.py (pytest) and run_tests.py (unittest). Installing twice is a no-op.

This is the one file in the example allowed to name a networking module, and the offline
scan allowlists it BY PATH. The four helpers at the bottom exist so that no second file has
to name one either: the guard's own tests drive the network through these, and they resolve
the primitive at CALL time, so they go through whatever this module installed.
"""
from __future__ import annotations

import socket
import urllib.request

# AF_UNIX stays open because nothing in this example uses it and a runner may. Every address
# family that can leave the machine is refused.
_ALLOWED_FAMILIES = {getattr(socket, "AF_UNIX", None)}

AF_INET = socket.AF_INET
AF_INET6 = socket.AF_INET6
STREAM = socket.SOCK_STREAM

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


# ---- the four ways out, for the guard's own tests to try -------------------------------
def open_socket(family=AF_INET, kind=STREAM):
    return socket.socket(family, kind)


def connect_out(address, timeout: float = 1.0):
    return socket.create_connection(address, timeout)


def resolve_name(host: str, port: int):
    return socket.getaddrinfo(host, port)


def http_get(url: str, timeout: float = 1.0):
    return urllib.request.urlopen(url, timeout=timeout)
