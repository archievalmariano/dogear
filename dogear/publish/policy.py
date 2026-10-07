"""Editorial settings the publisher needs but must not decide.

Each is ``None`` until the editor decides it. Production refuses to stage or
publish while any is unset; tests pass an explicit test policy.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

SLOT5_CHOICES = (40, 42)
SPARSE_CHOICES = ("publish", "hold-previous")  # a 1-2 item week
EMPTY_CHOICES = ("hold-previous",)  # a 0-item week; a "quiet week" issue is not built


class PolicyError(ValueError):
    pass


@dataclass(frozen=True)
class PublicationPolicy:
    slot5_bar: Optional[int] = None  # the score slots 5-6 need: 42 or 40
    sparse_week: Optional[str] = None
    empty_week: Optional[str] = None

    def __post_init__(self) -> None:
        for value, choices, name in ((self.slot5_bar, SLOT5_CHOICES, "slot5Bar"),
                                     (self.sparse_week, SPARSE_CHOICES, "sparseWeek"),
                                     (self.empty_week, EMPTY_CHOICES, "emptyWeek")):
            if value is not None and (isinstance(value, bool) or value not in choices):
                raise PolicyError(f"{name} must be one of {choices} or null, not {value!r}")

    def unset(self) -> list[str]:
        return [name for name, value in (("slot5Bar", self.slot5_bar), ("sparseWeek", self.sparse_week),
                                         ("emptyWeek", self.empty_week)) if value is None]

    def slot_bars(self) -> tuple[tuple[int, int], ...]:
        if self.slot5_bar is None:
            raise PolicyError("slot5Bar is unset (editorial decision pending)")
        return ((4, 30), (6, self.slot5_bar), (8, 52))

    def as_json(self) -> dict:
        return {"slot5Bar": self.slot5_bar, "sparseWeek": self.sparse_week, "emptyWeek": self.empty_week}


def load_policy(path: Path) -> PublicationPolicy:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        raise PolicyError(f"cannot read policy {path}: {err}") from None
    if not isinstance(raw, dict) or set(raw) - {"slot5Bar", "sparseWeek", "emptyWeek", "_note"}:
        raise PolicyError("policy must be an object with slot5Bar, sparseWeek, emptyWeek")
    return PublicationPolicy(raw.get("slot5Bar"), raw.get("sparseWeek"), raw.get("emptyWeek"))


# For tests only. These values are not editorial decisions.
TEST_POLICY = PublicationPolicy(slot5_bar=42, sparse_week="publish", empty_week="hold-previous")
