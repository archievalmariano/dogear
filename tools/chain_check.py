"""Weekly certificate-chain check (PUBLISHING.md §8): does each DOGEAR host present
a chain that verifies with ONLY the roots the device trusts?

    python3 tools/chain_check.py dogear-staging.pages.dev [dogear.archievalmariano.com ...]
    python3 tools/chain_check.py --sync-roots   # refresh data/dogear-trust-roots.pem from the firmware

The roots come from data/dogear-trust-roots.pem, a copy of the firmware's
DogearTrustRoots.h (a test keeps the two equal while the firmware checkout is
beside this one). OpenSSL path building approximates the device's wolfSSL; a
failure here is the early warning, G9 on hardware is the proof. Exit 1 if any
host fails, so the workflow alerts before devices do.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import re
import socket
import ssl
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROOTS_PEM = ROOT / "data" / "dogear-trust-roots.pem"
FIRMWARE_ROOTS = ROOT.parent / "crosspoint-reader" / "src" / "activities" / "dogear" / "DogearTrustRoots.h"
WARN_DAYS = 21


def roots_from_header(text: str) -> str:
    """The PEM blocks of kDogearTrustRoots, in order, each preceded by its name
    and SHA-256 comment from the header (which a test checks against the DER)."""
    names = re.findall(r"^\s*// (.+?) \(expires .*?\)\s*\n\s*// SHA-256 ([0-9A-F]{64})", text, flags=re.M)
    body = "".join(re.findall(r'^\s*"([^"\n]*)\\n"\s*;?\s*$', text, flags=re.M))
    pems = re.findall(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", body, flags=re.S)
    if not pems or len(pems) != len(names):
        raise ValueError(f"DogearTrustRoots.h: {len(pems)} certificates but {len(names)} name/SHA-256 comments")
    out = []
    for (name, sha), pem in zip(names, pems):
        b64 = pem[len("-----BEGIN CERTIFICATE-----"):-len("-----END CERTIFICATE-----")]
        lines = [b64[i:i + 64] for i in range(0, len(b64), 64)]
        out.append(f"# {name}\n# SHA-256 {sha}\n-----BEGIN CERTIFICATE-----\n" + "\n".join(lines)
                   + "\n-----END CERTIFICATE-----\n")
    return "".join(out)


def root_names(cadata: str) -> dict:
    """{SHA-256 of the DER: root name} from the comments in the PEM file."""
    return {sha.lower(): name for name, sha in re.findall(r"^# (.+)\n# SHA-256 ([0-9A-F]{64})$", cadata, flags=re.M)}


def check(host: str, cadata: str, port: int = 443, timeout: float = 15.0, now=None) -> tuple:
    """(ok, message)."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_verify_locations(cadata=cadata)
    try:
        with socket.create_connection((host, port), timeout=timeout) as raw, \
                ctx.wrap_socket(raw, server_hostname=host) as tls:
            cert = tls.getpeercert()
            chain = tls.get_verified_chain() if hasattr(tls, "get_verified_chain") else []  # DER, 3.13+
            version = tls.version()
    except ssl.SSLCertVerificationError as err:
        return False, f"{host}: chain does not verify with the device roots ({err.verify_message})"
    except (OSError, ssl.SSLError) as err:
        return False, f"{host}: {type(err).__name__}: {err}"
    expires = dt.datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=dt.timezone.utc)
    days = (expires - (now or dt.datetime.now(dt.timezone.utc))).days
    anchor = "a device root"
    if chain:
        der = chain[-1] if isinstance(chain[-1], bytes) else chain[-1].public_bytes(ssl.Encoding.DER)
        anchor = root_names(cadata).get(hashlib.sha256(der).hexdigest(), "an unnamed root")
    path = f"{len(chain) or '?'} certificates, anchored at {anchor}"
    if days < WARN_DAYS:
        return False, f"{host}: leaf expires in {days} days ({version}; {path})"
    return True, f"{host}: OK ({version}; {path}; leaf expires in {days} days)"


def main(argv: list) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("hosts", nargs="*")
    p.add_argument("--sync-roots", action="store_true")
    args = p.parse_args(argv)
    if args.sync_roots:
        ROOTS_PEM.write_text(roots_from_header(FIRMWARE_ROOTS.read_text(encoding="utf-8")), encoding="utf-8")
        print(f"wrote {ROOTS_PEM.relative_to(ROOT)}")
        return 0
    if not args.hosts:
        p.error("name at least one host")
    cadata = ROOTS_PEM.read_text(encoding="utf-8")
    failed = 0
    for host in args.hosts:
        ok, message = check(host, cadata)
        print(message)
        failed += not ok
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
