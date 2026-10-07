"""The R2 adapter (dogear.publish.r2) against AWS's published SigV4 examples and a
local fake of R2's S3 API: signatures checked on every request, conditional
writes, quoted ETags, and injected failures. No network beyond 127.0.0.1."""

from __future__ import annotations

import datetime as dt
import hashlib
import re
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from dogear.publish import routes
from dogear.publish.publisher import PublishedUnverified
from dogear.publish.r2 import EMPTY_SHA, R2Store, sigv4_authorization
from dogear.publish.registry import REGISTRY_KEY
from dogear.publish.store import PreconditionFailed, StoreError

try:
    from tests.test_publish import Fixture, W0, W1, at, four_weeks
except ImportError:
    from test_publish import Fixture, W0, W1, at, four_weeks

KEY_ID, SECRET = "AKIDEXAMPLEDOGEAR000", "secret/EXAMPLE+value"
NOW = dt.datetime(2026, 11, 9, 6, 2, tzinfo=dt.timezone.utc)
AUTH = re.compile(r"^AWS4-HMAC-SHA256 Credential=([^/]+)/(\d{8})/auto/s3/aws4_request, "
                  r"SignedHeaders=([a-z0-9;-]+), Signature=[0-9a-f]{64}$")


class SigV4Tests(unittest.TestCase):
    """AWS's documented examples (S3 API reference, 'Signature Calculations ... Authorization Header')."""

    def test_get_object_example(self):
        auth = sigv4_authorization(
            "GET", "/test.txt", "", {"Host": "examplebucket.s3.amazonaws.com", "Range": "bytes=0-9",
                                     "x-amz-content-sha256": EMPTY_SHA, "x-amz-date": "20130524T000000Z"},
            EMPTY_SHA, "20130524T000000Z", "AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", "us-east-1")
        self.assertTrue(auth.endswith("SignedHeaders=host;range;x-amz-content-sha256;x-amz-date, "
                                      "Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41"))

    def test_put_object_example(self):
        sha = hashlib.sha256(b"Welcome to Amazon S3.").hexdigest()
        auth = sigv4_authorization(
            "PUT", "/test%24file.text", "", {"Host": "examplebucket.s3.amazonaws.com",
                                             "Date": "Fri, 24 May 2013 00:00:00 GMT", "x-amz-date": "20130524T000000Z",
                                             "x-amz-storage-class": "REDUCED_REDUNDANCY", "x-amz-content-sha256": sha},
            sha, "20130524T000000Z", "AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", "us-east-1")
        self.assertTrue(auth.endswith("Signature=98ad721746da40c64f1a55b78f14c238d841ea1380cd77a1b5971af0ece108bd"))


class FakeR2:
    """Enough of R2's S3 API for the publisher: GET and PUT, If-Match / If-None-Match,
    quoted MD5 ETags, signature and payload-hash checks. ``fault(method, key)`` may
    return an HTTP status to answer instead, or "drop-after" (apply, then close the
    connection without answering: the ambiguous write)."""

    def __init__(self, bucket: str = "dogear-issues-staging"):
        self.bucket, self.objects, self.fault, self.requests = bucket, {}, None, []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _check(self, body: bytes):
                auth = self.headers.get("Authorization", "")
                m = AUTH.match(auth)
                if not m or m.group(1) != KEY_ID:
                    return 403
                signed = {name: self.headers.get(name, "") for name in m.group(3).split(";")}
                if "x-amz-content-sha256" not in signed or "host" not in signed:
                    return 403
                if signed["x-amz-content-sha256"] != hashlib.sha256(body).hexdigest():
                    return 400  # XAmzContentSHA256Mismatch
                expected = sigv4_authorization(self.command, self.path, "", signed, signed["x-amz-content-sha256"],
                                               self.headers["x-amz-date"], KEY_ID, SECRET, "auto")
                return None if expected == auth else 403

            def _key(self):
                prefix = f"/{fake.bucket}/"
                return self.path[len(prefix):] if self.path.startswith(prefix) else None

            def _reply(self, status, body=b"", headers=None):
                self.send_response(status)
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _error(self, status, code):
                self._reply(status, f"<Error><Code>{code}</Code></Error>".encode())

            def do_GET(self):
                fake.requests.append(("GET", self.path, {k.lower(): v for k, v in self.headers.items()}))
                bad = self._check(b"")
                if bad:
                    return self._error(bad, "SignatureDoesNotMatch")
                key = self._key()
                forced = fake.fault and fake.fault("GET", key)
                if isinstance(forced, int):
                    return self._error(forced, "InternalError")
                if key not in fake.objects:
                    return self._error(404, "NoSuchKey")
                data, _ctype = fake.objects[key]
                self._reply(200, data, {"ETag": f'"{hashlib.md5(data).hexdigest()}"'})

            def do_PUT(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                fake.requests.append(("PUT", self.path, {k.lower(): v for k, v in self.headers.items()}))
                bad = self._check(body)
                if bad:
                    return self._error(bad, "SignatureDoesNotMatch")
                key = self._key()
                forced = fake.fault and fake.fault("PUT", key)
                if isinstance(forced, int):
                    return self._error(forced, "InternalError")
                current = fake.objects.get(key)
                etag = current and hashlib.md5(current[0]).hexdigest()
                inm, im = self.headers.get("If-None-Match"), self.headers.get("If-Match")
                if (inm == "*" and current is not None) or (im is not None and (current is None or im != f'"{etag}"')):
                    return self._error(412, "PreconditionFailed")
                fake.objects[key] = (body, self.headers.get("Content-Type"))
                if forced == "drop-after":
                    self.close_connection = True
                    self.connection.shutdown(socket.SHUT_RDWR)
                    return
                self._reply(200, b"", {"ETag": f'"{hashlib.md5(body).hexdigest()}"'})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self.thread.start()
        self.endpoint = f"http://127.0.0.1:{self.server.server_address[1]}"

    def store(self, secret: str = SECRET) -> R2Store:
        return R2Store("0" * 32, self.bucket, KEY_ID, secret, endpoint=self.endpoint, timeout=5, clock=lambda: NOW)

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class R2StoreTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeR2()
        self.addCleanup(self.fake.close)
        self.s = self.fake.store()

    def test_round_trip_and_unquoted_etag(self):
        self.assertIsNone(self.s.get("issues/x.json"))
        etag = self.s.put("issues/x.json", b"{}", content_type="application/json")
        self.assertEqual(etag, hashlib.md5(b"{}").hexdigest())
        obj = self.s.get("issues/x.json")
        self.assertEqual((obj.data, obj.etag), (b"{}", etag))
        self.assertEqual(self.fake.objects["issues/x.json"][1], "application/json")

    def test_conditional_writes(self):
        first = self.s.put(REGISTRY_KEY, b"one", content_type="application/json", if_none_match=True)
        with self.assertRaises(PreconditionFailed):
            self.s.put(REGISTRY_KEY, b"two", content_type="application/json", if_none_match=True)
        with self.assertRaises(PreconditionFailed):
            self.s.put(REGISTRY_KEY, b"two", content_type="application/json", if_match="0" * 32)
        self.s.put(REGISTRY_KEY, b"two", content_type="application/json", if_match=first)
        self.assertEqual(self.fake.objects[REGISTRY_KEY][0], b"two")
        sent = [h for m, p, h in self.fake.requests if m == "PUT"]
        self.assertEqual(sent[-1]["if-match"], f'"{first}"')  # quoted on the wire

    def test_failures_are_unknown_outcomes_not_precondition(self):
        self.fake.fault = lambda m, k: 500
        with self.assertRaises(StoreError) as err:
            self.s.put("a.json", b"x", content_type="application/json")
        self.assertNotIsInstance(err.exception, PreconditionFailed)
        with self.assertRaises(StoreError):
            self.s.get("a.json")
        self.fake.fault = lambda m, k: "drop-after" if m == "PUT" else None
        with self.assertRaises(StoreError) as err:
            self.s.put("b.json", b"y", content_type="application/json")
        self.assertNotIsInstance(err.exception, PreconditionFailed)
        self.assertIn("b.json", self.fake.objects)  # it applied: only a read-back can tell

    def test_bad_credentials_are_refused_and_never_echoed(self):
        bad = self.fake.store(secret="wrong-secret-value")
        with self.assertRaises(StoreError) as err:
            bad.get("a.json")
        self.assertIn("HTTP 403", str(err.exception))
        for text in (str(err.exception), repr(bad), repr(self.s)):
            self.assertNotIn("wrong-secret-value", text)
            self.assertNotIn(SECRET, text)
            self.assertNotIn(KEY_ID, text)

    def test_configuration_is_strict(self):
        with self.assertRaises(ValueError):
            R2Store("0" * 32, "Bad_Bucket", KEY_ID, SECRET)
        with self.assertRaises(ValueError):
            R2Store("not-an-account", "dogear-issues", KEY_ID, SECRET)
        with self.assertRaises(ValueError):
            R2Store("0" * 32, "dogear-issues", KEY_ID, SECRET, endpoint="http://r2.example.com")
        with self.assertRaises(ValueError) as err:
            R2Store.from_env("dogear-issues", environ={"DOGEAR_R2_ACCOUNT_ID": "0" * 32})
        self.assertIn("DOGEAR_R2_SECRET_ACCESS_KEY", str(err.exception))
        with self.assertRaises(ValueError):
            self.s.get("../publication.json")
        self.assertTrue(R2Store("0" * 32, "dogear-issues", KEY_ID, SECRET).netloc.endswith(".r2.cloudflarestorage.com"))

    def test_endpoint_override_only_for_a_local_server_and_only_when_allowed(self):
        env = {"DOGEAR_R2_ACCOUNT_ID": "0" * 32, "DOGEAR_R2_ACCESS_KEY_ID": KEY_ID,
               "DOGEAR_R2_SECRET_ACCESS_KEY": SECRET}
        default = R2Store.from_env("dogear-issues-staging", environ=env)
        self.assertEqual(default.netloc, "0" * 32 + ".r2.cloudflarestorage.com")
        local = dict(env, DOGEAR_R2_ENDPOINT=self.fake.endpoint)
        with self.assertRaises(ValueError):
            R2Store.from_env("dogear-issues-staging", environ=local)  # not allowed: an error, not ignored
        self.assertEqual(R2Store.from_env("dogear-issues-staging", environ=local, allow_endpoint_override=True).host,
                         "127.0.0.1")
        for remote in ("https://r2.example.com", "https://attacker.example", "http://10.0.0.5:9000"):
            with self.assertRaises(ValueError, msg=remote):
                R2Store.from_env("dogear-issues-staging", environ=dict(env, DOGEAR_R2_ENDPOINT=remote),
                                 allow_endpoint_override=True)


class PublisherOnR2Tests(unittest.TestCase):
    """The real publisher, unchanged, with R2Store over the fake."""

    def setUp(self):
        self.fake = FakeR2()
        self.addCleanup(self.fake.close)

    def fixture(self):
        f = Fixture(self, four_weeks(), at(W0, 9))
        f.store = self.fake.store()
        f.served = lambda path: routes.fetch(f.store, path)
        return f

    def test_launch_promote_and_ambiguous_registry_write(self):
        f = self.fixture()
        self.assertEqual(f.run().launch(first_publication=True).status, "published")
        self.assertIn(REGISTRY_KEY, self.fake.objects)
        f.clock.t = at(W1)
        # The registry write applies but the connection drops: resolved by reading back.
        dropped = []

        def fault(method, key):
            if method == "PUT" and key == REGISTRY_KEY and not dropped:
                dropped.append(key)
                return "drop-after"
            return None
        self.fake.fault = fault
        out = f.run().promote()
        self.assertEqual((out.status, dropped), ("published", [REGISTRY_KEY]))
        status, body = routes.fetch(f.store, "/current.json")
        self.assertEqual(status, 200)
        self.assertIn(b"dogear-2026-11-09", body)

    def test_unavailable_storage_after_switch_is_unverified_not_success(self):
        f = self.fixture()
        f.run().launch(first_publication=True)
        f.clock.t = at(W1)
        f.served = lambda path: (503, b"")
        with self.assertRaises(PublishedUnverified):
            f.run().promote()


class HttpServedTests(unittest.TestCase):
    """http_served against a local stand-in for the Worker (the reference router over a store)."""

    def setUp(self):
        from dogear.publish.store import MemoryStore
        self.store, self.paths = MemoryStore(), []
        store, paths = self.store, self.paths

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                paths.append(self.path)
                r = routes.respond(store, "GET", self.path.split("?", 1)[0])
                self.send_response(r.status)
                for k, v in r.headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(r.body)))
                self.end_headers()
                self.wfile.write(r.body)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.base = f"http://127.0.0.1:{server.server_address[1]}"

    def test_reads_what_is_served_without_following_redirects(self):
        from dogear.publish.checks import http_served
        f = Fixture(self, four_weeks(), at(W0, 9))
        f.store = self.store
        f.run().launch(first_publication=True)
        served = http_served(self.base)
        self.assertEqual(served("/current.json"), routes.fetch(self.store, "/current.json"))
        self.assertEqual(served("/")[0], 302)  # not followed
        self.assertEqual(served("/2099-01-05/")[0], 404)
        self.assertTrue(all("?verify=" in p for p in self.paths))
        self.assertEqual(len(set(self.paths)), len(self.paths))  # a fresh cache-buster each time

    def test_publisher_verifies_over_http(self):
        from dogear.publish.checks import http_served
        f = Fixture(self, four_weeks(), at(W0, 9))
        f.store = self.store
        f.served = http_served(self.base)
        self.assertEqual(f.run().launch(first_publication=True).status, "published")
        self.assertTrue(any(p.startswith("/issues/dogear-2026-11-02.") for p in self.paths))
        self.assertTrue(any(p.startswith("/2026-11-02/") for p in self.paths))

    def test_only_https_or_a_local_test_host(self):
        from dogear.publish.checks import http_served
        with self.assertRaises(ValueError):
            http_served("http://dogear-staging.pages.dev")
        with self.assertRaises(ValueError):
            http_served(self.base)("/current.json?x=1")


class CliStoreTests(unittest.TestCase):
    def test_r2_store_without_credentials_is_not_ready(self):
        import os
        import shutil
        import tempfile
        from pathlib import Path
        from unittest import mock
        from dogear.cli import main
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        env = {k: v for k, v in os.environ.items() if not k.startswith("DOGEAR_R2_")}
        with mock.patch.dict(os.environ, env, clear=True):
            code = main(["publish", "--store", "r2:dogear-issues-staging", "--pubdata", str(tmp), "status"])
        self.assertEqual(code, 4)


if __name__ == "__main__":
    unittest.main()
