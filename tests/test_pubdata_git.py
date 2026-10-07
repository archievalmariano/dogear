"""git_committer against a real git remote (a local bare repository): what the
workflows do with the private publication-data repository."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dogear.publish.pubdata import PubData, SaveFailed, git_committer

try:
    from tests.test_publish import Fixture, W0, W1, at, four_weeks
except ImportError:
    from test_publish import Fixture, W0, W1, at, four_weeks

IDENTITY = {"GIT_AUTHOR_NAME": "dogear-publisher", "GIT_AUTHOR_EMAIL": "publisher@dogear.invalid",
            "GIT_COMMITTER_NAME": "dogear-publisher", "GIT_COMMITTER_EMAIL": "publisher@dogear.invalid",
            "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def git(*args, cwd=None) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


class GitRemoteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        patcher = mock.patch.dict(os.environ, IDENTITY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.remote = self.tmp / "publication-data.git"
        git("init", "-q", "--bare", "-b", "main", str(self.remote))
        seed = self.clone("seed")
        (seed / "README.md").write_text("DOGEAR publication data\n")
        git("add", "-A", cwd=seed)
        git("commit", "-q", "-m", "init", cwd=seed)
        git("push", "-q", "origin", "main", cwd=seed)

    def clone(self, name: str) -> Path:
        path = self.tmp / name
        git("clone", "-q", str(self.remote), str(path))
        return path

    def remote_log(self) -> list:
        return git("--git-dir", str(self.remote), "log", "--format=%s", "main").splitlines()

    def test_each_save_is_one_pushed_commit(self):
        work = self.clone("run1")
        pd = PubData(work, git_committer(work))
        pd.append_history_once({"txn": "20261102T090000-launch-000000000001", "status": "aborted"})
        pd.save("abort 20261102T090000-launch-000000000001")
        pd.save("nothing changed")  # no empty commit, push is a no-op
        self.assertEqual(self.remote_log(), ["abort 20261102T090000-launch-000000000001", "init"])
        fresh = PubData(self.clone("run2"))
        self.assertEqual(len(fresh.history()), 1)

    def test_rejected_push_is_save_failed(self):
        a, b = self.clone("a"), self.clone("b")
        (b / "other.txt").write_text("a concurrent writer\n")
        git("add", "-A", cwd=b)
        git("commit", "-q", "-m", "concurrent", cwd=b)
        git("push", "-q", cwd=b)
        pd = PubData(a, git_committer(a))
        (a / "pending.json").write_text("{}\n")
        with self.assertRaises(SaveFailed):
            pd.save("pending x")
        self.assertEqual(self.remote_log(), ["concurrent", "init"])  # the remote is as it was

    def test_publisher_runs_from_fresh_clones(self):
        """Each run clones, as a workflow does; the record survives between runs only through the remote."""
        f = Fixture(self, four_weeks(), at(W0, 9))

        def run(name):
            work = self.clone(name)
            f.pub = type(f.pub)(f.store, PubData(work, git_committer(work)), f.pub.inputs, f.policy, f.checks,
                                f.clock, mode=f.mode, assets={"inter.woff2": b"font-bytes"})
            return f.pub

        self.assertEqual(run("launch").launch(first_publication=True).status, "published")
        f.clock.t = at(W1)
        self.assertEqual(run("promote").promote().status, "published")
        self.assertEqual(run("again").promote().status, "noop")
        log = self.remote_log()
        self.assertTrue(any(s.startswith("pending ") for s in log))
        self.assertTrue(any(s.startswith("publish ") for s in log))
        committed = [h for h in PubData(self.clone("audit")).history() if h["status"] == "committed"]
        self.assertEqual([h["op"] for h in committed], ["launch", "promote"])


if __name__ == "__main__":
    unittest.main()
