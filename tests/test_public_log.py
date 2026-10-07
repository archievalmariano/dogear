"""--public-log (always used by the workflows): a public repository's Actions logs
are readable by anyone, so private text planted in a reason, a hold, a link URL, a
notice or an unexpected error must never reach the output. Refusal details go to
the private publication data instead."""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dogear.cli import main
from dogear.publish.pubdata import PubData
from dogear.publish.staging import LinkReport

ROOT = Path(__file__).resolve().parents[1]
REASON, HOLD, URL, WARN, EXC = ("SENTINEL-REASON-71c3", "SENTINEL-HOLD-a90e", "https://sentinel-url-5d2b.example/",
                                "https://sentinel-warn-0f6e.example/", "SENTINEL-EXCEPTION-c41d")
IDENTITY = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@invalid", "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@invalid", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def git(*args, cwd=None):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


class PublicLogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        patcher = mock.patch.dict(os.environ, IDENTITY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.remote = self.tmp / "data.git"
        git("init", "-q", "--bare", "-b", "main", str(self.remote))
        seed = self.tmp / "seed"
        git("clone", "-q", str(self.remote), str(seed))
        (seed / "README.md").write_text("x\n")
        git("add", "-A", cwd=seed)
        git("commit", "-q", "-m", "init", cwd=seed)
        git("push", "-q", "origin", "main", cwd=seed)
        self.runs = 0

    def run_cli(self, now: str, *op, public=True, checks=None) -> tuple:
        """(exit, everything printed). Each run from a fresh clone, with --git."""
        self.runs += 1
        work = self.tmp / f"run{self.runs}"
        git("clone", "-q", str(self.remote), str(work))
        argv = ["--dataset", str(ROOT / "fixtures" / "staging-year.json"), "--affinity",
                str(ROOT / "fixtures" / "staging-affinity.json"), "publish", "--store", str(self.tmp / "store"),
                "--pubdata", str(work), "--git", "--mode", "test", "--policy", str(ROOT / "fixtures" / "staging-policy.json"),
                "--base-url", "https://dogear-staging.example", "--now", now]
        if checks is None:
            argv.append("--no-network-checks")
        if public:
            argv.append("--public-log")
        out, err = io.StringIO(), io.StringIO()
        patches = [mock.patch("dogear.publish.cli.http_link_checker", lambda: checks),
                   mock.patch("dogear.publish.cli.mock_fit_checker", lambda: (lambda data, ids: []))] if checks else []
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            stack.enter_context(contextlib.redirect_stdout(out))
            stack.enter_context(contextlib.redirect_stderr(err))
            code = main(argv + list(op))
        return code, out.getvalue() + err.getvalue()

    def private_runs_text(self) -> str:
        fresh = self.tmp / f"audit{self.runs}"
        git("clone", "-q", str(self.remote), str(fresh))
        logs = sorted((fresh / "runs").glob("*.log")) if (fresh / "runs").is_dir() else []
        return "\n".join(p.read_text() for p in logs)

    def assert_clean(self, printed: str):
        for sentinel in (REASON, HOLD, URL, WARN, EXC):
            self.assertNotIn(sentinel, printed)

    def test_reasons_and_holds_stay_out_of_the_log(self):
        self.assertEqual(self.run_cli("2027-03-03T10:00:00+08:00", "launch", "--first-publication")[0], 0)
        self.assertEqual(self.run_cli("2027-03-08T06:02:00+08:00", "promote")[0], 0)
        code, printed = self.run_cli("2027-03-08T09:00:00+08:00", "correct", "--week", "2027-03-08", "--expect-rev", "0",
                                     "--reason", REASON)
        self.assertEqual(code, 0)
        self.assert_clean(printed)
        self.assertRegex(printed, r"^published: correct 2027-03-08 \d{8}T\d{6}-correct-[0-9a-f]{12}\n$")
        code, printed = self.run_cli("2027-03-08T09:30:00+08:00", "rollback", "--week", "2027-03-01", "--reason", HOLD)
        self.assertEqual(code, 0)
        self.assert_clean(printed)
        code, printed = self.run_cli("2027-03-15T06:02:00+08:00", "promote")  # held: its message names the reason
        self.assertEqual(code, 0)
        self.assert_clean(printed)
        self.assertTrue(printed.startswith("held: promote") or printed.startswith("noop: promote"), printed)
        code, printed = self.run_cli("2027-03-08T09:31:00+08:00", "status")
        self.assert_clean(printed)
        self.assertIn('"since"', printed)  # the hold is visible, its reason is not
        _, private = self.run_cli("2027-03-08T09:32:00+08:00", "status", public=False)
        self.assertIn(HOLD, private)  # control: without --public-log the operator sees it
        history = PubData(self.tmp / f"run{self.runs}").history()
        self.assertTrue(any(h.get("reason") == REASON for h in history))  # kept in private data

    def test_a_refusal_keeps_its_detail_privately(self):
        def blocking(urls):
            return LinkReport(blocking=[f"{URL}{n}: HTTP 404" for n, _ in enumerate(urls)], warnings=[])
        self.assertEqual(self.run_cli("2027-03-03T10:00:00+08:00", "launch", "--first-publication")[0], 0)
        code, printed = self.run_cli("2027-03-05T10:02:00+08:00", "stage-next", checks=blocking)
        self.assertEqual(code, 7)
        self.assert_clean(printed)
        self.assertIn("stage refused: stage-next (details saved in the private publication data (runs/))", printed)
        self.assertIn(URL, self.private_runs_text())

    def test_notices_are_counted_not_printed(self):
        def warning(urls):
            return LinkReport(blocking=[], warnings=[f"{WARN}: timeout"])
        self.assertEqual(self.run_cli("2027-03-03T10:00:00+08:00", "launch", "--first-publication")[0], 0)
        code, printed = self.run_cli("2027-03-05T10:02:00+08:00", "stage-next", checks=warning)
        self.assertEqual(code, 0)
        self.assert_clean(printed)
        self.assertIn("notice(s) saved in the private publication data", printed)
        self.assertIn(WARN, self.private_runs_text())

    def test_an_unexpected_error_is_withheld(self):
        with mock.patch("dogear.publish.publisher.Publisher.promote", side_effect=RuntimeError(EXC)):
            code, printed = self.run_cli("2027-03-08T06:02:00+08:00", "promote")
        self.assertEqual(code, 1)
        self.assert_clean(printed)
        self.assertIn("failed unexpectedly (RuntimeError)", printed)

    def test_the_workflow_entry_point_prints_only_operation_and_target(self):
        sys.path.insert(0, str(ROOT / "tools"))
        import run_publish
        env = {"DOGEAR_TARGET": "staging", "DOGEAR_OP": "correct", "DOGEAR_WEEK": "2027-03-08",
               "DOGEAR_EXPECT_REV": "0", "DOGEAR_REASON": REASON, "PUBDATA": "/tmp/pd"}
        out = io.StringIO()
        with mock.patch.dict(os.environ, env), mock.patch("subprocess.run") as run, contextlib.redirect_stdout(out):
            run.return_value.returncode = 0
            self.assertEqual(run_publish.main(), 0)
        self.assertEqual(out.getvalue(), "running: dogear publish correct --target staging\n")
        self.assertIn("--public-log", run.call_args[0][0])


if __name__ == "__main__":
    unittest.main()
