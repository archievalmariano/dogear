"""DOGEAR host tests (stdlib unittest). Run from dogear/:

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from dogear import select as sel
from dogear.affinity import parse_affinity
from dogear.balance import balance, render_balance
from dogear.cli import load_issues, stage
from dogear.dataset import DatasetError, approve, fingerprint, load_dataset, parse_dataset
from dogear.issue import build_issue, build_web, kicker, typeset
from dogear.select import History, select_issue
from dogear.web import render_web
from dogear.week import (
    current_week, day_month, long_date, range_dateline, range_dateline_short, short_date, week_of, week_starting,
)

WORDS_50 = " ".join(["word"] * 50)
NOW = dt.datetime(2026, 11, 23, 6, tzinfo=dt.timezone(dt.timedelta(hours=8)))


def rec(rid, month, day, year=1900, approved=True, **kw):
    r = {
        "id": rid,
        "month": month,
        "day": day,
        "year": year,
        "precision": "day",
        "dateBasis": "recorded",
        "claims": "straightforward",
        "type": "birth",
        "person": rid,
        "work": None,
        "headline": f"Headline {rid}",
        "copy": {"digest": WORDS_50, "expanded": None, "allowSecondPage": False},
        "tags": [],
        "significance": 3,
        "recognition": "core",
        "discoveryValue": 0,
        "phRelevance": 0,
        "seaRelevance": 0,
        "domain": "literature",
        "status": "verified",
        "sources": [{"title": "S", "url": "https://library.test/s", "kind": "institutional"}],
        "links": [],
        "notes": None,
        "area": "europe",
        "language": "en",
        "womenWriter": False,
        "approval": None,
    }
    if "link" in kw:  # tests name one link; the dataset keeps a list of candidates
        one = kw.pop("link")
        kw["links"] = [one] if one else []
    r.update(kw)
    if r["type"] == "observance" and "area" not in kw:
        r["area"] = "global"  # a national observance must name its locality
    if approved and r["status"] == "verified" and r["approval"] is None:
        r["approval"] = {"by": "Editor", "on": "2026-10-07", "fingerprint": fingerprint(r)}
    return r


def pub(rid, day, **kw):
    return rec(rid, 11, day, type="publication", person=None, work=rid, **kw)


def dataset(*records):
    return parse_dataset(json.dumps({"schemaVersion": 3, "records": list(records)}))


def ids(selection):
    return [c.record.id for c in selection.picked]


def cand(selection, rid):
    return next(c for c in selection.candidates if c.record.id == rid)


def excluded(selection, rid):
    return cand(selection, rid).excluded


WEEK = week_starting(dt.date(2026, 11, 23))  # Mon 23 - Sun 29 Nov 2026
def book(title, author, match="event_work", rights="public-domain-worldwide", type="read-online", **kw):
    """A book link. pub() records name their work after their id, so book(rid, ...) matches."""
    link = {"type": type, "url": "https://www.gutenberg.org/ebooks/1", "match": match, "title": title,
            "author": author, "provider": "Project Gutenberg", "rights": rights}
    link.update(kw)
    return link




class DatasetTests(unittest.TestCase):


    def assertProblem(self, record, fragment):
        with self.assertRaises(DatasetError) as ctx:
            dataset(record)
        self.assertTrue(any(fragment in p for p in ctx.exception.problems), ctx.exception.problems)

    def test_impossible_day_rejected(self):
        self.assertProblem(rec("a", 2, 30), "does not exist")
        self.assertProblem(rec("a", 4, 31), "does not exist")

    def test_leap_day_allowed(self):
        dataset(rec("a", 2, 29))

    def test_month_precision_may_not_invent_a_day(self):
        self.assertProblem(rec("a", 3, 5, precision="month"), "never invent a day")
        dataset(rec("a", 3, None, precision="month"))

    def test_duplicate_id_rejected(self):
        with self.assertRaises(DatasetError):
            dataset(rec("a", 1, 1), rec("a", 1, 2))

    def test_verified_needs_a_source(self):
        self.assertProblem(rec("a", 1, 1, sources=[]), "at least one source")
        dataset(rec("a", 1, 1, sources=[], status="provisional"))

    def test_read_free_needs_public_domain(self):
        self.assertProblem(rec("a", 1, 1, link=book("Book", "a", "representative_work", rights="in-copyright")),
                           "public-domain")
        dataset(rec("a", 1, 1, link=book("Book", "a", "representative_work")))

    def test_reading_links_name_the_book(self):
        link = {k: v for k, v in book("Book", "a", "representative_work").items() if k != "title"}
        self.assertProblem(rec("a", 1, 1, link=link), "title")

    def test_links_must_be_https(self):
        link = book("x", "a", "representative_work", rights="in-copyright", type="find-book", url="http://example.org")
        self.assertProblem(rec("a", 1, 1, link=link), "https")

    def test_bad_id_rejected(self):
        self.assertProblem(rec("Bad_ID", 1, 1), "kebab-case")

    def test_editorial_dimensions_are_validated(self):
        self.assertProblem(rec("a", 1, 1, significance=6), "significance")
        self.assertProblem(rec("a", 1, 1, recognition="famous"), "recognition")
        self.assertProblem(rec("a", 1, 1, phRelevance=3), "phRelevance")
        self.assertProblem(rec("a", 1, 1, domain="cookery"), "domain")
        self.assertProblem(rec("a", 1, 1, area="mars"), "area")
        self.assertProblem(rec("a", 1, 1, language="English"), "language")
        self.assertProblem(rec("a", 1, 1, label="Nobel"), "label")

    def test_copy_length_is_a_warning_not_an_error(self):
        ds = dataset(rec("a", 1, 1, copy={"digest": "Too short.", "expanded": None, "allowSecondPage": False}))
        self.assertTrue(any("digest copy is 2 words" in w for w in ds.warnings))


class ApprovalTests(unittest.TestCase):
    def test_unapproved_record_is_not_published(self):
        s = select_issue(dataset(rec("a", 11, 24, approved=False)), WEEK)
        self.assertEqual(ids(s), [])
        self.assertEqual(excluded(s, "a"), "awaiting approval")

    def test_material_edit_withdraws_approval(self):
        r = rec("a", 11, 24)
        r["copy"] = {**r["copy"], "digest": WORDS_50 + " more"}
        self.assertEqual(excluded(select_issue(dataset(r), WEEK), "a"), "changed since approval")

    def test_editorial_metadata_does_not_withdraw_approval(self):
        r = rec("a", 11, 24)
        r.update(significance=5, recognition="anchor", discoveryValue=2, phRelevance=1, seaRelevance=1,
                 domain="history", tags=["new"], notes="checked again", area="south-asia")
        self.assertEqual(dataset(r).records[0].approval_state, "approved")

    def test_approved_records_publish_without_issue_approval(self):
        s = select_issue(dataset(rec("a", 11, 24)), WEEK)
        self.assertEqual(ids(s), ["a"])
        self.assertFalse(s.preview)

    def test_preview_admits_pending_records_and_says_so(self):
        s = select_issue(dataset(rec("a", 11, 24, approved=False)), WEEK, preview=True)
        self.assertEqual(ids(s), ["a"])
        self.assertTrue(build_issue(s, NOW)["preview"])

    def test_preview_never_admits_unverified_records(self):
        s = select_issue(dataset(rec("p", 11, 24, status="provisional", approved=False)), WEEK, preview=True)
        self.assertEqual(ids(s), [])

    def test_approve_writes_fingerprint_and_refuses_unverified(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "d.json"
            records = [rec("a", 11, 24, approved=False), rec("p", 11, 25, status="disputed", approved=False)]
            path.write_text(json.dumps({"schemaVersion": 3, "records": records}))
            self.assertEqual(approve(path, ["a"], "Editor", dt.date(2026, 10, 7)), ["a"])
            self.assertEqual(load_dataset(path).by_id("a").approval_state, "approved")
            self.assertEqual(approve(path, ["a"], "Editor", dt.date(2026, 10, 8)), [])  # no-op
            with self.assertRaises(ValueError):
                approve(path, ["p"], "Editor", dt.date(2026, 10, 7))


class WeekTests(unittest.TestCase):
    def test_week_runs_monday_to_sunday(self):
        w = week_of(dt.date(2026, 11, 26))
        self.assertEqual((w.start, w.end), (dt.date(2026, 11, 23), dt.date(2026, 11, 29)))
        self.assertEqual(w.issue_id, "dogear-2026-11-23")

    def test_week_starting_rejects_non_monday(self):
        with self.assertRaises(ValueError):
            week_starting(dt.date(2026, 11, 24))

    def test_current_week_uses_manila_time(self):
        now = dt.datetime(2026, 11, 22, 22, 30, tzinfo=dt.timezone.utc)  # Mon 06:30 in Manila
        self.assertEqual(current_week(now).start, dt.date(2026, 11, 23))

    def test_datelines(self):
        self.assertEqual(range_dateline(week_starting(dt.date(2026, 10, 12))), "12–18 OCTOBER 2026")
        self.assertEqual(range_dateline(week_starting(dt.date(2026, 9, 28))), "28 SEPTEMBER – 4 OCTOBER 2026")
        self.assertEqual(range_dateline(week_starting(dt.date(2026, 12, 28))), "28 DECEMBER 2026 – 3 JANUARY 2027")
        self.assertEqual(range_dateline_short(week_starting(dt.date(2026, 12, 28))), "28 DEC 2026 – 3 JAN 2027")
        self.assertEqual(range_dateline_short(week_starting(dt.date(2026, 11, 30))), "30 NOV – 6 DEC 2026")

    def test_date_rule(self):
        self.assertEqual(short_date(dt.date(2026, 9, 30)), "Wed 30 Sep")
        self.assertEqual(long_date(dt.date(2026, 9, 30)), "Wednesday 30 September 2026")
        self.assertEqual(day_month(dt.date(2027, 1, 1)), "1 Jan")


class EligibilityAndShapeTests(unittest.TestCase):
    def test_week_boundaries(self):
        ds = dataset(rec("sun-before", 11, 22), rec("mon", 11, 23), rec("sun", 11, 29), rec("mon-after", 11, 30))
        self.assertEqual(sorted(ids(select_issue(ds, WEEK))), ["mon", "sun"])

    def test_week_across_new_year_uses_each_days_year(self):
        ds = dataset(rec("dec", 12, 31, year=1926), rec("jan", 1, 2, year=1927))
        s = select_issue(ds, week_starting(dt.date(2026, 12, 28)))
        self.assertEqual({c.record.id: (c.date.year, c.years) for c in s.picked},
                         {"dec": (2026, 100), "jan": (2027, 100)})

    def test_leap_day_only_in_leap_years(self):
        ds = dataset(rec("leap", 2, 29))
        self.assertEqual(ids(select_issue(ds, week_of(dt.date(2028, 2, 29)))), ["leap"])
        self.assertEqual(ids(select_issue(ds, week_of(dt.date(2027, 2, 28)))), [])

    def test_unverified_records_are_never_published(self):
        ds = dataset(rec("ok", 11, 24), rec("prov", 11, 24, status="provisional", approved=False),
                     rec("disp", 11, 25, status="disputed", approved=False))
        s = select_issue(ds, WEEK)
        self.assertEqual(ids(s), ["ok"])
        self.assertEqual(excluded(s, "disp"), "status is disputed")

    def test_month_precision_never_matches(self):
        ds = dataset(rec("noli", 11, None, precision="month", significance=5))
        self.assertEqual(select_issue(ds, WEEK).candidates, [])

    def test_weak_dates_are_not_padded_in(self):
        s = select_issue(dataset(rec("weak", 11, 24, significance=1)), WEEK)
        self.assertIn("significance below", excluded(s, "weak"))

    def test_future_and_zero_year_anniversaries_excluded(self):
        ds = dataset(rec("future", 11, 24, year=2030), rec("this-year", 11, 25, year=2026))
        self.assertEqual(ids(select_issue(ds, WEEK)), [])

    def test_one_subject_per_issue(self):
        ds = dataset(rec("born", 11, 24, person="Mara Ilagan"),
                     rec("book", 11, 26, type="publication", person="Mara Ilagan", work="The Salt Orchard", significance=5))
        s = select_issue(ds, WEEK)
        self.assertEqual(ids(s), ["book"])
        self.assertTrue(cand(s, "born").shape_blocked)

    def test_per_day_cap(self):
        ds = dataset(*(pub(f"w{i}", 24) for i in range(3)))
        self.assertEqual(len(select_issue(ds, WEEK).picked), sel.MAX_PER_DAY)

    def test_births_and_deaths_outer_limit(self):
        ds = dataset(*(rec(f"p{i}", 11, 23 + i % 7, significance=5) for i in range(9)))
        self.assertLessEqual(len(select_issue(ds, WEEK).picked), sel.MAX_PERSON_EVENTS)

    def test_slot_bars_rise_with_length(self):
        self.assertEqual([sel.slot_bar(n) for n in range(1, 9)], [30, 30, 30, 30, 42, 42, 52, 52])

    def test_a_weak_week_stays_short(self):
        weak = [rec(f"w{i}", 11, 23 + i, significance=2, type=t) for i, t in
                enumerate(["birth", "death", "publication", "observance", "birth", "death"])]
        s = select_issue(dataset(*weak), WEEK)
        self.assertLess(len(s.picked), 4)
        self.assertTrue(any("issue stopped" in (c.excluded or "") for c in s.candidates))

    def test_only_unusually_strong_weeks_reach_seven(self):
        mixed = [pub(f"p{i}", 23 + i % 7, significance=4) for i in range(8)]
        self.assertLessEqual(len(select_issue(dataset(*mixed), WEEK).picked), 6)
        tiers = ["anchor", "anchor", "discovery", "discovery", "core", "core", "core", "core"]
        types = ["birth", "death", "publication", "literary-event", "observance", "birth", "death", "publication"]
        strong = [rec(f"s{i}", 11, 23 + i % 7, significance=5, type=t, person=f"s{i}", recognition=tier,
                      discoveryValue=2, year=1926) for i, (t, tier) in enumerate(zip(types, tiers))]
        self.assertGreaterEqual(len(select_issue(dataset(*strong), WEEK).picked), 7)

    def test_sparse_anchor_week_stops_early(self):
        anchors = [rec(f"a{i}", 11, 23 + i, significance=4, recognition="anchor") for i in range(6)]
        s = select_issue(dataset(*anchors), WEEK)
        self.assertLessEqual(len(s.picked), 4)


class StrictValidationTests(unittest.TestCase):
    """Validation guarantees what selection and rendering rely on: a malformed
    record is a DatasetError, never a crash or a silently odd value."""

    def assertRejected(self, **kw):
        r = rec("x", 11, 24, approved=False)
        r.update(kw)  # after rec(), which needs a well-formed record to build
        with self.assertRaises(DatasetError, msg=str(kw)):
            dataset(r)

    def test_codex_reproductions(self):
        self.assertRejected(person=42)
        self.assertRejected(sources=[{"title": 5, "url": "https://example.org/s", "kind": "secondary"}])
        self.assertRejected(month=True)
        self.assertRejected(year=True)
        self.assertRejected(links=[None])
        self.assertRejected(copy="a string, not an object")
        self.assertRejected(approval=["Editor", "2026-10-07"])
        self.assertRejected(sources=[{"title": "S", "url": "https://", "kind": "secondary"}])

    def test_types_and_containers(self):
        for kw in (dict(day=True), dict(significance=True), dict(discoveryValue=True), dict(phRelevance="2"),
                   dict(work=7), dict(notes=["n"]), dict(verifiedBy=1), dict(verifiedOn="7 Oct"),
                   dict(tags="reading"), dict(tags=[1]), dict(sources={"title": "S"}), dict(sources=[None]),
                   dict(links={"type": "source"}), dict(links=[5]), dict(copy=None),
                   dict(copy={"digest": 5, "expanded": None, "allowSecondPage": False}),
                   dict(approval={"by": 5, "on": "2026-10-07", "fingerprint": "0" * 16}),
                   dict(approval={"by": "E", "on": 20261007, "fingerprint": "0" * 16})):
            self.assertRejected(**kw)

    def test_years_are_ce_within_range(self):
        for year in (0, -500, 19866, 2101, 1832.0, "1832"):
            self.assertRejected(year=year)
        self.assertEqual(dataset(rec("x", 11, 24, year=1)).records[0].year, 1)
        self.assertEqual(dataset(rec("x", 11, 24, year=2026)).records[0].year, 2026)

    def test_urls_need_a_real_https_host(self):
        for url in ("https://", "https:///x", "http://example.org/x", "https://user@example.org/x",
                    "https://example .org/x", "https://localhost/x", "https://1.2.3.4/x", "https://example.org:0/",
                    "https://example.org:99999/", "https://-bad.org/", "ftp://example.org/"):
            self.assertRejected(sources=[{"title": "S", "url": url, "kind": "secondary"}])
            self.assertRejected(links=[{"type": "read-more", "url": url, "title": "T", "provider": "P"}])
        ok = dataset(rec("x", 11, 24, sources=[{"title": "S", "url": "https://www.ncca.gov.ph/a/b/?p=1#x",
                                                 "kind": "institutional"}]))
        self.assertEqual(len(ok.records[0].sources), 1)

    def test_link_fields_are_typed(self):
        base = {"type": "read-more", "url": "https://example.org/x", "title": "T", "provider": "P"}
        for bad in ({**base, "title": 5}, {**base, "provider": ""}, {**base, "url": 5}, {**base, "author": 3}):
            self.assertRejected(links=[bad])

    def test_malformed_dataset_shapes(self):
        for text in ("not json", "[]", '{"schemaVersion": 3}', '{"schemaVersion": 3, "records": {}}',
                     '{"schemaVersion": 3, "records": [5]}', '{"schemaVersion": 3, "records": ["x"]}'):
            with self.assertRaises(DatasetError, msg=text):
                parse_dataset(text)


    def test_lone_surrogate_escapes_are_rejected(self):
        valid = json.dumps({"schemaVersion": 3, "records": [rec("x", 11, 24)]})
        self.assertEqual(len(parse_dataset(valid).records), 1)
        for field in ("person", "work", "notes"):
            r = rec("x", 11, 24)
            r[field] = "Ka\ud800tha"
            text = json.dumps({"schemaVersion": 3, "records": [r]})  # ASCII text holding the escape
            self.assertIn("\\ud800", text)
            with self.assertRaises(DatasetError, msg=field):
                parse_dataset(text)
        # In a key, in an unknown field, and as a low surrogate.
        for extra in ('{"\\udfff": 1}', '"\\udc80"'):
            text = valid[:-2] + ', "extra": ' + extra + "]}"
            with self.assertRaises(DatasetError, msg=extra):
                parse_dataset(text)
        with self.assertRaises(DatasetError):
            parse_dataset('{"schemaVersion": 3, "records": []}\ud800')  # a raw surrogate in the text itself

    def test_excessive_nesting_is_rejected(self):
        for depth in (100, 5000, 100000):
            for open_, close in (("[", "]"), ('{"a":', "}")):
                text = '{"schemaVersion": 3, "records": [], "x": ' + open_ * depth + "1" + close * depth + "}"
                with self.assertRaises(DatasetError, msg=f"{open_} x {depth}"):
                    parse_dataset(text)
        r = rec("x", 11, 24)
        r["notes"] = "n"
        r["links"] = [{"type": "read-more", "url": "https://example.org/x", "title": "T", "provider": "P",
                       "nested": [[[[[[[[[[[[[[[[[["deep"]]]]]]]]]]]]]]]]]]}]
        with self.assertRaises(DatasetError):
            parse_dataset(json.dumps({"schemaVersion": 3, "records": [r]}))

    def test_invalid_utf8_file_is_a_dataset_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_bytes(b'{"schemaVersion": 3, "records": [], "x": "\xff\xfe"}')
            with self.assertRaises(DatasetError):
                load_dataset(path)
            with self.assertRaises(DatasetError):
                approve(path, ["x"], "Editor", dt.date(2026, 10, 7))


class PreviewMarkingTests(unittest.TestCase):
    """An edition that shows anything unapproved is a proof, wherever it shows it."""

    def day_capped(self):
        # Two approved records fill 24 Nov; a third, unapproved, loses to the day cap.
        return dataset(rec("a", 11, 24, significance=5), rec("b", 11, 24, significance=5),
                       rec("pending", 11, 24, significance=4, approved=False))

    def test_unapproved_runner_up_makes_a_proof(self):
        s = select_issue(self.day_capped(), WEEK, preview=True)
        self.assertEqual(sorted(ids(s)), ["a", "b"])
        self.assertTrue(all(c.record.approval_state == "approved" for c in s.picked))
        self.assertEqual([c.record.id for c in s.runners_up], ["pending"])
        self.assertTrue(s.preview)
        issue = build_issue(s, NOW)
        web = build_web(s, issue)
        self.assertTrue(issue["preview"])
        html = render_web(web)
        self.assertIn('content="noindex"', html)
        self.assertIn("Proof.", html)

    def test_explicit_preview_stays_a_proof_even_when_all_approved(self):
        s = select_issue(dataset(rec("a", 11, 24), rec("b", 11, 25)), WEEK, preview=True)
        self.assertTrue(all(c.record.approval_state == "approved" for c in s.rendered))
        self.assertTrue(s.preview)

    def test_publication_mode_renders_only_approved_records(self):
        s = select_issue(self.day_capped(), WEEK)
        self.assertFalse(s.preview)
        self.assertNotIn("pending", [c.record.id for c in s.rendered])  # not even in ALSO THIS WEEK
        self.assertEqual(cand(s, "pending").excluded, "awaiting approval")
        html = render_web(build_web(s, build_issue(s, NOW)))
        self.assertNotIn("noindex", html)
        self.assertNotIn("Proof.", html)


class RepeatRecencyTests(unittest.TestCase):
    """The repeat penalty compares issue weeks, not anniversary dates."""

    def penalised(self, day, days_ago):
        r = rec("r", 11, day)
        hist = History(((WEEK.start - dt.timedelta(days=days_ago), ("r",)),))
        return cand(select_issue(dataset(r), WEEK, hist), "r").history == -sel.RECENT_PENALTY

    def test_boundary_is_365_days_from_monday_to_monday(self):
        for day in (23, 26, 29):  # Monday, Thursday, Sunday of the issue week
            self.assertTrue(self.penalised(day, 364), day)
            self.assertTrue(self.penalised(day, 365), day)
            self.assertFalse(self.penalised(day, 366), day)

    def test_one_prior_issue_judges_every_day_of_the_week_alike(self):
        for days_ago in (357, 364, 371):  # the issue 51, 52 and 53 weeks back
            self.assertEqual({self.penalised(day, days_ago) for day in range(23, 30)}, {days_ago <= 365}, days_ago)


class ScoringTests(unittest.TestCase):
    def score(self, **kw):
        s = select_issue(dataset(rec("x", 11, 24, **kw)), WEEK, affinity=parse_affinity(json.dumps(
            {"preferred": [{"name": "Fav"}], "adjacent": [{"name": "Thinker"}]})))
        return s.candidates[0]

    def test_significance_dominates(self):
        self.assertGreater(self.score(significance=5).score, self.score(significance=4, recognition="anchor",
                                                                         phRelevance=1).score)

    def test_regional_lifts_take_the_strongest_only(self):
        self.assertEqual(self.score(phRelevance=2).lifts, sel.PH_LIFT[2])
        self.assertEqual(self.score(area="southeast-asia").lifts, sel.SEA_LIFT[2])
        self.assertEqual(self.score(seaRelevance=1, area="north-america").lifts, sel.SEA_LIFT[1])
        self.assertEqual(self.score(area="east-asia").lifts, sel.WIDER_ASIA_LIFT)
        self.assertEqual(self.score(phRelevance=2, area="southeast-asia").lifts, sel.PH_LIFT[2])
        self.assertEqual(self.score(area="europe").lifts, 0)
        self.assertEqual(self.score(area="north-america").lifts, 0)

    def test_global_south_lift_keeps_the_real_region(self):
        for area, word in (("africa", "African"), ("latin-america", "Latin American"), ("middle-east", "Middle Eastern")):
            c = self.score(area=area)
            self.assertEqual(c.lifts, sel.GLOBAL_SOUTH_LIFT)
            self.assertIn(f"Global South: {word}", " ".join(c.reasons))

    def test_home_market_lift_is_strongest(self):
        self.assertGreater(sel.PH_LIFT[2], sel.SEA_LIFT[2])
        self.assertGreater(sel.SEA_LIFT[2], sel.WIDER_ASIA_LIFT)
        self.assertGreater(sel.SEA_LIFT[2], sel.GLOBAL_SOUTH_LIFT)

    def test_discovery_value_counts_only_for_discovery_tier(self):
        self.assertEqual(self.score(recognition="discovery", discoveryValue=2).lifts, 4)
        self.assertEqual(self.score(recognition="core", discoveryValue=2).lifts, 0)

    def test_affinity_lifts_significant_records_only(self):
        self.assertEqual(self.score(person="Fav").lifts, sel.AFFINITY_LIFT["preferred"])
        self.assertEqual(self.score(person="Thinker").lifts, sel.AFFINITY_LIFT["adjacent"])
        weak = self.score(person="Fav", significance=2)
        self.assertEqual(weak.lifts, 0)
        self.assertIn("no lift", " ".join(weak.reasons))

    def test_affinity_never_overrides_a_more_significant_item(self):
        aff = parse_affinity(json.dumps({"preferred": [{"name": "Fav"}]}))
        ds = dataset(rec("fav", 11, 24, person="Fav", significance=4),
                     rec("major", 11, 25, significance=5, recognition="anchor"))
        self.assertEqual(ids(select_issue(ds, WEEK, affinity=aff))[0], "major")

    def test_affinity_breaks_a_tie(self):
        aff = parse_affinity(json.dumps({"preferred": [{"name": "Fav"}]}))
        ds = dataset(rec("other", 11, 24), rec("fav", 11, 25, person="Fav"))
        s = select_issue(ds, WEEK, affinity=aff)
        self.assertEqual(min(s.picked, key=lambda c: c.pick_order).record.id, "fav")

    def test_disputed_dates_stay_out_even_with_affinity(self):
        aff = parse_affinity(json.dumps({"preferred": [{"name": "Fav"}]}))
        s = select_issue(dataset(rec("fav", 11, 24, person="Fav", significance=5, status="disputed",
                                     approved=False)), WEEK, preview=True, affinity=aff)
        self.assertEqual(excluded(s, "fav"), "status is disputed")

    def test_round_anniversary_bonus(self):
        self.assertEqual([sel.anniversary_bonus(y) for y in (100, 200, 150, 175, 75, 161, 0)], [8, 8, 6, 4, 4, 0, 0])


class CompositionTests(unittest.TestCase):
    def first_pick(self, *records):
        s = select_issue(dataset(*records), WEEK)
        return min(s.picked, key=lambda c: c.pick_order).record.id, s

    def test_first_anchor_is_nudged_in(self):
        rid, _ = self.first_pick(rec("core", 11, 24, significance=4, phRelevance=0),
                                 rec("anchor", 11, 25, significance=4, recognition="anchor"))
        self.assertEqual(rid, "anchor")

    def test_extra_anchors_cost_more_each_time(self):
        anchors = [pub(f"a{i}", 23 + i, significance=4, recognition="anchor") for i in range(4)]
        s = select_issue(dataset(*anchors), WEEK)
        by_order = {c.pick_order: c for c in s.picked}
        self.assertIn("first anchor (+4)", by_order[1].composition_reasons)
        self.assertIn("third anchor (-4)", by_order[3].composition_reasons)
        self.assertIn("fourth anchor (-8)", by_order[4].composition_reasons)

    def test_regional_item_gets_one_nudge_not_a_quota(self):
        s = select_issue(dataset(rec("ph", 11, 24, phRelevance=2), rec("ph2", 11, 25, phRelevance=2)), WEEK)
        nudged = [c for c in s.picked if any("first Philippine" in w for w in c.composition_reasons)]
        self.assertEqual(len(nudged), 1)
        self.assertEqual(sorted(ids(s)), ["ph", "ph2"])  # strong regional weeks keep their strength

    def test_no_regional_candidate_means_none_is_forced(self):
        s = select_issue(dataset(rec("a", 11, 24), rec("b", 11, 25)), WEEK)
        self.assertFalse(any(c.is_regional for c in s.picked))

    def test_event_type_variety(self):
        births = [rec(f"b{i}", 11, 23 + i, significance=4) for i in range(3)]
        s = select_issue(dataset(*births, pub("p", 27, significance=4)), WEEK)
        third_birth = next(c for c in s.picked if c.record.type == "birth" and c.pick_order == 4)
        self.assertIn("third birth (-3)", third_birth.composition_reasons)

    def test_recency_is_soft_not_an_exclusion(self):
        """Last year's lead still leads if it is still the week's strongest item."""
        ds = dataset(pub("jane-eyre", 24, significance=5), rec("b", 11, 25))
        s = select_issue(ds, WEEK, history=History(((dt.date(2025, 11, 24), ("jane-eyre",)),)))
        self.assertEqual(ids(s)[0], "jane-eyre")
        self.assertIn("(-6)", " ".join(s.picked[0].reasons))

    def test_recent_regional_overrepresentation_is_a_small_penalty(self):
        europe = tuple(f"e{i}" for i in range(8))
        recs = [rec(rid, 1, 1 + i) for i, rid in enumerate(europe)]
        hist = History(((dt.date(2026, 11, 16), europe),))
        s = select_issue(dataset(*recs, rec("x", 11, 24)), WEEK, history=hist)
        self.assertEqual(cand(s, "x").history, -sel.OVERREP_PENALTY)

    def test_balance_fields_never_affect_selection(self):
        a = [rec("x", 11, 24), rec("y", 11, 25, language="yo", womenWriter=True, domain="history")]
        b = [rec("x", 11, 24), rec("y", 11, 25, language="en", womenWriter=False)]
        self.assertEqual(ids(select_issue(dataset(*a), WEEK)), ids(select_issue(dataset(*b), WEEK)))


class SourceConfidenceTests(unittest.TestCase):
    WIKI = [{"title": "Wikipedia", "url": "https://en.wikipedia.org/wiki/X", "kind": "reference"}]
    BRIT = [{"title": "Britannica", "url": "https://www.britannica.com/x", "kind": "secondary"}]

    def test_reference_sources_carry_straightforward_facts(self):
        s = select_issue(dataset(rec("x", 11, 24, sources=self.WIKI)), WEEK)
        self.assertEqual(ids(s), ["x"])

    def test_complex_claims_need_a_secondary_or_better_source(self):
        ds = dataset(rec("x", 11, 24, claims="complex", sources=self.WIKI))
        self.assertTrue(any("not publishable" in w for w in ds.warnings))
        self.assertEqual(excluded(select_issue(ds, WEEK), "x"), "complex claim rests on reference sources only")
        ok = dataset(rec("x", 11, 24, claims="complex", sources=self.WIKI + self.BRIT))
        self.assertEqual(ids(select_issue(ok, WEEK)), ["x"])
        self.assertEqual(ok.records[0].source_confidence, "secondary")

    def test_observed_dates_publish_but_must_be_explained(self):
        with self.assertRaises(DatasetError):
            dataset(rec("x", 11, 24, dateBasis="observed", claims="complex", sources=self.BRIT))  # no notes
        with self.assertRaises(DatasetError):
            dataset(rec("x", 11, 24, dateBasis="observed", notes="kept date"))  # not marked complex
        ds = dataset(rec("x", 11, 24, dateBasis="observed", claims="complex", sources=self.BRIT, notes="kept date"))
        self.assertEqual(ids(select_issue(ds, WEEK)), ["x"])


class LinkMatchTests(unittest.TestCase):
    """A reading destination may not drift away from the work it belongs to."""

    def assertProblem(self, record, fragment):
        with self.assertRaises(DatasetError) as ctx:
            dataset(record)
        self.assertTrue(any(fragment in p for p in ctx.exception.problems), ctx.exception.problems)

    def test_event_work_must_be_the_events_work(self):
        def je(link):
            return rec("pub-salt-orchard", 10, 16, type="publication", person="Maria Velasco", work="The Salt Orchard",
                       link=link)

        self.assertProblem(je(book("Harbour Lights", "Maria Velasco")), "event is about")
        self.assertProblem(je(book("The Salt Orchard", "Rosa Velasco")), "does not match")
        dataset(je(book("The Salt Orchard", "Maria Velasco")))

    def test_mentioned_work_must_be_in_the_copy(self):
        copy = {"digest": WORDS_50 + " The night before he wrote Last Letter.", "expanded": None,
                "allowSecondPage": False}
        ok = rec("poet", 3, 14, type="death", person="Tomas Reyes", copy=copy,
                 link=book("Last Letter", "Tomas Reyes", "mentioned_work"))
        dataset(ok)
        self.assertProblem({**ok, "links": [book("The Long Road", "Tomas Reyes", "mentioned_work")]},
                           "does not mention")

    def test_mentioned_as_covers_a_short_form_in_the_copy(self):
        copy = {"digest": WORDS_50 + " He grew up in the town behind Tom Fields.", "expanded": None,
                "allowSecondPage": False}
        dataset(rec("elias", 6, 3, person="Elias Morrow", copy=copy,
                    link=book("The Adventures of Tom Fields", "Elias Morrow", "mentioned_work",
                              mentionedAs="Tom Fields")))

    def test_representative_work_is_only_for_author_entries_by_that_author(self):
        dataset(rec("a", 11, 24, person="Joseph Conrad", link=book("Lord Jim", "Joseph Conrad", "representative_work")))
        self.assertProblem(rec("a", 11, 24, person="Joseph Conrad",
                               link=book("Lord Jim", "Someone Else", "representative_work")), "does not match")
        self.assertProblem(pub("x", 24, link=book("Other", "A", "representative_work")), "author-focused")

    def test_book_links_must_declare_match_author_and_rights(self):
        for missing in ("match", "author", "rights"):
            link = {k: v for k, v in book("Book", "a", "representative_work").items() if k != missing}
            self.assertProblem(rec("a", 1, 1, link=link), missing)

    def test_context_links_take_no_match(self):
        link = {"type": "read-more", "url": "https://example.org", "title": "X", "provider": "Ex", "match": "event_work"}
        self.assertProblem(rec("a", 1, 1, link=link), "do not take a match")

    def test_rights_live_on_the_link(self):
        self.assertProblem(rec("a", 1, 1, rights="public-domain-worldwide"), "rights now belong to the link")


def access(type, market, url, provider, rights="in-copyright"):
    return book("w", "w", type=type, market=market, url=url, provider=provider, rights=rights)


class AccessHierarchyTests(unittest.TestCase):
    """Access before commerce, with a Philippine lens."""

    def pick(self, *links):
        return dataset(pub("w", 24, links=list(links))).records[0].link

    def test_best_tier_wins_whatever_the_listed_order(self):
        intl_shop = access("find-book", "intl", "https://shop.example/w", "Penguin")
        ph_shop = access("find-book", "ph", "https://fullybooked.example/w", "Fully Booked")
        intl_lib = access("borrow", "intl", "https://lib.example/w", "Open Library")
        ph_lib = access("borrow", "ph", "https://nlp.example/w", "National Library of the Philippines")
        free = book("w", "w", url="https://www.gutenberg.org/ebooks/9")
        self.assertEqual(self.pick(intl_shop, ph_shop).provider, "Fully Booked")
        self.assertEqual(self.pick(intl_shop, ph_shop, intl_lib).provider, "Open Library")
        self.assertEqual(self.pick(intl_lib, ph_lib).provider, "National Library of the Philippines")
        self.assertEqual(self.pick(ph_lib, intl_shop, free).type, "read-online")

    def test_us_only_free_text_falls_through_to_borrowing(self):
        us_only = book("w", "w", rights="public-domain-us", url="https://www.gutenberg.org/ebooks/9")
        lib = access("borrow", "intl", "https://lib.example/w", "Open Library")
        rec = dataset(pub("w", 24, links=[us_only, lib])).records[0]
        self.assertEqual(rec.link.type, "borrow")
        self.assertTrue(rec.free_reading_withheld)

    def test_context_links_never_beat_access(self):
        more = {"type": "read-more", "url": "https://www.nobelprize.org/x", "title": "Prize", "provider": "NobelPrize.org"}
        shop = access("find-book", "intl", "https://shop.example/w", "Penguin")
        self.assertEqual(self.pick(more, shop).type, "find-book")
        self.assertEqual(self.pick(more).type, "read-more")

    def test_borrow_and_find_need_a_market(self):
        with self.assertRaises(DatasetError):
            dataset(pub("w", 24, links=[book("w", "w", type="borrow", rights="in-copyright")]))
        with self.assertRaises(DatasetError):
            dataset(pub("w", 24, links=[book("w", "w", market="ph")]))  # free text takes no market

    def test_borrow_label_and_note(self):
        s = select_issue(dataset(pub("w", 24, significance=5, links=[
            access("borrow", "ph", "https://nlp.example/w", "National Library of the Philippines")])), WEEK)
        cta = build_issue(s, NOW)["entries"][0]["cta"]
        self.assertEqual((cta["label"], cta["note"]), ("BORROW", "Borrow via National Library of the Philippines"))

    def test_old_single_link_field_is_refused(self):
        with self.assertRaises(DatasetError):
            dataset({**pub("w", 24), "link": None})


class VisibleSourceTests(unittest.TestCase):
    def web_sources(self, rec_):
        s = select_issue(dataset(rec_), WEEK)
        return [x["url"] for x in build_web(s, build_issue(s, NOW))["entries"][0]["sources"]]

    def test_reading_destination_is_not_listed_again(self):
        gut = "https://www.gutenberg.org/ebooks/11"
        r = pub("alice", 26, significance=5, links=[book("alice", "alice", url=gut)], sources=[
            {"title": "Library", "url": "https://lib.example/alice", "kind": "institutional"},
            {"title": "Project Gutenberg #11", "url": gut + "/", "kind": "reference"}])
        self.assertEqual(self.web_sources(r), ["https://lib.example/alice"])
        self.assertEqual(len(dataset(r).records[0].sources), 2)  # provenance kept

    def test_wikidata_hidden_when_a_readable_source_exists(self):
        r = rec("m", 11, 25, sources=[
            {"title": "Wikidata Q1", "url": "https://www.wikidata.org/wiki/Q1", "kind": "reference"},
            {"title": "Wikipedia", "url": "https://en.wikipedia.org/wiki/M", "kind": "reference"},
            {"title": "Nippon.com", "url": "https://www.nippon.com/m", "kind": "secondary"}])
        self.assertEqual(self.web_sources(r), ["https://www.nippon.com/m", "https://en.wikipedia.org/wiki/M"])

    def test_wikidata_stays_when_it_is_the_only_source(self):
        r = rec("m", 11, 25, sources=[{"title": "Wikidata Q1", "url": "https://www.wikidata.org/wiki/Q1",
                                        "kind": "reference"}])
        self.assertEqual(self.web_sources(r), ["https://www.wikidata.org/wiki/Q1"])


class CtaTests(unittest.TestCase):
    def test_action_vocabulary_never_says_free(self):
        self.assertEqual(sel.CTA_LABELS, {"read-online": "READ ONLINE", "borrow": "BORROW", "find-book": "FIND THE BOOK",
                                          "read-more": "READ MORE", "source": "VIEW SOURCE"})
        self.assertFalse(any("FREE" in label for label in sel.CTA_LABELS.values()))

    def test_reading_screen_names_work_author_and_representative_choice(self):
        r = rec("conrad", 11, 24, person="Joseph Conrad",
                link=book("Lord Jim", "Joseph Conrad", "representative_work", edition="the 1900 text"))
        cta = build_issue(select_issue(dataset(r), WEEK), NOW)["entries"][0]["cta"]
        self.assertEqual(cta["title"], "Lord Jim")
        self.assertTrue(cta["representative"])
        self.assertEqual(cta["edition"], "the 1900 text")


class RolesAndQrTests(unittest.TestCase):
    def test_lead_is_intrinsic_not_lifted(self):
        ds = dataset(pub("major", 24, significance=5, recognition="anchor"),
                     pub("local", 25, significance=4, phRelevance=2, recognition="discovery", discoveryValue=2))
        s = select_issue(ds, WEEK)
        self.assertEqual(next(c for c in s.picked if c.role == "featured").record.id, "major")

    def test_literature_wins_a_tie(self):
        ds = dataset(pub("science", 24, significance=5, domain="science"), pub("novel", 26, significance=5))
        s = select_issue(ds, WEEK)
        self.assertEqual(next(c for c in s.picked if c.role == "featured").record.id, "novel")
        self.assertEqual(min(s.picked, key=lambda c: c.pick_order).record.id, "novel")

    def test_second_lead_needs_significance_five_on_a_round_anniversary(self):
        ds = dataset(pub("a", 24, significance=5), pub("b", 25, significance=5))
        self.assertEqual([c.role for c in select_issue(ds, WEEK).picked], ["featured", "standard"])
        ds = dataset(pub("a", 24, year=1876, significance=5), pub("b", 25, year=1926, significance=5))
        self.assertEqual([c.role for c in select_issue(ds, WEEK).picked], ["featured", "featured"])

    def test_qr_cap(self):
        ds = dataset(*(pub(f"b{i}", 23 + i, link=book(f"b{i}", "Author")) for i in range(4)))
        self.assertEqual(sum(c.qr for c in select_issue(ds, WEEK).picked), sel.MAX_QR)

    def test_us_only_public_domain_gets_no_qr(self):
        ds = dataset(pub("pooh", 24, link=book("pooh", "A. A. Milne", rights="public-domain-us"), significance=5))
        self.assertFalse(select_issue(ds, WEEK).picked[0].qr)

    def test_read_more_and_source_links_never_get_a_device_qr(self):
        for t in ("read-more", "source"):
            link = {"type": t, "url": "https://example.org/x", "title": "X", "provider": "Ex"}
            ds = dataset(rec("x", 11, 27, type="observance", year=2013, person=None, link=link))
            self.assertFalse(select_issue(ds, WEEK).picked[0].qr)


class IssueTests(unittest.TestCase):
    def test_kickers(self):
        ds = dataset(pub("pub", 24, year=1926), rec("born", 11, 25, year=1832),
                     rec("obs", 11, 27, year=2013, type="observance", person=None, area="global"),
                     rec("nobel", 11, 28, year=1988, type="literary-event", label="NOBEL PRIZE"))
        k = {c.record.id: kicker(c) for c in select_issue(ds, WEEK).picked}
        self.assertEqual(k, {"pub": "1926 · 100 YEARS", "born": "BORN 1832", "obs": "OBSERVANCE",
                             "nobel": "NOBEL PRIZE 1988"})

    def test_contents_labels_say_what_happened_in_words(self):
        ds = dataset(pub("pub", 24, year=1926), rec("born", 11, 25, year=1832),
                     rec("obs", 11, 27, year=2013, type="observance", person=None, area="philippines",
                         locality="PH", phRelevance=2),
                     rec("nobel", 11, 28, year=1988, type="literary-event", label="NOBEL PRIZE", person="Mahfouz"))
        issue = build_issue(select_issue(ds, WEEK), NOW)
        got = {e["id"]: (e["contentsLabel"], e["kicker"], e["title"]) for e in issue["entries"]}
        self.assertEqual(got["pub"][0], "PUBLISHED")  # the round-anniversary kicker drops the word; contents keep it
        self.assertEqual(got["born"][0], "BORN")
        self.assertEqual(got["obs"][:2], ("OBSERVANCE · PH", "OBSERVANCE · PH"))
        self.assertEqual(got["nobel"], ("NOBEL PRIZE", "NOBEL PRIZE 1988", "Mahfouz"))

    def test_locality_is_for_national_observances_only(self):
        obs = dict(type="observance", person=None)
        with self.assertRaises(DatasetError):
            dataset(rec("a", 11, 27, area="philippines", **obs))  # national, no locality
        with self.assertRaises(DatasetError):
            dataset(rec("b", 11, 27, area="global", locality="PH", **obs))
        with self.assertRaises(DatasetError):
            dataset(rec("c", 11, 27, locality="GB"))  # an ordinary birth carries no country tag
        with self.assertRaises(DatasetError):
            dataset(rec("d", 11, 27, area="philippines", locality="ph", **obs))
        self.assertEqual(dataset(rec("e", 11, 27, area="philippines", locality="PH", **obs)).records[0].locality, "PH")

    def test_locality_does_not_disturb_other_fingerprints(self):
        r = rec("a", 11, 27, approved=False)
        self.assertNotIn("locality", r)
        self.assertEqual(fingerprint(r), fingerprint(dict(r)))
        self.assertNotEqual(fingerprint({**r, "locality": "PH"}), fingerprint(r))

    def test_typeset_quotes(self):
        self.assertEqual(typeset('Benigno "Ninoy" Aquino\'s ("x")'), "Benigno “Ninoy” Aquino’s (“x”)")
        self.assertEqual(typeset("Dekada '70 and 'so'"), "Dekada ’70 and ‘so’")

    def test_payload(self):
        copy = {"digest": WORDS_50, "expanded": "Longer.", "allowSecondPage": True}
        issue = build_issue(select_issue(dataset(rec("a", 11, 24, copy=copy)), WEEK), NOW, "https://dogear.example")
        self.assertEqual(issue["editionUrl"], "https://dogear.example/2026-11-23/")
        self.assertEqual(issue["datelineShort"], "23–29 NOV 2026")
        self.assertTrue(issue["entries"][0]["secondPage"])
        self.assertEqual(issue["entries"][0]["body"], WORDS_50)


    def test_web_edition_expands_and_keeps_rights_rule(self):
        pooh = pub("pooh", 24, link=book("pooh", "A. A. Milne", rights="public-domain-us"), significance=5,
                   copy={"digest": WORDS_50, "expanded": "The fuller story.", "allowSecondPage": False})
        s = select_issue(dataset(pooh), WEEK)
        web = build_web(s, build_issue(s, NOW))
        self.assertEqual(web["entries"][0]["links"], [])
        self.assertTrue(web["entries"][0]["freeReadingWithheld"])
        self.assertNotIn("READ ONLINE", render_web(web))

    def test_web_edition_shows_read_more_links(self):
        link = {"type": "read-more", "url": "https://www.nobelprize.org/x", "title": "Nobel 1986", "provider": "NobelPrize.org"}
        s = select_issue(dataset(rec("n", 11, 24, type="literary-event", link=link)), WEEK)
        self.assertIn("READ MORE", render_web(build_web(s, build_issue(s, NOW))))


class StageTests(unittest.TestCase):
    def test_stage_points_current_at_a_hash_checked_issue(self):
        import hashlib
        with tempfile.TemporaryDirectory() as tmp:
            src, dest = Path(tmp) / "out", Path(tmp) / "stage"
            (src / "web" / "2026-11-23").mkdir(parents=True)
            body = json.dumps({"issueId": "dogear-2026-11-23", "entries": []}).encode()
            (src / "dogear-2026-11-23.json").write_bytes(body)
            (src / "web" / "2026-11-23" / "index.html").write_text("<p>web</p>")
            manifest = stage(src, "dogear-2026-11-23", dest)
            self.assertEqual(manifest["issuePath"], "issues/dogear-2026-11-23.json")
            self.assertEqual(manifest["issueSha256"], hashlib.sha256(body).hexdigest())
            self.assertEqual(json.loads((dest / "current.json").read_text()), manifest)
            self.assertEqual((dest / "issues" / "dogear-2026-11-23.json").read_bytes(), body)
            self.assertTrue((dest / "2026-11-23" / "index.html").is_file())
            with self.assertRaises(ValueError):
                stage(src, "../etc", dest)


class BalanceTests(unittest.TestCase):
    def test_counts_and_empty_traditions_are_shown(self):
        ds = dataset(rec("a", 1, 1, area="philippines"), rec("b", 1, 2, area="africa", language="yo", womenWriter=True))
        b = balance(ds.records)
        self.assertEqual(b["tradition"]["Philippine"], 1)
        self.assertEqual(b["women writers"]["woman writer"], 1)
        self.assertIn("Latin American", render_balance(ds))

    def test_rolling_windows_from_issue_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            for i in range(10):
                monday = dt.date(2026, 8, 3) + dt.timedelta(weeks=i)
                payload = {"entries": [{"id": "a"}, {"id": "b"}]}
                (Path(tmp) / f"dogear-{monday.isoformat()}.json").write_text(json.dumps(payload))
            (Path(tmp) / "dogear-2026-08-03.audit.json").write_text("{}")  # ignored
            issues = load_issues(Path(tmp))
            self.assertEqual(len(issues), 10)
            text = render_balance(dataset(rec("a", 1, 1), rec("b", 1, 2)), issues=issues)
            self.assertIn("== last 8 issues", text)
            self.assertIn("== last 12 issues (only 10 available)", text)


if __name__ == "__main__":
    unittest.main()
