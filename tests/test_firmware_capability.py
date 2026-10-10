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
import json
import socket
import subprocess
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

from dogear.publish import cli as publish_cli
from dogear.publish import firmware
from dogear.publish import registry as regmod
from dogear.publish.policy import PublicationPolicy
from dogear.publish.publisher import Aborted, NeedsHuman, NotReady, Publisher, shown_revisions
from dogear.publish.registry import REGISTRY_KEY, Refused

try:
    from tests.firmware_fixture import FIXTURES, SCHEMA, SyncedTree
    from tests.test_publish import W0, W1, W2, W3, Fixture, at
    from tests.test_quiet_week import QUIET, regular_week, stored_issue
except ImportError:
    from firmware_fixture import FIXTURES, SCHEMA, SyncedTree
    from test_publish import W0, W1, W2, W3, Fixture, at
    from test_quiet_week import QUIET, regular_week, stored_issue

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
                       lambda r: gpd(r).pop("dogear-e3-cant-load"),
                       lambda r: gpd(r).update({"dogear-v2-cache-503": "FAIL"}),
                       lambda r: gpd(r).update({"dogear-v3-q2-pages": "pass"}),
                       lambda r: gpd(r).update({"extra": "PASS"}),
                       lambda r: r["acceptance"]["packages"][1]["checks"].pop("dogear-v3-q2-navigation"),
                       lambda r: r["acceptance"]["packages"][2]["checks"].update({"dogear-opens": "PASS"})):
            with self.subTest(mutate=mutate):
                self.tree.edit_record(lambda r: (mutate(r), r.update(
                    acceptanceSha256=firmware.sha256(firmware.canonical(r["acceptance"])))))
                self.assert_refused()

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

    def test_only_transact_writes_the_registry(self):
        source = (Path(firmware.__file__).parent / "publisher.py").read_text()
        writes = [line for line in source.splitlines() if "put(REGISTRY_KEY" in line]
        self.assertEqual(len(writes), 1, writes)
        transact = source.split("    def _transact(", 1)[1].split("\n    def ", 1)[0]
        self.assertIn("put(REGISTRY_KEY", transact)
        self.assertIn("self._quiet_presentation_guard(reg, new_reg, fresh, uploads), guard", transact)
        control = source.split("    def _control(", 1)[1].split("\n    def ", 1)[0]
        self.assertIn("self._quiet_presentation_guard(reg, new_reg)", control)

    def test_the_override_is_refused_in_production_and_has_no_cli_flag(self):
        f = Fixture(self, regular_week(W0, "a"), at(W0, 9), policy=QUIET)
        for supported in (True, False):
            with self.assertRaises(ValueError):
                Publisher(f.store, f.remote.checkout(), f.pub.inputs, QUIET, f.checks, f.clock, mode="production",
                          quiet_supported=supported)
        cli_source = Path(publish_cli.__file__).read_text()
        self.assertNotIn("quiet_supported", cli_source)
        self.assertNotIn("quiet-supported", cli_source)


if __name__ == "__main__":
    unittest.main()
