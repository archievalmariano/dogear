"""The publication transactions (docs/PUBLISHING.md §4, §6, §12).

Every operation starts by reconciling: settling any transaction a previous run
left pending, and re-verifying served output a previous run could not
confirm. Publishing operations then:

1. upload immutable artifacts and verify them (unreachable until registered);
2. record the pending transaction durably;
3. immediately before every registry-write attempt (retries included), re-run
   every precondition: the Manila week where it matters, and the current
   eligibility of every record the change makes visible. Then write the
   registry conditionally on the ETag it was planned from, and resolve an
   unconfirmed write by reading storage back;
4. record the transaction in history exactly once;
5. verify what the host serves. Until it matches, the run is not a success and
   the verification stays recorded for the next run, which re-reads but never
   rewrites the registry. The history line keeps its own copy of the targets,
   and every committed transaction is either outstanding with exactly those
   targets or recorded as settled; anything else stops for a human.

A failure before 3 publishes nothing. After 3, the pending record or the
unverified record lets a later run finish the job from storage, never by
guessing. When storage shows a state the publisher cannot explain, it stops
for a human instead of recording anything it cannot prove.
"""

from __future__ import annotations

import datetime as dt
import json
import posixpath
import re
from dataclasses import dataclass, field
from typing import Callable, Optional

from ..week import MANILA, IssueWeek, week_of, week_starting
from .. import synthetic
from ..quiet import QUIET_SCHEMA_VERSION, QuietSource
from . import firmware, quiet_history
from .edition import revision_problems
from . import eligibility, routes
from . import registry as regmod
from .policy import PublicationPolicy
from .pubdata import CorruptState, PubData, SaveFailed, targets_problem
from .registry import REGISTRY_KEY, NoOp, Refused
from .staging import Checks, Staged, StageInputs, WeekHeld, provenance_bytes, verify_staged
from .staging import stage as stage_week
from .store import PreconditionFailed, StoreError

ROLLOVER = dt.time(6, 0)  # Monday, Asia/Manila

Guard = Callable[[], Optional[str]]


class UnguardedRegistryWrite(Exception):
    """A registry write that did not come from _transact immediately after every
    guard passed for exactly those bytes. A programming error, never retried."""


def is_registry_key(key: object) -> bool:
    """The registry's key in any spelling (leading slash, ./, case)."""
    return posixpath.normpath(str(key).strip().lstrip("/")).lower() == REGISTRY_KEY.lower()


_GATE_API = frozenset({"get", "put", "allow_once"})


class RegistryGate:
    """The publisher's only handle on storage (the registry-write boundary).

    Reads pass through. A write whose key is the registry's, however it is
    spelled or computed, is refused unless `_transact` has just authorised
    exactly those bytes for exactly one write, after its guards (the
    quiet-presentation guard first) passed.

    Only get, put and allow_once are reachable: every other attribute lookup is
    refused (the raw store's slot by any name, __dict__, __class__, __slots__),
    and attributes cannot be set or deleted. Reflection that steps around this
    (object.__getattribute__, gc, closures) is refused by the structural tests,
    which allow no such construct outside this class."""

    __slots__ = ("_store", "_allowed")

    def __init__(self, store) -> None:
        object.__setattr__(self, "_store", store)
        object.__setattr__(self, "_allowed", None)

    def __getattribute__(self, name: str):
        if name not in _GATE_API:
            raise AttributeError(f"RegistryGate exposes only get, put and allow_once; {name!r} is refused")
        return object.__getattribute__(self, name)

    def __setattr__(self, name: str, value) -> None:
        raise AttributeError("RegistryGate cannot be changed")

    def __delattr__(self, name: str) -> None:
        raise AttributeError("RegistryGate cannot be changed")

    def get(self, key: str):
        return object.__getattribute__(self, "_store").get(key)

    def allow_once(self, registry_sha: str) -> None:
        object.__setattr__(self, "_allowed", registry_sha)

    def put(self, key: str, data: bytes, **kwargs):
        if is_registry_key(key):
            allowed = object.__getattribute__(self, "_allowed")
            object.__setattr__(self, "_allowed", None)  # one write per authorisation
            if key != REGISTRY_KEY or allowed is None or allowed != regmod.sha256(data):
                raise UnguardedRegistryWrite(f"refused a registry write ({key!r}) that no guarded transaction "
                                             "authorised for these bytes")
        return object.__getattribute__(self, "_store").put(key, data, **kwargs)


class Aborted(Exception):
    """This run published nothing new; state is consistent (any pending record is resolvable)."""


class NeedsHuman(Exception):
    """Storage is in a state the publisher will not interpret on its own."""


class NotReady(Exception):
    """Production configuration incomplete: an editorial policy or a check is unset."""


class PublishedUnverified(Exception):
    """The registry was switched, but the public host does not yet serve it; retried each run."""


class PublishedUnrecorded(Exception):
    """The registry was switched but history could not be saved; the next run records it."""


@dataclass
class Outcome:
    status: str  # published | noop | held | staged | reconciled
    message: str
    txn: Optional[str] = None
    notices: list = field(default_factory=list)
    # A hold that needs action (exit 9): its public reason code and week. None for
    # every other result, including a manual stage's or promote's own "held".
    code: Optional[str] = None
    week: Optional[str] = None


# Public reason codes (the only hold detail a public log carries; PUBLISHING §4b).
AUTO_HOLD_CODES = ("AUTO_NO_PROOF", "AUTO_PROOF_CHANGED", "AUTO_PROOF_LATE")
STAGE_CODES = ("STAGE_WRONG_WEEK", "STAGE_NOT_SAVED", "STAGE_INVALID", "STAGE_WEEK_HELD")
HOLD_CODES = AUTO_HOLD_CODES + ("HELD",)


# A persisted proof that is there but wrongly shaped fails with these while it is read
# or verified (with ValueError for malformed JSON): unattended, each is a changed proof.
_UNREADABLE_PROOF = (TypeError, KeyError, AttributeError, IndexError)


class HoldNotProven(Exception):
    """A transaction recorded as a hold whose registries are not hold-only: never given hold semantics."""


_PAGE_FONT = re.compile(r"url\('\.\./fonts/([A-Za-z0-9-]+\.woff2)'\)")


def rollover_week(now: dt.datetime) -> Optional[IssueWeek]:
    """The week to publish at ``now``, or None before Monday 06:00 Manila."""
    local = now.astimezone(MANILA)
    week = week_of(local.date())
    if local.date() == week.start and local.time() < ROLLOVER:
        return None
    return week


class Publisher:
    def __init__(self, store, pubdata: PubData, inputs: StageInputs, policy: PublicationPolicy,
                 checks: Checks = Checks(), clock: Optional[Callable[[], dt.datetime]] = None,
                 mode: str = "production", assets: Optional[dict] = None,
                 served: Optional[Callable[[str], tuple]] = None, attempts: int = 3,
                 separation: Optional[str] = None, quiet_supported: Optional[bool] = None) -> None:
        if mode not in ("production", "test"):
            raise ValueError("mode must be production or test")
        if separation not in (None, "staging", "production") or (mode == "production" and separation == "staging"):
            raise ValueError("separation must be staging or production, and production mode is production")
        if mode == "production" and quiet_supported is not None:
            raise ValueError("quiet_supported is for test mode only: production reads the synced firmware contract")
        # Which data this target may show (dogear/synthetic.py): production mode is always
        # "production" (nothing synthetic); the staging target is "staging" (only synthetic).
        self.separation = "production" if mode == "production" else separation
        # The raw store is held only inside the gate: every registry write goes through _transact.
        self.store = RegistryGate(store)
        self.pubdata, self.inputs, self.policy, self.checks = pubdata, inputs, policy, checks
        self.clock = clock or (lambda: dt.datetime.now(MANILA))
        self.mode, self.assets, self.attempts = mode, assets or {}, attempts
        self.served = served or (lambda path: routes.fetch(self.store, path))
        # Test mode only: whether DOGEAR firmware presents quiet issues (None: read
        # the firmware contract, as production always does).
        self.quiet_supported = quiet_supported
        self._presentation_checked: Optional[str] = None  # the registry bytes the last guard pass allowed

    # Readiness and state.

    def _ready(self) -> None:
        if self.mode != "production":
            return
        missing = [f"policy.{name}" for name in self.policy.unset()]
        if self.checks.link_checker is None:
            missing.append("link checker")
        if self.checks.fit_checker is None:
            missing.append("fit checker")
        if missing:
            raise NotReady("production publishing refused; unset: " + ", ".join(missing))
        if self.policy.empty_week == "quiet-week":
            # The switch is the owner's, after presentation firmware is released and
            # verified on devices; this only refuses it while the firmware cannot.
            capable = self._quiet_capability()
            if not capable.ok:
                raise NotReady("production publishing refused; quiet-week needs firmware that presents quiet "
                               f"issues ({capable.reason})")

    def _quiet_capability(self) -> firmware.Capability:
        """Whether released firmware presents quiet issues: the synced firmware
        contract and its verification record (local files, no network)."""
        if self.quiet_supported is not None:  # test mode only (refused in production)
            return firmware.Capability(self.quiet_supported, None if self.quiet_supported else "test override")
        return firmware.capability()

    def _read_registry(self) -> tuple:
        try:
            obj = self.store.get(REGISTRY_KEY)
        except StoreError as err:
            raise Aborted(f"cannot read the registry: {err}") from None
        return (regmod.parse(obj.data) if obj else None), obj

    def _dataset_text(self) -> str:
        return self.inputs.dataset.read_text(encoding="utf-8")

    def _history(self, reg: Optional[dict], before: dt.date) -> list:
        """Published weeks before ``before``: [[monday, issueSha256, digest record ids]]."""
        if reg is None:
            return []
        committed = {h["txn"]: h for h in self.pubdata.history() if h["status"] == "committed"}
        out = []
        for week in sorted(reg["weeks"]):
            if dt.date.fromisoformat(week) >= before:
                continue
            rev = regmod.active_revision(reg, week)
            line = committed.get(rev["txn"])
            if line is None or line.get("issueSha256") != rev["issueSha256"]:
                raise NeedsHuman(f"history has no committed record of {week} revision {rev['rev']}")
            out.append([week, rev["issueSha256"], list(line["recordIds"])])
        return out

    def status(self) -> dict:
        reg, _obj = self._read_registry()
        pending = self.pubdata.read_pending()
        unverified = self._outstanding()
        summary = None if reg is None else {
            "current": reg["current"], "hold": reg["hold"], "txn": reg["txn"],
            "weeks": {w: {"active": e["active"], "revisions": len(e["revisions"])} for w, e in sorted(reg["weeks"].items())}}
        out = {"registry": summary, "pending": pending and pending["txn"],
               "unverified": [e["txn"] for e in unverified]}
        if self.mode == "production":
            capable = self._quiet_capability()
            out["firmware"] = {"quietCapable": capable.ok, "reason": capable.reason,
                               "quietShownWithoutCapability": self._quiet_shown_without_capability(reg)}
        return out

    def reset_preflight(self, expect_current: str) -> list:
        """Read-only (PUBLISHING.md §20g): why the live state is NOT quiescent and
        reconciled enough to archive. [] means PASS. It reconciles nothing, verifies
        nothing and saves nothing: unresolved state is reported, never moved aside."""
        problems = []
        try:
            pending = self.pubdata.read_pending()
            if pending is not None:
                problems.append(f"transaction {pending['txn']} is pending: reconcile it first")
            if self.pubdata.read_unverified():
                problems.append("unverified.json is not empty: served output awaits verification")
            outstanding = self._outstanding()
            if outstanding:
                problems.append(f"{len(outstanding)} committed transaction(s) are not verified")
            history = self.pubdata.history()
        except (CorruptState, KeyError, TypeError) as err:
            return problems + [f"the publication data is inconsistent: {err}"]
        committed = [h for h in history if h["status"] == "committed"]
        if not committed:
            problems.append("no committed history: there is no live state to archive")
        try:
            reg, obj = self._read_registry()
        except (Aborted, regmod.CorruptRegistry) as err:
            return problems + [f"the live registry cannot be read: {err}"]
        if reg is None:
            return problems + ["the store has no registry"]
        mirror = self.pubdata.root / "publication.json"
        if not mirror.is_file() or regmod.sha256(mirror.read_bytes()) != regmod.sha256(obj.data):
            problems.append("the publication-data mirror differs from the live registry")
        if reg["current"] != expect_current:
            problems.append(f"the live registry's current week is {reg['current']}, not {expect_current}")
        if reg["hold"] is not None:
            problems.append("the live registry holds publication")
        if committed and reg["txn"] != committed[-1]["txn"]:
            problems.append(f"the live registry's transaction {reg['txn']} is not the last committed one "
                            f"({committed[-1]['txn']}): an unresolved write outcome")
        try:
            self._history(reg, dt.date.max)  # every visible revision has its committed record
        except NeedsHuman as err:
            problems.append(str(err))
        lines = {h["txn"]: h for h in quiet_history.content_lines(self.pubdata)}
        for week in sorted(reg["weeks"]):  # each active revision's date/edition invariant, bound
            rev = regmod.active_revision(reg, week)
            line = lines.get(rev["txn"])
            if line is not None:
                problems += quiet_history.line_problems(line, reg, self.pubdata, self.store)
        shown = self._quiet_shown_without_capability(reg)
        if shown:
            problems.append(shown)
        return problems + self._hosted_problems(reg)

    def _fetch(self, path: str) -> tuple:
        """(status, body, None) from the host, or (None, None, why) if it could not be read."""
        try:
            status, body = self.served(path)
        except Exception as err:  # noqa: BLE001 - any failure to read the host is a HOLD
            return None, None, f"the host could not be read ({type(err).__name__})"
        if not isinstance(status, int) or not isinstance(body, (bytes, bytearray)):
            return None, None, "the host gave a malformed response"
        return status, bytes(body), None

    def _hosted_problems(self, reg: dict) -> list:
        """Read-only: everything the live registry makes visible, fetched through the host
        and compared byte for byte. Verification history is not enough: an object can
        disappear after it was verified.

        * the manifest (/current.json), and each week's ACTIVE page and device issue,
          against the hashes the registry records (verification_targets with no
          earlier registry: every week, not only changed ones);
        * every font those pages load (../fonts/NAME.woff2), against the publisher's
          own copy of that font (the bytes it uploads; the registry records none)."""
        problems, fonts = [], set()
        for t in verification_targets(None, reg):
            where = f"{t['kind']} {t['path']}"
            status, body, why = self._fetch(t["path"])
            if why:
                problems.append(f"{where}: {why}")
            elif status != 200:
                problems.append(f"{where}: the host answered {status}")
            elif regmod.sha256(body) != t["sha256"]:
                problems.append(f"{where}: the served bytes do not match the registry")
            elif t["kind"] == "page":
                fonts.update(_PAGE_FONT.findall(body.decode("utf-8", "replace")))
        for name in sorted(fonts):
            where = f"font /fonts/{name}"
            if name not in self.assets:
                problems.append(f"{where}: the publisher has no copy to compare it with")
                continue
            status, body, why = self._fetch(f"/fonts/{name}")
            if why:
                problems.append(f"{where}: {why}")
            elif status != 200:
                problems.append(f"{where}: the host answered {status}")
            elif body != self.assets[name]:
                problems.append(f"{where}: the served bytes differ from the publisher's copy")
        return problems

    def attest_subjects(self, txn: str, mapping: object, source: Optional[bytes]) -> Outcome:
        """Append one reviewed attestation of a LEGACY content line's subject identities
        (PUBLISHING.md §21). Local and explicit only. Refuses unless the line validates
        and has no subjects, nothing attests it yet, ``source`` is the publication's
        original frozen dataset (bytes hashing to the registry revision's
        datasetSha256), and the mapping names exactly its digest records at the
        fingerprints that revision published, with identity presence matching each
        original record. It infers nothing: the mapping is the reviewer's claim, and
        the evidence it is bound to is kept immutably under evidence/<txn>/."""
        reg, _obj = self._read_registry()
        lines = quiet_history.validated_lines(reg, self.pubdata, self.store)
        line = next((h for h in lines if h["txn"] == txn), None)
        if line is None:
            raise Refused(f"{txn} is not a committed content line")
        if any(isinstance(a, dict) and a.get("txn") == txn for a in self.pubdata.read_attestations()):
            raise Refused(f"{txn} is already attested")
        rev = reg["weeks"][line["week"]]["revisions"][line["rev"]]
        if source is None or regmod.sha256(source) != rev.get("datasetSha256"):
            raise Refused("the original frozen dataset is unavailable or does not match the registry's hash")
        if not isinstance(mapping, list):
            raise Refused("the mapping must be a list of {recordId, personId, workId}")
        prov = json.loads(self.pubdata.read_published_provenance(line["week"], line["rev"]))
        pinned = {r["recordId"]: r["fingerprint"] for r in prov["records"] if r["section"] == "digest"}
        entry = {"txn": txn, "sourceDatasetSha256": rev["datasetSha256"], "attestedAt": self.clock().isoformat(),
                 "by": "operator",
                 "subjects": [{"recordId": m.get("recordId"), "fingerprint": pinned.get(m.get("recordId")),
                               "personId": m.get("personId"), "workId": m.get("workId")}
                              for m in mapping if isinstance(m, dict)]}
        created = self.pubdata.read_evidence_dataset(txn) is None
        self.pubdata.write_evidence_dataset(txn, source)
        problems = quiet_history.attestation_problems(entry, line, reg, self.pubdata)
        if problems:
            if created:  # leave nothing behind from a refused attestation
                self.pubdata.evidence_dataset_path(txn).unlink()
            raise Refused("attestation refused: " + "; ".join(problems))
        self.pubdata.append_attestation(entry)
        try:
            self.pubdata.save(f"attest subjects {txn}")
        except SaveFailed as err:
            raise Aborted(f"the attestation was not saved ({err}); nothing changed in the remote") from None
        return Outcome("attested", f"{txn}: {len(entry['subjects'])} digest record(s) attested", txn)

    # Reconciliation.

    def reconcile(self, require_verified: bool = True, detect: bool = False) -> Optional[Outcome]:
        """Settle what earlier runs left: a pending transaction, then unverified serving.
        With `detect` (the reconcile operation itself), then also stop for a human if
        production currently shows a quiet issue that the firmware contract does not
        authorise. It never writes the registry."""
        messages = []
        pending = self.pubdata.read_pending()
        if pending is not None:
            messages.append(self._settle_pending(pending))
        if self._outstanding():
            failures, settled = self._verify_outstanding()
            if settled:
                messages.append("served output verified for " + ", ".join(settled))
            if failures:
                if require_verified:
                    raise PublishedUnverified("the host does not yet serve: " + "; ".join(failures))
                messages.append("still unverified: " + "; ".join(failures))
        if detect:
            shown = self._quiet_shown_without_capability()
            if shown:
                raise NeedsHuman(shown + ("; " + "; ".join(messages) if messages else ""))
        return Outcome("reconciled", "; ".join(messages)) if messages else None

    def _settle_pending(self, pending: dict) -> str:
        txn = pending["txn"]
        if regmod.sha256(regmod.dumps(pending["intendedRegistry"])) != pending["intendedRegistrySha"]:
            raise NeedsHuman(f"pending {txn} is inconsistent with itself")
        try:
            obj = self.store.get(REGISTRY_KEY)
        except StoreError as err:
            raise Aborted(f"cannot read the registry to reconcile {txn}: {err}") from None
        if obj is not None and regmod.sha256(obj.data) == pending["intendedRegistrySha"]:
            self._finalize(pending)
            return f"recorded {txn}, which had been published"
        if (obj is None and pending["expectedEtag"] is None) or (obj is not None and obj.etag == pending["expectedEtag"]):
            self._abandon(pending, "its registry write never applied")
            return f"closed {txn}: never applied"
        if obj is not None and self._carries(obj, pending):
            self._finalize(pending, mirror=obj.data)
            return f"recorded {txn}: it was published, then changed by a later transaction"
        raise NeedsHuman(f"pending {txn}: the registry is neither the state it expected nor the one it intended, "
                         f"and shows no trace of it; inspect, then `publish abandon {txn}`")

    @staticmethod
    def _carries(obj, pending: dict) -> bool:
        """Does this registry show that the pending transaction was applied? Only a
        transaction that added a revision leaves such a trace."""
        try:
            reg = regmod.parse(obj.data)
        except regmod.CorruptRegistry:
            return False
        return any(rev["txn"] == pending["txn"] for w in reg["weeks"].values() for rev in w["revisions"])

    def abandon(self, txn: str) -> Outcome:
        """Operator, after inspection: close a pending transaction that will not apply."""
        pending = self.pubdata.read_pending()
        if pending is None or pending["txn"] != txn:
            raise Refused(f"no pending transaction {txn}")
        reg, obj = self._read_registry()
        if obj is not None and (regmod.sha256(obj.data) == pending["intendedRegistrySha"] or self._carries(obj, pending)):
            self._finalize(pending, mirror=obj.data)
            return Outcome("reconciled", f"{txn} was published; recorded it", txn)
        self._abandon(pending, "closed by the operator after inspection")
        return Outcome("reconciled", f"closed {txn}", txn)

    def _save_quietly(self, message: str) -> Optional[str]:
        """Save bookkeeping whose loss is harmless (the next run redoes it)."""
        try:
            self.pubdata.save(message)
        except SaveFailed as err:
            return f"{message}: not saved ({err}); the next run repeats it"
        return None

    def _abandon(self, pending: dict, why: str) -> None:
        self.pubdata.append_history_once({"txn": pending["txn"], "op": pending["op"], "week": pending["week"],
                                          "status": "aborted", "reason": why})
        self.pubdata.clear_pending()
        try:
            self.pubdata.save(f"abort {pending['txn']}")
        except SaveFailed as err:
            raise Aborted(f"{pending['txn']} never applied, but recording that failed ({err}); the next run will") from None

    def _finalize(self, pending: dict, mirror: Optional[bytes] = None) -> None:
        """Record a transaction that is in the registry, once, and queue its served-output check."""
        reg = pending["intendedRegistry"]
        problem = _expected_targets_problem(pending)
        if problem:
            raise NeedsHuman(f"pending {pending['txn']}: its verification targets cannot be trusted ({problem}); "
                             "nothing recorded")
        self.pubdata.append_history_once(dict(pending["history"], status="committed", verify=pending["verify"]))
        self.pubdata.write_registry_mirror(mirror if mirror is not None else regmod.dumps(reg))
        if pending.get("provenance") is not None:
            rev = regmod.active_revision(reg, pending["week"])["rev"]
            self.pubdata.write_published_provenance(pending["week"], rev, provenance_bytes(pending["provenance"]))
        if pending["verify"] == []:
            # A proven hold (checked above): nothing to serve, so settled now. It never
            # enters unverified.json, and that file is not read or rewritten here.
            self.pubdata.append_verified_once(pending["txn"], self.clock().isoformat())
        else:
            entries = [e for e in (self.pubdata.read_unverified() or []) if e["txn"] != pending["txn"]]
            if not any(v["txn"] == pending["txn"] for v in self.pubdata.verified()):
                entries.append({"txn": pending["txn"], "since": self.clock().isoformat(), "targets": pending["verify"]})
            self.pubdata.write_unverified(entries)
        self.pubdata.clear_pending()
        try:
            self.pubdata.save(f"publish {pending['txn']}")
        except SaveFailed as err:
            raise PublishedUnrecorded(f"{pending['txn']} is published; saving its record failed ({err}); "
                                      "the next run records it") from None

    # Guards: re-run immediately before every registry-write attempt.

    def _week_guard(self, week: IssueWeek) -> Guard:
        return lambda: None if rollover_week(self.clock()) == week else "the Manila week changed before the write"

    def _separation_problem(self, provenance: dict, issue: dict, dataset_text: str) -> Optional[str]:
        """The target's synthetic/real separation, checked on the revision itself."""
        if self.separation is None:
            return None
        return synthetic.revision_problem(self.separation, provenance, issue, dataset_text)

    def _eligibility_guard(self, provenance: dict, issue_bytes: bytes, line: Optional[dict] = None,
                           monday: Optional[dt.date] = None) -> Guard:
        """Re-run before every registry-write attempt (launch, promote, correct, and each
        retry): eligibility, separation, and the date/edition invariant of the proposed
        revision against its proposed history line, all read afresh each time."""
        def guard() -> Optional[str]:
            try:
                text, issue = self._dataset_text(), json.loads(issue_bytes)
                problems = eligibility.check(provenance, text, issue)
            except (OSError, ValueError) as err:
                return f"cannot re-check eligibility: {err}"
            if problems:
                return "eligibility changed during publication: " + "; ".join(problems)
            separated = self._separation_problem(provenance, issue, text)
            if separated:
                return separated
            if monday is not None:
                problems = revision_problems(monday, issue, provenance, line)
                if line is not None and line.get("issueSha256") != regmod.sha256(issue_bytes):
                    problems.append("the proposed history line names other issue bytes")
                if problems:
                    return "the revision breaks the date/edition invariant: " + "; ".join(problems)
            return None
        return guard

    def _revision_is_quiet(self, week: str, rev: dict, fresh: Optional[dict], objects: dict) -> tuple:
        """(quiet, problem) for one revision, from its provenance's edition and its
        issue's schema, both read and hash-checked; they must agree. `fresh` and
        `objects` are the provenance and uploads of the revision this write adds."""
        if fresh is not None and rev.get("txn") == fresh.get("txn"):
            prov = provenance_bytes(fresh["provenance"])
        else:
            prov = self.pubdata.read_published_provenance(week, rev["rev"])
        if prov is None or regmod.sha256(prov) != rev["provenanceSha256"]:
            return False, f"{week} revision {rev['rev']}: no matching provenance to tell its edition"
        issue = objects.get(rev["issueKey"])
        if issue is None:
            try:
                obj = self.store.get(rev["issueKey"])
            except StoreError as err:
                return False, f"{week} revision {rev['rev']}: cannot read its issue ({err})"
            issue = obj.data if obj is not None else None
        if issue is None or regmod.sha256(issue) != rev["issueSha256"]:
            return False, f"{week} revision {rev['rev']}: its issue object is missing or altered"
        try:
            edition = json.loads(prov).get("edition")
            schema = json.loads(issue).get("schemaVersion")
        except (ValueError, AttributeError) as err:
            return False, f"{week} revision {rev['rev']}: unreadable ({err})"
        by_schema = isinstance(schema, int) and not isinstance(schema, bool) and schema >= QUIET_SCHEMA_VERSION
        if (edition == "quiet") != by_schema:
            return False, f"{week} revision {rev['rev']}: its provenance and issue disagree on whether it is quiet"
        return by_schema, None

    def _quiet_presentation_guard(self, before: Optional[dict], after: dict, fresh: Optional[dict] = None,
                                  uploads: list = ()) -> Guard:
        """Production: a registry write may make a quiet (schema-3) revision visible
        or current only while released firmware presents quiet issues (the synced
        firmware contract), whatever the policy says. What a write shows: the new
        current week's active revision and every week whose active revision changes.
        Moving away from a quiet revision is always allowed. A hold-only write (a hold
        set, nothing else: _hold_only_guard, re-checked here) shows nothing new, so it
        is authorised without reading any issue: a hold must work when they are gone."""
        shown = sorted(shown_revisions(before, after))
        objects = {key: data for key, data, _ctype in uploads}
        intended = regmod.sha256(regmod.dumps(after))

        def guard() -> Optional[str]:
            self._presentation_checked = None
            if _hold_only_guard(before, after) is None and regmod.sha256(regmod.dumps(after)) == intended:
                self._presentation_checked = intended
                return None
            if self.mode == "production":
                quiet = []
                for week, rev_no in shown:
                    is_quiet, problem = self._revision_is_quiet(week, after["weeks"][week]["revisions"][rev_no],
                                                                fresh, objects)
                    if problem:
                        return problem
                    if is_quiet:
                        quiet.append(f"{week} revision {rev_no}")
                if quiet:
                    capable = self._quiet_capability()
                    if not capable.ok:
                        return (f"this would show a quiet issue ({', '.join(quiet)}), but released firmware is not "
                                f"verified to present quiet issues: {capable.reason}")
            self._presentation_checked = intended
            return None
        return guard

    def _quiet_shown_without_capability(self, reg: Optional[dict] = None) -> Optional[str]:
        """Production: a description if the live current revision is quiet (or cannot
        be told) while the firmware contract does not authorise quiet issues."""
        if self.mode != "production":
            return None
        try:
            if reg is None:
                reg, _obj = self._read_registry()
        except (Aborted, regmod.CorruptRegistry) as err:
            return f"cannot read the registry to check quiet presentation: {err}"
        if reg is None:
            return None
        capable = self._quiet_capability()
        if capable.ok:
            return None
        week = reg["current"]
        rev = regmod.active_revision(reg, week)
        is_quiet, problem = self._revision_is_quiet(week, rev, None, {})
        if problem:
            return f"cannot tell whether the current issue is quiet ({problem}) and quiet issues are not authorised"
        if is_quiet:
            return (f"production shows a quiet issue ({week} revision {rev['rev']}) that released firmware is not "
                    f"verified to present: {capable.reason}; roll back to a regular issue")
        return None

    def _visible_revisions_guard(self, before: dict, after: dict) -> Guard:
        """For rollback and restore: every revision the change makes visible must still be publishable."""
        shown = [(w, e["active"]) for w, e in after["weeks"].items()
                 if before["weeks"][w]["active"] != e["active"]]
        # The current revision always: resume changes neither `current` nor an active
        # number, yet makes the current revision visible again for automatic publication.
        shown.append((after["current"], after["weeks"][after["current"]]["active"]))

        def guard() -> Optional[str]:
            for week, rev_no in sorted(set(shown)):
                rev = after["weeks"][week]["revisions"][rev_no]
                prov = self.pubdata.read_published_provenance(week, rev_no)
                if prov is None or regmod.sha256(prov) != rev["provenanceSha256"]:
                    return f"{week} revision {rev_no}: no matching provenance on record"
                try:
                    issue = self.store.get(rev["issueKey"])
                except StoreError as err:
                    return f"{week} revision {rev_no}: cannot read its issue ({err})"
                if issue is None or regmod.sha256(issue.data) != rev["issueSha256"]:
                    return f"{week} revision {rev_no}: its issue object is missing or altered"
                try:
                    text, prov_obj, issue_obj = self._dataset_text(), json.loads(prov), json.loads(issue.data)
                    problems = eligibility.check(prov_obj, text, issue_obj)
                except (OSError, ValueError) as err:
                    return f"cannot re-check eligibility: {err}"
                if problems:
                    return f"{week} revision {rev_no} is no longer publishable: " + "; ".join(problems)
                # Checked on the stored revision itself, not only by rebuilding it: an old
                # revision can never cross between the staging and production hosts.
                separated = self._separation_problem(prov_obj, issue_obj, text)
                if separated:
                    return f"{week} revision {rev_no} may not be shown here: {separated}"
                # The stored revision's date/edition invariant, bound to its committed
                # history line, provenance, issue and web bytes.
                lines = [h for h in quiet_history.content_lines(self.pubdata) if h["txn"] == rev["txn"]]
                if len(lines) != 1:
                    return f"{week} revision {rev_no}: no single committed history line for it"
                bound = quiet_history.line_problems(lines[0], after, self.pubdata, self.store)
                if bound:
                    return f"{week} revision {rev_no} may not be shown: " + "; ".join(bound)
            return None
        return guard

    @staticmethod
    def _all(*guards: Guard) -> Guard:
        def guard() -> Optional[str]:
            for g in guards:
                reason = g()
                if reason:
                    return reason
            return None
        return guard

    # The transaction.

    def _upload(self, key: str, data: bytes, content_type: str) -> None:
        for _ in range(self.attempts):
            try:
                current = self.store.get(key)
                if current is not None and current.data != data:
                    raise NeedsHuman(f"{key} already exists with different bytes")
                if current is None:
                    self.store.put(key, data, content_type=content_type)
                got = self.store.get(key)
            except StoreError:
                continue
            if got is not None and got.data == data:
                return
        raise Aborted(f"could not upload and verify {key}")

    def _transact(self, op: str, week: str, reg: Optional[dict], obj, new_reg: dict, uploads: list,
                  guard: Guard, history: dict, provenance: Optional[dict]) -> Outcome:
        txn = new_reg["txn"]
        new_bytes = regmod.dumps(new_reg)
        intended = regmod.sha256(new_bytes)
        try:  # before anything is uploaded or recorded: a hold must prove itself hold-only
            verify = expected_verify(op, reg, new_reg)
        except HoldNotProven as err:
            raise UnguardedRegistryWrite(f"{txn}: a hold that is not hold-only ({err}); nothing recorded") from None
        # Every registry write, whatever the operation: the firmware gate on quiet issues.
        fresh = {"txn": txn, "provenance": provenance} if provenance is not None else None
        guard = self._all(self._quiet_presentation_guard(reg, new_reg, fresh, uploads), guard)
        for key, data, ctype in uploads:
            try:
                self._upload(key, data, ctype)
            except ValueError as err:  # a malformed object key: configuration, not storage
                raise Aborted(f"cannot upload: {err}") from None
        expected = obj.etag if obj else None
        pending = {"txn": txn, "op": op, "week": week, "expectedEtag": expected,
                   "expectedRegistrySha": regmod.sha256(obj.data) if obj else None,
                   "expectedRegistry": obj.data.decode("utf-8") if obj else None,
                   "intendedRegistrySha": intended, "intendedRegistry": new_reg,
                   "history": history, "provenance": provenance, "createdAt": self.clock().isoformat(),
                   "verify": verify}
        self.pubdata.write_pending(pending)
        try:
            self.pubdata.save(f"pending {txn}")
        except SaveFailed as err:
            self.pubdata.clear_pending()
            raise Aborted(f"could not record the pending transaction ({err}); nothing was switched") from None

        uncertain = False  # an earlier attempt may have applied and could not be confirmed
        for _ in range(self.attempts):
            reason = guard()
            if reason:
                return self._stop(pending, reason, uncertain, intended, expected)
            if self._presentation_checked != intended:  # the guard just passed for exactly these bytes
                raise UnguardedRegistryWrite(f"{txn}: the quiet-presentation guard did not pass for this write")
            self.store.allow_once(intended)
            try:
                self.store.put(REGISTRY_KEY, new_bytes, content_type="application/json",
                               if_match=expected, if_none_match=expected is None)
            except PreconditionFailed:
                state = self._registry_state(intended, expected)
                if state == "ours":
                    break
                if uncertain or state == "unknown":
                    # This rejection says nothing about the earlier, unconfirmed attempt.
                    return self._unresolved(pending, "a conflicting write followed an unconfirmed attempt")
                self._abandon(pending, "another writer changed the registry (precondition failed)")
                raise Aborted(f"{txn}: the registry changed under us; nothing published by this run")
            except StoreError:
                pass  # in doubt: settled by reading the registry back, below
            # Published only when the live registry is affirmatively these bytes: a write
            # that reported success but did not land is not published.
            state = self._registry_state(intended, expected)
            if state == "ours":
                break
            if state == "other":
                return self._unresolved(pending, "the registry changed while our write was in doubt")
            uncertain = state == "unknown"  # "expected": definitely not applied; retry after the guards
        else:
            raise Aborted(f"{txn}: registry write not confirmed after {self.attempts} attempts; "
                          "pending kept for the next run")
        return self._committed(pending)

    def _committed(self, pending: dict) -> Outcome:
        """The live registry is affirmatively this transaction's (every path that
        confirms a write ends here, _stop's late confirmation included)."""
        if pending["verify"] == []:  # recorded as a hold: _finalize proves it before anything is written
            return self._committed_hold(pending)
        self._finalize(pending)
        failures, _settled = self._verify_outstanding()
        if failures:
            raise PublishedUnverified(f"{pending['txn']} is in the registry, but the host does not yet serve: "
                                      + "; ".join(failures) + "; each run re-checks until it does")
        return Outcome("published", f"{pending['op']} {pending['week']}: {pending['txn']}", pending["txn"])

    def _committed_hold(self, pending: dict) -> Outcome:
        """A committed hold's result is the hold (exit 9), whatever other transactions'
        served output shows: it settles nothing else and checks no host, so earlier
        outstanding entries stay exactly as they were for the next run to re-check."""
        self._finalize(pending)
        code = pending["history"].get("code") if pending["history"].get("code") in HOLD_CODES else "HELD"
        return Outcome("held", f"{code} {pending['week']}: held ({pending['txn']}); the previous issue stays current",
                       pending["txn"], code=code, week=pending["week"])

    def _registry_state(self, intended: str, expected: Optional[str]) -> str:
        try:
            cur = self.store.get(REGISTRY_KEY)
        except StoreError:
            return "unknown"
        if cur is not None and regmod.sha256(cur.data) == intended:
            return "ours"
        if (cur is None and expected is None) or (cur is not None and cur.etag == expected):
            return "expected"
        return "other"

    def _unresolved(self, pending: dict, why: str) -> Outcome:
        """The registry is someone else's now. Record ours only if it shows our trace."""
        try:
            obj = self.store.get(REGISTRY_KEY)
        except StoreError:
            obj = None
        if obj is not None and self._carries(obj, pending):
            self._finalize(pending, mirror=obj.data)
            raise NeedsHuman(f"{pending['txn']} was published and then changed by another writer ({why}); "
                             "recorded as published; check the current registry")
        raise NeedsHuman(f"{pending['txn']}: {why}; whether it applied cannot be shown, so it stays pending; "
                         f"inspect, then `publish abandon {pending['txn']}`")

    def _stop(self, pending: dict, reason: str, uncertain: bool, intended: str, expected: Optional[str]) -> Outcome:
        state = self._registry_state(intended, expected) if uncertain else "expected"
        if state == "ours":  # an earlier, unconfirmed attempt did apply, while still valid
            return self._committed(pending)
        if state == "expected":
            self._abandon(pending, reason)
            raise Aborted(f"{pending['txn']}: {reason}; nothing published")
        if state == "other":
            return self._unresolved(pending, reason)
        raise Aborted(f"{pending['txn']}: {reason}, and the registry cannot be confirmed; pending kept")

    def _outstanding(self) -> list:
        """The outstanding verification entries, checked against the independent record.

        Each committed history line keeps the targets its transaction must verify, and
        verified.jsonl the transactions settled. Every committed transaction must be
        outstanding with exactly its recorded targets, or settled; a missing, extra,
        or altered entry is CorruptState, never read as "nothing to verify"."""
        entries = self.pubdata.read_unverified() or []
        committed = {h["txn"]: h for h in self.pubdata.history() if h["status"] == "committed"}
        settled = {v["txn"] for v in self.pubdata.verified()}
        pending = self.pubdata.read_pending()
        in_flight = pending["txn"] if pending else None  # finalizing it writes its entry
        for txn, line in committed.items():
            problem = targets_problem(line.get("verify"), line.get("op"))
            if problem:
                raise CorruptState(f"history line {txn}: verification targets: {problem}")
        for txn in settled - set(committed):
            raise CorruptState(f"verified.jsonl settles {txn}, which history does not record as committed")
        listed = set()
        for e in entries:
            line = committed.get(e["txn"])
            if line is None:
                raise CorruptState(f"unverified.json lists {e['txn']}, which history does not record as committed")
            if line.get("op") == "hold":
                raise CorruptState(f"unverified.json lists {e['txn']}, a hold: a hold is never outstanding")
            if e["targets"] != line["verify"]:
                raise CorruptState(f"unverified.json targets for {e['txn']} differ from those its history line records")
            listed.add(e["txn"])
        for txn in sorted(set(committed) - listed - settled - {in_flight}):
            raise CorruptState(f"{txn} is committed but neither outstanding nor settled: its verification record is missing")
        # Listed and settled: a settle interrupted between its two writes. Settled wins.
        return [e for e in entries if e["txn"] not in settled]

    def _verify_outstanding(self) -> tuple:
        """Check every outstanding target against the host (read-only). Entries whose
        targets are all served, or superseded by a later registry change, are settled.
        Returns (failures, settled transaction ids)."""
        entries = self._outstanding()
        if not entries:
            return [], []
        try:
            reg, _ = self._read_registry()
        except (Aborted, regmod.CorruptRegistry) as err:
            return [f"cannot read the registry to verify: {err}"], []
        if reg is None:
            return ["no registry to verify against"], []
        failures, keep, settled = [], [], []
        for entry in entries:
            problems = [p for p in (self._check_target(reg, t) for t in entry["targets"]) if p]
            if problems:
                keep.append(entry)
                failures += [f"{entry['txn']}: {p}" for p in problems]
            else:
                settled.append(entry["txn"])
        if settled:
            at = self.clock().isoformat()
            for txn in settled:
                self.pubdata.append_verified_once(txn, at)
            self.pubdata.write_unverified(keep)
            self._save_quietly("verified " + ", ".join(settled))  # if lost, the next run re-verifies
        return failures, settled

    def _check_target(self, reg: dict, t: dict) -> Optional[str]:
        """None if served as expected or superseded; otherwise what is wrong."""
        if t["kind"] == "manifest" and regmod.sha256(regmod.manifest_bytes(reg)) != t["sha256"]:
            return None  # a later change replaced this manifest; its own entry checks the new one
        if t["kind"] == "page":
            week = t["week"]
            if week not in reg["weeks"] or regmod.active_revision(reg, week)["webSha256"] != t["sha256"]:
                return None  # a later correction or rollback replaced this page
        if t["kind"] == "issue":
            week = t["path"][len("/issues/dogear-"):][:10]  # the shape check binds the path to its week
            if week not in reg["weeks"] or regmod.active_revision(reg, week)["issueSha256"] != t["sha256"]:
                return None  # no longer the active revision: withdrawn from the host, by design
        try:
            status, body = self.served(t["path"])
        except Exception as err:  # verification never undoes a publication
            return f"{t['path']} unreachable: {err}"
        if status != 200 or regmod.sha256(body) != t["sha256"]:
            return f"{t['path']} served {status} with other content"
        return None

    # Staging.

    def _uploads(self, staged: Staged) -> list:
        out = [(f"fonts/{name}", data, "font/woff2" if name.endswith(".woff2") else "text/plain; charset=utf-8")
               for name, data in sorted(self.assets.items())]
        out.append((staged.web_key, staged.web_bytes, "text/html; charset=utf-8"))
        out.append((staged.issue_key, staged.issue_bytes, "application/json"))
        return out

    def _history_entry(self, op: str, week: str, rev: int, staged: Staged, published_at: str,
                       reason: Optional[str] = None) -> dict:
        by = {"digest": [], "also": []}
        for r in staged.provenance["records"]:
            by[r["section"]].append(r["recordId"])
        entry = {"txn": None, "op": op, "week": week, "rev": rev, "issueSha256": staged.issue_sha,
                 "webSha256": staged.web_sha, "recordIds": by["digest"], "alsoIds": by["also"],
                 "edition": staged.provenance.get("edition", "regular"), "publishedAt": published_at}
        if "subjects" in staged.provenance:  # stable identities, as the hash-pinned provenance records them
            entry["subjects"] = staged.provenance["subjects"]
        if reason:
            entry["reason"] = reason
        return entry

    def _write_staged(self, staged: Staged, notices: list) -> None:
        self.pubdata.write_staged(staged.week, staged.files())
        try:
            self.pubdata.save(f"stage {staged.week}")
        except SaveFailed as err:
            notices.append(f"staged copy not saved ({err}); the transaction carries its provenance")

    def _stage(self, week: IssueWeek, history: list, now: dt.datetime) -> Staged:
        try:
            return stage_week(week, self.inputs, self.policy, history, now, self.checks, self.mode)
        except WeekHeld as held:
            if not (held.empty and self.policy.empty_week == "quiet-week"):
                raise
        # An empty week under quiet-week: only now is history validated and read.
        return stage_week(week, self.inputs, self.policy, history, now, self.checks, self.mode,
                          quiet_source=self._quiet_source(week.start))

    def _quiet_source(self, monday: dt.date) -> QuietSource:
        """The quiet inputs for week ``monday``, from validated history only
        (CorruptState if any content line fails validation)."""
        reg, _obj = self._read_registry()
        lines = quiet_history.validated_lines(reg, self.pubdata, self.store)
        attested = quiet_history.attested_subjects(lines, reg, self.pubdata)
        return quiet_history.build_source(lines, attested, monday)

    def stage(self, monday: dt.date, unattended: bool = False) -> Outcome:
        """Stage a week (the Friday job stages next week; see stage_next).

        Unattended (the scheduled Friday run), success means an actionable proof:
        the week's staged files, read back from the SAVED publication data, equal what
        was generated and verify against the current inputs. Anything less is a
        held result with its code (exit 9), never a green run."""
        self._ready()
        notices = [r.message for r in [self.reconcile()] if r]
        reg, _obj = self._read_registry()
        if reg is not None and monday.isoformat() in reg["weeks"]:
            return Outcome("noop", f"{monday} is already published", notices=notices)
        history = self._history(reg, monday)
        try:
            staged = self._stage(week_starting(monday), history, self.clock())
        except WeekHeld as held:
            if unattended:
                return self._stage_failed("STAGE_WEEK_HELD", monday, notices + [str(held)])
            return Outcome("held", str(held), notices=notices)
        if unattended and (staged.week != monday or staged.provenance.get("week") != monday.isoformat()):
            return self._stage_failed("STAGE_WRONG_WEEK", monday, notices + [f"generated for {staged.week}"])
        self._write_staged(staged, notices)
        notices += staged.validation["warnings"]
        if unattended:
            problem = self._saved_proof_problem(staged, monday, history)
            if problem:
                return self._stage_failed(problem[0], monday, notices + [problem[1]])
        return Outcome("staged", f"staged {monday}: {staged.validation['items']} items, issue {staged.issue_sha[:12]}",
                       notices=notices)

    def _saved_proof_problem(self, staged: Staged, monday: dt.date, history: list) -> Optional[tuple]:
        """(code, detail) unless the SAVED staged/<monday>/ is exactly what was just
        generated, is for that week, and verifies against the current inputs."""
        saved = self.pubdata.read_saved_staged(monday)
        if saved is None or saved != staged.files():
            return "STAGE_NOT_SAVED", "the staged week is not in the saved publication data exactly as generated"
        try:
            proof = Staged.from_files(monday, saved)
        except (KeyError, TypeError, ValueError) as err:
            return "STAGE_INVALID", f"the saved staged week is unreadable ({err})"
        if proof.provenance.get("week") != monday.isoformat():
            return "STAGE_WRONG_WEEK", f"the saved proof is for {proof.provenance.get('week')}"
        try:
            source = self._quiet_source(monday) if proof.provenance.get("edition") == "quiet" else None
            problems = verify_staged(proof, self.inputs, self.policy, history, self._dataset_text(),
                                     self.checks, self.mode, quiet_source=source)
        except (OSError, ValueError) as err:
            problems = [f"cannot verify the saved staged week: {err}"]
        if problems:
            return "STAGE_INVALID", "; ".join(problems)
        return None

    @staticmethod
    def _stage_failed(code: str, monday: dt.date, notices: list) -> Outcome:
        return Outcome("held", f"{code} {monday}: no actionable proof staged", notices=notices, code=code,
                       week=monday.isoformat())

    def stage_next(self, unattended: bool = False) -> Outcome:
        """The Friday job: next week only."""
        local = self.clock().astimezone(MANILA).date()
        return self.stage(week_of(local).start + dt.timedelta(days=7), unattended)

    # Operations.

    def promote(self, unattended: bool = False) -> Outcome:
        """Publish the current Manila week once (Monday runs and the daily catch-up).

        Unattended (the scheduled runs, D2): only the persisted, reviewed Friday proof,
        exactly as staged, ever publishes. A missing, changed or late proof is an
        audited hold with its code instead (exit 9); nothing is staged, selected or
        link-checked here, and the previous issue stays current. Earlier unserved output never blocks that hold,
        but still stops a publication, before anything is written (exit 8)."""
        self._ready()
        notices = [r.message for r in [self.reconcile(require_verified=not unattended)] if r]
        now = self.clock()
        week = rollover_week(now)
        if week is None:
            return Outcome("noop", "before the Monday 06:00 rollover", notices=notices)
        key = week.start.isoformat()
        reg, obj = self._read_registry()
        if reg is None:
            raise Refused("nothing is published yet; the first issue is a `launch`")
        if key in reg["weeks"]:
            return Outcome("noop", f"{key} is already published (revision {reg['weeks'][key]['active']})", notices=notices)
        if reg["hold"] is not None:
            if unattended:  # the current week is unpublished and held: action needed
                return Outcome("held", f"HELD {key}: on hold since {reg['hold']['since']}", notices=notices,
                               code="HELD", week=key)
            return Outcome("noop", f"on hold since {reg['hold']['since']}: {reg['hold']['reason']}", notices=notices)
        history = self._history(reg, week.start)

        if unattended:  # loading the persisted proof is part of validating it
            staged, problems = self._persisted_proof(week.start)
            unreadable = _UNREADABLE_PROOF
        else:
            files = self.pubdata.read_staged(week.start)
            staged = Staged.from_files(week.start, files) if files else None
            problems, unreadable = [], ()
        if staged is not None:
            try:
                source = self._quiet_source(week.start) if staged.provenance.get("edition") == "quiet" else None
                problems = verify_staged(staged, self.inputs, self.policy, history, self._dataset_text(),
                                         self.checks, self.mode, quiet_source=source)
            except (OSError, ValueError) as err:
                problems = [f"cannot verify the staged week: {err}"]
            except unreadable as err:  # unattended: a wrongly shaped proof is a changed one, never a crash
                problems = [f"the staged proof is malformed ({type(err).__name__})"]
        if unattended:
            code, detail = self._proof_problem(staged, problems, week)
            if code:
                out = self._hold(reg, obj, code, key)
                out.notices = notices + [f"{code} {key}: {detail}"] + out.notices
                return out
            self.reconcile()  # earlier output still unserved: PublishedUnverified, before anything is written
        elif staged is None or problems:
            why = "; ".join(problems) if problems else "nothing was staged"
            try:
                staged = self._stage(week, history, now)
            except WeekHeld as held:
                return Outcome("held", f"{held}; the previous issue stays current", notices=notices)
            notices.append(f"CHANGED PROOF: {key} re-staged at promotion because {why}" if problems
                           else f"{key} staged at promotion because {why}")
            self._write_staged(staged, notices)

        published_at = now.isoformat()
        txn = regmod.make_txn(now, "promote", [obj.etag, staged.issue_sha, staged.web_sha])
        new_reg = regmod.promote(reg, week.start, staged.revision(published_at), txn)
        history_line = dict(self._history_entry("promote", key, 0, staged, published_at), txn=txn)
        guard = self._all(self._week_guard(week), self._eligibility_guard(staged.provenance, staged.issue_bytes,
                                                                          history_line, week.start))
        out = self._transact("promote", key, reg, obj, new_reg, self._uploads(staged), guard,
                             history_line, staged.provenance)
        out.notices = notices + out.notices
        return out

    def _persisted_proof(self, monday: dt.date) -> tuple:
        """(proof, []) for a readable, well-shaped persisted Friday proof; (None, [])
        when none was staged at all; (None, [why]) when one is there but incomplete,
        unreadable or wrongly shaped: that is a changed proof, never a valid one and
        never "absent"."""
        files = self.pubdata.read_staged(monday)
        if files is None:
            if self.pubdata.staged_dir(monday).exists():
                return None, ["the staged proof is incomplete (a staged file is missing or unreadable)"]
            return None, []
        try:
            proof = Staged.from_files(monday, files)
        except _UNREADABLE_PROOF + (ValueError,) as err:  # malformed JSON or text is a ValueError
            return None, [f"the staged proof is unreadable ({type(err).__name__})"]
        prov, inputs = proof.provenance, proof.provenance.get("inputs") if isinstance(proof.provenance, dict) else None
        if not (isinstance(prov, dict) and isinstance(inputs, dict) and isinstance(proof.validation, dict)
                and isinstance(prov.get("week"), str) and isinstance(prov.get("edition"), str)
                and isinstance(prov.get("records"), list)
                and all(isinstance(inputs.get(k), str) for k in ("generator", "datasetSha256", "affinitySha256",
                                                                 "generatedAt"))):
            return None, ["the staged proof has the wrong shape"]
        return proof, []

    @staticmethod
    def _proof_problem(staged: Optional[Staged], problems: list, week: IssueWeek) -> tuple:
        """(code, detail) for an unattended promotion that must hold, or (None, None).
        It judges only the persisted Friday proof: with none at all, the reason is simply
        that (a held week shows on Friday, as STAGE_WEEK_HELD); nothing is generated here.
        One that is there but unreadable or wrongly shaped is a changed proof."""
        if staged is None:
            if problems:
                return "AUTO_PROOF_CHANGED", "; ".join(problems)
            return "AUTO_NO_PROOF", "nothing was staged"
        if problems:
            return "AUTO_PROOF_CHANGED", "; ".join(problems)
        try:
            generated = dt.datetime.fromisoformat(staged.provenance["inputs"]["generatedAt"])
        except (KeyError, TypeError, ValueError):
            return "AUTO_PROOF_LATE", "the proof's generatedAt is unreadable"
        opens = dt.datetime.combine(week.start - dt.timedelta(days=3), dt.time(0, 0), tzinfo=MANILA)
        closes = dt.datetime.combine(week.start, ROLLOVER, tzinfo=MANILA)
        if generated.tzinfo is None or not opens <= generated < closes:
            return "AUTO_PROOF_LATE", f"generated at {staged.provenance['inputs']['generatedAt']}, outside " \
                                      f"[{opens.isoformat()}, {closes.isoformat()})"
        return None, None

    def launch(self, first_publication: bool) -> Outcome:
        """The inaugural issue: the current Manila week, into an empty store, once."""
        self._ready()
        if not first_publication:
            raise Refused("launch publishes the first issue only; pass --first-publication")
        self.reconcile()
        if any(h["status"] == "committed" for h in self.pubdata.history()):
            raise Refused("publication history exists; launch happens once")
        reg, obj = self._read_registry()
        if obj is not None:
            raise Refused("the store already has a registry")
        now = self.clock()
        week = rollover_week(now)
        if week is None:
            raise Refused("launch after the Monday 06:00 rollover")
        try:
            staged = self._stage(week, [], now)
        except WeekHeld as held:
            raise Refused(f"the launch week would be held: {held}") from None
        notices: list = []
        self._write_staged(staged, notices)
        key = week.start.isoformat()
        published_at = now.isoformat()
        txn = regmod.make_txn(now, "launch", [None, staged.issue_sha, staged.web_sha])
        new_reg = regmod.launch(None, week.start, staged.revision(published_at), txn)
        history_line = dict(self._history_entry("launch", key, 0, staged, published_at), txn=txn)
        guard = self._all(self._week_guard(week), self._eligibility_guard(staged.provenance, staged.issue_bytes,
                                                                          history_line, week.start))
        out = self._transact("launch", key, None, None, new_reg, self._uploads(staged), guard,
                             history_line, staged.provenance)
        out.notices = notices + out.notices
        return out

    def correct(self, monday: dt.date, expect_rev: int, reason: str) -> Outcome:
        """A new revision of a published week, from the dataset as it is now.

        Allowed while earlier served-output checks still fail: a fresh correction is
        the recovery for a broken revision (rollback to unapproved text is refused)."""
        self._ready()
        self.reconcile(require_verified=False)
        reg, obj = self._read_registry()
        key = monday.isoformat()
        if reg is None or key not in reg["weeks"]:
            raise Refused(f"{key} is not published")
        now = self.clock()
        try:
            staged = self._stage(week_starting(monday), self._history(reg, monday), now)
        except WeekHeld as held:
            raise Refused(f"the corrected {key} would be held ({held}); use rollback instead") from None
        published_at = now.isoformat()
        txn = regmod.make_txn(now, "correct", [obj.etag, key, expect_rev, staged.issue_sha, reason])
        try:
            new_reg = regmod.correct(reg, monday, expect_rev, staged.revision(published_at), reason, txn)
        except NoOp as noop:
            return Outcome("noop", str(noop))
        rev = new_reg["weeks"][key]["active"]
        history_line = dict(self._history_entry("correct", key, rev, staged, published_at, reason), txn=txn)
        return self._transact("correct", key, reg, obj, new_reg, self._uploads(staged),
                              self._eligibility_guard(staged.provenance, staged.issue_bytes, history_line, monday),
                              history_line, staged.provenance)

    def _control(self, op: str, plan: Callable[[dict, str], dict], basis: list, week: str, detail: dict) -> Outcome:
        self.reconcile(require_verified=False)  # an operator may need these while serving is broken
        reg, obj = self._read_registry()
        if reg is None:
            raise Refused("nothing is published")
        now = self.clock()
        txn = regmod.make_txn(now, op, [obj.etag] + basis)
        try:
            new_reg = plan(reg, txn)
        except NoOp as noop:
            return Outcome("noop", str(noop))
        guard = self._all(self._quiet_presentation_guard(reg, new_reg), self._visible_revisions_guard(reg, new_reg))
        reason = guard()  # preflight: refused before anything is recorded
        if reason:
            raise Refused(reason)
        line = dict(detail, txn=txn, op=op, week=week, at=now.isoformat())
        return self._transact(op, week, reg, obj, new_reg, [], guard, line, None)

    def hold(self, reason: str) -> Outcome:
        """Operator: hold automatic promotion. Nothing visible changes; `resume` releases."""
        self.reconcile(require_verified=False)  # earlier unserved output never blocks a hold
        reg, obj = self._read_registry()
        return self._hold(reg, obj, "HELD", reg["current"] if reg else "", reason)

    def _hold(self, reg: Optional[dict], obj, code: str, week: str, reason: Optional[str] = None) -> Outcome:
        """The hold-only write (an operator's hold, and unattended promotion's automatic
        ones): it reads no issue object, provenance or eligibility (only the registry,
        for its conditional write), so it works exactly when those are what is broken;
        its own guard re-proves, before every attempt, that the write changes nothing
        but the hold."""
        if reg is None:
            raise Refused("nothing is published")
        if code not in HOLD_CODES:
            raise ValueError(f"unknown hold code {code}")
        now = self.clock()
        reason = reason or f"{code}: {week} was not published automatically"
        txn = regmod.make_txn(now, "hold", [obj.etag, code, week, reason])
        try:
            new_reg = regmod.hold(reg, reason, now.isoformat(), txn)
        except NoOp as noop:
            return Outcome("noop", str(noop))
        line = {"txn": txn, "op": "hold", "week": week, "at": now.isoformat(), "code": code, "reason": reason}
        return self._transact("hold", week, reg, obj, new_reg, [], lambda: _hold_only_guard(reg, new_reg), line, None)

    def rollback(self, monday: dt.date, rev: Optional[int], reason: str) -> Outcome:
        since = self.clock().isoformat()
        return self._control("rollback", lambda reg, txn: regmod.rollback(reg, monday, rev, reason, since, txn),
                             [monday.isoformat(), rev, reason], monday.isoformat(), {"rev": rev, "reason": reason})

    def restore(self, monday: dt.date, rev: int) -> Outcome:
        return self._control("restore", lambda reg, txn: regmod.restore(reg, monday, rev, txn),
                             [monday.isoformat(), rev], monday.isoformat(), {"rev": rev})

    def resume(self) -> Outcome:
        reg, _ = self._read_registry()
        week = reg["current"] if reg else ""
        return self._control("resume", lambda reg, txn: regmod.resume(reg, txn), [], week, {})


def shown_revisions(before: Optional[dict], after: dict) -> set:
    """(week, rev) pairs a write makes visible: the new current week's active
    revision, and every week whose active revision changes."""
    out = {(after["current"], after["weeks"][after["current"]]["active"])}
    for week, entry in after["weeks"].items():
        if before is None or week not in before["weeks"] or before["weeks"][week]["active"] != entry["active"]:
            out.add((week, entry["active"]))
    return out


def _hold_only_guard(before: Optional[dict], after: dict) -> Optional[str]:
    """None if ``after`` only sets a hold on ``before``: it validates, the hold goes
    from none to one naming this transaction, and the current week, every week's
    revisions and active number, and the device manifest's bytes are identical. Then
    nothing the host serves changes. Otherwise what differs. Reads no store."""
    if before is None:
        return "nothing is published"
    try:
        regmod.validate(after)
    except regmod.CorruptRegistry as err:
        return f"the new registry is invalid ({err})"
    if before.get("hold") is not None or after["hold"] is None or after["hold"]["txn"] != after["txn"]:
        return "it does not set a new hold of its own"
    if after["current"] != before["current"] or after["weeks"] != before["weeks"]:
        return "it changes the current week or a revision"
    if regmod.manifest_bytes(after) != regmod.manifest_bytes(before):
        return "it changes the device manifest"
    if set(after) != set(before) or any(after[k] != before[k] for k in after if k not in ("hold", "txn")):
        return "it changes more than the hold"
    return None


def expected_verify(op: Optional[str], before: Optional[dict], after: dict) -> list:
    """The verification targets a registry change must record. Empty only for a hold
    proven hold-only on exactly these registries; an "op hold" without that proof
    raises HoldNotProven and never falls back to a full list. Every other operation:
    verification_targets, which always names the manifest."""
    if op == "hold":
        problem = _hold_only_guard(before, after)
        if problem:
            raise HoldNotProven(problem)
        return []
    return verification_targets(before, after)


def _expected_targets_problem(pending: dict) -> Optional[str]:
    """Recompute the complete target list from the registries before and after the
    switch, each bound to its recorded hash, and require ``pending["verify"]`` to equal
    it exactly: a target that is missing, extra or altered is a problem, not a smaller job.
    The list depends on the operation (expected_verify): a hold's is [] only if the
    registries prove it hold-only; anything else claiming to be a hold is a problem."""
    before_text, before_sha = pending.get("expectedRegistry"), pending.get("expectedRegistrySha")
    try:
        if before_text is None:
            if before_sha is not None or pending.get("expectedEtag") is not None:
                return "the registry it replaced is not recorded"
            before = None
        else:
            before_bytes = before_text.encode("utf-8") if isinstance(before_text, str) else b""
            if regmod.sha256(before_bytes) != before_sha:
                return "the registry it replaced does not match its recorded hash"
            before = regmod.parse(before_bytes)
        after_bytes = regmod.dumps(pending["intendedRegistry"])
        if regmod.sha256(after_bytes) != pending["intendedRegistrySha"]:
            return "the registry it published does not match its recorded hash"
        expected = expected_verify(pending.get("op"), before, regmod.parse(after_bytes))
    except HoldNotProven as err:
        return f"it is recorded as a hold, but its registries are not hold-only ({err})"
    except (regmod.CorruptRegistry, TypeError, ValueError, KeyError, AttributeError) as err:
        return f"its registries are unreadable: {err}"
    if pending["verify"] != expected:
        return "they differ from the targets its registry change requires"
    return None


def verification_targets(before: Optional[dict], after: dict) -> list:
    """What the host must serve once ``after`` is the registry: the device manifest,
    and the page and device issue of every week whose visible revision changed."""
    targets = [{"kind": "manifest", "path": "/current.json", "sha256": regmod.sha256(regmod.manifest_bytes(after))}]
    weeks = set()
    for week, entry in after["weeks"].items():
        if before is None or week not in before["weeks"] or before["weeks"][week]["active"] != entry["active"]:
            weeks.add(week)
    if before is None or before["current"] != after["current"]:
        weeks.add(after["current"])
    for week in sorted(weeks):
        rev = regmod.active_revision(after, week)
        targets.append({"kind": "page", "path": f"/{week}/", "week": week, "sha256": rev["webSha256"]})
        targets.append({"kind": "issue", "path": "/" + rev["issueKey"], "sha256": rev["issueSha256"]})
    return targets

