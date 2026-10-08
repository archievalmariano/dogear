"""The date and edition invariant for any revision that is, or is about to be, visible
(PUBLISHING.md §21). One function, called at stage, at the staged-week re-check, in
the guard before every registry write (launch, promote, correct, and each retry),
for every revision rollback, restore or resume makes visible, while validating
history before it feeds a quiet pool, and by the read-only reset preflight.

A regular issue (schema 2, no ``quiet``) holds only entries whose anniversary falls
in its own Monday-Sunday week. A quiet issue (schema 3, the exact ``quiet`` copy)
holds 3-5 earlier-dated entries, each at its occurrence in the week whose "Also
this week" recorded it, and nothing in "Also this week". The edition must agree
across the issue, its provenance and its history line.
"""

from __future__ import annotations

import datetime as dt
from typing import Optional

from ..dataset import SUBJECT_ID_RE
from ..quiet import (EDITIONS, QUIET_MAX, QUIET_MIN, QUIET_SCHEMA_VERSION, REGULAR_SCHEMA_VERSION, QuietSource,
                     quiet_object)
from ..select import MAX_ENTRIES


def _ids(provenance: dict, section: str) -> list:
    return [r.get("recordId") for r in provenance.get("records") or [] if isinstance(r, dict)
            and r.get("section") == section]


def subjects_problem(subjects: object, digest_ids: list) -> Optional[str]:
    """A ``subjects`` list must name exactly the digest records, in order, each with a
    well-formed id or null for its person and its work."""
    if not isinstance(subjects, list) or len(subjects) != len(digest_ids):
        return "subjects must list every digest record"
    for entry, rid in zip(subjects, digest_ids):
        if not isinstance(entry, dict) or set(entry) != {"recordId", "personId", "workId"}:
            return "a subjects entry has the wrong fields"
        if entry["recordId"] != rid:
            return "subjects are not in digest order"
        for key in ("personId", "workId"):
            value = entry[key]
            if value is not None and not (isinstance(value, str) and SUBJECT_ID_RE.match(value)):
                return f"subjects: {key} {value!r} is not a stable identity"
    return None


def revision_problems(monday: dt.date, issue: object, provenance: object, line: Optional[dict] = None) -> list:
    """Every way this revision breaks the date/edition invariant. [] means it holds.
    ``line`` is the history line (committed, or proposed before a write); None while
    staging, before any line exists."""
    if not isinstance(issue, dict) or not isinstance(provenance, dict):
        return ["issue or provenance malformed"]
    problems = []
    edition = provenance.get("edition", "regular")
    if edition not in EDITIONS:
        return [f"unknown edition {edition!r}"]
    quiet = edition == "quiet"
    end = monday + dt.timedelta(days=6)
    if issue.get("issueId") != f"dogear-{monday.isoformat()}":
        problems.append("the issue id is not this week's")
    if issue.get("rangeStart") != monday.isoformat() or issue.get("rangeEnd") != end.isoformat():
        problems.append("the issue's range is not this Monday-Sunday week")

    want_schema = QUIET_SCHEMA_VERSION if quiet else REGULAR_SCHEMA_VERSION
    if issue.get("schemaVersion") != want_schema:
        problems.append(f"a {edition} issue must be schema {want_schema}, not {issue.get('schemaVersion')!r}")
    if quiet and issue.get("quiet") != quiet_object():
        problems.append("a quiet issue must carry the approved heading and note exactly")
    if not quiet and "quiet" in issue:
        problems.append("a regular issue carries quiet copy")

    entries = issue.get("entries") if isinstance(issue.get("entries"), list) else []
    digest = _ids(provenance, "digest")
    also = _ids(provenance, "also")
    if [e.get("id") for e in entries if isinstance(e, dict)] != digest:
        problems.append("the issue's entries are not the provenance digest")
    dates = []
    for e in entries:
        try:
            dates.append((e.get("id"), dt.date.fromisoformat(e.get("date"))))
        except (TypeError, ValueError, AttributeError):
            problems.append("an entry has no valid date")
    if quiet:
        if not QUIET_MIN <= len(entries) <= QUIET_MAX:
            problems.append(f"a quiet issue holds {QUIET_MIN}-{QUIET_MAX} entries, not {len(entries)}")
        if also:
            problems.append("a quiet issue has no Also this week")
        try:
            source = QuietSource.from_json((provenance.get("inputs") or {}).get("quietSource"))
        except ValueError as err:
            problems.append(f"a quiet issue's provenance needs a valid quietSource ({err})")
            source = None
        if source is not None:
            recorded = {}
            for rid, week in source.pool:
                recorded.setdefault(rid, week)
            for rid, day in dates:
                week = recorded.get(rid)
                if week is None:
                    problems.append(f"{rid}: not in the quiet pool")
                    continue
                start = dt.date.fromisoformat(week)
                if not (start <= day <= start + dt.timedelta(days=6)) or day >= monday:
                    problems.append(f"{rid}: its date is not its occurrence in the week that recorded it")
                if rid in source.excluded:
                    problems.append(f"{rid}: was published as a full item")
    else:
        if (provenance.get("inputs") or {}).get("quietSource") is not None:
            problems.append("a regular issue's provenance carries a quiet source")
        if not 1 <= len(entries) <= MAX_ENTRIES:
            problems.append(f"a regular issue holds 1-{MAX_ENTRIES} entries, not {len(entries)}")
        for rid, day in dates:
            if not monday <= day <= end:
                problems.append(f"{rid}: a regular issue's entry falls outside its week")

    if "subjects" in provenance:
        problem = subjects_problem(provenance["subjects"], digest)
        if problem:
            problems.append(problem)

    if line is not None:
        if line.get("edition", "regular") != edition:
            problems.append("the history line's edition differs from the provenance")
        if line.get("recordIds") != digest:
            problems.append("the history line's recordIds differ from the provenance digest")
        if line.get("alsoIds") != also:
            problems.append("the history line's alsoIds differ from the provenance")
        if quiet and line.get("alsoIds") != []:
            problems.append("a quiet history line records Also this week")
        if ("subjects" in line) != ("subjects" in provenance):
            problems.append("subjects are recorded on only one of the history line and the provenance")
        elif "subjects" in line and line["subjects"] != provenance["subjects"]:
            problems.append("the history line's subjects differ from the provenance")
    return problems
