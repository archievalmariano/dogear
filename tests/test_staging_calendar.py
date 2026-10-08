"""Staging on the real calendar (PUBLISHING.md §20g): the year-round synthetic fixture
builds an issue for every real week, staging takes only synthetic records, production
takes none, and nothing in the workflow path can set the clock."""

from __future__ import annotations

import datetime as dt
import json
import re
import tempfile
import unittest
from pathlib import Path

from dogear.publish import registry as regmod
from dogear.publish.cli import TARGETS, _apply_target
from dogear.publish.policy import load_policy
from dogear.publish.publisher import Publisher, rollover_week
from dogear.publish.registry import REGISTRY_KEY
from dogear.publish.staging import Checks, StageInputs, StageRefused, stage, verify_staged
from dogear.synthetic import APPROVER, non_synthetic_records, synthetic_records
from dogear.week import MANILA, week_starting

from dogear.publish.store import MemoryStore

try:
    from tests.test_dogear import rec
    from tests.test_publish import Clock, Remote
except ImportError:
    from test_dogear import rec
    from test_publish import Clock, Remote

ROOT = Path(__file__).resolve().parents[1]
STAGING = TARGETS["staging"]
FIXTURE = STAGING["dataset"]
POLICY = load_policy(STAGING["policy"])
INPUTS = StageInputs(FIXTURE, STAGING["affinity"], STAGING["base_url"], "dogear@test")


def manila(day: dt.date, hh: int, mm: int = 0) -> dt.datetime:
    return dt.datetime(day.year, day.month, day.day, hh, mm, tzinfo=MANILA)


class YearRoundFixtureTests(unittest.TestCase):
    def test_every_real_week_builds_a_normal_issue(self):
        """Every Monday-Sunday week from 2026 to 2030 (leap 2028, every year boundary),
        published in sequence so the 365-day history applies from the second year."""
        monday, history, sizes = dt.date(2025, 12, 29), [], set()
        while monday <= dt.date(2030, 12, 30):
            staged = stage(week_starting(monday), INPUTS, POLICY, history, manila(monday - dt.timedelta(days=3), 10, 2),
                           Checks(), "test")
            n = staged.validation["items"]
            self.assertGreaterEqual(n, 3, monday)  # never sparse, never held
            sizes.add(n)
            ids = [r["recordId"] for r in staged.provenance["records"] if r["section"] == "digest"]
            self.assertTrue(all(i.startswith("staging-") for i in ids))
            history.append((monday.isoformat(), staged.issue_sha, ids))
            monday += dt.timedelta(days=7)
        self.assertLessEqual(max(sizes), 8)

    def test_leap_day_appears_only_in_leap_years(self):
        def ids(monday):
            staged = stage(week_starting(monday), INPUTS, POLICY, [], manila(monday, 9), Checks(), "test")
            return {r["recordId"] for r in staged.provenance["records"]}
        self.assertIn("staging-02-29", ids(dt.date(2028, 2, 28)))      # 28 Feb - 5 Mar 2028
        self.assertNotIn("staging-02-29", ids(dt.date(2027, 2, 22)))   # 22-28 Feb 2027
        self.assertNotIn("staging-02-29", ids(dt.date(2027, 3, 1)))


class SyntheticSeparationTests(unittest.TestCase):
    def dataset(self, records: list) -> Path:
        tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        self.addCleanup(Path(tmp.name).unlink)
        json.dump({"schemaVersion": 3, "records": records}, tmp)
        tmp.close()
        return Path(tmp.name)

    def test_markers(self):
        fixture = FIXTURE.read_text(encoding="utf-8")
        self.assertEqual(len(synthetic_records(fixture)), 366)
        self.assertEqual(non_synthetic_records(fixture), [])
        real = rec("ordinary-1", 3, 1)
        self.assertEqual(synthetic_records(json.dumps({"records": [real]})), [])
        self.assertEqual(non_synthetic_records(json.dumps({"records": [real]})), ["ordinary-1"])
        for change in ({"tags": ["staging-test"]}, {"id": "staging-x"}, {"approval": {"by": APPROVER}}):
            marked = dict(real, **change)
            self.assertEqual(len(synthetic_records(json.dumps({"records": [marked]}))), 1, change)
            self.assertEqual(len(non_synthetic_records(json.dumps({"records": [marked]}))), 1, change)  # not all four

    def test_production_refuses_the_fixture_and_any_synthetic_record(self):
        week = week_starting(dt.date(2026, 10, 5))
        with self.assertRaises(StageRefused) as caught:
            stage(week, INPUTS, POLICY, [], manila(week.start, 9), Checks(), "production")
        self.assertIn("synthetic", str(caught.exception))
        records = json.loads(FIXTURE.read_text(encoding="utf-8"))["records"]
        one = [dict(r, id=r["id"].replace("staging-", "real-"), tags=[], approval=None) for r in records]
        one[10] = records[10]  # a single synthetic record among ordinary ones
        mixed = StageInputs(self.dataset(one), STAGING["affinity"], STAGING["base_url"], "dogear@test")
        with self.assertRaises(StageRefused):
            stage(week, mixed, POLICY, [], manila(week.start, 9), Checks(), "production")

    def test_production_refuses_a_week_staged_from_synthetic_data(self):
        week = week_starting(dt.date(2026, 10, 5))
        staged = stage(week, INPUTS, POLICY, [], manila(week.start, 9), Checks(), "test")
        problems = verify_staged(staged, INPUTS, POLICY, [], FIXTURE.read_text(encoding="utf-8"), Checks(), "production")
        self.assertEqual(problems, ["the dataset holds synthetic records; production publishes no synthetic data"])

    def args(self, target: str, **extra):
        from dogear.cli import DEFAULT_AFFINITY, DEFAULT_DATASET
        base = dict(target=target, store=None, mode=None, policy=None, base_url=None,
                    dataset=DEFAULT_DATASET, affinity=DEFAULT_AFFINITY)
        return type("Args", (), dict(base, **extra))()

    def test_the_staging_target_takes_only_synthetic_records(self):
        self.assertIsNone(_apply_target(self.args("staging")))
        stray = self.dataset(json.loads(FIXTURE.read_text(encoding="utf-8"))["records"] + [rec("ordinary-1", 3, 1)])
        original = STAGING["dataset"]
        STAGING["dataset"] = stray
        try:
            problem = _apply_target(self.args("staging"))
        finally:
            STAGING["dataset"] = original
        self.assertEqual(problem, "--target staging publishes only synthetic records; 1 record(s) are not")

    def test_production_never_points_at_fixtures(self):
        prod = TARGETS["production"]
        for key in ("dataset", "affinity", "policy"):
            self.assertNotIn("fixtures", Path(prod[key]).parts, key)
        self.assertEqual((prod["store"], prod["mode"]), ("r2:dogear-issues", "production"))
        self.assertEqual((STAGING["store"], STAGING["mode"]), ("r2:dogear-issues-staging", "test"))
        self.assertEqual(Path(FIXTURE).name, "staging-year.json")


class RealCalendarTests(unittest.TestCase):
    def test_launch_stage_next_and_promote_on_the_real_calendar(self):
        """What the first real-calendar staging cycle does, with the staging target's own
        inputs: launch publishes the current Manila week; Friday stages the next; Monday
        06:00 promotes it."""
        tmp = Path(tempfile.mkdtemp())
        store, remote, clock = MemoryStore(), Remote(tmp), Clock(manila(dt.date(2026, 10, 8), 10))  # Thursday

        def run() -> Publisher:
            return Publisher(store, remote.checkout(), INPUTS, POLICY, Checks(), clock, mode="test",
                             assets={"inter.woff2": b"font-bytes"})

        self.assertEqual(rollover_week(clock()).start, dt.date(2026, 10, 5))
        self.assertEqual(run().launch(first_publication=True).status, "published")
        reg = regmod.parse(store.objects[REGISTRY_KEY].data)
        self.assertEqual(reg["current"], "2026-10-05")
        active = regmod.active_revision(reg, "2026-10-05")
        import hashlib  # current names an immutable, content-addressed issue whose bytes match it
        self.assertTrue(active["issueKey"].startswith("issues/") and active["issueSha256"][:16] in active["issueKey"])
        self.assertEqual(hashlib.sha256(store.objects[active["issueKey"]].data).hexdigest(), active["issueSha256"])
        clock.t = manila(dt.date(2026, 10, 9), 10, 2)  # Friday
        self.assertEqual(run().stage_next().status, "staged")
        clock.t = manila(dt.date(2026, 10, 12), 5, 59)  # Monday, before the rollover
        self.assertEqual(run().promote().status, "noop")
        clock.t = manila(dt.date(2026, 10, 12), 6, 2)
        self.assertEqual(run().promote().status, "published")
        self.assertEqual(regmod.parse(store.objects[REGISTRY_KEY].data)["current"], "2026-10-12")

    def test_the_workflow_path_never_sets_the_clock(self):
        import sys
        sys.path.insert(0, str(ROOT / "tools"))
        import run_publish
        for op, extra in (("launch", {}), ("status", {}), ("promote", {}), ("stage-next", {}),
                          ("stage", {"DOGEAR_WEEK": "2026-10-12"})):
            argv = run_publish.build_argv(dict({"DOGEAR_TARGET": "staging", "DOGEAR_OP": op, "PUBDATA": "/p"}, **extra))
            for flag in ("--now", "--drill", "--no-network-checks", "--mode", "--dataset", "--store", "--policy"):
                self.assertFalse(any(a.startswith(flag) for a in argv), (op, flag, argv))
        for wf in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
            self.assertNotRegex(wf.read_text(encoding="utf-8"), r"--now|DOGEAR_NOW|--drill", wf.name)


class OperateTargetTests(unittest.TestCase):
    """Codex review of 7f8fe9e, item 6: production through DOGEAR operate is never one click."""
    TEXT = (ROOT / ".github" / "workflows" / "dogear-operate.yml").read_text(encoding="utf-8")

    def script(self) -> str:
        block = self.TEXT.split("name: Confirm the target", 1)[1].split("run: |\n", 1)[1].split("\n\n", 1)[0]
        return "\n".join(line[10:] for line in block.splitlines())

    def ok(self, target: str, confirm: str) -> bool:
        import subprocess
        return subprocess.run(["bash", "-c", self.script()], env={"TARGET": target, "CONFIRM": confirm,
                                                                   "PATH": "/usr/bin:/bin"},
                              capture_output=True).returncode == 0

    def test_production_needs_the_typed_confirmation(self):
        self.assertTrue(self.ok("staging", ""))
        self.assertTrue(self.ok("staging", "anything"))
        self.assertTrue(self.ok("production", "production"))
        for confirm in ("", "yes", "Production", " production", "production "):
            self.assertFalse(self.ok("production", confirm), repr(confirm))
        self.assertFalse(self.ok("prod", "production"))

    def test_the_guard_comes_first_and_holds_nothing(self):
        on = self.TEXT.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
        self.assertIn("workflow_dispatch:", on)
        self.assertNotIn("schedule:", on)
        confirm = self.TEXT.split("\n  confirm:\n", 1)[1].split("\n  operate:\n", 1)[0]
        for absent in ("secrets", "environment:", "uses: actions/checkout"):
            self.assertNotIn(absent, confirm)
        operate = self.TEXT.split("\n  operate:\n", 1)[1]
        self.assertIn("needs: confirm", operate)
        self.assertIn("uses: ./.github/workflows/_dogear-publish.yml", operate)
        reusable = (ROOT / ".github" / "workflows" / "_dogear-publish.yml").read_text(encoding="utf-8")
        self.assertIn("environment: ${{ inputs.target }}", reusable)  # the reviewer still decides production
