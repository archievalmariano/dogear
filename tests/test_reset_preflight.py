"""The read-only reset preflight (PUBLISHING.md §20g; Codex review of 7f8fe9e, finding 3):
the staging reset may begin only from a quiescent, reconciled, verified live state.
Anything unresolved is a HOLD with its reason; nothing is ever moved aside."""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

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
    f = launched(test)
    for week in (W1, W2):
        if week > monday:
            break
        f.clock.t = at(week, 6, 2)
        test.assertEqual(f.run().promote().status, "published")
    return f


class ResetPreflightTests(unittest.TestCase):
    def check(self, f, expect=W1):
        return f.run().reset_preflight(expect.isoformat())

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
        self.assertIn("the host does not serve the live registry's manifest", self.check(f))

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
