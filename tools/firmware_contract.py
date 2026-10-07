"""The firmware contract DOGEAR's output must fit, vendored so DOGEAR stands alone:
- every constant in the firmware's DogearLimits.h → data/firmware-contract.json;
- the Unicode ranges the device's built-in fonts can draw (the fit check's glyph
  test) → mock/fonts/device-glyphs.json.

    python3 tools/firmware_contract.py --sync    # re-read the header (firmware beside this repo)

tests/test_vendored.py checks the Python limits against the vendored file
always, and the vendored file against the header while the firmware checkout
is present. Trust roots follow the same pattern (tools/chain_check.py).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "data" / "firmware-contract.json"
FIRMWARE = ROOT.parent / "crosspoint-reader"
HEADER = FIRMWARE / "src" / "activities" / "dogear" / "DogearLimits.h"
BUILTIN = FIRMWARE / "lib" / "EpdFont" / "builtinFonts"
GLYPHS = ROOT / "mock" / "fonts" / "device-glyphs.json"
GLYPH_FONTS = ("notoserif_14_regular", "notoserif_18_bold")  # what mock/render_dogear.py fitcheck reads
_CONST = re.compile(r"constexpr\s+size_t\s+(k\w+)\s*=\s*([0-9]+(?:\s*\*\s*[0-9]+)*)\s*;")


def parse_header(text: str) -> dict:
    """{name: value} for every `constexpr size_t kName = N [* M];`."""
    out = {}
    for name, expr in _CONST.findall(text):
        value = 1
        for factor in expr.split("*"):
            value *= int(factor)
        out[name] = value
    if not out:
        raise ValueError("no constants found in DogearLimits.h")
    return out


def glyph_ranges(font_name: str, text: str) -> list:
    """[[first, last], ...] from a generated font header's Intervals table."""
    block = text.split(f"{font_name}Intervals[] = {{", 1)[1].split("};", 1)[0]
    return [[int(a, 16), int(b, 16)] for a, b in re.findall(r"\{\s*(0x[0-9A-Fa-f]+),\s*(0x[0-9A-Fa-f]+),", block)]


def main(argv: list) -> int:
    if "--sync" not in argv:
        print(__doc__)
        return 2
    limits = parse_header(HEADER.read_text(encoding="utf-8"))
    commit = subprocess.run(["git", "-C", str(FIRMWARE), "rev-parse", "--short=8", "HEAD"],
                            capture_output=True, text=True).stdout.strip() or "unknown"
    CONTRACT.write_text(json.dumps({
        "_note": "Generated from crosspoint-reader src/activities/dogear/DogearLimits.h by "
                 "tools/firmware_contract.py --sync; do not edit.",
        "firmwareCommit": commit, "limits": dict(sorted(limits.items()))}, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {CONTRACT.relative_to(ROOT)}: {len(limits)} limits from firmware {commit}")
    fonts = {name: glyph_ranges(name, (BUILTIN / f"{name}.h").read_text(encoding="utf-8")) for name in GLYPH_FONTS}
    GLYPHS.write_text(json.dumps({
        "_note": "Generated from crosspoint-reader lib/EpdFont/builtinFonts/*.h by "
                 "tools/firmware_contract.py --sync; do not edit.",
        "firmwareCommit": commit, "fonts": fonts}, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {GLYPHS.relative_to(ROOT)}: " + ", ".join(f"{k} {len(v)} ranges" for k, v in fonts.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
