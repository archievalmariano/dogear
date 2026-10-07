"""The independent eligibility check: may these rendered records be published?

Deliberately separate from the selector. It reads the canonical dataset afresh
and, for every record a staged artifact renders (the digest and ALSO THIS
WEEK), requires that it exists, is publishable (verified, approved at its
current fingerprint, adequately sourced) and is the exact version recorded in
the artifact's provenance. Anything missing or unexpected is a problem.
"""

from __future__ import annotations

import json

from ..dataset import DatasetError, fingerprint, parse_dataset

SECTIONS = ("digest", "also")


def check(provenance: object, dataset_text: str, issue: object) -> list[str]:
    problems: list[str] = []
    if not isinstance(provenance, dict) or not isinstance(provenance.get("records"), list):
        return ["provenance missing or malformed"]
    if not isinstance(issue, dict) or not isinstance(issue.get("entries"), list):
        return ["issue malformed"]
    if issue.get("preview") is not False:
        problems.append("the issue is a proof (preview is not false)")
    try:
        parsed = parse_dataset(dataset_text)
        raw_records = {r["id"]: r for r in json.loads(dataset_text)["records"]}
    except DatasetError as err:
        return [f"dataset invalid: {err}"]
    records = {r.id: r for r in parsed.records}

    seen: set[str] = set()
    digest_ids: list[str] = []
    for i, entry in enumerate(provenance["records"]):
        if not isinstance(entry, dict) or set(entry) != {"recordId", "fingerprint", "section", "position"}:
            problems.append(f"provenance entry {i} malformed")
            continue
        rid, section = entry["recordId"], entry["section"]
        if section not in SECTIONS:
            problems.append(f"{rid}: unknown section {section!r}")
        if rid in seen:
            problems.append(f"{rid}: rendered twice")
        seen.add(rid)
        if section == "digest":
            digest_ids.append(rid)
        raw, record = raw_records.get(rid), records.get(rid)
        if raw is None or record is None:
            problems.append(f"{rid}: not in the dataset")
            continue
        current = fingerprint(raw)
        approval = raw.get("approval") or {}
        if raw.get("status") != "verified":
            problems.append(f"{rid}: status is {raw.get('status')}")
        if approval.get("fingerprint") != current:
            problems.append(f"{rid}: not approved at its current content")
        if not record.publishable:
            problems.append(f"{rid}: not publishable")
        if entry["fingerprint"] != current:
            problems.append(f"{rid}: the dataset changed since this artifact was generated")

    positions = [e["position"] for e in provenance["records"] if isinstance(e, dict) and e.get("section") == "digest"]
    if positions != list(range(1, len(positions) + 1)):
        problems.append("digest positions are not 1..n in order")
    issue_ids = [e.get("id") for e in issue["entries"] if isinstance(e, dict)]
    if digest_ids != issue_ids:
        problems.append("provenance digest does not match the issue's entries")
    return problems
