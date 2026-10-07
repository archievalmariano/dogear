"""The production link checker against a local server (no network)."""

from __future__ import annotations

import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from dogear.publish.checks import _probe, http_link_checker


class LinkCheckTests(unittest.TestCase):
    def setUp(self):
        test = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def answer(self, body: bool):
                routes = {"/ok": 200, "/head-only": 404, "/gone": 410, "/busy": 503, "/blocked-bots": 403}
                if self.path == "/to-http":
                    self.send_response(302)
                    self.send_header("Location", f"http://127.0.0.1:{test.port}/ok")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                status = routes.get(self.path, 404)
                if self.command == "HEAD" and self.path == "/head-only":
                    status = 200  # the trap: HEAD says fine, GET says gone
                payload = b"page\\n"
                self.send_response(status)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                if body:
                    self.wfile.write(payload)

            def do_GET(self):
                self.answer(True)

            def do_HEAD(self):
                self.answer(False)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.port = server.server_address[1]
        self.url = lambda path: f"http://127.0.0.1:{self.port}{path}"

    def test_verdicts(self):
        self.assertEqual(_probe(self.url("/ok"), 5)[0], "ok")
        self.assertEqual(_probe(self.url("/head-only"), 5), ("block", "HTTP 404"))  # GET decides, not HEAD
        self.assertEqual(_probe(self.url("/gone"), 5), ("block", "HTTP 410"))
        self.assertEqual(_probe(self.url("/busy"), 5), ("warn", "HTTP 503"))
        self.assertEqual(_probe(self.url("/blocked-bots"), 5), ("warn", "HTTP 403"))
        verdict, detail = _probe(self.url("/to-http"), 5)
        self.assertEqual(verdict, "block")
        self.assertIn("off HTTPS", detail)

    def test_report(self):
        report = http_link_checker(timeout=5)([self.url("/ok"), self.url("/to-http"), self.url("/busy")])
        self.assertEqual(len(report.blocking), 1)
        self.assertEqual(len(report.warnings), 1)


if __name__ == "__main__":
    unittest.main()
