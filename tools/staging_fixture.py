"""Writes fixtures/staging-year.json: the synthetic dataset for the staging host.

One placeholder record for every month and day of the calendar, 29 February
included (it appears only in leap years), so a real-clock staging run can
build an issue for ANY Monday-Sunday week. Staging follows the same calendar
as production; nothing here sets or fakes a date.

This is test infrastructure, not editorial data, and not an editorial rule:
one record a day is fixture density, chosen so a week offers about seven
candidates and selection still has to choose (scores vary by a fixed rotation
of significance, recognition and type). Every record carries all the synthetic
markers of dogear/synthetic.py (id prefix, tag, FIXTURE approval by a
non-editor, example.org links), so production refuses this dataset and the
staging target refuses anything else.

    python3 tools/staging_fixture.py           # rewrite
    python3 tools/staging_fixture.py --check   # exit 1 if out of date
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dogear.dataset import fingerprint  # noqa: E402
from dogear.synthetic import APPROVER, LINK_PREFIX, TAG  # noqa: E402

OUT = ROOT / "fixtures" / "staging-year.json"
LEAP_YEAR = 2028  # any leap year: only its month/day pairs are used
FIXED_DAY = "2026-10-08"  # when the fixture's shape was set (its approvals' "on")

# A fixed seven-step rotation so neighbouring days differ: (significance, recognition, discoveryValue, type).
ROTATION = (
    (5, "anchor", 0, "publication"),
    (4, "core", 0, "literary-event"),
    (4, "discovery", 2, "publication"),
    (3, "core", 0, "publication"),
    (5, "core", 0, "literary-event"),
    (3, "discovery", 1, "publication"),
    (4, "anchor", 0, "literary-event"),
)


def days() -> list:
    start = dt.date(LEAP_YEAR, 1, 1)
    return [start + dt.timedelta(days=i) for i in range(366)]


def record(day: dt.date, n: int) -> dict:
    significance, recognition, discovery, kind = ROTATION[n % len(ROTATION)]
    label = f"{day.day} {day.strftime('%B')}"
    text = (f"Synthetic staging item for {label}. It exists only on the DOGEAR staging host, where the weekly "
            "publisher builds, checks and serves a real issue on the real calendar. Seeing it on a reader means the "
            "device fetched it over verified HTTPS. Placeholder text, not a literary record.")
    raw = {
        "id": f"staging-{day.month:02d}-{day.day:02d}", "month": day.month, "day": day.day, "year": 1950,
        "precision": "day", "dateBasis": "recorded", "claims": "straightforward", "type": kind,
        "person": f"Staging Writer {day.strftime('%b')} {day.day}", "work": f"Staging Work {day.strftime('%b')} {day.day}",
        "headline": f"Staging item: {label}",
        "copy": {"digest": text, "expanded": text, "allowSecondPage": False},
        "tags": [TAG], "significance": significance, "recognition": recognition, "discoveryValue": discovery,
        "phRelevance": 0, "seaRelevance": 0, "domain": "reading-culture", "status": "verified",
        "verifiedBy": "synthetic-fixture", "verifiedOn": FIXED_DAY,
        "sources": [{"title": "Example Domain (IANA reserved)", "url": LINK_PREFIX, "kind": "secondary"}],
        "links": [{"type": "read-more", "url": LINK_PREFIX, "title": f"Staging Work {day.strftime('%b')} {day.day}",
                   "provider": "example.org"}],
        "notes": "Synthetic staging fixture. Not a literary record; never part of the canonical dataset.",
        "area": "europe", "language": "en", "womenWriter": False, "approval": None,
    }
    raw["approval"] = {"by": APPROVER, "on": FIXED_DAY, "fingerprint": fingerprint(raw)}
    return raw


def build() -> dict:
    return {"schemaVersion": 3, "records": [record(d, n) for n, d in enumerate(days())]}


def render() -> str:
    """One record per line: small, and a change shows as one line in a diff."""
    data = build()
    lines = ",\n".join(json.dumps(r, ensure_ascii=False, sort_keys=True) for r in data["records"])
    return f'{{"schemaVersion": {data["schemaVersion"]}, "records": [\n{lines}\n]}}\n'


def main(argv: list) -> int:
    text = render()
    if "--check" in argv:
        if not OUT.exists() or OUT.read_text(encoding="utf-8") != text:
            print(f"{OUT} is out of date; run python3 tools/staging_fixture.py", file=sys.stderr)
            return 1
        return 0
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}: {len(build()['records'])} records")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
