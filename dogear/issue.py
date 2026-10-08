"""Build the device issue, the web edition data and the audit from a selection.

The device payload is display-ready DATA, like a GOTO edition: every string the
device draws is prepared here, so the device only parses, paginates and renders.
The device version is the digest; the web edition (``build_web``) is the
expanded edition the final page's QR opens. The audit records why each
candidate was or was not picked.
"""

from __future__ import annotations

import datetime as dt
import json
import re

from .dataset import SOURCE_RANK, Record
from .quiet import QUIET_SCHEMA_VERSION, REGULAR_SCHEMA_VERSION, quiet_object
from .select import CTA_LABELS, Candidate, Selection, anniversary_bonus, offers_free_reading, qr_eligible
from .week import day_month, long_date, range_dateline, range_dateline_short, short_date

ISSUE_SCHEMA_VERSION = REGULAR_SCHEMA_VERSION
LABEL = "DOGEAR"
STRAPLINE = "a limited weekly reading digest"
# Planned host (not deployed). One stable, readable path per issue: unlike GOTO's
# news companion pages, a public DOGEAR archive is intended.
DEFAULT_BASE_URL = "https://dogear.archievalmariano.com"

_TYPE_WORDS = {"birth": "BORN", "death": "DIED", "publication": "PUBLISHED"}


def typeset(text: str) -> str:
    """Straight quotes to typographic ones, so the dataset can keep plain quotes."""
    text = re.sub(r'(^|[\s(\[])"', "\\1\u201c", text)
    text = text.replace('"', "\u201d")
    text = re.sub(r"(^|[\s(\[])'(?!\d)", "\\1\u2018", text)  # '70 is an elision, not a quote
    return text.replace("'", "\u2019")


def kicker(cand: Candidate) -> str:
    """``PUBLISHED 1865``, or ``1926 · 100 YEARS`` on a round anniversary.

    The round form drops the type word (the headline already says "is
    published" / "is born") so that with the date it stays one line on device.
    """
    rec = cand.record
    if rec.type == "observance":
        return event_label(rec)
    if anniversary_bonus(cand.years):
        return f"{rec.year} · {cand.years} YEARS"
    word = rec.label or _TYPE_WORDS.get(rec.type)
    return f"{word} {rec.year}" if word else str(rec.year)


def event_label(record: Record) -> str:
    """What kind of entry this is, in words, for the contents list: ``BORN``,
    ``DIED``, ``PUBLISHED``, a record label like ``NOBEL PRIZE``, or
    ``OBSERVANCE`` with ``· PH`` for a national one."""
    if record.type == "observance":
        return f"OBSERVANCE · {record.locality}" if record.locality else "OBSERVANCE"
    return record.label or _TYPE_WORDS.get(record.type) or "EVENT"


def contents_title(record: Record) -> str:
    """The short name used in the issue's contents list."""
    if record.type == "publication" and record.work:
        return record.work
    if record.type in ("birth", "death", "literary-event") and record.person:
        return record.person  # the label says what happened: BORN, NOBEL PRIZE
    return record.headline


def edition_url(selection: Selection, base_url: str) -> str:
    return f"{base_url.rstrip('/')}/{selection.week.start.isoformat()}/"


def _cta(rec: Record) -> dict:
    """What a reading screen needs to say exactly what the reader is opening."""
    link = rec.link
    note = {"read-online": f"Free on {link.provider}", "borrow": f"Borrow via {link.provider}",
            "find-book": f"Find it via {link.provider}"}.get(link.type, link.provider)
    return {
        "label": CTA_LABELS[link.type],
        "title": typeset(link.title),
        "author": link.author,
        "edition": link.edition,
        "match": link.match,
        "representative": link.representative,
        "note": note,
        "url": link.url,
    }


def build_issue(selection: Selection, generated_at: dt.datetime, base_url: str = DEFAULT_BASE_URL) -> dict:
    week = selection.week
    entries = []
    for cand in selection.picked:
        rec = cand.record
        entries.append(
            {
                "id": rec.id,
                "role": cand.role,
                "date": cand.date.isoformat(),
                "dateLabel": day_month(cand.date),
                "kicker": kicker(cand),
                "contentsLabel": event_label(rec),
                "title": typeset(contents_title(rec)),
                "headline": typeset(rec.headline),
                "body": typeset(rec.digest),
                "secondPage": rec.allow_second_page,
                "cta": _cta(rec) if cand.qr else None,
            }
        )
    next_monday = week.start + dt.timedelta(days=7)
    quiet = selection.edition == "quiet"
    issue = {
        # A quiet issue is schema 3: pre-quiet firmware accepts only 2, so it rejects a
        # quiet issue and keeps its verified cache rather than show past-dated items
        # without their explanation (PUBLISHING.md §21).
        "schemaVersion": QUIET_SCHEMA_VERSION if quiet else ISSUE_SCHEMA_VERSION,
        "issueId": week.issue_id,
        "preview": selection.preview,
        "label": LABEL,
        "strapline": STRAPLINE,
        "rangeStart": week.start.isoformat(),
        "rangeEnd": week.end.isoformat(),
        "dateline": range_dateline(week),
        "datelineShort": range_dateline_short(week),
        "nextIssue": long_date(next_monday),
        "editionUrl": edition_url(selection, base_url),
        "generatedAt": generated_at.isoformat(timespec="seconds"),
        "datasetSha256": selection.dataset_sha256,
        "entries": entries,
    }
    if quiet:  # the issue belongs to this week; each entry keeps its own original date
        issue["quiet"] = quiet_object()
    return issue


def _reading_links(rec: Record) -> list[dict]:
    """Links the web edition may show. Same territory rule as the device QR:
    a US-only public-domain copy is not offered as free reading."""
    if rec.link is None:
        return []
    if rec.link.type in ("source", "read-more"):
        return [_cta(rec)]
    if qr_eligible(rec):
        return [_cta(rec)]
    return []


def _same_url(a: str, b: str) -> bool:
    def norm(u: str) -> str:
        u = u.strip()
        u = u.split("://", 1)[-1].lower() if "://" in u else u.lower()
        return u[4:].rstrip("/") if u.startswith("www.") else u.rstrip("/")
    return norm(a) == norm(b)


def visible_sources(rec: Record, links: list[dict]) -> list[dict]:
    """The sources a reader sees: the dataset keeps every one for provenance,
    the publication stays selective.

    * A source that is the page the entry's reading action already opens is
      not listed again.
    * Wikidata is not listed when any other source remains: it backs the
      record, but a human-readable page serves a reader better.
    * Primary and institutional sources come first, then secondary, then
      reference.
    """
    shown = [s for s in rec.sources if not any(_same_url(s.url, l["url"]) for l in links)]
    readable = [s for s in shown if "wikidata.org" not in s.url]
    shown = sorted(readable or shown, key=lambda s: -SOURCE_RANK[s.kind])  # strongest first; stable
    return [{"title": s.title, "url": s.url} for s in shown]


def build_web(selection: Selection, issue: dict) -> dict:
    """The expanded edition: every device entry with fuller copy, sources and
    links, plus the week's other eligible dates that did not fit the digest."""
    by_id = {c.record.id: c for c in selection.picked}
    entries = []
    for e in issue["entries"]:
        rec = by_id[e["id"]].record
        links = _reading_links(rec)
        entries.append(
            {
                **e,
                "body": typeset(rec.expanded or rec.digest),
                "links": links,
                "sources": visible_sources(rec, links),
                "observedDate": rec.date_basis == "observed",
                "freeReadingWithheld": rec.free_reading_withheld,
            }
        )
    also = [
        {
            "dateLabel": day_month(c.date),
            "kicker": kicker(c),
            "contentsLabel": event_label(c.record),
            "headline": typeset(c.record.headline),
            "sources": [{"title": s.title, "url": s.url} for s in c.record.sources],
        }
        for c in sorted(selection.runners_up, key=lambda c: (c.date, c.record.id))
    ]
    return {**issue, "entries": entries, "alsoThisWeek": also}


def build_audit(selection: Selection) -> dict:
    return {
        "issueId": selection.week.issue_id,
        "preview": selection.preview,
        "datasetSha256": selection.dataset_sha256,
        "candidates": [
            {
                "id": c.record.id,
                "date": c.date.isoformat(),
                "status": c.record.status,
                "approval": c.record.approval_state,
                "recognition": c.record.recognition,
                "affinity": c.affinity,
                "intrinsic": c.intrinsic,
                "lifts": c.lifts,
                "history": c.history,
                "score": c.score,
                "composition": c.composition if c.pick_order else None,
                "reasons": c.reasons + (c.composition_reasons if c.pick_order else []),
                "picked": c.excluded is None,
                "pickOrder": c.pick_order,
                "role": c.role,
                "qr": c.qr,
                "excluded": c.excluded,
            }
            for c in selection.candidates
        ],
    }


def _tags(c: Candidate) -> str:
    r = c.record
    bits = [r.recognition]
    if r.ph_relevance:
        bits.append("PH")
    elif r.area == "southeast-asia" or r.sea_relevance:
        bits.append("SEA")
    elif r.is_wider_asian:
        bits.append("Asia")
    if c.affinity:
        bits.append(f"affinity:{c.affinity}")
    return ", ".join(bits)


def render_report(selection: Selection) -> str:
    """The editor's view of an issue: what was picked, what nearly was, and why."""
    w = selection.week
    out = [f"# {w.issue_id} — {range_dateline(w)}", ""]
    if selection.preview:
        out += ["*Proof: built with `--preview`; records still await approval.*", ""]
    out += ["## Selected", "", "| # | Entry | Date | Tier / lens | Score | Composition | Role |", "|---|---|---|---|---|---|---|"]
    for c in selection.picked:
        comp = f"{c.composition:+d} ({'; '.join(c.composition_reasons)})" if c.composition_reasons else "0"
        role = c.role + (" · QR" if c.qr else "")
        out.append(f"| {c.pick_order} | {c.record.headline} | {short_date(c.date)} | {_tags(c)} | {c.score} | {comp} | {role} |")
    out += ["", "Pick order is the order the composer chose them; the issue itself runs featured first, then by date.", ""]
    shaped = [c for c in selection.runners_up]
    other = [c for c in selection.candidates if c.excluded and c not in shaped]
    out += ["## Near misses (lost to the issue's shape)", ""]
    out += [f"- **{c.record.headline}** ({short_date(c.date)}, {_tags(c)}, score {c.score}): {c.excluded}" for c in shaped] or ["- none"]
    out += ["", "## Not eligible", ""]
    out += [f"- {c.record.headline} ({short_date(c.date)}): {c.excluded}" for c in other] or ["- none"]
    out += ["", "## Ranking factors", ""]
    for c in sorted(selection.candidates, key=lambda c: (-c.score, c.record.id)):
        mark = "✓" if c.excluded is None else "·"
        out.append(f"- {mark} `{c.record.id}` {c.score}: " + "; ".join(c.reasons))
    return "\n".join(out) + "\n"


def dumps(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def render_text(issue: dict) -> str:
    """A plain-text proof of the issue, for review in a terminal or a diff."""
    out = [issue["label"], issue["strapline"], issue["dateline"]]
    if issue.get("preview"):
        out.append("PREVIEW: contains records awaiting approval; not publishable")
    out.append("=" * 60)
    for e in issue["entries"]:
        tag = "FEATURED  " if e["role"] == "featured" else ""
        out.append("")
        out.append(f"{tag}{e['dateLabel'].upper()} · {e['kicker']}")
        out.append(e["headline"])
        out.append(e["body"])
        if e["cta"]:
            c = e["cta"]
            out.append(f"[QR] {c['label']}: {c['title']}, {c['author']} ({c['match']}) — {c['note']} — {c['url']}")
    out.append("")
    out.append("-" * 60)
    out.append("EVERYTHING IS DOGEARED HERE")
    out.append(f"[QR] {issue['editionUrl']}")
    out.append(f"Next issue {issue['nextIssue']}.")
    return "\n".join(out) + "\n"
