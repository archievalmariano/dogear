"""The firmware contract DOGEAR's output must fit, vendored so DOGEAR stands alone:
- every constant in the firmware's DogearLimits.h → data/firmware-contract.json;
- the Unicode ranges the device's built-in fonts can draw (the fit check's glyph
  test) → mock/fonts/device-glyphs.json;
- the firmware's acceptance schema → data/acceptance-schema/acceptance-v1.json.

Synced only from a published, immutable firmware release that was physically
accepted, and only with the owner-approved verification record for it
(PUBLISHING §22). Everything is read from that release and its tagged commit,
never from a working tree:

    python3 tools/firmware_contract.py --sync --release v1.2.0 \\
        --verification data/firmware-verifications/v1.2.0.json --firmware-clone ../crosspoint-reader
    python3 tools/firmware_contract.py --check     # local, read-only, no network

--sync reads the release, its assets, the annotated tag and the candidate run
from GitHub (public, read-only), and the tagged commit from the clone. Any
failed check writes nothing (exit 1); the contract is replaced last, so even a
failed write never leaves a contract that authorises anything unverified.
`--check` runs exactly what a production publication run decides from
(dogear.publish.firmware.capability).

tests/test_vendored.py checks the Python limits against the vendored file
always, and the vendored files against the contract's commit in a firmware
clone when one is present. Trust roots follow the same pattern (tools/chain_check.py).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dogear.publish import firmware  # noqa: E402
from dogear.publish.firmware import Invalid, require  # noqa: E402

CONTRACT = ROOT / firmware.CONTRACT
GLYPHS = ROOT / "mock" / "fonts" / "device-glyphs.json"
GLYPH_FONTS = ("notoserif_14_regular", "notoserif_18_bold")  # what mock/render_dogear.py fitcheck reads
LIMITS_HEADER = "src/activities/dogear/DogearLimits.h"
FONT_HEADERS = "lib/EpdFont/builtinFonts"
REPO = firmware.FIRMWARE_REPOSITORY
DOWNLOAD_PREFIX = f"https://github.com/{REPO}/releases/download/"
MAX_SUMS_BYTES = 64 * 1024
_CONST = re.compile(r"constexpr\s+size_t\s+(k\w+)\s*=\s*([0-9]+(?:\s*\*\s*[0-9]+)*)\s*;")


def parse_header(text: str) -> dict:
    """{name: value} for every `constexpr size_t kName = N [* M];`."""
    out = {}
    for name, expr in _CONST.findall(text):
        value = 1
        for factor in expr.split("*"):
            value *= int(factor)
        out[name] = value
    if not out:
        raise ValueError("no constants found in DogearLimits.h")
    return out


def glyph_ranges(font_name: str, text: str) -> list:
    """[[first, last], ...] from a generated font header's Intervals table."""
    block = text.split(f"{font_name}Intervals[] = {{", 1)[1].split("};", 1)[0]
    return [[int(a, 16), int(b, 16)] for a, b in re.findall(r"\{\s*(0x[0-9A-Fa-f]+),\s*(0x[0-9A-Fa-f]+),", block)]


class GitHub:
    """Read-only, unauthenticated GETs of the public firmware repository."""

    API = "https://api.github.com/"
    TIMEOUT_S = 30

    def get(self, path: str):
        req = urllib.request.Request(self.API + path, headers={
            "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "dogear-firmware-contract"})
        with urllib.request.urlopen(req, timeout=self.TIMEOUT_S) as resp:
            return json.loads(resp.read())

    def download(self, url: str, max_bytes: int) -> bytes:
        require(url.startswith(DOWNLOAD_PREFIX), f"refusing to download {url}")
        req = urllib.request.Request(url, headers={"User-Agent": "dogear-firmware-contract"})
        with urllib.request.urlopen(req, timeout=self.TIMEOUT_S) as resp:
            data = resp.read(max_bytes + 1)
        require(len(data) <= max_bytes, f"{url} is larger than {max_bytes} bytes")
        return data


class Clone:
    """The tagged commit, read with `git show` from a firmware clone."""

    def __init__(self, path: Path):
        self.path = path

    def git(self, *args) -> bytes:
        return subprocess.run(["git", "-C", str(self.path), *args], capture_output=True, check=True, timeout=120).stdout

    def resolve(self, tag: str) -> tuple:
        """(tag object sha, commit sha) of refs/tags/<tag>, which must be annotated."""
        kind = self.git("cat-file", "-t", f"refs/tags/{tag}").decode().strip()
        require(kind == "tag", f"{tag} in the clone is a {kind}, not an annotated tag")
        tag_object = self.git("rev-parse", "--verify", f"refs/tags/{tag}").decode().strip()
        commit = self.git("rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}").decode().strip()
        return tag_object, commit

    def show(self, commit: str, path: str) -> bytes:
        return self.git("show", f"{commit}:{path}")


def _sums_lines(data: bytes) -> dict:
    out = {}
    for line in data.decode("ascii").splitlines():
        m = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9._-]+)", line)
        require(m is not None, f"malformed {firmware.SUMS} line: {line!r}")
        require(m.group(2) not in out, f"{firmware.SUMS} names {m.group(2)} twice")
        out[m.group(2)] = m.group(1)
    return out


def verify_release(gh, record: dict, schema: dict) -> None:
    """The published release, its assets, its annotated tag and its candidate run
    are exactly what the verification record names."""
    rel = record["release"]
    tag = rel["tag"]
    release = gh.get(f"repos/{REPO}/releases/tags/{tag}")
    require(release.get("tag_name") == tag, f"GitHub returned release {release.get('tag_name')}, not {tag}")
    require(release.get("draft") is False, f"{tag} is a draft")
    require(release.get("prerelease") is False, f"{tag} is a prerelease")
    require(release.get("immutable") is True, f"{tag} is not an immutable release")
    require(release.get("id") == rel["releaseId"], f"{tag} is release {release.get('id')}, not {rel['releaseId']}")
    published = release.get("assets") or []
    names = [a.get("name") for a in published]
    require(len(names) == len(set(names)), "the release has duplicate asset names")
    require(sorted(names) == sorted(rel["assets"]), f"release assets {sorted(names)} are not the record's")
    for asset in published:
        require(asset.get("state") == "uploaded", f"{asset.get('name')} is not uploaded")
        require(asset.get("digest") == "sha256:" + rel["assets"][asset["name"]],
                f"{asset['name']}: GitHub digest {asset.get('digest')} is not the record's")
    sums_asset = next(a for a in published if a["name"] == firmware.SUMS)
    sums = gh.download(sums_asset.get("browser_download_url", ""), MAX_SUMS_BYTES)
    require(firmware.sha256(sums) == rel["sumsSha256"], f"the downloaded {firmware.SUMS} is not the record's")
    listed = _sums_lines(sums)
    expected = {n: d for n, d in rel["assets"].items() if n != firmware.SUMS}
    require(listed == expected, f"{firmware.SUMS} does not list exactly the record's files and digests")

    ref = gh.get(f"repos/{REPO}/git/ref/tags/{tag}")
    obj = ref.get("object") or {}
    require(obj.get("type") == "tag", f"{tag} is not an annotated tag")
    require(obj.get("sha") == rel["tagObject"], f"{tag} is tag object {obj.get('sha')}, not {rel['tagObject']}")
    tag_obj = gh.get(f"repos/{REPO}/git/tags/{rel['tagObject']}")
    target = tag_obj.get("object") or {}
    require(target.get("type") == "commit" and target.get("sha") == rel["commit"],
            f"{tag} does not point directly at {rel['commit']}")
    fields = firmware.parse_tag_message(tag_obj.get("message", ""), schema["packageOrder"])
    require(int(fields["candidate-run"]) == rel["candidateRun"], "the tag names another candidate run")
    require(fields["candidate-commit"] == rel["commit"], "the tag names another candidate commit")
    require(fields["candidate-sums-sha256"] == rel["sumsSha256"], f"the tag names another {firmware.SUMS}")
    require(fields["acceptance-sha256"] == record["acceptanceSha256"], "the tag names another acceptance record")

    run = gh.get(f"repos/{REPO}/actions/runs/{rel['candidateRun']}")
    require(str(run.get("path", "")).split("@", 1)[0] == firmware.CANDIDATE_WORKFLOW,
            f"run {rel['candidateRun']} is {run.get('path')!r}, not {firmware.CANDIDATE_WORKFLOW}")
    require((run.get("repository") or {}).get("full_name") == REPO and
            (run.get("head_repository") or {}).get("full_name") == REPO, "the run belongs to another repository")
    require(run.get("event") == "workflow_dispatch", "the candidate run was not dispatched")
    require(run.get("status") == "completed" and run.get("conclusion") == "success", "the candidate run did not succeed")
    require(str(run.get("head_branch", "")).startswith(f"release/{tag}"), "the run was not on the release branch")
    require(run.get("head_sha") == rel["commit"], f"the run built {run.get('head_sha')}, not {rel['commit']}")


def sync(tag: str, verification: Path, clone: Clone, gh, root: Path = None) -> str:
    """Check everything, then write the contract, the vendored schema and the
    glyph ranges. Nothing is written unless every check passes."""
    root = ROOT if root is None else root
    require(firmware.TAG.fullmatch(tag) is not None, f"{tag} is not a release tag")
    canonical_path = (root / firmware.VERIFICATIONS / f"{tag}.json").resolve()
    require(verification.resolve() == canonical_path,
            f"the verification record must be {firmware.VERIFICATIONS}/{tag}.json in this repository")
    record_bytes = verification.read_bytes()
    record = firmware.strict_json(record_bytes)
    firmware.exact_keys(record, firmware.VERIFICATION_KEYS, "verification record")
    firmware.exact_keys(record["release"], firmware.RELEASE_KEYS, "release")
    require(record["release"]["tag"] == tag, f"the record is for {record['release'].get('tag')}, not {tag}")

    # The tagged commit, from the clone (never a working tree).
    tag_object, commit = clone.resolve(tag)
    require(tag_object == record["release"]["tagObject"] and commit == record["release"]["commit"],
            f"the clone's {tag} is {tag_object} -> {commit}, not the record's")
    schema_bytes = clone.show(commit, firmware.FIRMWARE_SCHEMA_PATH)
    schema = firmware.check_schema(firmware.strict_json(schema_bytes))
    limits = parse_header(clone.show(commit, LIMITS_HEADER).decode("utf-8"))
    fonts = {name: glyph_ranges(name, clone.show(commit, f"{FONT_HEADERS}/{name}.h").decode("utf-8"))
             for name in GLYPH_FONTS}

    # The record in full, then the release it names.
    firmware.check_verification(record, schema, limits)
    verify_release(gh, record, schema)

    rel = record["release"]
    contract = {
        "_note": "Generated by tools/firmware_contract.py --sync from the published, immutable firmware release and "
                 "its tagged commit; do not edit.",
        "firmwareCommit": commit,
        "limits": dict(sorted(limits.items())),
        "release": {"tag": tag, "tagObject": tag_object, "commit": commit, "releaseId": rel["releaseId"],
                    "candidateRun": rel["candidateRun"], "sumsSha256": rel["sumsSha256"],
                    "acceptanceSha256": record["acceptanceSha256"],
                    "verification": f"{firmware.VERIFICATIONS}/{tag}.json",
                    "verificationSha256": firmware.sha256(record_bytes),
                    "acceptanceSchemaSha256": firmware.sha256(schema_bytes)},
    }
    outputs = {
        firmware.CONTRACT: (json.dumps(contract, indent=2) + "\n").encode("utf-8"),
        firmware.SCHEMA_COPY: schema_bytes,
        "mock/fonts/device-glyphs.json": (json.dumps({
            "_note": "Generated from crosspoint-reader lib/EpdFont/builtinFonts/*.h at the synced release's commit "
                     "by tools/firmware_contract.py --sync; do not edit.",
            "firmwareCommit": commit, "fonts": fonts}, indent=1) + "\n").encode("utf-8"),
    }
    # Prove the result authorises exactly as production will read it, before replacing anything.
    with tempfile.TemporaryDirectory() as staging:
        staged = Path(staging)
        for rel_path, data in list(outputs.items()) + [(canonical_path.relative_to(root.resolve()).as_posix(),
                                                         record_bytes)]:
            (staged / rel_path).parent.mkdir(parents=True, exist_ok=True)
            (staged / rel_path).write_bytes(data)
        result = firmware.capability(staged)
        require(result.ok, f"the synced contract would not authorise quiet issues: {result.reason}")
    for rel_path, data in sorted(outputs.items(), key=lambda kv: kv[0] == firmware.CONTRACT):  # contract last
        target = root / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, target)
    return f"synced {tag} ({commit}): {len(limits)} limits, quiet issues authorised"


def main(argv: list) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--sync", action="store_true")
    mode.add_argument("--check", action="store_true")
    p.add_argument("--release")
    p.add_argument("--verification", type=Path)
    p.add_argument("--firmware-clone", type=Path)
    args = p.parse_args(argv)
    if args.check:
        result = firmware.capability()
        print("quiet issues authorised" if result.ok else f"quiet issues NOT authorised: {result.reason}")
        return 0 if result.ok else 1
    if not (args.release and args.verification and args.firmware_clone):
        print("error: --sync needs --release, --verification and --firmware-clone: a contract is synced only from "
              "a published, verified release (PUBLISHING §22)", file=sys.stderr)
        return 2
    try:
        print(sync(args.release, args.verification, Clone(args.firmware_clone), GitHub()))
    except (Invalid, OSError, ValueError, KeyError, TypeError, AttributeError, StopIteration,
            subprocess.SubprocessError) as err:
        print(f"error: sync refused: {type(err).__name__}: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
