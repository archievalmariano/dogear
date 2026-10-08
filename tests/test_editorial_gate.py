"""tools/editorial_gate.py against a synthetic stand-in for the private editorial
repository: it passes on good checks, and on failure names only the failed check;
nothing the checks print reaches its output (sentinel text)."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SENTINEL = "SENTINEL-EDITORIAL-9b4e"


class EditorialGateTests(unittest.TestCase):
    @staticmethod
    def stand_in() -> str:
        """A canonical stand-in: the staging fixture with every synthetic marker removed."""
        data = json.loads((ROOT / "fixtures" / "staging-year.json").read_text(encoding="utf-8"))
        for r in data["records"]:
            r["id"] = r["id"].replace("staging-", "standin-")
            r["tags"], r["approval"] = [], None
            r["person"], r["work"] = r["person"].replace("Staging", "Standin"), r["work"].replace("Staging", "Standin")
            for x in r["links"] + r["sources"]:
                x["url"] = "https://library.test/"
        return json.dumps(data)

    def make(self, test_ok=True, samples_ok=True, fit_ok=True, synthetic=False) -> tuple:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        ed = tmp / "editorial"
        (ed / "data").mkdir(parents=True)
        (ed / "tests").mkdir()
        (ed / "tools").mkdir()
        if synthetic:  # the staging fixture as it is, copied over the canonical dataset
            shutil.copy(ROOT / "fixtures" / "staging-year.json", ed / "data" / "literary-dates.json")
        else:
            (ed / "data" / "literary-dates.json").write_text(self.stand_in(), encoding="utf-8")
        shutil.copy(ROOT / "fixtures" / "staging-affinity.json", ed / "data" / "affinity.json")
        (ed / "tests" / "test_stand_in.py").write_text(
            "import unittest\nclass T(unittest.TestCase):\n    def test_a(self):\n"
            f"        print('{SENTINEL} in a record')\n        self.assertTrue({test_ok}, '{SENTINEL}')\n"
            "    def test_b(self):\n        pass\n")
        (ed / "tools" / "check_samples.py").write_text(
            f"print('same  samples/a.json')\nprint('{SENTINEL}')\nraise SystemExit({0 if samples_ok else 1})\n")
        fit = tmp / "fit-python"
        fit.write_text(f"#!/bin/sh\necho '{SENTINEL} overflows'\nexit {0 if fit_ok else 1}\n")
        fit.chmod(fit.stat().st_mode | stat.S_IEXEC)
        subprocess.run(["git", "init", "-q", str(ed)], check=True)
        return ed, fit

    def gate(self, ed: Path, fit: Path) -> tuple:
        run = subprocess.run([sys.executable, str(ROOT / "tools" / "editorial_gate.py"), "--dataset", str(ed)],
                             capture_output=True, text=True, env=dict(os.environ, DOGEAR_FIT_PYTHON=str(fit)))
        return run.returncode, run.stdout + run.stderr

    def test_passes_with_counts_only(self):
        code, out = self.gate(*self.make())
        self.assertEqual(code, 0, out)
        self.assertIn("editorial tests: passed (2 tests)", out)
        self.assertIn("canonical validation: passed (dataset valid)", out)
        self.assertIn("samples: passed (1 files identical)", out)
        self.assertIn("editorial gate: passed", out)
        self.assertNotIn(SENTINEL, out)
        self.assertNotRegex(out, r"\d+ records")  # the dataset's size stays private

    def test_failures_name_the_check_and_nothing_else(self):
        for kwargs, failed in (({"test_ok": False}, "editorial tests"), ({"samples_ok": False}, "samples"),
                               ({"fit_ok": False}, "fit check"), ({"synthetic": True}, "no synthetic records")):
            code, out = self.gate(*self.make(**kwargs))
            self.assertEqual(code, 1, out)
            self.assertIn(f"{failed}: FAILED (details withheld", out)
            self.assertIn("editorial gate: FAILED (1 of 5 checks)", out)
            self.assertNotIn(SENTINEL, out)

    def test_no_editorial_checkout_fails(self):
        code, out = subprocess.run([sys.executable, str(ROOT / "tools" / "editorial_gate.py"), "--dataset",
                                    str(ROOT / "no-such-dir")], capture_output=True, text=True).returncode, ""
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
