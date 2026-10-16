"""tools/watchdog_token_check.sh, against a fake curl that plays GitHub: read-only GETs,
the token only in a header file, never followed redirects, and only status codes printed."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "watchdog_token_check.sh"
TOKEN = "TOKEN-SECRET-7c7c"
BODY = "SENTINEL-BODY-3b3b"

FAKE_CURL = r'''#!PYTHON
import json, os, sys
args = sys.argv[1:]
log = os.environ["FAKE_LOG"]
headers = []
i, out, url = 0, None, None
while i < len(args):
    a = args[i]
    if a == "-o":
        out = args[i + 1]; i += 2; continue
    if a == "-H":
        h = args[i + 1]
        if h.startswith("@"):
            h = open(h[1:]).read().strip()
        headers.append(h); i += 2; continue
    if a.startswith("https://") or a.startswith("http://"):
        url = a
    i += 1
with open(log, "a") as f:
    f.write(json.dumps({"argv": args, "url": url, "headers": headers}) + "\n")
codes = json.loads(os.environ["FAKE_CODES"])
path = url.split("/repos/archievalmariano/dogear/", 1)[1]
key = path.split("?")[0]
for prefix in ("actions/runs/", "actions/jobs/"):
    if key.startswith(prefix):
        key = prefix + "*" + key[key.index("/", len(prefix)):]
code = codes.get(key, 200)
jobs = json.loads(os.environ.get("FAKE_JOBS") or "null") or [
    {"id": 11, "status": "completed", "conclusion": "skipped", "steps": []},
    {"id": 12, "status": "completed", "conclusion": "cancelled", "steps": []},
    {"id": 77, "status": "completed", "conclusion": "success", "steps": [{"name": "x"}]}]
if key == "actions/jobs/*/logs" and url.split("/actions/jobs/", 1)[1].split("/")[0] in ("11", "12"):
    code = 404  # a job that never ran has no log
body = {"workflow_runs": [{"id": 42}], "jobs": jobs, "note": os.environ["FAKE_BODY"]}
if key.endswith("/runs") and os.environ.get("FAKE_NO_RUNS"):
    body["workflow_runs"] = []
open(out, "w").write(json.dumps(body))
sys.stdout.write(str(code))
'''


class TokenCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, True))
        curl = self.tmp / "bin" / "curl"
        curl.parent.mkdir()
        curl.write_text(FAKE_CURL.replace("#!PYTHON", "#!" + sys.executable))
        curl.chmod(curl.stat().st_mode | stat.S_IEXEC)
        self.log = self.tmp / "log.jsonl"

    def run_check(self, codes=None, args=(), token=TOKEN, **extra):
        self.log.unlink(missing_ok=True)  # each run's calls only
        env = {"PATH": f"{self.tmp / 'bin'}:{os.environ['PATH']}", "FAKE_LOG": str(self.log),
               "FAKE_CODES": json.dumps(codes or {}), "FAKE_BODY": BODY, **extra}
        if token is not None:
            env["GITHUB_READ_TOKEN"] = token
        p = subprocess.run(["bash", str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=60)
        calls = [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []
        return p.returncode, p.stdout + p.stderr, calls

    GOOD = {"actions/jobs/*/logs": 302}

    def test_a_correct_token_passes_with_exactly_the_five_gets(self):
        code, out, calls = self.run_check(self.GOOD)
        self.assertEqual(code, 0, out)
        self.assertIn("token check: PASS", out)
        self.assertEqual([c["url"].split("/dogear/", 1)[1] for c in calls], [
            "actions/workflows/dogear-stage.yml", "actions/workflows/dogear-promote.yml/runs?per_page=1",
            "actions/runs/42/jobs?per_page=50", "actions/jobs/77/logs", "commits?sha=main&per_page=1"])

    def test_read_only_no_redirects_https_only_and_the_token_only_in_a_header_file(self):
        _, out, calls = self.run_check(self.GOOD)
        for c in calls:
            argv = c["argv"]
            for forbidden in ("-X", "--request", "-d", "--data", "--data-raw", "-F", "--form", "-T", "--upload-file",
                              "-L", "--location", "-I", "--head"):
                self.assertNotIn(forbidden, argv)
            self.assertIn("--max-redirs", argv)
            self.assertEqual(argv[argv.index("--max-redirs") + 1], "0")
            self.assertEqual(argv[argv.index("--proto") + 1], "=https")
            self.assertTrue(c["url"].startswith("https://api.github.com/repos/archievalmariano/dogear/"))
            self.assertFalse(any(TOKEN in a for a in argv), "the token is never on the command line")
            self.assertIn(f"Authorization: Bearer {TOKEN}", c["headers"])  # from the header file
        self.assertNotIn(TOKEN, out)
        self.assertNotIn(BODY, out)

    def test_a_missing_permission_fails(self):
        for path, status in (("commits", 403), ("actions/jobs/*/logs", 404), ("actions/runs/*/jobs", 403),
                             ("actions/workflows/dogear-stage.yml", 401)):
            with self.subTest(path=path):
                code, out, _ = self.run_check(dict(self.GOOD, **{path: status}))
                self.assertEqual(code, 1, out)
                self.assertIn("token check: FAIL", out)
                self.assertNotIn(TOKEN, out)
        code, out, _ = self.run_check({})  # a followed redirect would show 200 for the logs: not accepted
        self.assertEqual(code, 1, out)

    def test_the_logs_check_uses_a_job_that_ran_not_a_skipped_one_listed_first(self):
        code, out, calls = self.run_check(self.GOOD)
        self.assertEqual(code, 0, out)
        self.assertIn("/actions/jobs/77/logs", calls[3]["url"])  # skipped 11 and never-started 12 passed over
        only_skipped = json.dumps([{"id": 11, "status": "completed", "conclusion": "skipped", "steps": []},
                                   {"id": 13, "status": "in_progress", "conclusion": None, "steps": [{"name": "x"}]}])
        code, out, calls = self.run_check(self.GOOD, FAKE_JOBS=only_skipped)
        self.assertEqual(code, 2, out)
        self.assertIn("no job that ran", out)
        self.assertFalse(any("/logs" in c["url"] for c in calls))

    def test_no_token_or_no_run_stops_before_anything_is_checked(self):
        code, _, calls = self.run_check(self.GOOD, token=None)
        self.assertNotEqual(code, 0)
        self.assertEqual(calls, [])
        code, out, _ = self.run_check(self.GOOD, FAKE_NO_RUNS="1")
        self.assertEqual(code, 2)
        self.assertIn("pass any run id", out)
        code, out, calls = self.run_check(self.GOOD, args=("12345",), FAKE_NO_RUNS="1")
        self.assertEqual(code, 0, out)
        self.assertIn("actions/runs/12345/jobs", calls[2]["url"])


if __name__ == "__main__":
    unittest.main()
