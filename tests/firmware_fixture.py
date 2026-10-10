"""A synced firmware contract for tests: the files `tools/firmware_contract.py
--sync` writes (contract, vendored acceptance schema) plus the owner-approved
verification record, all synthetic, in a temporary tree that
dogear.publish.firmware reads instead of the repository's data/."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from dogear.publish import firmware

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "firmware"
SCHEMA_BYTES = (FIXTURES / "acceptance-v1.json").read_bytes()
SCHEMA = json.loads(SCHEMA_BYTES)
TAG, COMMIT, TAG_OBJECT, RUN, RELEASE_ID = "v1.2.0", "c" * 40, "e" * 40, 4242, 777
LIMITS = {"kIssueSchemaMax": 3, "kIssueSchemaMin": 2, "kMaxBodyLen": 2048, "kMaxEntries": 12,
          "kMaxHeadlineLen": 240, "kMaxIssueBytes": 32768, "kMaxIssueIdLen": 17, "kMaxIssuePathLen": 128,
          "kMaxLineLen": 160, "kMaxManifestBytes": 4096, "kMaxShaLen": 64, "kMaxShortLen": 64, "kMaxUrlLen": 2953}


def h(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def sums_text(files: dict) -> bytes:
    """SHA256SUMS.txt as the firmware's candidate.py writes it."""
    return "".join(f"{files[name]}  {name}\n" for name in sorted(files)).encode("ascii")


def assets() -> dict:
    out = {name: h(name) for name in firmware.expected_assets(SCHEMA) if name != firmware.SUMS}
    for legacy, source in firmware.LEGACY_ASSETS.items():
        out[legacy] = out[source]
    out[firmware.SUMS] = firmware.sha256(sums_text(out))
    return out


def acceptance(release: dict) -> dict:
    entries = []
    for name in SCHEMA["packageOrder"]:
        spec = SCHEMA["packages"][name]
        entry = {"package": name, "env": f"env-{name}", "board": spec["board"], "file": f"firmware-{name}.bin",
                 "sha256": release["assets"][f"firmware-{name}.bin"], "install": spec["install"],
                 "level": spec["level"], "date": "2026-10-20", "checks": {c: "PASS" for c in spec["checks"]}}
        if spec["board"] == "X4 Pro":
            entry["pieces"] = {k: h(f"{name}/{k}") for k in firmware.PIECE_KEYS}
        entries.append(entry)
    return {"schema": SCHEMA["schema"], "repository": firmware.FIRMWARE_REPOSITORY, "tag": release["tag"],
            "commit": release["commit"], "candidateRun": release["candidateRun"], "sumsSha256": release["sumsSha256"],
            "fixtures": {"generator": "d" * 40,
                         "R": {"issueId": "dogear-2026-11-23", "schemaVersion": 2, "sha256": h("R"), "items": 4,
                               "pages": 6},
                         "Q1": {"issueId": "dogear-2026-11-16", "schemaVersion": 3, "sha256": h("Q1"), "items": 4,
                                "coverRows": 3, "pages": 6},
                         "Q2": {"issueId": "dogear-2026-12-21", "schemaVersion": 3, "sha256": h("Q2"), "items": 5,
                                "coverRows": 3, "pages": 7}},
            "packages": entries, "approvedBy": "owner", "approvedOn": "2026-10-21"}


def verification_record(commit: str = COMMIT, tag_object: str = TAG_OBJECT) -> dict:
    a = assets()
    release = {"tag": TAG, "tagObject": tag_object, "commit": commit, "releaseId": RELEASE_ID, "candidateRun": RUN,
               "sumsSha256": a[firmware.SUMS], "assets": a}
    acc = acceptance(release)
    return {"schema": 2, "repository": firmware.FIRMWARE_REPOSITORY, "release": release,
            "acceptanceSha256": firmware.sha256(firmware.canonical(acc)), "acceptance": acc,
            "ota": [{"board": "X4", "from": "v1.1.1", "to": TAG, "package": "x4-g", "result": "PASS",
                     "date": "2026-10-23"},
                    {"board": "X4 Pro", "from": "v1.1.1", "to": TAG, "package": "x4pro-gp", "slotVerified": True,
                     "result": "PASS", "date": "2026-10-23"}],
            "authorizes": {"kIssueSchemaMax": 3, "quietWeek": True}, "approvedBy": "owner", "approvedOn": "2026-10-23"}


def contract_for(record: dict, record_bytes: bytes, limits: dict = None) -> dict:
    rel = record["release"]
    return {"_note": "test", "firmwareCommit": rel["commit"], "limits": dict(limits or LIMITS),
            "release": {"tag": rel["tag"], "tagObject": rel["tagObject"], "commit": rel["commit"],
                        "releaseId": rel["releaseId"], "candidateRun": rel["candidateRun"],
                        "sumsSha256": rel["sumsSha256"], "acceptanceSha256": record["acceptanceSha256"],
                        "verification": f"{firmware.VERIFICATIONS}/{rel['tag']}.json",
                        "verificationSha256": firmware.sha256(record_bytes),
                        "acceptanceSchemaSha256": firmware.sha256(SCHEMA_BYTES)}}


class SyncedTree:
    """The synced files under `root`; `write` re-creates them consistently, and
    `rehash` re-pins an edited record so only its content is wrong."""

    def __init__(self, root: Path):
        self.root = root
        self.record = verification_record()
        self.limits = dict(LIMITS)

    def record_path(self) -> Path:
        return self.root / firmware.VERIFICATIONS / f"{TAG}.json"

    def write(self, record: dict = None, limits: dict = None) -> "SyncedTree":
        record = record if record is not None else self.record
        data = (json.dumps(record, indent=2) + "\n").encode()
        self.record_path().parent.mkdir(parents=True, exist_ok=True)
        self.record_path().write_bytes(data)
        (self.root / firmware.SCHEMA_COPY).parent.mkdir(parents=True, exist_ok=True)
        (self.root / firmware.SCHEMA_COPY).write_bytes(SCHEMA_BYTES)
        contract = contract_for(record, data, limits or self.limits)
        (self.root / firmware.CONTRACT).write_text(json.dumps(contract, indent=2) + "\n")
        return self

    def edit_record(self, mutate, rehash: bool = True) -> None:
        """Change the record. With rehash, the contract pins the changed bytes (as a
        bad sync would), so only the content check can refuse it."""
        record = copy.deepcopy(self.record)
        mutate(record)
        if rehash:
            self.write(record)
        else:
            self.record_path().write_text(json.dumps(record, indent=2) + "\n")

    def edit_contract(self, mutate) -> None:
        path = self.root / firmware.CONTRACT
        contract = json.loads(path.read_text())
        mutate(contract)
        path.write_text(json.dumps(contract, indent=2) + "\n")
