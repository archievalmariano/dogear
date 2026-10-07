"""Issue weeks and date formatting.

An issue covers Monday to Sunday and is published on the Monday morning,
Asia/Manila. Display strings follow the CrossPoint date rule: day first, month
always a word (``Sep``, never ``Sept``), 3-letter weekday in tight spaces.
Historical items carry the day and month only (``1 Jan``): DOGEAR is weekly, and
a weekday would belong to the issue year, not the year of the event.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from zoneinfo import ZoneInfo

MANILA = ZoneInfo("Asia/Manila")

MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


@dataclass(frozen=True)
class IssueWeek:
    start: dt.date  # Monday
    end: dt.date  # Sunday

    @property
    def issue_id(self) -> str:
        return f"dogear-{self.start.isoformat()}"

    def days(self) -> list[dt.date]:
        return [self.start + dt.timedelta(days=i) for i in range(7)]


def week_of(day: dt.date) -> IssueWeek:
    """The Monday-to-Sunday week containing ``day``."""
    start = day - dt.timedelta(days=day.weekday())
    return IssueWeek(start, start + dt.timedelta(days=6))


def week_starting(monday: dt.date) -> IssueWeek:
    if monday.weekday() != 0:
        raise ValueError(f"{monday} is a {WEEKDAYS[monday.weekday()]}, not a Monday")
    return week_of(monday)


def current_week(now: dt.datetime | None = None) -> IssueWeek:
    """The week a run at ``now`` (default: wall clock, Manila) should publish."""
    now = now or dt.datetime.now(MANILA)
    return week_of(now.astimezone(MANILA).date())


def short_date(day: dt.date) -> str:
    """``Thu 26 Nov`` — the CrossPoint short form."""
    return f"{WEEKDAYS[day.weekday()][:3]} {day.day} {MONTHS[day.month - 1][:3]}"


def day_month(day: dt.date) -> str:
    """``26 Nov`` — an item's date, with no weekday (the event was another year)."""
    return f"{day.day} {MONTHS[day.month - 1][:3]}"


def long_date(day: dt.date) -> str:
    """``Thursday 26 November 2026`` — the CrossPoint long form."""
    return f"{WEEKDAYS[day.weekday()]} {day.day} {MONTHS[day.month - 1]} {day.year}"


def range_dateline(week: IssueWeek) -> str:
    """``12–18 OCTOBER 2026``; spans months or years only when the week does."""
    a, b = week.start, week.end
    if a.year != b.year:
        text = f"{a.day} {MONTHS[a.month - 1]} {a.year} – {b.day} {MONTHS[b.month - 1]} {b.year}"
    elif a.month != b.month:
        text = f"{a.day} {MONTHS[a.month - 1]} – {b.day} {MONTHS[b.month - 1]} {b.year}"
    else:
        text = f"{a.day}–{b.day} {MONTHS[a.month - 1]} {a.year}"
    return text.upper()


def range_dateline_short(week: IssueWeek) -> str:
    """``28 DEC 2026 – 3 JAN 2027``: the fallback where the long form will not fit."""
    a, b = week.start, week.end
    m = lambda d: MONTHS[d.month - 1][:3].upper()  # noqa: E731
    if a.year != b.year:
        return f"{a.day} {m(a)} {a.year} – {b.day} {m(b)} {b.year}"
    if a.month != b.month:
        return f"{a.day} {m(a)} – {b.day} {m(b)} {b.year}"
    return f"{a.day}–{b.day} {m(a)} {a.year}"


def is_leap(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
