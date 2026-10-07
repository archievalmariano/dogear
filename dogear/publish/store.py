"""Object storage as the publisher sees it (R2 in production).

``put`` either succeeds, fails with ``PreconditionFailed`` (a conditional write
that was definitely not applied), or raises ``StoreError``, whose outcome the
caller must treat as unknown and resolve by reading back.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,255}$")


class StoreError(Exception):
    """A storage call failed; for a write, it may or may not have been applied."""


class PreconditionFailed(StoreError):
    """A conditional write was refused: the object was not what we expected."""


@dataclass(frozen=True)
class Obj:
    data: bytes
    etag: str


def check_key(key: str) -> str:
    if not _KEY.match(key) or ".." in key or "//" in key or key.endswith("/"):
        raise ValueError(f"bad object key {key!r}")
    return key


def etag_of(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()  # what R2 reports for a single-part upload


class MemoryStore:
    """In-memory store with fault injection for tests.

    ``fault(op, key, data)`` runs before every call and may return None (proceed),
    "fail" (raise StoreError, nothing done), "applied-then-fail" (write, then
    raise StoreError: the ambiguous case) or "lost" (raise StoreError, nothing
    written). It may also change the store or a clock first (a concurrent writer,
    a week boundary).
    """

    def __init__(self) -> None:
        self.objects: dict[str, Obj] = {}
        self.content_types: dict[str, str] = {}
        self.fault: Optional[Callable[[str, str, Optional[bytes]], Optional[str]]] = None
        self.calls: list[tuple[str, str]] = []

    def _fault(self, op: str, key: str, data: Optional[bytes]) -> Optional[str]:
        self.calls.append((op, key))
        return self.fault(op, key, data) if self.fault else None

    def get(self, key: str) -> Optional[Obj]:
        check_key(key)
        if self._fault("get", key, None) == "fail":
            raise StoreError(f"get {key}: injected failure")
        return self.objects.get(key)

    def put(self, key: str, data: bytes, *, content_type: str, if_match: Optional[str] = None,
            if_none_match: bool = False) -> str:
        check_key(key)
        action = self._fault("put", key, data)
        if action in ("fail", "lost"):
            raise StoreError(f"put {key}: injected {action}")
        current = self.objects.get(key)
        if if_none_match and current is not None:
            raise PreconditionFailed(f"{key} exists")
        if if_match is not None and (current is None or current.etag != if_match):
            raise PreconditionFailed(f"{key} changed")
        obj = Obj(bytes(data), etag_of(data))
        self.objects[key] = obj
        self.content_types[key] = content_type
        if action == "applied-then-fail":
            raise StoreError(f"put {key}: injected timeout after the write")
        return obj.etag


class DirStore:
    """A directory standing in for the bucket, for local runs (one writer)."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        return self.root / check_key(key)

    def get(self, key: str) -> Optional[Obj]:
        path = self._path(key)
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as err:
            raise StoreError(f"get {key}: {err}") from None
        return Obj(data, etag_of(data))

    def put(self, key: str, data: bytes, *, content_type: str, if_match: Optional[str] = None,
            if_none_match: bool = False) -> str:
        current = self.get(key)
        if if_none_match and current is not None:
            raise PreconditionFailed(f"{key} exists")
        if if_match is not None and (current is None or current.etag != if_match):
            raise PreconditionFailed(f"{key} changed")
        path = self._path(key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, path)
        except OSError as err:
            raise StoreError(f"put {key}: {err}") from None
        return etag_of(data)
