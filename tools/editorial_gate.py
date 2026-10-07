"""The production editorial gate: run the private editorial checks against THIS
public checkout and the editorial checkout at dataset/, and print only pass/fail
and counts. The workflows run it in the `editorial-gate` job; production may
start only after it passes, and only on the same pair of commits it printed.

    python3 tools/editorial_gate.py [--dataset DIR]

Checks: the editorial tests (dataset/tests), canonical validation, no synthetic
records (dogear/synthetic.py), the device fit check, and the sample
regeneration (dataset/tools/check_samples.py). Their
output can quote unpublished records, so it is captured and never printed (the
Actions log of a public repository is public); a failure says only which check
failed. Run the editorial checks privately to see why.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(argv: list, cwd: Path, env: dict) -> tuple:
    try:
        proc = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=900)
    except (OSError, subprocess.TimeoutExpired) as err:
        return 1, type(err).__name__
    return proc.returncode, proc.stdout + proc.stderr


def _sha(path: Path) -> str:
    out = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True, text=True)
    return out.stdout.strip() if out.returncode == 0 else "unknown"


def main(argv: list) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", type=Path, default=ROOT / "dataset")
    args = ap.parse_args(argv)
    editorial = args.dataset.resolve()
    if not (editorial / "data" / "literary-dates.json").is_file():
        print("editorial gate: FAILED (no editorial checkout at dataset/)")
        return 1
    env = dict(os.environ, DOGEAR_SRC=str(ROOT), PYTHONDONTWRITEBYTECODE="1")
    fit_python = os.environ.get("DOGEAR_FIT_PYTHON") or str(ROOT / "mock" / ".venv" / "bin" / "python")
    dataset = str(editorial / "data" / "literary-dates.json")
    checks = [
        ("editorial tests", [sys.executable, "-m", "unittest", "discover", "-s", str(editorial / "tests")], editorial,
         lambda out: (re.search(r"^Ran (\d+) tests?", out, re.M) or [None, "?"])[1] + " tests"),
        ("canonical validation", [sys.executable, "-m", "dogear.cli", "--dataset", dataset, "--affinity",
                                  str(editorial / "data" / "affinity.json"), "validate"], ROOT,
         lambda out: "dataset valid"),  # no record count: the dataset's size stays private
        ("no synthetic records", [sys.executable, "-m", "dogear.synthetic", "--none", dataset], ROOT,
         lambda out: "none"),  # staging fixtures never reach production (dogear/synthetic.py)
        ("fit check", [fit_python, str(ROOT / "mock" / "render_dogear.py"), "--fitcheck", dataset], ROOT,
         lambda out: "0 overflows"),
        ("samples", [sys.executable, str(editorial / "tools" / "check_samples.py")], editorial,
         lambda out: f"{len(re.findall(r'^same ', out, re.M))} files identical"),
    ]
    print(f"editorial gate: public {_sha(ROOT)} + editorial {_sha(editorial)}")
    failed = 0
    for name, cmd, cwd, count in checks:
        code, out = _run(cmd, cwd, env)
        if code == 0:
            print(f"  {name}: passed ({count(out)})")
        else:
            failed += 1
            print(f"  {name}: FAILED (details withheld from this public log; run the editorial checks privately)")
    print(f"editorial gate: {'passed' if not failed else f'FAILED ({failed} of {len(checks)} checks)'}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
