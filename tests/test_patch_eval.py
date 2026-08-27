#!/usr/bin/env python3
"""Unit tests for the paired patch evaluator. No model is called and no money is spent.

    python3 -m unittest tests.test_patch_eval -v

The first thing this module does is make a real network call impossible: socket.socket is replaced
before the evaluator is imported, so any code path that reaches for the network raises instead of
dialling. Every test that needs an API response supplies its own. If CI ever regresses into making a
live call, test_no_real_network_is_reachable fails rather than the bill arriving later.
"""
from __future__ import annotations

import io
import json
import os
import socket
import stat
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))


class NetworkUsed(RuntimeError):
    pass


def _no_network(*args, **kwargs):
    raise NetworkUsed("a test attempted a real network connection")


_REAL_SOCKET = socket.socket
socket.socket = _no_network          # installed before the import below, on purpose

import run_patch_eval as ev          # noqa: E402


def a_case(tmp: Path, case_id="money-core-x", skill="fin-money-core", body="VALUE = 1\n"):
    """A minimal fixture on disk, enough for digest, input building and grading."""
    case = tmp / case_id
    (case / "repo").mkdir(parents=True)
    (case / "oracle").mkdir(parents=True)
    (case / "repo" / "mod.py").write_text(body, encoding="utf-8")
    (case / "oracle" / "test_oracle.py").write_text(
        "import mod, sys\nsys.exit(0 if mod.VALUE == 2 else 1)\n", encoding="utf-8")
    (case / "fix.patch").write_text("", encoding="utf-8")
    return {
        "id": case_id, "skill": skill, "references": [], "task": "Fix it, please, thoroughly.",
        "allowed_paths": ["repo/mod.py"], "timeout_seconds": 10,
        "defect": "d", "oracle_proves": "p",
        "_dir": case, "_digest": ev.case_digest(case),
    }


PATCH_OK = (
    "--- a/repo/mod.py\n"
    "+++ b/repo/mod.py\n"
    "@@ -1 +1 @@\n"
    "-VALUE = 1\n"
    "+VALUE = 2\n"
)


class Network(unittest.TestCase):
    def test_no_real_network_is_reachable(self):
        with self.assertRaises(NetworkUsed):
            socket.socket()

    def test_the_evaluator_dials_nothing_without_yes(self):
        """--dry-run and the missing --yes path must both return before any request is built."""
        with mock.patch.object(ev.urllib.request, "urlopen", side_effect=NetworkUsed):
            with mock.patch.object(sys, "argv", [
                    "run_patch_eval.py", "--model", "m", "--effort", "low",
                    "--cases", "all", "--dry-run"]):
                with mock.patch.object(ev, "load_cases", return_value=[a_case(Path(self.tmp))]):
                    with mock.patch("sys.stdout", new=io.StringIO()):
                        self.assertEqual(ev.main(), 0)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()


class Pairing(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.spec = a_case(self.tmp)

    def tearDown(self):
        self._tmp.cleanup()

    def test_the_same_conditions_produce_the_same_key(self):
        a = ev.pair_key(self.spec, 0, "m", "low", "abc")
        b = ev.pair_key(self.spec, 0, "m", "low", "abc")
        self.assertEqual(a, b)

    def test_a_different_model_or_effort_is_a_different_key(self):
        base = ev.pair_key(self.spec, 0, "m", "low", "abc")
        self.assertNotEqual(base, ev.pair_key(self.spec, 0, "OTHER", "low", "abc"))
        self.assertNotEqual(base, ev.pair_key(self.spec, 0, "m", "high", "abc"))
        self.assertNotEqual(base, ev.pair_key(self.spec, 1, "m", "low", "abc"))
        self.assertNotEqual(base, ev.pair_key(self.spec, 0, "m", "low", "OTHERCOMMIT"))

    def test_editing_the_fixture_changes_the_digest_and_the_key(self):
        before = ev.pair_key(self.spec, 0, "m", "low", "abc")
        (self.spec["_dir"] / "repo" / "mod.py").write_text("VALUE = 99\n", encoding="utf-8")
        after_spec = dict(self.spec, _digest=ev.case_digest(self.spec["_dir"]))
        self.assertNotEqual(before, ev.pair_key(after_spec, 0, "m", "low", "abc"))

    def test_arm_order_is_counterbalanced_and_deterministic(self):
        seen = {ev.arm_order(f"case-{i}", r) for i in range(40) for r in range(3)}
        self.assertEqual(seen, {("on", "off"), ("off", "on")}, "both orders must occur")
        self.assertEqual(ev.arm_order("case-7", 2), ev.arm_order("case-7", 2), "and be stable")

    def test_unmatched_arms_are_dropped_from_the_comparison(self):
        key = "k1"
        records = [
            {"pair_key": key, "arm": "on", "outcome": "pass", "order": "on-off"},
            {"pair_key": "lonely", "arm": "off", "outcome": "pass", "order": "off-on"},
        ]
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            ev.summarise(records, repeats=1)
        text = out.getvalue()
        self.assertIn("complete 0", text)
        self.assertIn("dropped unmatched 2", text)

    def test_an_invalid_record_never_enters_the_effect_counts(self):
        records = [
            {"pair_key": "k", "arm": "off", "outcome": "invalid", "order": "off-on",
             "detail": "http 500"},
            {"pair_key": "k", "arm": "on", "outcome": "pass", "order": "off-on"},
        ]
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            ev.summarise(records, repeats=1)
        text = out.getvalue()
        self.assertIn("invalid 1", text)
        self.assertIn("on-only wins 0", text, "an invalid off arm cannot make on a winner")


class ApiClassification(unittest.TestCase):
    """Anything that is not a complete, parseable, patch-bearing response is invalid, never fail."""

    def _call(self, urlopen_side):
        with mock.patch.object(ev.urllib.request, "urlopen", urlopen_side):
            return ev.call_model("KEY-NOT-LOGGED", "m", "low", "prompt")

    def test_http_error_is_invalid(self):
        err = urllib.error.HTTPError("u", 500, "boom", {}, None)
        status, rec = self._call(mock.Mock(side_effect=err))
        self.assertEqual(status, "invalid")
        self.assertIn("http 500", rec["reason"])

    def test_transport_failure_is_invalid(self):
        status, rec = self._call(mock.Mock(side_effect=urllib.error.URLError("down")))
        self.assertEqual(status, "invalid")
        self.assertIn("transport", rec["reason"])

    def test_timeout_is_invalid(self):
        status, rec = self._call(mock.Mock(side_effect=TimeoutError()))
        self.assertEqual(status, "invalid")
        self.assertIn("transport", rec["reason"])

    def test_incomplete_response_is_invalid(self):
        status, rec = self._call(_responds({"status": "incomplete", "output_text": "{}"}))
        self.assertEqual(status, "invalid")
        self.assertIn("incomplete", rec["reason"])

    def test_malformed_structured_output_is_invalid(self):
        status, rec = self._call(_responds({"status": "completed", "output_text": "not json"}))
        self.assertEqual(status, "invalid")
        self.assertIn("did not parse", rec["reason"])

    def test_a_response_with_no_patch_is_invalid(self):
        status, rec = self._call(_responds(
            {"status": "completed", "output_text": json.dumps({"summary": "done"})}))
        self.assertEqual(status, "invalid")
        self.assertIn("no patch", rec["reason"])

    def test_a_good_response_carries_the_patch_and_reported_usage(self):
        status, rec = self._call(_responds({
            "status": "completed",
            "output_text": json.dumps({"patch": PATCH_OK, "summary": "s"}),
            "usage": {"input_tokens": 11, "output_tokens": 22},
        }))
        self.assertEqual(status, "ok")
        self.assertEqual(rec["patch"], PATCH_OK)
        self.assertEqual((rec["input_tokens"], rec["output_tokens"]), (11, 22))

    def test_the_api_key_is_not_written_into_any_record(self):
        status, rec = self._call(_responds({
            "status": "completed",
            "output_text": json.dumps({"patch": PATCH_OK, "summary": "s"})}))
        self.assertEqual(status, "ok")
        self.assertNotIn("KEY-NOT-LOGGED", json.dumps(rec))


def _responds(payload):
    body = json.dumps(payload).encode()
    handle = mock.MagicMock()
    handle.read.return_value = body
    handle.__enter__.return_value = handle
    return mock.Mock(return_value=handle)


class PatchSafety(unittest.TestCase):
    ALLOWED = ["repo/mod.py"]

    def test_an_acceptable_patch_passes(self):
        self.assertIsNone(ev.patch_is_safe(PATCH_OK, self.ALLOWED))

    def test_absolute_path_is_rejected(self):
        bad = PATCH_OK.replace("a/repo/mod.py", "/etc/passwd")
        self.assertIn("absolute path", ev.patch_is_safe(bad, self.ALLOWED) or "")

    def test_traversal_is_rejected(self):
        bad = PATCH_OK.replace("a/repo/mod.py", "repo/../../etc/passwd")
        reason = ev.patch_is_safe(bad, self.ALLOWED) or ""
        self.assertTrue("traversal" in reason or "outside repo/" in reason, reason)

    def test_a_header_outside_repo_is_rejected(self):
        bad = PATCH_OK.replace("a/repo/mod.py", "scripts/validate.py")
        self.assertIn("outside repo/", ev.patch_is_safe(bad, self.ALLOWED) or "")

    def test_an_undeclared_path_is_rejected(self):
        bad = PATCH_OK.replace("repo/mod.py", "repo/other.py")
        self.assertIn("undeclared", ev.patch_is_safe(bad, self.ALLOWED) or "")

    def test_a_binary_patch_is_rejected(self):
        self.assertIn("binary", ev.patch_is_safe("GIT binary patch\nliteral 0\n", self.ALLOWED) or "")

    def test_a_symlink_mode_is_rejected(self):
        bad = PATCH_OK + "\nnew mode 120000\n"
        self.assertIn("symlink", ev.patch_is_safe(bad, self.ALLOWED) or "")

    def test_an_empty_patch_is_rejected(self):
        self.assertIn("changes nothing", ev.patch_is_safe("no headers here", self.ALLOWED) or "")


class Grading(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.spec = a_case(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def test_a_correct_patch_passes_the_oracle(self):
        outcome, detail = ev.grade(self.spec, PATCH_OK)
        self.assertEqual(outcome, "pass", detail)

    def test_a_patch_that_does_not_apply_fails(self):
        stale = PATCH_OK.replace("-VALUE = 1", "-VALUE = 777")
        outcome, detail = ev.grade(self.spec, stale)
        self.assertEqual(outcome, "fail")
        self.assertIn("apply", detail)

    def test_a_patch_that_applies_but_leaves_the_defect_fails(self):
        wrong = PATCH_OK.replace("+VALUE = 2", "+VALUE = 3")
        outcome, detail = ev.grade(self.spec, wrong)
        self.assertEqual(outcome, "fail")
        self.assertIn("oracle exit", detail)

    def test_a_forbidden_path_never_reaches_the_filesystem(self):
        bad = PATCH_OK.replace("repo/mod.py", "repo/secret.py")
        outcome, detail = ev.grade(self.spec, bad)
        self.assertEqual(outcome, "fail")
        self.assertIn("undeclared", detail)
        self.assertFalse((self.spec["_dir"] / "repo" / "secret.py").exists())

    def test_grading_does_not_mutate_the_fixture(self):
        before = ev.case_digest(self.spec["_dir"])
        ev.grade(self.spec, PATCH_OK)
        self.assertEqual(before, ev.case_digest(self.spec["_dir"]),
                         "the patch is applied to a copy, never to the case")


class Environment(unittest.TestCase):
    def test_the_child_environment_is_an_allowlist_built_from_nothing(self):
        with mock.patch.dict(os.environ, {
                "OPENAI_API_KEY": "sk-should-not-propagate",
                "AWS_SECRET_ACCESS_KEY": "nope", "HTTPS_PROXY": "http://proxy",
                "PYTHONPATH": "/somewhere/else"}, clear=False):
            env = ev.minimal_env(Path("/tmp/x"))
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertNotIn("AWS_SECRET_ACCESS_KEY", env)
        self.assertNotIn("HTTPS_PROXY", env)
        self.assertEqual(env["PYTHONPATH"], "repo", "only the case's own repo/ is importable")
        self.assertEqual(env["PYTHONDONTWRITEBYTECODE"], "1")
        self.assertEqual(env["PYTHONNOUSERSITE"], "1")

    def test_the_oracle_cannot_see_an_api_key(self):
        tmp = tempfile.TemporaryDirectory()
        spec = a_case(Path(tmp.name))
        (spec["_dir"] / "oracle" / "test_oracle.py").write_text(
            "import os, sys\n"
            "sys.exit(1 if any('KEY' in k or 'TOKEN' in k for k in os.environ) else 0)\n",
            encoding="utf-8")
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-live-nope"}, clear=False):
            outcome, detail = ev.grade(dict(spec, allowed_paths=["repo/mod.py"]), PATCH_OK)
        self.assertEqual(outcome, "pass", f"the oracle saw a credential: {detail}")
        tmp.cleanup()


class RunDirectory(unittest.TestCase):
    def test_files_are_written_owner_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "rec.json"
            ev.write_private(target, "{}")
            mode = stat.S_IMODE(target.stat().st_mode)
            self.assertEqual(mode, 0o600, f"expected 0600, got {oct(mode)}")


if __name__ == "__main__":
    unittest.main()
