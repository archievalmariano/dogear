"""Staging/production separation (dogear/synthetic.py): every declared marker detects
synthetic data, and a revision already in the registry can never cross targets
through rollback or restore (Codex review of 7f8fe9e, findings 1 and 2)."""

from __future__ import annotations

import datetime as dt
import json
import unittest

from dogear.dataset import fingerprint
from dogear.publish.publisher import Publisher
from dogear.publish.registry import Refused
from dogear.publish.staging import StageRefused
from dogear.synthetic import APPROVER, MARKERS, TAG, markers, non_synthetic_records, synthetic_records

try:
    from tests.test_dogear import rec
    from tests.test_publish import BASE, W0, W1, W2, Fixture, at, week_records
except ImportError:
    from test_dogear import rec
    from test_publish import BASE, W0, W1, W2, Fixture, at, week_records


def synthetic_week(monday: dt.date, n: int = 3) -> list:
    out = []
    for i in range(n):
        r = rec(f"staging-{monday.isoformat()}-{i}", monday.month, monday.day + i, significance=4, approved=False,
                type="publication", person=f"Staging Writer {i}", work=f"Staging Work {monday} {i}", tags=[TAG],
                sources=[{"title": "Example", "url": "https://example.org/", "kind": "secondary"}],
                links=[{"type": "read-more", "url": "https://example.org/", "title": "T", "provider": "example.org"}])
        r["approval"] = {"by": APPROVER, "on": "2026-10-08", "fingerprint": fingerprint(r)}
        out.append(r)
    return out


class MarkerTests(unittest.TestCase):
    def test_each_declared_marker_alone_is_detected(self):
        """Finding 1: a record whose only sign is example.org links was not detected."""
        plain = rec("plain-1", 3, 1, type="publication", person="A Person", work="A Work",
                    links=[{"type": "read-more", "url": "https://library.test/a", "title": "T", "provider": "P"}])
        self.assertEqual(synthetic_records(json.dumps({"records": [plain]})), [])
        alone = {
            "id": {"id": "staging-x"},
            "tag": {"tags": [TAG]},
            "approver": {"approval": {"by": APPROVER, "on": "2026-10-08", "fingerprint": "x"}},
            "names": {"person": "Staging Writer 1"},
            "links": {"sources": [{"title": "S", "url": "https://example.org/", "kind": "secondary"}],
                      "links": []},
        }
        self.assertEqual(set(alone), set(MARKERS))  # the tests cover exactly the declared markers
        for marker, change in alone.items():
            marked = dict(plain, **change)
            self.assertEqual([k for k, v in markers(marked).items() if v], [marker])
            self.assertEqual(synthetic_records(json.dumps({"records": [marked]})), [marked["id"]], marker)

    def test_links_alone_reveal_synthetic_data_and_production_refuses_it(self):
        """The case Codex reproduced: only the sources and links are synthetic."""
        r = rec("ordinary-1", 10, 6, significance=5, type="publication", person="A Person", work="A Work",
                sources=[{"title": "Example", "url": "https://example.org/", "kind": "secondary"}],
                links=[{"type": "read-more", "url": "https://example.org/", "title": "T", "provider": "P"}])
        self.assertEqual(synthetic_records(json.dumps({"records": [r]})), ["ordinary-1"])
        f = Fixture(self, [r], at(dt.date(2026, 10, 5), 9), mode="production")
        with self.assertRaises(StageRefused) as caught:
            f.pub.launch(first_publication=True)
        self.assertIn("synthetic", str(caught.exception))

    def test_staging_needs_the_full_shape(self):
        full = synthetic_week(W0, 1)[0]
        self.assertEqual(non_synthetic_records(json.dumps({"records": [full]})), [])
        for change in ({"tags": []}, {"person": "A Person"}, {"work": "A Work"}, {"id": "other-1"},
                       {"links": [{"type": "read-more", "url": "https://library.test/", "title": "T", "provider": "P"}]},
                       {"approval": {"by": "Editor", "on": "2026-10-08", "fingerprint": "x"}}):
            self.assertEqual(len(non_synthetic_records(json.dumps({"records": [dict(full, **change)]}))), 1, change)


class RevisionSeparationTests(unittest.TestCase):
    """Finding 2: rollback and restore show a revision that already exists. They must
    re-check the target's separation on that revision, not only its approval and bytes."""

    def published(self, weeks: list) -> Fixture:
        """W0, W1, W2 published in test mode with no separation (as a mixed history
        could only arise by mistake), from these per-week records."""
        f = Fixture(self, sum(weeks, []), at(W0, 9))
        f.pub.launch(first_publication=True)
        for monday in (W1, W2):
            f.clock.t = at(monday, 6, 2)
            self.assertEqual(f.run().promote().status, "published")
        return f

    def as_target(self, f: Fixture, separation: str) -> Publisher:
        pub = f.run()
        pub.separation = separation
        return pub

    def test_production_refuses_to_show_a_synthetic_revision(self):
        f = self.published([week_records(W0, "real0"), synthetic_week(W1), week_records(W2, "real2")])
        with self.assertRaises(Refused) as caught:  # rollback to the synthetic week
            self.as_target(f, "production").rollback(W1, None, "test")
        self.assertIn("may not be shown here", str(caught.exception))
        self.assertEqual(f.registry()["current"], W2.isoformat())
        self.assertEqual(self.as_target(f, "production").rollback(W0, None, "a real week").status, "published")
        with self.assertRaises(Refused):  # restore the synthetic week
            self.as_target(f, "production").restore(W1, 0)
        self.assertEqual(f.registry()["current"], W0.isoformat())
        self.assertEqual(self.as_target(f, "production").restore(W2, 0).status, "published")  # same target: fine
        self.assertEqual(f.registry()["current"], W2.isoformat())

    def test_production_mode_is_always_separated(self):
        f = self.published([week_records(W0, "real0"), synthetic_week(W1), week_records(W2, "real2")])
        f.mode = "production"
        pub = f.run()
        self.assertEqual(pub.separation, "production")
        with self.assertRaises(Refused):
            pub.rollback(W1, None, "test")

    def test_staging_refuses_to_show_a_real_revision(self):
        f = self.published([synthetic_week(W0), week_records(W1, "real1"), synthetic_week(W2)])
        with self.assertRaises(Refused) as caught:  # rollback to the real week
            self.as_target(f, "staging").rollback(W1, None, "test")
        self.assertIn("may not be shown here", str(caught.exception))
        self.assertEqual(self.as_target(f, "staging").rollback(W0, None, "a synthetic week").status, "published")
        with self.assertRaises(Refused):  # restore the real week
            self.as_target(f, "staging").restore(W1, 0)
        self.assertEqual(f.registry()["current"], W0.isoformat())
        self.assertEqual(self.as_target(f, "staging").restore(W2, 0).status, "published")  # same target: fine
        self.assertEqual(f.registry()["current"], W2.isoformat())

    def test_one_marker_on_the_stored_revision_is_enough_for_production(self):
        """A revision whose records carry only the synthetic id prefix (approved by an
        editor, ordinary names and links) still may not be shown in production. The
        refusal must come from separation, not eligibility (which still passes)."""
        quiet = [dict(r, id=r["id"].replace("only", "staging-only")) for r in week_records(W1, "only")]
        for r in quiet:
            r["approval"] = {"by": "Editor", "on": "2026-10-08", "fingerprint": fingerprint(dict(r, approval=None))}
        f = self.published([week_records(W0, "real0"), quiet, week_records(W2, "real2")])
        with self.assertRaises(Refused) as caught:
            self.as_target(f, "production").rollback(W1, None, "test")
        self.assertIn("may not be shown here", str(caught.exception))
        self.assertEqual(self.as_target(f, None).rollback(W1, None, "unseparated").status, "published")  # eligible

    def test_the_guard_before_every_new_write_also_separates(self):
        """Defence in depth for launch, promote and correct: the guard re-run just before
        the registry write refuses content from the wrong side, even where the earlier
        stage-level checks were somehow passed."""
        from dogear.publish.staging import stage
        from dogear.week import week_starting
        f = Fixture(self, week_records(W0, "real0") + synthetic_week(W1), at(W0, 9))
        real = stage(week_starting(W0), f.pub.inputs, f.policy, [], at(W0, 9), f.checks, "test")
        fake = stage(week_starting(W1), f.pub.inputs, f.policy, [], at(W1, 9), f.checks, "test")
        guard = lambda pub, s: pub._eligibility_guard(s.provenance, s.issue_bytes)()
        self.assertIsNone(guard(self.as_target(f, "production"), real))
        self.assertIn("synthetic", guard(self.as_target(f, "production"), fake))
        self.assertIsNone(guard(self.as_target(f, "staging"), fake))
        self.assertIn("not synthetic", guard(self.as_target(f, "staging"), real))
        self.assertIsNone(guard(self.as_target(f, None), fake))

    def test_the_separation_values(self):
        f = Fixture(self, week_records(W0, "r"), at(W0, 9))
        with self.assertRaises(ValueError):
            Publisher(f.store, f.remote.checkout(), f.pub.inputs, f.policy, f.checks, f.clock, mode="production",
                      separation="staging")
        self.assertIsNone(f.run().separation)  # unit tests and local rehearsals: none unless asked
        self.assertTrue(BASE)
