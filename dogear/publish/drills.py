"""Transaction drills for S2 (PUBLISHING.md §17.8): one injected event around the
real registry write, so staging exercises each recovery path on real storage.
Test mode only (the CLI refuses ``--drill`` in production).

    conflict          another writer changes the registry just before our
                      conditional write: real storage answers 412
    lost              the registry write fails before it is sent (not applied)
    applied-then-drop the registry write applies, then the answer is lost
    unsaved-record    the save after the switch fails (the record is pending)

Each fires once per run, on the first registry write.
"""

from __future__ import annotations

from typing import Callable, Optional

from .pubdata import SaveFailed
from .registry import REGISTRY_KEY
from .store import StoreError

DRILLS = ("conflict", "lost", "applied-then-drop", "unsaved-record")


class DrillStore:
    def __init__(self, store, drill: str) -> None:
        if drill not in DRILLS[:3]:
            raise ValueError(f"not a storage drill: {drill}")
        self.store, self.drill, self.fired = store, drill, False

    def get(self, key: str):
        return self.store.get(key)

    def put(self, key: str, data: bytes, *, content_type: str, if_match: Optional[str] = None,
            if_none_match: bool = False) -> str:
        if key != REGISTRY_KEY or self.fired:
            return self.store.put(key, data, content_type=content_type, if_match=if_match, if_none_match=if_none_match)
        self.fired = True
        if self.drill == "conflict":
            current = self.store.get(REGISTRY_KEY)
            if current is not None:  # the same registry, other bytes: a different ETag, still valid
                self.store.put(REGISTRY_KEY, current.data + b"\n", content_type=content_type)
            return self.store.put(key, data, content_type=content_type, if_match=if_match, if_none_match=if_none_match)
        if self.drill == "lost":
            raise StoreError("drill: registry write lost before sending")
        etag = self.store.put(key, data, content_type=content_type, if_match=if_match, if_none_match=if_none_match)
        raise StoreError(f"drill: registry write applied ({etag[:8]}…), answer dropped")


def failing_once(committer: Optional[Callable[[str], None]]) -> Callable[[str], None]:
    """The save after the switch (``publish <txn>``) fails once; other saves proceed."""
    state = {"fired": False}

    def commit(message: str) -> None:
        if message.startswith("publish ") and not state["fired"]:
            state["fired"] = True
            raise SaveFailed("drill: saving the record after the switch failed")
        if committer is not None:
            committer(message)

    return commit
