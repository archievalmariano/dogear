"""The publisher (dogear.publish): registry, eligibility, transactions, recovery.

Synthetic datasets, simulated storage with injected faults, a fake clock, and
a simulated publication-data remote: a "next run" starts from a fresh checkout
of what was last saved, as a CI job would.
"""

from __future__ import annotations

import copy
import datetime as dt
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

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

    def checkout(self) -> PubData:
        self.n += 1
        work = self.base / f"work{self.n}"
        shutil.copytree(self.remote, work)
        return PubData(work, self.committer(work))


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
        self.pub = Publisher(self.store, self.remote.checkout(), StageInputs(self.dataset, self.tmp / "none.json",
                                                                             BASE, "dogear@test"),
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

    def test_the_committed_policy_file_leaves_empty_week_undecided(self):
        """slot5Bar 42 and sparseWeek publish are decided (owner, 8 October 2026); emptyWeek
        stays unset until quiet-week firmware ships, so production still refuses."""
        policy = load_policy(Path(__file__).resolve().parents[1] / "data" / "publication-policy.json")
        self.assertEqual((policy.slot5_bar, policy.sparse_week), (42, "publish"))
        self.assertEqual(policy.unset(), ["emptyWeek"])

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


if __name__ == "__main__":
    unittest.main()
