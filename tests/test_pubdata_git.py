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

from dogear.publish.pubdata import PubData, RemoteMoved, SaveFailed, git_committer

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
        # Like the private repositories on GitHub Free: no branch protection, so the
        # remote itself would accept a forced update. Only the client can refuse.
        git("--git-dir", str(self.remote), "config", "receive.denyNonFastForwards", "false")
        git("--git-dir", str(self.remote), "config", "receive.denyDeletes", "false")
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

    def remote_head(self) -> str:
        return git("--git-dir", str(self.remote), "rev-parse", "main").strip()

    def push_from(self, name: str, message: str, *extra: str) -> None:
        other = self.clone(name)
        (other / f"{name}.txt").write_text(message + "\n")
        git("add", "-A", cwd=other)
        git("commit", "-q", "-m", message, cwd=other)
        git("push", "-q", *extra, "origin", "main", cwd=other)

    def assert_refused(self, work: Path, pd: PubData, remote_before: str) -> None:
        head_before = git("rev-parse", "HEAD", cwd=work).strip()
        (work / "pending.json").write_text("{}\n")
        with self.assertRaises(RemoteMoved):
            pd.save("pending x")
        self.assertEqual(git("rev-parse", "HEAD", cwd=work).strip(), head_before)  # nothing committed either
        self.assertEqual(self.remote_head(), remote_before)

    def test_refuses_when_the_remote_moved_since_the_run_read_it(self):
        work = self.clone("run")
        pd = PubData(work, git_committer(work))  # the run reads the remote here
        self.push_from("other", "another writer")
        self.assert_refused(work, pd, self.remote_head())

    def test_refuses_when_the_remote_was_rewritten(self):
        work = self.clone("run")
        pd = PubData(work, git_committer(work))
        rewriter = self.tmp / "rewriter"
        git("init", "-q", "-b", "main", str(rewriter))
        git("commit", "-q", "--allow-empty", "-m", "unrelated history", cwd=rewriter)
        git("push", "-q", "--force", str(self.remote), "main", cwd=rewriter)
        rewritten = self.remote_head()
        self.assert_refused(work, pd, rewritten)
        self.assertEqual(self.remote_log(), ["unrelated history"])  # not overwritten back

    def test_refuses_when_the_remote_branch_was_deleted(self):
        work = self.clone("run")
        pd = PubData(work, git_committer(work))
        git("--git-dir", str(self.remote), "update-ref", "-d", "refs/heads/main")
        (work / "pending.json").write_text("{}\n")
        with self.assertRaises(RemoteMoved):
            pd.save("pending x")
        self.assertEqual(git("--git-dir", str(self.remote), "for-each-ref", "refs/heads").strip(), "")  # not recreated

    def test_refuses_a_write_that_would_not_fast_forward(self):
        work = self.clone("run")
        pd = PubData(work, git_committer(work))
        pd.append_history_once({"txn": "t1", "status": "aborted"})
        pd.save("abort t1")
        pushed = self.remote_head()
        git("reset", "-q", "--hard", "HEAD~1", cwd=work)  # a local rewrite of what was already pushed
        (work / "pending.json").write_text("{}\n")
        with self.assertRaises(RemoteMoved):
            pd.save("pending x")
        self.assertEqual(self.remote_head(), pushed)
        self.assertEqual(self.remote_log(), ["abort t1", "init"])

    def test_refuses_off_the_branch(self):
        work = self.clone("run")
        pd = PubData(work, git_committer(work))
        git("checkout", "-q", "--detach", cwd=work)
        self.assert_refused(work, pd, self.remote_head())

    def test_accepts_its_own_push_that_landed_unacknowledged(self):
        work = self.clone("run")
        pd = PubData(work, git_committer(work))
        (work / "pending.json").write_text("{}\n")
        git("add", "-A", cwd=work)
        git("commit", "-q", "-m", "pending x", cwd=work)
        git("push", "-q", "origin", "main", cwd=work)  # it landed, but the run never heard back
        pd.append_history_once({"txn": "t1", "status": "aborted"})
        pd.save("abort t1")
        self.assertEqual(self.remote_log(), ["abort t1", "pending x", "init"])

    def test_a_write_racing_the_push_is_rejected_not_forced(self):
        """Another writer lands after the check but before the push (a hook on the
        local commit makes it happen there). The push names no '+' and no --force,
        so it is refused instead of overwriting, even with a forcing push refspec
        configured locally."""
        work = self.clone("run")
        racer = self.clone("racer")
        hook = work / ".git" / "hooks" / "reference-transaction"
        hook.write_text("#!/bin/sh\n"
                        "[ \"$1\" = committed ] || exit 0\n"
                        "grep -q ' refs/heads/main$' || exit 0\n"
                        f"[ -e '{self.tmp}/raced' ] && exit 0\n"
                        f"touch '{self.tmp}/raced'\n"
                        "unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_PREFIX\n"
                        f"cd '{racer}' && git commit -q --allow-empty -m race && git push -q origin main\n")
        hook.chmod(0o755)
        git("config", "remote.origin.push", "+refs/heads/*:refs/heads/*", cwd=work)
        git("config", "push.default", "matching", cwd=work)
        pd = PubData(work, git_committer(work))
        (work / "pending.json").write_text("{}\n")
        with self.assertRaises(SaveFailed):
            pd.save("pending x")
        self.assertTrue((self.tmp / "raced").exists())
        self.assertEqual(self.remote_log(), ["race", "init"])  # the racer's write survives; ours never landed

    def test_the_next_save_after_a_refusal_still_refuses(self):
        work = self.clone("run")
        pd = PubData(work, git_committer(work))
        self.push_from("other", "another writer")
        moved = self.remote_head()
        for _ in range(2):
            self.assert_refused(work, pd, moved)


if __name__ == "__main__":
    unittest.main()
