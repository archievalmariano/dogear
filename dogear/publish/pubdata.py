"""The publication-data repository (private git in production).

    staged/<monday>/issue.json, web.html, provenance.json, validation.json,
                   dataset.json, affinity.json   (the frozen inputs)
    pending.json                       the one transaction in flight, if any
    history.jsonl                      every transaction, once, by txn id; a committed
                                       line keeps the targets its serving must show
    unverified.json                    committed transactions whose serving is unconfirmed
    verified.jsonl                     committed transactions whose serving was settled
    publication.json                   mirror of the registry after each commit
    published/<monday>/rev<N>.provenance.json

``save`` makes the working tree durable (commit + push). A save that raises
leaves the remote as it was: a later run that starts from a fresh checkout
must still find everything it needs, which is why the pending transaction is
saved before the registry is switched.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Callable, Optional


class CorruptState(Exception):
    pass


class SaveFailed(Exception):
    pass


STAGED_FILES = ("issue.json", "web.html", "provenance.json", "validation.json", "dataset.json", "affinity.json")


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _json(obj: object) -> bytes:
    return (json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


class PubData:
    def __init__(self, root: Path, committer: Optional[Callable[[str], None]] = None) -> None:
        self.root = Path(root)
        self.committer = committer

    def save(self, message: str) -> None:
        if self.committer is None:
            return
        try:
            self.committer(message)
        except SaveFailed:
            raise
        except Exception as err:  # a push or commit failure of any kind
            raise SaveFailed(f"{message}: {err}") from None

    # Staged weeks.

    def staged_dir(self, week: dt.date) -> Path:
        return self.root / "staged" / week.isoformat()

    def write_staged(self, week: dt.date, files: dict[str, bytes]) -> None:
        if set(files) != set(STAGED_FILES):
            raise ValueError(f"staged files must be {STAGED_FILES}")
        for name, data in files.items():
            _write(self.staged_dir(week) / name, data)

    def read_staged(self, week: dt.date) -> Optional[dict[str, bytes]]:
        d = self.staged_dir(week)
        if not d.is_dir():
            return None
        try:
            return {name: (d / name).read_bytes() for name in STAGED_FILES}
        except OSError:
            return None  # incomplete: treated as absent, and re-staged

    # The transaction in flight.

    @property
    def pending_path(self) -> Path:
        return self.root / "pending.json"

    def read_pending(self) -> Optional[dict]:
        try:
            raw = self.pending_path.read_bytes()
        except FileNotFoundError:
            return None
        try:
            pending = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as err:
            raise CorruptState(f"pending.json unreadable: {err}") from None
        need = {"txn", "op", "week", "expectedEtag", "intendedRegistrySha", "intendedRegistry", "history", "verify",
                "expectedRegistrySha", "expectedRegistry"}
        if not isinstance(pending, dict) or not need <= set(pending):
            raise CorruptState("pending.json is missing fields")
        return pending

    def write_pending(self, pending: dict) -> None:
        if self.pending_path.exists():
            raise CorruptState("a transaction is already pending; reconcile first")
        _write(self.pending_path, _json(pending))

    def clear_pending(self) -> None:
        self.pending_path.unlink(missing_ok=True)

    # History.

    @property
    def history_path(self) -> Path:
        return self.root / "history.jsonl"

    def history(self) -> list[dict]:
        try:
            lines = self.history_path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []
        out = []
        for n, line in enumerate(lines, 1):
            try:
                entry = json.loads(line)
            except ValueError:
                raise CorruptState(f"history.jsonl line {n} is not JSON") from None
            if not isinstance(entry, dict) or not isinstance(entry.get("txn"), str) or \
                    entry.get("status") not in ("committed", "aborted"):
                raise CorruptState(f"history.jsonl line {n} is malformed")
            out.append(entry)
        txns = [e["txn"] for e in out]
        if len(txns) != len(set(txns)):
            raise CorruptState("history.jsonl records a transaction twice")
        return out

    def append_history_once(self, entry: dict) -> bool:
        """Append unless this txn is already recorded. Returns whether it appended."""
        if any(e["txn"] == entry["txn"] for e in self.history()):
            return False
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        with self.history_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, sort_keys=True, ensure_ascii=False) + "\n")
        return True

    # Mirrors.

    def write_registry_mirror(self, data: bytes) -> None:
        _write(self.root / "publication.json", data)

    def write_published_provenance(self, week: str, rev: int, data: bytes) -> None:
        _write(self.root / "published" / week / f"rev{rev}.provenance.json", data)

    def read_published_provenance(self, week: str, rev: int) -> Optional[bytes]:
        try:
            return (self.root / "published" / week / f"rev{rev}.provenance.json").read_bytes()
        except FileNotFoundError:
            return None

    # Served output not yet verified: per transaction, the targets the host must show.

    @property
    def unverified_path(self) -> Path:
        return self.root / "unverified.json"

    def read_unverified(self) -> Optional[list]:
        """The outstanding verification entries, or None if nothing is outstanding.

        Only an absent file means nothing is outstanding; anything else that is not
        exactly well formed is CorruptState, never silently "verified"."""
        try:
            raw = self.unverified_path.read_bytes()
        except FileNotFoundError:
            return None
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as err:
            raise CorruptState(f"unverified.json unreadable: {err}") from None
        entries = data.get("entries") if isinstance(data, dict) and set(data) == {"entries"} else None
        if not isinstance(entries, list) or not entries:
            raise CorruptState("unverified.json must be {\"entries\": [...]} with at least one entry")
        for e in entries:
            if not (isinstance(e, dict) and set(e) == {"txn", "since", "targets"} and isinstance(e["txn"], str)
                    and isinstance(e["since"], str) and isinstance(e["targets"], list) and e["targets"]):
                raise CorruptState("unverified.json has a malformed entry")
            problem = targets_problem(e["targets"])
            if problem:
                raise CorruptState(f"unverified.json entry {e['txn']}: {problem}")
        if len({e["txn"] for e in entries}) != len(entries):
            raise CorruptState("unverified.json lists a transaction twice")
        return entries

    def write_unverified(self, entries: list) -> None:
        if entries:
            _write(self.unverified_path, _json({"entries": entries}))
        else:
            self.unverified_path.unlink(missing_ok=True)

    # Settled verification: append-only, once per transaction.

    @property
    def verified_path(self) -> Path:
        return self.root / "verified.jsonl"

    def verified(self) -> list[dict]:
        try:
            lines = self.verified_path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []
        out = []
        for n, line in enumerate(lines, 1):
            try:
                entry = json.loads(line)
            except ValueError:
                raise CorruptState(f"verified.jsonl line {n} is not JSON") from None
            if not (isinstance(entry, dict) and set(entry) == {"txn", "at"} and isinstance(entry["txn"], str)
                    and isinstance(entry["at"], str)):
                raise CorruptState(f"verified.jsonl line {n} is malformed")
            out.append(entry)
        txns = [e["txn"] for e in out]
        if len(txns) != len(set(txns)):
            raise CorruptState("verified.jsonl records a transaction twice")
        return out

    def append_verified_once(self, txn: str, at: str) -> None:
        if any(e["txn"] == txn for e in self.verified()):
            return
        self.verified_path.parent.mkdir(parents=True, exist_ok=True)
        with self.verified_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"at": at, "txn": txn}, sort_keys=True) + "\n")


_HEX64 = frozenset("0123456789abcdef")


def _valid_target(t: object) -> bool:
    if not isinstance(t, dict) or t.get("kind") not in ("manifest", "page", "issue"):
        return False
    keys = {"kind", "path", "sha256"} | ({"week"} if t["kind"] == "page" else set())
    sha = t.get("sha256")
    return (set(t) == keys and isinstance(t["path"], str) and t["path"].startswith("/")
            and isinstance(sha, str) and len(sha) == 64 and set(sha) <= _HEX64
            and (t["kind"] != "page" or isinstance(t.get("week"), str)))


_ISSUE_PATH = re.compile(r"^/issues/dogear-(\d{4}-\d{2}-\d{2})\.([0-9a-f]{16})\.json$")


def targets_problem(targets: object) -> Optional[str]:
    """What is wrong with a verification target list, or None. The shape is fixed by
    ``verification_targets``: the manifest, then a page and its device issue per week,
    weeks ascending and each a Monday."""
    if not isinstance(targets, list) or not targets or not all(_valid_target(t) for t in targets):
        return "targets are missing or malformed"
    head, rest = targets[0], targets[1:]
    if head["kind"] != "manifest" or head["path"] != "/current.json":
        return "the first target must be the manifest /current.json"
    if len(rest) % 2:
        return "every week needs a page and an issue target"
    weeks = []
    for page, issue in zip(rest[0::2], rest[1::2]):
        if page["kind"] != "page" or issue["kind"] != "issue":
            return "every week needs a page and an issue target"
        week = page["week"]
        try:
            monday = dt.date.fromisoformat(week)
        except ValueError:
            return f"page target week {week!r} is not a date"
        if monday.weekday() != 0 or page["path"] != f"/{week}/":
            return f"page target {page['path']} does not match its week {week}"
        m = _ISSUE_PATH.match(issue["path"])
        if not m or m.group(1) != week or m.group(2) != issue["sha256"][:16]:
            return f"issue target {issue['path']} does not match week {week} and its hash"
        weeks.append(week)
    if weeks != sorted(set(weeks)):
        return "weeks must be distinct and ascending"
    return None


def git_committer(root: Path, push: bool = True) -> Callable[[str], None]:
    """Commit everything in ``root`` and push it (production)."""

    def commit(message: str) -> None:
        def git(*args: str) -> None:
            subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True)

        git("add", "-A")
        status = subprocess.run(["git", "-C", str(root), "status", "--porcelain"], check=True,
                                capture_output=True, text=True).stdout
        if status.strip():
            git("commit", "-q", "-m", message)
        if push:
            git("push", "-q")

    return commit
