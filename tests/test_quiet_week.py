"""The quiet-week issue (PUBLISHING.md §21; QUIET-WEEK-PLAN v2, Codex PLAN CLEAR).

Selector, history reconstruction (validated, with stable identities), the
date/edition invariant at every write and visibility change, corrections,
rollback/restore/resume, legacy lines and attestation, schema v2/v3, web output,
the production gate, the staging-only workflow, and an end-to-end CLI drill
through the staging fixture's November gap."""

from __future__ import annotations

import copy
import datetime as dt
import json
import os
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from dogear.dataset import DatasetError, fingerprint, parse_dataset
from dogear.publish import quiet_history
from dogear.publish import registry as regmod
from dogear.publish.edition import revision_problems
from dogear.publish.policy import EMPTY_CHOICES, PublicationPolicy, TEST_POLICY, load_policy
from dogear.publish.publisher import NotReady, Publisher
from dogear.publish.pubdata import CorruptState, PubData
from dogear.publish.registry import REGISTRY_KEY, Refused
from dogear.publish.staging import (Checks, StageInputs, StageRefused, WeekHeld, device_limit_problems, generate,
                                    provenance_bytes, stage)
from dogear.publish.store import Obj
from dogear.quiet import (QUIET_HEADING, QUIET_MAX, QUIET_MIN, QUIET_NOTE, QUIET_POOL_DAYS, QUIET_SUBJECT_DAYS,
                          QUIET_UPCOMING_DAYS, QuietSource, anniversary_imminent, next_occurrence)
from dogear.select import select_issue, select_quiet_issue
from dogear.week import MANILA, week_of, week_starting

try:
    from tests.test_dogear import rec
    from tests.test_publish import W0, W1, W2, W3, Fixture, at
except ImportError:
    from test_dogear import rec
    from test_publish import W0, W1, W2, W3, Fixture, at

ROOT = Path(__file__).resolve().parents[1]
QUIET = PublicationPolicy(slot5_bar=42, sparse_week="publish", empty_week="quiet-week")
KINDS = ("publication", "literary-event", "birth", "death")


def strong(rid: str, day: dt.date, n: int = 0, sig: int = 5, **kw) -> dict:
    """An approved record scoring 52 (significance 5, core), type rotating by ``n``."""
    kind = KINDS[n % 4]
    base = dict(significance=sig, type=kind, year=1960 + (n % 9),
                links=[{"type": "read-more", "url": f"https://library.test/{rid}", "title": "T", "provider": "P"}])
    if kind in ("publication", "literary-event"):
        base.update(person=f"Author {rid}", work=f"Work {rid}")
    else:
        base.update(person=f"Person {rid}", work=None)
    base.update(kw)
    return rec(rid, day.month, day.day, **base)


def regular_week(monday: dt.date, tag: str) -> list:
    """Two days of three strong records: the issue takes two a day (the per-day limit),
    so the third of each day (``-2``, a birth or death) lands in "Also this week"."""
    out = []
    for d in (0, 2):
        day = monday + dt.timedelta(days=d)
        for i in range(3):
            n = i if i < 2 else 2 + (d // 2)
            out.append(strong(f"{tag}-d{d}-{i}", day, n))
    return out


def also_ids(tag: str) -> list:
    return [f"{tag}-d0-2", f"{tag}-d2-2"]


def published(test, weeks: list, policy=QUIET) -> Fixture:
    """Launch W0, then promote each following Monday; ``weeks`` holds W0.. records."""
    f = Fixture(test, sum(weeks, []), at(W0, 9), policy=policy)
    test.assertEqual(f.pub.launch(first_publication=True).status, "published")
    return f


def promote(f: Fixture, monday: dt.date):
    f.clock.t = at(monday, 6, 2)
    return f.run().promote()


def line_for(f: Fixture, week: dt.date, rev: int = 0) -> dict:
    return next(h for h in f.history() if h.get("week") == week.isoformat() and h.get("rev") == rev
                and h["status"] == "committed")


def stored_issue(f: Fixture, week: dt.date, rev: int = None) -> dict:
    reg = f.registry()
    entry = reg["weeks"][week.isoformat()]
    r = entry["revisions"][entry["active"] if rev is None else rev]
    return json.loads(f.store.objects[r["issueKey"]].data)


def rewrite_line(f: Fixture, txn: str, **changes) -> None:
    path = f.remote.remote / "history.jsonl"
    out = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        h = json.loads(raw)
        if h.get("txn") == txn:
            for k, v in changes.items():
                if v is None:
                    h.pop(k, None)
                else:
                    h[k] = v
        out.append(json.dumps(h, sort_keys=True, ensure_ascii=False))
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def make_legacy(f: Fixture, week: dt.date, rev: int = 0) -> str:
    """Rewrite one committed revision into the pre-identity format, consistently: its
    history line and provenance lose `subjects` and `edition`; the registry and its
    mirror pin the rewritten provenance. What lines written before §21 look like."""
    line = line_for(f, week, rev)
    prov_path = f.remote.remote / "published" / week.isoformat() / f"rev{rev}.provenance.json"
    prov = json.loads(prov_path.read_bytes())
    prov.pop("subjects"), prov.pop("edition")
    data = provenance_bytes(prov)
    prov_path.write_bytes(data)
    reg = f.registry()
    reg["weeks"][week.isoformat()]["revisions"][rev]["provenanceSha256"] = regmod.sha256(data)
    reg_bytes = regmod.dumps(reg)
    f.store.objects[REGISTRY_KEY] = Obj(reg_bytes, '"legacy"')
    (f.remote.remote / "publication.json").write_bytes(reg_bytes)
    rewrite_line(f, line["txn"], subjects=None, edition=None)
    return line["txn"]


class ConstantsAndPolicyTests(unittest.TestCase):
    def test_the_owners_values_are_pinned(self):
        self.assertEqual((QUIET_MIN, QUIET_MAX, QUIET_POOL_DAYS, QUIET_UPCOMING_DAYS, QUIET_SUBJECT_DAYS),
                         (3, 5, 182, 35, 365))
        self.assertEqual(QUIET_HEADING, "SOME QUIET THIS WEEK")
        self.assertEqual(QUIET_NOTE, "Not much on our calendar, but still a few things worth a dogear for you.")
        self.assertEqual(EMPTY_CHOICES, ("hold-previous", "quiet-week"))

    def test_production_policy_keeps_quiet_week_off(self):
        policy = load_policy(ROOT / "data" / "publication-policy.json")
        self.assertEqual((policy.slot5_bar, policy.sparse_week, policy.empty_week), (42, "publish", None))
        self.assertEqual(policy.unset(), ["emptyWeek"])  # production still refuses until the owner's switch
        self.assertEqual(load_policy(ROOT / "fixtures" / "staging-policy.json").empty_week, "quiet-week")


class AnniversaryWindowTests(unittest.TestCase):
    """[W, W + 35 days): day 34 excluded, day 35 eligible, 29 February the next leap day."""

    def test_boundaries(self):
        w = dt.date(2026, 11, 16)
        for days, imminent in ((0, True), (6, True), (34, True), (35, False), (60, False)):
            d = w + dt.timedelta(days=days)
            self.assertEqual(anniversary_imminent(d.month, d.day, w), imminent, days)
        before = w - dt.timedelta(days=1)  # yesterday: next occurrence a year away
        self.assertFalse(anniversary_imminent(before.month, before.day, w))

    def test_leap_day(self):
        self.assertEqual(next_occurrence(2, 29, dt.date(2027, 2, 1)), dt.date(2028, 2, 29))
        self.assertFalse(anniversary_imminent(2, 29, dt.date(2027, 2, 1)))
        self.assertTrue(anniversary_imminent(2, 29, dt.date(2028, 2, 7)))   # 22 days
        self.assertFalse(anniversary_imminent(2, 29, dt.date(2028, 1, 24)))  # 36 days
        self.assertTrue(anniversary_imminent(2, 29, dt.date(2028, 1, 26)))   # 34 days

    def test_imminent_records_are_dropped_by_the_selector(self):
        w = dt.date(2026, 11, 16)
        recorded = dt.date(2026, 10, 12)
        soon = w + dt.timedelta(days=20)  # a record whose own day is 20 days ahead...
        r = strong("soon-1", recorded, 0)
        r = rec("soon-1", soon.month, soon.day, significance=5, type="publication", person="A", work="B",
                year=1960, links=r["links"])
        ds = parse_dataset(json.dumps({"schemaVersion": 3, "records": [r]}))
        # ...even if (impossibly, for a 26-week pool) it were listed against a past week.
        src = QuietSource(pool=(("soon-1", week_of(soon - dt.timedelta(days=364)).start.isoformat()),))
        sel = select_quiet_issue(ds, week_starting(w), None, src, slot_bars=QUIET.slot_bars())
        self.assertEqual(sel.picked, [])


def pool_dataset(n: int, sig: int = 5, start: dt.date = dt.date(2026, 10, 5)) -> tuple:
    """n strong records on distinct earlier days, each listed in its week's "Also this week"."""
    records, pool = [], []
    for i in range(n):
        day = start + dt.timedelta(days=i * 3)
        records.append(strong(f"pool-{i}", day, i, sig=sig))
        pool.append((f"pool-{i}", week_starting(day - dt.timedelta(days=day.weekday())).start.isoformat()))
    return parse_dataset(json.dumps({"schemaVersion": 3, "records": records})), QuietSource(pool=tuple(pool))


class QuietSelectorTests(unittest.TestCase):
    W = week_starting(dt.date(2026, 11, 16))

    def quiet(self, ds, src):
        return select_quiet_issue(ds, self.W, None, src, slot_bars=QUIET.slot_bars())

    def test_three_to_five_never_more(self):
        for n, want in ((3, 3), (4, 4), (5, 5), (6, 5), (9, 5)):
            sel = self.quiet(*pool_dataset(n))
            self.assertEqual(len(sel.picked), want, n)
            self.assertEqual(sel.runners_up, [])  # a quiet issue has no Also this week
            self.assertEqual(sel.edition, "quiet")

    def test_same_bars_never_lowered(self):
        ds, src = pool_dataset(6, sig=2)  # 22 points: below the 30 every slot needs
        self.assertEqual(self.quiet(ds, src).picked, [])
        ds, src = pool_dataset(6, sig=4)  # 42, then composition: the 5th needs 42 like a regular 5th
        sel = self.quiet(ds, src)
        self.assertTrue(all(c.score + c.composition >= 30 for c in sel.picked))
        if len(sel.picked) == 5:
            self.assertGreaterEqual(sel.picked and min(c.score + c.composition for c in sel.picked if c.pick_order == 5), 42)

    def test_each_item_keeps_its_original_date(self):
        ds, src = pool_dataset(5)
        sel = self.quiet(ds, src)
        for c in sel.picked:
            self.assertLess(c.date, self.W.start)
            self.assertEqual((c.date.month, c.date.day), (c.record.month, c.record.day))

    def test_exclusions(self):
        ds, src = pool_dataset(5)
        ids = lambda s: sorted(c.record.id for c in s.picked)  # noqa: E731
        self.assertNotIn("pool-0", ids(self.quiet(ds, QuietSource(pool=src.pool, excluded=("pool-0",)))))
        person = ds.by_id("pool-1").person_id
        self.assertNotIn("pool-1", ids(self.quiet(ds, QuietSource(pool=src.pool, subject_identities=(person,)))))
        # a different record about a subject this week's earlier quiet revision picked
        self.assertNotIn("pool-1", ids(self.quiet(ds, QuietSource(pool=src.pool, same_week_identities=(person,)))))
        # ...but the earlier quiet pick itself may be kept
        self.assertIn("pool-1", ids(self.quiet(ds, QuietSource(pool=src.pool, retainable=("pool-1",),
                                                               same_week_identities=(person,)))))

    def test_a_quiet_issue_has_exactly_one_lead_first(self):
        """Every pool record is significance 5 on a centenary: a regular week would make
        a second lead (MAX_FEATURED = 2); a quiet issue never does (owner, Option B)."""
        records, pool = [], []
        for i in range(5):
            day = dt.date(2026, 10, 5) + dt.timedelta(days=i * 3)
            records.append(strong(f"cent-{i}", day, i, sig=5, year=1926))
            pool.append((f"cent-{i}", week_of(day).start.isoformat()))
        ds = parse_dataset(json.dumps({"schemaVersion": 3, "records": records}))
        sel = self.quiet(ds, QuietSource(pool=tuple(pool)))
        self.assertGreaterEqual(len(sel.picked), 3)
        self.assertEqual([c.role for c in sel.picked], ["featured"] + ["standard"] * (len(sel.picked) - 1))
        from dogear.select import MAX_FEATURED, _assign_roles
        regular = copy.deepcopy(sel.picked)
        _assign_roles(regular, MAX_FEATURED)
        self.assertEqual(sum(c.role == "featured" for c in regular), 2)  # the rule is what holds it to one

    def test_a_record_without_stable_identity_is_dropped(self):
        ds, src = pool_dataset(4)
        raw = json.loads(json.dumps({"schemaVersion": 3, "records": []}))
        for r in ds.records:
            raw["records"].append({"id": r.id})
        records = [strong(f"pool-{i}", dt.date(2026, 10, 5) + dt.timedelta(days=i * 3), i) for i in range(4)]
        records[0].pop("personId")
        records[0]["approval"] = None  # an unapproved record may lack ids; it is not eligible anyway
        ds2 = parse_dataset(json.dumps({"schemaVersion": 3, "records": records}))
        self.assertNotIn("pool-0", [c.record.id for c in self.quiet(ds2, src).picked])


class BuildSourceTests(unittest.TestCase):
    """The §3.3 sets from (already validated) lines: windows, corrections, legacy."""
    W = dt.date(2026, 11, 16)

    def line(self, weeks_before: int, txn: str, edition="regular", ids=(), also=(), subjects=True, rev=0):
        week = (self.W - dt.timedelta(weeks=weeks_before)).isoformat()
        out = {"txn": txn, "week": week, "rev": rev, "edition": edition, "recordIds": list(ids), "alsoIds": list(also)}
        if subjects:
            out["subjects"] = [{"recordId": i, "personId": f"dogear:p-{i}", "workId": None} for i in ids]
        return out

    def test_pool_window_is_26_weeks(self):
        src = quiet_history.build_source([self.line(26, "t1", also=["in"]), self.line(27, "t2", also=["out"])],
                                         {}, self.W)
        self.assertEqual([p[0] for p in src.pool], ["in"])

    def test_subject_window_is_365_days(self):
        src = quiet_history.build_source([self.line(52, "t1", ids=["near"]), self.line(53, "t2", ids=["far"])],
                                         {}, self.W)
        self.assertEqual(src.subject_identities, ("dogear:p-near",))
        self.assertEqual(set(src.excluded), {"near", "far"})  # digest exclusion has no window

    def test_target_week_rules(self):
        lines = [self.line(0, "reg", "regular", ids=["r1"]),           # W's own earlier regular revision
                 self.line(0, "qui", "quiet", ids=["q1"], rev=1),      # W's own earlier quiet revision
                 self.line(-1, "later", "regular", ids=["l1"]),        # a later week (correcting an older W)
                 self.line(3, "other-quiet", "quiet", ids=["o1"])]
        src = quiet_history.build_source(lines, {}, self.W)
        self.assertEqual(set(src.excluded), {"r1", "l1", "o1"})  # regular digests of W too
        self.assertEqual(src.retainable, ("q1",))
        self.assertEqual(set(src.subject_identities), {"dogear:p-r1", "dogear:p-l1", "dogear:p-o1"})
        self.assertEqual(src.same_week_identities, ("dogear:p-q1",))

    def test_a_quiet_pick_later_published_in_full_is_not_retainable(self):
        lines = [self.line(0, "qui", "quiet", ids=["x"]), self.line(-2, "later", "regular", ids=["x"])]
        src = quiet_history.build_source(lines, {}, self.W)
        self.assertEqual((src.retainable, "x" in src.excluded), ((), True))

    def test_legacy_line_in_window_makes_the_source_incomplete(self):
        legacy = self.line(4, "legacy", ids=["a"], subjects=False)
        self.assertFalse(quiet_history.build_source([legacy], {}, self.W).complete)
        old = self.line(60, "old", ids=["a"], subjects=False)  # outside the window: no effect
        self.assertTrue(quiet_history.build_source([old], {}, self.W).complete)
        attested = {"legacy": [{"recordId": "a", "personId": "dogear:p-a", "workId": None}]}
        src = quiet_history.build_source([legacy], attested, self.W)
        self.assertTrue(src.complete)
        self.assertEqual(src.subject_identities, ("dogear:p-a",))


class PublishedQuietTests(unittest.TestCase):
    def quiet_published(self):
        f = published(self, [regular_week(W0, "a"), regular_week(W1, "b")])
        self.assertEqual(promote(f, W1).status, "published")
        out = promote(f, W2)  # W2 has no records at all
        self.assertEqual(out.status, "published", out.message)
        return f

    def test_empty_week_becomes_a_quiet_issue(self):
        f = self.quiet_published()
        issue = stored_issue(f, W2)
        self.assertEqual(issue["schemaVersion"], 3)
        self.assertEqual(issue["quiet"], {"heading": QUIET_HEADING, "note": QUIET_NOTE})
        self.assertEqual((issue["rangeStart"], issue["issueId"]), (W2.isoformat(), f"dogear-{W2.isoformat()}"))
        ids = [e["id"] for e in issue["entries"]]
        self.assertTrue(QUIET_MIN <= len(ids) <= QUIET_MAX)
        self.assertEqual(set(ids), set(also_ids("a") + also_ids("b")))
        for e in issue["entries"]:
            self.assertLess(e["date"], W2.isoformat())  # each keeps its own earlier date
        line = line_for(f, W2)
        self.assertEqual((line["edition"], line["alsoIds"]), ("quiet", []))
        self.assertEqual([s["recordId"] for s in line["subjects"]], line["recordIds"])
        prov = json.loads((f.remote.remote / "published" / W2.isoformat() / "rev0.provenance.json").read_bytes())
        self.assertEqual(prov["edition"], "quiet")
        self.assertEqual([p[0] for p in prov["inputs"]["quietSource"]["pool"]],
                         sorted(also_ids("a") + also_ids("b")))
        self.assertEqual(f.served_manifest()["week"]["start"], W2.isoformat())

    def test_quiet_picks_are_never_reused_and_a_thin_pool_holds(self):
        f = self.quiet_published()
        before = f.registry()
        out = promote(f, W3)  # nothing left: every pool item went into W2
        self.assertEqual(out.status, "held")
        self.assertIn("fewer than 3", out.message)
        self.assertEqual(f.registry(), before)  # the previous issue stays current

    def test_sparse_week_is_never_padded(self):
        f = published(self, [regular_week(W0, "a"), [strong("lone-1", W1 + dt.timedelta(days=1), 0)]])
        f.clock.t = at(W1, 6, 2)
        pub = f.run()
        pub._quiet_source = mock.Mock(side_effect=AssertionError("the quiet pass must not run"))
        self.assertEqual(pub.promote().status, "published")
        issue = stored_issue(f, W1)
        self.assertEqual((issue["schemaVersion"], [e["id"] for e in issue["entries"]]), (2, ["lone-1"]))
        self.assertNotIn("quiet", issue)

    def test_hold_previous_policy_is_unchanged(self):
        f = published(self, [regular_week(W0, "a")], policy=TEST_POLICY)
        out = promote(f, W1)
        self.assertEqual(out.status, "held")
        self.assertIn("emptyWeek policy: hold-previous", out.message)

    def test_regular_digest_is_excluded_even_when_corrected_away(self):
        """W0 rev0 shows X in full; a correction pushes X down into Also this week. X
        is then a pool member but permanently excluded: it was published in full."""
        week0 = regular_week(W0, "a")
        f = published(self, [week0])
        x = "a-d0-1"
        self.assertIn(x, line_for(f, W0)["recordIds"])
        stronger = strong("a-d0-new", W0, 0, sig=5, year=1959, recognition="anchor")  # outranks the day's picks
        f.write(f.records + [stronger])
        self.assertEqual(f.run().correct(W0, 0, "a stronger item").status, "published")
        rev1 = line_for(f, W0, 1)
        self.assertIn(x, rev1["alsoIds"])
        self.assertNotIn(x, rev1["recordIds"])
        out = promote(f, W1)  # W1 empty
        if out.status == "published":
            self.assertNotIn(x, [e["id"] for e in stored_issue(f, W1)["entries"]])
        src = f.run()._quiet_source(W1)
        self.assertIn(x, src.excluded)

    def test_regular_to_quiet_correction_keeps_earlier_digest_and_subject_excluded(self):
        """W2 first publishes in full (c-1 about a shared person); its records are then
        withdrawn and a correction makes W2 quiet. c-1 stays excluded, and an Also item
        about the same person (a-d0-2) is blocked by W2's own earlier regular revision."""
        shared = "dogear:shared-person"
        week0 = regular_week(W0, "a")
        week0[2] = dict(week0[2], personId=shared)
        week0[2]["approval"] = dict(week0[2]["approval"], fingerprint=fingerprint(dict(week0[2], approval=None)))
        week2 = [strong("c-1", W2, 0, personId=shared), strong("c-2", W2 + dt.timedelta(days=1), 1)]
        f = published(self, [week0, regular_week(W1, "b"), week2])
        promote(f, W1)
        self.assertEqual(promote(f, W2).status, "published")
        self.assertIn("c-1", line_for(f, W2)["recordIds"])
        for rid in ("c-1", "c-2"):  # edited, so no longer approved at their current content
            f.edit(rid, headline=f"Edited {rid}")
        self.assertEqual(f.run().correct(W2, 0, "W2's records withdrawn").status, "published")
        rev1 = line_for(f, W2, 1)
        self.assertEqual(rev1["edition"], "quiet")
        self.assertNotIn("c-1", rev1["recordIds"])
        self.assertNotIn("a-d0-2", rev1["recordIds"])  # its subject was featured in W2's regular revision
        self.assertEqual(sorted(rev1["recordIds"]), sorted(["a-d2-2"] + also_ids("b")))
        src = f.run()._quiet_source(W2)
        self.assertIn("c-1", src.excluded)
        self.assertIn(shared, src.subject_identities)

    def test_quiet_to_quiet_correction_retains_own_picks_and_blocks_same_subject(self):
        week0 = regular_week(W0, "a")
        week1 = regular_week(W1, "b")
        f = published(self, [week0, week1])
        promote(f, W1)
        self.assertEqual(promote(f, W2).status, "published")
        rev0 = line_for(f, W2)
        kept = rev0["recordIds"]
        # Withdraw one pick; the correction must stay quiet and may keep the others.
        f.edit(kept[0], headline="Edited")
        out = f.run().correct(W2, 0, "one quiet pick withdrawn")
        if out.status == "published":
            rev1 = line_for(f, W2, 1)
            self.assertEqual(rev1["edition"], "quiet")
            self.assertTrue(set(rev1["recordIds"]) <= set(kept[1:]))
            self.assertTrue(set(kept[1:]) & set(rev1["recordIds"]))
        else:
            self.assertEqual(out.status, "held")
        src = f.run()._quiet_source(W2)
        self.assertEqual(set(src.retainable), set(kept) | set(line_for(f, W2, 1)["recordIds"])
                         if out.status == "published" else set(kept))

    def test_rollback_and_restore_of_a_quiet_week_pass_the_guard(self):
        f = self.quiet_published()
        pub = f.run()
        self.assertEqual(pub.rollback(W1, None, "check").status, "published")
        self.assertEqual(f.run().restore(W2, 0).status, "published")
        self.assertEqual(f.registry()["current"], W2.isoformat())

    def test_a_tampered_quiet_revision_is_refused_on_restore_and_resume(self):
        f = self.quiet_published()
        f.run().rollback(W1, None, "check")
        rewrite_line(f, line_for(f, W2)["txn"], edition="regular")  # line no longer matches its provenance
        with self.assertRaises(Refused) as caught:
            f.run().restore(W2, 0)
        self.assertIn("edition", str(caught.exception))

    def test_resume_checks_the_current_revision(self):
        f = self.quiet_published()
        f.run().rollback(W1, None, "check")
        rewrite_line(f, line_for(f, W1)["txn"], alsoIds=["forged"])
        with self.assertRaises(Refused):
            f.run().resume()  # current and active numbers unchanged, yet W1 is re-checked
        rewrite_line(f, line_for(f, W1)["txn"], alsoIds=also_ids("b"))
        self.assertEqual(f.run().resume().status, "published")

    def test_history_must_validate_before_it_feeds_a_quiet_pool(self):
        f = published(self, [regular_week(W0, "a"), regular_week(W1, "b")])
        promote(f, W1)
        txn = line_for(f, W0)["txn"]
        for change in ({"issueSha256": "0" * 64}, {"op": "correct"}, {"recordIds": ["forged"]},
                       {"alsoIds": []}, {"edition": "quiet"}, {"subjects": []}):
            with self.subTest(change):
                original = line_for(f, W0)
                rewrite_line(f, txn, **change)
                with self.assertRaises(CorruptState):
                    f.run()._quiet_source(W2)
                rewrite_line(f, txn, **{k: original[k] for k in change})
        # stored bytes the registry pins
        reg = f.registry()
        rev = reg["weeks"][W0.isoformat()]["revisions"][0]
        for key in ("webKey", "issueKey"):
            obj = f.store.objects[rev[key]]
            f.store.objects[rev[key]] = Obj(obj.data + b" ", obj.etag)
            with self.assertRaises(CorruptState):
                f.run()._quiet_source(W2)
            f.store.objects[rev[key]] = obj
        prov_path = f.remote.remote / "published" / W0.isoformat() / "rev0.provenance.json"
        good = prov_path.read_bytes()
        prov_path.write_bytes(good + b" ")
        with self.assertRaises(CorruptState):
            f.run()._quiet_source(W2)
        prov_path.write_bytes(good)
        self.assertTrue(f.run()._quiet_source(W2).complete)

    def test_write_retry_rechecks_the_proposed_line(self):
        f = published(self, [regular_week(W0, "a")])
        staged = f.run()._stage(week_starting(W1 - dt.timedelta(days=7)), [], at(W0, 9))
        pub = f.run()
        line = pub._history_entry("correct", W0.isoformat(), 1, staged, "x")
        guard = pub._eligibility_guard(staged.provenance, staged.issue_bytes, line, W0)
        self.assertIsNone(guard())
        line["edition"] = "quiet"  # changed between attempts
        self.assertIn("date/edition invariant", guard())

    def test_restage_when_history_changes_after_friday(self):
        week1 = regular_week(W1, "b")
        f = published(self, [regular_week(W0, "a"), week1])
        promote(f, W1)
        f.clock.t = at(W2 - dt.timedelta(days=3), 10, 2)
        self.assertEqual(f.run().stage_next().status, "staged")
        staged = json.loads((f.remote.remote / "staged" / W2.isoformat() / "provenance.json").read_bytes())
        self.assertEqual(staged["edition"], "quiet")
        f.write(f.records + [strong("b-extra", W1 + dt.timedelta(days=4), 1, year=1958)])
        self.assertEqual(f.run().correct(W1, 0, "an added item").status, "published")
        out = promote(f, W2)
        self.assertEqual(out.status, "published")
        self.assertTrue(any("CHANGED PROOF" in n for n in out.notices), out.notices)

    def test_regeneration_is_byte_for_byte(self):
        f = self.quiet_published()
        src = f.run()._quiet_source(W2)
        from dogear.publish.staging import freeze
        frozen = freeze(f.pub.inputs, QUIET, [], at(W2, 6, 2), src)
        a = generate(week_starting(W2), frozen)
        b = generate(week_starting(W2), frozen)
        self.assertEqual(a[1:], b[1:])


class LegacyAndAttestationTests(unittest.TestCase):
    def setup_legacy(self):
        f = published(self, [regular_week(W0, "a"), regular_week(W1, "b")])
        txn = make_legacy(f, W0)
        promote(f, W1)
        return f, txn

    def test_a_legacy_line_holds_the_quiet_week(self):
        f, _txn = self.setup_legacy()
        out = promote(f, W2)
        self.assertEqual(out.status, "held")
        self.assertIn("subject history incomplete", out.message)

    def mapping(self, f, txn):
        line = next(h for h in f.history() if h["txn"] == txn)
        return [{"recordId": r, "personId": f"dogear:person-{r}", "workId": f"dogear:work-{r}"
                 if f.records and next(x for x in f.records if x["id"] == r).get("work") else None}
                for r in line["recordIds"]]

    def source(self, f, week):
        return (f.remote.remote / "staged" / week.isoformat() / "dataset.json").read_bytes()

    def test_a_valid_attestation_completes_history(self):
        f, txn = self.setup_legacy()
        out = f.run().attest_subjects(txn, self.mapping(f, txn), self.source(f, W0))
        self.assertEqual(out.status, "attested")
        self.assertTrue((f.remote.remote / "evidence" / txn / "dataset.json").exists())
        self.assertEqual(promote(f, W2).status, "published")

    def test_attestation_refusals(self):
        f, txn = self.setup_legacy()
        pub = f.run()
        good = self.mapping(f, txn)
        cases = [
            ("missing source", good, None),
            ("changed source", good, self.source(f, W0) + b" "),
            ("coverage", good[:-1], self.source(f, W0)),
            ("presence", [dict(good[0], workId=None)] + good[1:], self.source(f, W0)),
            ("syntax", [dict(good[0], personId="Person A")] + good[1:], self.source(f, W0)),
        ]
        for name, mapping, source in cases:
            with self.subTest(name), self.assertRaises(Refused):
                f.run().attest_subjects(txn, mapping, source)
        self.assertFalse((f.remote.remote / "evidence").exists())  # refusals leave nothing behind
        self.assertEqual(pub.pubdata.read_attestations(), [])
        new_line = line_for(f, W1)["txn"]  # a line that records its own subjects
        with self.assertRaises(Refused):
            f.run().attest_subjects(new_line, self.mapping(f, new_line), self.source(f, W1))

    def test_duplicate_or_unproven_attestations_prove_nothing(self):
        f, txn = self.setup_legacy()
        f.run().attest_subjects(txn, self.mapping(f, txn), self.source(f, W0))
        with self.assertRaises(Refused):  # a second attestation for the same line
            f.run().attest_subjects(txn, self.mapping(f, txn), self.source(f, W0))
        att = PubData(f.remote.remote).read_attestations()[0]
        PubData(f.remote.remote).append_attestation(att)  # a duplicate appended by hand
        self.assertFalse(f.run()._quiet_source(W2).complete)
        path = f.remote.remote / "subject-attestations.jsonl"
        path.write_text(json.dumps(att, sort_keys=True) + "\n", encoding="utf-8")
        self.assertTrue(f.run()._quiet_source(W2).complete)
        evidence = f.remote.remote / "evidence" / txn / "dataset.json"
        evidence.write_bytes(evidence.read_bytes() + b" ")  # the evidence object altered
        self.assertFalse(f.run()._quiet_source(W2).complete)

    def test_a_hand_written_attestation_must_match_the_published_fingerprints(self):
        """The command fills fingerprints in from the provenance; an attestation appended
        by hand is checked the same way on every read, so a forged one proves nothing."""
        f, txn = self.setup_legacy()
        f.run().attest_subjects(txn, self.mapping(f, txn), self.source(f, W0))
        self.assertTrue(f.run()._quiet_source(W2).complete)
        path = f.remote.remote / "subject-attestations.jsonl"
        att = json.loads(path.read_text(encoding="utf-8"))
        forged = copy.deepcopy(att)
        forged["subjects"][0]["fingerprint"] = "0" * 16
        path.write_text(json.dumps(forged, sort_keys=True) + "\n", encoding="utf-8")
        self.assertFalse(f.run()._quiet_source(W2).complete)
        other = copy.deepcopy(att)
        other["subjects"][0]["recordId"], other["subjects"][1]["recordId"] = (other["subjects"][1]["recordId"],
                                                                              other["subjects"][0]["recordId"])
        path.write_text(json.dumps(other, sort_keys=True) + "\n", encoding="utf-8")
        self.assertFalse(f.run()._quiet_source(W2).complete)

    def test_no_inference_from_the_current_dataset(self):
        """The dataset now carries ids for every record, but a legacy line still holds
        until it is attested: identity is never read back from today's records."""
        f, _txn = self.setup_legacy()
        self.assertTrue(all("personId" in r for r in f.records))
        self.assertFalse(f.run()._quiet_source(W2).complete)


class EditionInvariantTests(unittest.TestCase):
    def setUp(self):
        f = published(self, [regular_week(W0, "a"), regular_week(W1, "b")])
        promote(f, W1)
        promote(f, W2)
        self.f = f
        self.issue = stored_issue(f, W2)
        self.prov = json.loads((f.remote.remote / "published" / W2.isoformat() / "rev0.provenance.json").read_bytes())
        self.line = line_for(f, W2)
        self.regular_issue = stored_issue(f, W1)
        self.regular_prov = json.loads((f.remote.remote / "published" / W1.isoformat() / "rev0.provenance.json")
                                       .read_bytes())

    def test_the_stored_revisions_hold(self):
        self.assertEqual(revision_problems(W2, self.issue, self.prov, self.line), [])
        self.assertEqual(revision_problems(W1, self.regular_issue, self.regular_prov, line_for(self.f, W1)), [])

    def broken(self, monday, issue=None, prov=None, line=None):
        return revision_problems(monday, issue or self.issue, prov or self.prov, line or self.line)

    def test_quiet_violations(self):
        cases = {
            "schema": dict(issue=dict(self.issue, schemaVersion=2)),
            "copy": dict(issue=dict(self.issue, quiet={"heading": QUIET_HEADING, "note": QUIET_NOTE + " "})),
            "no copy": dict(issue={k: v for k, v in self.issue.items() if k != "quiet"}),
            "also": dict(prov=dict(self.prov, records=self.prov["records"] + [
                {"recordId": "x", "fingerprint": "0" * 16, "section": "also", "position": 1}])),
            "line edition": dict(line=dict(self.line, edition="regular")),
            "line also": dict(line=dict(self.line, alsoIds=["x"])),
            "no source": dict(prov=dict(self.prov, inputs={k: v for k, v in self.prov["inputs"].items()
                                                           if k != "quietSource"})),
            "date": dict(issue=dict(self.issue, entries=[dict(self.issue["entries"][0], date=W2.isoformat())]
                                    + self.issue["entries"][1:])),
            "too few": dict(issue=dict(self.issue, entries=self.issue["entries"][:2])),
        }
        for name, kw in cases.items():
            with self.subTest(name):
                self.assertTrue(self.broken(W2, **kw), name)

    def test_quiet_roles_are_one_lead_first_then_standard(self):
        issue = self.issue
        self.assertEqual([e["role"] for e in issue["entries"]][0], "featured")
        def roles(*rs):
            return dict(issue, entries=[dict(e, role=r) for e, r in zip(issue["entries"], rs)])
        n = len(issue["entries"])
        cases = {
            "no lead": roles(*["standard"] * n),
            "two leads": roles("featured", "featured", *["standard"] * (n - 2)),
            "lead not first": roles("standard", "featured", *["standard"] * (n - 2)),
            "unknown role": roles("featured", "lead", *["standard"] * (n - 2)),
            "missing role": dict(issue, entries=[{k: v for k, v in e.items() if k != "role"} if i == 1 else e
                                                 for i, e in enumerate(issue["entries"])]),
        }
        for name, broken in cases.items():
            with self.subTest(name):
                self.assertTrue(any("exactly one featured" in p for p in revision_problems(W2, broken, self.prov)),
                                name)
        self.assertEqual(revision_problems(W2, roles("featured", *["standard"] * (n - 1)), self.prov, self.line), [])

    def test_regular_violations(self):
        issue, prov = self.regular_issue, self.regular_prov
        outside = dict(issue, entries=[dict(issue["entries"][0], date=(W1 - dt.timedelta(days=1)).isoformat())]
                       + issue["entries"][1:])
        self.assertTrue(revision_problems(W1, outside, prov))
        self.assertTrue(revision_problems(W1, dict(issue, schemaVersion=3), prov))
        self.assertTrue(revision_problems(W1, dict(issue, quiet={"heading": "x", "note": "y"}), prov))
        self.assertTrue(revision_problems(W1, issue, dict(prov, edition="quiet")))
        self.assertTrue(revision_problems(W2, issue, prov))  # another week's issue


class FormatTests(unittest.TestCase):
    def test_regular_issue_and_page_are_byte_identical_to_before(self):
        g = ROOT / "tests" / "golden"
        s = stage(week_starting(dt.date(2026, 11, 2)), StageInputs(g / "regular-dataset.json",
                  Path("/nonexistent.json"), "https://dogear.example.org", "dogear@golden"), TEST_POLICY, [],
                  dt.datetime(2026, 10, 30, 10, 2, tzinfo=MANILA), Checks(), "test")
        self.assertEqual(s.issue_bytes, (g / "regular-issue.json").read_bytes())
        self.assertEqual(s.web_bytes, (g / "regular-web.html").read_bytes())
        self.assertEqual(s.provenance["edition"], "regular")
        self.assertEqual([x["recordId"] for x in s.provenance["subjects"]],
                         [r["recordId"] for r in s.provenance["records"] if r["section"] == "digest"])
        self.assertNotIn("quietSource", s.provenance["inputs"])

    def test_quiet_web_and_device_data(self):
        f = published(self, [regular_week(W0, "a"), regular_week(W1, "b")])
        promote(f, W1)
        promote(f, W2)
        reg = f.registry()
        rev = regmod.active_revision(reg, W2.isoformat())
        web = f.store.objects[rev["webKey"]].data.decode("utf-8")
        self.assertIn(f'id="quiet">{QUIET_HEADING}</p><p>{QUIET_NOTE}</p>', web)
        self.assertNotIn('class="also"', web)  # no Also this week section (the stylesheet still names it)
        issue_bytes = f.store.objects[rev["issueKey"]].data
        issue = json.loads(issue_bytes)
        self.assertEqual(device_limit_problems(issue, issue_bytes), [])
        long = dict(issue, quiet={"heading": QUIET_HEADING, "note": "x" * 200})
        self.assertTrue(device_limit_problems(long, json.dumps(long).encode()))
        for e in issue["entries"]:  # contents and items show each entry's own date
            day = dt.date.fromisoformat(e["date"])
            self.assertIn(e["dateLabel"].upper(), web.upper())
            self.assertLess(day, W2)


class IdentityTests(unittest.TestCase):
    def ds(self, *records):
        return parse_dataset(json.dumps({"schemaVersion": 3, "records": list(records)}))

    def test_validation(self):
        with self.assertRaises(DatasetError):  # approved, person, no personId
            self.ds(rec("a-1", 3, 1, personId=None))
        for bad in ("Q42", "wikidata:42", "wikidata:Q0", "dogear:AB", "dogear:a", "isni:0000", "dogear:-x-"):
            with self.subTest(bad), self.assertRaises(DatasetError):
                self.ds(rec("a-1", 3, 1, personId=bad))
        self.ds(rec("a-1", 3, 1, personId="wikidata:Q42"), rec("a-2", 3, 2, personId="dogear:mara-ilagan"))
        self.ds(rec("a-3", 3, 3, personId=None, approved=False))  # unapproved may lack ids

    def test_lint_warnings(self):
        ds = self.ds(rec("a-1", 3, 1, person="José Rizal", personId="wikidata:Q1"),
                     rec("a-2", 3, 2, person="Jose Rizal", personId="wikidata:Q2"),
                     rec("a-3", 3, 3, person="Lu Xun", personId="wikidata:Q3"),
                     rec("a-4", 3, 4, person="鲁迅", personId="wikidata:Q3"))
        joined = "\n".join(ds.warnings)
        self.assertIn("carries 2 different ids", joined)   # one name, two ids
        self.assertIn("used with 2 different names", joined)  # one id, two spellings

    def test_non_latin_subjects_no_longer_collide(self):
        week = week_starting(dt.date(2026, 11, 23))
        ds = self.ds(rec("lu-1", 11, 24, person="鲁迅", personId="wikidata:Q23114", significance=4),
                     rec("go-1", 11, 25, person="Николай Гоголь", personId="wikidata:Q43718", significance=4))
        self.assertEqual(len(select_issue(ds, week).picked), 2)
        # the name fallback (proofs only) is Unicode-aware and never empty
        ds = self.ds(rec("lu-1", 11, 24, person="鲁迅", personId=None, approved=False),
                     rec("to-1", 11, 25, person="Толстой", personId=None, approved=False))
        self.assertEqual(len(select_issue(ds, week, preview=True).picked), 2)
        same = self.ds(rec("lu-1", 11, 24, person="Lu Xun", personId="wikidata:Q23114"),
                       rec("lu-2", 11, 26, person="鲁迅", personId="wikidata:Q23114", significance=5))
        self.assertEqual([c.record.id for c in select_issue(same, week).picked], ["lu-2"])  # one subject

    def test_generation_refuses_a_digest_without_identity(self):
        records = [strong("x-1", W0, 0)]
        records[0].pop("personId")
        records[0]["approval"] = None
        ds_path = Path(self.id().replace(".", "-") + ".json")
        self.addCleanup(lambda: ds_path.unlink(missing_ok=True) if hasattr(Path, "unlink") else None)
        ds_path.write_text(json.dumps({"schemaVersion": 3, "records": records}), encoding="utf-8")
        frozen_inputs = StageInputs(ds_path, Path("/none.json"), "https://dogear.example.org", "dogear@t")
        from dogear.publish.staging import freeze
        frozen = freeze(frozen_inputs, TEST_POLICY, [], at(W0, 9))
        from dogear.select import select_issue as sel
        # a preview would pick it; a publishable generation never may without ids
        self.assertEqual(sel(parse_dataset(ds_path.read_text()), week_starting(W0), preview=True).picked[0].record.id,
                         "x-1")
        with self.assertRaises(StageRefused):
            stage(week_starting(W0), frozen_inputs, TEST_POLICY, [], at(W0, 9), Checks(), "test")


class ProductionGateTests(unittest.TestCase):
    def pub(self, f, supported):
        p = Publisher(f.store, f.remote.checkout(), f.pub.inputs, QUIET, f.checks, f.clock, mode="production",
                      quiet_supported=supported)
        return p

    def test_quiet_week_needs_firmware_support_in_production(self):
        f = Fixture(self, regular_week(W0, "a"), at(W0, 9), policy=QUIET)
        with self.assertRaises(NotReady) as caught:
            self.pub(f, False)._ready()
        self.assertIn("firmware", str(caught.exception))
        self.pub(f, True)._ready()
        contract = json.loads((ROOT / "data" / "firmware-contract.json").read_text())
        self.assertNotIn("kIssueSchemaMax", contract["limits"])  # firmware has not shipped it yet
        with self.assertRaises(NotReady):
            self.pub(f, None)._ready()
        Publisher(f.store, f.remote.checkout(), f.pub.inputs, QUIET, f.checks, f.clock, mode="test")._ready()


class StagingOpsWorkflowTests(unittest.TestCase):
    TEXT = (ROOT / ".github" / "workflows" / "dogear-staging-ops.yml").read_text(encoding="utf-8")

    def script(self) -> str:
        block = self.TEXT.split("name: Main only, allowlisted operation", 1)[1].split("run: |\n", 1)[1]
        block = block.split("\n\n", 1)[0]
        return "\n".join(line[10:] for line in block.splitlines())

    def ok(self, ref: str, op: str) -> bool:
        return subprocess.run(["bash", "-c", self.script()], env={"REF": ref, "OP": op, "PATH": "/usr/bin:/bin"},
                              capture_output=True).returncode == 0

    def test_target_is_literally_staging_and_never_scheduled(self):
        on = self.TEXT.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
        self.assertIn("workflow_dispatch:", on)
        for absent in ("schedule:", "push:", "pull_request", "workflow_call", "target"):
            self.assertNotIn(absent, on)
        self.assertIn("options: [stage-next, promote, status]", on)
        job = self.TEXT.split("\n  staging:\n", 1)[1]
        self.assertIn("needs: guard", job)
        self.assertIn("if: ${{ github.ref == 'refs/heads/main' }}", job)
        self.assertIn("uses: ./.github/workflows/_dogear-publish.yml", job)
        self.assertIn("      target: staging\n", job)
        self.assertNotIn("production", job)
        guard = self.TEXT.split("\n  guard:\n", 1)[1].split("\n  staging:\n", 1)[0]
        for absent in ("secrets", "environment:", "uses:"):
            self.assertNotIn(absent, guard)

    def test_ref_and_operation_are_checked_at_run_time(self):
        for op in ("stage-next", "promote", "status"):
            self.assertTrue(self.ok("refs/heads/main", op))
        for ref, op in (("refs/heads/feature", "promote"), ("refs/tags/v1", "status"), ("refs/heads/main", "launch"),
                        ("refs/heads/main", "correct"), ("refs/heads/main", "promote; launch"),
                        ("refs/heads/main", "")):
            self.assertFalse(self.ok(ref, op), (ref, op))


class StagingFixtureTests(unittest.TestCase):
    def test_gap_and_identities(self):
        fixture = json.loads((ROOT / "fixtures" / "staging-year.json").read_text(encoding="utf-8"))
        days = {(r["month"], r["day"]) for r in fixture["records"]}
        self.assertEqual(len(fixture["records"]), 366 - 13)
        self.assertEqual({(11, d) for d in range(10, 23)} & days, set())
        self.assertIn((11, 9), days)
        self.assertIn((11, 23), days)
        for r in fixture["records"]:
            self.assertEqual(r["personId"], f"dogear:staging-person-{r['month']:02d}-{r['day']:02d}")
            self.assertEqual(r["workId"], f"dogear:staging-work-{r['month']:02d}-{r['day']:02d}")


class QuietDrillTests(unittest.TestCase):
    """End to end through the CLI, as the workflows run it (test mode, the staging
    fixture and policy, git publication data cloned fresh per run, R2 over HTTP, and
    the Worker's router serving): weekly promotion into the November gap."""

    try:
        from tests.test_drills import DrillTests as _D
    except ImportError:
        from test_drills import DrillTests as _D
    setUp, clone, pub, registry = _D.setUp, _D.clone, _D.pub, _D.registry
    pubdata = _D.pubdata

    def test_weekly_promotion_into_the_gap_gives_a_quiet_issue(self):
        self.assertEqual(self.pub("2026-10-08T10:00:00+08:00", "launch", "--first-publication"), 0)
        for monday in ("2026-10-12", "2026-10-19", "2026-10-26", "2026-11-02", "2026-11-09"):
            self.assertEqual(self.pub(f"{monday}T06:02:00+08:00", "promote"), 0, monday)
        sparse = self.registry()["weeks"]["2026-11-09"]
        self.assertEqual(self.pub("2026-11-16T06:02:00+08:00", "promote"), 0)
        reg = self.registry()
        self.assertEqual(reg["current"], "2026-11-16")
        rev = regmod.active_revision(reg, "2026-11-16")
        issue = json.loads(self.fake.objects[rev["issueKey"]][0])
        self.assertEqual((issue["schemaVersion"], issue["quiet"]["heading"]), (3, QUIET_HEADING))
        self.assertTrue(QUIET_MIN <= len(issue["entries"]) <= QUIET_MAX)
        self.assertTrue(all(e["date"] < "2026-11-16" for e in issue["entries"]))
        self.assertTrue(sparse)  # the sparse week before the gap published (never padded)
        work = self.pubdata
        lines = [h for h in PubData(work).history() if h["status"] == "committed"]
        self.assertEqual(lines[-1]["edition"], "quiet")
        self.assertEqual(json.loads((work / "verified.jsonl").read_text().splitlines()[-1])["txn"], lines[-1]["txn"])
        # the next week is ordinary again
        self.assertEqual(self.pub("2026-11-23T06:02:00+08:00", "promote"), 0)
        nxt = json.loads(self.fake.objects[regmod.active_revision(self.registry(), "2026-11-23")["issueKey"]][0])
        self.assertEqual(nxt["schemaVersion"], 2)


class MoreHistoryTests(unittest.TestCase):
    def quiet_w2(self):
        f = published(self, [regular_week(W0, "a"), regular_week(W1, "b")])
        promote(f, W1)
        self.assertEqual(promote(f, W2).status, "published")
        return f

    def test_older_week_correction_respects_a_later_full_digest(self):
        """W3 later features, in full, the person of one of W2's quiet picks. A
        correction of W2 must then drop that pick: subjects featured in later weeks
        count when an older week is corrected (only W's own quiet picks are retained)."""
        f = self.quiet_w2()
        pick = line_for(f, W2)["recordIds"][0]
        person = next(s["personId"] for s in line_for(f, W2)["subjects"] if s["recordId"] == pick)
        f.write(f.records + [strong("d-1", W3, 0, personId=person), strong("d-2", W3 + dt.timedelta(days=1), 1)])
        self.assertEqual(promote(f, W3).status, "published")
        src = f.run()._quiet_source(W2)
        self.assertIn(person, src.subject_identities)
        self.assertIn(pick, src.retainable)
        out = f.run().correct(W2, 0, "recheck against later weeks")
        self.assertEqual(out.status, "published", out.message)
        self.assertNotIn(pick, line_for(f, W2, 1)["recordIds"])
        self.assertEqual(line_for(f, W2, 1)["edition"], "quiet")

    def test_rolled_back_revisions_still_count(self):
        f = self.quiet_w2()
        quiet_picks = set(line_for(f, W2)["recordIds"])
        f.run().rollback(W1, None, "roll back past the quiet week")
        src = f.run()._quiet_source(W3)
        self.assertTrue(quiet_picks <= set(src.excluded))  # W2 is no longer current, but was shown
        self.assertTrue(set(line_for(f, W1)["recordIds"]) <= set(src.excluded))

    def test_only_published_weeks_feed_the_pool(self):
        f = published(self, [regular_week(W0, "a")], policy=TEST_POLICY)  # W1 empty: held
        self.assertEqual(promote(f, W1).status, "held")
        lines = quiet_history.content_lines(f.run().pubdata)
        self.assertEqual([h["week"] for h in lines], [W0.isoformat()])
        src = quiet_history.build_source(lines, {}, W2)
        self.assertEqual({p[1] for p in src.pool}, {W0.isoformat()})

    def test_separation_applies_to_quiet_issues(self):
        f = published(self, [regular_week(W0, "a"), regular_week(W1, "b")])
        promote(f, W1)
        f.clock.t = at(W2, 6, 2)
        pub = f.run()
        pub.separation = "staging"  # these records are real (not synthetic): staging must refuse them
        out = None
        try:
            out = pub.promote()
        except Exception as err:  # noqa: BLE001 - refused or aborted before any registry write
            self.assertIn("synthetic", str(err))
        if out is not None:
            self.assertNotEqual(out.status, "published")
        self.assertEqual(f.registry()["current"], W1.isoformat())

    def test_reset_preflight_checks_the_active_revisions_binding(self):
        f = self.quiet_w2()
        pub = f.run()
        pub.assets = {}
        self.assertFalse(any("edition" in p for p in pub.reset_preflight(W2.isoformat())))
        rewrite_line(f, line_for(f, W2)["txn"], edition="regular")
        problems = f.run().reset_preflight(W2.isoformat())
        self.assertTrue(any("edition" in p for p in problems), problems)


# Codex implementation review of 145ce9f: (1) genuine pre-ID evidence, (2) exact occurrence.

LEGACY_GOLDEN = ROOT / "tests" / "golden" / "legacy-staging-dataset.json.gz"
LEGACY_SHA = "92c7427938d086718092d7cc205266dd282fa691458a89f7d4276e7f389a3844"
LEGACY_DIGEST = ["staging-10-07", "staging-10-06", "staging-10-09", "staging-10-11"]


def legacy_bytes() -> bytes:
    """The staging launch's real frozen dataset (public fixtures/staging-year.json as of
    6b32df2): approved records with persons and works and no personId/workId."""
    import gzip
    return gzip.decompress(LEGACY_GOLDEN.read_bytes())


def legacy_provenance(data: bytes, digest=LEGACY_DIGEST) -> dict:
    raws = {r["id"]: r for r in json.loads(data)["records"]}
    return {"inputs": {"datasetSha256": regmod.sha256(data)},
            "records": [{"recordId": rid, "fingerprint": fingerprint(raws[rid]), "section": "digest",
                         "position": i} for i, rid in enumerate(digest, 1)]}


def reviewed(prov: dict, **override) -> list:
    """A reviewer's mapping, bound to the pinned fingerprints (what attest-subjects builds)."""
    out = []
    for r in prov["records"]:
        if r["section"] == "digest":
            s = {"recordId": r["recordId"], "fingerprint": r["fingerprint"],
                 "personId": f"dogear:reviewed-person-{r['recordId']}", "workId": f"dogear:reviewed-work-{r['recordId']}"}
            s.update(override.get(r["recordId"], {}))
            out.append(s)
    return out


def redump(data: bytes, edit) -> bytes:
    from dogear.dataset import dumps_dataset
    payload = json.loads(data)
    edit(payload["records"])
    return dumps_dataset(payload).encode("utf-8")


class GenuinePreIdEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.data = legacy_bytes()
        self.prov = legacy_provenance(self.data)

    def evidence(self, data=None, sha=None, prov=None, digest=LEGACY_DIGEST):
        data = self.data if data is None else data
        return quiet_history.legacy_evidence(data, sha or regmod.sha256(data), prov or self.prov, digest)

    def test_it_is_the_real_staging_evidence(self):
        self.assertEqual(regmod.sha256(self.data), LEGACY_SHA)
        records = json.loads(self.data)["records"]
        self.assertFalse(any("personId" in r or "workId" in r for r in records))
        for rid in LEGACY_DIGEST:
            raw = next(r for r in records if r["id"] == rid)
            self.assertTrue(raw["person"] and raw["work"] and raw["approval"]["fingerprint"] == fingerprint(raw))

    def test_the_modern_parser_still_rejects_it_and_only_for_identities(self):
        from dogear.dataset import parse_historical_dataset
        with self.assertRaises(DatasetError) as caught:
            parse_dataset(self.data.decode("utf-8"))
        self.assertTrue(caught.exception.problems)
        self.assertTrue(all(p.endswith(("needs personId", "needs workId")) for p in caught.exception.problems),
                        caught.exception.problems[:3])
        self.assertEqual(len(parse_historical_dataset(self.data.decode("utf-8")).records), 366)

    def test_the_historical_parser_keeps_every_other_rule(self):
        from dogear.dataset import parse_historical_dataset
        breaks = {
            "malformed id": lambda rs: rs[0].update(personId="Person A"),
            "id without name": lambda rs: rs[0].update(person=None, personId="dogear:someone"),
            "bad month": lambda rs: rs[0].update(month=13),
            "duplicate id": lambda rs: rs.append(dict(rs[0])),
            "no headline": lambda rs: rs[0].update(headline=""),
        }
        for name, edit in breaks.items():
            with self.subTest(name), self.assertRaises(DatasetError):
                parse_historical_dataset(redump(self.data, edit).decode("utf-8"))
        with self.assertRaises(DatasetError):
            parse_historical_dataset(json.dumps({"schemaVersion": 2, "records": []}))

    def test_accepted_with_frozen_hashes_fingerprints_and_a_reviewed_mapping(self):
        originals, problems = self.evidence()
        self.assertEqual(problems, [])
        self.assertEqual(list(originals), LEGACY_DIGEST)
        self.assertEqual(quiet_history.mapping_problems(reviewed(self.prov), originals, self.prov), [])

    def test_altered_evidence_is_refused(self):
        def recorded(data):  # a dataset that is internally consistent, but not the original
            return self.evidence(data, prov=dict(self.prov, inputs={"datasetSha256": regmod.sha256(data)}))[1]
        cases = {
            "missing": quiet_history.legacy_evidence(None, LEGACY_SHA, self.prov, LEGACY_DIGEST)[1],
            "bytes altered": self.evidence(self.data + b" ", sha=LEGACY_SHA)[1],
            "registry hash": self.evidence(sha="0" * 64)[1],
            "provenance hash": self.evidence(prov=dict(self.prov, inputs={"datasetSha256": "0" * 64}))[1],
            "record removed": recorded(redump(self.data, lambda rs: rs.remove(
                next(r for r in rs if r["id"] == "staging-10-09")))),
            "record substituted": recorded(redump(self.data, lambda rs: next(
                r for r in rs if r["id"] == "staging-10-09").update(headline="Another headline"))),
            "substituted and re-approved": recorded(redump(self.data, lambda rs: [
                r.update(headline="Another headline") or r["approval"].update(fingerprint=fingerprint(r))
                for r in rs if r["id"] == "staging-10-09"])),
            "approval withdrawn": recorded(redump(self.data, lambda rs: [
                r.update(approval=None) for r in rs if r["id"] == "staging-10-09"])),
            "pinned fingerprint": self.evidence(prov=dict(self.prov, records=[
                dict(self.prov["records"][0], fingerprint="0" * 16)] + self.prov["records"][1:]))[1],
            "digest coverage": self.evidence(digest=LEGACY_DIGEST[:3])[1],
            "digest order": self.evidence(digest=[LEGACY_DIGEST[1], LEGACY_DIGEST[0]] + LEGACY_DIGEST[2:])[1],
            "unreadable": self.evidence(b"not json", sha=regmod.sha256(b"not json"),
                                        prov=dict(self.prov, inputs={"datasetSha256": regmod.sha256(b"not json")}))[1],
        }
        for name, problems in cases.items():
            with self.subTest(name):
                self.assertTrue(problems, name)
        self.assertTrue(any("exactly once" in p for p in cases["record removed"]), cases["record removed"])

    def test_altered_mapping_is_refused(self):
        originals, _ = self.evidence()
        good = reviewed(self.prov)
        cases = {
            "coverage": good[:3],
            "extra": good + [dict(good[0], recordId="staging-10-08")],
            "order": [good[1], good[0]] + good[2:],
            "fingerprint": [dict(good[0], fingerprint="0" * 16)] + good[1:],
            "unbound": [{k: v for k, v in good[0].items() if k != "fingerprint"}] + good[1:],
            "extra field": [dict(good[0], name="A")] + good[1:],
            "id syntax": [dict(good[0], personId="Person A")] + good[1:],
            "id format": [dict(good[0], workId="wikidata:Q0")] + good[1:],
            "presence": [dict(good[0], workId=None)] + good[1:],
            "not a list": {"subjects": good},
        }
        for name, mapping in cases.items():
            with self.subTest(name):
                self.assertTrue(quiet_history.mapping_problems(mapping, originals, self.prov), name)

    def test_an_id_the_original_carried_cannot_be_changed(self):
        def with_id(rs):
            r = next(r for r in rs if r["id"] == "staging-10-07")
            r["personId"] = "wikidata:Q42"
            r["approval"]["fingerprint"] = fingerprint(r)
        data = redump(self.data, with_id)
        prov = legacy_provenance(data)
        originals, problems = quiet_history.legacy_evidence(data, regmod.sha256(data), prov, LEGACY_DIGEST)
        self.assertEqual(problems, [])
        mapping = reviewed(prov, **{"staging-10-07": {"personId": "wikidata:Q42"}})
        self.assertEqual(quiet_history.mapping_problems(mapping, originals, prov), [])
        mapping[0]["personId"] = "wikidata:Q43"
        self.assertTrue(quiet_history.mapping_problems(mapping, originals, prov))


def make_pre_id_legacy(f: Fixture, week: dt.date, rev: int = 0) -> tuple:
    """make_legacy, and also the frozen dataset as it was before §21: no personId or
    workId anywhere, approvals at those pre-ID fingerprints, and the provenance and
    registry pinning that dataset. (txn, original frozen dataset bytes)."""
    from dogear.dataset import dumps_dataset
    txn = make_legacy(f, week, rev)
    payload = json.loads((f.remote.remote / "staged" / week.isoformat() / "dataset.json").read_bytes())
    for r in payload["records"]:
        r.pop("personId", None), r.pop("workId", None)
        if r.get("approval"):
            r["approval"]["fingerprint"] = fingerprint(r)
    data = dumps_dataset(payload).encode("utf-8")
    fps = {r["id"]: fingerprint(r) for r in payload["records"]}
    prov_path = f.remote.remote / "published" / week.isoformat() / f"rev{rev}.provenance.json"
    prov = json.loads(prov_path.read_bytes())
    for r in prov["records"]:
        r["fingerprint"] = fps[r["recordId"]]
    prov["inputs"]["datasetSha256"] = regmod.sha256(data)
    prov_data = provenance_bytes(prov)
    prov_path.write_bytes(prov_data)
    reg = f.registry()
    r = reg["weeks"][week.isoformat()]["revisions"][rev]
    r["provenanceSha256"], r["datasetSha256"] = regmod.sha256(prov_data), regmod.sha256(data)
    reg_bytes = regmod.dumps(reg)
    f.store.objects[REGISTRY_KEY] = Obj(reg_bytes, '"pre-id"')
    (f.remote.remote / "publication.json").write_bytes(reg_bytes)
    return txn, data


class PreIdAttestationEndToEndTests(unittest.TestCase):
    def setup_pre_id(self):
        f = published(self, [regular_week(W0, "a"), regular_week(W1, "b")])
        txn, data = make_pre_id_legacy(f, W0)
        promote(f, W1)
        return f, txn, data

    def mapping(self, f, txn):
        return LegacyAndAttestationTests.mapping(None, f, txn)

    def test_a_genuine_pre_id_publication_can_be_attested(self):
        f, txn, data = self.setup_pre_id()
        with self.assertRaises(DatasetError):
            parse_dataset(data.decode("utf-8"))  # the modern path still refuses it
        self.assertFalse(f.run()._quiet_source(W2).complete)
        self.assertEqual(promote(f, W2).status, "held")
        out = f.run().attest_subjects(txn, self.mapping(f, txn), data)
        self.assertEqual(out.status, "attested")
        self.assertEqual((f.remote.remote / "evidence" / txn / "dataset.json").read_bytes(), data)
        self.assertTrue(f.run()._quiet_source(W2).complete)
        self.assertEqual(promote(f, W2).status, "published")

    def test_altered_pre_id_evidence_or_mapping_is_refused(self):
        f, txn, data = self.setup_pre_id()
        good = self.mapping(f, txn)
        later = (f.remote.remote / "staged" / W1.isoformat() / "dataset.json").read_bytes()
        cases = [
            ("bytes", good, data + b" "),
            ("a later dataset", good, later),
            ("record removed", good, redump(data, lambda rs: rs.remove(
                next(r for r in rs if r["id"] == good[0]["recordId"])))),
            ("coverage", good[:-1], data),
            ("presence", [dict(good[0], workId=None)] + good[1:], data),
            ("syntax", [dict(good[0], personId="Person A")] + good[1:], data),
        ]
        for name, mapping, source in cases:
            with self.subTest(name), self.assertRaises(Refused):
                f.run().attest_subjects(txn, mapping, source)
        self.assertFalse((f.remote.remote / "evidence").exists())
        self.assertEqual(PubData(f.remote.remote).read_attestations(), [])

    def test_a_stored_pre_id_attestation_is_rechecked_on_every_read(self):
        f, txn, data = self.setup_pre_id()
        f.run().attest_subjects(txn, self.mapping(f, txn), data)
        path = f.remote.remote / "subject-attestations.jsonl"
        att = json.loads(path.read_text(encoding="utf-8"))
        for name, edit in (("fingerprint", lambda a: a["subjects"][0].update(fingerprint="0" * 16)),
                           ("identity format", lambda a: a["subjects"][0].update(personId="Person A")),
                           ("source hash", lambda a: a.update(sourceDatasetSha256="0" * 64))):
            with self.subTest(name):
                forged = copy.deepcopy(att)
                edit(forged)
                path.write_text(json.dumps(forged, sort_keys=True) + "\n", encoding="utf-8")
                self.assertFalse(f.run()._quiet_source(W2).complete)
        path.write_text(json.dumps(att, sort_keys=True) + "\n", encoding="utf-8")
        self.assertTrue(f.run()._quiet_source(W2).complete)
        evidence = f.remote.remote / "evidence" / txn / "dataset.json"
        evidence.write_bytes(redump(data, lambda rs: rs.pop()))  # a different (valid) dataset
        self.assertFalse(f.run()._quiet_source(W2).complete)


NOV_2, NOV_3 = dt.date(2026, 11, 2), dt.date(2026, 11, 3)
EXACT = "the exact occurrence its provenance pinned"


def forge_entry_date(f: Fixture, week: dt.date, rev: int, rid: str, new: dt.date) -> None:
    """Move one stored quiet entry's date, re-pinning every hash consistently (issue
    object and key, provenance artifacts, registry, mirror, history line), so that only
    the exact-occurrence rule can notice."""
    from dogear.issue import dumps
    reg = f.registry()
    r = reg["weeks"][week.isoformat()]["revisions"][rev]
    issue = json.loads(f.store.objects[r["issueKey"]].data)
    for e in issue["entries"]:
        if e["id"] == rid:
            e["date"] = new.isoformat()
    data = dumps(issue).encode("utf-8")
    sha = regmod.sha256(data)
    r["issueKey"], r["issueSha256"] = regmod.issue_key(issue["issueId"], sha), sha
    f.store.objects[r["issueKey"]] = Obj(data, '"forged"')
    prov_path = f.remote.remote / "published" / week.isoformat() / f"rev{rev}.provenance.json"
    prov = json.loads(prov_path.read_bytes())
    prov["artifacts"]["issueSha256"] = sha
    prov_data = provenance_bytes(prov)
    prov_path.write_bytes(prov_data)
    r["provenanceSha256"] = regmod.sha256(prov_data)
    reg_bytes = regmod.dumps(reg)
    f.store.objects[REGISTRY_KEY] = Obj(reg_bytes, '"forged-reg"')
    (f.remote.remote / "publication.json").write_bytes(reg_bytes)
    rewrite_line(f, r["txn"], issueSha256=sha)


class ExactOccurrenceTests(unittest.TestCase):
    """W2 (16-22 November) is quiet; its pick a-d0-2 was recorded by W0's "Also this
    week" (2-8 November) and occurs on Monday 2 November."""

    def quiet(self, extra=()):
        f = published(self, [regular_week(W0, "a"), regular_week(W1, "b"), list(extra)])
        promote(f, W1)
        self.assertEqual(promote(f, W2).status, "published")
        return f

    def prov(self, f, week=W2, rev=0):
        return json.loads((f.remote.remote / "published" / week.isoformat() / f"rev{rev}.provenance.json").read_bytes())

    def test_generation_pins_the_record_anniversary_in_its_source_week(self):
        f = self.quiet()
        occ = self.prov(f)["quietOccurrences"]
        self.assertEqual(occ[0], {"recordId": "a-d0-2", "sourceWeek": "2026-11-02", "month": 11, "day": 2,
                                  "date": "2026-11-02"})
        raws = {r["id"]: r for r in f.records}
        from dogear.dataset import parse_dataset as pd
        ds = {r.id: r for r in pd(json.dumps({"schemaVersion": 3, "records": f.records})).records}
        from dogear.select import occurrence_in
        for o in occ:
            self.assertEqual(o["date"], occurrence_in(ds[o["recordId"]], week_starting(
                dt.date.fromisoformat(o["sourceWeek"]))).isoformat())
            self.assertEqual((o["month"], o["day"]), (raws[o["recordId"]]["month"], raws[o["recordId"]]["day"]))
        self.assertEqual([e["date"] for e in stored_issue(f, W2)["entries"]], [o["date"] for o in occ])
        self.assertNotIn("quietOccurrences", self.prov(f, W1))  # regular provenance is unchanged

    def test_2_to_3_november_is_refused_and_2_november_passes(self):
        f = self.quiet()
        issue, prov, line = stored_issue(f, W2), self.prov(f), line_for(f, W2)
        self.assertEqual(revision_problems(W2, issue, prov, line), [])
        moved = copy.deepcopy(issue)
        moved["entries"][0]["date"] = NOV_3.isoformat()
        # 3 November is inside the same source week and before W2: the old week-range
        # check would have accepted it.
        self.assertTrue(NOV_2 <= NOV_3 <= NOV_2 + dt.timedelta(days=6) and NOV_3 < W2)
        problems = revision_problems(W2, moved, prov, line)
        self.assertTrue(any(EXACT in p for p in problems), problems)
        moved["entries"][0]["date"] = NOV_2.isoformat()
        self.assertEqual(revision_problems(W2, moved, prov, line), [])

    def test_the_pinned_occurrence_itself_must_be_the_anniversary(self):
        f = self.quiet()
        issue, prov = stored_issue(f, W2), self.prov(f)
        raws = {r["id"]: r for r in f.records}
        self.assertEqual(revision_problems(W2, issue, prov, frozen_records=raws), [])

        def forged(**change):
            p = copy.deepcopy(prov)
            p["quietOccurrences"][0].update(change)
            i = copy.deepcopy(issue)
            i["entries"][0]["date"] = p["quietOccurrences"][0]["date"]
            return i, p
        cases = {
            # date and issue moved together, month/day left: not the anniversary
            "date only": (forged(date=NOV_3.isoformat()), None),
            # all three moved consistently: only the frozen record can tell
            "month/day too": (forged(date=NOV_3.isoformat(), day=3), raws),
            "source week": (forged(sourceWeek=W1.isoformat(), date="2026-11-09", day=9), None),
            "not a Monday": (forged(sourceWeek="2026-11-01", date="2026-11-01", day=1), None),
            "missing": ((issue, {k: v for k, v in prov.items() if k != "quietOccurrences"}), None),
            "order": ((issue, dict(prov, quietOccurrences=prov["quietOccurrences"][::-1])), None),
            "extra field": (forged(note="x"), None),
        }
        for name, ((i, p), frozen) in cases.items():
            with self.subTest(name):
                self.assertTrue(revision_problems(W2, i, p, frozen_records=frozen), name)
        # a pool forged to record W itself: only "before W" stops an in-week date
        p = copy.deepcopy(prov)
        p["inputs"]["quietSource"]["pool"] = [[rid, W2.isoformat() if rid == "a-d0-2" else wk]
                                              for rid, wk in p["inputs"]["quietSource"]["pool"]]
        p["quietOccurrences"][0].update(sourceWeek=W2.isoformat(), day=16, date=W2.isoformat())
        i = copy.deepcopy(issue)
        i["entries"][0]["date"] = W2.isoformat()
        problems = revision_problems(W2, i, p)
        self.assertTrue(any("anniversary in its source week" in x for x in problems), problems)
        # the consistent month/day forgery is caught only with the frozen record
        (i, p), _ = cases["month/day too"]
        self.assertEqual(revision_problems(W2, i, p), [])
        regular = self.prov(f, W1)
        self.assertTrue(revision_problems(W1, stored_issue(f, W1), dict(regular, quietOccurrences=[])))

    def test_every_stored_revision_path_refuses_the_moved_date(self):
        def fresh():
            f = self.quiet(extra=regular_week(W3, "c"))
            return f

        with self.subTest("history validation"):
            f = fresh()
            forge_entry_date(f, W2, 0, "a-d0-2", NOV_3)
            with self.assertRaises(CorruptState) as caught:
                f.run()._quiet_source(W3)
            self.assertIn(EXACT, str(caught.exception))

        with self.subTest("reset preflight"):
            f = fresh()
            pub = f.run()
            pub.assets = {}
            self.assertFalse(any(EXACT in p for p in pub.reset_preflight(W2.isoformat())))
            forge_entry_date(f, W2, 0, "a-d0-2", NOV_3)
            pub = f.run()
            pub.assets = {}
            problems = pub.reset_preflight(W2.isoformat())
            self.assertTrue(any(EXACT in p for p in problems), problems)

        with self.subTest("restore"):
            f = fresh()
            self.assertEqual(f.run().rollback(W1, None, "check").status, "published")
            forge_entry_date(f, W2, 0, "a-d0-2", NOV_3)
            with self.assertRaises(Refused) as caught:
                f.run().restore(W2, 0)
            self.assertIn(EXACT, str(caught.exception))

        with self.subTest("resume"):  # held on the quiet week: resume re-checks the current revision
            f = fresh()
            self.assertEqual(promote(f, W3).status, "published")
            self.assertEqual(f.run().rollback(W2, None, "check").status, "published")
            forge_entry_date(f, W2, 0, "a-d0-2", NOV_3)
            with self.assertRaises(Refused) as caught:
                f.run().resume()
            self.assertIn(EXACT, str(caught.exception))

        with self.subTest("rollback"):
            f = fresh()
            self.assertEqual(promote(f, W3).status, "published")
            forge_entry_date(f, W2, 0, "a-d0-2", NOV_3)
            with self.assertRaises(Refused) as caught:
                f.run().rollback(W2, None, "back to the quiet week")
            self.assertIn(EXACT, str(caught.exception))

        with self.subTest("correction"):
            f = fresh()
            forge_entry_date(f, W2, 0, "a-d0-2", NOV_3)
            f.edit("a-d2-2", headline="Edited")
            with self.assertRaises(CorruptState) as caught:
                f.run().correct(W2, 0, "a quiet pick edited")
            self.assertIn(EXACT, str(caught.exception))

        with self.subTest("pre-write guard and retry"):
            f = fresh()
            pub = f.run()
            reg = f.registry()
            rev = reg["weeks"][W2.isoformat()]["revisions"][0]
            good = f.store.objects[rev["issueKey"]].data
            guard = pub._eligibility_guard(self.prov(f), good, line_for(f, W2), W2)
            self.assertIsNone(guard())
            forge_entry_date(f, W2, 0, "a-d0-2", NOV_3)
            rev = f.registry()["weeks"][W2.isoformat()]["revisions"][0]
            moved = f.store.objects[rev["issueKey"]].data
            reason = pub._eligibility_guard(self.prov(f), moved, line_for(f, W2), W2)()
            self.assertIn(EXACT, reason or "")

    def test_the_pre_promotion_recheck_refuses_a_moved_staged_date(self):
        from dogear.publish.staging import Staged, verify_staged
        f = published(self, [regular_week(W0, "a"), regular_week(W1, "b")])
        promote(f, W1)
        f.clock.t = at(W2 - dt.timedelta(days=3), 10, 2)
        self.assertEqual(f.run().stage_next().status, "staged")
        pub = f.run()
        files = PubData(f.remote.remote).read_staged(W2)
        staged = Staged.from_files(W2, files)
        source = pub._quiet_source(W2)
        history = pub._history(f.registry(), W2)
        args = (pub.inputs, QUIET, history, pub._dataset_text(), Checks(), "test")
        self.assertEqual(verify_staged(staged, *args, quiet_source=source), [])
        issue = json.loads(files["issue.json"])
        issue["entries"][0]["date"] = NOV_3.isoformat()
        from dogear.issue import dumps
        moved = Staged.from_files(W2, dict(files, **{"issue.json": dumps(issue).encode("utf-8")}))
        self.assertTrue(verify_staged(moved, *args, quiet_source=source))
        # Monday's promotion re-stages from the frozen inputs: 2 November is what publishes
        PubData(f.remote.remote).write_staged(W2, dict(files, **{"issue.json": dumps(issue).encode("utf-8")}))
        self.assertEqual(promote(f, W2).status, "published")
        self.assertEqual(stored_issue(f, W2)["entries"][0]["date"], NOV_2.isoformat())

    def test_stage_and_the_recheck_compare_occurrences_with_the_frozen_records(self):
        import dogear.publish.staging as st
        seen = []
        real = st.revision_problems

        def spy(monday, issue, provenance, line=None, frozen_records=None):
            if provenance.get("edition") == "quiet":
                seen.append(frozen_records)
            return real(monday, issue, provenance, line, frozen_records=frozen_records)
        f = published(self, [regular_week(W0, "a"), regular_week(W1, "b")])
        promote(f, W1)
        f.clock.t = at(W2 - dt.timedelta(days=3), 10, 2)
        with mock.patch.object(st, "revision_problems", spy):
            self.assertEqual(f.run().stage_next().status, "staged")   # stage
            self.assertEqual(promote(f, W2).status, "published")      # verify_staged
        self.assertGreaterEqual(len(seen), 2)
        self.assertTrue(all(isinstance(s, dict) and "a-d0-2" in s for s in seen))
