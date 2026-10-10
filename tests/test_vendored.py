"""What DOGEAR copies from the firmware, so it stands alone as a repository:
the size limits (data/firmware-contract.json), the device fonts (mock/fonts),
the acceptance schema (data/acceptance-schema/) and the trust roots
(data/dogear-trust-roots.pem, tested in test_chain_check).

The first group always runs. The parity group runs when a firmware clone is
available (DOGEAR_FIRMWARE_CLONE, or ../crosspoint-reader) and holds the
contract's commit: it compares every copy with that commit, read with
`git show`, never with a working tree. DOGEAR_FIRMWARE_COMMIT names another
commit to compare (a release candidate before its sync)."""

from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from dogear.publish import registry as regmod
from dogear.publish.staging import MAX_BODY, MAX_ENTRIES, MAX_HEADLINE, MAX_LINE, MAX_SHORT, MAX_URL

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import firmware_contract  # noqa: E402

CONTRACT = json.loads(firmware_contract.CONTRACT.read_text(encoding="utf-8"))
LIMITS = CONTRACT["limits"]
FONT_FILES = [f"{fam}/{fam}-{style}.ttf" for fam in ("NotoSans", "NotoSerif")
              for style in ("Regular", "Bold", "Italic", "BoldItalic")] + ["NotoSans/OFL.txt", "NotoSerif/OFL.txt"]
FIRMWARE_FONTS = "lib/EpdFont/builtinFonts/source"
CLONE = Path(os.environ.get("DOGEAR_FIRMWARE_CLONE") or ROOT.parent / "crosspoint-reader")
COMMIT = os.environ.get("DOGEAR_FIRMWARE_COMMIT") or CONTRACT["firmwareCommit"]
ROUTER_VECTORS = ROOT / "hosting" / "dogear-site" / "test" / "vectors.json"
TAG_VECTORS = ROOT / "tests" / "fixtures" / "firmware" / "tag-vectors.json"


def show(path: str) -> bytes:
    """`path` at the compared commit (never the clone's working tree)."""
    return subprocess.run(["git", "-C", str(CLONE), "show", f"{COMMIT}:{path}"], capture_output=True,
                          check=True).stdout


def has(path: str) -> bool:
    return subprocess.run(["git", "-C", str(CLONE), "cat-file", "-e", f"{COMMIT}:{path}"],
                          capture_output=True).returncode == 0


def commit_available() -> bool:
    try:
        return subprocess.run(["git", "-C", str(CLONE), "cat-file", "-e", f"{COMMIT}^{{commit}}"],
                              capture_output=True).returncode == 0
    except OSError:
        return False


class ContractTests(unittest.TestCase):
    def test_python_limits_equal_the_vendored_contract(self):
        self.assertEqual(LIMITS["kMaxEntries"], MAX_ENTRIES)
        self.assertEqual((LIMITS["kMaxShortLen"], LIMITS["kMaxLineLen"], LIMITS["kMaxHeadlineLen"],
                          LIMITS["kMaxBodyLen"], LIMITS["kMaxUrlLen"]),
                         (MAX_SHORT, MAX_LINE, MAX_HEADLINE, MAX_BODY, MAX_URL))
        self.assertEqual(LIMITS["kMaxIssueBytes"], regmod.MAX_ISSUE_BYTES)

    def test_manifest_and_keys_fit_the_device(self):
        week = "2026-11-02"
        sha = "f" * 64
        key = regmod.issue_key(f"dogear-{week}", sha)
        self.assertEqual(len(f"dogear-{week}"), LIMITS["kMaxIssueIdLen"])
        self.assertLessEqual(len(key), LIMITS["kMaxIssuePathLen"])
        self.assertEqual(len(sha), LIMITS["kMaxShaLen"])
        rev = {"rev": 99, "op": "correct", "txn": "20261102T060200-correct-000000000000", "issueId": f"dogear-{week}",
               "issueKey": key, "issueSha256": sha, "issueBytes": LIMITS["kMaxIssueBytes"],
               "webKey": regmod.web_key(dt.date.fromisoformat(week), sha), "webSha256": sha, "provenanceSha256": sha,
               "datasetSha256": sha, "generator": "g", "generatedAt": "t", "publishedAt": "t", "reason": "r"}
        reg = {"schemaVersion": 1, "txn": rev["txn"], "current": week, "hold": None,
               "weeks": {week: {"active": 0, "revisions": [dict(rev, rev=0)]}}}
        self.assertLessEqual(len(regmod.manifest_bytes(regmod.validate(reg))), LIMITS["kMaxManifestBytes"])


class FontTests(unittest.TestCase):
    def test_fonts_are_vendored_and_the_renderer_prefers_them(self):
        for name in FONT_FILES:
            self.assertTrue((ROOT / "mock" / "fonts" / name).is_file(), name)
        source = (ROOT / "mock" / "render_dogear.py").read_text(encoding="utf-8")
        self.assertIn('_VENDORED = Path(__file__).resolve().parent / "fonts"', source)
        self.assertIn("_VENDORED if _VENDORED.is_dir()", source)  # before the firmware fallback
        glyphs = json.loads(firmware_contract.GLYPHS.read_text(encoding="utf-8"))["fonts"]
        self.assertEqual(sorted(glyphs), sorted(firmware_contract.GLYPH_FONTS))
        self.assertTrue(all(len(r) > 10 for r in glyphs.values()))
        self.assertIn("DEVICE_GLYPHS.is_file()", source)  # read before any firmware header


class SyncedFilesTests(unittest.TestCase):
    def test_a_synced_contract_pins_its_vendored_schema(self):
        release = CONTRACT.get("release")
        if release is None:
            self.assertFalse((ROOT / "data" / "acceptance-schema").exists(), "a schema copy without a sync")
            return
        schema = (ROOT / "data" / "acceptance-schema" / "acceptance-v1.json").read_bytes()
        self.assertEqual(hashlib.sha256(schema).hexdigest(), release["acceptanceSchemaSha256"])


@unittest.skipUnless(commit_available(), "no firmware clone holding the contract's commit")
class FirmwareParityTests(unittest.TestCase):
    """The vendored copies against the compared commit, never a working tree."""

    def test_contract_equals_the_header(self):
        if "DOGEAR_FIRMWARE_COMMIT" in os.environ:
            self.skipTest("comparing another commit than the contract's")
        fresh = firmware_contract.parse_header(show(firmware_contract.LIMITS_HEADER).decode("utf-8"))
        self.assertEqual(LIMITS, fresh, "sync from the release: tools/firmware_contract.py --sync")

    def test_glyph_ranges_equal_the_font_headers(self):
        vendored = json.loads(firmware_contract.GLYPHS.read_text(encoding="utf-8"))["fonts"]
        for name in firmware_contract.GLYPH_FONTS:
            header = show(f"{firmware_contract.FONT_HEADERS}/{name}.h").decode("utf-8")
            self.assertEqual(vendored[name], firmware_contract.glyph_ranges(name, header), name)

    def test_fonts_are_byte_identical(self):
        for name in FONT_FILES:
            ours = hashlib.sha256((ROOT / "mock" / "fonts" / name).read_bytes()).hexdigest()
            theirs = hashlib.sha256(show(f"{FIRMWARE_FONTS}/{name}")).hexdigest()
            self.assertEqual(ours, theirs, name)

    def test_router_vectors_vendored_in_the_firmware_are_ours(self):
        path = "test/dogear/fixtures/RouterVectors.inc"
        if not has(path):
            self.assertNotIn("release", CONTRACT, "a synced release must carry the vendored router vectors")
            self.skipTest("this firmware commit predates the vendored router vectors")
        m = re.search(r"^// sourceSha256: ([0-9a-f]{64})$", show(path).decode("utf-8"), re.M)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), hashlib.sha256(ROUTER_VECTORS.read_bytes()).hexdigest(),
                         "re-vendor: test/dogear/fixtures/extract_router_vectors.py")

    def test_the_firmware_tag_parser_agrees_with_ours_on_every_vector(self):
        path = "scripts/release/candidate.py"
        if not has(path) or b"acceptance-sha256" not in show(path):
            self.assertNotIn("release", CONTRACT, "a synced release must carry the acceptance tag fields")
            self.skipTest("this firmware commit predates the acceptance tag fields")
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "candidate.py").write_bytes(show(path))
            spec = importlib.util.spec_from_file_location("firmware_candidate", Path(d) / "candidate.py")
            candidate = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(candidate)
        vectors = json.loads(TAG_VECTORS.read_text(encoding="utf-8"))
        self.assertEqual(vectors["packages"], candidate.packages())
        for case in vectors["cases"]:
            try:
                candidate.parse_tag_message(case["message"])
                accepted = True
            except candidate.Problem:
                accepted = False
            self.assertEqual(accepted, case["ok"], case["name"])

    def test_the_acceptance_schema_is_the_firmwares(self):
        path = firmware_contract.firmware.FIRMWARE_SCHEMA_PATH
        if not has(path):
            self.assertNotIn("release", CONTRACT)
            self.skipTest("this firmware commit predates the acceptance schema")
        fixture = (ROOT / "tests" / "fixtures" / "firmware" / "acceptance-v1.json").read_bytes()
        self.assertEqual(show(path), fixture, "the test fixture must be the firmware's schema")
        if "release" in CONTRACT and "DOGEAR_FIRMWARE_COMMIT" not in os.environ:
            self.assertEqual(show(path), (ROOT / "data" / "acceptance-schema" / "acceptance-v1.json").read_bytes())


if __name__ == "__main__":
    unittest.main()
