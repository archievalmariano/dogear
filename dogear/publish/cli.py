"""``dogear publish``: run the publisher against a store and a publication-data directory.

    python3 -m dogear.cli publish --store DIR --pubdata DIR status
    ... stage-next | stage --week 2026-11-09 | promote | launch --first-publication
    ... correct --week W --expect-rev N --reason TEXT | rollback --week W [--rev N] --reason TEXT
    ... restore --week W --rev N | resume | reconcile | abandon TXN

``--store DIR`` is a local directory standing in for the bucket, verified
through the reference router. ``--store r2:BUCKET`` is R2 (credentials from
DOGEAR_R2_ACCOUNT_ID, DOGEAR_R2_ACCESS_KEY_ID, DOGEAR_R2_SECRET_ACCESS_KEY; never
arguments), and served output is then verified over HTTPS at ``--base-url``.
Production mode (the default) refuses while data/publication-policy.json has
an unset decision; ``--mode test`` with ``--policy`` is for local rehearsal.

``--target staging|production`` (what the workflows use) sets the store, host,
mode, dataset and policy together from ``TARGETS``, so the two can never be
mixed through loose settings; it refuses those flags alongside it.

Exit codes: 0 done or nothing to do; 2 aborted (nothing new published);
3 needs a human; 4 not ready (policy or checks unset, or a test-only flag in
production); 5 published but its record not saved (the next run records it);
6 refused; 7 stage refused; 8 published but the host does not serve it yet
(each run re-checks, read-only, until it does).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

from ..cli import DEFAULT_AFFINITY, DEFAULT_DATASET
from ..week import MANILA
from .checks import http_link_checker, http_served, mock_fit_checker
from .drills import DRILLS, DrillStore, failing_once
from .policy import PolicyError, load_policy
from .publisher import Aborted, NeedsHuman, NotReady, Publisher, PublishedUnrecorded, PublishedUnverified
from .pubdata import CorruptState, PubData, SaveFailed, git_committer
from .registry import CorruptRegistry, Refused
from .staging import Checks, StageInputs, StageRefused
from .r2 import R2Store
from .store import DirStore
from ..synthetic import non_synthetic_records

ROOT = Path(__file__).resolve().parents[2]
FONTS = ROOT / "web-assets" / "fonts"
PRODUCTION_HOST = "https://dogear.archievalmariano.com"

# The only two hosted configurations. Staging publishes the year-round synthetic fixture
# (and nothing else: see dogear/synthetic.py) in test mode with a test policy, on the
# real calendar; production publishes approved canonical records in production mode,
# refuses while any editorial decision is unset, and refuses any synthetic record.
TARGETS = {
    "staging": {"store": "r2:dogear-issues-staging", "base_url": "https://dogear-staging.pages.dev",
                "mode": "test", "policy": ROOT / "fixtures" / "staging-policy.json",
                "dataset": ROOT / "fixtures" / "staging-year.json",
                "affinity": ROOT / "fixtures" / "staging-affinity.json"},
    # The canonical dataset and affinity are private (dogear-editorial, checked out read-only at dataset/).
    "production": {"store": "r2:dogear-issues", "base_url": PRODUCTION_HOST, "mode": "production",
                   "policy": ROOT / "data" / "publication-policy.json",
                   "dataset": ROOT / "dataset" / "data" / "literary-dates.json",
                   "affinity": ROOT / "dataset" / "data" / "affinity.json"},
}


def add_parser(sub) -> None:
    p = sub.add_parser("publish", help="the weekly publisher (local store until S2)")
    p.add_argument("--target", choices=sorted(TARGETS), help="a fixed staging or production profile")
    p.add_argument("--store", help="a directory, or r2:BUCKET")
    p.add_argument("--pubdata", type=Path, required=True)
    p.add_argument("--mode", choices=("production", "test"))
    p.add_argument("--policy", type=Path)
    p.add_argument("--base-url")
    p.add_argument("--git", action="store_true", help="commit and push --pubdata after each step")
    p.add_argument("--now", help="override the clock (ISO datetime with offset); --mode test only")
    p.add_argument("--no-network-checks", action="store_true", help="test mode only: skip link and fit checks")
    p.add_argument("--drill", choices=DRILLS, help="test mode only: inject one event around the registry write")
    p.add_argument("--public-log", action="store_true",
                   help="print only result, operation, week and transaction (the workflows: public Actions logs)")
    ops = p.add_subparsers(dest="op", required=True)
    for name in ("status", "reconcile", "stage-next", "promote", "resume"):
        ops.add_parser(name)
    s = ops.add_parser("stage")
    s.add_argument("--week", required=True)
    s = ops.add_parser("launch")
    s.add_argument("--first-publication", action="store_true")
    s = ops.add_parser("correct")
    s.add_argument("--week", required=True)
    s.add_argument("--expect-rev", type=int, required=True)
    s.add_argument("--reason", required=True)
    s = ops.add_parser("rollback")
    s.add_argument("--week", required=True)
    s.add_argument("--rev", type=int)
    s.add_argument("--reason", required=True)
    s = ops.add_parser("restore")
    s.add_argument("--week", required=True)
    s.add_argument("--rev", type=int, required=True)
    s = ops.add_parser("abandon")
    s.add_argument("txn")
    p.set_defaults(fn=run)


def _generator() -> str:
    try:
        sha = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short=12", "HEAD"], capture_output=True,
                             text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", "--", "."], capture_output=True,
                               text=True, check=True).stdout.strip()
        return f"dogear@{sha}{'+dirty' if dirty else ''}"
    except (OSError, subprocess.CalledProcessError):
        return "dogear@unknown"


def _store(spec: str, base_url: str, fixed: bool) -> tuple:
    """(store, served): R2 is verified over HTTPS at the public host; a local
    directory through the reference router (served=None). A fixed --target never
    takes an endpoint override; an explicit r2: store may, for a local test server."""
    if spec.startswith("r2:"):
        return R2Store.from_env(spec[3:], allow_endpoint_override=not fixed), http_served(base_url)
    return DirStore(Path(spec)), None


def _apply_target(args) -> Optional[str]:
    """Fill the settings from --target, or the local defaults without one. An error, or None."""
    if args.target:
        clash = [flag for flag, value in (("--store", args.store), ("--mode", args.mode), ("--policy", args.policy),
                                          ("--base-url", args.base_url)) if value is not None]
        for flag, given, default in (("--dataset", args.dataset, DEFAULT_DATASET),
                                     ("--affinity", args.affinity, DEFAULT_AFFINITY)):
            if Path(given).resolve() != Path(default).resolve():
                clash.append(flag)
        if clash:
            return f"--target sets {', '.join(clash)}; do not pass them"
        t = TARGETS[args.target]
        args.store, args.base_url, args.mode, args.policy, args.dataset, args.affinity = (
            t["store"], t["base_url"], t["mode"], t["policy"], t["dataset"], t["affinity"])
        missing = [str(p.relative_to(ROOT)) for p in (args.dataset, args.affinity) if not p.is_file()]
        if missing:  # a missing affinity file would otherwise mean "no preferences", silently
            return f"--target {args.target} needs {', '.join(missing)}" + (
                " (check out dogear-editorial at dataset/)" if args.target == "production" else "")
        if args.target == "staging":
            strays = non_synthetic_records(args.dataset.read_text(encoding="utf-8"))
            if strays:  # the canonical dataset, or a real record in the fixture, never reaches staging
                return f"--target staging publishes only synthetic records; {len(strays)} record(s) are not"
        return None
    if args.store is None:
        return "--store or --target is required"
    args.mode = args.mode or "production"
    args.policy = args.policy or ROOT / "data" / "publication-policy.json"
    args.base_url = args.base_url or PRODUCTION_HOST
    return None


_TXN_ID = re.compile(r"\b[0-9]{8}T[0-9]{6}-[a-z]+-[0-9a-f]{12}\b")
_SAVED = (4, 6, 7)  # refusals that published nothing: their detail is kept privately


class _Report:
    """Where a run's result goes.

    Without --public-log everything is printed, as before. With it (always, from the
    workflows: a public repository's Actions logs are readable by anyone) a line
    carries only the kind of result, the operation, the week and the transaction
    id. Reasons, record ids, link URLs, hold reasons and notices are never printed.
    For a refusal that published nothing (exit 4, 6, 7), and for a success's
    notices, the detail is saved to the private publication data
    (runs/<UTC>-<op>.log). Anything else is recorded in that data already
    (history, registry, pending) or is reproduced by running the same command
    privately."""

    def __init__(self, args, pubdata: PubData) -> None:
        self.args, self.pubdata = args, pubdata

    def _line(self, kind: str, txns: list) -> str:
        week = getattr(self.args, "week", None)
        return " ".join([f"{kind}: {self.args.op}", *([week] if week else []), *txns])

    def _keep(self, detail: str) -> bool:
        if not self.args.git:
            return False
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = self.pubdata.root / "runs" / f"{stamp}-{self.args.op}.log"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(detail.rstrip() + "\n", encoding="utf-8")
            self.pubdata.save(f"run log {self.args.op}")
        except (OSError, SaveFailed):
            return False
        return True

    def failure(self, code: int, kind: str, err: Exception) -> int:
        detail = f"{kind}: {err}"
        if not self.args.public_log:
            print(detail, file=sys.stderr)
            return code
        kept = code in _SAVED and self._keep(detail)
        where = "saved in the private publication data (runs/)" if kept else \
            "withheld from this public log; see the private publication data, or run the same command privately"
        print(f"{self._line(kind, sorted(set(_TXN_ID.findall(str(err)))))} (details {where})", file=sys.stderr)
        return code

    def success(self, out) -> int:
        if not self.args.public_log:
            print(f"{out.status}: {out.message}")
            for note in out.notices:
                print(f"  notice: {note}")
            return 0
        txns = [out.txn] if getattr(out, "txn", None) else sorted(set(_TXN_ID.findall(out.message)))
        print(self._line(out.status, txns))
        if out.notices:
            kept = self._keep("\n".join(["notices:", *out.notices]))
            print(f"  {len(out.notices)} notice(s) {'saved in the private publication data (runs/)' if kept else 'withheld'}")
        return 0


def _public_status(status: dict) -> dict:
    """status without the hold's free-text reason."""
    reg = status.get("registry")
    if reg and reg.get("hold"):
        reg = dict(reg, hold={k: v for k, v in reg["hold"].items() if k != "reason"})
        status = dict(status, registry=reg)
    return status


def run(args) -> int:
    problem = _apply_target(args)
    if problem:
        print(f"error: {problem}", file=sys.stderr)
        return 4
    try:
        policy = load_policy(args.policy)
    except PolicyError as err:
        print(f"error: {err}", file=sys.stderr)
        return 4
    for flag, used in (("--no-network-checks", args.no_network_checks), ("--now", args.now), ("--drill", args.drill)):
        if used and args.mode != "test":
            print(f"error: {flag} is for --mode test only", file=sys.stderr)
            return 4
    checks = Checks() if args.no_network_checks else Checks(http_link_checker(), mock_fit_checker())
    clock = (lambda: dt.datetime.fromisoformat(args.now)) if args.now else (lambda: dt.datetime.now(MANILA))
    assets = {f.name: f.read_bytes() for f in sorted(FONTS.iterdir()) if f.is_file()} if FONTS.is_dir() else {}
    try:
        store, served = _store(args.store, args.base_url, fixed=bool(args.target))
    except ValueError as err:
        print(f"error: {err}", file=sys.stderr)
        return 4
    committer = git_committer(args.pubdata) if args.git else None
    if args.drill == "unsaved-record":
        committer = failing_once(committer)
    elif args.drill:
        store = DrillStore(store, args.drill)
    pub = Publisher(store, PubData(args.pubdata, committer),
                    StageInputs(args.dataset, args.affinity, args.base_url, _generator()), policy, checks, clock,
                    mode=args.mode, assets=assets, served=served)
    week = dt.date.fromisoformat(args.week) if getattr(args, "week", None) else None
    report = _Report(args, pub.pubdata)
    try:
        if args.op == "status":
            print(json.dumps(_public_status(pub.status()) if args.public_log else pub.status(), indent=2))
            return 0
        out = {
            "reconcile": lambda: pub.reconcile(),
            "stage-next": pub.stage_next,
            "stage": lambda: pub.stage(week),
            "promote": pub.promote,
            "launch": lambda: pub.launch(args.first_publication),
            "correct": lambda: pub.correct(week, args.expect_rev, args.reason),
            "rollback": lambda: pub.rollback(week, args.rev, args.reason),
            "restore": lambda: pub.restore(week, args.rev),
            "resume": pub.resume,
            "abandon": lambda: pub.abandon(args.txn),
        }[args.op]()
    except NotReady as err:
        return report.failure(4, "not ready", err)
    except StageRefused as err:
        return report.failure(7, "stage refused", err)
    except Refused as err:
        return report.failure(6, "refused", err)
    except PublishedUnrecorded as err:
        return report.failure(5, "published, record pending", err)
    except PublishedUnverified as err:
        return report.failure(8, "published, not yet served", err)
    except (NeedsHuman, CorruptRegistry, CorruptState) as err:
        return report.failure(3, "needs a human", err)
    except Aborted as err:
        return report.failure(2, "aborted", err)
    except Exception as err:  # noqa: BLE001
        if not args.public_log:
            raise
        return report.failure(1, f"failed unexpectedly ({type(err).__name__})", err)
    if out is None:
        print("nothing pending")
        return 0
    return report.success(out)
