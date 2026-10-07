"""The S2 drills, end to end through the CLI: the publisher writes to a fake R2
(signatures checked), a local stand-in for the Worker serves from it through the
reference router, and verification reads that over HTTP. The same commands run
against the real staging bucket in S2."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from dogear.cli import main
from dogear.publish import registry as regmod
from dogear.publish import routes
from dogear.publish.pubdata import PubData

try:
    from tests.test_pubdata_git import IDENTITY, git
    from tests.test_r2 import KEY_ID, SECRET, FakeR2
except ImportError:
    from test_pubdata_git import IDENTITY, git
    from test_r2 import KEY_ID, SECRET, FakeR2

ROOT = Path(__file__).resolve().parents[1]


class DrillTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeR2("dogear-issues-staging")
        self.addCleanup(self.fake.close)
        store = self.fake.store()

        class Worker(BaseHTTPRequestHandler):  # stands in for hosting/dogear-site
            def log_message(self, *args):
                pass

            def do_GET(self):
                r = routes.respond(store, "GET", self.path.split("?", 1)[0])
                self.send_response(r.status)
                for k, v in r.headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(r.body)))
                self.end_headers()
                self.wfile.write(r.body)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Worker)
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.base = f"http://127.0.0.1:{server.server_address[1]}"
        # Publication data in git, a fresh clone per run, as the workflows do.
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        env = dict(IDENTITY, DOGEAR_R2_ACCOUNT_ID="0" * 32, DOGEAR_R2_ACCESS_KEY_ID=KEY_ID,
                   DOGEAR_R2_SECRET_ACCESS_KEY=SECRET, DOGEAR_R2_ENDPOINT=self.fake.endpoint)
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.remote, self.runs = self.tmp / "data.git", 0
        git("init", "-q", "--bare", "-b", "main", str(self.remote))
        seed = self.clone()
        (seed / "README.md").write_text("staging publication data\n")
        git("add", "-A", cwd=seed)
        git("commit", "-q", "-m", "init", cwd=seed)
        git("push", "-q", "origin", "main", cwd=seed)

    def clone(self) -> Path:
        self.runs += 1
        path = self.tmp / f"run{self.runs}"
        git("clone", "-q", str(self.remote), str(path))
        return path

    @property
    def pubdata(self) -> Path:
        return self.clone()  # what the remote holds now

    def pub(self, now: str, *op, drill=None, mode="test") -> int:
        work = self.clone()
        argv = ["--dataset", str(ROOT / "fixtures" / "staging-year.json"), "publish", "--store", "r2:dogear-issues-staging",
                "--pubdata", str(work), "--git", "--mode", mode, "--policy", str(ROOT / "fixtures" / "staging-policy.json"),
                "--base-url", self.base, "--no-network-checks"] if mode == "test" else [
                "publish", "--store", "r2:dogear-issues-staging", "--pubdata", str(work), "--base-url", self.base]
        if mode == "test":
            argv += ["--now", now]
        if drill:
            argv += ["--drill", drill]
        return main(argv + list(op))

    def registry(self) -> dict:
        return regmod.parse(self.fake.objects[regmod.REGISTRY_KEY][0])

    def launch(self):
        self.assertEqual(self.pub("2027-03-03T10:00:00+08:00", "launch", "--first-publication"), 0)

    def test_conflict_is_a_real_412_and_publishes_nothing(self):
        self.launch()
        self.assertEqual(self.pub("2027-03-08T06:02:00+08:00", "promote", drill="conflict"), 2)
        self.assertEqual(self.registry()["current"], "2027-03-01")
        aborted = [h for h in PubData(self.pubdata).history() if h["status"] == "aborted"]
        self.assertIn("precondition failed", aborted[-1]["reason"])
        self.assertEqual(self.pub("2027-03-08T06:32:00+08:00", "promote"), 0)  # catch-up publishes
        self.assertEqual(self.registry()["current"], "2027-03-08")

    def test_lost_write_is_retried(self):
        self.launch()
        self.assertEqual(self.pub("2027-03-08T06:02:00+08:00", "promote", drill="lost"), 0)
        self.assertEqual(self.registry()["current"], "2027-03-08")

    def test_applied_write_with_a_dropped_answer_is_confirmed_by_reading_back(self):
        self.launch()
        puts = len([r for r in self.fake.requests if r[0] == "PUT" and r[1].endswith("/publication.json")])
        self.assertEqual(self.pub("2027-03-08T06:02:00+08:00", "promote", drill="applied-then-drop"), 0)
        after = len([r for r in self.fake.requests if r[0] == "PUT" and r[1].endswith("/publication.json")])
        self.assertEqual(after - puts, 1)  # never re-PUT
        self.assertEqual(self.registry()["current"], "2027-03-08")

    def test_unsaved_record_is_recorded_once_by_the_next_run(self):
        self.launch()
        self.assertEqual(self.pub("2027-03-08T06:02:00+08:00", "promote", drill="unsaved-record"), 5)
        self.assertEqual(self.registry()["current"], "2027-03-08")
        self.assertIsNotNone(PubData(self.pubdata).read_pending())
        self.assertEqual(self.pub("2027-03-08T06:32:00+08:00", "promote"), 0)
        self.assertIsNone(PubData(self.pubdata).read_pending())
        promoted = [h for h in PubData(self.pubdata).history() if h.get("week") == "2027-03-08"]
        self.assertEqual([h["status"] for h in promoted], ["committed"])

    def test_full_cycle_correct_rollback_restore(self):
        self.launch()
        self.assertEqual(self.pub("2027-03-05T10:02:00+08:00", "stage-next"), 0)
        self.assertEqual(self.pub("2027-03-08T06:02:00+08:00", "promote"), 0)
        self.assertEqual(self.pub("2027-03-08T09:00:00+08:00", "correct", "--week", "2027-03-08", "--expect-rev", "0",
                                  "--reason", "S2 drill: correction"), 0)
        self.assertEqual(json.loads(routes.fetch(self.fake.store(), "/current.json")[1])["revision"], 1)
        self.assertEqual(self.pub("2027-03-08T09:30:00+08:00", "rollback", "--week", "2027-03-01",
                                  "--reason", "S2 drill: rollback"), 0)
        self.assertEqual((self.registry()["current"], bool(self.registry()["hold"])), ("2027-03-01", True))
        self.assertEqual(self.pub("2027-03-09T06:32:00+08:00", "promote"), 0)  # held: catch-up does nothing
        self.assertEqual(self.registry()["current"], "2027-03-01")
        self.assertEqual(self.pub("2027-03-09T07:00:00+08:00", "restore", "--week", "2027-03-08", "--rev", "1"), 0)
        self.assertEqual((self.registry()["current"], self.registry()["hold"]), ("2027-03-08", None))
        self.assertIsNone(PubData(self.pubdata).read_unverified())  # all verified over HTTP

    def test_drills_are_refused_in_production(self):
        self.assertEqual(main(["publish", "--store", "r2:dogear-issues-staging", "--pubdata", str(self.pubdata),
                               "--drill", "conflict", "status"]), 4)


if __name__ == "__main__":
    unittest.main()
