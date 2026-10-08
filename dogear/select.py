"""Deterministic weekly selection.

Given the dataset and an issue week, find every record whose anniversary falls
in the week, score it, and compose a small issue. The same inputs always give
the same issue; there is no model and no randomness. Every candidate's score
breakdown and fate are recorded for the audit and the editor's report.

The aim, in one line: familiar enough to pull the reader in, regional enough to
feel distinctive, unfamiliar enough to teach them something.

1. Intrinsic strength (what the item is)
   * significance x 10                      the dominant term
   * recognition: anchor +5, core +2        familiarity pulls a reader in
   * round anniversary +8 / +6 / +4         100s / 50s / 25s

2. Editorial lifts (DOGEAR's point of view; modest by design)
   * regional, the strongest one only:      Philippine +8 (+4 if a connection),
                                            Southeast Asian +4 (+2), then +2 for
                                            wider Asia and the Global South (Africa,
                                            Latin America, the Middle East)
   * discovery: +2 per discoveryValue       only for discovery-tier records
   * affinity: preferred +3, adjacent +2    only when significance >= 3, so
                                            taste never rescues a weak date
   * worldwide-public-domain reading link +2

3. History (soft; never exclusions)
   * in an issue within 365 days            -6  (annual repeats rotate; measured
                                                 issue to issue: this week's Monday
                                                 against the earlier issue's Monday,
                                                 so every item in a week is judged alike)
   * area over half of the last 4 issues    -2  (recent regional overrepresentation)

4. Composition (applied while picking, against what is already in the issue)
   * first anchor +4; a third anchor -4, a fourth -8    aim 1-2 anchors
   * first Philippine/SEA/Asian item +4                 when a strong one exists
   * first discovery item (discoveryValue >= 1) +4;     aim 1-2 discoveries
     a third discovery -4, a fourth -8
   * each item of an event type already in the issue,  variety
     beyond the first: -3, -6, -9 ...
   These are nudges, not quotas: a week with no regional candidate gets none, and
   a week with several strong ones keeps them.

5. Length (the issue earns each slot)
   An item's effective score (score + composition nudge) must clear the bar for
   the slot it would fill: slots 1-4 >= 30, slots 5-6 >= 42, slots 7-8 >= 52.
   The composer stops at the first item that misses its bar. So a normal week
   runs 4-6, a genuinely weak one 3 (or fewer), and only an unusually strong
   week reaches 7-8. Because anchor penalties grow (-4, -8, ...), a sparse week
   stops early instead of filling up with anchors.

Ties on effective score go to the intrinsically stronger item, then to
literature over adjacent domains (history, philosophy, science, ...).

Shape limits stay hard: at most 8 entries, one per person or work, two per day,
five births/deaths. Only publishable records (verified, approved at their
current content, and with sourcing fit for their claims) are eligible;
``preview=True`` admits verified records that still await approval, for proofs
that must never be published.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter
from dataclasses import dataclass, field

from .affinity import EMPTY, Affinity
from .dataset import Dataset, Record
from .quiet import QUIET_MAX, QuietSource, anniversary_imminent
from .week import IssueWeek, is_leap, week_starting

# Shape. MAX is a ceiling, not a target: nothing pads an issue.
MAX_ENTRIES = 8
# (last slot, bar): the effective score an item needs to fill slots up to that one.
SLOT_BARS = ((4, 30), (6, 42), (8, 52))
MIN_SIGNIFICANCE = 2
MAX_PERSON_EVENTS = 5  # births and deaths together: an outer limit; variety comes from composition
MAX_PER_DAY = 2
MAX_QR = 2
MAX_FEATURED = 2

RECOGNITION_LIFT = {"anchor": 5, "core": 2, "discovery": 0}
PH_LIFT = {2: 8, 1: 4}
SEA_LIFT = {2: 4, 1: 2}
WIDER_ASIA_LIFT = 2
GLOBAL_SOUTH_LIFT = 2
GLOBAL_SOUTH = {"africa": "African", "latin-america": "Latin American", "middle-east": "Middle Eastern"}
DISCOVERY_LIFT_PER_POINT = 2
AFFINITY_LIFT = {"preferred": 3, "adjacent": 2}
AFFINITY_MIN_SIGNIFICANCE = 3
READ_FREE_LIFT = 2
RECENT_PENALTY = 6
RECENT_DAYS = 365
OVERREP_PENALTY = 2
OVERREP_WINDOW = 4  # issues

COMPOSITION = {
    "first_anchor": 4,
    "extra_anchor_step": -4,  # third anchor -4, fourth -8, ...
    "first_regional": 4,
    "first_discovery": 4,
    "extra_discovery_step": -4,  # third discovery -4, fourth -8, ...
    "same_type_step": -3,  # per item of the same event type beyond the first
}

# The action vocabulary. READ ONLINE never says FREE: the reading screen does.
CTA_LABELS = {"read-online": "READ ONLINE", "borrow": "BORROW", "find-book": "FIND THE BOOK", "read-more": "READ MORE",
              "source": "VIEW SOURCE"}


@dataclass(frozen=True)
class History:
    """What earlier issues published: drives the soft history penalties."""

    issues: tuple[tuple[dt.date, tuple[str, ...]], ...] = ()  # (monday, record ids), any order

    def last_seen(self, record_id: str, before: dt.date) -> dt.date | None:
        dates = [d for d, ids in self.issues if record_id in ids and d < before]
        return max(dates) if dates else None

    def recent(self, before: dt.date, n: int) -> list[tuple[dt.date, tuple[str, ...]]]:
        return sorted((i for i in self.issues if i[0] < before), reverse=True)[:n]


@dataclass
class Candidate:
    record: Record
    date: dt.date  # this year's occurrence
    years: int | None  # anniversary count; None for observances
    affinity: str | None
    intrinsic: int = 0
    lifts: int = 0
    history: int = 0
    composition: int = 0  # set when picked (or when last considered)
    reasons: list[str] = field(default_factory=list)
    composition_reasons: list[str] = field(default_factory=list)
    excluded: str | None = None
    shape_blocked: bool = False  # excluded only by the issue's shape (a near miss)
    role: str | None = None  # "featured" | "standard" once picked
    qr: bool = False
    pending_approval: bool = False  # admitted only because this is a preview
    pick_order: int | None = None

    @property
    def score(self) -> int:
        """The candidate's standing before composition."""
        return self.intrinsic + self.lifts + self.history

    @property
    def is_regional(self) -> bool:
        r = self.record
        return r.ph_relevance > 0 or r.sea_relevance > 0 or r.area == "southeast-asia" or r.is_wider_asian

    @property
    def is_discovery(self) -> bool:
        return self.record.recognition == "discovery" and self.record.discovery_value >= 1


@dataclass
class Selection:
    week: IssueWeek
    picked: list[Candidate]  # issue order
    candidates: list[Candidate]  # everything that matched the week, scored
    dataset_sha256: str
    preview: bool = False  # a proof: never publish (see select_issue)
    edition: str = "regular"  # "quiet": an empty week's quiet issue (select_quiet_issue)

    @property
    def runners_up(self) -> list[Candidate]:
        """Eligible records that lost only to the issue's shape, for the web edition.
        A quiet issue has none: its leftovers are neither shown nor recorded, so they
        never cycle back into a later quiet pool."""
        if self.edition == "quiet":
            return []
        return [c for c in self.candidates if c.shape_blocked]

    @property
    def rendered(self) -> list[Candidate]:
        """Everything an edition shows: the digest and ALSO THIS WEEK."""
        return self.picked + self.runners_up


def anniversary_bonus(years: int | None) -> int:
    if not years or years <= 0:
        return 0
    if years % 100 == 0:
        return 8
    if years % 50 == 0:
        return 6
    if years % 25 == 0:
        return 4
    return 0


def offers_free_reading(record: Record) -> bool:
    """A full-text reading link we are confident is lawful for a reader anywhere."""
    link = record.link
    return link is not None and link.type == "read-online" and link.rights == "public-domain-worldwide"


def qr_eligible(record: Record) -> bool:
    """Only access links reach the device (read, borrow, find); context links
    stay on the web."""
    return record.link is not None and record.link.access_tier is not None


def occurrence_in(record: Record, week: IssueWeek) -> dt.date | None:
    """The date in ``week`` on which this record's anniversary falls, if any."""
    if record.precision != "day":
        return None
    for day in week.days():
        if (day.month, day.day) != (record.month, record.day):
            continue
        if (record.month, record.day) == (2, 29) and not is_leap(day.year):
            return None  # leap-day records appear only in leap years
        return day
    return None


def _regional_lift(r: Record) -> tuple[int, str] | None:
    options = []
    if r.ph_relevance:
        options.append((PH_LIFT[r.ph_relevance], "Philippine" if r.ph_relevance == 2 else "Philippine connection"))
    sea = 2 if r.area == "southeast-asia" else r.sea_relevance
    if sea:
        options.append((SEA_LIFT[sea], "Southeast Asian" if sea == 2 else "Southeast Asian connection"))
    if r.is_wider_asian:
        options.append((WIDER_ASIA_LIFT, "wider Asian"))
    if r.area in GLOBAL_SOUTH:
        options.append((GLOBAL_SOUTH_LIFT, f"Global South: {GLOBAL_SOUTH[r.area]}"))
    return max(options) if options else None


def _score(record: Record, date: dt.date, affinity: Affinity, history: History, week: IssueWeek,
           overrep: set[str]) -> Candidate:
    years = None if record.type == "observance" or record.year is None else date.year - record.year
    cand = Candidate(record=record, date=date, years=years, affinity=affinity.level(record.person))
    add = cand.reasons.append

    cand.intrinsic = record.significance * 10
    add(f"significance {record.significance} (+{record.significance * 10})")
    if RECOGNITION_LIFT[record.recognition]:
        cand.intrinsic += RECOGNITION_LIFT[record.recognition]
        add(f"{record.recognition} (+{RECOGNITION_LIFT[record.recognition]})")
    bonus = anniversary_bonus(years)
    if bonus:
        cand.intrinsic += bonus
        add(f"{years}th anniversary (+{bonus})")

    regional = _regional_lift(record)
    if regional:
        cand.lifts += regional[0]
        add(f"{regional[1]} (+{regional[0]})")
    if record.recognition == "discovery" and record.discovery_value:
        lift = record.discovery_value * DISCOVERY_LIFT_PER_POINT
        cand.lifts += lift
        add(f"discovery value {record.discovery_value} (+{lift})")
    if cand.affinity:
        if record.significance >= AFFINITY_MIN_SIGNIFICANCE:
            cand.lifts += AFFINITY_LIFT[cand.affinity]
            add(f"editor's affinity, {cand.affinity} (+{AFFINITY_LIFT[cand.affinity]})")
        else:
            add(f"editor's affinity, {cand.affinity} (no lift: significance below {AFFINITY_MIN_SIGNIFICANCE})")
    if offers_free_reading(record):
        cand.lifts += READ_FREE_LIFT
        add(f"free legal reading link (+{READ_FREE_LIFT})")

    last = history.last_seen(record.id, week.start)
    if last is not None and 0 <= (week.start - last).days <= RECENT_DAYS:
        cand.history -= RECENT_PENALTY
        add(f"in the issue of {last.isoformat()} (-{RECENT_PENALTY})")
    if record.area in overrep:
        cand.history -= OVERREP_PENALTY
        add(f"{record.area} over half of the last {OVERREP_WINDOW} issues (-{OVERREP_PENALTY})")
    return cand


def _overrepresented(dataset: Dataset, history: History, week: IssueWeek) -> set[str]:
    recent = history.recent(week.start, OVERREP_WINDOW)
    areas = Counter()
    total = 0
    by_id = {r.id: r for r in dataset.records}
    for _, ids in recent:
        for rid in ids:
            if rid in by_id and by_id[rid].area:
                areas[by_id[rid].area] += 1
                total += 1
    return {a for a, n in areas.items() if total >= 8 and n * 2 > total}


def slot_bar(slot: int, bars: tuple[tuple[int, int], ...] = SLOT_BARS) -> int:
    for last, bar in bars:
        if slot <= last:
            return bar
    return 10**9


def _nth(n: int) -> str:
    return {3: "third", 4: "fourth", 5: "fifth"}.get(n, f"{n}th")


def _composition(cand: Candidate, picked: list[Candidate]) -> tuple[int, list[str]]:
    adj, why = 0, []
    anchors = sum(c.record.recognition == "anchor" for c in picked)
    discoveries = sum(c.is_discovery for c in picked)
    if cand.record.recognition == "anchor":
        if anchors == 0:
            adj += COMPOSITION["first_anchor"]
            why.append(f"first anchor (+{COMPOSITION['first_anchor']})")
        elif anchors >= 2:
            step = COMPOSITION["extra_anchor_step"] * (anchors - 1)
            adj += step
            why.append(f"{_nth(anchors + 1)} anchor ({step})")
    if cand.is_regional and not any(c.is_regional for c in picked):
        adj += COMPOSITION["first_regional"]
        why.append(f"first Philippine/Asian item (+{COMPOSITION['first_regional']})")
    if cand.is_discovery:
        if discoveries == 0:
            adj += COMPOSITION["first_discovery"]
            why.append(f"first discovery (+{COMPOSITION['first_discovery']})")
        elif discoveries >= 2:
            step = COMPOSITION["extra_discovery_step"] * (discoveries - 1)
            adj += step
            why.append(f"{_nth(discoveries + 1)} discovery ({step})")
    same = sum(c.record.type == cand.record.type for c in picked)
    if same >= 2:
        step = COMPOSITION["same_type_step"] * (same - 1)
        adj += step
        why.append(f"{_nth(same + 1)} {cand.record.type} ({step})")
    return adj, why


def _shape_block(cand: Candidate, picked: list[Candidate], used: set[str], per_day: Counter,
                 max_entries: int = MAX_ENTRIES) -> str | None:
    rec = cand.record
    if len(picked) >= max_entries:
        return f"issue full ({max_entries})"
    clash = next((k for k in rec.subject_keys if k in used), None)
    if clash:
        return f"same subject already in issue ({clash})"
    if per_day[cand.date] >= MAX_PER_DAY:
        return f"{MAX_PER_DAY} entries already on {cand.date.isoformat()}"
    if rec.type in ("birth", "death") and sum(c.record.type in ("birth", "death") for c in picked) >= MAX_PERSON_EVENTS:
        return f"{MAX_PERSON_EVENTS} births/deaths already in issue"
    return None


def select_issue(
    dataset: Dataset,
    week: IssueWeek,
    history: History | None = None,
    preview: bool = False,
    affinity: Affinity = EMPTY,
    slot_bars: tuple[tuple[int, int], ...] = SLOT_BARS,
) -> Selection:
    history = history or History()
    overrep = _overrepresented(dataset, history, week)
    candidates: list[Candidate] = []
    for record in dataset.records:
        date = occurrence_in(record, week)
        if date is None:
            continue
        cand = _score(record, date, affinity, history, week, overrep)
        cand.excluded = _ineligible(record, date, cand, preview)
        cand.pending_approval = cand.excluded is None and record.approval_state != "approved"
        candidates.append(cand)
    candidates.sort(key=lambda c: (-c.score, c.date, c.record.id))
    picked = _compose(candidates, slot_bars, MAX_ENTRIES)
    return Selection(
        week=week,
        picked=_finish(picked),
        candidates=candidates,
        dataset_sha256=dataset.sha256,
        # A --preview run is always a proof, whatever it picked; so is any issue
        # that renders a record awaiting approval, here or in ALSO THIS WEEK.
        # A publisher must still check approvals itself, not trust this flag.
        preview=preview or any(c.pending_approval for c in picked + [c for c in candidates if c.shape_blocked]),
    )


def _ineligible(record: Record, date: dt.date, cand: Candidate, preview: bool) -> str | None:
    """Why a record may not be published on this occurrence, or None."""
    if record.status != "verified":
        return f"status is {record.status}"
    if record.approval_state != "approved" and not preview:
        return "changed since approval" if record.approval_state == "changed" else "awaiting approval"
    if record.year is not None and record.year > date.year:
        return "event is in the future"
    if cand.years is not None and cand.years < 1:
        return "no anniversary yet"
    if not record.sourcing_ok:
        return "complex claim rests on reference sources only"
    if record.significance < MIN_SIGNIFICANCE:
        return f"significance below {MIN_SIGNIFICANCE}"
    return None


def _compose(candidates: list[Candidate], slot_bars: tuple[tuple[int, int], ...], max_entries: int) -> list[Candidate]:
    """Compose: at each step take the open candidate with the best score plus
    composition nudge, given what is already in the issue. Stops at the first item
    that misses its slot's bar, or at ``max_entries``; never pads."""
    picked: list[Candidate] = []
    used: set[str] = set()
    per_day: Counter = Counter()
    open_ = [c for c in candidates if not c.excluded]
    while open_:
        best, best_key = None, None
        for cand in open_:
            block = _shape_block(cand, picked, used, per_day, max_entries)
            if block:
                cand.excluded, cand.shape_blocked = block, True
                continue
            adj, why = _composition(cand, picked)
            cand.composition, cand.composition_reasons = adj, why
            key = (-(cand.score + adj), -cand.intrinsic, cand.record.domain != "literature",
                   cand.date, cand.record.id)
            if best_key is None or key < best_key:
                best, best_key = cand, key
        open_ = [c for c in open_ if not c.excluded]
        if best is None:
            break
        slot = len(picked) + 1
        bar = slot_bar(slot, slot_bars)
        if best.score + best.composition < bar:
            for c in open_:
                c.excluded = f"issue stopped at {len(picked)}: slot {slot} needs {bar}"
                c.shape_blocked = True
            break
        best.pick_order = len(picked) + 1
        picked.append(best)
        used.update(best.record.subject_keys)
        per_day[best.date] += 1
        open_.remove(best)
    return picked


def _finish(picked: list[Candidate]) -> list[Candidate]:
    """Roles, QR, and issue order: featured by strength, then standard by date."""
    _assign_roles(picked)
    _assign_qr(picked)
    featured = sorted((c for c in picked if c.role == "featured"), key=lambda c: (-c.intrinsic, -c.score, c.record.id))
    standard = sorted((c for c in picked if c.role == "standard"), key=lambda c: (c.date, -c.score, c.record.id))
    return featured + standard


def select_quiet_issue(
    dataset: Dataset,
    week: IssueWeek,
    history: History | None,
    source: "QuietSource",
    affinity: Affinity = EMPTY,
    slot_bars: tuple[tuple[int, int], ...] = SLOT_BARS,
) -> Selection:
    """An empty week's quiet issue (PUBLISHING.md §21), from ``source`` only.

    Candidates are earlier issues' "Also this week" mentions (``source.pool``), each
    at its ORIGINAL date (its occurrence in the week that mentioned it), never one
    published as a full item, never one whose anniversary falls in [W, W + 35 days),
    never one about a person or work featured within 365 days, and never one without
    stable identities for its person/work. Then the regular scorer and composer, with
    the regular slot bars and a ceiling of QUIET_MAX. The caller enforces QUIET_MIN.
    Never a proof: only approved records are candidates."""
    history = history or History()
    overrep = _overrepresented(dataset, history, week)
    by_id = {r.id: r for r in dataset.records}
    excluded = set(source.excluded)
    retainable = set(source.retainable)
    blocked_ids = set(source.subject_identities)
    same_week_ids = set(source.same_week_identities)
    seen: set[str] = set()
    candidates: list[Candidate] = []
    for rid, recorded in sorted(source.pool, key=lambda p: (p[1], p[0])):
        if rid in seen:
            continue
        seen.add(rid)
        record = by_id.get(rid)
        if record is None or rid in excluded:
            continue
        date = occurrence_in(record, week_starting(dt.date.fromisoformat(recorded)))
        if date is None or date >= week.start:
            continue
        cand = _score(record, date, affinity, history, week, overrep)
        cand.excluded = _ineligible(record, date, cand, preview=False)
        if cand.excluded is None and anniversary_imminent(record.month, record.day, week.start):
            cand.excluded = "its own anniversary is within the next five weeks"
        identities = [i for i in (record.person_id, record.work_id) if i]
        if cand.excluded is None and ((record.person and not record.person_id) or (record.work and not record.work_id)):
            cand.excluded = "no stable subject identity"
        if cand.excluded is None and any(i in blocked_ids for i in identities):
            cand.excluded = "subject featured within 365 days"
        if cand.excluded is None and rid not in retainable and any(i in same_week_ids for i in identities):
            cand.excluded = "subject of this week's earlier quiet pick"
        candidates.append(cand)
    candidates.sort(key=lambda c: (-c.score, c.date, c.record.id))
    picked = _compose(candidates, slot_bars, QUIET_MAX)
    return Selection(week=week, picked=_finish(picked), candidates=candidates, dataset_sha256=dataset.sha256,
                     preview=False, edition="quiet")


def _assign_roles(picked: list[Candidate]) -> None:
    """Lead with the intrinsically strongest item (what it is, not DOGEAR's lifts);
    on a tie, literature leads over adjacent domains.

    A second lead needs significance 5 on a round anniversary. Featured status is
    placement and typography, not longer copy.
    """
    for c in picked:
        c.role = "standard"
    if not picked:
        return
    ranked = sorted(picked, key=lambda c: (-c.intrinsic, c.record.domain != "literature", -c.score, c.date, c.record.id))
    ranked[0].role = "featured"
    for c in ranked[1:]:
        if sum(x.role == "featured" for x in picked) >= MAX_FEATURED:
            break
        if c.record.significance == 5 and anniversary_bonus(c.years):
            c.role = "featured"


def _assign_qr(picked: list[Candidate]) -> None:
    """At most MAX_QR links; free reading before finding, leads before the rest."""
    eligible = [c for c in picked if qr_eligible(c.record)]
    eligible.sort(key=lambda c: (c.record.link.access_tier, c.role != "featured", -c.score, c.record.id))
    for cand in eligible[:MAX_QR]:
        cand.qr = True
