"""Whether released DOGEAR firmware presents quiet issues (schema 3).

Production may make a quiet revision visible only with a synced firmware
contract: data/firmware-contract.json with a `release` block, written by
`tools/firmware_contract.py --sync` from a published, immutable firmware release
and the owner-approved verification record it names. Every GitHub check happens
once, at that sync. A publication run decides from the files in this repository
alone (`capability`): no network. Missing, malformed, mismatched or
non-authorising files mean "no".

The acceptance rules here are the firmware repository's (its
scripts/release/candidate.py and acceptance-v1.json, vendored at sync time into
data/acceptance-schema/), applied to the record's own release block.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from pathlib import Path
from typing import NamedTuple, Optional

from ..quiet import QUIET_SCHEMA_VERSION

ROOT = Path(__file__).resolve().parents[2]  # tests point this at a temporary tree
CONTRACT = "data/firmware-contract.json"
SCHEMA_COPY = "data/acceptance-schema/acceptance-v1.json"
VERIFICATIONS = "data/firmware-verifications"

FIRMWARE_REPOSITORY = "archievalmariano/crosspoint-reader"
CANDIDATE_WORKFLOW = ".github/workflows/release_candidate.yml"
FIRMWARE_SCHEMA_PATH = "scripts/release/acceptance-v1.json"
SUMS = "SHA256SUMS.txt"
# Byte copies kept for devices updating from 1.0.1: copy -> its source file.
LEGACY_ASSETS = {"firmware.bin": "firmware-x4-g.bin", "firmware-x4pro.bin": "firmware-x4pro-gp.bin"}

HEX40 = re.compile(r"[0-9a-f]{40}")
HEX64 = re.compile(r"[0-9a-f]{64}")
TAG = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+")
ISSUE_ID = re.compile(r"dogear-[0-9]{4}-[0-9]{2}-[0-9]{2}")
RECORD_KEYS = ("schema", "repository", "tag", "commit", "candidateRun", "sumsSha256", "fixtures", "packages",
               "hostCovered", "approvedBy", "approvedOn")
PACKAGE_KEYS = ("package", "env", "board", "file", "sha256", "install", "level", "date", "checks")
PIECE_KEYS = ("bootloader", "partitions", "boot_app0")
VERIFICATION_KEYS = ("schema", "repository", "release", "acceptanceSha256", "acceptance", "ota", "authorizes",
                     "approvedBy", "approvedOn")
RELEASE_KEYS = ("tag", "tagObject", "commit", "releaseId", "candidateRun", "sumsSha256", "assets")
CONTRACT_KEYS = ("_note", "firmwareCommit", "limits", "release")
CONTRACT_RELEASE_KEYS = ("tag", "tagObject", "commit", "releaseId", "candidateRun", "sumsSha256", "acceptanceSha256",
                         "verification", "verificationSha256", "acceptanceSchemaSha256")
OTA = (("X4", "x4-g"), ("X4 Pro", "x4pro-gp"))  # the update test: each board on its existing package
TAG_FIELDS = ("candidate-run", "candidate-commit", "candidate-sums-sha256", "physically-accepted",
              "acceptance-sha256")


# E3 (CAN'T LOAD AN ISSUE) is host-covered, never physical: "Physical E3 is not a release gate. Host E3 coverage is mandatory."
# The firmware's candidate run produces e3-host-evidence.json on its own commit (each of
# exactly these tests PASS, none skipped); it is a release asset, and the
# acceptance record carries the same evidence. Mirrors candidate.py.
E3_CHECK = "dogear-e3-cant-load"
E3_POLICY = "Physical E3 is not a release gate. Host E3 coverage is mandatory."
E3_EVIDENCE = "e3-host-evidence.json"
E3_EVIDENCE_SCHEMA = "crosspoint-e3-host-evidence/1"
E3_DEFINITION_KEYS = ('policy', 'scenario', 'reason', 'evidence', 'tests')
E3_EVIDENCE_KEYS = ('schema', 'check', 'repository', 'commit', 'candidateRun', 'definitionSha256', 'tests', 'skipped')
E3_HOST_TESTS = (
    "DogearBootstrap.ATlsReadFailureAfterTheExactBodyIsNeverNothingPublished",
    "DogearBootstrap.BytesReadOneAtATimeClassifyTheSame",
    "DogearBootstrap.ChunkedFraming",
    "DogearBootstrap.ContentLengthMustMatchTheBody",
    "DogearBootstrap.EveryTruncationIsAnError",
    "DogearBootstrap.NonOkBodiesAreCappedAt64Bytes",
    "DogearBootstrap.OnlyExplicitFramingCounts",
    "DogearBootstrap.OnlyStatus503",
    "DogearBootstrap.OnlyTheExactBodyCounts",
    "DogearBootstrap.OnlyTheFirstManifestResponse",
    "DogearBootstrap.RouterVectorsClassifyExactlyAsTheServerMeansThem",
    "DogearBootstrap.TheReaderStopsAtCompletionAndReportsHowItEnded",
    "DogearState.EachStatusScreenHasItsOwnHeadingAndNote",
    "DogearState.EveryCombinationFollowsTheThreeRules",
    "DogearState.StatusCopyIsExactlyTheApprovedCopy",
    "DogearState.TheCasesThatMatter",
    "DogearTls.CertificateForAnotherHostIsRefused",
    "DogearTls.ExpiredCertificateIsRefused",
    "DogearTls.FailsClosedWhenTrustCannotBeSetUp",
    "DogearTls.ProductionRootsDoNotTrustTheTestRoot",
    "DogearTls.SelfSignedCertificateIsRefused",
    "DogearTls.TrustedCertificateForTheRightHostConnects",
    "DogearTls.ValidCertificateDoesNotCoverADifferentRequestedHost",
    "DogearTlsRead.ACorruptRecordAfterTheExactBodyIsNotNothingPublished",
    "DogearTlsRead.AReadFailureAfterTheExactBodyIsNotNothingPublished",
    "DogearTlsRead.EvenACleanCloseCannotFrameNothingPublished",
    "DogearTlsRead.ExplicitFramingIsNothingPublishedAndACutBeforeItsEndIsNot",
    "DogearTlsRead.ReadResultWaitsOnlyWhileTheTransportIsOpen",
)


class Invalid(ValueError):
    """A contract, record or release that does not authorise anything."""


def require(ok: bool, message: str) -> None:
    if not ok:
        raise Invalid(message)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def strict_json(data: bytes):
    """JSON with no duplicate key anywhere and no NaN/Infinity."""

    def pairs(items):
        keys = [k for k, _ in items]
        dupes = sorted({k for k in keys if keys.count(k) > 1})
        require(not dupes, f"duplicate key {dupes[0]!r}" if dupes else "")
        return dict(items)

    def constant(name):
        raise Invalid(f"{name} is not JSON")

    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as err:
        raise Invalid(f"not JSON: {err}") from None


def canonical(obj) -> bytes:
    """The bytes the firmware tag's acceptance-sha256 is computed over."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def exact_keys(obj, keys, where: str) -> None:
    require(isinstance(obj, dict), f"{where} is not an object")
    missing, extra = sorted(set(keys) - set(obj)), sorted(set(obj) - set(keys))
    require(not missing and not extra, f"{where}: missing {missing}, unexpected {extra}")


def is_int(value) -> bool:
    return type(value) is int  # not bool


def is_hex(value, pattern) -> bool:
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def check_date(value, where: str) -> None:
    ok = isinstance(value, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is not None
    if ok:
        try:
            dt.date.fromisoformat(value)
        except ValueError:
            ok = False
    require(ok, f"{where} is not a YYYY-MM-DD date")


# ---- the firmware's acceptance schema and record ------------------------------


def check_schema(schema) -> dict:
    require(isinstance(schema, dict) and schema.get("schema") == "crosspoint-acceptance/1",
            "unknown acceptance schema")
    order = schema.get("packageOrder")
    packages = schema.get("packages")
    require(isinstance(order, list) and isinstance(packages, dict) and sorted(order) == sorted(packages)
            and len(order) == len(set(order)), "acceptance schema: packages and packageOrder differ")
    for name in order:
        spec = packages[name]
        checks = spec.get("checks")
        require(isinstance(checks, list) and checks and len(checks) == len(set(checks)), f"{name}: bad check list")
        require(all(c in schema.get("checks", {}) for c in checks), f"{name} names an undefined check")
        require(spec.get("board") in ("X4", "X4 Pro"), f"{name}: unknown board")
    flagships = schema.get("dogearFlagships")
    require(isinstance(flagships, list) and flagships and all(f in packages for f in flagships),
            "acceptance schema: bad dogearFlagships")
    for key in ("quietChecks", "regularCacheChecks"):
        group = schema.get(key)
        require(isinstance(group, list) and group, f"acceptance schema: no {key}")
        for name in flagships:
            require(all(c in packages[name]["checks"] for c in group), f"{name} lacks {key}")
    check_e3_definition(schema)
    return schema


def check_e3_definition(schema: dict) -> dict:
    """hostCovered is required: exactly E3, with the policy, the evidence file and
    exactly E3_HOST_TESTS; and E3 is not a physical check anywhere."""
    host = schema.get("hostCovered")
    require(isinstance(host, dict), "acceptance schema: hostCovered is missing; host E3 coverage is mandatory")
    exact_keys(host, [E3_CHECK], "hostCovered")
    definition = host[E3_CHECK]
    exact_keys(definition, E3_DEFINITION_KEYS, f"hostCovered.{E3_CHECK}")
    require(definition["policy"] == E3_POLICY, f"hostCovered.{E3_CHECK}: the policy must read {E3_POLICY!r}")
    require(definition["evidence"] == E3_EVIDENCE, f"hostCovered.{E3_CHECK}: evidence must be {E3_EVIDENCE}")
    require(definition["tests"] == list(E3_HOST_TESTS),
            f"hostCovered.{E3_CHECK}: tests are not exactly the required E3 host tests")
    require(E3_CHECK not in schema.get("checks", {}), f"{E3_CHECK} must not be a physical check")
    for name, spec in schema["packages"].items():
        require(E3_CHECK not in spec["checks"], f"{name}: physical E3 cannot stand in for host E3 evidence")
    return definition


def check_e3_evidence(evidence, schema: dict, *, commit, run) -> None:
    """The candidate run's E3 host-test PASS: this commit and run, this definition,
    exactly the required tests, each exactly PASS, none skipped."""
    exact_keys(evidence, E3_EVIDENCE_KEYS, "E3 host evidence")
    require(evidence["schema"] == E3_EVIDENCE_SCHEMA, f"E3 host evidence schema is not {E3_EVIDENCE_SCHEMA}")
    require(evidence["check"] == E3_CHECK, f"E3 host evidence is not for {E3_CHECK}")
    require(evidence["repository"] == FIRMWARE_REPOSITORY, "E3 host evidence names another repository")
    require(evidence["commit"] == commit, "E3 host evidence belongs to another commit")
    require(is_int(evidence["candidateRun"]) and evidence["candidateRun"] == run,
            "E3 host evidence belongs to another candidate run")
    require(evidence["definitionSha256"] == sha256(canonical(schema["hostCovered"][E3_CHECK])),
            "E3 host evidence is for another host-test definition")
    exact_keys(evidence["tests"], E3_HOST_TESTS, "E3 host evidence tests")
    failed = sorted(t for t, result in evidence["tests"].items() if result != "PASS")
    require(not failed, f"E3 host tests not PASS: {failed}")
    require(evidence["skipped"] == [], "E3 host evidence records skipped tests")


def expected_assets(schema: dict) -> list:
    names = [SUMS, E3_EVIDENCE, *LEGACY_ASSETS]
    for name in schema["packageOrder"]:
        names.append(f"firmware-{name}.bin")
        if schema["packages"][name]["board"] == "X4 Pro":
            names.append(f"firmware-{name}.factory.bin")
    return sorted(names)


def check_fixtures(fixtures, schema: dict) -> None:
    specs = schema["fixtures"]
    exact_keys(fixtures, ["generator", *specs], "fixtures")
    require(is_hex(fixtures["generator"], HEX40), "fixtures.generator is not a 40-hex commit")
    ids, digests = set(), set()
    for name, spec in specs.items():
        f, where = fixtures[name], f"fixture {name}"
        exact_keys(f, spec["keys"], where)
        require(is_int(f["schemaVersion"]) and f["schemaVersion"] == spec["schemaVersion"],
                f"{where} must be schema {spec['schemaVersion']}")
        require(is_hex(f["issueId"], ISSUE_ID), f"{where}: bad issueId")
        require(is_hex(f["sha256"], HEX64), f"{where}: bad sha256")
        for key in ("items", "pages", "coverRows"):
            if key in f:
                require(is_int(f[key]) and f[key] > 0, f"{where}: {key} is not a positive integer")
        require(f["pages"] == f["items"] + 2, f"{where}: pages must be the cover, one per item and the final page")
        if f["schemaVersion"] == QUIET_SCHEMA_VERSION:
            require(3 <= f["items"] <= 5, f"{where}: a quiet issue has 3 to 5 items")
            require(f["coverRows"] <= f["items"] - 1, f"{where}: more cover rows than supporting items")
        ids.add(f["issueId"])
        digests.add(f["sha256"])
    require(len(ids) == len(specs) and len(digests) == len(specs), "fixtures repeat an issue")


def check_acceptance(record, schema: dict, release: dict) -> None:
    """The firmware's acceptance rules, bound to `release` (a verification record's
    release block): exactly the packages in order, exactly each package's checks,
    each exactly PASS, every digest the release's."""
    exact_keys(record, RECORD_KEYS, "acceptance record")
    require(record["schema"] == schema["schema"], f"record schema is not {schema['schema']}")
    require(record["repository"] == FIRMWARE_REPOSITORY, "record names another repository")
    require(record["tag"] == release["tag"], "record tag is not the release's")
    require(record["commit"] == release["commit"], "record commit is not the release's")
    require(is_int(record["candidateRun"]) and record["candidateRun"] == release["candidateRun"],
            "record candidateRun is not the release's")
    require(record["sumsSha256"] == release["sumsSha256"], f"record sumsSha256 is not the release's {SUMS}")
    check_fixtures(record["fixtures"], schema)
    host = record["hostCovered"]
    require(isinstance(host, dict), "record hostCovered is missing; host E3 coverage is mandatory")
    exact_keys(host, [E3_CHECK], "record hostCovered")
    check_e3_evidence(host[E3_CHECK], schema, commit=release["commit"], run=release["candidateRun"])
    require(sha256(canonical(host[E3_CHECK])) == release["assets"].get(E3_EVIDENCE),
            f"the record's E3 host evidence is not the release's {E3_EVIDENCE}")
    entries = record["packages"]
    require(isinstance(entries, list) and all(isinstance(e, dict) for e in entries), "packages is not a list of objects")
    names = [e.get("package") for e in entries]
    require(len(names) == len(set(names)), "record repeats a package")
    require(names == schema["packageOrder"], f"record packages {names} are not exactly {schema['packageOrder']}")
    for entry in entries:
        name = entry["package"]
        spec = schema["packages"][name]
        pro = spec["board"] == "X4 Pro"
        exact_keys(entry, PACKAGE_KEYS + (("pieces",) if pro else ()), name)
        require(isinstance(entry["env"], str) and entry["env"], f"{name}: no env")
        require(entry["file"] == f"firmware-{name}.bin", f"{name}: file is not firmware-{name}.bin")
        require(entry["sha256"] == release["assets"].get(entry["file"]), f"{name}: digest is not the release asset's")
        for key in ("board", "install", "level"):
            require(entry[key] == spec[key], f"{name}: {key} must be {spec[key]!r}")
        check_date(entry["date"], f"{name} date")
        require(not (isinstance(entry["checks"], dict) and E3_CHECK in entry["checks"]),
                f"{name}: physical E3 cannot stand in for host E3 evidence")
        exact_keys(entry["checks"], spec["checks"], f"{name} checks")
        failed = sorted(c for c, result in entry["checks"].items() if result != "PASS")
        require(not failed, f"{name}: {failed} not PASS")
        if pro:
            exact_keys(entry["pieces"], PIECE_KEYS, f"{name} pieces")
            require(all(is_hex(v, HEX64) for v in entry["pieces"].values()), f"{name}: bad piece digest")
    require(record["approvedBy"] == "owner", "the acceptance record must be approved by the owner")
    check_date(record["approvedOn"], "acceptance approvedOn")


def quiet_authorised(schema: dict, record: dict, limits: dict) -> bool:
    """Schema-3 quiet issues may be shown: the firmware reads schema 3, and both
    DOGEAR flagships passed every quiet and regular-cache check on candidate bytes."""
    if not (is_int(limits.get("kIssueSchemaMax")) and limits["kIssueSchemaMax"] >= QUIET_SCHEMA_VERSION):
        return False
    by_name = {e["package"]: e for e in record["packages"]}
    required = schema["quietChecks"] + schema["regularCacheChecks"]
    return all(by_name[f]["checks"].get(c) == "PASS" for f in schema["dogearFlagships"] for c in required)


# ---- the verification record (data/firmware-verifications/<tag>.json) ---------


def check_release_block(release, schema: dict) -> None:
    exact_keys(release, RELEASE_KEYS, "release")
    require(is_hex(release["tag"], TAG), "release tag is not vX.Y.Z")
    require(is_hex(release["tagObject"], HEX40) and is_hex(release["commit"], HEX40), "release: bad tag or commit sha")
    require(release["tagObject"] != release["commit"], "release: the tag must be annotated (its own object)")
    for key in ("releaseId", "candidateRun"):
        require(is_int(release[key]) and release[key] > 0, f"release {key} is not a positive integer")
    require(is_hex(release["sumsSha256"], HEX64), "release sumsSha256 is not 64 hex")
    assets = release["assets"]
    require(isinstance(assets, dict) and sorted(assets) == expected_assets(schema),
            f"release assets are not exactly {expected_assets(schema)}")
    require(all(is_hex(v, HEX64) for v in assets.values()), "release: an asset digest is not 64 hex")
    require(assets[SUMS] == release["sumsSha256"], f"release: {SUMS} digest is not sumsSha256")
    for copy, source in LEGACY_ASSETS.items():
        require(assets[copy] == assets[source], f"release: {copy} is not a byte copy of {source}")


def check_verification(record, schema: dict, limits: dict) -> None:
    exact_keys(record, VERIFICATION_KEYS, "verification record")
    require(record["schema"] == 2, "verification record schema is not 2")
    require(record["repository"] == FIRMWARE_REPOSITORY, "verification record names another repository")
    release = record["release"]
    check_release_block(release, schema)
    check_acceptance(record["acceptance"], schema, release)
    require(record["acceptanceSha256"] == sha256(canonical(record["acceptance"])),
            "acceptanceSha256 is not the sha256 of the canonical acceptance record")
    ota = record["ota"]
    require(isinstance(ota, list) and len(ota) == len(OTA), "ota must have one entry per board")
    for entry, (board, package) in zip(ota, OTA):
        keys = ("board", "from", "to", "package", "result", "date") + (("slotVerified",) if board == "X4 Pro" else ())
        exact_keys(entry, keys, f"ota {board}")
        require(entry["board"] == board and entry["package"] == package, f"ota entry is not {board} {package}")
        require(is_hex(entry["from"], TAG) and entry["from"] != release["tag"], f"ota {board}: bad from")
        require(entry["to"] == release["tag"], f"ota {board}: not to {release['tag']}")
        require(entry["result"] == "PASS", f"ota {board} did not pass")
        require(entry.get("slotVerified", True) is True, f"ota {board}: slot not verified")
        check_date(entry["date"], f"ota {board} date")
    authorizes = record["authorizes"]
    exact_keys(authorizes, ("kIssueSchemaMax", "quietWeek"), "authorizes")
    require(authorizes["kIssueSchemaMax"] == limits.get("kIssueSchemaMax") and is_int(authorizes["kIssueSchemaMax"]),
            "authorizes.kIssueSchemaMax is not the firmware's")
    require(authorizes["quietWeek"] is quiet_authorised(schema, record["acceptance"], limits),
            "authorizes.quietWeek is not what the acceptance record supports")
    require(record["approvedBy"] == "owner", "the verification record must be approved by the owner")
    check_date(record["approvedOn"], "approvedOn")


# ---- the firmware's annotated release tag --------------------------------------


def parse_tag_message(text: str, packages: list) -> dict:
    """The firmware's candidate.py parse_tag_message rules (shared vectors:
    tests/fixtures/firmware/tag-vectors.json)."""
    fields = {}
    for line in text.splitlines():
        m = re.fullmatch(r"([a-z0-9-]+):\s*(.*?)\s*", line)
        if m:
            require(m.group(1) in TAG_FIELDS, f"tag message has an unknown field {m.group(1)}")
            require(m.group(1) not in fields, f"tag message repeats {m.group(1)}")
            fields[m.group(1)] = m.group(2)
    missing = [k for k in TAG_FIELDS if k not in fields]
    require(not missing, f"tag message lacks {', '.join(missing)}")
    require(re.fullmatch(r"[1-9][0-9]*", fields["candidate-run"]) is not None, "candidate-run is not a run id")
    require(is_hex(fields["candidate-commit"], HEX40), "candidate-commit is not a 40-hex commit")
    require(is_hex(fields["candidate-sums-sha256"], HEX64), "bad candidate-sums-sha256")
    require(is_hex(fields["acceptance-sha256"], HEX64), "bad acceptance-sha256")
    accepted = fields["physically-accepted"].split()
    require(len(accepted) == len(set(accepted)), "physically-accepted repeats a package")
    require(accepted == packages, f"physically-accepted {accepted} must be exactly {packages}, in that order")
    return fields


# ---- the capability, from local files only --------------------------------------


class Capability(NamedTuple):
    ok: bool
    reason: Optional[str]  # why not, when not ok


def check_contract(root: Path) -> dict:
    """The synced contract and everything it names, read from `root` only.
    Returns the contract; raises Invalid (or OSError) otherwise."""
    contract = strict_json((root / CONTRACT).read_bytes())
    require(isinstance(contract, dict) and "release" in contract,
            "the firmware contract has no release block (it was not synced from a verified release)")
    exact_keys(contract, CONTRACT_KEYS, "firmware contract")
    limits, block = contract["limits"], contract["release"]
    require(isinstance(limits, dict) and all(is_int(v) for v in limits.values()), "contract limits are not integers")
    exact_keys(block, CONTRACT_RELEASE_KEYS, "contract release block")
    require(is_hex(block["tag"], TAG), "contract release tag is not vX.Y.Z")
    require(block["verification"] == f"{VERIFICATIONS}/{block['tag']}.json",
            f"the verification record must be {VERIFICATIONS}/{block['tag']}.json")
    record_bytes = (root / block["verification"]).read_bytes()
    require(sha256(record_bytes) == block["verificationSha256"], "the verification record is not the synced one")
    schema_bytes = (root / SCHEMA_COPY).read_bytes()
    require(sha256(schema_bytes) == block["acceptanceSchemaSha256"], "the vendored acceptance schema is not the synced one")
    schema = check_schema(strict_json(schema_bytes))
    record = strict_json(record_bytes)
    check_verification(record, schema, limits)
    for key in ("tag", "tagObject", "commit", "releaseId", "candidateRun", "sumsSha256"):
        require(block[key] == record["release"][key], f"contract release {key} differs from the verification record")
    require(block["acceptanceSha256"] == record["acceptanceSha256"], "contract acceptanceSha256 differs from the record")
    require(contract["firmwareCommit"] == block["commit"], "contract firmwareCommit is not the release commit")
    return contract


def capability(root: Optional[Path] = None) -> Capability:
    """Whether production may show quiet (schema-3) issues. Local files only."""
    root = ROOT if root is None else root
    try:
        contract = check_contract(root)
        record = strict_json((root / contract["release"]["verification"]).read_bytes())
    except (Invalid, OSError, KeyError, TypeError, AttributeError) as err:
        return Capability(False, str(err) or type(err).__name__)
    if record["authorizes"]["quietWeek"] is not True:
        return Capability(False, "the verified firmware release does not authorise quiet issues")
    return Capability(True, None)
