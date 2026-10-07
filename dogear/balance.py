"""Balance report: editorial visibility, never selection input.

Shows how the corpus, and what DOGEAR has actually published lately, is spread
across traditions, recognition tiers, women writers, translation, event type,
domain, period and the editor's affinity list. The point is to notice when
source convenience is quietly becoming editorial policy. Nothing here feeds
``select``: individual weeks are never forced to look balanced.

Scopes: the full dataset, the records publishable today, and rolling windows of
the most recent 8 and 12 published issues (counted per published entry).
"""

from __future__ import annotations

import datetime as dt
from collections import Counter
from typing import Iterable

from .affinity import EMPTY, Affinity
from .dataset import AREAS, Dataset, Record

AREA_LABELS = {
    "philippines": "Philippine",
    "southeast-asia": "Southeast Asian (outside PH)",
    "east-asia": "Wider Asian: East Asian",
    "south-asia": "Wider Asian: South Asian",
    "middle-east": "Middle Eastern",
    "africa": "African",
    "europe": "European",
    "north-america": "North American",
    "latin-america": "Latin American",
    "oceania": "Oceanian",
    "global": "Global / institutional",
}
WINDOWS = (8, 12)


def _century(rec: Record) -> str:
    if rec.year is None:
        return "undated"
    c = (rec.year - 1) // 100 + 1
    suffix = "th" if 10 <= c % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(c % 10, "th")
    return f"{c}{suffix} century"


def _period_order(label: str) -> int:
    digits = "".join(ch for ch in label.split(" ")[0] if ch.isdigit())
    return int(digits) if digits else 10_000


def _translated(rec: Record) -> str:
    if rec.language is None:
        return "n/a (observance or unset)"
    return "English original" if rec.language == "en" else "non-English original (read in translation)"


def _women(rec: Record) -> str:
    if rec.women_writer is None:
        return "n/a (observance or unset)"
    return "woman writer" if rec.women_writer else "not a woman writer"


def balance(records: Iterable[Record], affinity: Affinity = EMPTY) -> dict[str, Counter]:
    recs = list(records)
    return {
        "tradition": Counter(AREA_LABELS.get(r.area, "unset") for r in recs),
        "recognition": Counter(r.recognition for r in recs),
        "affinity": Counter(affinity.level(r.person) or "none" for r in recs),
        "women writers": Counter(_women(r) for r in recs),
        "translation": Counter(_translated(r) for r in recs),
        "event type": Counter(r.type for r in recs),
        "domain": Counter(r.domain for r in recs),
        "period": Counter(_century(r) for r in recs),
    }


def _ordered_keys(dim: str, counts: Counter) -> list[str]:
    if dim == "tradition":  # empty traditions are listed: the gaps are the point
        return [AREA_LABELS[a] for a in AREAS] + [k for k in counts if k not in AREA_LABELS.values()]
    if dim == "recognition":
        return ["anchor", "core", "discovery"]
    if dim == "period":
        return sorted(counts, key=_period_order)
    return sorted(counts, key=lambda k: (-counts[k], k))


def render_counts(title: str, report: dict[str, Counter]) -> list[str]:
    total = sum(report["event type"].values())
    out = [f"== {title}: {total} entries", ""]
    for dim, counts in report.items():
        out.append(dim.upper())
        for key in _ordered_keys(dim, counts):
            n = counts.get(key, 0)
            pct = 100 * n / total if total else 0
            out.append(f"  {key:<44} {n:>4}  {pct:5.1f}%  {'#' * round(pct / 4)}")
        out.append("")
    return out


def render_balance(
    dataset: Dataset,
    affinity: Affinity = EMPTY,
    issues: list[tuple[dt.date, tuple[str, ...]]] | None = None,
) -> str:
    """Full dataset, publishable records, and the rolling published windows."""
    out = ["DOGEAR balance report (editorial visibility only; never used by selection)", ""]
    out += render_counts("full dataset", balance(dataset.records, affinity))
    publishable = [r for r in dataset.records if r.publishable]
    out += render_counts("publishable now (verified + approved)", balance(publishable, affinity))
    by_id = {r.id: r for r in dataset.records}
    ordered = sorted(issues or [], reverse=True)
    for n in WINDOWS:
        window = ordered[:n]
        if not window:
            out.append(f"== last {n} issues: no issues found")
            out.append("")
            continue
        recs = [by_id[rid] for _, ids in window for rid in ids if rid in by_id]
        span = f"{window[-1][0].isoformat()} to {window[0][0].isoformat()}"
        note = "" if len(window) == n else f" (only {len(window)} available)"
        out += render_counts(f"last {n} issues{note}, {span}", balance(recs, affinity))
    return "\n".join(out) + "\n"
