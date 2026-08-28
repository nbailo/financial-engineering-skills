"""The guard itself, and the offline scan. If these fail, nothing else here proves the
example runs offline.

The scan is an AST walk over EVERY Python file in the example tree, not a substring search:
`import socket as s` and `getattr(builtins, "ev" + "al")` are invisible to a substring list
and are structure to the parser. It is not a sandbox and does not pretend to be one - it
cannot stop code that is already running - it is a review that runs on every commit.

Only `tests/netguard.py` may name a networking module, and it is allowlisted BY PATH so a
second file cannot join it quietly. Nothing else in the tree, this file included, imports
one: the guard re-exports what its own tests need to drive.
"""
import ast
import unittest
from pathlib import Path

from tests import netguard

EXAMPLE_ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {"__pycache__", ".pytest_cache", ".git"}

# The one file allowed to name a networking module, by path. Not by name, not by pattern.
ALLOWLISTED_PATHS = frozenset({"tests/netguard.py"})

# Top-level modules that can leave this process or load code this scan cannot read.
FORBIDDEN_MODULES = frozenset({
    "subprocess", "socket", "socketserver", "ssl", "http", "urllib", "requests", "aiohttp",
    "httpx", "websocket", "websockets", "asyncio", "ftplib", "smtplib", "poplib", "imaplib",
    "telnetlib", "xmlrpc", "importlib", "ctypes", "multiprocessing", "builtins", "pty",
    "webbrowser",
})
# Names that execute a string, import by string, or reach the builtins table by string.
FORBIDDEN_NAMES = frozenset({"eval", "exec", "compile", "__import__", "breakpoint",
                             "builtins", "__builtins__"})
# Unambiguous attribute calls: a shell, a process, a dynamic import, a connection.
FORBIDDEN_ATTRS = frozenset({"system", "popen", "Popen", "execv", "execve", "execl",
                             "execlp", "spawnv", "spawnl", "fork", "forkpty",
                             "import_module", "urlopen", "urlretrieve", "create_connection",
                             "getaddrinfo", "check_output", "check_call"})
# `from os import ...`: the module is fine, these members are not.
FORBIDDEN_FROM_OS = frozenset({"system", "popen", "execv", "execve", "execl", "execlp",
                               "spawnv", "spawnl", "fork", "forkpty"})


def offences(source: str, filename: str = "<scanned>") -> list:
    """Every reason this file must not be part of an offline example, in source order."""
    found = []
    for node in ast.walk(ast.parse(source, filename=filename)):
        if isinstance(node, ast.Import):
            for alias in node.names:            # alias.name, so `as s` hides nothing
                if alias.name.split(".")[0] in FORBIDDEN_MODULES:
                    found.append(f"imports {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in FORBIDDEN_MODULES:
                found.append(f"imports from {node.module}")
            elif root == "os":
                found += [f"imports os.{a.name}" for a in node.names
                          if a.name in FORBIDDEN_FROM_OS]
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            found.append(f"names {node.id}")
        elif isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_ATTRS:
            found.append(f"calls .{node.attr}")
    return sorted(set(found))


def python_files() -> dict:
    """Every Python file in the tree, keyed by its path relative to the example root."""
    return {path.relative_to(EXAMPLE_ROOT).as_posix(): path.read_text(encoding="utf-8")
            for path in sorted(EXAMPLE_ROOT.rglob("*.py"))
            if not SKIP_DIRS & set(path.relative_to(EXAMPLE_ROOT).parts)}


class NetworkIsDenied(unittest.TestCase):
    def test_install_is_idempotent(self):
        self.assertFalse(netguard.install(), "the guard was not already installed")

    def test_opening_an_inet_socket_is_denied(self):
        with self.assertRaises(netguard.NetworkAccessDenied):
            netguard.open_socket(netguard.AF_INET)

    def test_opening_an_inet6_socket_is_denied(self):
        with self.assertRaises(netguard.NetworkAccessDenied):
            netguard.open_socket(netguard.AF_INET6)

    def test_connecting_out_is_denied(self):
        with self.assertRaises(netguard.NetworkAccessDenied):
            netguard.connect_out(("127.0.0.1", 9))

    def test_name_resolution_is_denied(self):
        with self.assertRaises(netguard.NetworkAccessDenied):
            netguard.resolve_name("localhost", 80)

    def test_an_http_client_cannot_reach_anything(self):
        with self.assertRaises(netguard.NetworkAccessDenied):
            netguard.http_get("http://127.0.0.1:9/")


class TheScanRejectsWhatASubstringListWouldMiss(unittest.TestCase):
    """Each of these is a way out of the process. The scan has to name every one."""

    REJECTED = (
        "import subprocess",
        "from os import system",
        "from urllib.request import urlopen",
        "import os\nos.system('ls')",
        "import os\nos.popen('ls')",
        "import subprocess\nsubprocess.Popen(['ls'])",
        "import subprocess\nsubprocess.check_output(['ls'])",
        "eval('1 + 1')",
        "exec('x = 1')",
        "compile('1', '<s>', 'eval')",
        "__import__('socket')",
        "import importlib\nimportlib.import_module('socket')",
        "from importlib import import_module",
        "import socket as s",
        "import urllib.request as u",
        "getattr(builtins, 'ev' + 'al')",
        "getattr(__builtins__, 'ex' + 'ec')",
        "import asyncio\nasyncio.open_connection('h', 1)",
    )

    def test_every_escape_route_is_rejected(self):
        for source in self.REJECTED:
            with self.subTest(source=source):
                self.assertTrue(offences(source),
                                "the scan accepted a way out of the process")

    def test_the_ordinary_modules_this_example_uses_are_accepted(self):
        for source in ("import json", "import threading", "import hashlib",
                       "from pathlib import Path", "from dataclasses import dataclass",
                       "import unittest\nunittest.TextTestRunner().run(suite)"):
            with self.subTest(source=source):
                self.assertEqual(offences(source), [],
                                 "the scan refused something harmless")


class TheWholeTreeIsOffline(unittest.TestCase):
    def setUp(self):
        self.files = python_files()

    def test_the_scan_covers_every_python_file_and_not_a_shortlist(self):
        for name in ("demo.py", "store.py", "ledger.py", "money.py", "safe_flow.py",
                     "unsafe_flow.py", "fake_processor.py", "conftest.py", "run_tests.py",
                     "tests/concurrency.py", "tests/netguard.py", "tests/test_ledger.py"):
            self.assertIn(name, self.files)
        self.assertGreaterEqual(len(self.files), 12)

    def test_nothing_outside_the_guard_reaches_for_a_shell_a_socket_or_eval(self):
        offenders = {name: offences(source, name) for name, source in self.files.items()
                     if offences(source, name)}
        self.assertEqual(set(offenders), set(ALLOWLISTED_PATHS),
                         f"unexpected reach outside the guard: {offenders}")

    def test_the_allowlist_is_one_path_and_that_path_still_needs_it(self):
        self.assertEqual(len(ALLOWLISTED_PATHS), 1)
        self.assertIn("imports socket", offences(self.files["tests/netguard.py"]))
