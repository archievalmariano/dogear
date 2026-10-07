"""What DOGEAR copies from the firmware, so it stands alone as a repository:
the size limits (data/firmware-contract.json), the device fonts (mock/fonts)
and the trust roots (data/dogear-trust-roots.pem, tested in test_chain_check).

The first group always runs. The parity group runs while a firmware checkout
sits beside this repository and fails if a copy has drifted from it."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import sys
import unittest
from pathlib import Path

from dogear.publish import registry as regmod
from dogear.publish.staging import MAX_BODY, MAX_ENTRIES, MAX_HEADLINE, MAX_LINE, MAX_SHORT, MAX_URL

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import firmware_contract  # noqa: E402

LIMITS = json.loads(firmware_contract.CONTRACT.read_text(encoding="utf-8"))["limits"]
FONT_FILES = [f"{fam}/{fam}-{style}.ttf" for fam in ("NotoSans", "NotoSerif")
              for style in ("Regular", "Bold", "Italic", "BoldItalic")] + ["NotoSans/OFL.txt", "NotoSerif/OFL.txt"]
FIRMWARE_FONTS = firmware_contract.FIRMWARE / "lib" / "EpdFont" / "builtinFonts" / "source"


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


@unittest.skipUnless(firmware_contract.HEADER.exists(), "firmware checkout not beside this repository")
class FirmwareParityTests(unittest.TestCase):
    def test_contract_equals_the_header(self):
        fresh = firmware_contract.parse_header(firmware_contract.HEADER.read_text(encoding="utf-8"))
        self.assertEqual(LIMITS, fresh, "run python3 tools/firmware_contract.py --sync")

    def test_glyph_ranges_equal_the_font_headers(self):
        vendored = json.loads(firmware_contract.GLYPHS.read_text(encoding="utf-8"))["fonts"]
        for name in firmware_contract.GLYPH_FONTS:
            header = (firmware_contract.BUILTIN / f"{name}.h").read_text(encoding="utf-8")
            self.assertEqual(vendored[name], firmware_contract.glyph_ranges(name, header), name)

    def test_fonts_are_byte_identical(self):
        for name in FONT_FILES:
            ours = hashlib.sha256((ROOT / "mock" / "fonts" / name).read_bytes()).hexdigest()
            theirs = hashlib.sha256((FIRMWARE_FONTS / name).read_bytes()).hexdigest()
            self.assertEqual(ours, theirs, name)


if __name__ == "__main__":
    unittest.main()
