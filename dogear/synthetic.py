"""Markers of synthetic test data, and the checks that keep it in staging.

The staging fixture (fixtures/staging-year.json) is test data like the unit
tests' records: placeholder people and works, links to the IANA-reserved
example.org, and a FIXTURE approval by APPROVER so the unchanged publisher
(which requires approval in every mode) can publish it to the staging host.
None of it is an editorial decision.

The declared markers (MARKERS; PUBLISHING.md §20g lists the same five):
  id        the id starts with "staging-" or "s2-"
  tag       tags include "staging-test"
  approver  the approval is by "synthetic-fixture-not-editorial"
  names     the person starts with "Staging Writer" or the work with "Staging Work"
  links     a source or link URL is on example.org (any one, for detection;
            every one, and at least one, for the staging shape)

* Production refuses any record with ANY marker (``synthetic_records``) and any
  revision whose records or issue carry one (``revision_problem``): synthetic
  data can never reach the production host, even if the fixture were copied
  over the canonical dataset or an old synthetic revision were rolled back to.
* The staging target refuses any record without ALL of them
  (``non_synthetic_records``), and any revision that is not wholly synthetic:
  the canonical dataset, or a real record pasted into the fixture, can never
  reach the staging host.
"""

from __future__ import annotations

import json
from typing import Optional
from urllib.parse import urlsplit

MARKERS = ("id", "tag", "approver", "names", "links")
APPROVER = "synthetic-fixture-not-editorial"
TAG = "staging-test"
ID_PREFIXES = ("staging-", "s2-")
NAME_PREFIXES = {"person": "Staging Writer", "work": "Staging Work"}
LINK_HOSTS = ("example.org", "www.example.org")
LINK_PREFIX = "https://example.org/"


def synthetic_url(url: object) -> bool:
    if not isinstance(url, str):
        return False
    try:
        return (urlsplit(url).hostname or "").lower() in LINK_HOSTS
    except ValueError:
        return False


def _records(dataset_text: str) -> list:
    try:
        payload = json.loads(dataset_text)
    except ValueError:
        return []
    records = payload.get("records") if isinstance(payload, dict) else None
    return [r for r in records if isinstance(r, dict)] if isinstance(records, list) else []


def _urls(raw: dict) -> list:
    return [x.get("url") for key in ("links", "sources") for x in (raw.get(key) or []) if isinstance(x, dict)]


def markers(raw: dict) -> dict:
    """{marker: present?} for one raw record."""
    approval = raw.get("approval") if isinstance(raw.get("approval"), dict) else {}
    urls = _urls(raw)
    return {
        "id": str(raw.get("id", "")).startswith(ID_PREFIXES),
        "tag": TAG in (raw.get("tags") or []),
        "approver": approval.get("by") == APPROVER,
        "names": any(str(raw.get(k) or "").startswith(p) for k, p in NAME_PREFIXES.items()),
        "links": any(synthetic_url(u) for u in urls),
    }


def fully_synthetic(raw: dict) -> bool:
    """The staging shape: every marker, and every URL on example.org."""
    urls = _urls(raw)
    names = all(str(raw.get(k) or "").startswith(p) for k, p in NAME_PREFIXES.items())
    return (all(markers(raw).values()) and names and bool(urls) and all(synthetic_url(u) for u in urls))


def synthetic_records(dataset_text: str) -> list:
    """Ids of records carrying any synthetic marker (production refuses them all)."""
    return [str(r.get("id")) for r in _records(dataset_text) if any(markers(r).values())]


def non_synthetic_records(dataset_text: str) -> list:
    """Ids of records without the full synthetic shape (the staging target refuses them)."""
    records = _records(dataset_text)
    if not records:
        return ["(no records)"]
    return [str(r.get("id")) for r in records if not fully_synthetic(r)]


def _entry_strings(issue: object) -> list:
    """Every string an issue's entries carry (not the issue's own edition URL)."""
    found, stack = [], [e for e in (issue.get("entries") or [])] if isinstance(issue, dict) else []
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
        elif isinstance(node, str):
            found.append(node)
    return found


def _issue_urls(issue: object) -> list:
    return [s for s in _entry_strings(issue) if s.startswith(("http://", "https://"))]


def revision_problem(separation: str, provenance: object, issue: object, dataset_text: str) -> Optional[str]:
    """Whether a revision (new, or one already in the registry) may become visible on
    this target, judged from the revision itself as well as the current dataset.

    separation "production": nothing synthetic, by record id, by the dataset's
    record, or by the issue's URLs. "staging": every rendered record is in the
    dataset with the full synthetic shape, and every external URL is example.org."""
    ids = [e.get("recordId") for e in (provenance.get("records") or []) if isinstance(e, dict)] \
        if isinstance(provenance, dict) else []
    by_id = {str(r.get("id")): r for r in _records(dataset_text)}
    urls = _issue_urls(issue)
    if separation == "production":
        marked = [i for i in ids if str(i).startswith(ID_PREFIXES) or any(markers(by_id.get(str(i), {})).values())]
        named = any(p in s for s in _entry_strings(issue) for p in NAME_PREFIXES.values())
        if marked or named or any(synthetic_url(u) for u in urls):
            return "it carries synthetic records; production shows no synthetic data"
        return None
    if separation == "staging":
        if not ids or any(not str(i).startswith(ID_PREFIXES) or not fully_synthetic(by_id.get(str(i), {}))
                          for i in ids) or not all(synthetic_url(u) for u in urls):
            return "it carries records that are not synthetic; staging shows only synthetic data"
        return None
    raise ValueError(f"unknown separation {separation!r}")


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
