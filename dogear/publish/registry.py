"""The publication registry (``publication.json``): the one publication boundary.

What devices fetch (``/current.json``) and which dated web pages exist are both
derived from this object, so publishing is a single conditional write of it.
Uploaded artifacts are unreachable until a revision here names them.

Transitions are pure: each takes a registry and returns a new one, or raises
``NoOp`` (nothing to do; a green run) or ``Refused`` (the request breaks a rule).
"""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import re
from typing import Optional

REGISTRY_KEY = "publication.json"
SCHEMA_VERSION = 1
MAX_ISSUE_BYTES = 32 * 1024  # DogearLimits.h kMaxIssueBytes

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_TXN = re.compile(r"^[0-9]{8}T[0-9]{6}-[a-z]+-[0-9a-f]{12}$")
OPS_WITH_ARTIFACTS = ("launch", "promote", "correct")
_REVISION_KEYS = {"rev", "op", "txn", "issueId", "issueKey", "issueSha256", "issueBytes", "webKey", "webSha256",
                  "provenanceSha256", "datasetSha256", "generator", "generatedAt", "publishedAt", "reason"}


class CorruptRegistry(Exception):
    pass


class Refused(Exception):
    pass


class NoOp(Exception):
    pass


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def dumps(reg: dict) -> bytes:
    return (json.dumps(reg, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def issue_key(issue_id: str, issue_sha: str) -> str:
    return f"issues/{issue_id}.{issue_sha[:16]}.json"


def web_key(week: dt.date, web_sha: str) -> str:
    return f"web/{week.isoformat()}/{web_sha[:16]}.html"


def _monday(value: str, where: str) -> dt.date:
    try:
        day = dt.date.fromisoformat(value)
    except (TypeError, ValueError):
        raise CorruptRegistry(f"{where}: not a date: {value!r}") from None
    if day.weekday() != 0 or day.isoformat() != value:
        raise CorruptRegistry(f"{where}: {value} is not a Monday")
    return day


def _check_revision(week: dt.date, i: int, rev: object) -> None:
    where = f"weeks.{week}.revisions[{i}]"
    if not isinstance(rev, dict):
        raise CorruptRegistry(f"{where}: not an object")
    required = _REVISION_KEYS - {"reason"}
    if not required <= set(rev) or set(rev) - _REVISION_KEYS:
        raise CorruptRegistry(f"{where}: wrong fields")
    if rev["rev"] != i or isinstance(rev["rev"], bool):
        raise CorruptRegistry(f"{where}: rev must be {i}")
    if rev["op"] not in OPS_WITH_ARTIFACTS or not isinstance(rev["txn"], str) or not _TXN.match(rev["txn"]):
        raise CorruptRegistry(f"{where}: bad op or txn")
    if rev["issueId"] != f"dogear-{week.isoformat()}":
        raise CorruptRegistry(f"{where}: issueId does not match the week")
    for field in ("issueSha256", "webSha256", "provenanceSha256", "datasetSha256"):
        if not isinstance(rev[field], str) or not _HEX64.match(rev[field]):
            raise CorruptRegistry(f"{where}: {field} is not a SHA-256")
    if rev["issueKey"] != issue_key(rev["issueId"], rev["issueSha256"]):
        raise CorruptRegistry(f"{where}: issueKey does not match its hash")
    if rev["webKey"] != web_key(week, rev["webSha256"]):
        raise CorruptRegistry(f"{where}: webKey does not match its hash")
    n = rev["issueBytes"]
    if isinstance(n, bool) or not isinstance(n, int) or not 0 < n <= MAX_ISSUE_BYTES:
        raise CorruptRegistry(f"{where}: issueBytes out of range")
    for field in ("generator", "generatedAt", "publishedAt"):
        if not isinstance(rev[field], str) or not rev[field]:
            raise CorruptRegistry(f"{where}: {field} missing")
    if rev["op"] == "correct" and not (isinstance(rev.get("reason"), str) and rev["reason"].strip()):
        raise CorruptRegistry(f"{where}: a correction needs a reason")


def validate(reg: object) -> dict:
    """The registry if it is exactly well formed; otherwise CorruptRegistry."""
    if not isinstance(reg, dict) or set(reg) != {"schemaVersion", "txn", "current", "hold", "weeks"}:
        raise CorruptRegistry("wrong top-level fields")
    if reg["schemaVersion"] != SCHEMA_VERSION or isinstance(reg["schemaVersion"], bool):
        raise CorruptRegistry(f"schemaVersion must be {SCHEMA_VERSION}")
    if not isinstance(reg["txn"], str) or not _TXN.match(reg["txn"]):
        raise CorruptRegistry("bad txn")
    weeks = reg["weeks"]
    if not isinstance(weeks, dict) or not weeks:
        raise CorruptRegistry("weeks must be a non-empty object")
    for key, entry in weeks.items():
        week = _monday(key, f"weeks.{key}")
        if not isinstance(entry, dict) or set(entry) != {"active", "revisions"}:
            raise CorruptRegistry(f"weeks.{key}: wrong fields")
        revs = entry["revisions"]
        if not isinstance(revs, list) or not revs:
            raise CorruptRegistry(f"weeks.{key}: no revisions")
        for i, rev in enumerate(revs):
            _check_revision(week, i, rev)
        active = entry["active"]
        if isinstance(active, bool) or not isinstance(active, int) or not 0 <= active < len(revs):
            raise CorruptRegistry(f"weeks.{key}: active out of range")
    if reg["current"] not in weeks:
        raise CorruptRegistry("current names no published week")
    hold = reg["hold"]
    if hold is not None and (not isinstance(hold, dict) or set(hold) != {"reason", "since", "txn"}
                             or not all(isinstance(v, str) and v for v in hold.values())):
        raise CorruptRegistry("bad hold")
    return reg


def parse(data: bytes) -> dict:
    try:
        reg = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError) as err:
        raise CorruptRegistry(f"not JSON: {err}") from None
    return validate(reg)


def active_revision(reg: dict, week: str) -> dict:
    entry = reg["weeks"][week]
    return entry["revisions"][entry["active"]]


def manifest(reg: dict) -> dict:
    """The device's ``/current.json``: the current week's active revision."""
    week = reg["current"]
    rev = active_revision(reg, week)
    start = dt.date.fromisoformat(week)
    return {
        "issueId": rev["issueId"],
        "issuePath": rev["issueKey"],
        "issueSha256": rev["issueSha256"],
        "revision": rev["rev"],
        "webPath": f"{week}/",
        "week": {"start": week, "end": (start + dt.timedelta(days=6)).isoformat()},
    }


def manifest_bytes(reg: dict) -> bytes:
    return (json.dumps(manifest(reg), indent=2) + "\n").encode("utf-8")


def make_txn(now: dt.datetime, op: str, basis: object) -> str:
    """Transaction id: when, what, and a digest of the request and prior state."""
    digest = sha256(json.dumps([op, basis], sort_keys=True, default=str).encode("utf-8"))[:12]
    return f"{now.strftime('%Y%m%dT%H%M%S')}-{op}-{digest}"


# Transitions. Every one returns a deep copy; the input is never changed.

def launch(reg: Optional[dict], week: dt.date, revision: dict, txn: str) -> dict:
    if reg is not None:
        raise Refused("launch needs an empty store: a registry already exists")
    return {"schemaVersion": SCHEMA_VERSION, "txn": txn, "current": week.isoformat(), "hold": None,
            "weeks": {week.isoformat(): {"active": 0, "revisions": [dict(revision, rev=0, op="launch", txn=txn)]}}}


def promote(reg: dict, week: dt.date, revision: dict, txn: str) -> dict:
    key = week.isoformat()
    if key in reg["weeks"]:
        raise NoOp(f"{key} is already published (revision {reg['weeks'][key]['active']})")
    if reg["hold"] is not None:
        raise NoOp(f"publication is on hold: {reg['hold']['reason']}")
    if key <= reg["current"]:
        raise Refused(f"{key} is not after the current week {reg['current']}")
    new = copy.deepcopy(reg)
    new["weeks"][key] = {"active": 0, "revisions": [dict(revision, rev=0, op="promote", txn=txn)]}
    new["current"], new["txn"] = key, txn
    return new


def correct(reg: dict, week: dt.date, expect_rev: int, revision: dict, reason: str, txn: str) -> dict:
    key = week.isoformat()
    if key not in reg["weeks"]:
        raise Refused(f"{key} is not published; nothing to correct")
    if not reason.strip():
        raise Refused("a correction needs a reason")
    entry = reg["weeks"][key]
    if entry["active"] != expect_rev:
        raise Refused(f"{key} is at revision {entry['active']}, not {expect_rev}")
    if active_revision(reg, key)["issueSha256"] == revision["issueSha256"] and \
            active_revision(reg, key)["webSha256"] == revision["webSha256"]:
        raise NoOp(f"{key}: the corrected artifacts are identical to revision {entry['active']}")
    new = copy.deepcopy(reg)
    revs = new["weeks"][key]["revisions"]
    revs.append(dict(revision, rev=len(revs), op="correct", txn=txn, reason=reason.strip()))
    new["weeks"][key]["active"] = len(revs) - 1
    new["txn"] = txn
    return new


def rollback(reg: dict, week: dt.date, rev: Optional[int], reason: str, since: str, txn: str) -> dict:
    """Within a week (``rev`` given): make an earlier revision active. To an earlier
    week (``rev`` None): make it current and hold automatic promotion."""
    key = week.isoformat()
    if key not in reg["weeks"]:
        raise Refused(f"{key} was never published")
    if not reason.strip():
        raise Refused("a rollback needs a reason")
    new = copy.deepcopy(reg)
    if rev is not None:
        entry = new["weeks"][key]
        if not 0 <= rev < len(entry["revisions"]) or rev == entry["active"]:
            raise Refused(f"{key} has no other revision {rev}")
        entry["active"] = rev
    else:
        if key >= reg["current"]:
            raise Refused(f"{key} is not before the current week {reg['current']}")
        new["current"] = key
        new["hold"] = {"reason": reason.strip(), "since": since, "txn": txn}
    new["txn"] = txn
    return new


def restore(reg: dict, week: dt.date, rev: int, txn: str) -> dict:
    """After a rollback: make a chosen published revision current and lift the hold."""
    key = week.isoformat()
    if reg["hold"] is None:
        raise Refused("nothing is held; use correct or rollback")
    if key not in reg["weeks"] or not 0 <= rev < len(reg["weeks"][key]["revisions"]):
        raise Refused(f"{key} revision {rev} was never published")
    new = copy.deepcopy(reg)
    new["current"], new["hold"], new["txn"] = key, None, txn
    new["weeks"][key]["active"] = rev
    return new


def resume(reg: dict, txn: str) -> dict:
    """Lift the hold only. Automatic promotion resumes with the next unpublished week."""
    if reg["hold"] is None:
        raise NoOp("nothing is held")
    new = copy.deepcopy(reg)
    new["hold"], new["txn"] = None, txn
    return new
