"""The publisher (dogear.publish): registry, eligibility, transactions, recovery.

Synthetic datasets, simulated storage with injected faults, a fake clock, and
a simulated publication-data remote: a "next run" starts from a fresh checkout
of what was last saved, as a CI job would.
"""

from __future__ import annotations

import contextlib
import copy
import datetime as dt
import io
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dogear.dataset import fingerprint
from dogear.publish import registry as regmod
from dogear.publish import routes
from dogear.publish.policy import PolicyError, PublicationPolicy, TEST_POLICY, load_policy
from dogear.publish.publisher import (Aborted, NeedsHuman, NotReady, Publisher, PublishedUnrecorded,
                                      PublishedUnverified, rollover_week)
from dogear.publish.pubdata import CorruptState, PubData
from dogear.publish.registry import REGISTRY_KEY, CorruptRegistry, NoOp, Refused
from dogear.publish.staging import (MAX_BODY, MAX_ENTRIES, MAX_HEADLINE, MAX_LINE, MAX_SHORT, MAX_URL, Checks,
                                    LinkReport, StageInputs, StageRefused)
from dogear.publish.store import MemoryStore, PreconditionFailed, StoreError

try:
    from tests.test_dogear import rec
except ImportError:  # run as `python -m unittest discover -s tests`
    from test_dogear import rec

MANILA = dt.timezone(dt.timedelta(hours=8))
W0, W1, W2, W3 = (dt.date(2026, 11, d) for d in (2, 9, 16, 23))
BASE = "https://dogear.example.org"


def at(day: dt.date, hh: int = 6, mm: int = 2) -> dt.datetime:
    return dt.datetime(day.year, day.month, day.day, hh, mm, tzinfo=MANILA)


def week_records(monday: dt.date, tag: str, n: int = 3) -> list:
    return [rec(f"{tag}-{i}", monday.month, monday.day + i, significance=4,
                links=[{"type": "read-more", "url": f"https://library.test/{tag}/{i}", "title": "T", "provider": "P"}])
            for i in range(n)]


class Clock:
    def __init__(self, t: dt.datetime):
        self.t = t

    def __call__(self) -> dt.datetime:
        return self.t


class Remote:
    """The publication-data remote: saves copy the working tree here; a fresh run clones it."""

    def __init__(self, base: Path):
        self.base, self.remote, self.fail, self.saves, self.n = base, base / "remote", 0, [], 0
        self.remote.mkdir()

    def committer(self, work: Path):
        def commit(message: str) -> None:
            if self.fail:
                self.fail -= 1
                raise RuntimeError("push rejected (simulated)")
            shutil.rmtree(self.remote)
            shutil.copytree(work, self.remote)
            self.saves.append(message)
        return commit

    def read_saved(self, paths: list) -> dict:
        """What was last saved (the remote), never a working tree."""
        return {p: (self.remote / p).read_bytes() for p in paths if (self.remote / p).is_file()}

    def checkout(self) -> PubData:
        self.n += 1
        work = self.base / f"work{self.n}"
        shutil.copytree(self.remote, work)
        return PubData(work, self.committer(work), self.read_saved)


class Fixture:
    def __init__(self, test: unittest.TestCase, records: list, now: dt.datetime, mode: str = "test",
                 policy: PublicationPolicy = TEST_POLICY, checks: Checks | None = None):
        self.tmp = Path(tempfile.mkdtemp())
        test.addCleanup(shutil.rmtree, self.tmp, True)
        self.dataset = self.tmp / "dataset.json"
        self.write(records)
        self.remote = Remote(self.tmp)
        self.store = MemoryStore()
        self.clock = Clock(now)
        self.mode, self.policy = mode, policy
        self.links_checked: list = []
        self.served = None
        self.generator = "dogear@test"
        self.affinity = self.tmp / "none.json"
        self.checks = checks if checks is not None else Checks(link_checker=self._links, fit_checker=lambda d, ids: [])
        self.pub = self.run()

    def _links(self, urls: list) -> LinkReport:
        self.links_checked.append(list(urls))
        return LinkReport()

    def write(self, records: list) -> None:
        self.records = records
        self.dataset.write_text(json.dumps({"schemaVersion": 3, "records": records}), encoding="utf-8")

    def edit(self, rid: str, **changes) -> None:
        recs = copy.deepcopy(self.records)
        for r in recs:
            if r["id"] == rid:
                r.update(changes)
        self.write(recs)

    def run(self) -> Publisher:
        """A new run: fresh checkout of the publication data, same store and clock."""
        self.pub = Publisher(self.store, self.remote.checkout(), StageInputs(self.dataset, self.affinity,
                                                                             BASE, self.generator),
                             self.policy, self.checks, self.clock, mode=self.mode,
                             assets={"inter.woff2": b"font-bytes"}, served=self.served)
        return self.pub

    def registry(self) -> dict:
        return regmod.parse(self.store.objects[REGISTRY_KEY].data)

    def served_manifest(self) -> dict:
        status, body = routes.fetch(self.store, "/current.json")
        assert status == 200, status
        return json.loads(body)

    def history(self) -> list:
        return PubData(self.remote.remote).history()


def four_weeks() -> list:
    return sum((week_records(w, f"w{i}") for i, w in enumerate((W0, W1, W2, W3))), [])


def launched(test: unittest.TestCase, **kw) -> Fixture:
    f = Fixture(test, four_weeks(), at(W0, 9), **kw)
    f.pub.launch(first_publication=True)
    return f


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.f = launched(self)

    def test_launch_publishes_a_valid_registry_and_manifest(self):
        reg = self.f.registry()
        self.assertEqual(reg["current"], "2026-11-02")
        m = self.f.served_manifest()
        rev = reg["weeks"]["2026-11-02"]["revisions"][0]
        self.assertEqual(m["issuePath"], rev["issueKey"])
        self.assertTrue(re.match(r"^issues/dogear-2026-11-02\.[0-9a-f]{16}\.json$", m["issuePath"]))
        status, body = routes.fetch(self.f.store, "/" + m["issuePath"])
        self.assertEqual(regmod.sha256(body), m["issueSha256"])
        self.assertEqual(json.loads(body)["issueId"], "dogear-2026-11-02")

    def test_corrupt_registries_are_rejected(self):
        good = self.f.registry()
        mutations = [
            lambda r: r.update(schemaVersion=2), lambda r: r.update(current="2026-11-09"),
            lambda r: r.update(current="2026-11-03"), lambda r: r.update(hold="yes"),
            lambda r: r.update(extra=1), lambda r: r["weeks"]["2026-11-02"].update(active=1),
            lambda r: r["weeks"]["2026-11-02"]["revisions"][0].update(issueSha256="0" * 64),
            lambda r: r["weeks"]["2026-11-02"]["revisions"][0].update(webKey="web/x.html"),
            lambda r: r["weeks"]["2026-11-02"]["revisions"][0].update(issueBytes=10**6),
            lambda r: r["weeks"]["2026-11-02"]["revisions"][0].update(issueId="dogear-2026-11-09"),
            lambda r: r["weeks"]["2026-11-02"]["revisions"][0].update(rev=True),
            lambda r: r.update(txn="x"),
        ]
        for mutate in mutations:
            bad = copy.deepcopy(good)
            mutate(bad)
            with self.assertRaises(CorruptRegistry):
                regmod.validate(bad)
        for raw in (b"", b"{", b"[]", b"\xff", b"[" * 100000):
            with self.assertRaises(CorruptRegistry):
                regmod.parse(raw)

    def test_transitions_are_pure_and_guarded(self):
        reg = self.f.registry()
        frozen = copy.deepcopy(reg)
        rev = dict(regmod.active_revision(reg, "2026-11-02"))
        with self.assertRaises(NoOp):
            regmod.promote(reg, W0, rev, "20261102T060200-promote-000000000000")
        with self.assertRaises(Refused):
            regmod.launch(reg, W0, rev, "20261102T060200-launch-000000000000")
        with self.assertRaises(Refused):
            regmod.correct(reg, W0, 3, rev, "typo", "20261102T060200-correct-000000000000")
        with self.assertRaises(Refused):
            regmod.restore(reg, W0, 0, "20261102T060200-restore-000000000000")
        with self.assertRaises(NoOp):
            regmod.resume(reg, "20261102T060200-resume-000000000000")
        self.assertEqual(reg, frozen)


class RoutingTests(unittest.TestCase):
    def test_only_registered_objects_are_served(self):
        f = launched(self)
        reg = f.registry()
        rev = regmod.active_revision(reg, "2026-11-02")
        # An uploaded but unregistered artifact (as after a failed promotion) is unreachable.
        stray_issue = "issues/dogear-2026-11-09.0123456789abcdef.json"
        f.store.put(stray_issue, b"{}", content_type="application/json")
        f.store.put("web/2026-11-09/0123456789abcdef.html", b"<html>", content_type="text/html")
        self.assertEqual(routes.resolve(f.store, "GET", "/" + stray_issue).status, 404)
        self.assertEqual(routes.resolve(f.store, "GET", "/2026-11-09/").status, 404)
        self.assertEqual(routes.resolve(f.store, "GET", "/" + rev["issueKey"]).status, 200)
        page = routes.resolve(f.store, "GET", "/2026-11-02/")
        self.assertEqual((page.status, page.key), (200, rev["webKey"]))
        self.assertEqual(page.headers["Cache-Control"], "public, max-age=60")
        mid = routes.resolve(f.store, "GET", "/2026-11-05/")
        self.assertEqual((mid.status, mid.headers["Location"]), (301, "/2026-11-02/"))
        self.assertEqual(routes.resolve(f.store, "GET", "/").headers["Location"], "/2026-11-02/")
        for path in ("/2026-13-01/", "/2026-11-02/../publication.json", "/publication.json", "/web/",
                     "/issues/dogear-2026-11-02.json", "/staged/2026-11-09/issue.json"):
            r = routes.resolve(f.store, "GET", path)
            self.assertEqual((r.status, r.headers["Cache-Control"]), (404, "no-store"), path)
        self.assertEqual(routes.resolve(f.store, "POST", "/current.json").status, 405)
        self.assertEqual(routes.resolve(f.store, "GET", "/current.json").headers["Cache-Control"], "no-store")

    def test_missing_or_corrupt_registry(self):
        store = MemoryStore()
        self.assertEqual(routes.resolve(store, "GET", "/current.json").status, 503)
        self.assertEqual(routes.resolve(store, "GET", "/2026-11-02/").status, 404)
        store.put(REGISTRY_KEY, b"{not json", content_type="application/json")
        self.assertEqual(routes.resolve(store, "GET", "/current.json").status, 503)


class PolicyTests(unittest.TestCase):
    def test_production_refuses_while_policy_or_checks_are_unset(self):
        f = Fixture(self, four_weeks(), at(W0, 9), mode="production", policy=PublicationPolicy())
        with self.assertRaises(NotReady) as err:
            f.pub.launch(first_publication=True)
        self.assertIn("slot5Bar", str(err.exception))
        self.assertIn("sparseWeek", str(err.exception))
        self.assertNotIn(REGISTRY_KEY, f.store.objects)
        g = Fixture(self, four_weeks(), at(W0, 9), mode="production", checks=Checks())
        with self.assertRaises(NotReady) as err:
            g.pub.launch(first_publication=True)
        self.assertIn("link checker", str(err.exception))

    def test_the_committed_policy_file_is_fully_decided(self):
        """slot5Bar 42 and sparseWeek publish are decided (owner, 8 October 2026); emptyWeek
        quiet-week is set after the firmware contract was synced from v1.2.0."""
        policy = load_policy(Path(__file__).resolve().parents[1] / "data" / "publication-policy.json")
        self.assertEqual((policy.slot5_bar, policy.sparse_week, policy.empty_week), (42, "publish", "quiet-week"))
        self.assertEqual(policy.unset(), [])

    def test_policy_values_are_restricted(self):
        for bad in (dict(slot5_bar=41), dict(slot5_bar=True), dict(sparse_week="maybe"), dict(empty_week="publish")):
            with self.assertRaises(PolicyError):
                PublicationPolicy(**bad)

    def test_slot5_policy_reaches_the_selector(self):
        # Five items whose fifth scores 41: in at 40, out at 42.
        records = [rec(f"s{i}", 11, 2 + (i % 7), significance=5 if i < 4 else 3, recognition="anchor" if i < 4 else "core",
                       discoveryValue=0) for i in range(5)]
        records[4]["significance"], records[4]["recognition"] = 4, "core"
        records[4]["approval"]["fingerprint"] = fingerprint(records[4])
        counts = {}
        for bar in (40, 42):
            f = Fixture(self, records, at(W0, 9), policy=PublicationPolicy(bar, "publish", "hold-previous"))
            f.pub.launch(first_publication=True)
            counts[bar] = len(json.loads(routes.fetch(f.store, "/" + f.served_manifest()["issuePath"])[1])["entries"])
        self.assertGreaterEqual(counts[40], counts[42])

    def test_sparse_and_empty_weeks_follow_policy(self):
        records = week_records(W0, "a", 3) + week_records(W1, "b", 2)
        f = Fixture(self, records, at(W0, 9), policy=PublicationPolicy(42, "hold-previous", "hold-previous"))
        f.pub.launch(first_publication=True)
        f.clock.t = at(W1)
        out = f.run().promote()
        self.assertEqual(out.status, "held")
        self.assertEqual(f.registry()["current"], "2026-11-02")
        f.clock.t = at(W2)  # no records at all
        self.assertEqual(f.run().promote().status, "held")
        g = Fixture(self, records, at(W0, 9))  # TEST_POLICY publishes a sparse week
        g.pub.launch(first_publication=True)
        g.clock.t = at(W1)
        self.assertEqual(g.run().promote().status, "published")


class EligibilityTests(unittest.TestCase):
    def test_only_publishable_records_reach_production(self):
        # Unapproved, held, and edited-after-approval records never render.
        recs = week_records(W0, "ok", 3)
        recs.append(rec("pending", 11, 5, significance=5, approved=False))
        held = rec("held", 11, 6, significance=5, approved=False)
        held["status"] = "disputed"
        recs.append(held)
        edited = rec("edited", 11, 7, significance=5)
        edited["headline"] = "Changed after approval"
        recs.append(edited)
        f = Fixture(self, recs, at(W0, 9))
        f.pub.launch(first_publication=True)
        line = [h for h in f.history() if h["status"] == "committed"][0]
        rendered = set(line["recordIds"]) | set(line["alsoIds"])
        self.assertTrue(rendered)
        self.assertFalse(rendered & {"pending", "held", "edited"})

    def test_independent_check_rejects_tampered_provenance(self):
        from dogear.publish import eligibility
        f = Fixture(self, four_weeks(), at(W0, 9))
        f.pub.stage(W1)
        files = f.pub.pubdata.read_staged(W1)
        prov, issue = json.loads(files["provenance.json"]), json.loads(files["issue.json"])
        text = f.dataset.read_text()
        self.assertEqual(eligibility.check(prov, text, issue), [])
        cases = {
            "missing": None,
            "unknown id": dict(prov, records=prov["records"][:-1] + [dict(prov["records"][-1], recordId="nope")]),
            "wrong fingerprint": dict(prov, records=[dict(prov["records"][0], fingerprint="0" * 16)] + prov["records"][1:]),
            "dropped entry": dict(prov, records=prov["records"][1:]),
            "duplicate": dict(prov, records=prov["records"] + prov["records"][:1]),
        }
        for name, bad in cases.items():
            self.assertTrue(eligibility.check(bad, text, issue), name)
        self.assertTrue(eligibility.check(prov, text, dict(issue, preview=True)))
        # Approval withdrawn after staging.
        f.edit(prov["records"][0]["recordId"], approval=None)
        self.assertTrue(eligibility.check(prov, f.dataset.read_text(), issue))



class GuardTests(unittest.TestCase):
    def test_history_records_each_transaction_once(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        pd = PubData(tmp)
        line = {"txn": "20261109T060200-promote-000000000001", "status": "committed"}
        self.assertTrue(pd.append_history_once(line))
        self.assertFalse(pd.append_history_once(dict(line, status="aborted")))
        self.assertEqual(len(pd.history()), 1)
        (tmp / "history.jsonl").write_text(json.dumps(line) + "\n" + json.dumps(line) + "\n")
        with self.assertRaises(CorruptState):
            pd.history()

    def test_unset_policy_refuses_staging_in_any_mode(self):
        f = Fixture(self, four_weeks(), at(W0, 9), policy=PublicationPolicy(42, "publish", None))
        with self.assertRaises(StageRefused) as err:
            f.pub.stage(W1)
        self.assertIn("emptyWeek", str(err.exception))

    def test_non_reproducible_output_is_refused(self):
        from dogear.publish import staging
        real = staging.generate
        calls = []

        def drifting(*args, **kw):
            sel, issue, web, prov = real(*args, **kw)
            calls.append(1)
            return sel, issue, web + b"<!-- %d -->" % len(calls), prov

        staging.generate = drifting
        self.addCleanup(setattr, staging, "generate", real)
        f = Fixture(self, four_weeks(), at(W0, 9))
        with self.assertRaises(StageRefused) as err:
            f.pub.stage(W1)
        self.assertIn("different bytes", str(err.exception))

    def test_stale_approval_is_ineligible(self):
        from dogear.publish import eligibility
        f = Fixture(self, four_weeks(), at(W0, 9))
        f.pub.stage(W1)
        files = f.pub.pubdata.read_staged(W1)
        prov, issue = json.loads(files["provenance.json"]), json.loads(files["issue.json"])
        rid = prov["records"][0]["recordId"]
        f.edit(rid, approval={"by": "Editor", "on": "2026-10-07", "fingerprint": "0" * 16})
        problems = eligibility.check(prov, f.dataset.read_text(), issue)
        self.assertTrue([p for p in problems if rid in p])


class StageTests(unittest.TestCase):
    def test_friday_stages_next_week_and_monday_publishes_those_exact_bytes(self):
        f = launched(self)
        f.clock.t = at(W0 + dt.timedelta(days=4), 10)  # Friday
        out = f.run().stage_next()
        self.assertEqual(out.status, "staged")
        staged = f.pub.pubdata.read_staged(W1)
        f.clock.t = at(W1)
        out = f.run().promote()
        self.assertEqual(out.status, "published", out.message)
        self.assertFalse([n for n in out.notices if "re-staged" in n or "CHANGED" in n])
        m = f.served_manifest()
        self.assertEqual(m["issueSha256"], regmod.sha256(staged["issue.json"]))
        # Friday's generatedAt, not Monday's.
        self.assertTrue(json.loads(staged["issue.json"])["generatedAt"].startswith("2026-11-06T10:"))

    def test_own_url_is_validated_locally_not_fetched(self):
        f = launched(self)
        f.run().stage(W1)
        checked = [u for batch in f.links_checked for u in batch]
        self.assertTrue(checked)
        self.assertFalse([u for u in checked if u.startswith(BASE)])
        issue = json.loads(f.pub.pubdata.read_staged(W1)["issue.json"])
        self.assertEqual(issue["editionUrl"], f"{BASE}/2026-11-09/")

    def test_blocking_link_refuses_the_stage(self):
        f = launched(self)
        f.checks = Checks(link_checker=lambda urls: LinkReport(blocking=[f"{urls[0]}: 404"]),
                          fit_checker=lambda d, ids: [])
        with self.assertRaises(StageRefused):
            f.run().stage(W1)
        self.assertIsNone(f.pub.pubdata.read_staged(W1))

    def test_withdrawn_approval_after_staging_restages_with_notice(self):
        f = launched(self)
        f.clock.t = at(W0 + dt.timedelta(days=4), 10)
        f.run().stage_next()
        staged = json.loads(f.pub.pubdata.read_staged(W1)["provenance.json"])
        victim = staged["records"][0]["recordId"]
        f.edit(victim, approval=None)
        f.clock.t = at(W1)
        out = f.run().promote()
        self.assertEqual(out.status, "published")
        self.assertTrue([n for n in out.notices if n.startswith("CHANGED PROOF")])
        line = [h for h in f.history() if h.get("week") == "2026-11-09"][0]
        self.assertNotIn(victim, line["recordIds"] + line["alsoIds"])

    def test_approval_added_after_staging_does_not_change_the_staged_issue(self):
        recs = four_weeks() + [rec("late", 11, 12, significance=5, approved=False)]
        f = Fixture(self, recs, at(W0, 9))
        f.pub.launch(first_publication=True)
        f.clock.t = at(W0 + dt.timedelta(days=4), 10)
        f.run().stage_next()
        staged_sha = regmod.sha256(f.pub.pubdata.read_staged(W1)["issue.json"])
        late = rec("late", 11, 12, significance=5)
        f.write([r for r in f.records if r["id"] != "late"] + [late])
        f.clock.t = at(W1)
        f.run().promote()
        self.assertEqual(f.served_manifest()["issueSha256"], staged_sha)

    def test_history_comes_from_published_weeks_only(self):
        f = launched(self)
        f.run().stage(W2)  # staged two weeks ahead, never published
        f.clock.t = at(W1)
        f.run().promote()
        prov = json.loads(f.run().pubdata.read_staged(W1)["provenance.json"])
        self.assertEqual([h[0] for h in prov["inputs"]["history"]], ["2026-11-02"])


class TransactionTests(unittest.TestCase):
    def promote_monday(self, f: Fixture, fault=None):
        f.clock.t = at(W1)
        f.store.fault = fault
        return f.run().promote()

    def test_failed_upload_publishes_nothing_and_the_next_run_succeeds(self):
        f = launched(self)
        before = f.served_manifest()

        def fault(op, key, data):
            return "fail" if op == "put" and key.startswith("issues/") else None

        with self.assertRaises(Aborted):
            self.promote_monday(f, fault)
        self.assertEqual(f.served_manifest(), before)
        self.assertIsNone(f.pub.pubdata.read_pending())
        f.store.fault = None
        self.assertEqual(f.run().promote().status, "published")

    def test_write_that_applied_but_timed_out_is_confirmed_by_reading_back(self):
        f = launched(self)
        out = self.promote_monday(f, lambda op, key, data: "applied-then-fail" if op == "put" and key == REGISTRY_KEY else None)
        self.assertEqual(out.status, "published")
        self.assertEqual(f.registry()["current"], "2026-11-09")
        self.assertEqual(len([h for h in f.history() if h["status"] == "committed"]), 2)
        self.assertIsNone(PubData(f.remote.remote).read_pending())

    def test_lost_write_is_retried_after_rechecking(self):
        f = launched(self)
        calls = []

        def fault(op, key, data):
            if op == "put" and key == REGISTRY_KEY:
                calls.append(1)
                return "lost" if len(calls) == 1 else None

        self.assertEqual(self.promote_monday(f, fault).status, "published")
        self.assertEqual(len(calls), 2)

    def test_retry_across_the_week_boundary_publishes_nothing(self):
        f = launched(self)
        f.clock.t = at(W1)
        f.run().promote()  # W1 published
        f.clock.t = at(W2 + dt.timedelta(days=6), 23, 59)  # Sunday 23:59 of W2, late catch-up

        def fault(op, key, data):
            if op == "put" and key == REGISTRY_KEY:
                f.clock.t = at(W3, 0, 1)  # Monday 00:01: the write is lost and the week ends
                return "lost"

        f.store.fault = fault
        with self.assertRaises(Aborted) as err:
            f.run().promote()
        self.assertIn("week changed", str(err.exception))
        self.assertEqual(f.registry()["current"], "2026-11-09")  # W2 never published retroactively
        self.assertIsNone(PubData(f.remote.remote).read_pending())
        aborted = [h for h in f.history() if h["status"] == "aborted"]
        self.assertEqual(len(aborted), 1)
        # Before 06:00 Monday nothing happens; after it, W3 (not W2) publishes.
        f.store.fault = None
        self.assertEqual(f.run().promote().status, "noop")
        f.clock.t = at(W3)
        self.assertEqual(f.run().promote().status, "published")
        self.assertEqual(f.registry()["current"], "2026-11-23")
        self.assertNotIn("2026-11-16", f.registry()["weeks"])

    def test_conflicting_writer_is_never_overwritten(self):
        f = launched(self)

        def fault(op, key, data):
            if op == "put" and key == REGISTRY_KEY:
                f.store.fault = None
                other = f.registry()
                other["txn"] = "20261109T060000-resume-aaaaaaaaaaaa"
                f.store.put(REGISTRY_KEY, regmod.dumps(other), content_type="application/json")
            return None

        with self.assertRaises(Aborted) as err:
            self.promote_monday(f, fault)
        self.assertIn("changed under us", str(err.exception))
        self.assertEqual(f.registry()["txn"], "20261109T060000-resume-aaaaaaaaaaaa")
        self.assertIsNone(PubData(f.remote.remote).read_pending())

    def test_failed_pending_save_switches_nothing(self):
        f = launched(self)
        f.remote.fail = 2  # promotion stages inline (save 1, tolerated), then records the pending (save 2)
        f.clock.t = at(W1)
        pub = f.run()
        with self.assertRaises(Aborted):
            pub.promote()
        self.assertEqual(f.registry()["current"], "2026-11-02")
        self.assertEqual(f.run().promote().status, "published")

    def test_unreadable_registry_aborts(self):
        f = launched(self)
        f.store.fault = lambda op, key, data: "fail" if op == "get" and key == REGISTRY_KEY else None
        f.clock.t = at(W1)
        with self.assertRaises(Aborted):
            f.run().promote()

    def test_corrupt_state_stops_the_publisher(self):
        f = launched(self)
        f.store.objects[REGISTRY_KEY] = f.store.objects[REGISTRY_KEY].__class__(b'{"schemaVersion": 1}', "x")
        f.clock.t = at(W1)
        with self.assertRaises(CorruptRegistry):
            f.run().promote()
        g = launched(self)
        (g.remote.remote / "history.jsonl").write_text("not json\n")
        g.clock.t = at(W1)
        with self.assertRaises(CorruptState):
            g.run().promote()
        h = launched(self)
        (h.remote.remote / "pending.json").write_text("{")
        with self.assertRaises(CorruptState):
            h.run().promote()

    def test_launch_is_once_and_needs_the_flag(self):
        f = Fixture(self, four_weeks(), at(W0, 9))
        with self.assertRaises(Refused):
            f.pub.launch(first_publication=False)
        f.pub.launch(first_publication=True)
        with self.assertRaises(Refused):
            f.run().launch(first_publication=True)
        g = Fixture(self, four_weeks(), at(W1, 5))  # Monday before 06:00
        with self.assertRaises(Refused):
            g.pub.launch(first_publication=True)

    def test_mid_week_launch_is_the_current_monday_to_sunday_week(self):
        f = Fixture(self, four_weeks(), at(W1 + dt.timedelta(days=2), 15))  # Wednesday
        f.pub.launch(first_publication=True)
        self.assertEqual(f.served_manifest()["week"], {"start": "2026-11-09", "end": "2026-11-15"})


class RecoverySequenceTests(unittest.TestCase):
    """Whole sequences across runs, as the scheduler would drive them."""

    def test_failed_monday_then_catch_up_then_unsaved_history_then_recorded_once(self):
        f = launched(self)
        old = f.served_manifest()

        # Monday 06:02: every registry write is lost; the run gives up, pending kept.
        f.clock.t = at(W1)
        f.store.fault = lambda op, key, data: "lost" if op == "put" and key == REGISTRY_KEY else None
        with self.assertRaises(Aborted) as err:
            f.run().promote()
        self.assertIn("pending kept", str(err.exception))
        self.assertIsNotNone(PubData(f.remote.remote).read_pending())
        self.assertEqual(f.served_manifest(), old)  # readers never saw a partial state

        # Monday 06:17 backup: storage still failing reads, so it cannot decide and changes nothing.
        f.clock.t = at(W1, 6, 17)
        f.store.fault = lambda op, key, data: "fail" if op == "get" and key == REGISTRY_KEY else None
        with self.assertRaises(Aborted):
            f.run().promote()

        # Daily catch-up 06:32: reconciles the never-applied write, then publishes, but the
        # history save after the switch fails.
        f.clock.t = at(W1, 6, 32)
        f.store.fault = None
        saves = len(f.remote.saves)

        def fail_after_switch(op, key, data):
            if op == "put" and key == REGISTRY_KEY:
                f.remote.fail = 1  # the next save is the finalize
            return None

        f.store.fault = fail_after_switch
        pub = f.run()
        with self.assertRaises(PublishedUnrecorded):
            pub.promote()
        self.assertGreater(len(f.remote.saves), saves)
        self.assertEqual(f.registry()["current"], "2026-11-09")
        self.assertEqual(f.served_manifest()["issueId"], "dogear-2026-11-09")
        self.assertIsNotNone(PubData(f.remote.remote).read_pending())

        # Next day's catch-up: records the publication exactly once, publishes nothing new.
        f.store.fault = None
        f.clock.t = at(W1 + dt.timedelta(days=1), 6, 32)
        out = f.run().promote()
        self.assertEqual(out.status, "noop")
        self.assertTrue([n for n in out.notices if "had been published" in n])
        hist = f.history()
        promoted = [h for h in hist if h.get("week") == "2026-11-09"]
        self.assertEqual([h["status"] for h in promoted], ["aborted", "committed"])
        self.assertIsNone(PubData(f.remote.remote).read_pending())
        # And the following week's selection sees W1 in its history.
        f.clock.t = at(W2)
        f.run().promote()
        prov = json.loads(f.run().pubdata.read_staged(W2)["provenance.json"])
        self.assertEqual([h[0] for h in prov["inputs"]["history"]], ["2026-11-02", "2026-11-09"])

    def test_correction_then_catch_up_never_reverts_it(self):
        f = launched(self)
        f.clock.t = at(W1)
        f.run().promote()
        first = f.served_manifest()
        f.edit("w1-0", headline="Corrected headline")
        f.edit("w1-0", approval={"by": "Editor", "on": "2026-11-10",
                                 "fingerprint": fingerprint(next(r for r in f.records if r["id"] == "w1-0"))})
        f.clock.t = at(W1 + dt.timedelta(days=1), 11)
        with self.assertRaises(Refused):
            f.run().correct(W1, expect_rev=1, reason="headline typo")  # wrong expected revision
        out = f.run().correct(W1, expect_rev=0, reason="headline typo")
        self.assertEqual(out.status, "published")
        corrected = f.served_manifest()
        self.assertEqual((corrected["revision"], corrected["issueId"]), (1, first["issueId"]))
        self.assertNotEqual(corrected["issueSha256"], first["issueSha256"])
        # The previous revision's objects remain for rollback, but are not current.
        self.assertIn(first["issuePath"][len(""):], f.store.objects)
        f.clock.t = at(W1 + dt.timedelta(days=2), 6, 32)
        self.assertEqual(f.run().promote().status, "noop")
        self.assertEqual(f.served_manifest(), corrected)
        status, body = routes.fetch(f.store, "/2026-11-09/")
        self.assertEqual(regmod.sha256(body), regmod.active_revision(f.registry(), "2026-11-09")["webSha256"])

    def test_rollback_holds_catch_up_until_restore_or_resume(self):
        f = launched(self)
        f.clock.t = at(W1)
        f.run().promote()
        f.clock.t = at(W1, 9)
        f.run().rollback(W0, None, "wrong issue went out")
        self.assertEqual(f.served_manifest()["issueId"], "dogear-2026-11-02")
        f.clock.t = at(W1 + dt.timedelta(days=1), 6, 32)
        self.assertEqual(f.run().promote().status, "noop")  # W1 is published; never re-promoted
        self.assertEqual(f.served_manifest()["issueId"], "dogear-2026-11-02")
        f.clock.t = at(W2)  # next Monday: the hold stops automatic promotion
        out = f.run().promote()
        self.assertEqual(out.status, "noop")
        self.assertIn("hold", out.message)
        self.assertEqual(f.served_manifest()["issueId"], "dogear-2026-11-02")
        # Restore puts a chosen published revision back explicitly.
        f.run().restore(W1, 0)
        self.assertEqual(f.served_manifest()["issueId"], "dogear-2026-11-09")
        self.assertIsNone(f.registry()["hold"])

    def test_resume_only_lifts_the_hold_and_waits_for_the_next_week(self):
        f = launched(self)
        f.clock.t = at(W1)
        f.run().promote()
        f.run().rollback(W0, None, "pulled for review")
        f.run().resume()
        self.assertEqual(f.served_manifest()["issueId"], "dogear-2026-11-02")  # not restored
        f.clock.t = at(W1, 6, 32)
        self.assertEqual(f.run().promote().status, "noop")  # W1 already published; not re-promoted
        f.clock.t = at(W2)
        self.assertEqual(f.run().promote().status, "published")
        self.assertEqual(f.served_manifest()["issueId"], "dogear-2026-11-16")

    def test_revision_rollback_within_a_week(self):
        f = launched(self)
        f.clock.t = at(W1)
        f.run().promote()
        original = copy.deepcopy(next(r for r in f.records if r["id"] == "w1-0"))
        f.edit("w1-0", headline="Second version")
        f.edit("w1-0", approval={"by": "E", "on": "2026-11-10",
                                 "fingerprint": fingerprint(next(r for r in f.records if r["id"] == "w1-0"))})
        f.run().correct(W1, 0, "update")
        # Revision 0 shows the record's earlier text, which is no longer the approved version.
        with self.assertRaises(Refused) as err:
            f.run().rollback(W1, 0, "the correction was wrong")
        self.assertIn("no longer publishable", str(err.exception))
        self.assertEqual(f.served_manifest()["revision"], 1)
        # Once the editor restores and re-approves that version, the rollback goes through.
        f.write([original if r["id"] == "w1-0" else r for r in f.records])
        f.run().rollback(W1, 0, "the correction was wrong")
        self.assertEqual(f.served_manifest()["revision"], 0)
        self.assertIsNone(f.registry()["hold"])

    def test_rollback_to_a_week_with_a_withdrawn_record_is_refused(self):
        f = launched(self)
        f.clock.t = at(W1)
        f.run().promote()
        f.edit("w0-0", approval=None)
        with self.assertRaises(Refused):
            f.run().rollback(W0, None, "pull this week")
        self.assertEqual(f.registry()["current"], "2026-11-09")

    def test_concurrent_change_while_in_doubt_needs_a_human(self):
        f = launched(self)

        def fault(op, key, data):
            if op == "put" and key == REGISTRY_KEY:
                f.store.fault = None
                other = f.registry()
                other["txn"] = "20261109T060000-resume-bbbbbbbbbbbb"
                f.store.put(REGISTRY_KEY, regmod.dumps(other), content_type="application/json")
                return "lost"
            return None

        f.clock.t = at(W1)
        f.store.fault = fault
        with self.assertRaises(NeedsHuman):
            f.run().promote()
        with self.assertRaises(NeedsHuman):
            f.run().promote()  # still refuses on the next run
        pending = PubData(f.remote.remote).read_pending()
        out = f.run().abandon(pending["txn"])
        self.assertEqual(out.status, "reconciled")
        self.assertIsNone(PubData(f.remote.remote).read_pending())


class CliTests(unittest.TestCase):
    """The command line end to end, on a directory store, with the real font assets."""

    def test_rehearsal_through_the_cli(self):
        from dogear.cli import main
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "data.json").write_text(json.dumps({"schemaVersion": 3, "records": four_weeks()}))
        (tmp / "policy.json").write_text(json.dumps(TEST_POLICY.as_json()))
        common = ["--dataset", str(tmp / "data.json"), "publish", "--store", str(tmp / "store"),
                  "--pubdata", str(tmp / "pub"), "--mode", "test", "--policy", str(tmp / "policy.json"),
                  "--no-network-checks"]
        self.assertEqual(main(common + ["--now", "2026-11-04T09:00:00+08:00", "launch", "--first-publication"]), 0)
        self.assertEqual(main(common + ["--now", "2026-11-06T10:02:00+08:00", "stage-next"]), 0)
        self.assertEqual(main(common + ["--now", "2026-11-09T06:02:00+08:00", "promote"]), 0)
        self.assertEqual(main(common + ["--now", "2026-11-09T06:32:00+08:00", "promote"]), 0)
        self.assertEqual(main(common + ["--now", "2026-11-09T07:00:00+08:00", "launch", "--first-publication"]), 6)
        from dogear.publish.store import DirStore
        store = DirStore(tmp / "store")
        self.assertEqual(json.loads(routes.fetch(store, "/current.json")[1])["issueId"], "dogear-2026-11-09")
        self.assertEqual(routes.fetch(store, "/fonts/LICENSE-Inter.txt")[0], 200)
        self.assertEqual(routes.fetch(store, "/2026-11-02/")[0], 200)
        # Production mode with an undecided policy refuses.
        (tmp / "undecided.json").write_text(json.dumps(PublicationPolicy().as_json()))
        prod = [a for a in common if a not in ("--mode", "test", "--no-network-checks")]
        prod[prod.index(str(tmp / "policy.json"))] = str(tmp / "undecided.json")
        self.assertEqual(main(prod + ["promote"]), 4)


def with_runner_up(monday: dt.date, tag: str) -> list:
    """Three records on one day: the per-day cap sends the third to ALSO THIS WEEK."""
    return [rec(f"{tag}-{i}", monday.month, monday.day, significance=4) for i in range(3)]


class ReviewTwoRegressionTests(unittest.TestCase):
    """Codex review of 599d720: each reproduced sequence, kept as a regression test."""

    def staged_w1(self, records: list) -> Fixture:
        f = Fixture(self, week_records(W0, "w0") + records, at(W0, 9))
        f.pub.launch(first_publication=True)
        f.clock.t = at(W0 + dt.timedelta(days=4), 10)
        f.run().stage_next()
        return f

    def test_1a_also_item_dropped_from_provenance_cannot_publish_after_withdrawal(self):
        f = self.staged_w1(with_runner_up(W1, "d"))
        staged_dir = f.run().pubdata.staged_dir(W1)
        prov = json.loads((staged_dir / "provenance.json").read_text())
        also = [r for r in prov["records"] if r["section"] == "also"]
        self.assertTrue(also, "fixture needs an ALSO THIS WEEK item")
        victim = also[0]["recordId"]
        prov["records"] = [r for r in prov["records"] if r["recordId"] != victim]
        (staged_dir / "provenance.json").write_text(json.dumps(prov))
        shutil.copytree(f.pub.pubdata.root, f.remote.remote, dirs_exist_ok=True)  # as if committed
        f.edit(victim, approval=None)
        f.clock.t = at(W1)
        f.run().promote()
        status, html = routes.fetch(f.store, "/2026-11-09/")
        self.assertEqual(status, 200)
        self.assertNotIn(f"Headline {victim}".encode(), html)

    def test_1b_altered_staged_html_is_not_published(self):
        f = self.staged_w1(week_records(W1, "w1"))
        staged_dir = f.run().pubdata.staged_dir(W1)
        web = (staged_dir / "web.html").read_bytes() + b"<p>INJECTED</p>"
        prov = json.loads((staged_dir / "provenance.json").read_text())
        prov["artifacts"]["webSha256"] = regmod.sha256(web)
        (staged_dir / "web.html").write_bytes(web)
        (staged_dir / "provenance.json").write_text(json.dumps(prov, sort_keys=True, indent=2) + "\n")
        shutil.copytree(f.pub.pubdata.root, f.remote.remote, dirs_exist_ok=True)
        f.clock.t = at(W1)
        f.run().promote()
        self.assertNotIn(b"INJECTED", routes.fetch(f.store, "/2026-11-09/")[1])

    def test_2_approval_withdrawn_during_publication_is_not_published(self):
        f = self.staged_w1(week_records(W1, "w1"))
        victim = "w1-0"

        def fault(op, key, data):
            if op == "put" and key.startswith("web/2026-11-09/"):
                f.edit(victim, approval=None)  # the editor withdraws it mid-run
            return None

        f.store.fault = fault
        f.clock.t = at(W1)
        with self.assertRaises(Aborted):
            f.run().promote()
        self.assertEqual(f.registry()["current"], "2026-11-02")
        f.store.fault = None
        f.run().promote()  # the next run re-stages without it
        line = [h for h in f.history() if h.get("week") == "2026-11-09" and h["status"] == "committed"][0]
        self.assertNotIn(victim, line["recordIds"] + line["alsoIds"])

    def test_2b_withdrawal_during_a_correction_is_not_published(self):
        f = launched(self)
        f.edit("w0-1", headline="Corrected")
        f.edit("w0-1", approval={"by": "E", "on": "2026-11-03",
                                 "fingerprint": fingerprint(next(r for r in f.records if r["id"] == "w0-1"))})

        def fault(op, key, data):
            if op == "put" and key.startswith("issues/"):
                f.edit("w0-1", approval=None)
            return None

        f.store.fault = fault
        with self.assertRaises(Aborted):
            f.run().correct(W0, 0, "headline")
        self.assertEqual(f.registry()["weeks"]["2026-11-02"]["active"], 0)

    def test_3_retry_412_after_uncertain_write_keeps_the_evidence(self):
        f = launched(self)
        state = {"puts": 0, "gets_failed": 0}

        def fault(op, key, data):
            if key != REGISTRY_KEY:
                return None
            if op == "put":
                state["puts"] += 1
                if state["puts"] == 1:
                    return "applied-then-fail"
                if state["puts"] == 2:  # before the retry, another writer rolls back
                    f.store.fault = None
                    other = f.registry()
                    other["current"], other["txn"] = "2026-11-02", "20261109T061000-rollback-cccccccccccc"
                    other["hold"] = {"reason": "operator", "since": "x", "txn": other["txn"]}
                    f.store.put(REGISTRY_KEY, regmod.dumps(other), content_type="application/json")
                    f.store.fault = fault
            if op == "get" and state["puts"] == 1 and state["gets_failed"] == 0:
                state["gets_failed"] += 1
                return "fail"  # the read-back after the timeout fails
            return None

        f.store.fault = fault
        f.clock.t = at(W1)
        with self.assertRaises((NeedsHuman, Aborted)):
            f.run().promote()
        f.store.fault = None
        committed = [h for h in f.history() if h.get("week") == "2026-11-09"]
        self.assertNotIn("aborted", [h["status"] for h in committed])
        # Whatever the run concluded, the following runs can carry on once resolved.
        try:
            f.run().reconcile()
        except NeedsHuman:
            pass
        statuses = [h["status"] for h in f.history() if h.get("week") == "2026-11-09"]
        self.assertEqual(statuses, ["committed"])
        f.run().stage(W2)  # history is complete again

    def test_4_production_promotion_runs_the_production_checks_on_the_staged_artifacts(self):
        f = Fixture(self, four_weeks(), at(W0, 9), checks=Checks())  # stage without checks
        f.pub.launch(first_publication=True)
        f.run().stage(W1)
        fit_calls = []

        def reject_fit(dataset, ids):
            fit_calls.append(ids)
            return ["overflows"]

        f.mode = "production"
        f.checks = Checks(link_checker=lambda urls: LinkReport(), fit_checker=reject_fit)
        f.clock.t = at(W1)
        with self.assertRaises(StageRefused):
            f.run().promote()
        self.assertTrue(fit_calls)
        self.assertEqual(f.registry()["current"], "2026-11-02")

    def test_4b_checks_are_rerun_at_promotion_even_after_a_production_stage(self):
        f = Fixture(self, four_weeks(), at(W0, 9), mode="production")
        f.pub.launch(first_publication=True)
        f.run().stage(W1)  # passes production checks on Friday
        f.checks = Checks(link_checker=lambda urls: LinkReport(), fit_checker=lambda d, ids: ["now overflows"])
        f.clock.t = at(W1)
        with self.assertRaises(StageRefused):
            f.run().promote()
        self.assertEqual(f.registry()["current"], "2026-11-02")

    def test_4c_a_test_mode_stage_is_restaged_under_production_validation(self):
        f = Fixture(self, four_weeks(), at(W0, 9), checks=Checks())
        f.pub.launch(first_publication=True)
        f.run().stage(W1)  # test mode, no checks
        f.mode = "production"
        f.checks = Checks(link_checker=lambda urls: LinkReport(), fit_checker=lambda d, ids: [])
        f.clock.t = at(W1)
        out = f.run().promote()
        self.assertEqual(out.status, "published")
        self.assertTrue([n for n in out.notices if "without production validation" in n])
        staged = json.loads(f.run().pubdata.read_staged(W1)["validation.json"])
        self.assertEqual(staged["mode"], "production")

    def test_5_clock_override_is_refused_in_production(self):
        from dogear.cli import main
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "policy.json").write_text(json.dumps(TEST_POLICY.as_json()))
        code = main(["publish", "--store", str(tmp / "s"), "--pubdata", str(tmp / "p"), "--policy",
                     str(tmp / "policy.json"), "--now", "2026-11-09T06:02:00+08:00", "status"])
        self.assertEqual(code, 4)

    def test_6_unverified_serving_is_not_success_and_is_retried(self):
        f = launched(self)
        f.served = lambda path: (503, b"")
        f.clock.t = at(W1)
        with self.assertRaises(PublishedUnverified):
            f.run().promote()
        self.assertEqual(f.registry()["current"], "2026-11-09")  # the registry did commit
        f.clock.t = at(W1, 6, 32)
        with self.assertRaises(PublishedUnverified):
            f.run().promote()  # still failing: still not success, and nothing rewritten
        f.served = None
        out = f.run().promote()
        self.assertEqual(out.status, "noop")
        self.assertTrue([n for n in out.notices if "verified" in n])
        self.assertFalse((f.remote.remote / "unverified.json").exists())


class ReviewThreeRegressionTests(unittest.TestCase):
    """Codex re-review of 613c1ce, finding 6: what verification must cover."""

    def test_device_issue_not_served_is_unverified(self):
        f = launched(self)
        f.served = lambda p: (404, b"") if p.startswith("/issues/") else routes.fetch(f.store, p)
        f.clock.t = at(W1)
        with self.assertRaises(PublishedUnverified) as err:
            f.run().promote()
        self.assertIn("/issues/dogear-2026-11-09.", str(err.exception))
        f.served = None
        f.clock.t = at(W1, 6, 32)
        out = f.run().promote()
        self.assertTrue([n for n in out.notices if "verified" in n])
        self.assertIsNone(PubData(f.remote.remote).read_unverified())

    def test_correcting_a_past_week_verifies_that_weeks_page(self):
        f = launched(self)
        f.clock.t = at(W1)
        f.run().promote()  # 9 Nov is current
        stale = routes.fetch(f.store, "/2026-11-02/")[1]
        f.edit("w0-1", headline="Corrected")
        f.edit("w0-1", approval={"by": "E", "on": "2026-11-10",
                                 "fingerprint": fingerprint(next(r for r in f.records if r["id"] == "w0-1"))})
        f.served = lambda p: (200, stale) if p == "/2026-11-02/" else routes.fetch(f.store, p)
        with self.assertRaises(PublishedUnverified) as err:
            f.run().correct(W0, 0, "headline")
        self.assertIn("/2026-11-02/", str(err.exception))
        f.clock.t = at(W1, 7)
        with self.assertRaises(PublishedUnverified):
            f.run().promote()  # still stale: still not success
        f.served = None
        f.run().promote()
        self.assertIsNone(PubData(f.remote.remote).read_unverified())

    def test_fresh_correction_proceeds_while_an_earlier_revision_fails_verification(self):
        f = launched(self)
        f.clock.t = at(W1)
        f.run().promote()
        rev0 = regmod.active_revision(f.registry(), "2026-11-09")["webKey"]
        broken = set()

        def served(path):
            r = routes.resolve(f.store, "GET", path)
            if r.key is not None and r.key.startswith("web/2026-11-09/") and (r.key in broken or not broken and r.key != rev0):
                return 503, b""  # the host cannot serve revision 1's page
            return routes.fetch(f.store, path)

        f.served = served

        def approve_edit(headline):
            f.edit("w1-0", headline=headline)
            f.edit("w1-0", approval={"by": "E", "on": "2026-11-10",
                                     "fingerprint": fingerprint(next(r for r in f.records if r["id"] == "w1-0"))})

        approve_edit("First correction")
        with self.assertRaises(PublishedUnverified):
            f.run().correct(W1, 0, "first")
        broken.add(regmod.active_revision(f.registry(), "2026-11-09")["webKey"])
        approve_edit("Second correction")
        out = f.run().correct(W1, 1, "replace the revision the host cannot serve")
        self.assertEqual(out.status, "published")
        self.assertEqual(f.served_manifest()["revision"], 2)
        self.assertIsNone(PubData(f.remote.remote).read_unverified())  # revision 1's page is superseded

    def test_malformed_verification_state_stops_the_publisher(self):
        f = launched(self)
        f.served = lambda p: (503, b"")
        f.clock.t = at(W1)
        with self.assertRaises(PublishedUnverified):
            f.run().promote()
        good = (f.remote.remote / "unverified.json").read_text()
        entry = json.loads(good)["entries"][0]
        bad_values = ["null", "{}", "[]", '{"entries": []}', '{"entries": [{"txn": "x"}]}', "not json",
                      json.dumps({"entries": [dict(entry, targets=[dict(entry["targets"][0], sha256="zz")])]}),
                      json.dumps({"entries": [dict(entry, targets=[])]}),
                      json.dumps({"entries": [entry], "extra": 1})]
        for bad in bad_values:
            (f.remote.remote / "unverified.json").write_text(bad)
            with self.assertRaises(CorruptState, msg=bad):
                f.run().promote()
        (f.remote.remote / "unverified.json").write_text(good)
        with self.assertRaises(PublishedUnverified):
            f.run().promote()  # the genuine record still fails while the host does


class ReviewFourRegressionTests(unittest.TestCase):
    """Codex re-review of b66e214, finding 6: a well-formed but incomplete or altered
    verification record must not pass. History keeps each transaction's targets."""

    def unverified_promote(self, broken: str = "/issues/"):
        """Promote 9 Nov while the host cannot serve paths starting with ``broken``."""
        f = launched(self)
        self.broken = broken
        f.served = lambda p: (404, b"") if p.startswith(broken) else routes.fetch(f.store, p)
        f.clock.t = at(W1)
        with self.assertRaises(PublishedUnverified):
            f.run().promote()
        f.clock.t = at(W1, 6, 32)
        return f

    def tamper(self, f, change) -> None:
        path = f.remote.remote / "unverified.json"
        data = json.loads(path.read_text())
        change(data["entries"][-1]["targets"])
        path.write_text(json.dumps(data))

    def assert_stops_and_clears_nothing(self, f) -> None:
        before = (f.remote.remote / "unverified.json").read_text()
        with self.assertRaises(CorruptState):
            f.run().promote()
        self.assertEqual((f.remote.remote / "unverified.json").read_text(), before)
        promote = f.history()[-1]
        self.assertNotIn(promote["txn"], [v["txn"] for v in PubData(f.remote.remote).verified()])
        broken = [t["path"] for t in promote["verify"] if t["path"].startswith(self.broken)]
        self.assertEqual([f.served(path)[0] for path in broken], [404])  # still broken: it stays outstanding

    def test_omitted_issue_target_is_rejected(self):
        f = self.unverified_promote()
        self.tamper(f, lambda ts: ts.pop())  # Codex's reproduction: drop the issue target
        self.assert_stops_and_clears_nothing(f)

    def test_omitted_week_is_rejected(self):
        f = self.unverified_promote()
        self.tamper(f, lambda ts: ts.__delitem__(slice(1, None)))  # well formed: the manifest alone
        self.assert_stops_and_clears_nothing(f)

    def test_page_target_with_the_wrong_week_is_rejected(self):
        f = self.unverified_promote(broken="/2026-11-09/")
        self.tamper(f, lambda ts: ts[1].update(week="2026-11-02"))  # Codex's reproduction
        self.assert_stops_and_clears_nothing(f)

    def test_consistent_but_wrong_week_is_rejected(self):
        f = self.unverified_promote(broken="/2026-11-09/")
        w0 = regmod.active_revision(f.registry(), "2026-11-02")

        def move(ts):  # point the targets at another, well-formed week entirely
            ts[1].update(week="2026-11-02", path="/2026-11-02/")
            ts[2].update(path="/" + w0["issueKey"], sha256=w0["issueSha256"])
        self.tamper(f, move)
        self.assert_stops_and_clears_nothing(f)

    def test_missing_verification_record_is_rejected(self):
        f = self.unverified_promote()
        (f.remote.remote / "unverified.json").unlink()  # "nothing outstanding" is not believed
        with self.assertRaises(CorruptState):
            f.run().promote()
        with self.assertRaises(CorruptState):
            f.run().status()

    def test_altered_history_copy_is_rejected(self):
        f = self.unverified_promote()
        path = f.remote.remote / "history.jsonl"
        lines = [json.loads(line) for line in path.read_text().splitlines()]
        good = path.read_text()
        lines[-1]["verify"] = lines[-1]["verify"][:1]
        path.write_text("".join(json.dumps(line) + "\n" for line in lines))
        with self.assertRaises(CorruptState):
            f.run().promote()
        launch = [json.loads(line) for line in good.splitlines()]
        del launch[0]["verify"]  # a settled transaction's copy is checked too
        path.write_text("".join(json.dumps(line) + "\n" for line in launch))
        with self.assertRaises(CorruptState):
            f.run().status()

    def test_settled_transactions_are_recorded_and_the_genuine_record_still_settles(self):
        f = self.unverified_promote()
        f.served = None
        out = f.run().promote()
        self.assertTrue([n for n in out.notices if "verified" in n])
        settled = [json.loads(line)["txn"] for line in (f.remote.remote / "verified.jsonl").read_text().splitlines()]
        committed = [h["txn"] for h in f.history() if h["status"] == "committed"]
        self.assertEqual(sorted(settled), sorted(committed))
        f.run().status()  # consistent: every committed transaction is settled
        good = (f.remote.remote / "verified.jsonl").read_text()
        (f.remote.remote / "verified.jsonl").write_text("")
        with self.assertRaises(CorruptState):
            f.run().status()  # settled records gone: the transactions are unaccounted for
        (f.remote.remote / "verified.jsonl").write_text(good + json.dumps({"txn": "invented", "at": "x"}) + "\n")
        with self.assertRaises(CorruptState):
            f.run().status()  # settles a transaction history never committed

    def test_entry_for_an_unknown_transaction_is_rejected(self):
        f = self.unverified_promote()
        path = f.remote.remote / "unverified.json"
        data = json.loads(path.read_text())
        data["entries"].append(dict(data["entries"][0], txn="20261109T070000-promote-000000000000"))
        path.write_text(json.dumps(data))
        with self.assertRaises(CorruptState):
            f.run().promote()

    def test_target_shape_is_fixed(self):
        from dogear.publish.pubdata import targets_problem
        f = self.unverified_promote()
        good = f.history()[-1]["verify"]
        self.assertIsNone(targets_problem(good))
        page, issue = good[1], good[2]
        other = "2026-11-02"
        bad = {
            "page week only": [good[0], dict(page, week=other), issue],
            "page path only": [good[0], dict(page, path=f"/{other}/"), issue],
            "not a Monday": [good[0], dict(page, week="2026-11-10", path="/2026-11-10/"), issue],
            "issue of another week": [good[0], page, dict(issue, path=issue["path"].replace("2026-11-09", other))],
            "issue hash prefix": [good[0], page, dict(issue, sha256="0" * 64)],
            "issue missing": [good[0], page],
            "page missing": [good[0], issue],
            "swapped": [good[0], issue, page],
            "no manifest": [page, issue],
            "duplicate week": [good[0], page, issue, page, issue],
        }
        for name, targets in bad.items():
            self.assertIsNotNone(targets_problem(targets), name)

    def test_pending_targets_that_disagree_with_its_registry_are_not_recorded(self):
        f = launched(self)
        f.clock.t = at(W1)

        def fail_after_switch(op, key, data):
            if op == "put" and key == REGISTRY_KEY:
                f.remote.fail = 1  # the finalize save fails: pending stays for the next run
            return None

        f.store.fault = fail_after_switch
        with self.assertRaises(PublishedUnrecorded):
            f.run().promote()
        f.store.fault = None
        path = f.remote.remote / "pending.json"
        pending = json.loads(path.read_text())
        pending["verify"][1]["sha256"] = "0" * 64  # well formed, but not the page it published
        path.write_text(json.dumps(pending))
        with self.assertRaises(NeedsHuman):
            f.run().promote()
        self.assertFalse([h for h in f.history() if h["txn"] == pending["txn"]])


class ReviewFiveRegressionTests(unittest.TestCase):
    """Codex re-review of 42f72fd, finding 6: finalization must not trust the pending
    target list; it recomputes the complete list from the registries before and after."""

    def switched_unrecorded(self, op=None):
        """Promote 9 Nov (or run ``op``); the registry switches, the record's save fails."""
        f = launched(self)
        f.clock.t = at(W1)
        f.served = lambda p: (404, b"") if p.startswith("/issues/") else routes.fetch(f.store, p)

        def fail_after_switch(o, key, data):
            if o == "put" and key == REGISTRY_KEY:
                f.remote.fail = 1  # the finalize save fails: pending stays for the next run
            return None

        if op is None:
            f.store.fault = fail_after_switch
            with self.assertRaises(PublishedUnrecorded):
                f.run().promote()
        else:
            with self.assertRaises(PublishedUnverified):
                f.run().promote()
            f.served = None
            f.clock.t = at(W1, 7)
            f.store.fault = fail_after_switch
            with self.assertRaises(PublishedUnrecorded):
                op(f.run())
        f.store.fault = None
        f.clock.t = at(W1 + dt.timedelta(days=1), 6, 32)
        return f

    def edit_pending(self, f, change) -> tuple:
        """Edit the genuine pending record (each edit starts from it)."""
        path = f.remote.remote / "pending.json"
        good = getattr(f, "genuine_pending", None) or path.read_text()
        f.genuine_pending = good
        pending = json.loads(good)
        change(pending)
        path.write_text(json.dumps(pending))
        return good, pending["txn"]

    def assert_recovery_stops(self, f, txn) -> None:
        for attempt in (lambda: f.run().promote(), lambda: f.run().reconcile(), lambda: f.run().abandon(txn)):
            with self.assertRaises(NeedsHuman):
                attempt()
        self.assertIsNotNone(PubData(f.remote.remote).read_pending())  # kept for a human
        self.assertFalse([h for h in f.history() if h["txn"] == txn])  # nothing recorded
        self.assertNotIn(txn, PubData(f.remote.remote).unverified_path.read_text()
                         if PubData(f.remote.remote).unverified_path.exists() else "")
        self.assertNotIn(txn, [v["txn"] for v in PubData(f.remote.remote).verified()])

    def test_pending_without_its_page_and_issue_stops_recovery(self):
        f = self.switched_unrecorded()
        good, txn = self.edit_pending(f, lambda p: p["verify"].__delitem__(slice(1, None)))  # Codex's reproduction
        self.assert_recovery_stops(f, txn)
        issue = "/" + regmod.active_revision(f.registry(), "2026-11-09")["issueKey"]
        self.assertEqual(f.served(issue)[0], 404)
        # The genuine record recovers: recorded once, and unverified until the host serves the issue.
        (f.remote.remote / "pending.json").write_text(good)
        with self.assertRaises(PublishedUnverified):
            f.run().promote()
        self.assertEqual(len([h for h in f.history() if h["txn"] == txn]), 1)
        f.served = None
        f.run().promote()
        self.assertIsNone(PubData(f.remote.remote).read_unverified())

    def test_pending_with_an_extra_or_altered_target_stops_recovery(self):
        f = self.switched_unrecorded()
        w0 = regmod.active_revision(f.registry(), "2026-11-02")
        extra = [{"kind": "page", "path": "/2026-11-02/", "week": "2026-11-02", "sha256": w0["webSha256"]},
                 {"kind": "issue", "path": "/" + w0["issueKey"], "sha256": w0["issueSha256"]}]
        good, txn = self.edit_pending(f, lambda p: p.update(verify=p["verify"][:1] + extra + p["verify"][1:]))
        self.assert_recovery_stops(f, txn)  # an extra, already-served week
        good, txn = self.edit_pending(f, lambda p: p["verify"][2].update(sha256="0" * 64))
        self.assert_recovery_stops(f, txn)  # the issue's hash altered

    def test_pending_with_an_altered_previous_registry_stops_recovery(self):
        f = self.switched_unrecorded()

        def change(p):  # the replaced registry, edited so the new week looks unchanged
            before = json.loads(p["expectedRegistry"])
            before["current"] = "2026-11-09"
            p["expectedRegistry"] = json.dumps(before)
        good, txn = self.edit_pending(f, change)
        self.assert_recovery_stops(f, txn)
        good, txn = self.edit_pending(f, lambda p: p.update(expectedRegistry=None, expectedRegistrySha=None))
        self.assert_recovery_stops(f, txn)

    def test_coordinated_edits_are_caught_by_the_recorded_hashes(self):
        """Edits that keep ``verify`` consistent with an altered registry: only the
        hashes recorded before the switch reveal them."""
        from dogear.publish.publisher import verification_targets
        f = self.switched_unrecorded()
        after = PubData(f.remote.remote).read_pending()["intendedRegistry"]

        def previous_edited(p):  # the replaced registry already showing 9 Nov: "nothing changed"
            before = dict(after, txn=json.loads(p["expectedRegistry"])["txn"])
            p["expectedRegistry"] = regmod.dumps(before).decode("utf-8")
            p["verify"] = verification_targets(before, after)
        good, txn = self.edit_pending(f, previous_edited)
        self.assertEqual(len(json.loads((f.remote.remote / "pending.json").read_text())["verify"]), 1)
        self.assert_recovery_stops(f, txn)

        def previous_dropped(p):  # as if this were the first publication
            p.update(expectedRegistry=None, expectedRegistrySha=None, verify=verification_targets(None, after))
        good, txn = self.edit_pending(f, previous_dropped)
        self.assert_recovery_stops(f, txn)

        def published_edited(p):  # the published registry replaced by the previous one
            before = regmod.parse(p["expectedRegistry"].encode("utf-8"))
            p["intendedRegistry"] = before
            p["verify"] = verification_targets(before, before)
        good, txn = self.edit_pending(f, published_edited)
        self.assert_recovery_stops(f, txn)

    def test_rollback_recovery_recomputes_the_newly_current_week(self):
        f = self.switched_unrecorded(lambda pub: pub.rollback(W0, None, "withdrawn"))
        pending = PubData(f.remote.remote).read_pending()
        self.assertEqual(pending["op"], "rollback")
        self.assertIn("/2026-11-02/", [t["path"] for t in pending["verify"]])
        good, txn = self.edit_pending(f, lambda p: p.update(verify=p["verify"][:1]))
        self.assert_recovery_stops(f, txn)
        (f.remote.remote / "pending.json").write_text(good)
        out = f.run().reconcile()
        self.assertIn("had been published", out.message)
        self.assertIsNone(PubData(f.remote.remote).read_unverified())


class ActiveIssueOnlyTests(unittest.TestCase):
    """Codex S2 review: only a week's active revision is addressable (§7), so a
    correction withdraws the old issue URL."""

    def set_headline(self, f, rid, headline):
        f.edit(rid, headline=headline)
        f.edit(rid, approval={"by": "E", "on": "2026-11-10",
                              "fingerprint": fingerprint(next(r for r in f.records if r["id"] == rid))})

    def test_correction_withdraws_the_old_issue_and_rollback_restores_it(self):
        f = launched(self)
        f.clock.t = at(W1)
        f.run().promote()
        rev0 = regmod.active_revision(f.registry(), "2026-11-09")
        original = next(r for r in f.records if r["id"] == "w1-0")["headline"]
        self.set_headline(f, "w1-0", "Corrected")
        f.run().correct(W1, 0, "headline")
        rev1 = regmod.active_revision(f.registry(), "2026-11-09")
        self.assertEqual(routes.resolve(f.store, "GET", "/" + rev0["issueKey"]).status, 404)
        self.assertEqual(routes.resolve(f.store, "GET", "/" + rev1["issueKey"]).status, 200)
        self.assertIn(rev0["issueKey"], f.store.objects)  # kept in storage for rollback
        self.set_headline(f, "w1-0", original)  # the original version approved again, so rollback is allowed
        f.run().rollback(W1, 0, "back to the original")
        self.assertEqual(routes.resolve(f.store, "GET", "/" + rev0["issueKey"]).status, 200)
        self.assertEqual(routes.resolve(f.store, "GET", "/" + rev1["issueKey"]).status, 404)
        self.assertIsNone(PubData(f.remote.remote).read_unverified())

    def test_unverified_issue_settles_once_a_correction_supersedes_it(self):
        f = launched(self)
        f.clock.t = at(W1)
        broken = []  # the host never serves revision 0's issue
        f.served = lambda p: (404, b"") if p in broken else routes.fetch(f.store, p)
        f.store.fault = lambda op, key, data: broken.append("/" + key) if op == "put" and \
            key.startswith("issues/dogear-2026-11-09.") and not broken else None
        with self.assertRaises(PublishedUnverified):
            f.run().promote()
        f.store.fault = None
        self.assertEqual(broken, ["/" + regmod.active_revision(f.registry(), "2026-11-09")["issueKey"]])
        self.set_headline(f, "w1-0", "Corrected")
        self.assertEqual(f.run().correct(W1, 0, "replace the revision the host could not serve").status, "published")
        self.assertIsNone(PubData(f.remote.remote).read_unverified())  # superseded, though still unserved


class WindowTests(unittest.TestCase):
    def test_rollover_is_monday_0600_manila(self):
        self.assertIsNone(rollover_week(at(W1, 5, 59)))
        self.assertEqual(rollover_week(at(W1, 6, 0)).start, W1)
        self.assertEqual(rollover_week(at(W1 + dt.timedelta(days=6), 23, 59)).start, W1)
        utc = dt.datetime(2026, 11, 8, 22, 2, tzinfo=dt.timezone.utc)  # Sunday 22:02 UTC
        self.assertEqual(rollover_week(utc).start, W1)



# ---- review by exception (PUBLISHING §4b): the audited hold and unattended runs ----

FRIDAY = dt.timedelta(days=3)  # the Friday before a Monday


def prod_launched(test: unittest.TestCase, records: list = None) -> Fixture:
    """Production mode (the presentation guard reads served issues), W0 launched, W1 promoted."""
    f = Fixture(test, records if records is not None else four_weeks(), at(W0, 9), mode="production")
    test.assertEqual(f.pub.launch(first_publication=True).status, "published")
    f.clock.t = at(W1)
    test.assertEqual(f.run().promote().status, "published")
    return f


def saved(f: Fixture, name: str) -> bytes:
    path = f.remote.remote / name
    return path.read_bytes() if path.is_file() else None


def lines(f: Fixture, op: str) -> list:
    return [h for h in f.history() if h["op"] == op]


def unserved(f: Fixture, *paths: str):
    """The host fails these paths; everything else is served as stored."""
    def served(path: str) -> tuple:
        if path in paths:
            return 404, b""
        return routes.fetch(f.store, path)
    return served


def remote_pending(f: Fixture) -> bytes:
    return saved(f, "pending.json")


def rewrite_remote_json(f: Fixture, name: str, change) -> None:
    path = f.remote.remote / name
    data = json.loads(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(json.dumps(data, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def rewrite_history(f: Fixture, txn: str, **changes) -> None:
    path = f.remote.remote / "history.jsonl"
    out = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        h = json.loads(raw)
        if h["txn"] == txn:
            h.update(changes)
        out.append(json.dumps(h, sort_keys=True))
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


class HoldTests(unittest.TestCase):
    """`hold <reason>`: audited, nothing visible changes, works when the current
    revision itself is what is broken (correction 1)."""

    def hold(self, f: Fixture, hh: int = 10, reason: str = "check the next issue"):
        f.clock.t = at(f.clock.t.date(), hh)
        return f.run().hold(reason)

    def test_a_hold_changes_nothing_visible_and_is_recorded_once(self):
        f = prod_launched(self)
        before, manifest = f.registry(), f.served_manifest()
        out = self.hold(f)
        self.assertEqual((out.status, out.code, out.week), ("held", "HELD", "2026-11-09"))
        after = f.registry()
        self.assertEqual((after["current"], after["weeks"]), (before["current"], before["weeks"]))
        self.assertEqual(regmod.manifest_bytes(after), regmod.manifest_bytes(before))
        self.assertEqual(after["hold"], {"reason": "check the next issue", "since": at(W1, 10).isoformat(),
                                         "txn": out.txn})
        self.assertEqual(f.served_manifest(), manifest)
        [line] = lines(f, "hold")
        self.assertEqual((line["status"], line["verify"], line["code"], line["txn"]), ("committed", [], "HELD", out.txn))
        self.assertEqual([v["txn"] for v in PubData(f.remote.remote).verified()].count(out.txn), 1)
        self.assertIsNone(saved(f, "unverified.json"))
        self.assertIsNone(remote_pending(f))
        self.assertEqual(saved(f, "publication.json"), f.store.objects[REGISTRY_KEY].data)  # the mirror

    def test_noop_when_held_and_refused_with_nothing_published(self):
        f = prod_launched(self)
        self.hold(f)
        self.assertEqual(self.hold(f, 11).status, "noop")
        self.assertEqual(len(lines(f, "hold")), 1)
        g = Fixture(self, four_weeks(), at(W0, 9), mode="production")
        with self.assertRaises(Refused):
            g.pub.hold("nothing yet")
        with self.assertRaises(Refused):
            self.hold(prod_launched(self), reason="   ")

    def test_promote_waits_while_held_and_resume_releases(self):
        f = prod_launched(self)
        self.hold(f)
        f.clock.t = at(W2)
        self.assertEqual(f.run().promote().status, "noop")
        self.assertEqual(f.registry()["current"], "2026-11-09")
        f.clock.t = at(W2, 9)
        self.assertEqual(f.run().resume().status, "published")
        self.assertEqual(f.run().promote().status, "published")
        self.assertEqual(f.registry()["current"], "2026-11-16")

    def test_a_hold_works_when_the_current_revision_is_broken(self):
        cases = {
            "approval withdrawn": lambda f: f.edit("w1-0", approval=None),
            "issue object missing": lambda f: f.store.objects.pop(regmod.active_revision(f.registry(), "2026-11-09")["issueKey"]),
            "provenance missing": lambda f: (f.remote.remote / "published" / "2026-11-09" / "rev0.provenance.json").unlink(),
        }
        for name, breaks in cases.items():
            with self.subTest(name):
                f = prod_launched(self)
                breaks(f)
                out = self.hold(f)
                self.assertEqual(out.status, "held")
                self.assertIsNotNone(f.registry()["hold"])
                # Control: the operations that make a revision visible still refuse it.
                f.clock.t = at(W1, 11)
                with self.assertRaises(Refused):
                    f.run().resume()

    def test_presentation_guard_hold_branch_is_only_for_hold_only_writes(self):
        from dogear.publish import publisher as pubmod
        f = prod_launched(self)
        f.store.objects.pop(regmod.active_revision(f.registry(), "2026-11-09")["issueKey"])  # current issue gone
        f.store.objects.pop(regmod.active_revision(f.registry(), "2026-11-02")["issueKey"])
        f.clock.t = at(W1, 10)
        pub = f.run()
        reg, _ = pub._read_registry()
        txn = regmod.make_txn(f.clock(), "hold", ["t"])
        held = regmod.hold(reg, "x", f.clock().isoformat(), txn)
        rolled = regmod.rollback(reg, W0, None, "x", f.clock().isoformat(), txn)  # also sets a hold, moves current
        reads = len(f.store.calls)
        self.assertIsNone(pub._quiet_presentation_guard(reg, held)())  # hold-only: authorised, no issue read
        self.assertEqual(f.store.calls[reads:], [])
        self.assertEqual(pub._presentation_checked, regmod.sha256(regmod.dumps(held)))
        self.assertIn("issue object is missing", pub._quiet_presentation_guard(reg, rolled)())  # full check
        self.assertIsNone(pub._presentation_checked)
        # Mutation: give every write the hold branch, and the full check is gone.
        with mock.patch.object(pubmod, "_hold_only_guard", lambda before, after: None):
            self.assertIsNone(pub._quiet_presentation_guard(reg, rolled)())

    def test_promotion_first_then_the_hold_is_abandoned_cleanly(self):
        f = prod_launched(self)
        f.clock.t = at(W2, 6, 2)
        pub = f.run()
        promoted = []

        def fault(op, key, data):
            if op == "put" and key == REGISTRY_KEY and not promoted:  # the hold's write: a promotion lands first
                promoted.append(1)
                f.store.fault = None
                other = f.run()
                self.assertEqual(other.promote().status, "published")
            return None

        f.store.fault = fault
        with self.assertRaises(Aborted) as err:
            pub.hold("too late")
        self.assertIn("changed under us", str(err.exception))
        self.assertEqual(f.registry()["current"], "2026-11-16")
        self.assertIsNone(f.registry()["hold"])
        self.assertIsNone(remote_pending(f))
        self.assertEqual([h["status"] for h in lines(f, "hold")], ["aborted"])

    def test_hold_first_then_promotion_holds(self):
        f = prod_launched(self)
        self.hold(f)
        f.clock.t = at(W2, 7, 7)
        self.assertEqual(f.run().promote().status, "noop")
        out = f.run().promote(unattended=True)
        self.assertEqual((out.status, out.code), ("held", "HELD"))
        self.assertEqual(f.registry()["current"], "2026-11-09")


class HoldAuditTests(unittest.TestCase):
    """Revision 3/3.1: the only empty-target transaction is a proven hold, settled at
    once, never outstanding; its result is the hold whatever else is unserved."""

    def held(self, f: Fixture, hh: int = 10):
        f.clock.t = at(f.clock.t.date(), hh)
        return f.run().hold("audit")

    def kept_pending(self, f: Fixture, op: str):
        """A transaction that applied but could not be confirmed: pending kept, registry = intended."""
        reads = {"after_put": False}

        def fault(o, key, data):
            if key != REGISTRY_KEY:
                return None
            if o == "put":
                reads["after_put"] = True
                return "applied-then-fail"
            return "fail" if reads["after_put"] else None

        f.store.fault = fault
        with self.assertRaises(NeedsHuman):
            if op == "hold":
                self.held(f)
            else:
                f.clock.t = at(W2)
                f.run().promote()
        f.store.fault = None
        pending = PubData(f.remote.remote).read_pending()
        self.assertIsNotNone(pending)
        self.assertEqual(regmod.sha256(f.store.objects[REGISTRY_KEY].data), pending["intendedRegistrySha"])
        return pending

    def final_state(self, f: Fixture) -> dict:
        return {name: saved(f, name) for name in ("history.jsonl", "verified.jsonl", "publication.json",
                                                    "unverified.json", "pending.json")}

    # (a)
    def test_a_hold_commits_with_empty_targets_that_recompute_exactly(self):
        from dogear.publish.publisher import _expected_targets_problem, expected_verify
        f = prod_launched(self)
        before = f.registry()
        out = self.held(f)
        self.assertEqual(lines(f, "hold")[0]["verify"], [])
        self.assertEqual(expected_verify("hold", before, f.registry()), [])
        pending = {"txn": out.txn, "op": "hold", "verify": [], "expectedEtag": "x",
                   "expectedRegistry": regmod.dumps(before).decode(), "expectedRegistrySha": regmod.sha256(regmod.dumps(before)),
                   "intendedRegistry": f.registry(), "intendedRegistrySha": regmod.sha256(regmod.dumps(f.registry()))}
        self.assertIsNone(_expected_targets_problem(pending))

    # (b)
    def test_empty_targets_are_refused_for_every_other_operation(self):
        from dogear.publish.pubdata import targets_problem
        for op in ("launch", "promote", "correct", "rollback", "restore", "resume", None):
            with self.subTest(op=op):
                self.assertIsNotNone(targets_problem([], op))
        self.assertIsNone(targets_problem([], "hold"))
        # A non-hold pending recorded with [] cannot be finalized ...
        f = prod_launched(self)
        self.kept_pending(f, "promote")
        rewrite_remote_json(f, "pending.json", lambda p: p.update(verify=[]))
        raw = remote_pending(f)
        with self.assertRaises(NeedsHuman) as err:
            f.run().reconcile()
        self.assertIn("cannot be trusted", str(err.exception))
        self.assertEqual(remote_pending(f), raw)
        # ... and a committed non-hold history line with [] stops everything.
        g = prod_launched(self)
        rewrite_history(g, lines(g, "promote")[0]["txn"], verify=[])
        with self.assertRaises(CorruptState):
            g.run().status()
        # An unverified.json entry with [] is malformed, whatever it names.
        h = prod_launched(self)
        (h.remote.remote / "unverified.json").write_text(json.dumps(
            {"entries": [{"txn": lines(h, "promote")[0]["txn"], "since": "x", "targets": []}]}))
        with self.assertRaises(CorruptState):
            h.run().status()

    # (c)
    def test_a_hold_is_settled_at_once_and_never_outstanding_even_with_the_host_down(self):
        f = prod_launched(self)

        def down(path):
            raise OSError("host unreachable")

        f.served = down
        out = self.held(f)
        self.assertEqual(out.status, "held")
        self.assertIn(out.txn, [v["txn"] for v in PubData(f.remote.remote).verified()])
        self.assertIsNone(saved(f, "unverified.json"))

    # (d)
    def test_an_interrupted_hold_recovers_to_the_identical_state(self):
        reference = prod_launched(self)
        self.held(reference)
        want = self.final_state(reference)
        self.assertIsNone(want["unverified.json"])

        # Crash after the registry write, before it could be read back: recovered by a later reconcile.
        f = prod_launched(self)
        self.kept_pending(f, "hold")
        out = f.run().reconcile()
        self.assertIn("recorded", out.message)
        self.assertEqual(self.final_state(f), want)

        # Crash at each write inside _finalize, on the same working tree and from a fresh checkout.
        for step in ("write_registry_mirror", "append_verified_once", "clear_pending"):
            for resume_on in ("same checkout", "fresh checkout"):
                with self.subTest(step=step, resume_on=resume_on):
                    g = prod_launched(self)
                    g.clock.t = at(W1, 10)
                    pub = g.run()
                    with mock.patch.object(PubData, step, side_effect=RuntimeError("crash")):
                        with self.assertRaises(RuntimeError):
                            pub.hold("audit")
                    self.assertIsNotNone(PubData(g.remote.remote).read_pending())  # saved before the switch
                    (pub if resume_on == "same checkout" else g.run()).reconcile()
                    self.assertEqual(self.final_state(g), want)

    def test_stop_after_an_uncertain_hold_write_returns_the_hold_result(self):
        # Codex: every path that confirms a hold write, _stop's late confirmation included,
        # ends in the hold result. The first attempt applies but cannot be read back; the
        # guard then stops the retry, and _stop finds the registry is ours.
        f = prod_launched(self)
        f.clock.t = at(W1, 10)
        pub = f.run()
        reg, obj = pub._read_registry()
        txn = regmod.make_txn(f.clock(), "hold", ["t"])
        new_reg = regmod.hold(reg, "audit", f.clock().isoformat(), txn)
        calls, reads = [], {"left": 0}

        def guard():
            calls.append(1)
            return None if len(calls) == 1 else "stopped before the retry"

        def fault(o, key, data):
            if key == REGISTRY_KEY and o == "put":
                reads["left"] = 1
                return "applied-then-fail"
            if key == REGISTRY_KEY and o == "get" and reads["left"]:
                reads["left"] -= 1
                return "fail"
            return None

        f.store.fault = fault
        line = {"txn": txn, "op": "hold", "week": "2026-11-09", "at": f.clock().isoformat(), "code": "HELD",
                "reason": "audit"}
        out = pub._transact("hold", "2026-11-09", reg, obj, new_reg, [], guard, line, None)
        self.assertEqual(len(calls), 2)
        self.assertEqual((out.status, out.code, out.txn), ("held", "HELD", txn))
        self.assertIn(txn, [v["txn"] for v in PubData(f.remote.remote).verified()])
        self.assertIsNone(saved(f, "unverified.json"))

    # (e)
    def test_a_mutated_hold_or_audit_record_is_refused_and_never_falls_back(self):
        from dogear.publish import publisher as pubmod
        from dogear.publish.publisher import UnguardedRegistryWrite
        manifest_target = lambda f: {"kind": "manifest", "path": "/current.json",
                                     "sha256": regmod.sha256(regmod.manifest_bytes(f.registry()))}
        # A hold pending whose targets were changed away from [].
        for targets in ("manifest", "other"):
            with self.subTest(pending_targets=targets):
                f = prod_launched(self)
                self.kept_pending(f, "hold")
                value = [manifest_target(f)] if targets == "manifest" else [{"kind": "x"}]
                rewrite_remote_json(f, "pending.json", lambda p: p.update(verify=value))
                raw = remote_pending(f)
                with self.assertRaises(NeedsHuman):
                    f.run().reconcile()
                self.assertEqual(remote_pending(f), raw)
                self.assertEqual(lines(f, "hold"), [])
        # A promotion relabelled as a hold: not hold-only, so neither [] nor its full list is accepted.
        for targets in ([], "full"):
            with self.subTest(relabelled=targets):
                f = prod_launched(self)
                full = self.kept_pending(f, "promote")["verify"]
                rewrite_remote_json(f, "pending.json", lambda p: p.update(op="hold", verify=[] if targets == [] else full))
                raw = remote_pending(f)
                with self.assertRaises(NeedsHuman) as err:
                    f.run().reconcile()
                self.assertIn("not hold-only", str(err.exception))
                self.assertEqual(remote_pending(f), raw)
        # Before any pending is saved: an "op hold" that is not hold-only is a programming error.
        f = prod_launched(self)
        f.clock.t = at(W1, 10)
        pub = f.run()
        reg, obj = pub._read_registry()
        txn = regmod.make_txn(f.clock(), "hold", ["x"])
        not_hold_only = regmod.rollback(reg, W0, None, "x", f.clock().isoformat(), txn)
        puts = len([c for c in f.store.calls if c[0] == "put"])
        with self.assertRaises(UnguardedRegistryWrite):
            pub._transact("hold", "2026-11-09", reg, obj, not_hold_only, [], lambda: None,
                          {"txn": txn, "op": "hold", "week": "2026-11-09"}, None)
        self.assertEqual(len([c for c in f.store.calls if c[0] == "put"]), puts)
        self.assertIsNone(pub.pubdata.read_pending())
        self.assertEqual(f.registry()["current"], "2026-11-09")
        # Mutation: a hold proof that accepts anything lets that write through.
        g = prod_launched(self)
        g.clock.t = at(W1, 10)
        gp = g.run()
        reg, obj = gp._read_registry()
        bad = regmod.rollback(reg, W0, None, "x", g.clock().isoformat(), txn)
        with mock.patch.object(pubmod, "_hold_only_guard", lambda before, after: None):
            gp._transact("hold", "2026-11-09", reg, obj, bad, [], lambda: None,
                         {"txn": txn, "op": "hold", "week": "2026-11-09"}, None)
        self.assertEqual(g.registry()["current"], "2026-11-02")  # what the guard prevents

        # Committed audit records altered after the fact.
        audits = {
            "hold line with targets": lambda f, t: rewrite_history(f, t, verify=[manifest_target(f)]),
            "hold listed as outstanding": lambda f, t: (f.remote.remote / "unverified.json").write_text(json.dumps(
                {"entries": [{"txn": t, "since": "x", "targets": [manifest_target(f)]}]})),
            "hold missing from verified.jsonl": lambda f, t: (f.remote.remote / "verified.jsonl").write_text(
                "".join(l + "\n" for l in (f.remote.remote / "verified.jsonl").read_text().splitlines() if t not in l)),
        }
        reasons = {"hold line with targets": "a hold verifies nothing", "hold listed as outstanding":
                   "a hold is never outstanding", "hold missing from verified.jsonl": "neither outstanding nor settled"}
        for name, alter in audits.items():
            with self.subTest(audit=name):
                f = prod_launched(self)
                t = self.held(f).txn
                alter(f, t)
                with self.assertRaises(CorruptState) as err:
                    f.run().status()
                self.assertIn(reasons[name], str(err.exception))

    def test_the_hold_proof_refuses_every_other_change_for_its_own_reason(self):
        from dogear.publish.publisher import _hold_only_guard
        f = prod_launched(self)
        reg = f.registry()
        txn = regmod.make_txn(at(W1, 10), "hold", ["t"])
        held = regmod.hold(reg, "x", "now", txn)
        self.assertIsNone(_hold_only_guard(reg, held))

        def variant(change):
            out = copy.deepcopy(held)
            change(out)
            return out
        older = copy.deepcopy(reg["weeks"]["2026-11-09"]["revisions"][0])
        cases = {
            "it changes the current week or a revision": [
                variant(lambda r: r.update(current="2026-11-02")),
                variant(lambda r: r["weeks"]["2026-11-09"]["revisions"].append(dict(older, rev=1))),
                variant(lambda r: r["weeks"].pop("2026-11-02")),
                variant(lambda r: r["weeks"]["2026-11-02"]["revisions"][0].update(publishedAt="later"))],
            "it does not set a new hold of its own": [
                reg, variant(lambda r: r["hold"].update(txn=regmod.make_txn(at(W1, 11), "hold", ["u"])))],
            "is invalid": [variant(lambda r: r.update(schemaVersion=2)), variant(lambda r: r.update(extra=1))],
        }
        for reason, afters in cases.items():
            for after in afters:
                with self.subTest(reason=reason):
                    self.assertIn(reason, _hold_only_guard(reg, after) or "")
        self.assertIn("does not set a new hold", _hold_only_guard(held, variant(lambda r: None)))  # already held
        self.assertEqual(_hold_only_guard(None, held), "nothing is published")

    # (f) Codex's regression
    def test_an_automatic_hold_is_the_result_despite_earlier_unserved_output(self):
        f = prod_launched(self)
        # W2 published with its page unserved: committed, its targets outstanding (exit 8).
        f.served = unserved(f, "/2026-11-16/")
        f.clock.t = at(W2)
        with self.assertRaises(PublishedUnverified):
            f.run().promote()
        outstanding = saved(f, "unverified.json")
        self.assertIsNotNone(outstanding)
        before = f.registry()
        # W3: no Friday proof; the scheduled promote must hold, not stop at exit 8.
        f.clock.t = at(W3, 7, 7)
        out = f.run().promote(unattended=True)
        self.assertEqual((out.status, out.code, out.week), ("held", "AUTO_NO_PROOF", "2026-11-23"))
        self.assertEqual(len(lines(f, "hold")), 1)
        self.assertEqual(lines(f, "hold")[0]["verify"], [])
        self.assertEqual([v["txn"] for v in PubData(f.remote.remote).verified()].count(out.txn), 1)
        self.assertEqual(saved(f, "unverified.json"), outstanding)  # untouched, not settled
        after = f.registry()
        self.assertEqual((after["current"], after["weeks"]), (before["current"], before["weeks"]))
        self.assertEqual(regmod.manifest_bytes(after), regmod.manifest_bytes(before))
        with self.assertRaises(PublishedUnverified):  # later runs still retry the earlier target
            f.run().reconcile()
        f.served = None
        self.assertIn("served output verified", f.run().reconcile().message)
        self.assertIsNone(saved(f, "unverified.json"))
        # The same for an operator's hold.
        g = prod_launched(self)
        g.served = unserved(g, "/2026-11-16/")
        g.clock.t = at(W2)
        with self.assertRaises(PublishedUnverified):
            g.run().promote()
        outstanding = saved(g, "unverified.json")
        out = self.held(g)
        self.assertEqual((out.status, out.code), ("held", "HELD"))
        self.assertEqual(saved(g, "unverified.json"), outstanding)

    # (g)
    def test_a_valid_proof_never_publishes_over_earlier_unserved_output(self):
        f = prod_launched(self)
        f.served = unserved(f, "/2026-11-16/")
        f.clock.t = at(W2)
        with self.assertRaises(PublishedUnverified):
            f.run().promote()
        # The Friday proof for W3, staged as Friday's run would have (the host check aside).
        f.clock.t = at(W3 - FRIDAY, 11, 7)
        pub = f.run()
        reg, _ = pub._read_registry()
        pub._write_staged(pub._stage(rollover_week(at(W3)), pub._history(reg, W3), f.clock()), [])
        before, puts = f.store.objects[REGISTRY_KEY].data, len(f.store.calls)
        f.clock.t = at(W3, 7, 7)
        with self.assertRaises(PublishedUnverified):
            f.run().promote(unattended=True)
        self.assertEqual(f.store.objects[REGISTRY_KEY].data, before)
        self.assertFalse([c for c in f.store.calls[puts:] if c[0] == "put"])
        self.assertIsNone(remote_pending(f))


class UnattendedPromoteTests(unittest.TestCase):
    """D2: the scheduled promote publishes only the reviewed Friday proof."""

    def friday(self, f: Fixture, monday: dt.date, hh: int = 11):
        f.clock.t = at(monday - FRIDAY, hh, 7)
        out = f.run().stage_next(unattended=True)
        self.assertEqual(out.status, "staged", out)
        return out

    def monday(self, f: Fixture, monday: dt.date, hh: int = 7):
        f.clock.t = at(monday, hh, 7)
        return f.run().promote(unattended=True)

    def assert_held(self, f: Fixture, out, code: str, current: str = "2026-11-09"):
        self.assertEqual((out.status, out.code), ("held", code), out)
        self.assertEqual(f.registry()["current"], current)
        self.assertIsNotNone(f.registry()["hold"])
        self.assertTrue(any(code in n for n in out.notices))

    def test_a_valid_friday_proof_publishes_exactly_the_staged_bytes(self):
        f = prod_launched(self)
        self.friday(f, W2)
        staged = {n: saved(f, f"staged/2026-11-16/{n}") for n in ("issue.json", "web.html")}
        out = self.monday(f, W2)
        self.assertEqual(out.status, "published")
        rev = regmod.active_revision(f.registry(), "2026-11-16")
        self.assertEqual(f.store.objects[rev["issueKey"]].data, staged["issue.json"])
        self.assertEqual(f.store.objects[rev["webKey"]].data, staged["web.html"])

    def test_no_proof_holds_and_uploads_nothing(self):
        f = prod_launched(self)
        keys = set(f.store.objects)
        out = self.monday(f, W2)
        self.assert_held(f, out, "AUTO_NO_PROOF")
        self.assertEqual(set(f.store.objects), keys)
        self.assertIsNone(saved(f, "staged/2026-11-16/issue.json"))  # never staged inline

    def test_a_changed_input_holds(self):
        changes = {
            "approval withdrawn": lambda f: f.edit("w2-0", approval=None),
            "policy": lambda f: setattr(f, "policy", PublicationPolicy(slot5_bar=40, sparse_week="publish",
                                                                       empty_week="hold-previous")),
            "generator": lambda f: setattr(f, "generator", "dogear@other"),
        }
        for name, change in changes.items():
            with self.subTest(name):
                f = prod_launched(self)
                self.friday(f, W2)
                change(f)
                self.assert_held(f, self.monday(f, W2), "AUTO_PROOF_CHANGED")

    def test_a_malformed_or_wrongly_shaped_proof_is_a_changed_proof(self):
        """Codex's regression: loading the persisted proof is part of validating it. A
        malformed or wrongly shaped one is AUTO_PROOF_CHANGED: one audited hold, the
        previous issue current, nothing uploaded or published, exit 9; never a crash,
        never "absent", never a publication."""
        from dogear.publish.cli import _Report

        def prov_edit(change):
            def edit(d):
                prov = json.loads((d / "provenance.json").read_text())
                change(prov)
                (d / "provenance.json").write_text(json.dumps(prov))
            return edit
        cases = {
            "malformed provenance JSON": lambda d: (d / "provenance.json").write_text('{"week": "2026-11-16", '),
            "provenance is a list": lambda d: (d / "provenance.json").write_text("[]"),
            "provenance is a string": lambda d: (d / "provenance.json").write_text('"proof"'),
            "provenance is not UTF-8": lambda d: (d / "provenance.json").write_bytes(b"\xff\xfe{}"),
            "provenance without inputs": prov_edit(lambda p: p.pop("inputs")),
            "inputs is a list": prov_edit(lambda p: p.update(inputs=[])),
            "generatedAt missing": prov_edit(lambda p: p["inputs"].pop("generatedAt")),
            "records is an object": prov_edit(lambda p: p.update(records={})),
            "history is a number": prov_edit(lambda p: p["inputs"].update(history=7)),
            "validation is a list": lambda d: (d / "validation.json").write_text("[]"),
            "malformed validation JSON": lambda d: (d / "validation.json").write_text("{"),
            "a staged file missing": lambda d: (d / "issue.json").unlink(),
        }
        at_load = {"malformed provenance JSON": "unreadable", "provenance is a list": "wrong shape",
                   "provenance is a string": "wrong shape", "provenance without inputs": "wrong shape",
                   "inputs is a list": "wrong shape", "generatedAt missing": "wrong shape",
                   "records is an object": "wrong shape", "validation is a list": "wrong shape",
                   "malformed validation JSON": "unreadable", "a staged file missing": "incomplete"}
        for name, corrupt in cases.items():
            with self.subTest(name):
                f = prod_launched(self)
                self.friday(f, W2)
                corrupt(f.remote.remote / "staged" / "2026-11-16")
                before, keys = f.registry(), set(f.store.objects)
                out = self.monday(f, W2)
                self.assertEqual((out.status, out.code, out.week), ("held", "AUTO_PROOF_CHANGED", "2026-11-16"))
                if name in at_load:  # refused while loading, before any verification reads it
                    self.assertTrue(any(at_load[name] in n for n in out.notices), out.notices)
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(_Report(mock.Mock(public_log=False), None).success(out), 9)
                holds = lines(f, "hold")
                self.assertEqual([(h["status"], h["code"], h["verify"]) for h in holds],
                                 [("committed", "AUTO_PROOF_CHANGED", [])])
                after = f.registry()
                self.assertEqual((after["current"], after["weeks"]), (before["current"], before["weeks"]))
                self.assertEqual(set(f.store.objects), keys)  # nothing uploaded, nothing published
                self.assertNotIn("2026-11-16", after["weeks"])
                # The next catch-up reports the durable hold; it neither re-holds nor publishes.
                again = self.monday(f, W2, 8)
                self.assertEqual((again.status, again.code), ("held", "HELD"))
                self.assertEqual(len(lines(f, "hold")), 1)
        # Only a genuinely absent proof is AUTO_NO_PROOF.
        f = prod_launched(self)
        self.assert_held(f, self.monday(f, W2), "AUTO_NO_PROOF")

    def test_manual_promote_of_a_malformed_proof_is_unchanged(self):
        f = prod_launched(self)
        self.friday(f, W2)
        (f.remote.remote / "staged" / "2026-11-16" / "provenance.json").write_text("[]")
        f.clock.t = at(W2)
        with self.assertRaises(AttributeError):  # as at b22e4cc: the manual path is not changed here
            f.run().promote()

    def test_a_proof_from_outside_the_window_holds(self):
        f = prod_launched(self)
        f.clock.t = at(W2 - dt.timedelta(days=4), 11)  # Thursday: before the Friday window
        self.assertEqual(f.run().stage(W2).status, "staged")
        self.assert_held(f, self.monday(f, W2), "AUTO_PROOF_LATE")
        g = prod_launched(self)
        g.clock.t = at(W2, 6, 30)  # after the rollover
        self.assertEqual(g.run().stage(W2).status, "staged")
        self.assert_held(g, self.monday(g, W2), "AUTO_PROOF_LATE")
        h = prod_launched(self)  # the window's edges: Friday 00:00 is in it
        h.clock.t = at(W2 - FRIDAY, 0, 0)
        self.assertEqual(h.run().stage(W2).status, "staged")
        self.assertEqual(self.monday(h, W2).status, "published")

    def test_an_affinity_change_after_friday_publishes_the_proof_unchanged(self):
        f = prod_launched(self)
        self.friday(f, W2)
        staged_issue = saved(f, "staged/2026-11-16/issue.json")
        f.affinity.write_text(json.dumps({"preferred": [{"name": "w2-1"}, {"name": "w3-1"}], "adjacent": []}), encoding="utf-8")
        self.assertEqual(self.monday(f, W2).status, "published")
        rev = regmod.active_revision(f.registry(), "2026-11-16")
        self.assertEqual(f.store.objects[rev["issueKey"]].data, staged_issue)
        # The next staging cycle reads the new affinity.
        self.friday(f, W3)
        prov = json.loads(saved(f, "staged/2026-11-23/provenance.json"))
        self.assertEqual(prov["inputs"]["affinitySha256"], regmod.sha256(f.affinity.read_bytes()))

    def test_a_missing_proof_is_never_classified_by_generating_the_week(self):
        # A held (empty) week, and a normal one: with no Friday proof both are
        # AUTO_NO_PROOF, and Monday runs no selector, staging or link check to say why.
        for name, records in (("held week", week_records(W0, "w0") + week_records(W1, "w1") + week_records(W3, "w3")),
                              ("normal week", None)):
            with self.subTest(name):
                f = prod_launched(self, records)
                checked = len(f.links_checked)
                with mock.patch.object(Publisher, "_stage", autospec=True, side_effect=Publisher._stage) as staging:
                    self.assert_held(f, self.monday(f, W2), "AUTO_NO_PROOF")
                self.assertFalse(staging.called, "Monday generated the week")
                self.assertEqual(len(f.links_checked), checked)
                self.assertIsNone(saved(f, "staged/2026-11-16/issue.json"))
        # The held week's own signal is Friday's run.
        f = prod_launched(self, week_records(W0, "w0") + week_records(W1, "w1") + week_records(W3, "w3"))
        f.clock.t = at(W2 - FRIDAY, 11, 7)
        self.assertEqual(f.run().stage_next(unattended=True).code, "STAGE_WEEK_HELD")

    def test_manual_promote_is_unchanged(self):
        f = prod_launched(self)
        f.clock.t = at(W2)
        out = f.run().promote()  # nothing staged: staged at promotion, as before
        self.assertEqual(out.status, "published")
        self.assertTrue(any("staged at promotion" in n for n in out.notices))

    def test_recovery_is_resume_then_a_manual_promote(self):
        f = prod_launched(self)
        self.assert_held(f, self.monday(f, W2), "AUTO_NO_PROOF")
        f.clock.t = at(W2, 9)
        self.assertEqual(f.run().resume().status, "published")
        # A daily catch-up between the two re-holds (still no proof): harmless.
        self.assert_held(f, self.monday(f, W2, 8), "AUTO_NO_PROOF")
        f.clock.t = at(W2, 9, 30)
        self.assertEqual(f.run().resume().status, "published")
        self.assertEqual(f.run().promote().status, "published")
        self.assertEqual(f.registry()["current"], "2026-11-16")
        self.assertIsNone(f.registry()["hold"])


class PromoteSlotTests(unittest.TestCase):
    """The Monday promote slots (dogear-promote.yml: 03:37, 05:07 and 07:07 PHT, plus the
    08:07 catch-up) under GitHub's late, early-slot, duplicated and out-of-order starts.
    The rollover guard reads the clock when the run executes: nothing before Monday 06:00
    Manila publishes; the first run after it publishes exactly the saved Friday proof; every
    later run is a no-op that writes nothing."""

    def setUp(self):
        self.f = prod_launched(self)  # W1 (2026-11-09) is current
        self.f.clock.t = at(W2 - FRIDAY, 11, 7)
        self.assertEqual(self.f.run().stage_next(unattended=True).status, "staged")
        self.staged = saved(self.f, "staged/2026-11-16/issue.json")

    def promote_at(self, t: dt.datetime):
        self.f.clock.t = t
        return self.f.run().promote(unattended=True)

    def snapshot(self):
        return ({k: o.data for k, o in self.f.store.objects.items()}, list(self.f.remote.saves),
                self.f.served_manifest())

    def test_the_pre_rollover_slots_are_no_ops_that_write_nothing(self):
        before = self.snapshot()
        for t in (at(W2, 3, 37), at(W2, 5, 7), dt.datetime(2026, 11, 16, 5, 59, 59, tzinfo=MANILA)):
            with self.subTest(t.isoformat()):
                out = self.promote_at(t)
                self.assertEqual((out.status, out.message), ("noop", "before the Monday 06:00 rollover"))
                self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.f.served_manifest()["issueId"], "dogear-2026-11-09")

    def test_the_first_run_after_the_rollover_publishes_the_proof_and_every_later_run_is_a_no_op(self):
        out = self.promote_at(dt.datetime(2026, 11, 16, 6, 0, 0, tzinfo=MANILA))
        self.assertEqual(out.status, "published", out)
        rev = regmod.active_revision(self.f.registry(), "2026-11-16")
        self.assertEqual(self.f.store.objects[rev["issueKey"]].data, self.staged)  # exactly the saved proof
        self.assertEqual(self.f.served_manifest()["issueId"], "dogear-2026-11-16")
        after = self.snapshot()
        # The 07:07 slot, the catch-up, a duplicate of it, a late start, Tuesday's catch-up.
        for t in (at(W2, 7, 7), at(W2, 8, 7), at(W2, 8, 7), at(W2, 12, 36), at(W2 + dt.timedelta(days=1), 8, 7)):
            with self.subTest(t.isoformat()):
                out = self.promote_at(t)
                self.assertEqual(out.status, "noop", out)
                self.assertIn("2026-11-16 is already published (revision 0)", out.message)
                self.assertEqual(self.snapshot(), after)

    def test_an_early_slot_started_late_publishes_on_its_execution_time(self):
        # The 03:37 slot, started by GitHub at 08:24 (as the 22-26 Oct delays would): it publishes.
        out = self.promote_at(at(W2, 8, 24))
        self.assertEqual(out.status, "published", out)
        rev = regmod.active_revision(self.f.registry(), "2026-11-16")
        self.assertEqual(self.f.store.objects[rev["issueKey"]].data, self.staged)

    def test_sunday_night_runs_touch_only_the_already_published_week(self):
        before = self.snapshot()
        out = self.promote_at(at(W2 - dt.timedelta(days=1), 23, 59))  # Sun 15 Nov, 23:59 Manila
        self.assertEqual(out.status, "noop", out)
        self.assertIn("2026-11-09 is already published", out.message)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(saved(self.f, "staged/2026-11-16/issue.json"), self.staged)  # the proof untouched

    def test_a_hold_is_quiet_before_the_rollover_and_holds_after_it(self):
        self.f.clock.t = at(W2 - dt.timedelta(days=1), 20, 0)
        self.assertEqual(self.f.run().hold("owner review").status, "held")
        held = self.snapshot()
        out = self.promote_at(at(W2, 3, 37))
        self.assertEqual((out.status, out.message), ("noop", "before the Monday 06:00 rollover"))
        self.assertEqual(self.snapshot(), held)
        out = self.promote_at(at(W2, 7, 7))
        self.assertEqual((out.status, out.code), ("held", "HELD"), out)
        self.assertEqual(self.f.served_manifest()["issueId"], "dogear-2026-11-09")  # nothing published


class UnattendedStageTests(unittest.TestCase):
    """The Friday run succeeds only with an actionable proof (correction 2)."""

    def friday(self, f: Fixture, unattended: bool = True):
        f.clock.t = at(W2 - FRIDAY, 11, 7)
        return f.run().stage_next(unattended=unattended)

    def test_the_nominal_case_saves_and_verifies_the_exact_bytes(self):
        f = prod_launched(self)
        out = self.friday(f)
        self.assertEqual(out.status, "staged")
        self.assertIsNone(out.code)
        files = f.run().pubdata.read_saved_staged(W2)
        self.assertEqual(files, PubData(f.remote.remote).read_staged(W2))

    def test_a_held_week_is_not_a_proof(self):
        records = week_records(W0, "w0") + week_records(W1, "w1") + week_records(W3, "w3")
        out = self.friday(prod_launched(self, records))
        self.assertEqual((out.status, out.code, out.week), ("held", "STAGE_WEEK_HELD", "2026-11-16"))

    def test_a_wrong_week_is_not_a_proof(self):
        f = prod_launched(self)
        real = Publisher._stage
        with mock.patch.object(Publisher, "_stage", lambda pub, week, history, now:
                               real(pub, rollover_week(at(W3)), pub._history(f.registry(), W3), now)):
            out = self.friday(f)
        self.assertEqual(out.code, "STAGE_WRONG_WEEK")

    def test_an_unsaved_proof_is_never_success(self):
        f = prod_launched(self)
        f.remote.fail = 1  # the stage save fails (tolerated as a notice before)
        out = self.friday(f)
        self.assertEqual((out.status, out.code), ("held", "STAGE_NOT_SAVED"))
        g = prod_launched(self)
        g.remote.fail = 1
        self.assertEqual(self.friday(g, unattended=False).status, "staged")  # manual: unchanged
        h = prod_launched(self)  # nowhere to read the saved state from: never "saved"
        h.clock.t = at(W2 - FRIDAY, 11, 7)
        pub = h.run()
        pub.pubdata.saved_reader = None
        self.assertEqual(pub.stage_next(unattended=True).code, "STAGE_NOT_SAVED")

    def test_a_saved_proof_that_does_not_verify_is_not_a_proof(self):
        f = prod_launched(self)
        with mock.patch("dogear.publish.publisher.verify_staged", return_value=["regenerates differently"]):
            out = self.friday(f)
        self.assertEqual((out.status, out.code), ("held", "STAGE_INVALID"))
        self.assertIn("regenerates differently", " ".join(out.notices))


if __name__ == "__main__":
    unittest.main()
