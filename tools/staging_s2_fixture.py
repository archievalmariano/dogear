"""Writes fixtures/staging-s2.json: the synthetic dataset for the S2 staging cycle.

Three placeholder items for each of five drill weeks from Monday 1 March 2027
(weeks no device has cached). Each record carries a FIXTURE approval
(by "synthetic-fixture-not-editorial") so the unchanged publisher, whose
eligibility check requires approval in every mode, can publish it to the
staging bucket. These are test data like the unit-test records: never part of
the canonical dataset, never an editorial approval. Links go to
https://example.org/ (IANA reserved; its sub-paths answer 404, which the link
checker would block).

    python3 tools/staging_s2_fixture.py           # rewrite
    python3 tools/staging_s2_fixture.py --check   # exit 1 if out of date
"""

from __future__ import annotations

import copy
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dogear.dataset import fingerprint  # noqa: E402

OUT = ROOT / "fixtures" / "staging-s2.json"
FIRST_WEEK = dt.date(2027, 3, 1)
WEEKS = 5
APPROVER = "synthetic-fixture-not-editorial"


def record(week: dt.date, n: int) -> dict:
    day = week + dt.timedelta(days=2 * (n - 1))  # Monday, Wednesday, Friday
    label = f"{week.day} {week.strftime('%B')}"
    text = (f"Synthetic S2 staging item {n} for the week of {label}. It exists only on the DOGEAR staging "
            "host, where it exercises the publication transaction: promotion, correction, rollback and catch-up. "
            "Seeing it on a reader means the device fetched it over verified HTTPS and checked its hash. "
            "It is placeholder text, not a literary record.")
    raw = {
        "id": f"s2-{week.isoformat()}-{n}", "month": day.month, "day": day.day, "year": 2026,
        "precision": "day", "dateBasis": "recorded", "claims": "straightforward", "type": "publication",
        "person": f"Staging Writer {n}", "work": f"S2 Week {label} Item {n}",
        "headline": f"S2 staging: week of {label}, item {n}",
        "copy": {"digest": text, "expanded": text, "allowSecondPage": False},
        "tags": ["staging-test"], "significance": 5, "recognition": "anchor", "discoveryValue": 0,
        "phRelevance": 0, "seaRelevance": 0, "domain": "reading-culture", "status": "verified",
        "verifiedBy": "synthetic-fixture", "verifiedOn": "2026-10-07",
        "sources": [{"title": "Example Domain (IANA reserved)", "url": "https://example.org/", "kind": "secondary"}],
        "links": [{"type": "read-more", "url": "https://example.org/", "title": f"S2 Week {label} Item {n}",
                   "provider": "example.org"}],
        "notes": "Synthetic S2 staging fixture. Not a literary record; never part of the canonical dataset.",
        "area": "europe", "language": "en", "womenWriter": False, "approval": None,
    }
    raw["approval"] = {"by": APPROVER, "on": "2026-10-07", "fingerprint": fingerprint(raw)}
    return raw


def build() -> dict:
    weeks = [FIRST_WEEK + dt.timedelta(weeks=i) for i in range(WEEKS)]
    return {"schemaVersion": 3, "records": [record(w, n) for w in weeks for n in (1, 2, 3)]}


def render() -> str:
    return json.dumps(build(), indent=1, ensure_ascii=False) + "\n"


def main(argv: list) -> int:
    text = render()
    if "--check" in argv:
        if not OUT.exists() or OUT.read_text(encoding="utf-8") != text:
            print(f"{OUT} is out of date; run python3 tools/staging_s2_fixture.py", file=sys.stderr)
            return 1
        return 0
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}: {len(build()['records'])} records")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
