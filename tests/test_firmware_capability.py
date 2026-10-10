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
import ast
import collections
import contextlib
import copy
import json
import os
import shutil
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
from dogear.publish.publisher import (Aborted, NeedsHuman, NotReady, Publisher, RegistryGate,
                                      UnguardedRegistryWrite, shown_revisions)
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

REPO = Path(firmware.__file__).resolve().parents[2]
WRITE_METHODS = frozenset({"put", "allow_once", "_request"})
# Names that reach the gate's raw store or its authorisation.
PRIVATE_NAMES = frozenset({"_store", "_allowed", "_RegistryGate__store", "_RegistryGate__allowed", "__store",
                           "__allowed"})
# Attributes that reflect past an object's interface.
REFLECTION_ATTRS = frozenset({"__getattribute__", "__getattr__", "__setattr__", "__dict__", "__class__",
                              "__slots__", "__closure__", "__func__", "__self__", "cell_contents", "__globals__",
                              "__code__", "__reduce__", "__reduce_ex__", "__getstate__", "__wrapped__"})
DYNAMIC_CALLS = frozenset({"getattr", "setattr", "delattr", "hasattr", "vars", "dir", "eval", "exec", "compile",
                           "__import__", "globals", "locals"})
REFLECTIVE_MODULES = frozenset({"gc", "inspect", "ctypes", "operator", "importlib", "functools", "copyreg",
                                "pickle", "marshal"})

SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
BLOCKS = ("body", "orelse", "finalbody", "handlers", "cases")
Finding = collections.namedtuple("Finding", "file scope kind detail at statement n")

# Every finding in the package and tools, reviewed, one entry per occurrence (see scan_source).
# A new one, by any spelling and wherever it is, fails here until it is reviewed and added.
REVIEWED = {
    # direct storage writes, each occurrence (DrillStore.put has four)
    Finding('dogear/publish/drills.py', 'DrillStore.put', 'write-call', 'self.store.put', 'body[0]/body[0]',  # test mode: wraps the store
            'return self.store.put(key, data, content_type=content_type, if_match=if_match, if_none_match=if_none_match)', 1),
    Finding('dogear/publish/drills.py', 'DrillStore.put', 'write-call', 'self.store.put', 'body[2]/body[1]/body[0]',  # test mode: wraps the store
            "self.store.put(REGISTRY_KEY, current.data + b'\\n', content_type=content_type)", 1),
    Finding('dogear/publish/drills.py', 'DrillStore.put', 'write-call', 'self.store.put', 'body[2]/body[2]',  # test mode: wraps the store
            'return self.store.put(key, data, content_type=content_type, if_match=if_match, if_none_match=if_none_match)', 1),
    Finding('dogear/publish/drills.py', 'DrillStore.put', 'write-call', 'self.store.put', 'body[4]',  # test mode: wraps the store
            'etag = self.store.put(key, data, content_type=content_type, if_match=if_match, if_none_match=if_none_match)', 1),
    Finding('dogear/publish/publisher.py', 'Publisher._transact', 'write-call', 'self.store.allow_once', 'body[11]/body[3]',  # after the guards
            'self.store.allow_once(intended)', 1),
    Finding('dogear/publish/publisher.py', 'Publisher._transact', 'write-call', 'self.store.put', 'body[11]/body[4]/body[0]',  # after the guards
            "self.store.put(REGISTRY_KEY, new_bytes, content_type='application/json', if_match=expected, if_none_match=expected is None)", 1),
    Finding('dogear/publish/publisher.py', 'Publisher._upload', 'write-call', 'self.store.put', 'body[0]/body[0]/body[2]/body[0]',  # artifacts, via the gate
            'self.store.put(key, data, content_type=content_type)', 1),
    Finding('dogear/publish/r2.py', 'R2Store.get', 'write-call', 'self._request', 'body[0]',
            "status, headers, data = self._request('GET', key)", 1),
    Finding('dogear/publish/r2.py', 'R2Store.put', 'write-call', 'self._request', 'body[3]',
            "status, headers, body = self._request('PUT', key, bytes(data), extra)", 1),
    Finding('tools/route_vectors.py', 'build', 'write-call', 'store.put', 'body[2]/body[1]/body[0]',  # an in-memory store for test vectors
            "store.put(key, body.encode('utf-8'), content_type='application/octet-stream')", 1),
    # the gate itself: the only code that touches the raw store
    Finding('dogear/publish/publisher.py', 'RegistryGate', 'private-name', '_allowed', '__slots__',
            "__slots__ = ('_store', '_allowed')", 1),
    Finding('dogear/publish/publisher.py', 'RegistryGate', 'private-name', '_store', '__slots__',
            "__slots__ = ('_store', '_allowed')", 1),
    Finding('dogear/publish/publisher.py', 'RegistryGate', 'reflection', '__slots__', '__slots__',
            "__slots__ = ('_store', '_allowed')", 1),
    Finding('dogear/publish/publisher.py', 'RegistryGate.__getattribute__', 'reflection', 'object.__getattribute__', 'body[1]',
            'return object.__getattribute__(self, name)', 1),
    Finding('dogear/publish/publisher.py', 'RegistryGate.__init__', 'private-name', '_allowed', 'body[1]',
            "object.__setattr__(self, '_allowed', None)", 1),
    Finding('dogear/publish/publisher.py', 'RegistryGate.__init__', 'private-name', '_store', 'body[0]',
            "object.__setattr__(self, '_store', store)", 1),
    Finding('dogear/publish/publisher.py', 'RegistryGate.__init__', 'reflection', 'object.__setattr__', 'body[0]',
            "object.__setattr__(self, '_store', store)", 1),
    Finding('dogear/publish/publisher.py', 'RegistryGate.__init__', 'reflection', 'object.__setattr__', 'body[1]',
            "object.__setattr__(self, '_allowed', None)", 1),
    Finding('dogear/publish/publisher.py', 'RegistryGate.allow_once', 'private-name', '_allowed', 'body[0]',
            "object.__setattr__(self, '_allowed', registry_sha)", 1),
    Finding('dogear/publish/publisher.py', 'RegistryGate.allow_once', 'reflection', 'object.__setattr__', 'body[0]',
            "object.__setattr__(self, '_allowed', registry_sha)", 1),
    Finding('dogear/publish/publisher.py', 'RegistryGate.get', 'private-name', '_store', 'body[0]',
            "return object.__getattribute__(self, '_store').get(key)", 1),
    Finding('dogear/publish/publisher.py', 'RegistryGate.get', 'reflection', 'object.__getattribute__', 'body[0]',
            "return object.__getattribute__(self, '_store').get(key)", 1),
    Finding('dogear/publish/publisher.py', 'RegistryGate.put', 'private-name', '_allowed', 'body[0]/body[0]',
            "allowed = object.__getattribute__(self, '_allowed')", 1),
    Finding('dogear/publish/publisher.py', 'RegistryGate.put', 'private-name', '_allowed', 'body[0]/body[1]',
            "object.__setattr__(self, '_allowed', None)", 1),
    Finding('dogear/publish/publisher.py', 'RegistryGate.put', 'private-name', '_store', 'body[1]',
            "return object.__getattribute__(self, '_store').put(key, data, **kwargs)", 1),
    Finding('dogear/publish/publisher.py', 'RegistryGate.put', 'reflection', 'object.__getattribute__', 'body[0]/body[0]',
            "allowed = object.__getattribute__(self, '_allowed')", 1),
    Finding('dogear/publish/publisher.py', 'RegistryGate.put', 'reflection', 'object.__getattribute__', 'body[1]',
            "return object.__getattribute__(self, '_store').put(key, data, **kwargs)", 1),
    Finding('dogear/publish/publisher.py', 'RegistryGate.put', 'reflection', 'object.__setattr__', 'body[0]/body[1]',
            "object.__setattr__(self, '_allowed', None)", 1),
    Finding('dogear/publish/publisher.py', 'RegistryGate.put', 'write-call', "object.__getattribute__(self, '_store').put", 'body[1]',
            "return object.__getattribute__(self, '_store').put(key, data, **kwargs)", 1),
    # the in-memory test store names the operation for its fault hook
    Finding('dogear/publish/store.py', 'MemoryStore.put', 'write-name', 'put', 'body[1]',
            "action = self._fault('put', key, data)", 1),
    # the gate's own API names
    Finding('dogear/publish/publisher.py', '', 'write-name', 'allow_once', '_GATE_API',
            "_GATE_API = frozenset({'get', 'put', 'allow_once'})", 1),
    Finding('dogear/publish/publisher.py', '', 'write-name', 'put', '_GATE_API',
            "_GATE_API = frozenset({'get', 'put', 'allow_once'})", 1),
    # the CLI's own store constructor (a function named _store), not the gate's slot
    Finding('dogear/publish/cli.py', 'run', 'private-name', '_store', 'body[9]/body[0]',
            'store, served = _store(args.store, args.base_url, fixed=bool(args.target))', 1),
    # constant, non-write attribute lookups
    Finding('dogear/publish/cli.py', '_Report._line', 'dynamic', "getattr(self.args, 'week', None)", 'body[0]',
            "week = getattr(self.args, 'week', None)", 1),
    Finding('dogear/publish/cli.py', '_Report.success', 'dynamic', "getattr(out, 'txn', None)", 'body[1]',
            "txns = [out.txn] if getattr(out, 'txn', None) else sorted(set(_TXN_ID.findall(out.message)))", 1),
    Finding('dogear/publish/cli.py', 'run', 'dynamic', "getattr(args, 'week', None)", 'body[13]',
            "week = dt.date.fromisoformat(args.week) if getattr(args, 'week', None) else None", 1),
    Finding('tools/chain_check.py', 'check', 'dynamic', "hasattr(tls, 'get_verified_chain')", 'body[4]/body[0]/body[1]',
            "chain = tls.get_verified_chain() if hasattr(tls, 'get_verified_chain') else []", 1),
}


def _statement_text(stmt) -> str:
    """The statement a finding sits in, canonically spelled; a compound
    statement contributes only its header (its body's statements are their own)."""
    head = copy.copy(stmt)
    for name in BLOCKS:
        if getattr(head, name, None):
            setattr(head, name, [])
    return " ".join(ast.unparse(head).split())


def _step(parent, field: str, index: int, stmt) -> str:
    """One step of a finding's position. In a function, the statement's index in
    its block: order there is reviewed (a write stays after its guards). At the
    top of a module or class, where order is declarative, the name the statement
    binds (or "-", leaving its text and n to identify it), so adding an
    unrelated import or constant above does not move it."""
    if not isinstance(parent, (ast.Module, ast.ClassDef)):
        return f"{field}[{index}]"
    if isinstance(stmt, SCOPES):
        return stmt.name
    targets = stmt.targets if isinstance(stmt, ast.Assign) else [getattr(stmt, "target", None)]
    if len(targets) == 1 and isinstance(targets[0], ast.Name):
        return targets[0].id
    return "-"


def scan_source(rel: str, source: str) -> set:
    """Every construct in one module that could write storage or reach the gate's
    raw store, from its syntax tree (not its spelling):
      write-call / write-ref  a write method, called directly or only referenced
                              (`writer = store.put` is a reference; so is partial(store.put))
      write-name, private-name  those names as strings (methodcaller("put"), getattr(g, "_store"))
      dynamic                 getattr/setattr/vars/eval/...: reviewed only with a constant, harmless name
      reflection              __getattribute__, __dict__, __closure__, object.__setattr__, ...
      import                  gc, inspect, ctypes, operator, functools, importlib, pickle, ...

    Each finding is one occurrence, identified structurally: file, enclosing
    class/function, kind, detail, `at` (its statement's position inside that
    scope, see _step: unrelated code moving elsewhere does not move it, but
    reordering the scope's own statements does), the statement's canonical
    text, and `n` (which identical occurrence at that position). A second
    occurrence anywhere, even inside an approved scope or statement, is a new
    finding: nothing collapses into an existing approval."""
    raw = []

    def note(scope, at, stmt, kind, detail):
        raw.append((rel, ".".join(scope), kind, detail, "/".join(at), _statement_text(stmt)))

    def walk(node, scope, at, stmt):
        for field, value in ast.iter_fields(node):
            children = value if isinstance(value, list) else [value]
            for index, child in enumerate(children):
                if not isinstance(child, ast.AST):
                    continue
                if isinstance(child, ast.stmt):
                    child_at = at + [_step(node, field, index, child)]
                    if isinstance(child, SCOPES):  # a new scope: positions restart inside it
                        visit(child, node, scope, child_at, child)
                        walk(child, scope + [child.name], [], child)
                        continue
                    visit(child, node, scope, child_at, child)
                    walk(child, scope, child_at, child)
                else:
                    visit(child, node, scope, at, stmt)
                    walk(child, scope, at, stmt)

    def visit(child, parent, scope, at, stmt):
        if isinstance(child, ast.Attribute):
            if child.attr in WRITE_METHODS:
                called = isinstance(parent, ast.Call) and parent.func is child
                note(scope, at, stmt, "write-call" if called else "write-ref", ast.unparse(child))
            if child.attr in REFLECTION_ATTRS:
                note(scope, at, stmt, "reflection", ast.unparse(child))
            if child.attr in PRIVATE_NAMES or "_RegistryGate" in child.attr:
                note(scope, at, stmt, "private-attr", ast.unparse(child))
        if isinstance(child, ast.Name) and (child.id in REFLECTION_ATTRS or child.id in PRIVATE_NAMES):
            note(scope, at, stmt, "reflection" if child.id in REFLECTION_ATTRS else "private-name", child.id)
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            if child.value in WRITE_METHODS:
                note(scope, at, stmt, "write-name", child.value)
            if child.value in PRIVATE_NAMES or "_RegistryGate" in child.value:
                note(scope, at, stmt, "private-name", child.value)
            if child.value in REFLECTION_ATTRS:
                note(scope, at, stmt, "reflection", child.value)
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Name) and child.func.id in DYNAMIC_CALLS:
            note(scope, at, stmt, "dynamic", ast.unparse(child))
        if isinstance(child, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in child.names] if isinstance(child, ast.Import) else [child.module or ""]
            for name in names:
                if name.split(".")[0] in REFLECTIVE_MODULES:
                    note(scope, at, stmt, "import", name)

    walk(ast.parse(source), [], [], None)
    seen = collections.Counter()
    found = set()
    for key in raw:
        seen[key] += 1
        found.add(Finding(*key, seen[key]))
    return found

# The scanned trees, at any depth. tests/ is not scanned: its probes are the bypasses.
SCANNED = ("dogear", "tools")


def discover(root: Path = REPO) -> dict:
    """{path: source} for every Python module beneath dogear/ and tools/, however
    deeply nested: each *.py file, and each extensionless script whose shebang
    names python. A symlink fails closed: what it points at is not reviewed here."""
    sources = {}
    for top in SCANNED:
        for dirpath, dirnames, filenames in os.walk(root / top):
            dirnames.sort()
            for name in dirnames + filenames:
                if os.path.islink(os.path.join(dirpath, name)):
                    raise AssertionError(f"{Path(dirpath, name).relative_to(root)} is a symlink; the scan does not "
                                         "follow it")
            for name in sorted(filenames):
                path = Path(dirpath, name)
                if path.suffix == ".py" or (not path.suffix and _python_script(path)):
                    sources[path.relative_to(root).as_posix()] = path.read_text(encoding="utf-8")
    return sources


def _python_script(path: Path) -> bool:
    with path.open("rb") as f:
        first = f.readline()
    return first.startswith(b"#!") and b"python" in first


def scan_repo(extra: dict = None, root: Path = REPO) -> set:
    """scan_source over every discovered module (plus `extra`: {path: source},
    for proving what a new or changed module would be flagged for)."""
    sources = discover(root)
    sources.update(extra or {})
    return set().union(*(scan_source(rel, src) for rel, src in sources.items()))


# Codex's demonstrated bypass: reflect the raw store out of the gate, save its
# put, and write the registry with the saved method.
BYPASS = """
from dogear.publish.registry import REGISTRY_KEY


def sneak(pub, data):
    raw = getattr(pub.store, "_RegistryGate__store")
    writer = raw.put
    writer(REGISTRY_KEY, data, content_type="application/json")
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
}


class RegistryWriteBoundaryTests(GuardBase):
    """The registry is written only by _transact, through the gate, right after
    the quiet-presentation guard passed for exactly those bytes: checked on the
    syntax tree (every write reference and every reflection) and at run time."""

    def assert_reviewed(self, found: set) -> None:
        unreviewed, stale = sorted(found - REVIEWED), sorted(REVIEWED - found)
        if unreviewed or stale:
            self.fail("a storage write or reflection changed: review it, then update REVIEWED\n"
                      + "".join(f"  new:  {tuple(f)}\n" for f in unreviewed)
                      + "".join(f"  gone: {tuple(f)}\n" for f in stale))

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
        moved = ("import itertools  # an unrelated import\n\n\ndef unrelated():\n    return 1\n\n\n"
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


if __name__ == "__main__":
    unittest.main()
