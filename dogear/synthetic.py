"""Markers of synthetic test data, and the two checks that keep it in staging.

The staging fixture (fixtures/staging-year.json) is test data like the unit
tests' records: placeholder people and works, links to the IANA-reserved
example.org, and a FIXTURE approval by APPROVER so the unchanged publisher
(which requires approval in every mode) can publish it to the staging host.
None of it is an editorial decision.

* Production refuses any dataset holding a record with ANY synthetic marker
  (``synthetic_records``): synthetic data can never reach the production host,
  even if the fixture were copied over the canonical dataset.
* The staging target refuses any record that lacks ALL of them
  (``non_synthetic_records``): the canonical dataset, or a real record pasted
  into the fixture, can never reach the staging host.
"""

from __future__ import annotations

import json

APPROVER = "synthetic-fixture-not-editorial"
TAG = "staging-test"
ID_PREFIXES = ("staging-", "s2-")
LINK_PREFIX = "https://example.org/"


def _records(dataset_text: str) -> list:
    try:
        payload = json.loads(dataset_text)
    except ValueError:
        return []
    records = payload.get("records") if isinstance(payload, dict) else None
    return [r for r in records if isinstance(r, dict)] if isinstance(records, list) else []


def _markers(raw: dict) -> dict:
    approval = raw.get("approval") if isinstance(raw.get("approval"), dict) else {}
    urls = [x.get("url") for key in ("links", "sources") for x in (raw.get(key) or []) if isinstance(x, dict)]
    return {
        "approver": approval.get("by") == APPROVER,
        "tag": TAG in (raw.get("tags") or []),
        "id": str(raw.get("id", "")).startswith(ID_PREFIXES),
        "links": bool(urls) and all(isinstance(u, str) and u.startswith(LINK_PREFIX) for u in urls),
    }


def synthetic_records(dataset_text: str) -> list:
    """Ids of records carrying any synthetic marker (production refuses them all)."""
    return [str(r.get("id")) for r in _records(dataset_text)
            if any(v for k, v in _markers(r).items() if k != "links")]


def non_synthetic_records(dataset_text: str) -> list:
    """Ids of records missing any synthetic marker (the staging target refuses them)."""
    records = _records(dataset_text)
    if not records:
        return ["(no records)"]
    return [str(r.get("id")) for r in records if not all(_markers(r).values())]


def main(argv: list) -> int:
    """``python -m dogear.synthetic --none PATH``: exit 1 if PATH holds any synthetic record
    (the production editorial gate). Prints a count only, never record ids."""
    if len(argv) != 2 or argv[0] != "--none":
        print("usage: python -m dogear.synthetic --none PATH")
        return 2
    with open(argv[1], encoding="utf-8") as f:
        found = synthetic_records(f.read())
    print(f"{len(found)} synthetic record(s)")
    return 1 if found else 0


if __name__ == "__main__":
    import sys
    raise SystemExit(main(sys.argv[1:]))
