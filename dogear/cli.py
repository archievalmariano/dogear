"""DOGEAR command line. Run from dogear/.

    python3 -m dogear.cli validate
    python3 -m dogear.cli review                       # records awaiting approval
    python3 -m dogear.cli approve <id>... --by "Name"  # human sign-off
    python3 -m dogear.cli links                        # every reading destination, for review
    python3 -m dogear.cli balance [--issues DIR]       # dataset + rolling balance report
    python3 -m dogear.cli issue --week 2026-11-23 --out out [--preview] [--issues DIR]
    python3 -m dogear.cli stage --from out --issue dogear-2026-11-23 --dir stage   # dev server layout
    python3 -m dogear.cli publish --store DIR --pubdata DIR <op>   # the weekly publisher (dogear/publish/cli.py)

``issue`` never touches the network and never calls a model: it reads the
dataset and the affinity list, selects, and writes ``<issueId>.json`` (device),
``<issueId>.audit.json``, ``<issueId>.report.md`` (editor's report),
``<issueId>.txt`` (proof) and ``web/<date>/index.html`` (expanded edition).
``--issues DIR`` points at earlier issue JSONs (the publication history) for the
repeat and regional-overrepresentation penalties and the rolling balance.
Only approved records are used unless ``--preview``, which also admits verified
records awaiting approval and marks the issue as a proof that must not publish.
Publishing is a separate, later step.

``stage`` lays issues out the way the device reads them, for a LOCAL dev server
only (``python3 -m http.server 8080 --directory stage``)::

    current.json                 {"issueId", "issuePath", "issueSha256"}
    issues/<issueId>.json        the device payload, byte for byte
    <monday>/index.html          the web edition the final QR opens
    fonts/                       its self-hosted typefaces (Inter, Newsreader; OFL)

The device trusts an issue only if its bytes hash to ``issueSha256``.
``current.json`` is written last and atomically, so it never points at a missing
issue. Build the issues with ``--base-url`` set to the dev server so the QRs
resolve on the same network.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import hashlib
import os
import re
import shutil

from .affinity import load_affinity
from .balance import render_balance
from .dataset import DatasetError, approve, load_dataset
from .issue import DEFAULT_BASE_URL, build_audit, build_issue, build_web, dumps, render_report, render_text
from .select import History, qr_eligible, select_issue
from .web import render_web
from .week import MANILA, current_week, week_starting

FONTS = Path(__file__).resolve().parent.parent / "web-assets" / "fonts"
# The canonical dataset and the editor's affinity list are private: they live in the
# dogear-editorial repository, checked out (or linked) at dataset/ beside this code.
EDITORIAL = Path(__file__).resolve().parent.parent / "dataset"
DEFAULT_DATASET = EDITORIAL / "data" / "literary-dates.json"
DEFAULT_AFFINITY = EDITORIAL / "data" / "affinity.json"
_ISSUE_FILE = re.compile(r"^dogear-(\d{4}-\d{2}-\d{2})\.json$")


def load_issues(directory: Path | None) -> list[tuple[dt.date, tuple[str, ...]]]:
    """Earlier issues as (monday, record ids), read from issue JSONs in a directory."""
    if not directory or not Path(directory).is_dir():
        return []
    out = []
    for path in sorted(Path(directory).iterdir()):
        m = _ISSUE_FILE.match(path.name)
        if m:
            payload = json.loads(path.read_text(encoding="utf-8"))
            out.append((dt.date.fromisoformat(m.group(1)), tuple(e["id"] for e in payload["entries"])))
    return out


def _load(path: Path):
    if not Path(path).exists():
        print(f"no dataset at {path}. The canonical dataset is private: check out dogear-editorial at "
              f"dataset/, or pass --dataset (e.g. fixtures/staging-year.json)", file=sys.stderr)
        sys.exit(2)
    try:
        return load_dataset(path)
    except DatasetError as err:
        print(err, file=sys.stderr)
        sys.exit(2)


def cmd_validate(args) -> int:
    ds = _load(args.dataset)
    for w in ds.warnings:
        print("warning:", w)
    counts: dict[str, int] = {}
    for r in ds.records:
        key = f"{r.status}/{r.approval_state}"
        counts[key] = counts.get(key, 0) + 1
    summary = ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))
    print(f"{len(ds.records)} records OK ({summary}); sha256 {ds.sha256[:12]}")
    return 0


def cmd_review(args) -> int:
    ds = _load(args.dataset)
    pending = [r for r in ds.records if r.status == "verified" and r.approval_state != "approved"]
    blocked = [r for r in ds.records if r.status != "verified"]
    for r in pending:
        when = f"{r.month:02d}-{r.day:02d}" if r.precision == "day" else f"{r.month:02d}-??"
        sourcing = r.source_confidence + ("" if r.sourcing_ok else " (too weak: complex claim)")
        claims = "complex" if r.claims == "complex" else ""
        print(f"{r.approval_state:<10} {when}  {r.id:<40} {r.recognition:<9} {sourcing:<36} {claims:<8} {r.headline}")
    for r in blocked:
        print(f"{r.status:<10} {'':5}  {r.id:<40} not approvable until verified")
    print(f"\n{len(pending)} awaiting approval, {len(blocked)} not yet verified.")
    return 0


def cmd_links(args) -> int:
    """Every link with the work it opens and why, so a mismatch is easy to spot."""
    ds = _load(args.dataset)
    rows = [r for r in ds.records if r.links]
    count = 0
    for r in rows:
        for l in r.links:
            count += 1
            chosen = l is r.link
            if chosen:
                device = "device QR" if qr_eligible(r) else "web only"
            else:
                device = "not shown (a better link wins)" if l.access_tier else "not shown (no access for PH readers)"
            tier = f"tier {l.access_tier}" if l.access_tier else "context" if l.type in ("read-more", "source") else "withheld"
            who = f", {l.author}" if l.author else ""
            market = f" [{l.market}]" if l.market else ""
            print(f"{r.id:<40} {l.type:<12}{market:<7} {tier:<9} {l.match or '-':<20} {l.title}{who} · {l.provider} · "
                  f"{l.rights or '-'} · {'CHOSEN · ' if chosen else ''}{device}")
    print(f"\n{count} links on {len(rows)} records. Each record shows its best link for a Philippine reader: free text, "
          "then borrow (PH, elsewhere), then find (PH bookseller, elsewhere). Matches are checked on every load.")
    return 0


def cmd_approve(args) -> int:
    try:
        done = approve(args.dataset, args.ids, args.by, dt.date.fromisoformat(args.on) if args.on else dt.date.today())
    except (KeyError, ValueError, DatasetError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    for rid in done:
        print("approved", rid)
    unchanged = sorted(set(args.ids) - set(done))
    if unchanged:
        print("already approved at current content:", ", ".join(unchanged))
    return 0


def cmd_balance(args) -> int:
    print(render_balance(_load(args.dataset), load_affinity(args.affinity), load_issues(args.issues)), end="")
    return 0


def cmd_issue(args) -> int:
    ds = _load(args.dataset)
    week = week_starting(dt.date.fromisoformat(args.week)) if args.week else current_week()
    history = History(tuple((d, ids) for d, ids in load_issues(args.issues) if d < week.start))
    selection = select_issue(ds, week, history, preview=args.preview, affinity=load_affinity(args.affinity))
    if not selection.picked:
        print(f"{week.issue_id}: no eligible entries; nothing written (the current issue stays).", file=sys.stderr)
        return 3
    now = dt.datetime.fromisoformat(args.now) if args.now else dt.datetime.now(MANILA)
    issue = build_issue(selection, now, args.base_url)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{week.issue_id}.json").write_text(dumps(issue), encoding="utf-8")
    (out / f"{week.issue_id}.audit.json").write_text(dumps(build_audit(selection)), encoding="utf-8")
    (out / f"{week.issue_id}.report.md").write_text(render_report(selection), encoding="utf-8")
    text = render_text(issue)
    (out / f"{week.issue_id}.txt").write_text(text, encoding="utf-8")
    web_dir = out / "web" / week.start.isoformat()
    web_dir.mkdir(parents=True, exist_ok=True)
    (web_dir / "index.html").write_text(render_web(build_web(selection, issue)), encoding="utf-8")
    if not args.quiet:
        print(text, end="")
    return 0


def stage(source: Path, issue_id: str, dest: Path) -> dict:
    """Copy an issue and every web edition from ``source`` into ``dest`` and point
    ``current.json`` at ``issue_id``. Returns the manifest."""
    if not _ISSUE_FILE.match(f"{issue_id}.json"):
        raise ValueError(f"not an issue id: {issue_id}")
    raw = (source / f"{issue_id}.json").read_bytes()
    json.loads(raw)  # refuse to stage a file that does not parse
    (dest / "issues").mkdir(parents=True, exist_ok=True)
    (dest / "issues" / f"{issue_id}.json").write_bytes(raw)
    if FONTS.is_dir():
        shutil.copytree(FONTS, dest / "fonts", dirs_exist_ok=True)
    web = source / "web"
    for day in sorted(web.iterdir()) if web.is_dir() else []:
        if (day / "index.html").is_file():
            (dest / day.name).mkdir(exist_ok=True)
            shutil.copyfile(day / "index.html", dest / day.name / "index.html")
    manifest = {"issueId": issue_id, "issuePath": f"issues/{issue_id}.json",
                "issueSha256": hashlib.sha256(raw).hexdigest()}
    tmp = dest / "current.json.tmp"
    tmp.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, dest / "current.json")
    return manifest


def cmd_stage(args) -> int:
    try:
        manifest = stage(Path(args.source), args.issue, Path(args.dir))
    except (OSError, ValueError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    print(f"current -> {manifest['issueId']} (sha256 {manifest['issueSha256'][:12]}) in {args.dir}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="dogear")
    p.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    p.add_argument("--affinity", type=Path, default=DEFAULT_AFFINITY)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("validate").set_defaults(fn=cmd_validate)
    sub.add_parser("review").set_defaults(fn=cmd_review)
    sub.add_parser("links").set_defaults(fn=cmd_links)
    pa = sub.add_parser("approve")
    pa.add_argument("ids", nargs="+")
    pa.add_argument("--by", required=True, help="the approving editor")
    pa.add_argument("--on", help="approval date YYYY-MM-DD (default today)")
    pa.set_defaults(fn=cmd_approve)
    pb = sub.add_parser("balance")
    pb.add_argument("--issues", type=Path, help="directory of published issue JSONs for the rolling windows")
    pb.set_defaults(fn=cmd_balance)
    pi = sub.add_parser("issue")
    pi.add_argument("--week", help="the Monday of the issue week, YYYY-MM-DD (default: this week, Manila)")
    pi.add_argument("--out", default="out")
    pi.add_argument("--preview", action="store_true", help="admit verified records awaiting approval (proof only)")
    pi.add_argument("--base-url", default=DEFAULT_BASE_URL, help="web edition host for the final QR")
    pi.add_argument("--issues", type=Path, help="directory of earlier issue JSONs (history)")
    pi.add_argument("--now", help="generatedAt override (ISO datetime), for reproducible output")
    pi.add_argument("--quiet", action="store_true")
    pi.set_defaults(fn=cmd_issue)
    ps = sub.add_parser("stage", help="lay out issues for a local dev server (never publishes)")
    ps.add_argument("--from", dest="source", default="out", help="an issue --out directory")
    ps.add_argument("--issue", required=True, help="the issue current.json points at, e.g. dogear-2026-11-23")
    ps.add_argument("--dir", default="stage")
    ps.set_defaults(fn=cmd_stage)
    from .publish.cli import add_parser as add_publish

    add_publish(sub)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
