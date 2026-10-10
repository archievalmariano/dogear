"""tools/firmware_contract.py --sync (PUBLISHING §22): the only way the firmware
contract changes. It reads a published, immutable release, its assets, its
annotated tag and candidate run (a fake GitHub here) and the tagged commit (a
throwaway git repository here, never a working tree), and writes nothing unless
every check passes."""

from __future__ import annotations

import copy
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from dogear.publish import firmware

try:
    from tests.firmware_fixture import LIMITS, RELEASE_ID, RUN, SCHEMA, SCHEMA_BYTES, TAG, sums_text, \
        verification_record
except ImportError:
    from firmware_fixture import LIMITS, RELEASE_ID, RUN, SCHEMA, SCHEMA_BYTES, TAG, sums_text, verification_record

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import firmware_contract as fc  # noqa: E402

GIT_ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@t", GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null")


def git(repo: Path, *args) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True,
                          env=GIT_ENV).stdout.strip()


def header(limits: dict) -> str:
    return "#pragma once\n" + "".join(f"inline constexpr size_t {k} = {v};\n" for k, v in limits.items())


def font_header(name: str) -> str:
    return (f"static const EpdUnicodeInterval {name}Intervals[] = {{\n"
            "    { 0x20, 0x7E, 0x0 },\n    { 0xA0, 0x17F, 0x5F },\n    { 0x2000, 0x2064, 0x36B },\n};\n")


def tag_message(record: dict, **override) -> str:
    rel = record["release"]
    fields = {"candidate-run": str(rel["candidateRun"]), "candidate-commit": rel["commit"],
              "candidate-sums-sha256": rel["sumsSha256"], "physically-accepted": " ".join(SCHEMA["packageOrder"]),
              "acceptance-sha256": record["acceptanceSha256"]}
    fields.update(override)
    return f"{TAG}\n\n" + "".join(f"{k}: {v}\n" for k, v in fields.items() if v is not None)


class FakeGitHub:
    def __init__(self, gets: dict, sums: bytes):
        self.gets, self.sums, self.fail = gets, sums, None

    def get(self, path):
        if self.fail:
            raise self.fail
        if path not in self.gets:
            raise OSError(f"HTTP 404 {path}")
        return copy.deepcopy(self.gets[path])

    def download(self, url, max_bytes):
        fc.require(url.startswith(fc.DOWNLOAD_PREFIX), f"refusing to download {url}")
        return self.sums


class SyncTests(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        self.root, self.repo = tmp / "dogear", tmp / "crosspoint-reader"
        self.root.mkdir()
        # The firmware clone: the tagged commit carries the limits, schema and fonts.
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        self.files = {fc.LIMITS_HEADER: header(LIMITS), firmware.FIRMWARE_SCHEMA_PATH: SCHEMA_BYTES.decode()}
        for name in fc.GLYPH_FONTS:
            self.files[f"{fc.FONT_HEADERS}/{name}.h"] = font_header(name)
        self.commit_files()
        git(self.repo, "tag", "-a", TAG, "-m", "placeholder")
        self.tag_object, self.commit = git(self.repo, "rev-parse", TAG), git(self.repo, "rev-parse", f"{TAG}^{{commit}}")
        # The owner-approved record, and GitHub as the published release shows it.
        self.record = verification_record(self.commit, self.tag_object)
        self.write_record()
        rel = self.record["release"]
        self.sums = sums_text({n: d for n, d in rel["assets"].items() if n != firmware.SUMS})
        self.gh = FakeGitHub({
            f"repos/{fc.REPO}/releases/tags/{TAG}": {
                "tag_name": TAG, "draft": False, "prerelease": False, "immutable": True, "id": RELEASE_ID,
                "assets": [{"name": n, "state": "uploaded", "digest": f"sha256:{d}",
                            "browser_download_url": f"{fc.DOWNLOAD_PREFIX}{TAG}/{n}"} for n, d in rel["assets"].items()]},
            f"repos/{fc.REPO}/git/ref/tags/{TAG}": {"object": {"type": "tag", "sha": self.tag_object}},
            f"repos/{fc.REPO}/git/tags/{self.tag_object}": {"object": {"type": "commit", "sha": self.commit},
                                                             "message": tag_message(self.record)},
            f"repos/{fc.REPO}/actions/runs/{RUN}": {
                "path": firmware.CANDIDATE_WORKFLOW + "@refs/heads/release/v1.2.0-dogear",
                "repository": {"full_name": fc.REPO}, "head_repository": {"full_name": fc.REPO},
                "event": "workflow_dispatch", "status": "completed", "conclusion": "success",
                "head_branch": "release/v1.2.0-dogear", "head_sha": self.commit},
        }, self.sums)
        # Today's contract stands in until a sync replaces it.
        self.old = b'{"_note": "old", "firmwareCommit": "3d9d9e56", "limits": {}}\n'
        (self.root / firmware.CONTRACT).parent.mkdir(parents=True, exist_ok=True)
        (self.root / firmware.CONTRACT).write_bytes(self.old)

    def commit_files(self):
        for path, text in self.files.items():
            (self.repo / path).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / path).write_text(text)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "firmware")

    def record_path(self) -> Path:
        return self.root / firmware.VERIFICATIONS / f"{TAG}.json"

    def write_record(self, record=None):
        self.record_path().parent.mkdir(parents=True, exist_ok=True)
        self.record_path().write_text(json.dumps(record or self.record, indent=2) + "\n")

    def sync(self):
        return fc.sync(TAG, self.record_path(), fc.Clone(self.repo), self.gh, root=self.root)

    def assert_refused(self, why: str = ""):
        with self.assertRaises((firmware.Invalid, OSError, ValueError, KeyError, subprocess.CalledProcessError)) as c:
            self.sync()
        if why:
            self.assertIn(why, str(c.exception))
        self.assertEqual((self.root / firmware.CONTRACT).read_bytes(), self.old, "nothing may be written")
        self.assertFalse((self.root / firmware.SCHEMA_COPY).exists())
        self.assertFalse(firmware.capability(self.root).ok)

    def test_a_published_verified_release_syncs_and_authorises(self):
        self.assertIn("quiet issues authorised", self.sync())
        contract = json.loads((self.root / firmware.CONTRACT).read_text())
        self.assertEqual(contract["firmwareCommit"], self.commit)
        self.assertEqual(contract["limits"], LIMITS)
        self.assertEqual(contract["release"]["tagObject"], self.tag_object)
        self.assertEqual((self.root / firmware.SCHEMA_COPY).read_bytes(), SCHEMA_BYTES)
        glyphs = json.loads((self.root / "mock/fonts/device-glyphs.json").read_text())
        self.assertEqual(glyphs["firmwareCommit"], self.commit)
        self.assertEqual(firmware.capability(self.root), firmware.Capability(True, None))

    def test_it_reads_the_tagged_commit_never_the_working_tree(self):
        (self.repo / fc.LIMITS_HEADER).write_text(header(dict(LIMITS, kIssueSchemaMax=9, kBogus=1)))  # uncommitted
        self.sync()
        self.assertEqual(json.loads((self.root / firmware.CONTRACT).read_text())["limits"], LIMITS)

    def test_the_release_must_be_published_immutable_and_the_records(self):
        path = f"repos/{fc.REPO}/releases/tags/{TAG}"
        for change, why in (({"draft": True}, "draft"), ({"prerelease": True}, "prerelease"),
                            ({"immutable": False}, "immutable"), ({"immutable": None}, "immutable"),
                            ({"id": RELEASE_ID + 1}, "release"), ({"tag_name": "v1.2.1"}, "v1.2.1")):
            with self.subTest(change=change):
                saved = self.gh.gets[path]
                self.gh.gets[path] = dict(saved, **change)
                self.assert_refused(why)
                self.gh.gets[path] = saved

    def test_assets_must_be_exactly_the_records_with_githubs_digests(self):
        path = f"repos/{fc.REPO}/releases/tags/{TAG}"
        for mutate in (lambda a: a.pop(0), lambda a: a.append(dict(a[0], name="notes.txt")),
                       lambda a: a.append(dict(a[0])), lambda a: a[3].update(digest="sha256:" + "0" * 64),
                       lambda a: a[3].update(digest=None), lambda a: a[2].update(state="starter")):
            with self.subTest(mutate=mutate):
                saved = copy.deepcopy(self.gh.gets[path])
                mutate(self.gh.gets[path]["assets"])
                self.assert_refused()
                self.gh.gets[path] = saved

    def test_the_sums_file_must_be_the_records(self):
        self.gh.sums = self.sums + b"\n"
        self.assert_refused(firmware.SUMS)
        self.gh.sums = self.sums
        path = f"repos/{fc.REPO}/releases/tags/{TAG}"
        sums = next(a for a in self.gh.gets[path]["assets"] if a["name"] == firmware.SUMS)
        sums["browser_download_url"] = "https://example.com/SHA256SUMS.txt"
        self.assert_refused("refusing to download")

    def test_the_annotated_tag_must_name_this_candidate_and_record(self):
        ref, obj = f"repos/{fc.REPO}/git/ref/tags/{TAG}", f"repos/{fc.REPO}/git/tags/{self.tag_object}"
        cases = [(ref, {"object": {"type": "commit", "sha": self.commit}}),  # lightweight
                 (ref, {"object": {"type": "tag", "sha": "0" * 40}}),
                 (obj, dict(self.gh.gets[obj], object={"type": "commit", "sha": "0" * 40}))]
        for override in ({"candidate-run": str(RUN + 1)}, {"candidate-commit": "0" * 40},
                         {"candidate-sums-sha256": "0" * 64}, {"acceptance-sha256": "0" * 64},
                         {"physically-accepted": "x4-g x4pro-gp"}, {"candidate-run": None}):
            cases.append((obj, dict(self.gh.gets[obj], message=tag_message(self.record, **override))))
        cases.append((obj, dict(self.gh.gets[obj], message=tag_message(self.record) + "notes: x\n")))
        for path, value in cases:
            with self.subTest(value=value):
                saved = self.gh.gets[path]
                self.gh.gets[path] = value
                self.assert_refused()
                self.gh.gets[path] = saved

    def test_the_candidate_run_must_be_release_candidate_at_the_commit(self):
        path = f"repos/{fc.REPO}/actions/runs/{RUN}"
        for change in ({"path": ".github/workflows/release.yml"}, {"head_sha": "0" * 40},
                       {"conclusion": "failure"}, {"head_branch": "develop"}, {"event": "push"},
                       {"repository": {"full_name": "x/y"}}):
            with self.subTest(change=change):
                saved = self.gh.gets[path]
                self.gh.gets[path] = dict(saved, **change)
                self.assert_refused()
                self.gh.gets[path] = saved

    def test_the_clone_must_hold_the_same_annotated_tag(self):
        git(self.repo, "tag", "-d", TAG)
        self.assert_refused()
        git(self.repo, "tag", TAG, self.commit)  # lightweight
        self.assert_refused("annotated")
        git(self.repo, "tag", "-d", TAG)
        git(self.repo, "tag", "-a", TAG, "-m", "another")  # same commit, another tag object
        self.assert_refused("not the record's")

    def test_the_record_must_validate(self):
        bad = copy.deepcopy(self.record)
        bad["acceptance"]["packages"][7]["checks"]["dogear-v3-q1-pages"] = "FAIL"
        bad["acceptanceSha256"] = firmware.sha256(firmware.canonical(bad["acceptance"]))
        self.write_record(bad)
        self.assert_refused("not PASS")
        bad = copy.deepcopy(self.record)
        bad["acceptance"]["packages"].pop(1)
        bad["acceptanceSha256"] = firmware.sha256(firmware.canonical(bad["acceptance"]))
        self.write_record(bad)
        self.assert_refused()
        self.write_record(dict(self.record, acceptanceSha256="0" * 64))
        self.assert_refused("acceptanceSha256")
        self.write_record()
        elsewhere = self.root / "record.json"
        elsewhere.write_bytes(self.record_path().read_bytes())
        with self.assertRaises(firmware.Invalid):
            fc.sync(TAG, elsewhere, fc.Clone(self.repo), self.gh, root=self.root)

    def test_firmware_without_schema_3_cannot_authorise(self):
        self.files[fc.LIMITS_HEADER] = header(dict(LIMITS, kIssueSchemaMax=2))
        self.commit_files()
        git(self.repo, "tag", "-f", "-a", TAG, "-m", "moved")
        self.tag_object, self.commit = git(self.repo, "rev-parse", TAG), git(self.repo, "rev-parse", f"{TAG}^{{commit}}")
        self.write_record(verification_record(self.commit, self.tag_object))
        self.assert_refused("kIssueSchemaMax")

    def test_network_failure_refuses_and_main_exits_1(self):
        self.gh.fail = OSError("network unreachable")
        self.assert_refused("network")
        err = io.StringIO()
        with mock.patch.object(fc, "GitHub", lambda: self.gh), mock.patch.object(fc, "ROOT", self.root), \
                redirect_stderr(err), redirect_stdout(io.StringIO()):
            code = fc.main(["--sync", "--release", TAG, "--verification", str(self.record_path()),
                            "--firmware-clone", str(self.repo)])
        self.assertEqual(code, 1)
        self.assertIn("sync refused: OSError: network unreachable", err.getvalue())

    def test_a_plain_sync_is_refused(self):
        with redirect_stderr(io.StringIO()):
            self.assertEqual(fc.main(["--sync"]), 2)
            self.assertEqual(fc.main(["--sync", "--release", TAG]), 2)


if __name__ == "__main__":
    unittest.main()
