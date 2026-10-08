"""Quiet-week inputs from committed publication history (PUBLISHING.md §21).

No new publication state: every content line (a committed launch, promote or
correct) already records its digest ``recordIds`` and its "Also this week"
``alsoIds``. Before a line contributes anything it is validated against the live
registry revision it claims and against that revision's hash-pinned provenance,
issue and web bytes (``validated_lines``); any mismatch is ``CorruptState`` and
nothing is published. Corrected-away and rolled-back revisions stay in the
registry, so their lines validate and still count.

Subject history uses stable identities only: the ``subjects`` written with every
new content line, or, for a legacy line written before identities existed, a
reviewed attestation bound to that publication's original frozen dataset
(``attested_subjects``). Neither is ever inferred from current record text. A
line in the subject window with neither leaves the source incomplete, and the
quiet pass holds.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from typing import Optional

from ..dataset import SUBJECT_ID_RE, DatasetError, fingerprint, parse_dataset
from ..quiet import QUIET_POOL_DAYS, QUIET_SUBJECT_DAYS, QuietSource
from . import registry as regmod
from .edition import revision_problems, subjects_problem
from .pubdata import CorruptState
from .store import StoreError

CONTENT_OPS = ("launch", "promote", "correct")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def content_lines(pubdata) -> list:
    """Committed launch/promote/correct lines, in commit order."""
    return [h for h in pubdata.history() if h["status"] == "committed" and h.get("op") in CONTENT_OPS]


def line_problems(line: dict, reg: dict, pubdata, store) -> list:
    """Why a content line does not match its registry revision and the revision's
    hash-pinned provenance, issue and web bytes. [] means it validates."""
    try:
        week = line["week"]
        rev_no = line["rev"]
        entry = reg["weeks"][week]
        if not isinstance(rev_no, int) or not 0 <= rev_no < len(entry["revisions"]):
            raise KeyError("rev")
        rev = entry["revisions"][rev_no]
    except (KeyError, TypeError):
        return [f"{line.get('txn')}: no registry revision {line.get('week')} rev {line.get('rev')}"]
    where = f"{week} revision {rev_no}"
    problems = []
    for key in ("txn", "issueSha256", "webSha256", "op"):
        if rev.get(key) != line.get(key):
            problems.append(f"{where}: the history line's {key} differs from the registry")
    prov_bytes = pubdata.read_published_provenance(week, rev_no)
    if prov_bytes is None or regmod.sha256(prov_bytes) != rev["provenanceSha256"]:
        return problems + [f"{where}: no provenance matching the registry's hash"]
    try:
        provenance = json.loads(prov_bytes)
    except ValueError:
        return problems + [f"{where}: provenance unreadable"]
    artifacts = provenance.get("artifacts") or {}
    if artifacts.get("issueSha256") != rev["issueSha256"] or artifacts.get("webSha256") != rev["webSha256"]:
        problems.append(f"{where}: the provenance's artifact hashes differ from the registry")
    issue = None
    for kind, key, sha in (("issue", "issueKey", "issueSha256"), ("web", "webKey", "webSha256")):
        try:
            obj = store.get(rev[key])
        except StoreError as err:
            return problems + [f"{where}: cannot read its {kind} ({err})"]
        if obj is None or regmod.sha256(obj.data) != rev[sha]:
            problems.append(f"{where}: its stored {kind} is missing or altered")
        elif kind == "issue":
            try:
                issue = json.loads(obj.data)
            except ValueError:
                problems.append(f"{where}: its issue is unreadable")
    if issue is not None:
        problems += [f"{where}: {p}" for p in revision_problems(dt.date.fromisoformat(week), issue, provenance, line)]
    if "subjects" in line:
        bad = subjects_problem(line["subjects"], line.get("recordIds") or [])
        if bad:
            problems.append(f"{where}: {bad}")
    return problems


def validated_lines(reg: Optional[dict], pubdata, store) -> list:
    """Every content line, each validated; CorruptState on the first that is not."""
    lines = content_lines(pubdata)
    if lines and reg is None:
        raise CorruptState("publication history exists, but the store has no registry")
    for line in lines:
        problems = line_problems(line, reg, pubdata, store)
        if problems:
            raise CorruptState("history cannot be used for a quiet week: " + "; ".join(problems))
    return lines


def attestation_problems(att: object, line: dict, reg: dict, pubdata) -> list:
    """Why an attestation does not prove the identities of a legacy line's digest."""
    if not isinstance(att, dict) or set(att) != {"txn", "sourceDatasetSha256", "subjects", "attestedAt", "by"}:
        return ["malformed attestation"]
    if "subjects" in line:
        return ["the line already records its subjects"]
    rev = reg["weeks"][line["week"]]["revisions"][line["rev"]]
    if att["sourceDatasetSha256"] != rev.get("datasetSha256"):
        return ["the attested source dataset is not the one the registry recorded"]
    evidence = pubdata.read_evidence_dataset(line["txn"])
    if evidence is None or _sha(evidence) != att["sourceDatasetSha256"]:
        return ["the original frozen dataset is missing or altered"]
    try:
        parse_dataset(evidence.decode("utf-8"))
        originals = {r["id"]: r for r in json.loads(evidence)["records"]}
    except (DatasetError, UnicodeDecodeError, ValueError, KeyError, TypeError):
        return ["the original frozen dataset is unreadable"]
    prov = json.loads(pubdata.read_published_provenance(line["week"], line["rev"]))
    pinned = {r["recordId"]: r["fingerprint"] for r in prov["records"] if r["section"] == "digest"}
    subjects = att["subjects"]
    if not isinstance(subjects, list) or [s.get("recordId") for s in subjects if isinstance(s, dict)] != line["recordIds"]:
        return ["the attestation does not cover exactly the line's digest, in order"]
    for s in subjects:
        if set(s) != {"recordId", "fingerprint", "personId", "workId"}:
            return ["an attested subject has the wrong fields"]
        rid, raw = s["recordId"], originals.get(s["recordId"])
        if raw is None or s["fingerprint"] != pinned.get(rid) or fingerprint(raw) != s["fingerprint"]:
            return [f"{rid}: not the record version the revision published"]
        for name_field, id_field in (("person", "personId"), ("work", "workId")):
            sid = s[id_field]
            if (raw.get(name_field) is None) != (sid is None):
                return [f"{rid}: {id_field} presence does not match the original record's {name_field}"]
            if sid is not None and not (isinstance(sid, str) and SUBJECT_ID_RE.match(sid)):
                return [f"{rid}: {id_field} is not a stable identity"]
    return []


def attested_subjects(lines: list, reg: Optional[dict], pubdata) -> dict:
    """{txn: subjects} for legacy lines whose attestation is proven. A duplicate or
    conflicting attestation, or one whose evidence is gone, proves nothing."""
    by_txn: dict = {}
    for att in pubdata.read_attestations():
        by_txn.setdefault(att.get("txn") if isinstance(att, dict) else None, []).append(att)
    lines_by_txn = {line["txn"]: line for line in lines}
    proven = {}
    for txn, atts in by_txn.items():
        line = lines_by_txn.get(txn)
        if line is None or len(atts) != 1 or reg is None:
            continue
        if not attestation_problems(atts[0], line, reg, pubdata):
            proven[txn] = [{"recordId": s["recordId"], "personId": s["personId"], "workId": s["workId"]}
                           for s in atts[0]["subjects"]]
    return proven


def build_source(lines: list, attested: dict, monday: dt.date) -> QuietSource:
    """The §3.3 sets for target week W (``monday``), from validated lines."""
    week_of = {id(line): dt.date.fromisoformat(line["week"]) for line in lines}
    quiet = lambda line: line.get("edition", "regular") == "quiet"  # noqa: E731

    excluded, retainable, pool = set(), set(), set()
    for line in lines:
        week = week_of[id(line)]
        if quiet(line) and week == monday:
            retainable.update(line["recordIds"])
        else:  # regular digest of any week (W included), or quiet in another week
            excluded.update(line["recordIds"])
        if monday - dt.timedelta(days=QUIET_POOL_DAYS) <= week < monday:
            pool.update((rid, line["week"]) for rid in line["alsoIds"])
    retainable -= excluded

    complete = True
    identities, same_week = set(), set()
    for line in lines:
        week = week_of[id(line)]
        in_window = (monday - dt.timedelta(days=QUIET_SUBJECT_DAYS) <= week < monday or week > monday
                     or (week == monday and not quiet(line)))
        same_week_quiet = week == monday and quiet(line)
        if not (in_window or same_week_quiet):
            continue
        subjects = line.get("subjects", attested.get(line["txn"]))
        if subjects is None:  # a legacy line nobody has attested: never guess
            complete = False
            continue
        ids = {s[k] for s in subjects for k in ("personId", "workId") if s[k]}
        (same_week if same_week_quiet else identities).update(ids)
    return QuietSource(pool=tuple(sorted(pool)), excluded=tuple(sorted(excluded)),
                       retainable=tuple(sorted(retainable)), subject_identities=tuple(sorted(identities)),
                       same_week_identities=tuple(sorted(same_week)),
                       history_through=lines[-1]["txn"] if lines else None, complete=complete)
