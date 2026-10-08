"""Generate and validate one week's publication artifacts.

The inputs are frozen and recorded in the provenance (generator, dataset,
affinity, the published history, generatedAt, base URL, policy), so the same
inputs must regenerate byte-identical artifacts. Every check must pass; any
problem refuses the stage and nothing is written.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from ..affinity import EMPTY, parse_affinity
from ..dataset import DatasetError, parse_dataset
from ..issue import build_issue, build_web, dumps
from ..quiet import QUIET_MIN, QUIET_SCHEMA_VERSION, REGULAR_SCHEMA_VERSION, QuietSource
from ..select import CTA_LABELS, History, select_issue, select_quiet_issue
from ..synthetic import synthetic_records
from ..web import render_web
from ..week import IssueWeek, week_starting
from . import eligibility
from .edition import revision_problems
from .policy import PublicationPolicy
from .registry import MAX_ISSUE_BYTES, issue_key, web_key

ALLOWED_CTA = frozenset(CTA_LABELS.values())

# DogearLimits.h, mirrored; tests/test_publish.py pins these to the header.
MAX_ENTRIES = 12
MAX_SHORT, MAX_LINE, MAX_HEADLINE, MAX_BODY, MAX_URL = 64, 160, 240, 2048, 2953

LinkChecker = Callable[[list], "LinkReport"]
FitChecker = Callable[[bytes, list], list]  # (dataset bytes, rendered record ids) -> problems


@dataclass
class LinkReport:
    blocking: list = field(default_factory=list)  # 404/410, DNS, TLS: refuse
    warnings: list = field(default_factory=list)  # timeouts, 5xx: re-checked at promotion


@dataclass(frozen=True)
class StageInputs:
    dataset: Path
    affinity: Path
    base_url: str
    generator: str  # "dogear@<git sha>"


@dataclass(frozen=True)
class Checks:
    link_checker: Optional[LinkChecker] = None
    fit_checker: Optional[FitChecker] = None


class StageRefused(Exception):
    def __init__(self, week: dt.date, problems: list):
        super().__init__(f"{week}: " + "; ".join(problems))
        self.problems = problems


class WeekHeld(StageRefused):
    """The editorial policy says this week does not publish (sparse or empty).
    ``empty``: the regular selection picked nothing, so a quiet issue may be tried."""

    def __init__(self, week: dt.date, problems: list, empty: bool = False):
        super().__init__(week, problems)
        self.empty = empty


def provenance_bytes(provenance: dict) -> bytes:
    return (json.dumps(provenance, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


@dataclass(frozen=True)
class Frozen:
    """Everything a generation depends on, captured once."""
    dataset_bytes: bytes
    affinity_bytes: bytes
    base_url: str
    generator: str
    policy: PublicationPolicy
    history: tuple  # ((monday iso, issueSha256, (record ids...)), ...) of published weeks
    generated_at: dt.datetime
    quiet_source: Optional[QuietSource] = None  # an empty week's quiet inputs (quiet_history)


@dataclass
class Staged:
    week: dt.date
    issue_bytes: bytes
    web_bytes: bytes
    provenance: dict
    validation: dict
    dataset_bytes: bytes = b""  # the frozen inputs, kept so promotion can regenerate
    affinity_bytes: bytes = b""

    @property
    def issue_id(self) -> str:
        return f"dogear-{self.week.isoformat()}"

    @property
    def issue_sha(self) -> str:
        return hashlib.sha256(self.issue_bytes).hexdigest()

    @property
    def web_sha(self) -> str:
        return hashlib.sha256(self.web_bytes).hexdigest()

    @property
    def provenance_bytes(self) -> bytes:
        return provenance_bytes(self.provenance)

    @property
    def issue_key(self) -> str:
        return issue_key(self.issue_id, self.issue_sha)

    @property
    def web_key(self) -> str:
        return web_key(self.week, self.web_sha)

    def revision(self, published_at: str) -> dict:
        inputs = self.provenance["inputs"]
        return {"issueId": self.issue_id, "issueKey": self.issue_key, "issueSha256": self.issue_sha,
                "issueBytes": len(self.issue_bytes), "webKey": self.web_key, "webSha256": self.web_sha,
                "provenanceSha256": hashlib.sha256(self.provenance_bytes).hexdigest(),
                "datasetSha256": inputs["datasetSha256"], "generator": inputs["generator"],
                "generatedAt": inputs["generatedAt"], "publishedAt": published_at}

    def files(self) -> dict:
        return {"issue.json": self.issue_bytes, "web.html": self.web_bytes,
                "provenance.json": self.provenance_bytes,
                "validation.json": (json.dumps(self.validation, sort_keys=True, indent=2) + "\n").encode("utf-8"),
                "dataset.json": self.dataset_bytes, "affinity.json": self.affinity_bytes}

    @classmethod
    def from_files(cls, week: dt.date, files: dict) -> "Staged":
        return cls(week, files["issue.json"], files["web.html"], json.loads(files["provenance.json"]),
                   json.loads(files["validation.json"]), files["dataset.json"], files["affinity.json"])


def _read(path: Path) -> bytes:
    try:
        return Path(path).read_bytes()
    except FileNotFoundError:
        return b""


def freeze(inputs: StageInputs, policy: PublicationPolicy, history: list, now: dt.datetime,
           quiet_source: Optional[QuietSource] = None) -> Frozen:
    return Frozen(_read(inputs.dataset), _read(inputs.affinity), inputs.base_url, inputs.generator, policy,
                  tuple((m, sha, tuple(ids)) for m, sha, ids in history), now, quiet_source)


def generate(week: IssueWeek, frozen: Frozen) -> tuple:
    """(selection, issue bytes, web bytes, provenance), from frozen inputs only."""
    try:
        dataset = parse_dataset(frozen.dataset_bytes.decode("utf-8"))
        affinity = parse_affinity(frozen.affinity_bytes.decode("utf-8")) if frozen.affinity_bytes else EMPTY
    except (DatasetError, UnicodeDecodeError, ValueError, KeyError, TypeError) as err:
        raise StageRefused(week.start, [f"inputs invalid: {err}"]) from None
    hist = History(tuple((dt.date.fromisoformat(m), tuple(ids)) for m, _sha, ids in frozen.history
                         if dt.date.fromisoformat(m) < week.start))
    selection = select_issue(dataset, week, hist, preview=False, affinity=affinity,
                             slot_bars=frozen.policy.slot_bars())
    source = frozen.quiet_source
    if (not selection.picked and frozen.policy.empty_week == "quiet-week" and source is not None
            and source.complete):
        selection = select_quiet_issue(dataset, week, hist, source, affinity=affinity,
                                       slot_bars=frozen.policy.slot_bars())
    subjects = []
    for c in selection.picked:  # stable identities, recorded with every digest (never names)
        r = c.record
        if (r.person and not r.person_id) or (r.work and not r.work_id):
            raise StageRefused(week.start, [f"{r.id}: no stable identity for its person or work"])
        subjects.append({"recordId": r.id, "personId": r.person_id, "workId": r.work_id})
    issue = build_issue(selection, frozen.generated_at, frozen.base_url)
    issue_bytes = dumps(issue).encode("utf-8")
    web_bytes = render_web(build_web(selection, issue)).encode("utf-8")
    records = [{"recordId": c.record.id, "fingerprint": c.record.fingerprint, "section": "digest", "position": i}
               for i, c in enumerate(selection.picked, 1)]
    records += [{"recordId": c.record.id, "fingerprint": c.record.fingerprint, "section": "also", "position": i}
                for i, c in enumerate(selection.runners_up, 1)]
    provenance = {
        "schemaVersion": 1,
        "week": week.start.isoformat(),
        "edition": selection.edition,
        "subjects": subjects,
        "records": records,
        "inputs": {
            "generator": frozen.generator,
            "datasetSha256": hashlib.sha256(frozen.dataset_bytes).hexdigest(),
            "affinitySha256": hashlib.sha256(frozen.affinity_bytes).hexdigest(),
            "history": [[m, sha, list(ids)] for m, sha, ids in frozen.history],
            "generatedAt": frozen.generated_at.isoformat(),
            "baseUrl": frozen.base_url,
            "policy": frozen.policy.as_json(),
            **({"quietSource": source.as_json()} if selection.edition == "quiet" else {}),
        },
        "artifacts": {"issueSha256": hashlib.sha256(issue_bytes).hexdigest(),
                      "webSha256": hashlib.sha256(web_bytes).hexdigest()},
    }
    return selection, issue_bytes, web_bytes, provenance


def external_links(selection) -> list:
    """Every outside reading link an edition shows (DOGEAR's own edition URL is not one)."""
    return sorted({c.record.link.url for c in selection.rendered if c.record.link is not None})


def run_checks(frozen: Frozen, selection, checks: Checks) -> tuple:
    """(blocking problems, warnings) from the link and fit checkers on these exact inputs."""
    problems, warnings = [], []
    if checks.link_checker is not None:
        report = checks.link_checker(external_links(selection))
        problems += [f"link: {b}" for b in report.blocking]
        warnings += [f"link: {w}" for w in report.warnings]
    if checks.fit_checker is not None:
        problems += [f"fit: {f}" for f in checks.fit_checker(frozen.dataset_bytes, [c.record.id for c in selection.rendered])]
    return problems, warnings


def device_limit_problems(issue: dict, issue_bytes: bytes) -> list:
    p = []
    if len(issue_bytes) > MAX_ISSUE_BYTES:
        p.append(f"issue is {len(issue_bytes)} B, over {MAX_ISSUE_BYTES}")
    if issue.get("schemaVersion") not in (REGULAR_SCHEMA_VERSION, QUIET_SCHEMA_VERSION):
        p.append(f"issue schemaVersion is not {REGULAR_SCHEMA_VERSION} or {QUIET_SCHEMA_VERSION}")
    quiet = issue.get("quiet")
    if quiet is not None:  # the device draws these on the cover (heading short, note one line)
        if not isinstance(quiet, dict) or set(quiet) != {"heading", "note"}:
            p.append("quiet must be {heading, note}")
        else:
            for name, limit in (("heading", MAX_SHORT), ("note", MAX_LINE)):
                value = quiet[name]
                if not isinstance(value, str) or not value or len(value.encode("utf-8")) > limit:
                    p.append(f"quiet {name} missing or over {limit} bytes")
    entries = issue.get("entries") or []
    if not 0 < len(entries) <= MAX_ENTRIES:
        p.append(f"{len(entries)} entries (device allows 1-{MAX_ENTRIES})")

    def cap(value, limit, name, required=True):
        if value is None or value == "":
            if required:
                p.append(f"{name} missing")
        elif not isinstance(value, str) or len(value.encode("utf-8")) > limit:
            p.append(f"{name} over {limit} bytes")

    for name, limit, req in (("label", MAX_SHORT, True), ("strapline", MAX_LINE, False),
                             ("dateline", MAX_SHORT, True), ("datelineShort", MAX_SHORT, True),
                             ("nextIssue", MAX_LINE, True), ("editionUrl", MAX_URL, False)):
        cap(issue.get(name), limit, name, req)
    for e in entries:
        for name, limit, req in (("id", MAX_SHORT, True), ("dateLabel", MAX_SHORT, True), ("kicker", MAX_SHORT, True),
                                 ("contentsLabel", MAX_SHORT, False), ("title", MAX_LINE, True),
                                 ("headline", MAX_HEADLINE, True), ("body", MAX_BODY, True)):
            cap(e.get(name), limit, f"entry {e.get('id')} {name}", req)
        cta = e.get("cta")
        if cta:
            for name, limit, req in (("label", MAX_SHORT, True), ("title", MAX_LINE, True), ("author", MAX_LINE, False),
                                     ("edition", MAX_LINE, False), ("note", MAX_LINE, False), ("url", MAX_URL, True)):
                cap(cta.get(name), limit, f"entry {e.get('id')} cta {name}", req)
            if not str(cta.get("url", "")).startswith("https://"):
                p.append(f"entry {e.get('id')} cta url is not https")
    return p


def stage(week: IssueWeek, inputs: StageInputs, policy: PublicationPolicy, history: list, now: dt.datetime,
          checks: Checks, mode: str = "test", quiet_source: Optional[QuietSource] = None) -> Staged:
    """A validated week, or StageRefused / WeekHeld.

    An empty week under ``emptyWeek: quiet-week`` needs ``quiet_source`` (the caller
    derives it from validated history only when needed): without one this raises
    WeekHeld(empty=True) so the caller can supply it; with one it builds a quiet
    issue of 3-5 items, or holds."""
    if policy.unset():
        raise StageRefused(week.start, [f"publication policy unset: {', '.join(policy.unset())}"])
    frozen = freeze(inputs, policy, history, now, quiet_source)
    if mode == "production":
        synthetic = synthetic_records(frozen.dataset_bytes.decode("utf-8", "replace"))
        if synthetic:  # the staging fixture, or any record marked like it, never reaches production
            raise StageRefused(week.start, [f"the dataset holds {len(synthetic)} synthetic record(s); "
                                            "production publishes no synthetic data"])
    selection, issue_bytes, web_bytes, provenance = generate(week, frozen)

    n = len(selection.picked)
    if selection.edition == "quiet":
        if n < QUIET_MIN:  # never padded: the previous issue stays current
            raise WeekHeld(week.start, [f"quiet week: {n} qualifying item(s), fewer than {QUIET_MIN}"])
    elif n == 0:
        if policy.empty_week == "quiet-week" and quiet_source is not None and not quiet_source.complete:
            raise WeekHeld(week.start, ["quiet week: subject history incomplete"])
        raise WeekHeld(week.start, [f"no eligible items; emptyWeek policy: {policy.empty_week}"],
                       empty=quiet_source is None)
    elif n <= 2 and policy.sparse_week == "hold-previous":
        raise WeekHeld(week.start, [f"{n} item(s); sparseWeek policy: hold-previous"])

    issue = json.loads(issue_bytes)
    problems = eligibility.check(provenance, frozen.dataset_bytes.decode("utf-8"), issue)
    _sel2, issue2, web2, prov2 = generate(week, frozen)
    if (issue2, web2, prov2) != (issue_bytes, web_bytes, provenance):
        problems.append("regenerating from the frozen inputs gave different bytes")
    problems += artifact_problems(week, inputs.base_url, selection, issue, issue_bytes, web_bytes)
    problems += revision_problems(week.start, issue, provenance)
    blocking, warnings = run_checks(frozen, selection, checks)
    problems += blocking
    if problems:
        raise StageRefused(week.start, problems)
    validation = {
        "week": week.start.isoformat(),
        "mode": mode,
        "checks": {"eligibility": "ok", "contentBinding": "ok", "notProof": "ok", "ownUrl": "ok",
                   "accessLabels": "ok", "deviceLimits": "ok",
                   "externalLinks": "ok" if checks.link_checker else "not configured",
                   "fit": "ok" if checks.fit_checker else "not configured"},
        "externalLinks": external_links(selection),
        "warnings": warnings,
        "items": n,
        "edition": selection.edition,
    }
    return Staged(week.start, issue_bytes, web_bytes, provenance, validation, frozen.dataset_bytes,
                  frozen.affinity_bytes)


def artifact_problems(week: IssueWeek, base_url: str, selection, issue: dict, issue_bytes: bytes,
                      web_bytes: bytes) -> list:
    problems = []
    if selection.preview or b"Proof." in web_bytes or b"noindex" in web_bytes:
        problems.append("artifact is marked as a proof")
    own = f"{base_url.rstrip('/')}/{week.start.isoformat()}/"
    if issue.get("editionUrl") != own:
        problems.append(f"final QR {issue.get('editionUrl')!r} is not {own!r}")
    for e in issue["entries"]:
        label = (e.get("cta") or {}).get("label")
        if label is not None and label not in ALLOWED_CTA:
            problems.append(f"entry {e['id']}: CTA label {label!r} not allowed")
    if b"READ FREE" in issue_bytes.upper() or b"READ FREE" in web_bytes.upper():
        problems.append("'READ FREE' appears in the artifacts")
    return problems + device_limit_problems(issue, issue_bytes)


def verify_staged(staged: Staged, inputs: StageInputs, policy: PublicationPolicy, history: list,
                  current_dataset_text: str, checks: Checks, mode: str,
                  quiet_source: Optional[QuietSource] = None) -> list:
    """Problems that stop a staged week from being published as it stands.

    The staged files are trusted for nothing: the issue, the web page and the
    complete provenance must regenerate byte for byte from the frozen inputs
    staged beside them, and every record that regeneration renders must be
    publishable in the dataset as it is now."""
    prov, problems = staged.provenance, []
    if mode == "production" and (synthetic_records(staged.dataset_bytes.decode("utf-8", "replace")) or
                                 synthetic_records(current_dataset_text)):
        return ["the dataset holds synthetic records; production publishes no synthetic data"]
    want = prov.get("inputs", {}) if isinstance(prov, dict) else {}
    if prov.get("week") != staged.week.isoformat():
        return ["staged for another week"]
    if want.get("datasetSha256") != hashlib.sha256(staged.dataset_bytes).hexdigest() or \
            want.get("affinitySha256") != hashlib.sha256(staged.affinity_bytes).hexdigest():
        return ["the staged inputs do not match their provenance"]
    if want.get("generator") != inputs.generator:
        problems.append(f"the generator changed since staging ({want.get('generator')} -> {inputs.generator})")
    if want.get("baseUrl") != inputs.base_url:
        problems.append("the base URL changed since staging")
    if want.get("policy") != policy.as_json():
        problems.append("the publication policy changed since staging")
    if want.get("history") != [list(h) for h in history]:
        problems.append("the published history changed since staging")
    frozen_source = None
    if prov.get("edition") == "quiet":
        # The quiet inputs are re-derived from validated history as it is now: any
        # change since Friday (a correction, a new line) re-stages at promotion.
        try:
            frozen_source = QuietSource.from_json(want.get("quietSource"))
        except ValueError as err:
            return [f"the staged quiet source is invalid ({err})"]
        if quiet_source is None or not quiet_source.complete or quiet_source.as_json() != want.get("quietSource"):
            problems.append("the published history changed since staging")
    elif "quietSource" in want:
        problems.append("a regular week's provenance carries a quiet source")
    if problems:
        return problems
    try:
        generated_at = dt.datetime.fromisoformat(want["generatedAt"])
    except (KeyError, TypeError, ValueError):
        return ["staged generatedAt unreadable"]
    frozen = Frozen(staged.dataset_bytes, staged.affinity_bytes, inputs.base_url, inputs.generator, policy,
                    tuple((m, sha, tuple(ids)) for m, sha, ids in history), generated_at, frozen_source)
    week = week_starting(staged.week)
    try:
        selection, issue_bytes, web_bytes, regenerated = generate(week, frozen)
    except StageRefused as err:
        return err.problems
    if (issue_bytes, web_bytes, provenance_bytes(regenerated)) != \
            (staged.issue_bytes, staged.web_bytes, provenance_bytes(prov)):
        return ["the staged files do not regenerate from their frozen inputs"]
    issue = json.loads(issue_bytes)
    problems += eligibility.check(regenerated, current_dataset_text, issue)
    problems += artifact_problems(week, inputs.base_url, selection, issue, issue_bytes, web_bytes)
    problems += revision_problems(week.start, issue, regenerated)
    if mode == "production" and staged.validation.get("mode") != "production":
        problems.append("staged without production validation")
    blocking, _warnings = run_checks(frozen, selection, checks)
    return problems + blocking
