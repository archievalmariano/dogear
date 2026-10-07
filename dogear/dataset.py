"""The canonical literary-date dataset: load, validate, fingerprint, approve.

The dataset (``data/literary-dates.json``) is the durable asset. Everything
DOGEAR publishes is a view over it; nothing here knows about weeks, issues or
rendering. A future Calendar export reads the same records.

Validation is strict about structure (a malformed record fails the whole load,
so a bad edit can never reach an issue) and lenient about copy length (those are
warnings an editor can act on).

Publication state
-----------------
A record is publishable only when it is ``verified`` AND a human has approved
it AND it has not materially changed since. Approval stores a fingerprint of the
record's material fields (facts, copy, sources, links and their rights); any later edit
to those fields changes the fingerprint, so the approval no longer matches and
the record drops out of selection until it is approved again. Editorial metadata
(significance, recognition, relevance, discovery value, tags, notes, balance
fields) can change without re-approval. Weekly issues built only from approved
records need no approval of their own.

Editorial dimensions (schema v3)
--------------------------------
Kept apart on purpose, because they are different questions:

* ``significance`` 1-5   how much it matters to books, writers and reading
* ``recognition``        anchor (broadly known) / core (known to engaged
                         readers) / discovery (significant but likely unfamiliar)
* ``discoveryValue`` 0-2 how rewarding it is to introduce to a new reader
* ``phRelevance`` 0-2    Philippine relevance (2 = Philippine subject)
* ``seaRelevance`` 0-2   Southeast Asian relevance outside the Philippines
* ``area``               where the writer or work belongs (also for the audit)
* ``locality``           observances only: the ISO 3166 country code of a
                         national observance (``PH``); absent for global ones.
                         Shown as ``OBSERVANCE · PH``.
* ``domain``             literature / history / philosophy / science /
                         nonfiction / reading-culture
Personal editorial affinity lives in ``data/affinity.json``, not in records.

Source confidence
-----------------
Sources are ranked: ``primary`` and ``institutional`` (preferred) above
``secondary`` (reputable publishers, encyclopaedias, newspapers) above
``reference`` (Wikipedia, Wikidata and other open wikis). Reference sources may
carry straightforward facts. A record whose ``claims`` are ``complex`` (a
disputed date, a calendar conversion, an observed rather than recorded date,
sources that conflict) needs at least one secondary-or-better source to be
publishable; until then selection skips it and says why.

``dateBasis`` is ``recorded`` (the default) or ``observed``: a date the subject
kept or chose when the record itself is uncertain, such as a writer's chosen
birthday. Observed dates publish, and the copy must say so.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from dataclasses import dataclass
from urllib.parse import urlsplit
from pathlib import Path
from typing import Optional

from .affinity import normalize

SCHEMA_VERSION = 3

TYPES = ("birth", "death", "publication", "literary-event", "observance")
RECOGNITION = ("anchor", "core", "discovery")
DOMAINS = ("literature", "history", "philosophy", "science", "nonfiction", "reading-culture")
PRECISIONS = ("day", "month", "year")
STATUSES = ("verified", "provisional", "disputed")
SOURCE_KINDS = ("primary", "institutional", "secondary", "reference")
# Higher is stronger. Primary and institutional are equally preferred.
SOURCE_RANK = {"primary": 3, "institutional": 3, "secondary": 2, "reference": 1}
DATE_BASES = ("recorded", "observed")
CLAIMS = ("straightforward", "complex")
LINK_TYPES = ("read-online", "borrow", "find-book", "read-more", "source")
# A record lists candidate links; the publication shows the best one for a
# Philippine reader (Record.link). Access comes before commerce:
#   1 read-online  free legal full text (public domain worldwide)
#   2 borrow       a library in the Philippines        (market "ph")
#   3 borrow       a library elsewhere                  (market "intl")
#   4 find-book    a Philippine bookseller              (market "ph")
#   5 find-book    a bookseller elsewhere               (market "intl")
# read-more and source links are context, used only when there is no access.
# A weak commercial link is worse than none: add one only when it is defensible.
MARKETS = ("ph", "intl")
# Links to a book (read-online, borrow, find-book) must say which work they open and why:
#   event_work           the work the event is about        (The Salt Orchard is published)
#   mentioned_work       a work the copy foregrounds        (a poet's death -> the last poem)
#   representative_work  an editor-chosen work for an author-focused entry (birth/death)
BOOK_LINKS = ("read-online", "borrow", "find-book")
LINK_MATCHES = ("event_work", "mentioned_work", "representative_work")
RIGHTS = ("public-domain-worldwide", "public-domain-us", "in-copyright")
# Balance-audit areas (where the writer or work belongs). "global" is for
# institutional observances that belong to no single tradition.
AREAS = (
    "philippines", "southeast-asia", "east-asia", "south-asia", "middle-east",
    "africa", "europe", "north-america", "latin-america", "oceania", "global",
)
WIDER_ASIA = ("east-asia", "south-asia")

ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
LANG_RE = re.compile(r"^[a-z]{2,3}$")
COUNTRY_RE = re.compile(r"^[A-Z]{2}$")
FP_RE = re.compile(r"^[0-9a-f]{16}$")
MAX_ID_LEN = 64
# Years are CE as the sources print them (no BCE, no calendar conversion),
# from 1 to 2100: within what Python dates support, and late enough for
# observances established now, early enough to catch a typo like 19866.
MIN_YEAR, MAX_YEAR = 1, 2100
# The real file nests six levels (file, records, record, links, link, value).
MAX_JSON_DEPTH = 16
_HOST_LABEL_RE = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")
MAX_HEADLINE_LEN = 64  # also the Calendar detail line, so keep it short

# Digest copy is what the device shows. It should fit one 480x800 page at the
# preferred 14pt body and must fit at the 12pt fallback. The real check is the
# mock's --fitcheck; this word range is only an early warning. Expanded copy is
# web-only and has no device limit.
DIGEST_WORDS = (40, 70)

# Fields an approval covers. Changing any of them withdraws the approval.
MATERIAL_FIELDS = (
    "id", "month", "day", "year", "precision", "type", "person", "work",
    "headline", "label", "copy", "status", "sources", "links", "dateBasis", "claims",
    "locality",
)
# Material fields added after records were first approved count only when
# present, so adding one never withdraws an unrelated approval.
_OPTIONAL_MATERIAL = ("locality",)

_DAYS_IN_MONTH = (31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)  # Feb 29 allowed


class DatasetError(Exception):
    """The dataset failed structural validation. ``problems`` lists every issue."""

    def __init__(self, problems: list[str]):
        super().__init__(f"{len(problems)} dataset problem(s):\n  " + "\n  ".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class Source:
    title: str
    url: str
    kind: str


@dataclass(frozen=True)
class Link:
    type: str
    url: str
    title: str  # the work the link opens
    provider: str  # who hosts it, e.g. "Project Gutenberg"
    match: Optional[str] = None  # event_work / mentioned_work / representative_work (book links)
    author: Optional[str] = None
    edition: Optional[str] = None  # e.g. "the 1818 text", shown on the reading screen
    rights: Optional[str] = None  # the linked edition's rights status
    market: Optional[str] = None  # "ph" or "intl" (borrow and find-book links)

    @property
    def representative(self) -> bool:
        return self.match == "representative_work"

    @property
    def access_tier(self) -> Optional[int]:
        """1 (best) to 5 for a reader in the Philippines; None for context links
        and for free text that is not public domain everywhere."""
        if self.type == "read-online":
            return 1 if self.rights == "public-domain-worldwide" else None
        if self.type == "borrow":
            return 2 if self.market == "ph" else 3
        if self.type == "find-book":
            return 4 if self.market == "ph" else 5
        return None


@dataclass(frozen=True)
class Approval:
    by: str
    on: str
    fingerprint: str


@dataclass(frozen=True)
class Record:
    id: str
    month: Optional[int]
    day: Optional[int]
    year: Optional[int]
    precision: str
    date_basis: str
    claims: str
    type: str
    person: Optional[str]
    work: Optional[str]
    headline: str
    label: Optional[str]  # kicker word override, e.g. "NOBEL PRIZE" for an event
    digest: str
    expanded: Optional[str]
    allow_second_page: bool
    tags: tuple[str, ...]
    significance: int
    recognition: str
    discovery_value: int
    ph_relevance: int
    sea_relevance: int
    domain: str
    status: str
    verified_by: Optional[str]
    verified_on: Optional[str]
    sources: tuple[Source, ...]
    links: tuple[Link, ...]  # every candidate, in the editor's order
    notes: Optional[str]
    area: Optional[str]
    locality: Optional[str]
    language: Optional[str]
    women_writer: Optional[bool]
    approval: Optional[Approval]
    fingerprint: str

    @property
    def is_philippine(self) -> bool:
        return self.ph_relevance > 0

    @property
    def is_wider_asian(self) -> bool:
        return self.area in WIDER_ASIA

    @property
    def approval_state(self) -> str:
        """``approved``, ``unapproved`` or ``changed`` (approved, then edited)."""
        if self.approval is None:
            return "unapproved"
        return "approved" if self.approval.fingerprint == self.fingerprint else "changed"

    @property
    def source_confidence(self) -> str:
        """The strongest kind of source the record cites."""
        if not self.sources:
            return "none"
        return max(self.sources, key=lambda s: SOURCE_RANK[s.kind]).kind

    @property
    def link(self) -> Optional[Link]:
        """The one link the publication shows: the best access link, else a
        context link (read more / view source), else none."""
        access = [l for l in self.links if l.access_tier is not None]
        if access:
            return min(access, key=lambda l: l.access_tier)  # min() keeps the first of equals
        context = [l for l in self.links if l.type in ("read-more", "source")]
        return context[0] if context else None

    @property
    def free_reading_withheld(self) -> bool:
        """A free copy is listed but not public domain everywhere, and nothing
        better stands in for it."""
        link = self.link
        return (any(l.type == "read-online" and l.access_tier is None for l in self.links)
                and (link is None or link.type != "read-online"))

    @property
    def sourcing_ok(self) -> bool:
        """Complex claims may not rest on reference sources alone."""
        if self.claims != "complex":
            return True
        return any(SOURCE_RANK[s.kind] >= SOURCE_RANK["secondary"] for s in self.sources)

    @property
    def publishable(self) -> bool:
        return self.status == "verified" and self.approval_state == "approved" and self.sourcing_ok

    @property
    def subject_keys(self) -> tuple[str, ...]:
        """Keys used to keep one person or one work from appearing twice in an issue."""
        keys = []
        if self.person:
            keys.append("person:" + _slug(self.person))
        if self.work:
            keys.append("work:" + _slug(self.work))
        return tuple(keys)


@dataclass(frozen=True)
class Dataset:
    records: tuple[Record, ...]
    sha256: str
    warnings: tuple[str, ...]

    def by_id(self, record_id: str) -> Record:
        for r in self.records:
            if r.id == record_id:
                return r
        raise KeyError(record_id)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _is_int(v) -> bool:
    """An integer that is not a bool (True is an int in Python)."""
    return isinstance(v, int) and not isinstance(v, bool)


def _is_text(v) -> bool:
    return isinstance(v, str) and bool(v.strip())


def _opt_text(v) -> bool:
    return v is None or _is_text(v)


def is_https_url(url) -> bool:
    """An absolute https URL with a real DNS hostname: no userinfo, spaces or
    control characters, a valid port if one is given."""
    if not isinstance(url, str) or any(c.isspace() or ord(c) < 0x20 for c in url):
        return False
    try:
        parts = urlsplit(url)
        port = parts.port  # raises on a bad port
    except ValueError:
        return False
    host = parts.hostname or ""
    if parts.scheme != "https" or "@" in parts.netloc or not host or port == 0:
        return False
    labels = host.split(".")
    return len(host) <= 253 and len(labels) >= 2 and all(_HOST_LABEL_RE.match(l) for l in labels) \
        and not labels[-1].isdigit()


def word_count(text: str) -> int:
    return len(text.split())


def fingerprint(raw: dict) -> str:
    """Stable digest of a raw record's material fields."""
    material = {k: raw.get(k) for k in MATERIAL_FIELDS if k in raw or k not in _OPTIONAL_MATERIAL}
    canon = json.dumps(material, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:16]


def _mentions(copy_text: str, phrase: str) -> bool:
    return normalize(phrase) in normalize(copy_text)


def _check_link(raw: dict, raw_link: dict, problems: list[str], where: str) -> Optional[Link]:
    """Validate a link, and keep a book link tied to the work it claims to open."""
    before = len(problems)

    def bad(msg: str) -> None:
        problems.append(f"{where}: {msg}")

    if not isinstance(raw_link, dict):
        bad("a link must be an object")
        return None
    ltype, url = raw_link.get("type"), raw_link.get("url")
    title, provider = raw_link.get("title"), raw_link.get("provider")
    if ltype not in LINK_TYPES:
        bad(f"link.type must be one of {LINK_TYPES}")
        return None
    if not is_https_url(url):
        bad("link.url must be an https URL with a hostname")
    if not _is_text(title) or not _is_text(provider):
        bad("a link needs a title and a provider (text)")
    for field in ("author", "edition", "mentionedAs"):
        if not _opt_text(raw_link.get(field)):
            bad(f"link.{field} must be text or absent")
    if len(problems) > before:
        return None
    match, author, rights = raw_link.get("match"), raw_link.get("author"), raw_link.get("rights")
    market = raw_link.get("market")
    if ltype in ("borrow", "find-book"):
        if market not in MARKETS:
            bad(f"a {ltype} link needs market: one of {MARKETS} (Philippine or elsewhere)")
    elif market is not None:
        bad(f"{ltype} links do not take a market")
    if ltype in BOOK_LINKS:
        if match not in LINK_MATCHES:
            bad(f"a {ltype} link needs match: one of {LINK_MATCHES}")
        if not author:
            bad(f"a {ltype} link needs the work's author")
        if rights not in RIGHTS:
            bad(f"a {ltype} link needs rights: one of {RIGHTS}")
        if ltype == "read-online" and rights not in ("public-domain-worldwide", "public-domain-us"):
            bad("a read-online link must be to a public-domain edition")
        # The record's own fields are type-checked separately; here, use only text.
        person = raw.get("person") if isinstance(raw.get("person"), str) else None
        work = raw.get("work") if isinstance(raw.get("work"), str) else None
        copy = raw.get("copy")
        digest = copy.get("digest") if isinstance(copy, dict) else None
        digest = digest if isinstance(digest, str) else ""
        if match == "event_work":
            if not work:
                bad("event_work links need the record to name its work")
            elif title and normalize(title) != normalize(work):
                bad(f"event_work link opens {title!r}, but the event is about {work!r}")
        elif match == "mentioned_work" and title:
            mentioned = raw_link.get("mentionedAs") or title
            if not _mentions(digest, mentioned):
                bad(f"mentioned_work link opens {title!r}, which the copy does not mention")
        elif match == "representative_work" and raw.get("type") not in ("birth", "death"):
            bad("representative_work links are only for author-focused entries (birth or death)")
        if match in ("event_work", "representative_work") and person and author \
                and normalize(author) != normalize(person):
            bad(f"link author {author!r} does not match the record's person {person!r}")
    elif match is not None:
        bad(f"{ltype} links do not take a match")
    if len(problems) > before:
        return None
    return Link(ltype, url, title, provider, match, author, raw_link.get("edition"), rights, market)


def _check_record(raw: dict, problems: list[str], warnings: list[str]) -> Optional[Record]:
    if not isinstance(raw, dict):
        problems.append(f"a record must be an object, not {type(raw).__name__}")
        return None
    rid = raw.get("id")
    where = f"record {rid!r}"
    before = len(problems)

    def bad(msg: str) -> None:
        problems.append(f"{where}: {msg}")

    if not isinstance(rid, str) or not ID_RE.match(rid) or len(rid) > MAX_ID_LEN:
        bad("id must be lowercase kebab-case, <= 64 chars")

    for field, allowed in (
        ("type", TYPES),
        ("precision", PRECISIONS),
        ("dateBasis", DATE_BASES),
        ("claims", CLAIMS),
        ("status", STATUSES),
        ("recognition", RECOGNITION),
        ("domain", DOMAINS),
    ):
        if raw.get(field) not in allowed:
            bad(f"{field} must be one of {allowed}")

    month, day, year = raw.get("month"), raw.get("day"), raw.get("year")
    precision = raw.get("precision")
    if precision in ("day", "month"):
        if not _is_int(month) or not 1 <= month <= 12:
            bad("month must be an integer 1-12")
    elif month is not None:
        bad("year-precision records must leave month null")
    if precision == "day":
        if not _is_int(day) or not _is_int(month) or not 1 <= month <= 12:
            bad("day-precision records need an integer month and day")
        elif not 1 <= day <= _DAYS_IN_MONTH[month - 1]:
            bad(f"day {day} does not exist in month {month}")
    elif day is not None:
        bad("only day-precision records may set day (never invent a day)")
    if year is not None and (not _is_int(year) or not MIN_YEAR <= year <= MAX_YEAR):
        bad(f"year must be an integer {MIN_YEAR}-{MAX_YEAR} (CE) or null")
    if raw.get("type") != "observance" and year is None:
        bad("non-observance records need a year")

    for field in ("person", "work", "notes", "verifiedBy"):
        if not _opt_text(raw.get(field)):
            bad(f"{field} must be text or null")
    verified_on = raw.get("verifiedOn")
    if verified_on is not None:
        try:
            dt.date.fromisoformat(verified_on if isinstance(verified_on, str) else "")
        except ValueError:
            bad("verifiedOn must be YYYY-MM-DD or null")
    tags = raw.get("tags", [])
    if not isinstance(tags, list) or not all(_is_text(t) for t in tags):
        bad("tags must be a list of text")
        tags = []

    headline = raw.get("headline")
    if not isinstance(headline, str) or not headline.strip():
        bad("headline is required")
    elif len(headline) > MAX_HEADLINE_LEN:
        bad(f"headline longer than {MAX_HEADLINE_LEN} chars")

    label = raw.get("label")
    if label is not None and not (isinstance(label, str) and label.isupper() and len(label) <= 24):
        bad("label must be a short uppercase string (<= 24 chars) or absent")

    copy = raw.get("copy")
    if not isinstance(copy, dict):
        bad("copy must be an object with digest, expanded and allowSecondPage")
        copy = {}
    digest, expanded = copy.get("digest"), copy.get("expanded")
    allow_second = copy.get("allowSecondPage", False)
    if not isinstance(digest, str) or not digest.strip():
        bad("copy.digest is required")
    else:
        n = word_count(digest)
        if not DIGEST_WORDS[0] <= n <= DIGEST_WORDS[1] and not allow_second:
            warnings.append(f"{where}: digest copy is {n} words (house range {DIGEST_WORDS})")
    if expanded is not None and (not isinstance(expanded, str) or not expanded.strip()):
        bad("copy.expanded must be a non-empty string or null")
    if not isinstance(allow_second, bool):
        bad("copy.allowSecondPage must be true or false")

    significance = raw.get("significance")
    if not _is_int(significance) or not 1 <= significance <= 5:
        bad("significance must be an integer 1-5")
    for field in ("discoveryValue", "phRelevance", "seaRelevance"):
        v = raw.get(field, 0)
        if not _is_int(v) or not 0 <= v <= 2:
            bad(f"{field} must be an integer 0-2")

    sources = []
    raw_sources = raw.get("sources", [])
    if not isinstance(raw_sources, list):
        bad("sources must be a list")
        raw_sources = []
    for s in raw_sources:
        if not isinstance(s, dict) or not _is_text(s.get("title")) or not is_https_url(s.get("url")):
            bad("each source needs a text title and an https URL with a hostname")
            continue
        if s.get("kind") not in SOURCE_KINDS:
            bad(f"source kind must be one of {SOURCE_KINDS}")
            continue
        sources.append(Source(s["title"], s["url"], s["kind"]))
    if raw.get("status") == "verified" and not sources:
        bad("verified records need at least one source")
    if raw.get("dateBasis") == "observed" and not raw.get("notes"):
        bad("an observed date needs notes explaining what is uncertain")
    if raw.get("dateBasis") == "observed" and raw.get("claims") != "complex":
        bad("an observed date is a complex claim")
    if (raw.get("status") == "verified" and raw.get("claims") == "complex" and sources
            and all(SOURCE_RANK[s.kind] < SOURCE_RANK["secondary"] for s in sources)):
        warnings.append(f"{where}: complex claim rests on reference sources only (not publishable)")

    if "rights" in raw:
        bad("rights now belong to the link (link.rights), not the record")
    if "link" in raw:
        bad("'link' is now 'links', a list of candidate links")
    links = []
    raw_links = raw.get("links", [])
    if not isinstance(raw_links, list):
        bad("links must be a list")
        raw_links = []
    for i, raw_link in enumerate(raw_links):
        link = _check_link(raw, raw_link, problems, f"{where} links[{i}]")
        if link:
            links.append(link)
    if len({l.url for l in links}) != len(links):
        bad("links repeat a URL")

    area, language, women = raw.get("area"), raw.get("language"), raw.get("womenWriter")
    if area is not None and area not in AREAS:
        bad(f"area must be one of {AREAS} or null")
    if area is None:
        warnings.append(f"{where}: no balance area")
    if language is not None and not (isinstance(language, str) and LANG_RE.match(language)):
        bad("language must be an ISO 639 code like 'en' or 'fil', or null")
    if women is not None and not isinstance(women, bool):
        bad("womenWriter must be true, false or null")
    locality = raw.get("locality")
    if locality is not None and not (isinstance(locality, str) and COUNTRY_RE.match(locality)):
        bad("locality must be an ISO 3166 country code like 'PH', or absent")
    elif locality is not None and raw.get("type") != "observance":
        bad("locality is for observances only (ordinary events carry no country tag)")
    elif raw.get("type") == "observance" and area not in (None, "global") and locality is None:
        bad("a national observance needs its locality (e.g. 'PH')")
    elif locality is not None and area == "global":
        bad("a global observance has no locality")

    approval = None
    raw_approval = raw.get("approval")
    if raw_approval is not None and not isinstance(raw_approval, dict):
        bad("approval must be an object or null")
    elif raw_approval is not None:
        by, on, fp = raw_approval.get("by"), raw_approval.get("on"), raw_approval.get("fingerprint")
        if not _is_text(by) or not isinstance(on, str) or not isinstance(fp, str) or not FP_RE.match(fp):
            bad("approval needs by, on and a 16-hex fingerprint")
        else:
            try:
                dt.date.fromisoformat(on)
                approval = Approval(by, str(on), fp)
            except ValueError:
                bad("approval.on must be YYYY-MM-DD")
        if raw.get("status") != "verified":
            bad("only verified records can be approved")

    if len(problems) > before:
        return None
    return Record(
        id=rid,
        month=month,
        day=day,
        year=year,
        precision=precision,
        date_basis=raw["dateBasis"],
        claims=raw["claims"],
        type=raw["type"],
        person=raw.get("person"),
        work=raw.get("work"),
        headline=headline,
        label=label,
        digest=digest,
        expanded=expanded,
        allow_second_page=allow_second,
        tags=tuple(tags),
        significance=significance,
        recognition=raw["recognition"],
        discovery_value=raw.get("discoveryValue", 0),
        ph_relevance=raw.get("phRelevance", 0),
        sea_relevance=raw.get("seaRelevance", 0),
        domain=raw["domain"],
        status=raw["status"],
        verified_by=raw.get("verifiedBy"),
        verified_on=raw.get("verifiedOn"),
        sources=tuple(sources),
        links=tuple(links),
        notes=raw.get("notes"),
        area=area,
        locality=locality,
        language=language,
        women_writer=women,
        approval=approval,
        fingerprint=fingerprint(raw),
    )


def _check_json_shape(payload) -> Optional[str]:
    """Nesting depth and string encodability, checked without recursion.

    JSON may escape a lone surrogate ("\\ud800"), which decodes to a string
    that cannot be written back as UTF-8.
    """
    stack = [(payload, 1)]
    while stack:
        node, depth = stack.pop()
        if depth > MAX_JSON_DEPTH:
            return f"nested deeper than {MAX_JSON_DEPTH} levels"
        if isinstance(node, str):
            try:
                node.encode("utf-8")
            except UnicodeEncodeError:
                return f"string {node[:40]!r} is not valid Unicode (lone surrogate escape)"
        elif isinstance(node, dict):
            stack.extend((x, depth + 1) for pair in node.items() for x in pair)
        elif isinstance(node, list):
            stack.extend((x, depth + 1) for x in node)
    return None


def parse_dataset(text: str) -> Dataset:
    try:
        encoded = text.encode("utf-8")
        payload = json.loads(text)
    except UnicodeEncodeError:
        raise DatasetError(["not valid UTF-8 text"]) from None
    except RecursionError:
        raise DatasetError([f"not valid JSON: nested deeper than {MAX_JSON_DEPTH} levels"]) from None
    except ValueError as err:
        raise DatasetError([f"not valid JSON: {err}"]) from None
    shape_problem = _check_json_shape(payload)
    if shape_problem:
        raise DatasetError([f"not valid JSON: {shape_problem}"])
    problems: list[str] = []
    warnings: list[str] = []
    if not isinstance(payload, dict) or payload.get("schemaVersion") != SCHEMA_VERSION:
        raise DatasetError([f"schemaVersion must be {SCHEMA_VERSION}"])
    if not isinstance(payload.get("records"), list):
        raise DatasetError(["records must be a list"])
    records = []
    seen = set()
    for raw in payload["records"]:
        rec = _check_record(raw, problems, warnings)
        if rec is None:
            continue
        if rec.id in seen:
            problems.append(f"record {rec.id!r}: duplicate id")
            continue
        seen.add(rec.id)
        records.append(rec)
    if problems:
        raise DatasetError(problems)
    digest = hashlib.sha256(encoded).hexdigest()
    return Dataset(records=tuple(records), sha256=digest, warnings=tuple(warnings))


def _read_dataset_text(path: Path) -> str:
    try:
        return Path(path).read_bytes().decode("utf-8")
    except UnicodeDecodeError as err:
        raise DatasetError([f"not valid UTF-8 at byte {err.start}"]) from None


def load_dataset(path: Path) -> Dataset:
    return parse_dataset(_read_dataset_text(path))


def dumps_dataset(payload: dict) -> str:
    """The one canonical file format, so tool edits and hand edits diff cleanly."""
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def approve(path: Path, record_ids: list[str], by: str, on: dt.date) -> list[str]:
    """Approve records in place at their current content. Returns the ids changed.

    Refuses unknown ids and records that are not verified, so approval can never
    make provisional or disputed facts publishable. Re-approving an unchanged
    record is a no-op.
    """
    path = Path(path)
    text = _read_dataset_text(path)
    parse_dataset(text)  # the file must be valid before anything is approved
    payload = json.loads(text)
    by_id = {r["id"]: r for r in payload["records"]}
    done = []
    for rid in record_ids:
        raw = by_id.get(rid)
        if raw is None:
            raise KeyError(f"no record {rid!r}")
        if raw.get("status") != "verified":
            raise ValueError(f"{rid}: status is {raw.get('status')}; only verified records can be approved")
        fp = fingerprint(raw)
        if (raw.get("approval") or {}).get("fingerprint") == fp:
            continue
        raw["approval"] = {"by": by, "on": on.isoformat(), "fingerprint": fp}
        done.append(rid)
    out = dumps_dataset(payload)
    parse_dataset(out)
    path.write_text(out, encoding="utf-8")
    return done
