#!/usr/bin/env python3
"""Unit tests for the paired patch evaluator. No model is called and no money is spent.

    python3 -m unittest tests.test_patch_eval -v

The first thing this module does is make a real network call impossible: socket.socket is replaced
before the evaluator is imported, so any code path that reaches for the network raises instead of
dialling. Every test that needs an API response supplies its own. If CI ever regresses into making a
live call, test_no_real_network_is_reachable fails rather than the bill arriving later.

Docker is never required here either. The container is proven by scripts/run_patch_eval.py
--sandbox-selftest, which is manual; what these tests hold is that the grader always goes through
the container and never around it.
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
from contextlib import contextmanager
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


# --------------------------------------------------------------------------- fixtures in memory


ORACLE = b"import mod, sys\nsys.exit(0 if mod.VALUE == 2 else 1)\n"

PATCH_OK = (
    "--- a/repo/mod.py\n"
    "+++ b/repo/mod.py\n"
    "@@ -1 +1 @@\n"
    "-VALUE = 1\n"
    "+VALUE = 2\n"
)


def frozen_case(case_id="money-core-x", body=b"VALUE = 1\n", allowed=("repo/mod.py",),
                oracle=ORACLE, timeout=10, prompt_digest="pd0"):
    """A FrozenCase built directly, so grading tests need nothing on disk."""
    return ev.FrozenCase(
        id=case_id, target_skill="fin-money-core", baseline_context=(),
        treatment_context=("fin-money-core",), task="Fix it, please, thoroughly.",
        allowed_paths=allowed, timeout_seconds=timeout,
        files=(("case.yaml", b"id: never-mounted\n"),
               ("fix.patch", b"THE REFERENCE ANSWER, WHICH MUST NOT BE MOUNTED\n"),
               ("oracle/test_oracle.py", oracle),
               ("repo/mod.py", body)),
        fixture_digest="fd0", baseline_prompt="BASE", treatment_prompt="TREAT",
        prompt_digest=prompt_digest, references=(("fin-money-core", ("a.md",)),))


def write_world(tmp: Path, case_ids=("ledger-a", "money-b"), baseline=()):
    """A miniature dataset and skills tree on disk, for the paths that really read files."""
    dataset, skills = tmp / "evals" / "behavioral", tmp / "skills"
    for name in ("fin-money-core", "fin-payments"):
        (skills / name / "references").mkdir(parents=True, exist_ok=True)
        (skills / name / "SKILL.md").write_text(f"# {name}\nrules\n", encoding="utf-8")
        (skills / name / "references" / "a.md").write_text(f"{name} reference a\n", encoding="utf-8")
    for case_id in case_ids:
        case = dataset / case_id
        (case / "repo").mkdir(parents=True, exist_ok=True)
        (case / "oracle").mkdir(parents=True, exist_ok=True)
        (case / "repo" / "mod.py").write_text("VALUE = 1\n", encoding="utf-8")
        (case / "oracle" / "test_oracle.py").write_bytes(ORACLE)
        (case / "fix.patch").write_text(PATCH_OK, encoding="utf-8")
        treatment = list(baseline) + ["fin-money-core"]
        refs = "\n".join(f"  {s}:\n    - a.md" for s in treatment)
        (case / "case.yaml").write_text(
            f"id: {case_id}\n"
            f"target_skill: fin-money-core\n"
            f"baseline_context: {list(baseline)}\n"
            f"treatment_context:\n" + "".join(f"  - {s}\n" for s in treatment) +
            f"references:\n{refs}\n"
            f"task: |\n  Please repair the defect in this small repository, thoroughly and "
            f"carefully, without breaking anything else.\n"
            f"allowed_paths:\n  - repo/mod.py\n"
            f"timeout_seconds: 10\n"
            f"defect: d\noracle_proves: p\n", encoding="utf-8")
    return dataset, skills


@contextmanager
def world(tmp: Path, **kwargs):
    dataset, skills = write_world(tmp, **kwargs)
    with mock.patch.object(ev, "DATASET", dataset), mock.patch.object(ev, "SKILLS", skills), \
         mock.patch.object(ev, "DATASET_BASE", tmp):
        yield dataset, skills


def _responds(payload, headers=None):
    body = json.dumps(payload).encode()
    handle = mock.MagicMock()
    handle.read.return_value = body
    handle.__enter__.return_value = handle
    return mock.Mock(return_value=handle)


# --------------------------------------------------------------------------- network


class Network(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_no_real_network_is_reachable(self):
        with self.assertRaises(NetworkUsed):
            socket.socket()

    def test_the_evaluator_dials_nothing_without_yes(self):
        """--dry-run and the missing --yes path must both return before any request is built."""
        for argv in (["--model", "m", "--effort", "low", "--cases", "all", "--dry-run"],
                     ["--model", "m", "--effort", "low", "--cases", "all"]):
            with world(self.tmp):
                with mock.patch.object(ev.urllib.request, "urlopen", side_effect=NetworkUsed):
                    with mock.patch.object(sys, "argv", ["run_patch_eval.py", *argv]):
                        with mock.patch("sys.stdout", new=io.StringIO()) as out:
                            self.assertEqual(ev.main(), 0)
            self.assertIn("would run", out.getvalue())

    def test_no_source_file_passes_unsafe_paths_to_git(self):
        """git apply --unsafe-paths honours a rename target outside the tree. It is gone for good.

        The literal is checked as an argument, not as a substring, so the two comments that explain
        why the flag is absent do not have to be deleted to keep this honest.
        """
        for name in ("run_patch_eval.py", "check_eval_dataset.py"):
            text = (ROOT / "scripts" / name).read_text(encoding="utf-8")
            for literal in ('"--unsafe-paths"', "'--unsafe-paths'"):
                self.assertNotIn(literal, text, f"{name} passes {literal} to git")


# --------------------------------------------------------------------------- §3 path confinement


class PathConfinement(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        _, self.skills = write_world(self.tmp)
        self._patch = mock.patch.object(ev, "SKILLS", self.skills)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()

    def test_an_absolute_reference_is_refused(self):
        with self.assertRaises(ev.ContextError):
            ev.confined_reference("fin-money-core", "/etc/hosts")

    def test_traversal_out_of_the_references_directory_is_refused(self):
        for attempt in ("../SKILL.md", "../../fin-payments/references/a.md",
                        "../../../etc/hosts", "a/../../SKILL.md"):
            with self.assertRaises(ev.ContextError, msg=attempt):
                ev.confined_reference("fin-money-core", attempt)

    def test_a_sibling_skill_cannot_be_reached(self):
        with self.assertRaises(ev.ContextError):
            ev.confined_reference("fin-money-core", "../fin-payments/references/a.md")

    def test_a_symlinked_reference_is_refused(self):
        target = self.skills / "fin-payments" / "references" / "a.md"
        link = self.skills / "fin-money-core" / "references" / "sneaky.md"
        link.symlink_to(target)
        with self.assertRaises(ev.ContextError):
            ev.confined_reference("fin-money-core", "sneaky.md")

    def test_a_symlinked_parent_directory_is_refused(self):
        link_dir = self.skills / "fin-money-core" / "references" / "elsewhere"
        link_dir.symlink_to(self.skills / "fin-payments" / "references")
        with self.assertRaises(ev.ContextError):
            ev.confined_reference("fin-money-core", "elsewhere/a.md")

    def test_an_unknown_skill_is_refused(self):
        with self.assertRaises(ev.ContextError):
            ev.confined_reference("../../etc", "hosts")
        with self.assertRaises(ev.ContextError):
            ev.confined_skill_md("not-a-skill")

    def test_none_of_that_reaches_a_prompt(self):
        """The end-to-end statement: a confined path failure stops the run, it does not degrade."""
        with tempfile.TemporaryDirectory() as tmp2:
            dataset, _ = write_world(Path(tmp2))
            bad = dataset / "ledger-a" / "case.yaml"
            bad.write_text(bad.read_text().replace("    - a.md", "    - ../../../../etc/hosts"),
                           encoding="utf-8")
            with mock.patch.object(ev, "DATASET", dataset), \
                 mock.patch.object(ev, "DATASET_BASE", Path(tmp2)):
                with self.assertRaises(ev.ContextError) as caught:
                    ev.freeze_cases("all", "m", "low", "abc")
        self.assertIn("traversal", str(caught.exception))


# --------------------------------------------------------------------------- §2 patch validation


class PatchValidation(unittest.TestCase):
    ALLOWED = ("repo/mod.py",)
    KNOWN = {"repo/mod.py", "repo/other.py", "oracle/test_oracle.py", "fix.patch", "case.yaml"}

    def reason(self, patch, allowed=None):
        _, why = ev.validate_patch(patch, allowed or self.ALLOWED, self.KNOWN)
        return why or ""

    def test_an_acceptable_patch_passes(self):
        touched, why = ev.validate_patch(PATCH_OK, self.ALLOWED, self.KNOWN)
        self.assertIsNone(why)
        self.assertEqual(touched, {"repo/mod.py"})

    def test_an_allowed_hunk_plus_a_rename_out_of_the_tree_is_rejected(self):
        attack = PATCH_OK + (
            "diff --git a/repo/mod.py b/repo/mod.py\n"
            "similarity index 100%\n"
            "rename from repo/mod.py\n"
            "rename to ../../escaped.py\n"
        )
        self.assertIn("renames", self.reason(attack))

    def test_copy_metadata_is_rejected(self):
        attack = PATCH_OK + ("copy from repo/mod.py\ncopy to repo/copy.py\n")
        self.assertIn("copies", self.reason(attack))

    def test_a_second_hidden_diff_block_is_rejected(self):
        attack = PATCH_OK + (
            "diff --git a/repo/other.py b/repo/other.py\n"
            "--- a/repo/other.py\n"
            "+++ b/repo/other.py\n"
            "@@ -1 +1 @@\n"
            "-x = 1\n"
            "+x = 2\n"
        )
        self.assertIn("undeclared path: repo/other.py", self.reason(attack))

    def test_dev_null_is_rejected(self):
        for attack in (PATCH_OK.replace("--- a/repo/mod.py", "--- /dev/null"),
                       PATCH_OK.replace("+++ b/repo/mod.py", "+++ /dev/null")):
            self.assertIn("adds or deletes a file", self.reason(attack))

    def test_an_absolute_rename_target_is_rejected(self):
        attack = PATCH_OK + "rename to /etc/cron.d/pwn\n"
        self.assertIn("renames", self.reason(attack))

    def test_an_absolute_header_is_rejected(self):
        self.assertIn("absolute path", self.reason(PATCH_OK.replace("a/repo/mod.py", "/etc/passwd")))

    def test_a_mode_only_change_is_rejected(self):
        attack = ("diff --git a/repo/mod.py b/repo/mod.py\n"
                  "old mode 100644\nnew mode 100755\n")
        self.assertIn("mode changes", self.reason(attack))

    def test_a_new_file_is_rejected(self):
        attack = ("diff --git a/repo/mod.py b/repo/mod.py\n"
                  "new file mode 100644\n--- a/repo/mod.py\n+++ b/repo/mod.py\n@@ -0,0 +1 @@\n+x\n")
        self.assertIn("new files", self.reason(attack))

    def test_a_symlink_mode_is_rejected(self):
        attack = "diff --git a/repo/mod.py b/repo/mod.py\nindex 0000000..1111111 120000\n"
        self.assertIn("symlink", self.reason(attack))

    def test_a_binary_patch_is_rejected(self):
        self.assertIn("binary", self.reason("GIT binary patch\nliteral 0\n"))
        self.assertIn("binary", self.reason("Binary files a/repo/mod.py and b/repo/mod.py differ\n"))

    def test_a_submodule_is_rejected(self):
        self.assertIn("submodules", self.reason(PATCH_OK + "Subproject commit deadbeef\n"))

    def test_traversal_in_a_header_is_rejected(self):
        why = self.reason(PATCH_OK.replace("a/repo/mod.py", "repo/../../etc/passwd"))
        self.assertTrue("traversal" in why or "outside repo/" in why, why)

    def test_a_header_outside_repo_is_rejected(self):
        self.assertIn("outside repo/", self.reason(PATCH_OK.replace("a/repo/mod.py",
                                                                    "scripts/validate.py")))

    def test_a_path_that_is_not_in_the_case_is_rejected(self):
        self.assertIn("not a file in this case",
                      self.reason(PATCH_OK.replace("repo/mod.py", "repo/invented.py")))

    def test_an_undeclared_but_existing_path_is_rejected(self):
        self.assertIn("undeclared", self.reason(PATCH_OK.replace("repo/mod.py", "repo/other.py")))

    def test_a_diff_header_that_moves_a_file_is_rejected(self):
        attack = ("diff --git a/repo/mod.py b/repo/other.py\n"
                  "--- a/repo/mod.py\n+++ b/repo/other.py\n@@ -1 +1 @@\n-a\n+b\n")
        why = self.reason(attack, allowed=("repo/mod.py", "repo/other.py"))
        self.assertIn("moves", why)

    def test_a_patch_with_no_hunk_changes_nothing(self):
        self.assertIn("no hunk", self.reason("--- a/repo/mod.py\n+++ b/repo/mod.py\n"))

    def test_a_patch_with_no_headers_is_rejected(self):
        self.assertIn("names no file", self.reason("just some prose about the repair"))


class InventoryDrift(unittest.TestCase):
    def test_a_created_path_is_caught(self):
        before = {"repo/mod.py": "file:a"}
        after = {"repo/mod.py": "file:a", "repo/new.py": "file:b"}
        self.assertIn("created", ev.inventory_drift(before, after, {"repo/mod.py"}) or "")

    def test_a_removed_path_is_caught(self):
        self.assertIn("removed", ev.inventory_drift({"a": "file:1", "b": "file:2"},
                                                    {"a": "file:1"}, {"a"}) or "")

    def test_a_type_change_is_caught(self):
        self.assertIn("changed type", ev.inventory_drift({"a": "file:1"}, {"a": "symlink"},
                                                         {"a"}) or "")

    def test_an_undeclared_content_change_is_caught(self):
        why = ev.inventory_drift({"a": "file:1", "b": "file:2"},
                                 {"a": "file:1", "b": "file:CHANGED"}, {"a"})
        self.assertIn("undeclared path: b", why or "")

    def test_a_declared_change_is_fine(self):
        self.assertIsNone(ev.inventory_drift({"a": "file:1"}, {"a": "file:2"}, {"a"}))


# --------------------------------------------------------------------------- §4 API classification


class ApiClassification(unittest.TestCase):
    """Anything that is not a completed, parseable, patch-bearing response is invalid, never fail."""

    def _call(self, urlopen_side, sleeper=None):
        with mock.patch.object(ev.urllib.request, "urlopen", urlopen_side):
            return ev.call_model("KEY-NOT-LOGGED", "m", "low", "prompt",
                                 sleeper=sleeper or (lambda _s: None))

    def test_a_completed_response_carrying_a_patch_is_the_only_accepted_shape(self):
        status, rec = self._call(_responds({
            "status": "completed",
            "output_text": json.dumps({"patch": PATCH_OK, "summary": "s"}),
            "usage": {"input_tokens": 11, "output_tokens": 22}}))
        self.assertEqual(status, "ok")
        self.assertEqual(rec["patch"], PATCH_OK)
        self.assertEqual((rec["input_tokens"], rec["output_tokens"]), (11, 22))

    def test_every_non_completed_status_is_invalid(self):
        for status in ("incomplete", "failed", "cancelled", "queued", "in_progress"):
            got, rec = self._call(_responds({"status": status, "output_text": "{}"}))
            self.assertEqual(got, "invalid", status)
            self.assertIn(status, rec["reason"])

    def test_an_unknown_status_is_invalid(self):
        got, rec = self._call(_responds({"status": "wobbling", "output_text": "{}"}))
        self.assertEqual(got, "invalid")
        self.assertIn("unknown status", rec["reason"])

    def test_a_missing_status_is_invalid(self):
        got, rec = self._call(_responds({"output_text": "{}"}))
        self.assertEqual(got, "invalid")
        self.assertIn("unknown status", rec["reason"])

    def test_completed_without_valid_structured_output_is_invalid(self):
        cases = {
            "not json": "did not parse",
            json.dumps({"summary": "done"}): "no patch",
            json.dumps({"patch": "   "}): "no patch",
            json.dumps(["a list"]): "no patch",
        }
        for text, expected in cases.items():
            got, rec = self._call(_responds({"status": "completed", "output_text": text}))
            self.assertEqual(got, "invalid", text[:30])
            self.assertIn(expected, rec["reason"])

    def test_completed_with_no_output_text_at_all_is_invalid(self):
        got, rec = self._call(_responds({"status": "completed", "output": []}))
        self.assertEqual(got, "invalid")
        self.assertIn("no output text", rec["reason"])

    def test_the_api_key_is_not_written_into_any_record(self):
        _, rec = self._call(_responds({"status": "completed",
                                       "output_text": json.dumps({"patch": PATCH_OK,
                                                                  "summary": "s"})}))
        self.assertNotIn("KEY-NOT-LOGGED", json.dumps(rec))


class Retries(unittest.TestCase):
    """Bounded, and only for the three things a retry can actually fix."""

    def _run(self, side_effects):
        slept = []
        urlopen = mock.Mock(side_effect=side_effects)
        with mock.patch.object(ev.urllib.request, "urlopen", urlopen):
            status, rec = ev.call_model("K", "m", "low", "p", sleeper=slept.append)
        return status, rec, urlopen.call_count, slept

    @staticmethod
    def _good():
        handle = mock.MagicMock()
        handle.read.return_value = json.dumps({
            "status": "completed",
            "output_text": json.dumps({"patch": PATCH_OK, "summary": ""})}).encode()
        handle.__enter__.return_value = handle
        return handle

    @staticmethod
    def _http(code, retry_after=None):
        headers = {"Retry-After": retry_after} if retry_after is not None else {}
        return urllib.error.HTTPError("u", code, "boom", headers, None)

    def test_a_429_is_retried_and_can_succeed(self):
        status, _, calls, slept = self._run([self._http(429), self._good()])
        self.assertEqual((status, calls), ("ok", 2))
        self.assertEqual(len(slept), 1)

    def test_a_500_is_retried(self):
        status, _, calls, _ = self._run([self._http(503), self._http(500), self._good()])
        self.assertEqual((status, calls), ("ok", 3))

    def test_retries_stop_at_the_ceiling(self):
        status, rec, calls, slept = self._run([self._http(500)] * 6)
        self.assertEqual(status, "invalid")
        self.assertEqual(calls, ev.MAX_RETRIES + 1, "one attempt plus at most MAX_RETRIES retries")
        self.assertEqual(rec["attempts"], ev.MAX_RETRIES + 1)
        self.assertEqual(len(slept), ev.MAX_RETRIES)

    def test_retry_after_is_honoured_and_capped(self):
        _, _, _, slept = self._run([self._http(429, "7"), self._good()])
        self.assertEqual(slept, [7.0])
        _, _, _, slept = self._run([self._http(429, "99999"), self._good()])
        self.assertEqual(slept, [ev.RETRY_AFTER_CEILING_S])

    def test_a_nonsense_retry_after_falls_back_to_backoff(self):
        _, _, _, slept = self._run([self._http(429, "soon"), self._good()])
        self.assertEqual(slept, [ev.BACKOFF_BASE_S])

    def test_backoff_is_exponential_under_a_hard_ceiling(self):
        self.assertEqual(ev._backoff(0), ev.BACKOFF_BASE_S)
        self.assertEqual(ev._backoff(1), ev.BACKOFF_BASE_S * 2)
        self.assertLessEqual(ev._backoff(40), ev.BACKOFF_CEILING_S)

    def test_a_transport_timeout_is_retried(self):
        status, _, calls, _ = self._run([TimeoutError(), self._good()])
        self.assertEqual((status, calls), ("ok", 2))
        status, _, calls, _ = self._run([urllib.error.URLError(TimeoutError()), self._good()])
        self.assertEqual((status, calls), ("ok", 2))

    def test_a_permanent_4xx_is_never_retried(self):
        for code in (400, 401, 403, 404, 422):
            status, rec, calls, slept = self._run([self._http(code)] * 4)
            self.assertEqual((status, calls, slept), ("invalid", 1, []), code)
            self.assertIn(f"http {code}", rec["reason"])

    def test_a_schema_failure_is_never_retried(self):
        handle = mock.MagicMock()
        handle.read.return_value = json.dumps({"status": "completed",
                                               "output_text": "not json"}).encode()
        handle.__enter__.return_value = handle
        status, rec, calls, slept = self._run([handle, self._good()])
        self.assertEqual((status, calls, slept), ("invalid", 1, []))
        self.assertIn("did not parse", rec["reason"])


# --------------------------------------------------------------------------- §5 frozen prompts


class FrozenPrompts(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_the_two_arms_differ_only_by_the_skill_context(self):
        with world(self.tmp):
            case = ev.freeze_cases("all", "m", "low", "abc")[0]
        self.assertNotIn("<skill_context>", case.baseline_prompt)
        self.assertIn("<skill_context>", case.treatment_prompt)
        self.assertIn("fin-money-core", case.treatment_prompt)
        for block in ("<task>", "<repository>", "repo/mod.py"):
            self.assertIn(block, case.baseline_prompt)
            self.assertIn(block, case.treatment_prompt)

    def test_a_layered_baseline_carries_the_domain_skill_in_both_arms(self):
        with world(self.tmp, case_ids=("verification-a",), baseline=("fin-payments",)):
            case = ev.freeze_cases("all", "m", "low", "abc")[0]
        self.assertIn("fin-payments", case.baseline_prompt)
        self.assertIn("fin-payments", case.treatment_prompt)
        self.assertNotIn("fin-money-core", case.baseline_prompt)
        self.assertIn("fin-money-core", case.treatment_prompt)

    def test_an_edit_after_freezing_cannot_change_the_active_run(self):
        with world(self.tmp) as (dataset, skills):
            case = ev.freeze_cases("all", "m", "low", "abc")[0]
            key = ev.pair_key("run-1", case, 0)
            baseline, treatment, digest = (case.baseline_prompt, case.treatment_prompt,
                                           case.prompt_digest)

            (dataset / case.id / "repo" / "mod.py").write_text("VALUE = 999\n", encoding="utf-8")
            (skills / "fin-money-core" / "SKILL.md").write_text("# rewritten\n", encoding="utf-8")

            self.assertEqual(case.baseline_prompt, baseline, "the frozen prompt must not move")
            self.assertEqual(case.treatment_prompt, treatment)
            self.assertEqual(case.prompt_digest, digest)
            self.assertEqual(ev.pair_key("run-1", case, 0), key, "and neither must the key")

            reloaded = ev.freeze_cases("all", "m", "low", "abc")[0]
        self.assertNotEqual(reloaded.prompt_digest, digest, "a NEW run must see a new digest")
        self.assertNotEqual(reloaded.fixture_digest, case.fixture_digest)

    def test_the_digest_binds_every_declared_ingredient(self):
        with world(self.tmp):
            base = ev.freeze_cases("all", "m", "low", "abc")[0].prompt_digest
            self.assertNotEqual(base, ev.freeze_cases("all", "OTHER", "low", "abc")[0].prompt_digest)
            self.assertNotEqual(base, ev.freeze_cases("all", "m", "high", "abc")[0].prompt_digest)
            self.assertNotEqual(base, ev.freeze_cases("all", "m", "low", "OTHER")[0].prompt_digest)

    def test_the_grader_reads_the_frozen_bytes_not_the_disk(self):
        with world(self.tmp) as (dataset, _):
            case = ev.freeze_cases("all", "m", "low", "abc")[0]
            (dataset / case.id / "repo" / "mod.py").write_text("VALUE = 999\n", encoding="utf-8")
            with tempfile.TemporaryDirectory() as out:
                ev.materialise(case, Path(out))
                self.assertEqual((Path(out) / "repo" / "mod.py").read_text(), "VALUE = 1\n")


class Schema(unittest.TestCase):
    """The closed schema and the one rule that makes the comparison layered rather than binary."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _spec_with(self, replacement):
        dataset, skills = write_world(self.tmp, case_ids=("ledger-a",))
        path = dataset / "ledger-a" / "case.yaml"
        path.write_text(replacement, encoding="utf-8")
        with mock.patch.object(ev, "SKILLS", skills):
            with self.assertRaises(ev.ContextError) as caught:
                ev.load_spec(dataset / "ledger-a")
        return str(caught.exception)

    BASE = ("id: ledger-a\ntarget_skill: fin-money-core\n"
            "baseline_context: {baseline}\ntreatment_context:\n{treatment}"
            "references:\n{refs}"
            "task: |\n  words words words words words words words words words words words "
            "words words words words words\n"
            "allowed_paths:\n  - repo/mod.py\ntimeout_seconds: 10\ndefect: d\noracle_proves: p\n")

    def test_treatment_must_be_baseline_plus_the_target(self):
        why = self._spec_with(self.BASE.format(
            baseline="[]", treatment="  - fin-payments\n", refs="  fin-payments:\n    - a.md\n"))
        self.assertIn("baseline_context with target_skill appended last", why)

    def test_the_target_may_not_already_be_in_the_baseline(self):
        why = self._spec_with(self.BASE.format(
            baseline="['fin-money-core']",
            treatment="  - fin-money-core\n  - fin-money-core\n",
            refs="  fin-money-core:\n    - a.md\n"))
        self.assertIn("would not differ", why)

    def test_every_context_skill_needs_references(self):
        why = self._spec_with(self.BASE.format(
            baseline="['fin-payments']",
            treatment="  - fin-payments\n  - fin-money-core\n",
            refs="  fin-money-core:\n    - a.md\n"))
        self.assertIn("no non-empty list for 'fin-payments'", why)

    def test_references_may_not_name_a_skill_outside_the_treatment(self):
        why = self._spec_with(self.BASE.format(
            baseline="[]", treatment="  - fin-money-core\n",
            refs="  fin-money-core:\n    - a.md\n  fin-payments:\n    - a.md\n"))
        self.assertIn("which is not in treatment_context", why)

    def test_an_unknown_key_is_refused(self):
        why = self._spec_with(self.BASE.format(
            baseline="[]", treatment="  - fin-money-core\n",
            refs="  fin-money-core:\n    - a.md\n") + "oracle: /bin/sh -c pwn\n")
        self.assertIn("unknown keys ['oracle']", why)


# --------------------------------------------------------------------------- §6 §7 §8 pairing


class RunIdentity(unittest.TestCase):
    def setUp(self):
        self.case = frozen_case()

    def test_the_pair_key_binds_the_run_and_the_frozen_prompts(self):
        key = ev.pair_key("run-1", self.case, 0)
        self.assertNotEqual(key, ev.pair_key("run-2", self.case, 0))
        self.assertNotEqual(key, ev.pair_key("run-1", self.case, 1))
        other = frozen_case(prompt_digest="DIFFERENT")
        self.assertNotEqual(key, ev.pair_key("run-1", other, 0))
        self.assertEqual(key, ev.pair_key("run-1", frozen_case(), 0), "and is stable")

    def _rec(self, run_id, arm, outcome="pass", key="k", case="c", repeat=0):
        return {"run_id": run_id, "pair_key": key, "arm": arm, "outcome": outcome,
                "oracle_passed": outcome == "pass", "case": case, "repeat": repeat,
                "order": "baseline-treatment"}

    def test_a_record_from_another_run_never_pairs(self):
        complete, excluded = ev.pair_records(
            [self._rec("A", "baseline"), self._rec("B", "treatment")], "A")
        self.assertEqual(len(complete), 0)
        self.assertIn("another run", excluded[0]["why"])

    def test_a_duplicate_arm_is_excluded(self):
        complete, excluded = ev.pair_records(
            [self._rec("A", "baseline"), self._rec("A", "baseline"), self._rec("A", "treatment")],
            "A")
        self.assertEqual(len(complete), 0)
        self.assertIn("duplicate arm", excluded[0]["why"])

    def test_a_lonely_arm_is_excluded(self):
        complete, excluded = ev.pair_records([self._rec("A", "baseline")], "A")
        self.assertEqual(len(complete), 0)
        self.assertIn("only the ['baseline'] arm", excluded[0]["why"])

    def test_a_well_formed_pair_is_complete(self):
        complete, excluded = ev.pair_records(
            [self._rec("A", "baseline"), self._rec("A", "treatment")], "A")
        self.assertEqual((len(complete), excluded), (1, []))


class InvalidPairsAreExcluded(unittest.TestCase):
    def _summary(self, records, run_id="A"):
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            ev.summarise(records, repeats=1, run_id=run_id)
        return out.getvalue()

    def _rec(self, arm, outcome, key="k", latency=1.0, tokens=100):
        return {"run_id": "A", "pair_key": key, "arm": arm, "outcome": outcome,
                "oracle_passed": outcome == "pass", "case": "case-a", "repeat": 0,
                "order": "baseline-treatment", "reason": "r", "latency_s": latency,
                "input_tokens": tokens, "output_tokens": tokens}

    def test_an_invalid_pass_pair_produces_complete_zero(self):
        text = self._summary([self._rec("baseline", "invalid"), self._rec("treatment", "pass")])
        self.assertIn("complete pairs 0", text)
        self.assertIn("the baseline arm was invalid", text)
        self.assertIn("treatment-only wins 0", text,
                      "an invalid baseline arm cannot make treatment a winner")
        self.assertIn("both pass 0", text)

    def test_an_invalid_arm_is_kept_out_of_the_paired_cost_comparison(self):
        text = self._summary([self._rec("baseline", "invalid", latency=99.0, tokens=9999),
                              self._rec("treatment", "pass", latency=1.0, tokens=100)])
        self.assertNotIn("9999", text.split("paired delta")[0].split("cost, over calls")[-1])
        self.assertIn("invalid", text)

    def test_marginal_rates_distinguish_all_calls_from_paired_calls(self):
        records = [self._rec("baseline", "invalid", key="k1"),
                   self._rec("treatment", "pass", key="k1"),
                   self._rec("baseline", "pass", key="k2"),
                   self._rec("treatment", "pass", key="k2")]
        text = self._summary(records)
        self.assertIn("complete pairs 1", text)
        rates = text.split("marginal arm rates")[1]
        line = [ln for ln in rates.splitlines() if ln.strip().startswith("treatment")][0]
        self.assertIn("2/2", line, "both completed treatment calls count in the marginal rate")
        self.assertIn("1/1", line, "only one of them is inside a complete pair")

    def test_the_displayed_sample_size_is_the_complete_pair_count(self):
        text = self._summary([self._rec("baseline", "invalid"), self._rec("treatment", "pass")])
        self.assertIn("(n = 0)", text)


class ArmSchedule(unittest.TestCase):
    SHIPPED = tuple(sorted(d.name for d in (ROOT / "evals" / "behavioral").iterdir()
                           if d.is_dir()))

    def test_the_shipped_default_schedule_is_exactly_eighteen_and_eighteen(self):
        """12 cases at 3 repeats. Not 'both orders occur somewhere' - the exact split."""
        self.assertEqual(len(self.SHIPPED), 12, "the suite is 12 cases")
        firsts = [ev.arm_order(self.SHIPPED, case, repeat)[0]
                  for case in self.SHIPPED for repeat in range(3)]
        self.assertEqual(firsts.count("baseline"), 18)
        self.assertEqual(firsts.count("treatment"), 18)
        self.assertEqual(len(firsts), 36)

    def test_no_case_leads_with_the_same_arm_on_every_repeat(self):
        for case in self.SHIPPED:
            firsts = {ev.arm_order(self.SHIPPED, case, r)[0] for r in range(3)}
            self.assertEqual(firsts, {"baseline", "treatment"}, case)

    def test_the_order_alternates_by_repeat_and_is_stable(self):
        ids = tuple(f"case-{i:02d}" for i in range(12))
        for case in ids:
            for repeat in range(4):
                self.assertNotEqual(ev.arm_order(ids, case, repeat),
                                    ev.arm_order(ids, case, repeat + 1))
                self.assertEqual(ev.arm_order(ids, case, repeat),
                                 ev.arm_order(ids, case, repeat), "and is stable")

    def test_the_starting_arm_comes_from_the_sorted_position(self):
        ids = ("zeta", "alpha", "mu")
        self.assertEqual(ev.arm_order(ids, "alpha", 0)[0], "baseline")   # index 0
        self.assertEqual(ev.arm_order(ids, "mu", 0)[0], "treatment")     # index 1
        self.assertEqual(ev.arm_order(ids, "zeta", 0)[0], "baseline")    # index 2

    def test_an_unknown_case_is_refused(self):
        with self.assertRaises(ev.ContextError):
            ev.arm_order(("a", "b"), "c", 0)

    def test_the_arms_are_named_baseline_and_treatment(self):
        self.assertEqual(ev.ARMS, ("baseline", "treatment"))
        for text in ((ROOT / "scripts" / "run_patch_eval.py").read_text(encoding="utf-8"),
                     (ROOT / "docs" / "evaluation.md").read_text(encoding="utf-8")):
            self.assertNotIn("skills-on", text)
            self.assertNotIn("skills-off", text)


# --------------------------------------------------------------------------- §1 §11 grading


class Grading(unittest.TestCase):
    """The grader always goes through the container, and always reports explicit fields."""

    def setUp(self):
        self.case = frozen_case()

    def _grade(self, patch, sandbox=(0, "")):
        seen = {}

        def fake(case_root, argv, timeout, label):
            seen["root"] = Path(case_root)
            seen["argv"] = argv
            seen["timeout"] = timeout
            seen["tree"] = {p.relative_to(case_root).as_posix(): p.read_bytes()
                            for p in Path(case_root).rglob("*") if p.is_file()}
            return sandbox

        with mock.patch.object(ev, "run_in_sandbox", side_effect=fake) as call:
            result = ev.grade(self.case, patch)
        return result, seen, call

    def test_a_patch_the_oracle_accepts(self):
        result, seen, _ = self._grade(PATCH_OK, sandbox=(0, ""))
        self.assertEqual(result["outcome"], "pass")
        self.assertTrue(result["patch_valid"] and result["patch_applied"])
        self.assertTrue(result["oracle_completed"] and result["oracle_passed"])
        self.assertEqual(seen["tree"]["repo/mod.py"], b"VALUE = 2\n",
                         "the patch really was applied to the tree the oracle saw")
        self.assertEqual(seen["argv"], ["-S", "oracle/test_oracle.py"])

    def test_a_patch_the_oracle_rejects(self):
        result, _, _ = self._grade(PATCH_OK, sandbox=(1, "assertion"))
        self.assertEqual(result["outcome"], "fail")
        self.assertTrue(result["patch_applied"] and result["oracle_completed"])
        self.assertFalse(result["oracle_passed"])
        self.assertIn("rejected the patch", result["reason"])

    def test_a_timeout_still_reports_the_patch_as_applied(self):
        result, _, _ = self._grade(PATCH_OK, sandbox=(124, "the oracle exceeded its 10s wall"))
        self.assertTrue(result["patch_applied"], "applying and then timing out is not 'never applied'")
        self.assertFalse(result["oracle_completed"])
        self.assertFalse(result["oracle_passed"])
        self.assertEqual(result["outcome"], "fail")
        self.assertIn("wall", result["reason"])

    def test_an_invalid_patch_never_reaches_the_container(self):
        for bad in (PATCH_OK.replace("repo/mod.py", "repo/other.py"),
                    PATCH_OK + "rename to ../../escaped.py\n",
                    "GIT binary patch\n"):
            result, _, call = self._grade(bad)
            self.assertFalse(result["patch_valid"], bad[:40])
            self.assertFalse(result["patch_applied"])
            self.assertEqual(call.call_count, 0, "nothing ran")

    def test_a_patch_that_does_not_apply_reports_so(self):
        result, _, call = self._grade(PATCH_OK.replace("-VALUE = 1", "-VALUE = 777"))
        self.assertTrue(result["patch_valid"])
        self.assertFalse(result["patch_applied"])
        self.assertIn("git apply", result["reason"])
        self.assertEqual(call.call_count, 0)

    def test_the_reference_answer_is_never_mounted(self):
        _, seen, _ = self._grade(PATCH_OK)
        self.assertNotIn("fix.patch", seen["tree"])
        self.assertNotIn("case.yaml", seen["tree"])
        self.assertIn("oracle/test_oracle.py", seen["tree"])

    def test_the_candidate_patch_file_is_not_left_in_the_graded_tree(self):
        _, seen, _ = self._grade(PATCH_OK)
        self.assertNotIn("candidate.patch", seen["tree"])

    def test_grading_uses_the_case_timeout(self):
        self.case = frozen_case(timeout=42)
        _, seen, _ = self._grade(PATCH_OK)
        self.assertEqual(seen["timeout"], 42)


class SandboxContract(unittest.TestCase):
    """Docker is not required to run these; the command it would issue is checked as a string."""

    def test_the_image_is_pinned_by_digest(self):
        self.assertRegex(ev.SANDBOX_IMAGE, r"^python@sha256:[0-9a-f]{64}$")

    def test_every_confinement_flag_is_present(self):
        command = ev.sandbox_command("/usr/bin/docker", "n", Path("/tmp/case"), ["-S", "x.py"])
        text = " ".join(command)
        for flag in ("--network none", "--cap-drop ALL", "--security-opt no-new-privileges",
                     "--read-only", "--pids-limit", "--memory", "--cpus",
                     "--user 65534:65534", "--entrypoint python3"):
            self.assertIn(flag, text, flag)
        self.assertIn("type=bind,source=/tmp/case,target=/case,readonly", text)
        self.assertIn("noexec,nosuid,nodev", text)
        self.assertTrue(text.endswith("-S x.py"))
        self.assertIn(ev.SANDBOX_IMAGE, command)

    def test_nothing_of_the_host_is_mounted(self):
        text = " ".join(ev.sandbox_command("/usr/bin/docker", "n", Path("/tmp/case"), []))
        for forbidden in ("docker.sock", str(ROOT), "/root", ".ssh", ".gitconfig",
                          "--privileged", "--cap-add", "--network host", "-v "):
            self.assertNotIn(forbidden, text, forbidden)

    def test_no_host_environment_reaches_the_container(self):
        command = ev.sandbox_command("/usr/bin/docker", "n", Path("/tmp/case"), [])
        passed = [command[i + 1] for i, part in enumerate(command) if part == "-e"]
        self.assertEqual(sorted(passed), sorted([
            "PYTHONPATH=repo", "PYTHONDONTWRITEBYTECODE=1", "PYTHONNOUSERSITE=1",
            "PYTHONHASHSEED=0", "HOME=/sandbox", "TMPDIR=/sandbox", "LC_ALL=C", "LANG=C"]))

    def test_the_docker_cli_environment_carries_no_credential(self):
        with mock.patch.dict(os.environ, {
                "OPENAI_API_KEY": "sk-should-not-propagate", "AWS_SECRET_ACCESS_KEY": "nope",
                "HTTPS_PROXY": "http://proxy", "DOCKER_HOST": "unix:///var/run/docker.sock"},
                clear=False):
            env = ev.docker_cli_env()
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertNotIn("AWS_SECRET_ACCESS_KEY", env)
        self.assertNotIn("HTTPS_PROXY", env)
        self.assertEqual(env["DOCKER_HOST"], "unix:///var/run/docker.sock")

    def test_a_paid_run_refuses_to_start_without_docker(self):
        with tempfile.TemporaryDirectory() as tmp:
            with world(Path(tmp)):
                with mock.patch.object(ev, "sandbox_preflight",
                                       return_value="docker is not on PATH"):
                    with mock.patch.object(ev, "require_clean_tree"):
                        with mock.patch.object(sys, "argv", [
                                "run_patch_eval.py", "--model", "m", "--effort", "low",
                                "--cases", "all", "--yes", "--max-calls", "99"]):
                            with mock.patch("sys.stdout", new=io.StringIO()):
                                with self.assertRaises(SystemExit) as caught:
                                    ev.main()
        self.assertIn("no local fallback", str(caught.exception))


# --------------------------------------------------------------------------- §12 ceilings


class Ceilings(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    @contextmanager
    def _paid(self, argv, call_side):
        runs = self.tmp / "runs"
        with world(self.tmp):
            with mock.patch.object(ev, "RUNS", runs), \
                 mock.patch.object(ev, "require_clean_tree"), \
                 mock.patch.object(ev, "sandbox_preflight", return_value=None), \
                 mock.patch.object(ev, "call_model", side_effect=call_side), \
                 mock.patch.object(ev, "run_in_sandbox", return_value=(0, "")), \
                 mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}, clear=False), \
                 mock.patch.object(sys, "argv", ["run_patch_eval.py", *argv]), \
                 mock.patch("sys.stdout", new=io.StringIO()) as out:
                yield out, runs

    def test_a_paid_run_requires_max_calls(self):
        with self._paid(["--model", "m", "--effort", "low", "--cases", "all", "--yes"],
                        lambda *a, **k: ("ok", {})):
            with self.assertRaises(SystemExit) as caught:
                ev.main()
        self.assertIn("--max-calls", str(caught.exception))

    def test_a_plan_larger_than_the_ceiling_is_refused(self):
        with self._paid(["--model", "m", "--effort", "low", "--cases", "all",
                         "--yes", "--max-calls", "2"], lambda *a, **k: ("ok", {})):
            with self.assertRaises(SystemExit) as caught:
                ev.main()
        self.assertIn("--max-calls is 2", str(caught.exception))

    def test_repeats_have_a_hard_ceiling(self):
        with mock.patch.object(sys, "argv", ["run_patch_eval.py", "--model", "m", "--effort",
                                             "low", "--repeats", str(ev.MAX_REPEATS + 1),
                                             "--cases", "all", "--dry-run"]):
            with self.assertRaises(SystemExit) as caught:
                ev.main()
        self.assertIn(f"between 1 and {ev.MAX_REPEATS}", str(caught.exception))

    def test_the_invalid_circuit_breaker_stops_the_run_and_keeps_what_it_had(self):
        def always_invalid(*args, **kwargs):
            return "invalid", {"reason": "http 500", "latency_s": 0.1, "attempts": 3}

        argv = ["--model", "m", "--effort", "low", "--cases", "all", "--repeats", "3",
                "--yes", "--max-calls", "99", "--max-invalid", "2"]
        with self._paid(argv, always_invalid) as (out, runs):
            code = ev.main()
        self.assertEqual(code, 1)
        text = out.getvalue()
        self.assertIn("STOPPING", text)
        self.assertIn("INCOMPLETE", text)
        run_dir = next(runs.iterdir())
        manifest = json.loads((run_dir / "manifest.json").read_text())
        self.assertFalse(manifest["complete"])
        self.assertIn("--max-invalid", manifest["stopped_because"])
        self.assertEqual(len(json.loads((run_dir / "records.json").read_text())), 2,
                         "the records it did get are preserved")

    def test_a_complete_paid_run_writes_pairs_and_a_finished_manifest(self):
        def good(*args, **kwargs):
            return "ok", {"patch": PATCH_OK, "summary": "s", "latency_s": 1.0, "attempts": 1,
                          "api_status": "completed", "input_tokens": 10, "output_tokens": 5}

        argv = ["--model", "m", "--effort", "low", "--cases", "all", "--repeats", "1",
                "--yes", "--max-calls", "99"]
        with self._paid(argv, good) as (out, runs):
            self.assertEqual(ev.main(), 0)
        text = out.getvalue()
        self.assertIn("complete pairs 2", text)
        self.assertIn("both pass 2", text)
        run_dir = next(runs.iterdir())
        manifest = json.loads((run_dir / "manifest.json").read_text())
        self.assertTrue(manifest["complete"])
        self.assertEqual(manifest["max_calls"], 99)
        self.assertRegex(manifest["run_id"], r"^[0-9a-f]{32}$")
        records = json.loads((run_dir / "records.json").read_text())
        self.assertTrue(all(r["run_id"] == manifest["run_id"] for r in records))
        self.assertTrue(all(r["prompt_digest"] == next(
            c["prompt_digest"] for c in manifest["cases"] if c["id"] == r["case"])
            for r in records), "every record is checked back against the manifest")

    def test_the_dry_run_prints_the_plan_and_the_exact_call_count(self):
        with world(self.tmp):
            with mock.patch.object(sys, "argv", [
                    "run_patch_eval.py", "--model", "m", "--effort", "low",
                    "--cases", "all", "--repeats", "3", "--dry-run"]):
                with mock.patch("sys.stdout", new=io.StringIO()) as out:
                    self.assertEqual(ev.main(), 0)
        text = out.getvalue()
        self.assertIn("2 case(s) x 3 repeat(s) x 2 arm(s) = 12 call(s)", text)
        self.assertIn("3 baseline-first", text)
        self.assertIn("3 treatment-first", text)
        self.assertIn("Dry run: no network access was attempted.", text)
        self.assertNotIn("$", text, "no monetary estimate is claimed anywhere")


# --------------------------------------------------------------------------- run directory


class RunDirectory(unittest.TestCase):
    def test_files_are_written_owner_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "rec.json"
            ev.write_private(target, "{}")
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)

    def test_runs_land_under_a_gitignored_directory(self):
        self.assertEqual(ev.RUNS.relative_to(ROOT).parts[0], ".agent")
        self.assertIn(".agent/", (ROOT / ".gitignore").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()


# --------------------------------------------------------------------------- §1 symlinked bases


class SymlinkedBases(unittest.TestCase):
    """A symlink standing in for a directory is followed silently by resolve(). It must not be."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        _, self.skills = write_world(self.tmp)
        self.elsewhere = self.tmp / "elsewhere"
        (self.elsewhere / "references").mkdir(parents=True)
        (self.elsewhere / "SKILL.md").write_text("# not reviewed\n", encoding="utf-8")
        (self.elsewhere / "references" / "a.md").write_text("# not reviewed\n", encoding="utf-8")
        self._patch = mock.patch.object(ev, "SKILLS", self.skills)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()

    def test_a_symlinked_references_directory_is_refused(self):
        refs = self.skills / "fin-money-core" / "references"
        shutil_rmtree(refs)
        refs.symlink_to(self.elsewhere / "references")
        with self.assertRaises(ev.ContextError) as caught:
            ev.confined_reference("fin-money-core", "a.md")
        self.assertIn("symlink", str(caught.exception))

    def test_a_symlinked_skill_directory_is_refused(self):
        skill = self.skills / "fin-money-core"
        shutil_rmtree(skill)
        skill.symlink_to(self.elsewhere)
        for call in (lambda: ev.confined_reference("fin-money-core", "a.md"),
                     lambda: ev.confined_skill_md("fin-money-core")):
            with self.assertRaises(ev.ContextError) as caught:
                call()
            self.assertIn("symlink", str(caught.exception))

    def test_a_symlinked_trusted_root_is_refused(self):
        link = self.tmp / "skills-link"
        link.symlink_to(self.skills)
        with mock.patch.object(ev, "SKILLS", link):
            with self.assertRaises(ev.ContextError) as caught:
                ev.confined_reference("fin-money-core", "a.md")
        self.assertIn("trusted root", str(caught.exception))

    def test_a_base_outside_the_trusted_root_is_refused(self):
        with self.assertRaises(ev.ContextError) as caught:
            ev._confine(self.elsewhere, "a.md", "probe", root=self.skills)
        self.assertIn("not under the trusted root", str(caught.exception))

    def test_a_symlinked_case_directory_is_refused(self):
        dataset = self.tmp / "evals" / "behavioral"
        (dataset / "linked-case").symlink_to(dataset / "ledger-a")
        with mock.patch.object(ev, "DATASET", dataset), \
             mock.patch.object(ev, "DATASET_BASE", self.tmp):
            with self.assertRaises(ev.ContextError) as caught:
                ev.dataset_cases()
        self.assertIn("must not be a symlink", str(caught.exception))

    def test_a_dot_entry_is_skipped_not_treated_as_a_case(self):
        dataset = self.tmp / "evals" / "behavioral"
        (dataset / ".omc" / "state").mkdir(parents=True)
        with mock.patch.object(ev, "DATASET", dataset), \
             mock.patch.object(ev, "DATASET_BASE", self.tmp):
            self.assertEqual([d.name for d in ev.dataset_cases()], ["ledger-a", "money-b"])

    def test_a_reference_that_is_a_directory_is_refused(self):
        (self.skills / "fin-money-core" / "references" / "sub").mkdir()
        with self.assertRaises(ev.ContextError):
            ev.confined_reference("fin-money-core", "sub")

    def test_dot_and_backslash_components_are_refused(self):
        for attempt in ("./a.md", "sub/./a.md", "sub\\a.md", "  "):
            with self.assertRaises(ev.ContextError, msg=attempt):
                ev.confined_reference("fin-money-core", attempt)


def shutil_rmtree(path: Path) -> None:
    for child in sorted(path.rglob("*"), reverse=True):
        child.rmdir() if child.is_dir() else child.unlink()
    path.rmdir()


# --------------------------------------------------------------------------- §2 infra is invalid


class SandboxFailuresAreInvalid(unittest.TestCase):
    """Docker breaking is a call that did not happen. It is never evidence about a patch."""

    def setUp(self):
        self.case = frozen_case()

    def _grade_with(self, sandbox):
        with mock.patch.object(ev, "run_in_sandbox", return_value=sandbox):
            return ev.grade(self.case, PATCH_OK)

    def test_every_infrastructure_exit_code_is_invalid(self):
        for code, expected in ev.SANDBOX_INFRA_EXITS.items():
            result = self._grade_with((code, "boom"))
            self.assertEqual(result["outcome"], "invalid", code)
            self.assertFalse(result["oracle_completed"], code)
            self.assertFalse(result["oracle_passed"], code)
            self.assertIn("sandbox unavailable", result["reason"])
            self.assertIn(expected.split(" (")[0], result["reason"])
            self.assertTrue(result["patch_applied"], "the patch still applied; docker is what broke")

    def test_the_documented_infrastructure_codes_are_the_expected_ones(self):
        self.assertEqual(sorted(ev.SANDBOX_INFRA_EXITS), [125, 126, 127, 137, 143])
        self.assertNotIn(ev.SANDBOX_TIMEOUT_EXIT, ev.SANDBOX_INFRA_EXITS,
                         "a timeout is the patch hanging, which IS evidence about the patch")

    def test_an_oracle_rejection_is_still_a_fail_not_invalid(self):
        result = self._grade_with((1, "assertion failed"))
        self.assertEqual(result["outcome"], "fail")
        self.assertTrue(result["oracle_completed"])

    def test_a_timeout_is_still_a_fail(self):
        result = self._grade_with((ev.SANDBOX_TIMEOUT_EXIT, "exceeded its 10s wall clock"))
        self.assertEqual(result["outcome"], "fail")
        self.assertTrue(result["patch_applied"])
        self.assertFalse(result["oracle_completed"])

    def test_a_missing_docker_binary_is_invalid_not_a_crash(self):
        with mock.patch.object(ev, "docker_binary", return_value=None):
            code, tail = ev.run_in_sandbox(Path("/tmp"), [], 5, "probe")
        self.assertEqual(code, 127)
        self.assertIn("docker", tail)
        with mock.patch.object(ev, "run_in_sandbox", return_value=(code, tail)):
            self.assertEqual(ev.grade(self.case, PATCH_OK)["outcome"], "invalid")

    def test_a_vanished_docker_binary_mid_run_is_invalid(self):
        with mock.patch.object(ev, "docker_binary", return_value="/usr/bin/docker"):
            with mock.patch.object(ev.subprocess, "run", side_effect=FileNotFoundError()):
                code, _ = ev.run_in_sandbox(Path("/tmp"), [], 5, "probe")
        self.assertEqual(code, 127)

    def test_a_docker_cli_that_will_not_start_is_invalid(self):
        with mock.patch.object(ev, "docker_binary", return_value="/usr/bin/docker"):
            with mock.patch.object(ev.subprocess, "run", side_effect=OSError("no fork")):
                code, _ = ev.run_in_sandbox(Path("/tmp"), [], 5, "probe")
        self.assertEqual(code, 125)

    def test_an_os_error_while_grading_is_invalid(self):
        with mock.patch.object(ev, "materialise", side_effect=OSError("disk full")):
            result = ev.grade(self.case, PATCH_OK)
        self.assertEqual(result["outcome"], "invalid")
        self.assertIn("sandbox unavailable", result["reason"])


# --------------------------------------------------------------------------- §3 read once


class SharedContextIsFrozenOnce(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_each_shared_file_is_read_exactly_once_per_run(self):
        ids = ("ledger-a", "money-b", "money-c")
        with world(self.tmp, case_ids=ids):
            freezer = ev.ContextFreezer()
            real = Path.read_text
            reads = []

            def counting(self, *a, **k):
                reads.append(str(self))
                return real(self, *a, **k)

            with mock.patch.object(Path, "read_text", counting):
                cases = ev.freeze_cases("all", "m", "low", "abc", freezer=freezer)
        self.assertEqual(len(cases), 3)
        skill_reads = [r for r in reads if "/skills/" in r]
        self.assertEqual(len(skill_reads), len(set(skill_reads)),
                         f"a skill file was read twice in one run: {skill_reads}")
        self.assertEqual(sorted(freezer.file_digests),
                         ["fin-money-core/SKILL.md", "fin-money-core/references/a.md"])

    def test_the_baseline_context_is_a_byte_exact_prefix_of_the_treatment_context(self):
        with world(self.tmp, case_ids=("verification-a",), baseline=("fin-payments",)):
            case = ev.freeze_cases("all", "m", "low", "abc")[0]
        baseline = case.baseline_prompt.split("<skill_context>\n")[1].split("\n</skill_context>")[0]
        treatment = case.treatment_prompt.split("<skill_context>\n")[1].split(
            "\n</skill_context>")[0]
        self.assertTrue(treatment.startswith(baseline + "\n\n"),
                        "the shared half of the two prompts must be identical byte for byte")
        self.assertEqual(ev._digest(baseline.encode())[:32], case.shared_context_digest)
        self.assertEqual(ev._digest(treatment.encode())[:32], case.treatment_context_digest)

    def test_two_cases_naming_one_skill_get_the_same_bytes(self):
        with world(self.tmp, case_ids=("ledger-a", "money-b")):
            a, b = ev.freeze_cases("all", "m", "low", "abc")
        self.assertEqual(a.treatment_context_digest, b.treatment_context_digest)

    def test_an_empty_baseline_has_a_recorded_digest_too(self):
        with world(self.tmp, case_ids=("ledger-a",)):
            case = ev.freeze_cases("all", "m", "low", "abc")[0]
        self.assertEqual(case.baseline_context, ())
        self.assertEqual(case.shared_context_digest, ev._digest(b"")[:32])
        self.assertNotEqual(case.shared_context_digest, case.treatment_context_digest)


# --------------------------------------------------------------------------- §5 validity


class ValidityGaps(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_timeout_seconds_is_enforced_by_the_schema_loader(self):
        dataset, skills = write_world(self.tmp, case_ids=("ledger-a",))
        path = dataset / "ledger-a" / "case.yaml"
        original = path.read_text()
        with mock.patch.object(ev, "SKILLS", skills):
            for bad in ("0", "301", "-1", "'60'", "60.5", "true"):
                path.write_text(original.replace("timeout_seconds: 10",
                                                 f"timeout_seconds: {bad}"), encoding="utf-8")
                with self.assertRaises(ev.ContextError, msg=bad) as caught:
                    ev.load_spec(dataset / "ledger-a")
                self.assertIn("timeout_seconds", str(caught.exception))
            for good in ("1", "300", "60"):
                path.write_text(original.replace("timeout_seconds: 10",
                                                 f"timeout_seconds: {good}"), encoding="utf-8")
                self.assertEqual(ev.load_spec(dataset / "ledger-a")["timeout_seconds"], int(good))

    def test_every_shipped_case_declares_a_timeout_inside_the_range(self):
        for case in ev.dataset_cases():
            spec = ev.load_spec(case)
            self.assertTrue(1 <= spec["timeout_seconds"] <= 300, case.name)

    def _rec(self, arm, outcome, run_id="A", key="k", attempts=1, tokens=100):
        return {"run_id": run_id, "pair_key": key, "arm": arm, "outcome": outcome,
                "oracle_passed": outcome == "pass", "case": "case-a", "repeat": 0,
                "order": "baseline-treatment", "reason": "r", "latency_s": 1.0,
                "attempts": attempts, "input_tokens": tokens, "output_tokens": tokens}

    def _summary(self, records, run_id="A"):
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            ev.summarise(records, repeats=1, run_id=run_id)
        return out.getvalue()

    def test_foreign_records_are_dropped_before_every_metric(self):
        records = [self._rec("baseline", "pass"), self._rec("treatment", "pass"),
                   self._rec("baseline", "invalid", run_id="OTHER", key="k2", tokens=9999),
                   self._rec("treatment", "pass", run_id="OTHER", key="k2", tokens=9999)]
        text = self._summary(records)
        self.assertIn("calls made 2", text, "the two foreign records are not calls this run made")
        self.assertIn("invalid 0", text, "a foreign invalid is not this run's invalid")
        self.assertIn("complete pairs 1", text)
        self.assertIn("dropped before every number above and below: 2", text)
        self.assertNotIn("9999", text)

    def test_attempts_are_reported_per_arm_and_include_retries(self):
        records = [self._rec("baseline", "pass", attempts=3),
                   self._rec("treatment", "pass", attempts=1)]
        text = self._summary(records)
        header = [ln for ln in text.splitlines() if "attempts" in ln][0]
        self.assertIn("calls", header)
        self.assertIn("invalid", header)
        baseline = [ln for ln in text.splitlines() if ln.strip().startswith("baseline")][0]
        self.assertRegex(baseline, r"baseline\s+1\s+0\s+3\s+100\s+100")
        self.assertIn("attempts include retries", text)

    def test_per_arm_counts_cover_calls_invalids_and_tokens(self):
        records = [self._rec("baseline", "invalid", attempts=3, tokens=0),
                   self._rec("treatment", "pass", attempts=1, tokens=50)]
        text = self._summary(records)
        treatment = [ln for ln in text.splitlines() if ln.strip().startswith("treatment")][0]
        self.assertRegex(treatment, r"treatment\s+1\s+0\s+1\s+50\s+50")
        baseline = [ln for ln in text.splitlines() if ln.strip().startswith("baseline")][0]
        self.assertRegex(baseline, r"baseline\s+1\s+1\s+3\s+0\s+0")

    def test_the_attempt_ceiling_follows_from_max_calls_and_max_retries(self):
        runs = self.tmp / "runs"
        calls = {"n": 0}

        def flaky(*args, **kwargs):
            calls["n"] += 1
            return "ok", {"patch": PATCH_OK, "summary": "", "latency_s": 1.0,
                          "attempts": 1 + ev.MAX_RETRIES, "api_status": "completed",
                          "input_tokens": 10, "output_tokens": 5}

        with world(self.tmp):
            with mock.patch.object(ev, "RUNS", runs), \
                 mock.patch.object(ev, "require_clean_tree"), \
                 mock.patch.object(ev, "sandbox_preflight", return_value=None), \
                 mock.patch.object(ev, "call_model", side_effect=flaky), \
                 mock.patch.object(ev, "run_in_sandbox", return_value=(0, "")), \
                 mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}, clear=False), \
                 mock.patch.object(sys, "argv", [
                     "run_patch_eval.py", "--model", "m", "--effort", "low", "--cases", "all",
                     "--repeats", "1", "--yes", "--max-calls", "4"]), \
                 mock.patch("sys.stdout", new=io.StringIO()) as out:
                ev.main()
        text = out.getvalue()
        self.assertIn(f"at most {4 * (1 + ev.MAX_RETRIES)} request attempt(s)", text)
        manifest = json.loads((next(runs.iterdir()) / "manifest.json").read_text())
        self.assertEqual(manifest["attempt_ceiling"], 4 * (1 + ev.MAX_RETRIES))
        self.assertEqual(manifest["max_retries_per_call"], ev.MAX_RETRIES)
        self.assertIn("fin-money-core/SKILL.md", manifest["shared_context_files"])

    def test_a_broken_sandbox_trips_the_invalid_breaker_after_grading(self):
        """The API is healthy and every patch applies; docker is what is broken."""
        runs = self.tmp / "runs"

        def good(*args, **kwargs):
            return "ok", {"patch": PATCH_OK, "summary": "", "latency_s": 1.0, "attempts": 1,
                          "api_status": "completed", "input_tokens": 10, "output_tokens": 5}

        with world(self.tmp):
            with mock.patch.object(ev, "RUNS", runs), \
                 mock.patch.object(ev, "require_clean_tree"), \
                 mock.patch.object(ev, "sandbox_preflight", return_value=None), \
                 mock.patch.object(ev, "call_model", side_effect=good), \
                 mock.patch.object(ev, "run_in_sandbox", return_value=(125, "daemon gone")), \
                 mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}, clear=False), \
                 mock.patch.object(sys, "argv", [
                     "run_patch_eval.py", "--model", "m", "--effort", "low", "--cases", "all",
                     "--repeats", "3", "--yes", "--max-calls", "99", "--max-invalid", "2"]), \
                 mock.patch("sys.stdout", new=io.StringIO()) as out:
                self.assertEqual(ev.main(), 1)
        text = out.getvalue()
        self.assertIn("STOPPING", text)
        self.assertIn("sandbox unavailable", text)
        records = json.loads((next(runs.iterdir()) / "records.json").read_text())
        self.assertEqual(len(records), 2, "it stopped after the ceiling, it did not grade the suite")
        self.assertTrue(all(r["outcome"] == "invalid" for r in records))
        self.assertTrue(all(r["patch_applied"] for r in records),
                        "the patches applied; only the container failed")


# --------------------------------------------------------------------------- the dataset checker


import check_eval_dataset as ced          # noqa: E402


class DatasetChecker(unittest.TestCase):
    """Direct tests for scripts/check_eval_dataset.py, the gate that makes the fixtures worth
    comparing against. It runs real oracles on the host, which is safe: nothing here is
    model-generated."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.dataset, self.skills = write_world(self.tmp, case_ids=("ledger-a",))
        self.case = self.dataset / "ledger-a"
        ced.errors[:] = []
        self._patches = [
            mock.patch.object(ced, "DATASET", self.dataset),
            mock.patch.object(ev, "DATASET", self.dataset),
            mock.patch.object(ev, "DATASET_BASE", self.tmp),
            mock.patch.object(ev, "SKILLS", self.skills),
        ]
        for patch in self._patches:
            patch.start()

    def tearDown(self):
        for patch in self._patches:
            patch.stop()
        ced.errors[:] = []
        self._tmp.cleanup()

    def _check(self, run_oracles=True):
        with mock.patch("sys.stdout", new=io.StringIO()) as out:
            ced.check_case(self.case, run_oracles=run_oracles)
        return list(ced.errors), out.getvalue()

    @staticmethod
    @contextmanager
    def _quiet():
        """The checker reports through err(), which prints. Keep a passing run silent."""
        with mock.patch("sys.stdout", new=io.StringIO()):
            yield

    def test_a_sound_case_passes_end_to_end(self):
        errors, text = self._check()
        self.assertEqual(errors, [])
        self.assertIn("oracle fails on the planted defect", text)
        self.assertIn("fix.patch applies", text)
        self.assertIn("oracle passes after the reference fix", text)

    def test_an_oracle_that_passes_on_the_defect_is_a_dataset_bug(self):
        (self.case / "oracle" / "test_oracle.py").write_text("import sys\nsys.exit(0)\n",
                                                             encoding="utf-8")
        errors, _ = self._check()
        self.assertTrue(any("DATASET BUG" in e and "PASSES against repo/" in e for e in errors),
                        errors)

    def test_an_oracle_that_still_fails_after_the_fix_is_a_dataset_bug(self):
        (self.case / "oracle" / "test_oracle.py").write_text(
            "import mod, sys\nsys.exit(0 if mod.VALUE == 3 else 1)\n", encoding="utf-8")
        errors, _ = self._check()
        self.assertTrue(any("still fails after the reference fix" in e for e in errors), errors)

    def test_a_fix_that_does_not_apply_is_caught(self):
        (self.case / "fix.patch").write_text(PATCH_OK.replace("-VALUE = 1", "-VALUE = 42"),
                                             encoding="utf-8")
        errors, _ = self._check()
        self.assertTrue(any("does not apply" in e for e in errors), errors)

    def test_a_reference_patch_is_held_to_the_graders_own_rule(self):
        (self.case / "fix.patch").write_text(PATCH_OK + "rename to ../../escaped.py\n",
                                             encoding="utf-8")
        errors, _ = self._check(run_oracles=False)
        self.assertTrue(any("would be refused by the grader" in e and "renames" in e
                            for e in errors), errors)

    def test_trailing_whitespace_in_a_patch_is_refused(self):
        with self._quiet():
                ced.check_patch_hygiene("--- a/repo/mod.py\n \n+++ b/repo/mod.py\n", "c")
        self.assertTrue(any("trailing whitespace" in e for e in ced.errors), ced.errors)

    def test_a_patch_ending_in_a_blank_line_or_no_newline_is_refused(self):
        with self._quiet():
                ced.check_patch_hygiene("--- a/repo/mod.py\n\n", "c")
        self.assertTrue(any("ends with a blank line" in e for e in ced.errors), ced.errors)
        ced.errors[:] = []
        with self._quiet():
                ced.check_patch_hygiene("--- a/repo/mod.py", "c")
        self.assertTrue(any("does not end with a newline" in e for e in ced.errors), ced.errors)

    def test_a_clean_patch_passes_hygiene(self):
        with self._quiet():
                ced.check_patch_hygiene(PATCH_OK, "c")
        self.assertEqual(ced.errors, [])

    def test_a_test_file_in_allowed_paths_is_refused(self):
        (self.case / "repo" / "tests").mkdir()
        (self.case / "repo" / "tests" / "test_mod.py").write_text("x = 1\n", encoding="utf-8")
        text = (self.case / "case.yaml").read_text().replace(
            "  - repo/mod.py", "  - repo/mod.py\n  - repo/tests/test_mod.py")
        (self.case / "case.yaml").write_text(text, encoding="utf-8")
        errors, _ = self._check(run_oracles=False)
        self.assertTrue(any("is a test file" in e for e in errors), errors)

    def test_a_banned_import_in_an_oracle_is_refused(self):
        for source, expected in (("import socket\n", "socket"),
                                 ("from subprocess import run\n", "subprocess"),
                                 ("import os\nos.system('x')\n", "system"),
                                 ("eval('1')\n", "eval")):
            ced.errors[:] = []
            path = self.case / "oracle" / "test_oracle.py"
            path.write_text(source, encoding="utf-8")
            with self._quiet():
                ced.scan_oracle(path, "c")
            self.assertTrue(any(expected in e for e in ced.errors), (source, ced.errors))

    def test_a_credential_shaped_literal_in_an_oracle_is_refused(self):
        # Assembled at run time so this test file does not itself carry a credential-shaped
        # literal for the repository's own secret scan to trip over.
        for prefix, expected in (("sk_live_", "live secret key"), ("AKIA", "aws access key")):
            ced.errors[:] = []
            path = self.case / "oracle" / "test_oracle.py"
            path.write_text(f'KEY = "{prefix}{"A1B2C3D4" * 3}"\n', encoding="utf-8")
            with self._quiet():
                ced.scan_oracle(path, "c")
            self.assertTrue(any(expected in e for e in ced.errors), (prefix, ced.errors))

    def test_a_symlink_or_a_cache_inside_a_case_is_refused(self):
        (self.case / "repo" / "__pycache__").mkdir()
        (self.case / "repo" / "__pycache__" / "mod.cpython-311.pyc").write_bytes(b"\x00")
        (self.case / "repo" / "link.py").symlink_to(self.case / "repo" / "mod.py")
        with self._quiet():
                ced.scan_tree(self.case, "c")
        self.assertTrue(any("is a symlink" in e for e in ced.errors), ced.errors)
        self.assertTrue(any("cache or artefact" in e for e in ced.errors), ced.errors)

    def test_a_reference_that_escapes_its_skill_is_reported_not_raised(self):
        text = (self.case / "case.yaml").read_text().replace("    - a.md",
                                                             "    - ../../../etc/hosts")
        (self.case / "case.yaml").write_text(text, encoding="utf-8")
        errors, _ = self._check(run_oracles=False)
        self.assertTrue(any("traversal" in e for e in errors), errors)

    def test_the_oracle_child_environment_carries_no_credential(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-nope", "HTTPS_PROXY": "p"},
                             clear=False):
            env = ced.minimal_env(self.tmp)
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertNotIn("HTTPS_PROXY", env)
        self.assertEqual(env["PYTHONPATH"], "repo")
        self.assertEqual(env["PYTHONDONTWRITEBYTECODE"], "1")



class ShippedSuite(unittest.TestCase):
    """Assertions about the real dataset. Deliberately not inside a class that patches DATASET."""

    def test_two_cases_target_each_installed_skill(self):
        self.assertEqual(ced.MIN_CASES_PER_SKILL, 2)
        counts = {name: 0 for name in ev.INSTALLED_SKILLS}
        for case in ev.dataset_cases():
            counts[ev.load_spec(case)["target_skill"]] += 1
        self.assertEqual(sorted(counts.values()), [2] * len(ev.INSTALLED_SKILLS), counts)

    def test_every_verification_case_keeps_a_domain_skill_in_its_baseline(self):
        """Verification is layered on domain knowledge, so its baseline is never empty."""
        for case in ev.dataset_cases():
            spec = ev.load_spec(case)
            if spec["target_skill"] != "fin-verification":
                continue
            baseline = list(spec["baseline_context"] or [])
            self.assertTrue(baseline, f"{case.name} compares verification against nothing")
            self.assertNotIn("fin-verification", baseline, case.name)

    def test_no_case_lets_a_patch_edit_its_own_tests(self):
        for case in ev.dataset_cases():
            for declared in ev.load_spec(case)["allowed_paths"]:
                name = Path(declared).name
                self.assertFalse(name.startswith("test_") or name.endswith("_test.py")
                                 or {"test", "tests"} & set(Path(declared).parts),
                                 f"{case.name} lets a patch edit {declared}")


class DatasetRootAndAllowedPaths(unittest.TestCase):
    """The two gaps the last review left: a symlinked dataset root, and an unusable allowed_paths."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.dataset, self.skills = write_world(self.tmp, case_ids=("ledger-a",))

    def tearDown(self):
        self._tmp.cleanup()

    def test_a_symlinked_dataset_root_is_refused_before_it_is_read(self):
        link = self.tmp / "behavioral-link"
        link.symlink_to(self.dataset)
        with mock.patch.object(ev, "DATASET", link), \
             mock.patch.object(ev, "DATASET_BASE", self.tmp):
            for call in (ev.dataset_root, ev.dataset_cases,
                         lambda: ev.freeze_cases("all", "m", "low", "abc")):
                with self.assertRaises(ev.ContextError) as caught:
                    call()
                self.assertIn("symlink", str(caught.exception))

    def test_the_root_is_checked_before_anything_iterates_it(self):
        """iterdir() must not be reached: the redirect is refused first, not filtered afterwards."""
        link = self.tmp / "behavioral-link"
        link.symlink_to(self.dataset)
        with mock.patch.object(ev, "DATASET", link), \
             mock.patch.object(ev, "DATASET_BASE", self.tmp):
            with mock.patch.object(Path, "iterdir", side_effect=AssertionError("iterated")):
                with self.assertRaises(ev.ContextError):
                    ev.dataset_cases()

    def test_a_symlinked_evals_parent_is_refused(self):
        """The leaf is real; its parent is not. Checking only the leaf would read a redirected tree."""
        elsewhere = self.tmp / "elsewhere"
        (elsewhere / "behavioral" / "planted-case").mkdir(parents=True)
        real_evals = self.tmp / "evals"
        for child in sorted(real_evals.rglob("*"), reverse=True):
            child.rmdir() if child.is_dir() else child.unlink()
        real_evals.rmdir()
        real_evals.symlink_to(elsewhere)
        with mock.patch.object(ev, "DATASET", self.tmp / "evals" / "behavioral"), \
             mock.patch.object(ev, "DATASET_BASE", self.tmp):
            for call in (ev.dataset_root, ev.dataset_cases):
                with self.assertRaises(ev.ContextError) as caught:
                    call()
                self.assertIn("symlink", str(caught.exception))
                self.assertIn("evals", str(caught.exception))

    def test_the_ancestor_check_runs_before_any_iteration(self):
        elsewhere = self.tmp / "elsewhere"
        (elsewhere / "behavioral").mkdir(parents=True)
        real_evals = self.tmp / "evals"
        for child in sorted(real_evals.rglob("*"), reverse=True):
            child.rmdir() if child.is_dir() else child.unlink()
        real_evals.rmdir()
        real_evals.symlink_to(elsewhere)
        with mock.patch.object(ev, "DATASET", self.tmp / "evals" / "behavioral"), \
             mock.patch.object(ev, "DATASET_BASE", self.tmp):
            with mock.patch.object(Path, "iterdir", side_effect=AssertionError("iterated")):
                with self.assertRaises(ev.ContextError):
                    ev.dataset_cases()

    def test_a_dataset_outside_the_repository_root_is_refused(self):
        with tempfile.TemporaryDirectory() as other:
            with mock.patch.object(ev, "DATASET", Path(other)), \
                 mock.patch.object(ev, "DATASET_BASE", self.tmp):
                with self.assertRaises(ev.ContextError) as caught:
                    ev.dataset_root()
        self.assertIn("is not under", str(caught.exception))

    def test_the_shipped_dataset_root_has_no_symlink_in_its_chain(self):
        """Against the real repository, not a fixture."""
        self.assertEqual(ev.dataset_root(), ev.DATASET)
        self.assertEqual(ev.DATASET_BASE, ev.ROOT)

    def test_a_missing_dataset_root_is_refused(self):
        with mock.patch.object(ev, "DATASET", self.tmp / "nowhere"), \
             mock.patch.object(ev, "DATASET_BASE", self.tmp):
            with self.assertRaises(ev.ContextError) as caught:
                ev.dataset_root()
        self.assertIn("no dataset", str(caught.exception))

    def test_a_real_dataset_root_is_accepted(self):
        with mock.patch.object(ev, "DATASET", self.dataset), \
             mock.patch.object(ev, "DATASET_BASE", self.tmp):
            self.assertEqual([d.name for d in ev.dataset_cases()], ["ledger-a"])

    def _spec_with_allowed(self, replacement):
        path = self.dataset / "ledger-a" / "case.yaml"
        text = path.read_text().replace("allowed_paths:\n  - repo/mod.py\n", replacement)
        path.write_text(text, encoding="utf-8")
        with mock.patch.object(ev, "SKILLS", self.skills):
            with self.assertRaises(ev.ContextError) as caught:
                ev.load_spec(self.dataset / "ledger-a")
        return str(caught.exception)

    def test_the_paid_runner_refuses_an_empty_or_malformed_allowed_paths(self):
        for replacement in ("allowed_paths: []\n",
                            "allowed_paths:\n",
                            "allowed_paths: repo/mod.py\n",
                            "allowed_paths: {}\n"):
            why = self._spec_with_allowed(replacement)
            self.assertIn("allowed_paths must be a non-empty list", why, replacement)

    def test_a_non_path_entry_in_allowed_paths_is_refused(self):
        for replacement in ("allowed_paths:\n  - 7\n", "allowed_paths:\n  - ''\n",
                            "allowed_paths:\n  - '   '\n"):
            why = self._spec_with_allowed(replacement)
            self.assertIn("which is not a", why, replacement)

    def test_the_rule_is_one_definition_shared_by_the_runner_and_the_checker(self):
        """check_eval_dataset must not carry a second, driftable copy of the same rule."""
        text = (ROOT / "scripts" / "check_eval_dataset.py").read_text(encoding="utf-8")
        self.assertNotIn("allowed_paths must list the files", text)
        self.assertIn("load_spec", text)

    def test_every_shipped_case_declares_a_usable_allowed_paths(self):
        for case in ev.dataset_cases():
            allowed = ev.load_spec(case)["allowed_paths"]
            self.assertTrue(isinstance(allowed, list) and allowed, case.name)
            self.assertTrue(all(isinstance(p, str) and p.startswith("repo/") for p in allowed),
                            case.name)
