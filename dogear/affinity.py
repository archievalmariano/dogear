"""Personal editorial affinity: who the editor is drawn to.

DOGEAR is authored. ``data/affinity.json`` lists preferred writers and an
adjacent group (thinkers and historians who matter to reading culture but are
not novelists or poets). Selection treats affinity as a small lift for records
that are already eligible and significant, never as a way in.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

LEVELS = ("preferred", "adjacent")


def normalize(name: str) -> str:
    """'Ana de la Cruz-Santos' -> 'anadelacruzsantos': case, accents and punctuation ignored."""
    text = unicodedata.normalize("NFKD", name)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]", "", text.lower())


@dataclass(frozen=True)
class Affinity:
    by_name: dict[str, str]  # normalized name -> level

    def level(self, person: Optional[str]) -> Optional[str]:
        if not person:
            return None
        return self.by_name.get(normalize(person))

    def names(self, level: str) -> list[str]:
        return sorted(k for k, v in self.by_name.items() if v == level)


EMPTY = Affinity({})


def parse_affinity(text: str) -> Affinity:
    payload = json.loads(text)
    by_name: dict[str, str] = {}
    for level in LEVELS:
        for entry in payload.get(level) or []:
            for name in [entry["name"], *entry.get("aliases", [])]:
                by_name[normalize(name)] = level
    return Affinity(by_name)


def load_affinity(path: Path) -> Affinity:
    path = Path(path)
    return parse_affinity(path.read_text(encoding="utf-8")) if path.exists() else EMPTY
