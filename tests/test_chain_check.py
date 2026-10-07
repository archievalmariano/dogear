"""tools/chain_check.py: the device-root copy, and a TLS check against local
throwaway certificates (openssl CLI; no network)."""

from __future__ import annotations

import base64
import hashlib
import re
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import chain_check  # noqa: E402

CONF = """[req]
distinguished_name = dn
prompt = no
[dn]
CN = {cn}
[leaf]
subjectAltName = DNS:localhost
basicConstraints = CA:FALSE
[ca]
basicConstraints = critical,CA:TRUE
keyUsage = critical,keyCertSign,cRLSign
"""


class RootsTests(unittest.TestCase):
    def test_each_root_matches_its_sha256_comment(self):
        text = chain_check.ROOTS_PEM.read_text(encoding="utf-8")
        names = chain_check.root_names(text)
        self.assertEqual(len(names), 7)
        for b64 in re.findall(r"-----BEGIN CERTIFICATE-----\n(.*?)\n-----END CERTIFICATE-----", text, flags=re.S):
            der = base64.b64decode("".join(b64.split()))
            self.assertIn(hashlib.sha256(der).hexdigest(), names)

    @unittest.skipUnless(chain_check.FIRMWARE_ROOTS.exists(), "firmware checkout not beside this one")
    def test_copy_equals_the_firmware_header(self):
        header = chain_check.FIRMWARE_ROOTS.read_text(encoding="utf-8")
        self.assertEqual(chain_check.roots_from_header(header), chain_check.ROOTS_PEM.read_text(encoding="utf-8"),
                         "run python3 tools/chain_check.py --sync-roots")


@unittest.skipUnless(shutil.which("openssl"), "openssl CLI not available")
class LocalTlsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def make(self, leaf_days: int):
        def run(*args):
            subprocess.run(["openssl", *args], cwd=self.tmp, check=True, capture_output=True)
        (self.tmp / "ca.cnf").write_text(CONF.format(cn="DOGEAR Test Root"))
        (self.tmp / "leaf.cnf").write_text(CONF.format(cn="localhost"))
        run("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", "ca.key", "-out", "ca.pem", "-days", "30",
            "-config", "ca.cnf", "-extensions", "ca")
        run("req", "-newkey", "rsa:2048", "-nodes", "-keyout", "leaf.key", "-out", "leaf.csr", "-config", "leaf.cnf")
        run("x509", "-req", "-in", "leaf.csr", "-CA", "ca.pem", "-CAkey", "ca.key", "-CAcreateserial",
            "-out", "leaf.pem", "-days", str(leaf_days), "-extfile", "leaf.cnf", "-extensions", "leaf")
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(self.tmp / "leaf.pem", self.tmp / "leaf.key")
        server = socket.create_server(("127.0.0.1", 0))
        self.addCleanup(server.close)

        def serve():
            while True:
                try:
                    conn, _ = server.accept()
                except OSError:
                    return
                try:
                    with ctx.wrap_socket(conn, server_side=True):
                        pass
                except (OSError, ssl.SSLError):
                    pass

        threading.Thread(target=serve, daemon=True).start()
        return server.getsockname()[1], (self.tmp / "ca.pem").read_text()

    def test_verifies_only_with_the_given_roots(self):
        port, ca = self.make(leaf_days=60)
        ok, message = chain_check.check("localhost", ca, port=port)
        self.assertTrue(ok, message)
        ok, message = chain_check.check("localhost", chain_check.ROOTS_PEM.read_text(), port=port)
        self.assertFalse(ok)
        self.assertIn("does not verify with the device roots", message)

    def test_an_expiring_leaf_alerts(self):
        port, ca = self.make(leaf_days=5)
        ok, message = chain_check.check("localhost", ca, port=port)
        self.assertFalse(ok)
        self.assertIn("expires in", message)

    def test_unreachable_host_fails(self):
        with socket.create_server(("127.0.0.1", 0)) as s:
            port = s.getsockname()[1]
        ok, _ = chain_check.check("localhost", chain_check.ROOTS_PEM.read_text(), port=port, timeout=2)
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
