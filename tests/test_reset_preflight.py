"""The read-only reset preflight (PUBLISHING.md §20g; Codex review of 7f8fe9e, finding 3):
the staging reset may begin only from a quiescent, reconciled, verified live state.
Anything unresolved is a HOLD with its reason; nothing is ever moved aside."""

from __future__ import annotations

import io
import os
import unittest.mock
import re
import subprocess
import sys
import unittest
from pathlib import Path

from dogear import web as web_mod
from dogear.publish import registry as regmod
from dogear.publish.drills import failing_once
from dogear.publish.publisher import PublishedUnrecorded, PublishedUnverified
from dogear.publish.registry import REGISTRY_KEY
from dogear.publish.store import Obj

try:
    from tests.test_publish import W0, W1, W2, at, launched
except ImportError:
    from test_publish import W0, W1, W2, at, launched

ROOT = Path(__file__).resolve().parents[1]


def promoted(test, monday):
    f = with_fonts(launched(test))
    for week in (W1, W2):
        if week > monday:
            break
        f.clock.t = at(week, 6, 2)
        test.assertEqual(f.run().promote().status, "published")
    return f


# The fonts every page loads, as the publisher would have uploaded them (the test
# fixture's own uploads carry only a placeholder font).
FONTS = {name: f"font-{name}".encode() for name in
         sorted(set(re.findall(r"\.\./fonts/([A-Za-z0-9-]+\.woff2)", web_mod._FONT_FACES)))}


def with_fonts(f):
    for name, data in FONTS.items():
        f.store.objects[f"fonts/{name}"] = Obj(data, f"\"{name}\"")
    return f


class ResetPreflightTests(unittest.TestCase):
    def check(self, f, expect=W1):
        pub = f.run()
        pub.assets = dict(FONTS)
        return pub.reset_preflight(expect.isoformat())

    def live(self, f):
        reg = regmod.parse(f.store.objects[REGISTRY_KEY].data)
        return reg, regmod.active_revision(reg, reg["current"])

    def test_a_quiet_verified_state_passes_and_nothing_is_written(self):
        f = promoted(self, W1)
        before_objects = {k: v.data for k, v in f.store.objects.items()}
        before_saves = list(f.remote.saves)
        self.assertEqual(self.check(f), [])
        self.assertEqual({k: v.data for k, v in f.store.objects.items()}, before_objects)
        self.assertEqual(f.remote.saves, before_saves)  # reconciled nothing, saved nothing

    def test_a_pending_transaction_holds(self):
        f = promoted(self, W1)
        f.clock.t = at(W2, 6, 2)
        pub = f.run()  # the pending save lands; the record after the switch does not
        pub.pubdata.committer = failing_once(pub.pubdata.committer)
        with self.assertRaises(PublishedUnrecorded):
            pub.promote()
        problems = self.check(f, W2)
        self.assertTrue(any("is pending: reconcile it first" in p for p in problems), problems)

    def test_unverified_serving_holds(self):
        f = promoted(self, W1)
        f.served = lambda path: (503, b"")
        f.clock.t = at(W2, 6, 2)
        with self.assertRaises(PublishedUnverified):  # published, not yet served
            f.run().promote()
        f.served = None
        problems = self.check(f, W2)
        self.assertTrue(any("unverified.json is not empty" in p for p in problems), problems)

    def test_the_wrong_current_week_holds(self):
        f = promoted(self, W1)
        self.assertIn(f"the live registry's current week is {W1}, not {W0}", self.check(f, W0))

    def test_a_hold_holds(self):
        f = promoted(self, W1)
        f.run().rollback(W0, None, "test")
        self.assertIn("the live registry holds publication", self.check(f, W0))

    def test_a_registry_that_differs_from_the_mirror_holds(self):
        f = promoted(self, W1)
        obj = f.store.objects[REGISTRY_KEY]
        reg = regmod.parse(obj.data)
        reg["txn"] = "20261109T060200-promote-000000000000"  # a write whose outcome was never recorded
        f.store.objects[REGISTRY_KEY] = Obj(regmod.dumps(reg), "\"altered\"")
        problems = self.check(f)
        self.assertIn("the publication-data mirror differs from the live registry", problems)
        self.assertTrue(any("an unresolved write outcome" in p for p in problems), problems)

    def test_a_host_that_serves_something_else_holds(self):
        f = promoted(self, W1)
        f.served = lambda path: (200, b"{}")
        problems = self.check(f)
        self.assertIn("manifest /current.json: the served bytes do not match the registry", problems)

    # Hosted active content (Codex re-review of 3228182, item 1): verification
    # history is not enough; every object the live registry shows is fetched again.

    def test_the_current_issue_missing_from_the_host_holds(self):
        f = promoted(self, W1)
        _reg, rev = self.live(f)
        del f.store.objects[rev["issueKey"]]
        self.assertIn(f"issue /{rev['issueKey']}: the host answered 404", self.check(f))

    def test_the_current_page_missing_from_the_host_holds(self):
        f = promoted(self, W1)
        _reg, rev = self.live(f)
        del f.store.objects[rev["webKey"]]
        self.assertIn(f"page /{W1}/: the host answered 404", self.check(f))

    def test_an_earlier_weeks_active_issue_missing_holds(self):
        f = promoted(self, W1)
        reg, _rev = self.live(f)
        older = regmod.active_revision(reg, W0.isoformat())
        del f.store.objects[older["issueKey"]]
        self.assertIn(f"issue /{older['issueKey']}: the host answered 404", self.check(f))

    def test_altered_issue_or_page_bytes_hold(self):
        for key, where in (("issueKey", "issue /{issueKey}"), ("webKey", f"page /{W1}/")):
            with self.subTest(key):
                f = promoted(self, W1)
                _reg, rev = self.live(f)
                obj = f.store.objects[rev[key]]
                f.store.objects[rev[key]] = Obj(obj.data + b" ", obj.etag)
                self.assertIn(where.format(**rev) + ": the served bytes do not match the registry", self.check(f))

    def test_page_fonts_missing_or_altered_hold(self):
        name = sorted(FONTS)[0]
        f = promoted(self, W1)
        del f.store.objects[f"fonts/{name}"]
        self.assertIn(f"font /fonts/{name}: the host answered 404", self.check(f))
        f = promoted(self, W1)
        f.store.objects[f"fonts/{name}"] = Obj(b"other bytes", "x")
        self.assertIn(f"font /fonts/{name}: the served bytes differ from the publisher's copy", self.check(f))
        f = promoted(self, W1)
        pub = f.run()  # no copy of the fonts to compare with: never assumed fine
        self.assertTrue(any("no copy to compare" in p for p in pub.reset_preflight(W1.isoformat())))

    def test_a_host_that_fails_or_answers_malformed_holds(self):
        f = promoted(self, W1)

        def boom(path):
            raise TimeoutError("simulated")
        f.served = boom
        self.assertTrue(any("could not be read (TimeoutError)" in p for p in self.check(f)))
        f.served = lambda path: (200, None)
        self.assertTrue(any("malformed response" in p for p in self.check(f)))

    def test_no_registry_or_no_history_holds(self):
        f = promoted(self, W1)
        del f.store.objects[REGISTRY_KEY]
        self.assertIn("the store has no registry", self.check(f))


class ResetPreflightCliTests(unittest.TestCase):
    def test_it_is_local_and_read_only(self):
        run = subprocess.run([sys.executable, "-m", "dogear.cli", "publish", "--store", "/nonexistent", "--pubdata",
                              "/nonexistent", "--git", "reset-preflight", "--expect-current", "2027-03-29"],
                             cwd=ROOT, capture_output=True, text=True, env=dict(os.environ))
        self.assertEqual(run.returncode, 4, run.stderr)
        self.assertIn("read-only and local", run.stderr)
        sys.path.insert(0, str(ROOT / "tools"))
        import run_publish
        self.assertNotIn("reset-preflight", run_publish.OPS)  # never from a workflow
        for wf in (ROOT / ".github" / "workflows").glob("*.yml"):
            self.assertNotIn("reset-preflight", wf.read_text(encoding="utf-8"), wf.name)


class PubdataGitTests(unittest.TestCase):
    """Codex re-review of 3228182, item 2: every git command in the preflight must
    succeed, or its failure is a HOLD reason; output is never trusted without it."""

    def setUp(self):
        import shutil
        import tempfile
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@invalid", "GIT_COMMITTER_NAME": "t",
                    "GIT_COMMITTER_EMAIL": "t@invalid", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
        patcher = unittest.mock.patch.dict(os.environ, self.env)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.remote = self.tmp / "remote.git"
        self.git("init", "-q", "--bare", "-b", "main", str(self.remote))
        self.work = self.tmp / "work"
        self.git("clone", "-q", str(self.remote), str(self.work))
        (self.work / "README.md").write_text("x\n")
        self.git("-C", str(self.work), "add", "-A")
        self.git("-C", str(self.work), "commit", "-q", "-m", "init")
        self.git("-C", str(self.work), "push", "-q", "origin", "main")

    def git(self, *args):
        subprocess.run(["git", *args], check=True, capture_output=True)

    def problems(self):
        from dogear.publish.cli import _pubdata_git_problems
        return _pubdata_git_problems(self.work)

    def test_a_clean_checkout_of_remote_main_has_no_problem(self):
        self.assertEqual(self.problems(), [])

    def test_git_status_failing_holds_even_though_head_matches_the_remote(self):
        (self.work / ".git" / "index").write_bytes(b"not an index")  # git status now exits non-zero
        problems = self.problems()
        self.assertTrue(any(p.startswith("git status failed") for p in problems), problems)
        self.assertNotIn("the publication-data checkout is not the remote's current main", problems)

    def test_git_status_failing_makes_the_whole_preflight_hold(self):
        from dogear.publish.cli import _reset_preflight
        (self.work / ".git" / "index").write_bytes(b"not an index")
        args = type("Args", (), {"pubdata": self.work, "expect_current": "2027-03-29"})()
        pub = type("Pub", (), {"reset_preflight": lambda self, week: []})()  # everything else would pass
        with unittest.mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(_reset_preflight(args, pub), 3)
        self.assertIn("reset preflight: HOLD", out.getvalue())
        self.assertIn("git status failed", out.getvalue())

    def test_an_unreadable_head_holds(self):
        (self.work / ".git" / "HEAD").write_text("ref: refs/heads/nowhere\n")
        self.assertTrue(any(p.startswith("git rev-parse HEAD failed") for p in self.problems()))

    def test_an_unreachable_remote_holds(self):
        self.git("-C", str(self.work), "remote", "set-url", "origin", str(self.tmp / "missing.git"))
        self.assertTrue(any(p.startswith("git ls-remote origin main failed") for p in self.problems()))

    def test_a_remote_without_main_holds(self):
        self.git("--git-dir", str(self.remote), "update-ref", "-d", "refs/heads/main")
        self.assertTrue(any(p.startswith("git ls-remote origin main failed") for p in self.problems()))

    def test_local_changes_or_a_moved_remote_hold(self):
        (self.work / "extra.txt").write_text("y\n")
        self.assertIn("the publication-data checkout has local changes", self.problems())
        (self.work / "extra.txt").unlink()
        other = self.tmp / "other"
        self.git("clone", "-q", str(self.remote), str(other))
        self.git("-C", str(other), "commit", "-q", "--allow-empty", "-m", "moved")
        self.git("-C", str(other), "push", "-q", "origin", "main")
        self.assertIn("the publication-data checkout is not the remote's current main", self.problems())
