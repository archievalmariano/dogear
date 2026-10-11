"""The production firmware gate on quiet issues (PUBLISHING §22).

- `firmware.capability`: only a synced contract whose verification record passes
  every check (exact 8 packages, exact checks, all PASS, bound hashes) authorises
  quiet issues, and it decides from local files with no network.
- Every publisher path that writes the registry is gated: a write may not make a
  quiet revision visible or current without that capability, whichever operation
  (launch, promote, correct, rollback, restore, resume) or policy; operations
  that never write the registry are proven not to."""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import io
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import types
import unittest
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from dogear.publish import cli as publish_cli
from dogear.publish import firmware
from dogear.publish import registry as regmod
from dogear.publish.policy import PublicationPolicy
from dogear.publish.publisher import (Aborted, NeedsHuman, NotReady, Publisher, RegistryGate,
                                      UnguardedRegistryWrite, shown_revisions)
from dogear.publish.registry import REGISTRY_KEY, Refused
from dogear.publish import quiet_history
from dogear.publish.edition import quiet_exclusion_problems, revision_problems
from dogear.publish.pubdata import CorruptState, PubData
from dogear.publish.policy import TEST_POLICY
from dogear.publish.staging import Checks, LinkReport, StageInputs, StageRefused, shown_link_problems
from dogear.publish.staging import stage as stage_week
from dogear.publish.store import MemoryStore, StoreError
from dogear.week import MANILA, week_starting
from dogear.quiet import QuietSource

try:
    from tests.firmware_fixture import FIXTURES, SCHEMA, SCHEMA_BYTES, SyncedTree
    from tests.write_boundary import (REPO, SCANNED, STATEMENTS, changed_regions, covered, dependencies, discover,
                                      regions, scan_repo, scan_source, surface, unreviewed)
    from tests.write_boundary_review import NOT_CRITICAL, PROTECTED, REVIEWED
    from tests.test_publish import W0, W1, W2, W3, Fixture, at
    from tests.test_quiet_week import QUIET, line_for, promote, published, regular_week, stored_issue
except ImportError:
    from firmware_fixture import FIXTURES, SCHEMA, SCHEMA_BYTES, SyncedTree
    from write_boundary import (REPO, SCANNED, STATEMENTS, changed_regions, covered, dependencies, discover,
                                regions, scan_repo, scan_source, surface, unreviewed)
    from write_boundary_review import NOT_CRITICAL, PROTECTED, REVIEWED
    from test_publish import W0, W1, W2, W3, Fixture, at
    from test_quiet_week import QUIET, line_for, promote, published, regular_week, stored_issue

HOLD_PREVIOUS = PublicationPolicy(slot5_bar=42, sparse_week="publish", empty_week="hold-previous")


class Tree(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, True))
        self.tree = SyncedTree(self.root).write()

    def cap(self):
        return firmware.capability(self.root)


class CapabilityTests(Tree):
    def assert_refused(self, why: str = ""):
        c = self.cap()
        self.assertFalse(c.ok, "capability should be refused")
        if why:
            self.assertIn(why, c.reason)

    def test_a_synced_verified_release_authorises_quiet_issues(self):
        self.assertEqual(self.cap(), firmware.Capability(True, None))

    def test_the_repository_contract_does_not_authorise_quiet_issues_yet(self):
        c = firmware.capability()  # the real data/: not synced from a verified release
        self.assertFalse(c.ok)
        self.assertIn("no release block", c.reason)

    def test_missing_or_old_style_files_refuse(self):
        (self.root / firmware.CONTRACT).unlink()
        self.assert_refused()
        self.tree.write()
        self.tree.edit_contract(lambda c: c.pop("release"))  # old-style, like today's contract
        self.assert_refused("no release block")
        self.tree.write()
        self.tree.record_path().unlink()
        self.assert_refused()
        self.tree.write()
        (self.root / firmware.SCHEMA_COPY).unlink()
        self.assert_refused()

    def test_an_edited_record_or_schema_refuses(self):
        self.tree.edit_record(lambda r: r.update(approvedOn="2026-10-24"), rehash=False)
        self.assert_refused("not the synced one")
        self.tree.write()
        (self.root / firmware.SCHEMA_COPY).write_bytes((self.root / firmware.SCHEMA_COPY).read_bytes() + b" ")
        self.assert_refused("schema is not the synced one")

    def test_duplicate_keys_refuse(self):
        data = self.tree.record_path().read_text().replace('"approvedBy": "owner",',
                                                           '"approvedBy": "owner", "approvedBy": "owner",', 1)
        self.tree.record_path().write_text(data)
        self.tree.edit_contract(lambda c: c["release"].update(verificationSha256=firmware.sha256(data.encode())))
        self.assert_refused("duplicate key")

    def test_packages_must_be_exactly_the_eight_once_each_in_order(self):
        pk = lambda r: r["acceptance"]["packages"]  # noqa: E731
        for mutate in (lambda r: pk(r).pop(1),
                       lambda r: pk(r).append(dict(pk(r)[0])),
                       lambda r: pk(r).insert(3, dict(pk(r)[3])),
                       lambda r: pk(r).reverse(),
                       lambda r: pk(r)[1].update(package="x4-d")):
            with self.subTest(mutate=mutate):
                # acceptanceSha256 recomputed: only the package rule itself can refuse it
                self.tree.edit_record(lambda r: (mutate(r), r.update(
                    acceptanceSha256=firmware.sha256(firmware.canonical(r["acceptance"])))))
                self.assert_refused()

    def test_checks_must_be_exactly_each_packages_checks_each_pass(self):
        gpd = lambda r: r["acceptance"]["packages"][7]["checks"]  # noqa: E731
        for mutate in (lambda r: gpd(r).pop("dogear-v3-q1-cover"),
                       lambda r: gpd(r).update({firmware.E3_CHECK: "PASS"}),  # physical E3: never a check
                       lambda r: gpd(r).update({"dogear-v2-cache-503": "FAIL"}),
                       lambda r: gpd(r).update({"dogear-v3-q2-pages": "pass"}),
                       lambda r: gpd(r).update({"extra": "PASS"}),
                       lambda r: r["acceptance"]["packages"][1]["checks"].pop("dogear-v3-q2-navigation"),
                       lambda r: r["acceptance"]["packages"][2]["checks"].update({"dogear-opens": "PASS"})):
            with self.subTest(mutate=mutate):
                self.tree.edit_record(lambda r: (mutate(r), r.update(
                    acceptanceSha256=firmware.sha256(firmware.canonical(r["acceptance"])))))
                self.assert_refused()

    def test_host_e3_evidence_is_mandatory_and_bound_to_the_release(self):
        e3 = firmware.E3_CHECK
        ev = lambda r: r["acceptance"]["hostCovered"][e3]  # noqa: E731

        def repin(r):  # the evidence asset and acceptanceSha256 follow the change: only the rule can refuse
            if "hostCovered" in r["acceptance"] and e3 in r["acceptance"]["hostCovered"]:
                r["release"]["assets"][firmware.E3_EVIDENCE] = firmware.sha256(firmware.canonical(ev(r)))
            r["acceptanceSha256"] = firmware.sha256(firmware.canonical(r["acceptance"]))

        mutations = {
            "hostCovered deleted": lambda r: r["acceptance"].pop("hostCovered"),
            "E3 entry deleted": lambda r: r["acceptance"]["hostCovered"].pop(e3),
            "a test failed": lambda r: ev(r)["tests"].update({firmware.E3_HOST_TESTS[0]: "FAIL"}),
            "a test missing": lambda r: ev(r)["tests"].pop(firmware.E3_HOST_TESTS[-1]),
            "a test skipped": lambda r: ev(r).update(skipped=[firmware.E3_HOST_TESTS[5]]),
            "another commit": lambda r: ev(r).update(commit="0" * 40),
            "another run": lambda r: ev(r).update(candidateRun=4243),
            "another definition": lambda r: ev(r).update(definitionSha256="0" * 64),
            "another repository": lambda r: ev(r).update(repository="x/y"),
            "physical E3 substituted": lambda r: (r["acceptance"].pop("hostCovered"), [
                p["checks"].update({e3: "PASS"}) for p in r["acceptance"]["packages"] if p["package"] in
                ("x4-gd", "x4pro-gpd")]),
        }
        for name, mutate in mutations.items():
            with self.subTest(case=name):
                self.tree.edit_record(lambda r: (mutate(r), repin(r)))
                self.assert_refused()
        # The evidence must be the release's own asset.
        self.tree.edit_record(lambda r: (r["release"]["assets"].update({firmware.E3_EVIDENCE: "0" * 64}),
                                         r.update(acceptanceSha256=firmware.sha256(
                                             firmware.canonical(r["acceptance"])))))
        self.assert_refused("E3 host evidence")
        self.tree.write()
        self.assertEqual(self.cap(), firmware.Capability(True, None))

    def test_the_schema_requires_hostCovered_exactly(self):
        e3 = firmware.E3_CHECK
        mutations = {
            "hostCovered deleted": lambda s: s.pop("hostCovered"),
            "a required test removed": lambda s: s["hostCovered"][e3]["tests"].pop(),
            "a test added": lambda s: s["hostCovered"][e3]["tests"].append("DogearPages.NavigationIsCircular"),
            "the policy reworded": lambda s: s["hostCovered"][e3].update(policy="optional"),
            "physical E3 back on a flagship": lambda s: (s["checks"].update({e3: "physical"}),
                                                          s["packages"]["x4pro-gpd"]["checks"].append(e3)),
        }
        for name, mutate in mutations.items():
            with self.subTest(case=name):
                schema = json.loads(SCHEMA_BYTES)
                mutate(schema)
                with self.assertRaises(firmware.Invalid):
                    firmware.check_schema(schema)
        # A vendored copy without hostCovered, pinned as if synced, still refuses.
        schema = json.loads(SCHEMA_BYTES)
        schema.pop("hostCovered")
        data = json.dumps(schema).encode()
        (self.root / firmware.SCHEMA_COPY).write_bytes(data)
        self.tree.edit_contract(lambda c: c["release"].update(acceptanceSchemaSha256=firmware.sha256(data)))
        self.assert_refused("hostCovered")

    def test_the_record_must_bind_the_tag_run_sums_and_assets(self):
        rel = lambda r: r["release"]  # noqa: E731
        for mutate, why in ((lambda r: r.update(acceptanceSha256="0" * 64), "acceptanceSha256"),
                            (lambda r: rel(r).update(candidateRun=4243), "candidateRun"),
                            (lambda r: rel(r).update(sumsSha256="0" * 64), "sumsSha256"),
                            (lambda r: rel(r)["assets"].update({"firmware-x4-gd.bin": "0" * 64}), "digest"),
                            (lambda r: rel(r)["assets"].update({"firmware.bin": "0" * 64}), "byte copy"),
                            (lambda r: rel(r)["assets"].pop("firmware-x4pro-pd.factory.bin"), "assets"),
                            (lambda r: rel(r)["assets"].update({"notes.txt": "0" * 64}), "assets"),
                            (lambda r: rel(r).update(tagObject=rel(r)["commit"]), "annotated"),
                            (lambda r: r["acceptance"].update(candidateRun=4243), "candidateRun"),
                            (lambda r: r["ota"][1].update(slotVerified=False), "slot"),
                            (lambda r: r["ota"].pop(), "ota"),
                            (lambda r: r.update(approvedBy="claude"), "owner")):
            with self.subTest(why=why):
                self.tree.edit_record(mutate)
                self.assert_refused()

    def test_the_contract_must_match_its_record(self):
        for mutate in (lambda c: c["release"].update(candidateRun=1),
                       lambda c: c["release"].update(sumsSha256="0" * 64),
                       lambda c: c["release"].update(commit="0" * 40),
                       lambda c: c["release"].update(acceptanceSha256="0" * 64),
                       lambda c: c.update(firmwareCommit="3d9d9e56"),
                       lambda c: c["release"].update(verification="data/elsewhere.json"),
                       lambda c: c["release"].update(extra=1)):
            with self.subTest(mutate=mutate):
                self.tree.write()
                self.tree.edit_contract(mutate)
                self.assert_refused()

    def test_schema_2_firmware_or_a_non_authorising_record_refuses(self):
        self.tree.write(limits=dict(self.tree.limits, kIssueSchemaMax=2))
        self.assert_refused("kIssueSchemaMax")  # the record still claims 3
        self.tree.edit_record(lambda r: r["authorizes"].update(kIssueSchemaMax=2))
        self.tree.write(self.tree.record, limits=dict(self.tree.limits, kIssueSchemaMax=2))
        self.assert_refused()
        record = json.loads(self.tree.record_path().read_text())
        record["authorizes"] = {"kIssueSchemaMax": 2, "quietWeek": False}
        self.tree.write(record, limits=dict(self.tree.limits, kIssueSchemaMax=2))
        self.assert_refused("does not authorise")

    def test_it_never_uses_the_network(self):
        def no(*a, **k):
            raise AssertionError("network or subprocess used")
        with mock.patch.object(socket, "socket", no), mock.patch.object(socket, "create_connection", no), \
                mock.patch.object(urllib.request, "urlopen", no), mock.patch.object(subprocess, "run", no), \
                mock.patch.object(subprocess, "Popen", no):
            self.assertTrue(self.cap().ok)
            self.tree.edit_record(lambda r: r.update(approvedOn="x"), rehash=False)
            self.assertFalse(self.cap().ok)


class TagVectorTests(unittest.TestCase):
    """The firmware tag rules, on vectors shared with candidate.py (test_vendored
    runs the firmware's own parser over the same file)."""

    def test_vectors(self):
        vectors = json.loads((FIXTURES / "tag-vectors.json").read_text())
        self.assertGreater(len(vectors["cases"]), 15)
        for case in vectors["cases"]:
            with self.subTest(case=case["name"]):
                if case["ok"]:
                    firmware.parse_tag_message(case["message"], SCHEMA["packageOrder"])
                else:
                    with self.assertRaises(firmware.Invalid):
                        firmware.parse_tag_message(case["message"], SCHEMA["packageOrder"])


# ---- the publisher -------------------------------------------------------------


class Spy:
    """Registry writes (and the presentation guard state) as they happen."""

    def __init__(self, f: Fixture):
        self.f, self.pub, self.writes, self.on_put = f, None, [], None
        real = f.store.put

        def put(key, data, **kw):
            if self.on_put:
                self.on_put(key)
            if key == REGISTRY_KEY:
                # Structural: no registry write unless the quiet-presentation guard
                # has just passed for exactly these bytes.
                assert self.pub is not None and self.pub._presentation_checked == regmod.sha256(data), \
                    "registry written without the quiet-presentation guard passing for these bytes"
                self.writes.append(regmod.sha256(data))
            return real(key, data, **kw)
        f.store.put = put

    def run(self) -> Publisher:
        self.pub = self.f.run()
        return self.pub


class GuardBase(Tree):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(firmware, "ROOT", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.f = Fixture(self, regular_week(W0, "a") + regular_week(W1, "b") + regular_week(W3, "d"), at(W0, 9),
                         mode="production", policy=QUIET)
        self.spy = Spy(self.f)
        self.assertEqual(self.spy.run().launch(first_publication=True).status, "published")
        self.promote(W1)
        self.assertEqual(self.promote(W2).status, "published")  # W2 has no records: quiet
        self.assertEqual(stored_issue(self.f, W2)["schemaVersion"], 3)

    def promote(self, monday):
        self.f.clock.t = at(monday, 6, 2)
        return self.spy.run().promote()

    def later(self, monday, hh=10):
        self.f.clock.t = at(monday, hh)
        return self.spy.run()

    def revoke(self):
        """Capability lost: the record no longer matches what was synced."""
        self.tree.edit_record(lambda r: r.update(approvedOn="2026-10-24"), rehash=False)
        self.assertFalse(firmware.capability().ok)

    def assert_refused(self, call, exc=Refused):
        before = self.f.store.objects[REGISTRY_KEY].data
        writes = len(self.spy.writes)
        with self.assertRaises(exc) as caught:
            call()
        self.assertEqual(self.f.store.objects[REGISTRY_KEY].data, before, "the registry must not change")
        self.assertEqual(len(self.spy.writes), writes, "no registry write may happen")
        self.assertIsNone(self.f.run().pubdata.read_pending(), "no pending transaction may remain")
        return str(caught.exception)


class QuietVisibilityTests(GuardBase):
    def test_with_the_capability_every_path_works(self):
        self.assertEqual(self.promote(W3).status, "published")
        self.assertEqual(self.later(W3).rollback(W2, None, "check").status, "published")  # quiet current, held
        self.assertEqual(self.later(W3, 11).resume().status, "published")
        self.assertEqual(self.later(W3, 12).rollback(W1, None, "check").status, "published")
        self.assertEqual(self.later(W3, 13).restore(W2, 0).status, "published")

    def test_restore_of_a_quiet_revision_is_refused(self):
        self.assertEqual(self.later(W2).rollback(W1, None, "check").status, "published")  # moving away
        self.revoke()
        why = self.assert_refused(lambda: self.later(W2, 11).restore(W2, 0))
        self.assertIn("quiet issue", why)

    def test_rollback_to_a_quiet_week_is_refused(self):
        self.promote(W3)
        self.revoke()
        self.assert_refused(lambda: self.later(W3).rollback(W2, None, "check"))

    def test_rollback_within_a_week_to_a_quiet_revision_is_refused(self):
        # W2: quiet rev 0, then a regular correction (records arrive), then capability lost.
        self.f.write(self.f.records + regular_week(W2, "c"))
        self.assertEqual(self.later(W2).correct(W2, 0, "records arrived").status, "published")
        self.assertEqual(stored_issue(self.f, W2)["schemaVersion"], 2)
        self.revoke()
        self.assert_refused(lambda: self.later(W2, 11).rollback(W2, 0, "back"))

    def test_resume_with_a_quiet_current_week_is_refused(self):
        self.promote(W3)
        self.assertEqual(self.later(W3).rollback(W2, None, "check").status, "published")
        self.revoke()
        self.assert_refused(lambda: self.later(W3, 11).resume())

    def test_promote_launch_and_correct_refuse_quiet_week_without_it(self):
        self.revoke()
        self.f.clock.t = at(W3, 6, 2)
        self.assertIn("quiet-week", self.assert_refused(lambda: self.spy.run().promote(), NotReady))
        self.assert_refused(lambda: self.later(W2).correct(W2, 0, "again"), NotReady)
        self.assert_refused(lambda: self.later(W2).stage(W3 + (W3 - W2)), NotReady)

    def test_the_registry_write_itself_is_gated_even_past_the_preflight(self):
        """No bypass: with `_ready` disabled, promote and correct still cannot show a
        quiet revision, because every registry write runs the guard."""
        f = Fixture(self, regular_week(W0, "a") + regular_week(W1, "b"), at(W0, 9), mode="production",
                    policy=QUIET)
        spy = Spy(f)
        spy.run().launch(first_publication=True)
        f.clock.t = at(W1, 6, 2)
        spy.run().promote()
        self.revoke()
        with mock.patch.object(Publisher, "_ready", lambda self: None):
            f.clock.t = at(W2, 6, 2)
            before = f.store.objects[REGISTRY_KEY].data
            with self.assertRaises(Aborted) as caught:
                spy.run().promote()  # W2 is empty: a quiet issue
            self.assertIn("quiet issue", str(caught.exception))
            self.assertEqual(f.store.objects[REGISTRY_KEY].data, before)
            self.assertIsNone(f.run().pubdata.read_pending())
        # correct a quiet week past the preflight
        self.tree.write()
        f.clock.t = at(W2, 6, 5)  # a later run (the aborted attempt's transaction id is spent)
        self.assertEqual(spy.run().promote().status, "published")  # quiet W2, with the capability
        self.revoke()
        with mock.patch.object(Publisher, "_ready", lambda self: None):
            f.clock.t = at(W2, 12)
            before = f.store.objects[REGISTRY_KEY].data
            with self.assertRaises(Aborted) as caught:
                spy.run().correct(W2, 0, "regenerate")  # a new quiet revision of W2
            self.assertIn("quiet issue", str(caught.exception))
            self.assertEqual(f.store.objects[REGISTRY_KEY].data, before)
            self.assertIsNone(f.run().pubdata.read_pending())

    def test_a_capability_lost_mid_transaction_stops_the_write(self):
        f = Fixture(self, regular_week(W0, "a") + regular_week(W1, "b"), at(W0, 9), mode="production",
                    policy=QUIET)
        spy = Spy(f)
        spy.run().launch(first_publication=True)
        f.clock.t = at(W1, 6, 2)
        spy.run().promote()

        def lose(key):
            if key.startswith("issues/") and firmware.capability().ok:
                self.revoke()  # capable at the preflight, lost while the artifacts upload
        spy.on_put = lose
        f.clock.t = at(W2, 6, 2)
        before = f.store.objects[REGISTRY_KEY].data
        with self.assertRaises(Aborted) as caught:
            spy.run().promote()  # W2 is empty: a quiet issue
        self.assertIn("quiet issue", str(caught.exception))
        self.assertFalse(firmware.capability().ok)
        self.assertEqual(f.store.objects[REGISTRY_KEY].data, before)
        self.assertIsNone(f.run().pubdata.read_pending())
        aborted = [h for h in f.history() if h.get("status") == "aborted"]
        self.assertTrue(aborted and "quiet issue" in aborted[-1]["reason"])

    def test_the_gate_holds_under_any_policy(self):
        self.assertEqual(self.later(W2).rollback(W1, None, "check").status, "published")
        self.f.policy = HOLD_PREVIOUS
        self.revoke()
        self.assert_refused(lambda: self.later(W2, 11).restore(W2, 0))
        self.tree.write()
        self.assertEqual(self.later(W2, 12).restore(W2, 0).status, "published")

    def test_moving_away_from_a_quiet_issue_is_always_allowed(self):
        self.promote(W3)
        self.assertEqual(self.later(W3).rollback(W2, None, "check").status, "published")  # quiet current
        self.revoke()
        self.assertEqual(self.later(W3, 11).rollback(W1, None, "away").status, "published")
        self.assertEqual(self.f.registry()["current"], W1.isoformat())

    def test_disagreeing_provenance_and_issue_refuse(self):
        self.assertEqual(self.later(W2).rollback(W1, None, "check").status, "published")
        reg = self.f.registry()
        rev = reg["weeks"][W2.isoformat()]["revisions"][0]
        pub = self.later(W2, 11)
        self.assertEqual(pub._revision_is_quiet(W2.isoformat(), rev, None, {}), (True, None))
        forged = json.dumps(dict(json.loads(self.f.store.objects[rev["issueKey"]].data), schemaVersion=2)).encode()
        self.assertIn("issue object", pub._revision_is_quiet(W2.isoformat(), rev, None, {rev["issueKey"]: forged})[1])

    def test_edition_and_schema_must_agree(self):
        pub = self.later(W2, 11)
        from dogear.publish.staging import provenance_bytes
        for edition, schema, want in (("quiet", 3, (True, None)), ("regular", 2, (False, None)), (None, 2, (False, None)),
                                      ("quiet", 2, "disagree"), ("regular", 3, "disagree"), (None, 3, "disagree"),
                                      ("quiet", True, "disagree")):
            with self.subTest(edition=edition, schema=schema):
                prov = {"edition": edition} if edition else {}
                issue = json.dumps({"schemaVersion": schema}).encode()
                rev = {"txn": "t-new", "rev": 0, "provenanceSha256": regmod.sha256(provenance_bytes(prov)),
                       "issueKey": "issues/x.json", "issueSha256": regmod.sha256(issue)}
                got = pub._revision_is_quiet("2026-11-16", rev, {"txn": "t-new", "provenance": prov},
                                             {"issues/x.json": issue})
                if want == "disagree":
                    self.assertIn("disagree", got[1])
                else:
                    self.assertEqual(got, want)

    def test_shown_revisions(self):
        reg = self.f.registry()
        self.assertEqual(shown_revisions(None, reg), {(w, e["active"]) for w, e in reg["weeks"].items()})
        self.assertEqual(shown_revisions(reg, reg), {(reg["current"], reg["weeks"][reg["current"]]["active"])})


class NeverWritesTests(GuardBase):
    """Operations that never write the registry, even while production shows a
    quiet issue it may not: they report it instead."""

    def setUp(self):
        super().setUp()
        self.revoke()  # current is quiet W2

    def test_status_reports_it(self):
        writes = len(self.spy.writes)
        status = self.later(W2).status()
        self.assertFalse(status["firmware"]["quietCapable"])
        self.assertIn("production shows a quiet issue", status["firmware"]["quietShownWithoutCapability"])
        self.assertEqual(len(self.spy.writes), writes)

    def test_reconcile_stops_for_a_human_and_writes_nothing(self):
        writes = len(self.spy.writes)
        with self.assertRaises(NeedsHuman) as caught:
            self.later(W2).reconcile(detect=True)
        self.assertIn("roll back to a regular issue", str(caught.exception))
        self.later(W2).reconcile()  # as the first step of other operations: no detection
        self.assertEqual(len(self.spy.writes), writes)

    def test_abandon_reset_preflight_and_attest_write_nothing(self):
        writes = len(self.spy.writes)
        with self.assertRaises(Refused):
            self.later(W2).abandon("nope")
        problems = self.later(W2).reset_preflight(W2.isoformat())
        self.assertTrue(any("production shows a quiet issue" in p for p in problems))
        with self.assertRaises(Exception):
            self.later(W2).attest_subjects("nope", [], None)
        self.assertEqual(len(self.spy.writes), writes)


class OperationClassificationTests(unittest.TestCase):
    """Every publish operation is classified. A new one fails here until it is
    added to one side, and the registry-writing side is gated (above)."""

    WRITES = {"launch", "promote", "correct", "rollback", "restore", "resume"}  # via _transact
    PREPARES = {"stage", "stage-next"}  # staged/ only, behind _ready()
    NEVER_WRITES = {"status", "reconcile", "abandon", "reset-preflight", "attest-subjects"}

    def test_every_operation_is_classified(self):
        parser = argparse.ArgumentParser()
        publish_cli.add_parser(parser.add_subparsers(dest="cmd"))
        publish = parser._subparsers._group_actions[0].choices["publish"]
        ops = next(a for a in publish._actions if isinstance(a, argparse._SubParsersAction)).choices
        self.assertEqual(set(ops), self.WRITES | self.PREPARES | self.NEVER_WRITES)

    def test_the_override_is_refused_in_production_and_has_no_cli_flag(self):
        f = Fixture(self, regular_week(W0, "a"), at(W0, 9), policy=QUIET)
        for supported in (True, False):
            with self.assertRaises(ValueError):
                Publisher(f.store, f.remote.checkout(), f.pub.inputs, QUIET, f.checks, f.clock, mode="production",
                          quiet_supported=supported)
        cli_source = Path(publish_cli.__file__).read_text()
        self.assertNotIn("quiet_supported", cli_source)
        self.assertNotIn("quiet-supported", cli_source)


# ---- the registry-write boundary ------------------------------------------------

# Codex's demonstrated bypass: reflect the raw store out of the gate, save its
# put, and write the registry with the saved method.
BYPASS = """
from dogear.publish.registry import REGISTRY_KEY


def sneak(pub, data):
    raw = getattr(pub.store, "_RegistryGate__store")
    writer = raw.put
    writer(REGISTRY_KEY, data, content_type="application/json")
"""
# Codex's second demonstrated bypass (no finding from the D6 scanner): qualified
# built-ins and computed names fetch the gate's slot descriptor from its class,
# __get__ applies it to the gate (stepping around RegistryGate.__getattribute__,
# which only sees lookups on the instance), and the saved put writes the registry.
QUALIFIED_BYPASS = """
import builtins


def sneak(pub, data):
    slot = builtins.vars(type(pub.store))["_st" + "ore"]
    raw = slot.__get__(pub.store)
    writer = builtins.getattr(raw, "p" + "ut")
    writer("publication" + ".json", data, content_type="application/json")
"""
# Codex's third probe, appended to publisher.py: a protected name rebound inside a
# top-level compound statement. At run time it disables registry-key recognition.
NESTED_REBINDING = """
if True:
    def is_registry_key(key):
        return False
"""
# Codex's fourth probe, appended to publisher.py: a walrus in a lambda default runs
# in the module scope and rebinds a protected name.
LAMBDA_REBINDING = """
_probe = (lambda x=(is_registry_key := lambda key: False): None)
"""
# A prohibited writer in a nested tools module.
NESTED_WRITER = """from dogear.publish.registry import REGISTRY_KEY


def write(store, data):
    store.put(REGISTRY_KEY, data, content_type="application/json")
"""
BYPASS_VARIANTS = {
    "object.__getattribute__": 'def f(g, d):\n    w = object.__getattribute__(g, "_store").put\n    w("publication.json", d)\n',
    "getattr write method": 'def f(raw, d):\n    w = getattr(raw, "put")\n    w("publication.json", d)\n',
    "computed getattr": 'def f(raw, d):\n    w = getattr(raw, "p" + "ut")\n    w("publication.json", d)\n',
    "partial": 'import functools\n\ndef f(raw, d):\n    functools.partial(raw.put, "publication.json")(d)\n',
    "methodcaller": 'from operator import methodcaller\n\ndef f(raw, d):\n    methodcaller("put", "publication.json", d)(raw)\n',
    "saved at init": 'class P:\n    def __init__(self, store):\n        self.w = store.put\n',
    "gc": 'import gc\n\ndef f(g):\n    return gc.get_referents(g)\n',
    "closure": 'def f(g):\n    return g.put.__func__.__closure__\n',
    "slot via __class__": 'def f(g):\n    return g.__class__._store.__get__(g)\n',
    # qualified and aliased reflection
    "builtins.getattr": 'import builtins\n\ndef f(raw, d):\n    builtins.getattr(raw, "p" + "ut")("publication.json", d)\n',
    "builtins.vars": 'import builtins\n\ndef f(g):\n    return builtins.vars(type(g))\n',
    "builtins module aliased": 'import builtins as b\n\ndef f(g):\n    return b.vars(type(g))\n',
    "builtin imported under another name": 'from builtins import getattr as fetch\n\ndef f(raw):\n    return fetch(raw, "p" + "ut")\n',
    "__builtins__ mapping": 'def f(raw):\n    return __builtins__["get" + "attr"](raw, "p" + "ut")\n',
    "a reflective builtin saved, not called": 'fetch = getattr\n\ndef f(raw):\n    return fetch(raw, "p" + "ut")\n',
    "builtins re-exported by another module": 'import dataclasses\n\ndef f(raw):\n    return dataclasses.builtins.getattr(raw, "p" + "ut")\n',
    "type() with three arguments": 'def f(base):\n    return type("S", (base,), {})\n',
    # descriptor lookups on the class
    "slot descriptor from the class __dict__": 'def f(g):\n    return type(g).__dict__["_st" + "ore"].__get__(g)\n',
    "descriptor via Formatter.get_field": 'from string import Formatter\n\ndef f(g):\n    slot = Formatter().get_field("0._st" + "ore", (type(g),), {})[0]\n    return slot.__get__(g)\n',
    "descriptor via attrgetter": 'import operator\n\ndef f(g):\n    return operator.attrgetter("_st" + "ore")(type(g))\n',
    "descriptor applied by the class mro": 'def f(g):\n    return [vars(c) for c in type(g).__mro__]\n',
    # frames, tracebacks and hooks hold the raw store as a local
    "frame locals": 'import sys\n\ndef f():\n    return sys._getframe(1).f_locals\n',
    "traceback frames": 'def f(g):\n    try:\n        g.put("x", object())\n    except Exception as e:\n        return e.__traceback__.tb_next.tb_frame.f_locals\n',
    "a profile hook": 'import sys\n\ndef f(hook):\n    sys.setprofile(hook)\n',
    "a python in another process": 'import os\n\ndef f(code):\n    os.system("python3 -c " + code)\n',
    # changing the surface from outside, without touching its source
    "rebind the key test": 'from dogear.publish import publisher\n\ndef f():\n    publisher.is_registry_key = lambda key: False\n',
    "rebind the guard": 'from dogear.publish.publisher import Publisher\n\ndef f(fake):\n    Publisher._quiet_presentation_guard = fake\n',
    "rebind the capability root": 'from dogear.publish import firmware\n\ndef f(tmp):\n    firmware.ROOT = tmp\n',
    "delete the capability": 'from dogear.publish import firmware\n\ndef f():\n    del firmware.capability\n',
    "replace the publisher's store": 'def f(pub, raw):\n    pub.store = raw\n',
    "subclass the publisher": 'from dogear.publish.publisher import Publisher\n\n\nclass P(Publisher):\n    pass\n',
    "a module nobody imported before": 'import ast\n',
}


class RegistryWriteBoundaryTests(GuardBase):
    """The registry is written only by _transact, through the gate, right after
    the quiet-presentation guard passed for exactly those bytes: checked on the
    syntax tree (every write reference and every reflection) and at run time."""

    def assert_reviewed(self, found: set) -> None:
        differs = unreviewed(found, REVIEWED)
        if differs:
            self.fail("a storage write or reflection changed: review it, then update write_boundary_review.REVIEWED\n"
                      + differs)

    def test_every_storage_write_and_reflection_is_reviewed(self):
        self.assert_reviewed(scan_repo())

    def test_a_second_occurrence_inside_a_reviewed_scope_is_a_new_finding(self):
        path = "dogear/publish/publisher.py"
        source = (REPO / path).read_text(encoding="utf-8")
        gate_put = "    def put(self, key: str, data: bytes, **kwargs):\n"
        raw_put = '        return object.__getattribute__(self, "_store").put(key, data, **kwargs)\n'
        api = '_GATE_API = frozenset({"get", "put", "allow_once"})\n'
        self.assertEqual((source.count(gate_put), source.count(raw_put), source.count(api)), (1, 1, 1))
        # Codex's probe: a second raw-store .put expression inside RegistryGate.put, an approved scope.
        codex = source.replace(gate_put, gate_put + '        object.__getattribute__(self, "_store").put(key, data, **kwargs)\n')
        old_identity = lambda found: {f[:4] for f in found}  # noqa: E731 -- (file, scope, kind, detail)
        self.assertEqual(old_identity(scan_repo({path: codex})), old_identity(REVIEWED))  # what used to collapse
        probes = {
            "Codex: a second raw-store put in RegistryGate.put": codex,
            "the same statement again, later in the scope": source.replace(raw_put, raw_put + raw_put),
            "the approved write moved above the gate's check": source.replace(raw_put, "").replace(
                gate_put, gate_put + raw_put),
            "the gate's API names bound a second time": source + "\n" + api,
        }
        for name, probe in probes.items():
            with self.subTest(probe=name):
                found = scan_repo({path: probe})
                self.assertNotEqual(found, REVIEWED)
                self.assertTrue(found - REVIEWED, "the new occurrence collapsed into an approval")
                self.assertEqual({f.file for f in found - REVIEWED}, {path})
                with self.assertRaises(AssertionError):
                    self.assert_reviewed(found)
        new = scan_repo({path: codex}) - REVIEWED
        self.assertIn(("RegistryGate.put", "write-call", "object.__getattribute__(self, '_store').put", "body[0]"),
                      {(f.scope, f.kind, f.detail, f.at) for f in new})
        self.assertEqual({f.scope for f in new}, {"RegistryGate.put"})
        # Ordinary movement elsewhere in the module does not disturb any approval.
        moved = ("import hmac  # an unrelated import (one the protected code does not use)\n\n\ndef unrelated():\n    return 1\n\n\n"
                 + source.replace(api, "UNRELATED = 1\n" + api).replace(
                     "    __slots__ = (", "    ALSO_UNRELATED = 2\n    __slots__ = ("))
        self.assert_reviewed(scan_repo({path: moved}))

    def test_nested_tools_modules_are_discovered_and_scanned(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for top in SCANNED:
                shutil.copytree(REPO / top, root / top, ignore=shutil.ignore_patterns("__pycache__"))
            self.assert_reviewed(scan_repo(root=root))  # the copy, unchanged
            nested = root / "tools" / "subdir" / "example.py"
            nested.parent.mkdir()
            nested.write_text(NESTED_WRITER, encoding="utf-8")
            self.assertIn("tools/subdir/example.py", discover(root))
            new = scan_repo(root=root) - REVIEWED
            self.assertEqual({(f.file, f.scope, f.kind, f.detail) for f in new},
                             {("tools/subdir/example.py", "write", "write-call", "store.put")})
            with self.assertRaises(AssertionError):
                self.assert_reviewed(scan_repo(root=root))
            # Any depth, and a python script without the .py suffix.
            nested.unlink()
            deeper = root / "tools" / "subdir" / "deeper" / "again" / "example.py"
            deeper.parent.mkdir(parents=True)
            deeper.write_text(NESTED_WRITER, encoding="utf-8")
            script = root / "tools" / "subdir" / "write-registry"
            script.write_text("#!/usr/bin/env python3\n" + NESTED_WRITER, encoding="utf-8")
            self.assertEqual({f.file for f in scan_repo(root=root) - REVIEWED},
                             {"tools/subdir/deeper/again/example.py", "tools/subdir/write-registry"})
            # A symlink is not followed silently: the scan refuses.
            (root / "tools" / "linked").symlink_to(deeper.parent, target_is_directory=True)
            with self.assertRaisesRegex(AssertionError, "symlink"):
                scan_repo(root=root)

    def test_a_symlinked_root_is_refused(self):
        for top in SCANNED:
            with self.subTest(root=top), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                for other in SCANNED:
                    shutil.copytree(REPO / other, root / other, ignore=shutil.ignore_patterns("__pycache__"))
                self.assert_reviewed(scan_repo(root=root))
                # The root itself becomes a symlink to an identical tree elsewhere.
                (root / top).rename(root / f"{top}.real")
                (root / top).symlink_to(root / f"{top}.real", target_is_directory=True)
                with self.assertRaisesRegex(AssertionError, f"^{top} is a symlink"):
                    discover(root)
                with self.assertRaisesRegex(AssertionError, f"^{top} is a symlink"):
                    scan_repo(root=root)
                (root / top).unlink()
                with self.assertRaisesRegex(AssertionError, f"^{top} is missing"):  # nor may it be absent
                    discover(root)

    def test_codexs_qualified_bypass_is_flagged_on_each_step(self):
        # Run time: this one is live (a plain store behind the gate, as in production).
        # The slot descriptor comes from the class, so the instance's __getattribute__
        # never sees the lookup; the source checks are what stop it.
        raw = MemoryStore()
        raw.put(REGISTRY_KEY, b"{}", content_type="application/json")
        gate = RegistryGate(raw)
        with self.assertRaises(UnguardedRegistryWrite):  # the ordinary write is refused
            gate.put(REGISTRY_KEY, b"forged", content_type="application/json")
        namespace = {}
        exec(compile(QUALIFIED_BYPASS, "bypass", "exec"), namespace)  # noqa: S102 -- the probe, in the test only
        namespace["sneak"](SimpleNamespace(store=gate), b"forged")
        self.assertEqual(raw.objects[REGISTRY_KEY].data, b"forged")
        # Structure: every step of it is a finding, so it fails the review anywhere.
        found = {f[2:4] for f in scan_source("dogear/publish/sneaky.py", QUALIFIED_BYPASS)}
        for step in (("import", "builtins"),
                     ("reflection", "builtins"),
                     ("dynamic", "builtins.vars(type(pub.store))"),
                     ("reflection", "slot.__get__(pub.store)"),
                     ("dynamic", "builtins.getattr(raw, 'p' + 'ut')")):
            self.assertIn(step, found)
        for path in ("dogear/publish/sneaky.py", "tools/sub/sneaky.py", "dogear/publish/cli.py"):
            with self.subTest(path=path):
                source = QUALIFIED_BYPASS if path != "dogear/publish/cli.py" else (
                    (REPO / path).read_text(encoding="utf-8") + QUALIFIED_BYPASS.replace("import builtins\n", ""))
                with self.assertRaises(AssertionError):
                    self.assert_reviewed(scan_repo({path: source}))

    def test_codexs_bypass_is_refused_at_run_time_and_flagged_by_the_scanner(self):
        pub = self.later(W2, 11)
        before = self.f.store.objects[REGISTRY_KEY].data
        # Run time: the private lookup is refused, so the bypass cannot even start.
        namespace = {}
        exec(compile(BYPASS, "bypass", "exec"), namespace)  # noqa: S102 -- the probe, in the test only
        with self.assertRaises(AttributeError):
            namespace["sneak"](pub, regmod.dumps(dict(self.f.registry(), current=W1.isoformat())))
        for name in ("_RegistryGate__store", "_store", "_allowed", "__dict__", "__class__", "__slots__",
                     "__getattribute__", "__reduce_ex__"):
            with self.subTest(name=name), self.assertRaises(AttributeError):
                getattr(pub.store, name)
        with self.assertRaises(AttributeError):
            pub.store.put = lambda *a, **k: None  # noqa: B010
        # Nor can the authorisation or the raw store be set from outside (self-authorising).
        for name, value in (("_allowed", regmod.sha256(before)), ("_store", self.f.store)):
            with self.subTest(set=name), self.assertRaises(AttributeError):
                setattr(pub.store, name, value)
        with self.assertRaises(AttributeError):
            delattr(pub.store, "_allowed")
        with self.assertRaises(UnguardedRegistryWrite):  # still unauthorised
            pub.store.put(REGISTRY_KEY, before, content_type="application/json")
        self.assertEqual(self.f.store.objects[REGISTRY_KEY].data, before)
        # Structure: the same code in any module is flagged, on each of its steps.
        found = {f[:4] for f in scan_source("dogear/publish/sneaky.py", BYPASS)}
        self.assertIn(("dogear/publish/sneaky.py", "sneak", "dynamic",
                       "getattr(pub.store, '_RegistryGate__store')"), found)
        self.assertIn(("dogear/publish/sneaky.py", "sneak", "private-name", "_RegistryGate__store"), found)
        self.assertIn(("dogear/publish/sneaky.py", "sneak", "write-ref", "raw.put"), found)  # saved, not called
        self.assertNotEqual(scan_repo({"dogear/publish/sneaky.py": BYPASS}), REVIEWED)

    def test_every_variant_of_the_bypass_is_flagged(self):
        for name, source in BYPASS_VARIANTS.items():
            with self.subTest(variant=name):
                found = scan_source("dogear/publish/sneaky.py", source)
                self.assertTrue(found, f"{name} was not flagged")
                self.assertNotEqual(scan_repo({"dogear/publish/sneaky.py": source}), REVIEWED)
                # ... and inside the most-reviewed module too, appended to the publisher.
                publisher = (REPO / "dogear/publish/publisher.py").read_text(encoding="utf-8")
                with self.assertRaises(AssertionError):
                    self.assert_reviewed(scan_repo({"dogear/publish/publisher.py": publisher + "\n\n" + source}))

    def test_the_publisher_holds_the_store_only_through_the_gate(self):
        pub = self.later(W2, 11)
        self.assertIsInstance(pub.store, RegistryGate)
        for name, value in vars(pub).items():
            self.assertIsNot(value, self.f.store, f"Publisher.{name} is the raw store")
            self.assertIsNot(getattr(value, "__self__", None), self.f.store, f"Publisher.{name} is a raw store method")
        with self.assertRaises(AttributeError):
            pub.store._request  # noqa: B018 -- nothing else of the store is reachable
        with self.assertRaises(TypeError):
            vars(pub.store)  # no __dict__: the raw store is not an attribute to find

    def test_a_registry_write_in_any_spelling_is_refused_outside_transact(self):
        pub = self.later(W2, 11)
        before = self.f.store.objects[REGISTRY_KEY].data
        alias = "".join(["publication", ".", "json"])
        for key in (REGISTRY_KEY, alias, "/publication.json", "./publication.json", "PUBLICATION.JSON",
                    " publication.json", "x/../publication.json"):
            with self.subTest(key=key), self.assertRaises(UnguardedRegistryWrite):
                pub.store.put(key, before, content_type="application/json")
        with self.assertRaises(UnguardedRegistryWrite):
            getattr(pub.store, "put")(REGISTRY_KEY, before, content_type="application/json")
        self.assertEqual(self.f.store.objects[REGISTRY_KEY].data, before)

    def test_an_alias_writer_in_a_publisher_method_is_refused(self):
        class Sneaky(Publisher):
            def sneak(self):
                key = REGISTRY_KEY  # an alias, not the literal call the old test matched
                reg, _ = self._read_registry()
                return self.store.put(key, regmod.dumps(dict(reg, current=W2.isoformat())),
                                      content_type="application/json")
        pub = self.later(W2, 11)
        sneaky = Sneaky(self.f.store, pub.pubdata, pub.inputs, pub.policy, pub.checks, pub.clock, mode="production")
        before = self.f.store.objects[REGISTRY_KEY].data
        with self.assertRaises(UnguardedRegistryWrite):
            sneaky.sneak()
        self.assertEqual(self.f.store.objects[REGISTRY_KEY].data, before)

    def test_an_authorisation_is_for_one_write_of_exactly_those_bytes_and_key(self):
        pub = self.later(W2, 11)
        before = self.f.store.objects[REGISTRY_KEY].data
        pub.store.allow_once(regmod.sha256(before))
        with self.assertRaises(UnguardedRegistryWrite):  # other bytes
            pub.store.put(REGISTRY_KEY, before + b" ", content_type="application/json")
        with self.assertRaises(UnguardedRegistryWrite):  # spent by the refused attempt
            pub.store.put(REGISTRY_KEY, before, content_type="application/json")
        pub.store.allow_once(regmod.sha256(before))
        with self.assertRaises(UnguardedRegistryWrite):  # another spelling of the key
            pub.store.put("/publication.json", before, content_type="application/json")
        self.assertEqual(self.f.store.objects[REGISTRY_KEY].data, before)

    def test_transact_writes_only_after_the_presentation_guard_passed_for_these_bytes(self):
        self.revoke()
        self.tree.write()

        def guard_that_never_ran(pub, *a, **k):
            return lambda: None  # passes, but never checked or recorded the bytes
        with mock.patch.object(Publisher, "_quiet_presentation_guard", guard_that_never_ran):
            before = self.f.store.objects[REGISTRY_KEY].data
            with self.assertRaises(UnguardedRegistryWrite):
                self.later(W2, 11).rollback(W1, None, "check")
            self.assertEqual(self.f.store.objects[REGISTRY_KEY].data, before)


class SourceReviewGuardTests(unittest.TestCase):
    """The critical surface (write_boundary.SURFACE) is fingerprinted from its
    syntax tree: any change to it fails until a reviewer records the new
    fingerprint, whatever the change does and whether or not the scan knows the
    trick. Changes outside it, and comments or formatting inside it, do not."""

    PUBLISHER = "dogear/publish/publisher.py"

    def source(self, rel: str) -> str:
        return (REPO / rel).read_text(encoding="utf-8")

    def changed(self, rel: str, old: str, new: str) -> str:
        source = self.source(rel)
        self.assertEqual(source.count(old), 1, old)
        return changed_regions(surface({rel: source.replace(old, new)}), PROTECTED)

    def test_the_critical_surface_matches_its_review(self):
        differs = changed_regions(surface(), PROTECTED)
        if differs:
            self.fail("the critical surface changed: review it, then record the new fingerprints in "
                      "write_boundary_review.PROTECTED\n" + differs)

    def test_a_change_the_scan_cannot_see_still_needs_review(self):
        # Each changes what the registry write does, with no new scanner finding at all.
        probes = (
            (self.PUBLISHER, ".lower() == REGISTRY_KEY.lower()", " == REGISTRY_KEY", "is_registry_key"),
            (self.PUBLISHER, "            if self._presentation_checked != intended:",
             "            if self._presentation_checked != intended and False:", "Publisher._transact"),
            (self.PUBLISHER, '_GATE_API = frozenset({"get", "put", "allow_once"})',
             '_GATE_API = frozenset({"get", "put", "allow_once", "_st" + "ore"})', "_GATE_API"),
            ("dogear/publish/firmware.py", '        require(not failed, f"{name}: {failed} not PASS")\n', "", ""),
            ("dogear/publish/registry.py", 'REGISTRY_KEY = "publication.json"', 'REGISTRY_KEY = "publication.JSON"',
             ""),
        )
        for rel, old, new, region in probes:
            with self.subTest(region=f"{rel} {region}"):
                source = self.source(rel)
                self.assertEqual(source.count(old), 1, old)
                probe = source.replace(old, new)
                if region != "_GATE_API":  # (that one adds a string the scan does see)
                    self.assertEqual(scan_repo({rel: probe}), REVIEWED,
                                     "the scan sees this one; the probe must be invisible to it")
                differs = changed_regions(surface({rel: probe}), PROTECTED)
                self.assertIn(repr((rel, region)), differs)

    def test_weakening_the_scan_or_its_regressions_needs_review(self):
        boundary = "tests/write_boundary.py"
        for old, new in (('"__name__", "__main__"', '"__get__", "__name__", "__main__"'),  # __get__ benign
                         ('    ("dogear/publish/firmware.py", ""),\n', ""),  # the surface shrinks
                         ('"executable",', ""),
                         ("        if os.path.islink(base):", "        if False:")):
            with self.subTest(change=new or old):
                self.assertIn(repr((boundary, "")), self.changed(boundary, old, new))
        tests = "tests/test_firmware_capability.py"
        self.assertIn(repr((tests, "RegistryWriteBoundaryTests")),
                      self.changed(tests, "    def test_" + "codexs_qualified_bypass_is_flagged_on_each_step(self):",
                                   "    def _retired(self):"))  # (spelled in two parts: not matching itself)
        self.assertIn(repr((tests, "BYPASS_VARIANTS")),
                      self.changed(tests, '    "builtins re-exported ' + 'by another module": ', '    "x": '))

    def test_unrelated_changes_and_formatting_do_not_need_review(self):
        source = self.source(self.PUBLISHER)
        probes = {
            "a change to an unprotected method": source.replace(
                "    def status(self) -> dict:\n", "    def status(self) -> dict:\n        _ = 1\n"),
            "a comment and reflowed lines inside the gate": source.replace(
                "    def allow_once(self, registry_sha: str) -> None:\n",
                "    def allow_once(self,\n                   registry_sha: str) -> None:  # one write\n"),
            "a new unprotected function": source + "\n\ndef unrelated():\n    return 1\n",
        }
        for name, probe in probes.items():
            with self.subTest(probe=name):
                self.assertNotEqual(probe, source)
                self.assertEqual(changed_regions(surface({self.PUBLISHER: probe}), PROTECTED), "")
        cli = self.source("dogear/publish/cli.py")
        self.assertEqual(changed_regions(surface({"dogear/publish/cli.py": cli + "\nX = 1\n"}), PROTECTED), "")

    def test_a_region_must_be_unambiguous_and_present(self):
        source = self.source(self.PUBLISHER)
        twice = source + "\n\ndef is_registry_key(key):\n    return False\n"
        with self.assertRaisesRegex(AssertionError, "more than once"):
            surface({self.PUBLISHER: twice})
        gone = source.replace("    def _upload(self", "    def _upload_renamed(self")
        with self.assertRaisesRegex(AssertionError, "Publisher._upload is gone"):
            surface({self.PUBLISHER: gone})
        self.assertIn("UnguardedRegistryWrite", regions(source)[0])


class BindingAndSurfaceTests(unittest.TestCase):
    """A name the protected code depends on is bound once, directly; any other
    binding of it anywhere in a protected module's execution flow (however
    nested, in whatever form) is a scan finding and changes the module's
    "<bindings>" fingerprint. And everything the surface uses is on it or
    reviewed as not critical."""

    PUBLISHER = "dogear/publish/publisher.py"
    CLASS_END = "\n\ndef shown_revisions("  # the end of the Publisher class body

    def setUp(self):
        self.source = (REPO / self.PUBLISHER).read_text(encoding="utf-8")
        self.assertEqual(self.source.count(self.CLASS_END), 1)

    def in_publisher_class(self, code: str) -> str:
        return self.source.replace(self.CLASS_END, "\n" + code + self.CLASS_END)

    def assert_rebinding(self, probe: str, name: str, scope: str = "") -> None:
        new = scan_repo({self.PUBLISHER: probe}) - REVIEWED
        self.assertIn((scope, "rebinding", name), {(f.scope, f.kind, f.detail.split(" ")[0]) for f in new})
        self.assertTrue(unreviewed(scan_repo({self.PUBLISHER: probe}), REVIEWED))
        try:
            differs = changed_regions(surface({self.PUBLISHER: probe}), PROTECTED)
        except AssertionError as refused:  # a second plain definition: the regions refuse it outright
            self.assertIn("defined more than once", str(refused))
        else:
            self.assertIn(repr((self.PUBLISHER, "<bindings>")), differs)

    def load(self, source: str):
        """The publisher module built from `source`, as a separate module."""
        module = types.ModuleType("dogear.publish._probe")
        module.__package__ = "dogear.publish"
        sys.modules[module.__name__] = module
        try:
            exec(compile(source, "publisher-probe", "exec"), module.__dict__)  # noqa: S102 -- the probe, in the test only
        finally:
            del sys.modules[module.__name__]
        return module

    def test_codexs_nested_rebinding_is_found_and_would_bypass_the_gate(self):
        probe = self.source + NESTED_REBINDING
        new = scan_repo({self.PUBLISHER: probe}) - REVIEWED
        self.assertEqual({(f.file, f.scope, f.kind, f.detail, f.at) for f in new},
                         {(self.PUBLISHER, "", "rebinding", "is_registry_key (def)", "-/body[0]")})
        self.assertTrue(unreviewed(scan_repo({self.PUBLISHER: probe}), REVIEWED))  # the review fails
        self.assertIn(repr((self.PUBLISHER, "<bindings>")), changed_regions(surface({self.PUBLISHER: probe}), PROTECTED))
        # Without the checks it would work: the probed module's gate lets a forged registry through.
        for source, refused in ((self.source, True), (probe, False)):
            gate_module = self.load(source)
            raw = MemoryStore()
            raw.put(REGISTRY_KEY, b"{}", content_type="application/json")
            gate = gate_module.RegistryGate(raw)
            if refused:
                with self.assertRaises(gate_module.UnguardedRegistryWrite):
                    gate.put(REGISTRY_KEY, b"forged", content_type="application/json")
                self.assertEqual(raw.objects[REGISTRY_KEY].data, b"{}")
            else:
                gate.put(REGISTRY_KEY, b"forged", content_type="application/json")
                self.assertEqual(raw.objects[REGISTRY_KEY].data, b"forged")

    def test_codexs_lambda_default_rebinding_is_found_and_would_bypass_the_gate(self):
        probe = self.source + LAMBDA_REBINDING
        new = scan_repo({self.PUBLISHER: probe}) - REVIEWED
        self.assertIn(("", "rebinding", "is_registry_key (walrus)"), {(f.scope, f.kind, f.detail) for f in new})
        self.assertTrue(unreviewed(scan_repo({self.PUBLISHER: probe}), REVIEWED))  # the review fails
        self.assertIn(repr((self.PUBLISHER, "<bindings>")), changed_regions(surface({self.PUBLISHER: probe}), PROTECTED))
        # Without the checks it would work: the default ran at import and replaced the key test.
        gate_module = self.load(probe)
        raw = MemoryStore()
        raw.put(REGISTRY_KEY, b"{}", content_type="application/json")
        gate_module.RegistryGate(raw).put(REGISTRY_KEY, b"forged", content_type="application/json")
        self.assertEqual(raw.objects[REGISTRY_KEY].data, b"forged")
        # Keyword-only and nested lambda defaults too; a walrus in a lambda's body is its own scope.
        for code in ("_probe = (lambda *, x=(is_registry_key := lambda key: False): None)\n",
                     "_probe = (lambda a=(lambda b=(is_registry_key := len): b): a)\n",
                     "def _f(x=(lambda y=(is_registry_key := len): y)):\n    return x\n"):
            with self.subTest(code=code):
                self.assert_rebinding(self.source + "\n" + code, "is_registry_key")
        body_only = self.source + "\n_probe = (lambda: (is_registry_key := len))\n"
        self.assertEqual(unreviewed(scan_repo({self.PUBLISHER: body_only}), REVIEWED), "")
        body_module = self.load(body_only)
        with self.assertRaises(body_module.UnguardedRegistryWrite):  # and at run time it rebinds nothing
            body_module.RegistryGate(raw).put(REGISTRY_KEY, b"x", content_type="application/json")

    def test_every_binding_form_is_found_however_nested(self):
        forms = {
            "def in try": ("try:\n    def is_registry_key(key):\n        return False\nexcept Exception:\n    pass\n",
                           "is_registry_key"),
            "def in an except handler": ("try:\n    pass\nexcept Exception:\n    def shown_revisions(b, a):\n"
                                         "        return set()\n", "shown_revisions"),
            "with ... as": ("import contextlib\nwith contextlib.nullcontext(len) as is_registry_key:\n    pass\n",
                            "is_registry_key"),
            "for target": ("for is_registry_key in (len,):\n    pass\n", "is_registry_key"),
            "while body": ("while True:\n    is_registry_key = len\n    break\n", "is_registry_key"),
            "import alias": ("if True:\n    from os.path import isabs as is_registry_key\n", "is_registry_key"),
            "star import": ("if True:\n    from os.path import *\n", "*"),
            "destructuring": ("_x, is_registry_key = 1, len\n", "is_registry_key"),
            "walrus in a comprehension": ("_y = [is_registry_key := f for f in (len,)]\n", "is_registry_key"),
            "augmented assignment": ("REGISTRY_KEY += '.bak'\n", "REGISTRY_KEY"),
            "annotated, nested": ("if True:\n    REGISTRY_KEY: str = 'x'\n", "REGISTRY_KEY"),
            "del": ("if False:\n    del shown_revisions\n", "shown_revisions"),
            "global in a function": ("def _sneak():\n    global shown_revisions\n    shown_revisions = None\n",
                                     "shown_revisions"),
            "a builtin the gate uses": ("if True:\n    frozenset = set\n", "frozenset"),
            "a module the gate uses": ("if True:\n    import os.path as posixpath\n", "posixpath"),
            "a class with the gate's name": ("if True:\n    class RegistryGate:\n        pass\n", "RegistryGate"),
        }
        if sys.version_info >= (3, 10):
            forms["match capture"] = ("match len:\n    case is_registry_key:\n        pass\n", "is_registry_key")
        for form, (code, name) in forms.items():
            with self.subTest(form=form):
                self.assert_rebinding(self.source + "\n" + code, name)
        # Inside the Publisher class body: a protected method replaced, however nested.
        members = {
            "a guard redefined in a nested block": ("    if True:\n        def _week_guard(self, week):\n"
                                                    "            return lambda: None\n", "_week_guard"),
            "a guard bound again": ("    _eligibility_guard = None\n", "_eligibility_guard"),
            "an attribute the protected methods use": ("    try:\n        store = None\n    finally:\n"
                                                       "        pass\n", "store"),
        }
        for form, (code, name) in members.items():
            with self.subTest(form=form):
                self.assert_rebinding(self.in_publisher_class(code), name, "Publisher")

    def test_unrelated_nested_bindings_are_not_findings(self):
        unrelated = ("if True:\n    def unrelated_helper(x):\n        return x\n"
                     "try:\n    from os.path import isabs as unrelated_isabs\nexcept ImportError:\n"
                     "    unrelated_isabs = None\nfor unrelated_i in range(1):\n    pass\n")
        for probe in (self.source + "\n" + unrelated,
                      self.in_publisher_class("    if True:\n        def unrelated_method(self):\n"
                                              "            return 1\n")):
            self.assertEqual(unreviewed(scan_repo({self.PUBLISHER: probe}), REVIEWED), "")
            self.assertEqual(changed_regions(surface({self.PUBLISHER: probe}), PROTECTED), "")

    def test_the_publication_guards_are_on_the_surface(self):
        # Codex's second probe and its siblings: each weakens what a write checks, with no scan finding.
        doc_end = '    revision, and every week whose active revision changes."""\n'
        probes = {
            "shown_revisions": (doc_end, doc_end + "    return set()\n"),
            "Publisher._eligibility_guard": ("                problems = eligibility.check(provenance, text, issue)",
                                             "                problems = []"),
            "Publisher._visible_revisions_guard": ("            for week, rev_no in sorted(set(shown)):",
                                                   "            for week, rev_no in []:"),
        }
        for region, (old, new) in probes.items():
            with self.subTest(region=region):
                self.assertEqual(self.source.count(old), 1, old)
                probe = self.source.replace(old, new)
                self.assertEqual(unreviewed(scan_repo({self.PUBLISHER: probe}), REVIEWED), "")  # the scan cannot see it
                self.assertIn(repr((self.PUBLISHER, region)),
                              changed_regions(surface({self.PUBLISHER: probe}), PROTECTED))

    def test_rebinding_each_publication_guard_is_found(self):
        cli = "dogear/publish/cli.py"
        cli_source = (REPO / cli).read_text(encoding="utf-8")
        for name in ("shown_revisions", "_eligibility_guard", "_visible_revisions_guard"):
            with self.subTest(name=name, where="from the CLI"):
                probe = cli_source + f"\n\ndef _sneak(target, fake):\n    target.{name} = fake\n"
                new = scan_repo({cli: probe}) - REVIEWED
                self.assertIn(("rebind", f"target.{name}"), {(f.kind, f.detail) for f in new})
        self.assert_rebinding(self.source + "\nif True:\n    def shown_revisions(before, after):\n"
                                            "        return set()\n", "shown_revisions")
        for name in ("_eligibility_guard", "_visible_revisions_guard"):
            with self.subTest(name=name, where="in the class body"):
                self.assert_rebinding(self.in_publisher_class(f"    if True:\n        def {name}(self, *a, **k):\n"
                                                              "            return lambda: None\n"), name, "Publisher")

    def test_every_dependency_of_the_surface_is_protected_or_reviewed(self):
        deps = dependencies()
        self.assertEqual(sorted(d for d in deps if not covered(d) and d not in NOT_CRITICAL), [],
                         "the surface uses these: protect them or review them into NOT_CRITICAL")
        self.assertEqual(sorted(d for d in NOT_CRITICAL if d not in deps), [], "stale NOT_CRITICAL entries")
        for dep in ((self.PUBLISHER, "shown_revisions"), (self.PUBLISHER, "Publisher._eligibility_guard"),
                    ("dogear/publish/eligibility.py", "check"), ("dogear/publish/registry.py", "promote")):
            self.assertTrue(covered(dep), dep)
        # A new helper the surface starts to use is uncovered until reviewed.
        doc_end = '    revision, and every week whose active revision changes."""\n'
        probe = self.source.replace(doc_end, doc_end + "    after = _new_helper(after)\n") + \
            "\n\ndef _new_helper(after):\n    return after\n"
        uncovered = [d for d in dependencies(overrides={self.PUBLISHER: probe})
                     if not covered(d) and d not in NOT_CRITICAL]
        self.assertEqual(uncovered, [(self.PUBLISHER, "_new_helper")])


class CliHandoffTests(unittest.TestCase):
    """The production storage is reached only through the CLI's checked handoff:
    --target -> (store, host, mode, policy, dataset, affinity, separation) ->
    _pairing_problem -> Publisher, all on the surface; any other reference to
    Publisher or R2Store is a scan finding."""

    CLI = "dogear/publish/cli.py"

    def setUp(self):
        self.source = (REPO / self.CLI).read_text(encoding="utf-8")
        self.prod, self.staging = publish_cli.TARGETS["production"], publish_cli.TARGETS["staging"]

    def pairing(self, **change):
        p = self.prod
        args = dict(target="production", store_spec=p["store"], base_url=p["base_url"], mode="production",
                    separation="production", policy=p["policy"], dataset=p["dataset"], affinity=p["affinity"])
        args.update(change)
        return publish_cli._pairing_problem(**args)

    def test_codexs_mode_mutation_needs_review(self):
        # Codex's probe: production target and storage, but the Publisher gets test mode
        # (which skips the production quiet-capability guard and the production separation).
        old = "                     mode=args.mode, assets=assets, served=served, separation=separation), None"
        self.assertEqual(self.source.count(old), 1)
        probe = self.source.replace(old, old.replace("mode=args.mode", 'mode="test"'))
        self.assertIn(repr((self.CLI, "_configure")), changed_regions(surface({self.CLI: probe}), PROTECTED))
        # The scan sees it too: the Publisher construction it edits is a pinned occurrence.
        new = scan_repo({self.CLI: probe}) - REVIEWED
        self.assertEqual({(f.scope, f.kind) for f in new}, {("_configure", "construct")})

    def test_every_part_of_the_handoff_needs_review(self):
        probes = {
            "TARGETS": ('"base_url": PRODUCTION_HOST, "mode": "production",', '"base_url": PRODUCTION_HOST, "mode": "test",'),
            "_apply_target": ('t["store"], t["base_url"], t["mode"], t["policy"]', 't["store"], t["base_url"], "test", t["policy"]'),
            "_pairing_problem": ('    if target == "production" or production_storage:', '    if False:'),
            "_configure": ('separation = {"production": "production", "staging": "staging"}.get(args.target)',
                           'separation = None'),
            "_store": ("allow_endpoint_override=not fixed", "allow_endpoint_override=True"),
        }
        for region, (old, new) in probes.items():
            with self.subTest(region=region):
                self.assertEqual(self.source.count(old), 1, old)
                differs = changed_regions(surface({self.CLI: self.source.replace(old, new)}), PROTECTED)
                self.assertIn(repr((self.CLI, region)), differs)

    def test_production_storage_only_with_the_whole_production_profile(self):
        self.assertIsNone(self.pairing())
        mismatches = {
            "test mode": dict(mode="test"),
            "no separation": dict(separation=None),
            "staging separation": dict(separation="staging"),
            "the staging policy": dict(policy=self.staging["policy"]),
            "the staging dataset": dict(dataset=self.staging["dataset"]),
            "the staging affinity": dict(affinity=self.staging["affinity"]),
            "no --target (explicit bucket)": dict(target=None, mode="test", separation=None),
            "the staging target": dict(target="staging"),
            "another bucket at the production host": dict(store_spec="r2:other"),
            "the production bucket at another host": dict(base_url="https://example.org"),
        }
        for name, change in mismatches.items():
            with self.subTest(name):
                self.assertIsNotNone(self.pairing(**change), name)
        s = self.staging
        self.assertIsNone(publish_cli._pairing_problem("staging", s["store"], s["base_url"], "test", "staging",
                                                       s["policy"], s["dataset"], s["affinity"]))
        self.assertIsNotNone(publish_cli._pairing_problem("staging", s["store"], s["base_url"], "test", None,
                                                          s["policy"], s["dataset"], s["affinity"]))
        # A local directory may carry the production host (its rehearsal URLs); an explicit
        # staging bucket in test mode stays allowed (the drills, against a local server).
        self.assertIsNone(publish_cli._pairing_problem(None, "/tmp/store", self.prod["base_url"], "test", None,
                                                       Path("p"), Path("d"), Path("a")))
        self.assertIsNone(publish_cli._pairing_problem(None, s["store"], "http://127.0.0.1:1", "test", None,
                                                       Path("p"), Path("d"), Path("a")))

    def test_the_cli_refuses_the_production_bucket_outside_the_production_profile(self):
        from dogear.cli import main
        with tempfile.TemporaryDirectory() as tmp:
            policy = Path(tmp) / "policy.json"
            policy.write_text(json.dumps(HOLD_PREVIOUS.as_json()))
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                code = main(["publish", "--store", self.prod["store"], "--mode", "test", "--policy", str(policy),
                             "--pubdata", tmp, "status"])
            self.assertEqual(code, 4)
            self.assertIn("production storage and host go only with --target production", err.getvalue())

    def test_another_route_to_a_publisher_or_production_storage_is_a_finding(self):
        routes_ = {
            "dogear/publish/cli.py": self.source + "\n\ndef _other(*a, **k):\n    return Publisher(*a, mode='test', **k)\n",
            "tools/sub/publish_direct.py": "from dogear.publish.r2 import R2Store\n\n\ndef store():\n"
                                           "    return R2Store.from_env('dogear-issues')\n",
            "tools/alias.py": "from dogear.publish import publisher\n\nP = publisher.Publisher\n",
        }
        for path, source in routes_.items():
            with self.subTest(path=path):
                new = scan_repo({path: source}) - REVIEWED
                self.assertIn("construct", {f.kind for f in new})


class QuietVerificationSurfaceTests(unittest.TestCase):
    """The quiet source (from complete, validated history), the pre-promotion
    verification of a staged week, and every quiet exclusion re-checked on the
    revision's own bytes."""

    def test_codexs_quiet_probes_need_review(self):
        qh, staging, publisher = ("dogear/publish/quiet_history.py", "dogear/publish/staging.py",
                                  "dogear/publish/publisher.py")
        probes = (
            # Codex: the exclusion set derived empty, so a featured item can enter the quiet pool.
            (qh, '            excluded.update(line["recordIds"])', "            pass", ""),
            # Codex: verify_staged always passes.
            (staging, '    prov, problems = staged.provenance, []\n', "    return []\n", "verify_staged"),
            (publisher, "        attested = quiet_history.attested_subjects(lines, reg, self.pubdata)",
             "        attested = {}", "Publisher._quiet_source"),
            (qh, "    if unrecorded or set(recorded) - set(revisions):", "    if False:", ""),
            ("dogear/publish/edition.py", "        if any(i in blocked for i in identities):", "        if False:", ""),
            ("dogear/publish/pubdata.py", "    def history(self) -> list[dict]:",
             "    def history(self) -> list[dict]:\n        return []", ""),
        )
        for rel, old, new, region in probes:
            with self.subTest(region=f"{rel} {region}"):
                source = (REPO / rel).read_text(encoding="utf-8")
                self.assertEqual(source.count(old), 1, old)
                probe = source.replace(old, new)
                self.assertIn(repr((rel, region)), changed_regions(surface({rel: probe}), PROTECTED))

    def test_rebinding_the_quiet_source_or_verification_is_found(self):
        cli = "dogear/publish/cli.py"
        cli_source = (REPO / cli).read_text(encoding="utf-8")
        for target, name in (("staging", "verify_staged"), ("quiet_history", "build_source"),
                             ("quiet_history", "validated_lines"), ("Publisher", "_quiet_source")):
            with self.subTest(name=name):
                probe = cli_source + f"\n\ndef _sneak({target}, fake):\n    {target}.{name} = fake\n"
                self.assertIn(("rebind", f"{target}.{name}"), {(f.kind, f.detail) for f in scan_repo({cli: probe}) - REVIEWED})
        staging = "dogear/publish/staging.py"
        source = (REPO / staging).read_text(encoding="utf-8")
        nested = source + "\nif True:\n    def verify_staged(*a, **k):\n        return []\n"
        self.assertIn(("", "rebinding", "verify_staged (def)"),
                      {(f.scope, f.kind, f.detail) for f in scan_repo({staging: nested}) - REVIEWED})
        publisher = "dogear/publish/publisher.py"
        source = (REPO / publisher).read_text(encoding="utf-8")
        nested = source.replace("\n\ndef shown_revisions(", "\n    if True:\n        def _quiet_source(self, monday):\n"
                                "            return None\n\n\ndef shown_revisions(")
        self.assertIn(("Publisher", "rebinding", "_quiet_source (def)"),
                      {(f.scope, f.kind, f.detail) for f in scan_repo({publisher: nested}) - REVIEWED})

    def setUp(self):
        f = published(self, [regular_week(W0, "a"), regular_week(W1, "b")])
        promote(f, W1)
        promote(f, W2)  # W2 is empty: a quiet issue
        self.f, self.issue, self.line = f, stored_issue(f, W2), line_for(f, W2)
        self.prov = json.loads((f.remote.remote / "published" / W2.isoformat() / "rev0.provenance.json").read_bytes())
        self.source = QuietSource.from_json(self.prov["inputs"]["quietSource"])

    def with_source(self, **sets) -> dict:
        raw = dict(self.prov["inputs"]["quietSource"], **sets)
        return dict(self.prov, inputs=dict(self.prov["inputs"], quietSource=raw))

    def test_every_quiet_exclusion_is_rechecked_on_the_revision(self):
        self.assertEqual(revision_problems(W2, self.issue, self.prov, self.line), [])
        subject = next(s for s in self.prov["subjects"] if s["personId"] or s["workId"])
        identity = subject["personId"] or subject["workId"]
        cases = {
            "subject featured within 365 days": (self.with_source(subjectIdentities=[identity]), "within 365 days"),
            "subject of this week's earlier quiet pick": (self.with_source(sameWeekQuietIdentities=[identity]),
                                                          "earlier quiet pick"),
            "no subjects": ({k: v for k, v in self.prov.items() if k != "subjects"}, "subject identities"),
            "published as a full item": (self.with_source(excluded=[subject["recordId"]]), "as a full item"),
        }
        for name, (prov, message) in cases.items():
            with self.subTest(name):
                problems = revision_problems(W2, self.issue, prov)
                self.assertTrue(any(message in p for p in problems), problems)
        retained = self.with_source(sameWeekQuietIdentities=[identity], retainable=[subject["recordId"]])
        self.assertFalse([p for p in revision_problems(W2, self.issue, retained) if "earlier quiet pick" in p])
        # Its own anniversary within five weeks, and (with the frozen record) a non-anniversary or a minor one.
        o = next(x for x in self.prov["quietOccurrences"] if x["recordId"] == subject["recordId"])
        soon = W2 + dt.timedelta(days=7)
        prov = dict(self.prov, quietOccurrences=[dict(x, month=soon.month, day=soon.day) if x is o else x
                                                 for x in self.prov["quietOccurrences"]])
        self.assertTrue(any("within the next five weeks" in p for p in
                            quiet_exclusion_problems(W2, prov, self.source, None)))
        frozen = {subject["recordId"]: {"personId": subject["personId"], "workId": subject["workId"],
                                        "year": int(o["date"][:4]), "significance": 1}}
        problems = quiet_exclusion_problems(W2, self.prov, self.source, frozen)
        self.assertTrue(any("not yet an anniversary" in p for p in problems), problems)
        self.assertTrue(any("significance below" in p for p in problems), problems)

    def test_history_must_be_complete_before_it_feeds_a_quiet_pool(self):
        pubdata, reg = PubData(self.f.remote.remote), self.f.registry()
        self.assertTrue(quiet_history.validated_lines(reg, pubdata, self.f.store))
        lines = pubdata.history()
        regular = next(h for h in lines if h.get("op") in ("launch", "promote") and h.get("edition", "regular") == "regular")
        with mock.patch.object(PubData, "history", lambda _self: [h for h in lines if h is not regular]):
            with self.assertRaisesRegex(CorruptState, "without exactly one content line"):
                quiet_history.validated_lines(reg, pubdata, self.f.store)


class PublicationInvariantTests(unittest.TestCase):
    """What the system says stays true: "published" only when the live registry is
    affirmatively the intended bytes (and only then is history committed);
    "verified" only after every required check ran on the real result; a quiet
    issue only as the reviewed selector composes it."""

    PUBLISHER, STAGING, SELECT = "dogear/publish/publisher.py", "dogear/publish/staging.py", "dogear/select.py"

    def changed(self, rel: str, old: str, new: str) -> str:
        source = (REPO / rel).read_text(encoding="utf-8")
        self.assertEqual(source.count(old), 1, old)
        return changed_regions(surface({rel: source.replace(old, new)}), PROTECTED)

    def lose_registry_writes(self, f, raise_error: bool):
        """Every registry write is lost before it lands: with a connection error, or
        (silently) with a put that reports success."""
        real = f.store.put

        def put(key, data, **kw):
            if key == REGISTRY_KEY:
                if raise_error:
                    raise StoreError("connection reset before the request was sent")
                return "etag-of-a-write-that-never-landed"
            return real(key, data, **kw)
        f.store.put = put

    def committed_txns(self, f) -> set:
        return {h["txn"] for h in f.history() if h.get("status") == "committed"}

    def test_codexs_lost_write_is_never_recorded_as_published(self):
        for raise_error in (True, False):
            with self.subTest(lost="with an error" if raise_error else "silently"):
                f = published(self, [regular_week(W0, "a"), regular_week(W1, "b")])
                before, committed = f.store.objects[REGISTRY_KEY].data, self.committed_txns(f)
                self.lose_registry_writes(f, raise_error)
                with self.assertRaises(Aborted):  # never "published"
                    promote(f, W1)
                self.assertEqual(f.store.objects[REGISTRY_KEY].data, before)  # still the previous revision
                self.assertEqual(self.committed_txns(f), committed)  # and no history commit
                self.assertIsNotNone(PubData(f.remote.remote).read_pending())  # kept for the next run
        # Codex's mutation (_registry_state answers "ours") needs review...
        self.assertIn(repr((self.PUBLISHER, "Publisher._registry_state")),
                      self.changed(self.PUBLISHER, "        if cur is not None and regmod.sha256(cur.data) == intended:",
                                   "        if True:"))
        # ... because, unreviewed, it would record the lost write as published:
        f = published(self, [regular_week(W0, "a"), regular_week(W1, "b")])
        before, committed = f.store.objects[REGISTRY_KEY].data, self.committed_txns(f)
        self.lose_registry_writes(f, True)
        with mock.patch.object(Publisher, "_registry_state", lambda *a, **k: "ours"):
            with contextlib.suppress(Exception):
                promote(f, W1)
        self.assertEqual(f.store.objects[REGISTRY_KEY].data, before)
        self.assertNotEqual(self.committed_txns(f), committed)  # the false record the guard prevents

    def test_the_history_commit_and_publication_result_need_review(self):
        probes = {
            "Publisher._finalize": ('        problem = _expected_targets_problem(pending)\n', '        problem = None\n'),
            "Publisher._committed": ("        if failures:\n            raise PublishedUnverified(",
                                     "        if False:\n            raise PublishedUnverified("),
            "Publisher._settle_pending": ('        if obj is not None and regmod.sha256(obj.data) == pending["intendedRegistrySha"]:',
                                          "        if obj is not None:"),
            "Publisher._transact": ("            state = self._registry_state(intended, expected)\n            if state == \"ours\":\n                break\n            if state == \"other\":",
                                    "            state = \"ours\"\n            if state == \"ours\":\n                break\n            if state == \"other\":"),
            "Publisher._check_target": ('    def _check_target(self, reg: dict, t: dict) -> Optional[str]:\n',
                                        '    def _check_target(self, reg: dict, t: dict) -> Optional[str]:\n        return None\n'),
        }
        for region, (old, new) in probes.items():
            with self.subTest(region=region):
                self.assertIn(repr((self.PUBLISHER, region)), self.changed(self.PUBLISHER, old, new))

    def test_every_required_check_needs_review_to_change(self):
        checks_py = "dogear/publish/checks.py"
        probes = (
            (self.STAGING, '    problems, warnings = [], []\n    if checks.link_checker is not None:',
             '    return [], []\n    problems, warnings = [], []\n    if checks.link_checker is not None:', "run_checks"),
            (checks_py, "        report = LinkReport()\n        for url in urls:", "        report = LinkReport()\n        for url in []:", ""),
            (checks_py, "        if run.returncode != 0:", "        if False:", ""),
            (checks_py, "                return resp.status, resp.read(MAX_SERVED_BYTES + 1)  # longer cannot match",
             "                return 200, resp.read(MAX_SERVED_BYTES + 1)", ""),
            ("mock/render_dogear.py", "def ", "def _unused():\n    pass\n\n\ndef ", ""),
            ("dogear/issue.py", "def typeset(", "def _typeset_old(", ""),
            (self.STAGING, "    return problems + shown_link_problems(base_url, selection, issue, web_bytes) + \\",
             "    return problems + [] + \\", "artifact_problems"),
            (self.STAGING, "    blocking, warnings = run_checks(frozen, selection, checks)\n    problems += blocking",
             "    blocking, warnings = [], []\n    problems += blocking", "stage"),
        )
        for rel, old, new, region in probes:
            with self.subTest(change=f"{rel} {region}"):
                source = (REPO / rel).read_text(encoding="utf-8")
                self.assertGreaterEqual(source.count(old), 1, old)
                probe = source.replace(old, new, 1)
                self.assertIn(repr((rel, region)), changed_regions(surface({rel: probe}), PROTECTED))

    def test_a_failed_check_stops_staging(self):
        golden = REPO / "tests" / "golden"
        inputs = StageInputs(golden / "regular-dataset.json", Path("/nonexistent.json"), "https://dogear.example.org",
                             "dogear@golden")
        week, now = week_starting(dt.date(2026, 11, 2)), dt.datetime(2026, 10, 30, 10, 2, tzinfo=MANILA)
        self.assertTrue(stage_week(week, inputs, TEST_POLICY, [], now, Checks(), "test"))
        failing = {
            "link": Checks(link_checker=lambda urls: LinkReport(blocking=[f"{u}: HTTP 404" for u in urls] or ["x"])),
            "fit": Checks(fit_checker=lambda dataset, ids: ["entry does not fit"]),
        }
        for name, checks in failing.items():
            with self.subTest(check=name), self.assertRaises(StageRefused) as refused:
                stage_week(week, inputs, TEST_POLICY, [], now, checks, "test")
            self.assertTrue(any(p.startswith(f"{name}:") for p in refused.exception.problems))

    def test_every_link_shown_is_one_the_link_check_was_given(self):
        golden = REPO / "tests" / "golden"
        inputs = StageInputs(golden / "regular-dataset.json", Path("/nonexistent.json"), "https://dogear.example.org",
                             "dogear@golden")
        week, now = week_starting(dt.date(2026, 11, 2)), dt.datetime(2026, 10, 30, 10, 2, tzinfo=MANILA)
        seen = []
        staged = stage_week(week, inputs, TEST_POLICY, [], now,
                            Checks(link_checker=lambda urls: seen.extend(urls) or LinkReport()), "test")
        issue = json.loads(staged.issue_bytes)
        from dogear.publish.staging import freeze, generate
        selection = generate(week, freeze(inputs, TEST_POLICY, [], now, None))[0]
        self.assertEqual(shown_link_problems(inputs.base_url, selection, issue, staged.web_bytes), [])
        extra = staged.web_bytes.replace(b"</body>", b'<a class="cta" href="https://unchecked.example.net/x">x</a></body>')
        self.assertEqual(shown_link_problems(inputs.base_url, selection, issue, extra),
                         ["shows a link the link check was not given: https://unchecked.example.net/x"])
        self.assertEqual(shown_link_problems(inputs.base_url, selection, dict(issue, extra="https://unchecked.example.net/y"),
                                             staged.web_bytes),
                         ["shows a link the link check was not given: https://unchecked.example.net/y"])

    GOLDEN_WEEK, GOLDEN_NOW = dt.date(2026, 11, 2), dt.datetime(2026, 10, 30, 10, 2, tzinfo=MANILA)

    def golden(self):
        golden = REPO / "tests" / "golden"
        return StageInputs(golden / "regular-dataset.json", Path("/nonexistent.json"), "https://dogear.example.org",
                           "dogear@golden")

    def test_codexs_single_quoted_link_is_refused_and_the_renderer_needs_review(self):
        # Codex's probe: the exempt renderer adds a single-quoted outside link; it went
        # through staging and promotion unchecked. The renderer is now on the surface...
        self.assertIn(repr(("dogear/web.py", "")), self.changed(
            "dogear/web.py", "def render_web(web: dict) -> str:\n",
            "def render_web(web: dict) -> str:\n    web = dict(web)\n"))
        # ... and the link binding reads the page as HTML, every quoting and place a URL can sit.
        import dogear.publish.staging as staging_module
        real = staging_module.render_web
        week = week_starting(self.GOLDEN_WEEK)
        for name, html_text in (
                ("single-quoted", "<a class='cta' href='https://unchecked.example.net/q'>Read</a>"),
                ("unquoted", "<a href=https://unchecked.example.net/q>Read</a>"),
                ("entity-encoded", '<a href="&#104;ttps://unchecked.example.net/q">Read</a>'),
                ("srcset", '<img srcset="//unchecked.example.net/q 1x">'),
                ("style url()", "<div style=\"background:url('https://unchecked.example.net/q')\"></div>"),
                ("meta refresh", '<meta http-equiv="refresh" content="0;url=https://unchecked.example.net/q">'),
                ("script", "<script>location='https://unchecked.example.net/q'</script>"),
                ("text", "<p>https://unchecked.example.net/q</p>")):
            with self.subTest(name), mock.patch.object(staging_module, "render_web",
                                                       lambda web, _h=html_text: real(web).replace("</body>", _h + "</body>")):
                with self.assertRaises(StageRefused) as refused:
                    stage_week(week, self.golden(), TEST_POLICY, [], self.GOLDEN_NOW, Checks(), "test")
                said = "; ".join(refused.exception.problems)
                self.assertTrue("shows a link the link check was not given" in said or "never uses" in said, said)
        self.assertTrue(stage_week(week, self.golden(), TEST_POLICY, [], self.GOLDEN_NOW, Checks(), "test"))

    def links(self, page: str, checked: str = "https://library.test/g/2/0", issue: dict = None) -> list:
        selection = SimpleNamespace(rendered=[SimpleNamespace(record=SimpleNamespace(
            link=SimpleNamespace(url=checked), sources=(SimpleNamespace(url="https://source.test/s"),)))])
        return shown_link_problems("https://dogear.example.org", selection,
                                   issue or {"editionUrl": "https://dogear.example.org/2026-11-02/"},
                                   f"<html><body>{page}</body></html>".encode("utf-8"))

    def test_codexs_altered_link_is_refused_and_an_unchanged_one_with_parentheses_passes(self):
        # Codex's probe: the checked link is .../g/2/0; the page shows .../g/2/0; (a
        # different URL). Values are compared whole and exactly, never trimmed.
        self.assertEqual(self.links('<a class="cta" href="https://library.test/g/2/0">Read</a>'), [])
        self.assertEqual(self.links('<a class="cta" href="https://library.test/g/2/0;">Read</a>'),
                         ["shows a link the link check was not given: https://library.test/g/2/0;"])
        # An unchanged, checked URL with parentheses passes, in an attribute or in text.
        parens = "https://library.test/wiki/Book_(novel)"
        self.assertEqual(self.links(f'<a class="cta" href="{parens}">Read</a><p>{parens}</p>', checked=parens), [])
        self.assertTrue(self.links(f'<a class="cta" href="{parens})">Read</a>', checked=parens))
        altered = {
            "a trailing period": '<a href="https://library.test/g/2/0.">x</a>',
            "in text, with a semicolon": "<p>https://library.test/g/2/0;</p>",
            "a tab inside": '<a href="https://library.test/g/2/\t0">x</a>',
            "backslashes for slashes": '<a href="https:\\\\evil.example/x">x</a>',
            "a javascript: link": "<a href=\"javascript:location='https://library.test/g/2/0'\">x</a>",
            "another scheme": '<a href="ftp://library.test/g/2/0">x</a>',
            "the scheme's case": '<a href="HTTPS://library.test/g/2/0">x</a>',
            "srcset": '<img srcset="https://library.test/g/2/0; 1x">',
            "a CSS url(": '<div style="background:url(https://library.test/g/2/0)"></div>',
            "an issue string": None,
        }
        for name, page in altered.items():
            with self.subTest(name):
                if page is None:
                    self.assertTrue(self.links("", issue={"editionUrl": "https://dogear.example.org/2026-11-02/",
                                                          "note": "https://library.test/g/2/0;"}))
                else:
                    self.assertTrue(self.links(page), name)
        refused = {
            "<base>": '<base href="https://dogear.example.org/">',
            "<script>": "<script>1</script>",
            "an event handler": '<p onclick="go()">x</p>',
            "meta refresh": '<meta http-equiv="refresh" content="0">',
            "a CSS escape": '<style>a{background:url(\\68ttps://evil.example)}</style>',
            "an embedded document": '<iframe src="https://dogear.example.org/x"></iframe>',
        }
        for name, page in refused.items():
            with self.subTest(name):
                self.assertTrue(any("never uses" in p for p in self.links(page)), name)
        # What the edition does use stays valid: anchors, its own URLs, relative fonts, sources.
        self.assertEqual(self.links('<a href="#golden-02-0">x</a><a href="https://dogear.example.org/2026-11-02/">e</a>'
                                    "<style>@font-face{src:url('../fonts/inter.woff2')}</style>"
                                    '<li><a href="https://source.test/s">source</a></li>'), [])

    def test_codexs_srcset_is_refused_outright(self):
        # Codex's probe: a comma can be part of a srcset candidate's URL, so the browser may
        # read "https://library.test/g/2/0,changed" as one URL. The edition never uses
        # srcset, so any is refused, whatever its quoting, case, spacing or candidates.
        codex = '<img srcset="https://library.test/g/2/0,changed 1x">'
        self.assertTrue(any("srcset" in p and "never uses" in p for p in self.links(codex)), self.links(codex))
        for page in ('<img srcset="https://library.test/g/2/0 1x">',
                     "<img srcset='https://library.test/g/2/0 1x'>",
                     "<img srcset=https://library.test/g/2/0>",
                     '<img SRCSET = "https://library.test/g/2/0 1x , https://library.test/g/2/0 2x">',
                     '<img srcset="https://dogear.example.org/x.png 1x">',
                     '<img srcset="../fonts/x.png">',
                     '<img srcset="">',
                     "<img srcset>",
                     '<source srcset="https://library.test/g/2/0">',
                     '<link rel="preload" imagesrcset="https://library.test/g/2/0 1x">'):
            with self.subTest(page):
                self.assertTrue(any("srcset" in p and "never uses" in p for p in self.links(page)), page)
        # The other checks are unchanged: parentheses pass, an altered link fails, and
        # normal output (no srcset) stages.
        parens = "https://library.test/wiki/Book_(novel)"
        self.assertEqual(self.links(f'<a class="cta" href="{parens}">Read</a>', checked=parens), [])
        self.assertTrue(self.links('<a class="cta" href="https://library.test/g/2/0;">Read</a>'))
        self.assertEqual(self.links('<a class="cta" href="https://library.test/g/2/0">Read</a>'), [])
        self.assertTrue(stage_week(week_starting(self.GOLDEN_WEEK), self.golden(), TEST_POLICY, [], self.GOLDEN_NOW,
                                   Checks(), "test"))

    def test_codexs_incomplete_selector_input_is_refused_and_needs_review(self):
        # Codex's probe: the exempt _read drops one record from what the selector reads,
        # while the canonical file stays intact.
        import dogear.publish.staging as staging_module
        real_read = staging_module._read

        def drop_one(path):
            data = real_read(path)
            if Path(path) == self.golden().dataset:
                raw = json.loads(data)
                raw["records"] = raw["records"][1:]
                data = json.dumps(raw).encode("utf-8")
            return data
        with mock.patch.object(staging_module, "_read", drop_one), self.assertRaises(StageRefused) as refused:
            stage_week(week_starting(self.GOLDEN_WEEK), self.golden(), TEST_POLICY, [], self.GOLDEN_NOW, Checks(), "test")
        self.assertIn("the frozen dataset is not the configured dataset file", refused.exception.problems)
        # And every selector input needs review to change: the reader, the binding, the
        # history and the preferences it scores against, and the staged copy promotion reads.
        probes = (
            (self.STAGING, "        return Path(path).read_bytes()", "        return Path(path).read_bytes()[:-1]", "_read"),
            (self.STAGING, "        if frozen_bytes != canonical:", "        if False:", "stage"),
            (self.PUBLISHER, "            if dt.date.fromisoformat(week) >= before:\n                continue",
             "            if True:\n                continue", "Publisher._history"),
            (self.PUBLISHER, "        self.pubdata.write_staged(staged.week, staged.files())",
             "        self.pubdata.write_staged(staged.week, dict(staged.files()))", "Publisher._write_staged"),
            ("dogear/affinity.py", "def parse_affinity(text: str) -> Affinity:\n",
             "def parse_affinity(text: str) -> Affinity:\n    text = '{}'\n", ""),
        )
        for rel, old, new, region in probes:
            with self.subTest(region=f"{rel} {region}"):
                self.assertIn(repr((rel, region)), self.changed(rel, old, new))

    def test_codexs_zero_slot_bars_need_review(self):
        # Codex's probe: generate hands the quiet selector zero slot bars; regeneration
        # would faithfully reproduce the wrong bytes, so the source must not change unreviewed.
        quiet_call = "                                       slot_bars=frozen.policy.slot_bars())"
        self.assertIn(repr((self.STAGING, "generate")), self.changed(
            self.STAGING, quiet_call,
            "                                       slot_bars=tuple((slot, 0) for slot, _bar in frozen.policy.slot_bars()))"))
        self.assertIn(repr((self.STAGING, "generate")), self.changed(
            self.STAGING, "        selection = select_quiet_issue(dataset, week, hist, source, affinity=affinity,",
            "        selection = select_issue(dataset, week, hist, source, affinity=affinity,"))
        for old, new in (("SLOT_BARS = ((4, 30), (6, 42), (8, 52))", "SLOT_BARS = ((4, 0), (6, 0), (8, 0))"),
                         ('RECOGNITION_LIFT = {"anchor": 5, "core": 2, "discovery": 0}',
                          'RECOGNITION_LIFT = {"anchor": 0, "core": 0, "discovery": 0}'),
                         ("        if cand.excluded is None and any(i in blocked_ids for i in identities):",
                          "        if False:")):
            with self.subTest(select=new):
                self.assertIn(repr((self.SELECT, "")), self.changed(self.SELECT, old, new))
        self.assertIn(repr(("dogear/publish/policy.py", "")), self.changed(
            "dogear/publish/policy.py", "        return ((4, 30), (6, self.slot5_bar), (8, 52))",
            "        return ((4, 0), (6, 0), (8, 0))"))


if __name__ == "__main__":
    unittest.main()
