"""The quiet-week issue (PUBLISHING.md §21): constants, the exact copy, the frozen
quiet source and the anniversary window. Pure: no I/O, no publication state.

A genuinely empty week (the regular selection picks nothing) may become a quiet
issue under ``emptyWeek: quiet-week``: 3 to 5 items drawn from earlier issues'
"Also this week" mentions, never from anything published as a full item, with
the same scoring and slot bars as a regular week. Fewer than 3 holds the
previous issue. These values are editorial decisions (owner, 8 October 2026);
tests pin each one.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Optional

QUIET_MIN = 3
QUIET_MAX = 5
QUIET_POOL_DAYS = 182      # "Also this week" mentions from the previous 26 weeks
QUIET_UPCOMING_DAYS = 35   # leave out a record whose anniversary falls in [W, W + 35 days)
QUIET_SUBJECT_DAYS = 365   # leave out a person or work featured in full within 365 days

# The approved copy, exactly (owner; note revised 9 October 2026). Plain ASCII: no typesetting.
QUIET_HEADING = "SOME QUIET THIS WEEK"
QUIET_NOTE = "Not much on our calendar, but still a few things worth a dogear for you."

REGULAR_SCHEMA_VERSION = 2  # what every DOGEAR reader so far accepts
QUIET_SCHEMA_VERSION = 3    # pre-quiet firmware rejects it and keeps its verified cache

EDITIONS = ("regular", "quiet")


def quiet_object() -> dict:
    return {"heading": QUIET_HEADING, "note": QUIET_NOTE}


@dataclass(frozen=True)
class QuietSource:
    """What a quiet selection for one week may draw on, derived once from validated
    publication history (dogear/publish/quiet_history.py) and frozen into a quiet
    issue's provenance, so the issue regenerates byte for byte.

    ``complete`` is False when some subject history in the window lacks stable
    identities (a legacy line with no attestation): the quiet pass then holds."""

    pool: tuple = ()                  # ((record id, recorded week ISO), ...) sorted
    excluded: tuple = ()              # ids ever published as a full item (regular, or quiet in another week)
    retainable: tuple = ()            # ids this week's own earlier quiet revisions picked
    subject_identities: tuple = ()    # person/work ids featured within the subject window
    same_week_identities: tuple = ()  # identities of this week's earlier quiet picks
    history_through: Optional[str] = None  # txn of the last content line considered
    complete: bool = True

    def as_json(self) -> dict:
        return {"pool": [list(p) for p in self.pool], "excluded": list(self.excluded),
                "retainable": list(self.retainable), "subjectIdentities": list(self.subject_identities),
                "sameWeekQuietIdentities": list(self.same_week_identities), "historyThrough": self.history_through}

    @classmethod
    def from_json(cls, raw: object) -> "QuietSource":
        if not isinstance(raw, dict) or set(raw) != {"pool", "excluded", "retainable", "subjectIdentities",
                                                     "sameWeekQuietIdentities", "historyThrough"}:
            raise ValueError("quietSource has the wrong fields")
        pool = raw["pool"]
        if not isinstance(pool, list) or not all(
                isinstance(p, list) and len(p) == 2 and all(isinstance(x, str) for x in p) for p in pool):
            raise ValueError("quietSource.pool must be [[id, week], ...]")
        lists = [raw[k] for k in ("excluded", "retainable", "subjectIdentities", "sameWeekQuietIdentities")]
        if not all(isinstance(v, list) and all(isinstance(x, str) for x in v) for v in lists):
            raise ValueError("quietSource lists must hold strings")
        if raw["historyThrough"] is not None and not isinstance(raw["historyThrough"], str):
            raise ValueError("quietSource.historyThrough must be a txn or null")
        return cls(tuple(tuple(p) for p in pool), *(tuple(v) for v in lists), raw["historyThrough"])


def next_occurrence(month: int, day: int, on_or_after: dt.date) -> dt.date:
    """The first date on or after ``on_or_after`` with this month and day. 29 February
    is the next actual leap day."""
    year = on_or_after.year
    while True:
        try:
            candidate = dt.date(year, month, day)
        except ValueError:  # 29 February outside a leap year
            year += 1
            continue
        if candidate >= on_or_after:
            return candidate
        year += 1


def anniversary_imminent(month: int, day: int, monday: dt.date) -> bool:
    """True when the record's own anniversary falls in [W, W + 35 days): the current
    week or the four after it, where it can compete in its own week. Day 34 is
    excluded; day 35 is eligible."""
    return next_occurrence(month, day, monday) < monday + dt.timedelta(days=QUIET_UPCOMING_DAYS)
